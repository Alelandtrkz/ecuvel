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


CANONICAL_HOME = {
    "HOME_DECOR_LIGHTING": ("home_decor_lighting", "Decoración e iluminación", "decoracion-e-iluminacion", 1, "12.00"),
    "HOME_KITCHEN_DINING": ("home_kitchen_dining", "Cocina y comedor", "cocina-y-comedor", 2, "10.00"),
    "HOME_CLEANING_SUPPLIES": ("home_cleaning_supplies", "Limpieza del hogar", "limpieza-del-hogar", 3, "10.00"),
    "HOME_STORAGE_ORGANIZATION": ("home_storage_organization", "Organización y almacenamiento", "organizacion-y-almacenamiento", 4, "10.00"),
    "HOME_TEXTILES": ("home_textiles", "Textiles del hogar", "textiles-del-hogar", 5, "12.00"),
    "HOME_FURNITURE": ("home_furniture", "Muebles", "muebles", 6, "10.00"),
}
LEGACY_HOME = {
    "HOME_DECORATION": ("home_decoration", "12.00"),
    "HOME_KITCHEN_TOOLS": ("home_kitchen_tools", "10.00"),
    "HOME_CLEANING": ("home_cleaning", "10.00"),
}


def _fields(template_key: str):
    return {field.key: field for field in PRODUCT_TEMPLATES[template_key].fields}


