from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    InventoryMovementType,
    LocationType,
    PackageStatus,
    ReservationStatus,
)
from app.models.fulfillment import OrderPackage
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
)
from app.models.order import Order, OrderItem, SellerOrder
from app.models.warehouse import WarehouseLocation


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


def _order_not_ready() -> OrderNotReadyForPackagingError:
    return OrderNotReadyForPackagingError(
        "El artículo todavía no fue recogido completamente."
    )


def _validate_order_item_fully_picked(
    *,
    session: Session,
    order_item: OrderItem,
) -> None:
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


def _get_order_item_warehouse_id(
    *,
    session: Session,
    order_item: OrderItem,
) -> uuid.UUID:
    warehouse_ids = set(
        session.scalars(
            select(WarehouseLocation.warehouse_id)
            .join(
                InventoryBalance,
                InventoryBalance.location_id
                == WarehouseLocation.id,
            )
            .join(
                InventoryReservation,
                InventoryReservation.balance_id
                == InventoryBalance.id,
            )
            .where(
                InventoryReservation.order_item_id
                == order_item.id
            )
        )
    )

    if not warehouse_ids:
        raise PackageIntegrityError(
            "No fue posible determinar el almacén del paquete."
        )

    if len(warehouse_ids) != 1:
        raise PackageIntegrityError(
            "Las reservas del paquete pertenecen "
            "a varios almacenes."
        )

    return next(iter(warehouse_ids))


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

    order_items = list(
        session.scalars(
            select(OrderItem)
            .join(
                SellerOrder,
                SellerOrder.id == OrderItem.seller_order_id,
            )
            .where(
                SellerOrder.order_id == order.id
            )
            .order_by(
                SellerOrder.id,
                OrderItem.id,
            )
            .with_for_update(of=OrderItem)
        )
    )

    if not order_items:
        raise PackageIntegrityError(
            "El pedido no contiene artículos para empaquetar."
        )

    for order_item in order_items:
        _validate_order_item_fully_picked(
            session=session,
            order_item=order_item,
        )

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
            .with_for_update()
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

    package = session.scalar(
        select(OrderPackage)
        .where(
            OrderPackage.package_code
            == normalized_package_code
        )
        .with_for_update()
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

    package = session.scalar(
        select(OrderPackage)
        .where(
            OrderPackage.package_code
            == normalized_package_code
        )
        .with_for_update()
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

    pickup_location = session.scalar(
        select(WarehouseLocation)
        .where(
            WarehouseLocation.code
            == normalized_location_code
        )
        .with_for_update()
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

    package_warehouse_id = _get_order_item_warehouse_id(
        session=session,
        order_item=order_item,
    )

    if pickup_location.warehouse_id != package_warehouse_id:
        raise InvalidPickupLocationError(
            "La ubicación de retiro pertenece a otro almacén."
        )

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


def handover_order_packages(
    *,
    session: Session,
    order_number: str,
    scanned_codes: Sequence[str],
    actor_user_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> HandoverOrderResult:
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

    if order is None:
        raise FulfillmentOrderNotFoundError(
            f"No existe el pedido {normalized_order_number}."
        )

    order_items = list(
        session.scalars(
            select(OrderItem)
            .join(
                SellerOrder,
                SellerOrder.id == OrderItem.seller_order_id,
            )
            .where(
                SellerOrder.order_id == order.id
            )
            .order_by(
                SellerOrder.id,
                OrderItem.id,
            )
            .with_for_update(of=OrderItem)
        )
    )

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
            .with_for_update()
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

    if not replayed:
        handover_time = datetime.now(timezone.utc)

        for package in packages:
            package.status = PackageStatus.HANDED_OVER
            package.handed_over_at = handover_time
            package.handed_over_by_user_id = actor_user_id
            package.handover_notes = notes

        session.flush()

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
