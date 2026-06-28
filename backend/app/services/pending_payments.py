from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
)
from app.services.inventory import (
    InventoryServiceError,
    release_inventory_reservation,
)


class PendingPaymentServiceError(Exception):
    """Error seguro para pagos pendientes por transferencia."""


class PendingPaymentNotFoundError(PendingPaymentServiceError):
    """No existe el pago pendiente solicitado."""


class InvalidPendingPaymentTransitionError(PendingPaymentServiceError):
    """El pago ya no admite la transición solicitada."""


class PendingPaymentIntegrityError(PendingPaymentServiceError):
    """El grafo del pedido o sus reservas no es consistente."""


@dataclass(frozen=True, slots=True)
class PendingPaymentTransitionResult:
    payment_attempt_id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    payment_status: PaymentStatus
    order_status: OrderStatus
    seller_order_status: SellerOrderStatus
    released_reservations: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class PendingPaymentExpirationBatchResult:
    processed: int
    expired: int
    skipped: int
    results: tuple[PendingPaymentTransitionResult, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _lock_payment_graph(
    session: Session,
    *,
    payment_attempt_id: uuid.UUID,
) -> tuple[
    PaymentAttempt,
    Order,
    PaymentProof | None,
    list[SellerOrder],
    list[OrderItem],
    list[InventoryReservation],
]:
    attempt = session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.method != PaymentMethod.BANK_TRANSFER:
        raise PendingPaymentNotFoundError(
            "No existe el pago por transferencia indicado."
        )

    order = session.scalar(
        select(Order).where(Order.id == attempt.order_id).with_for_update()
    )
    if order is None:
        raise PendingPaymentIntegrityError("El pago no tiene un pedido válido.")

    proof = session.scalar(
        select(PaymentProof)
        .where(PaymentProof.payment_attempt_id == attempt.id)
        .with_for_update()
    )

    seller_orders = list(
        session.scalars(
            select(SellerOrder)
            .where(SellerOrder.order_id == order.id)
            .order_by(SellerOrder.id)
            .with_for_update()
        )
    )
    seller_order_ids = [seller_order.id for seller_order in seller_orders]
    items = (
        list(
            session.scalars(
                select(OrderItem)
                .where(OrderItem.seller_order_id.in_(seller_order_ids))
                .order_by(OrderItem.id)
                .with_for_update()
            )
        )
        if seller_order_ids
        else []
    )
    item_ids = [item.id for item in items]
    reservations = (
        list(
            session.scalars(
                select(InventoryReservation)
                .where(InventoryReservation.order_item_id.in_(item_ids))
                .order_by(
                    InventoryReservation.balance_id,
                    InventoryReservation.id,
                )
                .with_for_update()
            )
        )
        if item_ids
        else []
    )

    by_item: dict[uuid.UUID, int] = {}
    for reservation in reservations:
        by_item[reservation.order_item_id] = (
            by_item.get(reservation.order_item_id, 0) + reservation.quantity
        )
    if (
        not seller_orders
        or not items
        or any(by_item.get(item.id, 0) != item.quantity for item in items)
    ):
        raise PendingPaymentIntegrityError(
            "Las reservas no coinciden con las cantidades del pedido."
        )

    return attempt, order, proof, seller_orders, items, reservations


def _transition_result(
    *,
    attempt: PaymentAttempt,
    order: Order,
    released: int,
    replayed: bool,
) -> PendingPaymentTransitionResult:
    return PendingPaymentTransitionResult(
        payment_attempt_id=attempt.id,
        order_id=order.id,
        order_number=order.order_number,
        payment_status=attempt.status,
        order_status=order.status,
        seller_order_status=SellerOrderStatus.CANCELLED,
        released_reservations=released,
        replayed=replayed,
    )


def _release_active_reservations(
    *,
    session: Session,
    reservations: list[InventoryReservation],
    actor_user_id: uuid.UUID | None,
    notes: str,
) -> int:
    released = 0
    for reservation in reservations:
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        result = release_inventory_reservation(
            session=session,
            reservation_id=reservation.id,
            actor_user_id=actor_user_id,
            notes=notes,
        )
        if not result.replayed:
            released += 1
    return released


def cancel_pending_bank_transfer_order(
    *,
    session: Session,
    payment_attempt_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> PendingPaymentTransitionResult:
    normalized_reason = " ".join((reason or "").split()) or "Cancelado por el comprador."
    if len(normalized_reason) > 500:
        raise ValueError("La razón no puede superar 500 caracteres.")
    effective_now = now or _utc_now()
    attempt, order, proof, seller_orders, _items, reservations = _lock_payment_graph(
        session,
        payment_attempt_id=payment_attempt_id,
    )

    if attempt.status == PaymentStatus.CANCELLED and order.status == OrderStatus.CANCELLED:
        return _transition_result(
            attempt=attempt,
            order=order,
            released=0,
            replayed=True,
        )
    if attempt.status == PaymentStatus.EXPIRED and order.status == OrderStatus.EXPIRED:
        raise InvalidPendingPaymentTransitionError(
            "El pago ya venció y no puede cancelarse manualmente."
        )
    if proof is not None:
        raise InvalidPendingPaymentTransitionError(
            "El pago ya tiene un comprobante en revisión."
        )
    if attempt.status != PaymentStatus.AWAITING_PROOF:
        raise InvalidPendingPaymentTransitionError(
            "El pago ya no está esperando comprobante."
        )
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise InvalidPendingPaymentTransitionError(
            "El pedido ya no está pendiente de pago."
        )
    if any(reservation.status != ReservationStatus.ACTIVE for reservation in reservations):
        raise InvalidPendingPaymentTransitionError(
            "Las reservas del pedido ya no están activas."
        )

    try:
        released = _release_active_reservations(
            session=session,
            reservations=reservations,
            actor_user_id=actor_user_id,
            notes=normalized_reason,
        )
    except InventoryServiceError as exc:
        raise PendingPaymentIntegrityError(str(exc)) from exc

    attempt.status = PaymentStatus.CANCELLED
    attempt.failed_at = effective_now
    order.status = OrderStatus.CANCELLED
    for seller_order in seller_orders:
        seller_order.status = SellerOrderStatus.CANCELLED
    session.flush()
    return _transition_result(
        attempt=attempt,
        order=order,
        released=released,
        replayed=False,
    )


def expire_pending_bank_transfer_payment(
    *,
    session: Session,
    payment_attempt_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> PendingPaymentTransitionResult:
    effective_now = now or _utc_now()
    attempt, order, proof, seller_orders, _items, reservations = _lock_payment_graph(
        session,
        payment_attempt_id=payment_attempt_id,
    )

    if attempt.status == PaymentStatus.EXPIRED and order.status == OrderStatus.EXPIRED:
        return _transition_result(
            attempt=attempt,
            order=order,
            released=0,
            replayed=True,
        )
    if proof is not None:
        raise InvalidPendingPaymentTransitionError(
            "El pago tiene comprobante y no debe expirar automáticamente."
        )
    if attempt.status != PaymentStatus.AWAITING_PROOF:
        raise InvalidPendingPaymentTransitionError(
            "El pago ya no está esperando comprobante."
        )
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise InvalidPendingPaymentTransitionError(
            "El pedido ya no está pendiente de pago."
        )
    if _ensure_aware(attempt.expires_at) > effective_now:
        raise InvalidPendingPaymentTransitionError(
            "El pago aún no ha vencido."
        )
    if any(reservation.status != ReservationStatus.ACTIVE for reservation in reservations):
        raise InvalidPendingPaymentTransitionError(
            "Las reservas del pedido ya no están activas."
        )

    try:
        released = _release_active_reservations(
            session=session,
            reservations=reservations,
            actor_user_id=actor_user_id,
            notes="Reserva liberada automáticamente por vencimiento del pago.",
        )
    except InventoryServiceError as exc:
        raise PendingPaymentIntegrityError(str(exc)) from exc

    attempt.status = PaymentStatus.EXPIRED
    attempt.failed_at = effective_now
    order.status = OrderStatus.EXPIRED
    for seller_order in seller_orders:
        seller_order.status = SellerOrderStatus.CANCELLED
    session.flush()
    return _transition_result(
        attempt=attempt,
        order=order,
        released=released,
        replayed=False,
    )


def expirable_bank_transfer_payment_ids(
    *,
    session: Session,
    limit: int,
    now: datetime | None = None,
    lock: bool = False,
) -> list[uuid.UUID]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit debe estar entre 1 y 1000.")
    effective_now = now or _utc_now()
    statement = (
        select(PaymentAttempt.id)
        .outerjoin(
            PaymentProof,
            PaymentProof.payment_attempt_id == PaymentAttempt.id,
        )
        .where(
            PaymentAttempt.method == PaymentMethod.BANK_TRANSFER,
            PaymentAttempt.status == PaymentStatus.AWAITING_PROOF,
            PaymentAttempt.expires_at <= effective_now,
            PaymentProof.id.is_(None),
        )
        .order_by(PaymentAttempt.expires_at, PaymentAttempt.id)
        .limit(limit)
    )
    if lock:
        statement = statement.with_for_update(
            of=PaymentAttempt,
            skip_locked=True,
        )
    return list(session.scalars(statement))


def expire_pending_bank_transfer_payments(
    *,
    session: Session,
    limit: int = 100,
    actor_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> PendingPaymentExpirationBatchResult:
    effective_now = now or _utc_now()
    attempt_ids = expirable_bank_transfer_payment_ids(
        session=session,
        limit=limit,
        now=effective_now,
        lock=True,
    )
    results: list[PendingPaymentTransitionResult] = []
    skipped = 0
    for attempt_id in attempt_ids:
        try:
            results.append(
                expire_pending_bank_transfer_payment(
                    session=session,
                    payment_attempt_id=attempt_id,
                    actor_user_id=actor_user_id,
                    now=effective_now,
                )
            )
        except InvalidPendingPaymentTransitionError:
            skipped += 1
    return PendingPaymentExpirationBatchResult(
        processed=len(attempt_ids),
        expired=sum(1 for result in results if not result.replayed),
        skipped=skipped,
        results=tuple(results),
    )
