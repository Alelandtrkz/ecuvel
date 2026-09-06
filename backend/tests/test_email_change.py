from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Store, StoreMember, User, UserAccountToken
from app.models.enums import (
    StoreMemberRole,
    StoreStatus,
    UserAccountTokenPurpose,
    UserStatus,
)
from app.services.account_tokens import create_account_token, hash_account_token
from app.services.authentication import LoginError, authenticate_customer
from app.services.mail import MailDeliveryError, mail_service
from app.services.user_profiles import (
    ProfileError,
    confirm_email_change,
    request_email_change,
)


pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
TOKEN_TTL_MINUTES = 30


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email: str | None,
    password: str | None = PASSWORD,
    status: UserStatus = UserStatus.ACTIVE,
    is_active: bool = True,
) -> User:
    token = uuid.uuid4().hex[:8]
    user = User(
        public_code=f"ECV-U-{token.upper()}",
        email=email,
        email_normalized=email.strip().casefold() if email else None,
        password_hash=generate_password_hash(password) if password else None,
        full_name="Cliente Email",
        status=status,
        is_active=is_active,
        email_verified_at=(
            datetime(2020, 1, 1, tzinfo=timezone.utc) if email else None
        ),
    )
    session.add(user)
    session.flush()
    return user


def _store(session, *, name: str) -> Store:
    token = uuid.uuid4().hex[:8]
    store = Store(
        public_code=f"ECV-S-{token.upper()}",
        name=name,
        slug=f"{name.casefold().replace(' ', '-')}-{token}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add(store)
    session.flush()
    return store


def _login(client, *, email: str, password: str = PASSWORD):
    return client.post(
        "/iniciar-sesion",
        data={"email": email, "password": password, "next": "/"},
    )


def _request(session, user: User, new_email: str) -> str:
    _user_result, token = request_email_change(
        session=session,
        user_id=user.id,
        new_email=new_email,
        current_password=PASSWORD,
        ttl_minutes=TOKEN_TTL_MINUTES,
    )
    return token


@pytest.mark.parametrize(
    ("current_email", "new_email"),
    (
        ("user@example.com", "user@example.com"),
        ("User@Example.com", "user@example.com"),
        ("user@example.com", " USER@example.com "),
    ),
)
def test_same_email_is_rejected_without_token_or_mail(
    client,
    session,
    current_email,
    new_email,
):
    _user(session, email=current_email)
    session.commit()
    _login(client, email=current_email)

    response = client.post(
        "/perfil/cambiar-correo",
        data={"new_email": new_email, "current_password": PASSWORD},
    )

    assert response.status_code == 400
    assert "igual a tu correo actual" in response.get_data(as_text=True)
    assert session.scalar(select(func.count(UserAccountToken.id))) == 0
    assert mail_service.outbox == []


@pytest.mark.parametrize("new_email", ("taken@example.com", "TAKEN@EXAMPLE.COM"))
def test_other_account_email_is_rejected_without_token_or_mail(
    client,
    session,
    new_email,
):
    _user(session, email="owner@example.com")
    _user(session, email="taken@example.com")
    session.commit()
    _login(client, email="owner@example.com")

    response = client.post(
        "/perfil/cambiar-correo",
        data={"new_email": new_email, "current_password": PASSWORD},
    )

    assert response.status_code == 400
    assert "Ya existe una cuenta con este correo" in response.get_data(as_text=True)
    assert session.scalar(select(func.count(UserAccountToken.id))) == 0
    assert mail_service.outbox == []


def test_wrong_current_password_is_rejected_without_token_or_mail(
    client,
    session,
):
    _user(session, email="old@example.com")
    session.commit()
    _login(client, email="old@example.com")

    response = client.post(
        "/perfil/cambiar-correo",
        data={
            "new_email": "new@example.com",
            "current_password": "wrong password",
        },
    )

    assert response.status_code == 400
    assert "contraseña actual no es correcta" in response.get_data(as_text=True)
    assert session.scalar(select(func.count(UserAccountToken.id))) == 0
    assert mail_service.outbox == []


def test_same_email_rejection_does_not_invalidate_previous_token(session):
    user = _user(session, email="old@example.com")
    previous_token = _request(session, user, "pending@example.com")
    previous_hash = hash_account_token(previous_token)
    session.commit()

    with pytest.raises(ProfileError, match="igual a tu correo actual"):
        _request(session, user, " OLD@example.com ")

    stored_tokens = session.scalars(
        select(UserAccountToken).where(
            UserAccountToken.purpose == UserAccountTokenPurpose.CHANGE_EMAIL
        )
    ).all()
    assert len(stored_tokens) == 1
    assert stored_tokens[0].token_hash == previous_hash
    assert stored_tokens[0].used_at is None


def test_valid_request_persists_token_and_sends_transactional_mail(
    client,
    app,
    session,
):
    _user(session, email="old@example.com")
    session.commit()
    _login(client, email="old@example.com")

    response = client.post(
        "/perfil/cambiar-correo",
        data={"new_email": "new@example.com", "current_password": PASSWORD},
    )
    account_token = session.scalar(
        select(UserAccountToken).where(
            UserAccountToken.purpose == UserAccountTokenPurpose.CHANGE_EMAIL
        )
    )

    assert response.status_code == 302
    assert account_token is not None
    assert account_token.new_email == "new@example.com"
    assert account_token.new_email_normalized == "new@example.com"
    assert account_token.used_at is None
    assert len(mail_service.outbox) == 1
    message = mail_service.outbox[0]
    assert message.subject == "Confirma tu nuevo correo de ECUVEL"
    assert message.tags == {"mail_type": "CHANGE_EMAIL"}
    assert message.text_body
    assert message.html_body
    assert "https://ecuvel.test/perfil/confirmar-correo/" in message.text_body
    assert "https://ecuvel.test/perfil/confirmar-correo/" in message.html_body
    expiration = str(app.config["EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"])
    assert expiration in message.text_body
    assert expiration in message.html_body


def test_mail_failure_is_controlled_and_keeps_persisted_token(
    client,
    session,
    monkeypatch,
    caplog,
):
    _user(session, email="old@example.com")
    session.commit()
    _login(client, email="old@example.com")

    def fail(_message):
        raise MailDeliveryError("provider detail for new@example.com")

    monkeypatch.setattr(mail_service, "send", fail)
    caplog.set_level("WARNING")
    response = client.post(
        "/perfil/cambiar-correo",
        data={"new_email": "new@example.com", "current_password": PASSWORD},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "No pudimos enviar el correo de confirmación" in body
    assert "Enviamos un enlace de confirmación" not in body
    assert session.scalar(select(func.count(UserAccountToken.id))) == 1
    assert "mail_type=CHANGE_EMAIL" in caplog.text
    assert "MailDeliveryError" in caplog.text
    assert "new@example.com" not in caplog.text


@pytest.mark.parametrize(
    ("status_before_confirmation", "is_active", "expected_status"),
    (
        (UserStatus.PENDING_VERIFICATION, True, UserStatus.ACTIVE),
        (UserStatus.ACTIVE, True, UserStatus.ACTIVE),
        (UserStatus.BLOCKED, True, UserStatus.BLOCKED),
        (UserStatus.SUSPENDED, False, UserStatus.SUSPENDED),
    ),
)
def test_confirmation_preserves_account_status_and_is_active(
    session,
    status_before_confirmation,
    is_active,
    expected_status,
):
    user = _user(session, email="old@example.com")
    token = _request(session, user, "new@example.com")
    user.status = status_before_confirmation
    user.is_active = is_active
    old_verified_at = user.email_verified_at
    session.commit()

    confirmed = confirm_email_change(session=session, token=token)

    assert confirmed.id == user.id
    assert confirmed.email == "new@example.com"
    assert confirmed.email_normalized == "new@example.com"
    assert confirmed.email_verified_at > old_verified_at
    assert confirmed.status == expected_status
    assert confirmed.is_active is is_active
    assert confirmed.auth_version == 1


def test_confirmed_token_cannot_be_reused(session):
    user = _user(session, email="old@example.com")
    token = _request(session, user, "new@example.com")
    session.commit()
    confirm_email_change(session=session, token=token)
    session.commit()

    with pytest.raises(ProfileError, match="no es válido o ya caducó"):
        confirm_email_change(session=session, token=token)


def test_expired_token_is_rejected_without_changing_email(session):
    user = _user(session, email="old@example.com")
    token = _request(session, user, "new@example.com")
    account_token = session.scalar(
        select(UserAccountToken).where(
            UserAccountToken.token_hash == hash_account_token(token)
        )
    )
    account_token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()

    with pytest.raises(ProfileError, match="no es válido o ya caducó"):
        confirm_email_change(session=session, token=token)

    assert user.email == "old@example.com"


def test_second_request_invalidates_first_change_email_token(session):
    user = _user(session, email="old@example.com")
    first_token = _request(session, user, "first@example.com")
    second_token = _request(session, user, "second@example.com")
    session.commit()

    with pytest.raises(ProfileError, match="no es válido o ya caducó"):
        confirm_email_change(session=session, token=first_token)
    session.rollback()

    confirmed = confirm_email_change(session=session, token=second_token)
    assert confirmed.email == "second@example.com"


def test_confirmation_revalidates_email_and_rolls_back_token_consumption(
    client,
    session,
):
    user = _user(session, email="old@example.com")
    user_id = user.id
    old_verified_at = user.email_verified_at
    token = _request(session, user, "target@example.com")
    token_hash = hash_account_token(token)
    session.commit()
    _user(session, email="target@example.com")
    session.commit()

    response = client.get(f"/perfil/confirmar-correo/{token}")
    session.expire_all()

    stored_user = session.get(User, user_id)
    stored_token = session.scalar(
        select(UserAccountToken).where(UserAccountToken.token_hash == token_hash)
    )
    assert response.status_code == 302
    assert stored_user.email == "old@example.com"
    assert stored_user.email_normalized == "old@example.com"
    assert stored_user.email_verified_at == old_verified_at
    assert stored_user.status == UserStatus.ACTIVE
    assert stored_token.used_at is None


def test_passwordless_token_issued_before_hardening_cannot_change_email(session):
    user = _user(session, email=None, password=None)
    user_id = user.id
    created = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.CHANGE_EMAIL,
        ttl_minutes=TOKEN_TTL_MINUTES,
        new_email="target@example.com",
    )
    token_hash = hash_account_token(created.token)
    session.commit()

    with pytest.raises(ProfileError, match="nueva verificación telefónica"):
        confirm_email_change(session=session, token=created.token)
    session.rollback()
    session.expire_all()

    assert session.get(User, user_id).email is None
    stored_token = session.scalar(
        select(UserAccountToken).where(UserAccountToken.token_hash == token_hash)
    )
    assert stored_token.used_at is None


class _ConstraintViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def _integrity_error(constraint_name: str) -> IntegrityError:
    return IntegrityError(
        "UPDATE users",
        {},
        _ConstraintViolation(constraint_name),
    )


def test_email_unique_violation_becomes_profile_error(session, monkeypatch):
    user = _user(session, email="old@example.com")
    user_id = user.id
    token = _request(session, user, "target@example.com")
    token_hash = hash_account_token(token)
    session.commit()
    real_flush = session.flush

    def fail_target_flush(*args, **kwargs):
        if user.email == "target@example.com":
            raise _integrity_error("ix_users_email_normalized")
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_target_flush)
    with pytest.raises(ProfileError, match="Ya existe una cuenta"):
        confirm_email_change(session=session, token=token)
    monkeypatch.undo()
    session.rollback()
    session.expire_all()

    assert session.get(User, user_id).email == "old@example.com"
    stored_token = session.scalar(
        select(UserAccountToken).where(UserAccountToken.token_hash == token_hash)
    )
    assert stored_token.used_at is None


