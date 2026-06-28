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
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProductReviewStatus


class ProductReview(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "product_reviews"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[ProductReviewStatus] = mapped_column(
        Enum(
            ProductReviewStatus,
            name="product_review_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ProductReviewStatus.PENDING_REVIEW,
        server_default=ProductReviewStatus.PENDING_REVIEW.value,
        index=True,
    )
    public_rejection_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    moderated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    moderated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    moderation_notes: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="product_reviews",
    )
    moderated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[moderated_by_user_id],
    )
    order: Mapped["Order"] = relationship("Order")
    order_item: Mapped["OrderItem"] = relationship("OrderItem")
    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="reviews",
    )
    images: Mapped[list["ProductReviewImage"]] = relationship(
        "ProductReviewImage",
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ProductReviewImage.sort_order",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "order_item_id",
            name="uq_product_reviews_user_order_item",
        ),
        CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="product_review_rating_range",
        ),
        CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 2000",
            name="product_review_body_length",
        ),
    )


class ProductReviewImage(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "product_review_images"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    public_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        unique=True,
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    media_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    width: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    height: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    review: Mapped["ProductReview"] = relationship(
        "ProductReview",
        back_populates="images",
    )

    __table_args__ = (
        UniqueConstraint(
            "review_id",
            "sort_order",
            name="uq_product_review_images_review_sort",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="product_review_image_size_positive",
        ),
        CheckConstraint(
            "width > 0 AND height > 0",
            name="product_review_image_dimensions_positive",
        ),
        CheckConstraint(
            "sort_order >= 0 AND sort_order < 5",
            name="product_review_image_sort_range",
        ),
    )
