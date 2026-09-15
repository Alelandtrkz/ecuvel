from __future__ import annotations

from decimal import Decimal

import pytest

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    condition_applies,
    get_product_template_for_category_code,
    template_key_for_category_code,
    validate_attributes,
    variant_axes_for_attributes,
)
from app.commands.marketplace_policy import INITIAL_CATEGORY_RATES
from app.commands.seed import _PRODUCT_CATEGORY_TREE
from app.services.product_specifications import resolve_product_template


ELECTRONICS_BINDINGS = {
    "ELECTRONICS_PHONES": "electronics_phones",
    "ELECTRONICS_COMPUTERS": "electronics_computers",
    "ELECTRONICS_HEADPHONES": "electronics_headphones",
    "ELECTRONICS_CAMERAS": "electronics_cameras",
    "ELECTRONICS_SECURITY": "electronics_security",
}

SECURITY_TYPES = (
    "Cámara de seguridad",
    "DVR",
    "NVR",
    "XVR / Grabador híbrido",
    "Kit de videovigilancia",
    "Videoportero / Timbre inteligente",
    "Accesorio de videovigilancia",
    "Otro",
)


def _fields(template_key: str):
    return {
        field.key: field
        for field in PRODUCT_TEMPLATES[template_key].fields
    }


def _applicable_fields(template_key: str, attributes: dict[str, object]) -> set[str]:
    return {
        field.key
        for field in PRODUCT_TEMPLATES[template_key].fields
        if condition_applies(field.condition, attributes)
    }


def test_electronics_seed_template_commission_registry_correspondence():
    _parent, children = next(
        entry
        for entry in _PRODUCT_CATEGORY_TREE
        if entry[0]["code"] == "ELECTRONICS"
    )
    seeded_by_code = {child["code"]: child for child in children}

    assert set(seeded_by_code) == set(ELECTRONICS_BINDINGS)
    for category_code, template_key in ELECTRONICS_BINDINGS.items():
        assert template_key_for_category_code(category_code) == template_key
        assert get_product_template_for_category_code(category_code) is PRODUCT_TEMPLATES[template_key]
        assert resolve_product_template(category_code) is PRODUCT_TEMPLATES[template_key]
        assert category_code in INITIAL_CATEGORY_RATES

    assert seeded_by_code["ELECTRONICS_SECURITY"] == {
        "code": "ELECTRONICS_SECURITY",
        "name": "Seguridad y videovigilancia",
        "slug": "seguridad-y-videovigilancia",
        "sort_order": 5,
    }
    assert INITIAL_CATEGORY_RATES["ELECTRONICS_SECURITY"] == Decimal("8.00")


def test_existing_electronics_selector_options_remain_stable():
    assert _fields("electronics_phones")["tipo_producto"].options == (
        "Smartphone",
        "Teléfono básico",
        "Cargador",
        "Cable",
        "Protector",
        "Soporte",
        "Repuesto",
        "Otro",
    )
    assert _fields("electronics_computers")["tipo_equipo"].options == (
        "Laptop",
        "Desktop",
        "Tablet",
        "Monitor",
        "Accesorio",
    )
    assert _fields("electronics_headphones")["tipo"].options == (
        "In-ear",
        "On-ear",
        "Over-ear",
        "Gaming",
        "Otro",
    )
    assert _fields("electronics_cameras")["tipo_camara"].options == (
        "Seguridad",
        "Fotográfica",
        "Deportiva",
        "Webcam",
        "Otro",
    )


