from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import conv

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import BankAccountType, BankAccountVersionStatus


class StoreBankAccountVersion(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Version inmutable de los datos bancarios de una tienda."""

    __tablename__ = "store_bank_account_versions"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    holder_name: Mapped[str] = mapped_column(String(150), nullable=False)
    holder_identification: Mapped[str] = mapped_column(String(40), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_type: Mapped[BankAccountType] = mapped_column(
        Enum(
            BankAccountType,
            name="bank_account_type",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=BankAccountType.UNKNOWN,
        server_default=BankAccountType.UNKNOWN.value,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )
    encrypted_account_number: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    encryption_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    account_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    account_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(40), nullable=False)
    fingerprint_key_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[BankAccountVersionStatus] = mapped_column(
        Enum(
            BankAccountVersionStatus,
            name="bank_account_version_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=BankAccountVersionStatus.PENDING_REVIEW,
        server_default=BankAccountVersionStatus.PENDING_REVIEW.value,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    usable_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_onboarding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_onboardings.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    store: Mapped["Store"] = relationship(
        "Store", back_populates="bank_account_versions"
    )
    reviewed_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[reviewed_by_user_id]
    )
    source_onboarding: Mapped["StoreOnboarding | None"] = relationship(
        "StoreOnboarding", foreign_keys=[source_onboarding_id]
    )
    payouts: Mapped[list["SellerPayout"]] = relationship(
        "SellerPayout",
        back_populates="bank_account_version",
        foreign_keys="SellerPayout.bank_account_version_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "store_id", "version", name="uq_store_bank_account_versions_store_version"
        ),
        UniqueConstraint(
            "id", "store_id", name="uq_store_bank_account_versions_id_store"
        ),
        Index(
            "ix_store_bank_versions_store_status_usable",
            "store_id",
            "status",
            "usable_from",
        ),
        Index(
            "uq_store_bank_versions_pending",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'PENDING_REVIEW'"),
        ),
        Index(
            "uq_store_bank_versions_approved",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'APPROVED'"),
        ),
        CheckConstraint("version > 0", name="store_bank_version_positive"),
        CheckConstraint("currency = 'USD'", name="store_bank_version_currency_usd"),
        CheckConstraint(
            "char_length(account_last4) = 4 AND account_last4 ~ '^[0-9]{4}$'",
            name="store_bank_version_last4_valid",
        ),
        CheckConstraint(
            "octet_length(encryption_nonce) = 12",
            name="store_bank_version_nonce_valid",
        ),
        CheckConstraint(
            "octet_length(encrypted_account_number) >= 17",
            name="bank_ciphertext_valid",
        ),
        CheckConstraint(
            "octet_length(account_fingerprint) = 32",
            name="bank_fingerprint_valid",
        ),
        CheckConstraint(
            "(status = 'PENDING_REVIEW' AND reviewed_at IS NULL "
            "AND usable_from IS NULL AND superseded_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_at IS NOT NULL "
            "AND usable_from IS NOT NULL AND superseded_at IS NULL) OR "
            "(status = 'SUPERSEDED' AND superseded_at IS NOT NULL "
            "AND ((reviewed_at IS NULL AND usable_from IS NULL) OR "
            "(reviewed_at IS NOT NULL AND usable_from IS NOT NULL)))",
            name="store_bank_version_state_valid",
        ),
        CheckConstraint(
            "reviewed_at IS NULL OR "
            "usable_from >= reviewed_at + INTERVAL '48 hours'",
            name=conv(
                "ck_store_bank_account_versions_store_bank_version_usabi_8fb6"
            ),
        ),
    )
