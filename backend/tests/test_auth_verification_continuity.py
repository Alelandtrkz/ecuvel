from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    InventoryReservation,
    Order,
    PaymentAttempt,
    User,
    UserAccountToken,
)
from app.models.enums import UserAccountTokenPurpose, UserStatus
from app.services.account_tokens import create_account_token
from app.services.cart_storage import load_cart_state_for_user
from app.services.mail import MailDeliveryError, mail_service
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _pending_user(session, *, email: str | None = None) -> User:
    token = uuid.uuid4().hex[:12]
    email = email or f"pending-{token}@test.local"
    user = User(
        public_code=f"LR3-{token.upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash(PASSWORD),
        full_name="Continuity Test",
        status=UserStatus.PENDING_VERIFICATION,
        email_verified_at=None,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User, *, next_url: str = "/"):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": PASSWORD,
            "next": next_url,
        },
    )


def _register(client, *, email: str, next_url: str):
    return client.post(
        "/registro",
        data={
            "email": email,
            "full_name": "Cliente Continuity",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "next": next_url,
        },
    )


def _verification_action_url(message) -> str:
    match = re.search(r"https://[^\s]+/verificar-correo/[^\s]+", message.text_body)
    assert match is not None
    action_url = match.group(0)
    assert action_url in message.html_body
    return action_url


def _relative_url(action_url: str) -> str:
    parsed = urlsplit(action_url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def _assert_next(location: str, expected: str) -> None:
    parsed = urlsplit(location)
    assert parse_qs(parsed.query).get("next") == [expected]


def test_register_mail_verify_cross_client_preserves_checkout_and_cart(
    app,
    session,
):
    base = create_catalog_and_stock(session, stock=5)
    session.commit()
    client_a = app.test_client()
    client_b = app.test_client()
    with client_a.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {
                str(base.offer_id): {"quantity": 2, "selected": True}
            },
        }

    response = _register(
        client_a,
        email="cross-client@example.com",
        next_url="/checkout",
    )

    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/verificacion-pendiente"
    _assert_next(response.headers["Location"], "/checkout")
    assert len(mail_service.outbox) == 1
    action_url = _verification_action_url(mail_service.outbox[0])
    parsed_action = urlsplit(action_url)
    assert parsed_action.scheme == "https"
    assert parsed_action.netloc == "ecuvel.test"
    assert parsed_action.path.startswith("/verificar-correo/")
    assert parse_qs(parsed_action.query)["next"] == ["/checkout"]

    verification = client_b.get(_relative_url(action_url))
    assert verification.status_code == 302
    assert verification.headers["Location"] == "/checkout"
    assert client_b.get("/perfil").status_code == 200

    session.expire_all()
    user = session.scalar(
        select(User).where(User.email_normalized == "cross-client@example.com")
    )
    assert user is not None
    assert user.status == UserStatus.ACTIVE
    assert user.email_verified_at is not None
    assert load_cart_state_for_user(session, user.id)["items"][
        str(base.offer_id)
    ] == {"quantity": 2, "selected": True}
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert session.scalar(select(func.count(InventoryReservation.id))) == 0

    age_redirect = client_b.get(verification.headers["Location"])
    assert age_redirect.status_code == 302
    assert urlsplit(age_redirect.headers["Location"]).path == "/verificar-edad"
    _assert_next(age_redirect.headers["Location"], "/checkout")

    stale_pending = client_a.get(response.headers["Location"])
    assert stale_pending.status_code == 302
    assert stale_pending.headers["Location"] == "/checkout"


def test_register_and_verify_preserve_product_query_string(app):
    client_a = app.test_client()
    client_b = app.test_client()
    next_url = "/productos/iphone-17-pro-max?variant=CRI-00000002-000042"

    response = _register(
        client_a,
        email="product-query@example.com",
        next_url=next_url,
    )
    action_url = _verification_action_url(mail_service.outbox[0])

    _assert_next(response.headers["Location"], next_url)
    assert parse_qs(urlsplit(action_url).query)["next"] == [next_url]
    verification = client_b.get(_relative_url(action_url))
    assert verification.status_code == 302
    assert verification.headers["Location"] == next_url


