from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Order,
    StoreContractAcceptance,
    User,
    UserLegalAcceptance,
    UserMarketingConsent,
)
from app.models.enums import (
    LegalAcceptanceAction,
    LegalAcceptanceSource,
    PaymentMethod,
    UserStatus,
)
from app.services import legal_documents
from app.services.checkout import CheckoutServiceError
from app.services.legal_documents import DocumentStatus
from app.services.legal_product import PRIVACY_KEY, TERMS_KEY
from app.services.legal_versioning import record_user_legal_evidence
from tests.factories import create_catalog_and_stock
from tests.legal_product_helpers import (
    PAST_PUBLICATION,
    create_and_publish_required_versions,
    create_legal_version_from_template,
    patch_legal_registry,
    publish_registry_version,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _registration_data(**overrides):
    values = {
        "email": f"legal-{uuid.uuid4().hex[:8]}@test.local",
        "full_name": "Cliente Legal",
        "password": "correct horse battery staple",
        "password_confirmation": "correct horse battery staple",
    }
    values.update(overrides)
    return values


def _enforced_registration_data(terms, privacy, **overrides):
    values = _registration_data(
        terms_version_presented=terms.version_identifier,
        privacy_version_presented=privacy.version_identifier,
        terms_accepted="1",
        privacy_acknowledged="1",
    )
    values.update(overrides)
    return values


def _buyer(session, app, base):
    user = session.get(User, base.buyer_id)
    user.email = app.config["CHECKOUT_DEMO_BUYER_EMAIL"]
    user.email_verified_at = datetime.now(timezone.utc)
    user.birth_date = date(1990, 1, 1)
    return user


def _login(client, user):
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user.id)
        browser_session["_fresh"] = True


def _set_cart(client, offer_id):
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {
                str(offer_id): {"quantity": 1, "selected": True},
            },
        }


def _checkout_token(client):
    response = client.get("/checkout")
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        return browser_session["checkout_draft"]["token"], response


def _checkout_post_data(token, **overrides):
    values = {
        "checkout_token": token,
        "payment_method": PaymentMethod.BANK_TRANSFER.value,
    }
    values.update(overrides)
    return values


def test_legal_enforcement_defaults_off(app):
    assert app.config["LEGAL_ENFORCEMENT_ENABLED"] is False


