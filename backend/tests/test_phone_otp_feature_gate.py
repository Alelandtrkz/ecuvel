from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Config, validate_phone_otp_configuration
from app.extensions import db
from app.models import PhoneOtpChallenge, User
from app.models.enums import PhoneOtpPurpose, UserStatus
from app.services.phone_otp import (
    PhoneOtpUnavailableError,
    fake_phone_otp_sender,
    request_phone_otp,
)


pytestmark = pytest.mark.integration

PHONE_SESSION_KEYS = (
    "phone_otp_challenge_id",
    "phone_otp_purpose",
    "phone_otp_next",
    "phone_registration_challenge_id",
)


@pytest.fixture
def disabled_phone_client(app):
    previous_enabled = app.config["PHONE_OTP_ENABLED"]
    previous_backend = app.config["PHONE_OTP_BACKEND"]
    previous_pepper = app.config["PHONE_OTP_PEPPER"]
    app.config["PHONE_OTP_ENABLED"] = False
    app.config["PHONE_OTP_BACKEND"] = "fake"
    app.config["PHONE_OTP_PEPPER"] = ""
    fake_phone_otp_sender.outbox.clear()
    test_client = app.test_client()
    yield test_client
    fake_phone_otp_sender.outbox.clear()
    app.config["PHONE_OTP_ENABLED"] = previous_enabled
    app.config["PHONE_OTP_BACKEND"] = previous_backend
    app.config["PHONE_OTP_PEPPER"] = previous_pepper
    db.session.remove()


def _user(
    session: Session,
    *,
    phone: str | None = None,
    phone_verified: bool = False,
) -> User:
    suffix = str(session.scalar(select(func.count(User.id))) or 0)
    email = f"phone-gate-{suffix}@test.local"
    user = User(
        public_code=f"PHONE-GATE-{suffix}",
        email=email,
        email_normalized=email,
        password_hash="test",
        full_name="Phone Gate Test",
        phone=phone,
        phone_normalized=phone,
        phone_verified_at=(
            datetime.now(timezone.utc) if phone_verified else None
        ),
        email_verified_at=datetime.now(timezone.utc),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def _login_as(client, user_id) -> None:
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user_id)
        browser_session["_fresh"] = True


def _seed_stale_phone_state(client) -> None:
    with client.session_transaction() as browser_session:
        browser_session["phone_otp_challenge_id"] = "stale-challenge"
        browser_session["phone_otp_purpose"] = "LOGIN_OR_REGISTER"
        browser_session["phone_otp_next"] = "/checkout"
        browser_session["phone_registration_challenge_id"] = "stale-registration"
        browser_session["unrelated_state"] = "preserved"


def _assert_phone_state_cleared(client) -> None:
    with client.session_transaction() as browser_session:
        assert all(key not in browser_session for key in PHONE_SESSION_KEYS)
        assert browser_session["unrelated_state"] == "preserved"


def test_phone_otp_default_is_disabled_in_pre_beta():
    assert Config.PHONE_OTP_ENABLED is False


def test_disabled_production_configuration_needs_no_provider_or_pepper():
    validate_phone_otp_configuration(
        enabled=False,
        backend="console",
        production=True,
        testing=False,
        pepper="",
    )


@pytest.mark.parametrize("backend", ("console", "fake"))
def test_enabled_production_configuration_rejects_nonproduction_backends(
    backend,
):
    with pytest.raises(RuntimeError, match="no está permitido en producción"):
        validate_phone_otp_configuration(
            enabled=True,
            backend=backend,
            production=True,
            testing=False,
            pepper="configured-pepper",
        )


def test_testing_can_explicitly_enable_fake_backend():
    validate_phone_otp_configuration(
        enabled=True,
        backend="fake",
        production=False,
        testing=True,
        pepper="test-pepper",
    )


