from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from flask import url_for

from app.models import ProductDraft, ProductDraftFile
from app.services.cart import CART_LOW_STOCK_THRESHOLD, MAX_CART_QUANTITY
from app.services.product_drafts import ChecklistItem, ProductDraftView
from app.services.product_variant_builder import family_variants_enabled


@dataclass(frozen=True, slots=True)
class DraftPreviewImage:
    url: str
    thumbnail_url: str
    alt: str
    width: int | None
    height: int | None
    is_primary: bool


@dataclass(frozen=True, slots=True)
class DraftPreviewSpecification:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class DraftPreviewProduct:
    offer_id: str
    product_id: uuid.UUID
    public_identifier: str
    name: str
    base_name: str
    description: str | None
    category_name: str
    category_url: str
    store_name: str
    store_url: str | None
    store_is_verified: bool
    store_rating: Decimal | None
    store_review_count: int
    current_price: Decimal | None
    compare_at_price: Decimal | None
    currency: str
    seller_sku: str
    catalog_sku: str
    variant_name: str | None
    gallery_images: tuple[DraftPreviewImage, ...]
    gallery_placeholder_url: str
    specifications: tuple[DraftPreviewSpecification, ...]
    highlights: tuple[DraftPreviewSpecification, ...]
    rating: Decimal | None
    review_count: int
    availability_label: str
    is_available: bool
    available_quantity: int
    max_quantity: int
    quantity_limit_reached: bool
    low_stock: bool
    availability_message: str
    is_favorite: bool
    variant_payload: dict[str, Any]
    price_pending: bool
    stock_pending: bool


@dataclass(frozen=True, slots=True)
class DraftPreviewDashboard:
    family_enabled: bool
    active_variants: int
    inactive_variants: int
    sold_out_variants: int
    stock_total: int
    price_min: Decimal | None
    price_max: Decimal | None
    image_count: int
    incomplete_galleries: tuple[str, ...]
    pending_items: tuple[ChecklistItem, ...]
    can_submit: bool


@dataclass(frozen=True, slots=True)
class DraftPreviewContext:
    product: DraftPreviewProduct
    dashboard: DraftPreviewDashboard
    selected_view: str


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _availability(stock: int | None) -> tuple[int, bool, str, str, bool]:
    if stock is None:
        return 0, False, "Stock pendiente", "Define el stock antes de publicar.", True
    quantity = max(0, stock)
    maximum = min(MAX_CART_QUANTITY, quantity)
    if quantity == 0:
        return 0, False, "Producto agotado", "Producto agotado.", False
    low_stock = quantity <= CART_LOW_STOCK_THRESHOLD
    message = (
        f"Solo quedan {quantity} unidades disponibles."
        if low_stock
        else f"{quantity} unidades disponibles."
    )
    return maximum, low_stock, "Disponible para ordenar", message, False


def _selected_variant(
    rows: list[dict[str, Any]],
    configuration: Mapping[str, Any],
    requested_sku: str | None,
) -> dict[str, Any] | None:
    if not rows:
        return None
    requested = str(requested_sku or "").strip()
    if requested:
        selected = next((row for row in rows if row.get("sku") == requested), None)
        if selected is not None:
            return selected
    default_id = configuration.get("default_variant_id")
    principal = next((row for row in rows if row.get("variant_id") == default_id), None)
    if principal is not None and (_integer(principal.get("stock")) or 0) > 0:
        return principal
    available = next((row for row in rows if (_integer(row.get("stock")) or 0) > 0), None)
    return available or principal or rows[0]


def _image_url(
    draft: ProductDraft,
    file: ProductDraftFile,
    *,
    media_endpoint: str,
) -> str:
    return url_for(
        media_endpoint,
        draft_id=draft.id,
        file_id=file.id,
    )


def _gallery_images(
    draft: ProductDraft,
    files: Sequence[ProductDraftFile],
    *,
    product_name: str,
    media_endpoint: str,
) -> tuple[DraftPreviewImage, ...]:
    ordered = sorted(
        files,
        key=lambda item: (not item.is_cover, item.position, item.created_at, item.id),
    )
    return tuple(
        DraftPreviewImage(
            url=_image_url(draft, item, media_endpoint=media_endpoint),
            thumbnail_url=_image_url(draft, item, media_endpoint=media_endpoint),
            alt=f"{product_name}, vista {index}",
            width=item.width,
            height=item.height,
            is_primary=index == 1,
        )
        for index, item in enumerate(ordered, start=1)
    )