@pytest.mark.parametrize(
    ("product_type", "included", "excluded"),
    (
        (
            "Smartphone",
            {"procesador", "resolucion_pantalla", "frecuencia_hz", "red_movil", "configuracion_sim", "nfc", "proteccion_ip"},
            {"numero_puertos", "protocolos_carga", "potencia_max_w", "velocidad_datos"},
        ),
        (
            "Cargador",
            {"numero_puertos", "protocolos_carga", "potencia_w"},
            {"procesador", "potencia_max_w", "velocidad_datos"},
        ),
        (
            "Cable",
            {"potencia_max_w", "velocidad_datos", "longitud_cm"},
            {"procesador", "numero_puertos", "protocolos_carga"},
        ),
        (
            "Protector",
            {"tipo_protector", "modelo_compatible"},
            {"procesador", "numero_puertos", "potencia_max_w"},
        ),
    ),
)
def test_phone_conditional_field_matrix(product_type, included, excluded):
    applicable = _applicable_fields(
        "electronics_phones", {"tipo_producto": product_type}
    )
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    ("equipment_type", "included", "excluded"),
    (
        (
            "Laptop",
            {"gpu", "resolucion_pantalla", "frecuencia_hz", "tipo_panel", "pantalla_tactil", "puertos", "wifi", "bluetooth_version"},
            {"tiene_sim", "tipo_conexion_monitor", "tipo_accesorio"},
        ),
        (
            "Desktop",
            {"gpu", "puertos", "wifi", "bluetooth_version"},
            {"pantalla_pulgadas", "resolucion_pantalla", "pantalla_tactil", "tipo_accesorio"},
        ),
        (
            "Tablet",
            {"resolucion_pantalla", "frecuencia_hz", "tipo_panel", "pantalla_tactil", "puertos", "wifi", "bluetooth_version", "tiene_sim"},
            {"gpu", "tipo_conexion_monitor", "tipo_accesorio"},
        ),
        (
            "Monitor",
            {"pantalla_pulgadas", "resolucion_pantalla", "frecuencia_hz", "tipo_panel", "pantalla_tactil", "tipo_conexion_monitor"},
            {"procesador", "ram_gb", "almacenamiento_gb", "gpu", "puertos", "wifi", "bluetooth_version"},
        ),
        (
            "Accesorio",
            {"tipo_accesorio"},
            {"procesador", "ram_gb", "pantalla_pulgadas", "gpu", "puertos", "wifi"},
        ),
    ),
)
def test_computer_conditional_field_matrix(equipment_type, included, excluded):
    applicable = _applicable_fields(
        "electronics_computers", {"tipo_equipo": equipment_type}
    )
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    ("attributes", "included", "excluded"),
    (
        (
            {"tipo": "In-ear", "conexion": "Bluetooth"},
            {"autonomia_horas", "version_bluetooth", "proteccion_ip"},
            {"conector_fisico", "surround", "plataformas"},
        ),
        (
            {"tipo": "Over-ear", "conexion": "Cable"},
            {"conector_fisico", "driver_mm", "impedancia_ohm"},
            {"autonomia_horas", "version_bluetooth", "proteccion_ip"},
        ),
        (
            {"tipo": "Gaming", "conexion": "USB"},
            {"surround", "plataformas", "conector_fisico"},
            {"autonomia_horas", "version_bluetooth", "proteccion_ip"},
        ),
    ),
)
def test_headphone_conditional_field_matrix(attributes, included, excluded):
    applicable = _applicable_fields("electronics_headphones", attributes)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


@pytest.mark.parametrize(
    ("camera_type", "included", "excluded"),
    (
        (
            "Fotográfica",
            {"tipo_fotografica", "tipo_sensor", "montura_lente", "resolucion_video"},
            {"profundidad_agua_m", "campo_vision_grados", "tapa_privacidad"},
        ),
        (
            "Deportiva",
            {"profundidad_agua_m", "fps_video", "bateria_mah", "resolucion_video"},
            {"tipo_fotografica", "campo_vision_grados", "tapa_privacidad"},
        ),
        (
            "Webcam",
            {"campo_vision_grados", "tapa_privacidad", "conexion_webcam", "microfono"},
            {"tipo_fotografica", "profundidad_agua_m", "conectividad"},
        ),
        (
            "Seguridad",
            {"vision_nocturna", "deteccion_movimiento", "alimentacion", "proteccion_ip", "conectividad"},
            {"tipo_fotografica", "profundidad_agua_m", "campo_vision_grados", "tapa_privacidad"},
        ),
    ),
)
def test_camera_conditional_field_matrix(camera_type, included, excluded):
    applicable = _applicable_fields(
        "electronics_cameras", {"tipo_camara": camera_type}
    )
    assert included <= applicable
    assert excluded.isdisjoint(applicable)


