from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import User
from app.models.enums import UserStatus
from app.services.phone_otp import fake_phone_otp_sender


pytestmark = pytest.mark.integration


@pytest.fixture
def phone_client(app):
    app.config["PHONE_OTP_BACKEND"] = "fake"
    app.config["PHONE_OTP_PEPPER"] = "test-only-phone-otp-pepper"
    app.config["PHONE_OTP_RESEND_COOLDOWN_SECONDS"] = 10
    app.config["PHONE_OTP_TTL_SECONDS"] = 300
    app.config["PHONE_OTP_MAX_ATTEMPTS"] = 5
    app.config["PHONE_OTP_CODE_LENGTH"] = 6
    fake_phone_otp_sender.outbox.clear()
    test_client = app.test_client()
    yield test_client
    fake_phone_otp_sender.outbox.clear()
    db.session.remove()


def latest_otp() -> str:
    assert fake_phone_otp_sender.outbox
    return fake_phone_otp_sender.outbox[-1][1]


def create_user(
    session,
    *,
    email: str | None = "cliente@test.local",
    password: str | None = "correct horse battery staple",
    phone: str | None = None,
    phone_verified: bool = False,
    active: bool = True,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold() if email else None,
        password_hash=generate_password_hash(password) if password else None,
        full_name="Cliente Ecuvel",
        phone=phone,
        phone_normalized=phone,
        phone_verified_at=datetime.now(timezone.utc) if phone_verified else None,
        status=status,
        email_verified_at=datetime.now(timezone.utc) if email else None,
        is_active=active,
    )
    session.add(user)
    session.flush()
    return user


def request_phone_code(client, phone: str = "0999330014", *, next_url: str = "/"):
    return client.post(
        "/ingresar-telefono",
        data={"phone": phone, "next": next_url},
        follow_redirects=False,
    )


def verify_phone_code(client, code: str | None = None):
    return client.post(
        "/verificar-telefono",
        data={"code": code or latest_otp()},
        follow_redirects=False,
    )