def test_register_failure_keeps_safe_next_in_hidden_form(client):
    next_url = "/productos/foo?variant=ABC"

    response = client.post(
        "/registro",
        data={
            "email": "failure@example.com",
            "full_name": "Cliente Failure",
            "password": "12345",
            "password_confirmation": "12345",
            "next": next_url,
        },
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert f'name="next" value="{next_url}"' in body
    assert "12345" not in body


def test_login_to_register_keeps_checkout_next(client):
    login_page = client.get("/iniciar-sesion?next=/checkout")
    register_page = client.get("/registro?next=/checkout")

    assert 'href="/registro?next=/checkout"' in login_page.get_data(as_text=True)
    assert 'name="next" value="/checkout"' in register_page.get_data(as_text=True)


def test_pending_user_checkout_resend_and_verify_preserve_next(
    client,
    session,
):
    user = _pending_user(session, email="existing-pending@example.com")
    session.commit()
    assert _login(client, user).status_code == 302

    blocked = client.get("/checkout")
    assert blocked.status_code == 302
    assert urlsplit(blocked.headers["Location"]).path == "/verificacion-pendiente"
    _assert_next(blocked.headers["Location"], "/checkout")

    pending_page = client.get(blocked.headers["Location"])
    assert pending_page.status_code == 200
    assert 'name="next" value="/checkout"' in pending_page.get_data(as_text=True)

    resent = client.post(
        "/reenviar-verificacion",
        data={"next": "/checkout"},
    )
    assert resent.status_code == 302
    _assert_next(resent.headers["Location"], "/checkout")
    action_url = _verification_action_url(mail_service.outbox[0])
    assert parse_qs(urlsplit(action_url).query)["next"] == ["/checkout"]

    verified = client.get(_relative_url(action_url))
    assert verified.status_code == 302
    assert verified.headers["Location"] == "/checkout"


def test_resend_invalidates_old_token_without_losing_checkout_intent(
    client,
    session,
):
    registered = _register(
        client,
        email="token-succession@example.com",
        next_url="/checkout",
    )
    old_action_url = _verification_action_url(mail_service.outbox[0])

    resent = client.post(
        "/reenviar-verificacion",
        data={"next": "/checkout"},
    )
    new_action_url = _verification_action_url(mail_service.outbox[1])

    assert registered.status_code == 302
    assert resent.status_code == 302
    assert urlsplit(old_action_url).path != urlsplit(new_action_url).path
    old_result = client.get(_relative_url(old_action_url))
    assert old_result.status_code == 302
    assert urlsplit(old_result.headers["Location"]).path == "/verificacion-pendiente"
    _assert_next(old_result.headers["Location"], "/checkout")

    session.expire_all()
    user = session.scalar(
        select(User).where(
            User.email_normalized == "token-succession@example.com"
        )
    )
    assert user is not None
    assert user.status == UserStatus.PENDING_VERIFICATION

    new_result = client.get(_relative_url(new_action_url))
    assert new_result.status_code == 302
    assert new_result.headers["Location"] == "/checkout"
    session.expire_all()
    assert session.get(User, user.id).status == UserStatus.ACTIVE


def test_initial_mail_failure_and_resend_keep_checkout_intent(
    client,
    session,
    monkeypatch,
):
    original_send = mail_service.send
    fail_initial = True

    def fail_once(message):
        nonlocal fail_initial
        if fail_initial:
            fail_initial = False
            raise MailDeliveryError("provider unavailable")
        return original_send(message)

    monkeypatch.setattr(mail_service, "send", fail_once)
    registered = _register(
        client,
        email="mail-failure@example.com",
        next_url="/checkout",
    )

    assert registered.status_code == 302
    _assert_next(registered.headers["Location"], "/checkout")
    assert mail_service.outbox == []
    session.expire_all()
    user = session.scalar(
        select(User).where(User.email_normalized == "mail-failure@example.com")
    )
    assert user is not None
    assert user.status == UserStatus.PENDING_VERIFICATION

    resent = client.post(
        "/reenviar-verificacion",
        data={"next": "/checkout"},
    )
    assert resent.status_code == 302
    _assert_next(resent.headers["Location"], "/checkout")
    assert len(mail_service.outbox) == 1
    action_url = _verification_action_url(mail_service.outbox[0])
    assert parse_qs(urlsplit(action_url).query)["next"] == ["/checkout"]

    tokens = session.scalars(
        select(UserAccountToken)
        .where(
            UserAccountToken.user_id == user.id,
            UserAccountToken.purpose == UserAccountTokenPurpose.VERIFY_EMAIL,
        )
        .order_by(UserAccountToken.created_at)
    ).all()
    assert len(tokens) == 2
    assert tokens[0].used_at is not None
    assert tokens[1].used_at is None


def test_resend_mail_failure_keeps_token_and_checkout_intent(
    client,
    session,
    monkeypatch,
):
    user = _pending_user(session, email="resend-failure@example.com")
    session.commit()
    assert _login(client, user).status_code == 302

    def fail(_message):
        raise MailDeliveryError("provider unavailable")

    monkeypatch.setattr(mail_service, "send", fail)
    response = client.post(
        "/reenviar-verificacion",
        data={"next": "/checkout"},
    )

    assert response.status_code == 302
    _assert_next(response.headers["Location"], "/checkout")
    assert mail_service.outbox == []
    session.expire_all()
    token = session.scalar(
        select(UserAccountToken).where(
            UserAccountToken.user_id == user.id,
            UserAccountToken.purpose == UserAccountTokenPurpose.VERIFY_EMAIL,
        )
    )
    assert token is not None
    assert token.used_at is None
    with client.session_transaction() as browser_session:
        assert browser_session["_flashes"][-1] == (
            "warning",
            "No pudimos enviar el correo de verificación en este momento. "
            "Inténtalo nuevamente más tarde.",
        )


@pytest.mark.parametrize(
    "malicious_next",
    (
        "https://evil.example",
        "//evil.example",
        "%2F%2Fevil.example",
        "/\\evil.example",
    ),
)
def test_malicious_next_fails_closed_at_every_verification_stage(
    client,
    malicious_next,
):
    registered = _register(
        client,
        email=f"malicious-{uuid.uuid4().hex[:8]}@example.com",
        next_url=malicious_next,
    )
    assert registered.status_code == 302
    assert not registered.headers["Location"].startswith("https://evil.example")
    _assert_next(registered.headers["Location"], "/")
    initial_action = _verification_action_url(mail_service.outbox[0])
    assert parse_qs(urlsplit(initial_action).query)["next"] == ["/"]

    pending = client.get(
        "/verificacion-pendiente",
        query_string={"next": malicious_next},
    )
    assert pending.status_code == 200
    assert 'name="next" value="/perfil"' in pending.get_data(as_text=True)

    resent = client.post(
        "/reenviar-verificacion",
        data={"next": malicious_next},
    )
    assert resent.status_code == 302
    assert not resent.headers["Location"].startswith("https://evil.example")
    _assert_next(resent.headers["Location"], "/perfil")
    latest_action = _verification_action_url(mail_service.outbox[1])
    assert parse_qs(urlsplit(latest_action).query)["next"] == ["/perfil"]

    verified = client.get(
        urlsplit(latest_action).path,
        query_string={"next": malicious_next},
    )
    assert verified.status_code == 302
    assert verified.headers["Location"] == "/perfil"


def test_invalid_and_expired_tokens_preserve_safe_next(client, session):
    invalid = client.get(
        "/verificar-correo/not-a-valid-token",
        query_string={"next": "/productos/foo?variant=ABC"},
    )
    assert invalid.status_code == 302
    _assert_next(
        invalid.headers["Location"],
        "/productos/foo?variant=ABC",
    )

    user = _pending_user(session)
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=30,
    )
    token = session.get(UserAccountToken, created.token_id)
    token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()

    expired = client.get(
        f"/verificar-correo/{created.token}",
        query_string={"next": "/checkout"},
    )
    assert expired.status_code == 302
    _assert_next(expired.headers["Location"], "/checkout")
    session.expire_all()
    assert session.get(User, user.id).status == UserStatus.PENDING_VERIFICATION
    assert session.get(UserAccountToken, created.token_id).used_at is None


