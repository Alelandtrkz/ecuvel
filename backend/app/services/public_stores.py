from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Product, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
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
class StoreProductRow:
    product_id: uuid.UUID
    product_slug: str
    product_title: str
    variant_id: uuid.UUID
    variant_title: str | None
    seller_sku: str
    offer_id: uuid.UUID
    currency: str
    price: Decimal
    compare_at_price: Decimal | None
    store_id: uuid.UUID
    store_name: str
    store_slug: str
    store_is_verified: bool


@dataclass(frozen=True, slots=True)
class PublicStoreProductsPage:
    store: PublicStoreView
    rows: tuple[StoreProductRow, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    previous_page: int | None
    next_page: int | None


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
) -> PublicStoreProductsPage | None:
    store = _active_store_by_slug(session, store_slug)
    if store is None:
        return None

    normalized_page = normalize_store_page(page)
    products_subquery = _store_canonical_products_subquery(store.id)
    total_items = session.scalar(
        select(func.count()).select_from(products_subquery).where(
            products_subquery.c.offer_rank == 1
        )
    ) or 0
    total_pages = max(1, math.ceil(total_items / page_size))
    normalized_page = min(normalized_page, total_pages)

    rows = session.execute(
        select(products_subquery)
        .where(products_subquery.c.offer_rank == 1)
        .order_by(
            products_subquery.c.product_title,
            products_subquery.c.product_id,
        )
        .offset((normalized_page - 1) * page_size)
        .limit(page_size)
    ).all()
    rating = _rating_summary_for_store(session, store.id)
    return PublicStoreProductsPage(
        store=_public_store_view(
            store=store,
            product_count=total_items,
            rating=rating,
        ),
        rows=tuple(_row_from_sql(row) for row in rows),
        page=normalized_page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
        previous_page=normalized_page - 1 if normalized_page > 1 else None,
        next_page=normalized_page + 1 if normalized_page < total_pages else None,
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
    products_subquery = _store_canonical_products_subquery(store_id)
    return session.scalar(
        select(func.count()).select_from(products_subquery).where(
            products_subquery.c.offer_rank == 1
        )
    ) or 0


def _store_canonical_products_subquery(store_id: uuid.UUID):
    return (
        select(
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            ProductVariant.id.label("variant_id"),
            ProductVariant.title.label("variant_title"),
            SellerOffer.seller_sku.label("seller_sku"),
            SellerOffer.id.label("offer_id"),
            SellerOffer.currency.label("currency"),
            SellerOffer.price.label("price"),
            SellerOffer.compare_at_price.label("compare_at_price"),
            Store.id.label("store_id"),
            Store.name.label("store_name"),
            Store.slug.label("store_slug"),
            Store.is_verified.label("store_is_verified"),
            func.row_number()
            .over(
                partition_by=Product.id,
                order_by=(SellerOffer.price, SellerOffer.id),
            )
            .label("offer_rank"),
        )
        .select_from(SellerOffer)
        .join(Store, Store.id == SellerOffer.store_id)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(
            Store.id == store_id,
            Store.status == StoreStatus.ACTIVE,
            SellerOffer.status == OfferStatus.ACTIVE,
            ProductVariant.is_active.is_(True),
            Product.is_active.is_(True),
            Category.is_active.is_(True),
        )
        .subquery()
    )


def _row_from_sql(row: Any) -> StoreProductRow:
    return StoreProductRow(
        product_id=row.product_id,
        product_slug=row.product_slug,
        product_title=row.product_title,
        variant_id=row.variant_id,
        variant_title=row.variant_title,
        seller_sku=row.seller_sku,
        offer_id=row.offer_id,
        currency=row.currency,
        price=row.price,
        compare_at_price=row.compare_at_price,
        store_id=row.store_id,
        store_name=row.store_name,
        store_slug=row.store_slug,
        store_is_verified=row.store_is_verified,
    )


def _logo_initial(name: str) -> str:
    return next((char.upper() for char in name if char.isalnum()), "T")
