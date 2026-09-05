from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import User
from app.models.enums import UserAccountTokenPurpose, UserStatus
from app.models.user import normalize_email
from app.services.age_eligibility import is_at_least_18
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
from app.services.delivery_eta import ecuador_local_date


class ProfileError(Exception):
    """Error base de perfil."""


VALID_GENDERS = {"male", "female", "other", "prefer_not_to_say", ""}
_EMAIL_UNIQUE_CONSTRAINTS = {
    "ix_users_email",
    "ix_users_email_normalized",
}
_PASSWORDLESS_EMAIL_CHANGE_MESSAGE = (
    "Por seguridad, el cambio de correo para cuentas sin contraseña "
    "requiere una nueva verificación telefónica. Esta función estará "
    "disponible próximamente."
)


def _validate_birth_date_transition(
    *,
    existing: date | None,
    incoming: date | None,
    today: date,
) -> None:
    if incoming == existing:
        return
    if incoming is None:
        raise ProfileError(
            "La fecha de nacimiento no puede eliminarse una vez registrada."
        )
    if existing is not None and is_at_least_18(existing, today=today):
        raise ProfileError(
            "La fecha de nacimiento no puede modificarse una vez registrada."
        )
    if incoming > today:
        raise ProfileError("La fecha de nacimiento no puede estar en el futuro.")
    if not is_at_least_18(incoming, today=today):
        raise ProfileError(
            "Debes tener al menos 18 años para registrar tu fecha de nacimiento."
        )


def update_profile(
    *,
    session: Session,
    user_id,
    full_name: str,
    phone: str | None,
    birth_date: date | None,
    gender: str | None,
    today: date | None = None,
) -> User:
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise ProfileError("No se encontró la cuenta.")
    name = normalize_full_name(full_name)
    if len(name) < 2 or len(name) > 120:
        raise ProfileError("Ingresa tu nombre y apellido.")
    normalized_gender = (gender or "").strip()
    if normalized_gender not in VALID_GENDERS:
        raise ProfileError("Selecciona una opción de género válida.")
    _validate_birth_date_transition(
        existing=user.birth_date,
        incoming=birth_date,
        today=today if today is not None else ecuador_local_date(),
    )
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
    if user.password_hash is None:
        raise ProfileError(_PASSWORDLESS_EMAIL_CHANGE_MESSAGE)
    if not check_password_hash(
        user.password_hash,
        current_password,
    ):
        raise ProfileError("La contraseña actual no es correcta.")
    display_email = new_email.strip()
    if not display_email or "@" not in display_email or len(display_email) > 254:
        raise ProfileError("Ingresa un correo electrónico válido.")
    normalized = normalize_email(display_email)
    if normalized == user.email_normalized:
        raise ProfileError(
            "El correo ingresado es igual a tu correo actual. "
            "Ingresa un correo diferente."
        )
    existing = session.scalar(
        select(User).where(
            User.email_normalized == normalized,
            User.id != user.id,
        )
    )
    if existing is not None:
        raise ProfileError(
            "Ya existe una cuenta con este correo. Prueba con otro."
        )
    token = create_account_token(
        session=session,
        user_id=user.id,
        purpose=UserAccountTokenPurpose.CHANGE_EMAIL,
        ttl_minutes=ttl_minutes,
        new_email=display_email,
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
    if (
        user is None
        or not account_token.new_email
        or not account_token.new_email_normalized
    ):
        raise ProfileError("El enlace no es válido o ya caducó.")
    if user.password_hash is None:
        raise ProfileError(_PASSWORDLESS_EMAIL_CHANGE_MESSAGE)
    conflicting_user_id = session.scalar(
        select(User.id).where(
            User.email_normalized == account_token.new_email_normalized,
            User.id != user.id,
        )
    )
    if conflicting_user_id is not None:
        raise ProfileError(
            "Ya existe una cuenta con este correo. Prueba con otro."
        )
    user.email = account_token.new_email
    user.email_normalized = account_token.new_email_normalized
    user.email_verified_at = datetime.now(timezone.utc)
    if user.status == UserStatus.PENDING_VERIFICATION:
        user.status = UserStatus.ACTIVE
    try:
        session.flush()
    except IntegrityError as exc:
        constraint_name = getattr(
            getattr(exc.orig, "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name in _EMAIL_UNIQUE_CONSTRAINTS:
            raise ProfileError(
                "Ya existe una cuenta con este correo. Prueba con otro."
            ) from exc
        raise
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
