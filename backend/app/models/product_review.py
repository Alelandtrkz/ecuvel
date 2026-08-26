from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ProductReviewStatus,
    ReviewModerationDecisionAction,
    ReviewModerationDecisionSource,
    ReviewModerationMatchMode,
    ReviewModerationOutcome,
    ReviewModerationProcessingStatus,
    ReviewModerationRisk,
    ReviewModerationSeverity,
    ReviewNotificationStatus,
)


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
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_review_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        index=True,
    )
    moderation_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    rejection_reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

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
    reply: Mapped["ProductReviewReply | None"] = relationship(
        "ProductReviewReply",
        back_populates="review",
        cascade="all, delete-orphan",
        uselist=False,
        single_parent=True,
    )
    revisions: Mapped[list["ProductReviewRevision"]] = relationship(
        "ProductReviewRevision",
        back_populates="review",
        cascade="all, delete-orphan",
        foreign_keys="ProductReviewRevision.review_id",
        order_by="ProductReviewRevision.revision_number",
    )
    current_revision: Mapped["ProductReviewRevision | None"] = relationship(
        "ProductReviewRevision",
        foreign_keys=[current_revision_id],
        post_update=True,
    )
    moderation_assessments: Mapped[list["ReviewModerationAssessment"]] = relationship(
        "ReviewModerationAssessment", back_populates="review", cascade="all, delete-orphan"
    )
    moderation_decisions: Mapped[list["ReviewModerationDecision"]] = relationship(
        "ReviewModerationDecision", back_populates="review", cascade="all, delete-orphan"
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


class ProductReviewReply(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "product_review_replies"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    review: Mapped["ProductReview"] = relationship(
        "ProductReview",
        back_populates="reply",
    )
    store: Mapped["Store"] = relationship("Store")
    created_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
    )
    updated_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(body)) >= 1 AND char_length(body) <= 500",
            name="product_review_reply_body_length",
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
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_review_revisions.id", ondelete="CASCADE"),
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
    revision: Mapped["ProductReviewRevision"] = relationship(
        "ProductReviewRevision", back_populates="images"
    )

    __table_args__ = (
        UniqueConstraint(
            "revision_id",
            "sort_order",
            name="uq_product_review_images_revision_sort",
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


class ProductReviewRevision(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "product_review_revisions"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now().astimezone(),
    )

    review: Mapped[ProductReview] = relationship(
        "ProductReview", back_populates="revisions", foreign_keys=[review_id]
    )
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_user_id])
    images: Mapped[list[ProductReviewImage]] = relationship(
        "ProductReviewImage", back_populates="revision", order_by="ProductReviewImage.sort_order"
    )

    __table_args__ = (
        UniqueConstraint("review_id", "revision_number", name="uq_product_review_revision_number"),
        CheckConstraint("revision_number > 0", name="ck_product_review_revision_number_positive"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_product_review_revision_rating"),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 2000", name="ck_product_review_revision_body"
        ),
    )


class ReviewModerationAssessment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "review_moderation_assessments"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_review_revisions.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=ReviewModerationProcessingStatus.PENDING.value,
        server_default=ReviewModerationProcessingStatus.PENDING.value,
        index=True,
    )
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    risk: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ReviewModerationRisk.NONE.value,
        server_default=ReviewModerationRisk.NONE.value, index=True,
    )
    engine_name: Mapped[str] = mapped_column(String(80), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    lexicon_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    review: Mapped[ProductReview] = relationship(
        "ProductReview", back_populates="moderation_assessments"
    )
    revision: Mapped[ProductReviewRevision] = relationship("ProductReviewRevision")
    signals: Mapped[list["ReviewModerationSignal"]] = relationship(
        "ReviewModerationSignal", back_populates="assessment", cascade="all, delete-orphan"
    )


class ReviewModerationSignal(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "review_moderation_signals"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_moderation_assessments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_review_revisions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    matched_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_moderation_terms.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    surface: Mapped[str] = mapped_column(String(20), nullable=False, server_default="TEXT")
    category_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    matched_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now().astimezone(),
    )

    assessment: Mapped[ReviewModerationAssessment] = relationship(
        "ReviewModerationAssessment", back_populates="signals"
    )
    matched_term: Mapped["ReviewModerationTerm | None"] = relationship(
        "ReviewModerationTerm"
    )


class ReviewModerationDecision(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "review_moderation_decisions"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_review_revisions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_moderation_assessments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    public_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now().astimezone(),
    )

    review: Mapped[ProductReview] = relationship(
        "ProductReview", back_populates="moderation_decisions"
    )
    revision: Mapped[ProductReviewRevision] = relationship("ProductReviewRevision")
    assessment: Mapped[ReviewModerationAssessment | None] = relationship(
        "ReviewModerationAssessment"
    )
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_user_id])


class ReviewModerationTerm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "review_moderation_terms"

    stable_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    language_code: Mapped[str] = mapped_column(String(10), nullable=False, server_default="es")
    pattern: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_pattern: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    match_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ReviewModerationMatchMode.TOKEN.value
    )
    category_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", index=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ReviewNotificationOutbox(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "review_notification_outbox"

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_review_revisions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(15), nullable=False, default=ReviewNotificationStatus.PENDING.value,
        server_default=ReviewNotificationStatus.PENDING.value, index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    review: Mapped[ProductReview] = relationship("ProductReview")
    revision: Mapped[ProductReviewRevision] = relationship("ProductReviewRevision")
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint(
            "review_id", "revision_id", "event_type",
            name="uq_review_notification_outbox_event",
        ),
        CheckConstraint("attempts >= 0", name="ck_review_notification_attempts"),
    )
