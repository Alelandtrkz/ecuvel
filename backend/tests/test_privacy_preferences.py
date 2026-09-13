from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    CatalogInteractionEvent,
    Product,
    ProductVariant,
    SellerOffer,
    User,
    UserLegalAcceptance,
    UserMarketingConsent,
    UserPrivacyPreferenceEvent,
)
from app.models.enums import (
    LegalAcceptanceAction,
    LegalAcceptanceSource,
    MarketingConsentChannel,
    MarketingConsentStatus,
    PrivacyPreferenceDecision,
    PrivacyPreferenceSource,
    UserStatus,
)
from app.services.catalog_telemetry import ANONYMOUS_SESSION_KEY
from app.services.legal_product import COOKIES_KEY, PRIVACY_KEY, TERMS_KEY
from app.services.legal_versioning import record_user_legal_evidence
from app.services.privacy_preferences import (
    PRIVACY_PREFERENCE_SESSION_KEY,
    is_optional_catalog_telemetry_allowed,
    record_privacy_preference,
    resolve_privacy_preference_state,
)
from tests.factories import create_catalog_and_stock
from tests.legal_product_helpers import (
    PAST_PUBLICATION,
    create_legal_version_from_template,
    publish_registry_version,
)


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


def _product(session, base) -> Product:
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    return session.get(Product, variant.product_id)


def _user(session) -> User:
    email = f"privacy-{uuid.uuid4().hex[:8]}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="Privacy Buyer",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": "correct horse battery staple",
            "next": "/",
        },
    )


def _logout(client):
    return client.post("/cerrar-sesion")


def _publish_cookies(app, session, monkeypatch, *, published_at=PAST_PUBLICATION):
    version = create_legal_version_from_template(
        app,
        session,
        COOKIES_KEY,
        published_at=published_at,
    )
    session.commit()
    publish_registry_version(monkeypatch, version)
    return version


def _choose(client, decision, notice_identifier=None):
    data = {"decision": decision, "return_to": "/privacidad/preferencias"}
    if notice_identifier is not None:
        data["notice_version_presented"] = notice_identifier
    return client.post("/privacidad/preferencias", data=data)


def test_prepublication_preference_page_is_public_and_cannot_fabricate_grant(
    client, session
):
    response = client.get("/privacidad/preferencias")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="decision" value="GRANTED"' in body
    assert "disabled aria-disabled" in body
    assert "Rechazar opcionales" in body
    assert "no afecta tu acceso" in body
    assert "cookies-y-tecnologias-similares" not in body
    assert "catalog-telemetry.js" not in body

    rejected = _choose(client, "REJECTED")
    assert rejected.status_code == 302
    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 0
    with client.session_transaction() as browser_session:
        assert browser_session[PRIVACY_PREFERENCE_SESSION_KEY]["decision"] == (
            PrivacyPreferenceDecision.REJECTED.value
        )
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_privacy_preference_migration_has_no_seed_or_backfill(session):
    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 0


def test_prepublication_grant_is_rejected_without_session_evidence(client):
    response = _choose(client, "GRANTED", "cookies-2020-09-20-v1")

    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert PRIVACY_PREFERENCE_SESSION_KEY not in browser_session
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_privacy_preference_change_requires_csrf(client, app, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

    response = client.post(
        "/privacidad/preferencias",
        data={"decision": "REJECTED"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("decision", [None, "REJECTED"])
def test_direct_telemetry_bypass_records_nothing_and_creates_no_uuid(
    client, session, decision
):
    create_catalog_and_stock(session, stock=3)
    session.commit()
    home = client.get("/")
    token = _ranking_context(home)
    assert "/static/js/catalog-telemetry.js" not in home.get_data(as_text=True)
    if decision is not None:
        assert _choose(client, decision).status_code == 302

    response = client.post(
        "/catalogo/interacciones",
        data={"event_type": "IMPRESSION", "ranking_context": token},
    )

    assert response.status_code == 202
    assert response.get_json()["recorded"] is False
    assert session.scalar(select(func.count(CatalogInteractionEvent.id))) == 0
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_catalog_browsing_and_cart_work_without_tracking_identifier(
    client, session
):
    base = create_catalog_and_stock(session, stock=3)
    product = _product(session, base)
    session.commit()
    home = client.get("/")
    token = _ranking_context(home)

    assert client.get("/", query_string={"q": product.title}).status_code == 200
    assert client.get(f"/productos/{product.slug}").status_code == 200
    added = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(base.offer_id),
            "quantity": "1",
            "next": "/",
            "ranking_context": token,
        },
        headers={"Accept": "application/json"},
    )

    assert added.status_code == 200
    assert session.scalar(select(func.count(CatalogInteractionEvent.id))) == 0
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_current_guest_grant_activates_client_and_server_telemetry(
    client, app, session, monkeypatch
):
    base = create_catalog_and_stock(session, stock=4)
    cookies = _publish_cookies(app, session, monkeypatch)

    response = _choose(client, "GRANTED", cookies.version_identifier)
    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY not in browser_session

    home = client.get("/")
    body = home.get_data(as_text=True)
    token = _ranking_context(home)
    assert "/static/js/catalog-telemetry.js" in body

    event_response = client.post(
        "/catalogo/interacciones",
        data={"event_type": "IMPRESSION", "ranking_context": token},
    )
    cart_response = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(base.offer_id),
            "quantity": "1",
            "next": "/",
            "ranking_context": token,
        },
        headers={"Accept": "application/json"},
    )

    assert event_response.get_json()["recorded"] is True
    assert cart_response.status_code == 200
    assert {
        value
        for value in session.scalars(
            select(CatalogInteractionEvent.event_type)
        )
    } == {"IMPRESSION", "ADD_TO_CART"}
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY in browser_session


