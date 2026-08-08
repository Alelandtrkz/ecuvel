from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from flask import url_for
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import ProductDraftFile, ProductMedia
from app.models.enums import ProductDraftFileKind, ProductDraftFileStatus


PAGE_SIZE = 20


@dataclass(frozen=True, slots=True)
class PartnerCatalogStatusOption:
    value: str
    label: str


STATUS_OPTIONS = (
    PartnerCatalogStatusOption("draft", "Borrador"),
    PartnerCatalogStatusOption("incomplete", "Incompleto"),
    PartnerCatalogStatusOption("ready", "Listo para enviar"),
    PartnerCatalogStatusOption("review", "En revisión"),
    PartnerCatalogStatusOption("changes", "Cambios solicitados"),
    PartnerCatalogStatusOption("rejected", "Rechazado"),
    PartnerCatalogStatusOption("approved", "Aprobado"),
    PartnerCatalogStatusOption("active", "Activo"),
    PartnerCatalogStatusOption("deactivated", "Desactivado"),
)
_STATUS_LABELS = {item.value: item.label for item in STATUS_OPTIONS}
_KNOWN_STATUSES = set(_STATUS_LABELS)


@dataclass(frozen=True, slots=True)
class PartnerCatalogCategoryOption:
    id: uuid.UUID
    name: str


@dataclass(frozen=True, slots=True)
class PartnerCatalogRow:
    source: str
    draft_id: uuid.UUID | None
    offer_id: uuid.UUID | None
    product_id: uuid.UUID | None
    product_slug: str | None
    base_title: str
    variant_name: str | None
    sku: str | None
    parent_sku: str | None
    category_id: uuid.UUID
    category_name: str
    price: Decimal | None
    currency: str
    stock: int | None
    status: str
    status_label: str
    updated_at: datetime
    provisional: bool
    visual_axis_key: str | None
    visual_value_key: str | None
    variant_attributes: dict[str, Any]
    variant_configuration: dict[str, Any]
    single_media_value_key: str | None
    draft_variant_count: int
    draft_image_count: int
    draft_document_count: int
    thumbnail_url: str
    action_label: str | None
    action_url: str | None
    edit_url: str | None
    preview_url: str | None
    delete_url: str | None
    public_url: str | None
    can_select: bool

    @property
    def display_title(self) -> str:
        if not self.variant_name or self.provisional:
            return self.base_title
        return f"{self.base_title} — {self.variant_name}"

    @property
    def is_deactivated(self) -> bool:
        return self.status == "deactivated"

    @property
    def has_actions(self) -> bool:
        return any((self.edit_url, self.preview_url, self.delete_url, self.public_url))


@dataclass(frozen=True, slots=True)
class PartnerCatalogPage:
    store_name: str
    items: tuple[PartnerCatalogRow, ...]
    categories: tuple[PartnerCatalogCategoryOption, ...]
    statuses: tuple[PartnerCatalogStatusOption, ...]
    query: str
    selected_status: str
    selected_category: str
    page: int
    page_size: int
    total_pages: int
    total_items: int
    filtered_items: int
    range_start: int
    range_end: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def page_window(self) -> tuple[int | None, ...]:
        """Return a compact, stable pagination window for the template."""
        candidates = {
            1,
            self.total_pages,
            self.page - 1,
            self.page,
            self.page + 1,
        }
        pages = sorted(page for page in candidates if 1 <= page <= self.total_pages)
        window: list[int | None] = []
        previous = 0
        for page in pages:
            if previous and page - previous > 1:
                window.append(None)
            window.append(page)
            previous = page
        return tuple(window)


