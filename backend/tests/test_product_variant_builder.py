from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from app.catalog.product_templates import PRODUCT_TEMPLATES, variant_axes_for_product_type
from app.services.product_variant_builder import (
    build_variant_state,
    publication_payload_from_draft,
    variant_rows_complete,
)


TEMPLATE = PRODUCT_TEMPLATES["electronics_phones"]


@pytest.mark.parametrize(
    ("product_type", "expected"),
    (
        ("Smartphone", {"color_principal", "almacenamiento_gb", "ram_gb", "pantalla_pulgadas"}),
        ("Teléfono básico", {"color_principal", "almacenamiento_gb", "pantalla_pulgadas"}),
        ("Cargador", {"color_principal", "potencia_w", "tipo_conector_salida"}),
        ("Cable", {"color_principal", "longitud_cm", "tipo_conector_salida", "tipo_conector_entrada"}),
        ("Protector", {"color_principal", "modelo_compatible", "material", "tipo_protector"}),
        ("Soporte", {"color_principal", "modelo_compatible", "material", "tipo_soporte"}),
        ("Repuesto", {"modelo_compatible", "color_principal", "tipo_repuesto"}),
        ("Otro", {"color_principal", "material"}),
    ),
)
def test_phone_variant_axis_matrix(product_type, expected):
    assert {axis.key for axis in variant_axes_for_product_type(TEMPLATE, product_type)} == expected


def test_listing_axis_policy_distinguishes_phone_and_shoe_detail_axes():
    phone_axes = {axis.key: axis for axis in TEMPLATE.variant_axes}
    shoe_axes = {
        axis.key: axis
        for axis in PRODUCT_TEMPLATES["fashion_shoes"].variant_axes
    }

    assert phone_axes["color_principal"].is_listing_axis is True
    assert phone_axes["ram_gb"].is_listing_axis is True
    assert phone_axes["almacenamiento_gb"].is_listing_axis is True
    assert phone_axes["pantalla_pulgadas"].is_listing_axis is False
    assert shoe_axes["color"].is_visual is True
    assert shoe_axes["color"].is_listing_axis is True
    assert shoe_axes["talla"].is_listing_axis is False


def _configuration(*axis_keys, default_id=None):
    return {
        "version": 3,
        "axes": [{"key": key} for key in axis_keys],
        "default_variant_id": default_id,
    }


def _row(variant_id, options, *, price="100", compare_at_price="", stock="4", enabled=True, previous_key=""):
    return {
        "variant_id": variant_id,
        "options": options,
        "previous_key": previous_key,
        "price": price,
        "compare_at_price": compare_at_price,
        "stock": stock,
        "enabled": enabled,
    }


def _form(configuration, *, rows=(), price="100", stock="4", default_id=None):
    values = [
        ("has_variants", "1"),
        ("variant_configuration", json.dumps(configuration)),
        ("price", price),
        ("stock_quantity", stock),
    ]
    if default_id:
        values.append(("variant_default_choice", default_id))
    for row in rows:
        values.extend(
            (
                ("variant_id[]", row["variant_id"]),
                ("variant_options[]", json.dumps(row["options"])),
                ("variant_combination_key[]", row.get("previous_key", "")),
                ("variant_price[]", row["price"]),
                ("variant_compare_at_price[]", row["compare_at_price"]),
                ("variant_stock[]", row["stock"]),
                ("variant_enabled[]", "1" if row["enabled"] else "0"),
            )
        )
    return MultiDict(values)


def _build(
    configuration,
    *,
    product_type="Smartphone",
    rows=(),
    default_id=None,
    existing_configuration=None,
    existing_variants=None,
    final=False,
):
    return build_variant_state(
        form=_form(configuration, rows=rows, default_id=default_id),
        template=TEMPLATE,
        attributes={"tipo_producto": product_type},
        product_code="CRI-00000001-000001",
        existing_configuration=existing_configuration,
        existing_variants=existing_variants,
        final=final,
    )


def test_rejects_axis_from_another_phone_product_type():
    _config, variants, errors = _build(
        _configuration("ram_gb"),
        product_type="Cable",
        rows=(_row("variant-1", {"ram_gb": "8"}),),
    )
    assert variants == []
    assert "no permitido" in errors["variants"]