def test_guest_grant_becomes_stale_when_cookie_notice_changes(
    client, app, session, monkeypatch
):
    create_catalog_and_stock(session, stock=3)
    cookies_v1 = _publish_cookies(app, session, monkeypatch)
    assert _choose(client, "GRANTED", cookies_v1.version_identifier).status_code == 302
    cookies_v2 = create_legal_version_from_template(
        app,
        session,
        COOKIES_KEY,
        published_at=PAST_PUBLICATION + timedelta(days=1),
    )
    session.commit()
    publish_registry_version(monkeypatch, cookies_v2)

    home = client.get("/")
    token = _ranking_context(home)
    response = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )

    assert "/static/js/catalog-telemetry.js" not in home.get_data(as_text=True)
    assert response.get_json()["recorded"] is False
    assert session.scalar(select(func.count(CatalogInteractionEvent.id))) == 0
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_stale_presented_cookie_notice_cannot_grant_new_version(
    client, app, session, monkeypatch
):
    cookies_v1 = _publish_cookies(app, session, monkeypatch)
    assert client.get("/privacidad/preferencias").status_code == 200
    cookies_v2 = create_legal_version_from_template(
        app,
        session,
        COOKIES_KEY,
        published_at=PAST_PUBLICATION + timedelta(days=1),
    )
    session.commit()
    publish_registry_version(monkeypatch, cookies_v2)

    response = _choose(client, "GRANTED", cookies_v1.version_identifier)

    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert PRIVACY_PREFERENCE_SESSION_KEY not in browser_session
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_authenticated_preference_history_and_withdrawal_stop_future_events(
    client, app, session, monkeypatch
):
    create_catalog_and_stock(session, stock=3)
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)
    assert _login(client, user).status_code == 302
    unknown_home = client.get("/")
    token = _ranking_context(unknown_home)
    assert "/static/js/catalog-telemetry.js" not in unknown_home.get_data(as_text=True)

    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302
    granted_home = client.get("/")
    assert "/static/js/catalog-telemetry.js" in granted_home.get_data(as_text=True)
    first = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )
    assert first.get_json()["recorded"] is True

    assert _choose(client, "REJECTED", cookies.version_identifier).status_code == 302
    second = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )
    events = session.scalars(
        select(UserPrivacyPreferenceEvent).order_by(
            UserPrivacyPreferenceEvent.occurred_at
        )
    ).all()

    assert [event.decision for event in events] == [
        PrivacyPreferenceDecision.GRANTED,
        PrivacyPreferenceDecision.REJECTED,
    ]
    assert events[0].notice_version_identifier == cookies.version_identifier
    assert events[0].notice_content_sha256 == cookies.content_sha256
    assert second.get_json()["recorded"] is False
    assert session.scalar(select(func.count(CatalogInteractionEvent.id))) == 1


def test_guest_choice_survives_login_without_becoming_account_grant(
    client, session
):
    user = _user(session)
    session.commit()
    assert _choose(client, "REJECTED").status_code == 302

    assert _login(client, user).status_code == 302

    with client.session_transaction() as browser_session:
        assert browser_session[PRIVACY_PREFERENCE_SESSION_KEY]["decision"] == (
            PrivacyPreferenceDecision.REJECTED.value
        )
    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 0