_CATALOG_CTE = r"""
WITH draft_rows AS (
    SELECT
        'draft'::text AS source,
        d.id AS draft_id,
        NULL::uuid AS offer_id,
        NULL::uuid AS product_id,
        NULL::text AS product_slug,
        COALESCE(NULLIF(d.title, ''), 'Producto sin título') AS base_title,
        CASE
            WHEN family.is_family AND variant.value IS NULL THEN 'Presentación pendiente'
            WHEN family.is_family THEN NULLIF(variant.value->>'name', '')
            ELSE NULL::text
        END AS variant_name,
        COALESCE(NULLIF(variant.value->>'sku', ''), d.seller_sku) AS sku,
        d.seller_sku AS parent_sku,
        d.subcategory_id AS category_id,
        category.name AS category_name,
        CASE
            WHEN family.is_family THEN NULLIF(variant.value->>'price', '')
            ELSE NULLIF(d.pricing_data->>'price', '')
        END AS price_text,
        COALESCE(NULLIF(d.pricing_data->>'currency', ''), 'USD') AS currency,
        CASE
            WHEN family.is_family THEN NULLIF(variant.value->>'stock', '')
            ELSE NULLIF(d.inventory_data->>'stock_quantity', '')
        END AS stock_text,
        CASE d.status::text
            WHEN 'DRAFT' THEN 'draft'
            WHEN 'INCOMPLETE' THEN 'incomplete'
            WHEN 'READY_FOR_REVIEW' THEN 'ready'
            WHEN 'SUBMITTED' THEN 'review'
            WHEN 'CHANGES_REQUESTED' THEN 'changes'
            WHEN 'REJECTED' THEN 'rejected'
            WHEN 'APPROVED' THEN 'approved'
            ELSE 'draft'
        END AS normalized_status,
        d.updated_at AS updated_at,
        (family.is_family AND variant.value IS NULL) AS provisional,
        NULLIF(d.variant_configuration->>'visual_axis_key', '') AS visual_axis_key,
        CASE
            WHEN variant.value IS NOT NULL AND NULLIF(d.variant_configuration->>'visual_axis_key', '') IS NOT NULL
            THEN variant.value->'options'->>(d.variant_configuration->>'visual_axis_key')
            ELSE NULL::text
        END AS visual_value_key,
        COALESCE(variant.value->'attributes', '{}'::jsonb) AS variant_attributes,
        COALESCE(d.variant_configuration, '{}'::jsonb) AS variant_configuration,
        NULLIF(d.variant_configuration->>'single_media_value_key', '') AS single_media_value_key,
        jsonb_array_length(COALESCE(d.variants, '[]'::jsonb)) AS draft_variant_count,
        1 AS source_priority,
        'draft:' || d.id::text || ':' || COALESCE(NULLIF(variant.value->>'variant_id', ''), 'single') AS entity_key
    FROM product_drafts AS d
    JOIN categories AS category ON category.id = d.subcategory_id
    CROSS JOIN LATERAL (
        SELECT (
            (
                COALESCE(NULLIF(d.variant_configuration->>'version', '')::integer, 1) < 4
                AND (
                    jsonb_array_length(COALESCE(d.variants, '[]'::jsonb)) > 0
                    OR COALESCE((d.variant_configuration->>'enabled')::boolean, false)
                    OR jsonb_array_length(COALESCE(d.variant_configuration->'axes', '[]'::jsonb)) > 0
                )
            ) OR (
                COALESCE((d.variant_configuration->>'enabled')::boolean, false)
                AND d.variant_configuration->>'mode' = 'family'
            )
        ) AS is_family
    ) AS family
    LEFT JOIN LATERAL jsonb_array_elements(COALESCE(d.variants, '[]'::jsonb)) AS variant(value)
        ON family.is_family
        AND COALESCE((variant.value->>'enabled')::boolean, true)
    WHERE d.store_id = :store_id
),
inventory AS (
    SELECT
        balance.offer_id,
        COALESCE(SUM(
            balance.on_hand_quantity
            - balance.reserved_quantity
            - balance.blocked_quantity
        ), 0)::text AS stock_text
    FROM inventory_balances AS balance
    JOIN seller_offers AS scoped_offer
        ON scoped_offer.id = balance.offer_id
        AND scoped_offer.store_id = :store_id
    GROUP BY balance.offer_id
),
offer_rows AS (
    SELECT
        'offer'::text AS source,
        NULL::uuid AS draft_id,
        offer.id AS offer_id,
        product.id AS product_id,
        product.slug AS product_slug,
        product.title AS base_title,
        NULLIF(variant.title, '') AS variant_name,
        variant.catalog_sku AS sku,
        offer.seller_sku AS parent_sku,
        product.category_id AS category_id,
        category.name AS category_name,
        offer.price::text AS price_text,
        offer.currency AS currency,
        COALESCE(inventory.stock_text, '0') AS stock_text,
        CASE
            WHEN NOT product.is_active OR NOT variant.is_active THEN 'deactivated'
            WHEN offer.status::text = 'ACTIVE' THEN 'active'
            WHEN offer.status::text IN ('PAUSED', 'ARCHIVED') THEN 'deactivated'
            WHEN offer.status::text = 'PENDING_REVIEW' THEN 'review'
            WHEN offer.status::text = 'REJECTED' THEN 'rejected'
            ELSE 'draft'
        END AS normalized_status,
        GREATEST(product.updated_at, variant.updated_at, offer.updated_at) AS updated_at,
        false AS provisional,
        NULLIF(product.variant_configuration->>'visual_axis_key', '') AS visual_axis_key,
        NULL::text AS visual_value_key,
        COALESCE(variant.attributes, '{}'::jsonb) AS variant_attributes,
        COALESCE(product.variant_configuration, '{}'::jsonb) AS variant_configuration,
        NULL::text AS single_media_value_key,
        0 AS draft_variant_count,
        2 AS source_priority,
        'offer:' || offer.id::text AS entity_key
    FROM seller_offers AS offer
    JOIN product_variants AS variant ON variant.id = offer.variant_id
    JOIN products AS product ON product.id = variant.product_id
    JOIN categories AS category ON category.id = product.category_id
    LEFT JOIN inventory ON inventory.offer_id = offer.id
    WHERE offer.store_id = :store_id
),
all_rows AS (
    SELECT * FROM draft_rows
    UNION ALL
    SELECT * FROM offer_rows
),
ranked_rows AS (
    SELECT
        all_rows.*,
        ARRAY_AGG(all_rows.draft_id) FILTER (WHERE all_rows.draft_id IS NOT NULL)
            OVER (
                PARTITION BY COALESCE(LOWER(all_rows.sku), all_rows.entity_key)
            ) AS matching_draft_ids,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(LOWER(all_rows.sku), all_rows.entity_key)
            ORDER BY all_rows.source_priority DESC, all_rows.updated_at DESC, all_rows.entity_key
        ) AS source_rank
    FROM all_rows
),
catalog_rows AS (
    SELECT * FROM ranked_rows WHERE source_rank = 1
)
"""


