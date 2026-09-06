from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.models import Cart, StaffProfile, User
from app.models.enums import (
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
    UserAccountTokenPurpose,
    UserStatus,
)
from app.services.account_tokens import create_account_token
from app.services.admin_users import (
    set_staff_access,
    set_user_suspension,
    update_staff_profile,
)
from app.services.authentication import (
    bump_auth_version,
    parse_authentication_identity,
)


pytestmark = pytest.mark.integration

OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct horse"


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email: str | None = None,
    password: str | None = OLD_PASSWORD,
    staff: bool = False,
) -> User:
    token = uuid.uuid4().hex[:8]
    email = email if email is not None else f"session-{token}@test.local"
    user = User(
        public_code=f"ECV-U-{token.upper()}",
        email=email,
        email_normalized=email.casefold() if email else None,
        password_hash=generate_password_hash(password) if password else None,
        full_name="Session Security",
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=staff,
        email_verified_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User, password: str = OLD_PASSWORD, *, remember=False):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": password,
            "remember": "1" if remember else "0",
            "next": "/perfil",
        },
    )


def _set_identity(client, identity: str) -> None:
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = identity
        browser_session["_fresh"] = True


def _reset_token(session, user: User) -> str:
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.RESET_PASSWORD,
        ttl_minutes=30,
    )
    session.commit()
    return created.token


def test_user_auth_version_defaults_and_get_id(session):
    user = _user(session)

    assert user.auth_version == 1
    assert user.get_id() == f"v1:{user.id}:1"


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        "garbage",
        "v1",
        "v1:",
        "v1:not-a-uuid:1",
        "v1:00000000-0000-0000-0000-000000000000:",
        "v1:00000000-0000-0000-0000-000000000000:0",
        "v1:00000000-0000-0000-0000-000000000000:-1",
        "v1:00000000-0000-0000-0000-000000000000:1.0",
        "v1:00000000-0000-0000-0000-000000000000:01",
        "v1:00000000-0000-0000-0000-000000000000:1:extra",
        "v2:00000000-0000-0000-0000-000000000000:1",
        "x" * 65,
    ),
)
def test_authentication_identity_parser_rejects_malformed_values(value):
    assert parse_authentication_identity(value) is None


def test_authentication_identity_parser_accepts_versioned_and_legacy_uuid():
    user_id = uuid.uuid4()

    versioned = parse_authentication_identity(f"v1:{user_id}:7")
    legacy = parse_authentication_identity(str(user_id))

    assert versioned is not None
    assert versioned.user_id == user_id
    assert versioned.auth_version == 7
    assert versioned.is_legacy is False
    assert legacy is not None
    assert legacy.user_id == user_id
    assert legacy.auth_version == 1
    assert legacy.is_legacy is True


def test_user_loader_parses_before_query_and_uses_one_user_lookup(
    app,
    engine,
    session,
):
    user = _user(session)
    session.commit()
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with app.app_context():
            db.session.remove()
            assert login_manager._user_callback("malformed") is None
            assert statements == []

            loaded = login_manager._user_callback(user.get_id())
            assert loaded is not None
            assert loaded.id == user.id
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    user_queries = [
        statement
        for statement in statements
        if " from users" in " ".join(statement.casefold().split())
    ]
    assert len(user_queries) == 1


