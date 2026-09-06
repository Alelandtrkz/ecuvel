from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

import app as app_package
import app.services.authentication as authentication_service
from app.config import Config, validate_public_base_url_configuration
from app.extensions import db
from app.models import User, UserAccountToken
from app.models.enums import UserAccountTokenPurpose, UserStatus
from app.services.account_tokens import create_account_token
from app.services.authentication import LoginError, authenticate_customer
from app.services.mail import MailConfigurationError
from app.services.transactional_mail import build_mail_action_url


pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct horse"


@pytest.fixture
def client(app):
    app.config["MAIL_BACKEND"] = "memory"
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email: str | None = None,
    status: UserStatus = UserStatus.ACTIVE,
    is_active: bool = True,
    password: str | None = PASSWORD,
    verified: bool = True,
) -> User:
    email = email or f"{uuid.uuid4().hex}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash(password) if password else None,
        full_name="Security Test",
        status=status,
        is_active=is_active,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User, *, remember: bool = False, next_url: str = "/"):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": PASSWORD,
            "remember": "1" if remember else "0",
            "next": next_url,
        },
    )


@pytest.mark.parametrize(
    "next_url",
    (
        "/checkout",
        "/carrito",
        "/perfil",
        "/producto/test",
        "/producto/test?variant=A",
    ),
)
def test_auth_preserves_safe_local_redirects(client, session, next_url):
    user = _user(session)
    session.commit()

    response = _login(client, user, next_url=next_url)

    assert response.status_code == 302
    assert response.headers["Location"] == next_url


@pytest.mark.parametrize(
    "next_url",
    (
        "https://evil.example",
        "http://evil.example",
        "//evil.example",
        "/%2f%2fevil.example",
        "/\\evil.example",
        "/%5cevil.example",
        "/safe\nunsafe",
        "/safe%0d%0aunsafe",
        "javascript:alert(1)",
        "data:text/html,unsafe",
        "/safe#//evil.example",
    ),
)
def test_auth_rejects_unsafe_or_manipulated_redirects(
    client,
    session,
    next_url,
):
    user = _user(session)
    session.commit()

    response = _login(client, user, next_url=next_url)

    assert response.status_code == 302
    if next_url == "/safe#//evil.example":
        assert response.headers["Location"] == "/safe"
    else:
        assert response.headers["Location"].endswith("/")


@pytest.mark.parametrize(
    ("status", "is_active", "password", "allowed"),
    (
        (UserStatus.ACTIVE, True, PASSWORD, True),
        (UserStatus.PENDING_VERIFICATION, True, PASSWORD, True),
        (UserStatus.BLOCKED, True, PASSWORD, False),
        (UserStatus.SUSPENDED, True, PASSWORD, False),
        (UserStatus.ACTIVE, False, PASSWORD, False),
        (UserStatus.ACTIVE, True, None, False),
    ),
)
def test_direct_login_enforces_account_availability(
    client,
    session,
    status,
    is_active,
    password,
    allowed,
):
    user = _user(
        session,
        status=status,
        is_active=is_active,
        password=password,
        verified=status != UserStatus.PENDING_VERIFICATION,
    )
    session.commit()

    response = _login(client, user)

    assert response.status_code == (302 if allowed else 400)
    if allowed:
        assert client.get("/perfil").status_code == 200
    else:
        assert "Correo o contraseña incorrectos" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("status", "is_active"),
    (
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.ACTIVE, False),
    ),
)
def test_existing_session_rechecks_account_availability(
    client,
    session,
    status,
    is_active,
):
    user = _user(session)
    session.commit()
    assert _login(client, user).status_code == 302
    assert client.get("/perfil").status_code == 200

    user.status = status
    user.is_active = is_active
    session.commit()

    response = client.get("/perfil")

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


@pytest.mark.parametrize(
    ("status", "is_active"),
    (
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.ACTIVE, False),
    ),
)
def test_remember_restoration_rechecks_account_availability(
    app,
    session,
    status,
    is_active,
):
    user = _user(session)
    session.commit()
    login_client = app.test_client()
    assert _login(login_client, user, remember=True).status_code == 302
    remember_name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    remember_cookie = login_client.get_cookie(remember_name)
    assert remember_cookie is not None

    user.status = status
    user.is_active = is_active
    session.commit()
    restored_client = app.test_client()
    restored_client.set_cookie(remember_name, remember_cookie.value)

    response = restored_client.get("/perfil")

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_invalid_login_paths_perform_equivalent_hash_work(session, monkeypatch):
    real_password_user = _user(session)
    passwordless_user = _user(session, password=None)
    session.commit()
    calls: list[tuple[str, str]] = []

    def record_hash_check(stored_hash: str, candidate: str) -> bool:
        calls.append((stored_hash, candidate))
        return False

    monkeypatch.setattr(
        authentication_service,
        "check_password_hash",
        record_hash_check,
    )

    for email in (
        "missing@test.local",
        passwordless_user.email,
        real_password_user.email,
    ):
        with pytest.raises(LoginError, match="Correo o contraseña incorrectos"):
            authenticate_customer(
                session=session,
                email=email,
                password="invalid candidate",
            )

    assert len(calls) == 3
    assert calls[0][0] == authentication_service.DUMMY_PASSWORD_HASH
    assert calls[1][0] == authentication_service.DUMMY_PASSWORD_HASH
    assert calls[2][0] == real_password_user.password_hash
    assert {
        stored_hash.split("$", 1)[0]
        for stored_hash, _candidate in calls
    } == {real_password_user.password_hash.split("$", 1)[0]}


