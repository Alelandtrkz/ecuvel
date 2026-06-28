"""add partner onboarding

Revision ID: d9f1a2b3c4e5
Revises: c7e2b9f4a1d0
Create Date: 2026-06-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d9f1a2b3c4e5"
down_revision: Union[str, None] = "c7e2b9f4a1d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


store_onboarding_status = postgresql.ENUM(
    "DRAFT", "SUBMITTED", "CORRECTIONS_REQUESTED", "APPROVED",
    "REJECTED", "CONTRACT_PENDING", "COMPLETED",
    name="store_onboarding_status", create_type=False,
)
store_onboarding_stage = postgresql.ENUM(
    "VERIFY_DATA", "WAITING_VERIFICATION", "CONTRACT_ACCEPTANCE", "PRODUCTS",
    name="store_onboarding_stage", create_type=False,
)
store_onboarding_document_status = postgresql.ENUM(
    "PENDING_REVIEW", "APPROVED", "REJECTED",
    name="store_onboarding_document_status", create_type=False,
)
store_verification_decision = postgresql.ENUM(
    "PENDING", "CORRECTIONS_REQUESTED", "APPROVED", "REJECTED",
    name="store_verification_decision", create_type=False,
)
store_contract_acceptance_status = postgresql.ENUM(
    "PENDING", "ACCEPTED",
    name="store_contract_acceptance_status", create_type=False,
)
store_contract_otp_channel = postgresql.ENUM(
    "PHONE", "EMAIL",
    name="store_contract_otp_channel", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        store_onboarding_status,
        store_onboarding_stage,
        store_onboarding_document_status,
        store_verification_decision,
        store_contract_acceptance_status,
        store_contract_otp_channel,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "store_onboardings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", store_onboarding_status, server_default="DRAFT", nullable=False),
        sa.Column("current_stage", store_onboarding_stage, server_default="VERIFY_DATA", nullable=False),
        sa.Column("current_step", sa.Integer(), server_default="1", nullable=False),
        sa.Column("store_name", sa.String(length=150), nullable=True),
        sa.Column("legal_id_number", sa.String(length=40), nullable=True),
        sa.Column("province", sa.String(length=100), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("whatsapp_or_nickname", sa.String(length=80), nullable=True),
        sa.Column("bank_account_owner", sa.String(length=150), nullable=True),
        sa.Column("bank_account_number", sa.String(length=50), nullable=True),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("bank_id_number", sa.String(length=40), nullable=True),
        sa.Column("bank_email", sa.String(length=320), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name=op.f("fk_store_onboardings_store_id_stores"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_store_onboardings_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_onboardings")),
        sa.UniqueConstraint("user_id", name="uq_store_onboardings_user"),
    )
    op.create_index(op.f("ix_store_onboardings_user_id"), "store_onboardings", ["user_id"])
    op.create_index(op.f("ix_store_onboardings_store_id"), "store_onboardings", ["store_id"])
    op.create_index(op.f("ix_store_onboardings_status"), "store_onboardings", ["status"])
    op.create_index(op.f("ix_store_onboardings_current_stage"), "store_onboardings", ["current_stage"])
    op.create_index(op.f("ix_store_onboardings_submitted_at"), "store_onboardings", ["submitted_at"])

    op.create_table(
        "store_onboarding_documents",
        sa.Column("onboarding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("status", store_onboarding_document_status, server_default="PENDING_REVIEW", nullable=False),
        sa.Column("admin_comment", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="store_onboarding_document_size_positive"),
        sa.ForeignKeyConstraint(["onboarding_id"], ["store_onboardings.id"], name=op.f("fk_store_onboarding_documents_onboarding_id_store_onboardings"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_onboarding_documents")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_store_onboarding_documents_storage_key")),
    )
    op.create_index(op.f("ix_store_onboarding_documents_onboarding_id"), "store_onboarding_documents", ["onboarding_id"])
    op.create_index(op.f("ix_store_onboarding_documents_status"), "store_onboarding_documents", ["status"])

    op.create_table(
        "store_verification_reviews",
        sa.Column("onboarding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", store_verification_decision, server_default="PENDING", nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["onboarding_id"], ["store_onboardings.id"], name=op.f("fk_store_verification_reviews_onboarding_id_store_onboardings"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], name=op.f("fk_store_verification_reviews_reviewer_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_verification_reviews")),
    )
    op.create_index(op.f("ix_store_verification_reviews_onboarding_id"), "store_verification_reviews", ["onboarding_id"])
    op.create_index(op.f("ix_store_verification_reviews_reviewer_user_id"), "store_verification_reviews", ["reviewer_user_id"])
    op.create_index(op.f("ix_store_verification_reviews_decision"), "store_verification_reviews", ["decision"])

    op.create_table(
        "store_contract_acceptances",
        sa.Column("onboarding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("annex_version", sa.String(length=50), nullable=False),
        sa.Column("status", store_contract_acceptance_status, server_default="PENDING", nullable=False),
        sa.Column("accepted_terms", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("otp_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_ip", sa.String(length=80), nullable=True),
        sa.Column("accepted_user_agent", sa.String(length=500), nullable=True),
        sa.Column("pdf_storage_key", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["onboarding_id"], ["store_onboardings.id"], name=op.f("fk_store_contract_acceptances_onboarding_id_store_onboardings"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_contract_acceptances")),
        sa.UniqueConstraint("onboarding_id", name=op.f("uq_store_contract_acceptances_onboarding_id")),
        sa.UniqueConstraint("pdf_storage_key", name=op.f("uq_store_contract_acceptances_pdf_storage_key")),
    )
    op.create_index(op.f("ix_store_contract_acceptances_onboarding_id"), "store_contract_acceptances", ["onboarding_id"])
    op.create_index(op.f("ix_store_contract_acceptances_accepted_at"), "store_contract_acceptances", ["accepted_at"])

    op.create_table(
        "store_contract_otp_challenges",
        sa.Column("onboarding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", store_contract_otp_channel, nullable=False),
        sa.Column("destination_masked", sa.String(length=120), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["onboarding_id"], ["store_onboardings.id"], name=op.f("fk_store_contract_otp_challenges_onboarding_id_store_onboardings"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_contract_otp_challenges")),
    )
    op.create_index(op.f("ix_store_contract_otp_challenges_onboarding_id"), "store_contract_otp_challenges", ["onboarding_id"])
    op.create_index(op.f("ix_store_contract_otp_challenges_channel"), "store_contract_otp_challenges", ["channel"])
    op.create_index(op.f("ix_store_contract_otp_challenges_expires_at"), "store_contract_otp_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_store_contract_otp_challenges_expires_at"), table_name="store_contract_otp_challenges")
    op.drop_index(op.f("ix_store_contract_otp_challenges_channel"), table_name="store_contract_otp_challenges")
    op.drop_index(op.f("ix_store_contract_otp_challenges_onboarding_id"), table_name="store_contract_otp_challenges")
    op.drop_table("store_contract_otp_challenges")
    op.drop_index(op.f("ix_store_contract_acceptances_accepted_at"), table_name="store_contract_acceptances")
    op.drop_index(op.f("ix_store_contract_acceptances_onboarding_id"), table_name="store_contract_acceptances")
    op.drop_table("store_contract_acceptances")
    op.drop_index(op.f("ix_store_verification_reviews_decision"), table_name="store_verification_reviews")
    op.drop_index(op.f("ix_store_verification_reviews_reviewer_user_id"), table_name="store_verification_reviews")
    op.drop_index(op.f("ix_store_verification_reviews_onboarding_id"), table_name="store_verification_reviews")
    op.drop_table("store_verification_reviews")
    op.drop_index(op.f("ix_store_onboarding_documents_status"), table_name="store_onboarding_documents")
    op.drop_index(op.f("ix_store_onboarding_documents_onboarding_id"), table_name="store_onboarding_documents")
    op.drop_table("store_onboarding_documents")
    op.drop_index(op.f("ix_store_onboardings_submitted_at"), table_name="store_onboardings")
    op.drop_index(op.f("ix_store_onboardings_current_stage"), table_name="store_onboardings")
    op.drop_index(op.f("ix_store_onboardings_status"), table_name="store_onboardings")
    op.drop_index(op.f("ix_store_onboardings_store_id"), table_name="store_onboardings")
    op.drop_index(op.f("ix_store_onboardings_user_id"), table_name="store_onboardings")
    op.drop_table("store_onboardings")
    bind = op.get_bind()
    for enum in (
        store_contract_otp_channel,
        store_contract_acceptance_status,
        store_verification_decision,
        store_onboarding_document_status,
        store_onboarding_stage,
        store_onboarding_status,
    ):
        enum.drop(bind, checkfirst=True)
