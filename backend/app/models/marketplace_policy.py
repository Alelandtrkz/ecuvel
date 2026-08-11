from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MarketplaceCommissionRule(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "marketplace_commission_rules"

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    # SellerOffer stores percentage points: 8.00 represents eight percent.
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True,
    )

    category: Mapped["Category | None"] = relationship("Category")
    store: Mapped["Store | None"] = relationship("Store")

    __table_args__ = (
        CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 100",
            name="marketplace_commission_rule_rate_valid",
        ),
        CheckConstraint(
            "store_id IS NULL OR category_id IS NOT NULL",
            name="marketplace_commission_rule_scope_valid",
        ),
        UniqueConstraint(
            "store_id", "category_id",
            name="uq_marketplace_commission_rule_store_category",
        ),
        Index(
            "uq_marketplace_commission_category_default",
            "category_id",
            unique=True,
            postgresql_where=text("store_id IS NULL AND category_id IS NOT NULL"),
        ),
        Index(
            "uq_marketplace_commission_global_default",
            text("(1)"),
            unique=True,
            postgresql_where=text("store_id IS NULL AND category_id IS NULL"),
        ),
    )


class StoreInventoryLocation(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "store_inventory_locations"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True,
    )

    store: Mapped["Store"] = relationship(
        "Store", back_populates="inventory_locations",
    )
    location: Mapped["WarehouseLocation"] = relationship("WarehouseLocation")

    __table_args__ = (
        UniqueConstraint(
            "store_id", "location_id",
            name="uq_store_inventory_location_store_location",
        ),
        Index(
            "uq_store_inventory_default_location",
            "store_id",
            unique=True,
            postgresql_where=text("is_default AND is_active"),
        ),
    )
