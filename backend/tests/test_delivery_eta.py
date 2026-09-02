from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.delivery_eta import (
    ecuador_local_date,
    estimated_delivery_date,
    format_delivery_eta_compact,
    format_delivery_eta_full,
)
from app.services.offer_preparation import (
    OfferPreparationValidationError,
    normalize_preparation_time_days,
)


@pytest.mark.parametrize(
    ("local_today", "days", "expected"),
    (
        (date(2026, 9, 1), 1, date(2026, 9, 2)),
        (date(2026, 9, 1), 2, date(2026, 9, 3)),
        (date(2026, 9, 30), 1, date(2026, 10, 1)),
        (date(2026, 12, 31), 1, date(2027, 1, 1)),
        (date(2026, 9, 4), 1, date(2026, 9, 5)),
    ),
)
def test_estimated_delivery_date_uses_calendar_days(
    local_today, days, expected
):
    assert estimated_delivery_date(days, local_today=local_today) == expected


def test_estimated_delivery_date_uses_guayaquil_at_utc_boundary():
    utc_instant = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    assert ecuador_local_date(utc_instant) == date(2026, 9, 1)
    assert estimated_delivery_date(1, now=utc_instant) == date(2026, 9, 2)
    assert utc_instant.astimezone(ZoneInfo("America/Guayaquil")).date() == date(
        2026, 9, 1
    )


@pytest.mark.parametrize(
    ("value", "compact", "full"),
    (
        (date(2026, 9, 2), "Mañana", "Entrega estimada mañana"),
        (
            date(2026, 9, 3),
            "Pasado mañana",
            "Entrega estimada pasado mañana",
        ),
        (
            date(2026, 9, 25),
            "25 de septiembre",
            "Entrega estimada el 25 de septiembre",
        ),
        (
            date(2026, 12, 1),
            "1 de diciembre",
            "Entrega estimada el 1 de diciembre",
        ),
        (
            date(2027, 1, 3),
            "3 de enero de 2027",
            "Entrega estimada el 3 de enero de 2027",
        ),
    ),
)
def test_delivery_eta_formatters(value, compact, full):
    reference = date(2026, 9, 1)
    assert format_delivery_eta_compact(
        value,
        reference_date=reference,
    ) == compact
    assert format_delivery_eta_full(value, reference_date=reference) == full


def test_delivery_eta_null_contract():
    reference = date(2026, 9, 1)
    assert estimated_delivery_date(None, local_today=reference) is None
    assert format_delivery_eta_compact(None, reference_date=reference) is None
    assert format_delivery_eta_full(None, reference_date=reference) is None


@pytest.mark.parametrize("value", (1, 2, "1", "2"))
def test_seller_preparation_policy_normalizes_valid_values(value):
    assert normalize_preparation_time_days(value, required=True) == int(value)


@pytest.mark.parametrize(
    "value",
    (0, 3, -1, 1.5, "1.5", "abc", " 1 ", True),
)
def test_seller_preparation_policy_rejects_invalid_values(value):
    with pytest.raises(OfferPreparationValidationError):
        normalize_preparation_time_days(value, required=True)


def test_seller_preparation_policy_allows_empty_edit_but_not_submit():
    assert normalize_preparation_time_days("", required=False) is None
    with pytest.raises(OfferPreparationValidationError):
        normalize_preparation_time_days("", required=True)
