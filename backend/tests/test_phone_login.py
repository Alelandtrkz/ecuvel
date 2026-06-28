from __future__ import annotations

from sqlalchemy import select

from app.models import User
from tests.phone_auth_helpers import (
    create_user,
    phone_client,
    request_phone_code,
    verify_phone_code,
)


def test_existing_phone_otp_logs_user_in(phone_client, session):
    user = create_user(
        session,
        email=None,
        password=None,
        phone="+593999330014",
        phone_verified=True,
    )
    session.commit()

    request_phone_code(phone_client)
    response = verify_phone_code(phone_client)
    session.expire_all()

    assert response.status_code == 302
    assert session.get(User, user.id).last_login_at is not None
    with phone_client.session_transaction() as browser_session:
        assert browser_session["_user_id"] == str(user.id)


def test_phone_login_updates_last_login_at(phone_client, session):
    user = create_user(
        session,
        email=None,
        password=None,
        phone="+593999330014",
        phone_verified=True,
    )
    session.commit()

    request_phone_code(phone_client)
    verify_phone_code(phone_client)
    session.expire_all()

    assert session.get(User, user.id).last_login_at is not None


def test_phone_login_rejects_inactive_user(phone_client, session):
    create_user(
        session,
        email=None,
        password=None,
        phone="+593999330014",
        phone_verified=True,
        active=False,
    )
    session.commit()

    request_phone_code(phone_client)
    response = verify_phone_code(phone_client)

    assert response.status_code == 400
    with phone_client.session_transaction() as browser_session:
        assert "_user_id" not in browser_session
