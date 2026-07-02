from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    ProductTemplateField,
    field_def,
    validate_template_registry,
)
from app.extensions import db
from app.models import (
    Category,
    ProductDraft,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
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


# ---------------------------------------------------------------------------
# Helpers (integration tests only)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(app, tmp_path):
    app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"] = str(tmp_path / "draft-files")
    app.config["PARTNER_PRODUCT_MIN_IMAGES"] = 3
    app.config["PARTNER_PRODUCT_MAX_IMAGES"] = 6
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(session: Session, *, email: str | None = None) -> User:
    email = email or f"qo-partner-{uuid.uuid4().hex}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="QO Partner",
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


def _enabled_store(session: Session, user: User) -> Store:
    store = Store(
        public_code=f"ST-{uuid.uuid4().hex[:8].upper()}",
        name="Tienda QO",
        slug=f"tienda-qo-{uuid.uuid4().hex[:8]}",
        legal_name="Tienda QO",
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
    member = StoreMember(store_id=store.id, user_id=user.id, role=StoreMemberRole.OWNER, is_active=True)
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


def _phone_category_tree(session: Session):
    electronics = Category(
        code="ELECTRONICS",
        name="Electrónicos",
        slug=f"electronics-{uuid.uuid4().hex[:6]}",
        is_active=True,
        sort_order=1,
    )
    phones = Category(
        code="ELECTRONICS_PHONES",
        name="Teléfonos y accesorios",
        slug=f"telefonos-{uuid.uuid4().hex[:6]}",
        parent=electronics,
        is_active=True,
        sort_order=1,
    )
    session.add_all([electronics, phones])
    session.flush()
    return electronics, phones


def _camera_category_tree(session: Session):
    electronics = Category(
        code="ELECTRONICS",
        name="Electrónicos",
        slug=f"electronics-{uuid.uuid4().hex[:6]}",
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


def _create_draft(client, session: Session, category: Category, subcategory: Category) -> ProductDraft:
    response = client.post(
        "/partners/products/drafts",
        data={"category_id": str(category.id), "subcategory_id": str(subcategory.id)},
        follow_redirects=False,
    )
    assert response.status_code == 302, f"Expected 302, got {response.status_code}"
    draft_id = uuid.UUID(response.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    draft = session.get(ProductDraft, draft_id)
    assert draft is not None
    return draft


# ---------------------------------------------------------------------------
# Group 1: DataClass / factory (3 tests)
# ---------------------------------------------------------------------------

def test_product_template_field_has_quick_options_attribute():
    f = ProductTemplateField(key="test", label="Test")
    assert hasattr(f, "quick_options")
    assert f.quick_options == ()


def test_field_def_accepts_quick_options_kwarg():
    f = field_def("ram_gb", "RAM", type="integer", quick_options=("4 GB|4", "8 GB|8"))
    assert f.quick_options == ("4 GB|4", "8 GB|8")


def test_field_def_quick_options_defaults_to_empty_tuple():
    f = field_def("some_field", "Some Field")
    assert f.quick_options == ()


# ---------------------------------------------------------------------------
# Group 2: Phone template (5 tests)
# ---------------------------------------------------------------------------

def test_phone_template_ram_field_has_five_quick_options():
    template = PRODUCT_TEMPLATES["electronics_phones"]
    ram = next(f for f in template.fields if f.key == "ram_gb")
    assert len(ram.quick_options) == 5


def test_phone_template_storage_field_has_five_quick_options():
    template = PRODUCT_TEMPLATES["electronics_phones"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    assert len(storage.quick_options) == 5


def test_phone_template_ram_chip_values():
    template = PRODUCT_TEMPLATES["electronics_phones"]
    ram = next(f for f in template.fields if f.key == "ram_gb")
    values = [opt.split("|")[1] if "|" in opt else opt for opt in ram.quick_options]
    assert values == ["4", "6", "8", "12", "16"]


def test_phone_template_storage_chip_values_include_1024_for_one_tb():
    template = PRODUCT_TEMPLATES["electronics_phones"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    values = [opt.split("|")[1] if "|" in opt else opt for opt in storage.quick_options]
    assert "1024" in values


def test_phone_template_storage_chip_labels_include_one_tb():
    template = PRODUCT_TEMPLATES["electronics_phones"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    labels = [opt.split("|")[0] for opt in storage.quick_options]
    assert "1 TB" in labels


# ---------------------------------------------------------------------------
# Group 3: Computer template (5 tests)
# ---------------------------------------------------------------------------

def test_computer_template_ram_field_has_five_quick_options():
    template = PRODUCT_TEMPLATES["electronics_computers"]
    ram = next(f for f in template.fields if f.key == "ram_gb")
    assert len(ram.quick_options) == 5


def test_computer_template_storage_field_has_five_quick_options():
    template = PRODUCT_TEMPLATES["electronics_computers"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    assert len(storage.quick_options) == 5


def test_computer_template_ram_chip_values():
    template = PRODUCT_TEMPLATES["electronics_computers"]
    ram = next(f for f in template.fields if f.key == "ram_gb")
    values = [opt.split("|")[1] if "|" in opt else opt for opt in ram.quick_options]
    assert values == ["4", "8", "16", "32", "64"]


def test_computer_template_storage_chip_values_include_2048_for_two_tb():
    template = PRODUCT_TEMPLATES["electronics_computers"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    values = [opt.split("|")[1] if "|" in opt else opt for opt in storage.quick_options]
    assert "2048" in values


def test_computer_template_storage_chip_labels_include_two_tb():
    template = PRODUCT_TEMPLATES["electronics_computers"]
    storage = next(f for f in template.fields if f.key == "almacenamiento_gb")
    labels = [opt.split("|")[0] for opt in storage.quick_options]
    assert "2 TB" in labels


# ---------------------------------------------------------------------------
# Group 4: Validation / regression (2 tests)
# ---------------------------------------------------------------------------

def test_validate_template_registry_passes_with_quick_options():
    validate_template_registry()


def test_non_phone_computer_templates_have_no_quick_options():
    keys_with_quick_options = {"electronics_phones", "electronics_computers"}
    for key, template in PRODUCT_TEMPLATES.items():
        if key in keys_with_quick_options:
            continue
        for f in template.fields:
            assert f.quick_options == (), (
                f"Template '{key}' field '{f.key}' unexpectedly has quick_options: {f.quick_options}"
            )


# ---------------------------------------------------------------------------
# Group 5: Rendering (4 integration tests)
# ---------------------------------------------------------------------------

def test_phone_draft_form_renders_chip_container_for_ram(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-quick-options' in html
    assert 'partner-attribute-chip' in html
    assert 'name="attributes[ram_gb]"' in html


def test_phone_draft_form_renders_chip_container_for_storage(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="attributes[almacenamiento_gb]"' in html
    assert '64 GB' in html
    assert '1 TB' in html


def test_phone_draft_form_one_tb_chip_has_data_value_1024(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-value="1024"' in html


def test_camera_draft_form_has_no_quick_option_chips(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _camera_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.get(f"/partners/products/drafts/{draft.id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'partner-attribute-chip' not in html
    assert 'data-quick-options' not in html


# ---------------------------------------------------------------------------
# Group 6: Persistence (2 integration tests)
# ---------------------------------------------------------------------------

def test_phone_draft_saves_ram_attribute_as_integer(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "Teléfono de prueba",
            "brand": "MarcaTest",
            "model_number": "Model-001",
            "description": "Descripción del teléfono de prueba con suficiente longitud.",
            "attributes[tipo_producto]": "Smartphone",
            "attributes[ram_gb]": "8",
            "price": "199.99",
            "stock_quantity": "10",
            "product_weight_kg": "0.18",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert str(saved.attributes.get("ram_gb")) == "8"


def test_phone_draft_saves_storage_1024_as_attribute(client, session):
    user = _user(session)
    _enabled_store(session, user)
    category, subcategory = _phone_category_tree(session)
    session.commit()
    _login(client, user)
    draft = _create_draft(client, session, category, subcategory)

    response = client.post(
        f"/partners/products/drafts/{draft.id}/save",
        data={
            "title": "Teléfono de prueba TB",
            "brand": "MarcaTest",
            "model_number": "Model-TB",
            "description": "Descripción del teléfono de prueba con suficiente longitud.",
            "attributes[tipo_producto]": "Smartphone",
            "attributes[almacenamiento_gb]": "1024",
            "price": "299.99",
            "stock_quantity": "5",
            "product_weight_kg": "0.18",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    session.expire_all()
    saved = session.get(ProductDraft, draft.id)
    assert str(saved.attributes.get("almacenamiento_gb")) == "1024"
