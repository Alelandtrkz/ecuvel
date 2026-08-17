from __future__ import annotations

import json
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from werkzeug.datastructures import MultiDict

from app.catalog.product_templates import (
    ProductTemplate,
    ProductTemplateField,
    VariantAxis,
    variant_axes_for_product_type,
)
from app.services.marketplace_policy import MINIMUM_PRICE_MESSAGE, MINIMUM_SELLER_PRICE


VARIANT_CONFIGURATION_VERSION = 4
MAX_VARIANT_AXES = 3
MAX_VALUES_PER_AXIS = 12
MAX_VARIANT_COMBINATIONS = 50
_VALUE_KEY_RE = re.compile(r"[^a-z0-9]+")
_SKU_SUFFIX_RE = re.compile(r"-V(\d+)$")
_SWATCH_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_VARIANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_COLOR_SWATCHES = {
    "negro": "#111827",
    "blanco": "#F8FAFC",
    "azul": "#2563EB",
    "verde": "#16A34A",
    "morado": "#7C3AED",
    "rojo": "#DC2626",
    "naranja": "#F97316",
    "gris": "#9CA3AF",
    "plata": "#CBD5E1",
    "dorado": "#D4AF37",
}


def family_variants_enabled(configuration: Mapping[str, Any] | None) -> bool:
    """Return whether a draft configuration represents a sellable family.

    Empty configurations are simple products. Legacy configurations before V4
    represented families implicitly; V4 requires both the explicit flag and
    family mode.
    """
    normalized = configuration or {}
    if not normalized:
        return False
    if normalized.get("version", 1) < VARIANT_CONFIGURATION_VERSION:
        return True
    return normalized.get("enabled") is True and normalized.get("mode") == "family"


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return _VALUE_KEY_RE.sub("-", ascii_value).strip("-")[:80] or "valor"


def _field_by_key(template: ProductTemplate, key: str) -> ProductTemplateField | None:
    return next((field for field in template.fields if field.key == key), None)


def _axis_public_data(axis: VariantAxis, *, is_default: bool) -> dict[str, Any]:
    return {
        "key": axis.key,
        "label": axis.label,
        "unit": axis.unit,
        "suggestions": list(axis.suggestions),
        "value_type": axis.value_type,
        "source_field": axis.source_field,
        "is_visual": axis.is_visual,
        "is_default": is_default,
        "default_for": list(axis.default_for),
        "allowed_product_types": list((axis.condition or {}).get("values", ())),
    }


