from __future__ import annotations

import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Category,
    Product,
    ProductDraft,
    ProductDraftModerationEvent,
    ProductDraftPublication,
    ProductMedia,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.models.enums import OfferStatus, ProductDraftStatus, StoreStatus
from app.services.inventory import InventoryServiceError, initialize_offer_inventory
from app.services.marketplace_policy import (
    CommissionRuleMissingError,
    StoreInventoryLocationMissingError,
    resolve_default_store_inventory_location,
    resolve_marketplace_commission,
)
from app.services.private_storage import private_file_path, verify_private_file
from app.services.product_drafts import build_product_draft_view
from app.services.product_variant_builder import publication_payload_from_draft


MODERATION_CHECKS = (
    "images", "identity", "description", "specifications",
    "variants", "category", "documentation",
)
MODERATION_REASONS = {
    "IMAGES_LOW_QUALITY": "Imágenes de baja calidad",
    "INCORRECT_INFORMATION": "Información incorrecta",
    "MISLEADING_TITLE": "Título engañoso",
    "INCOMPLETE_DESCRIPTION": "Descripción incompleta",
    "INCORRECT_SPECIFICATIONS": "Especificaciones incorrectas",
    "VARIANT_INCONSISTENCY": "Variantes inconsistentes",
    "PROHIBITED_PRODUCT": "Producto no permitido",
    "OTHER": "Otro",
}


class ProductModerationError(Exception):
    pass


class ProductModerationStateError(ProductModerationError):
    pass


class ProductModerationValidationError(ProductModerationError):
    pass


@dataclass(frozen=True, slots=True)
class PublicationResult:
    product: Product
    mapping: ProductDraftPublication
    copied_files: tuple[Path, ...]
    already_published: bool = False


def normalize_moderation_checklist(values: list[str] | tuple[str, ...]) -> dict[str, bool]:
    checked = set(values)
    return {key: key in checked for key in MODERATION_CHECKS}


def validate_moderation_reason(reason_code: str | None, note: str | None) -> tuple[str, str | None]:
    normalized_reason = str(reason_code or "").strip().upper()
    normalized_note = " ".join(str(note or "").strip().split()) or None
    if normalized_reason not in MODERATION_REASONS:
        raise ProductModerationValidationError("Selecciona un motivo válido.")
    if normalized_reason == "OTHER" and not normalized_note:
        raise ProductModerationValidationError("Describe el motivo seleccionado.")
    if normalized_note and len(normalized_note) > 2000:
        raise ProductModerationValidationError("La observación no puede superar 2000 caracteres.")
    return normalized_reason, normalized_note


