from __future__ import annotations

from dataclasses import dataclass, field
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
}

_UNIT_ICONS = {
    "GB": "hard-drive",
    "MP": "camera",
    "mAh": "battery-charging",
    "W": "zap",
    "V": "plug-zap",
    "A": "gauge",
    "cm": "ruler",
    "Hz": "activity",
    "horas": "clock",
    "in": "monitor",
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
) -> VariantAxis:
    return VariantAxis(
        key=key,
        label=label,
        unit=unit,
        suggestions=tuple(suggestions),
        condition=condition,
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
    )


def _electronics_computer() -> tuple[ProductTemplateField, ...]:
    _LAPTOP_DESKTOP_TABLET = ["Laptop", "Desktop", "Tablet"]
    _LAPTOP_DESKTOP = ["Laptop", "Desktop"]
    _LAPTOP_TABLET = ["Laptop", "Tablet"]
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
                  condition={"field": "tipo_equipo", "values": ["Laptop", "Tablet", "Monitor"]}),

        # Laptop / Tablet
        field_def("bateria_mah", "Batería", type="integer", section="energia", order=8, unit="mAh", min=0,
                  condition={"field": "tipo_equipo", "values": _LAPTOP_TABLET}),

        # Tablet
        field_def("tiene_sim", "Ranura SIM", type="boolean", section="conectividad", order=9,
                  condition={"field": "tipo_equipo", "values": _TABLET}),

        # Monitor
        field_def("resolucion_pantalla", "Resolución", section="pantalla", order=10,
                  placeholder="Ej. 1920x1080, 2560x1440",
                  condition={"field": "tipo_equipo", "values": _MONITOR}),
        field_def("frecuencia_hz", "Frecuencia de refresco", type="integer", section="pantalla", order=11, unit="Hz", min=0,
                  quick_options=("60 Hz|60", "75 Hz|75", "120 Hz|120", "144 Hz|144", "165 Hz|165", "240 Hz|240"),
                  condition={"field": "tipo_equipo", "values": _MONITOR}),
        field_def("tipo_panel", "Tipo de panel", type="select", section="pantalla", order=12,
                  options=("IPS", "VA", "TN", "OLED"),
                  condition={"field": "tipo_equipo", "values": _MONITOR}),
        field_def("tipo_conexion_monitor", "Conexiones disponibles", type="chips", section="conectividad", order=13,
                  help="Ej. HDMI, DisplayPort, VGA, USB-C",
                  condition={"field": "tipo_equipo", "values": _MONITOR}),

        # Accesorio
        field_def("tipo_accesorio", "Tipo de accesorio", section="tecnica", order=14,
                  placeholder="Ej. Teclado, Mouse, Hub USB",
                  condition={"field": "tipo_equipo", "values": _ACCESORIO}),
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


_TEMPLATE_FIELD_SETS = {
    "electronics_phones": _electronics_phone(),
    "electronics_computers": _electronics_computer(),
    "electronics_headphones": _common(
        field_def("tipo", "Tipo", type="select", required=True, section="audio", order=1, options=("In-ear", "On-ear", "Over-ear", "Gaming", "Otro")),
        field_def("conexion", "Conexión", type="select", section="audio", order=2, options=("Bluetooth", "Cable", "USB", "Mixta")),
        field_def("cancelacion_activa", "Cancelación activa", type="boolean", section="audio", order=3),
        field_def("microfono", "Micrófono", type="boolean", section="audio", order=4),
        field_def("autonomia_horas", "Autonomía", type="decimal", section="energia", order=5, unit="horas", min=Decimal("0")),
        field_def("surround", "Sonido envolvente (7.1)", type="boolean", section="audio", order=6,
                  condition={"field": "tipo", "values": ["Gaming"]}),
        field_def("plataformas", "Plataformas compatibles", type="chips", section="compatibilidad", order=7,
                  help="Ej. PC, PS5, Xbox, Switch",
                  condition={"field": "tipo", "values": ["Gaming"]}),
    ),
    "electronics_cameras": _electronics_camera(),
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
    "home_decoration": _home_common(),
    "home_kitchen_tools": _home_common(),
    "home_cleaning": _home_common(),
    "beauty_personal_care": _beauty_common(),
    "beauty_cosmetics": _beauty_common(),
    "beauty_skincare": _beauty_common(),
    "automotive_accessories": _automotive_common(),
    "automotive_tools": _automotive_common(),
    "automotive_basic_parts": _automotive_common(),
    "babies_toys": _babies_common(),
    "babies_clothing": _fashion_common(),
    "babies_care": _babies_common(),
}


