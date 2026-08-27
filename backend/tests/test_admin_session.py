from __future__ import annotations

import uuid
from datetime import date

import pytest
from itsdangerous import URLSafeTimedSerializer

from app.models import StaffProfile, User
from app.models.enums import (
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
    UserStatus,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    return app.test_client()


def _staff_user(session, *, name: str, with_profile: bool) -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}",
        email=f"{token}@ecuvel.test",
        email_normalized=f"{token}@ecuvel.test",
        password_hash="test",
        full_name=name,
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=True,
    )
    session.add(user)
    session.flush()
    if with_profile:
        session.add(
            StaffProfile(
                user_id=user.id,
                identification_type=StaffIdentificationType.OTHER,
                identification_number_normalized=f"ID-{token}",
                nationality_code="ECU",
                role=StaffRole.POINT_OPERATOR,
                employment_status=StaffEmploymentStatus.ACTIVE,
                employment_started_at=date.today(),
            )
        )
        session.flush()
    return user


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def test_admin_session_menu_renders_for_profile_and_respects_rbac(client, session):
    user = _staff_user(session, name="Patricia Operadora", with_profile=True)
    session.commit()
    employee_code = user.staff_profile.employee_code
    _login(client, user)

    response = client.get("/admin")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Cerrar sesión" in body
    assert 'aria-haspopup="menu"' in body
    assert 'aria-expanded="false"' in body
    assert employee_code in body
    assert "Operador de Punto ECUVEL" in body
    assert "Escanear paquete" in body
    assert ">Productos<" not in body
    assert ">Tiendas<" not in body
    assert ">Reseñas<" not in body


def test_admin_session_menu_supports_legacy_staff_without_profile(client, session):
    user = _staff_user(session, name="Administrador Legacy", with_profile=False)
    session.commit()
    _login(client, user)

    response = client.get("/admin")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Administrador Legacy" in body
    assert "Administrador ECUVEL" in body
    assert "Cerrar sesión" in body
    assert "EMP-" not in body


def test_admin_logout_is_post_only_csrf_protected_and_returns_to_login(
    app, client, session
):
    user = _staff_user(session, name="Admin Logout", with_profile=False)
    session.commit()
    _login(client, user)

    previous_csrf = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        raw_token = uuid.uuid4().hex
        with client.session_transaction() as browser:
            browser["csrf_token"] = raw_token
        signed_token = URLSafeTimedSerializer(
            app.config.get("WTF_CSRF_SECRET_KEY") or app.secret_key,
            salt="wtf-csrf-token",
        ).dumps(raw_token)

        assert client.get("/cerrar-sesion").status_code == 405
        page = client.get("/admin")
        assert page.status_code == 200
        assert 'name="csrf_token"' in page.get_data(as_text=True)

        logout = client.post(
            "/cerrar-sesion",
            data={"csrf_token": signed_token, "source": "admin"},
        )
        assert logout.status_code == 302, logout.get_data(as_text=True)
        assert logout.headers["Location"].endswith(
            "/iniciar-sesion?next=/admin"
        )
        assert client.get("/admin").status_code == 302
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous_csrf


def test_marketplace_logout_keeps_its_existing_destination(client, session):
    user = _staff_user(session, name="Admin Marketplace", with_profile=False)
    session.commit()
    _login(client, user)

    response = client.post("/cerrar-sesion")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
