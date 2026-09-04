from __future__ import annotations

from datetime import date

from app.services.delivery_eta import ecuador_local_date


ADULT_AGE_YEARS = 18


def is_at_least_18(
    birth_date: date | None,
    *,
    today: date | None = None,
) -> bool:
    """Return whether ``birth_date`` has reached ECUVEL's adult threshold.

    February 29 birthdays reach their anniversary on March 1 in non-leap
    years.  When ``today`` is omitted, Ecuador's civil date is authoritative.
    """
    if birth_date is None:
        return False

    reference_date = today if today is not None else ecuador_local_date()
    if birth_date > reference_date:
        return False

    cutoff_year = reference_date.year - ADULT_AGE_YEARS
    if cutoff_year < date.min.year:
        return False

    try:
        cutoff = reference_date.replace(year=cutoff_year)
    except ValueError:
        # The only in-range invalid replacement is Feb 29 into a non-leap year.
        cutoff = date(cutoff_year, 2, 28)

    return birth_date <= cutoff
