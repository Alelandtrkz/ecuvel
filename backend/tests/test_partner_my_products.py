from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Category,
    InventoryBalance,
    Product,
    ProductDraft,
    ProductDraftFile,
    ProductMedia,
    ProductVariant,
    SellerOffer,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    OfferStatus,
    ProductDraftFileKind,
    ProductDraftFileStatus,
    ProductDraftStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    StoreStatus,
    UserStatus,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _partner(session: Session) -> tuple[User, Store]:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}",
        email=f"partner-{token}@test.local",
        email_normalized=f"partner-{token}@test.local",
        password_hash=generate_password_hash("safe test password"),
        full_name="Partner Catalog",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    store = Store(
        public_code=f"STR-{token}",
        name="Tienda Catálogo",
        slug=f"tienda-{token}",
        legal_name="Tienda Catálogo",
        tax_id=f"RUC-{token}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add_all([user, store])
    session.flush()
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
    session.add_all(
        [
            onboarding,
            StoreMember(
                store_id=store.id,
                user_id=user.id,
                role=StoreMemberRole.OWNER,
                is_active=True,
            ),
        ]
    )
    session.flush()
    session.add(
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="catalog-v1",
            annex_version="catalog-a1",
            status=StoreContractAcceptanceStatus.ACCEPTED,
            accepted_terms=True,
            otp_verified=True,
            accepted_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return user, store


def _category(session: Session, *, name: str = "Teléfonos y Accesorios") -> tuple[Category, Category]:
    token = uuid.uuid4().hex[:8]
    parent = Category(
        code=f"ELECTRONICS-{token}",
        name="Electrónicos",
        slug=f"electronicos-{token}",
        is_active=True,
        sort_order=1,
    )
    child = Category(
        code=f"ELECTRONICS_PHONES-{token}",
        name=name,
        slug=f"telefonos-{token}",
        parent=parent,
        is_active=True,
        sort_order=1,
    )
    session.add_all([parent, child])
    session.flush()
    return parent, child


def _draft(
    session: Session,
    *,
    user: User,
    store: Store,
    parent: Category,
    child: Category,
    sku: str,
    title: str,
    status: ProductDraftStatus = ProductDraftStatus.DRAFT,
    configuration: dict | None = None,
    variants: list[dict] | None = None,
) -> ProductDraft:
    draft = ProductDraft(
        store_id=store.id,
        created_by_user_id=user.id,
        category_id=parent.id,
        subcategory_id=child.id,
        template_key="electronics_phones",
        title=title,
        seller_sku=sku,
        barcode=sku,
        condition="NEW",
        status=status,
        pricing_data={"price": "499.00", "currency": "USD"},
        inventory_data={"stock_quantity": 7},
        variant_configuration=configuration or {},
        variants=variants or [],
    )
    session.add(draft)
    session.flush()
    return draft


def _login(client, user: User) -> None:
    response = client.post(
        "/iniciar-sesion",
        data={"email": user.email, "password": "safe test password"},
    )
    assert response.status_code == 302


def test_my_products_empty_state_and_navigation(client, session: Session):
    user, _store = _partner(session)
    session.commit()
    _login(client, user)

    response = client.get("/partners/my-products")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Mis productos" in body
    assert "Todavía no tienes productos" in body
    assert 'href="/partners/products"' in body
    assert 'href="/partners/my-products" class="is-active"' not in body
    assert 'class="is-active" href="/partners/my-products"' in body


def test_my_products_expands_only_active_family_variants_and_provisional_row(
    client,
    session: Session,
):
    user, store = _partner(session)
    parent, child = _category(session)
    configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "visual_axis_key": "color_principal",
        "axes": [
            {
                "key": "color_principal",
                "label": "Color",
                "values": [
                    {"key": "azul", "label": "Azul", "swatch": "#2563EB"},
                    {"key": "negro", "label": "Negro", "swatch": "#111827"},
                ],
            }
        ],
    }
    variants = [
        {
            "variant_id": "blue",
            "name": "Azul / 256 GB",
            "sku": "IPH17-BLUE-256",
            "options": {"color_principal": "azul"},
            "price": "1099.00",
            "stock": 4,
            "enabled": True,
        },
        {
            "variant_id": "black",
            "name": "Negro / 512 GB",
            "sku": "IPH17-BLACK-512",
            "options": {"color_principal": "negro"},
            "price": "1299.00",
            "stock": 0,
            "enabled": False,
        },
    ]
    family_draft = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="IPH17-FAMILY",
        title="iPhone 17 Pro Max",
        status=ProductDraftStatus.READY_FOR_REVIEW,
        configuration=configuration,
        variants=variants,
    )
    family_image = ProductDraftFile(
        draft_id=family_draft.id,
        kind=ProductDraftFileKind.IMAGE,
        status=ProductDraftFileStatus.ACTIVE,
        storage_key=f"drafts/{uuid.uuid4().hex}.png",
        original_filename="iphone-blue.png",
        media_type="image/png",
        size_bytes=128,
        sha256="a" * 64,
        position=0,
        is_cover=True,
        width=100,
        height=100,
        variant_axis_key="color_principal",
        variant_value_key="azul",
    )
    session.add(family_image)
    _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="EMPTY-FAMILY",
        title="Familia pendiente",
        configuration=configuration,
        variants=[],
    )
    session.commit()
    _login(client, user)

    response = client.get("/partners/my-products")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "IPH17-BLUE-256" in body
    assert "Azul / 256 GB" in body
    assert "IPH17-BLACK-512" not in body
    assert "Presentación pendiente" in body
    assert f"/partners/products/drafts/{family_draft.id}/files/{family_image.id}" in body
    assert "2 presentaciones" in body
    assert "Listo para enviar" in body
    assert body.count("Editar") == 2


