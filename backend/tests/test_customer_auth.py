from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Order, PaymentAttempt, PaymentProof, User, UserAccountToken
from app.models.enums import (
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    UserAccountTokenPurpose,
    UserStatus,
)
from app.services.account_tokens import create_account_token, hash_account_token
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    app.config["MAIL_BACKEND"] = "memory"
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email="cliente@test.local",
    password="correct horse battery staple",
    verified=True,
    status=UserStatus.ACTIVE,
):
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash(password),
        full_name="Cliente Ecuvel",
        status=status,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, email="cliente@test.local", password="correct horse battery staple", next_url="/"):
    return client.post(
        "/iniciar-sesion",
        data={"email": email, "password": password, "next": next_url},
        follow_redirects=False,
    )


def test_registers_customer_with_hashed_password_and_token(client, session):
    response = client.post(
        "/registro",
        data={
            "email": "Cliente@Example.COM",
            "full_name": "  Cliente   Ecuvel  ",
            "password": "correct horse battery staple",
            "password_confirmation": "correct horse battery staple",
            "next": "/checkout",
        },
    )

    assert response.status_code == 302
    user = session.scalar(select(User).where(User.email_normalized == "cliente@example.com"))
    assert user is not None
    assert user.email == "Cliente@Example.COM"
    assert user.full_name == "Cliente Ecuvel"
    assert user.password_hash != "correct horse battery staple"
    assert "correct horse battery staple" not in response.get_data(as_text=True)
    token = session.scalar(select(UserAccountToken))
    assert token is not None
    assert token.purpose == UserAccountTokenPurpose.VERIFY_EMAIL
    assert len(token.token_hash) == 64


def test_registration_rejects_duplicate_email_case_insensitive(client, session):
    _user(session, email="cliente@example.com")
    session.commit()

    response = client.post(
        "/registro",
        data={
            "email": "CLIENTE@example.com",
            "full_name": "Cliente Otro",
            "password": "correct horse battery staple",
            "password_confirmation": "correct horse battery staple",
        },
    )

    assert response.status_code == 400
    assert session.scalar(select(func.count(User.id))) == 1


def test_login_is_case_insensitive_and_rejects_open_redirect(client, session):
    _user(session, email="cliente@example.com")
    session.commit()

    response = _login(client, email="CLIENTE@example.com", next_url="https://evil.test")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_rejects_wrong_password_with_generic_message(client, session):
    _user(session)
    session.commit()

    response = _login(client, password="wrong password")

    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Correo o contraseña incorrectos" in body
    assert "cliente@test.local" in body
    assert "wrong password" not in body


def test_logout_requires_post(client, session):
    _user(session)
    session.commit()
    assert _login(client).status_code == 302

    assert client.get("/cerrar-sesion").status_code == 405
    assert client.post("/cerrar-sesion").status_code == 302


def test_valid_email_verification_token_verifies_user(client, session):
    user = _user(session, verified=False, status=UserStatus.PENDING_VERIFICATION)
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=30,
    )
    session.commit()

    response = client.get(f"/verificar-correo/{created.token}")
    session.expire_all()

    assert response.status_code == 302
    user = session.get(User, user.id)
    assert user.status == UserStatus.ACTIVE
    assert user.email_verified_at is not None
    assert session.scalar(select(UserAccountToken.used_at)) is not None


def test_verification_token_is_one_time(client, session):
    user = _user(session, verified=False, status=UserStatus.PENDING_VERIFICATION)
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=30,
    )
    session.commit()

    assert client.get(f"/verificar-correo/{created.token}").status_code == 302
    second = client.get(f"/verificar-correo/{created.token}")

    assert second.status_code == 302
    assert b"Set-Cookie" not in second.data


def test_password_recovery_uses_generic_response(client, session):
    _user(session, email="known@test.local")
    session.commit()

    known = client.post("/recuperar-contrasena", data={"email": "known@test.local"})
    unknown = client.post("/recuperar-contrasena", data={"email": "missing@test.local"})

    assert known.status_code == 302
    assert unknown.status_code == 302
    assert session.scalar(select(func.count(UserAccountToken.id))) == 1


def test_password_reset_updates_hash_and_is_one_time(client, session):
    user = _user(session)
    old_hash = user.password_hash
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.RESET_PASSWORD,
        ttl_minutes=30,
    )
    session.commit()

    response = client.post(
        f"/restablecer-contrasena/{created.token}",
        data={
            "password": "another correct horse",
            "password_confirmation": "another correct horse",
        },
    )
    session.expire_all()

    assert response.status_code == 302
    assert session.get(User, user.id).password_hash != old_hash
    second = client.post(
        f"/restablecer-contrasena/{created.token}",
        data={
            "password": "another correct horse",
            "password_confirmation": "another correct horse",
        },
    )
    assert second.status_code == 400


