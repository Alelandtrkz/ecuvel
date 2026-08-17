from __future__ import annotations

from datetime import datetime, timezone

from app.models import (
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
)
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
)
from app.services.product_publication import (
    MODERATION_CHECKS,
    publish_product_draft,
    record_moderation_decision,
)
from app.services.product_drafts import (
    capture_submission_commission_snapshots,
    submit_saved_product_draft,
)
from tests.product_moderation_helpers import (
    create_commission_rule,
    create_complete_simple_draft,
    create_phone_categories,
    create_seller_location,
    create_store,
    create_user,
)


def _grant_catalog_access(session, user, store) -> None:
    onboarding = StoreOnboarding(
        user_id=user.id,
        store_id=store.id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=store.name,
        legal_id_number="210049391",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    session.add_all((
        StoreMember(
            store_id=store.id,
            user_id=user.id,
            role=StoreMemberRole.OWNER,
            is_active=True,
        ),
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="moderation-v1",
            annex_version="moderation-a1",
            status=StoreContractAcceptanceStatus.ACCEPTED,
            accepted_terms=True,
            otp_verified=True,
            accepted_at=datetime.now(timezone.utc),
        ),
    ))
    session.flush()


def _login(client, user) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _base(session, tmp_path):
    seller = create_user(session, name="Propietaria de tienda")
    moderator = create_user(session, staff=True, name="Moderador ECUVEL")
    store = create_store(session, name="Tienda con feedback")
    category, subcategory = create_phone_categories(session)
    _grant_catalog_access(session, seller, store)
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
    )
    session.commit()
    return seller, moderator, store, category, draft


def test_partner_preview_shows_current_feedback_but_not_stale_feedback(
    app, session, tmp_path,
):
    seller, moderator, _store, category, draft = _base(session, tmp_path)
    create_commission_rule(session, rate="8.00", category=category)
    record_moderation_decision(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        decision="CHANGES_REQUESTED",
        checklist={key: key != "specifications" for key in MODERATION_CHECKS},
        reason_code="INCORRECT_SPECIFICATIONS",
        note="Completa la capacidad real de la batería.",
    )
    session.commit()
    client = app.test_client()
    _login(client, seller)

    response = client.get(f"/partners/products/drafts/{draft.id}/preview")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Correcciones solicitadas por ECUVEL" in body
    assert "Completa la capacidad real de la batería." in body

    submit_saved_product_draft(
        session,
        user_id=seller.id,
        draft_id=draft.id,
    )
    session.commit()
    refreshed = client.get(f"/partners/products/drafts/{draft.id}/preview")
    assert refreshed.status_code == 200
    assert "Correcciones solicitadas por ECUVEL" not in refreshed.get_data(
        as_text=True
    )
    session.expire_all()
    assert len(session.get(type(draft), draft.id).moderation_events) == 1


def test_partner_preview_shows_rejection_as_read_only(app, session, tmp_path):
    seller, moderator, _store, _category, draft = _base(session, tmp_path)
    record_moderation_decision(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        decision="REJECTED",
        checklist={key: True for key in MODERATION_CHECKS},
        reason_code="PROHIBITED_PRODUCT",
        note="Este artículo no puede publicarse en el marketplace.",
    )
    session.commit()
    client = app.test_client()
    _login(client, seller)

    response = client.get(f"/partners/products/drafts/{draft.id}/preview")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Publicación rechazada por ECUVEL" in body
    assert "Volver a editar" not in body


def test_partner_preview_links_to_public_product_after_approval(
    app, session, tmp_path,
):
    seller, moderator, store, category, draft = _base(session, tmp_path)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=category)
    capture_submission_commission_snapshots(session, draft)
    session.commit()
    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist={key: True for key in MODERATION_CHECKS},
        source_media_root=tmp_path / "drafts",
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()
    client = app.test_client()
    _login(client, seller)

    response = client.get(f"/partners/products/drafts/{draft.id}/preview")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Vista privada · Publicado" in body
    assert "Ver publicación" in body
    assert f'/productos/{result.product.slug}' in body

    catalog = client.get("/partners/my-products")
    assert catalog.status_code == 200
    assert catalog.get_data(as_text=True).count(" data-catalog-row>") == 1
