from __future__ import annotations

import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PhoneOtpChallenge, User
from app.models.enums import PhoneOtpPurpose, UserAccountTokenPurpose, UserStatus
from app.models.user import normalize_email
from app.services.account_tokens import create_account_token
from app.services.authentication import public_user_code, normalize_full_name


LOGGER = logging.getLogger(__name__)


class PhoneOtpError(Exception):
    """Error base de autenticaciÃ³n telefÃ³nica."""


class InvalidPhoneNumberError(PhoneOtpError):
    """El telÃ©fono no cumple el formato permitido."""


class PhoneOtpCooldownError(PhoneOtpError):
    """Se solicitÃ³ un cÃ³digo demasiado pronto."""


class InvalidPhoneOtpError(PhoneOtpError):
    """El cÃ³digo no es vÃ¡lido."""


class PhoneAlreadyLinkedError(PhoneOtpError):
    """El telÃ©fono pertenece a otra cuenta."""


class PhoneRegistrationError(PhoneOtpError):
    """No se pudo crear la cuenta telefÃ³nica."""


@dataclass(frozen=True, slots=True)
class RequestedPhoneOtp:
    challenge: PhoneOtpChallenge
    masked_phone: str


@dataclass(frozen=True, slots=True)
class VerifiedPhoneOtp:
    challenge: PhoneOtpChallenge
    existing_user: User | None


@dataclass(frozen=True, slots=True)
class PhoneRegistrationResult:
    user: User
    verification_token: str | None = None


class PhoneOtpSender:
    def send_code(self, *, phone: str, code: str) -> None:
        raise NotImplementedError


class ConsolePhoneOtpSender(PhoneOtpSender):
    def send_code(self, *, phone: str, code: str) -> None:
        if current_app.config.get("ECUVEL_PRODUCTION"):
            raise PhoneOtpError("El backend console no estÃ¡ permitido en producciÃ³n.")
        LOGGER.info(
            "[DEV] CÃ³digo de autenticaciÃ³n para telÃ©fono terminado en %s: %s",
            mask_phone(phone)[-4:],
            code,
        )


class FakePhoneOtpSender(PhoneOtpSender):
    outbox: list[tuple[str, str]] = []

    def send_code(self, *, phone: str, code: str) -> None:
        self.outbox.append((phone, code))


fake_phone_otp_sender = FakePhoneOtpSender()


def get_phone_otp_sender() -> PhoneOtpSender:
    backend = current_app.config["PHONE_OTP_BACKEND"]
    if backend == "fake":
        return fake_phone_otp_sender
    return ConsolePhoneOtpSender()


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "No configurado"
    digits = "".join(char for char in phone if char.isdigit())
    if len(digits) < 4:
        return "****"
    return f"***{digits[-4:]}"


