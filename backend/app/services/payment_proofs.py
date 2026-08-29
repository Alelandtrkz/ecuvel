from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AdminAuditEvent,
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    PaymentNotificationOutbox,
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
    SellerOrderDecisionStatus,
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
from app.services.admin_permissions import user_has_permission
from app.services.seller_order_logistics import build_seller_order_delivery_window


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


PAYMENT_REJECTION_REASON_LABELS = {
    "AMOUNT_MISMATCH": "Monto incorrecto",
    "DESTINATION_ACCOUNT_MISMATCH": "Cuenta destino incorrecta",
    "DUPLICATE_PROOF": "Comprobante duplicado",
    "UNREADABLE_PROOF": "Comprobante ilegible",
    "INVALID_DATE": "Fecha inválida",
    "UNVERIFIABLE_TRANSACTION": "Transacción no verificable",
    "INVALID_DOCUMENT": "Documento incorrecto",
    "OTHER": "Otro",
}
PAYMENT_REJECTION_REASON_CODES = frozenset(PAYMENT_REJECTION_REASON_LABELS)

# Messages shown to the buyer.  Keep these deliberately separate from the
# operator labels above so internal classification language never leaks into
# customer-facing payment state.
PAYMENT_REJECTION_PUBLIC_REASONS = {
    "AMOUNT_MISMATCH": (
        "El monto del comprobante no coincide con el total pendiente del pedido."
    ),
    "DESTINATION_ACCOUNT_MISMATCH": (
        "La cuenta destino del comprobante no coincide con la cuenta indicada "
        "por ECUVEL."
    ),
    "DUPLICATE_PROOF": (
        "El comprobante ya fue utilizado o coincide con otro comprobante "
        "registrado."
    ),
    "UNREADABLE_PROOF": (
        "No fue posible verificar la información del comprobante porque el "
        "documento no es legible."
    ),
    "INVALID_DATE": (
        "La fecha del comprobante no corresponde al período esperado para este pago."
    ),
    "UNVERIFIABLE_TRANSACTION": (
        "No fue posible verificar la transacción con la información enviada."
    ),
    "INVALID_DOCUMENT": (
        "El archivo enviado no corresponde a un comprobante de pago válido."
    ),
}


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
    reason_code: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> ReviewPaymentProofResult:
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        raise PaymentProofServiceError("La decisión debe ser approve o reject.")
    normalized_reason = " ".join((reason or "").split()) or None
    normalized_notes = " ".join((notes or "").split()) or None
    normalized_reason_code = (reason_code or "").strip().upper() or None
    if normalized_decision == "reject" and not normalized_reason:
        raise PaymentProofServiceError("El rechazo requiere una razón.")
    if (
        normalized_decision == "reject"
        and normalized_reason_code not in PAYMENT_REJECTION_REASON_CODES
    ):
        raise PaymentProofServiceError("Selecciona un motivo de rechazo válido.")
    if normalized_decision == "approve":
        normalized_reason_code = None
    if normalized_reason and len(normalized_reason) > 500:
        raise PaymentProofServiceError("La razón no puede superar 500 caracteres.")
    if normalized_notes and len(normalized_notes) > 1000:
        raise PaymentProofServiceError("Las notas no pueden superar 1000 caracteres.")

    proof = session.scalar(
        select(PaymentProof).where(PaymentProof.id == proof_id).with_for_update()
    )
    if proof is None:
        raise PaymentProofNotFoundError("No existe el comprobante indicado.")
    reviewer = session.scalar(
        select(User).where(User.id == reviewer_user_id).with_for_update()
    )
    if (
        reviewer is None
        or reviewer.status != UserStatus.ACTIVE
        or not user_has_permission(reviewer, "payments.review")
    ):
        raise PaymentProofServiceError(
            "El revisor no está activo o no tiene permiso para revisar pagos."
        )
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
    attempt = session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == proof.payment_attempt_id)
        .with_for_update()
    )
    if attempt is None or attempt.status != PaymentStatus.PROCESSING:
        raise PaymentProofIntegrityError("El pago no está en revisión.")
    old_payment_status = attempt.status.value
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
                window = build_seller_order_delivery_window(effective_now)
                seller_order.decision_status = SellerOrderDecisionStatus.PENDING
                seller_order.decision_available_at = window.decision_available_at
                seller_order.ship_by_at = window.ship_by_at
                seller_order.estimated_delivery_from = window.estimated_delivery_from
                seller_order.estimated_delivery_to = window.estimated_delivery_to
                seller_order.approved_at = None
                seller_order.approved_by_user_id = None
                seller_order.rejected_at = None
                seller_order.rejected_by_user_id = None
                seller_order.rejection_reason = None
                seller_order.rejection_comment = None
                seller_order.requires_refund_resolution = False
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
                seller_order.decision_status = None
                seller_order.decision_available_at = None
                seller_order.ship_by_at = None
                seller_order.estimated_delivery_from = None
                seller_order.estimated_delivery_to = None
    except InventoryServiceError as exc:
        raise PaymentProofIntegrityError(str(exc)) from exc

    proof.status = target
    proof.reviewed_by_user_id = reviewer.id
    proof.reviewed_at = effective_now
    proof.rejection_reason = normalized_reason if target == PaymentProofStatus.REJECTED else None
    proof.rejection_reason_code = (
        normalized_reason_code if target == PaymentProofStatus.REJECTED else None
    )
    proof.review_notes = normalized_notes
    audit_metadata = {
        "payment_attempt_id": str(attempt.id),
        "payment_public_code": attempt.public_code,
        "proof_id": str(proof.id),
        "order_id": str(order.id),
        "order_number": order.order_number,
        "old_status": old_payment_status,
        "new_status": attempt.status.value,
    }
    if target == PaymentProofStatus.REJECTED:
        audit_metadata["reason_code"] = normalized_reason_code
    session.add(
        AdminAuditEvent(
            actor_user_id=reviewer.id,
            action=(
                "PAYMENT_APPROVED"
                if target == PaymentProofStatus.APPROVED
                else "PAYMENT_REJECTED"
            ),
            metadata_json=audit_metadata,
        )
    )
    session.add(
        PaymentNotificationOutbox(
            payment_attempt_id=attempt.id,
            order_id=order.id,
            user_id=order.buyer_id,
            event_type=(
                "PAYMENT_APPROVED"
                if target == PaymentProofStatus.APPROVED
                else "PAYMENT_REJECTED"
            ),
        )
    )
    session.flush()
    return ReviewPaymentProofResult(
        proof.id, order.order_number, proof.status, attempt.status,
        order.status, len(reservations), False
    )
