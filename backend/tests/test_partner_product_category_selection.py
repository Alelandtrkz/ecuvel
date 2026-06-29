from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Category,
    Product,
    ProductDraft,
    ProductVariant,
    SellerOffer,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
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
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _user(session, *, email: str | None = None, verified: bool = True) -> User:
    email = email or f"partner-{uuid.uuid4().hex}@test.local"
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name="Partner Ecuvel",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
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


def _enabled_store(
    session,
    user: User,
    *,
    role: StoreMemberRole = StoreMemberRole.OWNER,
    store_status: StoreStatus = StoreStatus.ACTIVE,
    verified: bool = True,
    onboarding_status: StoreOnboardingStatus = StoreOnboardingStatus.COMPLETED,
    contract_status: StoreContractAcceptanceStatus = StoreContractAcceptanceStatus.ACCEPTED,
    member_active: bool = True,
) -> Store:
    store = Store(
        public_code=f"ST-{uuid.uuid4().hex[:8].upper()}",
        name="Tienda Partner",
        slug=f"tienda-partner-{uuid.uuid4().hex[:8]}",
        legal_name="Tienda Partner",
        tax_id=f"RUC-{uuid.uuid4().hex[:12]}",
        status=store_status,
        is_verified=verified,
    )
    session.add(store)
    session.flush()
    member = StoreMember(
        store_id=store.id,
        user_id=user.id,
        role=role,
        is_active=member_active,
    )
    onboarding = StoreOnboarding(
        user_id=user.id,
        store_id=store.id,
        status=onboarding_status,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=store.name,
        legal_id_number="210049391",
        completed_at=datetime.now(timezone.utc) if onboarding_status == StoreOnboardingStatus.COMPLETED else None,
    )
    session.add_all([member, onboarding])
    session.flush()
    acceptance = StoreContractAcceptance(
        onboarding_id=onboarding.id,
        contract_version="test-v1",
        annex_version="test-a1",
        status=contract_status,
        accepted_terms=contract_status == StoreContractAcceptanceStatus.ACCEPTED,
        otp_verified=contract_status == StoreContractAcceptanceStatus.ACCEPTED,
        accepted_at=datetime.now(timezone.utc) if contract_status == StoreContractAcceptanceStatus.ACCEPTED else None,
    )
    session.add(acceptance)
    session.flush()
    return store


def _category_tree(
    session,
    *,
    parent_active: bool = True,
    child_active: bool = True,
):
    electronics = Category(
        code=f"ELECTRONICS_{uuid.uuid4().hex[:6]}",
        name="Electrónicos",
        slug=f"electronicos-{uuid.uuid4().hex[:6]}",
        is_active=parent_active,
        sort_order=1,
    )
    fashion = Category(
        code=f"FASHION_{uuid.uuid4().hex[:6]}",
        name="Moda",
        slug=f"moda-{uuid.uuid4().hex[:6]}",
        is_active=True,
        sort_order=2,
    )
    cameras = Category(
        code="ELECTRONICS_CAMERAS",
        name="Cámaras y Fotografía",
        slug=f"camaras-{uuid.uuid4().hex[:6]}",
        parent=electronics,
        is_active=child_active,
        sort_order=1,
    )
    shoes = Category(
        code=f"FASHION_SHOES_{uuid.uuid4().hex[:6]}",
        name="Calzado",
        slug=f"calzado-{uuid.uuid4().hex[:6]}",
        parent=fashion,
        is_active=True,
        sort_order=1,
    )
    session.add_all([electronics, fashion, cameras, shoes])
    session.flush()
    return electronics, cameras, fashion, shoes


def test_anonymous_user_is_redirected_to_login(client):
    response = client.get("/partners/products/new/category")
    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_authenticated_user_without_store_cannot_open_selector(client, session):
    user = _user(session)
    _category_tree(session)
    session.commit()

    assert _login(client, user).status_code == 302
    response = client.get("/partners/products/new/category")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/partners")


@pytest.mark.parametrize(
    ("role", "store_status", "verified", "onboarding_status", "contract_status", "member_active"),
    [
        (StoreMemberRole.VIEWER, StoreStatus.ACTIVE, True, StoreOnboardingStatus.COMPLETED, StoreContractAcceptanceStatus.ACCEPTED, True),
        (StoreMemberRole.OWNER, StoreStatus.PENDING_REVIEW, True, StoreOnboardingStatus.COMPLETED, StoreContractAcceptanceStatus.ACCEPTED, True),
        (StoreMemberRole.OWNER, StoreStatus.ACTIVE, False, StoreOnboardingStatus.COMPLETED, StoreContractAcceptanceStatus.ACCEPTED, True),
        (StoreMemberRole.OWNER, StoreStatus.ACTIVE, True, StoreOnboardingStatus.SUBMITTED, StoreContractAcceptanceStatus.ACCEPTED, True),
        (StoreMemberRole.OWNER, StoreStatus.ACTIVE, True, StoreOnboardingStatus.COMPLETED, StoreContractAcceptanceStatus.PENDING, True),
        (StoreMemberRole.OWNER, StoreStatus.ACTIVE, True, StoreOnboardingStatus.COMPLETED, StoreContractAcceptanceStatus.ACCEPTED, False),
    ],
)
def test_store_must_be_authorized_to_open_selector(
    client,
    session,
    role,
    store_status,
    verified,
    onboarding_status,
    contract_status,
    member_active,
):
    user = _user(session)
    _enabled_store(
        session,
        user,
        role=role,
        store_status=store_status,
        verified=verified,
        onboarding_status=onboarding_status,
        contract_status=contract_status,
        member_active=member_active,
    )
    _category_tree(session)
    session.commit()

    assert _login(client, user).status_code == 302
    response = client.get("/partners/products/new/category")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/partners")