_TEMPLATE_VARIANT_AXES: dict[str, tuple[VariantAxis, ...]] = {
    "electronics_phones": (
        axis_def("color", "Color", suggestions=("Negro", "Blanco", "Azul", "Verde", "Morado")),
        axis_def("almacenamiento", "Almacenamiento", unit="GB",
                 suggestions=("64", "128", "256", "512", "1024"),
                 condition={"field": "tipo_producto", "values": ["Smartphone", "Teléfono básico"]}),
        axis_def("potencia", "Potencia", unit="W", suggestions=("20", "45", "65"),
                 condition={"field": "tipo_producto", "values": ["Cargador"]}),
        axis_def("longitud", "Longitud", unit="cm", suggestions=("100", "150", "200"),
                 condition={"field": "tipo_producto", "values": ["Cable"]}),
        axis_def("modelo_compatible", "Modelo compatible",
                 condition={"field": "tipo_producto", "values": ["Protector", "Soporte", "Repuesto"]}),
    ),
    "electronics_computers": (
        axis_def("color", "Color", suggestions=("Negro", "Plata", "Gris")),
        axis_def("ram", "RAM", unit="GB", suggestions=("8", "16", "32"),
                 condition={"field": "tipo_equipo", "values": ["Laptop", "Desktop", "Tablet"]}),
        axis_def("almacenamiento", "Almacenamiento", unit="GB", suggestions=("256", "512", "1024"),
                 condition={"field": "tipo_equipo", "values": ["Laptop", "Desktop", "Tablet"]}),
        axis_def("tamano", "Tamaño", unit="in", suggestions=("24", "27", "32"),
                 condition={"field": "tipo_equipo", "values": ["Monitor"]}),
    ),
    "electronics_headphones": (
        axis_def("color", "Color", suggestions=("Negro", "Blanco", "Azul", "Rojo")),
    ),
    "electronics_cameras": (
        axis_def("color", "Color", suggestions=("Negro", "Blanco", "Gris")),
    ),
}


PRODUCT_TEMPLATES = {
    key: ProductTemplate(
        key=key,
        name=key.replace("_", " ").title(),
        category_code=key.split("_", 1)[0].upper(),
        subcategory_code=key.upper(),
        fields=fields,
        required_documents=("registro_sanitario",) if key.startswith("beauty_") else (),
        variant_axes=_TEMPLATE_VARIANT_AXES.get(key, ()),
    )
    for key, fields in _TEMPLATE_FIELD_SETS.items()
}


def get_product_template(template_key: str) -> ProductTemplate:
    try:
        return PRODUCT_TEMPLATES[template_key]
    except KeyError as exc:
        raise ProductTemplateError(f"No existe plantilla para {template_key}.") from exc


def validate_template_registry() -> None:
    errors: dict[str, str] = {}
    for key, template in PRODUCT_TEMPLATES.items():
        seen: set[str] = set()
        for item in template.fields:
            if item.key in seen:
                errors[f"{key}.{item.key}"] = "Campo duplicado."
            if item.type not in SUPPORTED_FIELD_TYPES:
                errors[f"{key}.{item.key}"] = "Tipo inválido."
            if item.type in {"select", "multiselect", "radio"} and not item.options:
                errors[f"{key}.{item.key}"] = "Opciones requeridas."
            seen.add(item.key)
    if errors:
        raise ProductTemplateValidationError(errors)


def validate_attributes(template: ProductTemplate, values: dict[str, Any], *, final: bool) -> dict[str, str]:
    errors: dict[str, str] = {}
    for item in template.fields:
        if item.condition:
            trigger_val = values.get(item.condition["field"])
            if trigger_val not in item.condition["values"]:
                continue
        value = values.get(item.key)
        if final and item.required and _is_empty(value):
            errors[f"attributes.{item.key}"] = f"{item.label} es obligatorio."
            continue
        if _is_empty(value):
            continue
        if item.type == "integer":
            try:
                number = int(value)
            except (TypeError, ValueError):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número entero."
                continue
            if item.min is not None and number < item.min:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser mayor o igual a {item.min}."
            if item.max is not None and number > item.max:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser menor o igual a {item.max}."
        elif item.type == "decimal":
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError):
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser un número decimal."
                continue
            if item.min is not None and number < item.min:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser mayor o igual a {item.min}."
            if item.max is not None and number > item.max:
                errors[f"attributes.{item.key}"] = f"{item.label} debe ser menor o igual a {item.max}."
        elif item.type in {"select", "radio"} and value not in item.options:
            errors[f"attributes.{item.key}"] = f"{item.label} contiene una opción inválida."
        elif item.type == "multiselect":
            selected = value if isinstance(value, list) else [value]
            if any(option not in item.options for option in selected):
                errors[f"attributes.{item.key}"] = f"{item.label} contiene opciones inválidas."
    return errors


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}
