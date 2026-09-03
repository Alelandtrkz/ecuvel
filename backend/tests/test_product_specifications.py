from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.catalog.product_templates import PRODUCT_TEMPLATES
from app.services.product_specifications import (
    SECTION_TITLES,
    build_product_specification_presentation,
    format_condition,
    format_spanish_number,
    is_publicly_empty,
    is_valid_gtin,
    product_specification_presentation_payload,
    resolve_product_template,
    with_unit,
)


def _row(
    *,
    category_code: str = "ELECTRONICS_PHONES",
    attributes: dict | None = None,
    **overrides,
):
    values = {
        "category_code": category_code,
        "category_name": "Categoría pública",
        "product_brand": "Marca",
        "product_model_number": "Modelo",
        "manufacturer_barcode": None,
        "variant_attributes": attributes or {},
        "weight_grams": None,
        "length_mm": None,
        "width_mm": None,
        "height_mm": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _items(presentation):
    return {
        item.key: item
        for section in presentation.sections
        for item in section.items
    }


def test_section_title_matrix_covers_the_real_template_registry():
    registry_sections = {
        field.section
        for template in PRODUCT_TEMPLATES.values()
        for field in template.fields
    }
    assert registry_sections <= SECTION_TITLES.keys()
    assert all(title.strip() for title in SECTION_TITLES.values())
    assert SECTION_TITLES["tecnica"] == "Especificaciones técnicas"
    assert SECTION_TITLES["presentacion"] == "Diseño y presentación"


def test_template_resolution_requires_exact_category_code():
    assert resolve_product_template("ELECTRONICS_PHONES") is PRODUCT_TEMPLATES["electronics_phones"]
    assert resolve_product_template("electronics_phones") is None
    assert resolve_product_template("phones") is None
    assert resolve_product_template(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("  ", True),
        ([], True),
        ({}, True),
        (["", None, {}], True),
        ({"one": "", "two": []}, True),
        (False, False),
        (0, False),
        ([0], False),
    ],
)
def test_public_empty_detection_is_recursive_without_dropping_false_or_zero(value, expected):
    assert is_publicly_empty(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(8, "8"), (8.0, "8"), (Decimal("6.7000"), "6,7"), (0, "0"), ("5647", "5647")],
)
def test_numeric_format_is_deterministic_spanish(value, expected):
    assert format_spanish_number(value) == expected


def test_units_are_metadata_driven_without_legacy_duplication():
    assert with_unit("8", "GB") == "8 GB"
    assert with_unit("8 GB", "GB") == "8 GB"
    assert with_unit("8GB", "GB") == "8GB"
    assert with_unit("6,7", "pulgadas") == "6,7 pulgadas"


def test_condition_formatter_is_preserved_but_unknown_states_are_not_invented():
    assert format_condition("NEW") == "Nuevo"
    assert format_condition("USED") is None


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("96385074", True),
        ("012345678905", True),
        ("4006381333931", True),
        ("10012345000017", True),
        ("4006381333932", False),
        ("CRI-00000002-000042", False),
        ("1234", False),
    ],
)
def test_gtin_validation_requires_supported_length_digits_and_checksum(value, valid):
    assert is_valid_gtin(value) is valid


def test_phone_presentation_uses_metadata_conditions_units_and_structured_sections():
    attributes = {
        "condition": "NEW",
        "country_origin": "Estados Unidos",
        "tipo_producto": "Smartphone",
        "sistema_operativo": "iOS",
        "ram_gb": "8",
        "almacenamiento_gb": "512",
        "pantalla_pulgadas": "6.7",
        "camara_principal_mp": "48",
        "bateria_mah": "5647",
        "potencia_w": "25",  # stale charger-only value must remain hidden
        "warranty": {
            "type": "Garantía de tienda",
            "duration": "3",
            "unit": "meses",
            "responsible": "ECUVEL",
            "conditions": "Aplican condiciones",
        },
        "package_contents": ["Teléfono", " ", "Cable USB-C"],
        "highlights": ["Acabado de titanio", ""],
        "variant_options": {"color_principal": "Naranja"},
        "unknown_internal_key": {"storage_key": "private/path"},
    }
    snapshot = deepcopy(attributes)
    presentation = build_product_specification_presentation(
        _row(
            attributes=attributes,
            category_name="Teléfonos",
            product_brand="Apple",
            product_model_number="iPhone 17 Pro Max",
            manufacturer_barcode="CRI-00000002-000042",
            weight_grams=500,
        )
    )
    items = _items(presentation)

    assert attributes == snapshot
    assert "condition" not in items
    assert items["country_origin"].label == "País de origen"
    assert items["ram_gb"].value == "8 GB"
    assert items["almacenamiento_gb"].value == "512 GB"
    assert items["pantalla_pulgadas"].value == "6,7 pulgadas"
    assert items["camara_principal_mp"].value == "48 MP"
    assert items["bateria_mah"].value == "5647 mAh"
    assert items["weight_grams"].value == "500 g"
    assert "potencia_w" not in items
    assert "manufacturer_barcode" not in items
    assert "variant_options" not in items
    assert "unknown_internal_key" not in items
    assert items["warranty_duration"].value == "3 meses"
    assert items["package_contents"].list_items == ("Teléfono", "Cable USB-C")
    assert items["seller_highlights"].list_items == ("Acabado de titanio",)
    assert [item.key for item in presentation.highlights] == [
        "brand",
        "model",
        "ram_gb",
        "almacenamiento_gb",
        "pantalla_pulgadas",
        "bateria_mah",
    ]


