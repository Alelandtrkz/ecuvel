from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


CART_VERSION = 1
MAX_CART_LINES = 50
MAX_CART_QUANTITY = 99
CART_LOW_STOCK_THRESHOLD = 5


class CartServiceError(Exception):
    """Error base para operaciones del carrito de sesión."""


class InvalidCartQuantityError(CartServiceError):
    """La cantidad del carrito no es un entero permitido."""


class CartItemLimitError(CartServiceError):
    """El carrito alcanzó su máximo de líneas."""


def _empty_cart() -> dict[str, Any]:
    return {"version": CART_VERSION, "items": {}}


def _normalized_offer_id(offer_id: object) -> str | None:
    try:
        return str(uuid.UUID(str(offer_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _validated_quantity(quantity: object) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidCartQuantityError(
            "La cantidad debe ser un número entero."
        )
    if quantity < 1 or quantity > MAX_CART_QUANTITY:
        raise InvalidCartQuantityError(
            f"La cantidad debe estar entre 1 y {MAX_CART_QUANTITY}."
        )
    return quantity


def get_cart_state(session_data: object) -> dict[str, Any]:
    """Devuelve una copia normalizada y segura del carrito."""

    if not isinstance(session_data, Mapping):
        return _empty_cart()
    if session_data.get("version") != CART_VERSION:
        return _empty_cart()

    raw_items = session_data.get("items")
    if not isinstance(raw_items, Mapping):
        return _empty_cart()

    normalized_items: dict[str, dict[str, int | bool]] = {}
    for raw_offer_id, raw_item in raw_items.items():
        if len(normalized_items) >= MAX_CART_LINES:
            break
        offer_id = _normalized_offer_id(raw_offer_id)
        if offer_id is None or not isinstance(raw_item, Mapping):
            continue

        quantity = raw_item.get("quantity")
        selected = raw_item.get("selected")
        try:
            normalized_quantity = _validated_quantity(quantity)
        except InvalidCartQuantityError:
            continue
        if type(selected) is not bool:
            continue

        normalized_items[offer_id] = {
            "quantity": normalized_quantity,
            "selected": selected,
        }

    return {"version": CART_VERSION, "items": normalized_items}


def add_cart_item(
    session_data: object,
    offer_id: uuid.UUID | str,
    quantity: int,
) -> dict[str, Any]:
    state = get_cart_state(session_data)
    normalized_offer_id = _normalized_offer_id(offer_id)
    if normalized_offer_id is None:
        raise CartServiceError("El identificador de oferta no es válido.")
    normalized_quantity = _validated_quantity(quantity)
    items = state["items"]

    if normalized_offer_id in items:
        existing_quantity = items[normalized_offer_id]["quantity"]
        items[normalized_offer_id] = {
            "quantity": min(
                MAX_CART_QUANTITY,
                int(existing_quantity) + normalized_quantity,
            ),
            "selected": True,
        }
    else:
        if len(items) >= MAX_CART_LINES:
            raise CartItemLimitError(
                f"El carrito admite hasta {MAX_CART_LINES} productos."
            )
        items[normalized_offer_id] = {
            "quantity": normalized_quantity,
            "selected": True,
        }
    return state


def set_cart_item_quantity(
    session_data: object,
    offer_id: uuid.UUID | str,
    quantity: int,
) -> dict[str, Any]:
    state = get_cart_state(session_data)
    normalized_offer_id = _normalized_offer_id(offer_id)
    normalized_quantity = _validated_quantity(quantity)
    if normalized_offer_id in state["items"]:
        state["items"][normalized_offer_id]["quantity"] = (
            normalized_quantity
        )
    return state


def set_cart_item_selected(
    session_data: object,
    offer_id: uuid.UUID | str,
    selected: bool,
) -> dict[str, Any]:
    state = get_cart_state(session_data)
    normalized_offer_id = _normalized_offer_id(offer_id)
    if normalized_offer_id in state["items"]:
        state["items"][normalized_offer_id]["selected"] = bool(selected)
    return state


def set_all_cart_items_selected(
    session_data: object,
    selected: bool,
) -> dict[str, Any]:
    state = get_cart_state(session_data)
    for item in state["items"].values():
        item["selected"] = bool(selected)
    return state


def remove_cart_item(
    session_data: object,
    offer_id: uuid.UUID | str,
) -> dict[str, Any]:
    state = get_cart_state(session_data)
    normalized_offer_id = _normalized_offer_id(offer_id)
    state["items"].pop(normalized_offer_id, None)
    return state


def remove_selected_cart_items(session_data: object) -> dict[str, Any]:
    state = get_cart_state(session_data)
    state["items"] = {
        offer_id: item
        for offer_id, item in state["items"].items()
        if not item["selected"]
    }
    return state


def clear_cart(_session_data: object) -> dict[str, Any]:
    return _empty_cart()


def get_cart_item_count(session_data: object) -> int:
    state = get_cart_state(session_data)
    return sum(int(item["quantity"]) for item in state["items"].values())
