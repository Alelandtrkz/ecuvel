from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models import (
    Product,
    ProductDraft,
    ProductDraftModerationEvent,
    ProductDraftPublication,
)
from app.models.enums import ProductDraftStatus
from app.models.enums import ProductDraftFileStatus
from app.services.admin_products import get_admin_products_page
from tests.product_moderation_helpers import (
    create_commission_rule,
    create_complete_simple_draft,
    create_complete_family_draft,
    create_phone_categories,
    create_seller_location,
    create_store,
    create_user,
)


CHECKLIST = (
    "images",
    "identity",
    "description",
    "specifications",
    "variants",
    "category",
)


def _login(client, user) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _base(session, tmp_path):
    seller = create_user(session, name="Seller prueba")
    staff = create_user(session, staff=True, name="Moderadora ECUVEL")
    store = create_store(session, name="Tienda moderada")
    category, subcategory = create_phone_categories(session)
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
    )
    session.commit()
    return seller, staff, store, category, subcategory, draft


def test_admin_products_requires_active_ecuvel_staff(app, session, tmp_path):
    seller, staff, _store, _category, _subcategory, draft = _base(
        session, tmp_path
    )
    client = app.test_client()

    anonymous = client.get("/admin/products")
    assert anonymous.status_code == 302

    _login(client, seller)
    assert client.get("/admin/products").status_code == 403

    _login(client, staff)
    response = client.get(f"/admin/products?draft={draft.id}")
    assert response.status_code == 200
    assert "Smartphone de prueba" in response.get_data(as_text=True)

    blocked_staff = create_user(session, staff=True, active=False)
    session.commit()
    _login(client, blocked_staff)
    assert client.get("/admin/products").status_code == 403


def test_admin_products_filters_counts_and_search(app, session, tmp_path):
    seller, staff, store, category, subcategory, review = _base(session, tmp_path)
    changes = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
        status=ProductDraftStatus.CHANGES_REQUESTED,
    )
    changes.title = "Producto que requiere cambios"
    rejected = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
        status=ProductDraftStatus.REJECTED,
    )
    rejected.title = "Producto rechazado único"
    untouched = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
        status=ProductDraftStatus.DRAFT,
    )
    untouched.title = "Borrador nunca enviado"
    session.commit()

    client = app.test_client()
    _login(client, staff)
    response = client.get("/admin/products?status=changes")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert changes.title in body
    assert review.title not in body
    assert untouched.title not in body
    assert "1 pendientes" in body

    searched = client.get(
        "/admin/products?status=rejected&q=Producto+rechazado+%C3%BAnico"
    )
    assert searched.status_code == 200
    assert rejected.title in searched.get_data(as_text=True)


def test_admin_products_paginates_and_clamps_out_of_range_pages(
    app, session, tmp_path,
):
    seller = create_user(session)
    store = create_store(session)
    category, subcategory = create_phone_categories(session)
    first = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
    )
    second = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
    )
    session.commit()

    with app.test_request_context():
        first_page = get_admin_products_page(
            session,
            status_key="review",
            query="",
            page=1,
            selected_id=None,
            per_page=1,
        )
        last_page = get_admin_products_page(
            session,
            status_key="review",
            query="",
            page=999,
            selected_id=None,
            per_page=1,
        )

    assert first_page.total == 2
    assert first_page.pages == 2
    assert first_page.page == 1
    assert len(first_page.rows) == 1
    assert last_page.page == 2
    assert len(last_page.rows) == 1
    assert {first_page.rows[0].draft.id, last_page.rows[0].draft.id} == {
        first.id,
        second.id,
    }


