from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.enums import InventoryMovementType, ReservationStatus
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
)
from app.services.inventory import (
    InsufficientSellableStockError,
    InvalidReservationTransitionError,
    consume_inventory_reservation,
    expire_inventory_reservations,
    pick_inventory_reservation,
    release_inventory_reservation,
    reserve_inventory,
)
from tests.assertions import assert_inventory_invariants
from tests.factories import (
    consume_item_reservations,
    create_catalog_and_stock,
    create_order_items,
    reserve_item,
)


pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


def _worker(session_factory, operation):
    def run(barrier):
        session = session_factory()
        try:
            with session.begin():
                barrier.wait()
                return operation(session)
        finally:
            session.close()
    return run


def test_two_checkouts_cannot_oversell_same_balance(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=5)
        _, _, item_ids = create_order_items(session, base, [4, 4])

    workers = [
        _worker(
            session_factory,
            lambda session, item_id=item_id, index=index: reserve_inventory(
                session=session,
                order_item_id=item_id,
                warehouse_id=base.warehouse_id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                idempotency_key=f"checkout-{index}",
            ),
        )
        for index, item_id in enumerate(item_ids)
    ]
    results, errors = concurrent_runner(workers)
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InsufficientSellableStockError)

    with session_factory() as session:
        balance = session.get(InventoryBalance, base.balance_id)
        assert balance.on_hand_quantity == 5
        assert balance.reserved_quantity == 4
        assert balance.available_quantity == 1
        assert session.scalar(
            select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.movement_type
                == InventoryMovementType.RESERVE
            )
        ) == 1
        assert_inventory_invariants(session)


def test_concurrent_same_reservation_is_idempotent(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, _, (item_id,) = create_order_items(session, base, [3])

    def operation(session):
        return reserve_inventory(
            session=session,
            order_item_id=item_id,
            warehouse_id=base.warehouse_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            idempotency_key="same-reservation",
        )

    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for _ in range(2)]
    )
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]

    with session_factory() as session:
        balance = session.get(InventoryBalance, base.balance_id)
        assert balance.reserved_quantity == 3
        assert session.scalar(
            select(func.count()).select_from(InventoryReservation)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.movement_type
                == InventoryMovementType.RESERVE
            )
        ) == 1
        assert_inventory_invariants(session)


def test_consume_and_release_cannot_both_win(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, _, (item_id,) = create_order_items(session, base, [2])
        (reservation_id,) = reserve_item(session, base, item_id)

    operations = [
        lambda session: consume_inventory_reservation(
            session=session, reservation_id=reservation_id
        ),
        lambda session: release_inventory_reservation(
            session=session, reservation_id=reservation_id
        ),
    ]
    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for operation in operations]
    )
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], InvalidReservationTransitionError)

    with session_factory() as session:
        reservation = session.get(InventoryReservation, reservation_id)
        balance = session.get(InventoryBalance, base.balance_id)
        assert reservation.status in {
            ReservationStatus.CONSUMED,
            ReservationStatus.RELEASED,
        }
        if reservation.status == ReservationStatus.CONSUMED:
            assert balance.reserved_quantity == 2
            assert reservation.consumed_at is not None
            assert reservation.released_at is None
        else:
            assert balance.reserved_quantity == 0
            assert reservation.released_at is not None
            assert reservation.consumed_at is None
        assert_inventory_invariants(session)


def test_concurrent_pick_is_applied_once(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, _, (item_id,) = create_order_items(session, base, [2])
        (reservation_id,) = reserve_item(session, base, item_id)
        consume_item_reservations(session, (reservation_id,))
        before_available = session.get(
            InventoryBalance, base.balance_id
        ).available_quantity

    operation = lambda session: pick_inventory_reservation(
        session=session, reservation_id=reservation_id
    )
    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for _ in range(2)]
    )
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]

    with session_factory() as session:
        balance = session.get(InventoryBalance, base.balance_id)
        assert balance.on_hand_quantity == 8
        assert balance.reserved_quantity == 0
        assert balance.available_quantity == before_available
        assert session.scalar(
            select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.movement_type == InventoryMovementType.PICK
            )
        ) == 1
        assert_inventory_invariants(session)


def test_concurrent_expiration_workers_do_not_double_release(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        _, _, item_ids = create_order_items(session, base, [1] * 6)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        for index, item_id in enumerate(item_ids):
            reserve_item(
                session,
                base,
                item_id,
                key=f"expire-{index}",
                expires_at=expires_at,
            )

    effective_now = expires_at + timedelta(hours=1)
    operation = lambda session: expire_inventory_reservations(
        session=session, now=effective_now, batch_size=100
    )
    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for _ in range(2)]
    )
    assert not errors
    assert sum(result.expired_count for result in results) == 6

    with session_factory() as session:
        reservations = list(session.scalars(select(InventoryReservation)))
        assert all(r.status == ReservationStatus.EXPIRED for r in reservations)
        assert session.scalar(
            select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.reference_type == "RESERVATION_EXPIRY"
            )
        ) == 6
        assert_inventory_invariants(session)
