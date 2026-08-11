from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    LogisticsPackageState,
    OrderPackage,
    PhysicalInventoryCount,
    PhysicalInventoryCountExpectedPackage,
    PhysicalInventoryCountScan,
    SellerInboundPackage,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import LogisticsPackageStatus, PackageStatus
from app.services.admin_operating_context import require_operating_location


PHYSICAL_COUNT_OPEN = "OPEN"
PHYSICAL_COUNT_FINALIZED = "FINALIZED"


class PhysicalInventoryCountError(Exception):
    pass


class PhysicalInventoryCountValidationError(PhysicalInventoryCountError):
    pass


@dataclass(frozen=True, slots=True)
class PhysicalCountStats:
    expected: int
    verified: int
    pending: int
    unexpected: int


@dataclass(frozen=True, slots=True)
class PhysicalCountScanResult:
    scan: PhysicalInventoryCountScan
    duplicate: bool


def normalize_count_code(value: str | None) -> str:
    return " ".join((value or "").strip().upper().split())[:120]


def _physical_inbound_statement(
    warehouse_id: uuid.UUID, location_id: uuid.UUID | None
):
    statement = (
        select(
            SellerInboundPackage.id.label("package_id"),
            SellerInboundPackage.package_code.label("package_code"),
            LogisticsPackageState.current_location_id.label("location_id"),
            WarehouseLocation.code.label("location_code"),
        )
        .join(
            LogisticsPackageState,
            LogisticsPackageState.seller_inbound_package_id
            == SellerInboundPackage.id,
        )
        .outerjoin(
            WarehouseLocation,
            WarehouseLocation.id == LogisticsPackageState.current_location_id,
        )
        .where(
            LogisticsPackageState.current_warehouse_id == warehouse_id,
            LogisticsPackageState.custodian_warehouse_id == warehouse_id,
            LogisticsPackageState.status.in_(
                (
                    LogisticsPackageStatus.AT_POINT,
                    LogisticsPackageStatus.ASSIGNED,
                    LogisticsPackageStatus.DEVIATED,
                )
            ),
        )
    )
    if location_id is not None:
        statement = statement.where(
            LogisticsPackageState.current_location_id == location_id
        )
    return statement


def _physical_customer_statement(
    warehouse_id: uuid.UUID, location_id: uuid.UUID | None
):
    statement = (
        select(
            OrderPackage.id.label("package_id"),
            OrderPackage.package_code.label("package_code"),
            OrderPackage.pickup_location_id.label("location_id"),
            WarehouseLocation.code.label("location_code"),
        )
        .join(
            WarehouseLocation,
            WarehouseLocation.id == OrderPackage.pickup_location_id,
        )
        .where(
            WarehouseLocation.warehouse_id == warehouse_id,
            OrderPackage.status == PackageStatus.READY_FOR_PICKUP,
        )
    )
    if location_id is not None:
        statement = statement.where(OrderPackage.pickup_location_id == location_id)
    return statement


def start_physical_inventory_count(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    location_id: str | uuid.UUID | None,
    actor_user_id: uuid.UUID,
    notes: str | None = None,
    now: datetime | None = None,
) -> PhysicalInventoryCount:
    normalized_notes = " ".join((notes or "").strip().split())[:500] or None
    warehouse = session.scalar(
        select(Warehouse)
        .where(
            Warehouse.id == warehouse_id,
            Warehouse.is_active.is_(True),
            Warehouse.seller_store_id.is_(None),
        )
        .with_for_update()
    )
    if warehouse is None:
        raise PhysicalInventoryCountValidationError(
            "El punto operativo ya no está disponible."
        )
    parsed_location_id: uuid.UUID | None = None
    if location_id:
        location = require_operating_location(
            session,
            warehouse_id=warehouse_id,
            location_id=location_id,
            lock=True,
        )
        parsed_location_id = location.id

    existing = session.scalar(
        select(PhysicalInventoryCount)
        .where(
            PhysicalInventoryCount.warehouse_id == warehouse_id,
            PhysicalInventoryCount.status == PHYSICAL_COUNT_OPEN,
        )
        .with_for_update()
    )
    if existing is not None:
        return existing

    count = PhysicalInventoryCount(
        warehouse_id=warehouse_id,
        location_id=parsed_location_id,
        status=PHYSICAL_COUNT_OPEN,
        started_by_user_id=actor_user_id,
        started_at=now or datetime.now(timezone.utc),
        notes=normalized_notes,
    )
    session.add(count)
    session.flush()

    inbound_rows = session.execute(
        _physical_inbound_statement(warehouse_id, parsed_location_id)
    ).all()
    customer_rows = session.execute(
        _physical_customer_statement(warehouse_id, parsed_location_id)
    ).all()
    for kind, rows in (("INBOUND", inbound_rows), ("CUSTOMER", customer_rows)):
        for row in rows:
            session.add(
                PhysicalInventoryCountExpectedPackage(
                    count_id=count.id,
                    package_kind=kind,
                    package_id=row.package_id,
                    package_code_snapshot=row.package_code,
                    expected_location_id=row.location_id,
                    expected_location_snapshot=row.location_code,
                )
            )
    session.flush()
    return count