def test_my_products_search_status_filter_pagination_and_out_of_range_page(
    client,
    session: Session,
):
    user, store = _partner(session)
    parent, child = _category(session)
    other_parent, other_child = _category(session, name="Cámaras y Fotografía")
    for index in range(21):
        _draft(
            session,
            user=user,
            store=store,
            parent=other_parent if index == 0 else parent,
            child=other_child if index == 0 else child,
            sku=f"CAT-{index:03d}",
            title=f"Producto {index:03d}",
            status=(
                ProductDraftStatus.SUBMITTED
                if index == 20
                else ProductDraftStatus.DRAFT
            ),
        )
    session.commit()
    _login(client, user)

    first_page = client.get("/partners/my-products")
    assert first_page.status_code == 200
    first_page_body = first_page.get_data(as_text=True)
    assert "Mostrando 1 a 20 de 21 presentaciones" in first_page_body
    assert first_page_body.count("data-catalog-select-native") == 2
    assert first_page_body.count("data-catalog-select-button") == 2
    assert first_page_body.count("data-catalog-select-menu") == 2
    assert 'role="listbox"' in first_page_body

    last_page = client.get("/partners/my-products?page=999")
    assert last_page.status_code == 200
    assert "Mostrando 21 a 21 de 21 presentaciones" in last_page.get_data(as_text=True)

    filtered = client.get("/partners/my-products?q=CAT-020&status=review")
    filtered_body = filtered.get_data(as_text=True)
    assert filtered.status_code == 200
    assert "CAT-020" in filtered_body
    assert "En revisión" in filtered_body
    assert "CAT-019" not in filtered_body
    assert "Vista previa" in filtered_body

    by_category = client.get(f"/partners/my-products?category={other_child.id}")
    category_body = by_category.get_data(as_text=True)
    assert by_category.status_code == 200
    assert "CAT-000" in category_body
    assert "CAT-001" not in category_body
    assert "Cámaras y Fotografía" in category_body


