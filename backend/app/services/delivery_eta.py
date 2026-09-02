from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


ECUADOR_TIMEZONE = ZoneInfo("America/Guayaquil")
SPANISH_MONTHS = (
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def ecuador_local_date(now: datetime | None = None) -> date:
    instant = now or datetime.now(ECUADOR_TIMEZONE)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return instant.astimezone(ECUADOR_TIMEZONE).date()


def estimated_delivery_date(
    preparation_time_days: int | None,
    *,
    now: datetime | None = None,
    local_today: date | None = None,
) -> date | None:
    if preparation_time_days is None:
        return None
    if (
        isinstance(preparation_time_days, bool)
        or not isinstance(preparation_time_days, int)
        or preparation_time_days < 1
    ):
        raise ValueError("preparation_time_days must be a positive integer")
    reference = local_today or ecuador_local_date(now)
    return reference + timedelta(days=preparation_time_days)


def format_spanish_date(
    value: date,
    *,
    reference_date: date,
) -> str:
    suffix = f" de {value.year}" if value.year != reference_date.year else ""
    return f"{value.day} de {SPANISH_MONTHS[value.month]}{suffix}"


def format_delivery_eta_compact(
    value: date | None,
    *,
    reference_date: date,
) -> str | None:
    if value is None:
        return None
    delta = (value - reference_date).days
    if delta == 1:
        return "Mañana"
    if delta == 2:
        return "Pasado mañana"
    return format_spanish_date(value, reference_date=reference_date)


def format_delivery_eta_full(
    value: date | None,
    *,
    reference_date: date,
) -> str | None:
    compact = format_delivery_eta_compact(value, reference_date=reference_date)
    if compact is None:
        return None
    if compact == "Mañana":
        return "Entrega estimada mañana"
    if compact == "Pasado mañana":
        return "Entrega estimada pasado mañana"
    return f"Entrega estimada el {compact}"


def delivery_eta_compact_label(
    preparation_time_days: int | None,
    *,
    now: datetime | None = None,
    local_today: date | None = None,
) -> str | None:
    reference = local_today or ecuador_local_date(now)
    value = estimated_delivery_date(
        preparation_time_days,
        local_today=reference,
    )
    return format_delivery_eta_compact(value, reference_date=reference)


def delivery_eta_full_label(
    preparation_time_days: int | None,
    *,
    now: datetime | None = None,
    local_today: date | None = None,
) -> str | None:
    reference = local_today or ecuador_local_date(now)
    value = estimated_delivery_date(
        preparation_time_days,
        local_today=reference,
    )
    return format_delivery_eta_full(value, reference_date=reference)
