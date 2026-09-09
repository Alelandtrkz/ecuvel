from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

import app.commands.users as users_command_module
import app.services.authentication as authentication_service
from app.config import Config, _environment_int_range
from app.extensions import db
from app.models import User, UserAccountToken
from app.models.enums import UserStatus
from app.services.authentication import (
    PasswordPolicyError,
    RegistrationError,
    register_customer,
    validate_password,
    validate_registration_email,
)
from app.services.mail import mail_service


pytestmark = pytest.mark.integration

PASSWORD_MINIMUM = 6
PASSWORD_MAXIMUM = 128


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email: str,
    password: str = "correct horse battery staple",
    public_code: str | None = None,
) -> User:
    user = User(
        public_code=public_code or f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.strip().casefold(),
        password_hash=generate_password_hash(password),
        full_name="Cliente Existente",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _register_with_service(session, *, email: str, password: str = "123456"):
    return register_customer(
        session=session,
        email=email,
        full_name="Cliente Nuevo",
        password=password,
        password_confirmation=password,
        password_min_length=PASSWORD_MINIMUM,
        verification_ttl_minutes=30,
    )


@pytest.mark.parametrize(
    ("password", "is_valid"),
    (
        ("", False),
        ("a" * 5, False),
        ("a" * 6, True),
        ("a" * 7, True),
        ("a" * 128, True),
        ("a" * 129, False),
    ),
)
def test_shared_password_validator_boundaries(password, is_valid):
    if is_valid:
        validate_password(password, min_length=PASSWORD_MINIMUM)
    else:
        with pytest.raises(PasswordPolicyError):
            validate_password(password, min_length=PASSWORD_MINIMUM)


def test_shared_password_validator_reports_exact_failures():
    with pytest.raises(
        PasswordPolicyError,
        match="La contraseña debe tener al menos 6 caracteres",
    ):
        validate_password("12345", min_length=PASSWORD_MINIMUM)
    with pytest.raises(
        PasswordPolicyError,
        match="La contraseña no puede superar 128 caracteres",
    ):
        validate_password("a" * 129, min_length=PASSWORD_MINIMUM)
    with pytest.raises(
        PasswordPolicyError,
        match="Las contraseñas no coinciden",
    ):
        validate_password(
            "123456",
            min_length=PASSWORD_MINIMUM,
            confirmation="654321",
        )


def test_password_validator_preserves_whitespace_and_unicode():
    password = "  ñ🙂  "
    assert len(password) == PASSWORD_MINIMUM
    validate_password(password, min_length=PASSWORD_MINIMUM)


def test_password_minimum_defaults_to_six():
    assert Config.AUTH_PASSWORD_MIN_LENGTH == PASSWORD_MINIMUM


@pytest.mark.parametrize(
    ("configured", "expected"),
    (("6", 6), ("128", 128)),
)
def test_password_minimum_environment_accepts_supported_range(
    monkeypatch,
    configured,
    expected,
):
    monkeypatch.setenv("AUTH_PASSWORD_MIN_LENGTH", configured)
    assert _environment_int_range(
        "AUTH_PASSWORD_MIN_LENGTH",
        PASSWORD_MINIMUM,
        PASSWORD_MINIMUM,
        PASSWORD_MAXIMUM,
    ) == expected


@pytest.mark.parametrize("configured", ("5", "129"))
def test_password_minimum_environment_rejects_out_of_range(
    monkeypatch,
    configured,
):
    monkeypatch.setenv("AUTH_PASSWORD_MIN_LENGTH", configured)
    with pytest.raises(RuntimeError, match="debe estar entre 6 y 128"):
        _environment_int_range(
            "AUTH_PASSWORD_MIN_LENGTH",
            PASSWORD_MINIMUM,
            PASSWORD_MINIMUM,
            PASSWORD_MAXIMUM,
        )


@pytest.mark.parametrize(
    "email",
    (
        "test@example.com",
        "Test.User@example.com",
        "test+tag@example.com",
        "USER@EXAMPLE.COM",
    ),
)
def test_registration_email_validator_accepts_normal_addresses(email):
    assert validate_registration_email(email) == email


@pytest.mark.parametrize(
    "email",
    (
        "",
        "correo",
        "correo@",
        "@dominio.com",
        "correo@@dominio.com",
        "correo dominio@example.com",
        "correo@example .com",
        "correo@example..com",
        "correo@example_com",
        "correo@-example.com",
        "correo@example-.com",
        "correo@example",
        "correo@example.com\n",
        "correo\x00@example.com",
        f"{'a' * 64}@{'b' * 63}.{'c' * 63}.{'d' * 61}.com",
    ),
)
def test_registration_email_validator_rejects_invalid_addresses(email):
    with pytest.raises(
        RegistrationError,
        match="Ingresa un correo electrónico válido",
    ):
        validate_registration_email(email)


def test_registration_email_validator_trims_outer_spaces_for_display():
    assert (
        validate_registration_email("  Alejandro@Example.com  ")
        == "Alejandro@Example.com"
    )


@pytest.mark.parametrize(
    "duplicate_email",
    (
        "alejandro@example.com",
        "ALEJANDRO@EXAMPLE.COM",
        " alejandro@example.com ",
    ),
)
def test_registration_duplicate_precheck_uses_normalized_email(
    session,
    duplicate_email,
):
    _user(session, email="Alejandro@Example.com")
    session.commit()

    with pytest.raises(
        RegistrationError,
        match="Ya existe una cuenta con este correo",
    ):
        _register_with_service(session, email=duplicate_email)

    assert session.scalar(select(func.count(User.id))) == 1
    assert session.scalar(select(func.count(UserAccountToken.id))) == 0


def test_registration_duplicate_race_is_classified_without_partial_state(
    session_factory,
):
    session_a = session_factory()
    session_b = session_factory()

    def commit_competing_registration(_session, _flush_context, _instances):
        _register_with_service(session_b, email="RACE@example.com")
        session_b.commit()

    event.listen(session_a, "before_flush", commit_competing_registration, once=True)
    try:
        with pytest.raises(
            RegistrationError,
            match="Ya existe una cuenta con este correo",
        ):
            with session_a.begin():
                _register_with_service(session_a, email="race@example.com")
    finally:
        session_a.rollback()
        session_a.close()
        session_b.close()

    verification_session = session_factory()
    try:
        assert verification_session.scalar(select(func.count(User.id))) == 1
        assert (
            verification_session.scalar(select(func.count(UserAccountToken.id)))
            == 1
        )
        assert mail_service.outbox == []
    finally:
        verification_session.close()


def test_registration_does_not_mask_unrelated_integrity_errors(
    session_factory,
    monkeypatch,
):
    duplicate_public_code = "ECV-U-COLLIDE"
    monkeypatch.setattr(
        authentication_service,
        "public_user_code",
        lambda: duplicate_public_code,
    )
    session_a = session_factory()
    session_b = session_factory()

    def commit_public_code_collision(_session, _flush_context, _instances):
        _user(
            session_b,
            email="other@example.com",
            public_code=duplicate_public_code,
        )
        session_b.commit()

    event.listen(session_a, "before_flush", commit_public_code_collision, once=True)
    try:
        with pytest.raises(IntegrityError):
            with session_a.begin():
                _register_with_service(session_a, email="new@example.com")
    finally:
        session_a.rollback()
        session_a.close()
        session_b.close()

    verification_session = session_factory()
    try:
        assert verification_session.scalar(select(func.count(User.id))) == 1
        assert (
            verification_session.scalar(select(func.count(UserAccountToken.id)))
            == 0
        )
    finally:
        verification_session.close()


@pytest.mark.parametrize(
    ("password", "expected_status", "message"),
    (
        ("12345", 400, "La contraseña debe tener al menos 6 caracteres"),
        ("123456", 302, None),
        ("a" * 128, 302, None),
        ("a" * 129, 400, "La contraseña no puede superar 128 caracteres"),
    ),
)
def test_register_route_enforces_password_boundaries(
    client,
    session,
    password,
    expected_status,
    message,
):
    response = client.post(
        "/registro",
        data={
            "email": f"boundary-{len(password)}@example.com",
            "full_name": "Cliente Boundary",
            "password": password,
            "password_confirmation": password,
        },
    )

    assert response.status_code == expected_status
    body = response.get_data(as_text=True)
    if message is not None:
        assert message in body
        assert password not in body
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(UserAccountToken.id))) == 0
        assert mail_service.outbox == []
    else:
        assert session.scalar(select(func.count(User.id))) == 1
        assert session.scalar(select(func.count(UserAccountToken.id))) == 1
        assert len(mail_service.outbox) == 1


