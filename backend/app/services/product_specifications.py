from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    ProductTemplate,
    ProductTemplateField,
)


@dataclass(frozen=True, slots=True)
class ProductSpecificationItemViewModel:
    key: str
    label: str
    value: str = ""
    kind: str = "text"
    list_items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductSpecificationSectionViewModel:
    key: str
    title: str
    items: tuple[ProductSpecificationItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class ProductSpecificationPresentation:
    sections: tuple[ProductSpecificationSectionViewModel, ...]
    highlights: tuple[ProductSpecificationItemViewModel, ...]
    compact_items: tuple[ProductSpecificationItemViewModel, ...]
    seller_highlights: tuple[str, ...]


# This matrix covers every section currently used by PRODUCT_TEMPLATES. New
# registry sections are safe-by-default: they are not rendered until a public
# Spanish title and semantic position are explicitly assigned here.
SECTION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("general", "Información general"),
    ("producto", "Producto"),
    ("tecnica", "Especificaciones técnicas"),
    ("software", "Software"),
    ("pantalla", "Pantalla"),
    ("imagen", "Imagen"),
    ("camara", "Cámara"),
    ("video", "Video"),
    ("audio", "Audio"),
    ("energia", "Batería y energía"),
    ("alimentacion", "Alimentación"),
    ("conectividad", "Conectividad"),
    ("deteccion", "Detección"),
    ("seguridad", "Seguridad"),
    ("proteccion", "Protección"),
    ("compatibilidad", "Compatibilidad"),
    ("prenda", "Prenda"),
    ("calzado", "Calzado"),
    ("tallas", "Tallas"),
    ("materiales", "Materiales"),
    ("presentacion", "Diseño y presentación"),
    ("medidas", "Medidas"),
    ("uso", "Uso"),
    ("cuidados", "Cuidados"),
    ("regulatorio", "Información regulatoria"),
    ("documentos", "Documentos"),
    ("warranty", "Garantía"),
    ("package_contents", "Contenido del paquete"),
    ("seller_highlights", "Características destacadas"),
)

SECTION_TITLES = dict(SECTION_DEFINITIONS)
SECTION_ORDER = {key: index for index, (key, _title) in enumerate(SECTION_DEFINITIONS)}

_TEMPLATES_BY_CATEGORY_CODE = {
    template.subcategory_code: template
    for template in PRODUCT_TEMPLATES.values()
}

_SHARED_ATTRIBUTE_KEYS = {
    "condition",
    "country_origin",
    "highlights",
    "package_contents",
    "variant_options",
    "warranty",
}

_HIDDEN_FIELD_TYPES = {
    "compatibility_table",
    "document",
    "file",
    "repeater",
    "size_table",
}

_LIST_FIELD_TYPES = {"chips", "multiselect"}
_NUMERIC_FIELD_TYPES = {"decimal", "dimension", "integer"}
_CONDITION_LABELS = {"NEW": "Nuevo"}
_WARRANTY_LABELS = (
    ("type", "Tipo"),
    ("duration", "Duración"),
    ("responsible", "Responsable"),
    ("conditions", "Condiciones"),
)


def resolve_product_template(category_code: str | None) -> ProductTemplate | None:
    """Resolve only an exact persisted Category.code; never guess from names."""
    if not category_code:
        return None
    return _TEMPLATES_BY_CATEGORY_CODE.get(category_code)


