from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import OfferStatus, SellerCommissionType


class Category(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "categories"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "categories.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(140),
        nullable=False,
        unique=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    parent: Mapped["Category | None"] = relationship(
        "Category",
        remote_side="Category.id",
        back_populates="children",
    )

    children: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="parent",
    )

    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="category",
    )

    __table_args__ = (
        CheckConstraint(
            "sort_order >= 0",
            name="category_sort_order_nonnegative",
        ),
    )


class Product(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "categories.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(250),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(280),
        nullable=False,
        unique=True,
        index=True,
    )

    brand: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )

    model_number: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    variant_configuration: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="products",
    )

    variants: Mapped[list["ProductVariant"]] = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
    )

    media: Mapped[list["ProductMedia"]] = relationship(
        "ProductMedia",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductMedia.position",
    )

    favorites: Mapped[list["Favorite"]] = relationship(
        "Favorite",
        back_populates="product",
    )

    reviews: Mapped[list["ProductReview"]] = relationship(
        "ProductReview",
        back_populates="product",
    )


class ProductVariant(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "products.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    catalog_sku: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        unique=True,
        index=True,
    )

    title: Mapped[str | None] = mapped_column(
        String(180),
        nullable=True,
    )

    manufacturer_barcode: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        unique=True,
        index=True,
    )

    attributes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    combination_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
    )

    weight_grams: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    length_mm: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    width_mm: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    height_mm: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="variants",
    )

    offers: Mapped[list["SellerOffer"]] = relationship(
        "SellerOffer",
        back_populates="variant",
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "combination_key",
            name="uq_product_variants_product_combination",
        ),
        CheckConstraint(
            "weight_grams IS NULL OR weight_grams >= 0",
            name="variant_weight_nonnegative",
        ),
        CheckConstraint(
            "length_mm IS NULL OR length_mm >= 0",
            name="variant_length_nonnegative",
        ),
        CheckConstraint(
            "width_mm IS NULL OR width_mm >= 0",
            name="variant_width_nonnegative",
        ),
        CheckConstraint(
            "height_mm IS NULL OR height_mm >= 0",
            name="variant_height_nonnegative",
        ),
    )


class SellerOffer(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "seller_offers"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stores.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "product_variants.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    seller_sku: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    compare_at_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )

    commission_type: Mapped[SellerCommissionType] = mapped_column(
        Enum(
            SellerCommissionType,
            name="seller_commission_type",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=SellerCommissionType.PERCENTAGE,
        server_default=SellerCommissionType.PERCENTAGE.value,
    )

    commission_fixed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True,
    )

    commission_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD",
    )

    status: Mapped[OfferStatus] = mapped_column(
        Enum(
            OfferStatus,
            name="offer_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=OfferStatus.DRAFT,
        server_default=OfferStatus.DRAFT.value,
        index=True,
    )

    store: Mapped["Store"] = relationship(
        "Store",
        back_populates="offers",
    )

    variant: Mapped["ProductVariant"] = relationship(
        "ProductVariant",
        back_populates="offers",
    )

    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "seller_sku",
            name="uq_seller_offers_store_sku",
        ),
        UniqueConstraint(
            "store_id",
            "variant_id",
            name="uq_seller_offers_store_variant",
        ),
        CheckConstraint(
            "price >= 0",
            name="seller_offer_price_nonnegative",
        ),
        CheckConstraint(
            """
            compare_at_price IS NULL
            OR compare_at_price >= price
            """,
            name="seller_offer_compare_price_valid",
        ),
        CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 100",
            name="seller_offer_commission_valid",
        ),
        CheckConstraint(
            "(commission_type = 'PERCENTAGE' AND commission_fixed_amount IS NULL) "
            "OR (commission_type = 'FIXED' AND commission_rate = 0 "
            "AND commission_fixed_amount > 0 AND commission_fixed_amount < price)",
            name="seller_offer_commission_mode_valid",
        ),
        CheckConstraint(
            "commission_currency = 'USD'",
            name="seller_offer_commission_currency_valid",
        ),
    )


class ProductMedia(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "product_media"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    public_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: uuid.uuid4().hex,
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thumbnail_storage_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, unique=True
    )
    thumbnail_media_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    thumbnail_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_version: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_cover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    variant_axis_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    variant_value_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    product: Mapped[Product] = relationship("Product", back_populates="media")

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="product_media_size_positive"),
        CheckConstraint("position >= 0", name="product_media_position_nonnegative"),
        CheckConstraint(
            "(variant_axis_key IS NULL) = (variant_value_key IS NULL)",
            name="product_media_variant_binding_complete",
        ),
        CheckConstraint(
            "(content_sha256 IS NULL AND processing_version IS NULL "
            "AND thumbnail_storage_key IS NULL AND thumbnail_media_type IS NULL "
            "AND thumbnail_size_bytes IS NULL AND thumbnail_width IS NULL "
            "AND thumbnail_height IS NULL AND thumbnail_sha256 IS NULL) OR "
            "(content_sha256 IS NOT NULL AND processing_version IS NOT NULL "
            "AND media_type = 'image/webp' AND width IS NOT NULL AND height IS NOT NULL "
            "AND thumbnail_storage_key IS NOT NULL "
            "AND thumbnail_media_type = 'image/webp' "
            "AND thumbnail_size_bytes IS NOT NULL AND thumbnail_width IS NOT NULL "
            "AND thumbnail_height IS NOT NULL AND thumbnail_sha256 IS NOT NULL)",
            name="product_media_processed_state_complete",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="product_media_content_sha256_format",
        ),
        CheckConstraint(
            "thumbnail_sha256 IS NULL OR thumbnail_sha256 ~ '^[0-9a-f]{64}$'",
            name="product_media_thumbnail_sha256_format",
        ),
        CheckConstraint(
            "thumbnail_size_bytes IS NULL OR thumbnail_size_bytes > 0",
            name="product_media_thumbnail_size_positive",
        ),
        CheckConstraint(
            "thumbnail_width IS NULL OR (thumbnail_width > 0 AND thumbnail_height > 0)",
            name="product_media_thumbnail_dimensions_positive",
        ),
        CheckConstraint(
            "processing_version IS NULL OR processing_version > 0",
            name="product_media_processing_version_positive",
        ),
    )