def _resolve_package(session: Session, code: str):
    inbound = session.scalar(
        select(SellerInboundPackage).where(
            or_(
                func.upper(SellerInboundPackage.package_code) == code,
                func.upper(SellerInboundPackage.barcode) == code,
            )
        )
    )
    customer = session.scalar(
        select(OrderPackage).where(
            or_(
                func.upper(OrderPackage.package_code) == code,
                func.upper(OrderPackage.barcode) == code,
            )
        )
    )
    if inbound is not None and customer is not None:
        raise PhysicalInventoryCountValidationError(
            "El código coincide con más de un paquete y requiere revisión."
        )
    if inbound is not None:
        state = session.scalar(
            select(LogisticsPackageState).where(
                LogisticsPackageState.seller_inbound_package_id == inbound.id
            )
        )
        location = None
        if state and state.current_location_id:
            location = session.get(WarehouseLocation, state.current_location_id)
        label = location.code if location else "Sin ubicación interna"
        return "INBOUND", inbound.id, label
    if customer is not None:
        location = (
            session.get(WarehouseLocation, customer.pickup_location_id)
            if customer.pickup_location_id
            else None
        )
        label = location.code if location else "Sin ubicación de retiro"
        return "CUSTOMER", customer.id, label
    raise PhysicalInventoryCountValidationError(
        "No encontramos un paquete real con ese código."
    )


def scan_physical_inventory_package(
    session: Session,
    *,
    count_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    code: str,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> PhysicalCountScanResult:
    normalized = normalize_count_code(code)
    if not normalized:
        raise PhysicalInventoryCountValidationError(
            "Escanea o escribe un código de paquete."
        )
    count = session.scalar(
        select(PhysicalInventoryCount)
        .where(PhysicalInventoryCount.id == count_id)
        .with_for_update()
    )
    if count is None or count.warehouse_id != warehouse_id:
        raise PhysicalInventoryCountValidationError(
            "El conteo no pertenece al punto operativo seleccionado."
        )
    if count.status != PHYSICAL_COUNT_OPEN:
        raise PhysicalInventoryCountValidationError(
            "El conteo ya fue finalizado y no admite más escaneos."
        )
    duplicate = session.scalar(
        select(PhysicalInventoryCountScan).where(
            PhysicalInventoryCountScan.count_id == count.id,
            PhysicalInventoryCountScan.scanned_code == normalized,
        )
    )
    if duplicate is not None:
        return PhysicalCountScanResult(duplicate, True)

    package_kind, package_id, registered_location = _resolve_package(
        session, normalized
    )
    expected = session.scalar(
        select(PhysicalInventoryCountExpectedPackage.id).where(
            PhysicalInventoryCountExpectedPackage.count_id == count.id,
            PhysicalInventoryCountExpectedPackage.package_kind == package_kind,
            PhysicalInventoryCountExpectedPackage.package_id == package_id,
        )
    )
    scan = PhysicalInventoryCountScan(
        count_id=count.id,
        scanned_code=normalized,
        package_kind=package_kind,
        package_id=package_id,
        classification="EXPECTED" if expected is not None else "UNEXPECTED",
        registered_location_snapshot=registered_location,
        scanned_by_user_id=actor_user_id,
        scanned_at=now or datetime.now(timezone.utc),
    )
    nested = session.begin_nested()
    try:
        session.add(scan)
        session.flush()
    except IntegrityError:
        nested.rollback()
        duplicate = session.scalar(
            select(PhysicalInventoryCountScan).where(
                PhysicalInventoryCountScan.count_id == count.id,
                PhysicalInventoryCountScan.scanned_code == normalized,
            )
        )
        if duplicate is not None:
            return PhysicalCountScanResult(duplicate, True)
        raise PhysicalInventoryCountValidationError(
            "No pudimos registrar el escaneo concurrente. Inténtalo nuevamente."
        )
    else:
        nested.commit()
    return PhysicalCountScanResult(scan, False)


def finalize_physical_inventory_count(
    session: Session,
    *,
    count_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> PhysicalInventoryCount:
    count = session.scalar(
        select(PhysicalInventoryCount)
        .where(PhysicalInventoryCount.id == count_id)
        .with_for_update()
    )
    if count is None or count.warehouse_id != warehouse_id:
        raise PhysicalInventoryCountValidationError(
            "El conteo no pertenece al punto operativo seleccionado."
        )
    if count.status == PHYSICAL_COUNT_FINALIZED:
        return count
    count.status = PHYSICAL_COUNT_FINALIZED
    count.finalized_by_user_id = actor_user_id
    count.finalized_at = now or datetime.now(timezone.utc)
    session.flush()
    return count


def get_physical_count_stats(
    session: Session, count_id: uuid.UUID
) -> PhysicalCountStats:
    expected = int(
        session.scalar(
            select(func.count()).select_from(PhysicalInventoryCountExpectedPackage).where(
                PhysicalInventoryCountExpectedPackage.count_id == count_id
            )
        )
        or 0
    )
    verified = int(
        session.scalar(
            select(func.count()).select_from(PhysicalInventoryCountScan).where(
                PhysicalInventoryCountScan.count_id == count_id,
                PhysicalInventoryCountScan.classification == "EXPECTED",
            )
        )
        or 0
    )
    unexpected = int(
        session.scalar(
            select(func.count()).select_from(PhysicalInventoryCountScan).where(
                PhysicalInventoryCountScan.count_id == count_id,
                PhysicalInventoryCountScan.classification == "UNEXPECTED",
            )
        )
        or 0
    )
    return PhysicalCountStats(
        expected=expected,
        verified=verified,
        pending=max(expected - verified, 0),
        unexpected=unexpected,
    )
