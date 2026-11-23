from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import Category, Product, ProductMedia, ProductVariant, SellerOffer, User
from app.services.adult_access_policy import (
    ADULT_CATEGORY_CODES, adult_public_eligible, is_adult_category,
)
from app.services.catalog_listings import load_public_listings
from app.services.partner_product_categories import list_main_categories
from app.services.product_drafts import capture_submission_commission_snapshots
from app.services.product_publication import MODERATION_CHECKS, publish_product_draft
from tests.factories import create_catalog_and_stock
from tests.product_moderation_helpers import (
    create_complete_simple_draft, create_seller_location, create_store, create_user,
)
from tests.test_partner_product_category_selection import _user, _enabled_store, _login


pytestmark = pytest.mark.integration


def login_as(client, user):
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


@pytest.fixture
def adult_case(app, session, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", False)
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    monkeypatch.setitem(app.config, "ADULT_RELEASED_PRODUCT_IDS", "")
    monkeypatch.setitem(app.config, "PRODUCT_CATALOG_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr("app.services.age_eligibility.ecuador_local_date", lambda: date(2026, 11, 22))
    base = create_catalog_and_stock(session, stock=8)
    normal = create_catalog_and_stock(session, stock=4)
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    parent = Category(code="ADULT", name="Productos para adultos", slug="productos-para-adultos", is_active=True)
    leaf = Category(code="ADULT_TOYS", name="Juguetes íntimos", slug="juguetes-intimos", parent=parent, is_active=True)
    session.add_all([parent, leaf])
    session.flush()
    product.category_id = leaf.id
    product.title = "SENSITIVE_ADULT_PRODUCT"
    product.slug = "sensitive-adult-product"
    Image.new("RGB", (20, 20)).save(tmp_path / "master.webp", format="WEBP")
    Image.new("RGB", (10, 10)).save(tmp_path / "thumb.webp", format="WEBP")
    media = ProductMedia(
        product_id=product.id, public_id=uuid.uuid4().hex, storage_key="master.webp",
        media_type="image/webp", size_bytes=100, width=20, height=20,
        content_sha256="a" * 64, processing_version=1,
        thumbnail_storage_key="thumb.webp", thumbnail_media_type="image/webp",
        thumbnail_size_bytes=100, thumbnail_width=10, thumbnail_height=10,
        thumbnail_sha256="b" * 64, is_active=True,
    )
    session.add(media)
    buyer = session.get(User, base.buyer_id)
    buyer.email_verified_at = datetime.now(timezone.utc)
    session.commit()
    return SimpleNamespace(base=base, normal=normal, product=product, offer=offer, variant=variant,
                           parent=parent, leaf=leaf, media=media, buyer=buyer)


def enable_public(app, monkeypatch, case):
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", True)
    monkeypatch.setitem(app.config, "ADULT_RELEASED_PRODUCT_IDS", str(case.product.id))


@pytest.mark.parametrize("code", ADULT_CATEGORY_CODES)
def test_domain_recognizes_canonical_codes_and_relationship(code):
    parent = SimpleNamespace(code=code, parent=None)
    assert is_adult_category(parent)
    assert is_adult_category(SimpleNamespace(code="UNRECOGNIZED_CHILD", parent=parent))
    assert not is_adult_category(SimpleNamespace(code="FASHION", parent=None))


@pytest.mark.parametrize(("birth_date", "expected"), (
    (date(2008, 11, 22), True), (date(2008, 11, 23), False), (None, False),
))
def test_policy_uses_existing_ecuador_civil_date(adult_case, app, monkeypatch, birth_date, expected):
    enable_public(app, monkeypatch, adult_case)
    viewer = SimpleNamespace(is_authenticated=True, is_active=True, birth_date=birth_date)
    assert adult_public_eligible(viewer) is expected


def test_public_flag_and_release_are_independent_from_partner(adult_case, app, monkeypatch):
    case = adult_case
    client = app.test_client()
    login_as(client, case.buyer)
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", True)
    assert client.get(f"/productos/{case.product.slug}").status_code == 404
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", True)
    # Administrative/product-specific review is still unresolved.
    assert client.get(f"/productos/{case.product.slug}").status_code == 404
    enable_public(app, monkeypatch, case)
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", False)
    assert client.get(f"/productos/{case.product.slug}").status_code == 200
    monkeypatch.setitem(app.config, "ADULT_RELEASED_PRODUCT_IDS", "invalid-id")
    assert client.get(f"/productos/{case.product.slug}").status_code == 404


def test_seed_private_flag_does_not_enable_public_sales(app, session, monkeypatch):
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", True)
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    adult = session.scalar(select(Category).where(Category.code == "ADULT"))
    assert adult.is_active is False
    assert "ADULT" not in {item.code for item in list_main_categories(session)}
    # Category activation is a separate, explicit ECUVEL operation, never seed.
    categories = session.scalars(select(Category).where(Category.code.in_(ADULT_CATEGORY_CODES))).all()
    for category in categories:
        category.is_active = True
    session.commit()
    assert "ADULT" in {item.code for item in list_main_categories(session)}
    assert app.config["ADULT_PUBLIC_ENABLED"] is False
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", False)
    assert "ADULT" not in {item.code for item in list_main_categories(session)}
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    session.expire_all()
    assert adult.is_active is False


def test_partner_crafted_posts_and_existing_draft_mutations_recheck_flag(app, session, monkeypatch):
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", True)
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    assert app.test_cli_runner().invoke(args=["seed-product-categories"]).exit_code == 0
    parent = session.scalar(select(Category).where(Category.code == "ADULT"))
    leaf = session.scalar(select(Category).where(Category.code == "ADULT_TOYS"))
    parent.is_active = leaf.is_active = True
    user = _user(session)
    _enabled_store(session, user)
    session.commit()
    client = app.test_client()
    _login(client, user)
    created = client.post("/partners/products/drafts", data={"category_id": parent.id, "subcategory_id": leaf.id})
    assert created.status_code == 302
    draft_url = created.headers["Location"]
    assert client.get(draft_url).status_code == 200
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", False)
    assert parent.is_active and leaf.is_active
    assert client.post("/partners/products/drafts", data={"category_id": parent.id, "subcategory_id": leaf.id}).status_code == 400
    assert client.get(draft_url).status_code == 404
    assert client.post(f"{draft_url}/save", data={"title": "Crafted save"}).status_code == 404
    assert client.post(f"{draft_url}/submit").status_code in (400, 404)


def test_admin_approval_can_materialize_private_product_without_public_release(app, session, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "ADULT_PARTNER_ENABLED", True)
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    assert runner.invoke(args=["marketplace-policy", "bootstrap"]).exit_code == 0
    parent = session.scalar(select(Category).where(Category.code == "ADULT"))
    leaf = session.scalar(select(Category).where(Category.code == "ADULT_GAMES"))
    parent.is_active = leaf.is_active = True
    seller, moderator = create_user(session), create_user(session, staff=True)
    store = create_store(session)
    create_seller_location(session, store)
    draft = create_complete_simple_draft(session, seller=seller, store=store, category=parent, subcategory=leaf, media_root=tmp_path / "drafts")
    draft.template_key = "adult_games"
    draft.attributes = {"tipo_producto": "Juego de cartas"}
    draft.variant_configuration = {}
    capture_submission_commission_snapshots(session, draft)
    session.commit()
    result = publish_product_draft(session, draft_id=draft.id, actor_user_id=moderator.id,
        checklist={key: True for key in MODERATION_CHECKS}, source_media_root=tmp_path / "drafts", catalog_media_root=tmp_path / "catalog")
    session.commit()
    assert result.product.is_active
    assert load_public_listings(session, product_id=result.product.id) == []
    assert app.test_client().get(f"/productos/{result.product.slug}").status_code == 404
