"""Fail-closed Adult access; activation is separate from administrative approval."""
from __future__ import annotations

import uuid

from flask import current_app, has_app_context, has_request_context, request
from flask_login import current_user
from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import aliased

from app.models import Category, Product, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
from app.services.age_eligibility import is_at_least_18


ADULT_CATEGORY_CODES = frozenset({
    "ADULT", "ADULT_CONDOMS", "ADULT_LUBRICANTS", "ADULT_TOYS", "ADULT_ANAL",
    "ADULT_BDSM", "ADULT_LINGERIE", "ADULT_INTIMATE_CARE", "ADULT_GAMES",
})
ADULT_FAMILY_CODES = ADULT_CATEGORY_CODES - {"ADULT"}


def adult_category_expression(category=Category):
    parent_ids = select(Category.id).where(Category.code.in_(ADULT_CATEGORY_CODES)).correlate(None)
    return or_(category.code.in_(ADULT_CATEGORY_CODES), and_(category.parent_id.is_not(None), category.parent_id.in_(parent_ids)))


def is_adult_category(category) -> bool:
    if category is None:
        return False
    if category.code in ADULT_CATEGORY_CODES:
        return True
    parent = getattr(category, "parent", None)
    return parent is not None and parent.code in ADULT_CATEGORY_CODES


def adult_partner_enabled() -> bool:
    return has_app_context() and current_app.config.get("ADULT_PARTNER_ENABLED") is True


def adult_public_eligible(user=None) -> bool:
    if not has_app_context() or current_app.config.get("ADULT_PUBLIC_ENABLED") is not True:
        return False
    viewer = user if user is not None else (current_user if has_request_context() else None)
    eligible = bool(
        viewer is not None and viewer.is_authenticated and viewer.is_active
        and is_at_least_18(viewer.birth_date)
    )
    if eligible and has_request_context():
        request.environ["ecuvel.adult_sensitive_response"] = True
    return eligible


def _released_product_ids() -> tuple[uuid.UUID, ...]:
    if not has_app_context():
        return ()
    raw = current_app.config.get("ADULT_RELEASED_PRODUCT_IDS", "")
    values = raw.split(",") if isinstance(raw, str) else raw
    if not isinstance(values, (tuple, list, set, frozenset)):
        return ()
    try:
        return tuple(uuid.UUID(str(value).strip()) for value in values if str(value).strip())
    except (TypeError, ValueError):
        return ()


def adult_public_category_clause(category=Category, *, user=None):
    adult = adult_category_expression(category)
    if not adult_public_eligible(user):
        return ~adult
    active_roots = select(Category.id).where(Category.code == "ADULT", Category.is_active.is_(True)).correlate(None)
    return or_(~adult, and_(category.is_active.is_(True), or_(
        and_(category.code == "ADULT", category.parent_id.is_(None)),
        and_(category.code.in_(ADULT_FAMILY_CODES), category.parent_id.in_(active_roots)),
    )))


def adult_public_product_clause(product=Product, category=Category, *, user=None):
    adult = adult_category_expression(category)
    if not adult_public_eligible(user):
        return ~adult
    # Explicit ECUVEL release follows individual moderation/regulatory review.
    # Empty/malformed configuration releases no Adult products.
    active_roots = select(Category.id).where(Category.code == "ADULT", Category.is_active.is_(True)).correlate(None)
    offer, variant, store = aliased(SellerOffer), aliased(ProductVariant), aliased(Store)
    available_offer = select(offer.id).join(variant, variant.id == offer.variant_id).join(store, store.id == offer.store_id).where(
        variant.product_id == product.id, variant.is_active.is_(True),
        offer.status == OfferStatus.ACTIVE, offer.currency == "USD",
        store.status == StoreStatus.ACTIVE, store.is_verified.is_(True),
    ).exists()
    return or_(~adult, and_(
        product.id.in_(_released_product_ids()), category.is_active.is_(True),
        product.is_active.is_(True), category.code.in_(ADULT_FAMILY_CODES),
        category.parent_id.in_(active_roots), available_offer,
    ))


def adult_partner_category_clause(category=Category):
    return ~adult_category_expression(category) if not adult_partner_enabled() else ~false()


def adult_product_visible(session, product_id, *, user=None) -> bool:
    return session.scalar(select(Product.id).join(Category).where(
        Product.id == product_id,
        adult_public_product_clause(user=user),
    )) is not None


def product_is_adult(session, product_id) -> bool:
    return session.scalar(select(Product.id).join(Category).where(
        Product.id == product_id, adult_category_expression(),
    )) is not None