@pytest.mark.parametrize(
    ("status", "is_active"),
    (
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.PENDING_VERIFICATION, False),
    ),
)
def test_verification_fails_closed_for_unavailable_account(
    client,
    session,
    status,
    is_active,
):
    user = _user(
        session,
        status=status,
        is_active=is_active,
        verified=False,
    )
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=30,
    )
    session.commit()

    response = client.get(f"/verificar-correo/{created.token}")
    session.expire_all()
    stored_user = session.get(User, user.id)
    stored_token = session.get(UserAccountToken, created.token_id)

    assert response.status_code == 302
    assert stored_user.status == status
    assert stored_user.is_active is is_active
    assert stored_user.email_verified_at is None
    assert stored_token.used_at is None
    with client.session_transaction() as browser_session:
        messages = [message for _category, message in browser_session["_flashes"]]
    assert "Correo verificado. Tu cuenta está activa." not in messages
    assert client.get("/perfil").status_code == 302


@pytest.mark.parametrize(
    ("status", "is_active", "token_expected"),
    (
        (UserStatus.ACTIVE, True, True),
        (UserStatus.PENDING_VERIFICATION, True, True),
        (UserStatus.BLOCKED, True, False),
        (UserStatus.SUSPENDED, True, False),
        (UserStatus.ACTIVE, False, False),
    ),
)
def test_reset_request_preserves_generic_response_and_checks_status(
    client,
    session,
    status,
    is_active,
    token_expected,
):
    user = _user(session, status=status, is_active=is_active)
    session.commit()

    response = client.post("/recuperar-contrasena", data={"email": user.email})

    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert browser_session["_flashes"][-1] == (
            "success",
            "Si existe una cuenta asociada, enviaremos instrucciones.",
        )
    token_count = session.scalar(
        select(func.count(UserAccountToken.id)).where(
            UserAccountToken.purpose == UserAccountTokenPurpose.RESET_PASSWORD
        )
    )
    assert token_count == int(token_expected)


@pytest.mark.parametrize(
    ("status", "is_active"),
    (
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.ACTIVE, False),
    ),
)
def test_issued_reset_token_fails_closed_after_status_change(
    client,
    session,
    status,
    is_active,
):
    user = _user(session)
    original_hash = user.password_hash
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.RESET_PASSWORD,
        ttl_minutes=30,
    )
    session.commit()
    user.status = status
    user.is_active = is_active
    session.commit()

    response = client.post(
        f"/restablecer-contrasena/{created.token}",
        data={
            "password": NEW_PASSWORD,
            "password_confirmation": NEW_PASSWORD,
        },
    )
    session.expire_all()

    assert response.status_code == 400
    assert session.get(User, user.id).password_hash == original_hash
    assert session.get(User, user.id).auth_version == 1
    assert session.get(UserAccountToken, created.token_id).used_at is None


def test_remember_cookie_samesite_matches_session_cookie(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"


@pytest.mark.parametrize(
    "value",
    (
        "",
        "ecuvel.com",
        "/relative",
        "http://ecuvel.com",
        "https://user:pass@ecuvel.com",
        "https://ecuvel.com?query=1",
        "https://ecuvel.com#fragment",
        "https://ecuvel.com/path",
        "https://-invalid.ecuvel.com",
        "https://ecuvel.com:not-a-port",
    ),
)
def test_production_rejects_untrusted_public_base_url(value):
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        validate_public_base_url_configuration(
            {"ECUVEL_PRODUCTION": True, "PUBLIC_BASE_URL": value}
        )


def test_production_accepts_trusted_https_public_base_url():
    validate_public_base_url_configuration(
        {
            "ECUVEL_PRODUCTION": True,
            "PUBLIC_BASE_URL": "https://ecuvel.com",
        }
    )


def test_development_allows_missing_public_base_url():
    validate_public_base_url_configuration(
        {"ECUVEL_PRODUCTION": False, "PUBLIC_BASE_URL": ""}
    )


def test_create_app_fails_fast_for_missing_production_public_base(
    monkeypatch,
):
    monkeypatch.setattr(Config, "ECUVEL_PRODUCTION", True)
    monkeypatch.setattr(Config, "PUBLIC_BASE_URL", "")

    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        app_package.create_app()


def test_production_mail_link_never_falls_back_to_request_host(app, monkeypatch):
    monkeypatch.setitem(app.config, "ECUVEL_PRODUCTION", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "")

    with app.test_request_context(base_url="https://attacker.example/"):
        with pytest.raises(MailConfigurationError, match="PUBLIC_BASE_URL"):
            build_mail_action_url("auth.verify_email", token="opaque")


def test_configured_mail_link_ignores_request_host(app, monkeypatch):
    monkeypatch.setitem(app.config, "ECUVEL_PRODUCTION", True)
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://ecuvel.com")

    with app.test_request_context(base_url="https://attacker.example/"):
        result = build_mail_action_url("auth.verify_email", token="opaque")

    assert result == "https://ecuvel.com/verificar-correo/opaque"