def is_publicly_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return not value or all(is_publicly_empty(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return not value or all(is_publicly_empty(item) for item in value)
    return False


def format_spanish_number(value: Any) -> str | None:
    if isinstance(value, bool) or is_publicly_empty(value):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"-0", ""}:
        rendered = "0"
    return rendered.replace(".", ",")


def with_unit(value: str, unit: str) -> str:
    displayed = value.strip()
    displayed_unit = unit.strip()
    if not displayed_unit:
        return displayed
    normalized_value = displayed.casefold().rstrip(". ")
    normalized_unit = displayed_unit.casefold()
    if (
        normalized_value == normalized_unit
        or normalized_value.endswith(f" {normalized_unit}")
        or normalized_value.endswith(normalized_unit)
        and normalized_value[: -len(normalized_unit)].rstrip().replace(",", ".").replace("-", "").isdigit()
    ):
        return displayed
    return f"{displayed} {displayed_unit}"


def is_valid_gtin(value: Any) -> bool:
    candidate = str(value or "").strip()
    if len(candidate) not in {8, 12, 13, 14} or not candidate.isascii() or not candidate.isdigit():
        return False
    total = 0
    for index, character in enumerate(reversed(candidate)):
        total += int(character) * (1 if index % 2 == 0 else 3)
    return total % 10 == 0


def format_condition(value: Any) -> str | None:
    """Keep the known enum formatter available for non-buyer/future policies."""
    if is_publicly_empty(value):
        return None
    return _CONDITION_LABELS.get(str(value).strip())


def build_product_specification_presentation(row: Any) -> ProductSpecificationPresentation:
    attributes = dict(row.variant_attributes or {})
    template = resolve_product_template(getattr(row, "category_code", None))
    sections: dict[str, list[ProductSpecificationItemViewModel]] = {}
    seen_concepts: set[str] = set()

    def add(section_key: str, item: ProductSpecificationItemViewModel, *, concept: str | None = None) -> None:
        effective_concept = concept or item.key
        if effective_concept in seen_concepts:
            return
        seen_concepts.add(effective_concept)
        sections.setdefault(section_key, []).append(item)

    for key, label, value in (
        ("category", "Categoría", row.category_name),
        ("brand", "Marca", row.product_brand),
        ("model", "Modelo", row.product_model_number),
    ):
        item = _scalar_item(key, label, value)
        if item:
            add("general", item)

    country = _scalar_item("country_origin", "País de origen", attributes.get("country_origin"))
    if country:
        add("general", country)

    if is_valid_gtin(row.manufacturer_barcode):
        add(
            "general",
            ProductSpecificationItemViewModel(
                "manufacturer_barcode",
                "Código de barras",
                str(row.manufacturer_barcode).strip(),
            ),
        )

    if template is not None:
        for field in sorted(template.fields, key=lambda item: (item.order, item.key)):
            if field.section not in SECTION_TITLES or field.key in _SHARED_ATTRIBUTE_KEYS:
                continue
            if not _condition_applies(field, attributes):
                continue
            item = _field_item(field, attributes.get(field.key))
            if item:
                add(field.section, item)

    for key, label, value, unit in (
        ("weight_grams", "Peso", row.weight_grams, "g"),
        ("length_mm", "Largo", row.length_mm, "mm"),
        ("width_mm", "Ancho", row.width_mm, "mm"),
        ("height_mm", "Alto", row.height_mm, "mm"),
    ):
        item = _numeric_item(key, label, value, unit)
        if item:
            add("medidas", item)

    warranty_items = _warranty_items(attributes.get("warranty"))
    if warranty_items:
        sections["warranty"] = list(warranty_items)

    package_items = _text_list(attributes.get("package_contents"))
    if package_items:
        sections["package_contents"] = [
            ProductSpecificationItemViewModel(
                "package_contents",
                "",
                kind="list",
                list_items=package_items,
            )
        ]

    seller_highlights = _text_list(attributes.get("highlights"))
    if seller_highlights:
        sections["seller_highlights"] = [
            ProductSpecificationItemViewModel(
                "seller_highlights",
                "",
                kind="list",
                list_items=seller_highlights,
            )
        ]

    section_models = tuple(
        ProductSpecificationSectionViewModel(
            key=key,
            title=SECTION_TITLES[key],
            items=tuple(items),
        )
        for key, items in sorted(
            sections.items(),
            key=lambda entry: SECTION_ORDER[entry[0]],
        )
        if items
    )
    return ProductSpecificationPresentation(
        sections=section_models,
        highlights=_build_summary(section_models),
        compact_items=_build_compact_items(section_models),
        seller_highlights=seller_highlights,
    )


def product_specification_presentation_payload(
    presentation: ProductSpecificationPresentation,
) -> dict[str, list[dict[str, Any]] | list[str]]:
    """Serialize only server-presented buyer values for client-side switching."""
    return {
        "public_summary": [
            _public_item_payload(item) for item in presentation.highlights
        ],
        "public_specifications": [
            _public_item_payload(item) for item in presentation.compact_items
        ],
        "public_seller_highlights": list(presentation.seller_highlights),
    }


def _condition_applies(field: ProductTemplateField, values: Mapping[str, Any]) -> bool:
    if not field.condition:
        return True
    trigger_key = field.condition.get("field")
    allowed_values = field.condition.get("values", ())
    return values.get(trigger_key) in allowed_values


def _scalar_item(key: str, label: str, value: Any) -> ProductSpecificationItemViewModel | None:
    if is_publicly_empty(value) or isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(value, bool):
        displayed = "Sí" if value else "No"
    else:
        displayed = str(value).strip()
    return ProductSpecificationItemViewModel(key, label, displayed)


def _numeric_item(
    key: str,
    label: str,
    value: Any,
    unit: str,
) -> ProductSpecificationItemViewModel | None:
    if is_publicly_empty(value) or isinstance(value, (Mapping, list, tuple, set, frozenset, bool)):
        return None
    raw = str(value).strip()
    displayed = format_spanish_number(value) or raw
    return ProductSpecificationItemViewModel(key, label, with_unit(displayed, unit))


def _field_item(
    field: ProductTemplateField,
    value: Any,
) -> ProductSpecificationItemViewModel | None:
    if is_publicly_empty(value) or field.type in _HIDDEN_FIELD_TYPES:
        return None
    if field.type == "boolean":
        if not isinstance(value, bool):
            return None
        return ProductSpecificationItemViewModel(
            field.key,
            field.label,
            "Sí" if value else "No",
        )
    if field.type in _LIST_FIELD_TYPES:
        items = _text_list(value)
        if not items:
            return None
        return ProductSpecificationItemViewModel(
            field.key,
            field.label,
            kind="list",
            list_items=items,
        )
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return None
    if field.type in _NUMERIC_FIELD_TYPES:
        unit = field.unit_label or field.unit
        return _numeric_item(field.key, field.label, value, unit)
    return _scalar_item(field.key, field.label, value)


def _text_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    items: list[str] = []
    for raw_item in value:
        if is_publicly_empty(raw_item) or isinstance(raw_item, (Mapping, list, tuple, set, frozenset)):
            continue
        rendered = "Sí" if raw_item is True else "No" if raw_item is False else str(raw_item).strip()
        if rendered:
            items.append(rendered)
    return tuple(items)


def _warranty_items(value: Any) -> tuple[ProductSpecificationItemViewModel, ...]:
    if not isinstance(value, Mapping):
        return ()
    items: list[ProductSpecificationItemViewModel] = []
    for key, label in _WARRANTY_LABELS:
        raw_value = value.get(key)
        if is_publicly_empty(raw_value) or isinstance(raw_value, (Mapping, list, tuple, set, frozenset)):
            continue
        displayed = str(raw_value).strip()
        if key == "duration":
            unit = value.get("unit")
            if not is_publicly_empty(unit) and not isinstance(unit, (Mapping, list, tuple, set, frozenset)):
                displayed = with_unit(displayed, str(unit))
        items.append(ProductSpecificationItemViewModel(f"warranty_{key}", label, displayed))
    return tuple(items)


def _build_compact_items(
    sections: tuple[ProductSpecificationSectionViewModel, ...],
) -> tuple[ProductSpecificationItemViewModel, ...]:
    compact: list[ProductSpecificationItemViewModel] = []
    for section in sections:
        if section.key == "seller_highlights":
            continue
        if section.key == "warranty":
            values = {item.key: item.value for item in section.items}
            warranty_type = values.get("warranty_type", "")
            duration = values.get("warranty_duration", "")
            primary = " · ".join(
                value for value in (warranty_type, duration) if value
            )
            secondary = tuple(
                f"{label}: {values[key]}"
                for key, label in (
                    ("warranty_responsible", "Responsable"),
                    ("warranty_conditions", "Condiciones"),
                )
                if values.get(key)
            )
            compact.append(
                ProductSpecificationItemViewModel(
                    key="warranty",
                    label="Garantía",
                    value=primary,
                    kind="multiline" if secondary else "text",
                    list_items=secondary,
                )
            )
            continue
        for item in section.items:
            compact.append(
                ProductSpecificationItemViewModel(
                    key=item.key,
                    label=item.label or section.title,
                    value=item.value,
                    kind=item.kind,
                    list_items=item.list_items,
                )
            )
    return tuple(compact)


def _public_item_payload(
    item: ProductSpecificationItemViewModel,
) -> dict[str, Any]:
    return {
        "label": item.label,
        "value": item.value,
        "kind": item.kind,
        "list_items": list(item.list_items),
    }


def _build_summary(
    sections: tuple[ProductSpecificationSectionViewModel, ...],
) -> tuple[ProductSpecificationItemViewModel, ...]:
    scalar_items = {
        item.key: item
        for section in sections
        for item in section.items
        if item.kind == "text" and len(item.value) <= 80
    }
    summary: list[ProductSpecificationItemViewModel] = []
    for key in ("brand", "model"):
        if key in scalar_items:
            summary.append(scalar_items.pop(key))

    # Unit-bearing values carry high comparison value across templates. Section
    # order breaks ties deterministically and gives screen/energy precedence over
    # camera details in the compact six-item summary.
    section_priority = {
        "tecnica": 0,
        "pantalla": 1,
        "energia": 2,
        "camara": 3,
        "imagen": 4,
    }
    candidates: list[tuple[int, int, str, ProductSpecificationItemViewModel]] = []
    for section in sections:
        if section.key in {"general", "warranty", "package_contents", "seller_highlights"}:
            continue
        for position, item in enumerate(section.items):
            if item.key not in scalar_items:
                continue
            has_unit = any(character.isdigit() for character in item.value) and " " in item.value
            candidates.append(
                (
                    0 if has_unit else 1,
                    section_priority.get(section.key, SECTION_ORDER[section.key] + 10),
                    f"{position:04d}:{item.key}",
                    item,
                )
            )
    for _unit_rank, _section_rank, _stable, item in sorted(candidates):
        if len(summary) == 6:
            break
        summary.append(item)
    if len(summary) < 6:
        for key in ("category", "country_origin"):
            item = scalar_items.get(key)
            if item and item not in summary:
                summary.append(item)
            if len(summary) == 6:
                break
    return tuple(summary[:6])
