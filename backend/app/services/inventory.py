from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.catalog import SellerOffer
from app.models.enums import (
    InventoryMovementType,
    LocationType,
    ReservationStatus,
)
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
)
from app.models.order import OrderItem
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

class OrderItemNotFoundError(InventoryServiceError):
    """No se encontró el artículo del pedido."""


class InvalidReservationExpiryError(InventoryServiceError):
    """La fecha de expiración de la reserva no es válida."""


class InsufficientSellableStockError(InventoryServiceError):
    """No existe suficiente inventario vendible."""


class ActiveReservationConflictError(InventoryServiceError):
    """El artículo ya tiene una reserva activa."""

class InventoryReservationNotFoundError(InventoryServiceError):
    """No se encontró la reserva solicitada."""


class InvalidReservationTransitionError(InventoryServiceError):
    """La reserva no permite la transición solicitada."""


class ReservationBalanceIntegrityError(InventoryServiceError):
    """El saldo reservado no coincide con la reserva."""

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

@dataclass(frozen=True, slots=True)
class ReservationAllocation:
    reservation_id: uuid.UUID
    balance_id: uuid.UUID
    location_id: uuid.UUID
    location_code: str
    quantity: int


@dataclass(frozen=True, slots=True)
class InventoryReservationResult:
    order_item_id: uuid.UUID
    offer_id: uuid.UUID
    total_reserved: int
    expires_at: datetime
    allocations: tuple[ReservationAllocation, ...]
    replayed: bool

@dataclass(frozen=True, slots=True)
class ReservationTransitionResult:
    reservation_id: uuid.UUID
    order_item_id: uuid.UUID
    balance_id: uuid.UUID
    quantity: int
    status: ReservationStatus
    balance_reserved_quantity: int
    available_quantity: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ExpirationBatchResult:
    expired_count: int
    reservations: tuple[ReservationTransitionResult, ...]


@dataclass(frozen=True, slots=True)
class InventoryPickResult:
    reservation_id: uuid.UUID
    order_item_id: uuid.UUID
    balance_id: uuid.UUID
    location_id: uuid.UUID
    location_code: str
    quantity: int
    movement_id: uuid.UUID
    on_hand_quantity: int
    reserved_quantity: int
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

def _reservation_key_prefix(
    idempotency_key: str,
) -> str:
    digest = hashlib.sha256(
        idempotency_key.encode("utf-8")
    ).hexdigest()[:32]

    return f"reserve:{digest}:"