def normalize_phone_number(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise InvalidPhoneNumberError("Ingresa un nÃºmero telefÃ³nico vÃ¡lido.")
    if re.search(r"[A-Za-z]", raw):
        raise InvalidPhoneNumberError("Ingresa un nÃºmero telefÃ³nico vÃ¡lido.")
    cleaned = re.sub(r"[\s\-\(\)]", "", raw)
    if cleaned.startswith("+"):
        if not re.fullmatch(r"\+5939\d{8}", cleaned):
            raise InvalidPhoneNumberError("Ingresa un nÃºmero telefÃ³nico ecuatoriano vÃ¡lido.")
        return cleaned
    if not cleaned.isdigit():
        raise InvalidPhoneNumberError("Ingresa un nÃºmero telefÃ³nico vÃ¡lido.")
    if re.fullmatch(r"09\d{8}", cleaned):
        return "+593" + cleaned[1:]
    if re.fullmatch(r"5939\d{8}", cleaned):
        return "+" + cleaned
    raise InvalidPhoneNumberError("Ingresa un nÃºmero telefÃ³nico ecuatoriano vÃ¡lido.")


def generate_otp_code(length: int) -> str:
    upper = 10**length
    return f"{secrets.randbelow(upper):0{length}d}"


def hash_otp_code(*, phone_normalized: str, code: str, pepper: str) -> str:
    if not pepper:
        raise PhoneOtpError("PHONE_OTP_PEPPER no estÃ¡ configurado.")
    message = f"{phone_normalized}:{code}".encode("utf-8")
    return hmac.new(pepper.encode("utf-8"), message, "sha256").hexdigest()


def _constant_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def _active_user_by_phone(session: Session, phone_normalized: str) -> User | None:
    return session.scalar(
        select(User).where(User.phone_normalized == phone_normalized)
    )


def _challenge_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def request_phone_otp(
    *,
    session: Session,
    phone: str,
    purpose: PhoneOtpPurpose,
    user_id=None,
) -> RequestedPhoneOtp:
    phone_normalized = normalize_phone_number(phone)
    now = datetime.now(timezone.utc)
    cooldown_seconds = current_app.config["PHONE_OTP_RESEND_COOLDOWN_SECONDS"]
    latest = session.scalar(
        select(PhoneOtpChallenge)
        .where(
            PhoneOtpChallenge.phone_normalized == phone_normalized,
            PhoneOtpChallenge.purpose == purpose,
            PhoneOtpChallenge.consumed_at.is_(None),
        )
        .order_by(PhoneOtpChallenge.created_at.desc())
        .with_for_update()
    )
    if latest and latest.last_sent_at + timedelta(seconds=cooldown_seconds) > now:
        raise PhoneOtpCooldownError("Espera antes de solicitar un nuevo cÃ³digo.")
    active = session.scalars(
        select(PhoneOtpChallenge)
        .where(
            PhoneOtpChallenge.phone_normalized == phone_normalized,
            PhoneOtpChallenge.purpose == purpose,
            PhoneOtpChallenge.consumed_at.is_(None),
        )
        .with_for_update()
    ).all()
    for challenge in active:
        challenge.consumed_at = now
    code = generate_otp_code(current_app.config["PHONE_OTP_CODE_LENGTH"])
    challenge = PhoneOtpChallenge(
        phone_normalized=phone_normalized,
        user_id=user_id,
        purpose=purpose,
        code_hash=hash_otp_code(
            phone_normalized=phone_normalized,
            code=code,
            pepper=current_app.config["PHONE_OTP_PEPPER"],
        ),
        expires_at=now + timedelta(seconds=current_app.config["PHONE_OTP_TTL_SECONDS"]),
        max_attempts=current_app.config["PHONE_OTP_MAX_ATTEMPTS"],
        last_sent_at=now,
    )
    session.add(challenge)
    session.flush()
    get_phone_otp_sender().send_code(phone=phone_normalized, code=code)
    return RequestedPhoneOtp(challenge=challenge, masked_phone=mask_phone(phone_normalized))


def verify_phone_otp(
    *,
    session: Session,
    challenge_id,
    code: str,
    expected_purpose: PhoneOtpPurpose,
) -> VerifiedPhoneOtp:
    challenge = session.get(
        PhoneOtpChallenge,
        _challenge_uuid(challenge_id),
        with_for_update=True,
    )
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.purpose != expected_purpose
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
        or challenge.attempt_count >= challenge.max_attempts
    ):
        raise InvalidPhoneOtpError("El cÃ³digo no es vÃ¡lido o ya caducÃ³.")
    submitted = (code or "").strip()
    expected_length = current_app.config["PHONE_OTP_CODE_LENGTH"]
    if not re.fullmatch(rf"\d{{{expected_length}}}", submitted):
        challenge.attempt_count += 1
        session.flush()
        raise InvalidPhoneOtpError("El cÃ³digo no es vÃ¡lido o ya caducÃ³.")
    expected_hash = hash_otp_code(
        phone_normalized=challenge.phone_normalized,
        code=submitted,
        pepper=current_app.config["PHONE_OTP_PEPPER"],
    )
    if not _constant_compare(challenge.code_hash, expected_hash):
        challenge.attempt_count += 1
        session.flush()
        raise InvalidPhoneOtpError("El cÃ³digo no es vÃ¡lido o ya caducÃ³.")
    challenge.verified_at = now
    existing_user = _active_user_by_phone(session, challenge.phone_normalized)
    if existing_user is not None and (
        not existing_user.is_active
        or existing_user.status in {UserStatus.BLOCKED, UserStatus.SUSPENDED}
    ):
        challenge.consumed_at = now
        session.flush()
        raise InvalidPhoneOtpError("El cÃ³digo no es vÃ¡lido o ya caducÃ³.")
    if existing_user is not None and expected_purpose == PhoneOtpPurpose.LOGIN_OR_REGISTER:
        challenge.consumed_at = now
        existing_user.last_login_at = now
    session.flush()
    return VerifiedPhoneOtp(challenge=challenge, existing_user=existing_user)


