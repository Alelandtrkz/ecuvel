from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    InventoryMovementType,
    PackageStatus,
    ReservationStatus,
)
from app.models.fulfillment import OrderPackage
from app.models.inventory import (
    InventoryMovement,
    InventoryReservation,
)
from app.models.order import Order, OrderItem, SellerOrder


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