def reserve_inventory(
    *,
    session: Session,
    order_item_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    expires_at: datetime,
    idempotency_key: str,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryReservationResult:
    """
    Reserva la cantidad completa de un OrderItem.

    Solo utiliza inventario de ubicaciones STORAGE o PICKING.
    El llamador debe abrir la transacción.
    """

    normalized_key = idempotency_key.strip()

    if not normalized_key:
        raise ValueError("idempotency_key es obligatoria.")

    if len(normalized_key) > 150:
        raise ValueError(
            "idempotency_key no puede superar 150 caracteres."
        )

    if notes is not None and len(notes) > 500:
        raise ValueError("notes no puede superar 500 caracteres.")

    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise InvalidReservationExpiryError(
            "expires_at debe incluir zona horaria."
        )

    now = datetime.now(expires_at.tzinfo)

    if expires_at <= now:
        raise InvalidReservationExpiryError(
            "La reserva debe expirar en una fecha futura."
        )

    # El bloqueo del artículo serializa reservas concurrentes
    # para la misma línea del pedido.
    order_item = session.scalar(
        select(OrderItem)
        .where(OrderItem.id == order_item_id)
        .with_for_update()
    )

    if order_item is None:
        raise OrderItemNotFoundError(
            f"No existe el artículo {order_item_id}."
        )

    required_quantity = order_item.quantity
    key_prefix = _reservation_key_prefix(normalized_key)

    # Primero comprobamos si esta operación ya fue procesada.
    existing_movements = list(
        session.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.idempotency_key.like(
                    f"{key_prefix}%"
                )
            )
            .order_by(
                InventoryMovement.idempotency_key
            )
        )
    )

    if existing_movements:
        movement_balance_ids = [
            movement.balance_id
            for movement in existing_movements
        ]

        balance_rows = session.execute(
            select(
                InventoryBalance,
                WarehouseLocation,
            )
            .join(
                WarehouseLocation,
                WarehouseLocation.id
                == InventoryBalance.location_id,
            )
            .where(
                InventoryBalance.id.in_(
                    movement_balance_ids
                )
            )
        ).all()

        balances_by_id = {
            balance.id: (balance, location)
            for balance, location in balance_rows
        }

        active_reservations = list(
            session.scalars(
                select(InventoryReservation)
                .where(
                    InventoryReservation.order_item_id
                    == order_item.id,
                    InventoryReservation.balance_id.in_(
                        movement_balance_ids
                    ),
                    InventoryReservation.status
                    == ReservationStatus.ACTIVE,
                )
                .with_for_update()
            )
        )

        reservations_by_balance = {
            reservation.balance_id: reservation
            for reservation in active_reservations
        }

        allocations: list[ReservationAllocation] = []
        total_reserved = 0

        for movement in existing_movements:
            balance_row = balances_by_id.get(
                movement.balance_id
            )
            reservation = reservations_by_balance.get(
                movement.balance_id
            )

            if balance_row is None or reservation is None:
                raise IdempotencyConflictError(
                    "La operación ya existe, pero su reserva "
                    "ya no está activa o está incompleta."
                )

            balance, location = balance_row

            represents_same_operation = all(
                (
                    movement.movement_type
                    == InventoryMovementType.RESERVE,
                    movement.delta_on_hand == 0,
                    movement.delta_reserved
                    == reservation.quantity,
                    movement.delta_blocked == 0,
                    movement.reference_type
                    == "ORDER_ITEM",
                    movement.reference_id
                    == order_item.id,
                    balance.offer_id
                    == order_item.offer_id,
                    location.warehouse_id
                    == warehouse_id,
                    location.location_type
                    in {
                        LocationType.STORAGE,
                        LocationType.PICKING,
                    },
                )
            )

            if not represents_same_operation:
                raise IdempotencyConflictError(
                    "La clave de idempotencia pertenece "
                    "a una operación diferente."
                )

            total_reserved += reservation.quantity

            allocations.append(
                ReservationAllocation(
                    reservation_id=reservation.id,
                    balance_id=balance.id,
                    location_id=location.id,
                    location_code=location.code,
                    quantity=reservation.quantity,
                )
            )

        if total_reserved != required_quantity:
            raise IdempotencyConflictError(
                "La operación existente no coincide con "
                "la cantidad del artículo."
            )

        reservation_expiry = min(
            reservation.expires_at
            for reservation in active_reservations
        )

        return InventoryReservationResult(
            order_item_id=order_item.id,
            offer_id=order_item.offer_id,
            total_reserved=total_reserved,
            expires_at=reservation_expiry,
            allocations=tuple(allocations),
            replayed=True,
        )

    # Un OrderItem solo puede tener un conjunto activo
    # de reservas.
    existing_active = list(
        session.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.order_item_id
                == order_item.id,
                InventoryReservation.status
                == ReservationStatus.ACTIVE,
            )
            .with_for_update()
        )
    )

    if existing_active:
        raise ActiveReservationConflictError(
            "El artículo ya tiene una reserva activa "
            "creada por otra operación."
        )

    # Solo consultamos ubicaciones vendibles.
    candidate_rows = session.execute(
        select(
            InventoryBalance,
            WarehouseLocation,
        )
        .join(
            WarehouseLocation,
            WarehouseLocation.id
            == InventoryBalance.location_id,
        )
        .where(
            InventoryBalance.offer_id
            == order_item.offer_id,
            WarehouseLocation.warehouse_id
            == warehouse_id,
            WarehouseLocation.is_active.is_(True),
            WarehouseLocation.location_type.in_(
                [
                    LocationType.PICKING,
                    LocationType.STORAGE,
                ]
            ),
            (
                InventoryBalance.on_hand_quantity
                - InventoryBalance.reserved_quantity
                - InventoryBalance.blocked_quantity
            )
            > 0,
        )
        .order_by(
            WarehouseLocation.code,
            InventoryBalance.id,
        )
        .with_for_update(of=InventoryBalance)
    ).all()

    total_available = sum(
        balance.available_quantity
        for balance, _location in candidate_rows
    )

    if total_available < required_quantity:
        raise InsufficientSellableStockError(
            "Stock vendible insuficiente. "
            f"Disponible: {total_available}; "
            f"solicitado: {required_quantity}."
        )

    remaining = required_quantity
    allocations: list[ReservationAllocation] = []

    for balance, location in candidate_rows:
        if remaining == 0:
            break

        available = balance.available_quantity

        if available <= 0:
            continue

        allocation_quantity = min(
            available,
            remaining,
        )

        balance.reserved_quantity += allocation_quantity

        reservation = InventoryReservation(
            order_item_id=order_item.id,
            balance_id=balance.id,
            quantity=allocation_quantity,
            status=ReservationStatus.ACTIVE,
            expires_at=expires_at,
        )

        movement = InventoryMovement(
            balance_id=balance.id,
            movement_type=InventoryMovementType.RESERVE,
            delta_on_hand=0,
            delta_reserved=allocation_quantity,
            delta_blocked=0,
            reference_type="ORDER_ITEM",
            reference_id=order_item.id,
            idempotency_key=(
                f"{key_prefix}{balance.id.hex}"
            ),
            actor_user_id=actor_user_id,
            notes=notes,
        )

        session.add(reservation)
        session.add(movement)
        session.flush()

        allocations.append(
            ReservationAllocation(
                reservation_id=reservation.id,
                balance_id=balance.id,
                location_id=location.id,
                location_code=location.code,
                quantity=allocation_quantity,
            )
        )

        remaining -= allocation_quantity

    if remaining != 0:
        # Esta condición no debería ocurrir después de calcular
        # total_available, pero se conserva como defensa.
        raise InsufficientSellableStockError(
            "No fue posible completar la reserva."
        )

    session.flush()

    return InventoryReservationResult(
        order_item_id=order_item.id,
        offer_id=order_item.offer_id,
        total_reserved=required_quantity,
        expires_at=expires_at,
        allocations=tuple(allocations),
        replayed=False,
    )

