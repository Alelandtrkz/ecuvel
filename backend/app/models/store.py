from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    event,
    text,
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

    registration_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
        server_default=text("nextval('store_registration_number_seq'::regclass)"),
    )

    product_code_prefix: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
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

    product_code_counter: Mapped["StoreProductCounter | None"] = relationship(
        "StoreProductCounter",
        back_populates="store",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def public_store_code(self) -> str:
        if not self.registration_number:
            return f"{self.product_code_prefix or 'ECU'}-PENDIENTE"
        return f"{self.product_code_prefix}-{self.registration_number:08d}"

    __table_args__ = (
        CheckConstraint(
            "registration_number > 0",
            name="ck_stores_registration_number_positive",
        ),
        CheckConstraint(
            "product_code_prefix ~ '^[A-Z0-9]{3}$'",
            name="ck_stores_product_code_prefix_format",
        ),
    )


class StoreProductCounter(db.Model):
    __tablename__ = "store_product_counters"

    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_value: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    store: Mapped["Store"] = relationship(
        "Store",
        back_populates="product_code_counter",
    )

    __table_args__ = (
        CheckConstraint(
            "last_value >= 0 AND last_value <= 999999",
            name="ck_store_product_counters_last_value_range",
        ),
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


@event.listens_for(Store, "before_insert")
def _set_store_product_code_prefix(_mapper, _connection, target: Store) -> None:
    if not target.product_code_prefix:
        target.product_code_prefix = _normalize_store_prefix(
            target.name or target.legal_name or target.public_code
        )


def _normalize_store_prefix(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").upper()
    characters = re.findall(r"[A-Z0-9]", ascii_text)
    if not characters:
        return "ECU"
    return ("".join(characters) + "XXX")[:3]
