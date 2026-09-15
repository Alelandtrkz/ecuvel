from __future__ import annotations

from decimal import Decimal

import pytest

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    condition_applies,
    get_product_template_for_category_code,
    template_key_for_category_code,
    validate_attributes,
    validate_template_registry,
    variant_axes_for_attributes,
)
from app.commands.marketplace_policy import INITIAL_CATEGORY_RATES
from app.commands.seed import _PRODUCT_CATEGORY_TREE
from app.services.partner_product_categories import LEGACY_NEW_LISTING_CATEGORY_CODES
from app.services.product_specifications import (
    build_product_specification_presentation,
    resolve_product_template,
)


CANONICAL_BEAUTY = {
    "BEAUTY_PERSONAL_HYGIENE": ("beauty_personal_hygiene", "Cuidado personal e higiene", "cuidado-personal-e-higiene", 1, "10.00"),
    "BEAUTY_MAKEUP": ("beauty_makeup", "Maquillaje", "maquillaje", 2, "8.00"),
    "BEAUTY_SKIN_CARE": ("beauty_skin_care", "Cuidado de la piel", "cuidado-de-la-piel", 3, "10.00"),
    "BEAUTY_HAIR_CARE": ("beauty_hair_care", "Cuidado del cabello", "cuidado-del-cabello", 4, "10.00"),
    "BEAUTY_FRAGRANCES": ("beauty_fragrances", "Fragancias", "fragancias", 5, "10.00"),
}
LEGACY_BEAUTY = {
    "BEAUTY_PERSONAL_CARE": ("beauty_personal_care", "10.00"),
    "BEAUTY_COSMETICS": ("beauty_cosmetics", "8.00"),
    "BEAUTY_SKINCARE": ("beauty_skincare", "10.00"),
}


def _fields(template_key: str):
    return {field.key: field for field in PRODUCT_TEMPLATES[template_key].fields}


def _applicable(template_key: str, product_type: str, **attributes: object) -> set[str]:
    values = {"tipo_producto": product_type, **attributes}
    return {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(field.condition, values)
    }


def _axis_keys(template_key: str, product_type: str) -> set[str]:
    return {
        axis.key
        for axis in variant_axes_for_attributes(
            PRODUCT_TEMPLATES[template_key], {"tipo_producto": product_type}
        )
    }


def _public_items(category_code: str, attributes: dict[str, object]):
    class Row:
        category_name = "Salud y belleza"
        product_brand = "Marca"
        product_model_number = "Modelo"
        manufacturer_barcode = None
        weight_grams = None
        length_mm = None
        width_mm = None
        height_mm = None

        def __init__(self):
            self.category_code = category_code
            self.variant_attributes = attributes

    presentation = build_product_specification_presentation(Row())
    return {
        item.key: item
        for section in presentation.sections
        for item in section.items
    }


def test_beauty_registry_seed_commission_and_public_resolution_integrity():
    validate_template_registry()
    _parent, children = next(
        entry for entry in _PRODUCT_CATEGORY_TREE if entry[0]["code"] == "BEAUTY_HEALTH"
    )
    seeded = {child["code"]: child for child in children}
    for code, (template_key, name, slug, sort_order, rate) in CANONICAL_BEAUTY.items():
        assert seeded[code] == {"code": code, "name": name, "slug": slug, "sort_order": sort_order}
        assert template_key_for_category_code(code) == template_key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[template_key]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal(rate)
        product_type = _fields(template_key)["tipo_producto"].options[0]
        assert "tipo_producto" in _public_items(code, {"tipo_producto": product_type})


def test_legacy_beauty_contracts_documents_rates_and_exclusion_are_preserved():
    expected_legacy_codes = {
        "FASHION_MEN", "FASHION_WOMEN", "FASHION_SHOES", "FASHION_ACCESSORIES",
        "HOME_DECORATION", "HOME_KITCHEN_TOOLS", "HOME_CLEANING",
        *LEGACY_BEAUTY,
    }
    assert LEGACY_NEW_LISTING_CATEGORY_CODES == frozenset(expected_legacy_codes)
    expected_fields = {"color_principal", "material", "tipo", "presentacion", "contenido_neto", "unidad", "ingredientes", "registro_sanitario"}
    for code, (template_key, rate) in LEGACY_BEAUTY.items():
        template = PRODUCT_TEMPLATES[template_key]
        assert template_key_for_category_code(code) == template_key
        assert resolve_product_template(code) is template
        assert INITIAL_CATEGORY_RATES[code] == Decimal(rate)
        assert set(_fields(template_key)) == expected_fields
        assert template.required_documents == ("registro_sanitario",)


