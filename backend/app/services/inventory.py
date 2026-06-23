from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import SellerOffer
from app.models.enums import InventoryMovementType, LocationType
from app.models.inventory import InventoryBalance, InventoryMovement
from app.models.warehouse import WarehouseLocation


class InventoryServiceError(Exception):
    """Error base de los servicios de inventario."""


class InvalidInventoryQuantityError(InventoryServiceError):
    """La cantidad indicada no es válida."""


class WarehouseLocationNotFoundError(InventoryServiceError):
    """No se encontró la ubicación del almacén."""


class SellerOfferNotFoundError(InventoryServiceError):
    """No se encontró la oferta del vendedor."""


class InvalidReceivingLocationError(InventoryServiceError):
    """La ubicación no permite recibir mercancía."""


class IdempotencyConflictError(InventoryServiceError):
    """La clave ya fue utilizada para una operación diferente."""


@dataclass(frozen=True, slots=True)
class InventoryReceiptResult:
    balance_id: uuid.UUID
    movement_id: uuid.UUID
    on_hand_quantity: int
    reserved_quantity: int
    blocked_quantity: int
    available_quantity: int
    replayed: bool


def _receipt_result(
    *,
    balance: InventoryBalance,
    movement: InventoryMovement,
    replayed: bool,
) -> InventoryReceiptResult:
    return InventoryReceiptResult(
        balance_id=balance.id,
        movement_id=movement.id,
        on_hand_quantity=balance.on_hand_quantity,
        reserved_quantity=balance.reserved_quantity,
        blocked_quantity=balance.blocked_quantity,
        available_quantity=balance.available_quantity,
        replayed=replayed,
    )


def receive_inventory(
    *,
    session: Session,
    offer_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity: int,
    reference_type: str,
    reference_id: uuid.UUID,
    idempotency_key: str,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryReceiptResult:
    """
    Registra mercancía recibida en una ubicación de recepción.

    La función supone que el llamador ya abrió una transacción.
    No ejecuta commit ni rollback directamente.
    """

    if quantity <= 0:
        raise InvalidInventoryQuantityError(
            "La cantidad recibida debe ser mayor que cero."
        )

    normalized_reference_type = reference_type.strip().upper()
    normalized_idempotency_key = idempotency_key.strip()

    if not normalized_reference_type:
        raise ValueError("reference_type es obligatorio.")

    if len(normalized_reference_type) > 50:
        raise ValueError(
            "reference_type no puede superar 50 caracteres."
        )

    if not normalized_idempotency_key:
        raise ValueError("idempotency_key es obligatoria.")

    if len(normalized_idempotency_key) > 150:
        raise ValueError(
            "idempotency_key no puede superar 150 caracteres."
        )

    if notes is not None and len(notes) > 500:
        raise ValueError("notes no puede superar 500 caracteres.")

    # Bloqueamos la ubicación para serializar recepciones concurrentes.
    location = session.scalar(
        select(WarehouseLocation)
        .where(WarehouseLocation.id == location_id)
        .with_for_update()
    )

    if location is None:
        raise WarehouseLocationNotFoundError(
            f"No existe la ubicación {location_id}."
        )

    if not location.is_active:
        raise InvalidReceivingLocationError(
            "La ubicación de recepción está desactivada."
        )

    if location.location_type != LocationType.RECEIVING:
        raise InvalidReceivingLocationError(
            "La mercancía solo puede recibirse inicialmente "
            "en una ubicación de tipo RECEIVING."
        )

    offer = session.get(SellerOffer, offer_id)

    if offer is None:
        raise SellerOfferNotFoundError(
            f"No existe la oferta {offer_id}."
        )

    # Si la operación ya existe, no volvemos a sumar inventario.
    existing_movement = session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.idempotency_key
            == normalized_idempotency_key
        )
    )

    if existing_movement is not None:
        existing_balance = session.get(
            InventoryBalance,
            existing_movement.balance_id,
        )

        if existing_balance is None:
            raise InventoryServiceError(
                "El movimiento existente no tiene un saldo válido."
            )

        represents_same_operation = all(
            (
                existing_movement.movement_type
                == InventoryMovementType.RECEIVE,
                existing_movement.delta_on_hand == quantity,
                existing_movement.delta_reserved == 0,
                existing_movement.delta_blocked == 0,
                existing_movement.reference_type
                == normalized_reference_type,
                existing_movement.reference_id == reference_id,
                existing_balance.offer_id == offer_id,
                existing_balance.location_id == location_id,
            )
        )

        if not represents_same_operation:
            raise IdempotencyConflictError(
                "La clave de idempotencia ya pertenece "
                "a una operación diferente."
            )

        return _receipt_result(
            balance=existing_balance,
            movement=existing_movement,
            replayed=True,
        )

    balance = session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.offer_id == offer_id,
            InventoryBalance.location_id == location_id,
        )
        .with_for_update()
    )

    if balance is None:
        balance = InventoryBalance(
            offer_id=offer_id,
            location_id=location_id,
            on_hand_quantity=0,
            reserved_quantity=0,
            blocked_quantity=0,
        )
        session.add(balance)
        session.flush()

    balance.on_hand_quantity += quantity

    movement = InventoryMovement(
        balance_id=balance.id,
        movement_type=InventoryMovementType.RECEIVE,
        delta_on_hand=quantity,
        delta_reserved=0,
        delta_blocked=0,
        reference_type=normalized_reference_type,
        reference_id=reference_id,
        idempotency_key=normalized_idempotency_key,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    session.add(movement)

    # Ejecuta los INSERT/UPDATE y valida las restricciones,
    # pero todavía no confirma la transacción.
    session.flush()

    return _receipt_result(
        balance=balance,
        movement=movement,
        replayed=False,
    )