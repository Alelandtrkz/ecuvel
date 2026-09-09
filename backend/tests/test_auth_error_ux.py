from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from flask import g
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

from app.extensions import limiter
from app.models import User
from app.models.enums import UserStatus


pytestmark = pytest.mark.integration

CSRF_MESSAGE = "Tu formulario venció por seguridad. Inténtalo nuevamente."
PASSWORD = "lr4-secret-password"


@pytest.fixture
def protected_client(app):
    previous_csrf = app.config["WTF_CSRF_ENABLED"]
    previous_ratelimit = app.config["RATELIMIT_ENABLED"]
    previous_limiter_enabled = limiter.enabled
    try:
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["RATELIMIT_ENABLED"] = True
        limiter.enabled = True
        limiter.reset()
        g.pop("csrf_token", None)
        client = app.test_client()
        yield client
    finally:
        g.pop("csrf_token", None)
        try:
            limiter.reset()
        finally:
            limiter.enabled = previous_limiter_enabled
            app.config["RATELIMIT_ENABLED"] = previous_ratelimit
            app.config["WTF_CSRF_ENABLED"] = previous_csrf


def _csrf_token(response) -> str:
    match = re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize("csrf_token", [None, "invalid-token"])
def test_login_csrf_rejection_recovers_with_new_form(protected_client, csrf_token):
    data = {
        "email": "buyer@example.test",
        "password": PASSWORD,
        "next": "/checkout",
    }
    if csrf_token is not None:
        data["csrf_token"] = csrf_token

    rejected = protected_client.post(
        "/iniciar-sesion",
        data=data,
        follow_redirects=False,
    )

    assert rejected.status_code == 302
    assert rejected.headers["Location"].endswith(
        "/iniciar-sesion?next=/checkout"
    )
    recovered = protected_client.get(rejected.headers["Location"])
    body = recovered.get_data(as_text=True)
    assert recovered.status_code == 200
    assert CSRF_MESSAGE in body
    assert 'name="csrf_token"' in body
    assert "Bad Request" not in body
    assert "The CSRF token" not in body


def test_login_csrf_recovery_rejects_external_next(protected_client):
    response = protected_client.post(
        "/iniciar-sesion",
        data={"next": "https://evil.example", "password": PASSWORD},
    )

    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/iniciar-sesion?next=/")


def test_register_csrf_rejection_returns_clean_form(protected_client):
    rejected = protected_client.post(
        "/registro",
        data={
            "email": "buyer@example.test",
            "full_name": "Buyer",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "next": "/checkout",
        },
    )

    assert rejected.status_code == 302
    assert rejected.headers["Location"].endswith("/registro?next=/checkout")
    recovered = protected_client.get(rejected.headers["Location"])
    body = recovered.get_data(as_text=True)
    assert CSRF_MESSAGE in body
    assert "buyer@example.test" not in body
    assert PASSWORD not in body


def test_csrf_rejection_never_exposes_password_or_form_data(
    protected_client,
    caplog,
):
    rejected = protected_client.post(
        "/iniciar-sesion",
        data={
            "email": "private-buyer@example.test",
            "password": PASSWORD,
            "next": "/checkout",
        },
    )
    recovered = protected_client.get(rejected.headers["Location"])
    combined_output = " ".join(
        [
            rejected.headers["Location"],
            rejected.get_data(as_text=True),
            recovered.get_data(as_text=True),
            caplog.text,
        ]
    )

    assert PASSWORD not in combined_output
    assert "private-buyer@example.test" not in combined_output


