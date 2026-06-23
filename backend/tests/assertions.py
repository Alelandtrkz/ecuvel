from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    InventoryMovementType,
    PackageStatus,
    ReservationStatus,
)
from app.models.fulfillment import OrderPackage
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
)
from app.models.order import OrderItem, SellerOrder


def assert_inventory_invariants(session: Session) -> None:
    balances = list(session.scalars(select(InventoryBalance)))
    reservations = list(session.scalars(select(InventoryReservation)))
    movements = list(session.scalars(select(InventoryMovement)))

    movement_keys = [movement.idempotency_key for movement in movements]
    assert len(movement_keys) == len(set(movement_keys))

    pick_by_reservation: dict = defaultdict(list)

    for movement in movements:
        if movement.movement_type == InventoryMovementType.PICK:
            pick_by_reservation[movement.reference_id].append(movement)

    expected_reserved: dict = defaultdict(int)

    for reservation in reservations:
        picks = pick_by_reservation[reservation.id]
        assert len(picks) <= 1

        if picks:
            movement = picks[0]
            assert movement.balance_id == reservation.balance_id
            assert movement.delta_on_hand == -reservation.quantity
            assert movement.delta_reserved == -reservation.quantity
            assert movement.delta_blocked == 0
            assert movement.reference_type == "INVENTORY_RESERVATION"
            assert movement.reference_id == reservation.id
            assert movement.idempotency_key == f"pick:{reservation.id.hex}"

        if reservation.status == ReservationStatus.ACTIVE:
            expected_reserved[reservation.balance_id] += reservation.quantity
        elif (
            reservation.status == ReservationStatus.CONSUMED
            and not picks
        ):
            expected_reserved[reservation.balance_id] += reservation.quantity
        elif reservation.status in {
            ReservationStatus.RELEASED,
            ReservationStatus.EXPIRED,
        }:
            key_prefix = (
                "release"
                if reservation.status == ReservationStatus.RELEASED
                else "expire"
            )
            expected_key = f"{key_prefix}:{reservation.id.hex}"
            release = next(
                (
                    movement
                    for movement in movements
                    if movement.idempotency_key == expected_key
                ),
                None,
            )
            assert release is not None
            assert release.movement_type == InventoryMovementType.RELEASE
            assert release.balance_id == reservation.balance_id
            assert release.delta_on_hand == 0
            assert release.delta_reserved == -reservation.quantity
            assert release.delta_blocked == 0

    for balance in balances:
        assert balance.on_hand_quantity >= 0
        assert balance.reserved_quantity >= 0
        assert balance.blocked_quantity >= 0
        assert balance.available_quantity >= 0
        assert balance.reserved_quantity == expected_reserved[balance.id]


def assert_fulfillment_invariants(session: Session) -> None:
    packages = list(session.scalars(select(OrderPackage)))
    order_items = {
        item.id: item
        for item in session.scalars(select(OrderItem))
    }
    item_counts = Counter(package.order_item_id for package in packages)
    assert all(count == 1 for count in item_counts.values())
    assert len({p.package_code for p in packages}) == len(packages)
    assert len({p.barcode for p in packages}) == len(packages)

    handed_by_order: dict = defaultdict(lambda: [0, 0])

    for package in packages:
        item = order_items[package.order_item_id]
        assert package.quantity == item.quantity
        seller_order = session.get(SellerOrder, item.seller_order_id)
        handed_by_order[seller_order.order_id][0] += 1

        if package.status == PackageStatus.CREATED:
            assert package.packed_at is None
            assert package.ready_at is None
            assert package.handed_over_at is None
        elif package.status == PackageStatus.PACKED:
            assert package.packed_at is not None
            assert package.ready_at is None
            assert package.handed_over_at is None
        elif package.status == PackageStatus.READY_FOR_PICKUP:
            assert package.packed_at is not None
            assert package.ready_at is not None
            assert package.pickup_location_id is not None
            assert package.handed_over_at is None
        elif package.status == PackageStatus.HANDED_OVER:
            assert package.packed_at is not None
            assert package.ready_at is not None
            assert package.pickup_location_id is not None
            assert package.handed_over_at is not None
            handed_by_order[seller_order.order_id][1] += 1

    for total, handed in handed_by_order.values():
        assert handed in {0, total}
