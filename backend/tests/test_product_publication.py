from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.catalog.product_templates import PRODUCT_TEMPLATES
from app.models import (
    Category,
    InventoryBalance,
    InventoryMovement,
    MarketplaceCommissionRule,
    Product,
    ProductDraft,
    ProductDraftModerationEvent,
    ProductDraftPublication,
    ProductMedia,
    ProductVariant,
    SellerOffer,
    StoreContractAcceptance,
    StoreInventoryLocation,
    StoreMember,
    StoreOnboarding,
    Warehouse,
    WarehouseLocation,
)
from app.commands.marketplace_policy import INITIAL_CATEGORY_RATES
from app.models.enums import (
    LocationType,
    OfferStatus,
    ProductDraftStatus,
    SellerCommissionType,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
)
from app.services.admin_operating_context import warehouse_options
from app.services.admin_products import commission_snapshot_complete
from app.services.marketplace_policy import (
    CommissionRuleMissingError,
    CommissionSnapshotError,
    InvalidSellerPriceError,
    MINIMUM_PRICE_MESSAGE,
    commission_from_snapshot,
    ensure_store_inventory_location,
    resolve_default_store_inventory_location,
    resolve_marketplace_commission,
)
from app.services.product_drafts import (
    ProductDraftValidationError,
    capture_submission_commission_snapshots,
    draft_commission_display_rows,
    submit_saved_product_draft,
)
from app.services.product_publication import (
    MODERATION_CHECKS,
    ProductModerationStateError,
    ProductModerationValidationError,
    normalize_moderation_checklist,
    publish_product_draft,
    record_moderation_decision,
)
from app.services.product_variant_builder import family_variants_enabled
from tests.product_moderation_helpers import (
    create_commission_rule,
    create_complete_family_draft,
    create_complete_simple_draft,
    create_network_warehouse,
    create_phone_categories,
    create_seller_location,
    create_store,
    create_user,
)


pytestmark = pytest.mark.integration


def _checklist() -> dict[str, bool]:
    return normalize_moderation_checklist(list(MODERATION_CHECKS))


def _rewrite_draft_images(
    draft: ProductDraft,
    source_root: Path,
    *,
    image_format: str,
    media_type: str,
    animated: bool = False,
) -> tuple[Path, ...]:
    paths = []
    image_files = sorted(
        (item for item in draft.files if item.kind.value == "IMAGE"),
        key=lambda item: (item.position, item.id),
    )
    for index, item in enumerate(image_files):
        path = source_root / item.storage_key
        if animated:
            frames = [
                Image.new("RGB", (80, 60), (index * 20, 40, 80)),
                Image.new("RGB", (80, 60), (80, index * 20, 40)),
            ]
            frames[0].save(
                path,
                format="WEBP",
                save_all=True,
                append_images=frames[1:],
                duration=100,
                loop=0,
            )
            for frame in frames:
                frame.close()
        else:
            image = Image.new("RGB", (900, 600), (index * 30, 80, 140))
            image.save(path, format=image_format)
            image.close()
        payload = path.read_bytes()
        item.media_type = media_type
        item.size_bytes = len(payload)
        item.sha256 = hashlib.sha256(payload).hexdigest()
        item.width = 80 if animated else 900
        item.height = 60 if animated else 600
        paths.append(path)
    return tuple(paths)


