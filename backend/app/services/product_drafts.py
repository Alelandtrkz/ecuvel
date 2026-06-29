from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from werkzeug.datastructures import FileStorage, MultiDict

from app.catalog.product_templates import (
    ProductTemplate,
    ProductTemplateError,
    get_product_template,
    validate_attributes,
)
from app.models import ProductDraft, ProductDraftFile, SellerOffer
from app.models.enums import (
    ProductDraftFileKind,
    ProductDraftFileStatus,
    ProductDraftStatus,
)
from app.services.partner_product_categories import (
    PARTNER_PRODUCT_DRAFT_SESSION_KEY,
    PartnerProductCategoryValidationError,
    get_saved_category_selection,
    require_partner_catalog_store,
)
from app.services.private_storage import (
    InvalidPrivateFileError,
    PrivateFileTooLargeError,
    StagedPrivateFile,
    delete_private_file,
    private_file_path,
    promote_private_file,
    stage_private_upload,
)
from app.services.public_identifiers import assign_product_code_to_draft


PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY = "partner_current_product_draft_id"
_EDITABLE_STATUSES = {
    ProductDraftStatus.DRAFT,
    ProductDraftStatus.INCOMPLETE,
    ProductDraftStatus.READY_FOR_REVIEW,
    ProductDraftStatus.CHANGES_REQUESTED,
}
_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$")
_MIN_IMAGE_COUNT = 3
_MAX_IMAGE_COUNT = 6


class ProductDraftError(Exception):
    pass


class ProductDraftAccessError(ProductDraftError):
    pass


class ProductDraftStateError(ProductDraftError):
    pass


class ProductDraftValidationError(ProductDraftError):
    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = dict(errors or {})


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    key: str
    label: str
    complete: bool
    message: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class ProductDraftView:
    draft: ProductDraft
    template: ProductTemplate
    checklist: tuple[ChecklistItem, ...]
    image_files: tuple[ProductDraftFile, ...]
    document_files: tuple[ProductDraftFile, ...]


def create_or_reuse_draft_from_selection(
    session: Session,
    *,
    user_id: uuid.UUID,
    browser_session,
) -> ProductDraft:
    selection = get_saved_category_selection(
        session,
        user_id,
        browser_session.get(PARTNER_PRODUCT_DRAFT_SESSION_KEY),
    )
    try:
        get_product_template(selection.template_key)
    except ProductTemplateError as exc:
        raise ProductDraftValidationError(
            "La subcategoría seleccionada no tiene una plantilla disponible.",
            {"template_key": "Selecciona otra subcategoría."},
        ) from exc

    current_id = _parse_uuid(browser_session.get(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY))
    if current_id is not None:
        existing = session.get(ProductDraft, current_id)
        if (
            existing is not None
            and existing.store_id == selection.store.store_id
            and existing.created_by_user_id == user_id
            and existing.category_id == selection.category_id
            and existing.subcategory_id == selection.subcategory_id
            and existing.status in _EDITABLE_STATUSES
        ):
            assign_product_code_to_draft(session, existing)
            return existing

    draft = ProductDraft(
        store_id=selection.store.store_id,
        created_by_user_id=user_id,
        category_id=selection.category_id,
        subcategory_id=selection.subcategory_id,
        template_key=selection.template_key,
        status=ProductDraftStatus.DRAFT,
        condition="NEW",
    )
    session.add(draft)
    session.flush()
    assign_product_code_to_draft(session, draft)
    browser_session[PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY] = str(draft.id)
    browser_session.modified = True
    return draft


def get_product_draft_for_user(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    lock: bool = False,
) -> ProductDraft:
    store = require_partner_catalog_store(session, user_id)
    query = (
        select(ProductDraft)
        .options(selectinload(ProductDraft.files), selectinload(ProductDraft.category), selectinload(ProductDraft.subcategory))
        .where(ProductDraft.id == draft_id)
    )
    if lock:
        query = query.with_for_update()
    draft = session.scalar(query)
    if draft is None or draft.store_id != store.store_id or draft.created_by_user_id != user_id:
        raise ProductDraftAccessError("No encontramos ese borrador.")
    return draft


def build_product_draft_view(draft: ProductDraft) -> ProductDraftView:
    template = get_product_template(draft.template_key)
    active_files = [item for item in draft.files if item.status == ProductDraftFileStatus.ACTIVE]
    image_files = tuple(_sort_files(item for item in active_files if item.kind == ProductDraftFileKind.IMAGE))
    document_files = tuple(
        _sort_files(item for item in active_files if item.kind == ProductDraftFileKind.DOCUMENT)
    )
    return ProductDraftView(
        draft=draft,
        template=template,
        checklist=calculate_checklist(draft, template, image_files=image_files, document_files=document_files),
        image_files=image_files,
        document_files=document_files,
    )


