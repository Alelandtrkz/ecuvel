"""add product moderation and publication policies

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-08-11 01:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "warehouses",
        sa.Column("seller_store_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_warehouses_seller_store_id_stores",
        "warehouses", "stores", ["seller_store_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_warehouses_seller_store_id"),
        "warehouses", ["seller_store_id"], unique=False,
    )

    op.create_table(
        "marketplace_commission_rules",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 100",
            name="marketplace_commission_rule_rate_valid",
        ),
        sa.CheckConstraint(
            "store_id IS NULL OR category_id IS NOT NULL",
            name="marketplace_commission_rule_scope_valid",
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "category_id", name="uq_marketplace_commission_rule_store_category"),
    )
    for column in ("category_id", "store_id", "is_active"):
        op.create_index(
            op.f(f"ix_marketplace_commission_rules_{column}"),
            "marketplace_commission_rules", [column], unique=False,
        )
    op.create_index(
        "uq_marketplace_commission_category_default",
        "marketplace_commission_rules", ["category_id"], unique=True,
        postgresql_where=sa.text("store_id IS NULL AND category_id IS NOT NULL"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_marketplace_commission_global_default "
        "ON marketplace_commission_rules ((1)) "
        "WHERE store_id IS NULL AND category_id IS NULL"
    )

    op.create_table(
        "store_inventory_locations",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["warehouse_locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id"),
        sa.UniqueConstraint("store_id", "location_id", name="uq_store_inventory_location_store_location"),
    )
    for column in ("store_id", "location_id", "is_default", "is_active"):
        op.create_index(
            op.f(f"ix_store_inventory_locations_{column}"),
            "store_inventory_locations", [column], unique=False,
        )
    op.create_index(
        "uq_store_inventory_default_location",
        "store_inventory_locations", ["store_id"], unique=True,
        postgresql_where=sa.text("is_default AND is_active"),
    )

    op.create_table(
        "product_draft_moderation_events",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("checklist_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'CHANGES_REQUESTED', 'REJECTED')",
            name="product_draft_moderation_decision_valid",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 2000",
            name="product_draft_moderation_note_valid",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["product_drafts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("draft_id", "decision", "reason_code", "actor_user_id", "created_at"):
        op.create_index(
            op.f(f"ix_product_draft_moderation_events_{column}"),
            "product_draft_moderation_events", [column], unique=False,
        )

    op.create_table(
        "product_draft_publications",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["product_drafts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id"),
        sa.UniqueConstraint("product_id"),
    )
    for column in ("draft_id", "product_id", "published_by_user_id", "published_at"):
        op.create_index(
            op.f(f"ix_product_draft_publications_{column}"),
            "product_draft_publications", [column], unique=False,
        )


def downgrade() -> None:
    op.drop_table("product_draft_publications")
    op.drop_table("product_draft_moderation_events")
    op.drop_index("uq_store_inventory_default_location", table_name="store_inventory_locations")
    op.drop_table("store_inventory_locations")
    op.execute("DROP INDEX IF EXISTS uq_marketplace_commission_global_default")
    op.drop_index("uq_marketplace_commission_category_default", table_name="marketplace_commission_rules")
    op.drop_table("marketplace_commission_rules")
    op.drop_index(op.f("ix_warehouses_seller_store_id"), table_name="warehouses")
    op.drop_constraint("fk_warehouses_seller_store_id_stores", "warehouses", type_="foreignkey")
    op.drop_column("warehouses", "seller_store_id")
