from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    Category,
    Favorite,
    Product,
    ProductMedia,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.services.product_media import (
    ordered_product_media,
    select_product_media,
    variant_media_binding,
)
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


@pytest.fixture
def catalog_media_root(app, tmp_path):
    previous = app.config["PRODUCT_CATALOG_MEDIA_DIR"]
    root = tmp_path / "catalog-media"
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(root)
    try:
        yield root
    finally:
        app.config["PRODUCT_CATALOG_MEDIA_DIR"] = previous


def _media_row(
    *,
    public_id: str,
    position: int = 0,
    is_cover: bool = False,
    is_active: bool = True,
    media_type: str = "image/jpeg",
    axis: str | None = None,
    value: str | None = None,
    created_offset: int = 0,
):
    return SimpleNamespace(
        id=uuid.UUID(int=int(public_id, 16)),
        public_id=public_id,
        position=position,
        is_cover=is_cover,
        is_active=is_active,
        media_type=media_type,
        variant_axis_key=axis,
        variant_value_key=value,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(seconds=created_offset),
    )


def test_selector_returns_none_without_eligible_general_media():
    other_variant = _media_row(
        public_id="1", axis="color", value="red", is_cover=True
    )
    assert select_product_media([]) is None
    assert select_product_media([other_variant]) is None
    assert (
        select_product_media(
            [other_variant],
            variant_axis_key="color",
            variant_value_key="blue",
        )
        is None
    )


def test_selector_orders_general_media_by_cover_position_time_and_id():
    later_cover = _media_row(
        public_id="4", is_cover=True, position=0, created_offset=1
    )
    earlier_cover_high_id = _media_row(
        public_id="3", is_cover=True, position=0
    )
    earlier_cover_low_id = _media_row(
        public_id="2", is_cover=True, position=0
    )
    lower_position = _media_row(public_id="1", position=1)
    ordered = ordered_product_media(
        [lower_position, later_cover, earlier_cover_high_id, earlier_cover_low_id]
    )
    assert [item.public_id for item in ordered] == ["2", "3", "4", "1"]


def test_selector_uses_lowest_position_when_general_media_has_no_cover():
    second = _media_row(public_id="2", position=2)
    first = _media_row(public_id="1", position=1)
    assert select_product_media([second, first]) is first


def test_selector_prefers_exact_visual_binding_then_general_fallback():
    general = _media_row(public_id="1", is_cover=True)
    red = _media_row(
        public_id="2", axis="color", value="red", is_cover=True
    )
    blue = _media_row(
        public_id="3", axis="color", value="blue", position=2
    )
    rows = [general, red, blue]
    assert (
        select_product_media(
            rows, variant_axis_key="color", variant_value_key="blue"
        )
        is blue
    )
    assert (
        select_product_media(
            rows, variant_axis_key="color", variant_value_key="green"
        )
        is general
    )


def test_selector_ignores_inactive_and_non_displayable_media():
    inactive = _media_row(public_id="1", is_cover=True, is_active=False)
    invalid = _media_row(
        public_id="2", is_cover=True, media_type="application/pdf"
    )
    valid = _media_row(public_id="3", position=9, media_type="image/webp")
    assert select_product_media([inactive, invalid, valid]) is valid


def test_variant_binding_maps_labels_to_canonical_keys():
    configuration = {
        "visual_axis_key": "color",
        "axes": [
            {
                "key": "color",
                "values": [{"key": "blue", "label": "Azul"}],
            }
        ],
    }
    assert variant_media_binding(configuration, {"color": "Azul"}) == (
        "color",
        "blue",
    )
    assert variant_media_binding(configuration, {}) == (None, None)


def _entities(
    session: Session, base: BaseData
) -> tuple[Product, ProductVariant, SellerOffer]:
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    variant = session.get(ProductVariant, offer.variant_id)
    assert variant is not None
    product = session.get(Product, variant.product_id)
    assert product is not None
    return product, variant, offer


def _align_with(
    session: Session,
    base: BaseData,
    reference_product: Product,
    reference_store_id: uuid.UUID,
    *,
    title: str,
) -> tuple[Product, ProductVariant, SellerOffer]:
    product, variant, offer = _entities(session, base)
    product.category_id = reference_product.category_id
    product.title = title
    offer.store_id = reference_store_id
    return product, variant, offer


def _add_media(
    session: Session,
    root: Path,
    product: Product,
    *,
    public_id: str,
    storage_key: str,
    exists: bool = True,
    axis: str | None = None,
    value: str | None = None,
    is_cover: bool = True,
    media_type: str = "image/jpeg",
) -> ProductMedia:
    payload = b"existing-published-image"
    if exists:
        path = root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    media = ProductMedia(
        product_id=product.id,
        public_id=public_id,
        storage_key=storage_key,
        media_type=media_type,
        size_bytes=len(payload),
        position=0,
        is_cover=is_cover,
        variant_axis_key=axis,
        variant_value_key=value,
        is_active=True,
    )
    session.add(media)
    session.flush()
    return media


def _login_as(client, user_id: uuid.UUID) -> None:
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user_id)
        browser_session["_fresh"] = True


def _put_in_cart(client, offer_id: uuid.UUID) -> None:
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {
                str(offer_id): {"quantity": 1, "selected": True},
            },
        }


