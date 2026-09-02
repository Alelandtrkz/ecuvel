from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import CatalogInteractionEvent, Product, ProductVariant, SellerOffer, User
from app.models.enums import UserStatus
from app.services.catalog_shadow_ranking import (
    ListingEventAggregate,
    extract_shadow_features,
    ranking_readiness_report,
    shadow_rank_listings,
)
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _ranking_context(response) -> str:
    match = re.search(
        r'data-ranking-context="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return html.unescape(match.group(1))


def _served_slugs(response) -> list[str]:
    return re.findall(
        r'data-product-card="([^"]+)"',
        response.get_data(as_text=True),
    )


def _product(session, base) -> Product:
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    return session.get(Product, variant.product_id)


def _user(session) -> User:
    email = f"telemetry-{uuid.uuid4().hex[:8]}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="Telemetry Buyer",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def test_signed_impression_is_anonymous_deduped_and_click_is_persisted(client, session):
    create_catalog_and_stock(session, stock=4)
    session.commit()
    token = _ranking_context(client.get("/"))

    first = client.post(
        "/catalogo/interacciones",
        data={"event_type": "IMPRESSION", "ranking_context": token},
    )
    duplicate = client.post(
        "/catalogo/interacciones",
        data={"event_type": "IMPRESSION", "ranking_context": token},
    )
    click = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )

    assert first.status_code == duplicate.status_code == click.status_code == 202
    assert first.get_json()["recorded"] is True
    assert duplicate.get_json()["recorded"] is False
    events = session.scalars(
        select(CatalogInteractionEvent).order_by(CatalogInteractionEvent.event_type)
    ).all()
    assert [event.event_type for event in events] == ["CLICK", "IMPRESSION"]
    assert all(event.actor_user_id is None for event in events)
    assert events[0].anonymous_session_id == events[1].anonymous_session_id
    assert events[0].anonymous_session_id is not None


def test_tampered_context_and_server_only_event_are_rejected(client, session):
    create_catalog_and_stock(session)
    session.commit()
    token = _ranking_context(client.get("/"))

    assert client.post(
        "/catalogo/interacciones",
        data={"event_type": "IMPRESSION", "ranking_context": token + "tampered"},
    ).status_code == 400
    assert client.post(
        "/catalogo/interacciones",
        data={"event_type": "PURCHASE", "ranking_context": token},
    ).status_code == 422
    assert session.scalar(select(func.count()).select_from(CatalogInteractionEvent)) == 0


def test_successful_add_to_cart_records_exact_offer_but_stock_failure_does_not(client, session):
    base = create_catalog_and_stock(session, stock=1)
    session.commit()
    token = _ranking_context(client.get("/"))

    success = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(base.offer_id),
            "quantity": "1",
            "next": "/",
            "ranking_context": token,
        },
        headers={"Accept": "application/json"},
    )
    failed = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(base.offer_id),
            "quantity": "1",
            "next": "/",
            "ranking_context": token,
        },
        headers={"Accept": "application/json"},
    )

    assert success.status_code == 200
    assert failed.status_code == 409
    events = session.scalars(
        select(CatalogInteractionEvent).where(
            CatalogInteractionEvent.event_type == "ADD_TO_CART"
        )
    ).all()
    assert len(events) == 1
    assert events[0].offer_id == base.offer_id


def test_successful_favorite_records_product_level_event_and_failure_isolated(
    client, session, monkeypatch
):
    base = create_catalog_and_stock(session, stock=2)
    product = _product(session, base)
    user = _user(session)
    session.commit()
    token = _ranking_context(client.get("/"))
    login = client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": "correct horse battery staple",
            "next": "/",
        },
    )
    assert login.status_code == 302

    response = client.post(
        f"/favoritos/productos/{product.slug}/agregar",
        data={"ranking_context": token},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    event = session.scalar(
        select(CatalogInteractionEvent).where(
            CatalogInteractionEvent.event_type == "FAVORITE"
        )
    )
    assert event is not None
    assert event.product_id == product.id
    assert event.actor_user_id == user.id

    other_base = create_catalog_and_stock(session, stock=1)
    other_product = _product(session, other_base)
    session.commit()
    monkeypatch.setattr(
        "app.storefront.record_context_event_best_effort",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telemetry down")),
    )
    isolated = client.post(
        f"/favoritos/productos/{other_product.slug}/agregar",
        data={"ranking_context": token},
        headers={"Accept": "application/json"},
    )
    assert isolated.status_code == 200


def test_shadow_smoothing_exploration_zero_price_weight_and_readiness(session):
    base = create_catalog_and_stock(session, stock=2)
    session.commit()
    from app.services.catalog_listings import load_public_listings

    listing = load_public_listings(session)[0]
    cold = extract_shadow_features(listing, ListingEventAggregate())
    low_sample = extract_shadow_features(
        listing,
        ListingEventAggregate(impressions=1, clicks=1),
    )
    established = extract_shadow_features(
        listing,
        ListingEventAggregate(impressions=1000, clicks=80),
    )

    assert cold.exploration == 1.0
    assert low_sample.exploration < cold.exploration
    assert established.exploration < low_sample.exploration
    assert low_sample.smoothed_ctr < 1.0
    assert established.smoothed_ctr > 0.07

    shadow = shadow_rank_listings(
        [listing],
        {listing.listing_key: ListingEventAggregate(impressions=1, clicks=1)},
    )
    assert shadow[0].position == 1
    assert shadow[0].features.price_competitiveness == 0.0

    report = ranking_readiness_report(
        session,
        all_listing_keys={listing.listing_key},
    )
    assert report["total_listings"] == 1
    assert report["listing_coverage"] == 0.0
    assert report["sample_distribution"]["0"] == 1


def test_shadow_order_and_shadow_failure_never_change_served_v1(
    client, session, app, monkeypatch
):
    for _index in range(5):
        create_catalog_and_stock(session, stock=2)
    session.commit()
    monkeypatch.setitem(app.config, "CATALOG_SHADOW_RANKING_ENABLED", False)
    baseline = _served_slugs(client.get("/"))
    monkeypatch.setitem(app.config, "CATALOG_SHADOW_RANKING_ENABLED", True)

    def inverted(listings, _aggregates):
        from app.services.catalog_shadow_ranking import ShadowFeatures, ShadowRankingResult

        features = ShadowFeatures(0, 0, 0, 0, 0, 0, 0, 0)
        return [
            ShadowRankingResult(item.listing_key, position, float(position), features)
            for position, item in enumerate(reversed(listings), start=1)
        ]

    monkeypatch.setattr("app.storefront.shadow_rank_listings", inverted)
    inverted_shadow = client.get("/")
    assert inverted_shadow.status_code == 200
    assert _served_slugs(inverted_shadow) == baseline

    monkeypatch.setattr(
        "app.storefront.load_listing_event_aggregates",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("shadow down")),
    )
    failed_shadow = client.get("/")
    assert failed_shadow.status_code == 200
    assert _served_slugs(failed_shadow) == baseline