def test_service_rejects_disabled_request_before_challenge_or_send(
    app,
    session: Session,
    monkeypatch,
):
    monkeypatch.setitem(app.config, "PHONE_OTP_ENABLED", False)
    fake_phone_otp_sender.outbox.clear()
    before = session.scalar(select(func.count(PhoneOtpChallenge.id)))

    with pytest.raises(PhoneOtpUnavailableError, match="próximamente"):
        request_phone_otp(
            session=session,
            phone="0999330014",
            purpose=PhoneOtpPurpose.LOGIN_OR_REGISTER,
        )

    assert session.scalar(select(func.count(PhoneOtpChallenge.id))) == before
    assert fake_phone_otp_sender.outbox == []


@pytest.mark.parametrize(
    ("phone", "verified"),
    ((None, False), ("+593999330014", True)),
)
def test_profile_preserves_phone_state_without_active_phone_cta(
    disabled_phone_client,
    session: Session,
    phone,
    verified,
):
    user = _user(session, phone=phone, phone_verified=verified)
    _login_as(disabled_phone_client, user.id)

    response = disabled_phone_client.get("/perfil")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Próximamente" in body
    assert 'href="/perfil/agregar-telefono"' not in body
    if verified:
        assert phone in body
        assert "Verificado" in body
    else:
        assert "No configurado" in body


@pytest.mark.parametrize("method", ("get", "post"))
def test_disabled_account_phone_routes_redirect_without_side_effects(
    disabled_phone_client,
    session: Session,
    method,
):
    user = _user(session)
    _login_as(disabled_phone_client, user.id)
    _seed_stale_phone_state(disabled_phone_client)

    response = getattr(disabled_phone_client, method)(
        "/perfil/agregar-telefono",
        data={"phone": "0999330014"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "disponible próximamente" in body
    assert "Enviamos un código" not in body
    assert "Enviar código" not in body
    assert session.scalar(select(func.count(PhoneOtpChallenge.id))) == 0
    assert fake_phone_otp_sender.outbox == []
    _assert_phone_state_cleared(disabled_phone_client)


@pytest.mark.parametrize(
    ("method", "path", "data"),
    (
        ("get", "/ingresar-telefono", None),
        ("post", "/ingresar-telefono", {"phone": "0999330014"}),
        ("get", "/verificar-telefono", None),
        ("post", "/verificar-telefono", {"code": "123456"}),
        ("post", "/reenviar-codigo-telefono", None),
        ("get", "/registro/telefono/completar", None),
        (
            "post",
            "/registro/telefono/completar",
            {"full_name": "Phone Test", "email": ""},
        ),
    ),
)
def test_disabled_auth_phone_routes_clear_stale_state_without_processing(
    disabled_phone_client,
    session: Session,
    method,
    path,
    data,
):
    _seed_stale_phone_state(disabled_phone_client)

    response = getattr(disabled_phone_client, method)(path, data=data)

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]
    assert session.scalar(select(func.count(PhoneOtpChallenge.id))) == 0
    assert fake_phone_otp_sender.outbox == []
    with disabled_phone_client.session_transaction() as browser_session:
        flashes = browser_session.get("_flashes", [])
        assert flashes[-1] == (
            "warning",
            "El acceso por teléfono estará disponible próximamente.",
        )
        assert all("Enviamos" not in message for _category, message in flashes)
    _assert_phone_state_cleared(disabled_phone_client)


def test_login_and_pending_verification_do_not_advertise_phone_flow(
    disabled_phone_client,
    session: Session,
):
    login_body = disabled_phone_client.get("/iniciar-sesion").get_data(
        as_text=True
    )
    assert "Acceso por teléfono — Próximamente" in login_body
    assert 'href="/ingresar-telefono' not in login_body

    user = _user(session)
    _login_as(disabled_phone_client, user.id)
    pending_body = disabled_phone_client.get(
        "/verificacion-pendiente"
    ).get_data(as_text=True)
    assert "puedes verificar un teléfono" not in pending_body
    assert 'href="/perfil/agregar-telefono"' not in pending_body
