from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import PhoneOtpChallenge
from tests.phone_auth_helpers import (
    latest_otp,
    phone_client,
    request_phone_code,
    verify_phone_code,
)


def test_valid_phone_otp_is_verified(phone_client, session):
    request_phone_code(phone_client)
    response = verify_phone_code(phone_client)
    challenge = session.scalar(select(PhoneOtpChallenge))

    assert response.status_code == 302
    assert "/registro/telefono/completar" in response.headers["Location"]
    assert challenge.verified_at is not None
    assert challenge.consumed_at is None


def test_invalid_phone_otp_increments_attempts(phone_client, session):
    request_phone_code(phone_client)
    response = verify_phone_code(phone_client, "000000")
    session.expire_all()
    challenge = session.scalar(select(PhoneOtpChallenge))

    assert response.status_code == 400
    assert challenge.attempt_count == 1


def test_expired_phone_otp_is_rejected(phone_client, session):
    request_phone_code(phone_client)
    challenge = session.scalar(select(PhoneOtpChallenge))
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()

    assert verify_phone_code(phone_client, latest_otp()).status_code == 400


def test_consumed_phone_otp_cannot_be_reused(phone_client, session):
    request_phone_code(phone_client)
    assert verify_phone_code(phone_client).status_code == 302
    first = session.scalar(select(PhoneOtpChallenge))
    first.consumed_at = datetime.now(timezone.utc)
    session.commit()

    assert verify_phone_code(phone_client, latest_otp()).status_code == 302


def test_phone_otp_locks_after_max_attempts(phone_client, session):
    request_phone_code(phone_client)
    for _ in range(5):
        verify_phone_code(phone_client, "111111")

    session.expire_all()
    challenge = session.scalar(select(PhoneOtpChallenge))
    assert challenge.attempt_count == 5
    assert verify_phone_code(phone_client, latest_otp()).status_code == 400
