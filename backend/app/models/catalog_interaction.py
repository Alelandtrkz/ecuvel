from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.extensions import db
from app.models.base import UUIDPrimaryKeyMixin


CATALOG_EVENT_TYPES = (
    "IMPRESSION",
    "CLICK",
    "FAVORITE",
    "ADD_TO_CART",
    "PURCHASE",
    "DELIVERED",
)
CATALOG_EVENT_SURFACES = (
    "HOME",
    "SEARCH",
    "CATEGORY",
    "STORE",
    "FAVORITES",
    "RECOMMENDATIONS",
)


class CatalogInteractionEvent(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "catalog_interaction_events"

    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    surface: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_key: Mapped[str] = mapped_column(String(40), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seller_offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    anonymous_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    ranking_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    served_ranker: Mapped[str | None] = mapped_column(String(40), nullable=True)
    served_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shadow_ranker: Mapped[str | None] = mapped_column(String(40), nullable=True)
    shadow_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shadow_score: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('IMPRESSION','CLICK','FAVORITE','ADD_TO_CART','PURCHASE','DELIVERED')",
            name="catalog_event_type_valid",
        ),
        CheckConstraint(
            "surface IN ('HOME','SEARCH','CATEGORY','STORE','FAVORITES','RECOMMENDATIONS')",
            name="catalog_event_surface_valid",
        ),
        CheckConstraint(
            "length(trim(listing_key)) > 0",
            name=conv("ck_catalog_interaction_events_catalog_event_listing_key_e6bf"),
        ),
        CheckConstraint(
            "served_position IS NULL OR served_position > 0",
            name=conv("ck_catalog_interaction_events_catalog_event_served_posi_2696"),
        ),
        CheckConstraint(
            "shadow_position IS NULL OR shadow_position > 0",
            name=conv("ck_catalog_interaction_events_catalog_event_shadow_posi_3e18"),
        ),
        CheckConstraint(
            "shadow_score IS NULL OR shadow_score BETWEEN -1000000000 AND 1000000000",
            name="catalog_event_shadow_score_finite",
        ),
        Index("ix_catalog_events_occurred_at", "occurred_at"),
        Index(
            "ix_catalog_events_listing_type_occurred",
            "listing_key",
            "event_type",
            "occurred_at",
        ),
        Index("ix_catalog_events_ranking_request", "ranking_request_id"),
        Index(
            "uq_catalog_events_impression_request_listing",
            "ranking_request_id",
            "listing_key",
            unique=True,
            postgresql_where=text("event_type = 'IMPRESSION'"),
        ),
    )
