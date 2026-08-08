from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SellerPayoutStatus


class SellerPayout(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """Transferencia agrupada y auditable de ECUVEL hacia una tienda."""

    __tablename__ = "seller_payouts"

    payout_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
        server_default=text(
            "('PAY-' || lpad(nextval('seller_payout_number_seq'::regclass)::text, 8, '0'))"
        ),
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[SellerPayoutStatus] = mapped_column(
        Enum(
            SellerPayoutStatus,
            name="seller_payout_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SellerPayoutStatus.SCHEDULED,
        server_default=SellerPayoutStatus.SCHEDULED.value,
        index=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )
    gross_sales_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    commission_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    net_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    external_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    destination_bank_name_snapshot: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    destination_account_last4: Mapped[str | None] = mapped_column(
        String(4), nullable=True
    )
    receipt_storage_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, unique=True
    )
    receipt_original_filename: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    receipt_media_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    receipt_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    store: Mapped["Store"] = relationship("Store", back_populates="payouts")
    items: Mapped[list["SellerPayoutItem"]] = relationship(
        "SellerPayoutItem",
        back_populates="payout",
        cascade="all, delete-orphan",
        order_by="SellerPayoutItem.seller_order_id",
    )

    __table_args__ = (
        CheckConstraint("gross_sales_total >= 0", name="seller_payout_gross_nonnegative"),
        CheckConstraint("discount_total >= 0", name="seller_payout_discount_nonnegative"),
        CheckConstraint("commission_total >= 0", name="seller_payout_commission_nonnegative"),
        CheckConstraint("net_total >= 0", name="seller_payout_net_nonnegative"),
        CheckConstraint(
            "net_total = gross_sales_total - discount_total - commission_total",
            name="seller_payout_total_consistent",
        ),
        CheckConstraint(
            "destination_account_last4 IS NULL OR char_length(destination_account_last4) = 4",
            name="seller_payout_account_last4_valid",
        ),
        CheckConstraint(
            "(receipt_storage_key IS NULL AND receipt_original_filename IS NULL "
            "AND receipt_media_type IS NULL AND receipt_size_bytes IS NULL "
            "AND receipt_sha256 IS NULL) OR "
            "(receipt_storage_key IS NOT NULL AND receipt_original_filename IS NOT NULL "
            "AND receipt_media_type IS NOT NULL AND receipt_size_bytes > 0 "
            "AND receipt_sha256 IS NOT NULL)",
            name="seller_payout_receipt_metadata_complete",
        ),
        CheckConstraint(
            "status != 'PAID' OR (paid_at IS NOT NULL AND external_reference IS NOT NULL)",
            name="seller_payout_paid_state_valid",
        ),
    )


class SellerPayoutItem(db.Model):
    """Snapshot financiero inmutable de una SellerOrder liquidada."""

    __tablename__ = "seller_payout_items"

    payout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_payouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    seller_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_orders.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    gross_amount_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_amount_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net_amount_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payout: Mapped[SellerPayout] = relationship("SellerPayout", back_populates="items")
    seller_order: Mapped["SellerOrder"] = relationship(
        "SellerOrder", back_populates="payout_item"
    )

    __table_args__ = (
        UniqueConstraint("seller_order_id", name="uq_seller_payout_items_seller_order"),
        CheckConstraint(
            "gross_amount_snapshot >= 0", name="seller_payout_item_gross_nonnegative"
        ),
        CheckConstraint(
            "discount_amount_snapshot >= 0", name="seller_payout_item_discount_nonnegative"
        ),
        CheckConstraint(
            "commission_amount_snapshot >= 0", name="seller_payout_item_commission_nonnegative"
        ),
        CheckConstraint(
            "net_amount_snapshot >= 0", name="seller_payout_item_net_nonnegative"
        ),
        CheckConstraint(
            "net_amount_snapshot = gross_amount_snapshot - discount_amount_snapshot "
            "- commission_amount_snapshot",
            name="seller_payout_item_total_consistent",
        ),
    )


_PAYOUT_TOTAL_FIELDS = (
    "gross_sales_total",
    "discount_total",
    "commission_total",
    "net_total",
    "currency",
    "store_id",
)
_PAYOUT_ITEM_SNAPSHOT_FIELDS = (
    "seller_order_id",
    "gross_amount_snapshot",
    "discount_amount_snapshot",
    "commission_amount_snapshot",
    "net_amount_snapshot",
    "eligible_at",
)


@event.listens_for(SellerPayout, "before_update")
def _keep_payout_financial_snapshot_immutable(_mapper, _connection, target) -> None:
    state = inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _PAYOUT_TOTAL_FIELDS):
        raise ValueError("Los importes y la tienda de una liquidación son inmutables.")


@event.listens_for(SellerPayoutItem, "before_update")
def _keep_payout_item_snapshot_immutable(_mapper, _connection, target) -> None:
    state = inspect(target)
    if any(
        state.attrs[field].history.has_changes()
        for field in _PAYOUT_ITEM_SNAPSHOT_FIELDS
    ):
        raise ValueError("El snapshot de un pedido liquidado es inmutable.")
