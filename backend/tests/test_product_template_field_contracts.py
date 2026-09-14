from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from flask import render_template
from werkzeug.datastructures import MultiDict

import app.catalog.product_templates as product_template_registry
from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    ProductTemplate,
    ProductTemplateValidationError,
    field_def,
    validate_attributes,
    validate_template_registry,
)
from app.services.product_drafts import _parse_attributes


def _template(*fields):
    return ProductTemplate(
        key="synthetic_contract",
        name="Synthetic contract",
        category_code="SYNTHETIC",
        subcategory_code="SYNTHETIC_FIELDS",
        fields=tuple(fields),
    )


def _render_field(app, field, value=None) -> str:
    view = SimpleNamespace(draft=SimpleNamespace(attributes={field.key: value}))
    with app.test_request_context():
        return render_template(
            "partners/_dynamic_field.html",
            field=field,
            view=view,
            errors={},
        )


def test_parser_uses_canonical_bool_list_and_scalar_shapes():
    template = _template(
        field_def("aplica", "Aplica", type="boolean"),
        field_def("omitido", "Omitido", type="boolean"),
        field_def(
            "puertos",
            "Puertos",
            type="multiselect",
            options=("HDMI", "USB-C, Thunderbolt"),
        ),
        field_def("cuidados", "Cuidados", type="chips"),
        field_def("talla", "Talla", type="variant_attribute"),
        field_def("entero", "Entero", type="integer"),
        field_def("decimal", "Decimal", type="decimal"),
        field_def("dimension", "Dimensión", type="dimension"),
    )
    form = MultiDict(
        [
            ("attributes[aplica]", "1"),
            ("attributes[puertos]", " HDMI "),
            ("attributes[puertos]", "USB-C, Thunderbolt"),
            ("attributes[puertos]", "HDMI"),
            ("attributes[puertos]", " "),
            ("attributes[cuidados]", " Lavar a mano "),
            ("attributes[cuidados]", "Agua fría, ciclo suave"),
            ("attributes[cuidados]", "Lavar a mano"),
            ("attributes[cuidados]", ""),
            ("attributes[talla]", " M "),
            ("attributes[entero]", " 12 "),
            ("attributes[decimal]", " 12.50 "),
            ("attributes[dimension]", " 48.25 "),
        ]
    )

    assert _parse_attributes(form, template) == {
        "aplica": True,
        "omitido": False,
        "puertos": ["HDMI", "USB-C, Thunderbolt"],
        "cuidados": ["Lavar a mano", "Agua fría, ciclo suave"],
        "talla": "M",
        "entero": "12",
        "decimal": "12.50",
        "dimension": "48.25",
    }


@pytest.mark.parametrize(
    ("field", "valid_value", "invalid_values"),
    (
        (
            field_def("entero", "Entero", type="integer", min=1, max=3),
            "2",
            ("1.5", "texto", "0", "4"),
        ),
        (
            field_def(
                "decimal",
                "Decimal",
                type="decimal",
                min=Decimal("1.1"),
                max=Decimal("2.2"),
            ),
            "1.5",
            ("texto", "NaN", "1.0", "2.3"),
        ),
        (
            field_def(
                "dimension",
                "Dimensión",
                type="dimension",
                min=Decimal("0"),
                max=Decimal("100"),
            ),
            "10.25",
            ("texto", "-0.1", "100.1"),
        ),
        (
            field_def("fecha", "Fecha", type="date"),
            "2024-02-29",
            ("2026-02-30", "2026-13-01", "14/09/2026", "2026-9-1"),
        ),
        (
            field_def("radio", "Radio", type="radio", options=("A", "B")),
            "A",
            ("C",),
        ),
        (
            field_def(
                "multi",
                "Multi",
                type="multiselect",
                options=("A", "B"),
            ),
            ["A", "B"],
            (["C"], "A"),
        ),
        (
            field_def("bool", "Booleano", type="boolean"),
            True,
            ("true", 1, [True]),
        ),
        (
            field_def("chips", "Chips", type="chips"),
            ["A", "B, C"],
            (["A", ["B"]], ["A", 2], "A"),
        ),
        (
            field_def("variante", "Variante", type="variant_attribute"),
            "M",
            (["M"], {"value": "M"}),
        ),
    ),
)
def test_validation_contracts(field, valid_value, invalid_values):
    template = _template(field)
    assert validate_attributes(template, {field.key: valid_value}, final=False) == {}
    for invalid_value in invalid_values:
        assert f"attributes.{field.key}" in validate_attributes(
            template,
            {field.key: invalid_value},
            final=False,
        )


