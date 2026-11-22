from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES, condition_applies, get_product_template_for_category_code,
    template_key_for_category_code, validate_attributes, validate_template_registry,
    variant_axes_for_attributes,
)
from app.commands.marketplace_policy import INITIAL_CATEGORY_RATES
from app.commands.seed import _PRODUCT_CATEGORY_TREE
from app.models import Category
from app.models.enums import SellerCommissionType
from app.services.marketplace_policy import resolve_marketplace_commission
from app.services.partner_product_categories import LEGACY_NEW_LISTING_CATEGORY_CODES
from app.services.product_specifications import build_product_specification_presentation, resolve_product_template


CANONICAL_BABIES = {
    "BABIES_TOYS_DEVELOPMENT": ("babies_toys_development", "Juguetes y desarrollo", "juguetes-y-desarrollo", 1, "10.00"),
    "BABIES_CLOTHING_FOOTWEAR": ("babies_clothing_footwear", "Ropa y calzado", "ropa-y-calzado", 2, "12.00"),
    "BABIES_HYGIENE_CARE": ("babies_hygiene_care", "Higiene y cuidado", "higiene-y-cuidado", 3, "10.00"),
    "BABIES_FEEDING": ("babies_feeding", "Alimentación y lactancia", "alimentacion-y-lactancia", 4, "10.00"),
    "BABIES_TRAVEL_SAFETY": ("babies_travel_safety", "Paseo y seguridad", "paseo-y-seguridad", 5, "10.00"),
}
LEGACY_BABIES = {"BABIES_TOYS": "babies_toys", "BABIES_CLOTHING": "babies_clothing", "BABIES_CARE": "babies_care"}


def _fields(key): return {field.key: field for field in PRODUCT_TEMPLATES[key].fields}


def _applicable(key, product_type, **values):
    attrs = {"tipo_producto": product_type, **values}
    return {field.key for field in PRODUCT_TEMPLATES[key].fields if condition_applies(field.condition, attrs)}


def _axes(key, product_type):
    return {axis.key for axis in variant_axes_for_attributes(PRODUCT_TEMPLATES[key], {"tipo_producto": product_type})}


def _public_items(code, attributes):
    class Row:
        category_name = "Bebés y niños"; product_brand = "Marca"; product_model_number = "Modelo"
        manufacturer_barcode = None; weight_grams = None; length_mm = None; width_mm = None; height_mm = None
        def __init__(self): self.category_code = code; self.variant_attributes = attributes
    result = build_product_specification_presentation(Row())
    return {item.key: item for section in result.sections for item in section.items}


def test_babies_registry_taxonomy_rates_and_legacy_contract():
    validate_template_registry()
    parent, children = next(item for item in _PRODUCT_CATEGORY_TREE if item[0]["code"] == "BABIES_KIDS")
    assert parent["code"] == "BABIES_KIDS"
    visible = [child for child in children if child["code"] not in LEGACY_NEW_LISTING_CATEGORY_CODES]
    assert [child["code"] for child in visible] == list(CANONICAL_BABIES)
    seeded = {child["code"]: child for child in children}
    for code, (key, name, slug, order, rate) in CANONICAL_BABIES.items():
        assert seeded[code] == {"code": code, "name": name, "slug": slug, "sort_order": order}
        assert template_key_for_category_code(code) == key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[key]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal(rate)
    assert set(LEGACY_BABIES) <= LEGACY_NEW_LISTING_CATEGORY_CODES
    for code, key in LEGACY_BABIES.items():
        assert template_key_for_category_code(code) == key
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[key]


def test_toys_exact_types_conditions_validation_and_color_axis():
    fields = _fields("babies_toys_development")
    assert fields["tipo_producto"].options == (
        "Sonajero / Mordedor", "Juguete sensorial", "Peluche", "Bloques / Construcción", "Encaje / Clasificación", "Juguete musical", "Gimnasio / Manta de actividades", "Juguete de baño", "Libro infantil / sensorial", "Rompecabezas", "Muñeco / Figura", "Vehículo de juguete", "Correpasillos / Montable", "Otro",
    )
    assert {"fuente_energia", "sonido", "luces"} <= _applicable("babies_toys_development", "Juguete musical")
    assert {"largo_cm", "ancho_cm", "alto_cm"} <= _applicable("babies_toys_development", "Gimnasio / Manta de actividades")
    assert "fuente_energia" not in _applicable("babies_toys_development", "Peluche")
    assert _axes("babies_toys_development", "Peluche") == {"color_principal"}
    template = PRODUCT_TEMPLATES["babies_toys_development"]
    assert validate_attributes(template, {"tipo_producto": "Peluche", "edad_minima_meses": 0, "cantidad_piezas": 1}, final=True) == {}
    assert "attributes.cantidad_piezas" in validate_attributes(template, {"tipo_producto": "Peluche", "cantidad_piezas": 0}, final=True)


