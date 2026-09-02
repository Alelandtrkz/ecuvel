from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

from app.services.catalog_ranking import (
    SURFACE_HOME,
    SURFACE_SEARCH,
    apply_soft_diversity,
    private_search_context,
    rank_listings_v1,
    search_relevance,
    stable_discovery_score,
)


def _listing(
    key: str,
    *,
    available: bool = True,
    product_id=None,
    store_id=None,
    title: str = "Producto",
    variant: str = "",
    price: int = 100,
):
    member = SimpleNamespace(
        catalog_sku=f"SKU-{key}",
        seller_sku=f"SELL-{key}",
        variant_title=variant,
    )
    return SimpleNamespace(
        listing_key=key,
        is_available=available,
        product_id=product_id or uuid.uuid4(),
        store_id=store_id or uuid.uuid4(),
        product_title=title,
        members=(member,),
        search_values=(title, variant, f"SKU-{key}"),
        price=price,
    )


def test_v1_is_stable_same_day_rotates_by_day_and_does_not_use_price():
    listings = [_listing(f"listing-{index}", price=index) for index in range(12)]
    day_one = date(2026, 9, 1)
    day_two = date(2026, 9, 2)

    first = rank_listings_v1(listings, surface=SURFACE_HOME, day=day_one)
    refresh = rank_listings_v1(list(reversed(listings)), surface=SURFACE_HOME, day=day_one)
    rotated = rank_listings_v1(listings, surface=SURFACE_HOME, day=day_two)
    before_price_change = [listing.listing_key for listing in first]
    for index, listing in enumerate(listings):
        listing.price = 10000 - index
    after_price_change = [
        listing.listing_key
        for listing in rank_listings_v1(listings, surface=SURFACE_HOME, day=day_one)
    ]

    assert [listing.listing_key for listing in refresh] == before_price_change
    assert [listing.listing_key for listing in rotated] != before_price_change
    assert after_price_change == before_price_change


def test_available_listings_always_precede_sold_out_on_discovery_surfaces():
    listings = [
        _listing("a", available=False),
        _listing("b", available=True),
        _listing("c", available=False),
        _listing("d", available=True),
    ]

    ranked = rank_listings_v1(
        listings,
        surface=SURFACE_HOME,
        day=date(2026, 9, 1),
    )

    assert [item.is_available for item in ranked] == [True, True, False, False]


def test_search_exact_sold_out_beats_weak_available_and_query_context_is_private():
    exact = _listing(
        "exact",
        available=False,
        title="iPhone 17 Pro Max",
    )
    accessory = _listing(
        "accessory",
        available=True,
        title="Funda para iPhone 17",
    )
    query = "iphone 17 pro max"

    ranked = rank_listings_v1(
        [accessory, exact],
        surface=SURFACE_SEARCH,
        context=private_search_context(query),
        query=query,
        day=date(2026, 9, 1),
    )

    assert ranked[0] is exact
    assert search_relevance(exact, query) > search_relevance(accessory, query)
    assert query not in private_search_context(query)


def test_soft_diversity_caps_then_fills_without_artificial_holes():
    product_p = uuid.uuid4()
    store_a = uuid.uuid4()
    many = [
        _listing(f"p-{index}", product_id=product_p, store_id=store_a)
        for index in range(10)
    ]
    alternatives = [
        _listing("q", store_id=uuid.uuid4()),
        _listing("r", store_id=uuid.uuid4()),
        _listing("s", store_id=uuid.uuid4()),
    ]

    diverse = apply_soft_diversity(
        many + alternatives,
        limit=5,
        max_per_product=2,
        max_per_store=3,
    )
    only_one_product = apply_soft_diversity(
        many,
        limit=8,
        max_per_product=2,
        max_per_store=3,
    )

    assert sum(item.product_id == product_p for item in diverse) == 2
    assert len(diverse) == 5
    assert len(only_one_product) == 8


def test_discovery_hash_changes_with_surface_context_and_not_python_hash():
    baseline = stable_discovery_score(
        listing_key="lst_abc",
        surface="HOME",
        context="",
        day=date(2026, 9, 1),
    )
    assert baseline == stable_discovery_score(
        listing_key="lst_abc",
        surface="HOME",
        context="",
        day=date(2026, 9, 1),
    )
    assert baseline != stable_discovery_score(
        listing_key="lst_abc",
        surface="CATEGORY",
        context="phones",
        day=date(2026, 9, 1),
    )
