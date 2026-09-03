from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Product, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
from app.services.inventory import get_sellable_quantities_for_offers


LISTING_IDENTITY_VERSION = 1
PUBLIC_CATALOG_CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class ListingIdentity:
    listing_key: str
    label: str | None
    values: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PublicListingMember:
    product_id: uuid.UUID
    product_slug: str
    product_title: str
    product_description: str | None
    product_brand: str | None
    product_model_number: str | None
    variant_configuration: dict[str, Any]
    category_id: uuid.UUID
    category_code: str
    category_name: str
    category_slug: str
    variant_id: uuid.UUID
    variant_title: str | None
    catalog_sku: str
    manufacturer_barcode: str | None
    variant_attributes: dict[str, Any]
    combination_key: str | None
    weight_grams: int | None
    length_mm: int | None
    width_mm: int | None
    height_mm: int | None
    offer_id: uuid.UUID
    seller_sku: str
    currency: str
    price: Decimal
    compare_at_price: Decimal | None
    preparation_time_days: int | None
    offer_status: OfferStatus
    store_id: uuid.UUID
    store_name: str
    store_slug: str
    store_is_verified: bool
    available_quantity: int = 0


@dataclass(frozen=True, slots=True)
class PublicListing:
    listing_key: str
    listing_label: str | None
    product_id: uuid.UUID
    product_slug: str
    product_title: str
    product_description: str | None
    product_brand: str | None
    product_model_number: str | None
    variant_configuration: dict[str, Any]
    category_id: uuid.UUID
    category_code: str
    category_name: str
    category_slug: str
    variant_id: uuid.UUID
    variant_title: str | None
    catalog_sku: str
    manufacturer_barcode: str | None
    variant_attributes: dict[str, Any]
    combination_key: str | None
    weight_grams: int | None
    length_mm: int | None
    width_mm: int | None
    height_mm: int | None
    offer_id: uuid.UUID
    seller_sku: str
    currency: str
    price: Decimal
    compare_at_price: Decimal | None
    preparation_time_days: int | None
    offer_status: OfferStatus
    store_id: uuid.UUID
    store_name: str
    store_slug: str
    store_is_verified: bool
    available_quantity: int
    is_available: bool
    members: tuple[PublicListingMember, ...]
    search_values: tuple[str, ...]


