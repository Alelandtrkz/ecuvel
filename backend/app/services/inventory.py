from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
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

class SameInventoryLocationError(InventoryServiceError):
    """El origen y el destino son la misma ubicación."""


class InvalidPutawayLocationError(InventoryServiceError):
    """Las ubicaciones no permiten realizar el putaway."""


class InsufficientMovableStockError(InventoryServiceError):
    """No existe suficiente stock libre para trasladar."""


class DestinationCapacityExceededError(InventoryServiceError):
    """La ubicación de destino no tiene capacidad suficiente."""


class DestinationMixingNotAllowedError(InventoryServiceError):
    """La ubicación no permite mezclar ofertas diferentes."""


@dataclass(frozen=True, slots=True)
class InventoryReceiptResult:
    balance_id: uuid.UUID
    movement_id: uuid.UUID
    on_hand_quantity: int
    reserved_quantity: int
    blocked_quantity: int
    available_quantity: int
    replayed: bool

@dataclass(frozen=True, slots=True)
class InventoryPutawayResult:
    source_balance_id: uuid.UUID
    destination_balance_id: uuid.UUID
    move_out_movement_id: uuid.UUID
    move_in_movement_id: uuid.UUID
    quantity: int
    source_on_hand_quantity: int
    source_available_quantity: int
    destination_on_hand_quantity: int
    destination_available_quantity: int
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

def _putaway_result(
    *,
    source_balance: InventoryBalance,
    destination_balance: InventoryBalance,
    move_out: InventoryMovement,
    move_in: InventoryMovement,
    quantity: int,
    replayed: bool,
) -> InventoryPutawayResult:
    return InventoryPutawayResult(
        source_balance_id=source_balance.id,
        destination_balance_id=destination_balance.id,
        move_out_movement_id=move_out.id,
        move_in_movement_id=move_in.id,
        quantity=quantity,
        source_on_hand_quantity=source_balance.on_hand_quantity,
        source_available_quantity=source_balance.available_quantity,
        destination_on_hand_quantity=(
            destination_balance.on_hand_quantity
        ),
        destination_available_quantity=(
            destination_balance.available_quantity
        ),
        replayed=replayed,
    )