def _applicable(template_key: str, product_type: str) -> set[str]:
    return {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(field.condition, {"tipo_producto": product_type})
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
        category_name = "Hogar y cocina"
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


def test_home_registry_seed_commission_and_public_resolution_integrity():
    validate_template_registry()
    _parent, children = next(
        entry for entry in _PRODUCT_CATEGORY_TREE if entry[0]["code"] == "HOME_KITCHEN"
    )
    seeded = {child["code"]: child for child in children}
    for code, (template_key, name, slug, sort_order, rate) in CANONICAL_HOME.items():
        assert seeded[code] == {"code": code, "name": name, "slug": slug, "sort_order": sort_order}
        assert template_key_for_category_code(code) == template_key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[template_key]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal(rate)
        product_type = _fields(template_key)["tipo_producto"].options[0]
        assert "tipo_producto" in _public_items(
            code, {"tipo_producto": product_type}
        )


def test_legacy_home_contracts_remain_resolvable_unchanged_and_excluded():
    assert frozenset({
        "FASHION_MEN",
        "FASHION_WOMEN",
        "FASHION_SHOES",
        "FASHION_ACCESSORIES",
        *LEGACY_HOME,
    }) <= LEGACY_NEW_LISTING_CATEGORY_CODES
    expected_fields = {"color_principal", "material", "tipo", "habitacion", "dimensiones", "cuidados"}
    for code, (template_key, rate) in LEGACY_HOME.items():
        assert template_key_for_category_code(code) == template_key
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal(rate)
        assert set(_fields(template_key)) == expected_fields


def test_canonical_home_primary_selectors_and_structured_fields():
    for template_key, *_ in CANONICAL_HOME.values():
        fields = _fields(template_key)
        assert fields["tipo_producto"].type == "select"
        assert fields["tipo_producto"].required is True
        assert fields["color_principal"].type == "color"
        assert all(field.key != "material" for field in fields.values())

    textiles = _fields("home_textiles")
    assert textiles["composicion"].type == "chips"
    assert textiles["cuidados"].type == "multiselect"
    assert textiles["tamano_textil"].type == "variant_attribute"
    assert textiles["cuidados"].options == (
        "Lavar a mano", "Lavado a máquina", "Lavar con agua fría",
        "Lavar con colores similares", "No usar blanqueador", "No usar secadora",
        "Secar a la sombra", "Secar en plano", "Planchar a baja temperatura",
        "No planchar", "Limpieza en seco", "No limpiar en seco",
    )


def test_canonical_home_product_type_options_are_exact():
    assert _fields("home_decor_lighting")["tipo_producto"].options == (
        "Cuadro / Lámina", "Decoración de pared", "Espejo", "Reloj decorativo",
        "Florero / Jarrón", "Figura / Adorno", "Portavelas / Candelabro", "Vela",
        "Planta artificial", "Lámpara decorativa", "Guirnalda / Luz decorativa", "Otro",
    )
    assert _fields("home_kitchen_dining")["tipo_producto"].options == (
        "Olla / Cacerola", "Sartén", "Wok", "Molde / Bandeja para horno", "Cuchillo",
        "Tabla de cortar", "Utensilio de cocina", "Colador / Escurridor", "Rallador / Pelador",
        "Recipiente para alimentos", "Botella / Termo", "Taza / Vaso", "Plato / Bowl",
        "Cubiertos", "Vajilla / Set de comedor", "Secaplatos / Organizador de cocina", "Otro",
    )
    assert _fields("home_cleaning_supplies")["tipo_producto"].options == (
        "Mopa / Trapeador", "Escoba", "Cepillo de limpieza", "Recogedor", "Balde",
        "Paño / Microfibra", "Esponja / Estropajo", "Plumero", "Limpiavidrios manual",
        "Guantes de limpieza", "Bolsas de basura", "Limpiador líquido", "Detergente",
        "Desinfectante", "Limpiador en polvo", "Otro",
    )
    assert _fields("home_storage_organization")["tipo_producto"].options == (
        "Caja / Contenedor", "Canasta", "Organizador de cajón", "Organizador de armario",
        "Organizador de joyería", "Zapatero", "Perchero", "Estante", "Perchas", "Ganchos",
        "Cesto para ropa", "Bolsa al vacío", "Organizador colgante", "Otro",
    )
    assert _fields("home_textiles")["tipo_producto"].options == (
        "Juego de sábanas", "Sábana", "Funda de almohada", "Almohada", "Edredón / Comforter",
        "Cobija / Manta", "Protector de colchón", "Toalla", "Alfombra", "Tapete de baño",
        "Cortina", "Cojín decorativo", "Mantel", "Servilleta de tela", "Otro",
    )
    assert _fields("home_furniture")["tipo_producto"].options == (
        "Silla", "Mesa", "Escritorio", "Sofá", "Sillón", "Cama / Base de cama", "Colchón",
        "Mesa de noche", "Cómoda", "Armario", "Gabinete", "Estantería / Librero",
        "Mueble para TV", "Banco", "Taburete", "Juego de comedor", "Otro",
    )


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Cuadro / Lámina", {"orientacion", "tipo_montaje", "enmarcado"}, {"con_marco", "mecanismo_reloj", "tipo_cera"}),
        ("Espejo", {"con_marco", "tipo_montaje"}, {"enmarcado", "orientacion", "mecanismo_reloj"}),
        ("Reloj decorativo", {"mecanismo_reloj", "alimentacion"}, {"tipo_cera", "voltaje_v"}),
        ("Vela", {"tipo_cera", "aroma", "duracion_horas"}, {"alimentacion", "tipo_planta"}),
        ("Lámpara decorativa", {"alimentacion", "voltaje_v", "potencia_w", "tipo_casquillo", "bombilla_incluida", "regulable"}, {"mecanismo_reloj", "cantidad_luces"}),
        ("Guirnalda / Luz decorativa", {"alimentacion", "longitud_m", "cantidad_luces", "uso_exterior"}, {"voltaje_v", "tipo_casquillo"}),
    ),
)
def test_decor_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("home_decor_lighting", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_decor_lighting", product_type) == {"color"}


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Olla / Cacerola", {"compatibilidad_coccion", "capacidad_l", "tapa_incluida"}, {"capacidad_ml", "tipo_cuchillo"}, {"color", "capacidad_l"}),
        ("Sartén", {"compatibilidad_coccion", "diametro_base_cm"}, {"capacidad_l", "capacidad_ml"}, {"color"}),
        ("Cuchillo", {"tipo_cuchillo", "material_hoja", "longitud_hoja_cm", "material_mango", "tipo_filo"}, {"capacidad_l", "capacidad_ml"}, {"color"}),
        ("Recipiente para alimentos", {"capacidad_ml", "hermetico", "apto_congelador", "apto_microondas"}, {"capacidad_l", "aislamiento_termico"}, {"color", "capacidad_ml"}),
        ("Botella / Termo", {"capacidad_ml", "aislamiento_termico", "horas_frio", "horas_caliente", "antiderrames"}, {"capacidad_l", "hermetico"}, {"color", "capacidad_ml"}),
        ("Vajilla / Set de comedor", {"cantidad_piezas", "numero_personas"}, {"capacidad_l", "capacidad_ml"}, {"color"}),
    ),
)
def test_kitchen_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("home_kitchen_dining", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_kitchen_dining", product_type) == axes
    fields = _fields("home_kitchen_dining")
    assert fields["capacidad_l"].unit == "L"
    assert fields["capacidad_ml"].unit == "ml"


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Mopa / Trapeador", {"color_principal", "superficie_recomendada", "longitud_mango_cm", "ancho_cabezal_cm"}, {"contenido_neto", "instrucciones_dilucion"}, {"color"}),
        ("Balde", {"color_principal", "capacidad_l"}, {"contenido_neto", "longitud_mango_cm"}, {"color"}),
        ("Bolsas de basura", {"capacidad_l", "cantidad_paquete"}, {"presentacion", "contenido_neto"}, {"color"}),
        ("Limpiador líquido", {"presentacion", "contenido_neto", "unidad", "superficie_uso", "instrucciones_dilucion", "advertencias"}, {"color_principal", "capacidad_l"}, set()),
        ("Detergente", {"presentacion", "contenido_neto", "concentrado"}, {"color_principal", "cantidad_paquete"}, set()),
    ),
)
def test_cleaning_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("home_cleaning_supplies", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_cleaning_supplies", product_type) == axes
    assert not {"registro_sanitario", "certificacion", "arcsa"} & set(_fields("home_cleaning_supplies"))


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Caja / Contenedor", {"capacidad_l", "tapa_incluida", "ruedas"}, {"capacidad_carga_kg", "numero_niveles"}),
        ("Organizador de cajón", {"numero_compartimentos"}, {"capacidad_l", "numero_niveles", "instalacion"}),
        ("Zapatero", {"capacidad_carga_kg", "numero_niveles"}, {"capacidad_l", "ruedas"}),
        ("Estante", {"capacidad_carga_kg", "numero_niveles", "instalacion", "ruedas"}, {"capacidad_l", "tapa_incluida"}),
        ("Cesto para ropa", {"capacidad_l", "ruedas"}, {"capacidad_carga_kg", "numero_compartimentos"}),
    ),
)
def test_storage_conditional_matrix(product_type, included, excluded):
    applicable = _applicable("home_storage_organization", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_storage_organization", product_type) == {"color"}


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Juego de sábanas", {"tamano_cama", "numero_hilos", "cantidad_piezas"}, {"tamano_textil", "material_relleno"}, {"color", "tamano_cama"}),
        ("Almohada", {"tamano_textil", "alto_cm", "material_relleno", "firmeza"}, {"tamano_cama", "numero_hilos"}, {"color", "tamano_textil"}),
        ("Toalla", {"tamano_textil", "gramaje_g_m2", "tipo_toalla"}, {"tamano_cama", "blackout"}, {"color", "tamano_textil"}),
        ("Alfombra", {"tamano_textil", "forma", "altura_pelo_mm", "base_antideslizante", "uso_ubicacion"}, {"tamano_cama", "blackout"}, {"color", "tamano_textil"}),
        ("Cortina", {"tamano_textil", "tipo_instalacion_cortina", "blackout", "translucidez", "cantidad_paneles"}, {"tamano_cama", "forma"}, {"color", "tamano_textil"}),
        ("Cojín decorativo", {"tamano_textil", "material_relleno", "tipo_cierre"}, {"tamano_cama", "firmeza"}, {"color", "tamano_textil"}),
    ),
)
def test_textiles_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("home_textiles", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_textiles", product_type) == axes


def test_textile_controlled_care_validation_and_quick_options():
    template = PRODUCT_TEMPLATES["home_textiles"]
    assert validate_attributes(template, {"tipo_producto": "Toalla", "cuidados": ["Lavado a máquina"]}, final=True) == {}
    assert "attributes.cuidados" in validate_attributes(template, {"tipo_producto": "Toalla", "cuidados": ["Inventado"]}, final=True)
    assert _fields("home_textiles")["composicion"].quick_options == ("Algodón", "Poliéster", "Microfibra", "Lino", "Lana", "Viscosa", "Bambú")


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Silla", {"numero_plazas", "material_tapizado", "material_relleno", "capacidad_max_kg"}, {"numero_puertas", "tamano_colchon"}, {"color"}),
        ("Mesa", {"forma", "numero_personas", "plegable"}, {"altura_ajustable", "numero_puertas"}, {"color"}),
        ("Escritorio", {"forma", "altura_ajustable", "plegable"}, {"numero_personas", "tamano_colchon"}, {"color"}),
        ("Sofá", {"numero_plazas", "material_tapizado", "capacidad_max_kg"}, {"numero_puertas", "forma"}, {"color"}),
        ("Armario", {"numero_puertas", "numero_cajones", "numero_estantes", "capacidad_max_kg"}, {"numero_plazas", "tamano_colchon"}, {"color"}),
        ("Cama / Base de cama", {"tamano_colchon_compatible", "material_estructura", "cabecero_incluido"}, {"tamano_colchon", "firmeza"}, {"color"}),
        ("Colchón", {"tamano_colchon", "largo_cm", "firmeza", "tipo_colchon", "peso_max_soportado_kg"}, {"numero_plazas", "tamano_colchon_compatible"}, {"color", "tamano_colchon"}),
    ),
)
def test_furniture_conditional_and_variant_matrix(product_type, included, excluded, axes):
    applicable = _applicable("home_furniture", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("home_furniture", product_type) == axes
    assert "peso_gramos" not in _fields("home_furniture")
    assert "weight_grams" not in _fields("home_furniture")


def test_home_icons_units_quick_options_and_public_condition_filtering():
    assert all(_fields("home_decor_lighting")[key].icon for key in ("tipo_producto", "tipo_montaje", "potencia_w"))
    assert all(_fields("home_cleaning_supplies")[key].icon for key in ("tipo_producto", "superficie_recomendada", "contenido_neto"))
    assert _fields("home_kitchen_dining")["capacidad_l"].quick_options
    assert _fields("home_kitchen_dining")["capacidad_ml"].quick_options
    assert _fields("home_cleaning_supplies")["capacidad_l"].quick_options == ("5", "10", "12", "15", "20")
    assert _fields("home_textiles")["gramaje_g_m2"].unit_label == "g/m²"

    items = _public_items("HOME_DECOR_LIGHTING", {"tipo_producto": "Vela", "tipo_cera": "Soya", "aroma": "Vainilla", "voltaje_v": "110"})
    assert {"tipo_producto", "tipo_cera", "aroma"} <= set(items)
    assert "voltaje_v" not in items
