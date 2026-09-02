from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from datetime import date, datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from app.services.catalog_listings import PublicListing


LIVE_RANKER_VERSION = "v1"
SHADOW_RANKER_VERSION = "v2-shadow-001"
RANKING_TIMEZONE = ZoneInfo("America/Guayaquil")

SURFACE_HOME = "HOME"
SURFACE_SEARCH = "SEARCH"
SURFACE_CATEGORY = "CATEGORY"
SURFACE_STORE = "STORE"
SURFACE_FAVORITES = "FAVORITES"
SURFACE_RECOMMENDATIONS = "RECOMMENDATIONS"
RANKING_SURFACES = frozenset(
    {
        SURFACE_HOME,
        SURFACE_SEARCH,
        SURFACE_CATEGORY,
        SURFACE_STORE,
        SURFACE_FAVORITES,
        SURFACE_RECOMMENDATIONS,
    }
)


def ranking_day(now: datetime | date | None = None) -> date:
    if isinstance(now, datetime):
        aware = now if now.tzinfo is not None else now.replace(tzinfo=RANKING_TIMEZONE)
        return aware.astimezone(RANKING_TIMEZONE).date()
    if isinstance(now, date):
        return now
    return datetime.now(RANKING_TIMEZONE).date()


def stable_discovery_score(
    *,
    listing_key: str,
    surface: str,
    context: str = "",
    day: datetime | date | None = None,
) -> int:
    payload = "\x1f".join(
        (ranking_day(day).isoformat(), surface, context, listing_key)
    )
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")


def private_search_context(query: str) -> str:
    """Return a stable request context without retaining the raw search text."""
    normalized = _normalize_text(query)
    return "q:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def search_relevance(listing: PublicListing, query: str) -> int:
    needle = _normalize_text(query)
    if not needle:
        return 0
    title = _normalize_text(listing.product_title)
    sku_values = {
        _normalize_text(member.catalog_sku) for member in listing.members
    } | {_normalize_text(member.seller_sku) for member in listing.members}
    variant_values = {
        _normalize_text(member.variant_title or "") for member in listing.members
    }
    other_values = {_normalize_text(value) for value in listing.search_values}

    if title == needle:
        return 1000
    if needle in sku_values:
        return 960
    if title.startswith(needle):
        return 920
    if needle in variant_values:
        return 900
    if any(value.startswith(needle) for value in variant_values | other_values):
        return 840
    if needle in title:
        return 800
    if any(needle in value for value in variant_values | other_values):
        return 720

    tokens = needle.split()
    if not tokens:
        return 0
    document_tokens = Counter(
        token
        for value in other_values | {title}
        for token in value.split()
    )
    matched = sum(1 for token in tokens if document_tokens[token] > 0)
    if matched == len(tokens):
        return 600 + min(99, matched * 10)
    if matched:
        return int(300 * matched / len(tokens))
    return 0


def rank_listings_v1(
    listings: Iterable[PublicListing],
    *,
    surface: str,
    context: str = "",
    query: str | None = None,
    day: datetime | date | None = None,
) -> list[PublicListing]:
    candidates = list(listings)
    if query:
        scored = [
            (search_relevance(listing, query), listing)
            for listing in candidates
        ]
        scored = [(score, listing) for score, listing in scored if score > 0]
        return [
            listing
            for _score, listing in sorted(
                scored,
                key=lambda item: (
                    -item[0],
                    not item[1].is_available,
                    stable_discovery_score(
                        listing_key=item[1].listing_key,
                        surface=surface,
                        context=context,
                        day=day,
                    ),
                    item[1].listing_key,
                ),
            )
        ]
    return sorted(
        candidates,
        key=lambda listing: (
            not listing.is_available,
            stable_discovery_score(
                listing_key=listing.listing_key,
                surface=surface,
                context=context,
                day=day,
            ),
            listing.listing_key,
        ),
    )


def apply_soft_diversity(
    listings: Iterable[PublicListing],
    *,
    limit: int,
    max_per_product: int | None,
    max_per_store: int | None,
) -> list[PublicListing]:
    ranked = list(listings)
    selected: list[PublicListing] = []
    deferred: list[PublicListing] = []
    product_counts: Counter = Counter()
    store_counts: Counter = Counter()
    for listing in ranked:
        product_full = (
            max_per_product is not None
            and product_counts[listing.product_id] >= max_per_product
        )
        store_full = (
            max_per_store is not None
            and store_counts[listing.store_id] >= max_per_store
        )
        if product_full or store_full:
            deferred.append(listing)
            continue
        selected.append(listing)
        product_counts[listing.product_id] += 1
        store_counts[listing.store_id] += 1
        if len(selected) == limit:
            return selected

    for listing in deferred:
        selected.append(listing)
        if len(selected) == limit:
            break
    return selected


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        normalized.encode("ascii", "ignore").decode("ascii").lower().split()
    )
