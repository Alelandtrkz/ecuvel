from __future__ import annotations

from sqlalchemy import select

import pytest

from app.models import LogisticsPackageState, User
from app.services.inventory_counts import (
    PHYSICAL_COUNT_FINALIZED,
    PhysicalInventoryCountValidationError,
    finalize_physical_inventory_count,
    get_physical_count_stats,
    scan_physical_inventory_package,
    start_physical_inventory_count,
)
from tests.factories import create_catalog_and_stock
from tests.test_admin_inventory import _received_inbound


pytestmark = pytest.mark.integration


def test_count_freezes_baseline_and_scan_never_moves_package(session):
    base = create_catalog_and_stock(session)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    expected_package, expected_state, _ = _received_inbound(session, base, staff)
    count = start_physical_inventory_count(
        session,
        warehouse_id=base.warehouse_id,
        location_id=None,
        actor_user_id=staff.id,
    )
    assert get_physical_count_stats(session, count.id).expected == 1

    later_package, later_state, _ = _received_inbound(session, base, staff)
    assert get_physical_count_stats(session, count.id).expected == 1
    before_expected = (
        expected_state.current_warehouse_id,
        expected_state.current_location_id,
        expected_state.custodian_warehouse_id,
        expected_state.status,
    )
    before_later = (
        later_state.current_warehouse_id,
        later_state.current_location_id,
        later_state.custodian_warehouse_id,
        later_state.status,
    )

    expected = scan_physical_inventory_package(
        session,
        count_id=count.id,
        warehouse_id=base.warehouse_id,
        code=expected_package.package_code,
        actor_user_id=staff.id,
    )
    unexpected = scan_physical_inventory_package(
        session,
        count_id=count.id,
        warehouse_id=base.warehouse_id,
        code=later_package.package_code,
        actor_user_id=staff.id,
    )
    assert expected.scan.classification == "EXPECTED"
    assert unexpected.scan.classification == "UNEXPECTED"
    assert get_physical_count_stats(session, count.id).unexpected == 1
    session.flush()
    session.expire_all()
    assert (
        session.get(LogisticsPackageState, expected_state.id).current_warehouse_id,
        session.get(LogisticsPackageState, expected_state.id).current_location_id,
        session.get(LogisticsPackageState, expected_state.id).custodian_warehouse_id,
        session.get(LogisticsPackageState, expected_state.id).status,
    ) == before_expected
    assert (
        session.get(LogisticsPackageState, later_state.id).current_warehouse_id,
        session.get(LogisticsPackageState, later_state.id).current_location_id,
        session.get(LogisticsPackageState, later_state.id).custodian_warehouse_id,
        session.get(LogisticsPackageState, later_state.id).status,
    ) == before_later


def test_duplicate_scan_is_idempotent_and_finalize_is_immutable(session):
    base = create_catalog_and_stock(session)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    package, _state, _ = _received_inbound(session, base, staff)
    count = start_physical_inventory_count(
        session,
        warehouse_id=base.warehouse_id,
        location_id=None,
        actor_user_id=staff.id,
    )
    first = scan_physical_inventory_package(
        session,
        count_id=count.id,
        warehouse_id=base.warehouse_id,
        code=package.package_code,
        actor_user_id=staff.id,
    )
    duplicate = scan_physical_inventory_package(
        session,
        count_id=count.id,
        warehouse_id=base.warehouse_id,
        code=package.package_code.lower(),
        actor_user_id=staff.id,
    )
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert get_physical_count_stats(session, count.id).verified == 1

    finalized = finalize_physical_inventory_count(
        session,
        count_id=count.id,
        warehouse_id=base.warehouse_id,
        actor_user_id=staff.id,
    )
    assert finalized.status == PHYSICAL_COUNT_FINALIZED
    with pytest.raises(PhysicalInventoryCountValidationError):
        scan_physical_inventory_package(
            session,
            count_id=count.id,
            warehouse_id=base.warehouse_id,
            code=package.package_code,
            actor_user_id=staff.id,
        )


def test_only_one_open_count_per_point(session):
    base = create_catalog_and_stock(session)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    first = start_physical_inventory_count(
        session,
        warehouse_id=base.warehouse_id,
        location_id=None,
        actor_user_id=staff.id,
    )
    second = start_physical_inventory_count(
        session,
        warehouse_id=base.warehouse_id,
        location_id=None,
        actor_user_id=staff.id,
    )
    assert second.id == first.id