@pytest.mark.parametrize(
    "invalid_field",
    (
        field_def("invalid", "Invalid", type="select", options=("A", "A")),
        field_def("invalid", "Invalid", type="radio", options=("A", "")),
        field_def("invalid", "Invalid", type="multiselect", options=(" ", "B")),
        field_def("invalid", "Invalid", type="decimal", min=Decimal("2"), max=Decimal("1")),
        field_def("invalid", "Invalid", type="integer", min=Decimal("0.5"), max=2),
    ),
)
def test_registry_rejects_invalid_options_and_numeric_bounds(monkeypatch, invalid_field):
    base = PRODUCT_TEMPLATES["electronics_phones"]
    monkeypatch.setitem(
        product_template_registry.PRODUCT_TEMPLATES,
        base.key,
        replace(base, fields=base.fields + (invalid_field,)),
    )

    with pytest.raises(ProductTemplateValidationError) as exc_info:
        validate_template_registry()

    assert "electronics_phones.invalid" in exc_info.value.errors


def test_numeric_rendering_emits_type_step_bounds_and_units(app):
    integer_html = _render_field(
        app,
        field_def("integer", "Integer", type="integer", min=0, max=10),
        "4",
    )
    decimal_html = _render_field(
        app,
        field_def(
            "decimal",
            "Decimal",
            type="decimal",
            min=Decimal("0.1"),
            max=Decimal("9.9"),
        ),
        "1.25",
    )
    dimension_html = _render_field(
        app,
        field_def("dimension", "Dimension", type="dimension", unit="cm", min=0, max=100),
        "22.5",
    )

    assert 'type="number"' in integer_html
    assert 'step="1"' in integer_html
    assert 'min="0"' in integer_html and 'max="10"' in integer_html
    assert 'type="number"' in decimal_html and 'step="any"' in decimal_html
    assert 'min="0.1"' in decimal_html and 'max="9.9"' in decimal_html
    assert 'type="number"' in dimension_html and 'step="any"' in dimension_html
    assert 'min="0"' in dimension_html and 'max="100"' in dimension_html
    assert "cm" in dimension_html


def test_radio_and_multiselect_render_distinct_repeated_controls(app):
    radio_html = _render_field(
        app,
        field_def("radio", "Radio", type="radio", options=("A", "B")),
        "B",
    )
    multi_html = _render_field(
        app,
        field_def("multi", "Multi", type="multiselect", options=("A", "B")),
        ["A", "B"],
    )

    assert radio_html.count('type="radio"') == 3
    assert "data-partner-select" not in radio_html
    assert radio_html.count('name="attributes[radio]"') == 3
    assert multi_html.count('type="checkbox"') == 2
    assert multi_html.count('name="attributes[multi]"') == 2
    assert "Separe los valores con coma" not in multi_html


def test_chips_render_repeated_hidden_values_and_variant_attribute_is_scalar(app):
    chips_html = _render_field(
        app,
        field_def("cuidados", "Cuidados", type="chips"),
        ["Lavar a mano", "Agua fría, ciclo suave"],
    )
    variant_html = _render_field(
        app,
        field_def("talla", "Talla", type="variant_attribute"),
        "M",
    )

    assert "data-chip-editor" in chips_html
    assert chips_html.count(
        '<input type="hidden" name="attributes[cuidados]"'
    ) == 2
    assert 'value="Agua fría, ciclo suave"' in chips_html
    assert 'type="text" name="attributes[talla]" value="M"' in variant_html
    assert "Separe los valores con coma" not in variant_html


def test_date_and_boolean_render_canonical_state_only(app):
    date_html = _render_field(
        app,
        field_def("fecha", "Fecha", type="date"),
        "2026-09-14",
    )
    true_html = _render_field(
        app,
        field_def("aplica", "Aplica", type="boolean"),
        True,
    )
    legacy_false_html = _render_field(
        app,
        field_def("aplica", "Aplica", type="boolean"),
        "false",
    )

    assert 'type="date"' in date_html
    assert 'value="2026-09-14"' in date_html
    assert " checked" in true_html
    assert " checked" not in legacy_false_html


def test_condition_metadata_renders_as_escaped_json(app):
    html = _render_field(
        app,
        field_def(
            "target",
            "Target",
            condition={
                "field": "clase_producto",
                "values": ["Laptop, Workstation", "Desktop"],
            },
        ),
    )

    assert 'data-condition-field="clase_producto"' in html
    assert "Laptop, Workstation" in html
    assert "&#34;Desktop&#34;" in html
