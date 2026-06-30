from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.catalog.product_templates import PRODUCT_TEMPLATES, validate_template_registry
from app.models import (
    Category,
    Product,
    ProductDraft,
    ProductDraftFile,
    ProductVariant,
    SellerOffer,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    StoreProductCounter,
    User,
)
from app.models.enums import (
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
from app.services.partner_product_categories import PARTNER_PRODUCT_DRAFT_SESSION_KEY


pytestmark = pytest.mark.integration

@pytest.fixture
def client(app, tmp_path):
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "draft-files")
    app.config["PARTNER_PRODUCT_MIN_IMAGES"] = 3
    app.config["PARTNER_PRODUCT_MAX_IMAGES"] = 6
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _png_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (1, 1), "#085DF8").save(stream, format="PNG")
    return stream.getvalue()


def _image_tuple(name: str = "image.png"):
    return (io.BytesIO(_png_bytes()), name)


def _user(session, *, email: str | None = None) -> User:
    email = email or f"draft-partner-{uuid.uuid4().hex}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="Partner Draft",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": "correct horse battery staple",
            "next": "/partners/products",
        },
        follow_redirects=False,
    )


def _enabled_store(session, user: User, *, role: StoreMemberRole = StoreMemberRole.OWNER) -> Store:
    store = Store(
        public_code=f"ST-{uuid.uuid4().hex[:8].upper()}",
        name="Tienda Draft",
        slug=f"tienda-draft-{uuid.uuid4().hex[:8]}",
        legal_name="Tienda Draft",
        tax_id=f"RUC-{uuid.uuid4().hex[:12]}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add(store)
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
    member = StoreMember(store_id=store.id, user_id=user.id, role=role, is_active=True)
    session.add_all([onboarding, member])
    session.flush()
    session.add(
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="test-v1",
            annex_version="test-a1",
            status=StoreContractAcceptanceStatus.ACCEPTED,
            accepted_terms=True,
            otp_verified=True,
            accepted_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return store


def _category_tree(session):
    existing = session.scalar(select(Category).where(Category.code == "ELECTRONICS_CAMERAS"))
    if existing is not None:
        return existing.parent, existing
    electronics = Category(
        code="ELECTRONICS",
        name="Electrónicos",
        slug=f"electronicos-{uuid.uuid4().hex[:6]}",
        is_active=True,
        sort_order=1,
    )
    cameras = Category(
        code="ELECTRONICS_CAMERAS",
        name="Cámaras y Fotografía",
        slug=f"camaras-{uuid.uuid4().hex[:6]}",
        parent=electronics,
        is_active=True,
        sort_order=1,
    )
    session.add_all([electronics, cameras])
    session.flush()
    return electronics, cameras


def _create_draft_via_selector(client, session, user: User) -> ProductDraft:
    category, subcategory = _category_tree(session)
    session.commit()
    _login(client, user)
    response = client.post(
        "/partners/products/drafts",
        data={"category_id": str(category.id), "subcategory_id": str(subcategory.id)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    draft_id = uuid.UUID(response.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    draft = session.get(ProductDraft, draft_id)
    assert draft is not None
    return draft


def test_template_registry_covers_seeded_subcategories():
    validate_template_registry()
    expected = {
        "electronics_phones",
        "electronics_computers",
        "electronics_headphones",
        "electronics_cameras",
        "fashion_men",
        "fashion_women",
        "fashion_shoes",
        "fashion_accessories",
        "home_decoration",
        "home_kitchen_tools",
        "home_cleaning",
        "beauty_personal_care",
        "beauty_cosmetics",
        "beauty_skincare",
        "automotive_accessories",
        "automotive_tools",
        "automotive_basic_parts",
        "babies_toys",
        "babies_clothing",
        "babies_care",
    }
    assert expected <= set(PRODUCT_TEMPLATES)


def test_template_registry_does_not_include_removed_package_content_field():
    validate_template_registry()
    for template in PRODUCT_TEMPLATES.values():
        assert "contenido" not in {field.key for field in template.fields}
        assert "Contenido del paquete" not in {field.label for field in template.fields}


def test_product_draft_form_removes_highlights_and_package_content_section(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Puntos destacados y contenido" not in html
    assert "Punto destacado" not in html
    assert "Cantidad en empaque" not in html
    assert "Contenido del paquete" not in html
    assert 'name="highlights[]"' not in html
    assert 'name="package_quantity[]"' not in html
    assert 'name="package_name[]"' not in html
    assert 'name="package_note[]"' not in html
    assert "Galería Multimedia" in html
    assert "Código del producto" in html
    assert "Variantes" in html
    assert "Precio de venta" in html


def test_create_and_save_draft_without_public_product_rows(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "Cámara de seguridad exterior 4MP",
            "brand": "Hikvision",
            "model_number": "DS-DEMO",
            "description": "Borrador inicial con una descripción suficientemente larga.",
            "attributes[tipo_camara]": "Seguridad",
            "attributes[resolucion_mp]": "4",
            "price": "45.00",
            "stock_quantity": "5",
            "product_weight_kg": "0.5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert saved.title == "Cámara de seguridad exterior 4MP"
    assert saved.seller_sku == f"{saved.store.product_code_prefix}-{saved.store.registration_number:08d}-000001"
    assert saved.barcode == saved.seller_sku
    assert saved.condition == "NEW"
    assert saved.status in {ProductDraftStatus.INCOMPLETE, ProductDraftStatus.READY_FOR_REVIEW}
    assert session.scalar(select(func.count()).select_from(Product)) == 0
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 0
    assert session.scalar(select(func.count()).select_from(SellerOffer)) == 0


def test_removed_fields_are_ignored_and_legacy_values_are_preserved(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    legacy_highlights = ["Dato antiguo"]
    legacy_contents = [{"quantity": "9", "name": "Contenido antiguo", "note": "No borrar"}]
    draft.highlights = legacy_highlights
    draft.package_contents = legacy_contents
    session.commit()

    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "Cámara de seguridad exterior 4MP",
            "brand": "Hikvision",
            "model_number": "DS-DEMO",
            "description": "Borrador inicial con una descripción suficientemente larga.",
            "attributes[tipo_camara]": "Seguridad",
            "attributes[resolucion_mp]": "4",
            "price": "45.00",
            "stock_quantity": "5",
            "product_weight_kg": "0.5",
            "highlights[]": ["Valor manipulado"],
            "package_quantity[]": ["1"],
            "package_name[]": ["Producto manipulado"],
            "package_note[]": ["Nota manipulada"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert saved.highlights == legacy_highlights
    assert saved.package_contents == legacy_contents
    assert 0 <= saved.completion_percentage <= 100


def test_submit_incomplete_draft_is_rejected(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/submit",
        data={"title": "Cámara"},
    )

    assert response.status_code == 400
    assert "Carga al menos 3" in response.get_data(as_text=True)
    session.expire_all()
    assert session.get(ProductDraft, draft.id).status != ProductDraftStatus.SUBMITTED


def test_valid_draft_submits_without_highlights_or_package_contents(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(3)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    response = client.post(
        f"/partners/products/drafts/{draft.id}/submit",
        data={
            "title": "Cámara de seguridad exterior 4MP",
            "brand": "Hikvision",
            "model_number": "DS-DEMO",
            "description": "Borrador listo con una descripción suficientemente detallada.",
            "attributes[tipo_camara]": "Seguridad",
            "attributes[resolucion_mp]": "4",
            "price": "45.00",
            "stock_quantity": "5",
            "product_weight_kg": "0.5",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    submitted = session.get(ProductDraft, draft.id)
    assert submitted.status == ProductDraftStatus.SUBMITTED
    assert submitted.completion_percentage == 100
    assert submitted.highlights == []
    assert submitted.package_contents == []


def test_valid_image_upload_is_private_and_authorized(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "file": (io.BytesIO(_png_bytes()), "cover.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    file_record = session.scalar(select(ProductDraftFile).where(ProductDraftFile.draft_id == draft.id))
    assert file_record is not None
    assert file_record.kind == ProductDraftFileKind.IMAGE
    assert file_record.is_cover is True
    assert not file_record.storage_key.startswith("static")

    preview = client.get(f"/partners/products/drafts/{draft.id}/files/{file_record.id}")
    assert preview.status_code == 200
    assert preview.headers["Content-Type"].startswith("image/png")


def test_gallery_uploads_multiple_images_and_uses_six_slot_limit(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(6)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.json["ok"] is True
    session.expire_all()
    images = session.scalars(
        select(ProductDraftFile)
        .where(
            ProductDraftFile.draft_id == draft.id,
            ProductDraftFile.kind == ProductDraftFileKind.IMAGE,
            ProductDraftFile.status == ProductDraftFileStatus.ACTIVE,
        )
        .order_by(ProductDraftFile.position)
    ).all()
    assert len(images) == 6
    assert [item.position for item in images] == list(range(6))
    assert [item.is_cover for item in images] == [True, False, False, False, False, False]
    assert "Galería Multimedia (6/6)" in response.json["gallery_html"]


def test_gallery_rejects_batches_that_exceed_remaining_slots(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(7)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert "6 imágenes" in response.json["errors"]["images"]
    assert session.scalar(
        select(func.count()).select_from(ProductDraftFile).where(ProductDraftFile.draft_id == draft.id)
    ) == 0


def test_gallery_reorder_persists_cover_and_compact_positions(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(3)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    session.expire_all()
    images = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.kind == ProductDraftFileKind.IMAGE)
        .order_by(ProductDraftFile.position)
    ).all()
    reordered_ids = [str(images[2].id), str(images[0].id), str(images[1].id)]

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files/reorder",
        json={"ordered_image_ids": reordered_ids},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    session.expire_all()
    reordered = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.status == ProductDraftFileStatus.ACTIVE)
        .order_by(ProductDraftFile.position)
    ).all()
    assert [str(item.id) for item in reordered] == reordered_ids
    assert reordered[0].is_cover is True
    assert all(item.is_cover is False for item in reordered[1:])


def test_gallery_delete_cover_promotes_next_image(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(3)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    session.expire_all()
    first = session.scalar(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.position == 0)
    )

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files/{first.id}/delete",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    session.expire_all()
    active = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.status == ProductDraftFileStatus.ACTIVE)
        .order_by(ProductDraftFile.position)
    ).all()
    assert len(active) == 2
    assert [item.position for item in active] == [0, 1]
    assert active[0].is_cover is True
    assert active[1].is_cover is False


def test_gallery_rejects_reorder_with_missing_or_foreign_ids(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"image-{index}.png") for index in range(2)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    session.expire_all()
    images = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.status == ProductDraftFileStatus.ACTIVE)
        .order_by(ProductDraftFile.position)
    ).all()

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files/reorder",
        json={"ordered_image_ids": [str(images[0].id), str(uuid.uuid4())]},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    session.expire_all()
    unchanged = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id, ProductDraftFile.status == ProductDraftFileStatus.ACTIVE)
        .order_by(ProductDraftFile.position)
    ).all()
    assert [item.id for item in unchanged] == [item.id for item in images]


def test_gallery_markup_uses_new_slots_without_separate_upload_button(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Galería Multimedia (0/6)" in html
    assert "data-draft-gallery" in html
    assert "data-open-gallery" in html
    assert "Subir imagen" not in html
    assert "Añadir portada" in html


def test_foreign_user_cannot_open_draft_or_file(client, session):
    owner = _user(session, email="owner-draft@test.local")
    _enabled_store(session, owner)
    draft = _create_draft_via_selector(client, session, owner)

    other = _user(session, email="other-draft@test.local")
    _enabled_store(session, other)
    session.commit()
    _login(client, other)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    assert response.status_code == 404


def test_product_code_is_generated_once_and_increments_per_store(client, session):
    user = _user(session)
    _enabled_store(session, user)
    first = _create_draft_via_selector(client, session, user)

    with client.session_transaction() as browser_session:
        browser_session.pop(PARTNER_PRODUCT_DRAFT_SESSION_KEY, None)
        browser_session.pop("partner_current_product_draft_id", None)
    second = _create_draft_via_selector(client, session, user)
    session.expire_all()
    first = session.get(ProductDraft, first.id)
    second = session.get(ProductDraft, second.id)

    assert first.seller_sku.endswith("-000001")
    assert second.seller_sku.endswith("-000002")
    assert first.barcode == first.seller_sku
    assert second.barcode == second.seller_sku
    assert session.get(StoreProductCounter, first.store_id).last_value == 2

    response = client.post(
        f"/partners/products/drafts/{first.id}/save",
        data={"title": "Producto de prueba largo"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    assert session.get(ProductDraft, first.id).seller_sku == first.seller_sku


def test_generated_code_fields_reject_form_manipulation(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    original_code = draft.seller_sku

    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "seller_sku": "MANUAL-SKU-001",
            "barcode": "OTHER-BARCODE",
            "condition": "used",
            "title": "Producto de prueba largo",
        },
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "generado por ECUVEL" in html
    assert "Todos los productos de ECUVEL deben registrarse como nuevos" in html
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert saved.seller_sku == original_code
    assert saved.barcode == original_code
    assert saved.condition == "NEW"


def test_barcode_svg_is_private_and_uses_generated_product_code(client, session):
    owner = _user(session, email="barcode-owner@test.local")
    _enabled_store(session, owner)
    draft = _create_draft_via_selector(client, session, owner)

    response = client.get(f"/partners/products/drafts/{draft.id}/barcode.svg")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/svg+xml")
    assert draft.seller_sku.encode("utf-8") in response.data

    other = _user(session, email="barcode-other@test.local")
    _enabled_store(session, other)
    session.commit()
    _login(client, other)

    forbidden = client.get(f"/partners/products/drafts/{draft.id}/barcode.svg")
    assert forbidden.status_code == 404
