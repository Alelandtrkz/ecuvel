from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    SellerInboundPackage,
    SellerOrder,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    LogisticsPackageStatus,
    LogisticsTrackingEventType,
    LogisticsTransferStatus,
    SellerInboundPackageStatus,
    UserStatus,
)
from app.services.admin_fulfillment import (
    get_admin_fulfillment_detail,
    get_admin_fulfillment_page,
)
from app.services.admin_orders import get_admin_order_detail
from app.services.logistics_tracking import (
    LogisticsConflictError,
    LogisticsValidationError,
    assign_package_transfer,
    confirm_package_pickup,
    receive_transfer_at_destination,
    register_received_inbound_package,
)
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


def _staff(session, *, active: bool = True) -> User:
    token = uuid.uuid4().hex[:10].upper()
    user = User(
        public_code=f"LOG-{token}",
        email=f"logistics-{token}@test.local",
        email_normalized=f"logistics-{token}@test.local",
        full_name="Responsable Logístico",
        status=UserStatus.ACTIVE,
        is_active=active,
        is_ecuvel_staff=True,
    )
    session.add(user)
    session.flush()
    return user


def _point(session, suffix: str) -> tuple[Warehouse, WarehouseLocation]:
    token = uuid.uuid4().hex[:8]
    warehouse = Warehouse(
        code=f"P-{suffix}-{token}",
        name=f"Punto {suffix}",
        address_line=f"Dirección {suffix}",
        city="Quito",
        country_code="EC",
        is_active=True,
    )
    session.add(warehouse)
    session.flush()
    location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"REC-{suffix}-{token}",
        barcode=f"LOC-{suffix}-{token}",
        name=f"Recepción {suffix}",
        location_type=LocationType.RECEIVING,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(location)
    session.flush()
    return warehouse, location


def _received_package(session):
    base = create_catalog_and_stock(session)
    order_id, order_number, _item_ids = create_order_items(session, base, [1])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    staff = _staff(session)
    point_a = session.get(Warehouse, base.warehouse_id)
    location_a = session.get(WarehouseLocation, base.receiving_location_id)
    point_b, location_b = _point(session, "B")
    point_f, location_f = _point(session, "F")
    token = uuid.uuid4().hex[:10].upper()
    package = SellerInboundPackage(
        seller_order_id=seller_order.id,
        package_code=f"PKG-{token}",
        barcode=f"PKG-{token}",
        status=SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
        ready_for_dropoff_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ready_for_dropoff_by_user_id=staff.id,
        received_at=datetime.now(timezone.utc),
        received_by_user_id=staff.id,
        received_location_id=location_a.id,
    )
    session.add(package)
    session.flush()
    register_received_inbound_package(
        session,
        package_id=package.id,
        location_id=location_a.id,
        actor_user_id=staff.id,
        idempotency_key=f"receive:{package.id}",
    )
    return package, order_number, staff, (point_a, location_a), (point_b, location_b), (point_f, location_f)