def test_admin_preview_and_private_media_are_read_only_and_protected(
    app, session, tmp_path,
):
    seller, staff, _store, _category, _subcategory, draft = _base(
        session, tmp_path
    )
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "drafts")
    client = app.test_client()
    _login(client, staff)

    preview = client.get(f"/admin/products/{draft.id}/preview")
    assert preview.status_code == 200
    assert "simulación 1:1" in preview.get_data(as_text=True)
    session.expire_all()
    assert session.scalar(select(func.count(Product.id))) == 0
    assert session.scalar(select(func.count(ProductDraftModerationEvent.id))) == 0
    assert session.get(ProductDraft, draft.id).status == ProductDraftStatus.SUBMITTED

    media = draft.files[0]
    response = client.get(f"/admin/products/{draft.id}/files/{media.id}")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get(
        f"/admin/products/{draft.id}/files/{uuid.uuid4()}"
    ).status_code == 404

    second = create_complete_simple_draft(
        session,
        seller=seller,
        store=draft.store,
        category=draft.category,
        subcategory=draft.subcategory,
        media_root=tmp_path / "drafts",
    )
    session.commit()
    assert client.get(
        f"/admin/products/{draft.id}/files/{second.files[0].id}"
    ).status_code == 404
    media.status = ProductDraftFileStatus.DELETED
    session.commit()
    assert client.get(
        f"/admin/products/{draft.id}/files/{media.id}"
    ).status_code == 404

    _login(client, seller)
    assert client.get(f"/admin/products/{draft.id}/preview").status_code == 403
    assert client.get(
        f"/admin/products/{draft.id}/files/{media.id}"
    ).status_code == 403


def test_admin_family_preview_selects_requested_variant(app, session, tmp_path):
    seller = create_user(session)
    staff = create_user(session, staff=True)
    store = create_store(session)
    category, subcategory = create_phone_categories(session)
    draft = create_complete_family_draft(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=tmp_path / "drafts",
    )
    session.commit()
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "drafts")
    selected = next(row for row in draft.variants if row["enabled"] and row["stock"] == 0)
    disabled = next(row for row in draft.variants if not row["enabled"])
    client = app.test_client()
    _login(client, staff)

    response = client.get(
        f"/admin/products/{draft.id}/preview?variant={selected['sku']}"
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert selected["sku"] in body
    assert selected["name"] in body
    assert "Producto agotado" in body
    assert disabled["sku"] not in body


def test_request_changes_and_reject_are_append_only_and_state_guarded(
    app, session, tmp_path,
):
    _seller, staff, _store, _category, _subcategory, draft = _base(
        session, tmp_path
    )
    client = app.test_client()
    _login(client, staff)
    payload = {
        "reason_code": "INCORRECT_SPECIFICATIONS",
        "note": "Corrige la ficha técnica de batería.",
        "checklist": ["images", "identity"],
    }
    response = client.post(
        f"/admin/products/{draft.id}/request-changes", data=payload
    )
    assert response.status_code == 302
    session.expire_all()
    stored = session.get(ProductDraft, draft.id)
    assert stored.status == ProductDraftStatus.CHANGES_REQUESTED
    assert len(stored.moderation_events) == 1
    event = stored.moderation_events[0]
    assert event.actor_user_id == staff.id
    assert event.reason_code == "INCORRECT_SPECIFICATIONS"
    assert event.note == payload["note"]
    assert event.checklist_snapshot["images"] is True
    assert event.checklist_snapshot["category"] is False

    replay = client.post(f"/admin/products/{draft.id}/reject", data=payload)
    assert replay.status_code == 302
    session.expire_all()
    assert len(session.get(ProductDraft, draft.id).moderation_events) == 1
    assert client.get(f"/admin/products/{draft.id}/reject").status_code == 405


def test_admin_approval_endpoint_gates_checklist_and_publishes_atomically(
    app, session, tmp_path,
):
    _seller, staff, store, category, _subcategory, draft = _base(
        session, tmp_path
    )
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=category)
    session.commit()
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "drafts")
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path / "catalog")
    client = app.test_client()
    _login(client, staff)

    blocked = client.post(
        f"/admin/products/{draft.id}/approve",
        data={"checklist": ["images"]},
    )
    assert blocked.status_code == 302
    session.expire_all()
    assert session.get(ProductDraft, draft.id).status == ProductDraftStatus.SUBMITTED
    assert session.scalar(select(func.count(Product.id))) == 0

    approved = client.post(
        f"/admin/products/{draft.id}/approve",
        data={"checklist": list(CHECKLIST)},
    )
    assert approved.status_code == 302
    assert "status=approved" in approved.location
    session.expire_all()
    stored = session.get(ProductDraft, draft.id)
    assert stored.status == ProductDraftStatus.APPROVED
    assert session.scalar(select(func.count(Product.id))) == 1
    assert session.scalar(select(func.count(ProductDraftPublication.id))) == 1
    assert stored.moderation_events[0].decision == "APPROVED"
