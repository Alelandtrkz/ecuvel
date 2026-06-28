from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, UserAccountToken
from app.models.enums import UserAccountTokenPurpose
from app.models.user import normalize_email


class AccountTokenError(Exception):
    """Error base de tokens de cuenta."""


class InvalidAccountTokenError(AccountTokenError):
    """El token no existe, venció o ya fue usado."""


@dataclass(frozen=True, slots=True)
class CreatedAccountToken:
    token: str
    token_id: uuid.UUID
    expires_at: datetime


def hash_account_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_account_token(
    *,
    session: Session,
    user_id: uuid.UUID,
    purpose: UserAccountTokenPurpose,
    ttl_minutes: int,
    new_email: str | None = None,
    invalidate_existing: bool = True,
) -> CreatedAccountToken:
    now = datetime.now(timezone.utc)
    if invalidate_existing:
        existing = session.scalars(
            select(UserAccountToken)
            .where(
                UserAccountToken.user_id == user_id,
                UserAccountToken.purpose == purpose,
                UserAccountToken.used_at.is_(None),
            )
            .with_for_update()
        ).all()
        for token in existing:
            token.used_at = now

    raw_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=ttl_minutes)
    account_token = UserAccountToken(
        user_id=user_id,
        purpose=purpose,
        token_hash=hash_account_token(raw_token),
        expires_at=expires_at,
        new_email=new_email.strip() if new_email else None,
        new_email_normalized=normalize_email(new_email) if new_email else None,
    )
    session.add(account_token)
    session.flush()
    return CreatedAccountToken(
        token=raw_token,
        token_id=account_token.id,
        expires_at=expires_at,
    )


def consume_account_token(
    *,
    session: Session,
    token: str,
    purpose: UserAccountTokenPurpose,
) -> UserAccountToken:
    token_hash = hash_account_token(token.strip())
    account_token = session.scalar(
        select(UserAccountToken)
        .where(
            UserAccountToken.token_hash == token_hash,
            UserAccountToken.purpose == purpose,
        )
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        account_token is None
        or account_token.used_at is not None
        or account_token.expires_at <= now
    ):
        raise InvalidAccountTokenError(
            "El enlace no es válido o ya caducó."
        )
    user = session.get(User, account_token.user_id, with_for_update=True)
    if user is None:
        raise InvalidAccountTokenError(
            "El enlace no es válido o ya caducó."
        )
    account_token.used_at = now
    session.flush()
    return account_token