def test_clothing_footwear_reuses_fashion_contract_and_only_two_axes():
    fields = _fields("babies_clothing_footwear")
    assert fields["talla"].required is True
    assert fields["talla"].quick_options == (
        "Prematuro", "RN", "0–3M", "3–6M", "6–9M", "9–12M",
        "12–18M", "18–24M", "2T", "3T", "4T", "5T",
    )
    assert fields["cuidados"].type == "multiselect"
    assert _axes("babies_clothing_footwear", "Body / Onesie") == {"color_principal", "talla"}
    footwear = _applicable("babies_clothing_footwear", "Zapatos")
    clothing = _applicable("babies_clothing_footwear", "Body / Onesie")
    shoe_fields = {"longitud_pie_cm", "ancho_horma", "material_exterior", "material_suela", "tipo_cierre"}
    assert shoe_fields <= footwear
    assert shoe_fields.isdisjoint(clothing)


@pytest.mark.parametrize(("product_type", "included", "excluded", "axes"), (
    ("Pañal desechable", {"talla_panal", "peso_min_kg", "peso_max_kg", "cantidad_paquete", "tipo_cierre"}, {"volumen_ml", "peso_g", "material_principal"}, {"talla_panal", "cantidad_paquete"}),
    ("Toallitas húmedas", {"cantidad_paquete", "aroma"}, {"talla_panal", "volumen_ml", "peso_g", "material_principal"}, {"cantidad_paquete"}),
    ("Shampoo / Gel para bebé", {"volumen_ml", "aroma", "ingredientes"}, {"talla_panal", "peso_g", "material_principal"}, {"volumen_ml"}),
    ("Aceite / Loción para bebé", {"volumen_ml", "aroma", "ingredientes"}, {"talla_panal", "peso_g", "material_principal"}, {"volumen_ml"}),
    ("Crema para pañal", {"peso_g", "aroma", "ingredientes"}, {"talla_panal", "volumen_ml", "material_principal"}, {"peso_g"}),
    ("Bañera", {"material_principal", "lavable", "largo_cm", "ancho_cm", "alto_cm"}, {"talla_panal", "volumen_ml", "peso_g", "ingredientes", "aroma"}, set()),
    ("Cepillo / Peine para bebé", {"material_principal", "cantidad_piezas"}, {"volumen_ml", "peso_g", "ingredientes", "aroma"}, set()),
))
def test_hygiene_formulation_and_physical_separation(product_type, included, excluded, axes):
    applicable = _applicable("babies_hygiene_care", product_type)
    assert included <= applicable; assert excluded.isdisjoint(applicable); assert _axes("babies_hygiene_care", product_type) == axes


def test_hygiene_positive_amount_contracts():
    template = PRODUCT_TEMPLATES["babies_hygiene_care"]
    assert _fields("babies_hygiene_care")["talla_panal"].quick_options == (
        "RN", "P", "M", "G", "XG", "XXG",
    )
    for product_type, key in (("Shampoo / Gel para bebé", "volumen_ml"), ("Crema para pañal", "peso_g")):
        assert f"attributes.{key}" in validate_attributes(template, {"tipo_producto": product_type, key: "0"}, final=True)
        assert validate_attributes(template, {"tipo_producto": product_type, key: "0.01"}, final=True) == {}


@pytest.mark.parametrize(("product_type", "included", "excluded", "axes"), (
    ("Biberón / Botella", {"capacidad_ml", "tipo_boquilla", "anti_colico", "apto_lavavajillas"}, {"flujo_tetina", "fuente_energia", "peso_max_kg"}, {"capacidad_ml", "color_principal"}),
    ("Vaso entrenador", {"capacidad_ml", "tipo_boquilla"}, {"anti_colico", "flujo_tetina", "fuente_energia"}, {"capacidad_ml", "color_principal"}),
    ("Tetina", {"flujo_tetina", "material_tetina", "cantidad_paquete"}, {"capacidad_ml", "fuente_energia"}, {"flujo_tetina"}),
    ("Silla alta / Booster", {"peso_max_kg", "plegable", "bandeja_removible", "arnes_puntos", "ajustable"}, {"capacidad_ml", "flujo_tetina", "fuente_energia"}, set()),
))
def test_feeding_conditions_and_axes(product_type, included, excluded, axes):
    applicable = _applicable("babies_feeding", product_type)
    assert included <= applicable; assert excluded.isdisjoint(applicable); assert _axes("babies_feeding", product_type) == axes


