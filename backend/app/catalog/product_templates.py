from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


SUPPORTED_FIELD_TYPES = {
    "text",
    "textarea",
    "integer",
    "decimal",
    "select",
    "multiselect",
    "radio",
    "boolean",
    "chips",
    "color",
    "dimension",
    "date",
    "repeater",
    "compatibility_table",
    "size_table",
    "file",
    "document",
    "variant_attribute",
}


@dataclass(frozen=True, slots=True)
class ProductTemplateField:
    key: str
    label: str
    type: str = "text"
    required: bool = False
    section: str = "general"
    order: int = 0
    placeholder: str = ""
    help: str = ""
    unit: str = ""
    unit_label: str = ""
    example: str = ""
    icon: str = ""
    options: tuple[str, ...] = ()
    quick_options: tuple[str, ...] = ()
    min: int | Decimal | None = None
    max: int | Decimal | None = None
    condition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VariantAxis:
    key: str
    label: str
    unit: str = ""
    suggestions: tuple[str, ...] = ()
    condition: dict[str, Any] | None = None
    source_field: str = ""
    value_type: str = "text"
    default_for: tuple[str, ...] = ()
    is_visual: bool = False
    is_listing_axis: bool = False


@dataclass(frozen=True, slots=True)
class ProductTemplate:
    key: str
    name: str
    category_code: str
    subcategory_code: str
    fields: tuple[ProductTemplateField, ...] = field(default_factory=tuple)
    required_documents: tuple[str, ...] = ()
    variant_axes: tuple[VariantAxis, ...] = ()

    @property
    def sections(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in sorted(self.fields, key=lambda field: (field.section, field.order, field.key)):
            if item.section not in seen:
                seen.append(item.section)
        return tuple(seen)


class ProductTemplateError(Exception):
    pass


class ProductTemplateValidationError(ProductTemplateError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("La plantilla contiene datos inválidos.")
        self.errors = errors


_UNIT_LABELS = {
    "GB": "GB",
    "TB": "TB",
    "MP": "MP",
    "mAh": "mAh",
    "W": "W",
    "V": "V",
    "A": "A",
    "cm": "cm",
    "Hz": "Hz",
    "horas": "horas",
    "in": "pulgadas",
    "meses": "meses",
    "L": "L",
    "ml": "ml",
    "kg": "kg",
    "g": "g",
    "g/m²": "g/m²",
    "°C": "°C",
}

_FIELD_EXAMPLES = {
    "ram_gb": "12",
    "almacenamiento_gb": "512",
    "pantalla_pulgadas": "6.7",
    "camara_principal_mp": "50",
    "resolucion_mp": "4",
    "bateria_mah": "6000",
    "potencia_w": "25",
    "voltaje": "5",
    "corriente_a": "3",
    "longitud_cm": "100",
    "frecuencia_hz": "120",
    "autonomia_horas": "8",
    "resolucion_pantalla": "1920x1080",
    "resolucion_video": "1080p o 4K",
    "proteccion_ip": "IP66",
    "numero_puertos": "2",
    "potencia_max_w": "100",
    "velocidad_datos": "10 Gbps",
    "version_bluetooth": "5.3",
    "bluetooth_version": "5.3",
    "driver_mm": "40",
    "impedancia_ohm": "32",
    "profundidad_agua_m": "10",
    "campo_vision_grados": "90",
    "canales": "8",
    "bahias_hdd": "2",
    "capacidad_max_hdd_tb": "8",
    "capacidad_disco_incluido_tb": "4",
    "puertos_red": "1",
    "puertos_poe": "8",
    "lente_mm": "2.8",
    "distancia_ir_m": "30",
    "cantidad_camaras": "4",
    "resolucion_camaras_mp": "4",
    "longitud_cable_m": "20",
}

_FIELD_HELP = {
    "ram_gb": "Memoria RAM del equipo. Escribe solo el número; la unidad es GB.",
    "almacenamiento_gb": "Capacidad interna de almacenamiento. Escribe solo el número; la unidad es GB.",
    "pantalla_pulgadas": "Tamaño diagonal de la pantalla en pulgadas.",
    "camara_principal_mp": "Resolución de la cámara principal en megapíxeles.",
    "resolucion_mp": "Resolución de foto o sensor en megapíxeles.",
    "bateria_mah": "Capacidad de batería en miliamperios-hora. Escribe solo el número.",
    "potencia_w": "Potencia máxima del cargador en watts.",
    "voltaje": "Voltaje de salida o alimentación en voltios.",
    "corriente_a": "Corriente máxima en amperios.",
    "longitud_cm": "Longitud del cable o accesorio en centímetros.",
    "frecuencia_hz": "Frecuencia de refresco de pantalla en hertz.",
    "autonomia_horas": "Duración aproximada de la batería por carga, en horas.",
    "resolucion_pantalla": "Resolución física de pantalla. Usa ancho x alto.",
    "resolucion_video": "Calidad máxima de video soportada, por ejemplo 1080p, 2K o 4K.",
    "proteccion_ip": "Grado de protección contra polvo/agua, por ejemplo IP66 o IP67.",
    "tipo_conector_salida": "Puerto que entrega energía o datos hacia el dispositivo.",
    "tipo_conector_entrada": "Puerto que se conecta al cargador, computadora o fuente.",
    "tipo_conexion_monitor": "Lista las entradas disponibles, separadas por coma si hace falta.",
    "conectividad": "Indica las tecnologías compatibles, por ejemplo Wi‑Fi o Ethernet.",
    "alimentacion": "Indica cómo se alimenta el producto: PoE, batería, cable 12V, USB, etc.",
}

_FIELD_ICONS = {
    "sistema_operativo": "smartphone",
    "tipo_producto": "package-search",
    "tipo_equipo": "monitor-cog",
    "tipo_camara": "camera",
    "tipo": "headphones",
    "ram_gb": "memory-stick",
    "almacenamiento_gb": "hard-drive",
    "tipo_almacenamiento": "database",
    "pantalla_pulgadas": "monitor",
    "resolucion_pantalla": "screen-share",
    "frecuencia_hz": "activity",
    "tipo_panel": "panel-top",
    "camara_principal_mp": "camera",
    "resolucion_mp": "camera",
    "resolucion_video": "video",
    "fps_video": "gauge",
    "bateria_mah": "battery-charging",
    "potencia_w": "zap",
    "voltaje": "plug-zap",
    "corriente_a": "gauge",
    "tipo_conector_salida": "cable",
    "tipo_conector_entrada": "cable",
    "carga_rapida": "zap",
    "longitud_cm": "ruler",
    "transferencia_datos": "shuffle",
    "tipo_protector": "shield",
    "modelo_compatible": "badge-check",
    "tipo_soporte": "smartphone-charging",
    "ajustable": "sliders-horizontal",
    "tipo_repuesto": "wrench",
    "numero_parte": "barcode",
    "procesador": "cpu",
    "tiene_sim": "scan-line",
    "tipo_conexion_monitor": "cable",
    "tipo_accesorio": "keyboard",
    "vision_nocturna": "moon",
    "deteccion_movimiento": "radar",
    "alimentacion": "plug",
    "proteccion_ip": "shield-check",
    "conectividad": "wifi",
    "tipo_sensor": "aperture",
    "montura_lente": "focus",
    "pantalla_abatible": "rotate-3d",
    "estabilizacion": "move-3d",
    "conexion_webcam": "usb",
    "microfono": "mic",
    "autofocus": "scan-search",
    "conexion": "bluetooth",
    "cancelacion_activa": "volume-x",
    "autonomia_horas": "clock",
    "surround": "waves",
    "plataformas": "gamepad-2",
    "red_movil": "wifi",
    "configuracion_sim": "scan-line",
    "nfc": "scan-line",
    "numero_puertos": "plug-zap",
    "protocolos_carga": "zap",
    "potencia_max_w": "zap",
    "velocidad_datos": "activity",
    "gpu": "monitor",
    "pantalla_tactil": "monitor",
    "puertos": "cable",
    "wifi": "wifi",
    "bluetooth_version": "bluetooth",
    "version_bluetooth": "bluetooth",
    "conector_fisico": "cable",
    "driver_mm": "headphones",
    "impedancia_ohm": "gauge",
    "tipo_fotografica": "camera",
    "profundidad_agua_m": "ruler",
    "campo_vision_grados": "aperture",
    "tapa_privacidad": "shield",
    "tipo_seguridad": "shield-check",
    "canales": "list-checks",
    "resolucion_grabacion": "video",
    "compresion_video": "video",
    "bahias_hdd": "hard-drive",
    "capacidad_max_hdd_tb": "hard-drive",
    "disco_incluido": "hard-drive",
    "capacidad_disco_incluido_tb": "hard-drive",
    "salidas_video": "cable",
    "puertos_red": "wifi",
    "puertos_poe": "plug-zap",
    "onvif": "wifi",
    "acceso_remoto": "smartphone",
    "formato_camara": "camera",
    "tecnologia_camara": "camera",
    "lente_mm": "focus",
    "distancia_ir_m": "moon",
    "audio_bidireccional": "mic",
    "poe": "plug-zap",
    "uso_instalacion": "shield-check",
    "almacenamiento_soportado": "hard-drive",
    "tipo_grabador": "video",
    "cantidad_camaras": "camera",
    "resolucion_camaras_mp": "camera",
    "tipo_camaras_kit": "camera",
    "cable_incluido": "cable",
    "longitud_cable_m": "ruler",
    "fuente_alimentacion_incluida": "plug",
    "timbre_interior_incluido": "bell",
    "tipo_accesorio_seguridad": "wrench",
    "compatibilidad": "badge-check",
    "genero": "users",
    "talla": "ruler",
    "sistema_talla": "ruler",
    "tipo_talla": "ruler",
    "material_principal": "layers",
    "material_exterior": "layers",
    "material_forro": "layers",
    "material_plantilla": "layers",
    "material_suela": "layers",
    "material_metal": "gem",
    "material_hebilla": "link",
    "material_montura": "glasses",
    "material_lente": "glasses",
    "material_caja": "watch",
    "material_correa": "watch",
    "material_cristal": "watch",
    "composicion": "layers",
    "cuidados": "washing-machine",
    "tipo_cierre": "link",
    "tipo_cierre_joyeria": "link",
    "peso_g": "scale",
    "movimiento": "clock",
    "resistencia_agua": "droplets",
    "resistente_agua": "droplets",
    "impermeable": "droplets",
    "proteccion_uv": "shield-check",
    "forma_montura": "glasses",
    "forma_caja": "watch",
    "clasificacion_joyeria": "gem",
    "piedra_principal": "gem",
    "ley_metal": "gem",
    "compatibilidad_so": "smartphone",
    "habitacion_uso": "house",
    "uso_ubicacion": "house",
    "orientacion": "image",
    "tipo_montaje": "wrench",
    "mecanismo_reloj": "clock",
    "tipo_cera": "flame",
    "tipo_planta": "flower-2",
    "tipo_casquillo": "lightbulb",
    "bombilla_incluida": "lightbulb",
    "regulable": "sliders-horizontal",
    "cantidad_luces": "lightbulb",
    "cantidad_piezas": "list-checks",
    "compatibilidad_coccion": "cooking-pot",
    "tipo_cuchillo": "utensils",
    "material_hoja": "layers",
    "material_mango": "layers",
    "tipo_filo": "utensils",
    "tipo_utensilio": "utensils",
    "tipo_cubierto": "utensils",
    "aislamiento_termico": "thermometer",
    "presentacion": "package",
    "contenido_neto": "flask-conical",
    "superficie_recomendada": "brush-cleaning",
    "superficie_uso": "spray-can",
    "reutilizable": "refresh-cw",
    "lavable": "washing-machine",
    "cantidad_paquete": "package",
    "apilable": "boxes",
    "plegable": "archive",
    "numero_compartimentos": "boxes",
    "numero_niveles": "list-ordered",
    "tapa_incluida": "package-check",
    "ruedas": "circle-dot",
    "instalacion": "wrench",
    "tamano_cama": "bed",
    "tamano_textil": "ruler",
    "material_relleno": "layers",
    "firmeza": "layers",
    "tipo_toalla": "bath",
    "blackout": "moon",
    "translucidez": "sun",
    "requiere_ensamblaje": "wrench",
    "numero_plazas": "armchair",
    "material_tapizado": "armchair",
    "altura_ajustable": "sliders-horizontal",
    "numero_puertas": "door-open",
    "numero_cajones": "archive",
    "numero_estantes": "library",
    "tamano_colchon": "bed",
    "tamano_colchon_compatible": "bed",
    "tipo_colchon": "bed",
    "tipo_piel": "droplets",
    "zona_uso": "scan-face",
    "zona_aplicacion": "scan-face",
    "aroma": "flower-2",
    "fragancia": "flower-2",
    "ingredientes": "list-checks",
    "ingredientes_destacados": "sparkles",
    "publico_objetivo": "users",
    "genero_objetivo": "users",
    "tono_color": "palette",
    "textura": "layers",
    "acabado": "sparkles",
    "cobertura": "layers",
    "spf_declarado": "sun",
    "dureza_cerdas": "brush",
    "tamano_cabezal": "ruler",
    "longitud_hilo_m": "ruler",
    "numero_hojas": "list-ordered",
    "familia_olfativa": "flower-2",
    "notas_salida": "flower-2",
    "notas_corazon": "flower-2",
    "notas_fondo": "flower-2",
    "volumen_ml": "flask-conical",
    "tipo_cabello": "scissors",
    "tipo_cuero_cabelludo": "scissors",
    "efecto_beneficio": "sparkles",
    "nivel_fijacion": "gauge",
    "codigo_tono": "palette",
    "tipo_coloracion": "palette",
    "diametro_mm": "ruler",
}

_UNIT_ICONS = {
    "GB": "hard-drive",
    "MP": "camera",
    "mAh": "battery-charging",
    "W": "zap",
    "V": "plug-zap",
    "A": "gauge",
    "cm": "ruler",
    "m": "ruler",
    "mm": "ruler",
    "TB": "hard-drive",
    "Ω": "gauge",
    "°": "aperture",
    "Hz": "activity",
    "horas": "clock",
    "in": "monitor",
    "L": "cup-soda",
    "ml": "cup-soda",
    "kg": "scale",
    "g": "scale",
    "g/m²": "scale",
    "°C": "thermometer",
}


def field_def(
    key: str,
    label: str,
    *,
    type: str = "text",
    required: bool = False,
    section: str = "general",
    order: int = 0,
    placeholder: str = "",
    help: str = "",
    unit: str = "",
    unit_label: str = "",
    example: str = "",
    icon: str = "",
    options: Iterable[str] = (),
    quick_options: Iterable[str] = (),
    min: int | Decimal | None = None,
    max: int | Decimal | None = None,
    condition: dict[str, Any] | None = None,
) -> ProductTemplateField:
    if type not in SUPPORTED_FIELD_TYPES:
        raise ProductTemplateError(f"Tipo de campo no soportado: {type}")
    resolved_unit_label = unit_label or _UNIT_LABELS.get(unit, unit)
    resolved_example = example or _FIELD_EXAMPLES.get(key, "")
    resolved_placeholder = placeholder or (f"Ej. {resolved_example}" if resolved_example else "")
    resolved_help = help or _FIELD_HELP.get(key, "")
    if unit and not resolved_help:
        resolved_help = f"Ingresa el valor en {resolved_unit_label}; no incluyas la unidad."
    resolved_icon = icon or _FIELD_ICONS.get(key, "") or _UNIT_ICONS.get(unit, "")
    return ProductTemplateField(
        key=key,
        label=label,
        type=type,
        required=required,
        section=section,
        order=order,
        placeholder=resolved_placeholder,
        help=resolved_help,
        unit=unit,
        unit_label=resolved_unit_label,
        example=resolved_example,
        icon=resolved_icon,
        options=tuple(options),
        quick_options=tuple(quick_options),
        min=min,
        max=max,
        condition=condition,
    )


def axis_def(
    key: str,
    label: str,
    *,
    unit: str = "",
    suggestions: Iterable[str] = (),
    condition: dict[str, Any] | None = None,
    source_field: str = "",
    value_type: str = "text",
    default_for: Iterable[str] = (),
    is_visual: bool = False,
    is_listing_axis: bool = False,
) -> VariantAxis:
    return VariantAxis(
        key=key,
        label=label,
        unit=unit,
        suggestions=tuple(suggestions),
        condition=condition,
        source_field=source_field or key,
        value_type=value_type,
        default_for=tuple(default_for),
        is_visual=is_visual,
        is_listing_axis=is_listing_axis,
    )


def _common(*fields: ProductTemplateField) -> tuple[ProductTemplateField, ...]:
    return (
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=10),
        field_def("material", "Material", section="presentacion", order=20),
        *fields,
    )


def _electronics_phone() -> tuple[ProductTemplateField, ...]:
    _SMARTPHONE = ["Smartphone"]
    _SMARTPHONE_BASIC = ["Smartphone", "Teléfono básico"]
    _CHARGER = ["Cargador"]
    _CHARGER_CABLE = ["Cargador", "Cable"]
    _CABLE = ["Cable"]
    _PROTECTOR = ["Protector"]
    _PROTECTOR_SOPORTE_REPUESTO = ["Protector", "Soporte", "Repuesto"]
    _SOPORTE = ["Soporte"]
    _REPUESTO = ["Repuesto"]
    return _common(
        # Selector principal — siempre visible
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="tecnica", order=1,
                  options=("Smartphone", "Teléfono básico", "Cargador", "Cable", "Protector", "Soporte", "Repuesto", "Otro")),

        # Smartphone / Teléfono básico
        field_def("sistema_operativo", "Sistema operativo", section="tecnica", order=2,
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("ram_gb", "RAM", type="integer", section="tecnica", order=3, unit="GB", min=0, max=2048,
                  quick_options=("4 GB|4", "6 GB|6", "8 GB|8", "12 GB|12", "16 GB|16"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("almacenamiento_gb", "Almacenamiento", type="integer", section="tecnica", order=4, unit="GB", min=0, max=8192,
                  quick_options=("64 GB|64", "128 GB|128", "256 GB|256", "512 GB|512", "1 TB|1024"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("pantalla_pulgadas", "Tamaño de pantalla", type="decimal", section="pantalla", order=5, unit="in",
                  min=Decimal("0"), max=Decimal("30"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("camara_principal_mp", "Cámara principal", type="decimal", section="camara", order=6, unit="MP",
                  min=Decimal("0"), max=Decimal("500"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("bateria_mah", "Batería", type="integer", section="energia", order=7, unit="mAh", min=0, max=50000,
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),

        # Cargador
        field_def("potencia_w", "Potencia", type="integer", section="tecnica", order=8, unit="W", min=0, max=300,
                  quick_options=("5W|5", "10W|10", "15W|15", "18W|18", "25W|25", "45W|45", "65W|65"),
                  condition={"field": "tipo_producto", "values": _CHARGER}),
        field_def("voltaje", "Voltaje", type="decimal", section="tecnica", order=9, unit="V", min=Decimal("0"),
                  condition={"field": "tipo_producto", "values": _CHARGER}),
        field_def("corriente_a", "Corriente", type="decimal", section="tecnica", order=10, unit="A", min=Decimal("0"),
                  condition={"field": "tipo_producto", "values": _CHARGER}),

        # Cargador + Cable
        field_def("tipo_conector_salida", "Conector de salida", type="select", section="conectividad", order=11,
                  options=("USB-A", "USB-C", "Lightning", "Micro-USB", "Multi-puerto"),
                  condition={"field": "tipo_producto", "values": _CHARGER_CABLE}),
        field_def("carga_rapida", "Carga rápida", type="boolean", section="conectividad", order=12,
                  condition={"field": "tipo_producto", "values": _CHARGER_CABLE}),

        # Cable
        field_def("longitud_cm", "Longitud", type="decimal", section="tecnica", order=13, unit="cm", min=Decimal("0"),
                  quick_options=("100 cm|100", "150 cm|150", "200 cm|200"),
                  condition={"field": "tipo_producto", "values": _CABLE}),
        field_def("tipo_conector_entrada", "Conector de entrada", type="select", section="conectividad", order=14,
                  options=("USB-A", "USB-C", "Lightning", "Micro-USB"),
                  condition={"field": "tipo_producto", "values": _CABLE}),
        field_def("transferencia_datos", "Transferencia de datos", type="boolean", section="conectividad", order=15,
                  condition={"field": "tipo_producto", "values": _CABLE}),

        # Protector
        field_def("tipo_protector", "Tipo de protector", type="select", section="tecnica", order=16,
                  options=("Vidrio templado", "Silicona", "Policarbonato", "Cuero sintético", "Otro"),
                  condition={"field": "tipo_producto", "values": _PROTECTOR}),

        # Protector / Soporte / Repuesto
        field_def("modelo_compatible", "Modelos compatibles", section="compatibilidad", order=17,
                  placeholder="Ej. iPhone 15, Samsung Galaxy S24",
                  condition={"field": "tipo_producto", "values": _PROTECTOR_SOPORTE_REPUESTO}),

        # Soporte
        field_def("tipo_soporte", "Tipo de soporte", type="select", section="tecnica", order=18,
                  options=("Mesa", "Auto", "Pared", "Cuello/Flexible", "Otro"),
                  condition={"field": "tipo_producto", "values": _SOPORTE}),
        field_def("ajustable", "Ajustable", type="boolean", section="tecnica", order=19,
                  condition={"field": "tipo_producto", "values": _SOPORTE}),

        # Repuesto
        field_def("tipo_repuesto", "Tipo de repuesto", type="select", section="tecnica", order=20,
                  options=("Pantalla", "Batería", "Carcasa", "Botón/Switch", "Puerto", "Otro"),
                  condition={"field": "tipo_producto", "values": _REPUESTO}),
        field_def("numero_parte", "Número de parte", section="compatibilidad", order=21,
                  condition={"field": "tipo_producto", "values": _REPUESTO}),

        # Especificaciones opcionales ampliadas
        field_def("procesador", "Procesador", section="tecnica", order=22,
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("resolucion_pantalla", "Resolución de pantalla", section="pantalla", order=23,
                  placeholder="Ej. 1920x1080, 2400x1080",
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("frecuencia_hz", "Frecuencia de refresco", type="integer", section="pantalla", order=24,
                  unit="Hz", min=0, quick_options=("60 Hz|60", "90 Hz|90", "120 Hz|120", "144 Hz|144"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("red_movil", "Red móvil", type="multiselect", section="conectividad", order=25,
                  options=("2G", "3G", "4G LTE", "5G"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("configuracion_sim", "Configuración SIM", type="select", section="conectividad", order=26,
                  options=("SIM", "Dual SIM", "eSIM", "SIM + eSIM"),
                  condition={"field": "tipo_producto", "values": _SMARTPHONE_BASIC}),
        field_def("nfc", "NFC", type="boolean", section="conectividad", order=27,
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("proteccion_ip", "Protección IP", section="proteccion", order=28,
                  placeholder="Ej. IP67, IP68",
                  condition={"field": "tipo_producto", "values": _SMARTPHONE}),
        field_def("numero_puertos", "Número de puertos", type="integer", section="conectividad", order=29,
                  min=1, quick_options=("1", "2", "3", "4"),
                  condition={"field": "tipo_producto", "values": _CHARGER}),
        field_def("protocolos_carga", "Protocolos de carga", type="multiselect", section="conectividad", order=30,
                  options=("USB Power Delivery", "PPS", "Quick Charge", "Carga propietaria", "Otro"),
                  condition={"field": "tipo_producto", "values": _CHARGER}),
        field_def("potencia_max_w", "Potencia máxima", type="integer", section="tecnica", order=31,
                  unit="W", min=0,
                  quick_options=("20 W|20", "30 W|30", "60 W|60", "65 W|65", "100 W|100", "140 W|140", "240 W|240"),
                  condition={"field": "tipo_producto", "values": _CABLE}),
        field_def("velocidad_datos", "Velocidad de datos", section="conectividad", order=32,
                  placeholder="Ej. 480 Mbps, 5 Gbps, 10 Gbps",
                  condition={"field": "tipo_producto", "values": _CABLE}),
    )


def _electronics_computer() -> tuple[ProductTemplateField, ...]:
    _LAPTOP_DESKTOP_TABLET = ["Laptop", "Desktop", "Tablet"]
    _LAPTOP_DESKTOP = ["Laptop", "Desktop"]
    _LAPTOP_TABLET = ["Laptop", "Tablet"]
    _LAPTOP_TABLET_MONITOR = ["Laptop", "Tablet", "Monitor"]
    _LAPTOP = ["Laptop"]
    _TABLET = ["Tablet"]
    _MONITOR = ["Monitor"]
    _ACCESORIO = ["Accesorio"]
    return _common(
        field_def("tipo_equipo", "Tipo de equipo", type="select", required=True, section="tecnica", order=1,
                  options=("Laptop", "Desktop", "Tablet", "Monitor", "Accesorio")),

        # Laptop / Desktop / Tablet
        field_def("procesador", "Procesador", section="tecnica", order=2,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
        field_def("ram_gb", "RAM", type="integer", section="tecnica", order=3, unit="GB", min=0,
                  quick_options=("4 GB|4", "8 GB|8", "16 GB|16", "32 GB|32", "64 GB|64"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
        field_def("almacenamiento_gb", "Almacenamiento", type="integer", section="tecnica", order=4, unit="GB", min=0,
                  quick_options=("128 GB|128", "256 GB|256", "512 GB|512", "1 TB|1024", "2 TB|2048"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
        field_def("tipo_almacenamiento", "Tipo de almacenamiento", type="select", section="tecnica", order=5,
                  options=("SSD", "HDD", "SSD + HDD"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP}),
        field_def("sistema_operativo", "Sistema operativo", section="software", order=6,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),

        # Laptop / Tablet / Monitor
        field_def("pantalla_pulgadas", "Tamaño de pantalla", type="decimal", section="pantalla", order=7, unit="in",
                  min=Decimal("0"), max=Decimal("100"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET_MONITOR}),

        # Laptop / Tablet
        field_def("bateria_mah", "Batería", type="integer", section="energia", order=8, unit="mAh", min=0,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET}),

        # Tablet
        field_def("tiene_sim", "Ranura SIM", type="boolean", section="conectividad", order=9,
                  condition={"field": "tipo_equipo", "values": _TABLET}),

        # Laptop / Tablet / Monitor
        field_def("resolucion_pantalla", "Resolución", section="pantalla", order=10,
                  placeholder="Ej. 1920x1080, 2560x1440",
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET_MONITOR}),
        field_def("frecuencia_hz", "Frecuencia de refresco", type="integer", section="pantalla", order=11, unit="Hz", min=0,
                  quick_options=("60 Hz|60", "75 Hz|75", "120 Hz|120", "144 Hz|144", "165 Hz|165", "240 Hz|240"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET_MONITOR}),
        field_def("tipo_panel", "Tipo de panel", type="select", section="pantalla", order=12,
                  options=("IPS", "VA", "TN", "OLED"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET_MONITOR}),

        # Monitor
        field_def("tipo_conexion_monitor", "Conexiones disponibles", type="chips", section="conectividad", order=13,
                  help="Ej. HDMI, DisplayPort, VGA, USB-C",
                  condition={"field": "tipo_equipo", "values": _MONITOR}),

        # Accesorio
        field_def("tipo_accesorio", "Tipo de accesorio", section="tecnica", order=14,
                  placeholder="Ej. Teclado, Mouse, Hub USB",
                  quick_options=("Teclado", "Mouse", "Hub USB", "Dock", "Disco externo", "Base para laptop", "Adaptador", "Otro"),
                  condition={"field": "tipo_equipo", "values": _ACCESORIO}),

        # Especificaciones opcionales ampliadas
        field_def("gpu", "Tarjeta gráfica / GPU", section="tecnica", order=15,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP}),
        field_def("pantalla_tactil", "Pantalla táctil", type="boolean", section="pantalla", order=16,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET_MONITOR}),
        field_def("puertos", "Puertos", type="chips", section="conectividad", order=17,
                  quick_options=("USB-A", "USB-C", "Thunderbolt", "HDMI", "DisplayPort", "Ethernet", "microSD"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
        field_def("wifi", "Wi-Fi", type="select", section="conectividad", order=18,
                  options=("Wi-Fi 4", "Wi-Fi 5", "Wi-Fi 6", "Wi-Fi 6E", "Wi-Fi 7", "No aplica"),
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
        field_def("bluetooth_version", "Bluetooth", section="conectividad", order=19,
                  placeholder="Ej. 5.3",
                  condition={"field": "tipo_equipo", "values": _LAPTOP_DESKTOP_TABLET}),
    )


def _electronics_headphones() -> tuple[ProductTemplateField, ...]:
    _BLUETOOTH_MIXED = ["Bluetooth", "Mixta"]
    _WIRED = ["Cable", "USB", "Mixta"]
    return _common(
        field_def("tipo", "Tipo", type="select", required=True, section="audio", order=1,
                  options=("In-ear", "On-ear", "Over-ear", "Gaming", "Otro")),
        field_def("conexion", "Conexión", type="select", section="audio", order=2,
                  options=("Bluetooth", "Cable", "USB", "Mixta")),
        field_def("cancelacion_activa", "Cancelación activa", type="boolean", section="audio", order=3),
        field_def("microfono", "Micrófono", type="boolean", section="audio", order=4),
        field_def("autonomia_horas", "Autonomía", type="decimal", section="energia", order=5,
                  unit="horas", min=Decimal("0"),
                  condition={"field": "conexion", "values": _BLUETOOTH_MIXED}),
        field_def("surround", "Sonido envolvente (7.1)", type="boolean", section="audio", order=6,
                  condition={"field": "tipo", "values": ["Gaming"]}),
        field_def("plataformas", "Plataformas compatibles", type="chips", section="compatibilidad", order=7,
                  help="Ej. PC, PS5, Xbox, Switch",
                  quick_options=("PC", "PS4", "PS5", "Xbox One", "Xbox Series", "Nintendo Switch", "Móvil"),
                  condition={"field": "tipo", "values": ["Gaming"]}),
        field_def("version_bluetooth", "Versión de Bluetooth", section="conectividad", order=8,
                  placeholder="Ej. 5.3",
                  condition={"field": "conexion", "values": _BLUETOOTH_MIXED}),
        field_def("conector_fisico", "Conector físico", type="select", section="conectividad", order=9,
                  options=("3.5 mm", "USB-A", "USB-C", "Lightning", "Otro"),
                  condition={"field": "conexion", "values": _WIRED}),
        field_def("driver_mm", "Tamaño del driver", type="decimal", section="audio", order=10,
                  unit="mm", min=Decimal("0"),
                  quick_options=("6 mm|6", "8 mm|8", "10 mm|10", "40 mm|40", "50 mm|50")),
        field_def("impedancia_ohm", "Impedancia", type="integer", section="audio", order=11,
                  unit="Ω", min=0,
                  quick_options=("16 Ω|16", "32 Ω|32", "64 Ω|64", "80 Ω|80", "250 Ω|250")),
        field_def("proteccion_ip", "Protección IP", section="proteccion", order=12,
                  placeholder="Ej. IPX4, IPX5, IPX7",
                  condition={"field": "tipo", "values": ["In-ear"]}),
    )


def _electronics_camera() -> tuple[ProductTemplateField, ...]:
    _SEGURIDAD = ["Seguridad"]
    _FOTOGRAFICA = ["Fotográfica"]
    _DEPORTIVA = ["Deportiva"]
    _WEBCAM = ["Webcam"]
    _SEG_FOT_DEP = ["Seguridad", "Fotográfica", "Deportiva"]
    _SEG_DEP = ["Seguridad", "Deportiva"]
    _DEP_WEB = ["Deportiva", "Webcam"]
    _FOT_DEP = ["Fotográfica", "Deportiva"]
    _SEG_FOT_DEP_WEB = ["Seguridad", "Fotográfica", "Deportiva", "Webcam"]
    return _common(
        # Siempre visibles
        field_def("tipo_camara", "Tipo de cámara", type="select", required=True, section="imagen", order=1,
                  options=("Seguridad", "Fotográfica", "Deportiva", "Webcam", "Otro")),
        field_def("resolucion_mp", "Resolución", type="decimal", required=True, section="imagen", order=2,
                  unit="MP", min=Decimal("0"), max=Decimal("500")),

        # Seguridad / Fotográfica / Deportiva / Webcam
        field_def("resolucion_video", "Resolución de video", section="video", order=3,
                  placeholder="Ej. 1920x1080, 4K",
                  quick_options=("720p", "1080p", "2K", "4K", "8K"),
                  condition={"field": "tipo_camara", "values": _SEG_FOT_DEP_WEB}),

        # Seguridad
        field_def("vision_nocturna", "Visión nocturna", type="boolean", section="deteccion", order=4,
                  condition={"field": "tipo_camara", "values": _SEGURIDAD}),
        field_def("deteccion_movimiento", "Detección de movimiento", type="boolean", section="deteccion", order=5,
                  condition={"field": "tipo_camara", "values": _SEGURIDAD}),
        field_def("alimentacion", "Alimentación", section="alimentacion", order=6,
                  placeholder="Ej. Cable 12V, PoE, Batería",
                  condition={"field": "tipo_camara", "values": _SEGURIDAD}),

        # Seguridad / Deportiva
        field_def("proteccion_ip", "Protección IP", section="proteccion", order=7,
                  placeholder="Ej. IP66, IP67",
                  condition={"field": "tipo_camara", "values": _SEG_DEP}),

        # Seguridad / Fotográfica / Deportiva
        field_def("conectividad", "Conectividad", type="chips", section="conectividad", order=8,
                  help="Ej. Wi-Fi, Ethernet, Bluetooth, 4G",
                  quick_options=("Wi-Fi", "Ethernet", "Bluetooth", "4G", "USB"),
                  condition={"field": "tipo_camara", "values": _SEG_FOT_DEP}),

        # Fotográfica
        field_def("tipo_sensor", "Tipo de sensor", type="select", section="imagen", order=9,
                  options=("Full Frame", "APS-C", "Micro Cuatro Tercios", "1 pulgada", "Otro"),
                  condition={"field": "tipo_camara", "values": _FOTOGRAFICA}),
        field_def("montura_lente", "Montura de lente", section="imagen", order=10,
                  placeholder="Ej. Canon EF, Sony E, Nikon Z",
                  condition={"field": "tipo_camara", "values": _FOTOGRAFICA}),
        field_def("pantalla_abatible", "Pantalla abatible", type="boolean", section="imagen", order=11,
                  condition={"field": "tipo_camara", "values": _FOTOGRAFICA}),

        # Fotográfica / Deportiva
        field_def("estabilizacion", "Estabilización de imagen", type="boolean", section="imagen", order=12,
                  condition={"field": "tipo_camara", "values": _FOT_DEP}),

        # Deportiva / Webcam
        field_def("fps_video", "FPS de video", type="select", section="video", order=13,
                  options=("24fps", "30fps", "60fps", "120fps", "240fps"),
                  condition={"field": "tipo_camara", "values": _DEP_WEB}),

        # Deportiva
        field_def("bateria_mah", "Batería", type="integer", section="energia", order=14,
                  unit="mAh", min=0, max=50000,
                  condition={"field": "tipo_camara", "values": _DEPORTIVA}),

        # Webcam
        field_def("conexion_webcam", "Conexión", type="select", section="conectividad", order=15,
                  options=("USB-A", "USB-C"),
                  condition={"field": "tipo_camara", "values": _WEBCAM}),
        field_def("microfono", "Micrófono integrado", type="boolean", section="audio", order=16,
                  condition={"field": "tipo_camara", "values": _WEBCAM}),
        field_def("autofocus", "Enfoque automático", type="boolean", section="imagen", order=17,
                  condition={"field": "tipo_camara", "values": _WEBCAM}),
        field_def("tipo_fotografica", "Tipo de cámara fotográfica", type="select", section="imagen", order=18,
                  options=("Mirrorless", "DSLR", "Compacta", "Instantánea", "Otra"),
                  condition={"field": "tipo_camara", "values": _FOTOGRAFICA}),
        field_def("profundidad_agua_m", "Profundidad de resistencia al agua", type="decimal",
                  section="proteccion", order=19, unit="m", min=Decimal("0"),
                  condition={"field": "tipo_camara", "values": _DEPORTIVA}),
        field_def("campo_vision_grados", "Campo de visión", type="integer", section="imagen", order=20,
                  unit="°", min=0, max=360,
                  quick_options=("60°|60", "70°|70", "78°|78", "90°|90", "110°|110", "120°|120"),
                  condition={"field": "tipo_camara", "values": _WEBCAM}),
        field_def("tapa_privacidad", "Tapa de privacidad", type="boolean", section="proteccion", order=21,
                  condition={"field": "tipo_camara", "values": _WEBCAM}),
    )


def _electronics_security() -> tuple[ProductTemplateField, ...]:
    _CAMERA = ["Cámara de seguridad"]
    _RECORDERS = ["DVR", "NVR", "XVR / Grabador híbrido"]
    _RECORDER_KIT = [*_RECORDERS, "Kit de videovigilancia"]
    _NVR_XVR = ["NVR", "XVR / Grabador híbrido"]
    _KIT = ["Kit de videovigilancia"]
    _DOORBELL = ["Videoportero / Timbre inteligente"]
    _ACCESSORY = ["Accesorio de videovigilancia"]
    _CAMERA_DOORBELL = [*_CAMERA, *_DOORBELL]
    return _common(
        field_def(
            "tipo_seguridad",
            "Tipo de producto de seguridad",
            type="select",
            required=True,
            section="seguridad",
            order=1,
            options=(
                "Cámara de seguridad",
                "DVR",
                "NVR",
                "XVR / Grabador híbrido",
                "Kit de videovigilancia",
                "Videoportero / Timbre inteligente",
                "Accesorio de videovigilancia",
                "Otro",
            ),
        ),

        # Grabadores y kits
        field_def("canales", "Número de canales", type="integer", section="tecnica", order=2,
                  min=1, quick_options=("4", "8", "16", "32", "64"),
                  condition={"field": "tipo_seguridad", "values": _RECORDER_KIT}),
        field_def("resolucion_grabacion", "Resolución máxima de grabación", type="select",
                  section="video", order=3,
                  options=("1080p", "3 MP", "4 MP", "5 MP", "8 MP / 4K", "12 MP", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("compresion_video", "Compresión de video", type="multiselect", section="video", order=4,
                  options=("H.264", "H.264+", "H.265", "H.265+", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("bahias_hdd", "Bahías para disco", type="integer", section="tecnica", order=5,
                  min=0, quick_options=("1", "2", "4", "8"),
                  condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("capacidad_max_hdd_tb", "Capacidad máxima de almacenamiento", type="decimal",
                  section="tecnica", order=6, unit="TB", min=Decimal("0"),
                  quick_options=("1 TB|1", "2 TB|2", "4 TB|4", "6 TB|6", "8 TB|8", "10 TB|10", "16 TB|16", "20 TB|20"),
                  condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("disco_incluido", "Disco incluido", type="boolean", section="tecnica", order=7,
                  condition={"field": "tipo_seguridad", "values": _RECORDER_KIT}),
        field_def("capacidad_disco_incluido_tb", "Capacidad del disco incluido", type="decimal",
                  section="tecnica", order=8, unit="TB", min=Decimal("0"),
                  quick_options=("1 TB|1", "2 TB|2", "4 TB|4", "6 TB|6", "8 TB|8", "10 TB|10"),
                  condition={"field": "tipo_seguridad", "values": _RECORDER_KIT}),
        field_def("salidas_video", "Salidas de video", type="multiselect", section="video", order=9,
                  options=("HDMI", "VGA", "DisplayPort", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("puertos_red", "Puertos de red", type="integer", section="conectividad", order=10,
                  min=0, condition={"field": "tipo_seguridad", "values": _RECORDERS}),
        field_def("puertos_poe", "Puertos PoE", type="integer", section="conectividad", order=11,
                  min=0, quick_options=("4", "8", "16", "24", "32"),
                  condition={"field": "tipo_seguridad", "values": _NVR_XVR}),
        field_def("acceso_remoto", "Acceso remoto / aplicación", type="boolean",
                  section="conectividad", order=12,
                  condition={"field": "tipo_seguridad", "values": _RECORDER_KIT}),

        # Cámara individual
        field_def("formato_camara", "Formato de cámara", type="select", section="imagen", order=13,
                  options=("Bullet", "Domo", "Turret", "PTZ", "Fisheye", "Cubo / Interior", "Otra"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("tecnologia_camara", "Tecnología", type="select", section="tecnica", order=14,
                  options=("IP", "Analógica", "Wi-Fi", "Otra"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("resolucion_mp", "Resolución", type="decimal", section="imagen", order=15,
                  unit="MP", min=Decimal("0"), max=Decimal("500"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("lente_mm", "Lente / distancia focal", type="decimal", section="imagen", order=16,
                  unit="mm", min=Decimal("0"),
                  quick_options=("2.8 mm|2.8", "3.6 mm|3.6", "4 mm|4", "6 mm|6", "8 mm|8", "12 mm|12"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("distancia_ir_m", "Distancia de visión nocturna / IR", type="integer",
                  section="deteccion", order=17, unit="m", min=0,
                  quick_options=("10 m|10", "20 m|20", "30 m|30", "50 m|50", "80 m|80", "100 m|100"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("uso_instalacion", "Uso", type="select", section="uso", order=18,
                  options=("Interior", "Exterior", "Interior / Exterior"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("proteccion_ip", "Protección IP", section="proteccion", order=19,
                  placeholder="Ej. IP66, IP67",
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),

        # Cámara y videoportero
        field_def("resolucion_video", "Resolución de video", section="video", order=20,
                  quick_options=("720p", "1080p", "2K", "4K", "8K"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("vision_nocturna", "Visión nocturna", type="boolean", section="deteccion", order=21,
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("deteccion_movimiento", "Detección de movimiento", type="boolean",
                  section="deteccion", order=22,
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("audio_bidireccional", "Audio bidireccional", type="boolean", section="audio", order=23,
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("conectividad", "Conectividad", type="multiselect", section="conectividad", order=24,
                  options=("Ethernet", "Wi-Fi", "4G", "Bluetooth", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("almacenamiento_soportado", "Almacenamiento soportado", type="multiselect",
                  section="tecnica", order=25,
                  options=("MicroSD", "NVR", "DVR / XVR", "Nube", "NAS", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("alimentacion", "Alimentación", section="alimentacion", order=26,
                  placeholder="Ej. PoE, 12V DC, batería",
                  quick_options=("Batería", "Cableado", "PoE", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _CAMERA_DOORBELL}),
        field_def("microfono", "Micrófono", type="boolean", section="audio", order=27,
                  condition={"field": "tipo_seguridad", "values": _CAMERA}),
        field_def("onvif", "ONVIF", type="boolean", section="conectividad", order=28,
                  condition={"field": "tipo_seguridad", "values": [*_CAMERA, *_NVR_XVR]}),

        # Kit
        field_def("tipo_grabador", "Tipo de grabador", type="select", section="tecnica", order=29,
                  options=("DVR", "NVR", "XVR"),
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("cantidad_camaras", "Cantidad de cámaras incluidas", type="integer",
                  section="tecnica", order=30, min=1,
                  quick_options=("2", "4", "6", "8", "16"),
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("resolucion_camaras_mp", "Resolución de las cámaras", type="decimal",
                  section="imagen", order=31, unit="MP", min=Decimal("0"),
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("tipo_camaras_kit", "Formato de cámaras", type="select", section="imagen", order=32,
                  options=("Bullet", "Domo", "Turret", "Mixto", "Otro"),
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("cable_incluido", "Cable incluido", type="boolean", section="tecnica", order=33,
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("longitud_cable_m", "Longitud de cable incluida", type="decimal", section="tecnica", order=34,
                  unit="m", min=Decimal("0"),
                  quick_options=("10 m|10", "20 m|20", "30 m|30", "50 m|50", "100 m|100"),
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("fuente_alimentacion_incluida", "Fuente de alimentación incluida", type="boolean",
                  section="alimentacion", order=35,
                  condition={"field": "tipo_seguridad", "values": _KIT}),
        field_def("poe", "PoE", type="boolean", section="conectividad", order=36,
                  condition={"field": "tipo_seguridad", "values": [*_CAMERA, *_KIT]}),

        # Videoportero
        field_def("timbre_interior_incluido", "Timbre interior incluido", type="boolean",
                  section="tecnica", order=37,
                  condition={"field": "tipo_seguridad", "values": _DOORBELL}),

        # Accesorios
        field_def("tipo_accesorio_seguridad", "Tipo de accesorio", type="select",
                  section="tecnica", order=38,
                  options=(
                      "Fuente de alimentación", "Balun", "Conector", "Cable coaxial", "Cable UTP",
                      "Switch PoE", "Disco para videovigilancia", "Soporte / base",
                      "Caja de conexiones", "Rack", "Adaptador", "Otro",
                  ),
                  condition={"field": "tipo_seguridad", "values": _ACCESSORY}),
        field_def("compatibilidad", "Compatibilidad", section="compatibilidad", order=39,
                  condition={"field": "tipo_seguridad", "values": _ACCESSORY}),
        field_def("numero_parte", "Número de parte", section="compatibilidad", order=40,
                  condition={"field": "tipo_seguridad", "values": _ACCESSORY}),
    )


def _fashion_common() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo", "Tipo", required=True, section="prenda", order=1),
        field_def("genero", "Género", type="select", section="prenda", order=2, options=("Hombre", "Mujer", "Unisex", "Niños")),
        field_def("talla", "Talla", type="variant_attribute", required=True, section="tallas", order=3),
        field_def("sistema_talla", "Sistema de talla", type="select", section="tallas", order=4, options=("US", "EU", "LATAM", "Único")),
        field_def("tabla_tallas", "Tabla de tallas", type="size_table", section="tallas", order=5),
        field_def("cuidados", "Cuidados", type="chips", section="cuidados", order=6),
    )


def _home_common() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo", "Tipo", required=True, section="uso", order=1),
        field_def("habitacion", "Habitación o uso", section="uso", order=2),
        field_def("dimensiones", "Dimensiones", type="dimension", section="medidas", order=3, unit="cm"),
        field_def("cuidados", "Cuidados", type="textarea", section="cuidados", order=4),
    )


def _beauty_common() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo", "Tipo", required=True, section="producto", order=1),
        field_def("presentacion", "Presentación", section="producto", order=2),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=3, min=Decimal("0")),
        field_def("unidad", "Unidad", type="select", section="producto", order=4, options=("ml", "g", "unidades")),
        field_def("ingredientes", "Ingredientes", type="textarea", section="regulatorio", order=5),
        field_def("registro_sanitario", "Número de registro", section="regulatorio", order=6),
    )


def _automotive_common() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo", "Tipo", required=True, section="producto", order=1),
        field_def("numero_parte", "Número de parte", section="compatibilidad", order=2),
        field_def("compatibilidad_vehiculos", "Compatibilidad de vehículos", type="compatibility_table", section="compatibilidad", order=3),
        field_def("voltaje", "Voltaje", type="decimal", section="tecnica", order=4, unit="V", min=Decimal("0")),
        field_def("instrucciones", "Instrucciones", type="document", section="documentos", order=5),
    )


def _babies_common() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo", "Tipo", required=True, section="producto", order=1),
        field_def("edad_minima_meses", "Edad mínima", type="integer", section="seguridad", order=2, unit="meses", min=0, max=240),
        field_def("edad_maxima_meses", "Edad máxima", type="integer", section="seguridad", order=3, unit="meses", min=0, max=240),
        field_def("advertencias", "Advertencias", type="textarea", section="seguridad", order=4),
        field_def("lavable", "Lavable", type="boolean", section="cuidados", order=5),
    )


_FASHION_GENDERS = ("Hombre", "Mujer", "Unisex")
_FASHION_CLOTHING_TYPES = (
    "Camiseta", "Camisa / Blusa", "Polo", "Suéter / Jersey",
    "Sudadera / Hoodie", "Chaqueta / Abrigo", "Chaleco", "Pantalón",
    "Jean", "Short", "Falda", "Vestido", "Traje / Conjunto", "Leggings",
    "Conjunto deportivo", "Ropa interior", "Medias / Calcetines", "Pijama",
    "Traje de baño", "Otro",
)
_FASHION_FOOTWEAR_TYPES = (
    "Sneakers / Tenis", "Calzado deportivo", "Zapatos casuales",
    "Zapatos formales", "Sandalias", "Botas / Botines", "Tacones",
    "Mocasines", "Pantuflas", "Otro",
)
_FASHION_ACCESSORY_TYPES = (
    "Bolso", "Mochila", "Cartera / Billetera", "Cinturón",
    "Gorra / Sombrero", "Bufanda / Pañuelo", "Lentes de sol", "Guantes",
    "Corbata / Pajarita", "Accesorio para cabello", "Llavero", "Otro",
)
_FASHION_JEWELRY_WATCH_TYPES = (
    "Anillo", "Aretes", "Collar", "Cadena", "Pulsera", "Tobillera",
    "Dije / Colgante", "Broche", "Piercing", "Set de joyería",
    "Reloj analógico", "Reloj digital", "Reloj híbrido", "Smartwatch", "Otro",
)


def _for_product_types(*values: str) -> dict[str, Any]:
    return {"field": "tipo_producto", "values": list(values)}


def _fashion_clothing() -> tuple[ProductTemplateField, ...]:
    upper_body = (
        "Camiseta", "Camisa / Blusa", "Polo", "Suéter / Jersey",
        "Sudadera / Hoodie", "Chaqueta / Abrigo", "Chaleco", "Vestido",
    )
    closable = (
        "Camisa / Blusa", "Chaqueta / Abrigo", "Pantalón", "Jean", "Short",
        "Falda", "Vestido",
    )
    trousers = ("Pantalón", "Jean", "Short")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True,
                  section="producto", order=1, options=_FASHION_CLOTHING_TYPES, icon="shirt"),
        field_def("genero", "Género", type="select", section="prenda", order=2,
                  options=_FASHION_GENDERS),
        field_def("color_principal", "Color principal", type="color",
                  section="presentacion", order=3),
        field_def("talla", "Talla", type="variant_attribute", required=True,
                  section="tallas", order=4, quick_options=("XS", "S", "M", "L", "XL", "XXL")),
        field_def("sistema_talla", "Sistema de talla", type="select", section="tallas",
                  order=5, options=("Internacional", "US", "EU", "LATAM", "Otro")),
        field_def("tipo_talla", "Tipo de talla", type="select", section="tallas",
                  order=6, options=("Alfabética", "Numérica", "Única", "Otra")),
        field_def("material_principal", "Material principal", type="select",
                  section="materiales", order=7, options=(
                      "Algodón", "Poliéster", "Lana", "Lino", "Viscosa / Rayón",
                      "Nylon / Poliamida", "Elastano", "Denim", "Cuero",
                      "Cuero sintético", "Mezcla", "Otro",
                  )),
        field_def("composicion", "Composición", type="chips", section="materiales",
                  order=8, help="Ej. Algodón 95%, Elastano 5%", quick_options=(
                      "Algodón", "Poliéster", "Elastano", "Lana", "Lino", "Viscosa", "Nylon",
                  )),
        field_def("ajuste", "Ajuste", type="select", section="prenda", order=9,
                  options=("Regular", "Slim", "Oversize", "Relaxed", "Ajustado", "Otro")),
        field_def("temporada", "Temporada", type="select", section="prenda", order=10,
                  options=("Todo el año", "Clima cálido", "Clima frío", "Entretiempo")),
        field_def("estilo", "Estilo", type="select", section="prenda", order=11,
                  options=("Casual", "Formal", "Deportivo", "Trabajo", "Fiesta", "Otro")),
        field_def("elasticidad", "Elasticidad", type="select", section="prenda", order=12,
                  options=("Sin elasticidad", "Ligera", "Media", "Alta")),
        field_def("cuidados", "Cuidados", type="multiselect", section="cuidados", order=13,
                  options=(
                      "Lavar a mano", "Lavado a máquina", "Lavar con agua fría",
                      "Lavar con colores similares", "No usar blanqueador", "No usar secadora",
                      "Secar a la sombra", "Secar en plano", "Planchar a baja temperatura",
                      "No planchar", "Limpieza en seco", "No limpiar en seco",
                  )),
        field_def("manga", "Largo de manga", type="select", section="prenda", order=14,
                  options=("Sin mangas", "Corta", "Tres cuartos", "Larga", "Otro"),
                  condition=_for_product_types(*upper_body)),
        field_def("cuello_escote", "Cuello / escote", type="select", section="prenda", order=15,
                  options=("Redondo", "V", "Polo", "Camisa", "Cuello alto", "Halter", "Sin cuello", "Otro"),
                  condition=_for_product_types(*upper_body)),
        field_def("tipo_cierre", "Tipo de cierre", type="select", section="prenda", order=16,
                  options=("Sin cierre", "Botones", "Cremallera", "Broches", "Cordón", "Hebilla", "Otro"),
                  condition=_for_product_types(*closable)),
        field_def("capucha", "Capucha", type="boolean", section="prenda", order=17,
                  condition=_for_product_types("Sudadera / Hoodie", "Chaqueta / Abrigo")),
        field_def("largo_prenda", "Largo de prenda", type="select", section="medidas", order=18,
                  options=("Mini", "Corto", "Midi", "Largo", "Maxi", "Otro"),
                  condition=_for_product_types("Vestido", "Falda")),
        field_def("corte", "Tipo de corte", type="select", section="prenda", order=19,
                  options=("Recto", "Slim", "Skinny", "Wide leg", "Bootcut", "Flare", "Cargo", "Otro"),
                  condition=_for_product_types(*trousers)),
        field_def("tiro", "Tiro", type="select", section="prenda", order=20,
                  options=("Bajo", "Medio", "Alto"),
                  condition=_for_product_types(*trousers, "Falda")),
        field_def("entrepierna_cm", "Entrepierna", type="decimal", unit="cm",
                  section="medidas", order=21, min=Decimal("0"),
                  condition=_for_product_types("Pantalón", "Jean")),
        field_def("impermeable", "Impermeable", type="boolean", section="prenda", order=22,
                  condition=_for_product_types("Chaqueta / Abrigo")),
        field_def("bolsillos", "Bolsillos", type="boolean", section="prenda", order=23,
                  condition=_for_product_types("Chaqueta / Abrigo", *trousers)),
        field_def("forro", "Forro", type="boolean", section="prenda", order=24,
                  condition=_for_product_types("Chaqueta / Abrigo", "Vestido")),
    )


def _fashion_footwear() -> tuple[ProductTemplateField, ...]:
    footwear_materials = (
        "Cuero", "Cuero sintético", "Textil", "Malla", "Gamuza", "Caucho", "EVA", "Otro",
    )
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True,
                  section="producto", order=1, options=_FASHION_FOOTWEAR_TYPES, icon="footprints"),
        field_def("genero", "Género", type="select", section="calzado", order=2,
                  options=_FASHION_GENDERS),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=3),
        field_def("talla", "Talla", type="variant_attribute", required=True, section="tallas",
                  order=4, quick_options=("35", "36", "37", "38", "39", "40", "41", "42", "43", "44")),
        field_def("sistema_talla", "Sistema de talla", type="select", section="tallas", order=5,
                  options=("US", "EU", "UK", "LATAM", "Otro")),
        field_def("longitud_pie_cm", "Longitud del pie", type="decimal", unit="cm",
                  section="medidas", order=6, min=Decimal("0"),
                  quick_options=("22", "23", "24", "25", "26", "27", "28")),
        field_def("ancho_horma", "Ancho de horma", type="select", section="calzado", order=7,
                  options=("Estrecha", "Regular", "Ancha")),
        field_def("material_exterior", "Material exterior", type="select", section="materiales",
                  order=8, options=footwear_materials),
        field_def("material_forro", "Material del forro", type="select", section="materiales",
                  order=9, options=footwear_materials),
        field_def("material_plantilla", "Material de la plantilla", type="select",
                  section="materiales", order=10, options=footwear_materials),
        field_def("material_suela", "Material de la suela", type="select", section="materiales",
                  order=11, options=("Caucho", "EVA", "TPU", "PU", "Cuero", "Otro")),
        field_def("tipo_cierre", "Tipo de cierre", type="select", section="calzado", order=12,
                  options=("Cordones", "Velcro", "Hebilla", "Cremallera", "Slip-on / Sin cierre", "Otro")),
        field_def("temporada", "Temporada", type="select", section="calzado", order=13,
                  options=("Todo el año", "Clima cálido", "Clima frío", "Lluvia / Entretiempo")),
        field_def("altura_tacon_cm", "Altura del tacón", type="decimal", unit="cm",
                  section="medidas", order=14, min=Decimal("0"),
                  condition=_for_product_types("Tacones", "Zapatos formales")),
        field_def("altura_plataforma_cm", "Altura de la plataforma", type="decimal", unit="cm",
                  section="medidas", order=15, min=Decimal("0"),
                  condition=_for_product_types("Tacones", "Sandalias", "Botas / Botines", "Sneakers / Tenis")),
        field_def("altura_cana_cm", "Altura de la caña", type="decimal", unit="cm",
                  section="medidas", order=16, min=Decimal("0"),
                  condition=_for_product_types("Botas / Botines")),
        field_def("impermeable", "Impermeable", type="boolean", section="calzado", order=17,
                  condition=_for_product_types("Botas / Botines", "Calzado deportivo")),
        field_def("uso_deportivo", "Uso deportivo", type="multiselect", section="uso", order=18,
                  options=("Running", "Training / Gimnasio", "Fútbol", "Senderismo / Trekking", "Tenis / Pádel", "Skate", "Otro"),
                  condition=_for_product_types("Calzado deportivo")),
    )


def _fashion_bags_accessories() -> tuple[ProductTemplateField, ...]:
    bags = ("Bolso", "Mochila")
    measured = (*bags, "Cartera / Billetera")
    sized = ("Cinturón", "Gorra / Sombrero", "Guantes")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True,
                  section="producto", order=1, options=_FASHION_ACCESSORY_TYPES, icon="briefcase"),
        field_def("genero", "Género", type="select", section="producto", order=2,
                  options=_FASHION_GENDERS),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=3),
        field_def("material", "Material", type="select", section="materiales", order=4,
                  options=("Cuero", "Cuero sintético", "Textil", "Nylon", "Poliéster", "Algodón", "Metal", "Plástico / Resina", "Acetato", "Mezcla", "Otro"),
                  icon="layers"),
        field_def("estilo", "Estilo", type="select", section="producto", order=5,
                  options=("Casual", "Formal", "Deportivo", "Viaje", "Trabajo", "Fiesta", "Otro")),
        field_def("talla", "Talla", type="variant_attribute", section="tallas", order=6,
                  quick_options=("S", "M", "L", "Única"), condition=_for_product_types(*sized)),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=7,
                  min=Decimal("0"), condition=_for_product_types(*measured)),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=8,
                  min=Decimal("0"), condition=_for_product_types(*measured, "Cinturón", "Bufanda / Pañuelo")),
        field_def("profundidad_cm", "Profundidad", type="decimal", unit="cm", section="medidas", order=9,
                  min=Decimal("0"), condition=_for_product_types(*measured)),
        field_def("tipo_cierre", "Tipo de cierre", type="select", section="producto", order=10,
                  options=("Cremallera", "Broche", "Hebilla", "Magnético", "Cordón", "Sin cierre", "Otro"),
                  condition=_for_product_types(*bags)),
        field_def("tipo_correa", "Tipo de correa", type="select", section="producto", order=11,
                  options=("Asa", "Hombro", "Bandolera", "Mochila", "Mixta", "Otro"),
                  condition=_for_product_types(*bags)),
        field_def("correa_ajustable", "Correa ajustable", type="boolean", section="producto", order=12,
                  condition=_for_product_types(*bags)),
        field_def("numero_compartimentos", "Número de compartimentos", type="integer",
                  section="producto", order=13, min=1,
                  condition=_for_product_types(*bags, "Cartera / Billetera")),
        field_def("compartimento_laptop", "Compartimento para laptop", type="boolean",
                  section="producto", order=14, condition=_for_product_types(*bags)),
        field_def("tamano_laptop_pulgadas", "Tamaño de laptop", type="decimal", unit="in",
                  section="medidas", order=15, min=Decimal("0"),
                  quick_options=("13", "14", "15.6", "16", "17", "17.3"),
                  condition=_for_product_types(*bags)),
        field_def("capacidad_litros", "Capacidad", type="decimal", unit="L", section="medidas",
                  order=16, min=Decimal("0"), quick_options=("10", "15", "20", "25", "30", "40"),
                  condition=_for_product_types(*bags)),
        field_def("resistente_agua", "Resistente al agua", type="boolean", section="producto",
                  order=17, condition=_for_product_types(*bags)),
        field_def("proteccion_rfid", "Protección RFID", type="boolean", section="producto",
                  order=18, condition=_for_product_types("Cartera / Billetera")),
        field_def("longitud_cm", "Longitud", type="decimal", unit="cm", section="medidas", order=19,
                  min=Decimal("0"), condition=_for_product_types("Cinturón", "Bufanda / Pañuelo")),
        field_def("material_hebilla", "Material de la hebilla", type="select", section="materiales",
                  order=20, options=("Metal", "Acero inoxidable", "Latón", "Aleación", "Plástico", "Otro"),
                  condition=_for_product_types("Cinturón")),
        field_def("tipo_hebilla", "Tipo de hebilla", type="select", section="producto", order=21,
                  options=("Clásica", "Automática", "Doble anilla", "Hebilla de placa", "Otra"),
                  condition=_for_product_types("Cinturón")),
        field_def("circunferencia_cm", "Circunferencia", type="decimal", unit="cm",
                  section="medidas", order=22, min=Decimal("0"),
                  quick_options=("54", "56", "58", "60", "62"),
                  condition=_for_product_types("Gorra / Sombrero")),
        field_def("ajustable", "Ajustable", type="boolean", section="producto", order=23,
                  condition=_for_product_types("Gorra / Sombrero")),
        field_def("forma_montura", "Forma de montura", type="select", section="producto", order=24,
                  options=("Aviador", "Redonda", "Cuadrada", "Rectangular", "Cat-eye", "Wayfarer", "Otra"),
                  condition=_for_product_types("Lentes de sol")),
        field_def("material_montura", "Material de montura", type="select", section="materiales", order=25,
                  options=("Metal", "Acetato", "Plástico", "Titanio", "Mixto", "Otro"),
                  condition=_for_product_types("Lentes de sol")),
        field_def("material_lente", "Material de lente", type="select", section="materiales", order=26,
                  options=("Policarbonato", "Cristal", "Resina", "Otro"),
                  condition=_for_product_types("Lentes de sol")),
        field_def("color_lente", "Color de lente", section="presentacion", order=27,
                  condition=_for_product_types("Lentes de sol")),
        field_def("proteccion_uv", "Protección UV", type="select", section="producto", order=28,
                  options=("UV400", "UVA / UVB", "No especificada", "Otra"),
                  condition=_for_product_types("Lentes de sol")),
        field_def("polarizado", "Polarizado", type="boolean", section="producto", order=29,
                  condition=_for_product_types("Lentes de sol")),
        field_def("pantalla_tactil", "Compatible con pantalla táctil", type="boolean",
                  section="producto", order=30, condition=_for_product_types("Guantes")),
        field_def("subtipo_accesorio_cabello", "Subtipo de accesorio para cabello", type="select",
                  section="producto", order=31,
                  options=("Diadema", "Pinza", "Pasador", "Banda", "Scrunchie", "Liga", "Otro"),
                  condition=_for_product_types("Accesorio para cabello")),
    )


def _fashion_jewelry_watches() -> tuple[ProductTemplateField, ...]:
    watch_types = ("Reloj analógico", "Reloj digital", "Reloj híbrido", "Smartwatch")
    jewelry_types = tuple(
        value for value in _FASHION_JEWELRY_WATCH_TYPES if value not in watch_types
    )
    chains = ("Collar", "Cadena", "Pulsera", "Tobillera")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True,
                  section="producto", order=1, options=_FASHION_JEWELRY_WATCH_TYPES, icon="gem"),
        field_def("genero", "Género", type="select", section="producto", order=2,
                  options=_FASHION_GENDERS),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=3),
        field_def("clasificacion_joyeria", "Clasificación de joyería", type="select",
                  section="producto", order=4, options=("Joyería", "Bisutería"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("material_metal", "Material / metal principal", type="select",
                  section="materiales", order=5,
                  options=("Oro", "Plata", "Acero inoxidable", "Titanio", "Latón", "Aleación", "Cuero", "Textil", "Plástico / Resina", "Otro"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("ley_metal", "Ley / pureza", type="select", section="materiales", order=6,
                  options=("375", "585", "750", "925", "950", "999", "Otra", "No aplica"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("revestimiento", "Revestimiento", type="select", section="materiales", order=7,
                  options=("Sin revestimiento", "Chapado en oro", "Baño de oro", "Rodio", "Plata", "Otro"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("piedra_principal", "Piedra principal", type="select", section="materiales", order=8,
                  options=("Sin piedra", "Diamante", "Circonita", "Perla", "Esmeralda", "Rubí", "Zafiro", "Cristal", "Otra"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("origen_piedra", "Origen de la piedra", type="select", section="materiales", order=9,
                  options=("Natural", "Sintética", "Imitación", "No aplica", "No especificado"),
                  condition=_for_product_types(*jewelry_types)),
        field_def("peso_g", "Peso", type="decimal", unit="g", section="medidas", order=10,
                  min=Decimal("0"), condition=_for_product_types(*jewelry_types)),
        field_def("hipoalergenico", "Hipoalergénico", type="boolean", section="producto", order=11,
                  condition=_for_product_types(*jewelry_types)),
        field_def("talla", "Talla", type="variant_attribute", section="tallas", order=12,
                  quick_options=("5", "6", "7", "8", "9", "10"),
                  condition=_for_product_types("Anillo")),
        field_def("diametro_interno_mm", "Diámetro interno", type="decimal", unit="mm",
                  section="medidas", order=13, min=Decimal("0"),
                  quick_options=("14.9", "15.7", "16.5", "17.3", "18.1", "18.9"),
                  condition=_for_product_types("Anillo")),
        field_def("longitud_cm", "Longitud", type="decimal", unit="cm", section="medidas",
                  order=14, min=Decimal("0"), quick_options=("40", "45", "50", "55", "60"),
                  condition=_for_product_types(*chains)),
        field_def("tipo_cierre_joyeria", "Tipo de cierre", type="select", section="producto",
                  order=15,
                  options=("Mosquetón", "Reasa", "Caja", "Magnético", "Gancho", "Sin cierre", "Presión", "Rosca", "Palanca", "Otro"),
                  condition=_for_product_types(*chains, "Aretes")),
        field_def("grosor_mm", "Grosor", type="decimal", unit="mm", section="medidas",
                  order=16, min=Decimal("0"), condition=_for_product_types(*chains, "Piercing")),
        field_def("tipo_arete", "Tipo de arete", type="select", section="producto", order=17,
                  options=("Botón", "Aro", "Colgante", "Trepador", "Ear cuff", "Otro"),
                  condition=_for_product_types("Aretes")),
        field_def("cantidad_piezas", "Cantidad de piezas", type="select", section="producto", order=18,
                  options=("Par", "Unidad"), condition=_for_product_types("Aretes")),
        field_def("largo_mm", "Largo", type="decimal", unit="mm", section="medidas", order=19,
                  min=Decimal("0"), condition=_for_product_types("Aretes", "Piercing")),
        field_def("zona_piercing", "Zona del piercing", type="select", section="producto", order=20,
                  options=("Oreja", "Nariz", "Labio", "Ceja", "Ombligo", "Lengua", "Otra"),
                  condition=_for_product_types("Piercing")),
        field_def("numero_piezas", "Número de piezas", type="integer", min=1,
                  section="producto", order=21, condition=_for_product_types("Set de joyería")),
        field_def("material_caja", "Material de la caja", type="select", section="materiales", order=22,
                  options=("Acero inoxidable", "Titanio", "Aluminio", "Latón", "Plástico / Resina", "Cerámica", "Otro"),
                  condition=_for_product_types(*watch_types)),
        field_def("material_correa", "Material de la correa", type="select", section="materiales", order=23,
                  options=("Acero inoxidable", "Titanio", "Cuero", "Cuero sintético", "Silicona", "Caucho", "Nylon / Textil", "Cerámica", "Otro"),
                  condition=_for_product_types(*watch_types)),
        field_def("material_cristal", "Material del cristal", type="select", section="materiales", order=24,
                  options=("Mineral", "Zafiro", "Acrílico", "Plástico", "Otro"),
                  condition=_for_product_types(*watch_types)),
        field_def("movimiento", "Movimiento", type="select", section="tecnica", order=25,
                  options=("Cuarzo", "Automático", "Mecánico", "Solar", "Kinetic", "Digital", "Otro"),
                  condition=_for_product_types(*watch_types)),
        field_def("forma_caja", "Forma de la caja", type="select", section="presentacion", order=26,
                  options=("Redonda", "Cuadrada", "Rectangular", "Otra"),
                  condition=_for_product_types(*watch_types)),
        field_def("diametro_caja_mm", "Diámetro de la caja", type="decimal", unit="mm",
                  section="medidas", order=27, min=Decimal("0"), quick_options=("36", "38", "40", "42", "44", "46"),
                  condition=_for_product_types(*watch_types)),
        field_def("grosor_caja_mm", "Grosor de la caja", type="decimal", unit="mm",
                  section="medidas", order=28, min=Decimal("0"), quick_options=("8", "10", "12", "14"),
                  condition=_for_product_types(*watch_types)),
        field_def("ancho_correa_mm", "Ancho de la correa", type="decimal", unit="mm",
                  section="medidas", order=29, min=Decimal("0"), quick_options=("18", "20", "22", "24"),
                  condition=_for_product_types(*watch_types)),
        field_def("longitud_correa_mm", "Longitud de la correa", type="decimal", unit="mm",
                  section="medidas", order=30, min=Decimal("0"), quick_options=("180", "200", "220"),
                  condition=_for_product_types(*watch_types)),
        field_def("resistencia_agua", "Resistencia al agua", type="select", section="tecnica", order=31,
                  options=("No especificada", "3 ATM", "5 ATM", "10 ATM", "20 ATM", "30 ATM", "Otra"),
                  condition=_for_product_types(*watch_types)),
        field_def("cronografo", "Cronógrafo", type="boolean", section="tecnica", order=32,
                  condition=_for_product_types(*watch_types)),
        field_def("calendario", "Fecha / calendario", type="boolean", section="tecnica", order=33,
                  condition=_for_product_types(*watch_types)),
        field_def("funciones_reloj", "Funciones del reloj", type="multiselect", section="tecnica", order=34,
                  options=("Alarma", "Cronómetro", "Temporizador", "Calendario", "Segundo huso horario", "Iluminación", "Otro"),
                  condition=_for_product_types(*watch_types)),
        field_def("compatibilidad_so", "Compatibilidad de sistema operativo", type="multiselect",
                  section="compatibilidad", order=35, options=("Android", "iOS"),
                  condition=_for_product_types("Smartwatch")),
        field_def("bluetooth_version", "Versión de Bluetooth", section="conectividad", order=36,
                  placeholder="Ej. 5.3", condition=_for_product_types("Smartwatch")),
        field_def("wifi", "Wi-Fi", type="boolean", section="conectividad", order=37,
                  condition=_for_product_types("Smartwatch")),
        field_def("gps", "GPS", type="boolean", section="conectividad", order=38,
                  condition=_for_product_types("Smartwatch")),
        field_def("nfc", "NFC", type="boolean", section="conectividad", order=39,
                  condition=_for_product_types("Smartwatch")),
        field_def("autonomia_horas", "Autonomía", type="decimal", unit="horas", section="energia",
                  order=40, min=Decimal("0"), condition=_for_product_types("Smartwatch")),
        field_def("pantalla_pulgadas", "Tamaño de pantalla", type="decimal", unit="in", section="pantalla",
                  order=41, min=Decimal("0"), condition=_for_product_types("Smartwatch")),
    )


_HOME_DECOR_TYPES = (
    "Cuadro / Lámina", "Decoración de pared", "Espejo", "Reloj decorativo",
    "Florero / Jarrón", "Figura / Adorno", "Portavelas / Candelabro", "Vela",
    "Planta artificial", "Lámpara decorativa", "Guirnalda / Luz decorativa", "Otro",
)
_HOME_KITCHEN_TYPES = (
    "Olla / Cacerola", "Sartén", "Wok", "Molde / Bandeja para horno", "Cuchillo",
    "Tabla de cortar", "Utensilio de cocina", "Colador / Escurridor", "Rallador / Pelador",
    "Recipiente para alimentos", "Botella / Termo", "Taza / Vaso", "Plato / Bowl",
    "Cubiertos", "Vajilla / Set de comedor", "Secaplatos / Organizador de cocina", "Otro",
)


def _home_decor_lighting() -> tuple[ProductTemplateField, ...]:
    wall = ("Cuadro / Lámina", "Decoración de pared")
    powered = ("Reloj decorativo", "Lámpara decorativa", "Guirnalda / Luz decorativa")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_DECOR_TYPES, icon="house"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3,
                  options=("Madera", "Metal", "Vidrio", "Cerámica", "Porcelana", "Plástico / Resina", "Textil", "Papel / Cartón", "Piedra", "Cera", "Mixto", "Otro")),
        field_def("estilo", "Estilo", type="select", section="presentacion", order=4,
                  options=("Moderno", "Minimalista", "Clásico", "Industrial", "Rústico", "Boho", "Infantil", "Otro")),
        field_def("habitacion_uso", "Habitación / uso", type="multiselect", section="uso", order=5,
                  options=("Sala", "Dormitorio", "Comedor", "Cocina", "Baño", "Oficina", "Pasillo", "Exterior", "Otro")),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=6, min=Decimal("0")),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=7, min=Decimal("0")),
        field_def("profundidad_cm", "Profundidad", type="decimal", unit="cm", section="medidas", order=8, min=Decimal("0")),
        field_def("forma", "Forma", type="select", section="presentacion", order=9, options=("Redonda", "Cuadrada", "Rectangular", "Ovalada", "Irregular", "Otra")),
        field_def("uso_ubicacion", "Uso", type="select", section="uso", order=10, options=("Interior", "Exterior", "Interior y exterior")),
        field_def("orientacion", "Orientación", type="select", section="presentacion", order=11, options=("Vertical", "Horizontal", "Cuadrada", "Otra"), condition=_for_product_types(*wall)),
        field_def("tipo_montaje", "Tipo de montaje", type="select", section="uso", order=12, options=("Pared", "Sobremesa", "Adhesivo", "Colgante", "Otro"), condition=_for_product_types(*wall, "Espejo")),
        field_def("enmarcado", "Enmarcado", type="boolean", section="presentacion", order=13, condition=_for_product_types("Cuadro / Lámina")),
        field_def("con_marco", "Con marco", type="boolean", section="presentacion", order=14, condition=_for_product_types("Espejo")),
        field_def("mecanismo_reloj", "Mecanismo del reloj", type="select", section="tecnica", order=15, options=("Cuarzo", "Mecánico", "Digital", "Otro"), condition=_for_product_types("Reloj decorativo")),
        field_def("alimentacion", "Alimentación", type="select", section="alimentacion", order=16, options=("Pilas", "USB", "Corriente eléctrica", "Batería / Pilas", "Solar", "Otra"), condition=_for_product_types(*powered)),
        field_def("tipo_cera", "Tipo de cera", type="select", section="materiales", order=17, options=("Parafina", "Soya", "Cera de abeja", "Coco", "Mezcla", "Otra"), condition=_for_product_types("Vela")),
        field_def("aroma", "Aroma", section="producto", order=18, condition=_for_product_types("Vela")),
        field_def("duracion_horas", "Duración estimada", type="decimal", unit="horas", section="tecnica", order=19, min=Decimal("0"), condition=_for_product_types("Vela")),
        field_def("tipo_planta", "Tipo de planta", section="producto", order=20, condition=_for_product_types("Planta artificial")),
        field_def("voltaje_v", "Voltaje", type="decimal", unit="V", section="alimentacion", order=21, min=Decimal("0"), condition=_for_product_types("Lámpara decorativa")),
        field_def("potencia_w", "Potencia", type="decimal", unit="W", section="alimentacion", order=22, min=Decimal("0"), condition=_for_product_types("Lámpara decorativa")),
        field_def("tipo_casquillo", "Tipo de casquillo", type="select", section="alimentacion", order=23, options=("E27", "E14", "GU10", "G9", "LED integrado", "Otro", "No aplica"), condition=_for_product_types("Lámpara decorativa")),
        field_def("bombilla_incluida", "Bombilla incluida", type="boolean", section="alimentacion", order=24, condition=_for_product_types("Lámpara decorativa")),
        field_def("regulable", "Regulable", type="boolean", section="alimentacion", order=25, condition=_for_product_types("Lámpara decorativa")),
        field_def("longitud_m", "Longitud", type="decimal", unit="m", section="medidas", order=26, min=Decimal("0"), condition=_for_product_types("Guirnalda / Luz decorativa")),
        field_def("cantidad_luces", "Cantidad de luces", type="integer", section="tecnica", order=27, min=1, condition=_for_product_types("Guirnalda / Luz decorativa")),
        field_def("uso_exterior", "Uso exterior", type="boolean", section="uso", order=28, condition=_for_product_types("Guirnalda / Luz decorativa")),
    )


def _home_kitchen_dining() -> tuple[ProductTemplateField, ...]:
    cookware = ("Olla / Cacerola", "Sartén", "Wok")
    ml_types = ("Recipiente para alimentos", "Botella / Termo", "Taza / Vaso")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_KITCHEN_TYPES, icon="cooking-pot"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3, options=("Acero inoxidable", "Aluminio", "Hierro fundido", "Vidrio", "Cerámica", "Porcelana", "Silicona", "Plástico", "Madera", "Bambú", "Acero al carbono", "Otro")),
        field_def("cantidad_piezas", "Cantidad de piezas", type="integer", section="producto", order=4, min=1),
        field_def("apto_lavavajillas", "Apto para lavavajillas", type="boolean", section="cuidados", order=5),
        field_def("diametro_cm", "Diámetro", type="decimal", unit="cm", section="medidas", order=6, min=Decimal("0"), condition=_for_product_types(*cookware, "Colador / Escurridor", "Plato / Bowl")),
        field_def("diametro_base_cm", "Diámetro de la base", type="decimal", unit="cm", section="medidas", order=7, min=Decimal("0"), condition=_for_product_types(*cookware)),
        field_def("tapa_incluida", "Tapa incluida", type="boolean", section="producto", order=8, condition=_for_product_types(*cookware)),
        field_def("revestimiento_antiadherente", "Revestimiento antiadherente", type="boolean", section="producto", order=9, condition=_for_product_types(*cookware)),
        field_def("mango_desmontable", "Mango desmontable", type="boolean", section="producto", order=10, condition=_for_product_types(*cookware)),
        field_def("compatibilidad_coccion", "Compatibilidad de cocción", type="multiselect", section="uso", order=11, options=("Gas", "Inducción", "Eléctrica", "Vitrocerámica"), condition=_for_product_types(*cookware)),
        field_def("capacidad_l", "Capacidad", type="decimal", unit="L", section="medidas", order=12, min=Decimal("0"), quick_options=("1", "1.5", "2", "3", "4", "5", "6"), condition=_for_product_types("Olla / Cacerola", "Wok")),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=13, min=Decimal("0"), condition=_for_product_types("Molde / Bandeja para horno", "Secaplatos / Organizador de cocina")),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=14, min=Decimal("0"), condition=_for_product_types("Molde / Bandeja para horno", "Tabla de cortar", "Secaplatos / Organizador de cocina")),
        field_def("largo_cm", "Largo", type="decimal", unit="cm", section="medidas", order=15, min=Decimal("0"), condition=_for_product_types("Molde / Bandeja para horno", "Tabla de cortar")),
        field_def("profundidad_cm", "Profundidad", type="decimal", unit="cm", section="medidas", order=16, min=Decimal("0"), condition=_for_product_types("Plato / Bowl", "Secaplatos / Organizador de cocina")),
        field_def("grosor_cm", "Grosor", type="decimal", unit="cm", section="medidas", order=17, min=Decimal("0"), condition=_for_product_types("Tabla de cortar")),
        field_def("apto_horno", "Apto para horno", type="boolean", section="uso", order=18, condition=_for_product_types("Molde / Bandeja para horno")),
        field_def("temperatura_max_c", "Temperatura máxima", type="integer", unit="°C", section="uso", order=19, min=0, condition=_for_product_types("Molde / Bandeja para horno")),
        field_def("tipo_cuchillo", "Tipo de cuchillo", type="select", section="producto", order=20, options=("Chef", "Pan", "Santoku", "Deshuesador", "Fileteador", "Utilitario", "Pelador", "Otro"), condition=_for_product_types("Cuchillo")),
        field_def("material_hoja", "Material de la hoja", type="select", section="materiales", order=21, options=("Acero inoxidable", "Acero al carbono", "Cerámica", "Otro"), condition=_for_product_types("Cuchillo")),
        field_def("longitud_hoja_cm", "Longitud de la hoja", type="decimal", unit="cm", section="medidas", order=22, min=Decimal("0"), condition=_for_product_types("Cuchillo")),
        field_def("material_mango", "Material del mango", type="select", section="materiales", order=23, options=("Madera", "Plástico", "Acero inoxidable", "Caucho", "Compuesto", "Otro"), condition=_for_product_types("Cuchillo")),
        field_def("tipo_filo", "Tipo de filo", type="select", section="producto", order=24, options=("Liso", "Dentado", "Mixto", "Otro"), condition=_for_product_types("Cuchillo")),
        field_def("antideslizante", "Antideslizante", type="boolean", section="producto", order=25, condition=_for_product_types("Tabla de cortar")),
        field_def("tipo_utensilio", "Tipo de utensilio", type="select", section="producto", order=26, options=("Espátula", "Cucharón", "Batidor", "Pinzas", "Cuchara", "Prensa", "Brocha", "Otro"), condition=_for_product_types("Utensilio de cocina")),
        field_def("longitud_cm", "Longitud", type="decimal", unit="cm", section="medidas", order=27, min=Decimal("0"), condition=_for_product_types("Utensilio de cocina")),
        field_def("resistente_calor", "Resistente al calor", type="boolean", section="uso", order=28, condition=_for_product_types("Utensilio de cocina")),
        field_def("plegable", "Plegable", type="boolean", section="producto", order=29, condition=_for_product_types("Colador / Escurridor")),
        field_def("tipo_rallador_pelador", "Tipo de rallador / pelador", type="select", section="producto", order=30, options=("Rallador", "Pelador", "Mandolina", "Cortador", "Otro"), condition=_for_product_types("Rallador / Pelador")),
        field_def("numero_superficies", "Número de superficies", type="integer", section="producto", order=31, min=1, condition=_for_product_types("Rallador / Pelador")),
        field_def("capacidad_ml", "Capacidad", type="decimal", unit="ml", section="medidas", order=32, min=Decimal("0"), quick_options=("200", "250", "300", "350", "400", "500", "600", "750", "1000", "1500", "2000"), condition=_for_product_types(*ml_types)),
        field_def("hermetico", "Hermético", type="boolean", section="producto", order=33, condition=_for_product_types("Recipiente para alimentos")),
        field_def("apto_congelador", "Apto para congelador", type="boolean", section="uso", order=34, condition=_for_product_types("Recipiente para alimentos")),
        field_def("apto_microondas", "Apto para microondas", type="boolean", section="uso", order=35, condition=_for_product_types("Recipiente para alimentos", "Taza / Vaso")),
        field_def("aislamiento_termico", "Aislamiento térmico", type="boolean", section="producto", order=36, condition=_for_product_types("Botella / Termo")),
        field_def("horas_frio", "Horas de frío", type="decimal", unit="horas", section="tecnica", order=37, min=Decimal("0"), condition=_for_product_types("Botella / Termo")),
        field_def("horas_caliente", "Horas de calor", type="decimal", unit="horas", section="tecnica", order=38, min=Decimal("0"), condition=_for_product_types("Botella / Termo")),
        field_def("antiderrames", "Antiderrames", type="boolean", section="producto", order=39, condition=_for_product_types("Botella / Termo")),
        field_def("pajilla_incluida", "Pajilla incluida", type="boolean", section="producto", order=40, condition=_for_product_types("Botella / Termo")),
        field_def("tipo_cubierto", "Tipo de cubierto", type="multiselect", section="producto", order=41, options=("Cuchara", "Tenedor", "Cuchillo", "Cucharilla", "Otro"), condition=_for_product_types("Cubiertos")),
        field_def("numero_personas", "Número de personas", type="integer", section="producto", order=42, min=1, condition=_for_product_types("Vajilla / Set de comedor")),
        field_def("numero_niveles", "Número de niveles", type="integer", section="producto", order=43, min=1, condition=_for_product_types("Secaplatos / Organizador de cocina")),
        field_def("escurridor", "Escurridor", type="boolean", section="producto", order=44, condition=_for_product_types("Secaplatos / Organizador de cocina")),
    )


_HOME_CLEANING_CHEMICAL_TYPES = ("Limpiador líquido", "Detergente", "Desinfectante", "Limpiador en polvo")
_HOME_CLEANING_TOOL_TYPES = (
    "Mopa / Trapeador", "Escoba", "Cepillo de limpieza", "Recogedor", "Balde",
    "Paño / Microfibra", "Esponja / Estropajo", "Plumero", "Limpiavidrios manual",
    "Guantes de limpieza", "Bolsas de basura",
)
_HOME_CLEANING_TYPES = (*_HOME_CLEANING_TOOL_TYPES, *_HOME_CLEANING_CHEMICAL_TYPES, "Otro")
_HOME_STORAGE_TYPES = (
    "Caja / Contenedor", "Canasta", "Organizador de cajón", "Organizador de armario",
    "Organizador de joyería", "Zapatero", "Perchero", "Estante", "Perchas", "Ganchos",
    "Cesto para ropa", "Bolsa al vacío", "Organizador colgante", "Otro",
)


def _home_cleaning_supplies() -> tuple[ProductTemplateField, ...]:
    handled = ("Mopa / Trapeador", "Escoba", "Cepillo de limpieza", "Plumero", "Limpiavidrios manual")
    headed = ("Mopa / Trapeador", "Escoba", "Cepillo de limpieza", "Limpiavidrios manual")
    surfaces = ("Piso", "Vidrio", "Cocina", "Baño", "Madera", "Cerámica", "Acero inoxidable", "Textiles", "Uso general", "Otro")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_CLEANING_TYPES, icon="spray-can"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2, condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3, options=("Plástico", "Acero", "Aluminio", "Microfibra", "Algodón", "Caucho", "Celulosa / Esponja", "Mixto", "Otro"), condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("superficie_recomendada", "Superficie recomendada", type="multiselect", section="uso", order=4, options=surfaces, condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("reutilizable", "Reutilizable", type="boolean", section="producto", order=5, condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("lavable", "Lavable", type="boolean", section="cuidados", order=6, condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("cantidad_paquete", "Cantidad por paquete", type="integer", section="producto", order=7, min=1, condition=_for_product_types(*_HOME_CLEANING_TOOL_TYPES)),
        field_def("longitud_mango_cm", "Longitud del mango", type="decimal", unit="cm", section="medidas", order=8, min=Decimal("0"), condition=_for_product_types(*handled)),
        field_def("ancho_cabezal_cm", "Ancho del cabezal", type="decimal", unit="cm", section="medidas", order=9, min=Decimal("0"), condition=_for_product_types(*headed)),
        field_def("capacidad_l", "Capacidad", type="decimal", unit="L", section="medidas", order=10, min=Decimal("0"), quick_options=("5", "10", "12", "15", "20"), condition=_for_product_types("Balde", "Bolsas de basura")),
        field_def("presentacion", "Presentación", type="select", section="producto", order=11, options=("Líquido", "Gel", "Polvo", "Spray", "Tabletas", "Otra"), condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=12, min=Decimal("0"), condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("unidad", "Unidad", type="select", section="producto", order=13, options=("ml", "L", "g", "kg", "unidades"), condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("fragancia", "Fragancia", section="producto", order=14, condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("concentrado", "Concentrado", type="boolean", section="producto", order=15, condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("superficie_uso", "Superficie de uso", type="multiselect", section="uso", order=16, options=surfaces, condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("instrucciones_dilucion", "Instrucciones de dilución", type="textarea", section="uso", order=17, condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
        field_def("advertencias", "Advertencias", type="textarea", section="uso", order=18, condition=_for_product_types(*_HOME_CLEANING_CHEMICAL_TYPES)),
    )


def _home_storage_organization() -> tuple[ProductTemplateField, ...]:
    capacity = ("Caja / Contenedor", "Canasta", "Cesto para ropa")
    load = ("Estante", "Zapatero", "Perchero", "Organizador de armario")
    compartments = ("Organizador de cajón", "Organizador de armario", "Organizador de joyería", "Organizador colgante")
    installation = ("Perchero", "Estante", "Ganchos", "Organizador colgante", "Organizador de armario")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_STORAGE_TYPES, icon="boxes"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3, options=("Plástico", "Tela", "Madera", "Metal", "Bambú", "Ratán", "Cartón", "Vidrio", "Mixto", "Otro")),
        field_def("habitacion_uso", "Habitación / uso", type="multiselect", section="uso", order=4, options=("Dormitorio", "Armario", "Baño", "Cocina", "Sala", "Oficina", "Lavandería", "Garaje", "Otro")),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=5, min=Decimal("0")),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=6, min=Decimal("0")),
        field_def("profundidad_cm", "Profundidad", type="decimal", unit="cm", section="medidas", order=7, min=Decimal("0")),
        field_def("apilable", "Apilable", type="boolean", section="producto", order=8),
        field_def("plegable", "Plegable", type="boolean", section="producto", order=9),
        field_def("capacidad_l", "Capacidad", type="decimal", unit="L", section="medidas", order=10, min=Decimal("0"), condition=_for_product_types(*capacity)),
        field_def("capacidad_carga_kg", "Capacidad de carga", type="decimal", unit="kg", section="medidas", order=11, min=Decimal("0"), condition=_for_product_types(*load)),
        field_def("numero_compartimentos", "Número de compartimentos", type="integer", section="producto", order=12, min=1, condition=_for_product_types(*compartments)),
        field_def("numero_niveles", "Número de niveles", type="integer", section="producto", order=13, min=1, condition=_for_product_types("Zapatero", "Estante", "Organizador de armario")),
        field_def("tapa_incluida", "Tapa incluida", type="boolean", section="producto", order=14, condition=_for_product_types("Caja / Contenedor", "Canasta")),
        field_def("ruedas", "Ruedas", type="boolean", section="producto", order=15, condition=_for_product_types("Caja / Contenedor", "Estante", "Perchero", "Cesto para ropa")),
        field_def("instalacion", "Instalación", type="select", section="uso", order=16, options=("Piso", "Pared", "Colgante", "Puerta", "Interior de cajón", "Otro"), condition=_for_product_types(*installation)),
    )


_HOME_TEXTILE_TYPES = (
    "Juego de sábanas", "Sábana", "Funda de almohada", "Almohada", "Edredón / Comforter",
    "Cobija / Manta", "Protector de colchón", "Toalla", "Alfombra", "Tapete de baño",
    "Cortina", "Cojín decorativo", "Mantel", "Servilleta de tela", "Otro",
)
_HOME_BEDDING_TYPES = (
    "Juego de sábanas", "Sábana", "Funda de almohada", "Edredón / Comforter",
    "Cobija / Manta", "Protector de colchón",
)
_HOME_TEXTILE_SIZE_TYPES = (
    "Almohada", "Toalla", "Alfombra", "Tapete de baño", "Cortina",
    "Cojín decorativo", "Mantel", "Servilleta de tela",
)
_HOME_FURNITURE_TYPES = (
    "Silla", "Mesa", "Escritorio", "Sofá", "Sillón", "Cama / Base de cama", "Colchón",
    "Mesa de noche", "Cómoda", "Armario", "Gabinete", "Estantería / Librero",
    "Mueble para TV", "Banco", "Taburete", "Juego de comedor", "Otro",
)


def _home_textiles() -> tuple[ProductTemplateField, ...]:
    fill = ("Almohada", "Cojín decorativo")
    shaped = ("Alfombra", "Tapete de baño", "Mantel", "Servilleta de tela")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_TEXTILE_TYPES, icon="layers"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3, options=("Algodón", "Poliéster", "Microfibra", "Lino", "Lana", "Viscosa / Rayón", "Bambú", "Mezcla", "Otro")),
        field_def("composicion", "Composición", type="chips", section="materiales", order=4, help="Ej. Algodón 100% o Algodón 60%, Poliéster 40%", quick_options=("Algodón", "Poliéster", "Microfibra", "Lino", "Lana", "Viscosa", "Bambú")),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=5, min=Decimal("0")),
        field_def("largo_cm", "Largo", type="decimal", unit="cm", section="medidas", order=6, min=Decimal("0")),
        field_def("cantidad_piezas", "Cantidad de piezas", type="integer", section="producto", order=7, min=1),
        field_def("cuidados", "Cuidados", type="multiselect", section="cuidados", order=8, options=("Lavar a mano", "Lavado a máquina", "Lavar con agua fría", "Lavar con colores similares", "No usar blanqueador", "No usar secadora", "Secar a la sombra", "Secar en plano", "Planchar a baja temperatura", "No planchar", "Limpieza en seco", "No limpiar en seco")),
        field_def("tamano_cama", "Tamaño", type="select", section="tallas", order=9, options=("Individual / 1 plaza", "1½ plazas", "2 plazas", "Queen", "King", "Otro"), condition=_for_product_types(*_HOME_BEDDING_TYPES)),
        field_def("numero_hilos", "Número de hilos", type="integer", section="materiales", order=10, min=0, condition=_for_product_types("Juego de sábanas", "Sábana", "Funda de almohada")),
        field_def("tamano_textil", "Tamaño", type="variant_attribute", section="tallas", order=11, quick_options=("Pequeño", "Mediano", "Grande"), condition=_for_product_types(*_HOME_TEXTILE_SIZE_TYPES)),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=12, min=Decimal("0"), condition=_for_product_types("Almohada")),
        field_def("material_relleno", "Material de relleno", type="select", section="materiales", order=13, options=("Fibra", "Espuma", "Espuma viscoelástica", "Plumas / Plumón", "Látex", "Algodón", "Otro"), condition=_for_product_types(*fill)),
        field_def("firmeza", "Firmeza", type="select", section="producto", order=14, options=("Suave", "Media", "Firme"), condition=_for_product_types("Almohada")),
        field_def("gramaje_g_m2", "Gramaje", type="decimal", unit="g/m²", section="materiales", order=15, min=Decimal("0"), condition=_for_product_types("Toalla")),
        field_def("tipo_toalla", "Tipo de toalla", type="select", section="producto", order=16, options=("Manos", "Rostro", "Baño", "Playa", "Deportiva", "Otra"), condition=_for_product_types("Toalla")),
        field_def("tipo_instalacion_cortina", "Tipo de instalación de cortina", type="select", section="uso", order=17, options=("Ojales", "Presillas", "Riel", "Barra", "Cinta fruncidora", "Otro"), condition=_for_product_types("Cortina")),
        field_def("blackout", "Blackout", type="boolean", section="producto", order=18, condition=_for_product_types("Cortina")),
        field_def("translucidez", "Translucidez", type="select", section="producto", order=19, options=("Transparente", "Semitransparente", "Opaca", "Blackout"), condition=_for_product_types("Cortina")),
        field_def("cantidad_paneles", "Cantidad de paneles", type="integer", section="producto", order=20, min=1, condition=_for_product_types("Cortina")),
        field_def("forma", "Forma", type="select", section="presentacion", order=21, options=("Rectangular", "Redonda", "Ovalada", "Cuadrada", "Irregular", "Otra"), condition=_for_product_types(*shaped)),
        field_def("altura_pelo_mm", "Altura del pelo", type="decimal", unit="mm", section="medidas", order=22, min=Decimal("0"), condition=_for_product_types("Alfombra", "Tapete de baño")),
        field_def("base_antideslizante", "Base antideslizante", type="boolean", section="producto", order=23, condition=_for_product_types("Alfombra", "Tapete de baño")),
        field_def("uso_ubicacion", "Uso", type="select", section="uso", order=24, options=("Interior", "Exterior", "Interior y exterior"), condition=_for_product_types("Alfombra", "Tapete de baño")),
        field_def("tipo_cierre", "Tipo de cierre", type="select", section="producto", order=25, options=("Cremallera", "Botones", "Sobre", "Sin cierre", "Otro"), condition=_for_product_types("Cojín decorativo")),
    )


def _home_furniture() -> tuple[ProductTemplateField, ...]:
    seating = ("Silla", "Sofá", "Sillón", "Banco", "Taburete")
    tables = ("Mesa", "Escritorio", "Juego de comedor")
    storage = ("Cómoda", "Armario", "Gabinete", "Estantería / Librero", "Mueble para TV", "Mesa de noche")
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_HOME_FURNITURE_TYPES, icon="armchair"),
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=2),
        field_def("material_principal", "Material principal", type="select", section="materiales", order=3, options=("Madera maciza", "MDF / MDP", "Metal", "Vidrio", "Plástico", "Ratán", "Bambú", "Tela", "Cuero", "Cuero sintético", "Mixto", "Otro")),
        field_def("alto_cm", "Alto", type="decimal", unit="cm", section="medidas", order=4, min=Decimal("0")),
        field_def("ancho_cm", "Ancho", type="decimal", unit="cm", section="medidas", order=5, min=Decimal("0")),
        field_def("profundidad_cm", "Profundidad", type="decimal", unit="cm", section="medidas", order=6, min=Decimal("0")),
        field_def("requiere_ensamblaje", "Requiere ensamblaje", type="boolean", section="producto", order=7),
        field_def("uso_ubicacion", "Uso", type="select", section="uso", order=8, options=("Interior", "Exterior", "Interior y exterior")),
        field_def("numero_plazas", "Número de plazas", type="integer", section="producto", order=9, min=1, condition=_for_product_types(*seating)),
        field_def("material_tapizado", "Material tapizado", type="select", section="materiales", order=10, options=("Tela", "Cuero", "Cuero sintético", "Terciopelo", "Microfibra", "Sin tapizado", "Otro"), condition=_for_product_types(*seating)),
        field_def("material_relleno", "Material de relleno", type="select", section="materiales", order=11, options=("Espuma", "Fibra", "Plumas", "Mixto", "Sin relleno", "Otro"), condition=_for_product_types(*seating)),
        field_def("capacidad_max_kg", "Capacidad máxima", type="decimal", unit="kg", section="medidas", order=12, min=Decimal("0"), condition=_for_product_types(*seating, *storage)),
        field_def("forma", "Forma", type="select", section="presentacion", order=13, options=("Rectangular", "Cuadrada", "Redonda", "Ovalada", "Otra"), condition=_for_product_types(*tables)),
        field_def("numero_personas", "Número de personas", type="integer", section="producto", order=14, min=1, condition=_for_product_types("Mesa", "Juego de comedor")),
        field_def("altura_ajustable", "Altura ajustable", type="boolean", section="producto", order=15, condition=_for_product_types("Escritorio")),
        field_def("plegable", "Plegable", type="boolean", section="producto", order=16, condition=_for_product_types("Mesa", "Escritorio")),
        field_def("numero_puertas", "Número de puertas", type="integer", section="producto", order=17, min=0, condition=_for_product_types(*storage)),
        field_def("numero_cajones", "Número de cajones", type="integer", section="producto", order=18, min=0, condition=_for_product_types(*storage)),
        field_def("numero_estantes", "Número de estantes", type="integer", section="producto", order=19, min=0, condition=_for_product_types(*storage)),
        field_def("tamano_colchon_compatible", "Tamaño de colchón compatible", type="select", section="tallas", order=20, options=("Individual / 1 plaza", "1½ plazas", "2 plazas", "Queen", "King", "Otro"), condition=_for_product_types("Cama / Base de cama")),
        field_def("material_estructura", "Material de la estructura", type="select", section="materiales", order=21, options=("Madera", "Metal", "Tapizado", "Mixto", "Otro"), condition=_for_product_types("Cama / Base de cama")),
        field_def("cabecero_incluido", "Cabecero incluido", type="boolean", section="producto", order=22, condition=_for_product_types("Cama / Base de cama")),
        field_def("tamano_colchon", "Tamaño", type="select", section="tallas", order=23, options=("Individual / 1 plaza", "1½ plazas", "2 plazas", "Queen", "King", "Otro"), condition=_for_product_types("Colchón")),
        field_def("largo_cm", "Largo", type="decimal", unit="cm", section="medidas", order=24, min=Decimal("0"), condition=_for_product_types("Colchón")),
        field_def("firmeza", "Firmeza", type="select", section="producto", order=25, options=("Suave", "Media", "Firme"), condition=_for_product_types("Colchón")),
        field_def("tipo_colchon", "Tipo de colchón", type="select", section="producto", order=26, options=("Espuma", "Resortes", "Híbrido", "Látex", "Otro"), condition=_for_product_types("Colchón")),
        field_def("peso_max_soportado_kg", "Peso máximo soportado", type="decimal", unit="kg", section="medidas", order=27, min=Decimal("0"), condition=_for_product_types("Colchón")),
    )


_BEAUTY_PERSONAL_HYGIENE_TYPES = (
    "Jabón corporal", "Gel de baño / ducha", "Espuma / Sales de baño",
    "Crema / Loción corporal", "Crema de manos", "Exfoliante corporal",
    "Desodorante / Antitranspirante", "Pasta dental", "Enjuague bucal",
    "Cepillo dental manual", "Hilo dental", "Crema / Gel / Espuma de afeitar",
    "Aftershave", "Rasuradora manual", "Algodón / Hisopos / Discos",
    "Toallitas húmedas", "Set de cuidado personal", "Otro",
)
_BEAUTY_MAKEUP_TYPES = (
    "Base / Foundation", "BB / CC Cream", "Corrector", "Polvo", "Rubor",
    "Bronceador", "Iluminador", "Primer", "Spray fijador", "Máscara de pestañas",
    "Sombra de ojos", "Delineador", "Producto para cejas", "Labial",
    "Gloss / Brillo labial", "Delineador de labios", "Esmalte de uñas",
    "Paleta / Set de maquillaje", "Brocha / Pincel", "Esponja / Aplicador", "Otro",
)
_BEAUTY_MAKEUP_SHADE_TYPES = (
    "Base / Foundation", "BB / CC Cream", "Corrector", "Polvo", "Rubor",
    "Bronceador", "Iluminador", "Máscara de pestañas", "Sombra de ojos",
    "Delineador", "Producto para cejas", "Labial", "Gloss / Brillo labial",
    "Delineador de labios", "Esmalte de uñas",
)


def _beauty_personal_hygiene() -> tuple[ProductTemplateField, ...]:
    body_types = (
        "Jabón corporal", "Gel de baño / ducha", "Crema / Loción corporal",
        "Crema de manos", "Exfoliante corporal",
    )
    formulation_types = (
        "Jabón corporal", "Gel de baño / ducha", "Espuma / Sales de baño",
        "Crema / Loción corporal", "Crema de manos", "Exfoliante corporal",
        "Desodorante / Antitranspirante", "Pasta dental", "Enjuague bucal",
        "Crema / Gel / Espuma de afeitar", "Aftershave", "Toallitas húmedas",
    )
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_BEAUTY_PERSONAL_HYGIENE_TYPES, icon="sparkles"),
        field_def("presentacion", "Presentación", type="select", section="producto", order=2, options=("Líquido", "Gel", "Crema", "Espuma", "Spray", "Roll-on", "Stick", "Barra / Sólido", "Polvo", "Toallitas", "Otro"), condition=_for_product_types(*formulation_types)),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=3, min=Decimal("0"), condition=_for_product_types(*formulation_types)),
        field_def("unidad", "Unidad", type="select", section="producto", order=4, options=("ml", "L", "g", "kg", "unidades"), condition=_for_product_types(*formulation_types)),
        field_def("cantidad_paquete", "Cantidad por paquete", type="integer", section="producto", order=5, min=1),
        field_def("aroma", "Aroma", section="producto", order=6, condition=_for_product_types(*formulation_types)),
        field_def("ingredientes", "Ingredientes", type="textarea", section="materiales", order=7, condition=_for_product_types(*formulation_types)),
        field_def("publico_objetivo", "Público objetivo", type="select", section="uso", order=8, options=("Adulto", "Adolescente", "Todo público")),
        field_def("genero_objetivo", "Género objetivo", type="select", section="uso", order=9, options=("Hombre", "Mujer", "Unisex")),
        field_def("tipo_piel", "Tipo de piel", type="multiselect", section="uso", order=10, options=("Normal", "Seca", "Grasa", "Mixta", "Sensible", "Todo tipo"), condition=_for_product_types(*body_types)),
        field_def("zona_uso", "Zona de uso", type="multiselect", section="uso", order=11, options=("Cuerpo", "Manos", "Rostro y cuerpo", "Otra"), condition=_for_product_types(*body_types)),
        field_def("formato_desodorante", "Formato de desodorante", type="select", section="producto", order=12, options=("Spray", "Roll-on", "Stick", "Crema", "Otro"), condition=_for_product_types("Desodorante / Antitranspirante")),
        field_def("antitranspirante", "Antitranspirante", type="boolean", section="producto", order=13, condition=_for_product_types("Desodorante / Antitranspirante")),
        field_def("dureza_cerdas", "Dureza de las cerdas", type="select", section="producto", order=14, options=("Suave", "Media", "Dura"), condition=_for_product_types("Cepillo dental manual")),
        field_def("tamano_cabezal", "Tamaño del cabezal", type="select", section="producto", order=15, options=("Compacto", "Medio", "Grande"), condition=_for_product_types("Cepillo dental manual")),
        field_def("longitud_hilo_m", "Longitud del hilo", type="decimal", unit="m", section="medidas", order=16, min=Decimal("0"), condition=_for_product_types("Hilo dental")),
        field_def("encerado", "Encerado", type="boolean", section="producto", order=17, condition=_for_product_types("Hilo dental")),
        field_def("numero_hojas", "Número de hojas", type="integer", section="producto", order=18, min=1, condition=_for_product_types("Rasuradora manual")),
        field_def("desechable", "Desechable", type="boolean", section="producto", order=19, condition=_for_product_types("Rasuradora manual")),
    )


def _beauty_makeup() -> tuple[ProductTemplateField, ...]:
    face_types = ("Base / Foundation", "BB / CC Cream", "Corrector", "Polvo", "Primer")
    tools = ("Brocha / Pincel", "Esponja / Aplicador")
    formulations = tuple(value for value in _BEAUTY_MAKEUP_TYPES if value not in tools)
    water_resistant = (
        "Máscara de pestañas", "Delineador", "Producto para cejas", "Labial",
        "Gloss / Brillo labial", "Delineador de labios",
    )
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_BEAUTY_MAKEUP_TYPES, icon="sparkles"),
        field_def("tono_color", "Tono / Color", type="variant_attribute", section="presentacion", order=2, condition=_for_product_types(*_BEAUTY_MAKEUP_SHADE_TYPES)),
        field_def("textura", "Textura", type="select", section="presentacion", order=3, options=("Líquida", "Crema", "Polvo", "Compacta", "Stick", "Gel", "Mousse", "Lápiz", "Sólida", "Otra"), condition=_for_product_types(*formulations)),
        field_def("acabado", "Acabado", type="select", section="presentacion", order=4, options=("Mate", "Satinado", "Natural", "Luminoso", "Metálico", "Glitter", "Gloss", "Otro"), condition=_for_product_types(*_BEAUTY_MAKEUP_SHADE_TYPES)),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=5, min=Decimal("0"), condition=_for_product_types(*formulations)),
        field_def("unidad", "Unidad", type="select", section="producto", order=6, options=("ml", "g", "unidades"), condition=_for_product_types(*formulations)),
        field_def("ingredientes", "Ingredientes", type="textarea", section="materiales", order=7, condition=_for_product_types(*formulations)),
        field_def("tipo_piel", "Tipo de piel", type="multiselect", section="uso", order=8, options=("Normal", "Seca", "Grasa", "Mixta", "Sensible", "Todo tipo"), condition=_for_product_types(*face_types)),
        field_def("cobertura", "Cobertura", type="select", section="presentacion", order=9, options=("Ligera", "Media", "Alta", "Construible", "No aplica"), condition=_for_product_types(*face_types)),
        field_def("spf_declarado", "SPF declarado", type="select", section="uso", order=10, options=("No aplica", "SPF 15", "SPF 30", "SPF 50", "SPF 50+", "Otro"), condition=_for_product_types(*face_types)),
        field_def("efecto_mascara", "Efecto de máscara", type="multiselect", section="presentacion", order=11, options=("Volumen", "Alargamiento", "Curvatura", "Separación"), condition=_for_product_types("Máscara de pestañas")),
        field_def("resistente_agua", "Resistente al agua", type="boolean", section="uso", order=12, condition=_for_product_types(*water_resistant)),
        field_def("tipo_accesorio", "Tipo de accesorio", type="select", section="producto", order=13, options=("Brocha", "Pincel", "Esponja", "Aplicador", "Set", "Otro"), condition=_for_product_types(*tools)),
        field_def("material", "Material", type="select", section="materiales", order=14, options=("Fibra sintética", "Fibra natural", "Espuma", "Silicona", "Plástico", "Madera", "Metal", "Mixto", "Otro"), condition=_for_product_types(*tools)),
        field_def("cantidad_piezas", "Cantidad de piezas", type="integer", section="producto", order=15, min=1, condition=_for_product_types(*tools)),
    )


_BEAUTY_SKIN_CARE_TYPES = (
    "Limpiador facial", "Agua micelar", "Tónico", "Serum", "Crema / Hidratante",
    "Gel facial", "Aceite facial", "Mascarilla", "Exfoliante", "Contorno de ojos",
    "Parche / Cuidado localizado", "Protector solar", "Bálsamo labial", "Set de skincare", "Otro",
)
_BEAUTY_HAIR_CARE_TYPES = (
    "Shampoo", "Acondicionador", "Mascarilla capilar", "Aceite / Serum capilar",
    "Tratamiento sin enjuague", "Crema para peinar", "Gel / Cera / Pomada",
    "Spray fijador", "Protector térmico", "Shampoo seco", "Exfoliante de cuero cabelludo",
    "Tinte / Coloración", "Decolorante", "Set capilar", "Cepillo / Peine", "Otro",
)
_BEAUTY_HAIR_VOLUME_TYPES = ("Shampoo", "Acondicionador")
_BEAUTY_FRAGRANCE_TYPES = (
    "Perfume / Parfum", "Eau de Parfum", "Eau de Toilette", "Eau de Cologne",
    "Body Mist / Body Spray", "Perfume en aceite", "Perfume sólido", "Set de fragancias", "Otro",
)
_BEAUTY_LIQUID_FRAGRANCE_TYPES = (
    "Perfume / Parfum", "Eau de Parfum", "Eau de Toilette", "Eau de Cologne",
    "Body Mist / Body Spray", "Perfume en aceite",
)


def _beauty_skin_care() -> tuple[ProductTemplateField, ...]:
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_BEAUTY_SKIN_CARE_TYPES, icon="droplets"),
        field_def("tipo_piel", "Tipo de piel", type="multiselect", section="uso", order=2, options=("Normal", "Seca", "Grasa", "Mixta", "Sensible", "Todo tipo")),
        field_def("zona_aplicacion", "Zona de aplicación", type="multiselect", section="uso", order=3, options=("Rostro", "Ojos", "Labios", "Cuello / escote", "Otra")),
        field_def("momento_uso", "Momento de uso", type="select", section="uso", order=4, options=("Día", "Noche", "Día y noche")),
        field_def("necesidad_cuidado", "Necesidad de cuidado", type="multiselect", section="uso", order=5, options=("Hidratación", "Nutrición", "Limpieza", "Control de grasa", "Calmante", "Luminosidad", "Exfoliación", "Firmeza", "Cuidado de manchas", "Otro")),
        field_def("textura", "Textura", type="select", section="presentacion", order=6, options=("Líquida", "Crema", "Gel", "Aceite", "Bálsamo", "Espuma", "Mascarilla", "Otra")),
        field_def("ingredientes_destacados", "Ingredientes destacados", type="chips", section="materiales", order=7),
        field_def("ingredientes", "Ingredientes", type="textarea", section="materiales", order=8),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=9, min=Decimal("0")),
        field_def("unidad", "Unidad", type="select", section="producto", order=10, options=("ml", "g", "unidades")),
        field_def("aroma", "Aroma", section="producto", order=11),
        field_def("spf_declarado", "SPF declarado", type="select", section="uso", order=12, options=("SPF 15", "SPF 30", "SPF 50", "SPF 50+", "Otro"), condition=_for_product_types("Protector solar")),
        field_def("formato_solar", "Formato solar", type="select", section="presentacion", order=13, options=("Crema", "Gel", "Fluido", "Spray", "Stick", "Otro"), condition=_for_product_types("Protector solar")),
        field_def("resistente_agua", "Resistente al agua", type="boolean", section="uso", order=14, condition=_for_product_types("Protector solar")),
    )


def _beauty_hair_care() -> tuple[ProductTemplateField, ...]:
    styling = ("Gel / Cera / Pomada", "Spray fijador")
    consumables = tuple(
        value for value in _BEAUTY_HAIR_CARE_TYPES if value != "Cepillo / Peine"
    )
    variable_unit_formulations = tuple(
        value for value in consumables if value not in _BEAUTY_HAIR_VOLUME_TYPES
    )
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_BEAUTY_HAIR_CARE_TYPES, icon="scissors"),
        field_def("tipo_cabello", "Tipo de cabello", type="multiselect", section="uso", order=2, options=("Liso", "Ondulado", "Rizado", "Muy rizado / Afro", "Fino", "Grueso", "Seco", "Graso", "Dañado", "Teñido", "Todo tipo"), condition=_for_product_types(*consumables)),
        field_def("tipo_cuero_cabelludo", "Tipo de cuero cabelludo", type="multiselect", section="uso", order=3, options=("Normal", "Seco", "Graso", "Sensible", "Todo tipo"), condition=_for_product_types(*consumables)),
        field_def("efecto_beneficio", "Efecto / beneficio", type="multiselect", section="uso", order=4, options=("Hidratación", "Nutrición", "Reparación", "Brillo", "Volumen", "Anti-frizz", "Definición de rizos", "Protección del color", "Protección térmica", "Limpieza profunda", "Fijación", "Otro"), condition=_for_product_types(*consumables)),
        field_def("contenido_neto", "Contenido neto", type="decimal", section="producto", order=5, min=Decimal("0"), condition=_for_product_types(*variable_unit_formulations)),
        field_def("unidad", "Unidad", type="select", section="producto", order=6, options=("ml", "g", "unidades"), condition=_for_product_types(*variable_unit_formulations)),
        field_def("volumen_ml", "Volumen", type="decimal", unit="ml", section="producto", order=6, min=Decimal("0.01"), condition=_for_product_types(*_BEAUTY_HAIR_VOLUME_TYPES)),
        field_def("aroma", "Aroma", section="producto", order=7, condition=_for_product_types(*consumables)),
        field_def("ingredientes_destacados", "Ingredientes destacados", type="chips", section="materiales", order=8, condition=_for_product_types(*consumables)),
        field_def("ingredientes", "Ingredientes", type="textarea", section="materiales", order=9, condition=_for_product_types(*consumables)),
        field_def("nivel_fijacion", "Nivel de fijación", type="select", section="producto", order=10, options=("Ligera", "Media", "Fuerte", "Extra fuerte"), condition=_for_product_types(*styling)),
        field_def("sin_enjuague", "Sin enjuague", type="boolean", section="uso", order=11, condition=_for_product_types("Tratamiento sin enjuague")),
        field_def("tono_color", "Tono / Color", type="variant_attribute", section="presentacion", order=12, condition=_for_product_types("Tinte / Coloración")),
        field_def("codigo_tono", "Código de tono", section="presentacion", order=13, condition=_for_product_types("Tinte / Coloración")),
        field_def("tipo_coloracion", "Tipo de coloración", type="select", section="producto", order=14, options=("Permanente", "Semipermanente", "Temporal", "Tonalizante", "Otro"), condition=_for_product_types("Tinte / Coloración")),
        field_def("tipo_accesorio", "Tipo de accesorio", type="select", section="producto", order=15, options=("Cepillo plano", "Cepillo redondo", "Peine", "Desenredante", "Otro"), condition=_for_product_types("Cepillo / Peine")),
        field_def("material", "Material", type="select", section="materiales", order=16, options=("Plástico", "Madera", "Metal", "Cerdas naturales", "Cerdas sintéticas", "Mixto", "Otro"), condition=_for_product_types("Cepillo / Peine")),
        field_def("diametro_mm", "Diámetro", type="decimal", unit="mm", section="medidas", order=17, min=Decimal("0"), condition={"field": "tipo_accesorio", "values": ["Cepillo redondo"]}),
        field_def("termico", "Térmico", type="boolean", section="producto", order=18, condition=_for_product_types("Cepillo / Peine")),
    )


def _beauty_fragrances() -> tuple[ProductTemplateField, ...]:
    return (
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="producto", order=1, options=_BEAUTY_FRAGRANCE_TYPES, icon="flower-2"),
        field_def("genero_objetivo", "Género objetivo", type="select", section="uso", order=2, options=("Hombre", "Mujer", "Unisex")),
        field_def("familia_olfativa", "Familia olfativa", type="multiselect", section="presentacion", order=3, options=("Cítrica", "Floral", "Frutal", "Aromática", "Acuática / Fresca", "Amaderada", "Ámbar / Oriental", "Gourmand", "Chipre", "Cuero", "Especiada", "Otra")),
        field_def("notas_salida", "Notas de salida", type="chips", section="presentacion", order=4, help="Primer aroma al aplicar, por ejemplo bergamota, limón o mandarina."),
        field_def("notas_corazon", "Notas de corazón", type="chips", section="presentacion", order=5, help="Aroma principal de la fragancia, por ejemplo rosa, jazmín o lavanda."),
        field_def("notas_fondo", "Notas de fondo", type="chips", section="presentacion", order=6, help="Aroma que permanece más tiempo, por ejemplo vainilla, madera o ámbar."),
        field_def("presentacion", "Presentación", type="select", section="presentacion", order=7, options=("Spray", "Splash", "Roll-on", "Gotero", "Sólido", "Otra")),
        field_def("volumen_ml", "Volumen", type="decimal", unit="ml", section="medidas", order=8, min=Decimal("0"), quick_options=("10", "30", "50", "75", "100", "150", "200"), condition=_for_product_types(*_BEAUTY_LIQUID_FRAGRANCE_TYPES)),
        field_def("recargable", "Recargable", type="boolean", section="producto", order=9, condition=_for_product_types(*_BEAUTY_LIQUID_FRAGRANCE_TYPES)),
        field_def("peso_g", "Peso", type="decimal", unit="g", section="medidas", order=10, min=Decimal("0"), condition=_for_product_types("Perfume sólido")),
        field_def("cantidad_piezas", "Cantidad de piezas", type="integer", section="producto", order=11, min=1, condition=_for_product_types("Set de fragancias")),
    )


@dataclass(frozen=True, slots=True)
class ProductTemplateCategoryBinding:
    template_key: str
    category_code: str
    subcategory_code: str


_TEMPLATE_CATEGORY_BINDINGS = (
    ProductTemplateCategoryBinding(
        "electronics_phones", "ELECTRONICS", "ELECTRONICS_PHONES"
    ),
    ProductTemplateCategoryBinding(
        "electronics_computers", "ELECTRONICS", "ELECTRONICS_COMPUTERS"
    ),
    ProductTemplateCategoryBinding(
        "electronics_headphones", "ELECTRONICS", "ELECTRONICS_HEADPHONES"
    ),
    ProductTemplateCategoryBinding(
        "electronics_cameras", "ELECTRONICS", "ELECTRONICS_CAMERAS"
    ),
    ProductTemplateCategoryBinding(
        "electronics_security", "ELECTRONICS", "ELECTRONICS_SECURITY"
    ),
    ProductTemplateCategoryBinding("fashion_men", "FASHION", "FASHION_MEN"),
    ProductTemplateCategoryBinding("fashion_women", "FASHION", "FASHION_WOMEN"),
    ProductTemplateCategoryBinding("fashion_shoes", "FASHION", "FASHION_SHOES"),
    ProductTemplateCategoryBinding(
        "fashion_accessories", "FASHION", "FASHION_ACCESSORIES"
    ),
    ProductTemplateCategoryBinding(
        "fashion_clothing", "FASHION", "FASHION_CLOTHING"
    ),
    ProductTemplateCategoryBinding(
        "fashion_footwear", "FASHION", "FASHION_FOOTWEAR"
    ),
    ProductTemplateCategoryBinding(
        "fashion_bags_accessories", "FASHION", "FASHION_BAGS_ACCESSORIES"
    ),
    ProductTemplateCategoryBinding(
        "fashion_jewelry_watches", "FASHION", "FASHION_JEWELRY_WATCHES"
    ),
    ProductTemplateCategoryBinding(
        "home_decoration", "HOME_KITCHEN", "HOME_DECORATION"
    ),
    ProductTemplateCategoryBinding(
        "home_kitchen_tools", "HOME_KITCHEN", "HOME_KITCHEN_TOOLS"
    ),
    ProductTemplateCategoryBinding("home_cleaning", "HOME_KITCHEN", "HOME_CLEANING"),
    ProductTemplateCategoryBinding("home_decor_lighting", "HOME_KITCHEN", "HOME_DECOR_LIGHTING"),
    ProductTemplateCategoryBinding("home_kitchen_dining", "HOME_KITCHEN", "HOME_KITCHEN_DINING"),
    ProductTemplateCategoryBinding("home_cleaning_supplies", "HOME_KITCHEN", "HOME_CLEANING_SUPPLIES"),
    ProductTemplateCategoryBinding("home_storage_organization", "HOME_KITCHEN", "HOME_STORAGE_ORGANIZATION"),
    ProductTemplateCategoryBinding("home_textiles", "HOME_KITCHEN", "HOME_TEXTILES"),
    ProductTemplateCategoryBinding("home_furniture", "HOME_KITCHEN", "HOME_FURNITURE"),
    ProductTemplateCategoryBinding(
        "beauty_personal_care", "BEAUTY_HEALTH", "BEAUTY_PERSONAL_CARE"
    ),
    ProductTemplateCategoryBinding(
        "beauty_cosmetics", "BEAUTY_HEALTH", "BEAUTY_COSMETICS"
    ),
    ProductTemplateCategoryBinding(
        "beauty_skincare", "BEAUTY_HEALTH", "BEAUTY_SKINCARE"
    ),
    ProductTemplateCategoryBinding(
        "beauty_personal_hygiene", "BEAUTY_HEALTH", "BEAUTY_PERSONAL_HYGIENE"
    ),
    ProductTemplateCategoryBinding("beauty_makeup", "BEAUTY_HEALTH", "BEAUTY_MAKEUP"),
    ProductTemplateCategoryBinding(
        "beauty_skin_care", "BEAUTY_HEALTH", "BEAUTY_SKIN_CARE"
    ),
    ProductTemplateCategoryBinding(
        "beauty_hair_care", "BEAUTY_HEALTH", "BEAUTY_HAIR_CARE"
    ),
    ProductTemplateCategoryBinding(
        "beauty_fragrances", "BEAUTY_HEALTH", "BEAUTY_FRAGRANCES"
    ),
    ProductTemplateCategoryBinding(
        "automotive_accessories", "AUTOMOTIVE", "AUTOMOTIVE_ACCESSORIES"
    ),
    ProductTemplateCategoryBinding("automotive_tools", "AUTOMOTIVE", "AUTOMOTIVE_TOOLS"),
    ProductTemplateCategoryBinding(
        "automotive_basic_parts", "AUTOMOTIVE", "AUTOMOTIVE_BASIC_PARTS"
    ),
    ProductTemplateCategoryBinding("babies_toys", "BABIES_KIDS", "BABIES_TOYS"),
    ProductTemplateCategoryBinding("babies_clothing", "BABIES_KIDS", "BABIES_CLOTHING"),
    ProductTemplateCategoryBinding("babies_care", "BABIES_KIDS", "BABIES_CARE"),
)


_TEMPLATE_FIELD_SETS = {
    "electronics_phones": _electronics_phone(),
    "electronics_computers": _electronics_computer(),
    "electronics_headphones": _electronics_headphones(),
    "electronics_cameras": _electronics_camera(),
    "electronics_security": _electronics_security(),
    "fashion_men": _fashion_common(),
    "fashion_women": _fashion_common(),
    "fashion_shoes": _common(
        field_def("tipo", "Tipo de calzado", required=True, section="calzado", order=1),
        field_def("talla", "Talla", type="variant_attribute", required=True, section="calzado", order=2),
        field_def("sistema_talla", "Sistema", type="select", section="calzado", order=3, options=("US", "EU", "LATAM")),
        field_def("exterior", "Material exterior", section="materiales", order=4),
        field_def("suela", "Suela", section="materiales", order=5),
    ),
    "fashion_accessories": _fashion_common(),
    "fashion_clothing": _fashion_clothing(),
    "fashion_footwear": _fashion_footwear(),
    "fashion_bags_accessories": _fashion_bags_accessories(),
    "fashion_jewelry_watches": _fashion_jewelry_watches(),
    "home_decoration": _home_common(),
    "home_kitchen_tools": _home_common(),
    "home_cleaning": _home_common(),
    "home_decor_lighting": _home_decor_lighting(),
    "home_kitchen_dining": _home_kitchen_dining(),
    "home_cleaning_supplies": _home_cleaning_supplies(),
    "home_storage_organization": _home_storage_organization(),
    "home_textiles": _home_textiles(),
    "home_furniture": _home_furniture(),
    "beauty_personal_care": _beauty_common(),
    "beauty_cosmetics": _beauty_common(),
    "beauty_skincare": _beauty_common(),
    "beauty_personal_hygiene": _beauty_personal_hygiene(),
    "beauty_makeup": _beauty_makeup(),
    "beauty_skin_care": _beauty_skin_care(),
    "beauty_hair_care": _beauty_hair_care(),
    "beauty_fragrances": _beauty_fragrances(),
    "automotive_accessories": _automotive_common(),
    "automotive_tools": _automotive_common(),
    "automotive_basic_parts": _automotive_common(),
    "babies_toys": _babies_common(),
    "babies_clothing": _fashion_common(),
    "babies_care": _babies_common(),
}


_TEMPLATE_VARIANT_AXES: dict[str, tuple[VariantAxis, ...]] = {
    "electronics_phones": (
        axis_def(
            "color_principal", "Color", suggestions=("Negro", "Blanco", "Azul", "Verde", "Morado"),
            condition={"field": "tipo_producto", "values": ["Smartphone", "Teléfono básico", "Cargador", "Cable", "Protector", "Soporte", "Repuesto", "Otro"]},
            default_for=("Smartphone", "Teléfono básico", "Cargador", "Cable", "Protector", "Soporte", "Otro"),
            is_visual=True,
            is_listing_axis=True,
        ),
        axis_def(
            "almacenamiento_gb", "Almacenamiento", unit="GB", value_type="integer",
            suggestions=("64", "128", "256", "512", "1024"),
            condition={"field": "tipo_producto", "values": ["Smartphone", "Teléfono básico"]},
            default_for=("Smartphone", "Teléfono básico"),
            is_listing_axis=True,
        ),
        axis_def(
            "ram_gb", "RAM", unit="GB", value_type="integer",
            suggestions=("4", "6", "8", "12", "16"),
            condition={"field": "tipo_producto", "values": ["Smartphone"]},
            is_listing_axis=True,
        ),
        axis_def(
            "pantalla_pulgadas", "Tamaño de pantalla", unit="in", value_type="decimal",
            suggestions=("5.5", "6.1", "6.5", "6.7", "6.9"),
            condition={"field": "tipo_producto", "values": ["Smartphone", "Teléfono básico"]},
        ),
        axis_def(
            "potencia_w", "Potencia", unit="W", value_type="integer", suggestions=("5", "10", "15", "18", "20", "25", "45", "65"),
            condition={"field": "tipo_producto", "values": ["Cargador"]}, default_for=("Cargador",),
        ),
        axis_def(
            "longitud_cm", "Longitud", unit="cm", value_type="decimal", suggestions=("100", "150", "200"),
            condition={"field": "tipo_producto", "values": ["Cable"]}, default_for=("Cable",),
        ),
        axis_def(
            "tipo_conector_salida", "Conector de salida", value_type="select",
            suggestions=("USB-A", "USB-C", "Lightning", "Micro-USB", "Multi-puerto"),
            condition={"field": "tipo_producto", "values": ["Cargador", "Cable"]},
        ),
        axis_def(
            "tipo_conector_entrada", "Conector de entrada", value_type="select",
            suggestions=("USB-A", "USB-C", "Lightning", "Micro-USB"),
            condition={"field": "tipo_producto", "values": ["Cable"]},
        ),
        axis_def(
            "modelo_compatible", "Modelo compatible",
            condition={"field": "tipo_producto", "values": ["Protector", "Soporte", "Repuesto"]},
            default_for=("Protector", "Soporte", "Repuesto"),
        ),
        axis_def(
            "material", "Material",
            condition={"field": "tipo_producto", "values": ["Protector", "Soporte", "Otro"]},
            default_for=("Otro",),
        ),
        axis_def(
            "tipo_protector", "Tipo de protector", value_type="select",
            suggestions=("Vidrio templado", "Silicona", "Policarbonato", "Cuero sintético", "Otro"),
            condition={"field": "tipo_producto", "values": ["Protector"]},
        ),
        axis_def(
            "tipo_soporte", "Tipo de soporte", value_type="select",
            suggestions=("Mesa", "Auto", "Pared", "Cuello/Flexible", "Otro"),
            condition={"field": "tipo_producto", "values": ["Soporte"]},
        ),
        axis_def(
            "tipo_repuesto", "Tipo de repuesto", value_type="select",
            suggestions=("Pantalla", "Batería", "Carcasa", "Botón/Switch", "Puerto", "Otro"),
            condition={"field": "tipo_producto", "values": ["Repuesto"]},
        ),
    ),
    "electronics_computers": (
        axis_def(
            "color", "Color", source_field="color_principal",
            suggestions=("Negro", "Plata", "Gris"), is_visual=True,
            is_listing_axis=True,
        ),
        axis_def("ram", "RAM", unit="GB", source_field="ram_gb", value_type="integer", suggestions=("8", "16", "32"),
                 condition={"field": "tipo_equipo", "values": ["Laptop", "Desktop", "Tablet"]},
                 is_listing_axis=True),
        axis_def("almacenamiento", "Almacenamiento", unit="GB", source_field="almacenamiento_gb", value_type="integer", suggestions=("256", "512", "1024"),
                 condition={"field": "tipo_equipo", "values": ["Laptop", "Desktop", "Tablet"]},
                 is_listing_axis=True),
        axis_def("tamano", "Tamaño", unit="in", source_field="pantalla_pulgadas", value_type="decimal", suggestions=("24", "27", "32"),
                 condition={"field": "tipo_equipo", "values": ["Monitor"]}),
    ),
    "electronics_headphones": (
        axis_def(
            "color", "Color", source_field="color_principal",
            suggestions=("Negro", "Blanco", "Azul", "Rojo"),
            is_visual=True, is_listing_axis=True,
        ),
    ),
    "electronics_cameras": (
        axis_def(
            "color", "Color", source_field="color_principal",
            suggestions=("Negro", "Blanco", "Gris"),
            is_visual=True, is_listing_axis=True,
        ),
    ),
    "electronics_security": (
        axis_def(
            "color", "Color", source_field="color_principal",
            suggestions=("Negro", "Blanco", "Gris"),
            is_visual=True, is_listing_axis=True,
        ),
    ),
    "fashion_men": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def("talla", "Talla"),
    ),
    "fashion_women": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def("talla", "Talla"),
    ),
    "fashion_shoes": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def("talla", "Talla"),
    ),
    "fashion_accessories": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def("talla", "Talla"),
    ),
    "fashion_clothing": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def(
            "talla", "Talla", source_field="talla", is_listing_axis=True,
        ),
    ),
    "fashion_footwear": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def(
            "talla", "Talla", source_field="talla", is_listing_axis=True,
        ),
    ),
    "fashion_bags_accessories": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def(
            "talla", "Talla", source_field="talla", is_listing_axis=True,
            condition={
                "field": "tipo_producto",
                "values": ["Cinturón", "Gorra / Sombrero", "Guantes"],
            },
        ),
    ),
    "fashion_jewelry_watches": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def(
            "talla", "Talla", source_field="talla", is_listing_axis=True,
            condition={"field": "tipo_producto", "values": ["Anillo"]},
        ),
    ),
    "home_decor_lighting": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True),
    ),
    "home_kitchen_dining": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True),
        axis_def("capacidad_l", "Capacidad", source_field="capacidad_l", unit="L", value_type="decimal",
                 condition={"field": "tipo_producto", "values": ["Olla / Cacerola", "Wok"]}),
        axis_def("capacidad_ml", "Capacidad", source_field="capacidad_ml", unit="ml", value_type="decimal",
                 condition={"field": "tipo_producto", "values": ["Recipiente para alimentos", "Botella / Termo", "Taza / Vaso"]}),
    ),
    "home_cleaning_supplies": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True,
                 condition={"field": "tipo_producto", "values": list(_HOME_CLEANING_TOOL_TYPES)}),
    ),
    "home_storage_organization": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True),
    ),
    "home_textiles": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True),
        axis_def("tamano_cama", "Tamaño", source_field="tamano_cama", value_type="select", is_listing_axis=True,
                 condition={"field": "tipo_producto", "values": list(_HOME_BEDDING_TYPES)}),
        axis_def("tamano_textil", "Tamaño", source_field="tamano_textil", is_listing_axis=True,
                 condition={"field": "tipo_producto", "values": list(_HOME_TEXTILE_SIZE_TYPES)}),
    ),
    "home_furniture": (
        axis_def("color", "Color", source_field="color_principal", is_visual=True, is_listing_axis=True),
        axis_def("tamano_colchon", "Tamaño", source_field="tamano_colchon", value_type="select", is_listing_axis=True,
                 condition={"field": "tipo_producto", "values": ["Colchón"]}),
    ),
    "beauty_makeup": (
        axis_def(
            "tono_color", "Tono / Color", source_field="tono_color",
            is_listing_axis=True,
            condition={"field": "tipo_producto", "values": list(_BEAUTY_MAKEUP_SHADE_TYPES)},
        ),
    ),
    "beauty_hair_care": (
        axis_def(
            "tono_color", "Tono / Color", source_field="tono_color",
            is_listing_axis=True,
            condition={"field": "tipo_producto", "values": ["Tinte / Coloración"]},
        ),
        axis_def(
            "volumen_ml", "Volumen", source_field="volumen_ml", unit="ml",
            value_type="decimal", is_listing_axis=True,
            condition={"field": "tipo_producto", "values": list(_BEAUTY_HAIR_VOLUME_TYPES)},
        ),
    ),
    "beauty_fragrances": (
        axis_def(
            "volumen_ml", "Volumen", source_field="volumen_ml", unit="ml",
            value_type="decimal", is_listing_axis=True,
            condition={"field": "tipo_producto", "values": list(_BEAUTY_LIQUID_FRAGRANCE_TYPES)},
        ),
    ),
    "babies_clothing": (
        axis_def(
            "color", "Color", source_field="color_principal",
            is_visual=True, is_listing_axis=True,
        ),
        axis_def("talla", "Talla"),
    ),
}


_BINDINGS_BY_TEMPLATE_KEY = {
    binding.template_key: binding for binding in _TEMPLATE_CATEGORY_BINDINGS
}

_TEMPLATE_KEYS_BY_CATEGORY_CODE = {
    binding.subcategory_code: binding.template_key
    for binding in _TEMPLATE_CATEGORY_BINDINGS
}

_LEGACY_BEAUTY_REQUIRED_DOCUMENT_TEMPLATES = frozenset({
    "beauty_personal_care",
    "beauty_cosmetics",
    "beauty_skincare",
})


PRODUCT_TEMPLATES = {
    key: ProductTemplate(
        key=key,
        name=key.replace("_", " ").title(),
        category_code=(
            _BINDINGS_BY_TEMPLATE_KEY[key].category_code
            if key in _BINDINGS_BY_TEMPLATE_KEY
            else ""
        ),
        subcategory_code=(
            _BINDINGS_BY_TEMPLATE_KEY[key].subcategory_code
            if key in _BINDINGS_BY_TEMPLATE_KEY
            else ""
        ),
        fields=fields,
        required_documents=("registro_sanitario",)
        if key in _LEGACY_BEAUTY_REQUIRED_DOCUMENT_TEMPLATES
        else (),
        variant_axes=_TEMPLATE_VARIANT_AXES.get(key, ()),
    )
    for key, fields in _TEMPLATE_FIELD_SETS.items()
}


def get_product_template(template_key: str) -> ProductTemplate:
    try:
        return PRODUCT_TEMPLATES[template_key]
    except KeyError as exc:
        raise ProductTemplateError(f"No existe plantilla para {template_key}.") from exc


def template_key_for_category_code(category_code: str | None) -> str | None:
    if not category_code:
        return None
    template_key = _TEMPLATE_KEYS_BY_CATEGORY_CODE.get(category_code)
    return template_key if template_key in PRODUCT_TEMPLATES else None


def get_product_template_for_category_code(
    category_code: str | None,
) -> ProductTemplate | None:
    template_key = template_key_for_category_code(category_code)
    return PRODUCT_TEMPLATES.get(template_key) if template_key else None


def condition_applies(
    condition: Mapping[str, Any] | None,
    values: Mapping[str, Any],
) -> bool:
    """Evaluate the registry's simple single-field membership condition."""
    if condition is None:
        return True
    if not isinstance(condition, Mapping):
        return False
    field_key = condition.get("field")
    allowed_values = condition.get("values")
    if (
        not isinstance(field_key, str)
        or not field_key.strip()
        or not isinstance(allowed_values, Collection)
        or isinstance(allowed_values, (str, bytes, bytearray, Mapping))
    ):
        return False
    try:
        return field_key in values and values[field_key] in allowed_values
    except TypeError:
        return False


def variant_axes_for_attributes(
    template: ProductTemplate,
    attributes: Mapping[str, Any],
) -> tuple[VariantAxis, ...]:
    """Return axes whose registry conditions apply to the current attributes."""
    return tuple(
        axis
        for axis in template.variant_axes
        if condition_applies(axis.condition, attributes)
    )


def _axis_is_default_for_attributes(
    axis: VariantAxis,
    attributes: Mapping[str, Any],
) -> bool:
    if not axis.default_for:
        return False
    condition_field = (
        axis.condition.get("field")
        if isinstance(axis.condition, Mapping)
        else None
    )
    trigger_field = condition_field or "tipo_producto"
    return attributes.get(trigger_field) in axis.default_for


def default_variant_axes_for_attributes(
    template: ProductTemplate,
    attributes: Mapping[str, Any],
) -> tuple[VariantAxis, ...]:
    """Return eligible axes selected by the registry's default metadata."""
    return tuple(
        axis
        for axis in variant_axes_for_attributes(template, attributes)
        if _axis_is_default_for_attributes(axis, attributes)
    )


def variant_axes_for_product_type(
    template: ProductTemplate,
    product_type: str | None,
) -> tuple[VariantAxis, ...]:
    """Compatibility wrapper for templates triggered by ``tipo_producto``."""
    return variant_axes_for_attributes(template, {"tipo_producto": product_type})


def default_variant_axes_for_product_type(
    template: ProductTemplate,
    product_type: str | None,
) -> tuple[VariantAxis, ...]:
    """Compatibility wrapper for templates triggered by ``tipo_producto``."""
    return default_variant_axes_for_attributes(
        template,
        {"tipo_producto": product_type},
    )


def _condition_validation_errors(
    condition: object,
    *,
    field_keys: set[str],
    fields_by_key: dict[str, ProductTemplateField],
) -> tuple[dict[str, str], Sequence[Any] | None]:
    errors: dict[str, str] = {}
    if not isinstance(condition, Mapping):
        return {"condition": "La condición debe ser un mapeo."}, None

    condition_field = condition.get("field")
    if not isinstance(condition_field, str) or not condition_field.strip():
        errors["condition.field"] = "La condición requiere un campo no vacío."
        condition_field = None
    elif condition_field not in field_keys:
        errors["condition.field"] = "La condición referencia un campo inexistente."

    condition_values = condition.get("values")
    if (
        not isinstance(condition_values, Sequence)
        or isinstance(condition_values, (str, bytes, bytearray))
        or not condition_values
    ):
        errors["condition.values"] = "La condición requiere una colección de valores no vacía."
        return errors, None

    trigger = fields_by_key.get(condition_field) if condition_field else None
    if trigger and trigger.type in {"select", "radio"}:
        if any(value not in trigger.options for value in condition_values):
            errors["condition.values"] = "La condición contiene un valor imposible para el campo disparador."
    return errors, condition_values


def validate_template_registry() -> None:
    errors: dict[str, str] = {}
    seen_template_bindings: dict[str, ProductTemplateCategoryBinding] = {}
    seen_category_codes: dict[str, str] = {}
    for index, binding in enumerate(_TEMPLATE_CATEGORY_BINDINGS):
        previous = seen_template_bindings.get(binding.template_key)
        if previous is not None:
            qualifier = "conflictiva" if previous != binding else "duplicada"
            errors[f"binding.{index}.{binding.template_key}"] = (
                f"Vinculación de plantilla {qualifier}."
            )
        else:
            seen_template_bindings[binding.template_key] = binding
        previous_template_key = seen_category_codes.get(binding.subcategory_code)
        if previous_template_key is not None:
            errors[f"binding.{index}.{binding.subcategory_code}"] = (
                "El código de subcategoría está vinculado más de una vez."
            )
        else:
            seen_category_codes[binding.subcategory_code] = binding.template_key
        if binding.template_key not in _TEMPLATE_FIELD_SETS:
            errors[f"binding.{index}.{binding.template_key}"] = (
                "La vinculación apunta a una plantilla inexistente."
            )

    for key in _TEMPLATE_FIELD_SETS:
        if key not in seen_template_bindings:
            errors[f"binding.{key}"] = "Falta metadata explícita de categoría."

    for key, template in PRODUCT_TEMPLATES.items():
        binding = seen_template_bindings.get(key)
        if binding is None:
            errors[f"binding.{key}"] = "Falta metadata explícita de categoría."
        else:
            if template.category_code != binding.category_code:
                errors[f"binding.{key}.category_code"] = (
                    "La categoría principal no coincide con la vinculación."
                )
            if template.subcategory_code != binding.subcategory_code:
                errors[f"binding.{key}.subcategory_code"] = (
                    "La subcategoría no coincide con la vinculación."
                )
        seen: set[str] = set()
        for item in template.fields:
            if item.key in seen:
                errors[f"{key}.{item.key}"] = "Campo duplicado."
            if item.type not in SUPPORTED_FIELD_TYPES:
                errors[f"{key}.{item.key}"] = "Tipo inválido."
            if item.type in {"select", "multiselect", "radio"}:
                if not item.options:
                    errors[f"{key}.{item.key}"] = "Opciones requeridas."
                elif any(
                    not isinstance(option, str) or not option.strip()
                    for option in item.options
                ):
                    errors[f"{key}.{item.key}"] = "Las opciones deben ser textos no vacíos."
                elif len(set(item.options)) != len(item.options):
                    errors[f"{key}.{item.key}"] = "Las opciones no pueden repetirse."
            if item.type in {"integer", "decimal", "dimension"}:
                if item.min is not None and item.max is not None:
                    try:
                        if item.min > item.max:
                            errors[f"{key}.{item.key}"] = "El mínimo no puede superar el máximo."
                    except (InvalidOperation, TypeError):
                        errors[f"{key}.{item.key}"] = "Los límites numéricos son inválidos."
                if item.type == "integer" and any(
                    bound is not None and not _is_integral_bound(bound)
                    for bound in (item.min, item.max)
                ):
                    errors[f"{key}.{item.key}"] = "Los límites enteros deben ser integrales."
            seen.add(item.key)
        field_keys = {item.key for item in template.fields}
        fields_by_key = {item.key: item for item in template.fields}
        for item in template.fields:
            if item.condition is None:
                continue
            condition_errors, _condition_values = _condition_validation_errors(
                item.condition,
                field_keys=field_keys,
                fields_by_key=fields_by_key,
            )
            for suffix, message in condition_errors.items():
                errors[f"{key}.{item.key}.{suffix}"] = message
        axis_keys: set[str] = set()
        for axis in template.variant_axes:
            if axis.key in axis_keys:
                errors[f"{key}.variant.{axis.key}"] = "Eje duplicado."
            if axis.source_field not in field_keys:
                errors[f"{key}.variant.{axis.key}"] = "El eje no corresponde a un campo de la plantilla."
            if axis.value_type not in {"text", "integer", "decimal", "select"}:
                errors[f"{key}.variant.{axis.key}"] = "Tipo de valor de variante inválido."
            if axis.condition is not None:
                condition_errors, condition_values = _condition_validation_errors(
                    axis.condition,
                    field_keys=field_keys,
                    fields_by_key=fields_by_key,
                )
                for suffix, message in condition_errors.items():
                    errors[f"{key}.variant.{axis.key}.{suffix}"] = message
                if condition_values is not None and any(
                    default_value not in condition_values
                    for default_value in axis.default_for
                ):
                    errors[f"{key}.variant.{axis.key}.default_for"] = (
                        "Los valores predeterminados no son compatibles con la condición."
                    )
            axis_keys.add(axis.key)
    if errors:
        raise ProductTemplateValidationError(errors)


def validate_attributes(
    template: ProductTemplate,
    values: dict[str, Any],
    *,
    final: bool,
    excluded_keys: set[str] | None = None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    excluded_keys = excluded_keys or set()
    for item in template.fields:
        if item.key in excluded_keys:
            continue
        if not condition_applies(item.condition, values):
            continue
        value = values.get(item.key)
        if final and item.required and _is_empty(value):
            errors[f"attributes.{item.key}"] = f"{item.label} es obligatorio."
            continue
        if _is_empty(value):
            continue
        if item.type == "integer":
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número entero."
                continue
            try:
                text_value = str(value)
                if not text_value or any(character in text_value for character in ".eE"):
                    raise ValueError
                number = int(text_value)
            except (TypeError, ValueError):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número entero."
                continue
            if item.min is not None and number < item.min:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser mayor o igual a {item.min}."
            if item.max is not None and number > item.max:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser menor o igual a {item.max}."
        elif item.type in {"decimal", "dimension"}:
            if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número decimal."
                continue
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número decimal."
                continue
            if not number.is_finite():
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número decimal."
                continue
            if item.min is not None and number < item.min:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser mayor o igual a {item.min}."
            if item.max is not None and number > item.max:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser menor o igual a {item.max}."
        elif item.type in {"select", "radio"} and value not in item.options:
            errors[f"attributes.{item.key}"] = f"{item.label} contiene una opción inválida."
        elif item.type == "multiselect":
            if not isinstance(value, list) or any(
                not isinstance(option, str) or option not in item.options
                for option in value
            ):
                errors[f"attributes.{item.key}"] = f"{item.label} contiene opciones inválidas."
        elif item.type == "boolean" and not isinstance(value, bool):
            errors[f"attributes.{item.key}"] = f"{item.label} debe ser verdadero o falso."
        elif item.type == "chips":
            if not isinstance(value, list) or any(
                not isinstance(token, str) or not token.strip()
                for token in value
            ):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser una lista de textos."
        elif item.type == "date":
            if not _is_canonical_date(value):
                errors[f"attributes.{item.key}"] = f"{item.label} debe usar el formato AAAA-MM-DD."
        elif item.type == "variant_attribute":
            if not isinstance(value, str):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un texto."
        elif item.type in {"text", "textarea", "color"} and isinstance(
            value, (Mapping, Collection)
        ) and not isinstance(value, str):
            errors[f"attributes.{item.key}"] = f"{item.label} debe ser un valor simple."
    return errors


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_integral_bound(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value == value.to_integral_value()
    )


def _is_canonical_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value
