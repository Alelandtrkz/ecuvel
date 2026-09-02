from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session, selectinload
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
from app.services.marketplace_policy import (
    CommissionRuleMissingError,
    InvalidSellerPriceError,
    MINIMUM_PRICE_MESSAGE,
    ResolvedSellerCommission,
    resolve_marketplace_commission,
)
from app.services.offer_preparation import (
    OfferPreparationValidationError,
    normalize_preparation_time_days,
    preparation_time_from_inventory,
)
from app.services.product_variant_builder import (
    available_variant_axes,
    build_variant_state,
    family_variants_enabled,
    variant_rows_complete,
)


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
    available_variant_axes: tuple[dict[str, Any], ...]
    commission_policy: dict[str, Any]
    family_enabled: bool


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
        .options(
            selectinload(ProductDraft.files),
            selectinload(ProductDraft.store),
            selectinload(ProductDraft.category),
            selectinload(ProductDraft.subcategory),
            selectinload(ProductDraft.moderation_events),
            selectinload(ProductDraft.publication),
        )
        .where(ProductDraft.id == draft_id)
    )
    if lock:
        query = query.with_for_update()
    draft = session.scalar(query)
    if draft is None or draft.store_id != store.store_id:
        raise ProductDraftAccessError("No encontramos ese borrador.")
    return draft


def build_product_draft_view(draft: ProductDraft) -> ProductDraftView:
    template = get_product_template(draft.template_key)
    active_files = [item for item in draft.files if item.status == ProductDraftFileStatus.ACTIVE]
    image_files = tuple(_sort_files(item for item in active_files if item.kind == ProductDraftFileKind.IMAGE))
    configuration = draft.variant_configuration or {}
    if configuration.get("version", 1) >= 4 and configuration.get("enabled") is False:
        selected_media_key = configuration.get("single_media_value_key")
        if selected_media_key:
            image_files = tuple(
                item for item in image_files
                if item.variant_value_key in {None, selected_media_key}
            )
    document_files = tuple(
        _sort_files(item for item in active_files if item.kind == ProductDraftFileKind.DOCUMENT)
    )
    policy: dict[str, Any] = {
        "threshold": "3.00",
        "fixed_amount": "0.25",
        "minimum_price": "0.25",
        "minimum_price_message": MINIMUM_PRICE_MESSAGE,
        "rate_percent": None,
        "category_path": [draft.category.name, draft.subcategory.name],
        "available": False,
    }
    session = object_session(draft)
    if session is not None:
        try:
            resolved_policy = resolve_marketplace_commission(
                session, category_id=draft.subcategory_id, price="3.00"
            )
            policy.update({
                "rate_percent": str(resolved_policy.rate_percent),
                "category_path": list(resolved_policy.category_labels),
                "available": True,
            })
        except (InvalidSellerPriceError, CommissionRuleMissingError):
            pass
    return ProductDraftView(
        draft=draft,
        template=template,
        checklist=calculate_checklist(draft, template, image_files=image_files, document_files=document_files),
        image_files=image_files,
        document_files=document_files,
        available_variant_axes=available_variant_axes(template, draft.attributes),
        commission_policy=policy,
        family_enabled=family_variants_enabled(draft.variant_configuration),
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
    *,
    variant_axis_key: str | None = None,
    variant_value_key: str | None = None,
) -> tuple[ProductDraftFile, ...]:
    active_images = [
        item
        for item in _active_image_files(draft)
        if item.variant_axis_key == variant_axis_key
        and item.variant_value_key == variant_value_key
    ]
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
    previous_variant_configuration = dict(draft.variant_configuration or {})
    errors = _apply_form_to_draft(draft, template, form, final=final)
    _reconcile_variant_image_bindings(draft, previous_variant_configuration)
    if form.get("variant_convert_base") == "1":
        _assign_general_images_to_default_visual_value(draft)
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
        _finalize_variant_mode(draft)
        capture_submission_commission_snapshots(session, draft)
        draft.status = ProductDraftStatus.SUBMITTED
        draft.submitted_at = datetime.now(timezone.utc)
        draft.completion_percentage = 100
    elif draft.completion_percentage == 100:
        draft.status = ProductDraftStatus.READY_FOR_REVIEW
    return draft


