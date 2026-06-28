from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreContractOtpChannel,
    StoreOnboardingDocumentStatus,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    StoreVerificationDecision,
)


class StoreOnboarding(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_onboardings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[StoreOnboardingStatus] = mapped_column(
        Enum(StoreOnboardingStatus, name="store_onboarding_status", native_enum=True, validate_strings=True),
        nullable=False,
        default=StoreOnboardingStatus.DRAFT,
        server_default=StoreOnboardingStatus.DRAFT.value,
        index=True,
    )
    current_stage: Mapped[StoreOnboardingStage] = mapped_column(
        Enum(StoreOnboardingStage, name="store_onboarding_stage", native_enum=True, validate_strings=True),
        nullable=False,
        default=StoreOnboardingStage.VERIFY_DATA,
        server_default=StoreOnboardingStage.VERIFY_DATA.value,
        index=True,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    store_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    legal_id_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    whatsapp_or_nickname: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bank_account_owner: Mapped[str | None] = mapped_column(String(150), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_id_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    bank_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User")
    store: Mapped["Store | None"] = relationship("Store")
    documents: Mapped[list["StoreOnboardingDocument"]] = relationship(
        "StoreOnboardingDocument",
        back_populates="onboarding",
        cascade="all, delete-orphan",
        order_by="StoreOnboardingDocument.created_at",
    )
    reviews: Mapped[list["StoreVerificationReview"]] = relationship(
        "StoreVerificationReview",
        back_populates="onboarding",
        cascade="all, delete-orphan",
        order_by="StoreVerificationReview.created_at",
    )
    contract_acceptance: Mapped["StoreContractAcceptance | None"] = relationship(
        "StoreContractAcceptance",
        back_populates="onboarding",
        uselist=False,
        cascade="all, delete-orphan",
    )
    contract_otps: Mapped[list["StoreContractOtpChallenge"]] = relationship(
        "StoreContractOtpChallenge",
        back_populates="onboarding",
        cascade="all, delete-orphan",
        order_by="StoreContractOtpChallenge.created_at",
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_store_onboardings_user"),
    )


class StoreOnboardingDocument(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_onboarding_documents"

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_onboardings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str] = mapped_column(String(80), nullable=False, default="IDENTITY_OR_BUSINESS")
    status: Mapped[StoreOnboardingDocumentStatus] = mapped_column(
        Enum(StoreOnboardingDocumentStatus, name="store_onboarding_document_status", native_enum=True, validate_strings=True),
        nullable=False,
        default=StoreOnboardingDocumentStatus.PENDING_REVIEW,
        server_default=StoreOnboardingDocumentStatus.PENDING_REVIEW.value,
        index=True,
    )
    admin_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    onboarding: Mapped["StoreOnboarding"] = relationship("StoreOnboarding", back_populates="documents")


class StoreVerificationReview(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_verification_reviews"

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_onboardings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    decision: Mapped[StoreVerificationDecision] = mapped_column(
        Enum(StoreVerificationDecision, name="store_verification_decision", native_enum=True, validate_strings=True),
        nullable=False,
        default=StoreVerificationDecision.PENDING,
        server_default=StoreVerificationDecision.PENDING.value,
        index=True,
    )
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    onboarding: Mapped["StoreOnboarding"] = relationship("StoreOnboarding", back_populates="reviews")
    reviewer: Mapped["User | None"] = relationship("User")


class StoreContractAcceptance(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_contract_acceptances"

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_onboardings.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    contract_version: Mapped[str] = mapped_column(String(50), nullable=False)
    annex_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[StoreContractAcceptanceStatus] = mapped_column(
        Enum(StoreContractAcceptanceStatus, name="store_contract_acceptance_status", native_enum=True, validate_strings=True),
        nullable=False,
        default=StoreContractAcceptanceStatus.PENDING,
        server_default=StoreContractAcceptanceStatus.PENDING.value,
    )
    accepted_terms: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    otp_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    accepted_ip: Mapped[str | None] = mapped_column(String(80), nullable=True)
    accepted_user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True)

    onboarding: Mapped["StoreOnboarding"] = relationship("StoreOnboarding", back_populates="contract_acceptance")


class StoreContractOtpChallenge(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_contract_otp_challenges"

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_onboardings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[StoreContractOtpChannel] = mapped_column(
        Enum(StoreContractOtpChannel, name="store_contract_otp_channel", native_enum=True, validate_strings=True),
        nullable=False,
        index=True,
    )
    destination_masked: Mapped[str] = mapped_column(String(120), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    onboarding: Mapped["StoreOnboarding"] = relationship("StoreOnboarding", back_populates="contract_otps")
