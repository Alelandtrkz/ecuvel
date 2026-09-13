from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import UUIDPrimaryKeyMixin
from app.models.enums import (
    PrivacyPreferenceDecision,
    PrivacyPreferencePurpose,
    PrivacyPreferenceSource,
)


class UserPrivacyPreferenceEvent(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "user_privacy_preference_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[PrivacyPreferencePurpose] = mapped_column(
        Enum(
            PrivacyPreferencePurpose,
            name="privacy_preference_purpose",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    decision: Mapped[PrivacyPreferenceDecision] = mapped_column(
        Enum(
            PrivacyPreferenceDecision,
            name="privacy_preference_decision",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[PrivacyPreferenceSource] = mapped_column(
        Enum(
            PrivacyPreferenceSource,
            name="privacy_preference_source",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    notice_version_identifier: Mapped[str | None] = mapped_column(
        String(140), nullable=True
    )
    notice_content_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "(notice_version_identifier IS NULL) = "
            "(notice_content_sha256 IS NULL)",
            name="notice_snapshots_paired",
        ),
        CheckConstraint(
            "decision <> 'GRANTED' OR notice_version_identifier IS NOT NULL",
            name="granted_notice_required",
        ),
        CheckConstraint(
            "notice_version_identifier IS NULL OR "
            "char_length(notice_version_identifier) BETWEEN 1 AND 140",
            name="notice_identifier_nonblank",
        ),
        CheckConstraint(
            "notice_content_sha256 IS NULL OR "
            "notice_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="notice_sha256_format",
        ),
        Index(
            "ix_user_privacy_preference_events_current",
            "user_id",
            "purpose",
            "occurred_at",
            "created_at",
        ),
    )
