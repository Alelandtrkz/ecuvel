from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import date

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.services.catalog_ranking import (
    SURFACE_CATEGORY,
    SURFACE_HOME,
    SURFACE_SEARCH,
    SURFACE_STORE,
    private_search_context,
)


CATALOG_FEED_CURSOR_SALT = "ecuvel-catalog-feed-cursor-v1"
CATALOG_FEED_CURSOR_VERSION = 1
CATALOG_FEED_SURFACES = frozenset(
    {SURFACE_HOME, SURFACE_SEARCH, SURFACE_CATEGORY, SURFACE_STORE}
)


class InvalidCatalogFeedCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogFeedCursor:
    version: int
    ranking_day: date
    surface: str
    context_hash: str
    category_slug: str | None
    store_slug: str | None
    next_position: int
    ranking_request_id: uuid.UUID
    batch_size: int


def catalog_feed_context_hash(
    *,
    surface: str,
    query: str = "",
    category_slug: str = "",
    store_slug: str = "",
) -> str:
    """Bind a feed to public routing context without retaining raw search text."""
    if surface == SURFACE_SEARCH:
        context = f"{private_search_context(query)}\x1e{category_slug}"
    elif surface == SURFACE_CATEGORY:
        context = category_slug
    elif surface == SURFACE_STORE:
        context = store_slug
    elif surface == SURFACE_HOME:
        context = ""
    else:
        raise ValueError("Superficie de catálogo no permitida.")
    payload = f"{surface}\x1f{context}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sign_catalog_feed_cursor(secret_key: str, cursor: CatalogFeedCursor) -> str:
    payload = asdict(cursor)
    payload["ranking_day"] = cursor.ranking_day.isoformat()
    payload["ranking_request_id"] = str(cursor.ranking_request_id)
    return _serializer(secret_key).dumps(payload)


def load_catalog_feed_cursor(
    secret_key: str,
    token: str,
    *,
    max_age_seconds: int,
) -> CatalogFeedCursor:
    try:
        payload = _serializer(secret_key).loads(
            token,
            max_age=max_age_seconds,
        )
        cursor = CatalogFeedCursor(
            version=int(payload["version"]),
            ranking_day=date.fromisoformat(str(payload["ranking_day"])),
            surface=str(payload["surface"]),
            context_hash=str(payload["context_hash"]),
            category_slug=(
                str(payload["category_slug"])
                if payload.get("category_slug")
                else None
            ),
            store_slug=(
                str(payload["store_slug"])
                if payload.get("store_slug")
                else None
            ),
            next_position=int(payload["next_position"]),
            ranking_request_id=uuid.UUID(str(payload["ranking_request_id"])),
            batch_size=int(payload["batch_size"]),
        )
    except (
        BadSignature,
        SignatureExpired,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise InvalidCatalogFeedCursorError(
            "El cursor del catálogo es inválido o expiró."
        ) from exc

    if (
        cursor.version != CATALOG_FEED_CURSOR_VERSION
        or cursor.surface not in CATALOG_FEED_SURFACES
        or len(cursor.context_hash) != 64
        or cursor.next_position < 0
        or not 1 <= cursor.batch_size <= 100
        or (cursor.surface == SURFACE_CATEGORY and not cursor.category_slug)
        or (cursor.surface == SURFACE_HOME and cursor.category_slug is not None)
        or (cursor.surface == SURFACE_STORE) != bool(cursor.store_slug)
    ):
        raise InvalidCatalogFeedCursorError(
            "El cursor del catálogo es inválido o expiró."
        )
    return cursor


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=CATALOG_FEED_CURSOR_SALT)