def available_variant_axes(
    template: ProductTemplate,
    attributes: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    product_type = str((attributes or {}).get("tipo_producto") or "")
    return tuple(
        _axis_public_data(axis, is_default=product_type in axis.default_for)
        for axis in template.variant_axes
    )


def _normalize_value(
    *,
    axis: VariantAxis,
    field: ProductTemplateField,
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    label = str(raw.get("label") or raw.get("value") or "").strip()[:160]
    if not label:
        return None, f"Selecciona un valor para {axis.label}."

    if axis.value_type == "integer":
        try:
            number = int(label)
        except (TypeError, ValueError):
            return None, f"{axis.label} debe usar números enteros."
        if field.min is not None and number < field.min:
            return None, f"{axis.label} debe ser mayor o igual a {field.min}."
        if field.max is not None and number > field.max:
            return None, f"{axis.label} debe ser menor o igual a {field.max}."
        label = str(number)
    elif axis.value_type == "decimal":
        try:
            number = Decimal(label)
        except (InvalidOperation, TypeError):
            return None, f"{axis.label} debe usar un número válido."
        if not number.is_finite():
            return None, f"{axis.label} debe usar un número válido."
        if field.min is not None and number < field.min:
            return None, f"{axis.label} debe ser mayor o igual a {field.min}."
        if field.max is not None and number > field.max:
            return None, f"{axis.label} debe ser menor o igual a {field.max}."
        label = format(number.normalize(), "f")
    elif axis.value_type == "select" and field.options and label not in field.options:
        return None, f"{label} no es un valor permitido para {axis.label}."

    value_key = _slug(label)
    swatch = str(raw.get("swatch") or "").strip()
    if axis.is_visual:
        if swatch and not _SWATCH_RE.fullmatch(swatch):
            return None, f"El color visual de {label} no es válido."
        swatch = swatch.upper() if swatch else _COLOR_SWATCHES.get(value_key, "")
    else:
        swatch = ""
    return {"key": value_key, "label": label, "swatch": swatch or None}, None


def _configuration_from_form(form: MultiDict) -> tuple[dict[str, Any] | None, str | None]:
    raw = form.get("variant_configuration")
    if not raw:
        return None, "Configura los campos de las variantes."
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "La configuración de variantes no es válida."
    if not isinstance(parsed, dict):
        return None, "La configuración de variantes no es válida."
    return parsed, None


def _combination_key(axes: list[dict[str, Any]], values: list[dict[str, Any]]) -> str:
    return "|".join(
        f"{axis['key']}={value['key']}"
        for axis, value in zip(axes, values, strict=True)
    )


def _combination_name(axes: list[dict[str, Any]], values: list[dict[str, Any]]) -> str:
    return " / ".join(
        f"{value['label']} {axis['unit']}" if axis.get("unit") else value["label"]
        for axis, value in zip(axes, values, strict=True)
    )


def _legacy_name_key(value: Any) -> str:
    return "|".join(_slug(part) for part in str(value or "").split("/") if part.strip())


def _submitted_rows(form: MultiDict) -> list[dict[str, Any]]:
    columns = {
        "variant_id": form.getlist("variant_id[]"),
        "options": form.getlist("variant_options[]"),
        "previous_combination_key": form.getlist("variant_combination_key[]"),
        "price": form.getlist("variant_price[]"),
        "compare_at_price": form.getlist("variant_compare_at_price[]"),
        "stock": form.getlist("variant_stock[]"),
        "enabled": form.getlist("variant_enabled[]"),
    }
    row_count = max((len(column) for column in columns.values()), default=0)
    return [
        {
            key: (
                (column[index] == "1")
                if key == "enabled" and index < len(column)
                else (column[index] if index < len(column) else (False if key == "enabled" else ""))
            )
            for key, column in columns.items()
        }
        for index in range(row_count)
    ]


def _next_sequence(
    existing_configuration: Mapping[str, Any],
    existing_variants: list[dict[str, Any]],
) -> int:
    configured = existing_configuration.get("next_sku_sequence")
    if isinstance(configured, int) and configured > 0:
        return configured
    highest = 0
    for row in existing_variants:
        match = _SKU_SUFFIX_RE.search(str(row.get("sku") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def build_variant_state(
    *,
    form: MultiDict,
    template: ProductTemplate,
    attributes: Mapping[str, Any],
    product_code: str | None,
    existing_configuration: Mapping[str, Any] | None,
    existing_variants: list[dict[str, Any]] | None,
    final: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    if form.get("has_variants") != "1":
        if not existing_configuration and not existing_variants:
            return {}, [], {}
        submitted_configuration, _submitted_error = _configuration_from_form(form)
        previous = dict(submitted_configuration or existing_configuration or {})
        archived_variants = list(existing_variants or [])
        default_id = previous.get("default_variant_id")
        default_variant = next(
            (row for row in archived_variants if row.get("variant_id") == default_id),
            archived_variants[0] if archived_variants else None,
        )
        visual_key = previous.get("visual_axis_key")
        single_media_value_key = (
            (default_variant.get("options") or {}).get(visual_key)
            if default_variant and visual_key else None
        )
        return {
            "version": VARIANT_CONFIGURATION_VERSION,
            "enabled": False,
            "mode": "single",
            "axes": list(previous.get("axes") or []),
            "visual_axis_key": previous.get("visual_axis_key"),
            "default_variant_id": previous.get("default_variant_id"),
            "default_combination_key": previous.get("default_combination_key"),
            "next_sku_sequence": _next_sequence(previous, list(existing_variants or [])),
            "source_snapshot": dict(previous.get("source_snapshot") or {}),
            "archived_family": True,
            "single_media_value_key": single_media_value_key,
        }, archived_variants, {}

    raw_configuration, configuration_error = _configuration_from_form(form)
    if configuration_error:
        return {}, [], {"variants": configuration_error}
    assert raw_configuration is not None
    errors: dict[str, str] = {}

    allowed = {
        axis.key: axis
        for axis in variant_axes_for_product_type(
            template, str(attributes.get("tipo_producto") or "")
        )
    }
    raw_axes = raw_configuration.get("axes")
    if not isinstance(raw_axes, list) or not raw_axes:
        return {}, [], {"variants": "Selecciona al menos un campo de variante."}
    if len(raw_axes) > MAX_VARIANT_AXES:
        return {}, [], {
            "variants": f"Puedes seleccionar hasta {MAX_VARIANT_AXES} campos de variante."
        }

    axes: list[dict[str, Any]] = []
    seen_axes: set[str] = set()
    for raw_axis in raw_axes:
        if not isinstance(raw_axis, dict):
            errors["variants"] = "La configuración contiene un campo inválido."
            continue
        axis_key = str(raw_axis.get("key") or "")
        axis = allowed.get(axis_key)
        if axis is None:
            errors["variants"] = "La configuración contiene un campo no permitido para este producto."
            continue
        if axis_key in seen_axes:
            errors["variants"] = "No puedes repetir un campo de variante."
            continue
        if _field_by_key(template, axis.source_field) is None:
            errors["variants"] = "La plantilla de variantes está incompleta."
            continue
        seen_axes.add(axis_key)
        axes.append({
            "key": axis.key,
            "label": axis.label,
            "unit": axis.unit,
            "value_type": axis.value_type,
            "source_field": axis.source_field,
            "is_visual": axis.is_visual,
            "values": [],
        })
    if errors:
        return {}, [], errors

    submitted = _submitted_rows(form)
    if not submitted:
        visual_axis = next((axis for axis in axes if axis["is_visual"]), None)
        configuration = {
            "version": VARIANT_CONFIGURATION_VERSION,
            "enabled": True,
            "mode": "family",
            "axes": axes,
            "visual_axis_key": visual_axis["key"] if visual_axis else None,
            "default_variant_id": None,
            "default_combination_key": None,
            "next_sku_sequence": _next_sequence(
                dict(existing_configuration or {}), list(existing_variants or [])
            ),
            "source_snapshot": dict(
                raw_configuration.get("source_snapshot")
                or dict(existing_configuration or {}).get("source_snapshot")
                or {}
            ),
            "archived_family": False,
        }
        return configuration, [], (
            {"variants": "Agrega al menos una variante."} if final else {}
        )
    if len(submitted) > MAX_VARIANT_COMBINATIONS:
        return {}, [], {
            "variants": f"Puedes crear hasta {MAX_VARIANT_COMBINATIONS} variantes."
        }

    existing_configuration = dict(existing_configuration or {})
    existing_variants = list(existing_variants or [])
    existing_by_id = {
        str(row.get("variant_id")): row
        for row in existing_variants
        if row.get("variant_id")
    }
    existing_by_key = {
        str(row.get("combination_key")): row
        for row in existing_variants
        if row.get("combination_key")
    }
    legacy_by_name = {
        _legacy_name_key(row.get("name")): row
        for row in existing_variants
        if row.get("name")
    }
    next_sequence = _next_sequence(existing_configuration, existing_variants)
    seen_variant_ids: set[str] = set()
    seen_combinations: set[str] = set()
    axis_values: dict[str, dict[str, dict[str, Any]]] = {
        axis["key"]: {} for axis in axes
    }
    active_axis_values: dict[str, dict[str, dict[str, Any]]] = {
        axis["key"]: {} for axis in axes
    }
    variants: list[dict[str, Any]] = []

    for index, posted in enumerate(submitted):
        try:
            raw_options = json.loads(str(posted.get("options") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_options = None
        if not isinstance(raw_options, dict):
            errors[f"variants.{index}"] = "La variante contiene opciones inválidas."
            continue

        values: list[dict[str, Any]] = []
        row_invalid = False
        for axis_data in axes:
            axis = allowed[axis_data["key"]]
            field = _field_by_key(template, axis.source_field)
            assert field is not None
            raw_value = raw_options.get(axis.key)
            if not isinstance(raw_value, dict):
                raw_value = {"label": raw_value}
            normalized, value_error = _normalize_value(
                axis=axis, field=field, raw=raw_value
            )
            if value_error:
                errors[f"variants.{index}.{axis.key}"] = value_error
                row_invalid = True
                continue
            assert normalized is not None
            values.append(normalized)
        if row_invalid or len(values) != len(axes):
            continue

        combination_key = _combination_key(axes, values)
        name = _combination_name(axes, values)
        raw_variant_id = str(posted.get("variant_id") or "").strip()
        previous_key = str(posted.get("previous_combination_key") or "").strip()
        previous = (
            existing_by_id.get(raw_variant_id)
            or existing_by_key.get(previous_key)
            or existing_by_key.get(combination_key)
            or legacy_by_name.get(_legacy_name_key(name))
            or {}
        )
        variant_id = raw_variant_id or str(previous.get("variant_id") or "")
        if not variant_id:
            variant_id = f"v-{uuid.uuid4().hex}"
        if not _VARIANT_ID_RE.fullmatch(variant_id):
            errors[f"variants.{index}"] = "El identificador de la variante no es válido."
            continue
        if variant_id in seen_variant_ids:
            errors[f"variants.{index}"] = "No puedes repetir el identificador de una variante."
            continue
        if combination_key in seen_combinations:
            errors[f"variants.{index}"] = f"La variante {name} ya existe."
            continue
        seen_variant_ids.add(variant_id)
        seen_combinations.add(combination_key)
        enabled = bool(posted.get("enabled"))
        posted_price = str(posted.get("price", form.get("price") or "")).strip()
        if enabled and final:
            try:
                normalized_price = Decimal(posted_price)
            except (InvalidOperation, TypeError, ValueError):
                normalized_price = None
            if normalized_price is not None and normalized_price <= MINIMUM_SELLER_PRICE:
                errors[f"variants.{index}.price"] = MINIMUM_PRICE_MESSAGE
        for axis_data, value in zip(axes, values, strict=True):
            axis_values[axis_data["key"]].setdefault(value["key"], value)
            if enabled:
                active_axis_values[axis_data["key"]].setdefault(value["key"], value)

        sku = previous.get("sku")
        if not sku:
            sku = f"{product_code}-V{next_sequence:02d}" if product_code else None
            next_sequence += 1
        variants.append({
            "variant_id": variant_id,
            "combination_key": combination_key,
            "options": {
                axis["key"]: value["key"]
                for axis, value in zip(axes, values, strict=True)
            },
            "attributes": {
                axis["key"]: value["label"]
                for axis, value in zip(axes, values, strict=True)
            },
            "swatches": {
                axis["key"]: value.get("swatch")
                for axis, value in zip(axes, values, strict=True)
                if value.get("swatch")
            },
            "name": name,
            "sku": sku,
            "price": posted_price,
            "compare_at_price": str(posted.get("compare_at_price", "")).strip(),
            "stock": str(posted.get("stock", form.get("stock_quantity") or "")).strip(),
            "enabled": enabled,
        })

    for axis in axes:
        values = list(axis_values[axis["key"]].values())
        if len(values) > MAX_VALUES_PER_AXIS:
            errors[f"variants.{axis['key']}"] = (
                f"{axis['label']} admite hasta {MAX_VALUES_PER_AXIS} valores distintos."
            )
        # La configuración publicada y las galerías solo exponen valores usados
        # por presentaciones activas. Las filas inactivas conservan sus opciones.
        axis["values"] = list(active_axis_values[axis["key"]].values())

    enabled_variants = [row for row in variants if row["enabled"]]
    requested_default_id = str(
        form.get("variant_default_choice")
        or raw_configuration.get("default_variant_id")
        or ""
    )
    if requested_default_id and requested_default_id not in {
        row["variant_id"] for row in enabled_variants
    }:
        requested_default_id = next(
            (
                row["variant_id"]
                for row in enabled_variants
                if row["combination_key"] == requested_default_id
            ),
            requested_default_id,
        )
    if not requested_default_id:
        requested_default_key = str(raw_configuration.get("default_combination_key") or "")
        requested_default_id = next(
            (
                row["variant_id"]
                for row in enabled_variants
                if row["combination_key"] == requested_default_key
            ),
            "",
        )
    enabled_ids = {row["variant_id"] for row in enabled_variants}
    if requested_default_id not in enabled_ids:
        if final:
            errors["variants.default"] = "Selecciona una variante predeterminada activa."
        requested_default_id = enabled_variants[0]["variant_id"] if enabled_variants else ""
    if not enabled_variants:
        errors["variants"] = "Activa al menos una variante."
    default_variant = next(
        (row for row in variants if row["variant_id"] == requested_default_id),
        None,
    )
    visual_axis = next((axis for axis in axes if axis["is_visual"]), None)
    configuration = {
        "version": VARIANT_CONFIGURATION_VERSION,
        "enabled": True,
        "mode": "family",
        "axes": axes,
        "visual_axis_key": visual_axis["key"] if visual_axis else None,
        "default_variant_id": requested_default_id or None,
        "default_combination_key": default_variant["combination_key"] if default_variant else None,
        "next_sku_sequence": next_sequence,
        "source_snapshot": dict(
            raw_configuration.get("source_snapshot")
            or existing_configuration.get("source_snapshot")
            or {}
        ),
        "archived_family": False,
    }
    return configuration, variants, errors


def variant_rows_complete(variants: list[dict[str, Any]]) -> bool:
    enabled = [row for row in variants if row.get("enabled", True)]
    if not enabled:
        return False
    seen_skus: set[str] = set()
    seen_ids: set[str] = set()
    seen_combinations: set[str] = set()
    for row in enabled:
        sku = str(row.get("sku") or "")
        variant_id = str(row.get("variant_id") or "")
        combination_key = str(row.get("combination_key") or "")
        try:
            price = Decimal(str(row.get("price") or ""))
            compare_at_raw = str(row.get("compare_at_price") or "").strip()
            compare_at_price = Decimal(compare_at_raw) if compare_at_raw else None
            stock_value = row.get("stock")
            stock = int(str(stock_value if stock_value is not None else ""))
        except (InvalidOperation, TypeError, ValueError):
            return False
        if (
            not price.is_finite()
            or price <= MINIMUM_SELLER_PRICE
            or (
                compare_at_price is not None
                and (not compare_at_price.is_finite() or compare_at_price <= price)
            )
            or stock < 0
            or not sku
            or not variant_id
            or not combination_key
            or sku in seen_skus
            or variant_id in seen_ids
            or combination_key in seen_combinations
        ):
            return False
        seen_skus.add(sku)
        seen_ids.add(variant_id)
        seen_combinations.add(combination_key)
    return True


def publication_payload_from_draft(draft: Any) -> dict[str, Any]:
    """Pure conversion contract used by the future moderation/publication workflow."""
    configuration = dict(draft.variant_configuration or {})
    family_enabled = (
        configuration.get("version", 1) < 4
        or configuration.get("enabled") is True
    )
    single_media_value_key = configuration.get("single_media_value_key")
    return {
        "product": {
            "title": draft.title,
            "brand": draft.brand,
            "model_number": draft.model_number,
            "description": draft.description,
            "variant_configuration": configuration,
        },
        "variants": [
            dict(row) for row in (draft.variants or []) if row.get("enabled", True)
        ] if family_enabled else [],
        "media": [
            {
                "storage_key": item.storage_key,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "width": item.width,
                "height": item.height,
                "position": item.position,
                "is_cover": item.is_cover,
                "variant_axis_key": item.variant_axis_key,
                "variant_value_key": item.variant_value_key,
            }
            for item in draft.files
            if getattr(item.status, "value", item.status) == "ACTIVE"
            and getattr(item.kind, "value", item.kind) == "IMAGE"
            and (
                family_enabled
                or not single_media_value_key
                or item.variant_value_key in {None, single_media_value_key}
            )
        ],
    }