def test_security_selector_and_field_keys_are_canonical_and_unique():
    fields = PRODUCT_TEMPLATES["electronics_security"].fields
    selector = _fields("electronics_security")["tipo_seguridad"]

    assert selector.required is True
    assert selector.type == "select"
    assert selector.options == SECURITY_TYPES
    assert len({field.key for field in fields}) == len(fields)
    assert all(not field.required for field in fields if field.key != "tipo_seguridad")


@pytest.mark.parametrize(
    ("security_type", "included", "excluded"),
    (
        (
            "Cámara de seguridad",
            {"formato_camara", "tecnologia_camara", "resolucion_mp", "lente_mm", "vision_nocturna", "microfono", "poe", "onvif"},
            {"canales", "bahias_hdd", "tipo_grabador", "timbre_interior_incluido", "tipo_accesorio_seguridad"},
        ),
        (
            "DVR",
            {"canales", "resolucion_grabacion", "compresion_video", "bahias_hdd", "disco_incluido", "acceso_remoto"},
            {"puertos_poe", "onvif", "lente_mm", "tipo_grabador"},
        ),
        (
            "NVR",
            {"canales", "bahias_hdd", "puertos_poe", "onvif", "acceso_remoto"},
            {"lente_mm", "tipo_grabador", "cantidad_camaras"},
        ),
        (
            "XVR / Grabador híbrido",
            {"canales", "bahias_hdd", "puertos_poe", "onvif", "acceso_remoto"},
            {"lente_mm", "tipo_grabador", "cantidad_camaras"},
        ),
        (
            "Kit de videovigilancia",
            {"tipo_grabador", "cantidad_camaras", "canales", "disco_incluido", "capacidad_disco_incluido_tb", "cable_incluido", "poe", "acceso_remoto"},
            {"lente_mm", "bahias_hdd", "resolucion_grabacion", "compresion_video"},
        ),
        (
            "Videoportero / Timbre inteligente",
            {"resolucion_video", "vision_nocturna", "audio_bidireccional", "conectividad", "alimentacion", "timbre_interior_incluido"},
            {"canales", "bahias_hdd", "lente_mm", "tipo_grabador"},
        ),
        (
            "Accesorio de videovigilancia",
            {"tipo_accesorio_seguridad", "compatibilidad", "numero_parte"},
            {"canales", "bahias_hdd", "lente_mm", "resolucion_video"},
        ),
        (
            "Otro",
            {"tipo_seguridad", "color_principal", "material"},
            {"canales", "bahias_hdd", "lente_mm", "resolucion_video", "tipo_accesorio_seguridad"},
        ),
    ),
)
def test_security_conditional_field_matrix(security_type, included, excluded):
    applicable = _applicable_fields(
        "electronics_security", {"tipo_seguridad": security_type}
    )
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    if security_type == "Otro":
        assert applicable == included


@pytest.mark.parametrize(
    ("template_key", "attributes", "expected"),
    (
        (
            "electronics_phones",
            {"tipo_producto": "Smartphone"},
            {"color_principal", "almacenamiento_gb", "ram_gb", "pantalla_pulgadas"},
        ),
        (
            "electronics_computers",
            {"tipo_equipo": "Laptop"},
            {"color", "ram", "almacenamiento"},
        ),
        (
            "electronics_computers",
            {"tipo_equipo": "Monitor"},
            {"color", "tamano"},
        ),
        ("electronics_headphones", {"tipo": "Gaming"}, {"color"}),
        ("electronics_cameras", {"tipo_camara": "Seguridad"}, {"color"}),
        ("electronics_security", {"tipo_seguridad": "NVR"}, {"color"}),
    ),
)
def test_electronics_variant_axis_regression(template_key, attributes, expected):
    assert {
        axis.key
        for axis in variant_axes_for_attributes(
            PRODUCT_TEMPLATES[template_key], attributes
        )
    } == expected


