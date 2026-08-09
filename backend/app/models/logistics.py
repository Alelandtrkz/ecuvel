from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    LogisticsPackageStatus,
    LogisticsTrackingEventType,
    LogisticsTransferStatus,
)


class LogisticsPackageState(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Transactional position and custody for an inbound physical package."""

    __tablename__ = "logistics_package_states"

    seller_inbound_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_inbound_packages.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[LogisticsPackageStatus] = mapped_column(
        Enum(
            LogisticsPackageStatus,
            name="logistics_package_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=LogisticsPackageStatus.AT_POINT,
        server_default=LogisticsPackageStatus.AT_POINT.value,
        index=True,
    )
    current_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    current_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    custodian_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    custodian_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    expected_destination_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_deviated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    seller_inbound_package: Mapped["SellerInboundPackage"] = relationship(
        "SellerInboundPackage"
    )
    current_warehouse: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[current_warehouse_id]
    )
    current_location: Mapped["WarehouseLocation | None"] = relationship(
        "WarehouseLocation", foreign_keys=[current_location_id]
    )
    custodian_warehouse: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[custodian_warehouse_id]
    )
    custodian_user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[custodian_user_id]
    )
    expected_destination: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[expected_destination_warehouse_id]
    )
    transfers: Mapped[list["LogisticsTransfer"]] = relationship(
        "LogisticsTransfer",
        back_populates="package_state",
        cascade="all, delete-orphan",
        order_by="LogisticsTransfer.assigned_at",
    )
    events: Mapped[list["LogisticsTrackingEvent"]] = relationship(
        "LogisticsTrackingEvent",
        back_populates="package_state",
        cascade="all, delete-orphan",
        order_by="LogisticsTrackingEvent.occurred_at",
    )

    __table_args__ = (
        CheckConstraint(
            "(custodian_warehouse_id IS NULL) <> (custodian_user_id IS NULL)",
            name="logistics_state_exactly_one_custodian",
        ),
        CheckConstraint(
            "current_location_id IS NULL OR current_warehouse_id IS NOT NULL",
            name="logistics_state_location_requires_warehouse",
        ),
        CheckConstraint(
            "status != 'IN_TRANSIT' OR "
            "(current_warehouse_id IS NULL AND current_location_id IS NULL "
            "AND custodian_user_id IS NOT NULL)",
            name="logistics_state_in_transit_valid",
        ),
        CheckConstraint(
            "status NOT IN ('AT_POINT', 'ASSIGNED', 'DEVIATED') OR "
            "(current_warehouse_id IS NOT NULL "
            "AND custodian_warehouse_id = current_warehouse_id)",
            name="logistics_state_at_point_custody_valid",
        ),
        Index(
            "ix_logistics_states_status_last_event",
            "status",
            "last_event_at",
        ),
        Index(
            "ix_logistics_states_deviated_last_event",
            "is_deviated",
            "last_event_at",
        ),
    )


class LogisticsTransfer(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "logistics_transfers"

    transfer_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(
            "('TRF-' || lpad(nextval('logistics_transfer_number_seq'::regclass)::text, 8, '0'))"
        ),
    )
    package_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logistics_package_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[LogisticsTransferStatus] = mapped_column(
        Enum(
            LogisticsTransferStatus,
            name="logistics_transfer_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=LogisticsTransferStatus.ASSIGNED,
        server_default=LogisticsTransferStatus.ASSIGNED.value,
        index=True,
    )
    vehicle_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_corrective: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    previous_transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logistics_transfers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    eta_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    package_state: Mapped[LogisticsPackageState] = relationship(
        "LogisticsPackageState", back_populates="transfers"
    )
    origin_warehouse: Mapped["Warehouse"] = relationship(
        "Warehouse", foreign_keys=[origin_warehouse_id]
    )
    destination_warehouse: Mapped["Warehouse"] = relationship(
        "Warehouse", foreign_keys=[destination_warehouse_id]
    )
    assigned_user: Mapped["User"] = relationship(
        "User", foreign_keys=[assigned_user_id]
    )
    previous_transfer: Mapped["LogisticsTransfer | None"] = relationship(
        "LogisticsTransfer", remote_side="LogisticsTransfer.id"
    )
    events: Mapped[list["LogisticsTrackingEvent"]] = relationship(
        "LogisticsTrackingEvent", back_populates="transfer"
    )

    __table_args__ = (
        CheckConstraint(
            "origin_warehouse_id <> destination_warehouse_id",
            name="logistics_transfer_distinct_points",
        ),
        CheckConstraint(
            "vehicle_code IS NULL OR char_length(btrim(vehicle_code)) BETWEEN 1 AND 40",
            name="logistics_transfer_vehicle_code_valid",
        ),
        CheckConstraint(
            "picked_up_at IS NULL OR picked_up_at >= assigned_at",
            name="logistics_transfer_pickup_after_assignment",
        ),
        CheckConstraint(
            "received_at IS NULL OR "
            "(picked_up_at IS NOT NULL AND received_at >= picked_up_at)",
            name="logistics_transfer_receipt_after_pickup",
        ),
        CheckConstraint(
            "eta_at IS NULL OR eta_at >= assigned_at",
            name="logistics_transfer_eta_after_assignment",
        ),
        Index(
            "uq_logistics_transfer_active_package",
            "package_state_id",
            unique=True,
            postgresql_where=text("status IN ('ASSIGNED', 'IN_TRANSIT')"),
        ),
        Index(
            "ix_logistics_transfers_destination_status",
            "destination_warehouse_id",
            "status",
        ),
    )


class LogisticsTrackingEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Append-only evidence for every physical logistics transition."""

    __tablename__ = "logistics_tracking_events"

    package_state_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logistics_package_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logistics_transfers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[LogisticsTrackingEventType] = mapped_column(
        Enum(
            LogisticsTrackingEventType,
            name="logistics_tracking_event_type",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouse_locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    previous_custodian_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    previous_custodian_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    new_custodian_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    new_custodian_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(150), nullable=True, unique=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    package_state: Mapped[LogisticsPackageState] = relationship(
        "LogisticsPackageState", back_populates="events"
    )
    transfer: Mapped[LogisticsTransfer | None] = relationship(
        "LogisticsTransfer", back_populates="events"
    )
    warehouse: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[warehouse_id]
    )
    location: Mapped["WarehouseLocation | None"] = relationship(
        "WarehouseLocation", foreign_keys=[location_id]
    )
    previous_custodian_warehouse: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[previous_custodian_warehouse_id]
    )
    previous_custodian_user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[previous_custodian_user_id]
    )
    new_custodian_warehouse: Mapped["Warehouse | None"] = relationship(
        "Warehouse", foreign_keys=[new_custodian_warehouse_id]
    )
    new_custodian_user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[new_custodian_user_id]
    )
    actor: Mapped["User | None"] = relationship(
        "User", foreign_keys=[actor_user_id]
    )

    __table_args__ = (
        CheckConstraint(
            "idempotency_key IS NULL OR "
            "char_length(btrim(idempotency_key)) BETWEEN 1 AND 150",
            name="logistics_event_idempotency_key_valid",
        ),
        CheckConstraint(
            "location_id IS NULL OR warehouse_id IS NOT NULL",
            name="logistics_event_location_requires_warehouse",
        ),
        Index(
            "ix_logistics_events_state_occurred",
            "package_state_id",
            "occurred_at",
        ),
    )


@event.listens_for(LogisticsTrackingEvent, "before_update")
def _prevent_tracking_event_update(*_args) -> None:
    raise ValueError("Los eventos logísticos son append-only.")


@event.listens_for(LogisticsTrackingEvent, "before_delete")
def _prevent_tracking_event_delete(*_args) -> None:
    raise ValueError("Los eventos logísticos no pueden eliminarse.")
