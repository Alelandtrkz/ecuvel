from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import ProductDraft, ProductDraftFile, ProductDraftModerationEvent, Store
from app.models.enums import ProductDraftFileKind, ProductDraftFileStatus, ProductDraftStatus
from app.services.product_draft_preview import (
    DraftPreviewContext,
    build_product_draft_preview,
)
from app.services.product_drafts import (
    ProductDraftView,
    build_product_draft_view,
    draft_commission_display_rows,
)
from app.services.product_publication import MODERATION_CHECKS, MODERATION_REASONS
from app.services.product_variant_builder import family_variants_enabled


STATUS_FILTERS = {
    "review": ProductDraftStatus.SUBMITTED,
    "approved": ProductDraftStatus.APPROVED,
    "changes": ProductDraftStatus.CHANGES_REQUESTED,
    "rejected": ProductDraftStatus.REJECTED,
}
STATUS_LABELS = {
    ProductDraftStatus.SUBMITTED: "En revisión",
    ProductDraftStatus.APPROVED: "Aprobado",
    ProductDraftStatus.CHANGES_REQUESTED: "Correcciones solicitadas",
    ProductDraftStatus.REJECTED: "Rechazado",
}
MANUAL_CHECK_LABELS = {
    "images": "Imágenes adecuadas y relacionadas con el producto",
    "identity": "Título, marca y modelo coherentes",
    "description": "Descripción clara y no engañosa",
    "specifications": "Especificaciones coherentes y suficientes",
    "variants": "Variantes, colores, SKU, precios y stock coherentes",
    "category": "Categoría y subcategoría correctas",
    "documentation": "Documentación requerida correcta",
}


@dataclass(frozen=True, slots=True)
class AdminProductRow:
    draft: ProductDraft
    cover: ProductDraftFile | None
    price_label: str
    active_variants: int
    status_label: str


@dataclass(frozen=True, slots=True)
class AdminProductPage:
    rows: tuple[AdminProductRow, ...]
    selected: AdminProductRow | None
    selected_view: ProductDraftView | None
    selected_preview: DraftPreviewContext | None
    selected_commissions: tuple[dict, ...]
    selected_commission_snapshot_complete: bool
    status_key: str
    query: str
    page: int
    pages: int
    total: int
    counts: dict[str, int]
    manual_checks: tuple[tuple[str, str], ...]
    reasons: dict[str, str]


def _decimal(value) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _price_label(draft: ProductDraft) -> str:
    if family_variants_enabled(draft.variant_configuration):
        prices = [
            _decimal(row.get("price"))
            for row in draft.variants or []
            if row.get("enabled", True)
        ]
    else:
        prices = [_decimal((draft.pricing_data or {}).get("price"))]
    prices = [value for value in prices if value is not None]
    if not prices:
        return "Pendiente"
    minimum, maximum = min(prices), max(prices)
    if minimum == maximum:
        return f"${minimum:,.2f}"
    return f"${minimum:,.2f} – ${maximum:,.2f}"


def _cover(draft: ProductDraft) -> ProductDraftFile | None:
    files = [
        item for item in draft.files
        if item.status == ProductDraftFileStatus.ACTIVE
        and item.kind == ProductDraftFileKind.IMAGE
    ]
    return min(
        files,
        key=lambda item: (not item.is_cover, item.position, item.created_at),
        default=None,
    )


def _row(draft: ProductDraft) -> AdminProductRow:
    active_variants = (
        sum(1 for item in draft.variants or [] if item.get("enabled", True))
        if family_variants_enabled(draft.variant_configuration) else 1
    )
    return AdminProductRow(
        draft=draft,
        cover=_cover(draft),
        price_label=_price_label(draft),
        active_variants=active_variants,
        status_label=STATUS_LABELS.get(draft.status, draft.status.value),
    )


def commission_snapshot_complete(
    draft: ProductDraft,
    commission_rows: tuple[dict, ...],
) -> bool:
    if family_variants_enabled(draft.variant_configuration):
        expected = {
            str(item.get("sku") or "").strip()
            for item in draft.variants or []
            if item.get("enabled", True)
        }
    else:
        expected = {str(draft.seller_sku or "").strip()}
    actual = {str(item.get("sku") or "").strip() for item in commission_rows}
    return bool(expected) and "" not in expected and actual == expected


def _options():
    return (
        selectinload(ProductDraft.files),
        selectinload(ProductDraft.store),
        selectinload(ProductDraft.category),
        selectinload(ProductDraft.subcategory),
        selectinload(ProductDraft.moderation_events).selectinload(
            ProductDraftModerationEvent.actor
        ),
        selectinload(ProductDraft.publication),
    )


def get_admin_product_draft(session: Session, draft_id: uuid.UUID) -> ProductDraft | None:
    return session.scalar(
        select(ProductDraft).options(*_options()).where(
            ProductDraft.id == draft_id,
            ProductDraft.status.in_(tuple(STATUS_FILTERS.values())),
        )
    )


def get_admin_products_page(
    session: Session,
    *,
    status_key: str,
    query: str,
    page: int,
    selected_id: uuid.UUID | None,
    per_page: int = 20,
) -> AdminProductPage:
    status_key = status_key if status_key in STATUS_FILTERS else "review"
    normalized_query = " ".join((query or "").strip().split())[:160]
    page = max(1, page)
    count_rows = session.execute(
        select(ProductDraft.status, func.count(ProductDraft.id))
        .where(ProductDraft.status.in_(tuple(STATUS_FILTERS.values())))
        .group_by(ProductDraft.status)
    ).all()
    raw_counts = dict(count_rows)
    counts = {key: int(raw_counts.get(status, 0)) for key, status in STATUS_FILTERS.items()}

    conditions = [ProductDraft.status == STATUS_FILTERS[status_key]]
    if normalized_query:
        term = f"%{normalized_query}%"
        conditions.append(or_(
            ProductDraft.title.ilike(term),
            ProductDraft.seller_sku.ilike(term),
            Store.name.ilike(term),
        ))
    base = select(ProductDraft).join(Store, Store.id == ProductDraft.store_id).where(*conditions)
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    drafts = session.scalars(
        base.options(*_options())
        .order_by(ProductDraft.submitted_at.desc().nullslast(), ProductDraft.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    rows = tuple(_row(draft) for draft in drafts)
    selected_draft = None
    if selected_id is not None:
        selected_draft = get_admin_product_draft(session, selected_id)
        if (
            selected_draft is not None
            and selected_draft.status != STATUS_FILTERS[status_key]
        ):
            selected_draft = None
    if selected_draft is None and rows:
        selected_draft = rows[0].draft
    selected_row = _row(selected_draft) if selected_draft is not None else None
    selected_view = build_product_draft_view(selected_draft) if selected_draft else None
    selected_preview = (
        build_product_draft_preview(
            selected_view,
            selected_view="summary",
            media_endpoint="admin.product_file",
        )
        if selected_view else None
    )
    selected_commissions = (
        draft_commission_display_rows(session, selected_draft)
        if selected_draft is not None else ()
    )
    return AdminProductPage(
        rows=rows,
        selected=selected_row,
        selected_view=selected_view,
        selected_preview=selected_preview,
        selected_commissions=selected_commissions,
        selected_commission_snapshot_complete=(
            commission_snapshot_complete(selected_draft, selected_commissions)
            if selected_draft is not None else False
        ),
        status_key=status_key,
        query=normalized_query,
        page=page,
        pages=pages,
        total=total,
        counts=counts,
        manual_checks=tuple((key, MANUAL_CHECK_LABELS[key]) for key in MODERATION_CHECKS),
        reasons=MODERATION_REASONS,
    )