def test_canonical_beauty_has_no_required_documents_or_regulatory_fields():
    disallowed_types = {"repeater", "compatibility_table", "size_table", "file", "document"}
    for template_key, *_ in CANONICAL_BEAUTY.values():
        template = PRODUCT_TEMPLATES[template_key]
        fields = _fields(template_key)
        assert template.required_documents == ()
        assert fields["tipo_producto"].type == "select"
        assert fields["tipo_producto"].required is True
        assert not disallowed_types & {field.type for field in template.fields}
        assert not {"registro_sanitario", "nso", "arcsa", "clasificacion_regulatoria"} & set(fields)


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Cepillo dental manual", {"dureza_cerdas", "tamano_cabezal", "cantidad_paquete"}, {"presentacion", "contenido_neto", "unidad", "aroma", "ingredientes", "longitud_hilo_m", "numero_hojas", "formato_desodorante"}),
        ("Hilo dental", {"longitud_hilo_m", "encerado", "cantidad_paquete"}, {"presentacion", "contenido_neto", "unidad", "aroma", "ingredientes", "dureza_cerdas", "numero_hojas", "formato_desodorante"}),
        ("Rasuradora manual", {"numero_hojas", "desechable", "cantidad_paquete"}, {"presentacion", "contenido_neto", "unidad", "aroma", "ingredientes", "dureza_cerdas", "longitud_hilo_m", "formato_desodorante"}),
        ("Desodorante / Antitranspirante", {"formato_desodorante", "antitranspirante"}, {"dureza_cerdas", "longitud_hilo_m", "numero_hojas"}),
        ("Jabón corporal", {"tipo_piel", "zona_uso"}, {"dureza_cerdas", "numero_hojas"}),
        ("Gel de baño / ducha", {"presentacion", "contenido_neto", "unidad", "aroma", "ingredientes", "tipo_piel", "zona_uso"}, {"dureza_cerdas", "longitud_hilo_m", "numero_hojas"}),
    ),
)
def test_personal_hygiene_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("beauty_personal_hygiene", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("beauty_personal_hygiene", product_type) == set()


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Base / Foundation", {"tono_color", "acabado", "tipo_piel", "cobertura", "spf_declarado"}, {"tipo_accesorio", "efecto_mascara"}, {"tono_color"}),
        ("Máscara de pestañas", {"tono_color", "acabado", "efecto_mascara", "resistente_agua"}, {"tipo_piel", "cobertura", "tipo_accesorio"}, {"tono_color"}),
        ("Delineador", {"tono_color", "resistente_agua"}, {"tipo_piel", "cobertura", "efecto_mascara"}, {"tono_color"}),
        ("Brocha / Pincel", {"tipo_accesorio", "material", "cantidad_piezas"}, {"tono_color", "textura", "acabado", "contenido_neto", "unidad", "ingredientes", "tipo_piel", "cobertura", "spf_declarado", "resistente_agua", "efecto_mascara"}, set()),
        ("Esponja / Aplicador", {"tipo_accesorio", "material", "cantidad_piezas"}, {"tono_color", "textura", "acabado", "contenido_neto", "unidad", "ingredientes", "tipo_piel", "cobertura", "spf_declarado", "resistente_agua", "efecto_mascara"}, set()),
        ("Primer", {"tipo_piel", "cobertura", "spf_declarado"}, {"tono_color", "tipo_accesorio"}, set()),
    ),
)
def test_makeup_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("beauty_makeup", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("beauty_makeup", product_type) == axes


def test_skin_care_sunscreen_fields_are_exclusive_and_has_no_axes():
    sunscreen = _applicable("beauty_skin_care", "Protector solar")
    cream = _applicable("beauty_skin_care", "Crema / Hidratante")
    solar_fields = {"spf_declarado", "formato_solar", "resistente_agua"}
    assert solar_fields <= sunscreen
    assert solar_fields.isdisjoint(cream)
    assert _axis_keys("beauty_skin_care", "Protector solar") == set()


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Shampoo", {"tipo_cabello", "tipo_cuero_cabelludo", "efecto_beneficio", "volumen_ml", "aroma", "ingredientes_destacados", "ingredientes"}, {"contenido_neto", "unidad", "nivel_fijacion", "tono_color", "tipo_accesorio", "material", "termico", "diametro_mm"}, {"volumen_ml"}),
        ("Acondicionador", {"tipo_cabello", "tipo_cuero_cabelludo", "efecto_beneficio", "volumen_ml", "aroma", "ingredientes_destacados", "ingredientes"}, {"contenido_neto", "unidad", "nivel_fijacion", "tono_color", "tipo_accesorio"}, {"volumen_ml"}),
        ("Gel / Cera / Pomada", {"nivel_fijacion"}, {"tono_color", "tipo_accesorio"}, set()),
        ("Spray fijador", {"nivel_fijacion"}, {"tono_color", "tipo_accesorio"}, set()),
        ("Tratamiento sin enjuague", {"sin_enjuague"}, {"nivel_fijacion", "tono_color"}, set()),
        ("Tinte / Coloración", {"tono_color", "codigo_tono", "tipo_coloracion"}, {"nivel_fijacion", "tipo_accesorio"}, {"tono_color"}),
        ("Cepillo / Peine", {"tipo_accesorio", "material", "termico"}, {"tipo_cabello", "tipo_cuero_cabelludo", "efecto_beneficio", "contenido_neto", "unidad", "volumen_ml", "aroma", "ingredientes_destacados", "ingredientes", "nivel_fijacion", "tono_color"}, set()),
    ),
)
def test_hair_care_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("beauty_hair_care", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("beauty_hair_care", product_type) == axes


def test_round_brush_diameter_uses_existing_single_trigger_condition():
    round_brush = _applicable("beauty_hair_care", "Cepillo / Peine", tipo_accesorio="Cepillo redondo")
    flat_brush = _applicable("beauty_hair_care", "Cepillo / Peine", tipo_accesorio="Cepillo plano")
    assert "diametro_mm" in round_brush
    assert "diametro_mm" not in flat_brush


def test_hair_variant_eligibility_switches_without_cross_type_leakage():
    assert _axis_keys("beauty_hair_care", "Shampoo") == {"volumen_ml"}
    assert _axis_keys("beauty_hair_care", "Cepillo / Peine") == set()
    assert _axis_keys("beauty_hair_care", "Tinte / Coloración") == {"tono_color"}
    assert _axis_keys("beauty_hair_care", "Shampoo") == {"volumen_ml"}


def test_hair_volume_uses_one_scalar_and_public_specification_contract():
    template = PRODUCT_TEMPLATES["beauty_hair_care"]
    fields = _fields("beauty_hair_care")
    assert fields["volumen_ml"].type == "decimal"
    assert fields["volumen_ml"].unit == "ml"
    assert fields["volumen_ml"].min == Decimal("0.01")
    assert "attributes.volumen_ml" in validate_attributes(
        template,
        {"tipo_producto": "Shampoo", "volumen_ml": "0"},
        final=True,
    )
    assert validate_attributes(
        template,
        {"tipo_producto": "Shampoo", "volumen_ml": "0.01"},
        final=True,
    ) == {}
    assert validate_attributes(
        template,
        {"tipo_producto": "Shampoo", "volumen_ml": "350"},
        final=True,
    ) == {}
    shampoo = _applicable("beauty_hair_care", "Shampoo")
    assert "volumen_ml" in shampoo
    assert {"contenido_neto", "unidad"}.isdisjoint(shampoo)
    items = _public_items(
        "BEAUTY_HAIR_CARE",
        {"tipo_producto": "Shampoo", "volumen_ml": "350"},
    )
    assert items["volumen_ml"].value == "350 ml"


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Eau de Parfum", {"volumen_ml", "recargable"}, {"peso_g", "cantidad_piezas"}, {"volumen_ml"}),
        ("Perfume en aceite", {"volumen_ml", "recargable"}, {"peso_g", "cantidad_piezas"}, {"volumen_ml"}),
        ("Perfume sólido", {"peso_g"}, {"volumen_ml", "recargable", "cantidad_piezas"}, set()),
        ("Set de fragancias", {"cantidad_piezas"}, {"volumen_ml", "recargable", "peso_g"}, set()),
    ),
)
def test_fragrance_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("beauty_fragrances", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("beauty_fragrances", product_type) == axes


def test_beauty_field_types_icons_units_and_quick_options():
    makeup = _fields("beauty_makeup")
    skin = _fields("beauty_skin_care")
    hair = _fields("beauty_hair_care")
    fragrance = _fields("beauty_fragrances")
    assert makeup["tono_color"].type == "variant_attribute"
    assert skin["ingredientes_destacados"].type == "chips"
    assert hair["tono_color"].type == "variant_attribute"
    assert hair["volumen_ml"].type == "decimal"
    assert hair["volumen_ml"].unit == "ml"
    assert fragrance["familia_olfativa"].type == "multiselect"
    assert fragrance["volumen_ml"].unit == "ml"
    assert fragrance["peso_g"].unit == "g"
    assert fragrance["volumen_ml"].quick_options == ("10", "30", "50", "75", "100", "150", "200")
    for template_key, keys in {
        "beauty_personal_hygiene": {"tipo_producto", "tipo_piel", "aroma", "contenido_neto"},
        "beauty_makeup": {"tipo_producto", "tono_color", "spf_declarado"},
        "beauty_skin_care": {"tipo_producto", "ingredientes_destacados", "resistente_agua"},
        "beauty_hair_care": {"tipo_producto", "tipo_cabello", "tono_color"},
        "beauty_fragrances": {"tipo_producto", "familia_olfativa", "volumen_ml"},
    }.items():
        fields = _fields(template_key)
        assert all(fields[key].icon for key in keys)


def test_fragrance_note_help_text_is_seller_facing_and_canonical():
    fields = _fields("beauty_fragrances")
    assert fields["notas_salida"].help == (
        "Primer aroma al aplicar, por ejemplo bergamota, limón o mandarina."
    )
    assert fields["notas_corazon"].help == (
        "Aroma principal de la fragancia, por ejemplo rosa, jazmín o lavanda."
    )
    assert fields["notas_fondo"].help == (
        "Aroma que permanece más tiempo, por ejemplo vainilla, madera o ámbar."
    )
    assert all(
        fields[key].type == "chips"
        for key in ("notas_salida", "notas_corazon", "notas_fondo")
    )
