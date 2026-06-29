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
    options: tuple[str, ...] = ()
    min: int | Decimal | None = None
    max: int | Decimal | None = None
    condition: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProductTemplate:
    key: str
    name: str
    category_code: str
    subcategory_code: str
    fields: tuple[ProductTemplateField, ...] = field(default_factory=tuple)
    required_documents: tuple[str, ...] = ()

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
    options: Iterable[str] = (),
    min: int | Decimal | None = None,
    max: int | Decimal | None = None,
    condition: dict[str, Any] | None = None,
) -> ProductTemplateField:
    if type not in SUPPORTED_FIELD_TYPES:
        raise ProductTemplateError(f"Tipo de campo no soportado: {type}")
    return ProductTemplateField(
        key=key,
        label=label,
        type=type,
        required=required,
        section=section,
        order=order,
        placeholder=placeholder,
        help=help,
        unit=unit,
        options=tuple(options),
        min=min,
        max=max,
        condition=condition,
    )


def _common(*fields: ProductTemplateField) -> tuple[ProductTemplateField, ...]:
    return (
        field_def("color_principal", "Color principal", type="color", section="presentacion", order=10),
        field_def("material", "Material", section="presentacion", order=20),
        field_def("contenido", "Contenido del paquete", type="repeater", section="contenido", order=30),
        *fields,
    )


def _electronics_phone() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo_producto", "Tipo de producto", type="select", required=True, section="tecnica", order=1, options=("Smartphone", "Teléfono básico", "Cargador", "Cable", "Protector", "Soporte", "Repuesto", "Otro")),
        field_def("sistema_operativo", "Sistema operativo", section="tecnica", order=2),
        field_def("ram_gb", "RAM", type="integer", section="tecnica", order=3, unit="GB", min=0, max=2048),
        field_def("almacenamiento_gb", "Almacenamiento", type="integer", section="tecnica", order=4, unit="GB", min=0, max=8192),
        field_def("pantalla_pulgadas", "Tamaño de pantalla", type="decimal", section="pantalla", order=5, unit="in", min=Decimal("0"), max=Decimal("30")),
        field_def("camara_principal_mp", "Cámara principal", type="decimal", section="camara", order=6, unit="MP", min=Decimal("0"), max=Decimal("500")),
        field_def("bateria_mah", "Batería", type="integer", section="energia", order=7, unit="mAh", min=0, max=50000),
    )


def _electronics_camera() -> tuple[ProductTemplateField, ...]:
    return _common(
        field_def("tipo_camara", "Tipo de cámara", type="select", required=True, section="imagen", order=1, options=("Seguridad", "Fotográfica", "Deportiva", "Webcam", "Otro")),
        field_def("resolucion_mp", "Resolución", type="decimal", required=True, section="imagen", order=2, unit="MP", min=Decimal("0"), max=Decimal("500")),
        field_def("resolucion_video", "Resolución de video", section="video", order=3, placeholder="Ej. 1920 x 1080"),
        field_def("vision_nocturna", "Visión nocturna", type="boolean", section="deteccion", order=4),
        field_def("deteccion_movimiento", "Detección de movimiento", type="boolean", section="deteccion", order=5),
        field_def("conectividad", "Conectividad", type="chips", section="conectividad", order=6, help="Ej. Wi-Fi, Ethernet, Bluetooth"),
        field_def("alimentacion", "Alimentación", section="alimentacion", order=7),
        field_def("proteccion_ip", "Protección IP", section="proteccion", order=8, placeholder="Ej. IP66"),
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
    "electronics_computers": _common(
        field_def("tipo_equipo", "Tipo de equipo", type="select", required=True, section="tecnica", order=1, options=("Laptop", "Desktop", "Tablet", "Monitor", "Accesorio")),
        field_def("procesador", "Procesador", section="tecnica", order=2),
        field_def("ram_gb", "RAM", type="integer", section="tecnica", order=3, unit="GB", min=0),
        field_def("almacenamiento_gb", "Almacenamiento", type="integer", section="tecnica", order=4, unit="GB", min=0),
        field_def("sistema_operativo", "Sistema operativo", section="software", order=5),
    ),
    "electronics_headphones": _common(
        field_def("tipo", "Tipo", type="select", required=True, section="audio", order=1, options=("In-ear", "On-ear", "Over-ear", "Gaming", "Otro")),
        field_def("conexion", "Conexión", type="select", section="audio", order=2, options=("Bluetooth", "Cable", "USB", "Mixta")),
        field_def("cancelacion_activa", "Cancelación activa", type="boolean", section="audio", order=3),
        field_def("microfono", "Micrófono", type="boolean", section="audio", order=4),
        field_def("autonomia_horas", "Autonomía", type="decimal", section="energia", order=5, unit="horas", min=Decimal("0")),
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


PRODUCT_TEMPLATES = {
    key: ProductTemplate(
        key=key,
        name=key.replace("_", " ").title(),
        category_code=key.split("_", 1)[0].upper(),
        subcategory_code=key.upper(),
        fields=fields,
        required_documents=("registro_sanitario",) if key.startswith("beauty_") else (),
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
