from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import case, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order, OrderItem, OrderPackage, PaymentAttempt, SellerOrder, SellerPayout,
    SellerPayoutItem, Store, StoreBankAccountVersion,
)
from app.models.enums import (
    PackageStatus, PaymentStatus, SellerOrderDecisionStatus, SellerOrderStatus,
    SellerPayoutStatus,
)
from app.services.bank_accounts import usable_bank_account_version
from app.services.financial_audit import (
    PAYOUT_CANCELLED, PAYOUT_HELD, PAYOUT_PAID, PAYOUT_RELEASED,
    PAYOUT_SCHEDULED, record_financial_audit,
)
from app.services.financial_reconciliation import (
    FinancialReconciliationError, reconcile_seller_order,
)
from app.services.payout_calendar import PayoutCycleWindow, validate_executable_cycle
from app.services.private_storage import StagedPrivateFile, promote_private_file


ZERO = Decimal("0.00")
PAYOUT_RELEASE_DELAY = timedelta(days=4)


class SellerPayoutError(Exception):
    pass


class SellerPayoutEligibilityError(SellerPayoutError):
    pass


class SellerPayoutTransitionError(SellerPayoutError):
    pass


class SellerPayoutNotFoundError(SellerPayoutError):
    pass


@dataclass(frozen=True, slots=True)
class DeliverySynchronizationResult:
    seller_order_id: uuid.UUID
    delivered_at: datetime | None
    payout_eligible_at: datetime | None
    synchronized: bool


@dataclass(frozen=True, slots=True)
class EligibleSellerOrderView:
    seller_order_id: uuid.UUID
    seller_order_number: str
    gross_amount: Decimal
    discount_amount: Decimal
    commission_amount: Decimal
    net_amount: Decimal
    currency: str
    eligible_at: datetime


@dataclass(frozen=True, slots=True)
class SellerPayoutScheduleResult:
    payout: SellerPayout
    order_count: int


@dataclass(frozen=True, slots=True)
class SellerPayoutPaidResult:
    payout: SellerPayout
    replayed: bool


@dataclass(frozen=True, slots=True)
class SellerPayoutTransitionResult:
    payout: SellerPayout
    previous_status: SellerPayoutStatus
    replayed: bool


@dataclass(frozen=True, slots=True)
class PayoutCycleStorePreview:
    store_id: uuid.UUID
    store_public_code: str
    order_count: int
    currency: str
    gross_total: Decimal
    discount_total: Decimal
    commission_total: Decimal
    net_total: Decimal