def register_phone_user(
    *,
    session: Session,
    challenge_id,
    full_name: str,
    email: str | None,
    verification_ttl_minutes: int,
) -> PhoneRegistrationResult:
    challenge = session.get(
        PhoneOtpChallenge,
        _challenge_uuid(challenge_id),
        with_for_update=True,
    )
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.purpose != PhoneOtpPurpose.LOGIN_OR_REGISTER
        or challenge.verified_at is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
    ):
        raise PhoneRegistrationError("La verificaciÃ³n telefÃ³nica no estÃ¡ disponible.")
    existing = _active_user_by_phone(session, challenge.phone_normalized)
    if existing is not None:
        challenge.consumed_at = now
        existing.last_login_at = now
        session.flush()
        return PhoneRegistrationResult(user=existing)
    name = normalize_full_name(full_name)
    if len(name) < 2 or len(name) > 120:
        raise PhoneRegistrationError("Ingresa tu nombre y apellido.")
    display_email = (email or "").strip() or None
    normalized_email = normalize_email(display_email) if display_email else None
    if display_email and ("@" not in display_email or len(display_email) > 254):
        raise PhoneRegistrationError("Ingresa un correo electrÃ³nico vÃ¡lido.")
    if normalized_email:
        if session.scalar(select(User).where(User.email_normalized == normalized_email)):
            raise PhoneRegistrationError("Ya existe una cuenta con este correo.")
    user = User(
        public_code=public_user_code(),
        email=display_email,
        email_normalized=normalized_email,
        password_hash=None,
        full_name=name,
        phone=challenge.phone_normalized,
        phone_normalized=challenge.phone_normalized,
        phone_verified_at=now,
        status=UserStatus.ACTIVE,
        is_active=True,
        last_login_at=now,
    )
    session.add(user)
    session.flush()
    challenge.user_id = user.id
    challenge.consumed_at = now
    verification_token = None
    if normalized_email:
        verification_token = create_account_token(
            session=session,
            user_id=user.id,
            purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
            ttl_minutes=verification_ttl_minutes,
        ).token
    session.flush()
    return PhoneRegistrationResult(user=user, verification_token=verification_token)


def link_verified_phone(
    *,
    session: Session,
    user_id,
    challenge_id,
) -> User:
    challenge = session.get(
        PhoneOtpChallenge,
        _challenge_uuid(challenge_id),
        with_for_update=True,
    )
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.purpose not in {PhoneOtpPurpose.LINK_PHONE, PhoneOtpPurpose.CHANGE_PHONE}
        or challenge.verified_at is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
    ):
        raise PhoneRegistrationError("La verificaciÃ³n telefÃ³nica no estÃ¡ disponible.")
    owner = _active_user_by_phone(session, challenge.phone_normalized)
    if owner is not None and owner.id != user_id:
        raise PhoneAlreadyLinkedError("No fue posible vincular este nÃºmero.")
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise PhoneRegistrationError("No se encontrÃ³ la cuenta.")
    user.phone = challenge.phone_normalized
    user.phone_normalized = challenge.phone_normalized
    user.phone_verified_at = now
    if user.status == UserStatus.PENDING_VERIFICATION:
        user.status = UserStatus.ACTIVE
    challenge.user_id = user.id
    challenge.consumed_at = now
    session.flush()
    return user
