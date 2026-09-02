"""add catalog ranking telemetry

Revision ID: 6499defb2c52
Revises: da1b2c3d4e5f
Create Date: 2026-09-02 02:49:51.792721

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6499defb2c52"
down_revision: Union[str, None] = "da1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _event_count() -> int:
    return int(
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM catalog_interaction_events"))
        .scalar_one()
    )


def upgrade() -> None:
    op.create_table(
        "catalog_interaction_events",
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("listing_key", sa.String(length=40), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column("offer_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("anonymous_session_id", sa.UUID(), nullable=True),
        sa.Column("ranking_request_id", sa.UUID(), nullable=True),
        sa.Column("served_ranker", sa.String(length=40), nullable=True),
        sa.Column("served_position", sa.Integer(), nullable=True),
        sa.Column("shadow_ranker", sa.String(length=40), nullable=True),
        sa.Column("shadow_position", sa.Integer(), nullable=True),
        sa.Column("shadow_score", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('IMPRESSION','CLICK','FAVORITE','ADD_TO_CART','PURCHASE','DELIVERED')",
            name=op.f("ck_catalog_interaction_events_catalog_event_type_valid"),
        ),
        sa.CheckConstraint(
            "surface IN ('HOME','SEARCH','CATEGORY','STORE','FAVORITES','RECOMMENDATIONS')",
            name=op.f("ck_catalog_interaction_events_catalog_event_surface_valid"),
        ),
        sa.CheckConstraint(
            "length(trim(listing_key)) > 0",
            name=op.f("ck_catalog_interaction_events_catalog_event_listing_key_nonempty"),
        ),
        sa.CheckConstraint(
            "served_position IS NULL OR served_position > 0",
            name=op.f("ck_catalog_interaction_events_catalog_event_served_position_positive"),
        ),
        sa.CheckConstraint(
            "shadow_position IS NULL OR shadow_position > 0",
            name=op.f("ck_catalog_interaction_events_catalog_event_shadow_position_positive"),
        ),
        sa.CheckConstraint(
            "shadow_score IS NULL OR shadow_score BETWEEN -1000000000 AND 1000000000",
            name=op.f("ck_catalog_interaction_events_catalog_event_shadow_score_finite"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_catalog_interaction_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["seller_offers.id"],
            name=op.f("fk_catalog_interaction_events_offer_id_seller_offers"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_catalog_interaction_events_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_catalog_interaction_events_variant_id_product_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_catalog_interaction_events"),
        ),
    )
    op.create_index(
        "ix_catalog_events_listing_type_occurred",
        "catalog_interaction_events",
        ["listing_key", "event_type", "occurred_at"],
    )
    op.create_index(
        "ix_catalog_events_occurred_at",
        "catalog_interaction_events",
        ["occurred_at"],
    )
    op.create_index(
        "ix_catalog_events_ranking_request",
        "catalog_interaction_events",
        ["ranking_request_id"],
    )
    op.create_index(
        "uq_catalog_events_impression_request_listing",
        "catalog_interaction_events",
        ["ranking_request_id", "listing_key"],
        unique=True,
        postgresql_where=sa.text("event_type = 'IMPRESSION'"),
    )


def downgrade() -> None:
    populated = _event_count()
    if populated:
        raise RuntimeError(
            "catalog interaction events exist; downgrade would discard "
            f"ranking telemetry; incompatible rows: {populated}"
        )
    op.drop_table("catalog_interaction_events")
