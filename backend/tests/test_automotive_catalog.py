from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

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
from app.models import Category
from app.models.enums import SellerCommissionType
from app.services.marketplace_policy import resolve_marketplace_commission
from app.services.partner_product_categories import LEGACY_NEW_LISTING_CATEGORY_CODES
from app.services.product_specifications import (
    build_product_specification_presentation,
    resolve_product_template,
)


CANONICAL_AUTOMOTIVE = {
    "AUTOMOTIVE_ACCESSORIES_GENERAL": ("automotive_accessories_general", "Accesorios para vehículo", "accesorios-para-vehiculo", 1),
    "AUTOMOTIVE_TOOLS_EQUIPMENT": ("automotive_tools_equipment", "Herramientas y equipo", "herramientas-y-equipo", 2),
    "AUTOMOTIVE_REPLACEMENT_PARTS": ("automotive_replacement_parts", "Repuestos y mantenimiento", "repuestos-y-mantenimiento", 3),
    "AUTOMOTIVE_OILS_FLUIDS": ("automotive_oils_fluids", "Aceites, lubricantes y fluidos", "aceites-lubricantes-y-fluidos", 4),
    "AUTOMOTIVE_CARE_DETAILING": ("automotive_care_detailing", "Cuidado y detailing", "cuidado-y-detailing", 5),
    "AUTOMOTIVE_ELECTRONICS_AUDIO": ("automotive_electronics_audio", "Electrónica y audio", "electronica-y-audio", 6),
    "AUTOMOTIVE_TIRES_WHEELS": ("automotive_tires_wheels", "Neumáticos y rines", "neumaticos-y-rines", 7),
    "AUTOMOTIVE_BATTERIES": ("automotive_batteries", "Baterías y arranque", "baterias-y-arranque", 8),
}
LEGACY_AUTOMOTIVE = {
    "AUTOMOTIVE_ACCESSORIES": "automotive_accessories",
    "AUTOMOTIVE_TOOLS": "automotive_tools",
    "AUTOMOTIVE_BASIC_PARTS": "automotive_basic_parts",
}
SPECIFIC_COMPATIBILITY_FIELDS = {
    "marca_vehiculo_compatible",
    "modelo_vehiculo_compatible",
    "anio_desde",
    "anio_hasta",
    "motor_compatible",
    "notas_compatibilidad",
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
        category_name = "Automotriz"
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


def test_automotive_registry_seed_mapping_and_new_listing_contract():
    validate_template_registry()
    _parent, children = next(
        entry for entry in _PRODUCT_CATEGORY_TREE if entry[0]["code"] == "AUTOMOTIVE"
    )
    seeded = {child["code"]: child for child in children}
    available = [
        child for child in children
        if child["code"] not in LEGACY_NEW_LISTING_CATEGORY_CODES
    ]
    assert [child["code"] for child in available] == list(CANONICAL_AUTOMOTIVE)
    assert len(available) == 8
    for code, (template_key, name, slug, sort_order) in CANONICAL_AUTOMOTIVE.items():
        assert seeded[code] == {
            "code": code,
            "name": name,
            "slug": slug,
            "sort_order": sort_order,
        }
        assert template_key_for_category_code(code) == template_key
        assert get_product_template_for_category_code(code) is PRODUCT_TEMPLATES[template_key]
        assert resolve_product_template(code) is PRODUCT_TEMPLATES[template_key]
        assert INITIAL_CATEGORY_RATES[code] == Decimal("8.00")


def test_legacy_automotive_templates_rates_resolution_and_exclusion_are_preserved():
    assert set(LEGACY_AUTOMOTIVE) <= LEGACY_NEW_LISTING_CATEGORY_CODES
    expected_fields = {
        "color_principal", "material", "tipo", "numero_parte",
        "compatibilidad_vehiculos", "voltaje", "instrucciones",
    }
    for code, template_key in LEGACY_AUTOMOTIVE.items():
        template = PRODUCT_TEMPLATES[template_key]
        assert template_key_for_category_code(code) == template_key
        assert resolve_product_template(code) is template
        assert INITIAL_CATEGORY_RATES[code] == Decimal("8.00")
        assert set(_fields(template_key)) == expected_fields
        assert "tipo" in _public_items(code, {"tipo": "Legado"})


def test_canonical_automotive_templates_use_only_stable_field_contracts():
    disallowed_types = {"repeater", "compatibility_table", "size_table", "file", "document"}
    for template_key, *_ in CANONICAL_AUTOMOTIVE.values():
        template = PRODUCT_TEMPLATES[template_key]
        fields = _fields(template_key)
        assert fields["tipo_producto"].type == "select"
        assert fields["tipo_producto"].required is True
        assert not disallowed_types & {field.type for field in template.fields}
        assert template.required_documents == ()


@pytest.mark.parametrize(
    "template_key",
    (
        "automotive_accessories_general",
        "automotive_replacement_parts",
        "automotive_electronics_audio",
        "automotive_tires_wheels",
        "automotive_batteries",
    ),
)
def test_vehicle_compatibility_is_generic_motorcycle_aware_and_never_an_axis(template_key):
    fields = _fields(template_key)
    assert "Motocicleta" in fields["tipo_vehiculo_compatible"].options
    universal = {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(field.condition, {"compatibilidad": "Universal"})
    }
    specific = {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(field.condition, {"compatibilidad": "Específica"})
    }
    assert SPECIFIC_COMPATIBILITY_FIELDS.isdisjoint(universal)
    assert SPECIFIC_COMPATIBILITY_FIELDS <= specific
    axis_sources = {axis.source_field for axis in PRODUCT_TEMPLATES[template_key].variant_axes}
    assert not ({"compatibilidad", "tipo_vehiculo_compatible"} | SPECIFIC_COMPATIBILITY_FIELDS) & axis_sources


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Cobertor de vehículo", {"largo_cm", "ancho_cm", "alto_cm", "impermeable", "proteccion_uv"}, {"tipo_asiento", "ancho_dispositivo_min_mm"}, {"color_principal", "tamano"}),
        ("Soporte para celular", {"tipo_montaje", "ubicacion_montaje", "ancho_dispositivo_min_mm", "ancho_dispositivo_max_mm"}, {"largo_cm", "tipo_asiento"}, {"color_principal"}),
        ("Forro de asiento", {"cantidad_asientos", "tipo_asiento"}, {"impermeable", "cantidad_piezas"}, {"color_principal", "tamano"}),
        ("Alfombra / Tapete", {"cantidad_piezas"}, {"cantidad_asientos", "capacidad_carga_kg"}, {"color_principal", "tamano"}),
        ("Barra / Portaequipaje de techo", {"capacidad_carga_kg", "tipo_montaje"}, {"cantidad_asientos", "impermeable"}, {"tamano"}),
    ),
)
def test_accessory_conditions_and_variant_axes(product_type, included, excluded, axes):
    applicable = _applicable("automotive_accessories_general", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_accessories_general", product_type) == axes


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Gato hidráulico", {"capacidad_carga_t", "altura_minima_cm", "altura_maxima_cm"}, {"presion_max_psi", "torque_min_nm", "conector"}),
        ("Compresor / Inflador", {"presion_max_psi", "caudal_l_min"}, {"capacidad_carga_t", "torque_min_nm"}),
        ("Escáner OBD2", {"conector", "conectividad", "protocolos"}, {"capacidad_carga_t", "presion_max_psi"}),
        ("Llave dinamométrica", {"torque_min_nm", "torque_max_nm", "incluye_estuche"}, {"presion_max_psi", "conector"}),
        ("Juego de herramientas", {"cantidad_piezas", "herramientas_incluidas", "incluye_estuche"}, {"voltaje_v", "presion_max_psi"}),
    ),
)
def test_tools_are_product_specific_and_have_no_axes(product_type, included, excluded):
    applicable = _applicable("automotive_tools_equipment", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_tools_equipment", product_type) == set()


def test_tools_energy_source_and_voltage_are_separate_canonical_dimensions():
    template_key = template_key_for_category_code("AUTOMOTIVE_TOOLS_EQUIPMENT")
    assert template_key == "automotive_tools_equipment"
    fields = _fields(template_key)
    assert fields["fuente_energia"].options == (
        "Manual",
        "Sistema eléctrico del vehículo",
        "Batería recargable",
        "Red eléctrica",
    )
    assert {"12 V", "24 V"}.isdisjoint(fields["fuente_energia"].options)
    assert fields["voltaje_v"].type == "decimal"
    assert fields["voltaje_v"].unit == "V"
    assert fields["voltaje_v"].label == "Voltaje de entrada"
    assert fields["voltaje_v"].help == (
        "Voltaje nominal o de entrada indicado por el fabricante."
    )
    manual_compressor = _applicable(
        "automotive_tools_equipment", "Compresor / Inflador", fuente_energia="Manual"
    )
    assert "voltaje_v" not in manual_compressor
    assert {"presion_max_psi", "caudal_l_min"} <= manual_compressor
    for source in (
        "Sistema eléctrico del vehículo",
        "Batería recargable",
        "Red eléctrica",
    ):
        powered_compressor = _applicable(
            "automotive_tools_equipment", "Compresor / Inflador", fuente_energia=source
        )
        assert {"voltaje_v", "presion_max_psi", "caudal_l_min"} <= powered_compressor
    assert _axis_keys("automotive_tools_equipment", "Compresor / Inflador") == set()


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Filtro de aceite", {"numero_parte", "codigo_oem", "cantidad_paquete"}, {"diametro_mm", "diametro_rosca_mm", "longitud_mm", "base_socket", "tipo_sensor"}),
        ("Disco de freno", {"diametro_mm", "espesor_mm", "eje"}, {"diametro_rosca_mm", "longitud_mm", "base_socket"}),
        ("Bujía", {"diametro_rosca_mm", "longitud_rosca_mm", "separacion_electrodo_mm"}, {"diametro_mm", "base_socket"}),
        ("Plumilla / Limpiaparabrisas", {"longitud_mm", "posicion"}, {"diametro_mm", "numero_canales"}),
        ("Bombillo / Lámpara", {"base_socket", "voltaje_v", "potencia_w", "tecnologia"}, {"diametro_mm", "tipo_sensor"}),
        ("Sensor", {"tipo_sensor", "numero_pines"}, {"base_socket", "diametro_mm"}),
    ),
)
def test_replacement_part_conditions_and_no_axes(product_type, included, excluded):
    applicable = _applicable("automotive_replacement_parts", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_replacement_parts", product_type) == set()


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Aceite de motor", {"volumen_l", "viscosidad_sae", "base_aceite", "clasificacion_api", "clasificacion_acea", "especificaciones"}, {"peso_g", "dot", "tipo_grasa"}, {"volumen_l"}),
        ("Aceite de transmisión", {"volumen_l", "tipo_transmision", "especificacion"}, {"peso_g", "viscosidad_sae", "dot"}, {"volumen_l"}),
        ("Líquido de frenos", {"volumen_l", "dot"}, {"peso_g", "viscosidad_sae", "tipo_grasa"}, {"volumen_l"}),
        ("Refrigerante / Anticongelante", {"volumen_l", "tipo_refrigerante", "proteccion_temperatura_c"}, {"peso_g", "dot"}, {"volumen_l"}),
        ("Grasa", {"peso_g", "tipo_grasa", "base_grasa"}, {"volumen_l", "viscosidad_sae", "dot"}, set()),
    ),
)
def test_oils_fluids_conditions_positive_quantity_and_axes(product_type, included, excluded, axes):
    template = PRODUCT_TEMPLATES["automotive_oils_fluids"]
    applicable = _applicable("automotive_oils_fluids", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_oils_fluids", product_type) == axes
    quantity_key = "peso_g" if product_type == "Grasa" else "volumen_l"
    assert f"attributes.{quantity_key}" in validate_attributes(
        template, {"tipo_producto": product_type, quantity_key: "0"}, final=True
    )
    assert validate_attributes(
        template, {"tipo_producto": product_type, quantity_key: "0.01"}, final=True
    ) == {}


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Champú automotriz", {"presentacion", "superficie_uso", "efecto", "volumen_ml", "aroma", "concentrado", "dilucion_recomendada"}, {"material", "gramaje_g_m2", "contenido_kit"}, {"volumen_ml"}),
        ("Microfibra", {"material", "largo_cm", "ancho_cm", "gramaje_g_m2", "cantidad_piezas"}, {"presentacion", "superficie_uso", "volumen_ml", "concentrado"}, set()),
        ("Esponja / Aplicador", {"material", "cantidad_piezas"}, {"presentacion", "volumen_ml", "gramaje_g_m2"}, set()),
        ("Kit de detailing", {"cantidad_piezas", "contenido_kit"}, {"presentacion", "volumen_ml", "material"}, set()),
    ),
)
def test_detailing_separates_consumables_from_physical_items(product_type, included, excluded, axes):
    applicable = _applicable("automotive_care_detailing", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_care_detailing", product_type) == axes


@pytest.mark.parametrize(
    ("product_type", "included", "excluded", "axes"),
    (
        ("Unidad principal / Radio", {"pantalla_pulgadas", "resolucion_pantalla", "ram_gb", "almacenamiento_gb", "apple_carplay", "android_auto"}, {"diametro_pulgadas", "resolucion_video", "numero_sensores"}, {"ram_gb", "almacenamiento_gb"}),
        ("Pantalla / CarPlay", {"pantalla_pulgadas", "ram_gb", "almacenamiento_gb"}, {"diametro_pulgadas", "numero_canales"}, {"ram_gb", "almacenamiento_gb"}),
        ("Parlante", {"diametro_pulgadas", "potencia_rms_w", "impedancia_ohm"}, {"pantalla_pulgadas", "numero_canales", "resolucion_video"}, set()),
        ("Subwoofer", {"diametro_pulgadas", "potencia_rms_w", "impedancia_ohm"}, {"pantalla_pulgadas", "numero_canales"}, set()),
        ("Dashcam", {"resolucion_video", "fps", "angulo_vision_grados", "vision_nocturna"}, {"impermeable", "numero_sensores"}, set()),
        ("Cámara de reversa", {"resolucion_video", "angulo_vision_grados", "impermeable"}, {"fps", "vision_nocturna"}, set()),
        ("TPMS", {"numero_sensores"}, {"pantalla_pulgadas", "resolucion_video"}, set()),
    ),
)
def test_electronics_audio_conditions_and_axes(product_type, included, excluded, axes):
    applicable = _applicable("automotive_electronics_audio", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_electronics_audio", product_type) == axes


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        ("Neumático", {"ancho_neumatico_mm", "perfil_neumatico", "diametro_rin_pulgadas", "indice_carga", "indice_velocidad", "temporada", "run_flat", "tubeless"}, {"ancho_rin_pulgadas", "pcd_mm", "diametro_compatible_pulgadas"}),
        ("Rin", {"diametro_rin_pulgadas", "ancho_rin_pulgadas", "numero_pernos", "pcd_mm", "offset_et_mm", "diametro_central_mm", "material_rin"}, {"ancho_neumatico_mm", "indice_carga", "diametro_compatible_pulgadas"}),
        ("Rueda completa", {"ancho_neumatico_mm", "perfil_neumatico", "diametro_rin_pulgadas", "ancho_rin_pulgadas", "pcd_mm", "material_rin"}, {"diametro_compatible_pulgadas", "tipo_valvula"}),
        ("Cámara", {"diametro_compatible_pulgadas", "ancho_compatible", "tipo_valvula"}, {"ancho_neumatico_mm", "pcd_mm", "material_rin"}),
    ),
)
def test_tires_wheels_technical_fields_and_no_axes(product_type, included, excluded):
    applicable = _applicable("automotive_tires_wheels", product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axis_keys("automotive_tires_wheels", product_type) == set()


def test_battery_contract_has_technical_fields_and_no_axes():
    fields = _fields("automotive_batteries")
    assert {
        "voltaje_v", "capacidad_ah", "corriente_arranque_a", "polaridad",
        "start_stop", "libre_mantenimiento", "largo_mm", "ancho_mm", "alto_mm",
    } <= set(fields)
    assert fields["voltaje_v"].unit == "V"
    assert fields["capacidad_ah"].unit == "Ah"
    assert fields["corriente_arranque_a"].unit == "A"
    assert _axis_keys("automotive_batteries", "Batería AGM") == set()


def test_variant_switching_does_not_leak_stale_automotive_axes():
    assert _axis_keys("automotive_oils_fluids", "Aceite de motor") == {"volumen_l"}
    assert _axis_keys("automotive_oils_fluids", "Grasa") == set()
    assert _axis_keys("automotive_care_detailing", "Champú automotriz") == {"volumen_ml"}
    assert _axis_keys("automotive_care_detailing", "Microfibra") == set()
    assert _axis_keys("automotive_electronics_audio", "Unidad principal / Radio") == {"ram_gb", "almacenamiento_gb"}
    assert _axis_keys("automotive_electronics_audio", "Parlante") == set()
    assert _axis_keys("automotive_accessories_general", "Cobertor de vehículo") == {"color_principal", "tamano"}
    assert _axis_keys("automotive_accessories_general", "Barra / Portaequipaje de techo") == {"tamano"}


@pytest.mark.parametrize(
    ("category_code", "attributes", "expected"),
    (
        ("AUTOMOTIVE_TIRES_WHEELS", {"tipo_producto": "Neumático", "ancho_neumatico_mm": "205", "perfil_neumatico": "55", "diametro_rin_pulgadas": "16"}, {"ancho_neumatico_mm", "perfil_neumatico", "diametro_rin_pulgadas"}),
        ("AUTOMOTIVE_BATTERIES", {"tipo_producto": "Batería AGM", "voltaje_v": "12", "capacidad_ah": "70"}, {"voltaje_v", "capacidad_ah"}),
        ("AUTOMOTIVE_OILS_FLUIDS", {"tipo_producto": "Aceite de motor", "viscosidad_sae": "5W-30", "volumen_l": "4"}, {"viscosidad_sae", "volumen_l"}),
        ("AUTOMOTIVE_ELECTRONICS_AUDIO", {"tipo_producto": "Unidad principal / Radio", "ram_gb": 4, "almacenamiento_gb": 64}, {"ram_gb", "almacenamiento_gb"}),
        ("AUTOMOTIVE_REPLACEMENT_PARTS", {"tipo_producto": "Filtro de aceite", "numero_parte": "ABC-123", "codigo_oem": ["OEM-9"]}, {"numero_parte", "codigo_oem"}),
        ("AUTOMOTIVE_ACCESSORIES_GENERAL", {"tipo_producto": "Cobertor de vehículo", "compatibilidad": "Específica", "marca_vehiculo_compatible": "Toyota", "modelo_vehiculo_compatible": "Corolla"}, {"compatibilidad", "marca_vehiculo_compatible", "modelo_vehiculo_compatible"}),
    ),
)
def test_canonical_automotive_public_specifications(category_code, attributes, expected):
    assert expected <= set(_public_items(category_code, attributes))


@pytest.mark.integration
def test_automotive_commission_is_eight_percent_with_global_minimum_floor(app, session):
    runner = app.test_cli_runner()
    seeded = runner.invoke(args=["seed-product-categories"])
    assert seeded.exit_code == 0, seeded.output
    bootstrapped = runner.invoke(args=["marketplace-policy", "bootstrap"])
    assert bootstrapped.exit_code == 0, bootstrapped.output
    category = session.scalar(
        select(Category).where(Category.code == "AUTOMOTIVE_BATTERIES")
    )
    normal = resolve_marketplace_commission(
        session, category_id=category.id, price="10.00"
    )
    assert normal.mode == SellerCommissionType.PERCENTAGE
    assert normal.rate_percent == Decimal("8.00")
    assert normal.commission_amount == Decimal("0.80")
    floor = resolve_marketplace_commission(
        session, category_id=category.id, price="3.00"
    )
    assert floor.mode == SellerCommissionType.FIXED
    assert floor.rate_percent == Decimal("8.00")
    assert floor.fixed_amount == Decimal("0.25")
    assert floor.commission_amount == Decimal("0.25")
