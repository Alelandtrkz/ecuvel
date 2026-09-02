from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatalogInteractionEvent
from app.services.catalog_listings import PublicListing
from app.services.catalog_ranking import SHADOW_RANKER_VERSION


SHADOW_WINDOW_DAYS = 30
RATE_PRIORS = {
    "clicks": (1.0, 19.0),
    "favorites": (1.0, 39.0),
    "add_to_cart": (1.0, 49.0),
    "purchases": (1.0, 99.0),
    "delivered": (1.0, 19.0),
}
SHADOW_WEIGHTS = {
    "smoothed_ctr": 0.35,
    "smoothed_favorite_rate": 0.20,
    "smoothed_cart_rate": 0.30,
    "smoothed_purchase_rate": 0.10,
    "smoothed_delivered_rate": 0.05,
    "exploration": 0.15,
    "freshness": 0.00,
    "delivery": 0.05,
    "price_competitiveness": 0.00,
}


@dataclass(frozen=True, slots=True)
class ListingEventAggregate:
    impressions: int = 0
    clicks: int = 0
    favorites: int = 0
    add_to_cart: int = 0
    purchases: int = 0
    delivered: int = 0


@dataclass(frozen=True, slots=True)
class ShadowFeatures:
    smoothed_ctr: float
    smoothed_favorite_rate: float
    smoothed_cart_rate: float
    smoothed_purchase_rate: float
    smoothed_delivered_rate: float
    exploration: float
    freshness: float
    delivery: float
    price_competitiveness: float = 0.0


@dataclass(frozen=True, slots=True)
class ShadowRankingResult:
    listing_key: str
    position: int
    score: float
    features: ShadowFeatures


def load_listing_event_aggregates(
    session: Session,
    listing_keys: set[str],
    *,
    window_days: int = SHADOW_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, ListingEventAggregate]:
    if not listing_keys:
        return {}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    rows = session.execute(
        select(
            CatalogInteractionEvent.listing_key,
            CatalogInteractionEvent.event_type,
            func.count(CatalogInteractionEvent.id),
        )
        .where(
            CatalogInteractionEvent.listing_key.in_(listing_keys),
            CatalogInteractionEvent.occurred_at >= cutoff,
        )
        .group_by(
            CatalogInteractionEvent.listing_key,
            CatalogInteractionEvent.event_type,
        )
    ).all()
    counts: dict[str, dict[str, int]] = {key: {} for key in listing_keys}
    for listing_key, event_type, count in rows:
        counts[listing_key][event_type] = int(count)
    return {
        key: ListingEventAggregate(
            impressions=values.get("IMPRESSION", 0),
            clicks=values.get("CLICK", 0),
            favorites=values.get("FAVORITE", 0),
            add_to_cart=values.get("ADD_TO_CART", 0),
            purchases=values.get("PURCHASE", 0),
            delivered=values.get("DELIVERED", 0),
        )
        for key, values in counts.items()
    }


def shadow_rank_listings(
    listings: Iterable[PublicListing],
    aggregates: dict[str, ListingEventAggregate],
) -> list[ShadowRankingResult]:
    scored = []
    for listing in listings:
        aggregate = aggregates.get(listing.listing_key, ListingEventAggregate())
        features = extract_shadow_features(listing, aggregate)
        score = sum(
            getattr(features, feature) * weight
            for feature, weight in SHADOW_WEIGHTS.items()
        )
        scored.append((score, listing.listing_key, features))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        ShadowRankingResult(
            listing_key=listing_key,
            position=position,
            score=score,
            features=features,
        )
        for position, (score, listing_key, features) in enumerate(scored, start=1)
    ]


def extract_shadow_features(
    listing: PublicListing,
    aggregate: ListingEventAggregate,
) -> ShadowFeatures:
    impressions = aggregate.impressions
    return ShadowFeatures(
        smoothed_ctr=_smoothed_rate(aggregate.clicks, impressions, "clicks"),
        smoothed_favorite_rate=_smoothed_rate(
            aggregate.favorites, impressions, "favorites"
        ),
        smoothed_cart_rate=_smoothed_rate(
            aggregate.add_to_cart, impressions, "add_to_cart"
        ),
        smoothed_purchase_rate=_smoothed_rate(
            aggregate.purchases, impressions, "purchases"
        ),
        smoothed_delivered_rate=_smoothed_rate(
            aggregate.delivered,
            aggregate.purchases,
            "delivered",
        ),
        exploration=1.0 / math.sqrt(impressions + 1.0),
        freshness=0.0,
        delivery={1: 1.0, 2: 0.5}.get(listing.preparation_time_days, 0.0),
    )


def _smoothed_rate(successes: int, trials: int, feature: str) -> float:
    alpha, beta = RATE_PRIORS[feature]
    bounded_successes = min(max(0, successes), max(0, trials))
    return (bounded_successes + alpha) / (max(0, trials) + alpha + beta)


def ranking_readiness_report(
    session: Session,
    *,
    all_listing_keys: set[str],
    window_days: int = SHADOW_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, object]:
    aggregates = load_listing_event_aggregates(
        session,
        all_listing_keys,
        window_days=window_days,
        now=now,
    )
    totals = {
        "impressions": sum(item.impressions for item in aggregates.values()),
        "clicks": sum(item.clicks for item in aggregates.values()),
        "favorites": sum(item.favorites for item in aggregates.values()),
        "add_to_cart": sum(item.add_to_cart for item in aggregates.values()),
        "purchases": sum(item.purchases for item in aggregates.values()),
        "delivered": sum(item.delivered for item in aggregates.values()),
    }
    impressions = [
        aggregates.get(key, ListingEventAggregate()).impressions
        for key in all_listing_keys
    ]
    with_impressions = sum(value > 0 for value in impressions)
    listing_count = len(all_listing_keys)
    return {
        "window_days": window_days,
        "total_listings": listing_count,
        "listings_with_impressions": with_impressions,
        "listing_coverage": (
            round(with_impressions / listing_count, 6) if listing_count else 0.0
        ),
        **totals,
        "sample_distribution": {
            "0": sum(value == 0 for value in impressions),
            "1-9": sum(1 <= value <= 9 for value in impressions),
            "10-99": sum(10 <= value <= 99 for value in impressions),
            "100+": sum(value >= 100 for value in impressions),
        },
    }