def test_package_moves_between_points_with_explicit_chain_of_custody(session):
    package, _order, staff, point_a, point_b, _point_f = _received_package(session)
    assigned = assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        vehicle_code="van-0012",
        eta_at=datetime.now(timezone.utc) + timedelta(hours=1),
        idempotency_key="assign-a-b",
    )
    assert assigned.package_state.status == LogisticsPackageStatus.ASSIGNED
    assert assigned.package_state.custodian_warehouse_id == point_a[0].id
    assert assigned.transfer.transfer_code.startswith("TRF-")
    assert assigned.transfer.vehicle_code == "VAN-0012"

    picked = confirm_package_pickup(
        session,
        package_code=package.package_code,
        actor_user_id=staff.id,
        idempotency_key="pickup-a-b",
    )
    assert picked.package_state.status == LogisticsPackageStatus.IN_TRANSIT
    assert picked.package_state.current_warehouse_id is None
    assert picked.package_state.custodian_user_id == staff.id

    received = receive_transfer_at_destination(
        session,
        package_code=package.package_code,
        warehouse_id=point_b[0].id,
        location_id=point_b[1].id,
        actor_user_id=staff.id,
        idempotency_key="arrival-b",
    )
    assert received.package_state.status == LogisticsPackageStatus.AT_POINT
    assert received.package_state.current_warehouse_id == point_b[0].id
    assert received.package_state.custodian_warehouse_id == point_b[0].id
    assert received.package_state.custodian_user_id is None
    assert received.transfer.status == LogisticsTransferStatus.RECEIVED
    event_types = set(session.scalars(
        select(LogisticsTrackingEvent.event_type).where(
            LogisticsTrackingEvent.package_state_id == received.package_state.id
        )
    ))
    assert {
        LogisticsTrackingEventType.RECEIVED_AT_POINT,
        LogisticsTrackingEventType.TRANSFER_ASSIGNED,
        LogisticsTrackingEventType.PICKED_UP,
        LogisticsTrackingEventType.ARRIVAL_SCAN,
        LogisticsTrackingEventType.RECEIVED_AT_DESTINATION,
    }.issubset(event_types)


def test_wrong_arrival_requires_correction_to_original_destination(session):
    package, _order, staff, _point_a, point_b, point_f = _received_package(session)
    assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        idempotency_key="assign-before-deviation",
    )
    confirm_package_pickup(
        session,
        package_code=package.package_code,
        actor_user_id=staff.id,
        idempotency_key="pickup-before-deviation",
    )
    deviated = receive_transfer_at_destination(
        session,
        package_code=package.package_code,
        warehouse_id=point_f[0].id,
        location_id=point_f[1].id,
        actor_user_id=staff.id,
        idempotency_key="wrong-arrival-f",
    )
    assert deviated.package_state.status == LogisticsPackageStatus.DEVIATED
    assert deviated.package_state.is_deviated
    assert deviated.package_state.current_warehouse_id == point_f[0].id
    assert deviated.package_state.expected_destination_warehouse_id == point_b[0].id

    with pytest.raises(LogisticsValidationError):
        assign_package_transfer(
            session,
            package_code=package.package_code,
            destination_warehouse_id=point_f[0].id,
            responsible_user_id=staff.id,
            actor_user_id=staff.id,
            corrective=True,
        )

    correction = assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        corrective=True,
        idempotency_key="correction-f-b",
    )
    assert correction.transfer.is_corrective
    assert correction.transfer.previous_transfer_id == deviated.transfer.id
    confirm_package_pickup(
        session,
        package_code=package.package_code,
        actor_user_id=staff.id,
        idempotency_key="corrective-pickup",
    )
    resolved = receive_transfer_at_destination(
        session,
        package_code=package.package_code,
        warehouse_id=point_b[0].id,
        location_id=point_b[1].id,
        actor_user_id=staff.id,
        idempotency_key="corrective-arrival",
    )
    assert resolved.package_state.status == LogisticsPackageStatus.AT_POINT
    assert not resolved.package_state.is_deviated


def test_events_are_idempotent_and_append_only(session):
    package, _order, staff, _point_a, point_b, _point_f = _received_package(session)
    first = assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        idempotency_key="same-assignment",
    )
    replay = assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        idempotency_key="same-assignment",
    )
    assert replay.replayed
    assert replay.transfer.id == first.transfer.id
    assert session.scalar(select(func.count(LogisticsTransfer.id))) == 1
    event = first.event
    event.notes = "Intento de mutación"
    with pytest.raises(ValueError, match="append-only"):
        session.flush()
    session.rollback()


