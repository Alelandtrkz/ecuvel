from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import CatalogInteractionEvent, Favorite, Product, ProductVariant, SellerOffer, Store
from app.services.catalog_feed import CatalogFeedCursor, catalog_feed_context_hash, sign_catalog_feed_cursor
from app.services.catalog_ranking import SURFACE_SEARCH, ranking_day
from app.services.catalog_telemetry import RankingContext, record_context_event, sign_ranking_context
from app.services.favorites import get_favorites_page
from tests.test_adult_access_policy import adult_case, enable_public, login_as


pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("public", "birth_date", "authenticated", "expected"), (
    (False, date(1990, 1, 1), True, 404),
    (True, date(1990, 1, 1), False, 404),
    (True, None, True, 404),
    (True, date(2010, 1, 1), True, 404),
    (True, date(1990, 1, 1), True, 200),
))
def test_direct_detail_and_both_image_urls_enforce_server_age_and_flags(adult_case, app, session, monkeypatch, public, birth_date, authenticated, expected):
    case = adult_case
    enable_public(app, monkeypatch, case)
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", public)
    case.buyer.birth_date = birth_date
    session.commit()
    client = app.test_client()
    if authenticated:
        login_as(client, case.buyer)
    root = f"/productos/{case.product.slug}"
    for path in (root, f"{root}/media/{case.media.public_id}", f"{root}/media/{case.media.public_id}/thumbnail"):
        response = client.get(path)
        assert response.status_code == expected
        if expected == 404:
            assert case.product.title not in response.get_data(as_text=True)
            assert case.media.public_id not in response.get_data(as_text=True)
        else:
            assert response.headers["Cache-Control"] == "private, no-store"
            assert "Cookie" in response.headers.get("Vary", "")
            assert "noindex" in response.headers["X-Robots-Tag"]
    normal_offer = session.get(SellerOffer, case.normal.offer_id)
    normal_variant = session.get(ProductVariant, normal_offer.variant_id)
    normal = session.get(Product, normal_variant.product_id)
    assert client.get(f"/productos/{normal.slug}").status_code == 200


@pytest.mark.parametrize("eligible", (False, True))
def test_discovery_surfaces_filter_products_categories_and_counts(adult_case, app, session, monkeypatch, eligible):
    case = adult_case
    enable_public(app, monkeypatch, case)
    client = app.test_client()
    if eligible:
        login_as(client, case.buyer)
    store = session.get(Store, case.base.store_id)
    paths = ("/", "/?q=SENSITIVE", "/?category=juguetes-intimos", f"/tiendas/{store.slug}", f"/tiendas/{store.slug}/productos/resumen")
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200
        if not eligible:
            assert case.product.title not in response.get_data(as_text=True)
            assert case.product.slug not in response.get_data(as_text=True)
            assert case.media.public_id not in response.get_data(as_text=True)
        elif "/productos/resumen" not in path:
            assert case.product.slug in response.get_data(as_text=True)
    if not eligible:
        assert "Productos para adultos" not in client.get("/").get_data(as_text=True)


def test_old_cursor_and_image_validator_cannot_bypass_disabled_flag(adult_case, app, monkeypatch):
    case = adult_case
    enable_public(app, monkeypatch, case)
    client = app.test_client()
    login_as(client, case.buyer)
    query = "SENSITIVE_ADULT_PRODUCT"
    cursor = sign_catalog_feed_cursor(app.config["SECRET_KEY"], CatalogFeedCursor(
        version=1, ranking_day=ranking_day(), surface=SURFACE_SEARCH,
        context_hash=catalog_feed_context_hash(surface=SURFACE_SEARCH, query=query),
        category_slug=None, store_slug=None, next_position=0, ranking_request_id=uuid.uuid4(), batch_size=20,
    ))
    assert case.product.slug in client.get("/catalogo/feed", query_string={"q": query, "cursor": cursor}).get_data(as_text=True)
    media_url = f"/productos/{case.product.slug}/media/{case.media.public_id}?v={case.media.content_sha256}"
    etag = client.get(media_url).headers["ETag"]
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    feed = client.get("/catalogo/feed", query_string={"q": query, "cursor": cursor})
    assert feed.status_code == 200
    assert case.product.slug not in feed.get_data(as_text=True)
    assert client.get(media_url, headers={"If-None-Match": etag}).status_code == 404