def submit_saved_product_draft(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> ProductDraft:
    """Submit the persisted draft without reapplying an editor form payload."""
    draft = get_product_draft_for_user(
        session,
        user_id=user_id,
        draft_id=draft_id,
        lock=True,
    )
    _ensure_editable(draft)
    assign_product_code_to_draft(session, draft)
    template = get_product_template(draft.template_key)
    family_enabled = family_variants_enabled(draft.variant_configuration)
    persisted_variant_errors: dict[str, str] = {}
    if family_enabled:
        variant_form = _variant_form_from_saved_draft(draft)
        configuration, variants, persisted_variant_errors = build_variant_state(
            form=variant_form,
            template=template,
            attributes=draft.attributes,
            product_code=draft.seller_sku,
            existing_configuration=draft.variant_configuration,
            existing_variants=draft.variants,
            final=True,
        )
        draft.variant_configuration = configuration
        draft.variants = variants
        family_enabled = family_variants_enabled(configuration)
    variant_sources = _variant_source_fields(draft.variant_configuration) if family_enabled else set()

    errors: dict[str, str] = dict(persisted_variant_errors)
    if draft.title and len(draft.title) < 8:
        errors["title"] = "El título debe ser más descriptivo."
    if draft.seller_sku and not _SKU_RE.match(draft.seller_sku):
        errors["seller_sku"] = "El código del producto debe ser alfanumérico y puede incluir guiones."
    errors.update(
        validate_attributes(
            template,
            draft.attributes,
            final=True,
            excluded_keys=variant_sources,
        )
    )
    errors.update(_validate_sku_uniqueness(session, draft))
    errors.update(_validate_final_requirements(draft, template))
    if errors:
        draft.status = ProductDraftStatus.INCOMPLETE
        draft.completion_percentage = _completion_percentage(
            calculate_checklist(draft, template)
        )
        raise ProductDraftValidationError("Revisa la información del producto.", errors)

    _finalize_variant_mode(draft)
    capture_submission_commission_snapshots(session, draft)
    draft.status = ProductDraftStatus.SUBMITTED
    draft.submitted_at = datetime.now(timezone.utc)
    draft.completion_percentage = 100
    return draft


def _variant_form_from_saved_draft(draft: ProductDraft) -> MultiDict:
    configuration = dict(draft.variant_configuration or {})
    form = MultiDict([
        ("has_variants", "1"),
        ("variant_configuration", json.dumps(configuration)),
        ("variant_default_choice", str(configuration.get("default_variant_id") or "")),
    ])
    for row in draft.variants or []:
        option_values = dict(row.get("options") or {})
        attributes = dict(row.get("attributes") or {})
        swatches = dict(row.get("swatches") or {})
        option_payload = {
            key: {
                "key": value_key,
                "label": attributes.get(key) or value_key,
                "swatch": swatches.get(key),
            }
            for key, value_key in option_values.items()
        }
        form.add("variant_id[]", str(row.get("variant_id") or ""))
        form.add("variant_options[]", json.dumps(option_payload))
        form.add("variant_combination_key[]", str(row.get("combination_key") or ""))
        form.add("variant_price[]", str(row.get("price") or ""))
        form.add("variant_compare_at_price[]", str(row.get("compare_at_price") or ""))
        form.add("variant_stock[]", str(row.get("stock") if row.get("stock") is not None else ""))
        form.add("variant_enabled[]", "1" if row.get("enabled", True) else "0")
    return form


def draft_commission_presentations(
    session: Session,
    draft: ProductDraft,
) -> tuple[tuple[str, str, ResolvedSellerCommission], ...]:
    """Resolve current editor prices; never trust commission fields from forms."""

    if family_variants_enabled(draft.variant_configuration):
        source_rows = [row for row in (draft.variants or []) if row.get("enabled", True)]
    else:
        source_rows = [{
            "sku": draft.seller_sku,
            "name": draft.title or "Producto",
            "price": (draft.pricing_data or {}).get("price"),
        }]
    resolved: list[tuple[str, str, ResolvedSellerCommission]] = []
    for row in source_rows:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            raise ProductDraftValidationError(
                "Revisa la información del producto.",
                {"variants": "Cada presentación necesita un SKU estable."},
            )
        try:
            commission = resolve_marketplace_commission(
                session,
                category_id=draft.subcategory_id,
                price=str(row.get("price") or ""),
            )
        except (InvalidSellerPriceError, CommissionRuleMissingError) as exc:
            key = "price" if not family_variants_enabled(draft.variant_configuration) else f"variants.{sku}"
            raise ProductDraftValidationError(
                "Revisa la información comercial del producto.", {key: str(exc)}
            ) from exc
        resolved.append((sku, str(row.get("name") or draft.title or sku), commission))
    return tuple(resolved)


def capture_submission_commission_snapshots(
    session: Session,
    draft: ProductDraft,
) -> None:
    """Freeze server-resolved commission terms for one submission cycle."""
    captured_at = datetime.now(timezone.utc).isoformat()
    presentations = draft_commission_presentations(session, draft)
    if family_variants_enabled(draft.variant_configuration):
        configuration = dict(draft.variant_configuration or {})
        configuration["commission_snapshots"] = {
            sku: commission.as_snapshot(captured_at=captured_at)
            for sku, _name, commission in presentations
        }
        configuration["commission_captured_at"] = captured_at
        draft.variant_configuration = configuration
        pricing = dict(draft.pricing_data or {})
        pricing.pop("commission_snapshot", None)
        draft.pricing_data = pricing
        return
    pricing = dict(draft.pricing_data or {})
    pricing["commission_snapshot"] = presentations[0][2].as_snapshot(
        captured_at=captured_at
    )
    draft.pricing_data = pricing
    configuration = dict(draft.variant_configuration or {})
    configuration.pop("commission_snapshots", None)
    configuration.pop("commission_captured_at", None)
    draft.variant_configuration = configuration


def draft_commission_display_rows(
    session: Session,
    draft: ProductDraft,
) -> tuple[dict[str, Any], ...]:
    """Return seller/admin-safe commercial rows, frozen while under review."""

    frozen = draft.status in {
        ProductDraftStatus.SUBMITTED,
        ProductDraftStatus.APPROVED,
        ProductDraftStatus.REJECTED,
    }
    snapshots: dict[str, Any]
    if family_variants_enabled(draft.variant_configuration):
        snapshots = dict((draft.variant_configuration or {}).get("commission_snapshots") or {})
        names = {
            str(row.get("sku") or ""): str(row.get("name") or row.get("sku") or "Presentación")
            for row in (draft.variants or []) if row.get("enabled", True)
        }
    else:
        snapshots = {
            str(draft.seller_sku or ""): (draft.pricing_data or {}).get("commission_snapshot")
        }
        names = {str(draft.seller_sku or ""): draft.title or "Producto"}

    if frozen:
        rows = []
        for sku, snapshot in snapshots.items():
            if not isinstance(snapshot, Mapping):
                continue
            rows.append({
                "sku": sku,
                "name": names.get(sku, sku),
                "mode": snapshot.get("mode"),
                "price": _decimal_or_none(snapshot.get("price")),
                "rate_percent": snapshot.get("rate_percent"),
                "fixed_amount": _decimal_or_none(snapshot.get("fixed_amount")),
                "commission_amount": _decimal_or_none(snapshot.get("commission_amount")),
                "seller_net_amount": _decimal_or_none(snapshot.get("seller_net_amount")),
                "category_labels": list(snapshot.get("category_labels") or ()),
                "captured_at": snapshot.get("captured_at"),
                "frozen": True,
            })
        return tuple(rows)

    try:
        live = draft_commission_presentations(session, draft)
    except ProductDraftValidationError:
        return ()
    return tuple({
        "sku": sku,
        "name": name,
        "mode": commission.mode.value,
        "price": commission.price,
        "rate_percent": (
            str(commission.rate_percent) if commission.rate_percent is not None else None
        ),
        "fixed_amount": commission.fixed_amount,
        "commission_amount": commission.commission_amount,
        "seller_net_amount": commission.seller_net_amount,
        "category_labels": list(commission.category_labels),
        "captured_at": None,
        "frozen": False,
    } for sku, name, commission in live)


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
    variant_axis_key: str | None = None,
    variant_value_key: str | None = None,
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
        variant_axis_key=variant_axis_key,
        variant_value_key=variant_value_key,
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
    variant_axis_key: str | None = None,
    variant_value_key: str | None = None,
) -> tuple[ProductDraftFile, ...]:
    if not staged_files:
        raise ProductDraftValidationError("Selecciona al menos un archivo.", {"file": "Selecciona al menos un archivo."})
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    if kind == ProductDraftFileKind.IMAGE:
        _validate_image_binding(draft, variant_axis_key, variant_value_key)
    active = [
        item
        for item in draft.files
        if item.status == ProductDraftFileStatus.ACTIVE and item.kind == kind
        and (
            kind != ProductDraftFileKind.IMAGE
            or (
                item.variant_axis_key == variant_axis_key
                and item.variant_value_key == variant_value_key
            )
        )
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
                variant_axis_key=variant_axis_key if kind == ProductDraftFileKind.IMAGE else None,
                variant_value_key=variant_value_key if kind == ProductDraftFileKind.IMAGE else None,
            )
            session.add(file_record)
            file_records.append(file_record)
            position += 1
        session.flush()
        if kind == ProductDraftFileKind.IMAGE:
            _sync_image_positions(
                draft,
                variant_axis_key=variant_axis_key,
                variant_value_key=variant_value_key,
            )
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
        _sync_image_positions(
            draft,
            variant_axis_key=file_record.variant_axis_key,
            variant_value_key=file_record.variant_value_key,
        )