def test_profile_requires_login_and_updates_allowed_fields(client, session):
    user = _user(session)
    session.commit()

    assert client.get("/perfil").status_code == 302
    _login(client)
    response = client.post(
        "/perfil/datos",
        data={
            "full_name": "Cliente Actualizado",
            "phone": "+593999333014",
            "birth_date": "2002-02-05",
            "gender": "male",
            "password_hash": "tampered",
        },
    )
    session.expire_all()

    assert response.status_code == 302
    updated = session.get(User, user.id)
    assert updated.full_name == "Cliente Actualizado"
    assert updated.birth_date == date(2002, 2, 5)
    assert updated.gender == "male"
    assert updated.password_hash != "tampered"


def test_email_change_does_not_apply_until_verified(client, session):
    user = _user(session)
    session.commit()
    _login(client)

    response = client.post(
        "/perfil/cambiar-correo",
        data={
            "new_email": "nuevo@test.local",
            "current_password": "correct horse battery staple",
        },
    )
    session.expire_all()
    token = session.scalar(
        select(UserAccountToken).where(
            UserAccountToken.purpose == UserAccountTokenPurpose.CHANGE_EMAIL
        )
    )

    assert response.status_code == 302
    assert session.get(User, user.id).email == "cliente@test.local"
    assert token is not None


def test_anonymous_and_unverified_checkout_are_blocked(client, session):
    base = create_catalog_and_stock(session, stock=5)
    session.commit()
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {str(base.offer_id): {"quantity": 1, "selected": True}},
        }

    anonymous = client.get("/checkout")
    assert anonymous.status_code == 302
    assert "/iniciar-sesion" in anonymous.headers["Location"]

    _user(session, verified=False, status=UserStatus.PENDING_VERIFICATION)
    session.commit()
    _login(client)
    unverified = client.get("/checkout")
    assert unverified.status_code == 302
    assert "/verificacion-pendiente" in unverified.headers["Location"]


def test_verified_checkout_uses_current_user_as_buyer(client, session):
    base = create_catalog_and_stock(session, stock=5)
    user = _user(session)
    session.commit()
    _login(client)
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {str(base.offer_id): {"quantity": 1, "selected": True}},
        }

    page = client.get("/checkout")
    with client.session_transaction() as browser_session:
        token = browser_session["checkout_draft"]["token"]
    response = client.post(
        "/checkout",
        data={"checkout_token": token, "payment_method": "BANK_TRANSFER"},
    )
    session.expire_all()

    assert page.status_code == 200
    assert response.status_code == 302
    assert session.scalar(select(Order)).buyer_id == user.id


def test_login_claims_only_current_session_demo_orders(client, app, session):
    base = create_catalog_and_stock(session, stock=5)
    demo = session.get(User, base.buyer_id)
    demo.email = app.config["CHECKOUT_DEMO_BUYER_EMAIL"]
    owned_id, _owned_number, _ = create_order_items(session, base, [1])
    other_base = create_catalog_and_stock(session, stock=5)
    other_id, _other_number, _ = create_order_items(session, other_base, [1])
    target = _user(session)
    session.commit()
    before_status = session.get(Order, owned_id).status
    with client.session_transaction() as browser_session:
        browser_session["checkout_order_ids"] = [str(owned_id), str(other_id)]

    _login(client)
    session.expire_all()

    assert session.get(Order, owned_id).buyer_id == target.id
    assert session.get(Order, other_id).buyer_id == other_base.buyer_id
    assert session.get(Order, owned_id).status == before_status


def test_orders_and_private_proof_are_owned_by_current_user(client, session):
    base = create_catalog_and_stock(session, stock=5)
    order_id, order_number, _ = create_order_items(session, base, [1])
    attempt = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.PROCESSING,
        amount=10,
        currency="USD",
        idempotency_key=f"checkout:{uuid.uuid4().hex}",
        request_fingerprint="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    session.add(attempt)
    session.flush()
    proof = PaymentProof(
        payment_attempt_id=attempt.id,
        storage_key=f"proofs/{uuid.uuid4().hex}.png",
        original_filename="proof.png",
        media_type="image/png",
        size_bytes=16,
        sha256="b" * 64,
        status=PaymentProofStatus.PENDING_REVIEW,
        upload_idempotency_key=f"upload:{uuid.uuid4().hex}",
        uploaded_by_user_id=base.buyer_id,
    )
    session.add(proof)
    _user(session, email="other@test.local")
    session.commit()

    _login(client, email="other@test.local")
    assert client.get(f"/pedidos/{order_number}").status_code == 404
    assert client.get(f"/pagos/comprobantes/{proof.id}/archivo").status_code == 404


def test_token_hash_helper_never_returns_plain_token():
    token = "secret-token"
    assert hash_account_token(token) != token
    assert len(hash_account_token(token)) == 64
