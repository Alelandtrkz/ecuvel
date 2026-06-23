from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.enums import PackageStatus
from app.models.fulfillment import OrderPackage
from app.models.inventory import InventoryBalance
from app.models.warehouse import WarehouseLocation
from app.services.fulfillment import (
    IncompletePackageScanError,
    create_packages_for_order,
    handover_order_packages,
    pack_order_package,
    stage_order_package_for_pickup,
)
from app.services.inventory import (
    consume_inventory_reservation,
    expire_inventory_reservations,
    pick_inventory_reservation,
    putaway_inventory,
    receive_inventory,
    release_inventory_reservation,
)
from tests.assertions import (
    assert_fulfillment_invariants,
    assert_inventory_invariants,
)
from tests.factories import (
    consume_item_reservations,
    create_catalog_and_stock,
    create_order_items,
    create_picked_order,
    create_ready_for_pickup_order,
    handover_ready_order,
    reserve_item,
)


pytestmark = pytest.mark.integration


def test_complete_flow_preserves_all_invariants(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=0)
        receive_inventory(
            session=session,
            offer_id=base.offer_id,
            location_id=base.receiving_location_id,
            quantity=5,
            reference_type="TEST_RECEIPT",
            reference_id=uuid.uuid4(),
            idempotency_key=f"receive-{uuid.uuid4().hex}",
        )
        putaway_inventory(
            session=session,
            offer_id=base.offer_id,
            source_location_id=base.receiving_location_id,
            destination_location_id=base.storage_location_id,
            quantity=5,
            reference_type="TEST_PUTAWAY",
            reference_id=uuid.uuid4(),
            idempotency_key=f"putaway-{uuid.uuid4().hex}",
        )
        ready = create_ready_for_pickup_order(session, base, [2])
        handover_ready_order(session, base, ready)
        assert_inventory_invariants(session)
        assert_fulfillment_invariants(session)


def test_reserved_quantity_matches_unpicked_reservations(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        _, _, item_ids = create_order_items(session, base, [1] * 5)
        now = datetime.now(timezone.utc)
        active = reserve_item(
            session,
            base,
            item_ids[0],
            expires_at=now + timedelta(hours=3),
        )
        consumed = reserve_item(session, base, item_ids[1])
        consume_item_reservations(session, consumed)
        picked = reserve_item(session, base, item_ids[2])
        consume_item_reservations(session, picked)
        pick_inventory_reservation(
            session=session, reservation_id=picked[0]
        )
        released = reserve_item(session, base, item_ids[3])
        release_inventory_reservation(
            session=session, reservation_id=released[0]
        )
        expires_at = now + timedelta(hours=1)
        reserve_item(
            session,
            base,
            item_ids[4],
            expires_at=expires_at,
        )
        expire_inventory_reservations(
            session=session,
            now=expires_at + timedelta(hours=1),
        )
        balance = session.get(InventoryBalance, base.balance_id)
        assert balance.reserved_quantity == 2
        assert active and consumed
        assert_inventory_invariants(session)


def test_package_state_timestamps_are_coherent(session_factory):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)

        _, created_order, _ = create_picked_order(session, base, [1])
        create_packages_for_order(
            session=session, order_number=created_order
        )

        _, packed_order, _ = create_picked_order(session, base, [1])
        packed_result = create_packages_for_order(
            session=session, order_number=packed_order
        )
        pack_order_package(
            session=session,
            package_code=packed_result.packages[0].package_code,
        )

        ready = create_ready_for_pickup_order(session, base, [1])
        handed = create_ready_for_pickup_order(session, base, [1])
        handover_ready_order(session, base, handed)
        assert ready
        assert_fulfillment_invariants(session)

        ready_package = session.scalar(
            select(OrderPackage).where(
                OrderPackage.package_code == ready.package_codes[0]
            )
        )
        ready_package.ready_at = None
        session.flush()

        with pytest.raises(AssertionError):
            assert_fulfillment_invariants(session)


def test_order_cannot_remain_partially_handed_over(session_factory):
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
        assert_fulfillment_invariants(session)
