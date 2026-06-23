from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StoreMemberRole, StoreStatus


class Store(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "stores"

    public_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        unique=True,
        index=True,
    )

    legal_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    tax_id: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        unique=True,
    )

    status: Mapped[StoreStatus] = mapped_column(
        Enum(
            StoreStatus,
            name="store_status",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=StoreStatus.DRAFT,
        server_default=StoreStatus.DRAFT.value,
        index=True,
    )

    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    members: Mapped[list["StoreMember"]] = relationship(
        "StoreMember",
        back_populates="store",
        cascade="all, delete-orphan",
    )

    offers: Mapped[list["SellerOffer"]] = relationship(
        "SellerOffer",
        back_populates="store",
    )


class StoreMember(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    db.Model,
):
    __tablename__ = "store_members"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "stores.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[StoreMemberRole] = mapped_column(
        Enum(
            StoreMemberRole,
            name="store_member_role",
            native_enum=True,
            validate_strings=True,
        ),
        nullable=False,
        default=StoreMemberRole.VIEWER,
        server_default=StoreMemberRole.VIEWER.value,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    store: Mapped["Store"] = relationship(
        "Store",
        back_populates="members",
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="store_memberships",
    )

    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "user_id",
            name="uq_store_members_store_user",
        ),
    )