@dataclass(frozen=True, slots=True)
class PayoutCycleScheduleResult:
    window: PayoutCycleWindow
    payouts: tuple[SellerPayoutScheduleResult, ...]
    skipped_store_ids: tuple[uuid.UUID, ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SellerPayoutEligibilityError("Los instantes financieros deben incluir zona horaria.")
    return value.astimezone(timezone.utc)


def _delivery_evidence(
    session: Session, seller_order_id: uuid.UUID
) -> tuple[int, int, datetime | None]:
    item_count, delivered_count, delivered_at = session.execute(
        select(
            func.count(OrderItem.id),
            func.sum(case(((OrderPackage.status == PackageStatus.HANDED_OVER)
                           & OrderPackage.handed_over_at.is_not(None), 1), else_=0)),
            func.max(OrderPackage.handed_over_at),
        )
        .select_from(OrderItem)
        .outerjoin(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
        .where(OrderItem.seller_order_id == seller_order_id)
    ).one()
    return int(item_count or 0), int(delivered_count or 0), delivered_at


def synchronize_seller_order_delivery(
    session: Session, *, seller_order_id: uuid.UUID, lock: bool = True
) -> DeliverySynchronizationResult:
    statement = select(SellerOrder).where(SellerOrder.id == seller_order_id)
    if lock:
        statement = statement.with_for_update()
    seller_order = session.scalar(statement)
    if seller_order is None:
        raise SellerPayoutNotFoundError("La presentación del pedido no existe.")
    if seller_order.decision_status != SellerOrderDecisionStatus.APPROVED:
        return DeliverySynchronizationResult(
            seller_order.id, seller_order.delivered_at,
            seller_order.payout_eligible_at, False,
        )
    item_count, delivered_count, delivered_at = _delivery_evidence(session, seller_order.id)
    if not item_count or delivered_count != item_count or delivered_at is None:
        return DeliverySynchronizationResult(
            seller_order.id, seller_order.delivered_at,
            seller_order.payout_eligible_at, False,
        )
    normalized_delivery = _utc(delivered_at)
    eligible_at = normalized_delivery + PAYOUT_RELEASE_DELAY
    changed = (
        seller_order.delivered_at != normalized_delivery
        or seller_order.payout_eligible_at != eligible_at
        or seller_order.status != SellerOrderStatus.COMPLETED
    )
    seller_order.delivered_at = normalized_delivery
    seller_order.payout_eligible_at = eligible_at
    seller_order.status = SellerOrderStatus.COMPLETED
    session.flush()
    return DeliverySynchronizationResult(
        seller_order.id, normalized_delivery, eligible_at, changed
    )


def backfill_seller_order_deliveries(
    session: Session, *, limit: int = 1000
) -> tuple[DeliverySynchronizationResult, ...]:
    ids = session.scalars(
        select(SellerOrder.id)
        .where(
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.delivered_at.is_(None),
            ~exists(select(SellerPayoutItem.seller_order_id).where(
                SellerPayoutItem.seller_order_id == SellerOrder.id
            )),
        )
        .order_by(SellerOrder.created_at, SellerOrder.id)
        .limit(max(1, min(limit, 10_000)))
    ).all()
    results = []
    for seller_order_id in ids:
        result = synchronize_seller_order_delivery(session, seller_order_id=seller_order_id)
        if result.delivered_at is not None:
            results.append(result)
    return tuple(results)


def _approved_payment_exists():
    return exists(select(PaymentAttempt.id).where(
        PaymentAttempt.order_id == SellerOrder.order_id,
        PaymentAttempt.status == PaymentStatus.APPROVED,
        PaymentAttempt.approved_at.is_not(None),
    ))


def _active_payout_item_exists():
    return exists(select(SellerPayoutItem.seller_order_id).where(
        SellerPayoutItem.seller_order_id == SellerOrder.id,
        SellerPayoutItem.released_at.is_(None),
    ))


def _store_payout_scheduling_lock_id(store_id: uuid.UUID) -> int:
    return int.from_bytes(
        hashlib.sha256(b"payout-scheduling:" + store_id.bytes).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _lock_store_payout_scheduling(session: Session, store_id: uuid.UUID) -> None:
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                _store_payout_scheduling_lock_id(store_id)
            )
        )
    )


def eligible_seller_orders(
    session: Session, *, store_id: uuid.UUID, eligible_through: datetime,
    currency: str = "USD", lock: bool = False,
) -> tuple[EligibleSellerOrderView, ...]:
    cutoff = _utc(eligible_through)
    statement = (
        select(SellerOrder, Order.currency)
        .join(Order, Order.id == SellerOrder.order_id)
        .where(
            SellerOrder.store_id == store_id,
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.status == SellerOrderStatus.COMPLETED,
            SellerOrder.requires_refund_resolution.is_(False),
            SellerOrder.delivered_at.is_not(None),
            SellerOrder.payout_eligible_at.is_not(None),
            SellerOrder.payout_eligible_at <= cutoff,
            SellerOrder.currency == currency,
            Order.currency == currency,
            _approved_payment_exists(),
            ~_active_payout_item_exists(),
        )
        .order_by(SellerOrder.payout_eligible_at, SellerOrder.id)
    )
    if lock:
        statement = statement.with_for_update(of=SellerOrder)
    rows = session.execute(statement).all()
    views = []
    for seller_order, order_currency in rows:
        try:
            snapshot = reconcile_seller_order(
                session, seller_order_id=seller_order.id,
                expected_store_id=store_id, expected_currency=currency, lock=lock,
            )
        except FinancialReconciliationError as exc:
            raise SellerPayoutEligibilityError(str(exc)) from exc
        if (
            snapshot.subtotal != seller_order.subtotal
            or snapshot.discount_total != seller_order.discount_total
            or snapshot.commission_total != seller_order.commission_total
            or snapshot.seller_net_total != seller_order.seller_net_total
        ):
            raise SellerPayoutEligibilityError(
                "Los snapshots financieros de los pedidos no son consistentes."
            )
        views.append(EligibleSellerOrderView(
            seller_order.id, seller_order.seller_order_number,
            seller_order.subtotal, seller_order.discount_total,
            seller_order.commission_total, seller_order.seller_net_total,
            order_currency, seller_order.payout_eligible_at,
        ))
    return tuple(views)


