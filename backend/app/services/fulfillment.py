from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.services.admin_operating_context import operational_scope
from app.services.admin_permissions import require_actor_permission
from app.models.enums import (
    InventoryMovementType,
    LocationType,
    LogisticsPackageStatus,
    SellerOrderStatus,
    PackageStatus,
    ReservationStatus,
    SellerOrderDecisionStatus,
)
from app.models.fulfillment import OrderPackage
from app.models.inventory import (
    InventoryMovement,
    InventoryReservation,
)
from app.models.order import Order, OrderItem, SellerOrder
from app.models.warehouse import Warehouse, WarehouseLocation
from app.services.order_custody import active_pickup_point, paid_order_condition, received_allocations, units_in_pickup_custody


class FulfillmentServiceError(Exception):
    """Error base de los servicios de fulfillment."""


class FulfillmentOrderNotFoundError(FulfillmentServiceError):
    """No se encontró el pedido solicitado."""


class OrderNotReadyForPackagingError(FulfillmentServiceError):
    """El pedido todavía no puede empaquetarse."""


class PackageNotFoundError(FulfillmentServiceError):
    """No se encontró el paquete solicitado."""


class PackageIntegrityError(FulfillmentServiceError):
    """Los datos del paquete son inconsistentes."""


class InvalidPackageTransitionError(FulfillmentServiceError):
    """El paquete no permite la transición solicitada."""


class PickupLocationNotFoundError(FulfillmentServiceError):
    """No existe la ubicación de retiro solicitada."""


class InvalidPickupLocationError(FulfillmentServiceError):
    """La ubicación no es válida para preparar paquetes."""


class EmptyPackageScanError(FulfillmentServiceError):
    """No se recibió ningún código de paquete."""


class DuplicatePackageScanError(FulfillmentServiceError):
    """El mismo paquete fue escaneado más de una vez."""


class UnexpectedPackageScanError(FulfillmentServiceError):
    """Se escaneó un paquete que no pertenece al pedido."""


class IncompletePackageScanError(FulfillmentServiceError):
    """No fueron escaneados todos los paquetes del pedido."""


@dataclass(frozen=True, slots=True)
class PackageCreationItem:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    order_item_id: uuid.UUID
    product_name: str
    quantity: int
    status: PackageStatus
    replayed: bool


@dataclass(frozen=True, slots=True)
class CreateOrderPackagesResult:
    order_id: uuid.UUID
    order_number: str
    packages: tuple[PackageCreationItem, ...]

    @property
    def created_count(self) -> int:
        return sum(
            1
            for package in self.packages
            if not package.replayed
        )


@dataclass(frozen=True, slots=True)
class PackPackageResult:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    order_item_id: uuid.UUID
    product_name: str
    quantity: int
    status: PackageStatus
    packed_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class StagePackageForPickupResult:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    order_item_id: uuid.UUID
    product_name: str
    quantity: int
    status: PackageStatus
    pickup_location_id: uuid.UUID
    pickup_location_code: str
    ready_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class HandedOverPackageItem:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    order_item_id: uuid.UUID
    product_name: str
    quantity: int
    status: PackageStatus
    pickup_location_code: str
    handed_over_at: datetime


@dataclass(frozen=True, slots=True)
class HandoverOrderResult:
    order_id: uuid.UUID
    order_number: str
    expected_package_count: int
    scanned_package_count: int
    packages: tuple[HandedOverPackageItem, ...]
    replayed: bool