def delete_product_draft_color_media(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    variant_axis_key: str,
    variant_value_key: str,
    root: str,
) -> int:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    targets = [
        item for item in _active_image_files(draft)
        if item.variant_axis_key == variant_axis_key
        and item.variant_value_key == variant_value_key
    ]
    for item in targets:
        item.status = ProductDraftFileStatus.DELETED
        item.is_cover = False
        delete_private_file(private_file_path(root, item.storage_key))
    return len(targets)


def set_cover_image(session: Session, *, user_id: uuid.UUID, draft_id: uuid.UUID, file_id: uuid.UUID) -> None:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    active_images = _active_image_files(draft)
    target = next((item for item in active_images if item.id == file_id), None)
    if target is None:
        raise ProductDraftAccessError("No encontramos esa imagen.")
    group = [
        item for item in active_images
        if item.variant_axis_key == target.variant_axis_key
        and item.variant_value_key == target.variant_value_key
    ]
    ordered_ids = [file_id, *(item.id for item in group if item.id != file_id)]
    _sync_image_positions(
        draft,
        ordered_ids,
        variant_axis_key=target.variant_axis_key,
        variant_value_key=target.variant_value_key,
    )


def assign_product_draft_image(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    file_id: uuid.UUID,
    variant_axis_key: str,
    variant_value_key: str,
    max_images: int,
) -> None:
    draft = get_product_draft_for_user(
        session, user_id=user_id, draft_id=draft_id, lock=True
    )
    _ensure_editable(draft)
    target = next(
        (item for item in _active_image_files(draft) if item.id == file_id),
        None,
    )
    if target is None:
        raise ProductDraftAccessError("No encontramos esa imagen.")
    _validate_image_binding(draft, variant_axis_key, variant_value_key)
    destination = [
        item
        for item in _active_image_files(draft)
        if item.id != target.id
        and item.variant_axis_key == variant_axis_key
        and item.variant_value_key == variant_value_key
    ]
    if len(destination) >= max_images:
        raise ProductDraftValidationError(
            "La galería de ese color ya está completa.",
            {"images": f"Cada color admite hasta {max_images} imágenes."},
        )
    previous_axis = target.variant_axis_key
    previous_value = target.variant_value_key
    target.variant_axis_key = variant_axis_key
    target.variant_value_key = variant_value_key
    target.position = len(destination)
    target.is_cover = not destination
    _sync_image_positions(
        draft,
        variant_axis_key=previous_axis,
        variant_value_key=previous_value,
    )
    _sync_image_positions(
        draft,
        variant_axis_key=variant_axis_key,
        variant_value_key=variant_value_key,
    )