def test_expired_login_csrf_token_uses_same_recovery(protected_client):
    class ExpiredTimestampSigner(TimestampSigner):
        def get_timestamp(self) -> int:
            return 1

    raw_token = "expired-csrf-token"
    with protected_client.session_transaction() as browser_session:
        browser_session["csrf_token"] = raw_token
    serializer = URLSafeTimedSerializer(
        protected_client.application.secret_key,
        salt="wtf-csrf-token",
        signer=ExpiredTimestampSigner,
    )
    expired_token = serializer.dumps(raw_token)
    g.pop("csrf_token", None)

    response = protected_client.post(
        "/iniciar-sesion",
        data={
            "csrf_token": expired_token,
            "email": "buyer@example.test",
            "password": PASSWORD,
            "next": "/checkout",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert CSRF_MESSAGE in body
    assert "The CSRF token has expired" not in body
    assert PASSWORD not in body
    assert _csrf_token(response) != expired_token


@pytest.mark.parametrize(
    ("path", "expected_location"),
    [
        ("/recuperar-contrasena", "/recuperar-contrasena"),
        (
            "/restablecer-contrasena/reset-token",
            "/restablecer-contrasena/reset-token",
        ),
        ("/personal/invitacion/invite-token", "/personal/invitacion/invite-token"),
        (
            "/reenviar-verificacion",
            "/verificacion-pendiente?next=/checkout",
        ),
        ("/cerrar-sesion", "/"),
    ],
)
def test_other_auth_posts_have_safe_csrf_recovery(
    protected_client,
    path,
    expected_location,
):
    response = protected_client.post(path, data={"next": "/checkout"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith(expected_location)


def test_logout_csrf_rejection_keeps_authenticated_session(
    protected_client,
    session,
):
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email="logout-csrf@example.test",
        email_normalized="logout-csrf@example.test",
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="Logout CSRF",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.commit()
    identity = user.get_id()
    with protected_client.session_transaction() as browser_session:
        browser_session["_user_id"] = identity
        browser_session["_fresh"] = True

    response = protected_client.post("/cerrar-sesion", data={})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    with protected_client.session_transaction() as browser_session:
        assert browser_session["_user_id"] == identity


def test_normal_authentication_error_is_not_replaced_by_csrf_ux(protected_client):
    token = _csrf_token(protected_client.get("/iniciar-sesion"))
    response = protected_client.post(
        "/iniciar-sesion",
        data={
            "csrf_token": token,
            "email": "missing@example.test",
            "password": "wrong password",
        },
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Correo o contraseña incorrectos" in body
    assert CSRF_MESSAGE not in body


def test_login_rate_limit_uses_auth_shell_and_safe_recovery(protected_client):
    token = _csrf_token(
        protected_client.get("/iniciar-sesion?next=/checkout")
    )
    payload = {
        "csrf_token": token,
        "email": "missing@example.test",
        "password": "wrong password",
        "next": "/checkout",
    }

    for _ in range(5):
        assert protected_client.post("/iniciar-sesion", data=payload).status_code == 400
    limited = protected_client.post("/iniciar-sesion", data=payload)
    body = limited.get_data(as_text=True)

    assert limited.status_code == 429
    assert "ECUVEL" in body
    assert "Demasiados intentos" in body
    assert "Has realizado varios intentos en poco tiempo" in body
    assert 'href="/iniciar-sesion?next=/checkout"' in body
    assert "<form" not in body
    assert "Too Many Requests" not in body
    assert "5 per minute" not in body
    assert "127.0.0.1" not in body
    assert "Traceback" not in body

    limiter.reset()
    assert protected_client.post("/iniciar-sesion", data=payload).status_code == 400


def test_login_rate_limit_does_not_preserve_external_next(protected_client):
    token = _csrf_token(protected_client.get("/iniciar-sesion"))
    payload = {
        "csrf_token": token,
        "email": "missing@example.test",
        "password": "wrong password",
        "next": "https://evil.example",
    }

    for _ in range(5):
        protected_client.post("/iniciar-sesion", data=payload)
    limited = protected_client.post("/iniciar-sesion", data=payload)
    body = limited.get_data(as_text=True)

    assert limited.status_code == 429
    assert "evil.example" not in body
    assert 'href="/iniciar-sesion?next=/"' in body


def test_non_auth_csrf_behavior_is_unchanged(protected_client):
    response = protected_client.post("/carrito/agregar", data={})
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Bad Request" in body
    assert CSRF_MESSAGE not in body


def test_disabled_phone_auth_csrf_behavior_is_unchanged(protected_client):
    response = protected_client.post(
        "/ingresar-telefono",
        data={"phone": "0999330014"},
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "Bad Request" in body
    assert CSRF_MESSAGE not in body