def test_feeding_extractor_voltage_is_source_driven_and_no_food_types_exist():
    fields = _fields("babies_feeding")
    assert fields["flujo_tetina"].quick_options == (
        "Lento", "Medio", "Rápido", "Variable", "Otro",
    )
    assert not {"Fórmula infantil", "Leche en polvo", "Papilla", "Cereal infantil"} & set(fields["tipo_producto"].options)
    assert "voltaje_v" not in _applicable("babies_feeding", "Extractor de leche", fuente_energia="Manual")
    for source in ("Batería recargable", "Red eléctrica"):
        assert "voltaje_v" in _applicable("babies_feeding", "Extractor de leche", fuente_energia=source)
    changed = _applicable("babies_feeding", "Biberón / Botella")
    assert {"fuente_energia", "voltaje_v"}.isdisjoint(changed)


@pytest.mark.parametrize(("product_type", "included", "excluded"), (
    ("Coche / Stroller", {"numero_ruedas", "posiciones_reclinado", "asiento_reversible", "arnes_puntos", "freno", "largo_plegado_cm"}, {"orientacion", "tipo_portabebe", "ancho_min_cm"}),
    ("Silla para auto", {"orientacion", "tipo_instalacion", "arnes_puntos", "altura_min_cm", "altura_max_cm"}, {"numero_ruedas", "tipo_portabebe", "ancho_min_cm"}),
    ("Portabebé / Mochila", {"tipo_portabebe", "posiciones_transporte", "ajustable"}, {"orientacion", "numero_ruedas", "ancho_min_cm"}),
    ("Barrera de seguridad", {"ancho_min_cm", "ancho_max_cm", "tipo_montaje", "altura_cm"}, {"orientacion", "numero_ruedas", "largo_cm"}),
    ("Cuna de viaje", {"largo_cm", "ancho_cm", "alto_cm", "peso_max_kg", "plegable"}, {"orientacion", "numero_ruedas", "ancho_min_cm"}),
))
def test_travel_declared_specifications_and_color_only_axis(product_type, included, excluded):
    applicable = _applicable("babies_travel_safety", product_type)
    assert included <= applicable; assert excluded.isdisjoint(applicable)
    assert _axes("babies_travel_safety", product_type) == {"color_principal"}


@pytest.mark.parametrize(("code", "attributes", "expected"), (
    ("BABIES_TOYS_DEVELOPMENT", {"tipo_producto": "Peluche", "edad_minima_meses": 0, "material_principal": "Tela", "habilidades_desarrollo": ["Sensorial"]}, {"edad_minima_meses", "material_principal", "habilidades_desarrollo"}),
    ("BABIES_CLOTHING_FOOTWEAR", {"tipo_producto": "Body / Onesie", "talla": "6–9M", "color_principal": "Azul", "material_principal": "Algodón"}, {"talla", "color_principal", "material_principal"}),
    ("BABIES_HYGIENE_CARE", {"tipo_producto": "Pañal desechable", "talla_panal": "M", "cantidad_paquete": 30}, {"talla_panal", "cantidad_paquete"}),
    ("BABIES_HYGIENE_CARE", {"tipo_producto": "Shampoo / Gel para bebé", "volumen_ml": "250"}, {"volumen_ml"}),
    ("BABIES_FEEDING", {"tipo_producto": "Biberón / Botella", "capacidad_ml": "240"}, {"capacidad_ml"}),
    ("BABIES_FEEDING", {"tipo_producto": "Tetina", "flujo_tetina": "Lento"}, {"flujo_tetina"}),
    ("BABIES_TRAVEL_SAFETY", {"tipo_producto": "Silla para auto", "orientacion": "Contramarcha", "tipo_instalacion": "ISOFIX / LATCH", "peso_min_kg": "2", "peso_max_kg": "18"}, {"orientacion", "tipo_instalacion", "peso_min_kg", "peso_max_kg"}),
    ("BABIES_TRAVEL_SAFETY", {"tipo_producto": "Coche / Stroller", "peso_max_kg": "22", "arnes_puntos": "5 puntos", "plegable": True, "largo_plegado_cm": "65"}, {"peso_max_kg", "arnes_puntos", "plegable", "largo_plegado_cm"}),
))
def test_babies_public_specifications(code, attributes, expected):
    assert expected <= set(_public_items(code, attributes))


@pytest.mark.integration
def test_babies_commission_rules_resolve_expected_percentages(app, session):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    assert runner.invoke(args=["marketplace-policy", "bootstrap"]).exit_code == 0
    for code, (*_metadata, rate) in CANONICAL_BABIES.items():
        category = session.scalar(select(Category).where(Category.code == code))
        result = resolve_marketplace_commission(session, category_id=category.id, price="10.00")
        assert result.mode == SellerCommissionType.PERCENTAGE
        assert result.rate_percent == Decimal(rate)
        assert result.commission_amount == (Decimal("1.20") if rate == "12.00" else Decimal("1.00"))