@pytest.mark.parametrize("role", [StoreMemberRole.OWNER, StoreMemberRole.ADMINISTRATOR])
def test_owner_and_administrator_can_open_category_selector(client, session, role):
    user = _user(session)
    _enabled_store(session, user, role=role)
    _category_tree(session)
    session.commit()

    _login(client, user)
    response = client.get("/partners/products/new/category")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Seleccione la categoría y plantilla" in html
    assert "Electrónicos" in html
    assert "Cámaras y Fotografía" in html
    assert "Moda" in html
    assert "template_key" in html


def test_valid_selection_is_saved_in_session_and_details_show_template_key(client, session):
    user = _user(session)
    _enabled_store(session, user)
    electronics, cameras, _fashion, _shoes = _category_tree(session)
    session.commit()

    _login(client, user)
    response = client.post(
        "/partners/products/new/category",
        data={"category_id": str(electronics.id), "subcategory_id": str(cameras.id)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/partners/products/drafts/" in response.headers["Location"]
    with client.session_transaction() as browser_session:
        draft = browser_session[PARTNER_PRODUCT_DRAFT_SESSION_KEY]
    assert draft["category_id"] == str(electronics.id)
    assert draft["subcategory_id"] == str(cameras.id)
    assert draft["template_key"] == "electronics_cameras"

    created_draft = session.scalar(select(ProductDraft))
    assert created_draft is not None
    assert created_draft.category_id == electronics.id
    assert created_draft.subcategory_id == cameras.id
    assert created_draft.template_key == "electronics_cameras"

    details = client.get("/partners/products/new/details")
    assert details.status_code == 302
    details = client.get(response.headers["Location"])
    assert details.status_code == 200
    html = details.get_data(as_text=True)
    assert "Electrónicos" in html
    assert "Cámaras y Fotografía" in html
    assert "Electronics Cameras" in html


def test_foreign_subcategory_is_rejected(client, session):
    user = _user(session)
    _enabled_store(session, user)
    electronics, _cameras, _fashion, shoes = _category_tree(session)
    session.commit()

    _login(client, user)
    response = client.post(
        "/partners/products/new/category",
        data={"category_id": str(electronics.id), "subcategory_id": str(shoes.id)},
    )

    assert response.status_code == 400
    assert "no pertenece a la categoría" in response.get_data(as_text=True)
    with client.session_transaction() as browser_session:
        assert PARTNER_PRODUCT_DRAFT_SESSION_KEY not in browser_session


@pytest.mark.parametrize(
    ("parent_active", "child_active"),
    [(False, True), (True, False)],
)
def test_inactive_categories_are_rejected(client, session, parent_active, child_active):
    user = _user(session)
    _enabled_store(session, user)
    electronics, cameras, _fashion, _shoes = _category_tree(
        session,
        parent_active=parent_active,
        child_active=child_active,
    )
    session.commit()

    _login(client, user)
    response = client.post(
        "/partners/products/new/category",
        data={"category_id": str(electronics.id), "subcategory_id": str(cameras.id)},
    )

    assert response.status_code == 400
    assert "ya no está disponible" in response.get_data(as_text=True)


def test_cancel_keeps_product_tables_empty(client, session):
    user = _user(session)
    _enabled_store(session, user)
    _category_tree(session)
    session.commit()

    _login(client, user)
    response = client.get("/partners/products")
    assert response.status_code == 200
    assert "Subir producto" in response.get_data(as_text=True)
    assert session.scalar(select(func.count()).select_from(Product)) == 0
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 0
    assert session.scalar(select(func.count()).select_from(SellerOffer)) == 0


def test_seed_product_categories_is_idempotent(app, session):
    runner = app.test_cli_runner()

    first = runner.invoke(args=["seed-product-categories"])
    second = runner.invoke(args=["seed-product-categories"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    session.expire_all()
    managed_codes = {
        "ELECTRONICS",
        "ELECTRONICS_PHONES",
        "ELECTRONICS_CAMERAS",
        "FASHION",
        "HOME_KITCHEN",
        "BEAUTY_HEALTH",
        "AUTOMOTIVE",
        "BABIES_KIDS",
    }
    rows = session.scalars(select(Category).where(Category.code.in_(managed_codes))).all()
    assert {row.code for row in rows} == managed_codes
    electronics = session.scalar(select(Category).where(Category.code == "ELECTRONICS"))
    cameras = session.scalar(select(Category).where(Category.code == "ELECTRONICS_CAMERAS"))
    assert electronics.parent_id is None
    assert cameras.parent_id == electronics.id

    category_count = session.scalar(select(func.count()).select_from(Category))
    third = runner.invoke(args=["seed-product-categories"])
    assert third.exit_code == 0
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Category)) == category_count
