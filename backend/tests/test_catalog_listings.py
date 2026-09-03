from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Category, InventoryBalance, Product, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
from app.services.catalog_listings import build_listing_identity, load_public_listings
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _entities(session, base):
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    store = session.get(Store, base.store_id)
    category = session.get(Category, product.category_id)
    return product, variant, offer, store, category


def _add_variant(
    session,
    base,
    product,
    store,
    *,
    sku,
    combination_key,
    attributes,
    price,
    stock,
):
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=sku,
        title=" / ".join(str(value) for value in attributes.values() if not isinstance(value, dict)),
        combination_key=combination_key,
        attributes=attributes,
        is_active=True,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=store.id,
        variant_id=variant.id,
        seller_sku=f"SELL-{sku}",
        currency="USD",
        price=Decimal(price),
        commission_rate=Decimal("0.00"),
        preparation_time_days=2,
        status=OfferStatus.ACTIVE,
    )
    session.add(offer)
    session.flush()
    session.add(
        InventoryBalance(
            offer_id=offer.id,
            location_id=base.storage_location_id,
            on_hand_quantity=stock,
            reserved_quantity=0,
            blocked_quantity=0,
        )
    )
    session.flush()
    return variant, offer


def test_listing_identity_is_stable_and_ignores_option_dict_order():
    product_id = uuid.uuid4()
    store_id = uuid.uuid4()
    first = build_listing_identity(
        product_id=product_id,
        store_id=store_id,
        listing_axis_keys=["color", "ram"],
        variant_options={"ram": "16", "color": "black"},
        variant_attributes={"color": "Negro", "ram": "16"},
        axis_definitions={"ram": {"unit": "GB"}},
    )
    second = build_listing_identity(
        product_id=product_id,
        store_id=store_id,
        listing_axis_keys=["color", "ram"],
        variant_options={"color": "black", "ram": "16"},
        variant_attributes={"ram": "16", "color": "Negro"},
        axis_definitions={"ram": {"unit": "GB"}},
    )

    assert first == second
    assert first.listing_key.startswith("lst_")
    assert first.label == "Negro · 16 GB"


def test_phone_listing_axes_create_three_exact_public_listings(session):
    base = create_catalog_and_stock(session, stock=3)
    product, first_variant, first_offer, store, _category = _entities(session, base)
    product.variant_configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "listing_axis_keys": ["color", "ram", "storage"],
        "default_combination_key": "color=black|ram=8|storage=256",
        "axes": [
            {"key": "color", "label": "Color", "unit": "", "is_listing_axis": True},
            {"key": "ram", "label": "RAM", "unit": "GB", "is_listing_axis": True},
            {"key": "storage", "label": "Almacenamiento", "unit": "GB", "is_listing_axis": True},
        ],
    }
    first_variant.combination_key = "color=black|ram=8|storage=256"
    first_variant.attributes = {
        "color": "Negro", "ram": "8", "storage": "256",
        "variant_options": {"color": "black", "ram": "8", "storage": "256"},
    }
    first_offer.preparation_time_days = 1
    _add_variant(
        session, base, product, store,
        sku="PHONE-BLACK-16-256",
        combination_key="color=black|ram=16|storage=256",
        attributes={
            "color": "Negro", "ram": "16", "storage": "256",
            "variant_options": {"color": "black", "ram": "16", "storage": "256"},
        },
        price="1200.00", stock=2,
    )
    _add_variant(
        session, base, product, store,
        sku="PHONE-BLUE-16-512",
        combination_key="color=blue|ram=16|storage=512",
        attributes={
            "color": "Azul", "ram": "16", "storage": "512",
            "variant_options": {"color": "blue", "ram": "16", "storage": "512"},
        },
        price="1600.00", stock=1,
    )

    listings = load_public_listings(session, product_id=product.id)

    assert len(listings) == 3
    assert {listing.listing_label for listing in listings} == {
        "Negro · 8 GB · 256 GB",
        "Negro · 16 GB · 256 GB",
        "Azul · 16 GB · 512 GB",
    }
    assert {len(listing.members) for listing in listings} == {1}
    assert all(listing.catalog_sku == listing.members[0].catalog_sku for listing in listings)


