from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import PackageStatus
from app.models.fulfillment import OrderPackage
from app.services.fulfillment import (
    create_packages_for_order,
    handover_order_packages,
)
from tests.assertions import assert_fulfillment_invariants
from tests.factories import (
    create_catalog_and_stock,
    create_picked_order,
    create_ready_for_pickup_order,
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


def test_concurrent_handover_is_idempotent(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        ready = create_ready_for_pickup_order(session, base, [2, 3])

    def operation(session):
        return handover_order_packages(
            session=session,
            order_number=ready.order_number,
            scanned_codes=ready.package_codes,
            actor_user_id=base.operator_id,
            notes="original note",
        )

    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for _ in range(2)]
    )
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]

    with session_factory() as session:
        packages = list(session.scalars(select(OrderPackage)))
        assert all(p.status == PackageStatus.HANDED_OVER for p in packages)
        assert len({p.handed_over_at for p in packages}) == 1
        assert all(p.handover_notes == "original note" for p in packages)
        assert_fulfillment_invariants(session)


def test_concurrent_package_creation_does_not_duplicate_packages(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=20)
        _, order_number, _ = create_picked_order(session, base, [2, 3])

    operation = lambda session: create_packages_for_order(
        session=session, order_number=order_number
    )
    results, errors = concurrent_runner(
        [_worker(session_factory, operation) for _ in range(2)]
    )
    assert not errors
    assert sorted(result.created_count for result in results) == [0, 2]

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(OrderPackage)
        ) == 2
        assert_fulfillment_invariants(session)
