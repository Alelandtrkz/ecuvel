from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Favorite, Product
from app.services.catalog_listings import PublicListing, load_public_listings


class FavoriteServiceError(Exception):
    """Base error for customer favorites."""


class FavoriteProductNotFoundError(FavoriteServiceError):
    """The product cannot be favorited through the public catalog."""


@dataclass(frozen=True, slots=True)
class FavoriteMutationResult:
    product_id: uuid.UUID
    product_slug: str
    is_favorite: bool
    favorite_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class FavoriteListItem:
    favorite_id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_title: str
    product_is_active: bool
    category_is_active: bool
    created_at: Any
    offer_id: uuid.UUID | None
    variant_id: uuid.UUID | None
    catalog_sku: str | None
    listing_key: str | None
    listing_label: str | None
    price: Decimal | None
    compare_at_price: Decimal | None
    currency: str | None
    preparation_time_days: int | None
    available_quantity: int

    @property
    def has_visible_offer(self) -> bool:
        return self.offer_id is not None and self.price is not None

    @property
    def is_catalog_visible(self) -> bool:
        return (
            self.product_is_active
            and self.category_is_active
            and self.has_visible_offer
        )

    @property
    def is_available(self) -> bool:
        return self.is_catalog_visible and self.available_quantity > 0


@dataclass(frozen=True, slots=True)
class FavoriteListPage:
    items: tuple[FavoriteListItem, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def normalize_favorites_page(value: str | None) -> int:
    try:
        page = int(value or "1")
    except ValueError:
        return 1
    return max(1, page)


def _public_product_by_slug(session: Session, product_slug: str) -> Product | None:
    product = session.scalar(
        select(Product).where(Product.slug == product_slug)
    )
    if product is None:
        return None
    return product if load_public_listings(session, product_id=product.id) else None


def favorite_count_for_user(session: Session, user_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count(Favorite.id)).where(Favorite.user_id == user_id)
        )
        or 0
    )


def favorite_product_ids_for_user(
    session: Session,
    user_id: uuid.UUID | None,
    product_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    if user_id is None or not product_ids:
        return set()
    return set(
        session.scalars(
            select(Favorite.product_id).where(
                Favorite.user_id == user_id,
                Favorite.product_id.in_(product_ids),
            )
        ).all()
    )


def add_favorite_by_slug(
    session: Session,
    *,
    user_id: uuid.UUID,
    product_slug: str,
) -> FavoriteMutationResult:
    product = _public_product_by_slug(session, product_slug)
    if product is None:
        raise FavoriteProductNotFoundError("Producto no disponible.")

    existing = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user_id,
            Favorite.product_id == product.id,
        )
    )
    if existing is not None:
        return FavoriteMutationResult(
            product_id=product.id,
            product_slug=product.slug,
            is_favorite=True,
            favorite_count=favorite_count_for_user(session, user_id),
            replayed=True,
        )

    favorite = Favorite(user_id=user_id, product_id=product.id)
    session.add(favorite)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(Favorite).where(
                Favorite.user_id == user_id,
                Favorite.product_id == product.id,
            )
        )
        if existing is None:
            raise
        replayed = True
    else:
        replayed = False

    return FavoriteMutationResult(
        product_id=product.id,
        product_slug=product.slug,
        is_favorite=True,
        favorite_count=favorite_count_for_user(session, user_id),
        replayed=replayed,
    )


def remove_favorite_by_slug(
    session: Session,
    *,
    user_id: uuid.UUID,
    product_slug: str,
) -> FavoriteMutationResult:
    product = session.scalar(select(Product).where(Product.slug == product_slug))
    if product is None:
        raise FavoriteProductNotFoundError("Producto no encontrado.")

    existing = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user_id,
            Favorite.product_id == product.id,
        )
    )
    replayed = existing is None
    if existing is not None:
        session.delete(existing)
        session.flush()

    return FavoriteMutationResult(
        product_id=product.id,
        product_slug=product.slug,
        is_favorite=False,
        favorite_count=favorite_count_for_user(session, user_id),
        replayed=replayed,
    )


def get_favorites_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    page: int,
    page_size: int,
) -> FavoriteListPage:
    page = max(1, page)
    page_size = max(1, page_size)
    total_items = favorite_count_for_user(session, user_id)
    total_pages = max(1, ceil(total_items / page_size))
    page = min(page, total_pages)

    rows = session.execute(
        select(
            Favorite.id.label("favorite_id"),
            Favorite.created_at.label("created_at"),
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            Product.is_active.label("product_is_active"),
            Category.is_active.label("category_is_active"),
        )
        .select_from(Favorite)
        .join(Product, Product.id == Favorite.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    listings_by_product: dict[uuid.UUID, list[PublicListing]] = {}
    for listing in load_public_listings(
        session,
        product_ids={row.product_id for row in rows},
    ):
        listings_by_product.setdefault(listing.product_id, []).append(listing)
    items = tuple(
        _favorite_item_from_row(
            row,
            _favorite_representative(listings_by_product.get(row.product_id, [])),
        )
        for row in rows
    )
    return FavoriteListPage(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


def _favorite_representative(
    listings: list[PublicListing],
) -> PublicListing | None:
    if not listings:
        return None
    available = [listing for listing in listings if listing.is_available]
    pool = available or listings
    default_key = str(
        (listings[0].variant_configuration or {}).get("default_combination_key")
        or ""
    )
    if default_key:
        default_groups = [
            listing
            for listing in pool
            if any(member.combination_key == default_key for member in listing.members)
        ]
        if default_groups:
            return min(default_groups, key=lambda listing: listing.listing_key)
    return min(pool, key=lambda listing: listing.listing_key)


def _favorite_item_from_row(row, listing: PublicListing | None) -> FavoriteListItem:
    return FavoriteListItem(
        favorite_id=row.favorite_id,
        product_id=row.product_id,
        product_slug=row.product_slug,
        product_title=row.product_title,
        product_is_active=row.product_is_active,
        category_is_active=row.category_is_active,
        created_at=row.created_at,
        offer_id=listing.offer_id if listing else None,
        variant_id=listing.variant_id if listing else None,
        catalog_sku=listing.catalog_sku if listing else None,
        listing_key=listing.listing_key if listing else None,
        listing_label=listing.listing_label if listing else None,
        price=listing.price if listing else None,
        compare_at_price=listing.compare_at_price if listing else None,
        currency=listing.currency if listing else None,
        preparation_time_days=(listing.preparation_time_days if listing else None),
        available_quantity=(listing.available_quantity if listing else 0),
    )