def test_manual_rows_do_not_create_cartesian_combinations():
    rows = (
        _row("black-8-128", {"color_principal": "Negro", "ram_gb": "8", "almacenamiento_gb": "128"}),
        _row("blue-16-512", {"color_principal": "Azul", "ram_gb": "16", "almacenamiento_gb": "512"}),
    )
    config, variants, errors = _build(
        _configuration("color_principal", "ram_gb", "almacenamiento_gb"),
        rows=rows,
        default_id="black-8-128",
    )
    assert errors == {}
    assert len(variants) == 2
    assert {row["name"] for row in variants} == {"Negro / 8 GB / 128 GB", "Azul / 16 GB / 512 GB"}
    assert config["version"] == 4
    assert config["mode"] == "family"
    assert config["enabled"] is True
    assert config["listing_axis_keys"] == [
        "color_principal",
        "ram_gb",
        "almacenamiento_gb",
    ]
    assert all(axis["is_listing_axis"] for axis in config["axes"])


def test_empty_family_can_autosave_but_not_finish_without_variants():
    config, variants, errors = _build(
        _configuration("color_principal", "almacenamiento_gb"),
        rows=(),
        final=False,
    )
    assert errors == {}
    assert variants == []
    assert config["mode"] == "family"

    _config, _variants, final_errors = _build(
        _configuration("color_principal", "almacenamiento_gb"),
        rows=(),
        final=True,
    )
    assert "Agrega al menos una variante" in final_errors["variants"]


def test_rejects_duplicate_manual_variant_and_more_than_fifty_rows():
    duplicate_rows = (
        _row("first", {"ram_gb": "8"}),
        _row("second", {"ram_gb": "08"}),
    )
    _config, _variants, duplicate_errors = _build(
        _configuration("ram_gb"), rows=duplicate_rows
    )
    assert "ya existe" in duplicate_errors["variants.1"]

    too_many = tuple(
        _row(f"variant-{index}", {"color_principal": f"Color {index}"})
        for index in range(51)
    )
    _config, _variants, limit_errors = _build(
        _configuration("color_principal"), rows=too_many
    )
    assert "hasta 50 variantes" in limit_errors["variants"]


def test_edit_preserves_stable_sku_price_and_stock_by_variant_id():
    first_rows = (
        _row("phone-main", {"color_principal": "Negro", "almacenamiento_gb": "128"}, price="125.50", stock="7"),
    )
    first_config, first_variants, errors = _build(
        _configuration("color_principal", "almacenamiento_gb"),
        rows=first_rows,
        default_id="phone-main",
    )
    assert errors == {}
    original_sku = first_variants[0]["sku"]

    edited_rows = (
        _row("phone-main", {"color_principal": "Verde", "almacenamiento_gb": "256"}, price="125.50", stock="7"),
    )
    second_config, second_variants, errors = _build(
        _configuration("color_principal", "almacenamiento_gb"),
        rows=edited_rows,
        default_id="phone-main",
        existing_configuration=first_config,
        existing_variants=first_variants,
    )
    assert errors == {}
    assert second_variants[0]["sku"] == original_sku
    assert second_variants[0]["price"] == "125.50"
    assert second_variants[0]["stock"] == "7"
    assert second_variants[0]["combination_key"] == "color_principal=verde|almacenamiento_gb=256"
    assert second_config["default_variant_id"] == "phone-main"


def test_v2_row_import_preserves_sku_using_previous_combination_key():
    old_key = "color_principal=negro|almacenamiento_gb=128"
    old_config = {
        "version": 2,
        "axes": [{"key": "color_principal"}, {"key": "almacenamiento_gb"}],
        "default_combination_key": old_key,
        "next_sku_sequence": 2,
    }
    old_variants = [{
        "combination_key": old_key,
        "attributes": {"color_principal": "Negro", "almacenamiento_gb": "128"},
        "name": "Negro / 128 GB",
        "sku": "CRI-00000001-000001-V01",
        "price": "119.99",
        "stock": "6",
        "enabled": True,
    }]
    rows = (
        _row(
            "imported-v2-row",
            {"color_principal": "Negro", "almacenamiento_gb": "128"},
            price="119.99",
            stock="6",
            previous_key=old_key,
        ),
    )
    config, variants, errors = _build(
        _configuration("color_principal", "almacenamiento_gb"),
        rows=rows,
        default_id="imported-v2-row",
        existing_configuration=old_config,
        existing_variants=old_variants,
    )
    assert errors == {}
    assert config["version"] == 4
    assert variants[0]["variant_id"] == "imported-v2-row"
    assert variants[0]["sku"] == "CRI-00000001-000001-V01"


