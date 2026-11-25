from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from app.models import (
    LogisticsPackageState, LogisticsTransfer, OrderItem, OrderPackage, SellerInboundPackage, SellerInboundPackageItem,
    SellerOrder, StaffPointAssignment, Warehouse, WarehouseLocation,
)
from app.models.enums import LocationType, LogisticsPackageStatus, LogisticsTransferStatus, PackageStatus, StaffRole
from app.services.admin_permissions import AdminAuthorizationError, user_has_permission


_ACTIVE_TRANSFERS = (LogisticsTransferStatus.ASSIGNED, LogisticsTransferStatus.IN_TRANSIT)
ADMIN_OPERATING_WAREHOUSE_SESSION_KEY = "admin_operating_warehouse_id"


class AdminOperatingContextError(Exception):
    """The selected operational point cannot be used."""


@dataclass(frozen=True, slots=True)
class OperationalScope:
    """None denotes explicit network supervision. An empty set grants no point."""
    warehouse_ids: frozenset[uuid.UUID] | None
    carrier_id: uuid.UUID | None = None

    def require_point(self, warehouse_id):
        if self.warehouse_ids is not None and warehouse_id not in self.warehouse_ids:
            raise AdminAuthorizationError()

    def state_clause(self):
        if self.warehouse_ids is None:
            return true()
        state = LogisticsPackageState
        if self.carrier_id is None:
            delivered_here = (
                select(SellerInboundPackageItem.package_id)
                .join(OrderPackage, OrderPackage.order_item_id == SellerInboundPackageItem.order_item_id)
                .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
                .where(
                    SellerInboundPackageItem.package_id == state.seller_inbound_package_id,
                    OrderPackage.status == PackageStatus.HANDED_OVER,
                    WarehouseLocation.warehouse_id.in_(self.warehouse_ids),
                ).correlate(state).exists()
            )
            return or_(
                and_(state.status == LogisticsPackageStatus.DELIVERED, delivered_here),
                state.current_warehouse_id.in_(self.warehouse_ids),
                state.expected_destination_warehouse_id.in_(self.warehouse_ids),
            )
        assigned = (
            select(LogisticsTransfer.id)
            .where(
                LogisticsTransfer.package_state_id == state.id,
                LogisticsTransfer.assigned_user_id == self.carrier_id,
                LogisticsTransfer.status.in_(_ACTIVE_TRANSFERS),
            )
            .correlate(state).exists()
        )
        return or_(state.custodian_user_id == self.carrier_id, assigned)

    def pickup_location_clause(self):
        if self.warehouse_ids is None:
            return true()
        if self.carrier_id is not None:
            return false()
        return OrderPackage.pickup_location_id.in_(
            select(WarehouseLocation.id).where(WarehouseLocation.warehouse_id.in_(self.warehouse_ids))
        )

    def order_clause(self, order_id):
        if self.warehouse_ids is None:
            return true()
        if not self.warehouse_ids and self.carrier_id is None:
            return false()

        # A full order detail includes all its lines and all its packages.
        state = LogisticsPackageState
        inbound = SellerInboundPackage
        inbound_item = SellerInboundPackageItem
        visible_state = self.state_clause()
        visible_pickup = self.pickup_location_clause()
        visible_lines = (
            select(inbound_item.order_item_id)
            .join(state, state.seller_inbound_package_id == inbound_item.package_id)
            .where(visible_state)
            .union(select(OrderPackage.order_item_id).where(visible_pickup))
        )
        orders_with_lines = select(SellerOrder.order_id).join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        related = orders_with_lines.where(OrderItem.id.in_(visible_lines))
        unrelated = orders_with_lines.where(OrderItem.id.not_in(visible_lines))
        outside_inbound = (
            select(SellerOrder.order_id)
            .join(inbound, inbound.seller_order_id == SellerOrder.id)
            .outerjoin(state, state.seller_inbound_package_id == inbound.id)
            .where(visible_state.is_not(True))
        )
        outside_outbound = (
            orders_with_lines.join(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
            .where(visible_pickup.is_not(True))
        )
        return and_(order_id.in_(related), order_id.not_in(unrelated.union(outside_inbound, outside_outbound)))


def operational_scope(session: Session, actor) -> OperationalScope:
    if user_has_permission(actor, "operations.supervise"):
        return OperationalScope(None)
    profile = actor.staff_profile
    if profile and user_has_permission(actor, "inventory.view_assigned_point"):
        # PostgreSQL now() is fixed at transaction start. Check the assignment
        # at the time of this authorization, including after a preceding scan.
        checked_at = func.statement_timestamp()
        points = session.scalars(
            select(StaffPointAssignment.warehouse_id)
            .join(Warehouse)
            .where(
                StaffPointAssignment.staff_profile_id == profile.id,
                StaffPointAssignment.starts_at <= checked_at,
                or_(StaffPointAssignment.ends_at.is_(None), StaffPointAssignment.ends_at > checked_at),
                Warehouse.is_active.is_(True),
                Warehouse.seller_store_id.is_(None),
            )
        )
        return OperationalScope(frozenset(points))
    if (
        profile and profile.role in (StaffRole.TRANSPORT_OPERATOR, StaffRole.DELIVERY)
        and user_has_permission(actor, "fulfillment.operate_assigned")
    ):
        routes = session.execute(
            select(LogisticsTransfer.origin_warehouse_id, LogisticsTransfer.destination_warehouse_id)
            .where(LogisticsTransfer.assigned_user_id == actor.id, LogisticsTransfer.status.in_(_ACTIVE_TRANSFERS))
        )
        return OperationalScope(frozenset(point for route in routes for point in route), actor.id)
    return OperationalScope(frozenset())


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


def warehouse_options(session: Session, scope: OperationalScope | None = None) -> tuple[AdminOperatingOption, ...]:
    return tuple(
        AdminOperatingOption(str(warehouse.id), f"{warehouse.name} · {warehouse.city}")
        for warehouse in session.scalars(
            select(Warehouse)
            .where(
                Warehouse.is_active.is_(True),
                Warehouse.id.in_(scope.warehouse_ids) if scope and scope.warehouse_ids is not None else true(),
                Warehouse.seller_store_id.is_(None),
            )
            .order_by(Warehouse.name, Warehouse.code)
        )
    )


def get_operating_point(
    session: Session, warehouse_id: str | uuid.UUID | None, *, scope: OperationalScope
) -> AdminOperatingPoint | None:
    if not warehouse_id:
        return None
    try:
        parsed_id = uuid.UUID(str(warehouse_id))
    except (TypeError, ValueError):
        return None
    scope.require_point(parsed_id)
    warehouse = session.scalar(
        select(Warehouse)
        .options(selectinload(Warehouse.locations))
        .where(
            Warehouse.id == parsed_id,
            Warehouse.is_active.is_(True),
            Warehouse.seller_store_id.is_(None),
        )
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
    statement = select(Warehouse).where(
        Warehouse.id == parsed_id,
        Warehouse.seller_store_id.is_(None),
    )
    if lock:
        statement = statement.with_for_update(read=True).execution_options(populate_existing=True)
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