def get_partner_product_catalog(
    session: Session,
    *,
    store_id: uuid.UUID,
    store_name: str,
    query: str | None,
    status: str | None,
    category: str | None,
    page: int | str | None,
) -> PartnerCatalogPage:
    normalized_query = str(query or "").strip()[:160]
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _KNOWN_STATUSES:
        normalized_status = ""
    category_id = _uuid_or_none(category)
    selected_category = str(category_id) if category_id else ""

    filters, parameters = _filter_sql(
        store_id=store_id,
        query=normalized_query,
        status=normalized_status,
        category_id=category_id,
    )
    count_sql = text(
        _CATALOG_CTE
        + f"""
        SELECT
            (SELECT COUNT(*) FROM catalog_rows) AS total_items,
            COUNT(*) AS filtered_items
        FROM catalog_rows
        WHERE {filters}
        """
    )
    counts = session.execute(count_sql, parameters).mappings().one()
    total_items = int(counts["total_items"] or 0)
    filtered_items = int(counts["filtered_items"] or 0)
    total_pages = max(1, math.ceil(filtered_items / PAGE_SIZE))
    normalized_page = min(_positive_int(page, default=1), total_pages)

    rows_sql = text(
        _CATALOG_CTE
        + f"""
        SELECT *
        FROM catalog_rows
        WHERE {filters}
        ORDER BY updated_at DESC, LOWER(COALESCE(sku, '')) ASC, entity_key ASC
        LIMIT :limit OFFSET :offset
        """
    )
    row_parameters = {
        **parameters,
        "limit": PAGE_SIZE,
        "offset": (normalized_page - 1) * PAGE_SIZE,
    }
    raw_rows = session.execute(rows_sql, row_parameters).mappings().all()
    items = [_row_view(row) for row in raw_rows]
    items = _attach_thumbnails(items, session=session)
    items = [_attach_action(item) for item in items]

    categories_sql = text(
        _CATALOG_CTE
        + """
        SELECT DISTINCT category_id, category_name
        FROM catalog_rows
        ORDER BY category_name, category_id
        """
    )
    category_rows = session.execute(
        categories_sql,
        {"store_id": store_id},
    ).mappings().all()
    categories = tuple(
        PartnerCatalogCategoryOption(id=row["category_id"], name=row["category_name"])
        for row in category_rows
    )
    range_start = (normalized_page - 1) * PAGE_SIZE + 1 if filtered_items else 0
    range_end = min(normalized_page * PAGE_SIZE, filtered_items)
    return PartnerCatalogPage(
        store_name=store_name,
        items=tuple(items),
        categories=categories,
        statuses=STATUS_OPTIONS,
        query=normalized_query,
        selected_status=normalized_status,
        selected_category=selected_category,
        page=normalized_page,
        page_size=PAGE_SIZE,
        total_pages=total_pages,
        total_items=total_items,
        filtered_items=filtered_items,
        range_start=range_start,
        range_end=range_end,
    )