def test_my_products_never_exposes_another_store_drafts(client, session: Session):
    user, store = _partner(session)
    foreign_user, foreign_store = _partner(session)
    parent, child = _category(session)
    _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="OWN-SKU",
        title="Producto propio",
    )
    _draft(
        session,
        user=foreign_user,
        store=foreign_store,
        parent=parent,
        child=child,
        sku="FOREIGN-SKU",
        title="Producto ajeno",
    )
    session.commit()
    _login(client, user)

    body = client.get("/partners/my-products").get_data(as_text=True)

    assert "OWN-SKU" in body
    assert "FOREIGN-SKU" not in body


def test_my_products_normalizes_every_draft_lifecycle_status(client, session: Session):
    user, store = _partner(session)
    parent, child = _category(session)
    expected = [
        (ProductDraftStatus.DRAFT, "draft", "Borrador"),
        (ProductDraftStatus.INCOMPLETE, "incomplete", "Incompleto"),
        (ProductDraftStatus.READY_FOR_REVIEW, "ready", "Listo para enviar"),
        (ProductDraftStatus.SUBMITTED, "review", "En revisión"),
        (ProductDraftStatus.CHANGES_REQUESTED, "changes", "Cambios solicitados"),
        (ProductDraftStatus.APPROVED, "approved", "Aprobado"),
        (ProductDraftStatus.REJECTED, "rejected", "Rechazado"),
    ]
    for index, (draft_status, _filter, _label) in enumerate(expected):
        _draft(
            session,
            user=user,
            store=store,
            parent=parent,
            child=child,
            sku=f"STATE-{index}",
            title=f"Estado {index}",
            status=draft_status,
        )
    session.commit()
    _login(client, user)

    for index, (_draft_status, status_filter, label) in enumerate(expected):
        response = client.get(f"/partners/my-products?status={status_filter}")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f"STATE-{index}" in body
        assert f"partner-catalog-status--{status_filter}" in body
        assert label in body


def test_published_offer_wins_deduplication_and_uses_available_stock(
    client,
    session: Session,
):
    user, store = _partner(session)
    parent, child = _category(session)
    _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="DUPLICATE-SKU",
        title="Título del borrador",
        status=ProductDraftStatus.APPROVED,
    )
    product = Product(
        category_id=child.id,
        title="iPhone publicado",
        slug=f"iphone-publicado-{uuid.uuid4().hex[:8]}",
        variant_configuration={},
        is_active=True,
    )
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku="DUPLICATE-SKU",
        title="Azul / 256 GB",
        attributes={"color_principal": "Azul"},
        is_active=True,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=store.id,
        variant_id=variant.id,
        seller_sku="SELLER-DUPLICATE-SKU",
        currency="USD",
        price=Decimal("1099.00"),
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE,
    )
    warehouse = Warehouse(
        code=f"WH-{uuid.uuid4().hex[:8]}",
        name="Bodega catálogo",
        address_line="Quito",
        city="Quito",
        country_code="EC",
        is_active=True,
    )
    session.add_all([offer, warehouse])
    session.flush()
    location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"LOC-{uuid.uuid4().hex[:8]}",
        barcode=f"BAR-{uuid.uuid4().hex[:8]}",
        name="Estantería",
        location_type=LocationType.STORAGE,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    media = ProductMedia(
        product_id=product.id,
        storage_key=f"catalog/{uuid.uuid4().hex}.png",
        media_type="image/png",
        size_bytes=128,
        position=0,
        is_cover=True,
        is_active=True,
    )
    session.add_all([location, media])
    session.flush()
    session.add(
        InventoryBalance(
            offer_id=offer.id,
            location_id=location.id,
            on_hand_quantity=10,
            reserved_quantity=3,
            blocked_quantity=2,
        )
    )
    session.commit()
    _login(client, user)

    active = client.get("/partners/my-products")
    active_body = active.get_data(as_text=True)

    assert active.status_code == 200
    assert active_body.count("DUPLICATE-SKU") >= 1
    assert "Título del borrador" not in active_body
    assert "iPhone publicado" in active_body
    assert "$1099.00" in active_body
    assert re.search(r"partner-catalog-stock[^>]*>.*?5\s*</span>", active_body, re.S)
    assert "Activo" in active_body
    assert "Ver publicación" in active_body
    assert f"/productos/{product.slug}?variant=DUPLICATE-SKU" in active_body
    assert f"/productos/{product.slug}/media/{media.public_id}" in active_body

    offer.status = OfferStatus.PAUSED
    session.commit()
    paused_body = client.get("/partners/my-products").get_data(as_text=True)
    assert "Desactivado" in paused_body
    assert "Ver publicación" not in paused_body


