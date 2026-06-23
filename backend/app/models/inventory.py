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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    InventoryMovementType,
    ReservationStatus,
)


class InventoryBalance(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "inventory_balances"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "seller_offers.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "warehouse_locations.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    on_hand_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    reserved_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    blocked_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    offer: Mapped["SellerOffer"] = relationship(
        "SellerOffer",
    )

    location: Mapped["WarehouseLocation"] = relationship(
        "WarehouseLocation",
    )

    reservations: Mapped[list["InventoryReservation"]] = relationship(
        "InventoryReservation",
        back_populates="balance",
    )

    movements: Mapped[list["InventoryMovement"]] = relationship(
        "InventoryMovement",
        back_populates="balance",
    )

    @property
    def available_quantity(self) -> int:
        return (
            self.on_hand_quantity
            - self.reserved_quantity
            - self.blocked_quantity
        )

    __table_args__ = (
        UniqueConstraint(
            "offer_id",
            "location_id",
            name="uq_inventory_balances_offer_location",
        ),
        CheckConstraint(
            "on_hand_quantity >= 0",
            name="inventory_on_hand_nonnegative",
        ),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="inventory_reserved_nonnegative",
        ),
        CheckConstraint(
            "blocked_quantity >= 0",
            name="inventory_blocked_nonnegative",
        ),
        CheckConstraint(
            """
            reserved_quantity
            + blocked_quantity
            <= on_hand_quantity
            """,
            name="inventory_allocated_not_greater_than_stock",
        ),
    )


class InventoryReservation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "inventory_reservations"

    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "order_items.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "inventory_balances.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            name="inventory_reservation_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ReservationStatus.ACTIVE,
        server_default=ReservationStatus.ACTIVE.value,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    order_item: Mapped["OrderItem"] = relationship(
        "OrderItem",
    )

    balance: Mapped["InventoryBalance"] = relationship(
        "InventoryBalance",
        back_populates="reservations",
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="inventory_reservation_quantity_positive",
        ),
        Index(
            "uq_inventory_active_reservation",
            "order_item_id",
            "balance_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class InventoryMovement(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "inventory_movements"

    balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "inventory_balances.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[InventoryMovementType] = mapped_column(
        Enum(
            InventoryMovementType,
            name="inventory_movement_type",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )

    delta_on_hand: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    delta_reserved: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    delta_blocked: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    reference_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    reference_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        unique=True,
        index=True,
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    notes: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    balance: Mapped["InventoryBalance"] = relationship(
        "InventoryBalance",
        back_populates="movements",
    )

    actor: Mapped["User | None"] = relationship(
        "User",
    )

    __table_args__ = (
        CheckConstraint(
            """
            delta_on_hand <> 0
            OR delta_reserved <> 0
            OR delta_blocked <> 0
            """,
            name="inventory_movement_has_effect",
        ),
    )