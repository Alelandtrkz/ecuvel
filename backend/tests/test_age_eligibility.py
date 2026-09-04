from __future__ import annotations

from datetime import date

import pytest

from app.services.age_eligibility import is_at_least_18


@pytest.mark.parametrize(
    ("birth_date", "today", "expected"),
    (
        (date(2008, 9, 4), date(2026, 9, 4), True),
        (date(2008, 9, 5), date(2026, 9, 4), False),
        (date(2000, 1, 1), date(2026, 9, 4), True),
        (date(2025, 1, 1), date(2026, 9, 4), False),
        (None, date(2026, 9, 4), False),
        (date(2026, 9, 5), date(2026, 9, 4), False),
        (date(2008, 2, 29), date(2026, 2, 28), False),
        (date(2008, 2, 29), date(2026, 3, 1), True),
    ),
)
def test_is_at_least_18_uses_completed_calendar_years(
    birth_date,
    today,
    expected,
):
    assert is_at_least_18(birth_date, today=today) is expected


def test_is_at_least_18_handles_february_29_reference_date():
    today = date(2024, 2, 29)

    assert is_at_least_18(date(2006, 2, 28), today=today)
    assert not is_at_least_18(date(2006, 3, 1), today=today)


def test_is_at_least_18_uses_ecuador_civil_date_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.age_eligibility.ecuador_local_date",
        lambda: date(2026, 9, 4),
    )

    assert is_at_least_18(date(2008, 9, 4))
    assert not is_at_least_18(date(2008, 9, 5))