def test_legacy_identity_only_authenticates_version_one_user(client, session):
    user = _user(session)
    session.commit()
    _set_identity(client, str(user.id))

    assert client.get("/perfil").status_code == 200

    bump_auth_version(user)
    session.commit()

    response = client.get("/perfil")
    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_version_mismatch_rejects_existing_session(client, session):
    user = _user(session)
    session.commit()
    assert _login(client, user).status_code == 302
    assert client.get("/perfil").status_code == 200

    bump_auth_version(user)
    session.commit()

    response = client.get("/perfil")
    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_password_reset_revokes_all_sessions_and_accepts_only_new_password(
    app,
    session,
):
    user = _user(session)
    session.commit()
    first_client = app.test_client()
    second_client = app.test_client()
    assert _login(first_client, user).status_code == 302
    assert _login(second_client, user).status_code == 302
    assert first_client.get("/perfil").status_code == 200
    assert second_client.get("/perfil").status_code == 200
    token = _reset_token(session, user)

    reset_response = app.test_client().post(
        f"/restablecer-contrasena/{token}",
        data={
            "password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
    )
    session.expire_all()

    assert reset_response.status_code == 302
    assert session.get(User, user.id).auth_version == 2
    assert first_client.get("/perfil").status_code == 302
    assert second_client.get("/perfil").status_code == 302
    assert _login(app.test_client(), user).status_code == 400
    assert _login(app.test_client(), user, NEW_PASSWORD).status_code == 302


def test_password_reset_revokes_remember_cookie_and_new_login_restores(
    app,
    session,
):
    user = _user(session)
    session.commit()
    login_client = app.test_client()
    assert _login(login_client, user, remember=True).status_code == 302
    remember_name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    old_cookie = login_client.get_cookie(remember_name)
    assert old_cookie is not None
    token = _reset_token(session, user)

    assert app.test_client().post(
        f"/restablecer-contrasena/{token}",
        data={
            "password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
    ).status_code == 302

    stale_client = app.test_client()
    stale_client.set_cookie(remember_name, old_cookie.value)
    assert stale_client.get("/perfil").status_code == 302

    fresh_login = app.test_client()
    assert _login(fresh_login, user, NEW_PASSWORD, remember=True).status_code == 302
    fresh_cookie = fresh_login.get_cookie(remember_name)
    assert fresh_cookie is not None
    restored_client = app.test_client()
    restored_client.set_cookie(remember_name, fresh_cookie.value)
    assert restored_client.get("/perfil").status_code == 200


def test_profile_password_change_revokes_session_and_preserves_cart(
    app,
    session,
):
    user = _user(session)
    cart = Cart(user_id=user.id)
    session.add(cart)
    session.commit()
    client = app.test_client()
    assert _login(client, user).status_code == 302

    response = client.post(
        "/perfil/cambiar-contrasena",
        data={
            "current_password": OLD_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirmation": NEW_PASSWORD,
        },
    )
    session.expire_all()

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]
    assert client.get("/perfil").status_code == 302
    assert session.get(User, user.id).auth_version == 2
    assert session.scalar(select(Cart).where(Cart.user_id == user.id)).id == cart.id
    assert _login(app.test_client(), user).status_code == 400
    assert _login(app.test_client(), user, NEW_PASSWORD).status_code == 302


def test_profile_password_creation_revokes_session_and_preserves_cart(
    app,
    session,
):
    user = _user(session, email="", password=None)
    cart = Cart(user_id=user.id)
    session.add(cart)
    session.commit()
    client = app.test_client()
    _set_identity(client, user.get_id())

    response = client.post(
        "/perfil/crear-contrasena",
        data={
            "new_password": NEW_PASSWORD,
            "new_password_confirmation": NEW_PASSWORD,
        },
    )
    session.expire_all()

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]
    assert client.get("/perfil").status_code == 302
    stored = session.get(User, user.id)
    assert stored.auth_version == 2
    assert stored.password_hash is not None
    assert check_password_hash(stored.password_hash, NEW_PASSWORD)
    assert stored.full_name == "Session Security"
    assert session.scalar(select(Cart).where(Cart.user_id == user.id)).id == cart.id


