from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    OrderStatus,
    SellerCommissionType,
    SellerOrderDecisionStatus,
    SellerOrderRejectionReason,
    SellerOrderStatus,
)


class Order(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "orders"

    order_number: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
    )

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OrderStatus.PENDING_PAYMENT,
        server_default=OrderStatus.PENDING_PAYMENT.value,
        index=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    shipping_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    grand_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    buyer: Mapped["User"] = relationship(
        "User",
    )

    seller_orders: Mapped[list["SellerOrder"]] = relationship(
        "SellerOrder",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "currency = 'USD'",
            name="order_currency_usd",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="order_subtotal_nonnegative",
        ),
        CheckConstraint(
            "discount_total >= 0",
            name="order_discount_nonnegative",
        ),
        CheckConstraint(
            "shipping_total >= 0",
            name="order_shipping_nonnegative",
        ),
        CheckConstraint(
            "tax_total >= 0",
            name="order_tax_nonnegative",
        ),
        CheckConstraint(
            "grand_total >= 0",
            name="order_grand_total_nonnegative",
        ),
        CheckConstraint(
            "discount_total <= subtotal",
            name="order_discount_not_greater_than_subtotal",
        ),
        CheckConstraint(
            """
            grand_total =
            subtotal
            - discount_total
            + shipping_total
            + tax_total
            """,
            name="order_total_consistent",
        ),
    )


class SellerOrder(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "seller_orders"

    seller_order_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "orders.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stores.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[SellerOrderStatus] = mapped_column(
        Enum(
            SellerOrderStatus,
            name="seller_order_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SellerOrderStatus.PENDING_PAYMENT,
        server_default=SellerOrderStatus.PENDING_PAYMENT.value,
        index=True,
    )

    decision_status: Mapped[SellerOrderDecisionStatus | None] = mapped_column(
        Enum(
            SellerOrderDecisionStatus,
            name="seller_order_decision_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
        index=True,
    )

    decision_available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    rejection_reason: Mapped[SellerOrderRejectionReason | None] = mapped_column(
        Enum(
            SellerOrderRejectionReason,
            name="seller_order_rejection_reason",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    rejection_comment: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    ship_by_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    estimated_delivery_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estimated_delivery_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requires_refund_resolution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    payout_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    commission_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    seller_net_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    order: Mapped["Order"] = relationship(
        "Order",
        back_populates="seller_orders",
    )

    store: Mapped["Store"] = relationship(
        "Store",
    )

    approved_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[approved_by_user_id]
    )
    rejected_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[rejected_by_user_id]
    )

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="seller_order",
        cascade="all, delete-orphan",
    )

    inbound_packages: Mapped[list["SellerInboundPackage"]] = relationship(
        "SellerInboundPackage",
        back_populates="seller_order",
        cascade="all, delete-orphan",
    )

    payout_item: Mapped["SellerPayoutItem | None"] = relationship(
        "SellerPayoutItem",
        back_populates="seller_order",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "store_id",
            name="uq_seller_orders_order_store",
        ),
        Index(
            "ix_seller_orders_store_payout_eligible",
            "store_id",
            "payout_eligible_at",
        ),
        CheckConstraint(
            "currency = 'USD'",
            name="seller_order_currency_usd",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="seller_order_subtotal_nonnegative",
        ),
        CheckConstraint(
            "discount_total >= 0",
            name="seller_order_discount_nonnegative",
        ),
        CheckConstraint(
            "commission_total >= 0",
            name="seller_order_commission_nonnegative",
        ),
        CheckConstraint(
            "seller_net_total >= 0",
            name="seller_order_net_nonnegative",
        ),
        CheckConstraint(
            "discount_total <= subtotal",
            name="seller_order_discount_valid",
        ),
        CheckConstraint(
            """
            seller_net_total =
            subtotal
            - discount_total
            - commission_total
            """,
            name="seller_order_net_consistent",
        ),
        CheckConstraint(
            "rejection_comment IS NULL OR "
            "(char_length(btrim(rejection_comment)) >= 1 "
            "AND char_length(rejection_comment) <= 300)",
            name="seller_order_rejection_comment_length",
        ),
        CheckConstraint(
            "estimated_delivery_from IS NULL OR estimated_delivery_to IS NULL "
            "OR estimated_delivery_from <= estimated_delivery_to",
            name="seller_order_delivery_window_valid",
        ),
        CheckConstraint(
            "payout_eligible_at IS NULL OR delivered_at IS NOT NULL",
            name="seller_order_payout_requires_delivery",
        ),
        CheckConstraint(
            "payout_eligible_at IS NULL OR payout_eligible_at >= delivered_at",
            name="seller_order_payout_after_delivery",
        ),
    )


class OrderItem(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "order_items"

    seller_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "seller_orders.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "seller_offers.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    store_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    line_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    gross_line_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    product_name_snapshot: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
    )

    seller_name_snapshot: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    seller_sku_snapshot: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    image_url_snapshot: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    variant_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    commission_type_snapshot: Mapped[SellerCommissionType] = mapped_column(
        Enum(
            SellerCommissionType,
            name="seller_commission_type",
            native_enum=True,
            validate_strings=True,
            create_constraint=False,
        ),
        nullable=False,
    )
    commission_rate_snapshot: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    commission_fixed_amount_snapshot: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    commission_amount_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    category_name_snapshot: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    category_code_snapshot: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    seller_order: Mapped["SellerOrder"] = relationship(
        "SellerOrder",
        back_populates="items",
    )

    offer: Mapped["SellerOffer"] = relationship(
        "SellerOffer",
    )

    package: Mapped["OrderPackage | None"] = relationship(
        "OrderPackage",
        back_populates="order_item",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="order_item_quantity_positive",
        ),
        CheckConstraint(
            "currency = 'USD'",
            name="order_item_currency_usd",
        ),
        CheckConstraint(
            "gross_line_amount = unit_price * quantity",
            name="order_item_gross_line_consistent",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="order_item_unit_price_nonnegative",
        ),
        CheckConstraint(
            "discount_amount >= 0",
            name="order_item_discount_nonnegative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="order_item_tax_nonnegative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="order_item_total_nonnegative",
        ),
        CheckConstraint(
            """
            discount_amount <= unit_price * quantity
            """,
            name="order_item_discount_valid",
        ),
        CheckConstraint(
            """
            line_total =
            unit_price * quantity
            - discount_amount
            + tax_amount
            """,
            name="order_item_total_consistent",
        ),
        CheckConstraint(
            "commission_rate_snapshot IS NULL OR "
            "(commission_rate_snapshot >= 0 AND commission_rate_snapshot <= 100)",
            name="order_item_commission_rate_valid",
        ),
        CheckConstraint(
            "commission_fixed_amount_snapshot IS NULL OR "
            "commission_fixed_amount_snapshot >= 0",
            name="order_item_commission_fixed_nonnegative",
        ),
        CheckConstraint(
            "commission_amount_snapshot >= 0",
            name="order_item_commission_amount_nonnegative",
        ),
        CheckConstraint(
            "(commission_type_snapshot = 'PERCENTAGE' "
            "AND commission_rate_snapshot IS NOT NULL "
            "AND commission_fixed_amount_snapshot IS NULL) OR "
            "(commission_type_snapshot = 'FIXED' "
            "AND commission_rate_snapshot IS NULL "
            "AND commission_fixed_amount_snapshot IS NOT NULL)",
            name="order_item_commission_snapshot_complete",
        ),
    )
