"""Custody at an unexpected arrival point and at the assigned destination."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import InventoryMovement, LogisticsTrackingEvent, LogisticsTransfer, OrderPackage, StaffPointAssignment, User, Warehouse
from app.models.enums import LogisticsPackageStatus, LogisticsTrackingEventType as Event, LogisticsTransferStatus, StaffRole
from app.services.admin_permissions import AdminAuthorizationError
from app.services.logistics_tracking import (
    LogisticsConflictError, assign_package_transfer, confirm_package_pickup,
    receive_transfer_at_destination, report_logistics_incident,
)
from tests.factories import create_catalog_and_stock, create_ready_for_pickup_order
from tests.test_admin_rbac import denied, domain_state, staff_identity
from tests.test_admin_scanner import _set_point
from tests.test_admin_users import _login, _user
from tests.test_logistics_tracking import _received_package


@pytest.fixture
def route(session):
    package, order, _, (w1, _), (w2, loc2), (w3, loc3) = _received_package(session)
    admin = staff_identity(session, StaffRole.SUPER_ADMIN)
    t1 = staff_identity(session, StaffRole.TRANSPORT_OPERATOR)
    o2 = staff_identity(session, StaffRole.POINT_OPERATOR, point=w2)
    o3 = staff_identity(session, StaffRole.POINT_OPERATOR, point=w3)
    w1.name, w2.name, w3.name = "W1", "W2", "W3"
    t1.full_name = "Transportista T1 de prueba"
    transfer = assign_package_transfer(
        session, package_code=package.package_code, destination_warehouse_id=w2.id,
        responsible_user_id=t1.id, actor_user_id=admin.id,
    ).transfer
    pickup = confirm_package_pickup(
        session, package_code=package.package_code, actor_user_id=t1.id,
        expected_origin_warehouse_id=w1.id, idempotency_key="carrier-pickup",
    )
    session.commit()
    return SimpleNamespace(code=package.package_code, order=order, w1=w1, w2=w2, w3=w3,
                           loc2=loc2, loc3=loc3, t1=t1, o2=o2, o3=o3, admin=admin,
                           transfer=transfer, state=pickup.package_state)


def arrival_browser(app, user, warehouse):
    client = app.test_client()
    _login(client, user)
    _set_point(client, warehouse.id)
    return client


@pytest.mark.parametrize("identity", ["o3", "admin"])
def test_wrong_point_scan_offers_only_incident(app, session, route, identity):
    client = arrival_browser(app, getattr(route, identity), route.w3)
    before = domain_state(session)
    page = client.get("/admin/scanner/arrival", query_string={"code": route.code})
    assert page.status_code == 200
    assert b'name="operation" value="accept"' not in page.data
    assert b'name="operation" value="reject"' in page.data
    assert f"Destino asignado: {route.w2.name}." in page.get_data(as_text=True)
    assert "No recibir." in page.get_data(as_text=True)
    assert route.t1.full_name.encode() not in page.data
    assert route.order.encode() not in page.data
    assert route.transfer.transfer_code.encode() not in page.data
    assert domain_state(session) == before



def test_incident_service_preserves_custody_and_is_idempotent(session, route):
    params = dict(package_code=route.code, warehouse_id=route.w3.id, actor_user_id=route.o3.id,
                  reason="x" * 500, idempotency_key="incident-at-w3")
    before_moves = session.scalar(select(func.count(InventoryMovement.id)))
    first = report_logistics_incident(session, **params)
    session.commit()
    replay = report_logistics_incident(session, **params)
    assert replay.replayed and replay.event.id == first.event.id
    assert first.event.event_type == Event.INCIDENT_REPORTED
    assert first.event.actor_user_id == route.o3.id and first.event.warehouse_id == route.w3.id
    assert first.event.occurred_at and params["reason"] in first.event.notes
    assert first.event.previous_custodian_user_id == first.event.new_custodian_user_id == route.t1.id
    assert first.event.new_custodian_warehouse_id is None
    assert session.scalar(select(func.count(InventoryMovement.id))) == before_moves
    assert_in_transit(session, route)


def assert_in_transit(session, route):
    session.refresh(route.state)
    session.refresh(route.transfer)
    assert route.state.status == LogisticsPackageStatus.IN_TRANSIT
    assert route.state.expected_destination_warehouse_id == route.w2.id
    assert route.state.custodian_user_id == route.t1.id
    assert route.state.current_warehouse_id is None
    assert route.state.current_location_id is None
    assert route.state.custodian_warehouse_id is None
    assert not route.state.is_deviated
    assert route.transfer.status == LogisticsTransferStatus.IN_TRANSIT
    assert route.transfer.received_at is None
    assert session.scalar(select(func.count(LogisticsTransfer.id))) == 1


ARRIVAL = "/admin/scanner/arrival"


def incident_form(route):
    return {"package_code": route.code, "operation": "reject", "notes": "Etiqueta presentada en W3.",
            "idempotency_key": "report-wrong-point"}


@pytest.mark.parametrize("with_incident", [True, False], ids=["via-w3", "direct"])
def test_arrival_chain_of_custody(app, session, route, with_incident):
    state, transfer = route.state, route.transfer
    assert_in_transit(session, route)
    if with_incident:
        o3 = arrival_browser(app, route.o3, route.w3)
        for path in ("/admin/fulfillment", "/admin/inventory", "/admin/inventory/expected", "/admin/orders"):
            page = o3.get(path)
            assert page.status_code == 200
            assert route.code.encode() not in page.data and route.order.encode() not in page.data
        denied(session, o3, "GET", ARRIVAL, 404, query_string={"code": route.code[:-2]})
        denied(session, o3, "GET", f"/admin/fulfillment/{route.code}", 404)
        denied(session, o3, "GET", "/admin/scanner/package", 404, query_string={"code": route.code})
        before_moves = session.scalar(select(func.count(InventoryMovement.id)))
        assert o3.post(ARRIVAL, data=incident_form(route)).status_code == 302
        assert_in_transit(session, route)
        assert session.scalar(select(func.count(InventoryMovement.id))) == before_moves
        before_retry = domain_state(session)
        assert o3.post(ARRIVAL, data=incident_form(route)).status_code == 302
        assert domain_state(session) == before_retry
        incident = session.scalars(select(LogisticsTrackingEvent).where(
            LogisticsTrackingEvent.event_type == Event.INCIDENT_REPORTED,
        )).one()
        assert (incident.actor_user_id, incident.warehouse_id, incident.transfer_id) == (
            route.o3.id, route.w3.id, transfer.id,
        )
        assert incident.occurred_at and "Etiqueta presentada en W3." in incident.notes

    o2 = arrival_browser(app, route.o2, route.w2)
    page = o2.get(ARRIVAL, query_string={"code": route.code})
    assert page.status_code == 200 and b'name="operation" value="accept"' in page.data
    assert b'name="operation" value="reject"' not in page.data
    payload = {"package_code": route.code, "operation": "accept", "idempotency_key": "receive-w2",
               "received_location_id": str(route.loc2.id)}
    assert o2.post(ARRIVAL, data=payload).status_code == 302
    session.refresh(state)
    session.refresh(transfer)
    assert transfer.status == LogisticsTransferStatus.RECEIVED and transfer.received_at
    assert state.status == LogisticsPackageStatus.AT_POINT
    assert state.custodian_warehouse_id == state.current_warehouse_id == state.expected_destination_warehouse_id == route.w2.id
    assert state.current_location_id == route.loc2.id and state.custodian_user_id is None
    before_retry = domain_state(session)
    assert o2.post(ARRIVAL, data=payload).status_code == 302
    assert domain_state(session) == before_retry
    events = session.scalars(select(LogisticsTrackingEvent).where(
        LogisticsTrackingEvent.transfer_id == transfer.id,
    ).order_by(LogisticsTrackingEvent.occurred_at, LogisticsTrackingEvent.created_at)).all()
    types = [e.event_type for e in events]
    expected = [Event.TRANSFER_ASSIGNED, Event.PICKED_UP]
    if with_incident:
        expected.append(Event.INCIDENT_REPORTED)
    assert types[:len(expected)] == expected
    assert len(types) == len(expected) + 2
    assert set(types[len(expected):]) == {Event.ARRIVAL_SCAN, Event.RECEIVED_AT_DESTINATION}
    receipt = next(e for e in events if e.event_type == Event.RECEIVED_AT_DESTINATION)
    assert (receipt.actor_user_id, receipt.warehouse_id, receipt.previous_custodian_user_id) == (
        route.o2.id, route.w2.id, route.t1.id,
    )
    assert session.scalar(select(func.count(LogisticsTransfer.id))) == 1
    if with_incident:
        denied(session, o3, "POST", ARRIVAL, 404, data=incident_form(route))
        denied(session, o3, "GET", ARRIVAL, 404, query_string={"code": route.code})


@pytest.mark.parametrize("identity", ["o3", "admin"])
def test_physical_receipt_at_wrong_point_rejected_everywhere(app, session, route, identity):
    actor = getattr(route, identity)
    client = arrival_browser(app, actor, route.w3)
    denied(session, client, "POST", ARRIVAL, 409, data={
        "operation": "accept", "package_code": route.code, "received_location_id": str(route.loc3.id),
        "idempotency_key": "wrong-physical-receipt",
    })
    denied(session, client, "POST", f"/admin/fulfillment/{route.code}/receive", 409,
           data={"warehouse_id": str(route.w3.id)})
    before = domain_state(session)
    with pytest.raises(LogisticsConflictError):
        receive_transfer_at_destination(session, actor_user_id=actor.id, package_code=route.code,
                                        warehouse_id=route.w3.id, location_id=route.loc3.id)
    session.flush()
    assert domain_state(session) == before
    assert_in_transit(session, route)


def test_incident_requires_current_assignment_and_scanner_permission(app, session, route):
    external = _user(session)
    session.commit()
    for actor in (route.o2, route.t1, external):
        client = arrival_browser(app, actor, route.w3)
        denied(session, client, "GET", ARRIVAL, query_string={"code": route.code})
        denied(session, client, "POST", ARRIVAL, data=incident_form(route))
        before = domain_state(session)
        with pytest.raises(AdminAuthorizationError):
            report_logistics_incident(session, actor_user_id=actor.id, package_code=route.code,
                                      warehouse_id=route.w3.id, reason="Fuera de asignación")
        session.flush()
        assert domain_state(session) == before
        session.rollback()
    client = arrival_browser(app, route.o3, route.w3)
    assert client.get(ARRIVAL, query_string={"code": route.code}).status_code == 200
    assignment = session.scalars(select(StaffPointAssignment).where(
        StaffPointAssignment.staff_profile_id == route.o3.staff_profile.id,
    )).one()
    assignment.ends_at = datetime.now(timezone.utc)
    session.commit()
    denied(session, client, "POST", ARRIVAL, data=incident_form(route))


def test_arrival_post_requires_csrf_and_exact_code(app, session, route, monkeypatch):
    client = arrival_browser(app, route.o3, route.w3)
    denied(session, client, "POST", ARRIVAL, 404, data=dict(incident_form(route), package_code=route.code[:-1]))
    denied(session, client, "POST", ARRIVAL, 400, data=dict(incident_form(route), operation="unknown"))
    denied(session, client, "POST", ARRIVAL, 400, data=dict(incident_form(route), notes=" "))
    at_destination = arrival_browser(app, route.o2, route.w2)
    denied(session, at_destination, "POST", ARRIVAL, 400, data=incident_form(route))
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    denied(session, client, "POST", ARRIVAL, 400, data=incident_form(route))


@pytest.mark.parametrize("role", [None, StaffRole.POINT_OPERATOR, StaffRole.OPERATIONS_SUPERVISOR],
                         ids=["seed-demo-owner", "point-without-context", "network-supervisor"])
def test_demo_handover_uses_effective_permissions(app, session, role):
    runner = app.test_cli_runner()
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    if role is None:
        seed = runner.invoke(args=["seed-demo"])
        assert seed.exit_code == 0, (seed.output, seed.exception)
        demo = session.scalars(select(User).where(User.email == "admin@ecuvel.local")).one()
        assert not demo.is_ecuvel_staff
    else:
        point = session.get(Warehouse, base.warehouse_id) if role == StaffRole.POINT_OPERATOR else None
        demo = staff_identity(session, role, point=point)
        demo.email = demo.email_normalized = "admin@ecuvel.local"
    session.commit()
    before = domain_state(session)
    result = runner.invoke(args=[
        "handover-demo-order", "--order-number", ready.order_number, "--scan", ready.package_codes[0],
    ])
    if role != StaffRole.OPERATIONS_SUPERVISOR:
        assert result.exit_code != 0 and isinstance(result.exception, AdminAuthorizationError), result.exception
        assert domain_state(session) == before
    else:
        assert result.exit_code == 0, (result.output, result.exception)
        package = session.scalars(select(OrderPackage).where(OrderPackage.package_code == ready.package_codes[0])).one()
        assert package.handed_over_by_user_id == demo.id


def test_parallel_incident_retries_append_once(session, session_factory, concurrent_runner, route):
    params = dict(package_code=route.code, warehouse_id=route.w3.id, actor_user_id=route.o3.id,
                  reason="Llegada equivocada", idempotency_key="parallel-incident")

    def report(barrier):
        with session_factory.begin() as transaction:
            barrier.wait()
            return report_logistics_incident(transaction, **params).replayed

    results, errors = concurrent_runner((report, report))
    assert not errors
    assert sorted(results) == [False, True]
    assert session.scalar(select(func.count(LogisticsTrackingEvent.id)).where(
        LogisticsTrackingEvent.event_type == Event.INCIDENT_REPORTED,
    )) == 1
    assert_in_transit(session, route)