def get_locked_submitted_draft(session: Session, draft_id: uuid.UUID) -> ProductDraft:
    draft = session.scalar(
        select(ProductDraft)
        .options(
            selectinload(ProductDraft.files),
            selectinload(ProductDraft.store),
            selectinload(ProductDraft.category),
            selectinload(ProductDraft.subcategory),
            selectinload(ProductDraft.publication),
        )
        .where(ProductDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if draft is None:
        raise ProductModerationValidationError("No encontramos la publicación.")
    return draft


def record_moderation_decision(
    session: Session,
    *,
    draft_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    decision: str,
    checklist: dict[str, bool],
    reason_code: str | None = None,
    note: str | None = None,
) -> ProductDraft:
    draft = get_locked_submitted_draft(session, draft_id)
    if draft.status != ProductDraftStatus.SUBMITTED:
        raise ProductModerationStateError("La publicación ya no está en revisión.")
    if decision not in {"CHANGES_REQUESTED", "REJECTED"}:
        raise ProductModerationValidationError("La decisión no es válida.")
    reason_code, note = validate_moderation_reason(reason_code, note)
    draft.status = (
        ProductDraftStatus.CHANGES_REQUESTED
        if decision == "CHANGES_REQUESTED"
        else ProductDraftStatus.REJECTED
    )
    session.add(ProductDraftModerationEvent(
        draft_id=draft.id,
        decision=decision,
        reason_code=reason_code,
        note=note,
        checklist_snapshot=checklist,
        actor_user_id=actor_user_id,
    ))
    return draft


def _slug_fragment(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = "-".join("".join(char.lower() if char.isalnum() else " " for char in ascii_value).split())
    return slug[:220] or "producto"


def _unique_slug(session: Session, draft: ProductDraft) -> str:
    base = _slug_fragment(draft.title or "producto")
    candidate = base
    if session.scalar(select(Product.id).where(Product.slug == candidate)) is not None:
        candidate = f"{base}-{str(draft.id).split('-')[0]}"
    return candidate[:280]


def _decimal(value, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductModerationValidationError(f"{field} no es válido.") from exc
    if not parsed.is_finite():
        raise ProductModerationValidationError(f"{field} no es válido.")
    return parsed


def _integer(value, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ProductModerationValidationError(f"{field} no es válido.") from exc
    if parsed < 0:
        raise ProductModerationValidationError(f"{field} no puede ser negativo.")
    return parsed


def _millimeters(value) -> int | None:
    if value in {None, ""}:
        return None
    return int((_decimal(value, "La dimensión") * 10).quantize(Decimal("1")))


def _grams(value) -> int | None:
    if value in {None, ""}:
        return None
    return int((_decimal(value, "El peso") * 1000).quantize(Decimal("1")))


def _family_enabled(draft: ProductDraft) -> bool:
    configuration = draft.variant_configuration or {}
    return (
        configuration.get("version", 1) < 4
        or (
            configuration.get("enabled") is True
            and configuration.get("mode") == "family"
        )
    )


def _variant_rows(draft: ProductDraft) -> list[dict]:
    if _family_enabled(draft):
        return [dict(row) for row in draft.variants or [] if row.get("enabled", True)]
    return [{
        "variant_id": str(draft.id),
        "combination_key": "simple",
        "name": None,
        "sku": draft.seller_sku,
        "attributes": {},
        "options": {},
        "price": (draft.pricing_data or {}).get("price"),
        "compare_at_price": (draft.pricing_data or {}).get("compare_at_price"),
        "stock": (draft.inventory_data or {}).get("stock_quantity"),
    }]


def _shared_attributes(draft: ProductDraft) -> dict:
    values = dict(draft.attributes or {})
    values["condition"] = draft.condition or "NEW"
    values["country_origin"] = draft.country_origin
    values["package_contents"] = list(draft.package_contents or [])
    values["highlights"] = list(draft.highlights or [])
    values["warranty"] = dict(draft.warranty_data or {})
    return {
        key: value for key, value in values.items()
        if value is not None and value != ""
    }


def _copy_catalog_media(
    *,
    draft: ProductDraft,
    product: Product,
    source_root: str | Path,
    destination_root: str | Path,
    family_enabled: bool,
    publication_media: list[dict],
) -> tuple[list[ProductMedia], list[Path]]:
    view = build_product_draft_view(draft)
    allowed_storage_keys = {str(item["storage_key"]) for item in publication_media}
    media_rows: list[ProductMedia] = []
    copied: list[Path] = []
    extension_by_type = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    for source in view.image_files:
        if source.storage_key not in allowed_storage_keys:
            continue
        source_path = verify_private_file(
            root=source_root,
            storage_key=source.storage_key,
            size_bytes=source.size_bytes,
            sha256=source.sha256,
        )
        public_id = uuid.uuid4().hex
        extension = extension_by_type.get(source.media_type)
        if extension is None:
            raise ProductModerationValidationError("Una imagen tiene un formato no publicable.")
        storage_key = f"products/{product.id}/{public_id}.{extension}"
        destination = private_file_path(destination_root, storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ProductModerationValidationError("No se pudo reservar una clave pública única.")
        shutil.copy2(source_path, destination)
        copied.append(destination)
        media_rows.append(ProductMedia(
            product_id=product.id,
            public_id=public_id,
            storage_key=storage_key,
            media_type=source.media_type,
            size_bytes=source.size_bytes,
            width=source.width,
            height=source.height,
            position=source.position,
            is_cover=source.is_cover,
            variant_axis_key=source.variant_axis_key if family_enabled else None,
            variant_value_key=source.variant_value_key if family_enabled else None,
            is_active=True,
        ))
    return media_rows, copied


def publish_product_draft(
    session: Session,
    *,
    draft_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    checklist: dict[str, bool],
    source_media_root: str | Path,
    catalog_media_root: str | Path,
) -> PublicationResult:
    draft = get_locked_submitted_draft(session, draft_id)
    if draft.publication is not None:
        return PublicationResult(
            product=draft.publication.product,
            mapping=draft.publication,
            copied_files=(),
            already_published=True,
        )
    if draft.status != ProductDraftStatus.SUBMITTED:
        raise ProductModerationStateError("La publicación ya no está en revisión.")
    required_manual_checks = tuple(
        key for key in MODERATION_CHECKS
        if key != "documentation" or bool(draft.template_key)
    )
    # The actual document requirement comes from the selected template.
    preview_view = build_product_draft_view(draft)
    if not preview_view.template.required_documents:
        required_manual_checks = tuple(
            key for key in required_manual_checks if key != "documentation"
        )
    if not all(checklist.get(key) is True for key in required_manual_checks):
        raise ProductModerationValidationError("Completa toda la lista de control antes de aprobar.")

    view = preview_view
    pending = [item.label for item in view.checklist if not item.optional and not item.complete]
    if pending:
        raise ProductModerationValidationError(
            "El borrador tiene pendientes: " + ", ".join(pending)
        )
    store = session.get(Store, draft.store_id)
    category = session.get(Category, draft.subcategory_id)
    if store is None or store.status != StoreStatus.ACTIVE or not store.is_verified:
        raise ProductModerationValidationError("La tienda debe estar activa y verificada.")
    if category is None or not category.is_active or not draft.category.is_active:
        raise ProductModerationValidationError("La categoría seleccionada no está activa.")

    try:
        commission = resolve_marketplace_commission(
            session, store_id=draft.store_id, category_id=draft.subcategory_id,
        )
    except CommissionRuleMissingError as exc:
        raise ProductModerationValidationError(str(exc)) from exc
    try:
        inventory_location = resolve_default_store_inventory_location(
            session, store_id=draft.store_id,
        )
    except StoreInventoryLocationMissingError as exc:
        raise ProductModerationValidationError(str(exc)) from exc
    publication_payload = publication_payload_from_draft(draft)
    rows = (
        [dict(row) for row in publication_payload["variants"]]
        if _family_enabled(draft)
        else _variant_rows(draft)
    )
    if not rows:
        raise ProductModerationValidationError("La familia no tiene variantes activas.")

    family_enabled = _family_enabled(draft)
    product_data = publication_payload["product"]
    product = Product(
        id=uuid.uuid4(),
        category_id=draft.subcategory_id,
        title=product_data.get("title") or "Producto sin título",
        slug=_unique_slug(session, draft),
        brand=product_data.get("brand"),
        model_number=product_data.get("model_number"),
        description=product_data.get("description"),
        variant_configuration=dict(product_data.get("variant_configuration") or {}),
        is_active=True,
    )
    session.add(product)
    copied: list[Path] = []
    try:
        media_rows, copied = _copy_catalog_media(
            draft=draft,
            product=product,
            source_root=source_media_root,
            destination_root=catalog_media_root,
            family_enabled=family_enabled,
            publication_media=publication_payload["media"],
        )
        session.add_all(media_rows)
        dimensions = draft.dimensions_data or {}
        shared = _shared_attributes(draft)
        for row in rows:
            sku = str(row.get("sku") or "").strip()
            if not sku:
                raise ProductModerationValidationError("Una presentación no tiene SKU.")
            price = _decimal(row.get("price"), "El precio")
            stock = _integer(row.get("stock"), "El stock")
            if price <= 0:
                raise ProductModerationValidationError("El precio debe ser mayor a cero.")
            compare_raw = row.get("compare_at_price")
            compare_price = _decimal(compare_raw, "El precio anterior") if compare_raw not in {None, ""} else None
            if compare_price is not None and compare_price <= price:
                raise ProductModerationValidationError("El precio anterior debe superar al precio actual.")
            attributes = dict(shared)
            attributes.update(dict(row.get("attributes") or {}))
            attributes["variant_options"] = dict(row.get("options") or {})
            variant = ProductVariant(
                id=uuid.uuid4(),
                product_id=product.id,
                catalog_sku=sku,
                title=row.get("name") if family_enabled else None,
                manufacturer_barcode=sku,
                attributes=attributes,
                combination_key=str(row.get("combination_key") or "simple"),
                weight_grams=_grams(dimensions.get("product_weight_kg")),
                length_mm=_millimeters(dimensions.get("product_length_cm")),
                width_mm=_millimeters(dimensions.get("product_width_cm")),
                height_mm=_millimeters(dimensions.get("product_height_cm")),
                is_active=True,
            )
            offer = SellerOffer(
                id=uuid.uuid4(),
                store_id=store.id,
                variant_id=variant.id,
                seller_sku=sku,
                currency="USD",
                price=price,
                compare_at_price=compare_price,
                commission_rate=commission.rate,
                status=OfferStatus.ACTIVE,
            )
            session.add_all((variant, offer))
            session.flush()
            try:
                initialize_offer_inventory(
                    session,
                    offer=offer,
                    location=inventory_location,
                    quantity=stock,
                    actor_user_id=actor_user_id,
                    reference_id=draft.id,
                )
            except InventoryServiceError as exc:
                raise ProductModerationValidationError(str(exc)) from exc

        mapping = ProductDraftPublication(
            draft_id=draft.id,
            product_id=product.id,
            published_by_user_id=actor_user_id,
        )
        draft.status = ProductDraftStatus.APPROVED
        session.add_all((
            mapping,
            ProductDraftModerationEvent(
                draft_id=draft.id,
                decision="APPROVED",
                reason_code=None,
                note=None,
                checklist_snapshot={
                    **checklist,
                    "commission_rule_id": str(commission.rule_id),
                    "commission_scope": commission.scope,
                    "commission_rate": str(commission.rate),
                    "inventory_location_id": str(inventory_location.id),
                },
                actor_user_id=actor_user_id,
            ),
        ))
        session.flush()
        return PublicationResult(product=product, mapping=mapping, copied_files=tuple(copied))
    except Exception:
        for path in copied:
            path.unlink(missing_ok=True)
        raise


def remove_copied_publication_files(paths: tuple[Path, ...] | list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