def order_ready_for_pickup_predicate():
    """SQL predicate for a complete customer order awaiting pickup.

    Every order item must have its unique outbound package staged with a
    readiness timestamp at its authorized destination. Keeping
    this rule in fulfillment prevents dashboards from redefining readiness in
    terms of seller suborders or package row counts.
    """

    order_item_count = (
        select(func.count(OrderItem.id))
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    staged_package_count = (
        select(func.count(OrderPackage.id))
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
        .join(Warehouse, Warehouse.id == WarehouseLocation.warehouse_id)
        .where(
            Warehouse.id == Order.pickup_warehouse_id, Warehouse.is_active.is_(True), Warehouse.seller_store_id.is_(None),
            WarehouseLocation.is_active.is_(True), WarehouseLocation.location_type == LocationType.PICKUP_STAGING,
            SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
            SellerOrder.status.not_in((SellerOrderStatus.CANCELLED, SellerOrderStatus.COMPLETED)),
            SellerOrder.order_id == Order.id,
            OrderPackage.status == PackageStatus.READY_FOR_PICKUP,
            OrderPackage.quantity == OrderItem.quantity,
            units_in_pickup_custody(OrderItem, Order) == OrderItem.quantity,
            OrderPackage.ready_at.is_not(None),
        )
        .correlate(Order)
        .scalar_subquery()
    )
    return and_(
        Order.pickup_warehouse_id.is_not(None),
        paid_order_condition(),
        order_item_count > 0,
        staged_package_count == order_item_count,
    )


def _order_not_ready() -> OrderNotReadyForPackagingError:
    return OrderNotReadyForPackagingError(
        "El artículo todavía no fue recogido completamente."
    )


def _validate_order_item_fully_picked(
    *,
    session: Session,
    order_item: OrderItem,
) -> None:
    seller_order = session.scalar(
        select(SellerOrder)
        .where(SellerOrder.id == order_item.seller_order_id)
        .with_for_update()
    )
    if (
        seller_order is None
        or seller_order.status in (SellerOrderStatus.CANCELLED, SellerOrderStatus.COMPLETED)
        or seller_order.decision_status != SellerOrderDecisionStatus.APPROVED
    ):
        raise OrderNotReadyForPackagingError(
            "La tienda todavía no ha aprobado este pedido."
        )
    reservations = list(
        session.scalars(
            select(InventoryReservation)
            .where(
                InventoryReservation.order_item_id
                == order_item.id
            )
            .order_by(
                InventoryReservation.balance_id,
                InventoryReservation.id,
            )
            .with_for_update()
        )
    )

    if not reservations:
        raise _order_not_ready()

    if any(
        reservation.status != ReservationStatus.CONSUMED
        for reservation in reservations
    ):
        raise _order_not_ready()

    total_reserved = sum(
        reservation.quantity
        for reservation in reservations
    )

    if total_reserved != order_item.quantity:
        raise _order_not_ready()

    reservation_ids = [
        reservation.id
        for reservation in reservations
    ]

    pick_movements = list(
        session.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.movement_type
                == InventoryMovementType.PICK,
                InventoryMovement.reference_id.in_(
                    reservation_ids
                ),
            )
            .order_by(
                InventoryMovement.reference_id,
                InventoryMovement.id,
            )
        )
    )

    movements_by_reservation: dict[
        uuid.UUID,
        list[InventoryMovement],
    ] = {
        reservation_id: []
        for reservation_id in reservation_ids
    }

    for movement in pick_movements:
        movements = movements_by_reservation.get(
            movement.reference_id
        )

        if movements is not None:
            movements.append(movement)

    for reservation in reservations:
        movements = movements_by_reservation[reservation.id]

        if len(movements) != 1:
            raise _order_not_ready()

        movement = movements[0]
        movement_key = f"pick:{reservation.id.hex}"

        represents_valid_pick = all(
            (
                movement.idempotency_key == movement_key,
                movement.movement_type
                == InventoryMovementType.PICK,
                movement.balance_id == reservation.balance_id,
                movement.delta_on_hand == -reservation.quantity,
                movement.delta_reserved == -reservation.quantity,
                movement.delta_blocked == 0,
                movement.reference_type
                == "INVENTORY_RESERVATION",
                movement.reference_id == reservation.id,
            )
        )

        if not represents_valid_pick:
            raise _order_not_ready()


def _item_custody(session, item, destination_id):
    if destination_id is None:
        raise InvalidPickupLocationError("El pedido no tiene un destino de retiro registrado.")
    allocations = received_allocations(session, item, for_pickup_at=destination_id)
    if sum(quantity for quantity, _ in allocations) != item.quantity:
        raise OrderNotReadyForPackagingError(
            "Falta verificar la recepción física y la custodia de todas las unidades en el punto de retiro."
        )
    return [state for _, state in allocations]


def _lock_paid_package_order(session, code):
    order = session.scalar(
        select(Order).join(SellerOrder).join(OrderItem).join(OrderPackage)
        .where(OrderPackage.package_code == code)
        .with_for_update(of=Order)
    )
    if order is None:
        raise PackageNotFoundError(f"No existe el paquete {code}.")
    _require_paid_order(session, order)
    return order