def test_commission_bootstrap_is_reproducible_and_idempotent(app, session):
    runner = app.test_cli_runner()

    seeded = runner.invoke(args=["seed-product-categories"])
    assert seeded.exit_code == 0, seeded.output
    first = runner.invoke(args=["marketplace-policy", "bootstrap"])
    assert first.exit_code == 0, first.output
    assert "Reglas creadas: 20; actualizadas: 0." in first.output
    second = runner.invoke(args=["marketplace-policy", "bootstrap"])
    assert second.exit_code == 0, second.output
    assert "Reglas creadas: 0; actualizadas: 20." in second.output

    session.expire_all()
    rules = session.scalars(
        select(MarketplaceCommissionRule).where(
            MarketplaceCommissionRule.store_id.is_(None),
            MarketplaceCommissionRule.category_id.is_not(None),
        )
    ).all()
    by_code = {rule.category.code: rule.commission_rate for rule in rules}
    assert by_code == INITIAL_CATEGORY_RATES
    assert by_code["ELECTRONICS_CAMERAS"] == Decimal("8.00")
    assert by_code["AUTOMOTIVE_BASIC_PARTS"] == Decimal("8.00")
    assert by_code["BABIES_CARE"] == Decimal("10.00")
    assert by_code["BABIES_CLOTHING"] == Decimal("12.00")
    assert by_code["HOME_CLEANING"] == Decimal("10.00")

    categories = list(session.scalars(
        select(Category).where(Category.is_active.is_(True)).order_by(Category.code)
    ))
    parent_ids = {category.parent_id for category in categories if category.parent_id}
    publishable = [
        category
        for category in categories
        if category.id not in parent_ids and category.code.lower() in PRODUCT_TEMPLATES
    ]
    resolutions = {
        category.code: resolve_marketplace_commission(
            session,
            category_id=category.id,
            price="100.00",
        )
        for category in publishable
    }
    assert len(publishable) == 20
    assert set(resolutions) == {category.code for category in publishable}
    assert all(result.mode == SellerCommissionType.PERCENTAGE for result in resolutions.values())

    low_price = resolve_marketplace_commission(
        session,
        category_id=next(
            category.id
            for category in publishable
            if category.code == "BABIES_CLOTHING"
        ),
        price="2.99",
    )
    assert low_price.mode == SellerCommissionType.FIXED
    assert low_price.rate == Decimal("0.00")
    assert low_price.fixed_amount == Decimal("0.25")


@pytest.mark.parametrize(
    ("configuration", "expected"),
    (
        (None, False),
        ({}, False),
        ({"version": 3}, True),
        ({"version": 4, "enabled": True, "mode": "family"}, True),
        ({"version": 4, "enabled": True, "mode": "single"}, False),
        ({"version": 4, "enabled": False, "mode": "family"}, False),
    ),
)
def test_family_variant_classification_is_canonical(configuration, expected):
    assert family_variants_enabled(configuration) is expected


def _enable_catalog_access(session, *, seller, store) -> None:
    onboarding = StoreOnboarding(
        user_id=seller.id,
        store_id=store.id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=store.name,
        legal_id_number="210049391",
        completed_at=datetime.now(timezone.utc),
    )
    session.add_all([
        onboarding,
        StoreMember(
            store_id=store.id,
            user_id=seller.id,
            role=StoreMemberRole.OWNER,
            is_active=True,
        ),
    ])
    session.flush()
    session.add(StoreContractAcceptance(
        onboarding_id=onboarding.id,
        contract_version="test-v1",
        annex_version="test-a1",
        status=StoreContractAcceptanceStatus.ACCEPTED,
        accepted_terms=True,
        otp_verified=True,
        accepted_at=datetime.now(timezone.utc),
    ))
    session.flush()


