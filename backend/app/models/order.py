from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import OrderStatus, SellerOrderStatus


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

    order: Mapped["Order"] = relationship(
        "Order",
        back_populates="seller_orders",
    )

    store: Mapped["Store"] = relationship(
        "Store",
    )

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="seller_order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "store_id",
            name="uq_seller_orders_order_store",
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

    seller_order: Mapped["SellerOrder"] = relationship(
        "SellerOrder",
        back_populates="items",
    )

    offer: Mapped["SellerOffer"] = relationship(
        "SellerOffer",
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="order_item_quantity_positive",
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
    )