def _sort_files(files) -> list[ProductDraftFile]:
    return sorted(files, key=lambda item: (item.position, item.created_at, item.id))


def _active_image_files(draft: ProductDraft) -> list[ProductDraftFile]:
    return _sort_files(
        item
        for item in draft.files
        if item.status == ProductDraftFileStatus.ACTIVE and item.kind == ProductDraftFileKind.IMAGE
    )


def _sync_image_positions(
    draft: ProductDraft,
    ordered_ids: Sequence[uuid.UUID] | None = None,
) -> tuple[ProductDraftFile, ...]:
    active_images = _active_image_files(draft)
    if ordered_ids is not None:
        normalized_ids = [_parse_uuid(item) for item in ordered_ids]
        if any(item is None for item in normalized_ids):
            raise ProductDraftValidationError(
                "El orden de las imágenes no es válido.",
                {"images": "El orden de las imágenes no es válido."},
            )
        image_by_id = {item.id: item for item in active_images}
        requested_ids = list(normalized_ids)
        if len(requested_ids) != len(active_images) or set(requested_ids) != set(image_by_id):
            raise ProductDraftValidationError(
                "El orden debe incluir exactamente las imágenes activas del borrador.",
                {"images": "Actualiza la página e inténtalo otra vez."},
            )
        active_images = [image_by_id[item_id] for item_id in requested_ids if item_id is not None]
    for position, item in enumerate(active_images):
        item.position = position
        item.is_cover = position == 0
    for item in draft.files:
        if item.kind == ProductDraftFileKind.IMAGE and item.status != ProductDraftFileStatus.ACTIVE:
            item.is_cover = False
    return tuple(active_images)


def save_product_draft(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    form: MultiDict,
    final: bool = False,
) -> ProductDraft:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id)
    _ensure_editable(draft)
    assign_product_code_to_draft(session, draft)
    template = get_product_template(draft.template_key)
    errors = _apply_form_to_draft(draft, template, form, final=final)
    errors.update(_validate_sku_uniqueness(session, draft))
    if final:
        errors.update(_validate_final_requirements(draft, template))
    if errors:
        draft.status = ProductDraftStatus.INCOMPLETE
        draft.completion_percentage = _completion_percentage(
            calculate_checklist(draft, template)
        )
        raise ProductDraftValidationError("Revisa la información del producto.", errors)
    checklist = calculate_checklist(draft, template)
    draft.completion_percentage = _completion_percentage(checklist)
    if draft.status == ProductDraftStatus.DRAFT and draft.completion_percentage > 0:
        draft.status = ProductDraftStatus.INCOMPLETE
    if final:
        draft.status = ProductDraftStatus.SUBMITTED
        draft.submitted_at = datetime.now(timezone.utc)
        draft.completion_percentage = 100
    elif draft.completion_percentage == 100:
        draft.status = ProductDraftStatus.READY_FOR_REVIEW
    return draft


def stage_product_draft_upload(
    uploaded_file: FileStorage,
    *,
    root: str,
    kind: ProductDraftFileKind,
    max_bytes: int,
) -> StagedPrivateFile:
    try:
        if kind == ProductDraftFileKind.IMAGE:
            return stage_private_upload(
                uploaded_file,
                root=root,
                max_bytes=max_bytes,
                allowed_extensions={"jpg", "jpeg", "png", "webp"},
                storage_prefix="images",
                require_image_decode=True,
            )
        return stage_private_upload(
            uploaded_file,
            root=root,
            max_bytes=max_bytes,
            allowed_extensions={"jpg", "jpeg", "png", "webp", "pdf"},
            storage_prefix="documents",
            require_image_decode=False,
        )
    except PrivateFileTooLargeError as exc:
        raise ProductDraftValidationError(
            "El archivo supera el tamaño permitido.",
            {"file": "El archivo supera el tamaño permitido."},
        ) from exc
    except InvalidPrivateFileError as exc:
        raise ProductDraftValidationError("Archivo inválido.", {"file": str(exc)}) from exc


def attach_product_draft_file(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    staged: StagedPrivateFile,
    kind: ProductDraftFileKind,
    document_type: str | None,
    root: str,
    max_images: int,
) -> ProductDraftFile:
    return attach_product_draft_files(
        session,
        user_id=user_id,
        draft_id=draft_id,
        staged_files=(staged,),
        kind=kind,
        document_type=document_type,
        root=root,
        max_images=max_images,
    )[0]


