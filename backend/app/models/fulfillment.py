from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PackageStatus


class OrderPackage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "order_packages"

    package_code: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
    )

    barcode: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        unique=True,
        index=True,
    )

    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "order_items.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[PackageStatus] = mapped_column(
        Enum(
            PackageStatus,
            name="package_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=PackageStatus.CREATED,
        server_default=PackageStatus.CREATED.value,
        index=True,
    )

    packed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    packed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    packing_notes: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    order_item: Mapped["OrderItem"] = relationship(
        "OrderItem",
        back_populates="package",
    )

    packed_by: Mapped["User | None"] = relationship(
        "User",
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="order_package_quantity_positive",
        ),
    )
