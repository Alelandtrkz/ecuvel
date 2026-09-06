from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import User
from app.models.enums import UserAccountTokenPurpose, UserStatus
from app.models.user import normalize_email
from app.services.account_tokens import (
    InvalidAccountTokenError,
    create_account_token,
    consume_account_token,
)


DUMMY_PASSWORD_HASH = generate_password_hash(
    "ecuvel-auth-dummy-password-not-a-user-credential"
)
AUTH_IDENTITY_PREFIX = "v1"
AUTH_IDENTITY_MAX_LENGTH = 64


class AuthenticationError(Exception):
    """Error base de autenticación."""


class RegistrationError(AuthenticationError):
    """Los datos de registro no son válidos."""


class LoginError(AuthenticationError):
    """Las credenciales no son válidas."""


class PasswordPolicyError(AuthenticationError):
    """La contraseña no cumple la política."""


@dataclass(frozen=True, slots=True)
class RegisteredUserResult:
    user: User
    verification_token: str


@dataclass(frozen=True, slots=True)
class AuthenticationIdentity:
    user_id: uuid.UUID
    auth_version: int
    is_legacy: bool = False


def normalize_full_name(value: str) -> str:
    return " ".join(value.strip().split())


def validate_password(password: str, *, min_length: int) -> None:
    if len(password) < min_length:
        raise PasswordPolicyError(
            f"La contraseña debe tener al menos {min_length} caracteres."
        )
    if len(password) > 128:
        raise PasswordPolicyError(
            "La contraseña no puede superar 128 caracteres."
        )


def public_user_code() -> str:
    return f"ECV-U-{uuid.uuid4().hex[:8].upper()}"


def is_user_authentication_allowed(user: User | None) -> bool:
    return bool(
        user is not None
        and user.is_active
        and user.status
        in {UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION}
    )


def parse_authentication_identity(
    value: str | None,
) -> AuthenticationIdentity | None:
    if not isinstance(value, str) or not value or len(value) > AUTH_IDENTITY_MAX_LENGTH:
        return None

    is_legacy = ":" not in value
    if is_legacy:
        user_id_text = value
        auth_version = 1
    else:
        parts = value.split(":")
        if len(parts) != 3 or parts[0] != AUTH_IDENTITY_PREFIX:
            return None
        user_id_text, version_text = parts[1], parts[2]
        if not version_text.isascii() or not version_text.isdecimal():
            return None
        if version_text.startswith("0"):
            return None
        auth_version = int(version_text)
        if auth_version > 2_147_483_647:
            return None

    try:
        user_id = uuid.UUID(user_id_text)
    except (AttributeError, TypeError, ValueError):
        return None
    if str(user_id) != user_id_text:
        return None
    return AuthenticationIdentity(
        user_id=user_id,
        auth_version=auth_version,
        is_legacy=is_legacy,
    )


def bump_auth_version(user: User) -> int:
    current_version = int(user.auth_version)
    if current_version < 1 or current_version >= 2_147_483_647:
        raise RuntimeError("La versión de autenticación no es válida.")
    user.auth_version = current_version + 1
    return user.auth_version


def register_customer(
    *,
    session: Session,
    email: str,
    full_name: str,
    password: str,
    password_confirmation: str,
    password_min_length: int,
    verification_ttl_minutes: int,
) -> RegisteredUserResult:
    normalized_email = normalize_email(email)
    display_email = email.strip()
    name = normalize_full_name(full_name)
    if not display_email or "@" not in display_email or len(display_email) > 254:
        raise RegistrationError("Ingresa un correo electrónico válido.")
    if len(name) < 2 or len(name) > 120:
        raise RegistrationError("Ingresa tu nombre y apellido.")
    if password != password_confirmation:
        raise RegistrationError("Las contraseñas no coinciden.")
    validate_password(password, min_length=password_min_length)
    existing = session.scalar(
        select(User).where(User.email_normalized == normalized_email)
    )
    if existing is not None:
        raise RegistrationError("Ya existe una cuenta con este correo.")
    user = User(
        public_code=public_user_code(),
        email=display_email,
        email_normalized=normalized_email,
        password_hash=generate_password_hash(password),
        full_name=name,
        status=UserStatus.PENDING_VERIFICATION,
        is_active=True,
    )
    session.add(user)
    session.flush()
    token = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        ttl_minutes=verification_ttl_minutes,
    )
    return RegisteredUserResult(user=user, verification_token=token.token)


def authenticate_customer(
    *,
    session: Session,
    email: str,
    password: str,
) -> User:
    normalized_email = normalize_email(email)
    user = session.scalar(
        select(User).where(User.email_normalized == normalized_email)
    )
    stored_hash = (
        user.password_hash
        if user is not None and user.password_hash
        else DUMMY_PASSWORD_HASH
    )
    valid_password = check_password_hash(stored_hash, password)
    if (
        not is_user_authentication_allowed(user)
        or not user.password_hash
        or not valid_password
    ):
        raise LoginError("Correo o contraseña incorrectos.")
    user.last_login_at = datetime.now(timezone.utc)
    session.flush()
    return user


def verify_customer_email(
    *,
    session: Session,
    token: str,
) -> User:
    try:
        account_token = consume_account_token(
            session=session,
            token=token,
            purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
        )
    except InvalidAccountTokenError as exc:
        raise LoginError(str(exc)) from exc
    user = session.get(User, account_token.user_id, with_for_update=True)
    if not is_user_authentication_allowed(user):
        raise LoginError("El enlace no es válido o ya caducó.")
    now = datetime.now(timezone.utc)
    if user.email_verified_at is None:
        user.email_verified_at = now
    if user.status == UserStatus.PENDING_VERIFICATION:
        user.status = UserStatus.ACTIVE
    session.flush()
    return user


def request_password_reset(
    *,
    session: Session,
    email: str,
    ttl_minutes: int,
) -> tuple[User, str] | None:
    user = session.scalar(
        select(User).where(User.email_normalized == normalize_email(email))
    )
    if not is_user_authentication_allowed(user) or not user.email:
        return None
    token = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.RESET_PASSWORD,
        ttl_minutes=ttl_minutes,
    )
    return user, token.token


def reset_password(
    *,
    session: Session,
    token: str,
    password: str,
    password_confirmation: str,
    password_min_length: int,
) -> User:
    if password != password_confirmation:
        raise PasswordPolicyError("Las contraseñas no coinciden.")
    validate_password(password, min_length=password_min_length)
    try:
        account_token = consume_account_token(
            session=session,
            token=token,
            purpose=UserAccountTokenPurpose.RESET_PASSWORD,
        )
    except InvalidAccountTokenError as exc:
        raise LoginError(str(exc)) from exc
    user = session.get(User, account_token.user_id, with_for_update=True)
    if not is_user_authentication_allowed(user):
        raise LoginError("El enlace no es válido o ya caducó.")
    user.password_hash = generate_password_hash(password)
    bump_auth_version(user)
    session.flush()
    return user