def test_prepublication_registration_creates_no_legal_evidence(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", False)

    response = client.post("/registro", data=_registration_data())

    assert response.status_code == 302
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_enforced_registration_exposes_exact_terms_and_privacy_versions(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()

    response = client.get("/registro")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert terms.version_identifier in body
    assert privacy.version_identifier in body
    assert 'name="terms_accepted"' in body
    assert 'name="privacy_acknowledged"' in body
    assert "no autoriza marketing" in body


@pytest.mark.parametrize(
    "missing_field,expected_message",
    [
        ("terms_accepted", "Debes aceptar expresamente"),
        ("privacy_acknowledged", "Debes confirmar que recibiste"),
    ],
)
def test_enforced_registration_requires_separate_actions_atomically(
    client,
    app,
    session,
    monkeypatch,
    missing_field,
    expected_message,
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    data = _enforced_registration_data(terms, privacy)
    data.pop(missing_field)

    response = client.post("/registro", data=data)
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert expected_message in body
    assert data["email"] in body
    assert data["full_name"] in body
    assert data["password"] not in body
    assert session.scalar(select(func.count(User.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_valid_registration_records_exact_separate_evidence_only(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()

    response = client.post(
        "/registro",
        data=_enforced_registration_data(terms, privacy),
    )
    session.expire_all()

    assert response.status_code == 302
    evidence = session.scalars(
        select(UserLegalAcceptance).order_by(UserLegalAcceptance.action)
    ).all()
    assert {
        (row.legal_document_version_id, row.action, row.source)
        for row in evidence
    } == {
        (terms.id, LegalAcceptanceAction.ACCEPTED, LegalAcceptanceSource.REGISTER),
        (
            privacy.id,
            LegalAcceptanceAction.ACKNOWLEDGED,
            LegalAcceptanceSource.REGISTER,
        ),
    }
    assert session.scalar(select(func.count(UserMarketingConsent.id))) == 0
    assert session.scalar(select(func.count(StoreContractAcceptance.id))) == 0


@pytest.mark.parametrize("changed_key", [TERMS_KEY, PRIVACY_KEY])
def test_registration_rejects_version_changed_after_presentation(
    client, app, session, monkeypatch, changed_key
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    assert client.get("/registro").status_code == 200
    replacement = create_legal_version_from_template(
        app,
        session,
        changed_key,
        published_at=PAST_PUBLICATION + timedelta(days=1),
    )
    session.commit()
    publish_registry_version(monkeypatch, replacement)

    response = client.post(
        "/registro",
        data=_enforced_registration_data(terms, privacy),
    )

    assert response.status_code == 400
    assert replacement.version_identifier in response.get_data(as_text=True)
    assert session.scalar(select(func.count(User.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_registration_rolls_back_user_when_legal_evidence_fails(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("evidence failure")
        return record_user_legal_evidence(*args, **kwargs)

    monkeypatch.setattr(
        "app.blueprints.auth.record_user_legal_evidence", fail_second
    )
    with pytest.raises(RuntimeError, match="evidence failure"):
        client.post(
            "/registro",
            data=_enforced_registration_data(terms, privacy),
        )

    assert session.scalar(select(func.count(User.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_enforced_registration_fails_closed_for_registry_db_split_brain(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    terms, _privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    replacement = create_legal_version_from_template(
        app,
        session,
        TERMS_KEY,
        published_at=PAST_PUBLICATION + timedelta(days=1),
    )
    session.commit()

    response = client.get("/registro")

    assert response.status_code == 503
    assert terms.version_identifier != replacement.version_identifier
    assert "documentación legal vigente está siendo actualizada" in (
        response.get_data(as_text=True)
    )


def test_enforced_registration_fails_closed_when_registry_is_draft(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    create_legal_version_from_template(app, session, TERMS_KEY)
    create_legal_version_from_template(app, session, PRIVACY_KEY)
    session.commit()

    assert client.get("/registro").status_code == 503


def test_enforced_registration_fails_closed_without_db_version(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    document = legal_documents.document_by_path(*TERMS_KEY, published_only=False)
    families = tuple(
        replace(
            family,
            documents=tuple(
                replace(
                    item,
                    status=DocumentStatus.PUBLISHED,
                    version_identifier="terms-2020-09-20-v1",
                    published_at=PAST_PUBLICATION.isoformat(),
                    effective_at=PAST_PUBLICATION.isoformat(),
                )
                if item is document
                else item
                for item in family.documents
            ),
        )
        for family in legal_documents.FAMILIES
    )
    patch_legal_registry(monkeypatch, families)

    assert client.get("/registro").status_code == 503


def test_prepublication_checkout_creates_order_without_fabricated_evidence(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", False)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, page = _checkout_token(client)

    response = client.post("/checkout", data=_checkout_post_data(token))

    assert "no registra aceptación legal" in page.get_data(as_text=True)
    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 1
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_telemetry_rejection_never_blocks_checkout(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", False)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    session.commit()
    assert client.post(
        "/privacidad/preferencias",
        data={"decision": "REJECTED"},
    ).status_code == 302
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, _page = _checkout_token(client)

    response = client.post("/checkout", data=_checkout_post_data(token))

    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 1


def test_enforced_checkout_renders_only_missing_current_requirements(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    record_user_legal_evidence(
        session,
        user_id=user.id,
        legal_document_version_id=privacy.id,
        action=LegalAcceptanceAction.ACKNOWLEDGED,
        source=LegalAcceptanceSource.CHECKOUT,
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)

    _token, response = _checkout_token(client)
    body = response.get_data(as_text=True)

    assert terms.version_identifier in body
    assert 'name="terms_accepted"' in body
    assert 'name="privacy_acknowledged"' not in body


def test_enforced_checkout_can_require_only_privacy_acknowledgment(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    record_user_legal_evidence(
        session,
        user_id=user.id,
        legal_document_version_id=terms.id,
        action=LegalAcceptanceAction.ACCEPTED,
        source=LegalAcceptanceSource.CHECKOUT,
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)

    _token, response = _checkout_token(client)
    body = response.get_data(as_text=True)

    assert privacy.version_identifier in body
    assert 'name="terms_accepted"' not in body
    assert 'name="privacy_acknowledged"' in body


def test_enforced_checkout_with_current_evidence_has_no_redundant_actions(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    for version, action in (
        (terms, LegalAcceptanceAction.ACCEPTED),
        (privacy, LegalAcceptanceAction.ACKNOWLEDGED),
    ):
        record_user_legal_evidence(
            session,
            user_id=user.id,
            legal_document_version_id=version.id,
            action=action,
            source=LegalAcceptanceSource.CHECKOUT,
        )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)

    _token, response = _checkout_token(client)
    body = response.get_data(as_text=True)

    assert "requisitos legales vigentes ya están registrados" in body
    assert 'name="terms_accepted"' not in body
    assert 'name="privacy_acknowledged"' not in body


def test_checkout_missing_terms_creates_no_order_or_evidence(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, _page = _checkout_token(client)

    response = client.post(
        "/checkout",
        data=_checkout_post_data(
            token,
            terms_version_presented=terms.version_identifier,
            privacy_version_presented=privacy.version_identifier,
            privacy_acknowledged="1",
        ),
    )

    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_valid_checkout_records_missing_evidence_and_order_atomically(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, _page = _checkout_token(client)

    response = client.post(
        "/checkout",
        data=_checkout_post_data(
            token,
            terms_version_presented=terms.version_identifier,
            privacy_version_presented=privacy.version_identifier,
            terms_accepted="1",
            privacy_acknowledged="1",
        ),
    )

    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 1
    evidence = session.scalars(select(UserLegalAcceptance)).all()
    assert {row.legal_document_version_id for row in evidence} == {
        terms.id,
        privacy.id,
    }
    assert {row.source for row in evidence} == {LegalAcceptanceSource.CHECKOUT}


def test_checkout_rolls_back_flushed_legal_evidence_when_order_creation_fails(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, _page = _checkout_token(client)

    def fail_after_legal_evidence_flush(*, session, **_kwargs):
        evidence_count = session.scalar(
            select(func.count(UserLegalAcceptance.id))
        )
        assert evidence_count == 2
        raise CheckoutServiceError("forced checkout failure")

    monkeypatch.setattr(
        "app.storefront.create_checkout_order",
        fail_after_legal_evidence_flush,
    )

    response = client.post(
        "/checkout",
        data=_checkout_post_data(
            token,
            terms_version_presented=terms.version_identifier,
            privacy_version_presented=privacy.version_identifier,
            terms_accepted="1",
            privacy_acknowledged="1",
        ),
    )

    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_checkout_rejects_unseen_new_terms_and_preserves_old_evidence(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    terms_v1, privacy = create_and_publish_required_versions(
        app, session, monkeypatch
    )
    old_evidence = record_user_legal_evidence(
        session,
        user_id=user.id,
        legal_document_version_id=terms_v1.id,
        action=LegalAcceptanceAction.ACCEPTED,
        source=LegalAcceptanceSource.CHECKOUT,
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)
    token, page = _checkout_token(client)
    assert 'name="terms_accepted"' not in page.get_data(as_text=True)
    terms_v2 = create_legal_version_from_template(
        app,
        session,
        TERMS_KEY,
        published_at=PAST_PUBLICATION + timedelta(days=1),
    )
    session.commit()
    publish_registry_version(monkeypatch, terms_v2)

    response = client.post(
        "/checkout",
        data=_checkout_post_data(
            token,
            privacy_version_presented=privacy.version_identifier,
            privacy_acknowledged="1",
        ),
    )

    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.get(UserLegalAcceptance, old_evidence.id) is not None
    assert session.scalar(
        select(func.count(UserLegalAcceptance.id)).where(
            UserLegalAcceptance.legal_document_version_id == terms_v2.id
        )
    ) == 0


def test_supporting_none_documents_never_block_checkout(
    client, app, session, monkeypatch
):
    monkeypatch.setitem(app.config, "LEGAL_ENFORCEMENT_ENABLED", True)
    base = create_catalog_and_stock(session, stock=3)
    user = _buyer(session, app, base)
    create_and_publish_required_versions(app, session, monkeypatch)
    create_legal_version_from_template(
        app,
        session,
        ("privacidad", "cookies-y-tecnologias-similares"),
    )
    session.commit()
    _login(client, user)
    _set_cart(client, base.offer_id)

    _token, response = _checkout_token(client)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="terms_accepted"' in body
    assert 'name="privacy_acknowledged"' in body
    assert "cookies_version_presented" not in body
