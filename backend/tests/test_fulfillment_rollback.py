from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import PackageStatus
from app.models.fulfillment import OrderPackage
from app.models.warehouse import WarehouseLocation
from app.services.fulfillment import (
    DuplicatePackageScanError,
    IncompletePackageScanError,
    InvalidPickupLocationError,
    OrderNotReadyForPackagingError,
    UnexpectedPackageScanError,
    create_packages_for_order,
    handover_order_packages,
    pack_order_package,
    stage_order_package_for_pickup,
)
from tests.factories import (
    consume_item_reservations,
    create_catalog_and_stock,
    create_order_items,
    create_picked_order,
    create_ready_for_pickup_order,
    reserve_item,
)


pytestmark = pytest.mark.integration


def test_package_creation_is_all_or_nothing(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        _, order_number, item_ids = create_order_items(session, base, [2, 2])
        first = reserve_item(session, base, item_ids[0])
        consume_item_reservations(session, first)
        from app.services.inventory import pick_inventory_reservation
        for reservation_id in first:
            pick_inventory_reservation(
                session=session, reservation_id=reservation_id
            )
        second = reserve_item(session, base, item_ids[1])
        consume_item_reservations(session, second)

    with pytest.raises(OrderNotReadyForPackagingError):
        with session_factory.begin() as session:
            create_packages_for_order(
                session=session, order_number=order_number
            )

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(OrderPackage)
        ) == 0


def test_incomplete_scan_rolls_back_entire_handover(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        ready = create_ready_for_pickup_order(session, base, [1, 1])

    with pytest.raises(IncompletePackageScanError):
        with session_factory.begin() as session:
            handover_order_packages(
                session=session,
                order_number=ready.order_number,
                scanned_codes=(ready.package_codes[0],),
            )

    with session_factory() as session:
        packages = list(session.scalars(select(OrderPackage)))
        assert all(p.status == PackageStatus.READY_FOR_PICKUP for p in packages)
        assert all(p.handed_over_at is None for p in packages)
        assert all(p.handed_over_by_user_id is None for p in packages)
        assert all(p.handover_notes is None for p in packages)


def test_foreign_package_scan_changes_no_order(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        first = create_ready_for_pickup_order(session, base, [1])
        second = create_ready_for_pickup_order(session, base, [1])

    with pytest.raises(UnexpectedPackageScanError):
        with session_factory.begin() as session:
            handover_order_packages(
                session=session,
                order_number=first.order_number,
                scanned_codes=(second.package_codes[0],),
            )

    with session_factory() as session:
        packages = list(session.scalars(select(OrderPackage)))
        assert all(p.status == PackageStatus.READY_FOR_PICKUP for p in packages)
        assert all(p.handed_over_at is None for p in packages)


def test_duplicate_package_scan_changes_nothing(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        ready = create_ready_for_pickup_order(session, base, [1])

    with pytest.raises(DuplicatePackageScanError):
        with session_factory.begin() as session:
            handover_order_packages(
                session=session,
                order_number=ready.order_number,
                scanned_codes=(ready.package_codes[0], ready.barcodes[0]),
            )

    with session_factory() as session:
        package = session.scalar(select(OrderPackage))
        assert package.status == PackageStatus.READY_FOR_PICKUP
        assert package.handed_over_at is None


def test_invalid_staging_location_rolls_back_package_transition(
    session_factory,
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, order_number, _ = create_picked_order(session, base, [1])
        created = create_packages_for_order(
            session=session, order_number=order_number
        )
        package_code = created.packages[0].package_code
        pack_order_package(session=session, package_code=package_code)
        storage_code = session.get(
            WarehouseLocation, base.storage_location_id
        ).code

    with pytest.raises(InvalidPickupLocationError):
        with session_factory.begin() as session:
            stage_order_package_for_pickup(
                session=session,
                package_code=package_code,
                pickup_location_code=storage_code,
            )

    with session_factory() as session:
        package = session.scalar(select(OrderPackage))
        assert package.status == PackageStatus.PACKED
        assert package.pickup_location_id is None
        assert package.ready_at is None