def build_listing_identity(
    *,
    product_id: uuid.UUID,
    store_id: uuid.UUID,
    listing_axis_keys: Iterable[str],
    variant_options: Mapping[str, Any],
    variant_attributes: Mapping[str, Any] | None = None,
    axis_definitions: Mapping[str, Mapping[str, Any]] | None = None,
) -> ListingIdentity:
    """Build a stable, non-financial identity for one public listing group."""
    attributes = variant_attributes or {}
    definitions = axis_definitions or {}
    values: list[tuple[str, str]] = []
    labels: list[str] = []
    for key in listing_axis_keys:
        normalized_key = str(key)
        raw_value = variant_options.get(normalized_key)
        if raw_value is None:
            raw_value = attributes.get(normalized_key, "")
        value_key = str(raw_value or "")
        values.append((normalized_key, value_key))

        definition = definitions.get(normalized_key) or {}
        display_value = str(attributes.get(normalized_key) or value_key).strip()
        if display_value:
            unit = str(definition.get("unit") or "").strip()
            labels.append(f"{display_value} {unit}" if unit else display_value)

    canonical = json.dumps(
        {
            "version": LISTING_IDENTITY_VERSION,
            "product_id": str(product_id),
            "store_id": str(store_id),
            "values": values,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return ListingIdentity(
        listing_key=f"lst_{digest}",
        label=" · ".join(labels) or None,
        values=tuple(values),
    )


def public_offer_candidates_statement(
    *,
    product_id: uuid.UUID | None = None,
    product_ids: set[uuid.UUID] | None = None,
    category_slug: str | None = None,
    category_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    store_slug: str | None = None,
    exclude_product_ids: set[uuid.UUID] | None = None,
):
    statement = (
        select(
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            Product.description.label("product_description"),
            Product.brand.label("product_brand"),
            Product.model_number.label("product_model_number"),
            Product.variant_configuration.label("variant_configuration"),
            Category.id.label("category_id"),
            Category.code.label("category_code"),
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            ProductVariant.id.label("variant_id"),
            ProductVariant.title.label("variant_title"),
            ProductVariant.catalog_sku.label("catalog_sku"),
            ProductVariant.manufacturer_barcode.label("manufacturer_barcode"),
            ProductVariant.attributes.label("variant_attributes"),
            ProductVariant.combination_key.label("combination_key"),
            ProductVariant.weight_grams.label("weight_grams"),
            ProductVariant.length_mm.label("length_mm"),
            ProductVariant.width_mm.label("width_mm"),
            ProductVariant.height_mm.label("height_mm"),
            SellerOffer.id.label("offer_id"),
            SellerOffer.seller_sku.label("seller_sku"),
            SellerOffer.currency.label("currency"),
            SellerOffer.price.label("price"),
            SellerOffer.compare_at_price.label("compare_at_price"),
            SellerOffer.preparation_time_days.label("preparation_time_days"),
            SellerOffer.status.label("offer_status"),
            Store.id.label("store_id"),
            Store.name.label("store_name"),
            Store.slug.label("store_slug"),
            Store.is_verified.label("store_is_verified"),
        )
        .select_from(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(
            SellerOffer.status == OfferStatus.ACTIVE,
            SellerOffer.currency == PUBLIC_CATALOG_CURRENCY,
            ProductVariant.is_active.is_(True),
            Product.is_active.is_(True),
            Category.is_active.is_(True),
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
        )
    )
    if product_id is not None:
        statement = statement.where(Product.id == product_id)
    if product_ids:
        statement = statement.where(Product.id.in_(product_ids))
    if category_slug:
        statement = statement.where(Category.slug == category_slug)
    if category_id is not None:
        statement = statement.where(Category.id == category_id)
    if store_id is not None:
        statement = statement.where(Store.id == store_id)
    if store_slug:
        statement = statement.where(Store.slug == store_slug)
    if exclude_product_ids:
        statement = statement.where(Product.id.not_in(exclude_product_ids))
    return statement.order_by(
        Product.id,
        Store.id,
        ProductVariant.combination_key,
        ProductVariant.catalog_sku,
        SellerOffer.id,
    )


def load_public_listings(
    session: Session,
    *,
    product_id: uuid.UUID | None = None,
    product_ids: set[uuid.UUID] | None = None,
    category_slug: str | None = None,
    category_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    store_slug: str | None = None,
    exclude_product_ids: set[uuid.UUID] | None = None,
) -> list[PublicListing]:
    rows = session.execute(
        public_offer_candidates_statement(
            product_id=product_id,
            product_ids=product_ids,
            category_slug=category_slug,
            category_id=category_id,
            store_id=store_id,
            store_slug=store_slug,
            exclude_product_ids=exclude_product_ids,
        )
    ).all()
    quantities = get_sellable_quantities_for_offers(
        session=session,
        offer_ids={row.offer_id for row in rows},
    )
    members = [
        _member_from_row(row, max(0, quantities.get(row.offer_id, 0)))
        for row in rows
    ]
    return group_public_listing_members(members)


def group_public_listing_members(
    members: Iterable[PublicListingMember],
) -> list[PublicListing]:
    grouped: dict[str, tuple[ListingIdentity, list[PublicListingMember]]] = {}
    for member in members:
        configuration = member.variant_configuration or {}
        raw_keys = configuration.get("listing_axis_keys")
        # Missing metadata is a deliberate legacy fallback to one Product+Store listing.
        listing_axis_keys = (
            tuple(str(key) for key in raw_keys)
            if isinstance(raw_keys, list)
            else ()
        )
        axis_definitions = {
            str(axis.get("key")): axis
            for axis in configuration.get("axes") or ()
            if isinstance(axis, dict) and axis.get("key")
        }
        attributes = member.variant_attributes or {}
        options = attributes.get("variant_options")
        if not isinstance(options, dict):
            options = {}
        identity = build_listing_identity(
            product_id=member.product_id,
            store_id=member.store_id,
            listing_axis_keys=listing_axis_keys,
            variant_options=options,
            variant_attributes=attributes,
            axis_definitions=axis_definitions,
        )
        entry = grouped.setdefault(identity.listing_key, (identity, []))
        entry[1].append(member)

    listings = [
        _listing_from_group(identity, group_members)
        for identity, group_members in grouped.values()
    ]
    return sorted(listings, key=lambda listing: listing.listing_key)


def _member_from_row(row: Any, available_quantity: int) -> PublicListingMember:
    return PublicListingMember(
        product_id=row.product_id,
        product_slug=row.product_slug,
        product_title=row.product_title,
        product_description=row.product_description,
        product_brand=row.product_brand,
        product_model_number=row.product_model_number,
        variant_configuration=dict(row.variant_configuration or {}),
        category_id=row.category_id,
        category_code=row.category_code,
        category_name=row.category_name,
        category_slug=row.category_slug,
        variant_id=row.variant_id,
        variant_title=row.variant_title,
        catalog_sku=row.catalog_sku,
        manufacturer_barcode=row.manufacturer_barcode,
        variant_attributes=dict(row.variant_attributes or {}),
        combination_key=row.combination_key,
        weight_grams=row.weight_grams,
        length_mm=row.length_mm,
        width_mm=row.width_mm,
        height_mm=row.height_mm,
        offer_id=row.offer_id,
        seller_sku=row.seller_sku,
        currency=row.currency,
        price=row.price,
        compare_at_price=row.compare_at_price,
        preparation_time_days=row.preparation_time_days,
        offer_status=row.offer_status,
        store_id=row.store_id,
        store_name=row.store_name,
        store_slug=row.store_slug,
        store_is_verified=row.store_is_verified,
        available_quantity=available_quantity,
    )


def _stable_member_key(member: PublicListingMember) -> tuple[str, str, str]:
    return (
        member.combination_key or "",
        member.catalog_sku,
        str(member.offer_id),
    )


def _representative_member(
    members: list[PublicListingMember],
) -> PublicListingMember:
    configuration = members[0].variant_configuration or {}
    default_key = str(configuration.get("default_combination_key") or "")
    available = [member for member in members if member.available_quantity > 0]
    pool = available or members
    if default_key:
        default = [member for member in pool if member.combination_key == default_key]
        if default:
            return min(default, key=_stable_member_key)
    return min(pool, key=_stable_member_key)


def _listing_from_group(
    identity: ListingIdentity,
    members: list[PublicListingMember],
) -> PublicListing:
    representative = _representative_member(members)
    ordered_members = tuple(sorted(members, key=_stable_member_key))
    search_values: list[str] = []
    for member in ordered_members:
        search_values.extend(
            str(value)
            for value in (
                member.product_title,
                member.product_brand,
                member.product_model_number,
                member.variant_title,
                member.catalog_sku,
                member.seller_sku,
                identity.label,
                *member.variant_attributes.values(),
            )
            if value not in (None, "")
        )
    return PublicListing(
        listing_key=identity.listing_key,
        listing_label=identity.label,
        product_id=representative.product_id,
        product_slug=representative.product_slug,
        product_title=representative.product_title,
        product_description=representative.product_description,
        product_brand=representative.product_brand,
        product_model_number=representative.product_model_number,
        variant_configuration=representative.variant_configuration,
        category_id=representative.category_id,
        category_code=representative.category_code,
        category_name=representative.category_name,
        category_slug=representative.category_slug,
        variant_id=representative.variant_id,
        variant_title=representative.variant_title,
        catalog_sku=representative.catalog_sku,
        manufacturer_barcode=representative.manufacturer_barcode,
        variant_attributes=representative.variant_attributes,
        combination_key=representative.combination_key,
        weight_grams=representative.weight_grams,
        length_mm=representative.length_mm,
        width_mm=representative.width_mm,
        height_mm=representative.height_mm,
        offer_id=representative.offer_id,
        seller_sku=representative.seller_sku,
        currency=representative.currency,
        price=representative.price,
        compare_at_price=representative.compare_at_price,
        preparation_time_days=representative.preparation_time_days,
        offer_status=representative.offer_status,
        store_id=representative.store_id,
        store_name=representative.store_name,
        store_slug=representative.store_slug,
        store_is_verified=representative.store_is_verified,
        available_quantity=representative.available_quantity,
        is_available=any(member.available_quantity > 0 for member in members),
        members=ordered_members,
        search_values=tuple(dict.fromkeys(search_values)),
    )
