from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductMedia, ProductVariant
from app.services.private_storage import PrivateStorageError, private_file_path


logger = logging.getLogger(__name__)

DISPLAYABLE_PRODUCT_MEDIA_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)


def variant_media_binding(
    configuration: dict[str, Any],
    attributes: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return the configured visual axis and this variant's canonical value."""
    visual_axis = configuration.get("visual_axis_key")
    if not visual_axis:
        return None, None

    axis_key = str(visual_axis)
    value_key = variant_value_key(configuration, attributes, axis_key)
    if value_key is None:
        return None, None
    return axis_key, value_key


def variant_value_key(
    configuration: dict[str, Any],
    attributes: dict[str, Any],
    axis_key: str,
) -> str | None:
    """Normalize a variant attribute label to its configured stable key."""
    raw_value = attributes.get(axis_key)
    if raw_value is None:
        return None

    for axis in configuration.get("axes") or []:
        if not isinstance(axis, dict) or axis.get("key") != axis_key:
            continue
        for value in axis.get("values") or []:
            if not isinstance(value, dict):
                continue
            if raw_value in {value.get("key"), value.get("label")}:
                canonical_value = value.get("key")
                if canonical_value is not None:
                    return str(canonical_value)
    return str(raw_value)


def ordered_product_media(
    media_rows: Iterable[ProductMedia],
    *,
    variant_axis_key: str | None = None,
    variant_value_key: str | None = None,
) -> tuple[ProductMedia, ...]:
    """Select and order one compatible media group without I/O."""
    eligible = [
        media
        for media in media_rows
        if media.is_active
        and media.media_type.lower() in DISPLAYABLE_PRODUCT_MEDIA_TYPES
    ]
    candidates: list[ProductMedia] = []
    if variant_axis_key is not None and variant_value_key is not None:
        candidates = [
            media
            for media in eligible
            if media.variant_axis_key == variant_axis_key
            and media.variant_value_key == variant_value_key
        ]
    if not candidates:
        candidates = [
            media
            for media in eligible
            if media.variant_axis_key is None
            and media.variant_value_key is None
        ]
    return tuple(
        sorted(
            candidates,
            key=lambda media: (
                not media.is_cover,
                media.position,
                media.created_at,
                media.id,
            ),
        )
    )


def select_product_media(
    media_rows: Iterable[ProductMedia],
    *,
    variant_axis_key: str | None = None,
    variant_value_key: str | None = None,
) -> ProductMedia | None:
    ordered = ordered_product_media(
        media_rows,
        variant_axis_key=variant_axis_key,
        variant_value_key=variant_value_key,
    )
    return ordered[0] if ordered else None


def load_product_card_media(
    session: Session,
    product_variant_pairs: Iterable[tuple[uuid.UUID, uuid.UUID]],
    *,
    media_root: str | Path,
) -> dict[uuid.UUID, ProductMedia]:
    """Resolve media for all represented variants with one database query."""
    requested = set(product_variant_pairs)
    if not requested:
        return {}

    product_ids = {product_id for product_id, _variant_id in requested}
    variant_ids = {variant_id for _product_id, variant_id in requested}
    rows = session.execute(
        select(
            ProductMedia,
            ProductVariant.id.label("variant_id"),
            Product.variant_configuration.label("variant_configuration"),
            ProductVariant.attributes.label("variant_attributes"),
        )
        .select_from(ProductMedia)
        .join(Product, Product.id == ProductMedia.product_id)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(
            ProductMedia.product_id.in_(product_ids),
            ProductVariant.id.in_(variant_ids),
            ProductMedia.is_active.is_(True),
        )
        .order_by(
            ProductMedia.product_id,
            ProductVariant.id,
            ProductMedia.is_cover.desc(),
            ProductMedia.position,
            ProductMedia.created_at,
            ProductMedia.id,
        )
    ).all()

    media_by_pair: dict[
        tuple[uuid.UUID, uuid.UUID], list[ProductMedia]
    ] = defaultdict(list)
    binding_by_pair: dict[
        tuple[uuid.UUID, uuid.UUID], tuple[str | None, str | None]
    ] = {}
    for media, variant_id, configuration, attributes in rows:
        pair = (media.product_id, variant_id)
        if pair not in requested:
            continue
        media_by_pair[pair].append(media)
        binding_by_pair[pair] = variant_media_binding(
            configuration or {},
            attributes or {},
        )

    selected_by_variant: dict[uuid.UUID, ProductMedia] = {}
    for pair in requested:
        axis_key, value_key = binding_by_pair.get(pair, (None, None))
        candidates: Sequence[ProductMedia] = ordered_product_media(
            media_by_pair.get(pair, ()),
            variant_axis_key=axis_key,
            variant_value_key=value_key,
        )
        for media in candidates:
            try:
                exists = private_file_path(media_root, media.storage_key).is_file()
            except (OSError, PrivateStorageError):
                exists = False
            if exists:
                selected_by_variant[pair[1]] = media
                break
            logger.warning(
                "Published product media file is unavailable; using a fallback "
                "(product_id=%s, media_public_id=%s)",
                pair[0],
                media.public_id,
            )
    return selected_by_variant
