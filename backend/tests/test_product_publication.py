from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
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
)
from app.models.enums import OfferStatus, ProductDraftStatus
from app.services.admin_operating_context import warehouse_options
from app.services.marketplace_policy import (
    CommissionRuleMissingError,
    resolve_marketplace_commission,
)
from app.services.product_publication import (
    MODERATION_CHECKS,
    ProductModerationStateError,
    ProductModerationValidationError,
    normalize_moderation_checklist,
    publish_product_draft,
    record_moderation_decision,
)
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


def test_commission_policy_precedence_and_explicit_fallback(session):
    seller = create_user(session)
    first_store = create_store(session, name="TechStore")
    second_store = create_store(session, name="Otra tienda")
    parent, child = create_phone_categories(session)
    global_rule = create_commission_rule(session, rate="12.00")
    category_rule = create_commission_rule(session, rate="8.00", category=parent)
    override = create_commission_rule(
        session, rate="6.50", category=parent, store=first_store,
    )
    session.commit()

    specific = resolve_marketplace_commission(
        session, store_id=first_store.id, category_id=child.id,
    )
    inherited = resolve_marketplace_commission(
        session, store_id=second_store.id, category_id=child.id,
    )
    assert specific.rate == Decimal("6.50")
    assert specific.rule_id == override.id
    assert inherited.rate == Decimal("8.00")
    assert inherited.rule_id == category_rule.id

    category_rule.is_active = False
    session.commit()
    fallback = resolve_marketplace_commission(
        session, store_id=second_store.id, category_id=child.id,
    )
    assert fallback.rate == Decimal("12.00")
    assert fallback.rule_id == global_rule.id
    assert seller.id is not None


def test_missing_commission_has_no_implicit_default(session):
    store = create_store(session)
    _parent, child = create_phone_categories(session)
    session.commit()
    with pytest.raises(CommissionRuleMissingError):
        resolve_marketplace_commission(
            session, store_id=store.id, category_id=child.id,
        )


def test_simple_approval_materializes_only_explicit_seller_inventory(
    session, tmp_path,
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    _network = create_network_warehouse(session)
    seller_warehouse, seller_location, _mapping = create_seller_location(session, store)
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
    assert result.product.is_active is True
    assert session.scalar(select(func.count(ProductVariant.id))) == 1
    assert session.scalar(select(func.count(ProductMedia.id))) == 3
    assert session.scalar(select(func.count(ProductDraftPublication.id))) == 1
    assert session.scalar(select(func.count(ProductDraftModerationEvent.id))) == 1
    balance = session.scalar(select(InventoryBalance).where(InventoryBalance.offer_id == offer.id))
    assert balance is not None
    assert balance.location_id == seller_location.id
    assert balance.on_hand_quantity == 20
    assert balance.location.warehouse_id == seller_warehouse.id
    assert session.scalar(select(func.count(InventoryMovement.id))) == 1
    assert session.get(ProductDraft, draft.id).status == ProductDraftStatus.APPROVED
    assert all(path.exists() for path in result.copied_files)
    assert [option.value for option in warehouse_options(session)] == [str(_network.id)]
    assert str(seller_warehouse.id) not in [option.value for option in warehouse_options(session)]
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
    assert session.scalar(select(func.count(SellerOffer.id))) == 2
    assert result.product.variant_configuration == expected_configuration
    media = session.scalars(
        select(ProductMedia).where(ProductMedia.product_id == result.product.id)
    ).all()
    assert len(media) == 3
    assert {item.variant_axis_key for item in media} == {"color_principal"}
    assert {item.variant_value_key for item in media} == {"negro", "azul"}
    assert sum(1 for item in media if item.variant_value_key == "negro") == 2
    assert sum(1 for item in media if item.variant_value_key == "azul") == 1


@pytest.mark.parametrize("missing", ["commission", "location"])
def test_approval_prerequisite_failure_rolls_back_without_partial_catalog(
    session, tmp_path, missing,
):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session)
    parent, child = create_phone_categories(session)
    if missing != "location":
        create_seller_location(session, store)
    if missing != "commission":
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

    assert "comisión" in str(error.value) if missing == "commission" else "ubicación" in str(error.value)
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
