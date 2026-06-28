from __future__ import annotations

from sqlalchemy import func, select

from app.models import InventoryMovement, User
from app.services.phone_otp import fake_phone_otp_sender
from tests.phone_auth_helpers import (
    create_user,
    latest_otp,
    phone_client,
    request_phone_code,
)


def test_phone_auth_requires_csrf(app, session):
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["PHONE_OTP_BACKEND"] = "fake"
    app.config["PHONE_OTP_PEPPER"] = "test-only-phone-otp-pepper"
    client = app.test_client()

    response = client.post("/ingresar-telefono", data={"phone": "0999330014"})

    assert response.status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False


def test_external_next_redirect_is_rejected(phone_client, session):
    user = create_user(
        session,
        email=None,
        password=None,
        phone="+593999330014",
        phone_verified=True,
    )
    session.commit()

    request_phone_code(phone_client, next_url="https://evil.test")
    response = phone_client.post("/verificar-telefono", data={"code": latest_otp()})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_phone_auth_does_not_modify_inventory(phone_client, session):
    before = session.scalar(select(func.count(InventoryMovement.id)))
    request_phone_code(phone_client)
    phone_client.post("/verificar-telefono", data={"code": latest_otp()})
    after = session.scalar(select(func.count(InventoryMovement.id)))

    assert before == after


def test_phone_number_is_masked_in_fake_outbox(phone_client, session):
    request_phone_code(phone_client)

    phone, code = fake_phone_otp_sender.outbox[-1]
    assert phone == "+593999330014"
    assert code not in phone