def test_unrelated_integrity_error_is_not_hidden(session, monkeypatch):
    user = _user(session, email="old@example.com")
    token = _request(session, user, "target@example.com")
    session.commit()
    real_flush = session.flush

    def fail_target_flush(*args, **kwargs):
        if user.email == "target@example.com":
            raise _integrity_error("uq_unrelated_domain")
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_target_flush)
    with pytest.raises(IntegrityError):
        confirm_email_change(session=session, token=token)
    monkeypatch.undo()
    session.rollback()


def test_email_change_preserves_multiple_store_memberships_and_login_identity(
    session,
):
    user = _user(session, email="old@example.com")
    first_store = _store(session, name="Tienda Uno")
    second_store = _store(session, name="Tienda Dos")
    first_member = StoreMember(
        store_id=first_store.id,
        user_id=user.id,
        role=StoreMemberRole.OWNER,
        is_active=True,
    )
    second_member = StoreMember(
        store_id=second_store.id,
        user_id=user.id,
        role=StoreMemberRole.FINANCE_OPERATOR,
        is_active=False,
    )
    session.add_all((first_member, second_member))
    session.flush()
    user_id = user.id
    snapshots = {
        member.id: (member.store_id, member.user_id, member.role, member.is_active)
        for member in (first_member, second_member)
    }

    token = _request(session, user, "new@example.com")
    session.commit()
    assert user.email == "old@example.com"
    assert {
        member.id: (member.store_id, member.user_id, member.role, member.is_active)
        for member in session.scalars(
            select(StoreMember).where(StoreMember.user_id == user_id)
        )
    } == snapshots

    confirmed = confirm_email_change(session=session, token=token)
    session.commit()
    session.expire_all()

    memberships = session.scalars(
        select(StoreMember).where(StoreMember.user_id == user_id)
    ).all()
    assert confirmed.id == user_id
    assert {
        member.id: (member.store_id, member.user_id, member.role, member.is_active)
        for member in memberships
    } == snapshots
    assert authenticate_customer(
        session=session,
        email="new@example.com",
        password=PASSWORD,
    ).id == user_id
    with pytest.raises(LoginError):
        authenticate_customer(
            session=session,
            email="old@example.com",
            password=PASSWORD,
        )
