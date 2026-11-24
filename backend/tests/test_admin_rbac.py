"""Server-side authorization for internal marketplace operations."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, inspect, select, text

from app.models import AdminAuditEvent, StaffPointAssignment, StaffProfile, Warehouse
from app.models.enums import StaffEmploymentStatus, StaffIdentificationType, StaffRole
from app.services import admin_permissions
from app.services.admin_users import create_staff_member, update_staff_profile
from app.services.inventory_counts import start_physical_inventory_count
from app.services.logistics_tracking import assign_package_transfer
from app.services.partner_onboarding import review_onboarding
from app.services.product_publication import record_moderation_decision
from tests.factories import create_catalog_and_stock, create_order_items
from tests.test_admin_inventory import _received_inbound, _set_point
from tests.test_admin_product_moderation import _base as moderation_fixture
from tests.test_admin_scanner import _ready_inbound
from tests.test_admin_stores import _submitted_onboarding
from tests.test_admin_users import _login, _user, _warehouse


def staff_identity(session, role, *, point=None, employment=StaffEmploymentStatus.ACTIVE):
    user = _user(session, staff=True)
    profile = StaffProfile(
        user=user, identification_type=StaffIdentificationType.PASSPORT,
        identification_number_normalized=uuid.uuid4().hex[:15],
        nationality_code="ECU", role=role, employment_status=employment,
    )
    session.add(profile)
    if point is not None:
        session.add(StaffPointAssignment(
            staff_profile=profile, warehouse_id=point.id, created_by_user_id=user.id,
            starts_at=datetime.now(timezone.utc), is_primary=True,
        ))
    session.flush()
    return user


def test_transport_cannot_open_document_handler(app, session):
    actor = staff_identity(session, StaffRole.TRANSPORT_OPERATOR)
    session.commit()
    client = app.test_client()
    _login(client, actor)
    before_state = domain_state(session)
    before = session.scalar(select(func.count()).select_from(AdminAuditEvent))
    response = client.get(f"/admin/stores/{uuid.uuid4()}/documents/{uuid.uuid4()}")
    assert response.status_code == 403
    assert response.mimetype != "application/pdf"
    assert domain_state(session) == before_state
    assert session.scalar(select(func.count()).select_from(AdminAuditEvent)) == before


def test_legacy_delegation_policy_excludes_super_admin(session):
    actor = _user(session, staff=True)
    assert StaffRole.SUPER_ADMIN not in admin_permissions.delegable_staff_roles(actor)



def domain_state(session):
    # Use the real migrated test schema, including tables with composite keys.
    # Compare values as well as row counts to catch in-place changes.
    if "rbac_snapshot" not in session.info:
        engine = session.get_bind()
        quote = engine.dialect.identifier_preparer.quote
        tables = sorted(inspect(engine).get_table_names())
        columns = [
            f"(SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r))::text) FROM {quote(table)} r)"
            for table in tables
        ]
        session.info["rbac_snapshot"] = text("SELECT " + ", ".join(columns))
    return tuple(session.execute(session.info["rbac_snapshot"]).one())


def denied(session, client, method, path, status=403, **kwargs):
    before = domain_state(session)
    response = client.open(path, method=method, **kwargs)
    assert response.status_code == status, (method, path, response.status_code)
    assert domain_state(session) == before, (method, path)
    return response


# The expected routes are independent of the production permission table.
_ADMIN_READS = {"document", "products", "stores", "scanner", "inventory", "fulfillment", "orders", "staff"}
_OPS_READS = {"scanner", "inventory", "fulfillment", "orders"}


@pytest.mark.parametrize(("identity", "allowed"), [
    ("buyer", set()), ("owner", set()),
    ("SUPER_ADMIN", _ADMIN_READS), ("legacy", _ADMIN_READS),
    ("OPERATIONS_SUPERVISOR", _OPS_READS | {"staff"}),
    ("W1", _OPS_READS), ("W2", _OPS_READS),
    ("TRANSPORT_OPERATOR", {"scanner", "fulfillment"}),
    ("DELIVERY", {"scanner", "fulfillment", "orders"}),
    ("SUPPORT", {"orders"}), ("suspended", set()), ("disabled", set()),
])
def test_admin_read_matrix(app, session, tmp_path, monkeypatch, identity, allowed):
    monkeypatch.setitem(app.config, "PARTNER_DOCUMENT_UPLOAD_DIR", str(tmp_path))
    owner, _, onboarding, document = _submitted_onboarding(session, app)
    if identity == "owner":
        actor = owner
    elif identity in {"buyer", "legacy", "disabled"}:
        actor = _user(session, staff=identity != "buyer", active=identity != "disabled")
    elif identity in {"W1", "W2"}:
        actor = staff_identity(session, StaffRole.POINT_OPERATOR, point=_warehouse(session))
    elif identity == "suspended":
        actor = staff_identity(session, StaffRole.SUPER_ADMIN, employment=StaffEmploymentStatus.SUSPENDED)
    else:
        actor = staff_identity(session, StaffRole(identity))
    session.commit()
    client = app.test_client()
    _login(client, actor)
    paths = {
        "document": f"/admin/stores/{onboarding.id}/documents/{document.id}",
        "products": "/admin/products", "stores": "/admin/stores",
        "scanner": "/admin/scanner", "inventory": "/admin/inventory",
        "fulfillment": "/admin/fulfillment", "orders": "/admin/orders",
        "staff": "/admin/users/staff",
    }
    for area, path in paths.items():
        if area in allowed:
            response = client.get(path)
            assert response.status_code == 200, (area, identity)
            if area == "document":
                assert response.data == b"%PDF-1.4\n% store moderation test\n%%EOF\n"
        else:
            response = denied(session, client, "GET", path, 302 if identity == "disabled" else 403)
            if area == "document":
                assert b"store moderation test" not in response.data
                assert b"%PDF" not in response.data
    if identity not in {"SUPER_ADMIN", "disabled"}:
        denied(session, client, "POST", f"/admin/stores/{onboarding.id}/approve")


def test_foreign_resources_and_revoked_context(app, session):
    first, second = create_catalog_and_stock(session), create_catalog_and_stock(session)
    supervisor = staff_identity(session, StaffRole.OPERATIONS_SUPERVISOR)
    fixtures = []
    for base in (first, second):
        point = session.get(Warehouse, base.warehouse_id)
        operator = staff_identity(session, StaffRole.POINT_OPERATOR, point=point)
        package, _, number = _received_inbound(session, base, supervisor)
        count = start_physical_inventory_count(
            session, warehouse_id=point.id, location_id=None, actor_user_id=supervisor.id,
        )
        fixtures.append((operator, point, package.package_code, number, count.id))
    session.commit()

    for i, (actor, point, package, order, count_id) in enumerate(fixtures):
        _, foreign_point, foreign_package, foreign_order, foreign_count = fixtures[1 - i]
        client = app.test_client()
        _login(client, actor)
        assert client.post("/admin/scanner/context", data={"warehouse_id": str(point.id)}).status_code == 302
        for path in ("/admin/scanner", "/admin/inventory", "/admin/fulfillment", "/admin/orders"):
            page = client.get(path)
            assert page.status_code == 200
            assert foreign_package.encode() not in page.data
            assert foreign_order.encode() not in page.data
        for path in (f"/admin/fulfillment/{package}", f"/admin/orders/{order}",
                     f"/admin/inventory/counts/{count_id}", f"/admin/scanner/package?code={package}"):
            assert client.get(path).status_code == 200, path
        for path in (f"/admin/fulfillment/{foreign_package}", f"/admin/fulfillment/{foreign_package}/label",
                     f"/admin/orders/{foreign_order}", f"/admin/inventory/counts/{foreign_count}",
                     f"/admin/scanner/package?code={foreign_package}"):
            denied(session, client, "GET", path, 404)
        search = client.get(f"/admin/search?q={foreign_order}")
        assert search.status_code == 200
        assert f'/admin/orders/{foreign_order}'.encode() not in search.data
        denied(session, client, "POST", "/admin/scanner/context", data={"warehouse_id": str(foreign_point.id)})
        denied(session, client, "POST", f"/admin/inventory/counts/{foreign_count}/finalize", data={"confirmed": "1"})
        denied(session, client, "POST", f"/admin/fulfillment/{foreign_package}/receive", data={"warehouse_id": str(point.id)})
        denied(session, client, "POST", "/admin/scanner/receive", data={
            "idempotency_key": "out-of-point", "package_code": foreign_package,
            "received_location_id": str((first, second)[1 - i].receiving_location_id),
        })
        _set_point(client, foreign_point.id)
        denied(session, client, "GET", "/admin/inventory/stock")
        denied(session, client, "POST", "/admin/scanner/arrival", data={
            "idempotency_key": "test-revoked", "operation": "reject", "package_code": foreign_package,
        })

    actor, point, *_ = fixtures[0]
    client = app.test_client()
    _login(client, actor)
    _set_point(client, point.id)
    assignment = session.scalar(select(StaffPointAssignment).where(
        StaffPointAssignment.staff_profile_id == actor.staff_profile.id,
    ))
    assignment.ends_at = datetime.now(timezone.utc)
    session.commit()
    denied(session, client, "GET", "/admin/inventory")
    denied(session, client, "POST", "/admin/scanner/transport", data={"idempotency_key": "expired"})


def test_point_operator_can_receive_first_physical_handoff_at_assigned_point(app, session):
    base, _, package, item, order = _ready_inbound(session, quantity=1)
    actor = staff_identity(session, StaffRole.POINT_OPERATOR, point=session.get(Warehouse, base.warehouse_id))
    session.commit()
    client = app.test_client()
    _login(client, actor)
    _set_point(client, base.warehouse_id)
    denied(session, client, "GET", f"/admin/orders/{order}", 404)
    denied(session, client, "GET", f"/admin/scanner/package?code={package.package_code}", 404)
    identified = client.get(f"/admin/scanner/receive?code={package.package_code}")
    assert identified.status_code == 200 and item.seller_sku_snapshot.encode() in identified.data
    response = client.post("/admin/scanner/receive", data={
        "idempotency_key": "first-physical-handoff", "package_code": package.package_code,
        "received_location_id": str(base.receiving_location_id), "verified_product_codes": item.seller_sku_snapshot,
    })
    assert response.status_code == 302
    session.refresh(package)
    assert package.received_by_user_id == actor.id
    assert package.received_location_id == base.receiving_location_id
    assert client.get(f"/admin/orders/{order}").status_code == 200


@pytest.mark.parametrize("role", [StaffRole.TRANSPORT_OPERATOR, StaffRole.DELIVERY])
def test_transport_reads_and_changes_only_personal_transfer(app, session, role):
    base = create_catalog_and_stock(session)
    supervisor = staff_identity(session, StaffRole.OPERATIONS_SUPERVISOR)
    selected = staff_identity(session, role)
    other = staff_identity(session, role)
    destination = _warehouse(session)
    package, state, order = _received_inbound(session, base, supervisor)
    assigned = assign_package_transfer(
        session, actor_user_id=supervisor.id, package_code=package.package_code,
        destination_warehouse_id=destination.id, responsible_user_id=selected.id,
    )
    session.commit()
    client = app.test_client()
    _login(client, other)
    path = f"/admin/fulfillment/{package.package_code}"
    denied(session, client, "GET", path, 404)
    denied(session, client, "POST", f"{path}/pickup")
    _login(client, selected)
    assert client.get(path).status_code == 200
    if role == StaffRole.DELIVERY:
        assert client.get(f"/admin/orders/{order}").status_code == 200
    else:
        denied(session, client, "GET", f"/admin/orders/{order}")
    assert client.post(f"{path}/pickup").status_code == 302
    session.refresh(state)
    session.refresh(assigned.transfer)
    assert state.custodian_user_id == selected.id
    assert assigned.transfer.status.value == "IN_TRANSIT"


def staff_form(role):
    return dict(first_names="Ana", last_names="Prueba", email="synthetic-role@test.local",
                phone="", identification_type="PASSPORT", identification_number="QA123456",
                nationality_code="ECU", role=role, employment_status="ACTIVE")


def test_delegation_bounds_in_routes_and_services(app, session):
    legacy = _user(session, staff=True)
    superior = staff_identity(session, StaffRole.SUPER_ADMIN)
    target = staff_identity(session, StaffRole.SUPPORT)
    operator = staff_identity(session, StaffRole.POINT_OPERATOR, point=_warehouse(session))
    session.commit()
    create_url = "/admin/users/staff/new"
    edit_url = f"/admin/users/staff/{target.staff_profile.employee_code}/edit"
    super_url = f"/admin/users/staff/{superior.staff_profile.employee_code}"
    client = app.test_client()
    for actor in (legacy, operator):
        _login(client, actor)
        denied(session, client, "POST", create_url, data=staff_form("SUPER_ADMIN"))
        denied(session, client, "POST", edit_url, data=staff_form("SUPER_ADMIN"))
        own_form = dict(staff_form("SUPER_ADMIN"), email=actor.email, link_existing_user="1")
        denied(session, client, "POST", create_url, data=own_form)
        before = domain_state(session)
        with pytest.raises(admin_permissions.AdminAuthorizationError):
            create_staff_member(session, actor_user_id=actor.id, **staff_form("SUPER_ADMIN"),
                                employment_started_at=None, warehouse_id=None, invitation_ttl_minutes=60)
        session.flush()
        assert domain_state(session) == before
        with pytest.raises(admin_permissions.AdminAuthorizationError):
            update_staff_profile(session, profile=target.staff_profile, actor_user_id=actor.id, role="SUPER_ADMIN",
                                 employment_status="ACTIVE", warehouse_id=None, phone="", reason="test")
        session.flush()
        assert domain_state(session) == before
        session.rollback()

    _login(client, legacy)
    assert b'<option value="SUPER_ADMIN"' not in client.get(create_url).data
    denied(session, client, "POST", f"{super_url}/edit", data=staff_form("SUPPORT"))
    denied(session, client, "POST", f"{super_url}/access", data={"action": "disable", "reason": "test"})
    assert client.post(edit_url, data=staff_form("DELIVERY")).status_code == 302
    session.refresh(target.staff_profile)
    assert target.staff_profile.role == StaffRole.DELIVERY

    _login(client, superior)
    assert client.post(edit_url, data=staff_form("SUPER_ADMIN")).status_code == 302
    session.refresh(target.staff_profile)
    assert target.staff_profile.role == StaffRole.SUPER_ADMIN
    audit = session.scalars(select(AdminAuditEvent).where(
        AdminAuditEvent.target_user_id == target.id, AdminAuditEvent.action == "STAFF_UPDATED",
    ).order_by(AdminAuditEvent.created_at)).all()
    assert [(row.actor_user_id, row.metadata_json["previous_role"], row.metadata_json["role"]) for row in audit] == [
        (legacy.id, "SUPPORT", "DELIVERY"), (superior.id, "DELIVERY", "SUPER_ADMIN"),
    ]


def test_moderation_denials_with_real_targets(app, session, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "PARTNER_DOCUMENT_UPLOAD_DIR", str(tmp_path / "docs"))
    _, _, onboarding, _ = _submitted_onboarding(session, app)
    *_, draft = moderation_fixture(session, tmp_path)
    limited = staff_identity(session, StaffRole.OPERATIONS_SUPERVISOR)
    legacy = _user(session, staff=True)
    session.commit()
    client = app.test_client()
    _login(client, limited)
    for action in ("reject", "approve", "request-changes"):
        denied(session, client, "POST", f"/admin/products/{draft.id}/{action}",
               data={"reason_code": "INCORRECT_SPECIFICATIONS", "note": "Contenido de prueba."})
    denied(session, client, "GET", f"/admin/products/{draft.id}/preview")
    denied(session, client, "POST", f"/admin/stores/{onboarding.id}/request-corrections")
    before = domain_state(session)
    with pytest.raises(admin_permissions.AdminAuthorizationError):
        record_moderation_decision(session, draft_id=draft.id, actor_user_id=limited.id,
                                   decision="REJECTED", checklist={})
    session.flush()
    assert domain_state(session) == before
    with pytest.raises(admin_permissions.AdminAuthorizationError):
        review_onboarding(session=session, onboarding_id=onboarding.id, reviewer_user_id=legacy.id,
                          decision="approve")
    session.flush()
    assert domain_state(session) == before
    session.rollback()

    super_admin = staff_identity(session, StaffRole.SUPER_ADMIN)
    session.commit()
    _login(client, super_admin)
    response = client.post(f"/admin/products/{draft.id}/request-changes", data={
        "reason_code": "INCORRECT_SPECIFICATIONS", "note": "Corregir los datos técnicos de la ficha.",
    })
    assert response.status_code == 302
    session.refresh(draft)
    assert draft.status.value == "CHANGES_REQUESTED"
    assert [event.actor_user_id for event in draft.moderation_events] == [super_admin.id]


def test_mixed_order_requires_every_line_to_be_related(app, session):
    first, second = create_catalog_and_stock(session), create_catalog_and_stock(session)
    supervisor = staff_identity(session, StaffRole.OPERATIONS_SUPERVISOR)
    operator = staff_identity(session, StaffRole.POINT_OPERATOR, point=session.get(Warehouse, first.warehouse_id))
    order_id, order_number, (local_line, foreign_line) = create_order_items(session, first, [1, 1])
    local_package, _, _ = _received_inbound(
        session, first, supervisor, order_data=(order_id, order_number, (local_line,)),
    )
    foreign_package, _, _ = _received_inbound(
        session, second, supervisor, order_data=(order_id, order_number, (foreign_line,)),
    )
    session.commit()
    client = app.test_client()
    _login(client, operator)
    denied(session, client, "GET", f"/admin/orders/{order_number}", 404)
    assert f'/admin/orders/{order_number}'.encode() not in client.get(f"/admin/search?q={order_number}").data
    assert order_number.encode() not in client.get("/admin/orders").data
    _login(client, supervisor)
    page = client.get(f"/admin/orders/{order_number}")
    assert page.status_code == 200
    assert local_package.package_code.encode() in page.data
    assert foreign_package.package_code.encode() in page.data