def reorder_product_draft_images(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    ordered_image_ids: Sequence[uuid.UUID],
) -> None:
    draft = get_product_draft_for_user(session, user_id=user_id, draft_id=draft_id, lock=True)
    _ensure_editable(draft)
    if not ordered_image_ids:
        raise ProductDraftValidationError("El orden de las imágenes no es válido.", {"images": "El orden de las imágenes no es válido."})
    first = next((item for item in _active_image_files(draft) if item.id == ordered_image_ids[0]), None)
    if first is None:
        raise ProductDraftAccessError("No encontramos esa imagen.")
    _sync_image_positions(
        draft,
        ordered_image_ids,
        variant_axis_key=first.variant_axis_key,
        variant_value_key=first.variant_value_key,
    )


def _validate_image_binding(
    draft: ProductDraft,
    variant_axis_key: str | None,
    variant_value_key: str | None,
) -> None:
    visual_key, visual_values = _visual_axis_and_values(draft.variant_configuration)
    if not visual_key:
        if variant_axis_key or variant_value_key:
            raise ProductDraftValidationError("La galería indicada no existe.", {"images": "La galería indicada no existe."})
        return
    if variant_axis_key != visual_key or variant_value_key not in visual_values:
        raise ProductDraftValidationError("Selecciona un color válido para las imágenes.", {"images": "Selecciona un color válido para las imágenes."})


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
    family_enabled = family_variants_enabled(draft.variant_configuration)
    variant_sources = _variant_source_fields(draft.variant_configuration) if family_enabled else set()
    required_attrs = [
        item for item in template.fields
        if item.required and item.key not in variant_sources
    ]
    attrs_complete = all(not _is_empty(draft.attributes.get(item.key)) for item in required_attrs)
    price = _decimal_or_none(draft.pricing_data.get("price"))
    stock = _int_or_none(draft.inventory_data.get("stock_quantity"))
    try:
        preparation_time = preparation_time_from_inventory(
            draft.inventory_data,
            required=False,
        )
    except OfferPreparationValidationError:
        preparation_time = None
    required_docs_complete = all(
        any(item.document_type == doc for item in document_files)
        for doc in template.required_documents
    )
    gallery_complete, gallery_message = _gallery_completion(draft, tuple(image_files))
    return (
        ChecklistItem("category", "Categoría", True, "Categoría seleccionada."),
        ChecklistItem("title", "Título", bool(draft.title), "Agrega un título claro."),
        ChecklistItem("gallery", "Galería", gallery_complete, gallery_message),
        ChecklistItem("description", "Descripción", bool(draft.description and len(draft.description.strip()) >= 20), "Describe el producto con detalle."),
        ChecklistItem("attributes", "Características", attrs_complete, "Completa los campos obligatorios de la plantilla."),
        ChecklistItem("variants", "Variantes", _variants_complete(draft), "Configura variantes o usa la oferta única."),
        ChecklistItem("price", "Precio", _variants_complete(draft) if family_enabled else price is not None and price > 0, "Define precios vÃ¡lidos."),
        ChecklistItem("stock", "Stock", _variants_complete(draft) if family_enabled else stock is not None and stock >= 0, "Define stock inicial."),
        ChecklistItem("preparation_time", "Preparación", preparation_time is not None, "Selecciona 1 o 2 días."),
        ChecklistItem("dimensions", "Dimensiones", bool(draft.dimensions_data.get("product_weight_kg")), "Agrega peso y dimensiones básicas."),
        ChecklistItem("documents", "Documentación", required_docs_complete, "Agrega documentos requeridos.", optional=not template.required_documents),
    )


