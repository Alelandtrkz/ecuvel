from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    CatalogInteractionEvent,
    Category,
    InventoryBalance,
    Product,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.models.enums import OfferStatus, StoreStatus
from app.services.catalog_feed import (
    InvalidCatalogFeedCursorError,
    load_catalog_feed_cursor,
)
from app.services.catalog_telemetry import load_ranking_context
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _feed_fixture(session, *, count: int = 55):
    base = create_catalog_and_stock(session, stock=20)
    store = session.get(Store, base.store_id)
    first_offer = session.get(SellerOffer, base.offer_id)
    first_variant = session.get(ProductVariant, first_offer.variant_id)
    first_product = session.get(Product, first_variant.product_id)
    category = session.get(Category, first_product.category_id)
    first_product.title = "Phone product 00"
    for index in range(1, count):
        token = uuid.uuid4().hex[:12]
        product = Product(
            category_id=category.id,
            title=f"Phone product {index:02d}",
            slug=f"feed-product-{token}",
            is_active=True,
        )
        session.add(product)
        session.flush()
        variant = ProductVariant(
            product_id=product.id,
            catalog_sku=f"FEED-SKU-{token}",
            attributes={},
            is_active=True,
        )
        session.add(variant)
        session.flush()
        offer = SellerOffer(
            store_id=store.id,
            variant_id=variant.id,
            seller_sku=f"FEED-SELL-{token}",
            currency="USD",
            price=Decimal(index + 10),
            commission_rate=Decimal("0.00"),
            status=OfferStatus.ACTIVE,
        )
        session.add(offer)
        session.flush()
        session.add(InventoryBalance(
            offer_id=offer.id,
            location_id=base.storage_location_id,
            on_hand_quantity=0 if index >= count - 3 else 20,
            reserved_quantity=0,
            blocked_quantity=0,
        ))
    session.commit()
    return base, store, category


def _attribute(html: str, name: str) -> str:
    match = re.search(fr'{name}="([^"]*)"', html)
    assert match is not None
    return match.group(1)


def _listing_keys(html: str) -> list[str]:
    return re.findall(r'data-listing-key="([^"]+)"', html)


def _ranking_tokens(html: str) -> list[str]:
    return re.findall(r'data-ranking-context="([^"]+)"', html)


def _next(client, cursor: str, **context):
    return client.get(
        "/catalogo/feed",
        query_string={"cursor": cursor, **context},
    )


def test_home_feed_reaches_55_without_duplicates_and_keeps_global_positions(
    client, app, session,
):
    _feed_fixture(session)
    first = client.get("/")
    first_html = first.get_data(as_text=True)
    cursor = _attribute(first_html, "data-feed-cursor")
    second = _next(client, cursor)
    second_payload = second.get_json()
    third = _next(client, second_payload["next_cursor"])
    third_payload = third.get_json()

    chunks = [first_html, second_payload["html"], third_payload["html"]]
    keys = [key for chunk in chunks for key in _listing_keys(chunk)]
    assert [len(_listing_keys(chunk)) for chunk in chunks] == [20, 20, 15]
    assert len(keys) == len(set(keys)) == 55
    assert second_payload["loaded_count"] == 40
    assert third_payload["loaded_count"] == 55
    assert third_payload["has_more"] is False
    assert third_payload["next_cursor"] is None
    assert "Catálogo en crecimiento" not in second_payload["html"]

    contexts = [
        load_ranking_context(
            app.config["SECRET_KEY"],
            token,
            max_age_seconds=app.config["CATALOG_RANKING_CONTEXT_TTL_SECONDS"],
        )
        for chunk in chunks
        for token in _ranking_tokens(chunk)
    ]
    assert [context.served_position for context in contexts] == list(range(1, 56))
    assert len({context.ranking_request_id for context in contexts}) == 1


def test_search_category_and_store_cursors_are_context_bound(client, session):
    _base, store, category = _feed_fixture(session, count=47)

    search_html = client.get("/", query_string={"q": "phone"}).get_data(as_text=True)
    search_cursor = _attribute(search_html, "data-feed-cursor")
    search_next = _next(client, search_cursor, q="phone")
    assert search_next.status_code == 200
    assert _next(client, search_cursor, q="zapatos").status_code == 400

    category_html = client.get(
        "/", query_string={"category": category.slug}
    ).get_data(as_text=True)
    category_cursor = _attribute(category_html, "data-feed-cursor")
    assert _next(client, category_cursor, category=category.slug).status_code == 200
    assert _next(client, category_cursor, category="otra-categoria").status_code == 400

    store_html = client.get(f"/tiendas/{store.slug}").get_data(as_text=True)
    store_cursor = _attribute(store_html, "data-feed-cursor")
    assert _next(client, store_cursor, store=store.slug).status_code == 200
    assert _next(client, store_cursor, store="otra-tienda").status_code == 400
    assert "Página " not in store_html