def test_commission_policy_precedence_and_explicit_fallback(session):
    seller = create_user(session)
    first_store = create_store(session, name="TechStore")
    second_store = create_store(session, name="Otra tienda")
    parent, child = create_phone_categories(session)
    global_rule = create_commission_rule(session, rate="12.00")
    category_rule = create_commission_rule(session, rate="8.00", category=parent)
    child_rule = create_commission_rule(session, rate="6.00", category=child)
    override = create_commission_rule(
        session, rate="6.50", category=parent, store=first_store,
    )
    session.commit()

    specific = resolve_marketplace_commission(
        session, store_id=first_store.id, category_id=child.id, price="10.03",
    )
    inherited = resolve_marketplace_commission(
        session, store_id=second_store.id, category_id=child.id, price="10.03",
    )
    assert specific.rate == Decimal("6.00")
    assert specific.rule_id == child_rule.id
    assert specific.commission_amount == Decimal("0.60")
    assert specific.seller_net_amount == Decimal("9.43")
    assert inherited.rate == Decimal("6.00")
    assert inherited.rule_id == child_rule.id

    child_rule.is_active = False
    session.commit()
    inherited_parent = resolve_marketplace_commission(
        session, store_id=second_store.id, category_id=child.id, price="10.00",
    )
    assert inherited_parent.rate == Decimal("8.00")
    assert inherited_parent.rule_id == category_rule.id
    category_rule.is_active = False
    session.commit()
    fallback = resolve_marketplace_commission(
        session, store_id=second_store.id, category_id=child.id, price="3.00",
    )
    assert fallback.rate == Decimal("12.00")
    assert fallback.rule_id == global_rule.id
    fixed = resolve_marketplace_commission(
        session, store_id=first_store.id, category_id=child.id, price="2.99",
    )
    assert fixed.mode == SellerCommissionType.FIXED
    assert fixed.fixed_amount == Decimal("0.25")
    assert fixed.commission_amount == Decimal("0.25")
    assert fixed.seller_net_amount == Decimal("2.74")
    assert override.id is not None  # legacy store rules are deliberately ignored
    assert seller.id is not None


def test_missing_commission_has_no_implicit_default(session):
    store = create_store(session)
    _parent, child = create_phone_categories(session)
    session.commit()
    with pytest.raises(CommissionRuleMissingError):
        resolve_marketplace_commission(
            session, store_id=store.id, category_id=child.id, price="3.00",
        )


@pytest.mark.parametrize("price", ["0", "-1", "0.15", "0.25"])
def test_minimum_price_is_rejected_with_canonical_message(session, price):
    _parent, child = create_phone_categories(session)
    session.commit()
    with pytest.raises(InvalidSellerPriceError, match=MINIMUM_PRICE_MESSAGE):
        resolve_marketplace_commission(session, category_id=child.id, price=price)


@pytest.mark.parametrize(
    ("price", "mode", "commission", "net"),
    [
        ("0.75", SellerCommissionType.FIXED, "0.25", "0.50"),
        ("1.00", SellerCommissionType.FIXED, "0.25", "0.75"),
        ("2.99", SellerCommissionType.FIXED, "0.25", "2.74"),
        ("3.00", SellerCommissionType.PERCENTAGE, "0.24", "2.76"),
        ("100.00", SellerCommissionType.PERCENTAGE, "8.00", "92.00"),
    ],
)
def test_canonical_price_boundaries(session, price, mode, commission, net):
    parent, child = create_phone_categories(session)
    create_commission_rule(session, rate="8.00", category=parent)
    session.commit()
    resolved = resolve_marketplace_commission(
        session, category_id=child.id, price=price
    )
    assert resolved.mode == mode
    assert resolved.commission_amount == Decimal(commission)
    assert resolved.seller_net_amount == Decimal(net)


def test_snapshot_validation_enforces_price_mode_boundaries(session):
    parent, child = create_phone_categories(session)
    create_commission_rule(session, rate="3.00", category=parent)
    session.commit()

    percentage = resolve_marketplace_commission(
        session, category_id=child.id, price="3.50"
    )
    assert percentage.commission_amount == Decimal("0.11")

    invalid_percentage = percentage.as_snapshot(captured_at="2026-08-16T00:00:00+00:00")
    invalid_percentage.update({
        "price": "2.99",
        "commission_amount": "0.09",
        "seller_net_amount": "2.90",
    })
    with pytest.raises(CommissionSnapshotError):
        commission_from_snapshot(
            invalid_percentage,
            expected_price="2.99",
            expected_category_id=child.id,
        )

    invalid_minimum = {
        **invalid_percentage,
        "mode": SellerCommissionType.FIXED.value,
        "price": "0.25",
        "rate_percent": None,
        "fixed_amount": "0.25",
        "commission_amount": "0.25",
        "seller_net_amount": "0.00",
        "rule_id": None,
    }
    with pytest.raises(CommissionSnapshotError):
        commission_from_snapshot(
            invalid_minimum,
            expected_price="0.25",
            expected_category_id=child.id,
        )