def _reservation_transition_result(
    *,
    reservation: InventoryReservation,
    balance: InventoryBalance,
    replayed: bool,
) -> ReservationTransitionResult:
    return ReservationTransitionResult(
        reservation_id=reservation.id,
        order_item_id=reservation.order_item_id,
        balance_id=balance.id,
        quantity=reservation.quantity,
        status=reservation.status,
        balance_reserved_quantity=balance.reserved_quantity,
        available_quantity=balance.available_quantity,
        replayed=replayed,
    )


def _lock_reservation(
    *,
    session: Session,
    reservation_id: uuid.UUID,
) -> tuple[InventoryReservation, InventoryBalance]:
    reservation = session.scalar(
        select(InventoryReservation)
        .where(InventoryReservation.id == reservation_id)
        .with_for_update()
    )

    if reservation is None:
        raise InventoryReservationNotFoundError(
            f"No existe la reserva {reservation_id}."
        )

    balance = session.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.id == reservation.balance_id
        )
        .with_for_update()
    )

    if balance is None:
        raise ReservationBalanceIntegrityError(
            "La reserva no tiene un saldo de inventario válido."
        )

    return reservation, balance


def release_inventory_reservation(
    *,
    session: Session,
    reservation_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> ReservationTransitionResult:
    """
    Libera una reserva temporal.

    Se utiliza cuando el pago falla, el cliente cancela
    o el checkout es abandonado explícitamente.
    """

    if notes is not None and len(notes) > 500:
        raise ValueError("notes no puede superar 500 caracteres.")

    reservation, balance = _lock_reservation(
        session=session,
        reservation_id=reservation_id,
    )

    if reservation.status in {
        ReservationStatus.RELEASED,
        ReservationStatus.EXPIRED,
    }:
        return _reservation_transition_result(
            reservation=reservation,
            balance=balance,
            replayed=True,
        )

    if reservation.status == ReservationStatus.CONSUMED:
        raise InvalidReservationTransitionError(
            "Una reserva confirmada por pago no puede "
            "liberarse mediante esta operación."
        )

    if reservation.status != ReservationStatus.ACTIVE:
        raise InvalidReservationTransitionError(
            f"No se puede liberar una reserva en estado "
            f"{reservation.status.value}."
        )

    if balance.reserved_quantity < reservation.quantity:
        raise ReservationBalanceIntegrityError(
            "El saldo reservado es menor que la cantidad "
            "registrada en la reserva."
        )

    movement_key = f"release:{reservation.id.hex}"

    existing_movement = session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.idempotency_key
            == movement_key
        )
    )

    if existing_movement is not None:
        raise ReservationBalanceIntegrityError(
            "Existe un movimiento de liberación, pero "
            "la reserva continúa activa."
        )

    balance.reserved_quantity -= reservation.quantity

    reservation.status = ReservationStatus.RELEASED
    reservation.released_at = datetime.now(timezone.utc)

    movement = InventoryMovement(
        balance_id=balance.id,
        movement_type=InventoryMovementType.RELEASE,
        delta_on_hand=0,
        delta_reserved=-reservation.quantity,
        delta_blocked=0,
        reference_type="INVENTORY_RESERVATION",
        reference_id=reservation.id,
        idempotency_key=movement_key,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    session.add(movement)
    session.flush()

    return _reservation_transition_result(
        reservation=reservation,
        balance=balance,
        replayed=False,
    )


def consume_inventory_reservation(
    *,
    session: Session,
    reservation_id: uuid.UUID,
) -> ReservationTransitionResult:
    """
    Confirma la reserva cuando el pago ha sido aprobado.

    No libera las unidades y no disminuye on_hand.
    Las cantidades se mantienen reservadas hasta el picking.
    """

    reservation, balance = _lock_reservation(
        session=session,
        reservation_id=reservation_id,
    )

    if reservation.status == ReservationStatus.CONSUMED:
        return _reservation_transition_result(
            reservation=reservation,
            balance=balance,
            replayed=True,
        )

    if reservation.status in {
        ReservationStatus.RELEASED,
        ReservationStatus.EXPIRED,
    }:
        raise InvalidReservationTransitionError(
            "No puede confirmarse una reserva liberada "
            "o expirada."
        )

    if reservation.status != ReservationStatus.ACTIVE:
        raise InvalidReservationTransitionError(
            f"No se puede consumir una reserva en estado "
            f"{reservation.status.value}."
        )

    if balance.reserved_quantity < reservation.quantity:
        raise ReservationBalanceIntegrityError(
            "El saldo reservado es menor que la cantidad "
            "registrada en la reserva."
        )

    reservation.status = ReservationStatus.CONSUMED
    reservation.consumed_at = datetime.now(timezone.utc)

    session.flush()

    return _reservation_transition_result(
        reservation=reservation,
        balance=balance,
        replayed=False,
    )


def expire_inventory_reservations(
    *,
    session: Session,
    now: datetime | None = None,
    batch_size: int = 100,
) -> ExpirationBatchResult:
    """
    Libera reservas ACTIVE cuyo plazo haya vencido.

    Procesa un lote limitado para que posteriormente pueda
    ejecutarse periódicamente mediante Celery.
    """

    effective_now = now or datetime.now(timezone.utc)

    if (
        effective_now.tzinfo is None
        or effective_now.utcoffset() is None
    ):
        raise InvalidReservationExpiryError(
            "now debe incluir zona horaria."
        )

    if batch_size <= 0 or batch_size > 1000:
        raise ValueError(
            "batch_size debe estar entre 1 y 1000."
        )

    reservations = list(
        session.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.status
                == ReservationStatus.ACTIVE,
                InventoryReservation.expires_at
                <= effective_now,
            )
            .order_by(
                InventoryReservation.balance_id,
                InventoryReservation.expires_at,
                InventoryReservation.id,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )

    results: list[ReservationTransitionResult] = []

    for reservation in reservations:
        balance = session.scalar(
            select(InventoryBalance)
            .where(
                InventoryBalance.id
                == reservation.balance_id
            )
            .with_for_update()
        )

        if balance is None:
            raise ReservationBalanceIntegrityError(
                f"La reserva {reservation.id} no tiene "
                "un saldo válido."
            )

        if balance.reserved_quantity < reservation.quantity:
            raise ReservationBalanceIntegrityError(
                f"El saldo reservado no cubre la reserva "
                f"{reservation.id}."
            )

        movement_key = f"expire:{reservation.id.hex}"

        existing_movement = session.scalar(
            select(InventoryMovement).where(
                InventoryMovement.idempotency_key
                == movement_key
            )
        )

        if existing_movement is not None:
            raise ReservationBalanceIntegrityError(
                "Existe un movimiento de expiración, pero "
                "la reserva continúa activa."
            )

        balance.reserved_quantity -= reservation.quantity

        reservation.status = ReservationStatus.EXPIRED
        reservation.released_at = effective_now

        movement = InventoryMovement(
            balance_id=balance.id,
            movement_type=InventoryMovementType.RELEASE,
            delta_on_hand=0,
            delta_reserved=-reservation.quantity,
            delta_blocked=0,
            reference_type="RESERVATION_EXPIRY",
            reference_id=reservation.id,
            idempotency_key=movement_key,
            actor_user_id=None,
            notes=(
                "Reserva liberada automáticamente "
                "por vencimiento."
            ),
        )

        session.add(movement)
        session.flush()

        results.append(
            _reservation_transition_result(
                reservation=reservation,
                balance=balance,
                replayed=False,
            )
        )

    return ExpirationBatchResult(
        expired_count=len(results),
        reservations=tuple(results),
    )


def pick_inventory_reservation(
    *,
    session: Session,
    reservation_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryPickResult:
    if notes is not None and len(notes) > 500:
        raise ValueError(
            "notes no puede superar 500 caracteres."
        )

    reservation, balance = _lock_reservation(
        session=session,
        reservation_id=reservation_id,
    )

    location = session.get(
        WarehouseLocation,
        balance.location_id,
    )

    if location is None:
        raise ReservationBalanceIntegrityError(
            "El saldo no tiene una ubicación válida."
        )

    if location.location_type not in {
        LocationType.STORAGE,
        LocationType.PICKING,
    }:
        raise InvalidReservationTransitionError(
            "Solo puede hacerse picking desde una ubicación "
            "STORAGE o PICKING."
        )

    movement_key = f"pick:{reservation.id.hex}"

    existing_movement = session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.idempotency_key
            == movement_key
        )
    )

    if existing_movement is not None:
        represents_same_operation = all(
            (
                existing_movement.movement_type
                == InventoryMovementType.PICK,
                existing_movement.balance_id
                == balance.id,
                existing_movement.delta_on_hand
                == -reservation.quantity,
                existing_movement.delta_reserved
                == -reservation.quantity,
                existing_movement.delta_blocked == 0,
                existing_movement.reference_type
                == "INVENTORY_RESERVATION",
                existing_movement.reference_id
                == reservation.id,
            )
        )

        if not represents_same_operation:
            raise IdempotencyConflictError(
                "La clave de picking pertenece "
                "a una operación diferente."
            )

        return InventoryPickResult(
            reservation_id=reservation.id,
            order_item_id=reservation.order_item_id,
            balance_id=balance.id,
            location_id=location.id,
            location_code=location.code,
            quantity=reservation.quantity,
            movement_id=existing_movement.id,
            on_hand_quantity=balance.on_hand_quantity,
            reserved_quantity=balance.reserved_quantity,
            available_quantity=balance.available_quantity,
            replayed=True,
        )

    if reservation.status == ReservationStatus.ACTIVE:
        raise InvalidReservationTransitionError(
            "No puede hacerse picking de una reserva "
            "sin pago confirmado."
        )

    if reservation.status == ReservationStatus.RELEASED:
        raise InvalidReservationTransitionError(
            "No puede hacerse picking de una reserva liberada."
        )

    if reservation.status == ReservationStatus.EXPIRED:
        raise InvalidReservationTransitionError(
            "No puede hacerse picking de una reserva expirada."
        )

    if reservation.status != ReservationStatus.CONSUMED:
        raise InvalidReservationTransitionError(
            "La reserva no permite realizar picking."
        )

    if balance.on_hand_quantity < reservation.quantity:
        raise ReservationBalanceIntegrityError(
            "La existencia física es menor que "
            "la cantidad reservada."
        )

    if balance.reserved_quantity < reservation.quantity:
        raise ReservationBalanceIntegrityError(
            "El saldo reservado es menor que "
            "la cantidad de la reserva."
        )

    balance.on_hand_quantity -= reservation.quantity
    balance.reserved_quantity -= reservation.quantity

    movement = InventoryMovement(
        balance_id=balance.id,
        movement_type=InventoryMovementType.PICK,
        delta_on_hand=-reservation.quantity,
        delta_reserved=-reservation.quantity,
        delta_blocked=0,
        reference_type="INVENTORY_RESERVATION",
        reference_id=reservation.id,
        idempotency_key=movement_key,
        actor_user_id=actor_user_id,
        notes=notes,
    )

    session.add(movement)
    session.flush()

    return InventoryPickResult(
        reservation_id=reservation.id,
        order_item_id=reservation.order_item_id,
        balance_id=balance.id,
        location_id=location.id,
        location_code=location.code,
        quantity=reservation.quantity,
        movement_id=movement.id,
        on_hand_quantity=balance.on_hand_quantity,
        reserved_quantity=balance.reserved_quantity,
        available_quantity=balance.available_quantity,
        replayed=False,
    )