def test_only_active_rows_populate_selector_and_default_must_be_active():
    rows = (
        _row("black", {"color_principal": "Negro"}, price="100", stock="2"),
        _row("blue", {"color_principal": "Azul"}, price="", stock="", enabled=False),
    )
    config, variants, errors = _build(
        _configuration("color_principal"),
        rows=rows,
        default_id="black",
        final=True,
    )
    assert errors == {}
    assert config["default_variant_id"] == "black"
    assert config["axes"][0]["values"] == [{"key": "negro", "label": "Negro", "swatch": "#111827"}]
    assert variant_rows_complete(variants)

    _config, _variants, errors = _build(
        _configuration("color_principal"),
        rows=rows,
        default_id="blue",
        final=True,
    )
    assert "predeterminada activa" in errors["variants.default"]


def test_active_manual_variant_requires_valid_commercial_values():
    _config, variants, errors = _build(
        _configuration("color_principal"),
        rows=(_row("black", {"color_principal": "Negro"}, price="", stock="-1"),),
        default_id="black",
    )
    assert errors == {}
    assert not variant_rows_complete(variants)


def test_compare_at_price_must_be_greater_than_variant_price():
    _config, variants, errors = _build(
        _configuration("color_principal"),
        rows=(_row("black", {"color_principal": "Negro"}, price="100", compare_at_price="90"),),
        default_id="black",
    )
    assert errors == {}
    assert not variant_rows_complete(variants)

    _config, variants, errors = _build(
        _configuration("color_principal"),
        rows=(_row("black", {"color_principal": "Negro"}, price="100", compare_at_price="120"),),
        default_id="black",
    )
    assert errors == {}
    assert variant_rows_complete(variants)


def test_disabling_family_archives_existing_rows_without_renumbering():
    existing = [{
        "variant_id": "black",
        "combination_key": "color_principal=negro",
        "attributes": {"color_principal": "Negro"},
        "name": "Negro",
        "sku": "CRI-00000001-000001-V01",
        "price": "100",
        "compare_at_price": "120",
        "stock": "2",
        "enabled": True,
    }]
    form = MultiDict((
        ("variant_configuration", json.dumps(_configuration("color_principal"))),
    ))
    config, variants, errors = build_variant_state(
        form=form,
        template=TEMPLATE,
        attributes={"tipo_producto": "Smartphone"},
        product_code="CRI-00000001-000001",
        existing_configuration={"version": 4, "enabled": True, "mode": "family", "axes": [{"key": "color_principal"}]},
        existing_variants=existing,
        final=False,
    )
    assert errors == {}
    assert config["enabled"] is False
    assert config["archived_family"] is True
    assert variants == existing


def test_publication_contract_never_emits_archived_variants_as_offers():
    media = [
        SimpleNamespace(
            storage_key="images/blue.png",
            media_type="image/png",
            size_bytes=10,
            width=1,
            height=1,
            position=0,
            is_cover=True,
            variant_axis_key="color_principal",
            variant_value_key="azul",
            status="ACTIVE",
            kind="IMAGE",
        ),
        SimpleNamespace(
            storage_key="images/red.png",
            media_type="image/png",
            size_bytes=10,
            width=1,
            height=1,
            position=0,
            is_cover=True,
            variant_axis_key="color_principal",
            variant_value_key="rojo",
            status="ACTIVE",
            kind="IMAGE",
        ),
    ]
    draft = SimpleNamespace(
        title="iPhone 17 Pro Max",
        brand="Apple",
        model_number="17 Pro Max",
        description="Familia",
        variant_configuration={
            "version": 4,
            "enabled": False,
            "mode": "single",
            "single_media_value_key": "azul",
        },
        variants=[{"variant_id": "blue", "enabled": True}],
        files=media,
    )

    payload = publication_payload_from_draft(draft)

    assert payload["variants"] == []
    assert [item["storage_key"] for item in payload["media"]] == ["images/blue.png"]