def _files_for_variant(
    view: ProductDraftView,
    selected: Mapping[str, Any] | None,
) -> tuple[ProductDraftFile, ...]:
    configuration = view.draft.variant_configuration or {}
    visual_key = configuration.get("visual_axis_key")
    if not visual_key:
        return tuple(item for item in view.image_files if item.variant_axis_key is None)
    if selected is None:
        return ()
    value_key = (selected.get("options") or {}).get(visual_key)
    return tuple(
        item for item in view.image_files
        if item.variant_axis_key == visual_key and item.variant_value_key == value_key
    )


def _display_value(value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip()) or None
    return str(value)


def _technical_rows(
    view: ProductDraftView,
    selected: Mapping[str, Any] | None,
) -> tuple[DraftPreviewSpecification, ...]:
    draft = view.draft
    values = dict(draft.attributes or {})
    if selected:
        values.update(selected.get("attributes") or {})
    rows: list[DraftPreviewSpecification] = []

    def add(label: str, value: Any, unit: str | None = None) -> None:
        displayed = _display_value(value)
        if displayed is not None:
            rows.append(DraftPreviewSpecification(label, f"{displayed} {unit}" if unit else displayed))

    add("Categoría", draft.category.name)
    add("Marca", draft.brand)
    add("Modelo", draft.model_number)
    add("Variante", selected.get("name") if selected else None)
    add("SKU", selected.get("sku") if selected else draft.seller_sku)
    add("Código de barras", draft.barcode)
    add("País de origen", draft.country_origin)
    for field in view.template.fields:
        add(field.label, values.get(field.key), field.unit)
    dimensions = draft.dimensions_data or {}
    add("Peso del producto", dimensions.get("product_weight_kg"), "kg")
    add("Largo del producto", dimensions.get("product_length_cm"), "cm")
    add("Ancho del producto", dimensions.get("product_width_cm"), "cm")
    add("Alto del producto", dimensions.get("product_height_cm"), "cm")
    warranty = draft.warranty_data or {}
    add("Garantía", warranty.get("type"))
    duration = warranty.get("duration")
    if duration:
        add("Duración de garantía", duration, warranty.get("unit"))
    return tuple(rows)


def _highlight_rows(
    view: ProductDraftView,
    selected: Mapping[str, Any] | None,
) -> tuple[DraftPreviewSpecification, ...]:
    specifications = _technical_rows(view, selected)
    excluded = {"SKU", "Código de barras", "País de origen", "Peso del producto"}
    return tuple(item for item in specifications if item.label not in excluded)[:6]


def _variant_payload(
    view: ProductDraftView,
    rows: list[dict[str, Any]],
    selected: Mapping[str, Any] | None,
    *,
    base_title: str,
    media_endpoint: str,
) -> dict[str, Any]:
    variants: list[dict[str, Any]] = []
    for row in rows:
        stock = _integer(row.get("stock"))
        maximum, low_stock, label, message, stock_pending = _availability(stock)
        images = _gallery_images(
            view.draft,
            _files_for_variant(view, row),
            media_endpoint=media_endpoint,
            product_name=f"{base_title} — {row.get('name') or 'Variante'}",
        )
        variants.append({
            "catalog_sku": row.get("sku") or row.get("variant_id") or "",
            "combination_key": row.get("combination_key") or "",
            "attributes": dict(row.get("attributes") or {}),
            "name": row.get("name") or "",
            "seller_sku": row.get("sku") or "SKU pendiente",
            "offer_id": "",
            "currency": "USD",
            "price": str(row.get("price") or "") or None,
            "compare_at_price": str(row.get("compare_at_price") or "") or None,
            "available_quantity": max(0, stock or 0),
            "max_quantity": maximum,
            "is_available": stock is not None and stock > 0,
            "low_stock": low_stock,
            "availability_label": label,
            "availability_message": message,
            "stock_pending": stock_pending,
            "images": [image.url for image in images],
        })
    return {
        "base_title": base_title,
        "axes": list((view.draft.variant_configuration or {}).get("axes") or []),
        "visual_axis_key": (view.draft.variant_configuration or {}).get("visual_axis_key"),
        "selected_catalog_sku": selected.get("sku") if selected else None,
        "variants": variants,
        "preview_mode": True,
    }


def _incomplete_galleries(view: ProductDraftView, rows: list[dict[str, Any]]) -> tuple[str, ...]:
    configuration = view.draft.variant_configuration or {}
    visual_key = configuration.get("visual_axis_key")
    if not visual_key:
        return ("Galería general",) if len(view.image_files) < 3 else ()
    values: dict[str, str] = {}
    for row in rows:
        key = (row.get("options") or {}).get(visual_key)
        label = (row.get("attributes") or {}).get(visual_key) or key
        if key:
            values[str(key)] = str(label)
    missing = []
    for key, label in values.items():
        count = sum(1 for item in view.image_files if item.variant_value_key == key)
        if count < 1:
            missing.append(str(label))
    return tuple(missing)