def _candidate_store_ids(
    session: Session, *, cutoff: datetime, store_id: uuid.UUID | None
) -> tuple[uuid.UUID, ...]:
    statement = (
        select(SellerOrder.store_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .where(
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.status == SellerOrderStatus.COMPLETED,
            SellerOrder.requires_refund_resolution.is_(False),
            SellerOrder.delivered_at.is_not(None),
            SellerOrder.payout_eligible_at.is_not(None),
            SellerOrder.payout_eligible_at <= cutoff,
            SellerOrder.currency == "USD", Order.currency == "USD",
            _approved_payment_exists(), ~_active_payout_item_exists(),
        )
        .distinct().order_by(SellerOrder.store_id)
    )
    if store_id is not None:
        statement = statement.where(SellerOrder.store_id == store_id)
    return tuple(session.scalars(statement).all())


def preview_payout_cycle(
    session: Session, *, cycle_date: date, now: datetime,
    store_id: uuid.UUID | None = None,
) -> tuple[PayoutCycleWindow, tuple[PayoutCycleStorePreview, ...]]:
    try:
        window = validate_executable_cycle(cycle_date, now=now)
    except ValueError as exc:
        raise SellerPayoutEligibilityError(str(exc)) from exc
    previews = []
    for candidate_store_id in _candidate_store_ids(
        session, cutoff=window.cutoff_utc, store_id=store_id
    ):
        rows = eligible_seller_orders(
            session, store_id=candidate_store_id,
            eligible_through=window.cutoff_utc,
        )
        if not rows:
            continue
        store = session.get(Store, candidate_store_id)
        previews.append(PayoutCycleStorePreview(
            candidate_store_id, store.public_code, len(rows), "USD",
            sum((row.gross_amount for row in rows), ZERO),
            sum((row.discount_amount for row in rows), ZERO),
            sum((row.commission_amount for row in rows), ZERO),
            sum((row.net_amount for row in rows), ZERO),
        ))
    return window, tuple(previews)


def _schedule_store_payout(
    session: Session, *, store_id: uuid.UUID, window: PayoutCycleWindow,
    executed_at: datetime, actor_user_id: uuid.UUID | None, notes: str | None,
) -> SellerPayoutScheduleResult:
    # Canonical scheduling lock order:
    # Store advisory lock -> SellerOrders -> OrderItems/reconciliation ->
    # BankAccountVersion -> SellerPayout/SellerPayoutItem creation.
    _lock_store_payout_scheduling(session, store_id)
    orders = eligible_seller_orders(
        session, store_id=store_id, eligible_through=window.cutoff_utc, lock=True
    )
    if not orders:
        raise SellerPayoutEligibilityError("No existen pedidos elegibles para liquidar.")
    bank_version = usable_bank_account_version(
        session, store_id=store_id, at=_utc(executed_at), lock=True
    )
    if bank_version is None:
        raise SellerPayoutEligibilityError(
            "La tienda no tiene una versión bancaria aprobada y utilizable."
        )
    gross = sum((row.gross_amount for row in orders), ZERO)
    discounts = sum((row.discount_amount for row in orders), ZERO)
    commission = sum((row.commission_amount for row in orders), ZERO)
    net = sum((row.net_amount for row in orders), ZERO)
    if net != gross - discounts - commission:
        raise SellerPayoutEligibilityError(
            "Los snapshots financieros de los pedidos no son consistentes."
        )
    payout = SellerPayout(
        store_id=store_id, bank_account_version_id=bank_version.id,
        status=SellerPayoutStatus.SCHEDULED, currency="USD",
        gross_sales_total=gross, discount_total=discounts,
        commission_total=commission, net_total=net,
        scheduled_for=window.scheduled_for_utc,
        destination_bank_name_snapshot=bank_version.bank_name,
        destination_account_last4=bank_version.account_last4,
        notes=(notes or "").strip() or None,
    )
    session.add(payout)
    session.flush()
    session.add_all([SellerPayoutItem(
        payout_id=payout.id, seller_order_id=row.seller_order_id,
        gross_amount_snapshot=row.gross_amount,
        discount_amount_snapshot=row.discount_amount,
        commission_amount_snapshot=row.commission_amount,
        net_amount_snapshot=row.net_amount, eligible_at=row.eligible_at,
    ) for row in orders])
    record_financial_audit(
        session, action=PAYOUT_SCHEDULED, actor_user_id=actor_user_id,
        metadata={"store_id": str(store_id), "payout_id": str(payout.id),
                  "bank_account_version_id": str(bank_version.id),
                  "status": SellerPayoutStatus.SCHEDULED.value},
    )
    session.flush()
    return SellerPayoutScheduleResult(payout, len(orders))


def schedule_seller_payout(
    session: Session, *, store_id: uuid.UUID, cycle_date: date, now: datetime,
    actor_user_id: uuid.UUID | None = None, notes: str | None = None,
) -> SellerPayoutScheduleResult:
    try:
        window = validate_executable_cycle(cycle_date, now=now)
    except ValueError as exc:
        raise SellerPayoutEligibilityError(str(exc)) from exc
    return _schedule_store_payout(
        session, store_id=store_id, window=window, executed_at=now,
        actor_user_id=actor_user_id, notes=notes,
    )


def schedule_payout_cycle(
    session: Session, *, cycle_date: date, now: datetime,
    store_id: uuid.UUID | None = None, actor_user_id: uuid.UUID | None = None,
) -> PayoutCycleScheduleResult:
    try:
        window = validate_executable_cycle(cycle_date, now=now)
    except ValueError as exc:
        raise SellerPayoutEligibilityError(str(exc)) from exc
    payouts, skipped = [], []
    for candidate_store_id in _candidate_store_ids(
        session, cutoff=window.cutoff_utc, store_id=store_id
    ):
        try:
            payouts.append(_schedule_store_payout(
                session, store_id=candidate_store_id, window=window,
                executed_at=now, actor_user_id=actor_user_id, notes=None,
            ))
        except SellerPayoutEligibilityError:
            skipped.append(candidate_store_id)
    return PayoutCycleScheduleResult(window, tuple(payouts), tuple(skipped))


def _lock_payout(session: Session, payout_number: str) -> SellerPayout:
    payout = session.scalar(
        select(SellerPayout)
        .where(SellerPayout.payout_number == (payout_number or "").strip().upper())
        .with_for_update()
    )
    if payout is None:
        raise SellerPayoutNotFoundError("La liquidación no existe.")
    return payout


def _lock_items(session: Session, payout_id: uuid.UUID) -> tuple[SellerPayoutItem, ...]:
    return tuple(session.scalars(
        select(SellerPayoutItem)
        .where(SellerPayoutItem.payout_id == payout_id)
        .order_by(SellerPayoutItem.seller_order_id).with_for_update()
    ).all())


def _audit_transition(
    session: Session, *, action: str, payout: SellerPayout,
    actor_user_id: uuid.UUID | None,
) -> None:
    record_financial_audit(
        session, action=action, actor_user_id=actor_user_id,
        metadata={"store_id": str(payout.store_id), "payout_id": str(payout.id),
                  "bank_account_version_id": str(payout.bank_account_version_id),
                  "status": payout.status.value},
    )


def _revalidate_payout(
    session: Session, payout: SellerPayout,
    items: tuple[SellerPayoutItem, ...],
) -> None:
    if not items:
        raise SellerPayoutTransitionError("La liquidación no contiene pedidos.")
    bank_version = session.get(StoreBankAccountVersion, payout.bank_account_version_id)
    if (bank_version is None or bank_version.store_id != payout.store_id
            or bank_version.currency != "USD"):
        raise SellerPayoutTransitionError("La versión bancaria histórica no es válida.")
    gross = discount = commission = net = ZERO
    for item in items:
        if item.released_at is not None:
            raise SellerPayoutTransitionError("La liquidación contiene una asignación liberada.")
        seller_order = session.scalar(
            select(SellerOrder).where(SellerOrder.id == item.seller_order_id).with_for_update()
        )
        if (
            seller_order is None or seller_order.store_id != payout.store_id
            or seller_order.currency != "USD"
            or seller_order.status != SellerOrderStatus.COMPLETED
            or seller_order.requires_refund_resolution
            or seller_order.delivered_at is None
            or seller_order.payout_eligible_at is None
        ):
            raise SellerPayoutTransitionError(
                "Un pedido de la liquidación ya no cumple las condiciones financieras."
            )
        active_assignment = session.scalar(select(SellerPayoutItem.payout_id).where(
            SellerPayoutItem.seller_order_id == seller_order.id,
            SellerPayoutItem.released_at.is_(None),
        ))
        if active_assignment != payout.id:
            raise SellerPayoutTransitionError("La asignación activa del pedido es inconsistente.")
        payment_ok = session.scalar(select(exists(select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == seller_order.order_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
        ))))
        if not payment_ok:
            raise SellerPayoutTransitionError("El pago del comprador ya no está aprobado.")
        item_count, delivered_count, delivered_at = _delivery_evidence(session, seller_order.id)
        if (
            not item_count or delivered_count != item_count or delivered_at is None
            or _utc(delivered_at) != _utc(seller_order.delivered_at)
            or _utc(seller_order.payout_eligible_at)
            != _utc(seller_order.delivered_at) + PAYOUT_RELEASE_DELAY
        ):
            raise SellerPayoutTransitionError("La evidencia de entrega del pedido es inconsistente.")
        try:
            snapshot = reconcile_seller_order(
                session, seller_order_id=seller_order.id,
                expected_store_id=payout.store_id, expected_currency="USD", lock=True,
            )
        except FinancialReconciliationError as exc:
            raise SellerPayoutTransitionError(str(exc)) from exc
        if (
            item.gross_amount_snapshot != snapshot.subtotal
            or item.discount_amount_snapshot != snapshot.discount_total
            or item.commission_amount_snapshot != snapshot.commission_total
            or item.net_amount_snapshot != snapshot.seller_net_total
        ):
            raise SellerPayoutTransitionError(
                "Un snapshot del payout no coincide con su pedido canónico."
            )
        gross += item.gross_amount_snapshot
        discount += item.discount_amount_snapshot
        commission += item.commission_amount_snapshot
        net += item.net_amount_snapshot
    if (
        gross != payout.gross_sales_total or discount != payout.discount_total
        or commission != payout.commission_total or net != payout.net_total
        or net != gross - discount - commission
    ):
        raise SellerPayoutTransitionError(
            "El lote no coincide con sus snapshots y requiere revisión financiera."
        )