def test_row_menu_and_selection_capabilities_follow_status(client, session: Session):
    user, store = _partner(session)
    parent, child = _category(session)
    editable = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="MENU-EDITABLE",
        title="Producto editable",
    )
    submitted = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="MENU-REVIEW",
        title="Producto en revisión",
        status=ProductDraftStatus.SUBMITTED,
    )
    session.commit()
    _login(client, user)

    body = client.get("/partners/my-products").get_data(as_text=True)

    assert "data-catalog-menu-trigger" in body
    assert "data-catalog-delete-single" in body
    assert f'/products/drafts/{editable.id}/delete' in body
    assert "Editar" in body
    assert "Vista previa" in body
    assert "Eliminar" in body
    assert f'data-draft-id="{editable.id}"' in body
    assert f'value="{submitted.id}"' in body
    submitted_checkbox = re.search(
        rf'<input type="checkbox"[^>]+value="{submitted.id}"[^>]+>', body, re.S
    )
    assert submitted_checkbox is not None
    assert "disabled" in submitted_checkbox.group(0)
    assert "Enviar a revisión" in body
    assert "data-catalog-bulk-delete" in body


def test_individual_delete_removes_family_files_and_current_session(
    client,
    app,
    session: Session,
    tmp_path,
):
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "draft-files")
    user, store = _partner(session)
    parent, child = _category(session)
    draft = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="DELETE-FAMILY",
        title="Familia para eliminar",
        configuration={"version": 4, "enabled": True, "mode": "family"},
        variants=[
            {"variant_id": "one", "name": "Azul", "enabled": True},
            {"variant_id": "two", "name": "Rojo", "enabled": False},
        ],
    )
    storage_key = f"drafts/{uuid.uuid4().hex}.png"
    stored = Path(app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"]) / storage_key
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"draft image")
    file_record = ProductDraftFile(
        draft_id=draft.id,
        kind=ProductDraftFileKind.IMAGE,
        status=ProductDraftFileStatus.ACTIVE,
        storage_key=storage_key,
        original_filename="draft.png",
        media_type="image/png",
        size_bytes=len(b"draft image"),
        sha256="b" * 64,
        position=0,
        is_cover=True,
    )
    session.add(file_record)
    session.commit()
    draft_id = draft.id
    _login(client, user)
    with client.session_transaction() as browser:
        browser["partner_current_product_draft_id"] = str(draft_id)

    response = client.post(
        f"/partners/products/drafts/{draft_id}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/partners/my-products" in response.headers["Location"]
    session.expire_all()
    assert session.scalar(
        select(func.count()).select_from(ProductDraft).where(ProductDraft.id == draft_id)
    ) == 0
    assert session.scalar(
        select(func.count()).select_from(ProductDraftFile).where(
            ProductDraftFile.draft_id == draft_id
        )
    ) == 0
    assert not stored.exists()
    with client.session_transaction() as browser:
        assert "partner_current_product_draft_id" not in browser


def test_bulk_delete_is_deduplicated_and_all_or_nothing_on_state_change(
    client,
    app,
    session: Session,
    tmp_path,
):
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "draft-files")
    user, store = _partner(session)
    parent, child = _category(session)
    first = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="BULK-ONE",
        title="Primero",
    )
    blocked = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="BULK-TWO",
        title="Segundo",
        status=ProductDraftStatus.SUBMITTED,
    )
    session.commit()
    first_id = first.id
    blocked_id = blocked.id
    _login(client, user)

    blocked_response = client.post(
        "/partners/my-products/bulk/delete",
        data={"draft_ids": [str(first_id), str(first_id), str(blocked_id)]},
        follow_redirects=False,
    )
    assert blocked_response.status_code == 302
    session.expire_all()
    assert session.get(ProductDraft, first_id) is not None
    assert session.get(ProductDraft, blocked_id) is not None

    blocked.status = ProductDraftStatus.CHANGES_REQUESTED
    session.commit()
    deleted_response = client.post(
        "/partners/my-products/bulk/delete",
        data={"draft_ids": [str(first_id), str(first_id), str(blocked_id)]},
        follow_redirects=False,
    )
    assert deleted_response.status_code == 302
    session.expire_all()
    assert session.scalar(
        select(func.count()).select_from(ProductDraft).where(
            ProductDraft.id.in_([first_id, blocked_id])
        )
    ) == 0