def test_new_electronics_field_types_options_and_quick_options():
    phone = _fields("electronics_phones")
    computer = _fields("electronics_computers")
    headphones = _fields("electronics_headphones")
    camera = _fields("electronics_cameras")
    security = _fields("electronics_security")

    assert phone["red_movil"].type == "multiselect"
    assert phone["red_movil"].options == ("2G", "3G", "4G LTE", "5G")
    assert phone["numero_puertos"].min == 1
    assert phone["potencia_max_w"].unit == "W"
    assert computer["puertos"].type == "chips"
    assert computer["tipo_accesorio"].type == "text"
    assert computer["tipo_accesorio"].quick_options == (
        "Teclado", "Mouse", "Hub USB", "Dock", "Disco externo",
        "Base para laptop", "Adaptador", "Otro",
    )
    assert headphones["plataformas"].type == "chips"
    assert headphones["conector_fisico"].options == (
        "3.5 mm", "USB-A", "USB-C", "Lightning", "Otro",
    )
    assert camera["campo_vision_grados"].max == 360
    assert camera["resolucion_video"].quick_options == (
        "720p", "1080p", "2K", "4K", "8K",
    )
    assert security["resolucion_grabacion"].type == "select"
    assert security["compresion_video"].type == "multiselect"
    assert security["disco_incluido"].type == "boolean"
    assert security["canales"].type == "integer"
    assert security["capacidad_max_hdd_tb"].type == "decimal"
    assert security["alimentacion"].type == "text"
    assert security["alimentacion"].quick_options == (
        "Batería", "Cableado", "PoE", "Otro",
    )
    new_field_keys = {
        "electronics_phones": {
            "procesador", "resolucion_pantalla", "frecuencia_hz", "red_movil",
            "configuracion_sim", "nfc", "proteccion_ip", "numero_puertos",
            "protocolos_carga", "potencia_max_w", "velocidad_datos",
        },
        "electronics_computers": {
            "gpu", "pantalla_tactil", "puertos", "wifi", "bluetooth_version",
        },
        "electronics_headphones": {
            "version_bluetooth", "conector_fisico", "driver_mm",
            "impedancia_ohm", "proteccion_ip",
        },
        "electronics_cameras": {
            "tipo_fotografica", "profundidad_agua_m", "campo_vision_grados",
            "tapa_privacidad",
        },
        "electronics_security": set(security) - {"color_principal", "material"},
    }
    for template_key, field_keys in new_field_keys.items():
        fields = _fields(template_key)
        assert all(fields[field_key].icon for field_key in field_keys)


def test_new_electronics_field_validation_uses_existing_type_contracts():
    security = PRODUCT_TEMPLATES["electronics_security"]
    valid_security = {
        "tipo_seguridad": "DVR",
        "canales": "4",
        "resolucion_grabacion": "4 MP",
        "compresion_video": ["H.264", "H.265"],
        "bahias_hdd": "2",
        "capacidad_max_hdd_tb": "8.5",
        "disco_incluido": True,
        "salidas_video": ["HDMI"],
    }
    assert validate_attributes(security, valid_security, final=True) == {}

    invalid_security = {
        "tipo_seguridad": "DVR",
        "canales": "0",
        "resolucion_grabacion": "16K",
        "compresion_video": ["MPEG-2"],
        "capacidad_max_hdd_tb": "-1",
        "disco_incluido": "true",
    }
    assert set(validate_attributes(security, invalid_security, final=False)) == {
        "attributes.canales",
        "attributes.resolucion_grabacion",
        "attributes.compresion_video",
        "attributes.capacidad_max_hdd_tb",
        "attributes.disco_incluido",
    }

    computer = PRODUCT_TEMPLATES["electronics_computers"]
    assert validate_attributes(
        computer,
        {"tipo_equipo": "Laptop", "puertos": ["USB-C", "HDMI"]},
        final=False,
    ) == {}
    assert "attributes.puertos" in validate_attributes(
        computer,
        {"tipo_equipo": "Laptop", "puertos": ["USB-C", ""]},
        final=False,
    )

    camera = PRODUCT_TEMPLATES["electronics_cameras"]
    assert "attributes.campo_vision_grados" in validate_attributes(
        camera,
        {"tipo_camara": "Webcam", "campo_vision_grados": "361"},
        final=False,
    )


def test_inapplicable_new_fields_are_ignored_by_validation():
    assert validate_attributes(
        PRODUCT_TEMPLATES["electronics_security"],
        {
            "tipo_seguridad": "Otro",
            "canales": "not-an-integer",
            "compresion_video": ["invalid"],
        },
        final=True,
    ) == {}
