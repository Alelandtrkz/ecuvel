from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Callable
from typing import Any

from flask import current_app, request, session as flask_session
from flask_login import current_user
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    Cart,
    CartAdoption,
    CartItem,
    Category,
    Product,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.models.enums import OfferStatus, StoreStatus
from app.services.cart import (
    CART_VERSION,
    MAX_CART_LINES,
    MAX_CART_QUANTITY,
    get_cart_item_count,
    get_cart_state,
    get_guest_cart_merge_token,
    guest_cart_payload,
)
from app.services.inventory import get_sellable_quantities_for_offers


CART_SESSION_KEY = "cart"
CartMutation = Callable[[dict[str, Any]], dict[str, Any]]


def _new_guest_merge_token() -> str:
    return secrets.token_hex(32)


def _legacy_guest_merge_token(raw_cart: object) -> str:
    cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "session")
    signed_cookie = request.cookies.get(cookie_name)
    if signed_cookie:
        source = b"signed-cookie\0" + signed_cookie.encode("utf-8")
    else:
        canonical_cart = json.dumps(
            get_cart_state(raw_cart),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        source = b"canonical-cart-fallback\0" + canonical_cart

    secret = current_app.config["SECRET_KEY"]
    secret_bytes = (
        secret
        if isinstance(secret, bytes)
        else str(secret).encode("utf-8")
    )
    return hmac.new(
        secret_bytes,
        b"ecuvel-cart-adoption-v1\0" + source,
        hashlib.sha256,
    ).hexdigest()


def _pending_guest_cart() -> tuple[dict[str, Any], str] | None:
    raw_cart = flask_session.get(CART_SESSION_KEY)
    state = get_cart_state(raw_cart)
    if not state["items"]:
        return None
    merge_token = get_guest_cart_merge_token(raw_cart)
    if merge_token is None:
        merge_token = _legacy_guest_merge_token(raw_cart)
    return state, merge_token


def _save_guest_cart_state(state: object) -> dict[str, Any]:
    raw_cart = flask_session.get(CART_SESSION_KEY)
    merge_token = get_guest_cart_merge_token(raw_cart)
    if merge_token is None:
        legacy_state = get_cart_state(raw_cart)
        merge_token = (
            _legacy_guest_merge_token(raw_cart)
            if legacy_state["items"]
            else _new_guest_merge_token()
        )
    payload = guest_cart_payload(state, merge_token=merge_token)
    flask_session[CART_SESSION_KEY] = payload
    flask_session.modified = True
    return get_cart_state(payload)


def _user_cart_state(
    database_session: Session,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    rows = database_session.execute(
        select(
            CartItem.seller_offer_id,
            CartItem.quantity,
            CartItem.selected,
        )
        .join(Cart, Cart.id == CartItem.cart_id)
        .where(Cart.user_id == user_id)
        .order_by(CartItem.created_at, CartItem.id)
    ).all()
    return {
        "version": CART_VERSION,
        "items": {
            str(row.seller_offer_id): {
                "quantity": row.quantity,
                "selected": row.selected,
            }
            for row in rows
        },
    }


def load_cart_state_for_user(
    database_session: Session,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    return _user_cart_state(database_session, user_id)


def load_cart_state_for_identity() -> dict[str, Any]:
    if current_user.is_authenticated:
        return _user_cart_state(db.session, current_user.id)
    return get_cart_state(flask_session.get(CART_SESSION_KEY))


def _lock_or_create_cart(database_session: Session, user_id: uuid.UUID) -> Cart:
    database_session.execute(
        postgresql_insert(Cart)
        .values(id=uuid.uuid4(), user_id=user_id)
        .on_conflict_do_nothing(index_elements=[Cart.user_id])
    )
    return database_session.scalar(
        select(Cart).where(Cart.user_id == user_id).with_for_update()
    )


def _locked_cart_state(database_session: Session, cart: Cart) -> dict[str, Any]:
    rows = database_session.execute(
        select(
            CartItem.seller_offer_id,
            CartItem.quantity,
            CartItem.selected,
        )
        .where(CartItem.cart_id == cart.id)
        .order_by(CartItem.created_at, CartItem.id)
    ).all()
    return {
        "version": CART_VERSION,
        "items": {
            str(row.seller_offer_id): {
                "quantity": row.quantity,
                "selected": row.selected,
            }
            for row in rows
        },
    }


def _persist_cart_state(
    database_session: Session,
    cart: Cart,
    state: object,
) -> dict[str, Any]:
    normalized = get_cart_state(state)
    items = normalized["items"]
    offer_ids = [uuid.UUID(offer_id) for offer_id in items]

    delete_statement = delete(CartItem).where(CartItem.cart_id == cart.id)
    if offer_ids:
        delete_statement = delete_statement.where(
            CartItem.seller_offer_id.not_in(offer_ids)
        )
    database_session.execute(delete_statement)

    if items:
        values = [
            {
                "id": uuid.uuid4(),
                "cart_id": cart.id,
                "seller_offer_id": uuid.UUID(offer_id),
                "quantity": int(item["quantity"]),
                "selected": bool(item["selected"]),
            }
            for offer_id, item in items.items()
        ]
        insert_statement = postgresql_insert(CartItem).values(values)
        database_session.execute(
            insert_statement.on_conflict_do_update(
                constraint="uq_cart_items_cart_offer",
                set_={
                    "quantity": insert_statement.excluded.quantity,
                    "selected": insert_statement.excluded.selected,
                    "updated_at": func.now(),
                },
            )
        )
    database_session.execute(
        update(Cart).where(Cart.id == cart.id).values(updated_at=func.now())
    )
    return normalized


def mutate_cart_for_user(
    database_session: Session,
    user_id: uuid.UUID,
    mutation: CartMutation,
) -> dict[str, Any]:
    try:
        cart = _lock_or_create_cart(database_session, user_id)
        state = mutation(_locked_cart_state(database_session, cart))
        persisted = _persist_cart_state(database_session, cart, state)
        database_session.commit()
        return persisted
    except Exception:
        database_session.rollback()
        raise


def mutate_cart_for_identity(mutation: CartMutation) -> dict[str, Any]:
    if current_user.is_authenticated:
        return mutate_cart_for_user(db.session, current_user.id, mutation)
    state = mutation(get_cart_state(flask_session.get(CART_SESSION_KEY)))
    return _save_guest_cart_state(state)


def replace_cart_state_for_identity(state: object) -> dict[str, Any]:
    normalized = get_cart_state(state)
    if current_user.is_authenticated:
        return mutate_cart_for_user(
            db.session,
            current_user.id,
            lambda _current: normalized,
        )
    return _save_guest_cart_state(normalized)


def _merge_cart_states(
    user_state: object,
    guest_state: object,
) -> dict[str, Any]:
    merged = get_cart_state(user_state)
    guest = get_cart_state(guest_state)
    items = merged["items"]
    for offer_id, guest_item in guest["items"].items():
        existing = items.get(offer_id)
        if existing is not None:
            existing["quantity"] = min(
                MAX_CART_QUANTITY,
                int(existing["quantity"]) + int(guest_item["quantity"]),
            )
            existing["selected"] = bool(existing["selected"]) or bool(
                guest_item["selected"]
            )
        elif len(items) < MAX_CART_LINES:
            items[offer_id] = {
                "quantity": int(guest_item["quantity"]),
                "selected": bool(guest_item["selected"]),
            }
    return merged


def _rehydrate_state_for_persistence(
    database_session: Session,
    state: object,
) -> dict[str, Any]:
    normalized = get_cart_state(state)
    item_states = normalized["items"]
    offer_ids = {uuid.UUID(offer_id) for offer_id in item_states}
    if not offer_ids:
        return normalized

    rows = database_session.execute(
        select(
            SellerOffer.id.label("offer_id"),
            SellerOffer.currency,
            SellerOffer.status.label("offer_status"),
            ProductVariant.is_active.label("variant_is_active"),
            Product.is_active.label("product_is_active"),
            Category.is_active.label("category_is_active"),
            Store.status.label("store_status"),
        )
        .select_from(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(SellerOffer.id.in_(offer_ids))
    ).all()
    rows_by_offer_id = {row.offer_id: row for row in rows}
    availability = get_sellable_quantities_for_offers(
        session=database_session,
        offer_ids=set(rows_by_offer_id),
    )

    clean_items: dict[str, dict[str, int | bool]] = {}
    for stored_offer_id, item in item_states.items():
        offer_id = uuid.UUID(stored_offer_id)
        row = rows_by_offer_id.get(offer_id)
        if row is None:
            continue
        is_visible = all(
            (
                row.offer_status == OfferStatus.ACTIVE,
                row.currency == "USD",
                row.variant_is_active,
                row.product_is_active,
                row.category_is_active,
                row.store_status == StoreStatus.ACTIVE,
            )
        )
        available_quantity = (
            max(0, availability.get(offer_id, 0)) if is_visible else 0
        )
        available = is_visible and available_quantity > 0
        quantity = int(item["quantity"])
        if available:
            quantity = min(quantity, MAX_CART_QUANTITY, available_quantity)
        clean_items[stored_offer_id] = {
            "quantity": quantity,
            "selected": bool(item["selected"]) and available,
        }
    normalized["items"] = clean_items
    return normalized


def adopt_guest_cart_for_user(
    database_session: Session,
    *,
    user_id: uuid.UUID,
    guest_state: object,
    merge_token: str,
) -> bool:
    try:
        adoption_id = database_session.scalar(
            postgresql_insert(CartAdoption)
            .values(
                id=uuid.uuid4(),
                merge_token=merge_token,
                claimed_user_id=user_id,
            )
            .on_conflict_do_nothing(index_elements=[CartAdoption.merge_token])
            .returning(CartAdoption.id)
        )
        if adoption_id is None:
            database_session.commit()
            return False

        cart = _lock_or_create_cart(database_session, user_id)
        current_state = _rehydrate_state_for_persistence(
            database_session,
            _locked_cart_state(database_session, cart),
        )
        normalized_guest = _rehydrate_state_for_persistence(
            database_session,
            guest_state,
        )
        merged = _merge_cart_states(current_state, normalized_guest)
        reconciled = _rehydrate_state_for_persistence(database_session, merged)
        _persist_cart_state(database_session, cart, reconciled)
        database_session.commit()
        return True
    except Exception:
        database_session.rollback()
        raise


def adopt_guest_cart_for_authenticated_user() -> bool:
    if CART_SESSION_KEY not in flask_session:
        return False
    if not current_user.is_authenticated:
        return False
    pending = _pending_guest_cart()
    if pending is None:
        flask_session.pop(CART_SESSION_KEY, None)
        return False

    guest_state, merge_token = pending
    adopted = adopt_guest_cart_for_user(
        db.session,
        user_id=current_user.id,
        guest_state=guest_state,
        merge_token=merge_token,
    )
    flask_session.pop(CART_SESSION_KEY, None)
    flask_session.modified = True
    return adopted


def cart_count_for_identity() -> int:
    if not current_user.is_authenticated:
        return get_cart_item_count(flask_session.get(CART_SESSION_KEY))
    return int(
        db.session.scalar(
            select(func.coalesce(func.sum(CartItem.quantity), 0))
            .select_from(CartItem)
            .join(Cart, Cart.id == CartItem.cart_id)
            .where(Cart.user_id == current_user.id)
        )
        or 0
    )