def hold_seller_payout(
    session: Session, *, payout_number: str,
    actor_user_id: uuid.UUID | None = None,
) -> SellerPayoutTransitionResult:
    payout = _lock_payout(session, payout_number)
    previous = payout.status
    if previous == SellerPayoutStatus.ON_HOLD:
        return SellerPayoutTransitionResult(payout, previous, True)
    if previous != SellerPayoutStatus.SCHEDULED:
        raise SellerPayoutTransitionError("Solo una liquidación programada puede ponerse en hold.")
    _lock_items(session, payout.id)
    payout.status = SellerPayoutStatus.ON_HOLD
    _audit_transition(session, action=PAYOUT_HELD, payout=payout, actor_user_id=actor_user_id)
    session.flush()
    return SellerPayoutTransitionResult(payout, previous, False)


def resume_seller_payout(
    session: Session, *, payout_number: str,
    actor_user_id: uuid.UUID | None = None,
) -> SellerPayoutTransitionResult:
    payout = _lock_payout(session, payout_number)
    previous = payout.status
    if previous == SellerPayoutStatus.SCHEDULED:
        return SellerPayoutTransitionResult(payout, previous, True)
    if previous != SellerPayoutStatus.ON_HOLD:
        raise SellerPayoutTransitionError("Solo una liquidación en hold puede reanudarse.")
    items = _lock_items(session, payout.id)
    _revalidate_payout(session, payout, items)
    payout.status = SellerPayoutStatus.SCHEDULED
    _audit_transition(session, action=PAYOUT_RELEASED, payout=payout, actor_user_id=actor_user_id)
    session.flush()
    return SellerPayoutTransitionResult(payout, previous, False)


