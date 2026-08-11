from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PhysicalInventoryCount(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Immutable-baseline physical package count for one operating point."""

    __tablename__ = "physical_inventory_counts"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", server_default="OPEN", index=True
    )
    started_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    warehouse: Mapped["Warehouse"] = relationship("Warehouse")
    location: Mapped["WarehouseLocation | None"] = relationship("WarehouseLocation")
    started_by: Mapped["User"] = relationship(
        "User", foreign_keys=[started_by_user_id]
    )
    finalized_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[finalized_by_user_id]
    )
    expected_packages: Mapped[list["PhysicalInventoryCountExpectedPackage"]] = relationship(
        "PhysicalInventoryCountExpectedPackage",
        back_populates="count",
        cascade="all, delete-orphan",
    )
    scans: Mapped[list["PhysicalInventoryCountScan"]] = relationship(
        "PhysicalInventoryCountScan",
        back_populates="count",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'FINALIZED')",
            name="physical_inventory_count_status_valid",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND finalized_at IS NULL AND finalized_by_user_id IS NULL) "
            "OR (status = 'FINALIZED' AND finalized_at IS NOT NULL "
            "AND finalized_by_user_id IS NOT NULL)",
            name="physical_inventory_count_finalization_valid",
        ),
        CheckConstraint(
            "notes IS NULL OR char_length(btrim(notes)) BETWEEN 1 AND 500",
            name="physical_inventory_count_notes_valid",
        ),
        Index(
            "uq_physical_inventory_open_warehouse",
            "warehouse_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )


class PhysicalInventoryCountExpectedPackage(
    UUIDPrimaryKeyMixin, TimestampMixin, db.Model
):
    """Frozen baseline package captured when the count starts."""

    __tablename__ = "physical_inventory_count_expected_packages"

    count_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("physical_inventory_counts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    package_code_snapshot: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )
    expected_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    expected_location_snapshot: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )

    count: Mapped[PhysicalInventoryCount] = relationship(
        "PhysicalInventoryCount", back_populates="expected_packages"
    )
    expected_location: Mapped["WarehouseLocation | None"] = relationship(
        "WarehouseLocation"
    )

    __table_args__ = (
        CheckConstraint(
            "package_kind IN ('INBOUND', 'CUSTOMER')",
            name="physical_inventory_expected_kind_valid",
        ),
        UniqueConstraint(
            "count_id",
            "package_kind",
            "package_id",
            name="uq_physical_inventory_expected_package",
        ),
    )


class PhysicalInventoryCountScan(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Append-only scan observation made during an open physical count."""

    __tablename__ = "physical_inventory_count_scans"

    count_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("physical_inventory_counts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scanned_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    package_kind: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )
    package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    classification: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )
    registered_location_snapshot: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    scanned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    count: Mapped[PhysicalInventoryCount] = relationship(
        "PhysicalInventoryCount", back_populates="scans"
    )
    scanned_by: Mapped["User"] = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "package_kind IS NULL OR package_kind IN ('INBOUND', 'CUSTOMER')",
            name="physical_inventory_scan_kind_valid",
        ),
        CheckConstraint(
            "classification IN ('EXPECTED', 'UNEXPECTED')",
            name="physical_inventory_scan_classification_valid",
        ),
        CheckConstraint(
            "(package_kind IS NULL) = (package_id IS NULL)",
            name="physical_inventory_scan_package_identity_valid",
        ),
        UniqueConstraint(
            "count_id", "scanned_code", name="uq_physical_inventory_scan_code"
        ),
    )
