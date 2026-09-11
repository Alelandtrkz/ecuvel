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
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import UUIDPrimaryKeyMixin
from app.models.enums import (
    LegalAcceptanceAction,
    LegalAcceptanceMode,
    LegalAcceptanceSource,
)


class ImmutableLegalEvidenceError(RuntimeError):
    """Raised when application code tries to alter append-only legal evidence."""


class LegalDocumentVersion(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "legal_document_versions"

    family: Mapped[str] = mapped_column(String(40), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    version_prefix: Mapped[str] = mapped_column(String(80), nullable=False)
    version_identifier: Mapped[str] = mapped_column(
        String(140), nullable=False, unique=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    template_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    acceptance_mode: Mapped[LegalAcceptanceMode] = mapped_column(
        Enum(
            LegalAcceptanceMode,
            name="legal_acceptance_mode",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    acceptances: Mapped[list["UserLegalAcceptance"]] = relationship(
        "UserLegalAcceptance",
        back_populates="legal_document_version",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "family",
            "slug",
            "version_number",
            name="uq_legal_document_version_logical_number",
        ),
        CheckConstraint(
            "version_number > 0",
            name="version_number_positive",
        ),
        CheckConstraint(
            "char_length(btrim(family)) BETWEEN 1 AND 40",
            name="family_nonblank",
        ),
        CheckConstraint(
            "char_length(btrim(slug)) BETWEEN 1 AND 100",
            name="slug_nonblank",
        ),
        CheckConstraint(
            "version_prefix ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="prefix_format",
        ),
        CheckConstraint(
            "version_identifier ~ "
            "'^[a-z0-9]+(-[a-z0-9]+)*-[0-9]{4}-[0-9]{2}-[0-9]{2}-v[1-9][0-9]*$'",
            name="identifier_format",
        ),
        CheckConstraint(
            "version_identifier ~ "
            "('^' || version_prefix || "
            "'-[0-9]{4}-[0-9]{2}-[0-9]{2}-v' || version_number::text || '$')",
            name="identifier_matches_fields",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_format",
        ),
        CheckConstraint(
            "char_length(content_snapshot) > 0",
            name="content_nonblank",
        ),
        CheckConstraint(
            "effective_at >= published_at",
            name="effective_after_publication",
        ),
        CheckConstraint(
            "source_revision IS NULL OR "
            "char_length(btrim(source_revision)) BETWEEN 1 AND 64",
            name="source_revision_nonblank",
        ),
        Index(
            "ix_legal_document_versions_current_lookup",
            "family",
            "slug",
            "effective_at",
            "published_at",
        ),
    )


class UserLegalAcceptance(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "user_legal_acceptances"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    legal_document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_document_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action: Mapped[LegalAcceptanceAction] = mapped_column(
        Enum(
            LegalAcceptanceAction,
            name="legal_acceptance_action",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[LegalAcceptanceSource] = mapped_column(
        Enum(
            LegalAcceptanceSource,
            name="legal_acceptance_source",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version_identifier_snapshot: Mapped[str] = mapped_column(
        String(140), nullable=False
    )
    content_sha256_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship("User")
    legal_document_version: Mapped[LegalDocumentVersion] = relationship(
        "LegalDocumentVersion", back_populates="acceptances"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "legal_document_version_id",
            name="uq_user_legal_acceptance_user_version",
        ),
        CheckConstraint(
            "char_length(version_identifier_snapshot) BETWEEN 1 AND 140",
            name="identifier_nonblank",
        ),
        CheckConstraint(
            "content_sha256_snapshot ~ '^[0-9a-f]{64}$'",
            name="sha256_format",
        ),
        CheckConstraint(
            "ip_address IS NULL OR char_length(ip_address) BETWEEN 2 AND 45",
            name="ip_length",
        ),
        CheckConstraint(
            "user_agent IS NULL OR char_length(user_agent) BETWEEN 1 AND 500",
            name="user_agent_length",
        ),
    )


def _reject_legal_evidence_mutation(_mapper, _connection, target) -> None:
    raise ImmutableLegalEvidenceError(
        f"{type(target).__name__} is append-only legal evidence."
    )


for _model in (LegalDocumentVersion, UserLegalAcceptance):
    event.listen(_model, "before_update", _reject_legal_evidence_mutation)
    event.listen(_model, "before_delete", _reject_legal_evidence_mutation)