def cancel_seller_payout(
    session: Session, *, payout_number: str, cancelled_at: datetime,
    actor_user_id: uuid.UUID | None = None,
) -> SellerPayoutTransitionResult:
    payout = _lock_payout(session, payout_number)
    previous = payout.status
    items = _lock_items(session, payout.id)
    if previous == SellerPayoutStatus.CANCELLED:
        if payout.cancelled_at is not None and all(
            item.released_at == payout.cancelled_at for item in items
        ):
            return SellerPayoutTransitionResult(payout, previous, True)
        raise SellerPayoutTransitionError("La historia de cancelación es inconsistente.")
    if previous == SellerPayoutStatus.PAID:
        raise SellerPayoutTransitionError("Una liquidación pagada no puede cancelarse.")
    if previous not in {SellerPayoutStatus.SCHEDULED, SellerPayoutStatus.ON_HOLD}:
        raise SellerPayoutTransitionError("La liquidación no puede cancelarse.")
    effective = _utc(cancelled_at)
    payout.status = SellerPayoutStatus.CANCELLED
    payout.cancelled_at = effective
    for item in items:
        if item.released_at is not None:
            raise SellerPayoutTransitionError("La asignación ya fue liberada previamente.")
        item.released_at = effective
    _audit_transition(session, action=PAYOUT_CANCELLED, payout=payout,
                      actor_user_id=actor_user_id)
    session.flush()
    return SellerPayoutTransitionResult(payout, previous, False)


