from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.models import (
    AdminAuditEvent,
    StaffAccessInvitation,
    StaffPointAssignment,
    StaffProfile,
    Store,
    User,
    Warehouse,
)
from app.models.enums import (
    StaffEmploymentStatus,
    StaffOperationalStatus,
    StaffRole,
    StoreStatus,
    UserStatus,
)
from app.services.admin_permissions import permissions_for_user
from app.services.admin_users import (
    AdminUserError,
    accept_staff_invitation,
    create_staff_member,
    get_admin_staff_detail,
    get_admin_staff_page,
    operational_warehouses,
    revoke_staff_invitations,
    set_staff_access,
    validate_ecuadorian_cedula,
)
from app.services.mail import mail_service


@pytest.fixture
def client(app):
    return app.test_client()


def _user(session, *, staff=False, active=True, name="Admin Test", email=None):
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}", email=email or f"{token}@test.local",
        email_normalized=(email or f"{token}@test.local").casefold(),
        password_hash=generate_password_hash("ValidPassword123!"), full_name=name,
        status=UserStatus.ACTIVE if active else UserStatus.SUSPENDED,
        is_active=active, is_ecuvel_staff=staff,
    )
    session.add(user); session.flush()
    return user


def _warehouse(session, *, seller=False):
    token = uuid.uuid4().hex[:8]
    store = None
    if seller:
        store = Store(
            public_code=f"STR-{token}",
            product_code_prefix=token[:3].upper(),
            name=f"Tienda {token}",
            slug=f"tienda-{token}",
            status=StoreStatus.DRAFT,
            is_verified=False,
        )
        session.add(store)
        session.flush()
    warehouse = Warehouse(
        code=f"WH-{token}", name=f"Punto {token}", address_line="Calle 1",
        city="Guayaquil", country_code="EC", is_active=True,
        seller_store_id=store.id if store else None,
    )
    session.add(warehouse); session.flush()
    return warehouse


def _login(client, user):
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def test_ecuadorian_cedula_validation_is_mathematical():
    assert validate_ecuadorian_cedula("1710034065")
    assert validate_ecuadorian_cedula("0926687856")
    assert not validate_ecuadorian_cedula("1712345678")
    assert not validate_ecuadorian_cedula("2510034065")
    assert not validate_ecuadorian_cedula("1760034065")
    assert not validate_ecuadorian_cedula("17A0034065")


def test_users_requires_internal_staff(client, session):
    assert client.get("/admin/users").status_code == 302
    buyer = _user(session); session.commit(); _login(client, buyer)
    assert client.get("/admin/users").status_code == 403


def test_legacy_staff_can_open_real_client_listing_and_detail(client, session):
    admin = _user(session, staff=True)
    buyer = _user(session, name="María Cliente", email="maria@example.com")
    session.commit(); _login(client, admin)
    response = client.get("/admin/users?q=maria@example.com")
    assert response.status_code == 200
    assert b"Mar\xc3\xada Cliente" in response.data
    response = client.get(f"/admin/users/{buyer.public_account_code}")
    assert response.status_code == 200
    assert buyer.public_account_code.encode() in response.data


def test_staff_creation_reuses_user_identity_and_generates_stable_employee_code(session):
    admin = _user(session, staff=True)
    point = _warehouse(session)
    created = create_staff_member(
        session, actor_user_id=admin.id, first_names="Ana María", last_names="Pérez Ruiz",
        email="ana.staff@ecuvel.com", phone="+593991234567",
        identification_type="ECUADOR_CEDULA", identification_number="1710034065",
        nationality_code="ECU", role="POINT_OPERATOR", employment_status="PENDING",
        employment_started_at=date(2026, 8, 20), warehouse_id=point.id,
        invitation_ttl_minutes=60,
    )
    session.commit()
    profile = session.scalar(select(StaffProfile).where(StaffProfile.id == created.profile.id))
    assert profile.employee_code.startswith("EMP-")
    assert profile.user.staff_profile.id == profile.id
    assert profile.user.is_ecuvel_staff
    assert profile.user.password_hash is None
    assert created.invitation_token
    invitation = session.scalar(select(StaffAccessInvitation).where(StaffAccessInvitation.staff_profile_id == profile.id))
    assert invitation.token_hash == hashlib.sha256(created.invitation_token.encode()).hexdigest()
    assert created.invitation_token not in invitation.token_hash


def test_seller_warehouse_is_never_an_operational_assignment(session):
    admin = _user(session, staff=True)
    seller_warehouse = _warehouse(session, seller=True)
    session.commit()
    assert seller_warehouse.id not in {item.id for item in operational_warehouses(session)}
    with pytest.raises(AdminUserError, match="Punto ECUVEL"):
        create_staff_member(
            session, actor_user_id=admin.id, first_names="Juan", last_names="Operador",
            email="juan.staff@ecuvel.com", phone="", identification_type="PASSPORT",
            identification_number="PASS-123", nationality_code="ECU", role="POINT_OPERATOR",
            employment_status="PENDING", employment_started_at=None,
            warehouse_id=seller_warehouse.id, invitation_ttl_minutes=60,
        )


