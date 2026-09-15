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
from app.services.partner_product_categories import (
    LEGACY_NEW_LISTING_CATEGORY_CODES,
)
from app.services.product_specifications import (
    build_product_specification_presentation,
    resolve_product_template,
)


CANONICAL_FASHION = {
    "FASHION_CLOTHING": ("fashion_clothing", "Ropa", "ropa", 1),
    "FASHION_FOOTWEAR": ("fashion_footwear", "Calzado", "calzado-moda", 2),
    "FASHION_BAGS_ACCESSORIES": (
        "fashion_bags_accessories",
        "Bolsos y accesorios",
        "bolsos-y-accesorios",
        3,
    ),
    "FASHION_JEWELRY_WATCHES": (
        "fashion_jewelry_watches",
        "Joyería, bisutería y relojes",
        "joyeria-bisuteria-y-relojes",
        4,
    ),
}

LEGACY_FASHION = {
    "FASHION_MEN": "fashion_men",
    "FASHION_WOMEN": "fashion_women",
    "FASHION_SHOES": "fashion_shoes",
    "FASHION_ACCESSORIES": "fashion_accessories",
}


def _fields(template_key: str):
    return {field.key: field for field in PRODUCT_TEMPLATES[template_key].fields}


def _applicable(template_key: str, product_type: str) -> set[str]:
    return {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(
            field.condition,
            {"tipo_producto": product_type},
        )
    }


def _axis_keys(template_key: str, product_type: str) -> set[str]:
    return {
        axis.key
        for axis in variant_axes_for_attributes(
            PRODUCT_TEMPLATES[template_key],
            {"tipo_producto": product_type},
        )
    }


def _public_items(category_code: str, attributes: dict[str, object]):
    class Row:
        category_name = "Moda"
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


def test_fashion_registry_seed_and_commission_integrity():
    validate_template_registry()
    _parent, children = next(
        entry for entry in _PRODUCT_CATEGORY_TREE if entry[0]["code"] == "FASHION"
    )
    seeded = {child["code"]: child for child in children}

    assert set(CANONICAL_FASHION) <= set(seeded)
    for code, (template_key, name, slug, sort_order) in CANONICAL_FASHION.items():
        assert seeded[code] == {
            "code": code,
            "name": name,
            "slug": slug,
            "sort_order": sort_order,
        }
        assert template_key_for_category_code(code) == template_key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[
            template_key
        ]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal("12.00")


def test_legacy_fashion_registry_and_rates_remain_compatible():
    assert LEGACY_NEW_LISTING_CATEGORY_CODES == frozenset(LEGACY_FASHION)
    for code, template_key in LEGACY_FASHION.items():
        assert template_key_for_category_code(code) == template_key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[
            template_key
        ]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal("12.00")

    common_keys = {
        "color_principal",
        "material",
        "tipo",
        "genero",
        "talla",
        "sistema_talla",
        "tabla_tallas",
        "cuidados",
    }
    for key in ("fashion_men", "fashion_women", "fashion_accessories"):
        assert set(_fields(key)) == common_keys
        assert [axis.key for axis in PRODUCT_TEMPLATES[key].variant_axes] == [
            "color",
            "talla",
        ]
    assert set(_fields("fashion_shoes")) == {
        "color_principal",
        "material",
        "tipo",
        "talla",
        "sistema_talla",
        "exterior",
        "suela",
    }


