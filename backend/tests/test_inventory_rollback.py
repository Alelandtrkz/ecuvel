from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import InventoryMovementType, ReservationStatus
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
)
from app.services.inventory import (
    InvalidReservationTransitionError,
    ReservationBalanceIntegrityError,
    pick_inventory_reservation,
    release_inventory_reservation,
)
from tests.factories import (
    consume_item_reservations,
    create_catalog_and_stock,
    create_order_items,
    reserve_item,
)


pytestmark = pytest.mark.integration


def test_outer_transaction_rolls_back_previous_pick_when_later_pick_fails(
    session_factory,
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, _, item_ids = create_order_items(session, base, [2, 2])
        reservation_ids = []
        for item_id in item_ids:
            ids = reserve_item(session, base, item_id)
            consume_item_reservations(session, ids)
            reservation_ids.extend(ids)

    with session_factory.begin() as session:
        balance = session.get(InventoryBalance, base.balance_id)
        balance.reserved_quantity = 3

    with pytest.raises(ReservationBalanceIntegrityError):
        with session_factory.begin() as session:
            pick_inventory_reservation(
                session=session, reservation_id=reservation_ids[0]
            )
            pick_inventory_reservation(
                session=session, reservation_id=reservation_ids[1]
            )

    with session_factory() as session:
        balance = session.get(InventoryBalance, base.balance_id)
        assert balance.on_hand_quantity == 10
        assert balance.reserved_quantity == 3
        assert session.scalar(
            select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.movement_type == InventoryMovementType.PICK
            )
        ) == 0


def test_failed_release_of_consumed_reservation_changes_nothing(
    session_factory,
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        _, _, (item_id,) = create_order_items(session, base, [2])
        (reservation_id,) = reserve_item(session, base, item_id)
        consume_item_reservations(session, (reservation_id,))

    with session_factory() as session:
        reservation = session.get(InventoryReservation, reservation_id)
        before = (
            reservation.status,
            reservation.consumed_at,
            reservation.released_at,
            session.get(InventoryBalance, base.balance_id).reserved_quantity,
            session.scalar(select(func.count()).select_from(InventoryMovement)),
        )

    with pytest.raises(InvalidReservationTransitionError):
        with session_factory.begin() as session:
            release_inventory_reservation(
                session=session, reservation_id=reservation_id
            )

    with session_factory() as session:
        reservation = session.get(InventoryReservation, reservation_id)
        after = (
            reservation.status,
            reservation.consumed_at,
            reservation.released_at,
            session.get(InventoryBalance, base.balance_id).reserved_quantity,
            session.scalar(select(func.count()).select_from(InventoryMovement)),
        )
        assert before == after
        assert reservation.status == ReservationStatus.CONSUMED
