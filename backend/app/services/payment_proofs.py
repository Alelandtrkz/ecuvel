from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    UserStatus,
)
from app.services.inventory import (
    InventoryServiceError,
    consume_inventory_reservation,
    release_inventory_reservation,
)
from app.services.private_storage import (
    PrivateStorageError,
    StagedPrivateFile,
    delete_private_file,
    promote_private_file,
    verify_private_file,
)


class PaymentProofServiceError(Exception):
    """Error seguro del flujo de comprobantes."""


class PaymentProofNotFoundError(PaymentProofServiceError):
    pass


class PaymentProofUploadConflictError(PaymentProofServiceError):
    pass


class InvalidPaymentProofTransitionError(PaymentProofServiceError):
    pass


class PaymentProofIntegrityError(PaymentProofServiceError):
    pass


class PaymentProofExpiredError(PaymentProofServiceError):
    pass


@dataclass(frozen=True, slots=True)
class SubmitPaymentProofResult:
    proof_id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    storage_path: Path
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReviewPaymentProofResult:
    proof_id: uuid.UUID
    order_number: str
    proof_status: PaymentProofStatus
    payment_status: PaymentStatus
    order_status: OrderStatus
    reservation_count: int
    replayed: bool


def _order_graph(
    session: Session, order_id: uuid.UUID, *, lock: bool
) -> tuple[Order, list[SellerOrder], list[OrderItem], list[InventoryReservation]]:
    order_statement = select(Order).where(Order.id == order_id)
    if lock:
        order_statement = order_statement.with_for_update()
    order = session.scalar(order_statement)
    if order is None:
        raise PaymentProofIntegrityError("El pago no tiene un pedido válido.")

    seller_statement = (
        select(SellerOrder)
        .where(SellerOrder.order_id == order.id)
        .order_by(SellerOrder.id)
    )
    if lock:
        seller_statement = seller_statement.with_for_update()
    seller_orders = list(session.scalars(seller_statement))
    seller_ids = [item.id for item in seller_orders]
    item_statement = (
        select(OrderItem)
        .where(OrderItem.seller_order_id.in_(seller_ids))
        .order_by(OrderItem.id)
    )
    if lock:
        item_statement = item_statement.with_for_update()
    items = list(session.scalars(item_statement)) if seller_ids else []
    item_ids = [item.id for item in items]
    reservation_statement = (
        select(InventoryReservation)
        .where(InventoryReservation.order_item_id.in_(item_ids))
        .order_by(InventoryReservation.balance_id, InventoryReservation.id)
    )
    if lock:
        reservation_statement = reservation_statement.with_for_update()
    reservations = (
        list(session.scalars(reservation_statement)) if item_ids else []
    )
    by_item: dict[uuid.UUID, int] = {}
    for reservation in reservations:
        by_item[reservation.order_item_id] = (
            by_item.get(reservation.order_item_id, 0) + reservation.quantity
        )
    if not items or any(by_item.get(item.id, 0) != item.quantity for item in items):
        raise PaymentProofIntegrityError(
            "Las reservas no coinciden con las cantidades del pedido."
        )
    return order, seller_orders, items, reservations


