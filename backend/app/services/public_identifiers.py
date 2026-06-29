from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models import ProductDraft, Store, StoreProductCounter


PRODUCT_SEQUENCE_MAX = 999_999
_PREFIX_RE = re.compile(r"^[A-Z0-9]{3}$")
_PRODUCT_CODE_RE = re.compile(r"^[A-Z0-9]{3}-\d{8}-\d{6}$")


class PublicIdentifierError(Exception):
    pass


class ProductCodeLimitReachedError(PublicIdentifierError):
    pass


def format_user_code(registration_number: int | None) -> str:
    if not registration_number or registration_number < 1:
        return "U-PENDIENTE"
    return f"U-{registration_number:08d}"


def normalize_store_prefix(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").upper()
    characters = re.findall(r"[A-Z0-9]", ascii_text)
    if not characters:
        return "ECU"
    return ("".join(characters) + "XXX")[:3]


def format_store_code(prefix: str | None, registration_number: int | None) -> str:
    normalized_prefix = prefix if prefix and _PREFIX_RE.match(prefix) else "ECU"
    if not registration_number or registration_number < 1:
        return f"{normalized_prefix}-PENDIENTE"
    return f"{normalized_prefix}-{registration_number:08d}"


def format_product_code(
    prefix: str | None,
    store_registration_number: int | None,
    product_sequence: int,
) -> str:
    if product_sequence < 1 or product_sequence > PRODUCT_SEQUENCE_MAX:
        raise ProductCodeLimitReachedError(
            "La tienda alcanzó el límite de códigos de producto."
        )
    store_code = format_store_code(prefix, store_registration_number)
    if "PENDIENTE" in store_code:
        raise PublicIdentifierError(
            "La tienda aún no tiene un número público asignado."
        )
    return f"{store_code}-{product_sequence:06d}"


def is_product_code(value: str | None) -> bool:
    return bool(value and _PRODUCT_CODE_RE.match(value))


def ensure_store_public_identity(store: Store) -> None:
    if not store.product_code_prefix:
        store.product_code_prefix = normalize_store_prefix(store.name or store.legal_name)


def assign_product_code_to_draft(session: Session, draft: ProductDraft) -> str:
    if draft.seller_sku:
        draft.barcode = draft.seller_sku
        draft.condition = "NEW"
        return draft.seller_sku

    store = session.get(Store, draft.store_id, with_for_update=True)
    if store is None:
        raise PublicIdentifierError("No encontramos la tienda del borrador.")
    ensure_store_public_identity(store)

    counter = session.get(StoreProductCounter, store.id, with_for_update=True)
    if counter is None:
        counter = StoreProductCounter(store_id=store.id, last_value=0)
        session.add(counter)
        session.flush()

    next_value = counter.last_value + 1
    if next_value > PRODUCT_SEQUENCE_MAX:
        raise ProductCodeLimitReachedError(
            "La tienda alcanzó el límite de códigos de producto."
        )
    counter.last_value = next_value
    code = format_product_code(
        store.product_code_prefix,
        store.registration_number,
        next_value,
    )
    draft.seller_sku = code
    draft.barcode = code
    draft.condition = "NEW"
    session.flush()
    return code
