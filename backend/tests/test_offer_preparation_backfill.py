from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import (
    Product,
    ProductDraft,
    ProductDraftPublication,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.models.enums import ProductDraftStatus, StoreStatus
from app.services.offer_preparation_backfill import (
    OfferPreparationBackfillError,
    backfill_offer_preparation_time,
    inspect_offer_preparation_backfill,
)
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


def _mapped_offer(session, *, source_value=2):
    base = create_catalog_and_stock(session)
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    draft = ProductDraft(
        store_id=base.store_id,
        created_by_user_id=base.operator_id,
        category_id=product.category_id,
        subcategory_id=product.category_id,
        template_key="electronics_phones",
        seller_sku=f"DRAFT-{uuid.uuid4().hex[:12]}",
        inventory_data={"preparation_time_days": source_value},
        status=ProductDraftStatus.APPROVED,
    )
    session.add(draft)
    session.flush()
    session.add(ProductDraftPublication(
        draft_id=draft.id,
        product_id=product.id,
        published_by_user_id=base.operator_id,
    ))
    session.commit()
    return base, offer, draft, product


def test_backfill_dry_run_finds_proven_mapping_without_mutation(session):
    _base, offer, _draft, _product = _mapped_offer(
        session,
        source_value="2",
    )
    report = inspect_offer_preparation_backfill(session)
    assert report.scanned_count == 1
    assert report.candidate_count == 1
    assert report.populated_count == 0
    assert report.entries[0].source_value == "2"
    session.expire_all()
    assert session.get(SellerOffer, offer.id).preparation_time_days is None


def test_backfill_apply_is_idempotent_and_never_overwrites(session):
    _base, offer, _draft, _product = _mapped_offer(session, source_value=2)
    first = backfill_offer_preparation_time(session, offer_id=offer.id)
    second = backfill_offer_preparation_time(session, offer_id=offer.id)
    assert (first.status, first.preparation_time_days) == ("updated", 2)
    assert (second.status, second.preparation_time_days) == ("skipped", 2)

    persisted = session.get(SellerOffer, offer.id)
    persisted.preparation_time_days = 1
    session.commit()
    third = backfill_offer_preparation_time(session, offer_id=offer.id)
    assert (third.status, third.preparation_time_days) == ("skipped", 1)


@pytest.mark.parametrize(
    ("source_value", "expected_status"),
    ((None, "missing_source"), ("3", "invalid_source")),
)
def test_backfill_skips_missing_or_invalid_source(
    session, source_value, expected_status
):
    _base, offer, _draft, _product = _mapped_offer(
        session,
        source_value=source_value,
    )
    report = inspect_offer_preparation_backfill(session)
    assert report.entries[0].status == expected_status
    with pytest.raises(OfferPreparationBackfillError):
        backfill_offer_preparation_time(session, offer_id=offer.id)
    session.expire_all()
    assert session.get(SellerOffer, offer.id).preparation_time_days is None


def test_backfill_skips_untraceable_offer_and_store_mismatch(session):
    base = create_catalog_and_stock(session)
    session.commit()
    report = inspect_offer_preparation_backfill(session)
    untraceable_offer = session.get(SellerOffer, base.offer_id)
    assert next(
        entry for entry in report.entries if entry.offer_id == untraceable_offer.id
    ).status == "untraceable"

    _base, offer, draft, _product = _mapped_offer(session, source_value=2)
    other_store = Store(
        public_code=f"OTHER-{uuid.uuid4().hex[:8]}",
        name="Other Store",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add(other_store)
    session.flush()
    draft.store_id = other_store.id
    session.commit()
    report = inspect_offer_preparation_backfill(session)
    mismatch = next(entry for entry in report.entries if entry.offer_id == offer.id)
    assert mismatch.status == "store_mismatch"


def test_backfill_revalidates_after_dry_run_and_cli_second_apply_is_zero(
    app, session
):
    _base, offer, _draft, _product = _mapped_offer(session, source_value=2)
    assert inspect_offer_preparation_backfill(session).candidate_count == 1
    offer.preparation_time_days = 1
    session.commit()
    assert backfill_offer_preparation_time(
        session,
        offer_id=offer.id,
    ).status == "skipped"

    offer.preparation_time_days = None
    session.commit()
    runner = app.test_cli_runner()
    dry = runner.invoke(
        args=["product-offers", "backfill-preparation-time"]
    )
    assert dry.exit_code == 0
    assert "DRY RUN" in dry.output and "would_update=1" in dry.output
    session.expire_all()
    assert session.get(SellerOffer, offer.id).preparation_time_days is None

    first = runner.invoke(
        args=["product-offers", "backfill-preparation-time", "--apply"]
    )
    second = runner.invoke(
        args=["product-offers", "backfill-preparation-time", "--apply"]
    )
    assert first.exit_code == second.exit_code == 0
    assert "successes=1" in first.output
    assert "successes=0" in second.output
