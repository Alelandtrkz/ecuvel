"""Reproducible, non-gating H5 catalog CPU benchmark.

Run inside the application container:
    python tests/catalog_feed_benchmark.py

It intentionally creates no database rows or image files. Query structure is
covered separately by the integration tests.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from datetime import date
from decimal import Decimal

from app.models.enums import OfferStatus
from app.services.catalog_listings import (
    PublicListingMember,
    _listing_from_group,
    build_listing_identity,
)
from app.services.catalog_ranking import (
    SURFACE_HOME,
    SURFACE_SEARCH,
    SURFACE_STORE,
    apply_soft_diversity,
    private_search_context,
    rank_listings_v1,
)
from app.services.catalog_shadow_ranking import shadow_rank_listings


DAY = date(2026, 9, 2)


def _member(index: int) -> PublicListingMember:
    product_id = uuid.UUID(int=index + 1)
    variant_id = uuid.UUID(int=10_000 + index)
    offer_id = uuid.UUID(int=20_000 + index)
    store_id = uuid.UUID(int=30_000 + (index % 125))
    return PublicListingMember(
        product_id=product_id,
        product_slug=f"phone-{index}",
        product_title=f"Phone benchmark {index}",
        product_description="Synthetic catalog benchmark",
        product_brand="Ecuvel Test",
        product_model_number=f"M-{index}",
        variant_configuration={},
        category_id=uuid.UUID(int=40_000),
        category_name="Phones",
        category_slug="phones",
        variant_id=variant_id,
        variant_title="Black",
        catalog_sku=f"SKU-{index}",
        manufacturer_barcode=None,
        variant_attributes={"color": "Black"},
        combination_key="black",
        weight_grams=200,
        length_mm=150,
        width_mm=70,
        height_mm=8,
        offer_id=offer_id,
        seller_sku=f"SELL-{index}",
        currency="USD",
        price=Decimal("999.99") + index,
        compare_at_price=None,
        preparation_time_days=1 + (index % 2),
        offer_status=OfferStatus.ACTIVE,
        store_id=store_id,
        store_name=f"Store {index % 125}",
        store_slug=f"store-{index % 125}",
        store_is_verified=True,
        available_quantity=0 if index % 20 == 0 else 5,
    )


def _measure(operation, repeats: int = 5):
    values = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        values.append((time.perf_counter() - started) * 1000)
    return result, round(statistics.median(values), 3)


def benchmark(size: int) -> dict[str, object]:
    members = [_member(index) for index in range(size)]

    def group():
        return [
            _listing_from_group(
                build_listing_identity(
                    product_id=member.product_id,
                    store_id=member.store_id,
                    listing_axis_keys=(),
                    variant_options={},
                ),
                [member],
            )
            for member in members
        ]

    listings, grouping_ms = _measure(group)
    ranked, home_v1_ms = _measure(
        lambda: rank_listings_v1(listings, surface=SURFACE_HOME, day=DAY)
    )
    _diverse, diversity_ms = _measure(
        lambda: apply_soft_diversity(
            ranked,
            limit=len(ranked),
            max_per_product=2,
            max_per_store=6,
        )
    )
    _search, search_v1_ms = _measure(
        lambda: rank_listings_v1(
            listings,
            surface=SURFACE_SEARCH,
            context=private_search_context("phone"),
            query="phone",
            day=DAY,
        )
    )
    _store, store_v1_ms = _measure(
        lambda: rank_listings_v1(
            listings,
            surface=SURFACE_STORE,
            context="store-benchmark",
            day=DAY,
        )
    )
    _shadow, shadow_ms = _measure(lambda: shadow_rank_listings(listings, {}))
    return {
        "candidates": size,
        "batch": 20,
        "grouping_ms": grouping_ms,
        "home_v1_ms": home_v1_ms,
        "diversity_ms": diversity_ms,
        "search_v1_ms": search_v1_ms,
        "store_v1_ms": store_v1_ms,
        "shadow_ms": shadow_ms,
    }


if __name__ == "__main__":
    print(json.dumps([benchmark(size) for size in (100, 500, 1000, 5000)], indent=2))
