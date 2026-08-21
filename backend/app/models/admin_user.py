from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    MarketingConsentChannel,
    MarketingConsentStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
)


class UserMarketingConsent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "user_marketing_consents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    channel: Mapped[MarketingConsentChannel] = mapped_column(
        Enum(MarketingConsentChannel, name="marketing_consent_channel",
             native_enum=True, validate_strings=True),
        nullable=False, index=True,
    )
    status: Mapped[MarketingConsentStatus] = mapped_column(
        Enum(MarketingConsentStatus, name="marketing_consent_status",
             native_enum=True, validate_strings=True),
        nullable=False, default=MarketingConsentStatus.UNKNOWN,
        server_default=MarketingConsentStatus.UNKNOWN.value, index=True,
    )
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80), nullable=False, server_default="UNKNOWN")
    policy_version: Mapped[str | None] = mapped_column(String(40))

    user: Mapped["User"] = relationship("User", back_populates="marketing_consents")

    __table_args__ = (
        UniqueConstraint("user_id", "channel", name="uq_user_marketing_consent_channel"),
        CheckConstraint("char_length(btrim(source)) BETWEEN 1 AND 80",
                        name="ck_user_marketing_consent_source"),
    )


class StaffProfile(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "staff_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )
    employee_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True,
        server_default=text("nextval('staff_employee_number_seq'::regclass)"),
    )
    identification_type: Mapped[StaffIdentificationType] = mapped_column(
        Enum(StaffIdentificationType, name="staff_identification_type",
             native_enum=True, validate_strings=True), nullable=False,
    )
    identification_number_normalized: Mapped[str] = mapped_column(
        String(40), nullable=False,
    )
    nationality_code: Mapped[str] = mapped_column(String(3), nullable=False, server_default="ECU")
    role: Mapped[StaffRole] = mapped_column(
        Enum(StaffRole, name="staff_role", native_enum=True, validate_strings=True),
        nullable=False, index=True,
    )
    employment_status: Mapped[StaffEmploymentStatus] = mapped_column(
        Enum(StaffEmploymentStatus, name="staff_employment_status",
             native_enum=True, validate_strings=True),
        nullable=False, default=StaffEmploymentStatus.PENDING,
        server_default=StaffEmploymentStatus.PENDING.value, index=True,
    )
    employment_started_at: Mapped[date | None] = mapped_column(Date)
    employment_ended_at: Mapped[date | None] = mapped_column(Date)
    last_employment_reason: Mapped[str | None] = mapped_column(String(500))

    user: Mapped["User"] = relationship("User", back_populates="staff_profile")
    assignments: Mapped[list["StaffPointAssignment"]] = relationship(
        "StaffPointAssignment", back_populates="staff_profile",
        cascade="all, delete-orphan", order_by="StaffPointAssignment.starts_at",
    )
    invitations: Mapped[list["StaffAccessInvitation"]] = relationship(
        "StaffAccessInvitation", back_populates="staff_profile",
        cascade="all, delete-orphan", order_by="StaffAccessInvitation.created_at",
    )

    @property
    def employee_code(self) -> str:
        return f"EMP-{self.employee_number:06d}" if self.employee_number else "EMP-PENDIENTE"

    __table_args__ = (
        UniqueConstraint(
            "identification_type", "identification_number_normalized",
            name="uq_staff_identification_type_number",
        ),
        CheckConstraint("employee_number > 0", name="ck_staff_employee_number_positive"),
        CheckConstraint(
            "char_length(btrim(identification_number_normalized)) BETWEEN 3 AND 40",
            name="ck_staff_identification_number_length",
        ),
        CheckConstraint(
            "nationality_code ~ '^[A-Z]{3}$'", name="ck_staff_nationality_code_format"
        ),
        CheckConstraint(
            "employment_ended_at IS NULL OR employment_started_at IS NULL "
            "OR employment_ended_at >= employment_started_at",
            name="ck_staff_employment_dates",
        ),
    )


class StaffPointAssignment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "staff_point_assignments"

    staff_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_profiles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )

    staff_profile: Mapped[StaffProfile] = relationship("StaffProfile", back_populates="assignments")
    warehouse: Mapped["Warehouse"] = relationship("Warehouse")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_staff_assignment_dates"),
        Index(
            "uq_staff_primary_active_assignment", "staff_profile_id", unique=True,
            postgresql_where=text("ends_at IS NULL AND is_primary IS TRUE"),
        ),
    )


class StaffAccessInvitation(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "staff_access_invitations"

    staff_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff_profiles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )

    staff_profile: Mapped[StaffProfile] = relationship("StaffProfile", back_populates="invitations")
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_user_id])


class AdminAuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "admin_audit_events"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True,
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)

    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_user_id])
    target_user: Mapped["User | None"] = relationship("User", foreign_keys=[target_user_id])

    __table_args__ = (
        CheckConstraint("char_length(btrim(action)) BETWEEN 1 AND 80",
                        name="ck_admin_audit_action"),
    )
