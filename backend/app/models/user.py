from __future__ import annotations

from datetime import date, datetime
import uuid

from flask_login import UserMixin
from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PhoneOtpPurpose, UserAccountTokenPurpose, UserStatus


def normalize_email(value: str) -> str:
    return value.strip().casefold()


class User(
    UserMixin,
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "users"

    public_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        unique=True,
        index=True,
    )

    email_normalized: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        unique=True,
        index=True,
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    phone_normalized: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        unique=True,
        index=True,
    )

    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    birth_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    gender: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="user_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=UserStatus.PENDING_VERIFICATION,
        server_default=UserStatus.PENDING_VERIFICATION.value,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    store_memberships: Mapped[list["StoreMember"]] = relationship(
        "StoreMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    account_tokens: Mapped[list["UserAccountToken"]] = relationship(
        "UserAccountToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    phone_otp_challenges: Mapped[list["PhoneOtpChallenge"]] = relationship(
        "PhoneOtpChallenge",
        back_populates="user",
    )

    favorites: Mapped[list["Favorite"]] = relationship(
        "Favorite",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    product_reviews: Mapped[list["ProductReview"]] = relationship(
        "ProductReview",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ProductReview.user_id",
    )

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


class UserAccountToken(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "user_account_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[UserAccountTokenPurpose] = mapped_column(
        Enum(
            UserAccountTokenPurpose,
            name="user_account_token_purpose",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    new_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    new_email_normalized: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="account_tokens",
    )


class PhoneOtpChallenge(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "phone_otp_challenges"

    phone_normalized: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[PhoneOtpPurpose] = mapped_column(
        Enum(
            PhoneOtpPurpose,
            name="phone_otp_purpose",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="phone_otp_challenges",
    )


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def _set_normalized_email(_mapper, _connection, target: User) -> None:
    if target.email:
        target.email = target.email.strip()
        target.email_normalized = normalize_email(target.email)
    else:
        target.email = None
        target.email_normalized = None