def mark_seller_payout_paid(
    session: Session, *, payout_number: str, external_reference: str,
    paid_at: datetime, actor_user_id: uuid.UUID | None = None,
    staged_receipt: StagedPrivateFile | None = None,
    receipt_root: str | Path | None = None,
) -> SellerPayoutPaidResult:
    normalized_number = (payout_number or "").strip().upper()
    normalized_reference = (external_reference or "").strip()
    if not normalized_number or not normalized_reference:
        raise SellerPayoutTransitionError(
            "La liquidación y la referencia bancaria son obligatorias."
        )
    if len(normalized_reference) > 200:
        raise SellerPayoutTransitionError(
            "La referencia bancaria no puede superar 200 caracteres."
        )
    effective_paid_at = _utc(paid_at)
    payout = _lock_payout(session, normalized_number)
    if payout.status == SellerPayoutStatus.PAID:
        same = (payout.external_reference == normalized_reference
                and payout.paid_at == effective_paid_at and staged_receipt is None)
        if same:
            return SellerPayoutPaidResult(payout, True)
        raise SellerPayoutTransitionError(
            "La liquidación ya fue marcada como pagada con datos diferentes."
        )
    if payout.status == SellerPayoutStatus.ON_HOLD:
        raise SellerPayoutTransitionError("Una liquidación en hold debe reanudarse antes de pagar.")
    if payout.status == SellerPayoutStatus.CANCELLED:
        raise SellerPayoutTransitionError("Una liquidación cancelada no puede pagarse.")
    if payout.status != SellerPayoutStatus.SCHEDULED:
        raise SellerPayoutTransitionError("La liquidación no puede pagarse desde su estado actual.")
    if effective_paid_at < payout.scheduled_for:
        raise SellerPayoutTransitionError("La fecha de pago no puede preceder al ciclo programado.")
    items = _lock_items(session, payout.id)
    _revalidate_payout(session, payout, items)
    duplicate = session.scalar(select(SellerPayout.id).where(
        SellerPayout.external_reference == normalized_reference,
        SellerPayout.id != payout.id,
    ))
    if duplicate is not None:
        raise SellerPayoutTransitionError("La referencia bancaria ya fue utilizada.")
    if staged_receipt is not None and receipt_root is None:
        raise SellerPayoutTransitionError(
            "No se configuró el almacenamiento privado del comprobante."
        )
    try:
        with session.begin_nested():
            payout.status = SellerPayoutStatus.PAID
            payout.external_reference = normalized_reference
            payout.paid_at = effective_paid_at
            _audit_transition(
                session,
                action=PAYOUT_PAID,
                payout=payout,
                actor_user_id=actor_user_id,
            )
            session.flush()
    except IntegrityError as exc:
        constraint_name = getattr(
            getattr(exc.orig, "diag", None), "constraint_name", None
        )
        if constraint_name == "uq_seller_payouts_external_reference":
            raise SellerPayoutTransitionError(
                "La referencia bancaria ya fue utilizada."
            ) from exc
        raise
    if staged_receipt is not None:
        promote_private_file(staged_receipt, root=receipt_root)
        payout.receipt_storage_key = staged_receipt.storage_key
        payout.receipt_original_filename = staged_receipt.original_filename
        payout.receipt_media_type = staged_receipt.media_type
        payout.receipt_size_bytes = staged_receipt.size_bytes
        payout.receipt_sha256 = staged_receipt.sha256
        session.flush()
    return SellerPayoutPaidResult(payout, False)
