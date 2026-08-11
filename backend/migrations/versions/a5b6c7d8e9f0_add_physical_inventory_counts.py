"""add persistent physical package counts

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-08-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_inventory_counts",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="OPEN", nullable=False),
        sa.Column("started_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('OPEN', 'FINALIZED')", name="physical_inventory_count_status_valid"),
        sa.CheckConstraint(
            "(status = 'OPEN' AND finalized_at IS NULL AND finalized_by_user_id IS NULL) "
            "OR (status = 'FINALIZED' AND finalized_at IS NOT NULL "
            "AND finalized_by_user_id IS NOT NULL)",
            name="physical_inventory_count_finalization_valid",
        ),
        sa.CheckConstraint(
            "notes IS NULL OR char_length(btrim(notes)) BETWEEN 1 AND 500",
            name="physical_inventory_count_notes_valid",
        ),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["warehouse_locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["finalized_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "warehouse_id", "location_id", "status", "started_by_user_id",
        "started_at", "finalized_by_user_id", "finalized_at",
    ):
        op.create_index(
            op.f(f"ix_physical_inventory_counts_{column}"),
            "physical_inventory_counts", [column], unique=False,
        )
    op.create_index(
        "uq_physical_inventory_open_warehouse",
        "physical_inventory_counts",
        ["warehouse_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )

    op.create_table(
        "physical_inventory_count_expected_packages",
        sa.Column("count_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_kind", sa.String(length=16), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_code_snapshot", sa.String(length=40), nullable=False),
        sa.Column("expected_location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_location_snapshot", sa.String(length=120), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("package_kind IN ('INBOUND', 'CUSTOMER')", name="physical_inventory_expected_kind_valid"),
        sa.ForeignKeyConstraint(["count_id"], ["physical_inventory_counts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expected_location_id"], ["warehouse_locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("count_id", "package_kind", "package_id", name="uq_physical_inventory_expected_package"),
    )
    for column in (
        "count_id", "package_kind", "package_id", "package_code_snapshot",
        "expected_location_id",
    ):
        op.create_index(
            op.f(f"ix_physical_inventory_count_expected_packages_{column}"),
            "physical_inventory_count_expected_packages", [column], unique=False,
        )

    op.create_table(
        "physical_inventory_count_scans",
        sa.Column("count_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scanned_code", sa.String(length=120), nullable=False),
        sa.Column("package_kind", sa.String(length=16), nullable=True),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("classification", sa.String(length=20), nullable=False),
        sa.Column("registered_location_snapshot", sa.String(length=160), nullable=True),
        sa.Column("scanned_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("package_kind IS NULL OR package_kind IN ('INBOUND', 'CUSTOMER')", name="physical_inventory_scan_kind_valid"),
        sa.CheckConstraint("classification IN ('EXPECTED', 'UNEXPECTED')", name="physical_inventory_scan_classification_valid"),
        sa.CheckConstraint("(package_kind IS NULL) = (package_id IS NULL)", name="physical_inventory_scan_package_identity_valid"),
        sa.ForeignKeyConstraint(["count_id"], ["physical_inventory_counts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scanned_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("count_id", "scanned_code", name="uq_physical_inventory_scan_code"),
    )
    for column in (
        "count_id", "scanned_code", "package_kind", "package_id",
        "classification", "scanned_by_user_id", "scanned_at",
    ):
        op.create_index(
            op.f(f"ix_physical_inventory_count_scans_{column}"),
            "physical_inventory_count_scans", [column], unique=False,
        )


def downgrade() -> None:
    op.drop_table("physical_inventory_count_scans")
    op.drop_table("physical_inventory_count_expected_packages")
    op.drop_index(
        "uq_physical_inventory_open_warehouse",
        table_name="physical_inventory_counts",
    )
    op.drop_table("physical_inventory_counts")
