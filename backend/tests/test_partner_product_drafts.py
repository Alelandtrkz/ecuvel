from __future__ import annotations

import io
import json
import re
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


def _phone_category_tree(session):
    existing = session.scalar(select(Category).where(Category.code == "ELECTRONICS_PHONES"))
    if existing is not None:
        return existing.parent, existing
    electronics = Category(
        code="ELECTRONICS",
        name="Electrónicos",
        slug=f"electronicos-{uuid.uuid4().hex[:6]}",
        is_active=True,
        sort_order=1,
    )
    phones = Category(
        code="ELECTRONICS_PHONES",
        name="Teléfonos y Accesorios",
        slug=f"telefonos-{uuid.uuid4().hex[:6]}",
        parent=electronics,
        is_active=True,
        sort_order=1,
    )
    session.add_all([electronics, phones])
    session.flush()
    return electronics, phones


def _create_phone_draft(client, session, user: User) -> ProductDraft:
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    response = client.post(
        "/partners/products/drafts",
        data={"category_id": str(category.id), "subcategory_id": str(subcategory.id)},
        follow_redirects=False,
    )
    draft_id = uuid.UUID(response.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    return session.get(ProductDraft, draft_id)


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


def test_electronics_templates_expose_unit_guidance_and_icons():
    validate_template_registry()
    required_units = {
        "electronics_phones": {
            "ram_gb": "GB",
            "almacenamiento_gb": "GB",
            "pantalla_pulgadas": "pulgadas",
            "camara_principal_mp": "MP",
            "bateria_mah": "mAh",
            "potencia_w": "W",
            "voltaje": "V",
            "corriente_a": "A",
            "longitud_cm": "cm",
        },
        "electronics_computers": {
            "ram_gb": "GB",
            "almacenamiento_gb": "GB",
            "pantalla_pulgadas": "pulgadas",
            "bateria_mah": "mAh",
            "frecuencia_hz": "Hz",
        },
        "electronics_cameras": {
            "resolucion_mp": "MP",
            "bateria_mah": "mAh",
        },
        "electronics_headphones": {
            "autonomia_horas": "horas",
        },
    }
    for template_key, fields in required_units.items():
        template_fields = {field.key: field for field in PRODUCT_TEMPLATES[template_key].fields}
        for field_key, unit_label in fields.items():
            field = template_fields[field_key]
            assert field.unit_label == unit_label
            assert field.help
            assert field.example
            assert field.icon


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
    assert "Administrar variantes" in html
    assert "Galerías por color" in html
    assert "data-variant-axis-select-button" in html
    assert "data-variant-axis-select-menu" in html
    assert "Estado</span><span>Variante</span><span>SKU" in html
    assert "Presentación individual" in html
    assert "Agregar variante" in html
    assert "Generar combinaciones" not in html
    assert "Precio de venta" in html
    assert 'data-partner-select' in html
    assert 'class="partner-select__native"' in html
    assert 'data-partner-select-button' in html
    assert 'class="partner-boolean-control"' in html
    assert 'class="partner-boolean-control__input"' in html
    assert 'class="partner-input-with-unit"' in html
    assert 'data-lucide="camera"' in html
    assert 'data-lucide="battery-charging"' in html
    assert "MP" in html
    assert "mAh" in html
    assert "Resolución de foto o sensor en megapíxeles." in html
    assert "Capacidad de batería en miliamperios-hora." in html


def test_family_variants_do_not_write_default_values_or_inventory_to_mother(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_phone_draft(client, session, user)
    configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "axes": [{"key": "color_principal"}, {"key": "almacenamiento_gb"}],
        "default_variant_id": "green-512",
    }
    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "iPhone 17 Pro Max",
            "brand": "Apple",
            "model_number": "17 Pro Max",
            "attributes[tipo_producto]": "Smartphone",
            "attributes[color_principal]": "Azul",
            "attributes[almacenamiento_gb]": "256",
            "price": "999",
            "compare_at_price": "1099",
            "stock_quantity": "8",
            "has_variants": "1",
            "variant_configuration": json.dumps(configuration),
            "variant_id[]": "green-512",
            "variant_options[]": json.dumps({
                "color_principal": {"label": "Verde", "swatch": "#16A34A"},
                "almacenamiento_gb": {"label": "512"},
            }),
            "variant_combination_key[]": "",
            "variant_price[]": "1199",
            "variant_compare_at_price[]": "1299",
            "variant_stock[]": "3",
            "variant_enabled[]": "1",
            "variant_default_choice": "green-512",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert saved.variant_configuration["version"] == 4
    assert saved.variant_configuration["mode"] == "family"
    assert saved.attributes["color_principal"] is None
    assert saved.attributes["almacenamiento_gb"] is None
    assert saved.pricing_data["price"] is None
    assert saved.inventory_data["stock_quantity"] is None
    assert saved.variant_configuration["source_snapshot"]["attributes"] == {
        "color_principal": "Azul",
        "almacenamiento_gb": "256",
    }
    assert saved.variants[0]["attributes"] == {
        "color_principal": "Verde",
        "almacenamiento_gb": "512",
    }
    assert saved.variants[0]["compare_at_price"] == "1299"
    editor = client.get(f"/partners/products/drafts/{draft.id}")
    editor_html = editor.get_data(as_text=True)
    assert editor.status_code == 200
    assert "partner-variant-gallery-accordion" in editor_html
    assert "0 imágenes" in editor_html
    assert "1 variante" in editor_html
    assert "3 en stock" in editor_html
    preview = client.get(f"/partners/products/drafts/{draft.id}/preview")
    assert preview.status_code == 200
    preview_html = preview.get_data(as_text=True)
    assert "Resumen del borrador" in preview_html
    assert "Familia" in preview_html
    assert 'role="tablist"' in preview_html
    assert 'aria-selected="true"' in preview_html


def test_private_storefront_preview_selects_variants_and_isolates_color_media(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_phone_draft(client, session, user)
    configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "axes": [{"key": "color_principal"}],
        "default_variant_id": "blue-256",
    }
    saved = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "iPhone 17 Pro Max",
            "description": "Descripción suficientemente completa para la vista previa privada.",
            "attributes[tipo_producto]": "Smartphone",
            "has_variants": "1",
            "variant_configuration": json.dumps(configuration),
            "variant_id[]": ["blue-256", "red-256"],
            "variant_options[]": [
                json.dumps({"color_principal": {"label": "Azul", "swatch": "#085DF8"}}),
                json.dumps({"color_principal": {"label": "Rojo", "swatch": "#DC2626"}}),
            ],
            "variant_combination_key[]": ["", ""],
            "variant_price[]": ["999", "1049"],
            "variant_compare_at_price[]": ["1099", ""],
            "variant_stock[]": ["0", "4"],
            "variant_enabled[]": ["1", "1"],
            "variant_default_choice": "blue-256",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 302
    for filename, value_key in (("blue.png", "azul"), ("red.png", "rojo")):
        upload = client.post(
            f"/partners/products/drafts/{draft.id}/files",
            data={
                "kind": "IMAGE",
                "file": _image_tuple(filename),
                "variant_axis_key": "color_principal",
                "variant_value_key": value_key,
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        assert upload.status_code == 200
    session.expire_all()
    draft = session.get(ProductDraft, draft.id)
    images = session.scalars(
        select(ProductDraftFile)
        .where(ProductDraftFile.draft_id == draft.id)
        .order_by(ProductDraftFile.position)
    ).all()
    assert len(images) == 2
    session.expire_all()
    draft = session.get(ProductDraft, draft.id)

    default_preview = client.get(
        f"/partners/products/drafts/{draft.id}/preview?view=storefront"
    )
    default_html = default_preview.get_data(as_text=True)
    assert default_preview.status_code == 200
    default_title = re.search(r'<h1 id="product-title">([^<]+)</h1>', default_html)
    assert default_title is not None
    assert default_title.group(1) == "iPhone 17 Pro Max — Rojo"
    assert default_html.count(str(images[1].id)) > default_html.count(str(images[0].id))

    blue_sku = f"{draft.seller_sku}-V01"
    selected_preview = client.get(
        f"/partners/products/drafts/{draft.id}/preview?view=storefront&variant={blue_sku}"
    )
    selected_html = selected_preview.get_data(as_text=True)
    assert selected_preview.status_code == 200
    selected_title = re.search(r'<h1 id="product-title">([^<]+)</h1>', selected_html)
    assert selected_title is not None
    assert selected_title.group(1) == "iPhone 17 Pro Max — Azul"
    assert selected_html.count(str(images[0].id)) > selected_html.count(str(images[1].id))
    assert "Producto agotado" in selected_html
    assert 'data-preview-commercial' in selected_html
    assert 'class="purchase-card__cart-form" method="post"' not in selected_html
    assert 'data-favorite-form' not in selected_html
    assert "Disponible cuando el producto sea publicado" in selected_html


def test_incomplete_simple_draft_still_renders_storefront_placeholders(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)

    response = client.get(
        f"/partners/products/drafts/{draft.id}/preview?view=storefront"
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Producto sin título" in html
    assert "Precio pendiente" in html
    assert "Stock pendiente" in html
    assert "Imagen de presentación" in html
    assert "Descripción pendiente" in html
    assert 'data-preview-commercial' in html


def test_family_activation_recovers_saved_mother_values_when_variant_inputs_are_omitted(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_phone_draft(client, session, user)
    first_save = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "iPhone 17 Pro Max",
            "attributes[tipo_producto]": "Smartphone",
            "attributes[color_principal]": "Azul",
            "attributes[almacenamiento_gb]": "256",
            "price": "999",
            "compare_at_price": "1099",
            "stock_quantity": "8",
        },
    )
    assert first_save.status_code == 302

    family_save = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "iPhone 17 Pro Max",
            "attributes[tipo_producto]": "Smartphone",
            "has_variants": "1",
            "variant_configuration": json.dumps({
                "version": 4,
                "enabled": True,
                "mode": "family",
                "axes": [
                    {"key": "color_principal"},
                    {"key": "almacenamiento_gb"},
                ],
                "source_snapshot": {},
            }),
        },
    )
    assert family_save.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert saved.variant_configuration["source_snapshot"] == {
        "attributes": {"color_principal": "Azul", "almacenamiento_gb": "256"},
        "price": "999",
        "compare_at_price": "1099",
        "stock": "8",
    }


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


def test_saved_draft_can_be_submitted_from_summary_without_creating_catalog_rows(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    upload = client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={
            "kind": "IMAGE",
            "files": [_image_tuple(f"saved-{index}.png") for index in range(3)],
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert upload.status_code == 200
    saved = client.post(
        f"/partners/products/drafts/{draft.id}/save",
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
    assert saved.status_code == 302

    summary = client.get(f"/partners/products/drafts/{draft.id}/preview")
    summary_html = summary.get_data(as_text=True)
    assert summary.status_code == 200
    assert "Enviar a revisión" in summary_html
    assert f'/partners/products/drafts/{draft.id}/submit-saved' in summary_html

    response = client.post(
        f"/partners/products/drafts/{draft.id}/submit-saved",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "view=summary" in response.headers["Location"]
    session.expire_all()
    submitted = session.get(ProductDraft, draft.id)
    assert submitted.status == ProductDraftStatus.SUBMITTED
    assert submitted.completion_percentage == 100
    assert session.scalar(select(func.count()).select_from(Product)) == 0
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 0
    assert session.scalar(select(func.count()).select_from(SellerOffer)) == 0


def test_submit_saved_rejects_incomplete_draft_without_overwriting_it(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    draft.title = "Título persistido incompleto"
    session.commit()

    response = client.post(
        f"/partners/products/drafts/{draft.id}/submit-saved",
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Revisa la información del producto" in html
    assert "Completa los pendientes antes de enviar" in html
    session.expire_all()
    persisted = session.get(ProductDraft, draft.id)
    assert persisted.title == "Título persistido incompleto"
    assert persisted.status != ProductDraftStatus.SUBMITTED


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
    assert html.count("data-open-gallery") == 6
    assert "Subir imagen" not in html
    assert "Añadir portada" in html


def test_unassigned_image_can_be_moved_to_a_color_without_data_loss(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={"kind": "IMAGE", "file": _image_tuple("recoverable.png")},
        content_type="multipart/form-data",
    )
    session.expire_all()
    draft = session.get(ProductDraft, draft.id)
    draft.variant_configuration = {
        "version": 2,
        "visual_axis_key": "color_principal",
        "axes": [
            {
                "key": "color_principal",
                "label": "Color",
                "is_visual": True,
                "values": [
                    {"key": "negro", "label": "Negro"},
                    {"key": "azul", "label": "Azul"},
                ],
            }
        ],
    }
    session.commit()
    image = session.scalar(
        select(ProductDraftFile).where(ProductDraftFile.draft_id == draft.id)
    )

    response = client.post(
        f"/partners/products/drafts/{draft.id}/files/{image.id}/assign",
        data={
            "variant_axis_key": "color_principal",
            "variant_value_key": "azul",
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    session.expire_all()
    moved = session.get(ProductDraftFile, image.id)
    assert moved.status == ProductDraftFileStatus.ACTIVE
    assert moved.variant_axis_key == "color_principal"
    assert moved.variant_value_key == "azul"
    assert moved.is_cover is True
    assert moved.storage_key == image.storage_key
    assert "Azul" in response.json["gallery_html"]


def test_color_gallery_can_be_permanently_deleted_after_explicit_request(client, session):
    user = _user(session)
    _enabled_store(session, user)
    draft = _create_draft_via_selector(client, session, user)
    client.post(
        f"/partners/products/drafts/{draft.id}/files",
        data={"kind": "IMAGE", "file": _image_tuple("blue.png")},
        content_type="multipart/form-data",
    )
    session.expire_all()
    draft = session.get(ProductDraft, draft.id)
    draft.variant_configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "visual_axis_key": "color_principal",
        "axes": [{
            "key": "color_principal",
            "label": "Color",
            "is_visual": True,
            "values": [{"key": "azul", "label": "Azul"}],
        }],
    }
    session.commit()
    image = session.scalar(select(ProductDraftFile).where(ProductDraftFile.draft_id == draft.id))
    client.post(
        f"/partners/products/drafts/{draft.id}/files/{image.id}/assign",
        data={"variant_axis_key": "color_principal", "variant_value_key": "azul"},
        headers={"Accept": "application/json"},
    )

    response = client.post(
        f"/partners/products/drafts/{draft.id}/variant-media/delete",
        data={"variant_axis_key": "color_principal", "variant_value_key": "azul"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.json["deleted_count"] == 1
    session.expire_all()
    assert session.get(ProductDraftFile, image.id).status == ProductDraftFileStatus.DELETED


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
    preview = client.get(f"/partners/products/drafts/{draft.id}/preview")
    assert preview.status_code == 404
    submit = client.post(f"/partners/products/drafts/{draft.id}/submit-saved")
    assert submit.status_code == 404


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