def test_bulk_submit_processes_valid_drafts_and_reports_invalid_ones(
    client,
    session: Session,
    monkeypatch,
):
    from app.services import partner_product_actions
    from app.services.product_drafts import ProductDraftValidationError

    user, store = _partner(session)
    parent, child = _category(session)
    valid = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="SUBMIT-VALID",
        title="Producto válido",
    )
    invalid = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="SUBMIT-INVALID",
        title="Producto inválido",
    )
    session.commit()
    _login(client, user)

    def fake_submit(database_session, *, user_id, draft_id):
        target = database_session.get(ProductDraft, draft_id)
        if draft_id == invalid.id:
            raise ProductDraftValidationError(
                "Revisa la información del producto.",
                {"images": "Faltan imágenes."},
            )
        target.status = ProductDraftStatus.SUBMITTED
        return target

    monkeypatch.setattr(partner_product_actions, "submit_saved_product_draft", fake_submit)
    response = client.post(
        "/partners/my-products/bulk/submit",
        data={"draft_ids": [str(valid.id), str(valid.id), str(invalid.id)]},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    session.expire_all()
    assert session.get(ProductDraft, valid.id).status == ProductDraftStatus.SUBMITTED
    assert session.get(ProductDraft, invalid.id).status == ProductDraftStatus.DRAFT
    assert "1 enviados y 1 pendientes" in body
    assert "Faltan imágenes" in body
    assert f"/partners/products/drafts/{invalid.id}" in body


def test_store_administrator_can_manage_another_members_draft(
    client,
    session: Session,
    tmp_path,
    app,
):
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "draft-files")
    owner, store = _partner(session)
    parent, child = _category(session)
    draft = _draft(
        session,
        user=owner,
        store=store,
        parent=parent,
        child=child,
        sku="ADMIN-DRAFT",
        title="Borrador del owner",
    )
    token = uuid.uuid4().hex[:10]
    admin = User(
        public_code=f"ADM-{token}",
        email=f"admin-{token}@test.local",
        email_normalized=f"admin-{token}@test.local",
        password_hash=generate_password_hash("safe test password"),
        full_name="Store Admin",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(admin)
    session.flush()
    session.add(
        StoreMember(
            store_id=store.id,
            user_id=admin.id,
            role=StoreMemberRole.ADMINISTRATOR,
            is_active=True,
        )
    )
    session.commit()
    draft_id = draft.id
    _login(client, admin)

    assert client.get(f"/partners/products/drafts/{draft_id}").status_code == 200
    assert client.post(
        f"/partners/products/drafts/{draft_id}/delete",
        follow_redirects=False,
    ).status_code == 302
    session.expire_all()
    assert session.scalar(
        select(func.count()).select_from(ProductDraft).where(ProductDraft.id == draft_id)
    ) == 0


def test_delete_endpoint_requires_csrf_when_enabled(client, app, session: Session):
    user, store = _partner(session)
    parent, child = _category(session)
    draft = _draft(
        session,
        user=user,
        store=store,
        parent=parent,
        child=child,
        sku="CSRF-DRAFT",
        title="Borrador protegido",
    )
    session.commit()
    _login(client, user)
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(f"/partners/products/drafts/{draft.id}/delete")
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous

    assert response.status_code == 400
    session.expire_all()
    assert session.get(ProductDraft, draft.id) is not None