def test_canonical_primary_selectors_and_core_contracts():
    clothing = _fields("fashion_clothing")
    footwear = _fields("fashion_footwear")
    bags = _fields("fashion_bags_accessories")
    jewelry = _fields("fashion_jewelry_watches")

    for fields in (clothing, footwear, bags, jewelry):
        assert fields["tipo_producto"].type == "select"
        assert fields["tipo_producto"].required is True
        assert fields["genero"].options == ("Hombre", "Mujer", "Unisex")
        assert fields["color_principal"].type == "color"

    assert clothing["talla"].type == "variant_attribute"
    assert clothing["talla"].required is True
    assert footwear["talla"].type == "variant_attribute"
    assert footwear["talla"].required is True
    assert clothing["cuidados"].type == "multiselect"
    assert clothing["cuidados"].options == (
        "Lavar a mano",
        "Lavado a máquina",
        "Lavar con agua fría",
        "Lavar con colores similares",
        "No usar blanqueador",
        "No usar secadora",
        "Secar a la sombra",
        "Secar en plano",
        "Planchar a baja temperatura",
        "No planchar",
        "Limpieza en seco",
        "No limpiar en seco",
    )


def test_canonical_product_type_options_are_exact():
    assert _fields("fashion_clothing")["tipo_producto"].options == (
        "Camiseta", "Camisa / Blusa", "Polo", "Suéter / Jersey",
        "Sudadera / Hoodie", "Chaqueta / Abrigo", "Chaleco", "Pantalón",
        "Jean", "Short", "Falda", "Vestido", "Traje / Conjunto", "Leggings",
        "Conjunto deportivo", "Ropa interior", "Medias / Calcetines", "Pijama",
        "Traje de baño", "Otro",
    )
    assert _fields("fashion_footwear")["tipo_producto"].options == (
        "Sneakers / Tenis", "Calzado deportivo", "Zapatos casuales",
        "Zapatos formales", "Sandalias", "Botas / Botines", "Tacones",
        "Mocasines", "Pantuflas", "Otro",
    )
    assert _fields("fashion_bags_accessories")["tipo_producto"].options == (
        "Bolso", "Mochila", "Cartera / Billetera", "Cinturón",
        "Gorra / Sombrero", "Bufanda / Pañuelo", "Lentes de sol", "Guantes",
        "Corbata / Pajarita", "Accesorio para cabello", "Llavero", "Otro",
    )
    assert _fields("fashion_jewelry_watches")["tipo_producto"].options == (
        "Anillo", "Aretes", "Collar", "Cadena", "Pulsera", "Tobillera",
        "Dije / Colgante", "Broche", "Piercing", "Set de joyería",
        "Reloj analógico", "Reloj digital", "Reloj híbrido", "Smartwatch", "Otro",
    )


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Camiseta", {"manga", "cuello_escote"}, {"corte", "tiro", "capucha"}),
        ("Pantalón", {"tipo_cierre", "corte", "tiro", "entrepierna_cm", "bolsillos"}, {"manga", "forro"}),
        ("Jean", {"tipo_cierre", "corte", "tiro", "entrepierna_cm", "bolsillos"}, {"manga", "forro"}),
        ("Vestido", {"manga", "cuello_escote", "tipo_cierre", "largo_prenda", "forro"}, {"corte", "capucha"}),
        ("Sudadera / Hoodie", {"manga", "cuello_escote", "capucha"}, {"corte", "tipo_cierre"}),
        ("Chaqueta / Abrigo", {"manga", "cuello_escote", "tipo_cierre", "capucha", "impermeable", "bolsillos", "forro"}, {"corte", "tiro"}),
    ),
)
def test_clothing_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("fashion_clothing", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


def test_clothing_multiselect_validation_and_composition_quick_options():
    template = PRODUCT_TEMPLATES["fashion_clothing"]
    assert validate_attributes(
        template,
        {
            "tipo_producto": "Camiseta",
            "talla": "M",
            "cuidados": ["Lavado a máquina", "No usar secadora"],
        },
        final=True,
    ) == {}
    assert "attributes.cuidados" in validate_attributes(
        template,
        {
            "tipo_producto": "Camiseta",
            "talla": "M",
            "cuidados": ["Instrucción inventada"],
        },
        final=True,
    )
    assert _fields("fashion_clothing")["composicion"].quick_options == (
        "Algodón",
        "Poliéster",
        "Elastano",
        "Lana",
        "Lino",
        "Viscosa",
        "Nylon",
    )


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Sneakers / Tenis", {"talla", "sistema_talla", "longitud_pie_cm", "material_exterior", "tipo_cierre", "altura_plataforma_cm"}, {"uso_deportivo", "altura_tacon_cm", "altura_cana_cm"}),
        ("Calzado deportivo", {"talla", "sistema_talla", "longitud_pie_cm", "material_suela", "uso_deportivo", "impermeable"}, {"altura_tacon_cm", "altura_cana_cm"}),
        ("Botas / Botines", {"altura_plataforma_cm", "altura_cana_cm", "impermeable"}, {"uso_deportivo", "altura_tacon_cm"}),
        ("Tacones", {"altura_tacon_cm", "altura_plataforma_cm"}, {"altura_cana_cm", "uso_deportivo", "impermeable"}),
    ),
)
def test_footwear_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("fashion_footwear", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Bolso", {"alto_cm", "ancho_cm", "profundidad_cm", "tipo_cierre", "tipo_correa", "compartimento_laptop", "tamano_laptop_pulgadas", "capacidad_litros"}, {"talla", "proteccion_rfid", "circunferencia_cm"}),
        ("Mochila", {"alto_cm", "ancho_cm", "profundidad_cm", "numero_compartimentos", "compartimento_laptop", "resistente_agua"}, {"talla", "proteccion_rfid", "forma_montura"}),
        ("Cinturón", {"talla", "longitud_cm", "ancho_cm", "material_hebilla", "tipo_hebilla"}, {"alto_cm", "compartimento_laptop", "forma_montura"}),
        ("Gorra / Sombrero", {"talla", "circunferencia_cm", "ajustable"}, {"alto_cm", "longitud_cm", "forma_montura"}),
        ("Lentes de sol", {"forma_montura", "material_montura", "material_lente", "color_lente", "proteccion_uv", "polarizado"}, {"talla", "alto_cm", "pantalla_tactil"}),
        ("Guantes", {"talla", "pantalla_tactil"}, {"alto_cm", "forma_montura", "circunferencia_cm"}),
    ),
)
def test_bags_accessories_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("fashion_bags_accessories", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Anillo", {"material_metal", "ley_metal", "piedra_principal", "talla", "diametro_interno_mm"}, {"longitud_cm", "tipo_arete", "zona_piercing"}),
        ("Aretes", {"material_metal", "tipo_arete", "tipo_cierre_joyeria", "cantidad_piezas", "largo_mm"}, {"talla", "longitud_cm", "zona_piercing"}),
        ("Cadena", {"material_metal", "ley_metal", "longitud_cm", "tipo_cierre_joyeria", "grosor_mm"}, {"talla", "tipo_arete", "zona_piercing"}),
        ("Pulsera", {"material_metal", "longitud_cm", "tipo_cierre_joyeria", "grosor_mm"}, {"talla", "tipo_arete", "numero_piezas"}),
        ("Piercing", {"material_metal", "zona_piercing", "grosor_mm", "largo_mm"}, {"talla", "longitud_cm", "cantidad_piezas"}),
        ("Set de joyería", {"material_metal", "ley_metal", "piedra_principal", "numero_piezas"}, {"talla", "longitud_cm", "tipo_arete"}),
    ),
)
def test_jewelry_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("fashion_jewelry_watches", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    "product_type",
    ("Reloj analógico", "Reloj digital", "Reloj híbrido", "Smartwatch"),
)
def test_watch_common_fields(product_type):
    applicable = _applicable("fashion_jewelry_watches", product_type)
    assert {
        "material_caja",
        "material_correa",
        "material_cristal",
        "movimiento",
        "forma_caja",
        "diametro_caja_mm",
        "grosor_caja_mm",
        "ancho_correa_mm",
        "longitud_correa_mm",
        "resistencia_agua",
        "cronografo",
        "calendario",
        "funciones_reloj",
    } <= applicable
    assert {"material_metal", "ley_metal", "piedra_principal"}.isdisjoint(applicable)