def _require_paid_order(session, order):
    if not session.scalar(select(Order.id).where(Order.id == order.id, paid_order_condition())):
        raise OrderNotReadyForPackagingError("El pedido debe estar vigente y tener un pago aprobado.")


def _locked_order_items(session, order_id):
    seller_ids = session.scalars(
        select(SellerOrder.id).where(SellerOrder.order_id == order_id)
        .order_by(SellerOrder.id).with_for_update()
    ).all()
    return session.scalars(
        select(OrderItem).where(OrderItem.seller_order_id.in_(seller_ids))
        .order_by(OrderItem.seller_order_id, OrderItem.id).with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def create_packages_for_order(
    *,
    session: Session,
    order_number: str,
) -> CreateOrderPackagesResult:
    normalized_order_number = order_number.strip()

    if not normalized_order_number:
        raise ValueError("order_number es obligatorio.")

    order = session.scalar(
        select(Order)
        .where(
            Order.order_number == normalized_order_number
        )
        .with_for_update()
    )

    if order is None:
        raise FulfillmentOrderNotFoundError(
            f"No existe el pedido {normalized_order_number}."
        )

    _require_paid_order(session, order)
    order_items = _locked_order_items(session, order.id)

    if not order_items:
        raise PackageIntegrityError(
            "El pedido no contiene artículos para empaquetar."
        )

    for order_item in order_items:
        _validate_order_item_fully_picked(
            session=session,
            order_item=order_item,
        )
        _item_custody(session, order_item, order.pickup_warehouse_id)

    order_item_ids = [
        order_item.id
        for order_item in order_items
    ]

    existing_packages = list(
        session.scalars(
            select(OrderPackage)
            .where(
                OrderPackage.order_item_id.in_(order_item_ids)
            )
            .order_by(OrderPackage.order_item_id)
            .with_for_update().execution_options(populate_existing=True)
        )
    )

    packages_by_order_item = {
        package.order_item_id: package
        for package in existing_packages
    }
    created_order_item_ids: set[uuid.UUID] = set()

    for order_item in order_items:
        package_code = f"PKG-{order_item.id.hex.upper()}"
        barcode = f"ECUVEL-{package_code}"
        existing = packages_by_order_item.get(order_item.id)

        if existing is not None:
            represents_same_package = all(
                (
                    existing.package_code == package_code,
                    existing.barcode == barcode,
                    existing.quantity == order_item.quantity,
                    existing.order_item_id == order_item.id,
                )
            )

            if not represents_same_package:
                raise PackageIntegrityError(
                    "El paquete existente no coincide con "
                    "el artículo del pedido."
                )

            continue

        package = OrderPackage(
            package_code=package_code,
            barcode=barcode,
            order_item_id=order_item.id,
            quantity=order_item.quantity,
            status=PackageStatus.CREATED,
        )

        session.add(package)
        packages_by_order_item[order_item.id] = package
        created_order_item_ids.add(order_item.id)

    session.flush()

    results = tuple(
        PackageCreationItem(
            package_id=packages_by_order_item[order_item.id].id,
            package_code=(
                packages_by_order_item[order_item.id].package_code
            ),
            barcode=packages_by_order_item[order_item.id].barcode,
            order_item_id=order_item.id,
            product_name=order_item.product_name_snapshot,
            quantity=packages_by_order_item[order_item.id].quantity,
            status=packages_by_order_item[order_item.id].status,
            replayed=(
                order_item.id not in created_order_item_ids
            ),
        )
        for order_item in order_items
    )

    return CreateOrderPackagesResult(
        order_id=order.id,
        order_number=order.order_number,
        packages=results,
    )


def pack_order_package(
    *,
    session: Session,
    package_code: str,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> PackPackageResult:
    normalized_package_code = package_code.strip()

    if not normalized_package_code:
        raise ValueError("package_code es obligatorio.")

    if notes is not None and len(notes) > 500:
        raise ValueError(
            "notes no puede superar 500 caracteres."
        )

    order = _lock_paid_package_order(session, normalized_package_code)
    package = session.scalar(
        select(OrderPackage)
        .where(
            OrderPackage.package_code
            == normalized_package_code
        )
        .with_for_update().execution_options(populate_existing=True)
    )

    if package is None:
        raise PackageNotFoundError(
            f"No existe el paquete {normalized_package_code}."
        )

    order_item = session.get(
        OrderItem,
        package.order_item_id,
    )

    if order_item is None:
        raise PackageIntegrityError(
            "El paquete no tiene un artículo válido."
        )

    if package.quantity != order_item.quantity:
        raise PackageIntegrityError(
            "La cantidad del paquete no coincide con "
            "el artículo del pedido."
        )

    _validate_order_item_fully_picked(
        session=session,
        order_item=order_item,
    )

    actor = require_actor_permission(session, actor_user_id, "scanner.dispatch_package")
    operational_scope(session, actor).require_point(order.pickup_warehouse_id)
    if not active_pickup_point(session, order.pickup_warehouse_id):
        raise InvalidPickupLocationError("El punto de retiro no está disponible.")
    _item_custody(session, order_item, order.pickup_warehouse_id)

    if package.status == PackageStatus.PACKED:
        if package.packed_at is None:
            raise PackageIntegrityError(
                "El paquete empacado no tiene fecha de empaque."
            )

        return PackPackageResult(
            package_id=package.id,
            package_code=package.package_code,
            barcode=package.barcode,
            order_item_id=order_item.id,
            product_name=order_item.product_name_snapshot,
            quantity=package.quantity,
            status=package.status,
            packed_at=package.packed_at,
            replayed=True,
        )

    if package.status != PackageStatus.CREATED:
        raise InvalidPackageTransitionError(
            f"No puede empacarse un paquete en estado "
            f"{package.status.value}."
        )

    package.status = PackageStatus.PACKED
    package.packed_at = datetime.now(timezone.utc)
    package.packed_by_user_id = actor_user_id
    package.packing_notes = notes

    session.flush()

    return PackPackageResult(
        package_id=package.id,
        package_code=package.package_code,
        barcode=package.barcode,
        order_item_id=order_item.id,
        product_name=order_item.product_name_snapshot,
        quantity=package.quantity,
        status=package.status,
        packed_at=package.packed_at,
        replayed=False,
    )


def stage_order_package_for_pickup(
    *,
    session: Session,
    package_code: str,
    pickup_location_code: str,
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> StagePackageForPickupResult:
    normalized_package_code = package_code.strip()
    normalized_location_code = pickup_location_code.strip()

    if not normalized_package_code:
        raise ValueError("package_code es obligatorio.")

    if not normalized_location_code:
        raise ValueError("pickup_location_code es obligatorio.")

    if notes is not None and len(notes) > 500:
        raise ValueError(
            "notes no puede superar 500 caracteres."
        )

    order = _lock_paid_package_order(session, normalized_package_code)
    package = session.scalar(
        select(OrderPackage)
        .where(
            OrderPackage.package_code
            == normalized_package_code
        )
        .with_for_update().execution_options(populate_existing=True)
    )

    if package is None:
        raise PackageNotFoundError(
            f"No existe el paquete {normalized_package_code}."
        )

    order_item = session.get(
        OrderItem,
        package.order_item_id,
    )

    if order_item is None:
        raise PackageIntegrityError(
            "El paquete no tiene un artículo válido."
        )

    _validate_order_item_fully_picked(
        session=session,
        order_item=order_item,
    )

    states = _item_custody(session, order_item, order.pickup_warehouse_id)
    point_id = states[0].current_warehouse_id
    if active_pickup_point(session, order.pickup_warehouse_id) != point_id:
        raise InvalidPickupLocationError("No se puede determinar un destino de retiro autorizado para el pedido.")
    pickup_location = session.scalar(
        select(WarehouseLocation)
        .where(WarehouseLocation.code == normalized_location_code, WarehouseLocation.warehouse_id == point_id)
        .with_for_update(read=True).execution_options(populate_existing=True)
    )
    if pickup_location is None:
        raise PickupLocationNotFoundError(
            f"No existe la ubicación {normalized_location_code}."
        )

    if not pickup_location.is_active:
        raise InvalidPickupLocationError(
            "La ubicación de retiro está inactiva."
        )

    if pickup_location.location_type != LocationType.PICKUP_STAGING:
        raise InvalidPickupLocationError(
            "La ubicación indicada no es un área de retiro."
        )

    actor = require_actor_permission(session, actor_user_id, "scanner.dispatch_package")
    operational_scope(session, actor).require_point(point_id)
    if package.quantity != order_item.quantity:
        raise PackageIntegrityError("La cantidad de salida no corresponde al artículo.")

    if package.status == PackageStatus.READY_FOR_PICKUP:
        if (
            package.pickup_location_id is None
            or package.ready_at is None
        ):
            raise PackageIntegrityError(
                "El paquete preparado tiene datos incompletos."
            )

        if package.pickup_location_id != pickup_location.id:
            raise InvalidPackageTransitionError(
                "El paquete ya está preparado "
                "en otra ubicación."
            )

        return StagePackageForPickupResult(
            package_id=package.id,
            package_code=package.package_code,
            barcode=package.barcode,
            order_item_id=order_item.id,
            product_name=order_item.product_name_snapshot,
            quantity=package.quantity,
            status=package.status,
            pickup_location_id=pickup_location.id,
            pickup_location_code=pickup_location.code,
            ready_at=package.ready_at,
            replayed=True,
        )

    if package.status == PackageStatus.CREATED:
        raise InvalidPackageTransitionError(
            "El paquete debe estar empacado antes "
            "de prepararlo para retiro."
        )

    if package.status == PackageStatus.HANDED_OVER:
        raise InvalidPackageTransitionError(
            "El paquete ya fue entregado."
        )

    if package.status == PackageStatus.CANCELLED:
        raise InvalidPackageTransitionError(
            "Un paquete cancelado no puede prepararse para retiro."
        )

    if package.status != PackageStatus.PACKED:
        raise InvalidPackageTransitionError(
            f"No puede prepararse un paquete en estado "
            f"{package.status.value}."
        )

    if package.packed_at is None:
        raise PackageIntegrityError(
            "El paquete figura como PACKED pero no tiene packed_at."
        )

    package.status = PackageStatus.READY_FOR_PICKUP
    package.pickup_location_id = pickup_location.id
    package.ready_at = datetime.now(timezone.utc)
    package.ready_by_user_id = actor_user_id
    package.ready_notes = notes

    session.flush()

    return StagePackageForPickupResult(
        package_id=package.id,
        package_code=package.package_code,
        barcode=package.barcode,
        order_item_id=order_item.id,
        product_name=order_item.product_name_snapshot,
        quantity=package.quantity,
        status=package.status,
        pickup_location_id=pickup_location.id,
        pickup_location_code=pickup_location.code,
        ready_at=package.ready_at,
        replayed=False,
    )


def prepare_order_for_pickup(session, *, order_number, warehouse_id, pickup_location_id, actor_user_id):
    actor = require_actor_permission(session, actor_user_id, "scanner.dispatch_package")
    scope = operational_scope(session, actor)
    scope.require_point(warehouse_id)
    location = session.scalar(
        select(WarehouseLocation).where(
            WarehouseLocation.id == pickup_location_id, WarehouseLocation.warehouse_id == warehouse_id,
            WarehouseLocation.is_active.is_(True), WarehouseLocation.location_type == LocationType.PICKUP_STAGING,
        )
    )
    if location is None:
        raise InvalidPickupLocationError("La ubicación no corresponde al punto de retiro autorizado.")
    order = session.scalar(
        select(Order).where(Order.order_number == order_number, scope.order_clause(Order.id)).with_for_update()
    )
    if order is None:
        raise FulfillmentOrderNotFoundError("No existe un pedido disponible en el punto seleccionado.")
    if order.pickup_warehouse_id != warehouse_id or not active_pickup_point(session, warehouse_id):
        raise InvalidPickupLocationError("Este punto no es el destino de retiro registrado en el pedido.")
    with session.begin_nested():
        created = create_packages_for_order(session=session, order_number=order_number)
        staged = []
        for package in created.packages:
            if package.status == PackageStatus.CREATED:
                pack_order_package(session=session, package_code=package.package_code, actor_user_id=actor.id)
            staged.append(stage_order_package_for_pickup(
                session=session, package_code=package.package_code, pickup_location_code=location.code,
                actor_user_id=actor.id,
            ))
    return tuple(staged)


def handover_order_packages(
    *,
    session: Session,
    order_number: str,
    scanned_codes: Sequence[str],
    actor_user_id: uuid.UUID | None = None,
    expected_warehouse_id: uuid.UUID | None = None,
    expected_buyer_id: uuid.UUID,
    notes: str | None = None,
) -> HandoverOrderResult:
    actor = require_actor_permission(session, actor_user_id, "scanner.dispatch_package")
    scope = operational_scope(session, actor)
    scope.require_point(expected_warehouse_id)
    normalized_order_number = order_number.strip()
    normalized_scans = tuple(
        code.strip()
        for code in scanned_codes
    )

    if not normalized_order_number:
        raise ValueError("order_number es obligatorio.")

    if not normalized_scans:
        raise EmptyPackageScanError(
            "Debe escanear al menos un paquete."
        )

    if any(not scan_code for scan_code in normalized_scans):
        raise ValueError(
            "Los códigos escaneados no pueden estar vacíos."
        )

    if notes is not None and len(notes) > 500:
        raise ValueError(
            "notes no puede superar 500 caracteres."
        )

    order = session.scalar(
        select(Order)
        .where(
            Order.order_number == normalized_order_number
        )
        .with_for_update()
    )

    if order is None or order.buyer_id != expected_buyer_id:
        raise FulfillmentOrderNotFoundError(
            f"No existe el pedido {normalized_order_number}."
        )

    if expected_warehouse_id is not None and order.pickup_warehouse_id != expected_warehouse_id:
        raise InvalidPickupLocationError("El pedido no corresponde al punto operativo actual.")

    order_items = _locked_order_items(session, order.id)

    if not order_items:
        raise PackageIntegrityError(
            "El pedido no contiene artículos."
        )

    order_items_by_id = {
        order_item.id: order_item
        for order_item in order_items
    }

    packages = list(
        session.scalars(
            select(OrderPackage)
            .where(
                OrderPackage.order_item_id.in_(
                    list(order_items_by_id)
                )
            )
            .order_by(OrderPackage.id)
            .with_for_update().execution_options(populate_existing=True)
        )
    )

    if (
        len(packages) != len(order_items)
        or {
            package.order_item_id
            for package in packages
        }
        != set(order_items_by_id)
    ):
        raise PackageIntegrityError(
            "El pedido no tiene exactamente un paquete "
            "por artículo."
        )

    for package in packages:
        order_item = order_items_by_id[package.order_item_id]

        if package.quantity != order_item.quantity:
            raise PackageIntegrityError(
                "La cantidad de un paquete no coincide "
                "con el artículo del pedido."
            )

    package_by_scan_code: dict[str, OrderPackage] = {}

    for package in packages:
        for scan_code in (
            package.package_code,
            package.barcode,
        ):
            existing = package_by_scan_code.get(scan_code)

            if existing is not None and existing.id != package.id:
                raise PackageIntegrityError(
                    "Existen códigos de paquete ambiguos."
                )

            package_by_scan_code[scan_code] = package

    scanned_package_ids: set[uuid.UUID] = set()

    for scan_code in normalized_scans:
        package = package_by_scan_code.get(scan_code)

        if package is None:
            raise UnexpectedPackageScanError(
                f"El código {scan_code} no pertenece al pedido."
            )

        if package.id in scanned_package_ids:
            raise DuplicatePackageScanError(
                f"El paquete {package.package_code} "
                "fue escaneado más de una vez."
            )

        scanned_package_ids.add(package.id)

    expected_package_ids = {
        package.id
        for package in packages
    }

    if scanned_package_ids != expected_package_ids:
        missing_package_codes = [
            package.package_code
            for package in packages
            if package.id not in scanned_package_ids
        ]

        raise IncompletePackageScanError(
            "Faltan paquetes por escanear: "
            + ", ".join(missing_package_codes)
        )

    handed_over_packages = [
        package
        for package in packages
        if package.status == PackageStatus.HANDED_OVER
    ]

    if handed_over_packages and len(handed_over_packages) != len(packages):
        raise PackageIntegrityError(
            "El pedido presenta una entrega parcial inconsistente."
        )

    replayed = len(handed_over_packages) == len(packages)

    if replayed:
        if any(
            package.handed_over_at is None
            for package in packages
        ):
            raise PackageIntegrityError(
                "Un paquete entregado tiene datos "
                "de entrega incompletos."
            )

    else:
        if any(
            package.status == PackageStatus.CANCELLED
            for package in packages
        ):
            raise InvalidPackageTransitionError(
                "Un paquete cancelado no puede entregarse."
            )

        if any(
            package.status != PackageStatus.READY_FOR_PICKUP
            for package in packages
        ):
            raise InvalidPackageTransitionError(
                "Todos los paquetes deben estar empacados "
                "y preparados para retiro."
            )

        if any(
            package.pickup_location_id is None
            or package.ready_at is None
            for package in packages
        ):
            raise PackageIntegrityError(
                "Un paquete preparado tiene datos "
                "de retiro incompletos."
            )

    pickup_location_ids = {
        package.pickup_location_id
        for package in packages
        if package.pickup_location_id is not None
    }
    pickup_locations = list(
        session.scalars(
            select(WarehouseLocation)
            .where(
                WarehouseLocation.id.in_(pickup_location_ids)
            )
            .order_by(WarehouseLocation.id)
            .with_for_update()
        )
    )
    pickup_locations_by_id = {
        location.id: location
        for location in pickup_locations
    }

    if any(
        package.pickup_location_id
        not in pickup_locations_by_id
        for package in packages
    ):
        raise PackageIntegrityError(
            "Un paquete no tiene una ubicación "
            "de retiro válida."
        )

    point_ids = {location.warehouse_id for location in pickup_locations}
    if len(point_ids) != 1 or (expected_warehouse_id is not None and expected_warehouse_id not in point_ids):
        raise InvalidPickupLocationError("Todos los paquetes deben estar preparados en el punto operativo actual.")
    point_id = next(iter(point_ids))
    scope.require_point(point_id)
    if not replayed:
        _require_paid_order(session, order)
        if active_pickup_point(session, order.pickup_warehouse_id) != point_id or any(
            not location.is_active or location.location_type != LocationType.PICKUP_STAGING
            for location in pickup_locations
        ):
            raise InvalidPickupLocationError("El punto de retiro no está autorizado.")
        states = {}
        for item in order_items:
            _validate_order_item_fully_picked(session=session, order_item=item)
            for state in _item_custody(session, item, order.pickup_warehouse_id):
                states[state.id] = state
        handover_time = datetime.now(timezone.utc)

        for package in packages:
            package.status = PackageStatus.HANDED_OVER
            package.handed_over_at = handover_time
            package.handed_over_by_user_id = actor_user_id
            package.handover_notes = notes

        for state in states.values():
            state.status = LogisticsPackageStatus.DELIVERED
            state.current_warehouse_id = None
            state.current_location_id = None
            state.custodian_warehouse_id = None
            state.custodian_user_id = order.buyer_id
            state.last_event_at = handover_time

        session.flush()

    # La entrega al comprador es el Ãºnico hito que inicia la espera de
    # liquidaciÃ³n. Se sincroniza tambiÃ©n en replays para reparar Ã³rdenes
    # histÃ³ricas que tengan evidencia completa pero no los timestamps.
    from app.services.seller_payouts import synchronize_seller_order_delivery

    for seller_order_id in sorted(
        {item.seller_order_id for item in order_items}, key=str
    ):
        synchronize_seller_order_delivery(
            session,
            seller_order_id=seller_order_id,
            lock=True,
        )

    result_items = tuple(
        HandedOverPackageItem(
            package_id=package.id,
            package_code=package.package_code,
            barcode=package.barcode,
            order_item_id=package.order_item_id,
            product_name=(
                order_items_by_id[
                    package.order_item_id
                ].product_name_snapshot
            ),
            quantity=package.quantity,
            status=package.status,
            pickup_location_code=(
                pickup_locations_by_id[
                    package.pickup_location_id
                ].code
            ),
            handed_over_at=package.handed_over_at,
        )
        for package in packages
    )

    return HandoverOrderResult(
        order_id=order.id,
        order_number=order.order_number,
        expected_package_count=len(packages),
        scanned_package_count=len(scanned_package_ids),
        packages=result_items,
        replayed=replayed,
    )
