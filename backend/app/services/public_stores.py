from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Store
from app.models.enums import StoreStatus
from app.services.catalog_listings import PublicListing, load_public_listings
from app.services.catalog_ranking import SURFACE_STORE, rank_listings_v1
from app.services.public_identifiers import format_store_code
from app.services.product_reviews import review_stats_for_store_ids


@dataclass(frozen=True, slots=True)
class PublicStoreView:
    slug: str
    public_code: str
    display_name: str
    logo_initial: str
    is_verified: bool
    rating_average: Decimal | None
    rating_count: int
    active_products_count: int


@dataclass(frozen=True, slots=True)
class StoreRatingSummary:
    average: Decimal | None
    count: int
    source: str
    explanation: str


@dataclass(frozen=True, slots=True)
class StoreInformationView:
    title: str
    public_code: str
    public_address: str
    is_verified: bool


@dataclass(frozen=True, slots=True)
class StoreProductsSummary:
    count: int
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class PublicStoreProductsPage:
    store: PublicStoreView
    rows: tuple[PublicListing, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    previous_page: int | None
    next_page: int | None


@dataclass(frozen=True, slots=True)
class PublicStoreCatalog:
    store: PublicStoreView
    rows: tuple[PublicListing, ...]


STORE_RATING_SOURCE_PRODUCT_REVIEWS = "PUBLISHED_PRODUCT_REVIEWS"
STORE_RATING_SOURCE_NO_RATINGS = "NO_RATINGS"


def normalize_store_page(page: int | str | None) -> int:
    try:
        value = int(page or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def get_public_store_header(
    session: Session,
    *,
    store_slug: str,
) -> PublicStoreView | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None
    product_count = _public_product_count(session, store.id)
    rating = _rating_summary_for_store(session, store.id)
    return _public_store_view(
        store=store,
        product_count=product_count,
        rating=rating,
    )


def get_public_store_products_page(
    session: Session,
    *,
    store_slug: str,
    page: int | str | None,
    page_size: int,
    day: datetime | date | None = None,
) -> PublicStoreProductsPage | None:
    catalog = get_public_store_catalog(
        session,
        store_slug=store_slug,
        day=day,
    )
    if catalog is None:
        return None

    normalized_page = normalize_store_page(page)
    listings = list(catalog.rows)
    total_items = len(listings)
    total_pages = max(1, math.ceil(total_items / page_size))
    normalized_page = min(normalized_page, total_pages)

    start = (normalized_page - 1) * page_size
    rows = listings[start : start + page_size]
    return PublicStoreProductsPage(
        store=catalog.store,
        rows=tuple(rows),
        page=normalized_page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
        previous_page=normalized_page - 1 if normalized_page > 1 else None,
        next_page=normalized_page + 1 if normalized_page < total_pages else None,
    )


def get_public_store_catalog(
    session: Session,
    *,
    store_slug: str,
    day: datetime | date | None = None,
) -> PublicStoreCatalog | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None
    listings = rank_listings_v1(
        load_public_listings(session, store_id=store.id),
        surface=SURFACE_STORE,
        context=store.slug,
        day=day,
    )
    rating = _rating_summary_for_store(session, store.id)
    return PublicStoreCatalog(
        store=_public_store_view(
            store=store,
            product_count=len(listings),
            rating=rating,
        ),
        rows=tuple(listings),
    )


def get_public_store_information(
    session: Session,
    *,
    store_slug: str,
) -> StoreInformationView | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None
    return StoreInformationView(
        title="Información de la tienda",
        public_code=format_store_code(
            store.product_code_prefix,
            store.registration_number,
        ),
        public_address="Dirección comercial no publicada",
        is_verified=store.is_verified,
    )


def get_public_store_rating_summary(
    session: Session,
    *,
    store_slug: str,
) -> StoreRatingSummary | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None
    return _rating_summary_for_store(session, store.id)


def get_public_store_products_summary(
    session: Session,
    *,
    store_slug: str,
) -> StoreProductsSummary | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None
    count = _public_product_count(session, store.id)
    label = f"{count} producto publicado" if count == 1 else f"{count} productos publicados"
    description = (
        "Esta tienda todavía no tiene productos publicados."
        if count == 0
        else f"Esta tienda tiene {label} actualmente."
    )
    return StoreProductsSummary(
        count=count,
        label=label,
        description=description,
    )


def _active_store_by_slug(session: Session, store_slug: str) -> Store | None:
    return session.scalar(
        select(Store).where(
            Store.slug == store_slug,
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
        )
    )


def _public_store_view(
    *,
    store: Store,
    product_count: int,
    rating: StoreRatingSummary,
) -> PublicStoreView:
    return PublicStoreView(
        slug=store.slug,
        public_code=format_store_code(
            store.product_code_prefix,
            store.registration_number,
        ),
        display_name=store.name,
        logo_initial=_logo_initial(store.name),
        is_verified=store.is_verified,
        rating_average=rating.average,
        rating_count=rating.count,
        active_products_count=product_count,
    )


def _rating_summary_for_store(
    session: Session,
    store_id: uuid.UUID,
) -> StoreRatingSummary:
    stats = review_stats_for_store_ids(session, {store_id}).get(store_id)
    if stats is None or stats.count == 0:
        return StoreRatingSummary(
            average=None,
            count=0,
            source=STORE_RATING_SOURCE_NO_RATINGS,
            explanation="Esta tienda todavía no tiene calificaciones publicadas.",
        )
    return StoreRatingSummary(
        average=stats.average,
        count=stats.count,
        source=STORE_RATING_SOURCE_PRODUCT_REVIEWS,
        explanation=(
            "La calificación se basa en las reseñas publicadas y en la "
            "puntuación media de los productos vendidos por esta tienda."
        ),
    )


def _public_product_count(session: Session, store_id: uuid.UUID) -> int:
    return len(load_public_listings(session, store_id=store_id))


def _logo_initial(name: str) -> str:
    return next((char.upper() for char in name if char.isalnum()), "T")