def test_register_route_reports_mismatch_without_password_echo(client, session):
    response = client.post(
        "/registro",
        data={
            "email": "mismatch@example.com",
            "full_name": "Cliente Mismatch",
            "password": "123456",
            "password_confirmation": "654321",
        },
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Las contraseñas no coinciden" in body
    assert "123456" not in body
    assert "654321" not in body
    assert session.scalar(select(func.count(User.id))) == 0
    assert mail_service.outbox == []


def test_register_route_rejects_invalid_email_before_side_effects(client, session):
    response = client.post(
        "/registro",
        data={
            "email": "correo@example..com",
            "full_name": "Cliente Email",
            "password": "123456",
            "password_confirmation": "123456",
        },
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Ingresa un correo electrónico válido" in body
    assert "correo@example..com" in body
    assert "123456" not in body
    assert session.scalar(select(func.count(User.id))) == 0
    assert session.scalar(select(func.count(UserAccountToken.id))) == 0
    assert mail_service.outbox == []


def test_register_route_preserves_password_whitespace_and_unicode(client, session):
    password = "  ñ🙂  "
    response = client.post(
        "/registro",
        data={
            "email": "unicode-password@example.com",
            "full_name": "Cliente Unicode",
            "password": password,
            "password_confirmation": password,
        },
    )

    user = session.scalar(
        select(User).where(User.email_normalized == "unicode-password@example.com")
    )
    assert response.status_code == 302
    assert user is not None
    assert check_password_hash(user.password_hash, password)
    assert not check_password_hash(user.password_hash, password.strip())


def test_historical_short_password_still_authenticates(client, session):
    _user(session, email="historical@example.com", password="old")
    session.commit()

    response = client.post(
        "/iniciar-sesion",
        data={"email": "historical@example.com", "password": "old"},
    )

    assert response.status_code == 302


def test_create_customer_cli_uses_shared_password_policy(app, session, monkeypatch):
    monkeypatch.setattr(
        users_command_module.click,
        "prompt",
        lambda *_args, **_kwargs: "12345",
    )

    result = app.test_cli_runner().invoke(
        args=[
            "create-customer-user",
            "--email",
            "cli@example.com",
            "--name",
            "Cliente CLI",
        ]
    )

    assert result.exit_code == 1
    assert "La contraseña debe tener al menos 6 caracteres" in result.output
    assert session.scalar(select(func.count(User.id))) == 0


def test_register_phone_cta_is_noninteractive_when_disabled(app):
    previous = app.config["PHONE_OTP_ENABLED"]
    app.config["PHONE_OTP_ENABLED"] = False
    try:
        body = app.test_client().get("/registro").get_data(as_text=True)
    finally:
        app.config["PHONE_OTP_ENABLED"] = previous

    assert "Registro por teléfono — Próximamente" in body
    assert 'href="/ingresar-telefono' not in body
    assert 'minlength="6"' in body
    assert body.count('maxlength="128"') == 2


def test_register_phone_cta_remains_functional_when_enabled_in_tests(app):
    previous = app.config["PHONE_OTP_ENABLED"]
    app.config["PHONE_OTP_ENABLED"] = True
    try:
        body = app.test_client().get("/registro").get_data(as_text=True)
    finally:
        app.config["PHONE_OTP_ENABLED"] = previous

    assert "Registrarme con número telefónico" in body
    assert 'href="/ingresar-telefono' in body
    assert "Registro por teléfono — Próximamente" not in body
