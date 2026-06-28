from __future__ import annotations

from sqlalchemy import func, select

from app.models import PhoneOtpChallenge, User
from app.services.phone_otp import fake_phone_otp_sender, normalize_phone_number
from tests.phone_auth_helpers import create_user, phone_client, request_phone_code


def test_phone_otp_request_normalizes_ecuador_number(phone_client, session):
    response = request_phone_code(phone_client, "0999330014")
    challenge = session.scalar(select(PhoneOtpChallenge))

    assert response.status_code == 302
    assert challenge.phone_normalized == "+593999330014"
    assert normalize_phone_number("593999330014") == "+593999330014"
    assert normalize_phone_number("+593999330014") == "+593999330014"


def test_phone_otp_request_does_not_reveal_existing_account(phone_client, session):
    create_user(session, phone="+593999330014", phone_verified=True)
    session.commit()

    existing = request_phone_code(phone_client, "0999330014")
    body_existing = phone_client.get(existing.headers["Location"]).get_data(as_text=True)
    session.expunge_all()
    new = request_phone_code(phone_client, "0999330015")
    body_new = phone_client.get(new.headers["Location"]).get_data(as_text=True)

    assert existing.status_code == 302
    assert new.status_code == 302
    assert "Código de verificación" in body_existing
    assert "Código de verificación" in body_new


def test_phone_otp_is_not_stored_in_plain_text(phone_client, session):
    request_phone_code(phone_client)
    challenge = session.scalar(select(PhoneOtpChallenge))
    code = fake_phone_otp_sender.outbox[-1][1]

    assert challenge.code_hash != code
    assert len(challenge.code_hash) == 64


def test_phone_otp_request_respects_cooldown(phone_client, session):
    assert request_phone_code(phone_client).status_code == 302
    second = request_phone_code(phone_client)

    assert second.status_code == 400
    assert session.scalar(select(func.count(PhoneOtpChallenge.id))) == 1
