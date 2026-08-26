"""add deterministic review moderation v1

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-08-21 10:00:00.000000
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "product_review_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_number > 0", name="ck_product_review_revision_number_positive"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_product_review_revision_rating"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 2000", name="ck_product_review_revision_body"),
        sa.ForeignKeyConstraint(["review_id"], ["product_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "revision_number", name="uq_product_review_revision_number"),
    )
    op.create_index("ix_product_review_revisions_review_id", "product_review_revisions", ["review_id"])
    op.create_index("ix_product_review_revisions_content_hash", "product_review_revisions", ["content_hash"])

    op.add_column("product_reviews", sa.Column("current_revision_id", postgresql.UUID(as_uuid=True)))
    op.add_column("product_reviews", sa.Column("moderation_source", sa.String(30)))
    op.add_column("product_reviews", sa.Column("rejection_reason_code", sa.String(50)))
    op.create_foreign_key(
        "fk_product_reviews_current_revision", "product_reviews", "product_review_revisions",
        ["current_revision_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_product_reviews_current_revision_id", "product_reviews", ["current_revision_id"])
    op.add_column("product_review_images", sa.Column("revision_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_product_review_images_revision", "product_review_images", "product_review_revisions",
        ["revision_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_product_review_images_revision_id", "product_review_images", ["revision_id"])
    op.drop_constraint(
        "uq_product_review_images_review_sort", "product_review_images", type_="unique"
    )

    op.create_table(
        "review_moderation_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("outcome", sa.String(30)),
        sa.Column("risk", sa.String(10), server_default="NONE", nullable=False),
        sa.Column("engine_name", sa.String(80), nullable=False),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column("lexicon_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_body", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.String(500)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["review_id"], ["product_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["product_review_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id"),
    )
    for column in ("review_id", "revision_id", "processing_status", "outcome", "risk"):
        op.create_index(f"ix_review_moderation_assessments_{column}", "review_moderation_assessments", [column])

    op.create_table(
        "review_moderation_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matched_term_id", postgresql.UUID(as_uuid=True)),
        sa.Column("surface", sa.String(20), server_default="TEXT", nullable=False),
        sa.Column("category_code", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("matched_value", sa.String(160)),
        sa.Column("start_offset", sa.Integer()),
        sa.Column("end_offset", sa.Integer()),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["review_moderation_assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["product_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["product_review_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("assessment_id", "review_id", "revision_id", "matched_term_id", "category_code", "severity"):
        op.create_index(f"ix_review_moderation_signals_{column}", "review_moderation_signals", [column])

    op.create_table(
        "review_moderation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(50)),
        sa.Column("public_reason", sa.String(500)),
        sa.Column("internal_notes", sa.String(1000)),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["product_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["product_review_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assessment_id"], ["review_moderation_assessments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("review_id", "revision_id", "assessment_id", "action", "source"):
        op.create_index(f"ix_review_moderation_decisions_{column}", "review_moderation_decisions", [column])

    op.create_table(
        "review_moderation_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(100), nullable=False),
        sa.Column("language_code", sa.String(10), server_default="es", nullable=False),
        sa.Column("pattern", sa.String(160), nullable=False),
        sa.Column("normalized_pattern", sa.String(160), nullable=False),
        sa.Column("match_mode", sa.String(10), nullable=False),
        sa.Column("category_code", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.String(500)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_key"),
    )
    for column in ("normalized_pattern", "category_code", "is_active"):
        op.create_index(f"ix_review_moderation_terms_{column}", "review_moderation_terms", [column])
    op.create_foreign_key(
        "fk_review_moderation_signals_matched_term",
        "review_moderation_signals",
        "review_moderation_terms",
        ["matched_term_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "review_notification_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(15), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("attempts >= 0", name="ck_review_notification_attempts"),
        sa.ForeignKeyConstraint(["review_id"], ["product_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["product_review_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "revision_id", "event_type", name="uq_review_notification_outbox_event"),
    )
    for column in ("review_id", "revision_id", "user_id", "status", "next_attempt_at"):
        op.create_index(f"ix_review_notification_outbox_{column}", "review_notification_outbox", [column])

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, user_id, rating, body, status, public_rejection_reason, "
        "moderated_by_user_id, moderated_at, moderation_notes, created_at "
        "FROM product_reviews ORDER BY created_at, id"
    )).mappings().all()
    for row in rows:
        revision_id = uuid.uuid4()
        body = row["body"]
        content_hash = hashlib.sha256(f'{row["rating"]}\0{body}'.encode("utf-8")).hexdigest()
        connection.execute(sa.text(
            "INSERT INTO product_review_revisions "
            "(id, review_id, revision_number, rating, body, content_hash, created_by_user_id, created_at) "
            "VALUES (:id, :review_id, 1, :rating, :body, :content_hash, :user_id, :created_at)"
        ), {"id": revision_id, "review_id": row["id"], "rating": row["rating"],
            "body": body, "content_hash": content_hash, "user_id": row["user_id"],
            "created_at": row["created_at"]})
        connection.execute(sa.text(
            "UPDATE product_reviews SET current_revision_id=:revision_id WHERE id=:review_id"
        ), {"revision_id": revision_id, "review_id": row["id"]})
        connection.execute(sa.text(
            "UPDATE product_review_images SET revision_id=:revision_id WHERE review_id=:review_id"
        ), {"revision_id": revision_id, "review_id": row["id"]})
        if row["status"] in ("PUBLISHED", "REJECTED"):
            action = "APPROVE" if row["status"] == "PUBLISHED" else "REJECT"
            decision_id = uuid.uuid4()
            connection.execute(sa.text(
                "INSERT INTO review_moderation_decisions "
                "(id, review_id, revision_id, action, source, public_reason, internal_notes, "
                "actor_user_id, idempotency_key, created_at) "
                "VALUES (:id, :review_id, :revision_id, :action, 'LEGACY_IMPORT', :reason, "
                ":notes, :actor, :key, COALESCE(:created_at, now()))"
            ), {"id": decision_id, "review_id": row["id"], "revision_id": revision_id,
                "action": action, "reason": row["public_rejection_reason"],
                "notes": row["moderation_notes"], "actor": row["moderated_by_user_id"],
                "key": f'legacy:{row["id"]}:1:{action.lower()}',
                "created_at": row["moderated_at"] or row["created_at"]})
            connection.execute(sa.text(
                "UPDATE product_reviews SET moderation_source='LEGACY_IMPORT' WHERE id=:review_id"
            ), {"review_id": row["id"]})

    op.alter_column("product_review_images", "revision_id", nullable=False)
    op.create_unique_constraint(
        "uq_product_review_images_revision_sort",
        "product_review_images",
        ["revision_id", "sort_order"],
    )
    op.create_index(
        "ix_product_reviews_status_created_at_id", "product_reviews", ["status", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_product_review_images_revision_sort", "product_review_images", type_="unique"
    )
    op.create_unique_constraint(
        "uq_product_review_images_review_sort",
        "product_review_images",
        ["review_id", "sort_order"],
    )
    op.drop_index("ix_product_reviews_status_created_at_id", table_name="product_reviews")
    op.drop_constraint("fk_product_review_images_revision", "product_review_images", type_="foreignkey")
    op.drop_index("ix_product_review_images_revision_id", table_name="product_review_images")
    op.drop_column("product_review_images", "revision_id")
    op.drop_constraint("fk_product_reviews_current_revision", "product_reviews", type_="foreignkey")
    op.drop_index("ix_product_reviews_current_revision_id", table_name="product_reviews")
    op.drop_column("product_reviews", "rejection_reason_code")
    op.drop_column("product_reviews", "moderation_source")
    op.drop_column("product_reviews", "current_revision_id")
    for table in (
        "review_notification_outbox", "review_moderation_decisions",
        "review_moderation_signals", "review_moderation_terms",
        "review_moderation_assessments", "product_review_revisions",
    ):
        op.drop_table(table)