def build_product_draft_preview(
    view: ProductDraftView,
    *,
    requested_sku: str | None = None,
    selected_view: str | None = None,
    media_endpoint: str = "partners.product_draft_file",
) -> DraftPreviewContext:
    draft = view.draft
    configuration = draft.variant_configuration or {}
    family_enabled = family_variants_enabled(draft.variant_configuration)
    active_rows = [dict(row) for row in (draft.variants or []) if row.get("enabled", True)] if family_enabled else []
    inactive_count = sum(1 for row in (draft.variants or []) if not row.get("enabled", True)) if family_enabled else 0
    selected = _selected_variant(active_rows, configuration, requested_sku) if family_enabled else None

    base_title = draft.title or "Producto sin título"
    variant_name = str(selected.get("name") or "") if selected else None
    full_title = f"{base_title} — {variant_name}" if variant_name else base_title
    price = _decimal(selected.get("price")) if selected else _decimal((draft.pricing_data or {}).get("price"))
    compare_at_price = _decimal(selected.get("compare_at_price")) if selected else _decimal((draft.pricing_data or {}).get("compare_at_price"))
    stock = _integer(selected.get("stock")) if selected else _integer((draft.inventory_data or {}).get("stock_quantity"))
    maximum, low_stock, availability_label, availability_message, stock_pending = _availability(stock)
    # build_product_draft_view already limits a restored simple draft to the
    # gallery selected during conversion. Those files can still carry their
    # archived color binding until final submission, so keep the complete view.
    selected_files = _files_for_variant(view, selected) if family_enabled else view.image_files
    images = _gallery_images(
        draft, selected_files, product_name=full_title,
        media_endpoint=media_endpoint,
    )
    payload = _variant_payload(
        view, active_rows, selected, base_title=base_title,
        media_endpoint=media_endpoint,
    ) if family_enabled else {
        "base_title": base_title,
        "axes": [],
        "visual_axis_key": None,
        "selected_catalog_sku": draft.seller_sku,
        "variants": [],
        "preview_mode": True,
    }
    sku = str(selected.get("sku") or "SKU pendiente") if selected else (draft.seller_sku or "SKU pendiente")
    product = DraftPreviewProduct(
        offer_id="",
        product_id=draft.id,
        public_identifier=str(draft.id),
        name=full_title,
        base_name=base_title,
        description=draft.description,
        category_name=draft.subcategory.name,
        category_url="#",
        store_name=draft.store.name,
        store_url=None,
        store_is_verified=bool(draft.store.is_verified),
        store_rating=None,
        store_review_count=0,
        current_price=price,
        compare_at_price=compare_at_price,
        currency="USD",
        seller_sku=sku,
        catalog_sku=sku,
        variant_name=variant_name,
        gallery_images=images,
        gallery_placeholder_url=url_for("static", filename="images/placeholders/product-placeholder.svg"),
        specifications=_technical_rows(view, selected),
        highlights=_highlight_rows(view, selected),
        rating=None,
        review_count=0,
        availability_label=availability_label,
        is_available=stock is not None and stock > 0,
        available_quantity=max(0, stock or 0),
        max_quantity=maximum,
        quantity_limit_reached=maximum == 1,
        low_stock=low_stock,
        availability_message=availability_message,
        is_favorite=False,
        variant_payload=payload,
        price_pending=price is None,
        stock_pending=stock_pending,
    )

    stocks = [_integer(row.get("stock")) for row in active_rows]
    prices = [_decimal(row.get("price")) for row in active_rows]
    if not family_enabled:
        stocks = [stock]
        prices = [price]
    valid_prices = [item for item in prices if item is not None and item > 0]
    pending_items = tuple(item for item in view.checklist if not item.optional and not item.complete)
    dashboard = DraftPreviewDashboard(
        family_enabled=family_enabled,
        active_variants=len(active_rows),
        inactive_variants=inactive_count,
        sold_out_variants=sum(1 for item in stocks if item == 0),
        stock_total=sum(max(0, item or 0) for item in stocks),
        price_min=min(valid_prices) if valid_prices else None,
        price_max=max(valid_prices) if valid_prices else None,
        image_count=len(view.image_files),
        incomplete_galleries=_incomplete_galleries(view, active_rows),
        pending_items=pending_items,
        can_submit=not pending_items,
    )
    return DraftPreviewContext(
        product=product,
        dashboard=dashboard,
        selected_view="storefront" if selected_view == "storefront" else "summary",
    )
