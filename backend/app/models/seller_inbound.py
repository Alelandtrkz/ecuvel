from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SellerInboundPackageStatus


class SellerInboundPackage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    """Physical package travelling from a seller to an ECUVEL location."""

    __tablename__ = "seller_inbound_packages"

    seller_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_code: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True
    )
    barcode: Mapped[str] = mapped_column(
        String(16), nullable=False, unique=True
    )
    status: Mapped[SellerInboundPackageStatus] = mapped_column(
        Enum(
            SellerInboundPackageStatus,
            name="seller_inbound_package_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SellerInboundPackageStatus.CREATED,
        server_default=SellerInboundPackageStatus.CREATED.value,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ready_for_dropoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ready_for_dropoff_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    received_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    seller_order: Mapped["SellerOrder"] = relationship(
        "SellerOrder", back_populates="inbound_packages"
    )
    items: Mapped[list["SellerInboundPackageItem"]] = relationship(
        "SellerInboundPackageItem",
        back_populates="package",
        cascade="all, delete-orphan",
    )
    created_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )
    ready_for_dropoff_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[ready_for_dropoff_by_user_id]
    )
    received_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[received_by_user_id]
    )
    received_location: Mapped["WarehouseLocation | None"] = relationship(
        "WarehouseLocation"
    )

    __table_args__ = (
        CheckConstraint(
            "barcode = package_code",
            name="seller_inbound_package_barcode_matches_code",
        ),
        CheckConstraint(
            "(status != 'CREATED') OR "
            "(ready_for_dropoff_at IS NULL AND received_at IS NULL "
            "AND received_location_id IS NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_created_state_valid",
        ),
        CheckConstraint(
            "(status != 'READY_FOR_DROPOFF') OR "
            "(ready_for_dropoff_at IS NOT NULL AND received_at IS NULL "
            "AND received_location_id IS NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_ready_state_valid",
        ),
        CheckConstraint(
            "(status != 'RECEIVED_BY_ECUVEL') OR "
            "(ready_for_dropoff_at IS NOT NULL AND received_at IS NOT NULL "
            "AND received_location_id IS NOT NULL AND cancelled_at IS NULL)",
            name="seller_inbound_package_received_state_valid",
        ),
        CheckConstraint(
            "(status != 'CANCELLED') OR "
            "(cancelled_at IS NOT NULL AND received_at IS NULL)",
            name="seller_inbound_package_cancelled_state_valid",
        ),
        Index(
            "ix_seller_inbound_packages_order_status",
            "seller_order_id",
            "status",
        ),
    )


class SellerInboundPackageItem(TimestampMixin, db.Model):
    __tablename__ = "seller_inbound_package_items"

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_inbound_packages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    package: Mapped["SellerInboundPackage"] = relationship(
        "SellerInboundPackage", back_populates="items"
    )
    order_item: Mapped["OrderItem"] = relationship("OrderItem")

    __table_args__ = (
        CheckConstraint(
            "quantity > 0", name="seller_inbound_package_item_quantity_positive"
        ),
    )