def test_smartwatch_extra_fields_are_exclusive():
    smartwatch = _applicable("fashion_jewelry_watches", "Smartwatch")
    analog = _applicable("fashion_jewelry_watches", "Reloj analógico")
    extras = {
        "compatibilidad_so",
        "bluetooth_version",
        "wifi",
        "gps",
        "nfc",
        "autonomia_horas",
        "pantalla_pulgadas",
    }
    assert extras <= smartwatch
    assert extras.isdisjoint(analog)


@pytest.mark.parametrize(
    ("template_key", "product_type", "expected"),
    (
        ("fashion_clothing", "Camiseta", {"color", "talla"}),
        ("fashion_footwear", "Botas / Botines", {"color", "talla"}),
        ("fashion_bags_accessories", "Bolso", {"color"}),
        ("fashion_bags_accessories", "Cinturón", {"color", "talla"}),
        ("fashion_bags_accessories", "Gorra / Sombrero", {"color", "talla"}),
        ("fashion_bags_accessories", "Guantes", {"color", "talla"}),
        ("fashion_bags_accessories", "Lentes de sol", {"color"}),
        ("fashion_jewelry_watches", "Anillo", {"color", "talla"}),
        ("fashion_jewelry_watches", "Aretes", {"color"}),
        ("fashion_jewelry_watches", "Smartwatch", {"color"}),
    ),
)
def test_canonical_fashion_variant_axes(template_key, product_type, expected):
    assert _axis_keys(template_key, product_type) == expected