def test_inactive_point_and_non_staff_responsible_are_rejected(session):
    package, _order, staff, _point_a, point_b, _point_f = _received_package(session)
    point_b[0].is_active = False
    with pytest.raises(LogisticsValidationError, match="inactivo"):
        assign_package_transfer(
            session,
            package_code=package.package_code,
            destination_warehouse_id=point_b[0].id,
            responsible_user_id=staff.id,
            actor_user_id=staff.id,
        )
    point_b[0].is_active = True
    outsider = User(
        public_code=f"OUT-{uuid.uuid4().hex[:10]}",
        email=f"outsider-{uuid.uuid4().hex[:10]}@test.local",
        full_name="Usuario externo",
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=False,
    )
    session.add(outsider)
    session.flush()
    from app.services.logistics_tracking import LogisticsAccessError
    with pytest.raises(LogisticsAccessError):
        assign_package_transfer(
            session,
            package_code=package.package_code,
            destination_warehouse_id=point_b[0].id,
            responsible_user_id=outsider.id,
            actor_user_id=staff.id,
        )


def test_admin_fulfillment_query_and_private_routes(session, app):
    package, order_number, staff, point_a, point_b, _point_f = _received_package(session)
    assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=point_b[0].id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
    )
    session.commit()
    page = get_admin_fulfillment_page(session, status="awaiting-pickup", query=package.package_code)
    assert page.total_items == 1
    assert page.rows[0].order_number == order_number
    assert get_admin_fulfillment_page(
        session, point=point_a[0].code
    ).total_items == 1
    assert get_admin_fulfillment_page(
        session, destination=point_b[0].code
    ).total_items == 1
    detail = get_admin_fulfillment_detail(session, package_code=package.package_code)
    assert detail is not None
    assert detail.can_pickup

    client = app.test_client()
    assert client.get("/admin/fulfillment").status_code == 302
    with client.session_transaction() as browser:
        browser["_user_id"] = str(staff.id)
        browser["_fresh"] = True
    response = client.get("/admin/fulfillment")
    assert response.status_code == 200
    assert package.package_code.encode() in response.data
    detail_response = client.get(f"/admin/fulfillment/{package.package_code}")
    assert detail_response.status_code == 200
    assert b"Historial de trazabilidad" in detail_response.data
    order_detail = get_admin_order_detail(session, order_number=order_number)
    assert order_detail.inbound_packages[0].tracking_available
    assert order_detail.inbound_packages[0].package_code == package.package_code
    pickup_response = client.post(
        f"/admin/fulfillment/{package.package_code}/pickup",
        data={"idempotency_key": "route-pickup"},
    )
    assert pickup_response.status_code == 302
    session.expire_all()
    state = session.scalar(
        select(LogisticsPackageState).where(
            LogisticsPackageState.seller_inbound_package_id == package.id
        )
    )
    assert state.status == LogisticsPackageStatus.IN_TRANSIT


def test_parallel_assignments_create_only_one_active_transfer(
    session, session_factory, concurrent_runner
):
    package, _order, staff, _point_a, point_b, point_f = _received_package(session)
    package_code = package.package_code
    staff_id = staff.id
    destination_ids = (point_b[0].id, point_f[0].id)
    session.commit()

    def worker(destination_id, key):
        def run(barrier):
            database_session = session_factory()
            try:
                barrier.wait()
                try:
                    with database_session.begin():
                        assign_package_transfer(
                            database_session,
                            package_code=package_code,
                            destination_warehouse_id=destination_id,
                            responsible_user_id=staff_id,
                            actor_user_id=staff_id,
                            idempotency_key=key,
                        )
                except LogisticsConflictError:
                    return "conflict"
                return "created"
            finally:
                database_session.close()
        return run

    results, errors = concurrent_runner((
        worker(destination_ids[0], "parallel-1"),
        worker(destination_ids[1], "parallel-2"),
    ))
    assert not errors
    assert sorted(results) == ["conflict", "created"]
    assert session.scalar(
        select(func.count(LogisticsTransfer.id)).where(
            LogisticsTransfer.status.in_((
                LogisticsTransferStatus.ASSIGNED,
                LogisticsTransferStatus.IN_TRANSIT,
            ))
        )
    ) == 1
