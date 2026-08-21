"""add admin users, staff identity and access domains

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-08-20 03:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS staff_employee_number_seq START WITH 1")

    marketing_channel = postgresql.ENUM(
        "EMAIL", "SMS_WHATSAPP", name="marketing_consent_channel", create_type=False
    )
    marketing_status = postgresql.ENUM(
        "GRANTED", "REVOKED", "UNKNOWN", name="marketing_consent_status", create_type=False
    )
    identification_type = postgresql.ENUM(
        "ECUADOR_CEDULA", "PASSPORT", "OTHER", name="staff_identification_type", create_type=False
    )
    staff_role = postgresql.ENUM(
        "SUPER_ADMIN", "OPERATIONS_SUPERVISOR", "POINT_OPERATOR", "DELIVERY",
        "TRANSPORT_OPERATOR", "SUPPORT", name="staff_role", create_type=False
    )
    employment_status = postgresql.ENUM(
        "PENDING", "ACTIVE", "SUSPENDED", "INACTIVE",
        name="staff_employment_status", create_type=False
    )
    op.execute("CREATE TYPE marketing_consent_channel AS ENUM ('EMAIL','SMS_WHATSAPP')")
    op.execute("CREATE TYPE marketing_consent_status AS ENUM ('GRANTED','REVOKED','UNKNOWN')")
    op.execute("CREATE TYPE staff_identification_type AS ENUM ('ECUADOR_CEDULA','PASSPORT','OTHER')")
    op.execute("CREATE TYPE staff_role AS ENUM ('SUPER_ADMIN','OPERATIONS_SUPERVISOR','POINT_OPERATOR','DELIVERY','TRANSPORT_OPERATOR','SUPPORT')")
    op.execute("CREATE TYPE staff_employment_status AS ENUM ('PENDING','ACTIVE','SUSPENDED','INACTIVE')")

    def timestamps():
        return (
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    op.create_table(
        "user_marketing_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", marketing_channel, nullable=False),
        sa.Column("status", marketing_status, server_default="UNKNOWN", nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(80), server_default="UNKNOWN", nullable=False),
        sa.Column("policy_version", sa.String(40)),
        *timestamps(),
        sa.CheckConstraint("char_length(btrim(source)) BETWEEN 1 AND 80", name="ck_user_marketing_consent_source"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", name="uq_user_marketing_consent_channel"),
    )
    op.create_index("ix_user_marketing_consents_user_id", "user_marketing_consents", ["user_id"])
    op.create_index("ix_user_marketing_consents_channel", "user_marketing_consents", ["channel"])
    op.create_index("ix_user_marketing_consents_status", "user_marketing_consents", ["status"])

    op.create_table(
        "staff_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_number", sa.BigInteger(), server_default=sa.text("nextval('staff_employee_number_seq'::regclass)"), nullable=False),
        sa.Column("identification_type", identification_type, nullable=False),
        sa.Column("identification_number_normalized", sa.String(40), nullable=False),
        sa.Column("nationality_code", sa.String(3), server_default="ECU", nullable=False),
        sa.Column("role", staff_role, nullable=False),
        sa.Column("employment_status", employment_status, server_default="PENDING", nullable=False),
        sa.Column("employment_started_at", sa.Date()),
        sa.Column("employment_ended_at", sa.Date()),
        sa.Column("last_employment_reason", sa.String(500)),
        *timestamps(),
        sa.CheckConstraint("employee_number > 0", name="ck_staff_employee_number_positive"),
        sa.CheckConstraint("char_length(btrim(identification_number_normalized)) BETWEEN 3 AND 40", name="ck_staff_identification_number_length"),
        sa.CheckConstraint("nationality_code ~ '^[A-Z]{3}$'", name="ck_staff_nationality_code_format"),
        sa.CheckConstraint("employment_ended_at IS NULL OR employment_started_at IS NULL OR employment_ended_at >= employment_started_at", name="ck_staff_employment_dates"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("employee_number"),
        sa.UniqueConstraint("identification_type", "identification_number_normalized", name="uq_staff_identification_type_number"),
    )
    for column in ("user_id", "employee_number", "role", "employment_status"):
        op.create_index(f"ix_staff_profiles_{column}", "staff_profiles", [column])
    op.execute("ALTER SEQUENCE staff_employee_number_seq OWNED BY staff_profiles.employee_number")

    op.create_table(
        "staff_point_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_staff_assignment_dates"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["staff_profile_id"], ["staff_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("staff_profile_id", "warehouse_id", "starts_at", "ends_at"):
        op.create_index(f"ix_staff_point_assignments_{column}", "staff_point_assignments", [column])
    op.create_index(
        "uq_staff_primary_active_assignment", "staff_point_assignments", ["staff_profile_id"],
        unique=True, postgresql_where=sa.text("ends_at IS NULL AND is_primary IS TRUE"),
    )

    op.create_table(
        "staff_access_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["staff_profile_id"], ["staff_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    for column in ("staff_profile_id", "token_hash", "expires_at", "accepted_at", "revoked_at"):
        op.create_index(f"ix_staff_access_invitations_{column}", "staff_access_invitations", [column])

    op.create_table(
        "admin_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text())),
        *timestamps(),
        sa.CheckConstraint("char_length(btrim(action)) BETWEEN 1 AND 80", name="ck_admin_audit_action"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_user_id", "target_user_id", "action"):
        op.create_index(f"ix_admin_audit_events_{column}", "admin_audit_events", [column])
    op.create_index("ix_admin_audit_events_created_at", "admin_audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("admin_audit_events")
    op.drop_table("staff_access_invitations")
    op.drop_index("uq_staff_primary_active_assignment", table_name="staff_point_assignments")
    op.drop_table("staff_point_assignments")
    op.drop_table("staff_profiles")
    op.drop_table("user_marketing_consents")
    op.execute("DROP TYPE staff_employment_status")
    op.execute("DROP TYPE staff_role")
    op.execute("DROP TYPE staff_identification_type")
    op.execute("DROP TYPE marketing_consent_status")
    op.execute("DROP TYPE marketing_consent_channel")
    op.execute("DROP SEQUENCE IF EXISTS staff_employee_number_seq")
