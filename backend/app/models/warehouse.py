from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import LocationType


class Warehouse(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    address_line: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
    )

    city: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    country_code: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default="EC",
        server_default="EC",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    locations: Mapped[list["WarehouseLocation"]] = relationship(
        "WarehouseLocation",
        back_populates="warehouse",
        cascade="all, delete-orphan",
    )


class WarehouseLocation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "warehouse_locations"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "warehouses.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "warehouse_locations.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
    )

    barcode: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    location_type: Mapped[LocationType] = mapped_column(
        Enum(
            LocationType,
            name="warehouse_location_type",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )

    capacity_units: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    allows_mixed_offers: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    warehouse: Mapped["Warehouse"] = relationship(
        "Warehouse",
        back_populates="locations",
    )

    parent: Mapped["WarehouseLocation | None"] = relationship(
        "WarehouseLocation",
        remote_side="WarehouseLocation.id",
        back_populates="children",
    )

    children: Mapped[list["WarehouseLocation"]] = relationship(
        "WarehouseLocation",
        back_populates="parent",
    )

    __table_args__ = (
        UniqueConstraint(
            "warehouse_id",
            "code",
            name="uq_warehouse_locations_warehouse_code",
        ),
        CheckConstraint(
            "capacity_units IS NULL OR capacity_units > 0",
            name="warehouse_location_capacity_positive",
        ),
    )