def putaway_inventory(
    *,
    session: Session,
    offer_id: uuid.UUID,
    source_location_id: uuid.UUID,
    destination_location_id: uuid.UUID,
    quantity: int,
    reference_type: str,
    reference_id: uuid.UUID,
    idempotency_key: str,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryPutawayResult:
    """
    Mueve inventario desde recepción hacia almacenamiento.

    Genera dos movimientos inseparables:

    - MOVE_OUT en la ubicación de origen.
    - MOVE_IN en la ubicación de destino.

    El llamador debe abrir la transacción.
    """

    if quantity <= 0:
        raise InvalidInventoryQuantityError(
            "La cantidad trasladada debe ser mayor que cero."
        )

    if source_location_id == destination_location_id:
        raise SameInventoryLocationError(
            "La ubicación de origen y destino no pueden ser iguales."
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

    # Reservamos espacio para los sufijos :out y :in.
    if len(normalized_idempotency_key) > 146:
        raise ValueError(
            "idempotency_key no puede superar 146 caracteres."
        )

    if notes is not None and len(notes) > 500:
        raise ValueError("notes no puede superar 500 caracteres.")

    move_out_key = f"{normalized_idempotency_key}:out"
    move_in_key = f"{normalized_idempotency_key}:in"

    offer = session.get(SellerOffer, offer_id)

    if offer is None:
        raise SellerOfferNotFoundError(
            f"No existe la oferta {offer_id}."
        )

    # Se bloquean siempre en orden estable para reducir el riesgo
    # de interbloqueos entre operaciones concurrentes.
    locked_locations = list(
        session.scalars(
            select(WarehouseLocation)
            .where(
                WarehouseLocation.id.in_(
                    [
                        source_location_id,
                        destination_location_id,
                    ]
                )
            )
            .order_by(WarehouseLocation.id)
            .with_for_update()
        )
    )

    locations_by_id = {
        location.id: location
        for location in locked_locations
    }

    source_location = locations_by_id.get(source_location_id)
    destination_location = locations_by_id.get(
        destination_location_id
    )

    if source_location is None:
        raise WarehouseLocationNotFoundError(
            f"No existe la ubicación de origen "
            f"{source_location_id}."
        )

    if destination_location is None:
        raise WarehouseLocationNotFoundError(
            f"No existe la ubicación de destino "
            f"{destination_location_id}."
        )

    if not source_location.is_active:
        raise InvalidPutawayLocationError(
            "La ubicación de origen está desactivada."
        )

    if not destination_location.is_active:
        raise InvalidPutawayLocationError(
            "La ubicación de destino está desactivada."
        )

    if source_location.location_type != LocationType.RECEIVING:
        raise InvalidPutawayLocationError(
            "El origen del putaway debe ser una ubicación "
            "de tipo RECEIVING."
        )

    if destination_location.location_type not in {
        LocationType.STORAGE,
        LocationType.PICKING,
    }:
        raise InvalidPutawayLocationError(
            "El destino debe ser una ubicación de tipo "
            "STORAGE o PICKING."
        )

    if (
        source_location.warehouse_id
        != destination_location.warehouse_id
    ):
        raise InvalidPutawayLocationError(
            "El putaway solo puede realizarse dentro "
            "del mismo almacén."
        )

    # Comprobación de idempotencia después de bloquear
    # las ubicaciones.
    existing_movements = list(
        session.scalars(
            select(InventoryMovement).where(
                InventoryMovement.idempotency_key.in_(
                    [move_out_key, move_in_key]
                )
            )
        )
    )

    if existing_movements:
        movements_by_key = {
            movement.idempotency_key: movement
            for movement in existing_movements
        }

        move_out = movements_by_key.get(move_out_key)
        move_in = movements_by_key.get(move_in_key)

        if move_out is None or move_in is None:
            raise InventoryServiceError(
                "La operación idempotente está incompleta: "
                "falta MOVE_OUT o MOVE_IN."
            )

        source_balance = session.get(
            InventoryBalance,
            move_out.balance_id,
        )
        destination_balance = session.get(
            InventoryBalance,
            move_in.balance_id,
        )

        if source_balance is None or destination_balance is None:
            raise InventoryServiceError(
                "Los movimientos existentes no tienen "
                "saldos válidos."
            )

        represents_same_operation = all(
            (
                move_out.movement_type
                == InventoryMovementType.MOVE_OUT,
                move_in.movement_type
                == InventoryMovementType.MOVE_IN,
                move_out.delta_on_hand == -quantity,
                move_in.delta_on_hand == quantity,
                move_out.delta_reserved == 0,
                move_in.delta_reserved == 0,
                move_out.delta_blocked == 0,
                move_in.delta_blocked == 0,
                move_out.reference_type
                == normalized_reference_type,
                move_in.reference_type
                == normalized_reference_type,
                move_out.reference_id == reference_id,
                move_in.reference_id == reference_id,
                source_balance.offer_id == offer_id,
                destination_balance.offer_id == offer_id,
                source_balance.location_id
                == source_location_id,
                destination_balance.location_id
                == destination_location_id,
            )
        )

        if not represents_same_operation:
            raise IdempotencyConflictError(
                "La clave de idempotencia ya pertenece "
                "a otro putaway."
            )

        return _putaway_result(
            source_balance=source_balance,
            destination_balance=destination_balance,
            move_out=move_out,
            move_in=move_in,
            quantity=quantity,
            replayed=True,
        )

    source_balance = session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.offer_id == offer_id,
            InventoryBalance.location_id
            == source_location_id,
        )
        .with_for_update()
    )

    if source_balance is None:
        raise InsufficientMovableStockError(
            "No existe inventario de la oferta "
            "en la ubicación de origen."
        )

    if source_balance.available_quantity < quantity:
        raise InsufficientMovableStockError(
            "Stock libre insuficiente para realizar "
            f"el putaway. Disponible: "
            f"{source_balance.available_quantity}; "
            f"solicitado: {quantity}."
        )

    destination_balance = session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.offer_id == offer_id,
            InventoryBalance.location_id
            == destination_location_id,
        )
        .with_for_update()
    )

    if not destination_location.allows_mixed_offers:
        conflicting_balance_id = session.scalar(
            select(InventoryBalance.id)
            .where(
                InventoryBalance.location_id
                == destination_location_id,
                InventoryBalance.offer_id != offer_id,
                InventoryBalance.on_hand_quantity > 0,
            )
            .limit(1)
        )

        if conflicting_balance_id is not None:
            raise DestinationMixingNotAllowedError(
                "La ubicación de destino no permite "
                "mezclar ofertas diferentes."
            )

    if destination_location.capacity_units is not None:
        current_destination_units = session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        InventoryBalance.on_hand_quantity
                    ),
                    0,
                )
            ).where(
                InventoryBalance.location_id
                == destination_location_id
            )
        )

        if (
            current_destination_units + quantity
            > destination_location.capacity_units
        ):
            raise DestinationCapacityExceededError(
                "La cantidad supera la capacidad "
                "de la ubicación de destino."
            )

    if destination_balance is None:
        destination_balance = InventoryBalance(
            offer_id=offer_id,
            location_id=destination_location_id,
            on_hand_quantity=0,
            reserved_quantity=0,
            blocked_quantity=0,
        )
        session.add(destination_balance)
        session.flush()

    source_balance.on_hand_quantity -= quantity
    destination_balance.on_hand_quantity += quantity

    move_out = InventoryMovement(
        balance_id=source_balance.id,
        movement_type=InventoryMovementType.MOVE_OUT,
        delta_on_hand=-quantity,
        delta_reserved=0,
        delta_blocked=0,
        reference_type=normalized_reference_type,
        reference_id=reference_id,
        idempotency_key=move_out_key,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    move_in = InventoryMovement(
        balance_id=destination_balance.id,
        movement_type=InventoryMovementType.MOVE_IN,
        delta_on_hand=quantity,
        delta_reserved=0,
        delta_blocked=0,
        reference_type=normalized_reference_type,
        reference_id=reference_id,
        idempotency_key=move_in_key,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    session.add_all([move_out, move_in])
    session.flush()

    return _putaway_result(
        source_balance=source_balance,
        destination_balance=destination_balance,
        move_out=move_out,
        move_in=move_in,
        quantity=quantity,
        replayed=False,
    )