def test_new_account_rejection_prevents_browser_grant_after_logout(
    client, app, session, monkeypatch
):
    create_catalog_and_stock(session, stock=3)
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)

    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302
    with client.session_transaction() as browser_session:
        assert ANONYMOUS_SESSION_KEY not in browser_session
    assert _login(client, user).status_code == 302
    assert _choose(client, "REJECTED", cookies.version_identifier).status_code == 302
    assert _logout(client).status_code == 302

    home = client.get("/")
    token = _ranking_context(home)
    direct = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )

    assert "/static/js/catalog-telemetry.js" not in home.get_data(as_text=True)
    assert direct.get_json()["recorded"] is False
    assert session.scalar(select(func.count(CatalogInteractionEvent.id))) == 0
    with client.session_transaction() as browser_session:
        assert browser_session[PRIVACY_PREFERENCE_SESSION_KEY]["decision"] == (
            PrivacyPreferenceDecision.REJECTED.value
        )
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_existing_account_rejection_neutralizes_browser_grant_on_login(
    client, app, session, monkeypatch
):
    create_catalog_and_stock(session, stock=3)
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)
    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302
    record_privacy_preference(
        session,
        {},
        user_id=user.id,
        decision=PrivacyPreferenceDecision.REJECTED,
        source=PrivacyPreferenceSource.PRIVACY_SETTINGS,
        presented_notice_identifier=cookies.version_identifier,
    )
    session.commit()

    assert _login(client, user).status_code == 302
    account_home = client.get("/")
    assert "/static/js/catalog-telemetry.js" not in account_home.get_data(
        as_text=True
    )
    assert _logout(client).status_code == 302

    anonymous_home = client.get("/")
    token = _ranking_context(anonymous_home)
    direct = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )

    assert "/static/js/catalog-telemetry.js" not in anonymous_home.get_data(
        as_text=True
    )
    assert direct.get_json()["recorded"] is False
    with client.session_transaction() as browser_session:
        assert browser_session[PRIVACY_PREFERENCE_SESSION_KEY]["decision"] == (
            PrivacyPreferenceDecision.REJECTED.value
        )
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_account_grant_is_not_copied_to_browser_scope(
    client, app, session, monkeypatch
):
    create_catalog_and_stock(session, stock=3)
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)

    assert _login(client, user).status_code == 302
    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302
    authenticated_home = client.get("/")
    assert "/static/js/catalog-telemetry.js" in authenticated_home.get_data(
        as_text=True
    )
    with client.session_transaction() as browser_session:
        assert PRIVACY_PREFERENCE_SESSION_KEY not in browser_session

    assert _logout(client).status_code == 302
    anonymous_home = client.get("/")
    token = _ranking_context(anonymous_home)
    direct = client.post(
        "/catalogo/interacciones",
        data={"event_type": "CLICK", "ranking_context": token},
    )

    assert "/static/js/catalog-telemetry.js" not in anonymous_home.get_data(
        as_text=True
    )
    assert direct.get_json()["recorded"] is False
    with client.session_transaction() as browser_session:
        assert PRIVACY_PREFERENCE_SESSION_KEY not in browser_session
        assert ANONYMOUS_SESSION_KEY not in browser_session


def test_authenticated_preference_history_cascades_with_user_deletion(
    client, app, session, monkeypatch
):
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)
    assert _login(client, user).status_code == 302
    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302
    user_id = user.id
    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 1

    session.execute(User.__table__.delete().where(User.id == user_id))
    session.commit()

    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 0


def test_legal_and_marketing_state_never_enable_optional_telemetry(
    client, app, session, monkeypatch
):
    user = _user(session)
    cookies = _publish_cookies(app, session, monkeypatch)
    terms = create_legal_version_from_template(app, session, TERMS_KEY)
    privacy = create_legal_version_from_template(app, session, PRIVACY_KEY)
    for version, action in (
        (terms, LegalAcceptanceAction.ACCEPTED),
        (privacy, LegalAcceptanceAction.ACKNOWLEDGED),
    ):
        record_user_legal_evidence(
            session,
            user_id=user.id,
            legal_document_version_id=version.id,
            action=action,
            source=LegalAcceptanceSource.REGISTER,
        )
    session.add(
        UserMarketingConsent(
            user_id=user.id,
            channel=MarketingConsentChannel.EMAIL,
            status=MarketingConsentStatus.GRANTED,
            granted_at=datetime.now(timezone.utc),
            source="test",
        )
    )
    session.commit()

    state = resolve_privacy_preference_state(
        session,
        {},
        user_id=user.id,
    )

    assert cookies is not None
    assert state.telemetry_allowed is False
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 2
    assert session.scalar(select(func.count(UserMarketingConsent.id))) == 1


def test_telemetry_grant_creates_neither_marketing_nor_legal_evidence(
    client, app, session, monkeypatch
):
    cookies = _publish_cookies(app, session, monkeypatch)

    assert _choose(client, "GRANTED", cookies.version_identifier).status_code == 302

    assert session.scalar(select(func.count(UserPrivacyPreferenceEvent.id))) == 0
    assert session.scalar(select(func.count(UserMarketingConsent.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_footer_uses_only_working_docs_and_privacy_links(client):
    body = client.get("/").get_data(as_text=True)
    expected = {
        "/docs",
        "/docs/ecuvel/como-funciona",
        "/docs/ecuvel/contacto",
        "/docs/compradores",
        "/docs/privacidad",
        "/docs/vendedores",
        "/docs/legal/operador",
        "/privacidad/preferencias",
    }

    for path in expected:
        assert f'href="{path}"' in body
        assert client.get(path).status_code == 200
    assert 'href="/docs/compradores/terminos-y-condiciones"' not in body
    assert 'href="/docs/privacidad/politica-de-privacidad"' not in body