def _apply_form_to_draft(draft: ProductDraft, template: ProductTemplate, form: MultiDict, *, final: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    previous_attributes = dict(draft.attributes or {})
    previous_pricing = dict(draft.pricing_data or {})
    previous_inventory = dict(draft.inventory_data or {})
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
    try:
        preparation_time_days = normalize_preparation_time_days(
            form.get("preparation_time_days"),
            required=final,
        )
    except OfferPreparationValidationError as exc:
        preparation_time_days = None
        errors["preparation_time_days"] = str(exc)
    draft.inventory_data = {
        "stock_quantity": _clean_text(form.get("stock_quantity"), 20),
        "stock_minimum": _clean_text(form.get("stock_minimum"), 20),
        "max_per_buyer": _clean_text(form.get("max_per_buyer"), 20),
        "preparation_time_days": preparation_time_days,
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
    _apply_product_weight(draft, form)
    variant_configuration, variants, variant_errors = build_variant_state(
        form=form,
        template=template,
        attributes=draft.attributes,
        product_code=draft.seller_sku,
        existing_configuration=draft.variant_configuration,
        existing_variants=draft.variants,
        final=final,
    )
    draft.variant_configuration = variant_configuration
    draft.variants = variants
    family_enabled = family_variants_enabled(variant_configuration)
    variant_sources = _variant_source_fields(variant_configuration) if family_enabled else set()
    if family_enabled:
        snapshot = dict(variant_configuration.get("source_snapshot") or {})
        if not snapshot:
            snapshot = {
                "attributes": {
                    key: (
                        previous_attributes.get(key)
                        if not _is_empty(previous_attributes.get(key))
                        else draft.attributes.get(key)
                    )
                    for key in variant_sources
                    if not _is_empty(previous_attributes.get(key))
                    or not _is_empty(draft.attributes.get(key))
                },
                "price": (
                    previous_pricing.get("price")
                    if not _is_empty(previous_pricing.get("price"))
                    else draft.pricing_data.get("price")
                ),
                "compare_at_price": (
                    previous_pricing.get("compare_at_price")
                    if not _is_empty(previous_pricing.get("compare_at_price"))
                    else draft.pricing_data.get("compare_at_price")
                ),
                "stock": (
                    previous_inventory.get("stock_quantity")
                    if not _is_empty(previous_inventory.get("stock_quantity"))
                    else draft.inventory_data.get("stock_quantity")
                ),
            }
            variant_configuration["source_snapshot"] = snapshot
        for source_field in variant_sources:
            draft.attributes[source_field] = None
        draft.pricing_data["price"] = None
        draft.pricing_data["compare_at_price"] = None
        draft.inventory_data["stock_quantity"] = None
    errors.update(variant_errors)
    if draft.title and len(draft.title) < 8:
        errors["title"] = "El título debe ser más descriptivo."
    if draft.seller_sku and not _SKU_RE.match(draft.seller_sku):
        errors["seller_sku"] = "El código del producto debe ser alfanumérico y puede incluir guiones."
    errors.update(
        validate_attributes(
            template,
            draft.attributes,
            final=final,
            excluded_keys=variant_sources,
        )
    )
    return errors


def _validate_final_requirements(draft: ProductDraft, template: ProductTemplate) -> dict[str, str]:
    errors: dict[str, str] = {}
    images = [item for item in draft.files if item.kind == ProductDraftFileKind.IMAGE and item.status == ProductDraftFileStatus.ACTIVE]
    if (draft.variant_configuration or {}).get("version", 1) >= 4 and (draft.variant_configuration or {}).get("enabled") is False:
        selected_media_key = (draft.variant_configuration or {}).get("single_media_value_key")
        if selected_media_key:
            images = [item for item in images if item.variant_value_key in {None, selected_media_key}]
    documents = [item for item in draft.files if item.kind == ProductDraftFileKind.DOCUMENT and item.status == ProductDraftFileStatus.ACTIVE]
    price = _decimal_or_none(draft.pricing_data.get("price"))
    stock = _int_or_none(draft.inventory_data.get("stock_quantity"))
    if not draft.title:
        errors["title"] = "El título es obligatorio."
    if not draft.seller_sku:
        errors["seller_sku"] = "El código del producto es obligatorio."
    if not draft.description or len(draft.description.strip()) < 20:
        errors["description"] = "La descripción debe tener al menos 20 caracteres."
    gallery_complete, gallery_message = _gallery_completion(draft, tuple(images))
    if not gallery_complete:
        errors["images"] = gallery_message
    family_enabled = family_variants_enabled(draft.variant_configuration)
    if not family_enabled and (price is None or price <= Decimal("0.25")):
        errors["price"] = MINIMUM_PRICE_MESSAGE
    if not family_enabled and (stock is None or stock < 0):
        errors["stock_quantity"] = "El stock debe ser un entero no negativo."
    try:
        preparation_time_days = preparation_time_from_inventory(
            draft.inventory_data,
            required=True,
        )
    except OfferPreparationValidationError as exc:
        errors["preparation_time_days"] = str(exc)
    else:
        inventory_data = dict(draft.inventory_data or {})
        inventory_data["preparation_time_days"] = preparation_time_days
        draft.inventory_data = inventory_data
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
    if not family_variants_enabled(draft.variant_configuration):
        return True
    if not draft.variants:
        return not draft.variant_configuration
    return variant_rows_complete(draft.variants)


def _variant_source_fields(configuration: Mapping[str, Any] | None) -> set[str]:
    return {
        str(axis.get("source_field"))
        for axis in (configuration or {}).get("axes") or []
        if isinstance(axis, dict) and axis.get("source_field")
    }


def _finalize_variant_mode(draft: ProductDraft) -> None:
    """Discard draft-only recovery state once the seller submits a final mode."""
    configuration = dict(draft.variant_configuration or {})
    if not configuration:
        return

    if family_variants_enabled(configuration):
        configuration.pop("source_snapshot", None)
        configuration.pop("single_media_value_key", None)
        configuration["archived_family"] = False
        draft.variant_configuration = configuration
        return

    selected_media_key = configuration.get("single_media_value_key")
    for item in _active_image_files(draft):
        if item.variant_value_key not in {None, selected_media_key}:
            item.status = ProductDraftFileStatus.DELETED
            item.is_cover = False
            continue
        item.variant_axis_key = None
        item.variant_value_key = None
    _sync_group_positions(_active_image_files(draft))

    draft.variants = []
    draft.variant_configuration = {
        "version": 4,
        "enabled": False,
        "mode": "single",
        "axes": [],
        "visual_axis_key": None,
        "default_variant_id": None,
        "default_combination_key": None,
        "next_sku_sequence": configuration.get("next_sku_sequence", 1),
        "archived_family": False,
    }


def _visual_axis_and_values(configuration: Mapping[str, Any] | None) -> tuple[str | None, set[str]]:
    configuration = configuration or {}
    if not family_variants_enabled(configuration):
        return None, set()
    visual_key = configuration.get("visual_axis_key")
    if not visual_key:
        return None, set()
    for axis in configuration.get("axes") or []:
        if isinstance(axis, dict) and axis.get("key") == visual_key:
            return str(visual_key), {
                str(value.get("key"))
                for value in axis.get("values") or []
                if isinstance(value, dict) and value.get("key")
            }
    return None, set()


def _reconcile_variant_image_bindings(
    draft: ProductDraft,
    previous_configuration: Mapping[str, Any] | None,
) -> None:
    if draft.variant_configuration and not family_variants_enabled(draft.variant_configuration):
        return
    previous_visual, _previous_values = _visual_axis_and_values(previous_configuration)
    visual_key, visual_values = _visual_axis_and_values(draft.variant_configuration)
    active_images = _active_image_files(draft)
    if not visual_key:
        for item in active_images:
            item.variant_axis_key = None
            item.variant_value_key = None
        _sync_group_positions(active_images)
        return

    for item in active_images:
        if (
            item.variant_axis_key != visual_key
            or item.variant_value_key not in visual_values
        ):
            item.variant_axis_key = None
            item.variant_value_key = None

    _sync_group_positions(active_images)


def _assign_general_images_to_default_visual_value(draft: ProductDraft) -> None:
    visual_key, visual_values = _visual_axis_and_values(draft.variant_configuration)
    default_id = (draft.variant_configuration or {}).get("default_variant_id")
    default_variant = next(
        (row for row in (draft.variants or []) if row.get("variant_id") == default_id),
        None,
    )
    if not visual_key or not default_variant:
        return
    raw_value = (default_variant.get("options") or {}).get(visual_key)
    value_key = str(raw_value or "")
    if value_key not in visual_values:
        return
    for item in _active_image_files(draft):
        if item.variant_value_key is None:
            item.variant_axis_key = visual_key
            item.variant_value_key = value_key
    _sync_group_positions(_active_image_files(draft))


def _sync_group_positions(images: Sequence[ProductDraftFile]) -> None:
    groups: dict[tuple[str | None, str | None], list[ProductDraftFile]] = {}
    for item in images:
        groups.setdefault((item.variant_axis_key, item.variant_value_key), []).append(item)
    for group in groups.values():
        for position, item in enumerate(sorted(group, key=lambda image: (image.position, image.created_at, image.id))):
            item.position = position
            item.is_cover = position == 0


def _gallery_completion(
    draft: ProductDraft,
    images: tuple[ProductDraftFile, ...],
) -> tuple[bool, str]:
    visual_key, visual_values = _visual_axis_and_values(draft.variant_configuration)
    if not visual_key:
        complete = _MIN_IMAGE_COUNT <= len(images) <= _MAX_IMAGE_COUNT
        return complete, f"{len(images)}/{_MIN_IMAGE_COUNT} imágenes mínimas."
    if len(images) < _MIN_IMAGE_COUNT:
        return False, f"Carga al menos {_MIN_IMAGE_COUNT} imágenes en total."
    unassigned = [item for item in images if item.variant_value_key is None]
    if unassigned:
        return False, f"Asigna {len(unassigned)} imagen(es) a un color."
    for value_key in visual_values:
        count = sum(
            1
            for item in images
            if item.variant_axis_key == visual_key and item.variant_value_key == value_key
        )
        if count < 1:
            return False, "Cada color necesita al menos una imagen."
        if count > _MAX_IMAGE_COUNT:
            return False, f"Cada color admite hasta {_MAX_IMAGE_COUNT} imágenes."
    return True, f"{len(images)} imágenes distribuidas por color."


def _ensure_editable(draft: ProductDraft) -> None:
    if draft.status not in _EDITABLE_STATUSES:
        raise ProductDraftStateError("Este borrador ya no se puede editar.")


def _completion_percentage(items: tuple[ChecklistItem, ...]) -> int:
    required = [item for item in items if not item.optional]
    if not required:
        return 0
    return round(100 * sum(1 for item in required if item.complete) / len(required))


def _apply_product_weight(draft: ProductDraft, form: MultiDict) -> None:
    # El vendedor escribe el peso en g o kg; se guarda tal cual para mostrarlo
    # igual al recargar, y product_weight_kg queda siempre en kg (canónico).
    weight_value = _clean_text(form.get("product_weight_value"), 40)
    weight_unit = _clean_text(form.get("product_weight_unit"), 10)
    if weight_unit not in ("g", "kg"):
        weight_unit = "kg"
    if weight_value is None:
        if form.get("product_weight_value") is not None:
            draft.dimensions_data["product_weight_kg"] = None
            draft.dimensions_data["product_weight_value"] = None
            draft.dimensions_data["product_weight_unit"] = weight_unit
        return
    draft.dimensions_data["product_weight_value"] = weight_value
    draft.dimensions_data["product_weight_unit"] = weight_unit
    number = _decimal_or_none(weight_value)
    if number is None:
        draft.dimensions_data["product_weight_kg"] = weight_value
        return
    if weight_unit == "g":
        number = number / Decimal("1000")
    draft.dimensions_data["product_weight_kg"] = str(number)


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