def test_invitation_is_single_use_and_enables_same_user(session):
    admin = _user(session, staff=True); point = _warehouse(session)
    created = create_staff_member(
        session, actor_user_id=admin.id, first_names="Ana", last_names="Operadora",
        email="invite@ecuvel.com", phone="", identification_type="PASSPORT",
        identification_number="PASS-456", nationality_code="ECU", role="POINT_OPERATOR",
        employment_status="ACTIVE", employment_started_at=None, warehouse_id=point.id,
        invitation_ttl_minutes=60,
    ); session.commit()
    user_id = created.profile.user_id
    user = accept_staff_invitation(
        session, token=created.invitation_token, password="SecurePassword123!",
        confirmation="SecurePassword123!", password_min_length=12,
    ); session.commit()
    assert user.id == user_id and user.is_active and user.status == UserStatus.ACTIVE
    with pytest.raises(AdminUserError, match="no es válida"):
        accept_staff_invitation(
            session, token=created.invitation_token, password="SecurePassword123!",
            confirmation="SecurePassword123!", password_min_length=12,
        )


def test_pending_invitation_can_be_revoked_without_changing_employment(session):
    admin = _user(session, staff=True); point = _warehouse(session)
    created = create_staff_member(
        session, actor_user_id=admin.id, first_names="Eva", last_names="Operadora",
        email="revoke@ecuvel.com", phone="", identification_type="OTHER",
        identification_number="OTHER-REVOKE", nationality_code="ECU", role="POINT_OPERATOR",
        employment_status="ACTIVE", employment_started_at=None, warehouse_id=point.id,
        invitation_ttl_minutes=60,
    ); session.commit()
    assert revoke_staff_invitations(
        session, profile=created.profile, actor_user_id=admin.id,
        reason="Alta cancelada por administración",
    ) == 1
    session.commit()
    invitation = session.scalar(select(StaffAccessInvitation).where(
        StaffAccessInvitation.staff_profile_id == created.profile.id
    ))
    assert invitation.revoked_at is not None
    assert created.profile.employment_status == StaffEmploymentStatus.ACTIVE
    assert session.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "STAFF_INVITATION_REVOKED"
    ))


def test_employment_operational_and_access_are_independent(session):
    admin = _user(session, staff=True); point = _warehouse(session)
    created = create_staff_member(
        session, actor_user_id=admin.id, first_names="Luis", last_names="Operador",
        email="states@ecuvel.com", phone="", identification_type="OTHER",
        identification_number="OTHER-789", nationality_code="ECU", role="POINT_OPERATOR",
        employment_status="ACTIVE", employment_started_at=None, warehouse_id=point.id,
        invitation_ttl_minutes=60,
    ); session.commit()
    detail = get_admin_staff_detail(session, created.profile.employee_code)
    assert detail.profile.employment_status == StaffEmploymentStatus.ACTIVE
    assert detail.operational_status == StaffOperationalStatus.AVAILABLE
    assert detail.access_status == "INVITATION_PENDING"
    set_staff_access(session, profile=detail.profile, actor_user_id=admin.id, enable=False, reason="Acceso temporalmente bloqueado")
    session.commit()
    assert detail.profile.employment_status == StaffEmploymentStatus.ACTIVE


def test_staff_listing_filters_by_operational_status_and_ecuvel_point(session):
    admin = _user(session, staff=True)
    point = _warehouse(session)
    other_point = _warehouse(session)
    created = create_staff_member(
        session, actor_user_id=admin.id, first_names="Lucía", last_names="Operadora",
        email="filters@ecuvel.com", phone="", identification_type="OTHER",
        identification_number="OTHER-FILTER", nationality_code="ECU", role="POINT_OPERATOR",
        employment_status="ACTIVE", employment_started_at=None, warehouse_id=point.id,
        invitation_ttl_minutes=60,
    )
    session.commit()
    matching = get_admin_staff_page(
        session, operational="AVAILABLE", warehouse_id=str(point.id),
    )
    assert created.profile.id in {row.profile.id for row in matching.rows}
    assert matching.operational == "AVAILABLE"
    assert matching.warehouse_id == str(point.id)
    other = get_admin_staff_page(session, warehouse_id=str(other_point.id))
    assert created.profile.id not in {row.profile.id for row in other.rows}


def test_legacy_staff_permissions_remain_compatible(session):
    legacy = _user(session, staff=True)
    assert "admin.staff.manage" in permissions_for_user(legacy)


def test_admin_password_reset_is_post_only_and_audited(client, session, app):
    admin = _user(session, staff=True); buyer = _user(session, email="reset@example.com")
    session.commit(); _login(client, admin)
    mail_service.outbox.clear(); app.config["MAIL_BACKEND"] = "memory"
    assert client.get(f"/admin/users/{buyer.public_account_code}/password-reset").status_code == 405
    response = client.post(f"/admin/users/{buyer.public_account_code}/password-reset")
    assert response.status_code == 302 and len(mail_service.outbox) == 1
    assert "restablecer" in mail_service.outbox[0].body.lower()
    assert session.scalar(select(AdminAuditEvent).where(AdminAuditEvent.action == "ADMIN_PASSWORD_RESET_REQUESTED"))


def test_staff_pages_render_and_employee_code_cannot_be_supplied(client, session):
    admin = _user(session, staff=True); point = _warehouse(session); session.commit(); _login(client, admin)
    response = client.post("/admin/users/staff/new", data={
        "first_names":"Carlos", "last_names":"Punto", "email":"carlos@ecuvel.com",
        "identification_type":"ECUADOR_CEDULA", "identification_number":"0926687856",
        "nationality_code":"ECU", "role":"POINT_OPERATOR", "employment_status":"PENDING",
        "warehouse_id":str(point.id), "employee_code":"EMP-999999",
    })
    assert response.status_code == 302
    profile = session.scalar(select(StaffProfile).join(User).where(User.email_normalized == "carlos@ecuvel.com"))
    assert profile and profile.employee_code != "EMP-999999"
    assert client.get("/admin/users/staff").status_code == 200
    assert client.get(f"/admin/users/staff/{profile.employee_code}").status_code == 200
