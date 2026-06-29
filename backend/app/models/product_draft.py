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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ProductDraftFileKind,
    ProductDraftFileStatus,
    ProductDraftStatus,
)


class ProductDraft(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "product_drafts"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subcategory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    template_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    title: Mapped[str | None] = mapped_column(String(250), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seller_sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(80), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    country_origin: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    package_contents: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    highlights: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    warranty_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    variants: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    pricing_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    inventory_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    dimensions_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    documents_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    status: Mapped[ProductDraftStatus] = mapped_column(
        Enum(
            ProductDraftStatus,
            name="product_draft_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ProductDraftStatus.DRAFT,
        server_default=ProductDraftStatus.DRAFT.value,
        index=True,
    )
    completion_percentage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    store: Mapped["Store"] = relationship("Store")
    created_by: Mapped["User"] = relationship("User")
    category: Mapped["Category"] = relationship("Category", foreign_keys=[category_id])
    subcategory: Mapped["Category"] = relationship("Category", foreign_keys=[subcategory_id])
    files: Mapped[list["ProductDraftFile"]] = relationship(
        "ProductDraftFile",
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="ProductDraftFile.position",
    )

    __table_args__ = (
        UniqueConstraint("store_id", "seller_sku", name="uq_product_drafts_store_sku"),
        UniqueConstraint("seller_sku", name="uq_product_drafts_seller_sku"),
        CheckConstraint(
            "completion_percentage >= 0 AND completion_percentage <= 100",
            name="product_draft_completion_percentage_valid",
        ),
        CheckConstraint(
            "condition IS NULL OR condition = 'NEW'",
            name="ck_product_drafts_condition_new_only",
        ),
        CheckConstraint(
            "barcode IS NULL OR seller_sku IS NULL OR barcode = seller_sku",
            name="ck_product_drafts_barcode_matches_seller_sku",
        ),
    )

    @property
    def product_code(self) -> str | None:
        return self.seller_sku


class ProductDraftFile(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "product_draft_files"

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[ProductDraftFileKind] = mapped_column(
        Enum(
            ProductDraftFileKind,
            name="product_draft_file_kind",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[ProductDraftFileStatus] = mapped_column(
        Enum(
            ProductDraftFileStatus,
            name="product_draft_file_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=ProductDraftFileStatus.ACTIVE,
        server_default=ProductDraftFileStatus.ACTIVE.value,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_cover: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)

    draft: Mapped[ProductDraft] = relationship("ProductDraft", back_populates="files")

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="product_draft_file_size_positive"),
        CheckConstraint("position >= 0", name="product_draft_file_position_nonnegative"),
    )