def test_real_media_and_placeholder_flow_across_all_card_surfaces(
    client, app, session: Session, catalog_media_root: Path
):
    primary_base = create_catalog_and_stock(session, stock=10)
    empty_base = create_catalog_and_stock(session, stock=10)
    recommendation_base = create_catalog_and_stock(session, stock=10)
    primary, variant, offer = _entities(session, primary_base)
    primary.title = "Media Product"
    primary.variant_configuration = {
        "visual_axis_key": "color",
        "axes": [
            {
                "key": "color",
                "values": [
                    {"key": "blue", "label": "Azul"},
                    {"key": "red", "label": "Rojo"},
                ],
            }
        ],
    }
    variant.attributes = {"color": "Azul"}
    empty, _empty_variant, _empty_offer = _align_with(
        session,
        empty_base,
        primary,
        primary_base.store_id,
        title="No Media Product",
    )
    recommendation, _rec_variant, _rec_offer = _align_with(
        session,
        recommendation_base,
        primary,
        primary_base.store_id,
        title="Recommended Product",
    )

    exact = _add_media(
        session,
        catalog_media_root,
        primary,
        public_id="blue-media",
        storage_key="published/private-name-blue.jpg",
        axis="color",
        value="blue",
    )
    other = _add_media(
        session,
        catalog_media_root,
        primary,
        public_id="red-media",
        storage_key="published/private-name-red.jpg",
        axis="color",
        value="red",
    )
    recommended = _add_media(
        session,
        catalog_media_root,
        recommendation,
        public_id="recommended-media",
        storage_key="published/private-name-recommended.jpg",
    )
    session.add_all(
        [
            Favorite(user_id=primary_base.buyer_id, product_id=primary.id),
            Favorite(user_id=primary_base.buyer_id, product_id=empty.id),
        ]
    )
    session.commit()

    exact_url = f"/productos/{primary.slug}/media/{exact.public_id}"
    other_url = f"/productos/{primary.slug}/media/{other.public_id}"
    recommended_url = (
        f"/productos/{recommendation.slug}/media/{recommended.public_id}"
    )
    category = session.get(Category, primary.category_id)
    store = session.get(Store, primary_base.store_id)
    assert category is not None and store is not None
    category_slug = category.slug

    pages = [
        client.get("/"),
        client.get("/?q=Media+Product"),
        client.get(f"/?category={category_slug}"),
        client.get(f"/tiendas/{store.slug}"),
    ]
    for response in pages:
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert exact_url in body
        assert other_url not in body
        assert "private-name-blue.jpg" not in body
    assert "product-placeholder.svg" in pages[0].get_data(as_text=True)

    _login_as(client, primary_base.buyer_id)
    favorites = client.get("/favoritos")
    assert favorites.status_code == 200
    assert exact_url in favorites.get_data(as_text=True)
    assert "product-placeholder.svg" in favorites.get_data(as_text=True)

    _put_in_cart(client, offer.id)
    cart = client.get("/carrito")
    assert cart.status_code == 200
    assert exact_url in cart.get_data(as_text=True)
    assert recommended_url in cart.get_data(as_text=True)

    detail = client.get(f"/productos/{primary.slug}")
    assert detail.status_code == 200
    assert exact_url in detail.get_data(as_text=True)
    assert recommended_url in detail.get_data(as_text=True)

    image = client.get(exact_url)
    assert image.status_code == 200
    assert image.cache_control.public is True
    assert image.cache_control.max_age == 31536000
    assert image.get_etag()[0]
    not_modified = client.get(
        exact_url,
        headers={"If-None-Match": image.headers["ETag"]},
    )
    assert not_modified.status_code == 304
    assert client.get(f"/productos/{primary.slug}/media/unknown").status_code == 404

    other.is_active = False
    session.commit()
    assert client.get(other_url).status_code == 404


def test_missing_physical_file_falls_back_and_public_route_returns_404(
    client, session: Session, catalog_media_root: Path
):
    base = create_catalog_and_stock(session)
    product, _variant, _offer = _entities(session, base)
    missing = _add_media(
        session,
        catalog_media_root,
        product,
        public_id="missing-media",
        storage_key="published/missing.jpg",
        exists=False,
    )
    session.commit()

    home = client.get("/")
    body = home.get_data(as_text=True)
    assert home.status_code == 200
    assert "product-placeholder.svg" in body
    assert missing.public_id not in body
    assert "published/missing.jpg" not in body
    assert (
        client.get(f"/productos/{product.slug}/media/{missing.public_id}").status_code
        == 404
    )


def test_home_media_query_and_total_query_count_are_constant(
    client, engine: Engine, session: Session
):
    create_catalog_and_stock(session)
    session.commit()

    def measured_get() -> tuple[int, int]:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            response = client.get("/")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200
        media_queries = sum(
            "product_media" in statement.lower() for statement in statements
        )
        return len(statements), media_queries

    one_card_queries, one_card_media_queries = measured_get()
    for _index in range(9):
        create_catalog_and_stock(session)
    session.commit()
    ten_card_queries, ten_card_media_queries = measured_get()

    assert one_card_media_queries == ten_card_media_queries == 1
    assert ten_card_queries == one_card_queries
    assert ten_card_queries <= 7
