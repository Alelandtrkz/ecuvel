from __future__ import annotations

from sqlalchemy import select

from app.models import User
from tests.phone_auth_helpers import phone_client, request_phone_code, verify_phone_code


def test_verified_new_phone_can_create_user(phone_client, session):
    request_phone_code(phone_client)
    verify_phone_code(phone_client)
    response = phone_client.post(
        "/registro/telefono/completar",
        data={"full_name": "Cliente Teléfono", "email": ""},
        follow_redirects=False,
    )
    user = session.scalar(select(User).where(User.phone_normalized == "+593999330014"))

    assert response.status_code == 302
    assert user is not None
    assert user.email is None
    assert user.password_hash is None
    assert user.phone_verified_at is not None


def test_phone_registration_requires_full_name(phone_client, session):
    request_phone_code(phone_client)
    verify_phone_code(phone_client)
    response = phone_client.post(
        "/registro/telefono/completar",
        data={"full_name": "", "email": ""},
    )

    assert response.status_code == 400
    assert session.scalar(select(User)) is None


def test_phone_registration_preserves_cart(phone_client, session):
    with phone_client.session_transaction() as browser_session:
        browser_session["cart"] = {"version": 1, "items": {"offer": {"quantity": 2}}}
    request_phone_code(phone_client)
    verify_phone_code(phone_client)
    phone_client.post(
        "/registro/telefono/completar",
        data={"full_name": "Cliente Teléfono", "email": ""},
    )

    with phone_client.session_transaction() as browser_session:
        assert browser_session["cart"]["items"]["offer"]["quantity"] == 2
