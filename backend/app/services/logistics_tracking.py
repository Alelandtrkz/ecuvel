from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    SellerInboundPackage,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LogisticsPackageStatus,
    LogisticsTrackingEventType,
    LogisticsTransferStatus,
    SellerInboundPackageStatus,
    UserStatus,
)


ACTIVE_TRANSFER_STATUSES = (
    LogisticsTransferStatus.ASSIGNED,
    LogisticsTransferStatus.IN_TRANSIT,
)


class LogisticsTrackingError(Exception):
    pass


class LogisticsNotFoundError(LogisticsTrackingError):
    pass


class LogisticsValidationError(LogisticsTrackingError):
    pass


class LogisticsConflictError(LogisticsTrackingError):
    pass


class LogisticsAccessError(LogisticsTrackingError):
    pass


@dataclass(frozen=True, slots=True)
class LogisticsMutationResult:
    package_state: LogisticsPackageState
    transfer: LogisticsTransfer | None
    event: LogisticsTrackingEvent | None
    replayed: bool


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)


def _normalized_notes(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if len(normalized) > 500:
        raise LogisticsValidationError("Las notas no pueden superar 500 caracteres.")
    return normalized


def _normalized_vehicle(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().upper().split())
    if not normalized:
        return None
    if len(normalized) > 40:
        raise LogisticsValidationError(
            "El identificador del vehículo no puede superar 40 caracteres."
        )
    return normalized


def _require_staff(session: Session, user_id: uuid.UUID, *, lock: bool = False) -> User:
    statement = select(User).where(User.id == user_id)
    if lock:
        statement = statement.with_for_update()
    user = session.scalar(statement)
    if (
        user is None
        or not user.is_active
        or not user.is_ecuvel_staff
        or user.status != UserStatus.ACTIVE
    ):
        raise LogisticsAccessError(
            "La operación requiere un usuario interno ECUVEL activo."
        )
    return user


def _require_warehouse(
    session: Session, warehouse_id: uuid.UUID, *, lock: bool = False
) -> Warehouse:
    statement = select(Warehouse).where(Warehouse.id == warehouse_id)
    if lock:
        statement = statement.with_for_update()
    warehouse = session.scalar(statement)
    if warehouse is None:
        raise LogisticsNotFoundError("No existe el punto ECUVEL seleccionado.")
    if not warehouse.is_active:
        raise LogisticsValidationError("El punto ECUVEL seleccionado está inactivo.")
    return warehouse


def _locked_state(session: Session, package_code: str) -> LogisticsPackageState:
    normalized = (package_code or "").strip().upper()
    state = session.scalar(
        select(LogisticsPackageState)
        .join(
            SellerInboundPackage,
            SellerInboundPackage.id
            == LogisticsPackageState.seller_inbound_package_id,
        )
        .where(SellerInboundPackage.package_code == normalized)
        .with_for_update(of=LogisticsPackageState)
    )
    if state is None:
        raise LogisticsNotFoundError(
            "El paquete no tiene trazabilidad activa en la red ECUVEL."
        )
    return state


def _active_transfer(
    session: Session, state_id: uuid.UUID, *, lock: bool = False
) -> LogisticsTransfer | None:
    statement = (
        select(LogisticsTransfer)
        .where(
            LogisticsTransfer.package_state_id == state_id,
            LogisticsTransfer.status.in_(ACTIVE_TRANSFER_STATUSES),
        )
        .order_by(LogisticsTransfer.assigned_at.desc(), LogisticsTransfer.id.desc())
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _latest_transfer(
    session: Session, state_id: uuid.UUID, *, lock: bool = False
) -> LogisticsTransfer | None:
    statement = (
        select(LogisticsTransfer)
        .where(LogisticsTransfer.package_state_id == state_id)
        .order_by(LogisticsTransfer.assigned_at.desc(), LogisticsTransfer.id.desc())
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _event_for_key(
    session: Session, idempotency_key: str | None
) -> LogisticsTrackingEvent | None:
    if not idempotency_key:
        return None
    return session.scalar(
        select(LogisticsTrackingEvent).where(
            LogisticsTrackingEvent.idempotency_key == idempotency_key
        )
    )


def _append_event(
    session: Session,
    *,
    state: LogisticsPackageState,
    event_type: LogisticsTrackingEventType,
    occurred_at: datetime,
    actor_user_id: uuid.UUID | None,
    transfer: LogisticsTransfer | None = None,
    warehouse_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    previous_custodian_warehouse_id: uuid.UUID | None = None,
    previous_custodian_user_id: uuid.UUID | None = None,
    new_custodian_warehouse_id: uuid.UUID | None = None,
    new_custodian_user_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    notes: str | None = None,
) -> LogisticsTrackingEvent:
    event_record = LogisticsTrackingEvent(
        package_state_id=state.id,
        transfer_id=transfer.id if transfer else None,
        event_type=event_type,
        occurred_at=occurred_at,
        warehouse_id=warehouse_id,
        location_id=location_id,
        previous_custodian_warehouse_id=previous_custodian_warehouse_id,
        previous_custodian_user_id=previous_custodian_user_id,
        new_custodian_warehouse_id=new_custodian_warehouse_id,
        new_custodian_user_id=new_custodian_user_id,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
        notes=notes,
    )
    session.add(event_record)
    state.last_event_at = occurred_at
    session.flush()
    return event_record


def register_received_inbound_package(
    session: Session,
    *,
    package_id: uuid.UUID,
    location_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> LogisticsMutationResult:
    actor = _require_staff(session, actor_user_id)
    package = session.scalar(
        select(SellerInboundPackage)
        .where(SellerInboundPackage.id == package_id)
        .with_for_update()
    )
    if package is None:
        raise LogisticsNotFoundError("No existe el paquete recibido.")
    if package.status != SellerInboundPackageStatus.RECEIVED_BY_ECUVEL:
        raise LogisticsConflictError(
            "El paquete debe estar recibido por ECUVEL antes de iniciar trazabilidad."
        )
    location = session.scalar(
        select(WarehouseLocation)
        .where(WarehouseLocation.id == location_id)
        .with_for_update()
    )
    if location is None or not location.is_active:
        raise LogisticsValidationError(
            "La ubicación de recepción no es una ubicación ECUVEL activa."
        )
    warehouse = _require_warehouse(session, location.warehouse_id)
    state = session.scalar(
        select(LogisticsPackageState)
        .where(LogisticsPackageState.seller_inbound_package_id == package.id)
        .with_for_update()
    )
    if state is not None:
        return LogisticsMutationResult(state, _active_transfer(session, state.id), None, True)
    occurred_at = _now(now)
    state = LogisticsPackageState(
        seller_inbound_package_id=package.id,
        status=LogisticsPackageStatus.AT_POINT,
        current_warehouse_id=warehouse.id,
        current_location_id=location.id,
        custodian_warehouse_id=warehouse.id,
        custodian_user_id=None,
        expected_destination_warehouse_id=None,
        is_deviated=False,
        last_event_at=occurred_at,
    )
    session.add(state)
    session.flush()
    event_record = _append_event(
        session,
        state=state,
        event_type=LogisticsTrackingEventType.RECEIVED_AT_POINT,
        occurred_at=occurred_at,
        actor_user_id=actor.id,
        warehouse_id=warehouse.id,
        location_id=location.id,
        new_custodian_warehouse_id=warehouse.id,
        idempotency_key=idempotency_key,
        notes="Paquete recibido físicamente por ECUVEL.",
    )
    return LogisticsMutationResult(state, None, event_record, False)


def assign_package_transfer(
    session: Session,
    *,
    package_code: str,
    destination_warehouse_id: uuid.UUID,
    responsible_user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    vehicle_code: str | None = None,
    eta_at: datetime | None = None,
    notes: str | None = None,
    corrective: bool = False,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> LogisticsMutationResult:
    previous = _event_for_key(session, idempotency_key)
    if previous is not None:
        return LogisticsMutationResult(
            previous.package_state,
            previous.transfer,
            previous,
            True,
        )
    actor = _require_staff(session, actor_user_id)
    responsible = _require_staff(session, responsible_user_id)
    destination = _require_warehouse(session, destination_warehouse_id)
    state = _locked_state(session, package_code)
    if state.status not in {
        LogisticsPackageStatus.AT_POINT,
        LogisticsPackageStatus.DEVIATED,
    }:
        raise LogisticsConflictError(
            "El paquete debe estar en un punto ECUVEL para asignar un traslado."
        )
    if state.current_warehouse_id is None:
        raise LogisticsConflictError("El paquete no tiene un punto de origen conocido.")
    origin = _require_warehouse(session, state.current_warehouse_id, lock=True)
    if origin.id == destination.id:
        raise LogisticsValidationError("El origen y el destino deben ser diferentes.")
    if _active_transfer(session, state.id, lock=True) is not None:
        raise LogisticsConflictError("El paquete ya tiene un traslado activo.")
    if corrective and not state.is_deviated:
        raise LogisticsConflictError(
            "Solo un paquete desviado admite un traslado correctivo."
        )
    if state.is_deviated and not corrective:
        raise LogisticsConflictError(
            "El paquete desviado requiere crear un traslado correctivo."
        )
    occurred_at = _now(now)
    if corrective and state.expected_destination_warehouse_id != destination.id:
        raise LogisticsValidationError(
            "El traslado correctivo debe dirigirse al destino esperado original."
        )
    if eta_at is not None:
        if eta_at.tzinfo is None or eta_at.utcoffset() is None:
            eta_at = eta_at.replace(tzinfo=timezone.utc)
        if eta_at < occurred_at:
            raise LogisticsValidationError(
                "La fecha estimada debe ser posterior a la asignación."
            )
    previous_transfer = _latest_transfer(session, state.id, lock=True)
    transfer = LogisticsTransfer(
        package_state_id=state.id,
        origin_warehouse_id=origin.id,
        destination_warehouse_id=destination.id,
        assigned_user_id=responsible.id,
        status=LogisticsTransferStatus.ASSIGNED,
        vehicle_code=_normalized_vehicle(vehicle_code),
        is_corrective=corrective,
        previous_transfer_id=previous_transfer.id if corrective and previous_transfer else None,
        assigned_at=occurred_at,
        eta_at=eta_at,
    )
    session.add(transfer)
    session.flush()
    state.status = LogisticsPackageStatus.ASSIGNED
    state.expected_destination_warehouse_id = destination.id
    event_record = _append_event(
        session,
        state=state,
        transfer=transfer,
        event_type=(
            LogisticsTrackingEventType.CORRECTIVE_TRANSFER_CREATED
            if corrective
            else LogisticsTrackingEventType.TRANSFER_ASSIGNED
        ),
        occurred_at=occurred_at,
        actor_user_id=actor.id,
        warehouse_id=origin.id,
        previous_custodian_warehouse_id=origin.id,
        new_custodian_warehouse_id=origin.id,
        idempotency_key=idempotency_key,
        notes=_normalized_notes(notes),
    )
    return LogisticsMutationResult(state, transfer, event_record, False)


def reassign_package_transfer(
    session: Session,
    *,
    package_code: str,
    responsible_user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    vehicle_code: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> LogisticsMutationResult:
    previous = _event_for_key(session, idempotency_key)
    if previous is not None:
        return LogisticsMutationResult(previous.package_state, previous.transfer, previous, True)
    actor = _require_staff(session, actor_user_id)
    responsible = _require_staff(session, responsible_user_id)
    state = _locked_state(session, package_code)
    transfer = _active_transfer(session, state.id, lock=True)
    if transfer is None or transfer.status != LogisticsTransferStatus.ASSIGNED:
        raise LogisticsConflictError(
            "Solo un traslado asignado y aún no recogido puede reasignarse."
        )
    transfer.assigned_user_id = responsible.id
    transfer.vehicle_code = _normalized_vehicle(vehicle_code)
    occurred_at = _now(now)
    event_record = _append_event(
        session,
        state=state,
        transfer=transfer,
        event_type=LogisticsTrackingEventType.TRANSFER_REASSIGNED,
        occurred_at=occurred_at,
        actor_user_id=actor.id,
        warehouse_id=state.current_warehouse_id,
        previous_custodian_warehouse_id=state.custodian_warehouse_id,
        new_custodian_warehouse_id=state.custodian_warehouse_id,
        idempotency_key=idempotency_key,
        notes=_normalized_notes(notes),
    )
    return LogisticsMutationResult(state, transfer, event_record, False)


def confirm_package_pickup(
    session: Session,
    *,
    package_code: str,
    actor_user_id: uuid.UUID,
    expected_origin_warehouse_id: uuid.UUID | None = None,
    notes: str | None = None,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> LogisticsMutationResult:
    previous = _event_for_key(session, idempotency_key)
    if previous is not None:
        return LogisticsMutationResult(previous.package_state, previous.transfer, previous, True)
    actor = _require_staff(session, actor_user_id)
    state = _locked_state(session, package_code)
    transfer = _active_transfer(session, state.id, lock=True)
    if (
        transfer is not None
        and transfer.status == LogisticsTransferStatus.IN_TRANSIT
        and state.status == LogisticsPackageStatus.IN_TRANSIT
    ):
        return LogisticsMutationResult(state, transfer, None, True)
    if transfer is None or transfer.status != LogisticsTransferStatus.ASSIGNED:
        raise LogisticsConflictError("El paquete no tiene un traslado listo para recoger.")
    if (
        expected_origin_warehouse_id is not None
        and (
            transfer.origin_warehouse_id != expected_origin_warehouse_id
            or state.current_warehouse_id != expected_origin_warehouse_id
        )
    ):
        raise LogisticsConflictError(
            "El paquete no se encuentra en el punto operativo actual."
        )
    if state.current_warehouse_id != transfer.origin_warehouse_id:
        raise LogisticsConflictError(
            "El paquete ya no se encuentra en el origen del traslado."
        )
    occurred_at = _now(now)
    previous_warehouse_id = state.custodian_warehouse_id
    transfer.status = LogisticsTransferStatus.IN_TRANSIT
    transfer.picked_up_at = occurred_at
    state.status = LogisticsPackageStatus.IN_TRANSIT
    state.current_warehouse_id = None
    state.current_location_id = None
    state.custodian_warehouse_id = None
    state.custodian_user_id = transfer.assigned_user_id
    event_record = _append_event(
        session,
        state=state,
        transfer=transfer,
        event_type=LogisticsTrackingEventType.PICKED_UP,
        occurred_at=occurred_at,
        actor_user_id=actor.id,
        warehouse_id=transfer.origin_warehouse_id,
        previous_custodian_warehouse_id=previous_warehouse_id,
        new_custodian_user_id=transfer.assigned_user_id,
        idempotency_key=idempotency_key,
        notes=_normalized_notes(notes),
    )
    return LogisticsMutationResult(state, transfer, event_record, False)


def receive_transfer_at_destination(
    session: Session,
    *,
    package_code: str,
    warehouse_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    location_id: uuid.UUID | None = None,
    notes: str | None = None,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> LogisticsMutationResult:
    previous = _event_for_key(session, idempotency_key)
    if previous is not None:
        return LogisticsMutationResult(previous.package_state, previous.transfer, previous, True)
    actor = _require_staff(session, actor_user_id)
    warehouse = _require_warehouse(session, warehouse_id, lock=True)
    location = None
    if location_id is not None:
        location = session.scalar(
            select(WarehouseLocation)
            .where(WarehouseLocation.id == location_id)
            .with_for_update()
        )
        if (
            location is None
            or not location.is_active
            or location.warehouse_id != warehouse.id
        ):
            raise LogisticsValidationError(
                "La ubicación interna no pertenece al punto ECUVEL seleccionado."
            )
    state = _locked_state(session, package_code)
    transfer = _active_transfer(session, state.id, lock=True)
    if transfer is None:
        latest = _latest_transfer(session, state.id, lock=True)
        if (
            latest is not None
            and latest.received_at is not None
            and state.current_warehouse_id == warehouse.id
        ):
            return LogisticsMutationResult(state, latest, None, True)
        raise LogisticsConflictError("El paquete no tiene un traslado en tránsito.")
    if (
        transfer.status != LogisticsTransferStatus.IN_TRANSIT
        or state.status != LogisticsPackageStatus.IN_TRANSIT
        or state.custodian_user_id != transfer.assigned_user_id
    ):
        raise LogisticsConflictError(
            "El estado del paquete no permite confirmar esta recepción."
        )
    occurred_at = _now(now)
    previous_user_id = state.custodian_user_id
    transfer.received_at = occurred_at
    state.current_warehouse_id = warehouse.id
    state.current_location_id = location.id if location else None
    state.custodian_warehouse_id = warehouse.id
    state.custodian_user_id = None
    state.last_event_at = occurred_at
    arrived_as_expected = warehouse.id == transfer.destination_warehouse_id
    if arrived_as_expected:
        transfer.status = LogisticsTransferStatus.RECEIVED
        state.status = LogisticsPackageStatus.AT_POINT
        state.is_deviated = False
    else:
        transfer.status = LogisticsTransferStatus.DEVIATED
        state.status = LogisticsPackageStatus.DEVIATED
        state.is_deviated = True
    arrival = _append_event(
        session,
        state=state,
        transfer=transfer,
        event_type=LogisticsTrackingEventType.ARRIVAL_SCAN,
        occurred_at=occurred_at,
        actor_user_id=actor.id,
        warehouse_id=warehouse.id,
        location_id=location.id if location else None,
        previous_custodian_user_id=previous_user_id,
        new_custodian_warehouse_id=warehouse.id,
        idempotency_key=(f"{idempotency_key}:arrival" if idempotency_key else None),
        notes=_normalized_notes(notes),
    )
    if arrived_as_expected:
        event_record = _append_event(
            session,
            state=state,
            transfer=transfer,
            event_type=LogisticsTrackingEventType.RECEIVED_AT_DESTINATION,
            occurred_at=occurred_at,
            actor_user_id=actor.id,
            warehouse_id=warehouse.id,
            location_id=location.id if location else None,
            previous_custodian_user_id=previous_user_id,
            new_custodian_warehouse_id=warehouse.id,
            idempotency_key=idempotency_key,
            notes="Recepción confirmada en el destino esperado.",
        )
    else:
        event_record = _append_event(
            session,
            state=state,
            transfer=transfer,
            event_type=LogisticsTrackingEventType.DEVIATION_DETECTED,
            occurred_at=occurred_at,
            actor_user_id=actor.id,
            warehouse_id=warehouse.id,
            location_id=location.id if location else None,
            previous_custodian_user_id=previous_user_id,
            new_custodian_warehouse_id=warehouse.id,
            idempotency_key=idempotency_key,
            notes=(
                "El paquete fue recibido en un punto distinto de su destino esperado."
            ),
        )
    session.flush()
    return LogisticsMutationResult(state, transfer, event_record or arrival, False)


# Stable domain entry points for the future Scanner module.
receive_package_at_point = register_received_inbound_package
confirm_package_departure = confirm_package_pickup