def test_suspension_bumps_once_and_reactivation_never_revives_old_cookie(
    app,
    session,
):
    admin = _user(session, staff=True)
    user = _user(session)
    session.commit()
    client = app.test_client()
    assert _login(client, user, remember=True).status_code == 302
    remember_name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    old_cookie = client.get_cookie(remember_name)
    assert old_cookie is not None

    set_user_suspension(
        session,
        user=user,
        actor_user_id=admin.id,
        suspend=True,
        reason="Revisión de seguridad",
    )
    session.commit()
    assert user.auth_version == 2
    blocked_client = app.test_client()
    blocked_client.set_cookie(remember_name, old_cookie.value)
    assert blocked_client.get("/perfil").status_code == 302

    set_user_suspension(
        session,
        user=user,
        actor_user_id=admin.id,
        suspend=False,
        reason="Reactivación aprobada",
    )
    session.commit()
    assert user.auth_version == 2
    reactivated_client = app.test_client()
    reactivated_client.set_cookie(remember_name, old_cookie.value)
    assert reactivated_client.get("/perfil").status_code == 302
    assert _login(app.test_client(), user).status_code == 302


def test_blocked_then_reactivated_user_cannot_reuse_old_identity(client, session):
    user = _user(session)
    session.commit()
    stale_identity = user.get_id()
    _set_identity(client, stale_identity)

    user.status = UserStatus.BLOCKED
    bump_auth_version(user)
    session.commit()
    assert client.get("/perfil").status_code == 302

    user.status = UserStatus.ACTIVE
    session.commit()
    _set_identity(client, stale_identity)
    assert client.get("/perfil").status_code == 302


def test_staff_access_disable_bumps_and_enable_does_not_revive_session(
    app,
    session,
):
    admin = _user(session, staff=True)
    staff = _user(session, staff=True)
    profile = StaffProfile(
        user_id=staff.id,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"OTHER-{uuid.uuid4().hex[:8]}",
        nationality_code="ECU",
        role=StaffRole.SUPPORT,
        employment_status=StaffEmploymentStatus.ACTIVE,
    )
    session.add(profile)
    session.commit()
    client = app.test_client()
    assert _login(client, staff).status_code == 302

    set_staff_access(
        session,
        profile=profile,
        actor_user_id=admin.id,
        enable=False,
        reason="Acceso suspendido",
    )
    session.commit()
    assert staff.auth_version == 2
    assert client.get("/perfil").status_code == 302

    set_staff_access(
        session,
        profile=profile,
        actor_user_id=admin.id,
        enable=True,
        reason="Acceso restaurado",
    )
    session.commit()
    assert staff.auth_version == 2
    assert client.get("/perfil").status_code == 302


def test_staff_employment_inactive_bumps_auth_version(session):
    admin = _user(session, staff=True)
    staff = _user(session, staff=True)
    profile = StaffProfile(
        user_id=staff.id,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"OTHER-{uuid.uuid4().hex[:8]}",
        nationality_code="ECU",
        role=StaffRole.SUPPORT,
        employment_status=StaffEmploymentStatus.ACTIVE,
    )
    session.add(profile)
    session.commit()

    update_staff_profile(
        session,
        profile=profile,
        actor_user_id=admin.id,
        role=StaffRole.SUPPORT.value,
        employment_status=StaffEmploymentStatus.INACTIVE.value,
        phone="",
        warehouse_id=None,
        reason="Relación laboral finalizada",
    )
    session.commit()

    assert staff.auth_version == 2
    assert staff.is_active is False
    assert staff.status == UserStatus.SUSPENDED


def test_concurrent_locked_bumps_do_not_lose_auth_version_increment(
    session,
    session_factory,
    concurrent_runner,
):
    user = _user(session)
    user_id = user.id
    session.commit()

    def worker():
        def run(barrier):
            local_session = session_factory()
            try:
                barrier.wait()
                with local_session.begin():
                    locked_user = local_session.get(
                        User,
                        user_id,
                        with_for_update=True,
                    )
                    bump_auth_version(locked_user)
            finally:
                local_session.close()

        return run

    _results, errors = concurrent_runner(
        (
            worker(),
            worker(),
        )
    )
    session.expire_all()

    assert errors == []
    assert session.get(User, user.id).auth_version == 3