def _filter_sql(
    *,
    store_id: uuid.UUID,
    query: str,
    status: str,
    category_id: uuid.UUID | None,
) -> tuple[str, dict[str, Any]]:
    clauses = ["true"]
    parameters: dict[str, Any] = {"store_id": store_id}
    if query:
        clauses.append(
            "concat_ws(' ', base_title, variant_name, sku, parent_sku) ILIKE :search"
        )
        parameters["search"] = f"%{query}%"
    if status:
        clauses.append("normalized_status = :status")
        parameters["status"] = status
    if category_id:
        clauses.append("category_id = :category_id")
        parameters["category_id"] = category_id
    return " AND ".join(clauses), parameters


def _row_view(row: Mapping[str, Any]) -> PartnerCatalogRow:
    status = str(row["normalized_status"])
    matching_drafts = row.get("matching_draft_ids") or []
    return PartnerCatalogRow(
        source=str(row["source"]),
        draft_id=row["draft_id"] or (matching_drafts[0] if matching_drafts else None),
        offer_id=row["offer_id"],
        product_id=row["product_id"],
        product_slug=row["product_slug"],
        base_title=str(row["base_title"]),
        variant_name=row["variant_name"],
        sku=row["sku"],
        parent_sku=row["parent_sku"],
        category_id=row["category_id"],
        category_name=str(row["category_name"]),
        price=_decimal_or_none(row["price_text"]),
        currency=str(row["currency"] or "USD"),
        stock=_int_or_none(row["stock_text"]),
        status=status,
        status_label=_STATUS_LABELS.get(status, "Borrador"),
        updated_at=row["updated_at"],
        provisional=bool(row["provisional"]),
        visual_axis_key=row["visual_axis_key"],
        visual_value_key=row["visual_value_key"],
        variant_attributes=dict(row["variant_attributes"] or {}),
        variant_configuration=dict(row["variant_configuration"] or {}),
        single_media_value_key=row["single_media_value_key"],
        draft_variant_count=max(1, int(row["draft_variant_count"] or 0)),
        draft_image_count=0,
        draft_document_count=0,
        thumbnail_url=url_for("static", filename="images/placeholders/product-placeholder.svg"),
        action_label=None,
        action_url=None,
        edit_url=None,
        preview_url=None,
        delete_url=None,
        public_url=None,
        can_select=False,
    )


def _attach_thumbnails(
    items: Sequence[PartnerCatalogRow],
    *,
    session: Session,
) -> list[PartnerCatalogRow]:
    draft_ids = {item.draft_id for item in items if item.draft_id is not None}
    product_ids = {item.product_id for item in items if item.product_id is not None}
    draft_media: dict[uuid.UUID, list[ProductDraftFile]] = {}
    public_media: dict[uuid.UUID, list[ProductMedia]] = {}
    if draft_ids:
        files = session.scalars(
            select(ProductDraftFile)
            .where(
                ProductDraftFile.draft_id.in_(draft_ids),
                ProductDraftFile.status == ProductDraftFileStatus.ACTIVE,
            )
            .order_by(
                ProductDraftFile.draft_id,
                ProductDraftFile.is_cover.desc(),
                ProductDraftFile.position,
                ProductDraftFile.created_at,
                ProductDraftFile.id,
            )
        ).all()
        for file in files:
            draft_media.setdefault(file.draft_id, []).append(file)
    if product_ids:
        media_rows = session.scalars(
            select(ProductMedia)
            .where(
                ProductMedia.product_id.in_(product_ids),
                ProductMedia.is_active.is_(True),
            )
            .order_by(
                ProductMedia.product_id,
                ProductMedia.is_cover.desc(),
                ProductMedia.position,
                ProductMedia.created_at,
                ProductMedia.id,
            )
        ).all()
        for media in media_rows:
            public_media.setdefault(media.product_id, []).append(media)

    resolved = []
    for item in items:
        thumbnail = item.thumbnail_url
        if item.source == "draft" and item.draft_id:
            files = draft_media.get(item.draft_id, [])
            images = [file for file in files if file.kind == ProductDraftFileKind.IMAGE]
            documents = [file for file in files if file.kind == ProductDraftFileKind.DOCUMENT]
            selected = _draft_thumbnail(item, images)
            if selected:
                thumbnail = url_for(
                    "partners.product_draft_file",
                    draft_id=item.draft_id,
                    file_id=selected.id,
                )
            item = replace(
                item,
                draft_image_count=len(images),
                draft_document_count=len(documents),
            )
        elif item.source == "offer" and item.product_id:
            selected = _public_thumbnail(item, public_media.get(item.product_id, []))
            if selected and item.product_slug:
                thumbnail = url_for(
                    "storefront.product_media",
                    product_slug=item.product_slug,
                    public_id=selected.public_id,
                )
        resolved.append(replace(item, thumbnail_url=thumbnail))
    return resolved