def test_cursor_tamper_expiry_and_privacy_are_controlled(client, app, session):
    _feed_fixture(session, count=21)
    html = client.get("/", query_string={"q": "phone"}).get_data(as_text=True)
    token = _attribute(html, "data-feed-cursor")
    assert "phone" not in token.lower()
    assert _next(client, token + "tampered", q="phone").status_code == 400
    with pytest.raises(InvalidCatalogFeedCursorError):
        load_catalog_feed_cursor(
            app.config["SECRET_KEY"],
            token,
            max_age_seconds=-1,
        )


@pytest.mark.parametrize("candidate_count", (100, 1000))
def test_feed_batch_query_count_is_constant_for_100_and_1000_candidates(
    client, session, candidate_count,
):
    from sqlalchemy import event

    _feed_fixture(session, count=candidate_count)
    html = client.get("/").get_data(as_text=True)
    cursor = _attribute(html, "data-feed-cursor")
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = _next(client, cursor)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert response.status_code == 200
    assert len(_listing_keys(response.get_json()["html"])) == 20
    assert len(statements) == 7
    assert sum("product_media" in statement for statement in statements) <= 1
    assert sum("inventory_balances" in statement for statement in statements) <= 1


def test_mobile_navigation_hero_and_accessibility_contract(client, session):
    _feed_fixture(session, count=2)
    home = client.get("/")
    html = home.get_data(as_text=True)

    assert home.status_code == 200
    assert 'aria-label="Navegación principal móvil"' in html
    for label in ("Inicio", "Catálogo", "Favoritos", "Carrito", "Mi Ecuvel"):
        assert f">{label}<" in html
    assert html.count('id="category-modal"') == 1
    assert html.count("data-category-modal-open") == 2
    assert 'aria-labelledby="category-modal-title"' in html
    assert 'data-promotion-slot="home-hero"' in html
    assert 'data-promotion-owner="ecuvel"' in html
    assert 'href="/pedidos"' in html
    assert 'aria-current="page"' in html

    catalog_html = client.get("/", query_string={"q": "phone"}).get_data(as_text=True)
    assert 'mobile-bottom-nav__item is-active" type="button"' in catalog_html

    css = client.get("/static/css/mobile-navigation.css").get_data(as_text=True)
    assert "env(safe-area-inset-bottom" in css
    assert "repeat(5, minmax(0, 1fr))" in css
    home_css = client.get("/static/css/home.css").get_data(as_text=True)
    assert "repeat(2, minmax(0, 1fr))" in home_css
    assert "@media (max-width: 359px)" in home_css
    feed_js = client.get("/static/js/catalog-feed.js").get_data(as_text=True)
    assert 'rootMargin: "600px 0px"' in feed_js
    assert "sessionStorage" in feed_js
    assert 'event.persisted' in feed_js
    assert "seenListingKeys" in feed_js
    telemetry_js = client.get(
        "/static/js/catalog-telemetry.js"
    ).get_data(as_text=True)
    assert "EcuvelCatalogTelemetry" in telemetry_js
    assert "observedCards" in telemetry_js


def test_batch_two_card_keeps_exact_cart_offer_and_telemetry(client, session):
    _feed_fixture(session, count=25)
    first_html = client.get("/").get_data(as_text=True)
    response = _next(client, _attribute(first_html, "data-feed-cursor"))
    fragment = response.get_json()["html"]
    assert 'name="next" value="/"' in fragment
    offer_id = _attribute(fragment, 'name="offer_id" value')
    ranking_token = _attribute(fragment, 'name="ranking_context" value')

    added = client.post(
        "/carrito/agregar",
        data={
            "offer_id": offer_id,
            "quantity": "1",
            "ranking_context": ranking_token,
            "next": "/",
        },
        headers={"Accept": "application/json"},
    )

    assert added.status_code == 200
    assert added.get_json()["ok"] is True
    event = session.scalar(
        select(CatalogInteractionEvent).where(
            CatalogInteractionEvent.event_type == "ADD_TO_CART"
        )
    )
    assert event is not None
    assert str(event.offer_id) == offer_id