@pytest.mark.parametrize(
    ("category_code", "attributes", "expected_key", "expected_label", "expected_value"),
    [
        ("ELECTRONICS_CAMERAS", {"tipo_camara": "Fotográfica", "resolucion_mp": "24.0"}, "resolucion_mp", "Resolución", "24 MP"),
        ("FASHION_MEN", {"tipo": "Camisa", "talla": "M"}, "talla", "Talla", "M"),
        ("FASHION_SHOES", {"tipo": "Deportivo", "sistema_talla": "EU"}, "sistema_talla", "Sistema", "EU"),
        ("BEAUTY_COSMETICS", {"tipo": "Labial", "contenido_neto": 0}, "contenido_neto", "Contenido neto", "0"),
        ("HOME_DECORATION", {"tipo": "Lámpara", "dimensiones": "35"}, "dimensiones", "Dimensiones", "35 cm"),
        ("BABIES_TOYS", {"tipo": "Sonajero", "lavable": False}, "lavable", "Lavable", "No"),
    ],
)
def test_multiple_category_families_follow_canonical_metadata(
    category_code,
    attributes,
    expected_key,
    expected_label,
    expected_value,
):
    item = _items(build_product_specification_presentation(_row(category_code=category_code, attributes=attributes)))[expected_key]
    assert item.label == expected_label
    assert item.value == expected_value


def test_unknown_and_undocumented_structured_fields_are_hidden_without_mutation():
    attributes = {
        "tipo": "Camisa",
        "tabla_tallas": [{"size": "M", "chest": 90}],
        "unknown": ["private", "values"],
    }
    snapshot = deepcopy(attributes)
    items = _items(
        build_product_specification_presentation(
            _row(category_code="FASHION_WOMEN", attributes=attributes)
        )
    )
    assert "tipo" in items
    assert "tabla_tallas" not in items
    assert "unknown" not in items
    assert attributes == snapshot


def test_legacy_fallback_only_exposes_safe_common_and_physical_values():
    presentation = build_product_specification_presentation(
        _row(
            category_code="LEGACY_UNKNOWN",
            attributes={"condition": "NEW", "country_origin": "Ecuador", "secret": "hide me"},
            manufacturer_barcode="4006381333931",
            length_mm=0,
        )
    )
    items = _items(presentation)
    assert set(items) == {
        "category",
        "brand",
        "model",
        "country_origin",
        "manufacturer_barcode",
        "length_mm",
    }
    assert items["length_mm"].value == "0 mm"


def test_summary_is_stable_deduplicated_and_never_exceeds_six_items():
    row = _row(
        attributes={
            "tipo_producto": "Smartphone",
            "ram_gb": "8 GB",
            "almacenamiento_gb": "512",
            "pantalla_pulgadas": "6.7",
            "camara_principal_mp": "48",
            "bateria_mah": "5000",
            "color_principal": "Azul",
        }
    )
    first = build_product_specification_presentation(row)
    second = build_product_specification_presentation(row)
    assert first.highlights == second.highlights
    assert len(first.highlights) == 6
    assert len({item.key for item in first.highlights}) == len(first.highlights)
    assert _items(first)["ram_gb"].value == "8 GB"


def test_public_variant_payload_reuses_compact_d2_presentation_without_raw_fields():
    presentation = build_product_specification_presentation(
        _row(
            attributes={
                "condition": "NEW",
                "tipo_producto": "Smartphone",
                "ram_gb": "16",
                "package_contents": ["Teléfono", "Cable USB-C"],
                "highlights": ["<img src=x onerror=alert(1)>"],
                "variant_options": {"color_principal": "Azul"},
                "warranty": {
                    "type": "Garantía de tienda",
                    "duration": "12",
                    "unit": "meses",
                    "responsible": "ECUVEL",
                    "conditions": "Con factura",
                },
            }
        )
    )

    payload = product_specification_presentation_payload(presentation)

    assert payload["public_seller_highlights"] == [
        "<img src=x onerror=alert(1)>"
    ]
    assert {item["label"] for item in payload["public_summary"]} >= {"Marca", "RAM"}
    assert {
        "label": "Garantía",
        "value": "Garantía de tienda · 12 meses",
        "kind": "multiline",
        "list_items": ["Responsable: ECUVEL", "Condiciones: Con factura"],
    } in payload["public_specifications"]
    assert {
        "label": "Contenido del paquete",
        "value": "",
        "kind": "list",
        "list_items": ["Teléfono", "Cable USB-C"],
    } in payload["public_specifications"]
    serialized = repr(payload)
    assert "condition" not in serialized
    assert "variant_options" not in serialized


def test_compact_grid_contract_is_two_up_then_one_up_without_section_cards():
    backend_root = Path(__file__).resolve().parents[1]
    css = (backend_root / "app/static/css/product-detail.css").read_text(encoding="utf-8")
    template = (backend_root / "app/templates/components/product_specs.html").read_text(encoding="utf-8")

    assert (
        ".product-specs {\n"
        "  display: grid;\n"
        "  grid-template-columns: repeat(2, minmax(0, 1fr));"
    ) in css
    mobile = css.split("@media (max-width: 767px)", maxsplit=1)[1]
    assert ".product-specs" in mobile
    assert "grid-template-columns: 1fr;" in mobile
    assert 'class="product-specs product-specs--buyer"' in template
    assert "product-specification-section" not in template