def test_shoe_size_is_detail_only_and_available_member_represents_color(session):
    base = create_catalog_and_stock(session, stock=0)
    product, first_variant, first_offer, store, _category = _entities(session, base)
    product.variant_configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "listing_axis_keys": ["color"],
        "default_combination_key": "color=white|size=36",
        "axes": [
            {"key": "color", "label": "Color", "unit": "", "is_listing_axis": True},
            {"key": "size", "label": "Talla", "unit": "", "is_listing_axis": False},
        ],
    }
    first_variant.combination_key = "color=white|size=36"
    first_variant.attributes = {
        "color": "Blanco", "size": "36",
        "variant_options": {"color": "white", "size": "36"},
    }
    first_offer.price = Decimal("100.00")
    white_38, white_38_offer = _add_variant(
        session, base, product, store,
        sku="SHOE-WHITE-38", combination_key="color=white|size=38",
        attributes={
            "color": "Blanco", "size": "38",
            "variant_options": {"color": "white", "size": "38"},
        },
        price="105.00", stock=2,
    )
    _add_variant(
        session, base, product, store,
        sku="SHOE-BLACK-36", combination_key="color=black|size=36",
        attributes={
            "color": "Negro", "size": "36",
            "variant_options": {"color": "black", "size": "36"},
        },
        price="110.00", stock=1,
    )

    listings = load_public_listings(session, product_id=product.id)
    white = next(listing for listing in listings if listing.listing_label == "Blanco")

    assert len(listings) == 2
    assert len(white.members) == 2
    assert white.is_available is True
    assert white.variant_id == white_38.id
    assert white.offer_id == white_38_offer.id
    assert white.price == Decimal("105.00")
    assert "38" not in white.listing_label


def test_legacy_product_without_listing_snapshot_collapses_to_one_listing(session):
    base = create_catalog_and_stock(session, stock=1)
    product, _variant, _offer, store, _category = _entities(session, base)
    product.variant_configuration = {"version": 4, "enabled": True, "mode": "family"}
    _add_variant(
        session, base, product, store,
        sku="LEGACY-SECOND", combination_key="color=blue",
        attributes={"color": "Azul"}, price="500.00", stock=1,
    )

    listings = load_public_listings(session, product_id=product.id)

    assert len(listings) == 1
    assert listings[0].listing_label is None
    assert len(listings[0].members) == 2


def test_card_deep_link_opens_same_exact_variant_price_and_eta(client, session):
    base = create_catalog_and_stock(session, stock=2)
    product, variant, offer, _store, _category = _entities(session, base)
    offer.preparation_time_days = 1
    session.commit()

    home = client.get("/")
    body = home.get_data(as_text=True)
    expected_url = f"/productos/{product.slug}?variant={variant.catalog_sku}"

    assert home.status_code == 200
    assert expected_url in body
    assert "$10.00" in body
    assert "Mañana" in body
    detail = client.get(expected_url)
    detail_body = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert variant.catalog_sku not in detail_body
    assert detail.request.query_string.decode() == f"variant={variant.catalog_sku}"
    assert "$10.00" in detail_body
    assert "Entrega estimada mañana" in detail_body


def test_sold_out_listing_remains_visible_with_disabled_blue_button(client, session):
    base = create_catalog_and_stock(session, stock=0)
    product, _variant, _offer, _store, _category = _entities(session, base)
    session.commit()

    body = client.get("/").get_data(as_text=True)

    assert product.title in body
    assert 'class="product-card__cart-button" type="button" disabled' in body
    assert 'aria-disabled="true"' in body
    assert "Agotado" in body
    assert 'class="product-card__cart-form"' not in body


@pytest.mark.parametrize(
    "mutation",
    [
        lambda _p, _v, o, _s, _c: setattr(o, "status", OfferStatus.PAUSED),
        lambda _p, v, _o, _s, _c: setattr(v, "is_active", False),
        lambda p, _v, _o, _s, _c: setattr(p, "is_active", False),
        lambda _p, _v, _o, _s, c: setattr(c, "is_active", False),
        lambda _p, _v, _o, s, _c: setattr(s, "status", StoreStatus.SUSPENDED),
        lambda _p, _v, _o, s, _c: setattr(s, "is_verified", False),
        lambda _p, _v, o, _s, _c: setattr(o, "currency", "EUR"),
    ],
)
def test_public_eligibility_rejects_each_ineligible_dimension(session, mutation):
    base = create_catalog_and_stock(session, stock=1)
    entities = _entities(session, base)
    mutation(*entities)
    session.flush()

    assert load_public_listings(session) == []
