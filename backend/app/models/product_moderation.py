from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ProductDraftModerationEvent(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "product_draft_moderation_events"

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_drafts.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True,
    )

    draft: Mapped["ProductDraft"] = relationship(
        "ProductDraft", back_populates="moderation_events",
    )
    actor: Mapped["User"] = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "decision IN ('APPROVED', 'CHANGES_REQUESTED', 'REJECTED')",
            name="product_draft_moderation_decision_valid",
        ),
        CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 2000",
            name="product_draft_moderation_note_valid",
        ),
    )


class ProductDraftPublication(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "product_draft_publications"

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_drafts.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )
    published_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True,
    )

    draft: Mapped["ProductDraft"] = relationship(
        "ProductDraft", back_populates="publication",
    )
    product: Mapped["Product"] = relationship("Product")
    published_by: Mapped["User"] = relationship("User")