def attach_product_draft_files(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    staged_files: Sequence[StagedPrivateFile],
    kind: ProductDraftFileKind,
    document_type: str | None,
    root: str,
    max_images: int,
) -> tuple[ProductDraftFile, ...]:
    if not staged_files:
        raise ProductDraftValidationError("Selecciona al menos un archivo.", {"file": "Selecciona al menos un archivo."})
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    active = [
        item
        for item in draft.files
        if item.status == ProductDraftFileStatus.ACTIVE and item.kind == kind
    ]
    if kind == ProductDraftFileKind.IMAGE:
        remaining = max_images - len(active)
        if remaining <= 0 or len(staged_files) > remaining:
            message = (
                f"La galería admite hasta {max_images} imágenes. "
                f"Puedes agregar {max(remaining, 0)} más."
            )
            raise ProductDraftValidationError(message, {"images": message})
    position = max((item.position for item in active), default=-1) + 1
    file_records: list[ProductDraftFile] = []
    promoted_paths = []
    try:
        for staged in staged_files:
            file_record = ProductDraftFile(
                draft=draft,
                kind=kind,
                storage_key=staged.storage_key,
                original_filename=staged.original_filename,
                media_type=staged.media_type,
                size_bytes=staged.size_bytes,
                sha256=staged.sha256,
                width=staged.width,
                height=staged.height,
                position=position,
                is_cover=False,
                document_type=_clean_text(document_type, 80) if document_type else None,
            )
            session.add(file_record)
            file_records.append(file_record)
            position += 1
        session.flush()
        if kind == ProductDraftFileKind.IMAGE:
            _sync_image_positions(draft)
        for staged in staged_files:
            promoted_paths.append(promote_private_file(staged, root=root))
        return tuple(file_records)
    except Exception:
        for path in promoted_paths:
            delete_private_file(path)
        raise


def delete_product_draft_file(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    file_id: uuid.UUID,
    root: str,
) -> None:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    file_record = next((item for item in draft.files if item.id == file_id), None)
    if file_record is None:
        raise ProductDraftAccessError("No encontramos ese archivo.")
    file_record.status = ProductDraftFileStatus.DELETED
    file_record.is_cover = False
    delete_private_file(private_file_path(root, file_record.storage_key))
    if file_record.kind == ProductDraftFileKind.IMAGE:
        _sync_image_positions(draft)


def set_cover_image(session: Session, *, user_id: uuid.UUID, draft_id: uuid.UUID, file_id: uuid.UUID) -> None:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    active_images = _active_image_files(draft)
    if not any(item.id == file_id for item in active_images):
        raise ProductDraftAccessError("No encontramos esa imagen.")
    ordered_ids = [file_id, *(item.id for item in active_images if item.id != file_id)]
    _sync_image_positions(draft, ordered_ids)


def reorder_product_draft_images(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    ordered_image_ids: Sequence[uuid.UUID],
) -> None:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    _sync_image_positions(draft, ordered_image_ids)


def calculate_checklist(
    draft: ProductDraft,
    template: ProductTemplate,
    *,
    image_files: tuple[ProductDraftFile, ...] | None = None,
    document_files: tuple[ProductDraftFile, ...] | None = None,
) -> tuple[ChecklistItem, ...]:
    if image_files is None or document_files is None:
        active = [item for item in draft.files if item.status == ProductDraftFileStatus.ACTIVE]
        image_files = tuple(item for item in active if item.kind == ProductDraftFileKind.IMAGE)
        document_files = tuple(item for item in active if item.kind == ProductDraftFileKind.DOCUMENT)
    required_attrs = [item for item in template.fields if item.required]
    attrs_complete = all(not _is_empty(draft.attributes.get(item.key)) for item in required_attrs)
    price = _decimal_or_none(draft.pricing_data.get("price"))
    stock = _int_or_none(draft.inventory_data.get("stock_quantity"))
    required_docs_complete = all(
        any(item.document_type == doc for item in document_files)
        for doc in template.required_documents
    )
    return (
        ChecklistItem("category", "Categoría", True, "Categoría seleccionada."),
        ChecklistItem("title", "Título", bool(draft.title), "Agrega un título claro."),
        ChecklistItem("gallery", "Galería", _MIN_IMAGE_COUNT <= len(image_files) <= _MAX_IMAGE_COUNT, f"{len(image_files)}/{_MIN_IMAGE_COUNT} imágenes mínimas."),
        ChecklistItem("description", "Descripción", bool(draft.description and len(draft.description.strip()) >= 20), "Describe el producto con detalle."),
        ChecklistItem("attributes", "Características", attrs_complete, "Completa los campos obligatorios de la plantilla."),
        ChecklistItem("variants", "Variantes", _variants_complete(draft), "Configura variantes o usa la oferta única."),
        ChecklistItem("price", "Precio", price is not None and price > 0, "Define un precio mayor a cero."),
        ChecklistItem("stock", "Stock", stock is not None and stock >= 0, "Define stock inicial."),
        ChecklistItem("dimensions", "Dimensiones", bool(draft.dimensions_data.get("product_weight_kg")), "Agrega peso y dimensiones básicas."),
        ChecklistItem("documents", "Documentación", required_docs_complete, "Agrega documentos requeridos.", optional=not template.required_documents),
    )