def test_canonical_fashion_icons_and_quick_options_are_present():
    required_icon_keys = {
        "fashion_clothing": {"tipo_producto", "genero", "talla", "material_principal", "composicion", "cuidados", "tipo_cierre", "impermeable"},
        "fashion_footwear": {"tipo_producto", "talla", "longitud_pie_cm", "material_exterior", "tipo_cierre", "impermeable"},
        "fashion_bags_accessories": {"tipo_producto", "talla", "material", "forma_montura", "proteccion_uv"},
        "fashion_jewelry_watches": {"tipo_producto", "material_metal", "peso_g", "movimiento", "resistencia_agua", "compatibilidad_so"},
    }
    for template_key, keys in required_icon_keys.items():
        fields = _fields(template_key)
        assert all(fields[key].icon for key in keys)

    assert _fields("fashion_bags_accessories")[
        "tamano_laptop_pulgadas"
    ].quick_options == ("13", "14", "15.6", "16", "17", "17.3")
    assert _fields("fashion_jewelry_watches")["talla"].quick_options
    assert _fields("fashion_jewelry_watches")["diametro_caja_mm"].quick_options


@pytest.mark.parametrize(
    ("category_code", "attributes", "included", "excluded"),
    (
        ("FASHION_CLOTHING", {"tipo_producto": "Camiseta", "talla": "M", "manga": "Corta", "corte": "Slim"}, {"tipo_producto", "talla", "manga"}, {"corte"}),
        ("FASHION_FOOTWEAR", {"tipo_producto": "Botas / Botines", "talla": "39", "altura_cana_cm": "18", "uso_deportivo": ["Running"]}, {"tipo_producto", "talla", "altura_cana_cm"}, {"uso_deportivo"}),
        ("FASHION_BAGS_ACCESSORIES", {"tipo_producto": "Lentes de sol", "forma_montura": "Aviador", "polarizado": False, "talla": "M"}, {"tipo_producto", "forma_montura", "polarizado"}, {"talla"}),
        ("FASHION_JEWELRY_WATCHES", {"tipo_producto": "Smartwatch", "movimiento": "Digital", "gps": True, "material_metal": "Oro"}, {"tipo_producto", "movimiento", "gps"}, {"material_metal"}),
    ),
)
def test_public_specifications_resolve_conditions(
    category_code,
    attributes,
    included,
    excluded,
):
    items = _public_items(category_code, attributes)
    assert included <= set(items)
    assert excluded.isdisjoint(items)
