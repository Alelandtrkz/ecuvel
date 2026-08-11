from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Warehouse, WarehouseLocation
from app.models.enums import LocationType


ADMIN_OPERATING_WAREHOUSE_SESSION_KEY = "admin_operating_warehouse_id"


class AdminOperatingContextError(Exception):
    """The selected operational point cannot be used."""


@dataclass(frozen=True, slots=True)
class AdminOperatingOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class AdminOperatingPoint:
    id: uuid.UUID
    code: str
    name: str
    city: str
    receiving_locations: tuple[AdminOperatingOption, ...]

    @property
    def label(self) -> str:
        return f"{self.name} · {self.city}"


def warehouse_options(session: Session) -> tuple[AdminOperatingOption, ...]:
    return tuple(
        AdminOperatingOption(str(warehouse.id), f"{warehouse.name} · {warehouse.city}")
        for warehouse in session.scalars(
            select(Warehouse)
            .where(Warehouse.is_active.is_(True))
            .order_by(Warehouse.name, Warehouse.code)
        )
    )


def get_operating_point(
    session: Session, warehouse_id: str | uuid.UUID | None
) -> AdminOperatingPoint | None:
    if not warehouse_id:
        return None
    try:
        parsed_id = uuid.UUID(str(warehouse_id))
    except (TypeError, ValueError):
        return None
    warehouse = session.scalar(
        select(Warehouse)
        .options(selectinload(Warehouse.locations))
        .where(Warehouse.id == parsed_id, Warehouse.is_active.is_(True))
    )
    if warehouse is None:
        return None
    receiving = tuple(
        AdminOperatingOption(str(location.id), f"{location.name} · {location.code}")
        for location in sorted(
            (
                location
                for location in warehouse.locations
                if location.is_active
                and location.location_type == LocationType.RECEIVING
            ),
            key=lambda value: (value.name, value.code),
        )
    )
    return AdminOperatingPoint(
        warehouse.id, warehouse.code, warehouse.name, warehouse.city, receiving
    )


def require_active_operating_point(
    session: Session,
    warehouse_id: str | uuid.UUID | None,
    *,
    lock: bool = False,
) -> Warehouse:
    try:
        parsed_id = uuid.UUID(str(warehouse_id))
    except (TypeError, ValueError) as exc:
        raise AdminOperatingContextError(
            "Selecciona un punto operativo válido."
        ) from exc
    statement = select(Warehouse).where(Warehouse.id == parsed_id)
    if lock:
        statement = statement.with_for_update()
    warehouse = session.scalar(statement)
    if warehouse is None or not warehouse.is_active:
        raise AdminOperatingContextError(
            "El punto operativo seleccionado no está disponible."
        )
    return warehouse


def require_operating_location(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    location_id: str | uuid.UUID | None,
    lock: bool = False,
) -> WarehouseLocation:
    try:
        parsed_id = uuid.UUID(str(location_id))
    except (TypeError, ValueError) as exc:
        raise AdminOperatingContextError("Selecciona una ubicación válida.") from exc
    statement = select(WarehouseLocation).where(
        WarehouseLocation.id == parsed_id,
        WarehouseLocation.warehouse_id == warehouse_id,
        WarehouseLocation.is_active.is_(True),
    )
    if lock:
        statement = statement.with_for_update()
    location = session.scalar(statement)
    if location is None:
        raise AdminOperatingContextError(
            "La ubicación no pertenece al punto operativo seleccionado."
        )
    return location
