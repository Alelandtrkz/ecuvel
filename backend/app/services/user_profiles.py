from __future__ import annotations

from datetime import date, datetime, timezone

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
from app.services.authentication import (
    PasswordPolicyError,
    normalize_full_name,
    validate_password,
)


class ProfileError(Exception):
    """Error base de perfil."""


VALID_GENDERS = {"male", "female", "other", "prefer_not_to_say", ""}


def update_profile(
    *,
    session: Session,
    user_id,
    full_name: str,
    phone: str | None,
    birth_date: date | None,
    gender: str | None,
) -> User:
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ProfileError("No se encontró la cuenta.")
    name = normalize_full_name(full_name)
    if len(name) < 2 or len(name) > 120:
        raise ProfileError("Ingresa tu nombre y apellido.")
    if birth_date and birth_date > date.today():
        raise ProfileError("La fecha de nacimiento no puede estar en el futuro.")
    normalized_gender = (gender or "").strip()
    if normalized_gender not in VALID_GENDERS:
        raise ProfileError("Selecciona una opción de género válida.")
    user.full_name = name
    user.birth_date = birth_date
    user.gender = normalized_gender or None
    session.flush()
    return user


def request_email_change(
    *,
    session: Session,
    user_id,
    new_email: str,
    current_password: str,
    ttl_minutes: int,
) -> tuple[User, str]:
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ProfileError("La contraseña actual no es correcta.")
    if user.password_hash and not check_password_hash(
        user.password_hash,
        current_password,
    ):
        raise ProfileError("La contraseña actual no es correcta.")
    normalized = normalize_email(new_email)
    if not new_email.strip() or "@" not in new_email or len(new_email.strip()) > 254:
        raise ProfileError("Ingresa un correo electrónico válido.")
    existing = session.scalar(
        select(User).where(
            User.email_normalized == normalized,
            User.id != user.id,
        )
    )
    if existing is not None:
        raise ProfileError("Ya existe una cuenta con este correo.")
    token = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.CHANGE_EMAIL,
        ttl_minutes=ttl_minutes,
        new_email=new_email.strip(),
    )
    return user, token.token


def confirm_email_change(
    *,
    session: Session,
    token: str,
) -> User:
    try:
        account_token = consume_account_token(
            session=session,
            token=token,
            purpose=UserAccountTokenPurpose.CHANGE_EMAIL,
        )
    except InvalidAccountTokenError as exc:
        raise ProfileError(str(exc)) from exc
    user = session.get(User, account_token.user_id, with_for_update=True)
    if user is None or not account_token.new_email:
        raise ProfileError("El enlace no es válido o ya caducó.")
    user.email = account_token.new_email
    user.email_normalized = normalize_email(account_token.new_email)
    user.email_verified_at = datetime.now(timezone.utc)
    user.status = UserStatus.ACTIVE
    session.flush()
    return user


def change_password(
    *,
    session: Session,
    user_id,
    current_password: str,
    new_password: str,
    new_password_confirmation: str,
    password_min_length: int,
) -> User:
    user = session.get(User, user_id, with_for_update=True)
    if (
        user is None
        or not user.password_hash
        or not check_password_hash(user.password_hash, current_password)
    ):
        raise ProfileError("La contraseña actual no es correcta.")
    if new_password != new_password_confirmation:
        raise PasswordPolicyError("Las contraseñas no coinciden.")
    validate_password(new_password, min_length=password_min_length)
    user.password_hash = generate_password_hash(new_password)
    session.flush()
    return user


def create_password(
    *,
    session: Session,
    user_id,
    new_password: str,
    new_password_confirmation: str,
    password_min_length: int,
) -> User:
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ProfileError("No se encontró la cuenta.")
    if user.password_hash:
        raise ProfileError("Tu cuenta ya tiene una contraseña.")
    if new_password != new_password_confirmation:
        raise PasswordPolicyError("Las contraseñas no coinciden.")
    validate_password(new_password, min_length=password_min_length)
    user.password_hash = generate_password_hash(new_password)
    session.flush()
    return user
