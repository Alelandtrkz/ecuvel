from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import case, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    SellerOrder,
    SellerPayout,
    SellerPayoutItem,
    StoreOnboarding,
)
from app.models.enums import (
    PackageStatus,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreOnboardingStatus,
)
from app.services.private_storage import StagedPrivateFile, promote_private_file


ZERO = Decimal("0.00")
PAYOUT_RELEASE_DELAY = timedelta(days=15)


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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _delivery_evidence(
    session: Session, seller_order_id: uuid.UUID
) -> tuple[int, int, datetime | None]:
    item_count, delivered_count, delivered_at = session.execute(
        select(
            func.count(OrderItem.id),
            func.sum(
                case(
                    (
                        (OrderPackage.status == PackageStatus.HANDED_OVER)
                        & OrderPackage.handed_over_at.is_not(None),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.max(OrderPackage.handed_over_at),
        )
        .select_from(OrderItem)
        .outerjoin(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
        .where(OrderItem.seller_order_id == seller_order_id)
    ).one()
    return int(item_count or 0), int(delivered_count or 0), delivered_at


def synchronize_seller_order_delivery(
    session: Session,
    *,
    seller_order_id: uuid.UUID,
    lock: bool = True,
) -> DeliverySynchronizationResult:
    statement = select(SellerOrder).where(SellerOrder.id == seller_order_id)
    if lock:
        statement = statement.with_for_update()
    seller_order = session.scalar(statement)
    if seller_order is None:
        raise SellerPayoutNotFoundError("La presentación del pedido no existe.")
    if seller_order.decision_status != SellerOrderDecisionStatus.APPROVED:
        return DeliverySynchronizationResult(
            seller_order_id=seller_order.id,
            delivered_at=seller_order.delivered_at,
            payout_eligible_at=seller_order.payout_eligible_at,
            synchronized=False,
        )

    item_count, delivered_count, delivered_at = _delivery_evidence(
        session, seller_order.id
    )
    if not item_count or delivered_count != item_count or delivered_at is None:
        return DeliverySynchronizationResult(
            seller_order_id=seller_order.id,
            delivered_at=seller_order.delivered_at,
            payout_eligible_at=seller_order.payout_eligible_at,
            synchronized=False,
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
        seller_order_id=seller_order.id,
        delivered_at=normalized_delivery,
        payout_eligible_at=eligible_at,
        synchronized=changed,
    )


def backfill_seller_order_deliveries(
    session: Session, *, limit: int = 1000
) -> tuple[DeliverySynchronizationResult, ...]:
    ids = session.scalars(
        select(SellerOrder.id)
        .where(
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.delivered_at.is_(None),
        )
        .order_by(SellerOrder.created_at, SellerOrder.id)
        .limit(max(1, min(limit, 10_000)))
    ).all()
    results = []
    for seller_order_id in ids:
        result = synchronize_seller_order_delivery(
            session, seller_order_id=seller_order_id
        )
        if result.delivered_at is not None:
            results.append(result)
    return tuple(results)


def _approved_payment_exists():
    return exists(
        select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == SellerOrder.order_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
        )
    )


def eligible_seller_orders(
    session: Session,
    *,
    store_id: uuid.UUID,
    now: datetime | None = None,
    currency: str = "USD",
    limit: int = 1000,
    lock: bool = False,
) -> tuple[EligibleSellerOrderView, ...]:
    effective_now = _utc(now or datetime.now(timezone.utc))
    statement = (
        select(SellerOrder, Order.currency)
        .join(Order, Order.id == SellerOrder.order_id)
        .outerjoin(
            SellerPayoutItem,
            SellerPayoutItem.seller_order_id == SellerOrder.id,
        )
        .where(
            SellerOrder.store_id == store_id,
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.status == SellerOrderStatus.COMPLETED,
            SellerOrder.requires_refund_resolution.is_(False),
            SellerOrder.delivered_at.is_not(None),
            SellerOrder.payout_eligible_at.is_not(None),
            SellerOrder.payout_eligible_at <= effective_now,
            SellerPayoutItem.seller_order_id.is_(None),
            Order.currency == currency,
            _approved_payment_exists(),
        )
        .order_by(SellerOrder.payout_eligible_at, SellerOrder.id)
        .limit(max(1, min(limit, 10_000)))
    )
    if lock:
        statement = statement.with_for_update(of=SellerOrder, skip_locked=True)
    rows = session.execute(statement).all()
    return tuple(
        EligibleSellerOrderView(
            seller_order_id=seller_order.id,
            seller_order_number=seller_order.seller_order_number,
            gross_amount=seller_order.subtotal,
            discount_amount=seller_order.discount_total,
            commission_amount=seller_order.commission_total,
            net_amount=seller_order.seller_net_total,
            currency=order_currency,
            eligible_at=seller_order.payout_eligible_at,
        )
        for seller_order, order_currency in rows
    )


def _bank_snapshot(session: Session, store_id: uuid.UUID) -> tuple[str, str]:
    onboarding = session.scalar(
        select(StoreOnboarding)
        .where(
            StoreOnboarding.store_id == store_id,
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
        )
        .order_by(StoreOnboarding.completed_at.desc().nullslast(), StoreOnboarding.id)
        .limit(1)
    )
    bank_name = (onboarding.bank_name or "").strip() if onboarding else ""
    raw_account = (onboarding.bank_account_number or "").strip() if onboarding else ""
    compact_account = re.sub(r"[^A-Za-z0-9]", "", raw_account)
    if not bank_name or len(compact_account) < 4:
        raise SellerPayoutEligibilityError(
            "La tienda no tiene una cuenta bancaria completa para programar la liquidación."
        )
    return bank_name[:120], compact_account[-4:]


def schedule_seller_payout(
    session: Session,
    *,
    store_id: uuid.UUID,
    scheduled_for: datetime,
    now: datetime | None = None,
    currency: str = "USD",
    limit: int = 1000,
    status: SellerPayoutStatus = SellerPayoutStatus.SCHEDULED,
    notes: str | None = None,
) -> SellerPayoutScheduleResult:
    if status not in {SellerPayoutStatus.SCHEDULED, SellerPayoutStatus.ON_HOLD}:
        raise SellerPayoutTransitionError("El lote nuevo debe quedar programado o en revisión.")
    scheduled_at = _utc(scheduled_for)
    orders = eligible_seller_orders(
        session,
        store_id=store_id,
        now=now,
        currency=currency,
        limit=limit,
        lock=True,
    )
    if not orders:
        raise SellerPayoutEligibilityError("No existen pedidos elegibles para liquidar.")
    bank_name, account_last4 = _bank_snapshot(session, store_id)
    gross = sum((row.gross_amount for row in orders), ZERO)
    discounts = sum((row.discount_amount for row in orders), ZERO)
    commission = sum((row.commission_amount for row in orders), ZERO)
    net = sum((row.net_amount for row in orders), ZERO)
    if net != gross - discounts - commission:
        raise SellerPayoutEligibilityError(
            "Los snapshots financieros de los pedidos no son consistentes."
        )
    payout = SellerPayout(
        store_id=store_id,
        status=status,
        currency=currency,
        gross_sales_total=gross,
        discount_total=discounts,
        commission_total=commission,
        net_total=net,
        scheduled_for=scheduled_at,
        destination_bank_name_snapshot=bank_name,
        destination_account_last4=account_last4,
        notes=(notes or "").strip() or None,
    )
    session.add(payout)
    session.flush()
    for row in orders:
        session.add(
            SellerPayoutItem(
                payout_id=payout.id,
                seller_order_id=row.seller_order_id,
                gross_amount_snapshot=row.gross_amount,
                discount_amount_snapshot=row.discount_amount,
                commission_amount_snapshot=row.commission_amount,
                net_amount_snapshot=row.net_amount,
                eligible_at=row.eligible_at,
            )
        )
    session.flush()
    return SellerPayoutScheduleResult(payout=payout, order_count=len(orders))


def mark_seller_payout_paid(
    session: Session,
    *,
    payout_number: str,
    external_reference: str,
    paid_at: datetime,
    staged_receipt: StagedPrivateFile | None = None,
    receipt_root: str | Path | None = None,
) -> SellerPayoutPaidResult:
    normalized_number = (payout_number or "").strip().upper()
    normalized_reference = (external_reference or "").strip()
    if not normalized_number or not normalized_reference:
        raise SellerPayoutTransitionError(
            "La liquidación y la referencia bancaria son obligatorias."
        )
    effective_paid_at = _utc(paid_at)
    payout = session.scalar(
        select(SellerPayout)
        .options(selectinload(SellerPayout.items))
        .where(SellerPayout.payout_number == normalized_number)
        .with_for_update()
    )
    if payout is None:
        raise SellerPayoutNotFoundError("La liquidación no existe.")
    if payout.status == SellerPayoutStatus.PAID:
        same = (
            payout.external_reference == normalized_reference
            and payout.paid_at == effective_paid_at
            and staged_receipt is None
        )
        if same:
            return SellerPayoutPaidResult(payout=payout, replayed=True)
        raise SellerPayoutTransitionError(
            "La liquidación ya fue marcada como pagada con datos diferentes."
        )
    if payout.status == SellerPayoutStatus.CANCELLED:
        raise SellerPayoutTransitionError("Una liquidación cancelada no puede pagarse.")
    item_gross = sum((item.gross_amount_snapshot for item in payout.items), ZERO)
    item_discount = sum((item.discount_amount_snapshot for item in payout.items), ZERO)
    item_commission = sum((item.commission_amount_snapshot for item in payout.items), ZERO)
    item_net = sum((item.net_amount_snapshot for item in payout.items), ZERO)
    if (
        item_gross != payout.gross_sales_total
        or item_discount != payout.discount_total
        or item_commission != payout.commission_total
        or item_net != payout.net_total
    ):
        raise SellerPayoutTransitionError(
            "El lote no coincide con sus snapshots y requiere revisión financiera."
        )
    if staged_receipt is not None:
        if receipt_root is None:
            raise SellerPayoutTransitionError(
                "No se configuró el almacenamiento privado del comprobante."
            )
        promote_private_file(staged_receipt, root=receipt_root)
        payout.receipt_storage_key = staged_receipt.storage_key
        payout.receipt_original_filename = staged_receipt.original_filename
        payout.receipt_media_type = staged_receipt.media_type
        payout.receipt_size_bytes = staged_receipt.size_bytes
        payout.receipt_sha256 = staged_receipt.sha256
    payout.status = SellerPayoutStatus.PAID
    payout.external_reference = normalized_reference[:200]
    payout.paid_at = effective_paid_at
    session.flush()
    return SellerPayoutPaidResult(payout=payout, replayed=False)
