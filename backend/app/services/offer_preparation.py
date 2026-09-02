from __future__ import annotations

from collections.abc import Mapping
from typing import Any


PREPARATION_TIME_MESSAGE = "Selecciona un tiempo de preparación de 1 o 2 días."


class OfferPreparationValidationError(ValueError):
    pass


def normalize_preparation_time_days(
    value: Any,
    *,
    required: bool,
) -> int | None:
    """Normalize the seller MVP policy without inventing a legacy value."""
    if value is None or value == "":
        if required:
            raise OfferPreparationValidationError(PREPARATION_TIME_MESSAGE)
        return None
    if isinstance(value, bool):
        raise OfferPreparationValidationError(PREPARATION_TIME_MESSAGE)
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and value in {"1", "2"}:
        normalized = int(value)
    else:
        raise OfferPreparationValidationError(PREPARATION_TIME_MESSAGE)
    if normalized not in {1, 2}:
        raise OfferPreparationValidationError(PREPARATION_TIME_MESSAGE)
    return normalized


def preparation_time_from_inventory(
    inventory_data: Mapping[str, Any] | None,
    *,
    required: bool,
) -> int | None:
    return normalize_preparation_time_days(
        (inventory_data or {}).get("preparation_time_days"),
        required=required,
    )