def _apply_form_to_draft(draft: ProductDraft, template: ProductTemplate, form: MultiDict, *, final: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    draft.title = _clean_text(form.get("title"), 250)
    draft.brand = _clean_text(form.get("brand"), 120)
    draft.model_number = _clean_text(form.get("model_number"), 120)
    generated_code = draft.seller_sku
    submitted_sku = _clean_text(form.get("seller_sku"), 80)
    submitted_barcode = _clean_text(form.get("barcode"), 80)
    submitted_condition = _clean_text(form.get("condition"), 40)
    if submitted_sku and submitted_sku != generated_code:
        errors["seller_sku"] = "El código del producto es generado por ECUVEL y no se puede editar."
    if submitted_barcode and submitted_barcode != generated_code:
        errors["barcode"] = "El código de barras usa el código generado por ECUVEL."
    if submitted_condition and submitted_condition.upper() not in {"NEW", "NUEVO"}:
        errors["condition"] = "Todos los productos de ECUVEL deben registrarse como nuevos."
    draft.barcode = generated_code
    draft.condition = "NEW"
    draft.country_origin = _clean_text(form.get("country_origin"), 80)
    draft.description = _clean_text(form.get("description"), 5000)
    draft.highlights = _nonempty_list(form.getlist("highlights[]"))
    draft.package_contents = _package_contents(form)
    draft.warranty_data = {
        "type": _clean_text(form.get("warranty_type"), 80),
        "duration": _clean_text(form.get("warranty_duration"), 20),
        "unit": _clean_text(form.get("warranty_unit"), 20),
        "responsible": _clean_text(form.get("warranty_responsible"), 80),
        "conditions": _clean_text(form.get("warranty_conditions"), 500),
    }
    draft.attributes = _parse_attributes(form, template)
    draft.pricing_data = {
        "price": _clean_text(form.get("price"), 40),
        "compare_at_price": _clean_text(form.get("compare_at_price"), 40),
        "currency": "USD",
    }
    draft.inventory_data = {
        "stock_quantity": _clean_text(form.get("stock_quantity"), 20),
        "stock_minimum": _clean_text(form.get("stock_minimum"), 20),
        "max_per_buyer": _clean_text(form.get("max_per_buyer"), 20),
        "preparation_time_days": _clean_text(form.get("preparation_time_days"), 20),
        "availability_mode": _clean_text(form.get("availability_mode"), 40) or "immediate",
    }
    draft.dimensions_data = {
        key: _clean_text(form.get(key), 40)
        for key in (
            "product_length_cm",
            "product_width_cm",
            "product_height_cm",
            "product_weight_kg",
            "package_length_cm",
            "package_width_cm",
            "package_height_cm",
            "package_weight_kg",
            "package_count",
            "fragile",
            "special_orientation",
            "package_notes",
        )
    }
    draft.variants = _parse_variants(form, draft.seller_sku)
    if draft.title and len(draft.title) < 8:
        errors["title"] = "El título debe ser más descriptivo."
    if draft.seller_sku and not _SKU_RE.match(draft.seller_sku):
        errors["seller_sku"] = "El código del producto debe ser alfanumérico y puede incluir guiones."
    errors.update(validate_attributes(template, draft.attributes, final=final))
    return errors


def _validate_final_requirements(draft: ProductDraft, template: ProductTemplate) -> dict[str, str]:
    errors: dict[str, str] = {}
    images = [item for item in draft.files if item.kind == ProductDraftFileKind.IMAGE and item.status == ProductDraftFileStatus.ACTIVE]
    documents = [item for item in draft.files if item.kind == ProductDraftFileKind.DOCUMENT and item.status == ProductDraftFileStatus.ACTIVE]
    price = _decimal_or_none(draft.pricing_data.get("price"))
    stock = _int_or_none(draft.inventory_data.get("stock_quantity"))
    if not draft.title:
        errors["title"] = "El título es obligatorio."
    if not draft.seller_sku:
        errors["seller_sku"] = "El código del producto es obligatorio."
    if not draft.description or len(draft.description.strip()) < 20:
        errors["description"] = "La descripción debe tener al menos 20 caracteres."
    if len(images) < _MIN_IMAGE_COUNT:
        errors["images"] = f"Carga al menos {_MIN_IMAGE_COUNT} imágenes."
    elif len(images) > _MAX_IMAGE_COUNT:
        errors["images"] = f"La galería admite hasta {_MAX_IMAGE_COUNT} imágenes."
    if price is None or price <= 0:
        errors["price"] = "El precio debe ser mayor a cero."
    if stock is None or stock < 0:
        errors["stock_quantity"] = "El stock debe ser un entero no negativo."
    if not draft.dimensions_data.get("product_weight_kg"):
        errors["product_weight_kg"] = "El peso del producto es obligatorio."
    for required_doc in template.required_documents:
        if not any(item.document_type == required_doc for item in documents):
            errors[f"document.{required_doc}"] = "Este documento es obligatorio para la plantilla."
    if not _variants_complete(draft):
        errors["variants"] = "Completa las variantes o desactívalas."
    return errors


def _validate_sku_uniqueness(session: Session, draft: ProductDraft) -> dict[str, str]:
    if not draft.seller_sku:
        return {}
    with session.no_autoflush:
        other_draft = session.scalar(
            select(ProductDraft.id).where(
                ProductDraft.seller_sku == draft.seller_sku,
                ProductDraft.id != draft.id,
                ProductDraft.status != ProductDraftStatus.REJECTED,
            )
        )
        offer = session.scalar(
            select(SellerOffer.id).where(
                SellerOffer.seller_sku == draft.seller_sku,
            )
        )
    if other_draft or offer:
        return {"seller_sku": "Ya existe un producto o borrador con este código."}
    return {}


def _parse_attributes(form: MultiDict, template: ProductTemplate) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in template.fields:
        name = f"attributes[{item.key}]"
        if item.type == "boolean":
            values[item.key] = name in form
        elif item.type in {"multiselect", "chips"}:
            raw_values = form.getlist(name)
            if len(raw_values) == 1 and "," in raw_values[0]:
                raw_values = raw_values[0].split(",")
            values[item.key] = _nonempty_list(raw_values)
        else:
            values[item.key] = _clean_text(form.get(name), 1000)
    return values


def _parse_variants(form: MultiDict, product_code: str | None) -> list[dict[str, Any]]:
    if form.get("has_variants") != "1":
        return []
    rows: list[dict[str, Any]] = []
    names = form.getlist("variant_name[]")
    prices = form.getlist("variant_price[]")
    stocks = form.getlist("variant_stock[]")
    for index, name in enumerate(names):
        row = {
            "name": _clean_text(name, 120),
            "sku": f"{product_code}-V{index + 1:02d}" if product_code else None,
            "price": _clean_text(prices[index] if index < len(prices) else "", 40),
            "stock": _clean_text(stocks[index] if index < len(stocks) else "", 20),
        }
        if row["name"] or row["price"] or row["stock"]:
            rows.append(row)
    return rows


def _variants_complete(draft: ProductDraft) -> bool:
    if not draft.variants:
        return True
    seen: set[str] = set()
    for row in draft.variants:
        sku = row.get("sku")
        price = _decimal_or_none(row.get("price"))
        stock = _int_or_none(row.get("stock"))
        if not row.get("name") or not sku or sku in seen or price is None or price <= 0 or stock is None or stock < 0:
            return False
        seen.add(sku)
    return True


def _package_contents(form: MultiDict) -> list[dict[str, str]]:
    quantities = form.getlist("package_quantity[]")
    names = form.getlist("package_name[]")
    notes = form.getlist("package_note[]")
    rows: list[dict[str, str]] = []
    for index, name in enumerate(names):
        row = {
            "quantity": _clean_text(quantities[index] if index < len(quantities) else "", 20),
            "name": _clean_text(name, 120),
            "note": _clean_text(notes[index] if index < len(notes) else "", 200),
        }
        if any(row.values()):
            rows.append(row)
    return rows


def _ensure_editable(draft: ProductDraft) -> None:
    if draft.status not in _EDITABLE_STATUSES:
        raise ProductDraftStateError("Este borrador ya no se puede editar.")


def _completion_percentage(items: tuple[ChecklistItem, ...]) -> int:
    required = [item for item in items if not item.optional]
    if not required:
        return 0
    return round(100 * sum(1 for item in required if item.complete) / len(required))


def _clean_text(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


def _nonempty_list(values: list[str]) -> list[str]:
    return [item.strip()[:160] for item in values if item and item.strip()]


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _parse_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