def test_simple_approval_materializes_only_explicit_seller_inventory(
    session, tmp_path,
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    _network = create_network_warehouse(session)
    rule = create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    destination_root = tmp_path / "catalog"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    draft.variant_configuration = {}
    draft.inventory_data = {
        "stock_quantity": 50,
        "preparation_time_days": 1,
    }
    capture_submission_commission_snapshots(session, draft)
    commission_rows = draft_commission_display_rows(session, draft)
    assert commission_snapshot_complete(draft, commission_rows) is True
    session.commit()

    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=destination_root,
    )
    session.commit()

    offers = session.scalars(select(SellerOffer)).all()
    assert len(offers) == 1
    offer = offers[0]
    assert offer.status == OfferStatus.ACTIVE
    assert offer.commission_rate == Decimal("8.00")
    assert offer.commission_type == SellerCommissionType.PERCENTAGE
    assert offer.commission_fixed_amount is None
    assert offer.commission_currency == "USD"
    assert offer.preparation_time_days == 1
    assert result.product.is_active is True
    assert session.scalar(select(func.count(ProductVariant.id))) == 1
    assert session.scalar(select(func.count(ProductMedia.id))) == 3
    assert session.scalar(select(func.count(ProductDraftPublication.id))) == 1
    assert session.scalar(select(func.count(ProductDraftModerationEvent.id))) == 1
    balance = session.scalar(select(InventoryBalance).where(InventoryBalance.offer_id == offer.id))
    mapping = session.scalar(
        select(StoreInventoryLocation).where(
            StoreInventoryLocation.store_id == store.id,
            StoreInventoryLocation.is_default.is_(True),
        )
    )
    assert mapping is not None
    seller_location = mapping.location
    seller_warehouse = session.scalar(
        select(Warehouse).where(Warehouse.seller_store_id == store.id)
    )
    assert seller_warehouse is not None
    assert balance is not None
    assert balance.location_id == seller_location.id
    assert balance.on_hand_quantity == 50
    assert balance.reserved_quantity == 0
    assert balance.available_quantity == 50
    assert balance.location.warehouse_id == seller_warehouse.id
    assert session.scalar(select(func.count(InventoryMovement.id))) == 1
    assert session.get(ProductDraft, draft.id).status == ProductDraftStatus.APPROVED
    assert all(path.exists() for path in result.copied_files)
    assert [option.value for option in warehouse_options(session)] == [str(_network.id)]
    assert str(seller_warehouse.id) not in [option.value for option in warehouse_options(session)]
    first_location = ensure_store_inventory_location(session, store=store)
    second_location = ensure_store_inventory_location(session, store=store)
    session.flush()
    assert first_location.id == second_location.id == seller_location.id
    assert session.scalar(
        select(func.count(Warehouse.id)).where(Warehouse.seller_store_id == store.id)
    ) == 1
    assert session.scalar(
        select(func.count(StoreInventoryLocation.id)).where(
            StoreInventoryLocation.store_id == store.id,
            StoreInventoryLocation.is_default.is_(True),
        )
    ) == 1

    secondary_warehouse = Warehouse(
        code=f"SECONDARY-{store.public_code}"[:30],
        name="Bodega secundaria futura",
        address_line="Inventario seller secundario",
        city="Guayaquil",
        country_code="EC",
        is_active=True,
        seller_store_id=store.id,
    )
    session.add(secondary_warehouse)
    session.flush()
    secondary_location = WarehouseLocation(
        warehouse_id=secondary_warehouse.id,
        code="STOCK-2",
        barcode=f"SECONDARY-{store.id}-STOCK",
        name="Stock secundario",
        location_type=LocationType.STORAGE,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(secondary_location)
    session.flush()
    session.add(StoreInventoryLocation(
        store_id=store.id,
        location_id=secondary_location.id,
        is_default=False,
        is_active=True,
    ))
    session.flush()
    assert session.scalar(
        select(func.count(Warehouse.id)).where(Warehouse.seller_store_id == store.id)
    ) == 2
    assert resolve_default_store_inventory_location(
        session, store_id=store.id,
    ).id == seller_location.id
    assert rule.id is not None


def test_family_approval_preserves_enabled_variants_skus_and_color_media(
    session, tmp_path,
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="7.25", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_family_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    capture_submission_commission_snapshots(session, draft)
    expected_skus = {row["sku"] for row in draft.variants if row["enabled"]}
    disabled_sku = next(row["sku"] for row in draft.variants if not row["enabled"])
    expected_configuration = dict(draft.variant_configuration)
    session.commit()

    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()

    variants = session.scalars(
        select(ProductVariant).where(ProductVariant.product_id == result.product.id)
    ).all()
    assert {variant.catalog_sku for variant in variants} == expected_skus
    assert disabled_sku not in {variant.catalog_sku for variant in variants}
    offers = session.scalars(select(SellerOffer)).all()
    assert len(offers) == 2
    assert {offer.preparation_time_days for offer in offers} == {2}
    assert result.product.variant_configuration == expected_configuration
    media = session.scalars(
        select(ProductMedia).where(ProductMedia.product_id == result.product.id)
    ).all()
    assert len(media) == 3
    assert {item.variant_axis_key for item in media} == {"color_principal"}
    assert {item.variant_value_key for item in media} == {"negro", "azul"}
    assert sum(1 for item in media if item.variant_value_key == "negro") == 2
    assert sum(1 for item in media if item.variant_value_key == "azul") == 1


def test_publication_normalizes_legacy_string_preparation_time(
    session, tmp_path
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    draft.inventory_data = {
        **draft.inventory_data,
        "preparation_time_days": "2",
    }
    capture_submission_commission_snapshots(session, draft)
    session.commit()

    publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()
    offer = session.scalar(select(SellerOffer))
    assert offer.preparation_time_days == 2
    assert isinstance(offer.preparation_time_days, int)


def test_publication_rejects_missing_preparation_without_partial_catalog(
    session, tmp_path
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    catalog_root = tmp_path / "catalog"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    draft.inventory_data = {
        **draft.inventory_data,
        "preparation_time_days": None,
    }
    capture_submission_commission_snapshots(session, draft)
    session.commit()

    with pytest.raises(ProductModerationValidationError, match="Preparación"):
        publish_product_draft(
            session,
            draft_id=draft.id,
            actor_user_id=moderator.id,
            checklist=_checklist(),
            source_media_root=source_root,
            catalog_media_root=catalog_root,
        )
    session.rollback()
    assert session.scalar(select(func.count(Product.id))) == 0
    assert session.scalar(select(func.count(SellerOffer.id))) == 0
    assert not catalog_root.exists()


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [
        ("JPEG", "image/jpeg"),
        ("PNG", "image/png"),
        ("WEBP", "image/webp"),
    ],
)
def test_publication_normalizes_supported_inputs_to_master_and_thumbnail_webp(
    session, tmp_path, image_format, media_type
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    catalog_root = tmp_path / "catalog"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    draft_sources = _rewrite_draft_images(
        draft,
        source_root,
        image_format=image_format,
        media_type=media_type,
    )
    capture_submission_commission_snapshots(session, draft)
    session.commit()

    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=catalog_root,
    )
    session.commit()

    media_rows = session.scalars(
        select(ProductMedia)
        .where(ProductMedia.product_id == result.product.id)
        .order_by(ProductMedia.position)
    ).all()
    assert len(media_rows) == 3
    assert len(result.copied_files) == 6
    assert all(path.is_file() for path in result.copied_files)
    assert all(path.is_file() for path in draft_sources)
    assert all(media.media_type == "image/webp" for media in media_rows)
    assert all(media.storage_key.endswith("/master.webp") for media in media_rows)
    assert all(
        media.thumbnail_storage_key.endswith("/thumbnail.webp")
        for media in media_rows
    )
    assert all(len(media.content_sha256) == 64 for media in media_rows)
    assert all(len(media.thumbnail_sha256) == 64 for media in media_rows)
    assert not any(
        path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        for path in catalog_root.rglob("*")
        if path.is_file()
    )


def test_publication_rejects_animated_webp_without_catalog_or_db_mutation(
    session, tmp_path
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    catalog_root = tmp_path / "catalog"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    sources = _rewrite_draft_images(
        draft,
        source_root,
        image_format="WEBP",
        media_type="image/webp",
        animated=True,
    )
    capture_submission_commission_snapshots(session, draft)
    session.commit()

    with pytest.raises(ProductModerationValidationError):
        publish_product_draft(
            session,
            draft_id=draft.id,
            actor_user_id=moderator.id,
            checklist=_checklist(),
            source_media_root=source_root,
            catalog_media_root=catalog_root,
        )
    session.rollback()

    assert all(path.is_file() for path in sources)
    assert session.scalar(select(func.count(ProductMedia.id))) == 0
    assert session.scalar(select(func.count(Product.id))) == 0
    assert not [path for path in catalog_root.rglob("*") if path.is_file()]


def test_family_publication_uses_frozen_mixed_commissions(session, tmp_path):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    rule = create_commission_rule(session, rate="7.25", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_family_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    rows = [dict(row) for row in draft.variants]
    rows[0]["price"] = "2.50"
    rows[0]["compare_at_price"] = None
    rows[1]["price"] = "3.00"
    rows[1]["compare_at_price"] = None
    rows[2]["price"] = "5.00"
    rows[2]["compare_at_price"] = None
    rows[2]["enabled"] = True
    rows[2]["combination_key"] = "color_principal=negro|almacenamiento_gb=512"
    rows[2]["name"] = "Negro / 512 GB"
    rows[2]["attributes"] = {"color_principal": "Negro", "almacenamiento_gb": "512"}
    rows[2]["options"] = {"color_principal": "negro", "almacenamiento_gb": "512"}
    draft.variants = rows
    configuration = dict(draft.variant_configuration)
    axes = [dict(axis) for axis in configuration["axes"]]
    storage_axis = dict(axes[1])
    storage_axis["values"] = [*storage_axis["values"], {"key": "512", "label": "512"}]
    axes[1] = storage_axis
    configuration["axes"] = axes
    draft.variant_configuration = configuration
    capture_submission_commission_snapshots(session, draft)
    rule.commission_rate = Decimal("20.00")
    session.commit()

    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()

    offers = session.scalars(
        select(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .where(ProductVariant.product_id == result.product.id)
        .order_by(SellerOffer.price)
    ).all()
    assert offers[0].commission_type == SellerCommissionType.FIXED
    assert offers[0].commission_rate == Decimal("0.00")
    assert offers[0].commission_fixed_amount == Decimal("0.25")
    assert offers[1].commission_type == SellerCommissionType.PERCENTAGE
    assert offers[1].commission_rate == Decimal("7.25")
    assert offers[1].price == Decimal("3.00")
    assert offers[2].commission_type == SellerCommissionType.PERCENTAGE
    assert offers[2].commission_rate == Decimal("7.25")
    assert offers[2].price == Decimal("5.00")
    event = session.scalar(
        select(ProductDraftModerationEvent).where(
            ProductDraftModerationEvent.draft_id == draft.id,
            ProductDraftModerationEvent.decision == "APPROVED",
        )
    )
    assert len(event.checklist_snapshot["commission_snapshots"]) == 3


def test_resubmission_replaces_active_commission_snapshot(session, tmp_path):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    _enable_catalog_access(session, seller=seller, store=store)
    parent, child = create_phone_categories(session)
    rule = create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    capture_submission_commission_snapshots(session, draft)
    assert draft.pricing_data["commission_snapshot"]["rate_percent"] == "8.00"
    session.commit()

    record_moderation_decision(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        decision="CHANGES_REQUESTED",
        checklist=_checklist(),
        reason_code="OTHER",
        note="Reenvía para aceptar la política comercial actual.",
    )
    rule.commission_rate = Decimal("9.00")
    session.commit()

    submit_saved_product_draft(
        session, user_id=seller.id, draft_id=draft.id
    )
    session.commit()
    session.refresh(draft)
    assert draft.status == ProductDraftStatus.SUBMITTED
    assert draft.pricing_data["commission_snapshot"]["rate_percent"] == "9.00"

    publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()
    offer = session.scalar(select(SellerOffer))
    assert offer.commission_rate == Decimal("9.00")


def test_approval_without_frozen_commission_rolls_back_without_partial_catalog(
    session, tmp_path,
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    draft_id = draft.id
    session.commit()

    with pytest.raises(ProductModerationValidationError) as error:
        publish_product_draft(
            session,
            draft_id=draft_id,
            actor_user_id=moderator.id,
            checklist=_checklist(),
            source_media_root=source_root,
            catalog_media_root=tmp_path / "catalog",
        )
    session.rollback()

    assert str(error.value) == (
        "El producto no tiene una comisión fijada al momento del envío. "
        "Devuélvelo al vendedor para volver a enviarlo."
    )
    assert session.get(ProductDraft, draft_id).status == ProductDraftStatus.SUBMITTED
    assert session.scalar(select(func.count(Product.id))) == 0
    assert session.scalar(select(func.count(ProductVariant.id))) == 0
    assert session.scalar(select(func.count(SellerOffer.id))) == 0
    assert session.scalar(select(func.count(ProductMedia.id))) == 0
    assert session.scalar(select(func.count(ProductDraftPublication.id))) == 0


def test_double_approval_is_idempotent(session, tmp_path):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    capture_submission_commission_snapshots(session, draft)
    session.commit()

    first = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()
    second = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist=_checklist(),
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()

    assert second.already_published is True
    assert second.product.id == first.product.id
    assert session.scalar(select(func.count(Product.id))) == 1
    assert session.scalar(select(func.count(ProductVariant.id))) == 1
    assert session.scalar(select(func.count(SellerOffer.id))) == 1
    assert session.scalar(select(func.count(ProductMedia.id))) == 3
    assert session.scalar(select(func.count(ProductDraftModerationEvent.id))) == 1
    assert session.scalar(select(func.count(ProductDraftPublication.id))) == 1


def test_stale_approval_is_rejected_after_another_moderator_requests_changes(
    session, tmp_path,
):
    seller = create_user(session)
    first_moderator = create_user(session, staff=True)
    second_moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=parent)
    source_root = tmp_path / "drafts"
    draft = create_complete_simple_draft(
        session,
        seller=seller,
        store=store,
        category=parent,
        subcategory=child,
        media_root=source_root,
    )
    session.commit()

    record_moderation_decision(
        session,
        draft_id=draft.id,
        actor_user_id=first_moderator.id,
        decision="CHANGES_REQUESTED",
        checklist=_checklist(),
        reason_code="INCORRECT_INFORMATION",
        note="Revisa la información.",
    )
    session.commit()

    with pytest.raises(ProductModerationStateError):
        publish_product_draft(
            session,
            draft_id=draft.id,
            actor_user_id=second_moderator.id,
            checklist=_checklist(),
            source_media_root=source_root,
            catalog_media_root=tmp_path / "catalog",
        )
    session.rollback()
    session.expire_all()
    assert session.get(ProductDraft, draft.id).status == ProductDraftStatus.CHANGES_REQUESTED
    assert session.scalar(select(func.count(Product.id))) == 0
