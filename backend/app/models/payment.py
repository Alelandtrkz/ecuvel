from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PaymentMethod, PaymentProofStatus, PaymentStatus


class PaymentAttempt(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "payment_attempts"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(
            PaymentMethod,
            name="payment_method",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(150), nullable=False, unique=True, index=True
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped["Order"] = relationship("Order")
    proof: Mapped["PaymentProof | None"] = relationship(
        "PaymentProof",
        back_populates="payment_attempt",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "amount > 0", name="payment_attempt_amount_positive"
        ),
    )


class PaymentProof(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "payment_proofs"

    payment_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(
        String(180), nullable=False, unique=True, index=True
    )
    original_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    status: Mapped[PaymentProofStatus] = mapped_column(
        Enum(
            PaymentProofStatus,
            name="payment_proof_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=PaymentProofStatus.PENDING_REVIEW,
        server_default=PaymentProofStatus.PENDING_REVIEW.value,
        index=True,
    )
    upload_idempotency_key: Mapped[str] = mapped_column(
        String(150), nullable=False, unique=True, index=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    review_notes: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )

    payment_attempt: Mapped["PaymentAttempt"] = relationship(
        "PaymentAttempt", back_populates="proof"
    )
    uploaded_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[uploaded_by_user_id]
    )
    reviewed_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[reviewed_by_user_id]
    )
    analysis: Mapped["PaymentProofAnalysis | None"] = relationship(
        "PaymentProofAnalysis",
        back_populates="payment_proof",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "size_bytes > 0", name="payment_proof_size_positive"
        ),
    )