def submit_bank_transfer_proof(
    *,
    session: Session,
    payment_attempt_id: uuid.UUID,
    staged_file: StagedPrivateFile,
    upload_idempotency_key: str,
    storage_root: str | Path,
    uploaded_by_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> SubmitPaymentProofResult:
    key = upload_idempotency_key.strip()
    if not key or len(key) > 150:
        raise PaymentProofServiceError("La clave de carga no es válida.")
    attempt = session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.method != PaymentMethod.BANK_TRANSFER:
        raise PaymentProofServiceError("El intento de pago no admite comprobantes.")
    locked_order = session.scalar(
        select(Order).where(Order.id == attempt.order_id).with_for_update()
    )
    if locked_order is None:
        raise PaymentProofIntegrityError("El pago no tiene un pedido vÃ¡lido.")
    existing = session.scalar(
        select(PaymentProof)
        .where(PaymentProof.payment_attempt_id == attempt.id)
        .with_for_update()
    )
    order, _, _, reservations = _order_graph(session, attempt.order_id, lock=True)
    if attempt.amount != order.grand_total or attempt.currency != order.currency:
        raise PaymentProofIntegrityError(
            "El monto del pago no coincide con el total del pedido."
        )
    if existing is not None:
        delete_private_file(staged_file.temporary_path)
        if existing.upload_idempotency_key != key:
            raise PaymentProofUploadConflictError(
                "Este pago ya tiene un comprobante diferente."
            )
        return SubmitPaymentProofResult(
            existing.id,
            order.id,
            order.order_number,
            verify_private_file(
                root=storage_root,
                storage_key=existing.storage_key,
                size_bytes=existing.size_bytes,
                sha256=existing.sha256,
            ),
            True,
        )

    effective_now = now or datetime.now(timezone.utc)
    if attempt.status != PaymentStatus.AWAITING_PROOF:
        raise InvalidPaymentProofTransitionError(
            "El pago ya no está esperando un comprobante."
        )
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise InvalidPaymentProofTransitionError(
            "El pedido ya no está pendiente de pago."
        )
    if attempt.expires_at <= effective_now or any(
        reservation.expires_at <= effective_now for reservation in reservations
    ):
        raise PaymentProofExpiredError(
            "La reserva venció; no es posible cargar el comprobante."
        )
    if any(r.status != ReservationStatus.ACTIVE for r in reservations):
        raise InvalidPaymentProofTransitionError(
            "Las reservas del pedido ya no están activas."
        )
    if uploaded_by_user_id is not None and uploaded_by_user_id != order.buyer_id:
        raise PaymentProofServiceError("El comprobante no pertenece al comprador.")

    proof = PaymentProof(
        payment_attempt_id=attempt.id,
        storage_key=staged_file.storage_key,
        original_filename=staged_file.original_filename,
        media_type=staged_file.media_type,
        size_bytes=staged_file.size_bytes,
        sha256=staged_file.sha256,
        status=PaymentProofStatus.PENDING_REVIEW,
        upload_idempotency_key=key,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    session.add(proof)
    attempt.status = PaymentStatus.PROCESSING
    session.flush()
    try:
        final_path = promote_private_file(staged_file, root=storage_root)
    except PrivateStorageError as exc:
        raise PaymentProofServiceError(str(exc)) from exc
    return SubmitPaymentProofResult(
        proof.id, order.id, order.order_number, final_path, False
    )


def review_payment_proof(
    *,
    session: Session,
    proof_id: uuid.UUID,
    decision: str,
    reviewer_user_id: uuid.UUID,
    storage_root: str | Path,
    reason: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> ReviewPaymentProofResult:
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        raise PaymentProofServiceError("La decisión debe ser approve o reject.")
    normalized_reason = " ".join((reason or "").split()) or None
    normalized_notes = " ".join((notes or "").split()) or None
    if normalized_decision == "reject" and not normalized_reason:
        raise PaymentProofServiceError("El rechazo requiere una razón.")
    if normalized_reason and len(normalized_reason) > 500:
        raise PaymentProofServiceError("La razón no puede superar 500 caracteres.")
    if normalized_notes and len(normalized_notes) > 1000:
        raise PaymentProofServiceError("Las notas no pueden superar 1000 caracteres.")

    proof = session.scalar(
        select(PaymentProof).where(PaymentProof.id == proof_id).with_for_update()
    )
    if proof is None:
        raise PaymentProofNotFoundError("No existe el comprobante indicado.")
    target = (
        PaymentProofStatus.APPROVED
        if normalized_decision == "approve"
        else PaymentProofStatus.REJECTED
    )
    if proof.status == target:
        attempt = session.get(PaymentAttempt, proof.payment_attempt_id)
        order = session.get(Order, attempt.order_id) if attempt else None
        if attempt is None or order is None:
            raise PaymentProofIntegrityError("La decisión guardada está incompleta.")
        return ReviewPaymentProofResult(
            proof.id, order.order_number, proof.status, attempt.status,
            order.status, 0, True
        )
    if proof.status != PaymentProofStatus.PENDING_REVIEW:
        raise InvalidPaymentProofTransitionError(
            "El comprobante ya tiene una decisión opuesta."
        )
    reviewer = session.scalar(
        select(User).where(User.id == reviewer_user_id).with_for_update()
    )
    if reviewer is None or reviewer.status != UserStatus.ACTIVE:
        raise PaymentProofServiceError("El revisor no existe o no está activo.")
    attempt = session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == proof.payment_attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.status != PaymentStatus.PROCESSING:
        raise PaymentProofIntegrityError("El pago no está en revisión.")
    order, seller_orders, _, reservations = _order_graph(
        session, attempt.order_id, lock=True
    )
    if attempt.amount != order.grand_total or attempt.currency != order.currency:
        raise PaymentProofIntegrityError(
            "El monto del pago no coincide con el total del pedido."
        )
    try:
        verify_private_file(
            root=storage_root,
            storage_key=proof.storage_key,
            size_bytes=proof.size_bytes,
            sha256=proof.sha256,
        )
    except PrivateStorageError as exc:
        raise PaymentProofIntegrityError(str(exc)) from exc

    effective_now = now or datetime.now(timezone.utc)
    try:
        if target == PaymentProofStatus.APPROVED:
            if attempt.expires_at <= effective_now or any(
                r.expires_at <= effective_now for r in reservations
            ):
                raise PaymentProofExpiredError(
                    "Una reserva vencida no puede aprobarse."
                )
            if any(r.status != ReservationStatus.ACTIVE for r in reservations):
                raise InvalidPaymentProofTransitionError(
                    "Todas las reservas deben estar activas para aprobar."
                )
            for reservation in reservations:
                consume_inventory_reservation(
                    session=session, reservation_id=reservation.id
                )
            attempt.status = PaymentStatus.APPROVED
            attempt.approved_at = effective_now
            order.status = OrderStatus.CONFIRMED
            for seller_order in seller_orders:
                seller_order.status = SellerOrderStatus.CONFIRMED
        else:
            if any(r.status == ReservationStatus.CONSUMED for r in reservations):
                raise InvalidPaymentProofTransitionError(
                    "No puede rechazarse un pedido con reservas consumidas."
                )
            for reservation in reservations:
                if reservation.status == ReservationStatus.ACTIVE:
                    release_inventory_reservation(
                        session=session,
                        reservation_id=reservation.id,
                        actor_user_id=reviewer.id,
                        notes="Reserva liberada por rechazo del comprobante.",
                    )
            attempt.status = PaymentStatus.REJECTED
            attempt.rejected_at = effective_now
            order.status = OrderStatus.CANCELLED
            for seller_order in seller_orders:
                seller_order.status = SellerOrderStatus.CANCELLED
    except InventoryServiceError as exc:
        raise PaymentProofIntegrityError(str(exc)) from exc

    proof.status = target
    proof.reviewed_by_user_id = reviewer.id
    proof.reviewed_at = effective_now
    proof.rejection_reason = normalized_reason if target == PaymentProofStatus.REJECTED else None
    proof.review_notes = normalized_notes
    session.flush()
    return ReviewPaymentProofResult(
        proof.id, order.order_number, proof.status, attempt.status,
        order.status, len(reservations), False
    )