def _draft_thumbnail(
    item: PartnerCatalogRow,
    files: Sequence[ProductDraftFile],
) -> ProductDraftFile | None:
    if item.visual_axis_key and item.visual_value_key:
        return next(
            (
                file for file in files
                if file.variant_axis_key == item.visual_axis_key
                and file.variant_value_key == item.visual_value_key
            ),
            None,
        )
    if item.single_media_value_key:
        return next(
            (
                file for file in files
                if file.variant_value_key in {None, item.single_media_value_key}
            ),
            None,
        )
    return next((file for file in files if file.variant_axis_key is None), None)


def _public_thumbnail(
    item: PartnerCatalogRow,
    media: Sequence[ProductMedia],
) -> ProductMedia | None:
    visual_key = item.visual_axis_key
    visual_value = _variant_value_key(
        item.variant_configuration,
        item.variant_attributes,
        visual_key,
    ) if visual_key else None
    if visual_key and visual_value:
        selected = next(
            (
                row for row in media
                if row.variant_axis_key == visual_key
                and row.variant_value_key == visual_value
            ),
            None,
        )
        if selected:
            return selected
    return next((row for row in media if row.variant_axis_key is None), None)


def _variant_value_key(
    configuration: Mapping[str, Any],
    attributes: Mapping[str, Any],
    axis_key: str,
) -> str | None:
    raw = attributes.get(axis_key)
    if raw is None:
        return None
    for axis in configuration.get("axes") or []:
        if not isinstance(axis, dict) or axis.get("key") != axis_key:
            continue
        for value in axis.get("values") or []:
            if isinstance(value, dict) and raw in {value.get("key"), value.get("label")}:
                return str(value.get("key"))
    return str(raw)


def _attach_action(item: PartnerCatalogRow) -> PartnerCatalogRow:
    if item.source == "draft" and item.draft_id:
        preview_url = url_for(
            "partners.product_draft_preview",
            draft_id=item.draft_id,
            view="storefront",
            variant=item.sku,
        )
        if item.status in {"draft", "incomplete", "ready", "changes"}:
            return replace(
                item,
                action_label="Editar",
                action_url=url_for("partners.product_draft", draft_id=item.draft_id),
                edit_url=url_for("partners.product_draft", draft_id=item.draft_id),
                preview_url=preview_url,
                delete_url=url_for(
                    "partners.delete_product_draft_route",
                    draft_id=item.draft_id,
                ),
                can_select=True,
            )
        return replace(
            item,
            action_label="Vista previa",
            action_url=preview_url,
            preview_url=preview_url,
        )
    if item.status == "active" and item.product_slug:
        public_url = url_for(
            "storefront.product_detail",
            product_slug=item.product_slug,
            variant=item.sku,
        )
        return replace(
            item,
            action_label="Ver publicación",
            action_url=public_url,
            public_url=public_url,
        )
    if (
        item.source == "offer"
        and item.status in {"draft", "incomplete", "ready", "changes"}
        and item.draft_id
    ):
        return replace(
            item,
            action_label="Editar",
            action_url=url_for("partners.product_draft", draft_id=item.draft_id),
            edit_url=url_for("partners.product_draft", draft_id=item.draft_id),
        )
    if item.source == "offer" and item.status in {"review", "rejected"} and item.draft_id:
        preview_url = url_for(
            "partners.product_draft_preview",
            draft_id=item.draft_id,
            view="storefront",
            variant=item.sku,
        )
        return replace(
            item,
            action_label="Vista previa",
            action_url=preview_url,
            preview_url=preview_url,
        )
    return item


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