@pytest.mark.parametrize("disabled", ("parent", "leaf", "offer", "release"))
def test_other_publication_conditions_are_required_even_for_adults(adult_case, app, session, monkeypatch, disabled):
    case = adult_case
    enable_public(app, monkeypatch, case)
    client = app.test_client()
    login_as(client, case.buyer)
    if disabled in {"parent", "leaf"}:
        getattr(case, disabled).is_active = False
    elif disabled == "offer":
        from app.models.enums import OfferStatus
        case.offer.status = OfferStatus.PAUSED
    else:
        monkeypatch.setitem(app.config, "ADULT_RELEASED_PRODUCT_IDS", "")
    session.commit()
    assert client.get(f"/productos/{case.product.slug}").status_code == 404
    assert client.get(f"/productos/{case.product.slug}/media/{case.media.public_id}").status_code == 404


def test_favorites_and_header_count_hide_stored_adult_items_when_disabled(adult_case, app, session, monkeypatch):
    case = adult_case
    session.add(Favorite(user_id=case.buyer.id, product_id=case.product.id))
    session.commit()
    client = app.test_client()
    login_as(client, case.buyer)
    body = client.get("/favoritos").get_data(as_text=True)
    assert case.product.slug not in body and case.product.title not in body
    with app.test_request_context():
        page = get_favorites_page(session, user_id=case.buyer.id, page=1, page_size=20)
        assert page.total_items == 0
        assert page.items == ()
    assert client.post(f"/favoritos/productos/{case.product.slug}/agregar", headers={"Accept": "application/json"}).status_code == 404
    assert client.post(f"/favoritos/productos/{case.product.slug}/eliminar", headers={"Accept": "application/json"}).status_code == 404
    enable_public(app, monkeypatch, case)
    assert case.product.slug in client.get("/favoritos").get_data(as_text=True)


def test_sensitive_telemetry_rejects_previously_signed_contexts(adult_case, app, session, monkeypatch):
    case = adult_case
    context = RankingContext(ranking_request_id=uuid.uuid4(), surface="HOME", listing_key="adult-test",
        product_id=case.product.id, variant_id=case.variant.id, offer_id=case.offer.id,
        served_ranker="v1", served_position=1)
    assert record_context_event(session, event_type="CLICK", context=context, actor_user_id=case.buyer.id, anonymous_id=None) is False
    assert session.scalars(select(CatalogInteractionEvent)).all() == []


def test_relationship_guard_cannot_be_bypassed_by_miscoded_child(adult_case, app, session, monkeypatch):
    case = adult_case
    case.leaf.code = "MISC_CHILD_OF_ADULT"
    session.commit()
    assert app.test_client().get(f"/productos/{case.product.slug}").status_code == 404
    assert case.product.slug not in app.test_client().get("/").get_data(as_text=True)
    enable_public(app, monkeypatch, case)
    client = app.test_client()
    login_as(client, case.buyer)
    assert client.get(f"/productos/{case.product.slug}").status_code == 404


def test_alternate_review_image_endpoint_is_guarded(app, session, monkeypatch, tmp_path):
    from PIL import Image
    from app.models import ProductReviewImage
    from app.models.enums import ProductReviewStatus
    from tests.test_review_notifications import _rejected_review
    review = _rejected_review(session)
    review.product.category.code = "ADULT_TOYS"
    review.status = ProductReviewStatus.PUBLISHED
    image = ProductReviewImage(review_id=review.id, revision_id=review.current_revision_id,
        public_id=uuid.uuid4().hex, storage_key="sensitive-review.png", original_filename="review.png",
        media_type="image/png", size_bytes=100, width=10, height=10, sort_order=0)
    Image.new("RGB", (10, 10)).save(tmp_path / "sensitive-review.png")
    session.add(image)
    session.commit()
    monkeypatch.setitem(app.config, "PRODUCT_REVIEW_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    assert app.test_client().get(f"/resenas/imagenes/{image.public_id}").status_code == 404
