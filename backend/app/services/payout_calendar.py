from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo


PAYOUT_TIMEZONE = ZoneInfo("America/Guayaquil")


class PayoutCycleKind(StrEnum):
    MID_MONTH = "MID_MONTH"
    MONTH_END = "MONTH_END"


@dataclass(frozen=True, slots=True)
class PayoutCycleWindow:
    cycle_date_local: date
    cycle_kind: PayoutCycleKind
    cutoff_local: datetime
    cutoff_utc: datetime
    scheduled_for_utc: datetime


def is_business_day(value: date) -> bool:
    """L2 V1: business days are Monday-Friday.

    Ecuador/banking holiday calendar is not modeled in L2 V1.
    """

    return value.weekday() < 5


def last_business_day(year: int, month: int) -> date:
    candidate = date(year, month, monthrange(year, month)[1])
    while not is_business_day(candidate):
        candidate = candidate.fromordinal(candidate.toordinal() - 1)
    return candidate


def is_payout_cycle_date(value: date) -> bool:
    return value.day == 15 or value == last_business_day(value.year, value.month)


def payout_cycle_window(value: date) -> PayoutCycleWindow:
    month_end = last_business_day(value.year, value.month)
    if value.day == 15:
        kind = PayoutCycleKind.MID_MONTH
        cutoff_date = date(value.year, value.month, 14)
    elif value == month_end:
        kind = PayoutCycleKind.MONTH_END
        cutoff_date = value.fromordinal(value.toordinal() - 1)
    else:
        raise ValueError("La fecha no corresponde a un ciclo oficial de liquidación.")

    cutoff_local = datetime.combine(
        cutoff_date,
        time(23, 59, 59),
        tzinfo=PAYOUT_TIMEZONE,
    )
    scheduled_local = datetime.combine(value, time.min, tzinfo=PAYOUT_TIMEZONE)
    return PayoutCycleWindow(
        cycle_date_local=value,
        cycle_kind=kind,
        cutoff_local=cutoff_local,
        cutoff_utc=cutoff_local.astimezone(timezone.utc),
        scheduled_for_utc=scheduled_local.astimezone(timezone.utc),
    )


def payout_cycle_windows(year: int, month: int) -> tuple[PayoutCycleWindow, ...]:
    return (
        payout_cycle_window(date(year, month, 15)),
        payout_cycle_window(last_business_day(year, month)),
    )


def local_date(value: datetime) -> date:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("El instante debe incluir zona horaria.")
    return value.astimezone(PAYOUT_TIMEZONE).date()


def validate_executable_cycle(value: date, *, now: datetime) -> PayoutCycleWindow:
    window = payout_cycle_window(value)
    if value > local_date(now):
        raise ValueError("No se puede ejecutar un ciclo futuro.")
    return window
