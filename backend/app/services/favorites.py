from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Favorite, Product, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
from app.services.inventory import get_sellable_quantities_for_offers


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
    price: Decimal | None
    compare_at_price: Decimal | None
    currency: str | None
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


def _canonical_offers_subquery():
    return (
        select(
            Product.id.label("product_id"),
            SellerOffer.id.label("offer_id"),
            SellerOffer.currency.label("currency"),
            SellerOffer.price.label("price"),
            SellerOffer.compare_at_price.label("compare_at_price"),
            func.row_number()
            .over(
                partition_by=Product.id,
                order_by=(SellerOffer.price, SellerOffer.id),
            )
            .label("offer_rank"),
        )
        .select_from(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(
            SellerOffer.status == OfferStatus.ACTIVE,
            SellerOffer.currency == "USD",
            ProductVariant.is_active.is_(True),
            Product.is_active.is_(True),
            Category.is_active.is_(True),
            Store.status == StoreStatus.ACTIVE,
        )
        .subquery()
    )


def _public_product_by_slug(session: Session, product_slug: str) -> Product | None:
    canonical_offers = _canonical_offers_subquery()
    return session.scalar(
        select(Product)
        .join(
            canonical_offers,
            canonical_offers.c.product_id == Product.id,
        )
        .where(
            Product.slug == product_slug,
            canonical_offers.c.offer_rank == 1,
        )
    )


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

    canonical_offers = _canonical_offers_subquery()
    rows = session.execute(
        select(
            Favorite.id.label("favorite_id"),
            Favorite.created_at.label("created_at"),
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            Product.is_active.label("product_is_active"),
            Category.is_active.label("category_is_active"),
            canonical_offers.c.offer_id,
            canonical_offers.c.price,
            canonical_offers.c.compare_at_price,
            canonical_offers.c.currency,
        )
        .select_from(Favorite)
        .join(Product, Product.id == Favorite.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(
            canonical_offers,
            (canonical_offers.c.product_id == Product.id)
            & (canonical_offers.c.offer_rank == 1),
        )
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    offer_ids = {row.offer_id for row in rows if row.offer_id is not None}
    availability = get_sellable_quantities_for_offers(
        session=session,
        offer_ids=offer_ids,
    )
    items = tuple(
        FavoriteListItem(
            favorite_id=row.favorite_id,
            product_id=row.product_id,
            product_slug=row.product_slug,
            product_title=row.product_title,
            product_is_active=row.product_is_active,
            category_is_active=row.category_is_active,
            created_at=row.created_at,
            offer_id=row.offer_id,
            price=row.price,
            compare_at_price=row.compare_at_price,
            currency=row.currency,
            available_quantity=(
                max(0, availability.get(row.offer_id, 0))
                if row.offer_id is not None
                else 0
            ),
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
