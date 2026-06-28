from __future__ import annotations

from sqlalchemy import select

from app.models import User
from tests.phone_auth_helpers import create_user, latest_otp, phone_client


def _login(client, email="cliente@test.local", password="correct horse battery staple"):
    return client.post(
        "/iniciar-sesion",
        data={"email": email, "password": password, "next": "/"},
    )


def test_email_user_can_link_verified_phone(phone_client, session):
    user = create_user(session)
    session.commit()
    _login(phone_client)

    assert phone_client.post(
        "/perfil/agregar-telefono",
        data={"phone": "0999330014"},
    ).status_code == 302
    assert phone_client.post(
        "/verificar-telefono",
        data={"code": latest_otp()},
    ).status_code == 302
    session.expire_all()

    updated = session.get(User, user.id)
    assert updated.phone_normalized == "+593999330014"
    assert updated.phone_verified_at is not None


def test_user_cannot_link_phone_owned_by_another_user(phone_client, session):
    create_user(
        session,
        email="owner@test.local",
        phone="+593999330014",
        phone_verified=True,
    )
    other = create_user(session, email="other@test.local")
    session.commit()
    _login(phone_client, email="other@test.local")

    phone_client.post("/perfil/agregar-telefono", data={"phone": "0999330014"})
    response = phone_client.post("/verificar-telefono", data={"code": latest_otp()})
    session.expire_all()

    assert response.status_code == 400
    assert session.get(User, other.id).phone_normalized is None


def test_phone_user_can_add_email_and_create_password(phone_client, session):
    user = create_user(
        session,
        email=None,
        password=None,
        phone="+593999330014",
        phone_verified=True,
    )
    session.commit()
    phone_client.post("/ingresar-telefono", data={"phone": "0999330014", "next": "/"})
    phone_client.post("/verificar-telefono", data={"code": latest_otp()})

    email_response = phone_client.post(
        "/perfil/cambiar-correo",
        data={"new_email": "nuevo@test.local"},
    )
    password_response = phone_client.post(
        "/perfil/crear-contrasena",
        data={
            "new_password": "correct horse battery staple",
            "new_password_confirmation": "correct horse battery staple",
        },
    )
    session.expire_all()

    assert email_response.status_code == 302
    assert password_response.status_code == 302
    assert session.get(User, user.id).password_hash is not None
