from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class SellerOrderDeliveryWindow:
    decision_available_at: datetime
    ship_by_at: datetime
    estimated_delivery_from: datetime
    estimated_delivery_to: datetime


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_seller_order_delivery_window(
    confirmed_at: datetime,
) -> SellerOrderDeliveryWindow:
    """Build the current 24h decision/dispatch and 24-48h delivery SLA."""
    start = aware_utc(confirmed_at)
    return SellerOrderDeliveryWindow(
        decision_available_at=start,
        ship_by_at=start + timedelta(hours=24),
        estimated_delivery_from=start + timedelta(hours=24),
        estimated_delivery_to=start + timedelta(hours=48),
    )