def test_verify_without_next_keeps_historical_profile_fallback(client, session):
    user = _pending_user(session)
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=30,
    )
    session.commit()

    response = client.get(f"/verificar-correo/{created.token}")

    assert response.status_code == 302
    assert response.headers["Location"] == "/perfil"


def test_pending_checkout_get_keeps_query_but_post_returns_to_get_surface(
    client,
    session,
):
    user = _pending_user(session)
    session.commit()
    assert _login(client, user).status_code == 302

    get_response = client.get("/checkout?foo=bar")
    assert get_response.status_code == 302
    _assert_next(get_response.headers["Location"], "/checkout?foo=bar")

    post_response = client.post(
        "/checkout?foo=bar",
        data={"checkout_token": "must-not-run"},
    )
    assert post_response.status_code == 302
    _assert_next(post_response.headers["Location"], "/checkout")
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert session.scalar(select(func.count(InventoryReservation.id))) == 0


def test_safe_next_fragment_is_removed_before_mail_and_verify(app):
    client_a = app.test_client()
    client_b = app.test_client()
    sanitized = "/producto/foo?variant=1"

    response = _register(
        client_a,
        email="fragment@example.com",
        next_url=f"{sanitized}#reviews",
    )
    _assert_next(response.headers["Location"], sanitized)
    action_url = _verification_action_url(mail_service.outbox[0])
    assert parse_qs(urlsplit(action_url).query)["next"] == [sanitized]

    verified = client_b.get(_relative_url(action_url))
    assert verified.status_code == 302
    assert verified.headers["Location"] == sanitized
