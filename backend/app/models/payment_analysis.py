from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
)


class PaymentProofAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "payment_proof_analyses"

    payment_proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_proofs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    processing_status: Mapped[PaymentProofAnalysisStatus] = mapped_column(
        Enum(
            PaymentProofAnalysisStatus,
            name="payment_proof_analysis_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=PaymentProofAnalysisStatus.PENDING,
        server_default=PaymentProofAnalysisStatus.PENDING.value,
        index=True,
    )
    outcome: Mapped[PaymentProofPrecheckOutcome | None] = mapped_column(
        Enum(
            PaymentProofPrecheckOutcome,
            name="payment_proof_precheck_outcome",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
        index=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    run_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    processing_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    qr_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    qr_decoded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    qr_payload_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    bank_name_detected: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    amount_detected: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    transaction_at_detected: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    destination_account_suffix: Mapped[str | None] = mapped_column(
        String(4), nullable=True
    )
    receipt_number_detected: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    transaction_reference_detected: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    ocr_mean_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    ocr_word_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    amount_matches: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    destination_account_matches: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    date_is_plausible: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    receipt_appears_unique: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    qr_ocr_are_consistent: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    bank_is_recognized: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    findings: Mapped[list[dict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    payment_proof: Mapped["PaymentProof"] = relationship(
        "PaymentProof", back_populates="analysis"
    )

    __table_args__ = (
        CheckConstraint("run_count >= 0", name="payment_proof_analysis_run_count"),
        CheckConstraint(
            "ocr_word_count >= 0", name="payment_proof_analysis_word_count"
        ),
        CheckConstraint(
            "ocr_mean_confidence IS NULL OR "
            "(ocr_mean_confidence >= 0 AND ocr_mean_confidence <= 100)",
            name="payment_proof_analysis_confidence",
        ),
        Index(
            "ix_payment_proof_analyses_bank_receipt",
            "bank_name_detected",
            "receipt_number_detected",
        ),
    )
