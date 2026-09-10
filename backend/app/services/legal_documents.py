from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class DocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class DocumentSection:
    anchor: str
    title: str


@dataclass(frozen=True, slots=True)
class HistoricalVersion:
    version_identifier: str
    published_at: str
    effective_at: str


@dataclass(frozen=True, slots=True)
class DocumentDefinition:
    family: str
    slug: str
    title: str
    description: str
    status: DocumentStatus
    navigation_order: int
    template_name: str | None = None
    version_identifier: str | None = None
    published_at: str | None = None
    effective_at: str | None = None
    sections: tuple[DocumentSection, ...] = ()
    requires_acceptance: bool = False
    historical_versions: tuple[HistoricalVersion, ...] = ()
    kind: str = "article"

    @property
    def is_published(self) -> bool:
        return self.status == DocumentStatus.PUBLISHED


@dataclass(frozen=True, slots=True)
class FamilyDefinition:
    slug: str
    title: str
    description: str
    navigation_order: int
    documents: tuple[DocumentDefinition, ...]

    @property
    def published_documents(self) -> tuple[DocumentDefinition, ...]:
        return tuple(document for document in self.documents if document.is_published)

    @property
    def draft_documents(self) -> tuple[DocumentDefinition, ...]:
        return tuple(document for document in self.documents if not document.is_published)


@dataclass(frozen=True, slots=True)
class OperatorInformation:
    name: str
    ruc: str
    legal_address: str
    legal_phone: str
    help_email: str
    claims_email: str
    privacy_email: str
    controller_representative: str
    data_protection_officer: str


OPERATOR_INFORMATION = OperatorInformation(
    name="Ecuvel",
    ruc="2100497391001",
    legal_address="Av. 9 de Octubre y Miguel Gamboa",
    legal_phone="0963267781",
    help_email="ecuvel.help@hotmail.com",
    claims_email="ecuvel.reclamos@hotmail.com",
    privacy_email="ecuvel.privacidad@hotmail.com",
    controller_representative="Edison Alejandro Campos Leines",
    data_protection_officer=(
        "La asesoría jurídica concluye que la designación no corresponde a la "
        "operación actual de ECUVEL; debe reevaluarse si cambian la escala, el "
        "monitoreo, las categorías tratadas o las condiciones regulatorias."
    ),
)


def _draft(
    family: str,
    slug: str,
    title: str,
    description: str,
    order: int,
    *,
    template_name: str | None = None,
    sections: tuple[tuple[str, str], ...] = (),
    requires_acceptance: bool = False,
) -> DocumentDefinition:
    return DocumentDefinition(
        family=family,
        slug=slug,
        title=title,
        description=description,
        status=DocumentStatus.DRAFT,
        navigation_order=order,
        template_name=template_name,
        sections=tuple(
            DocumentSection(anchor=anchor, title=section_title)
            for anchor, section_title in sections
        ),
        requires_acceptance=requires_acceptance,
    )


def _published(
    family: str,
    slug: str,
    title: str,
    description: str,
    order: int,
    template_name: str,
    sections: tuple[tuple[str, str], ...],
    *,
    kind: str = "article",
) -> DocumentDefinition:
    return DocumentDefinition(
        family=family,
        slug=slug,
        title=title,
        description=description,
        status=DocumentStatus.PUBLISHED,
        navigation_order=order,
        template_name=template_name,
        sections=tuple(
            DocumentSection(anchor=anchor, title=section_title)
            for anchor, section_title in sections
        ),
        kind=kind,
    )


FAMILIES: tuple[FamilyDefinition, ...] = (
    FamilyDefinition(
        slug="ecuvel",
        title="ECUVEL",
        description="Información general sobre la plataforma y sus canales.",
        navigation_order=10,
        documents=(
            _published(
                "ecuvel",
                "que-es-ecuvel",
                "Qué es ECUVEL",
                "Una introducción factual a la plataforma y a lo que ofrece actualmente.",
                10,
                "docs/content/ecuvel/que_es_ecuvel.html",
                (
                    ("la-plataforma", "La plataforma"),
                    ("disponible-hoy", "Disponible actualmente"),
                    ("funciones-en-desarrollo", "Funciones en desarrollo"),
                ),
            ),
            _published(
                "ecuvel",
                "como-funciona",
                "Cómo funciona",
                "Recorrido actual desde el catálogo hasta el retiro de una compra.",
                20,
                "docs/content/ecuvel/como_funciona.html",
                (
                    ("explorar", "Explorar el catálogo"),
                    ("cuenta", "Cuenta e identidad"),
                    ("compra-y-pago", "Compra y pago"),
                    ("retiro", "Preparación y retiro"),
                    ("vendedores", "Vendedores"),
                ),
            ),
            _published(
                "ecuvel",
                "contacto",
                "Contacto",
                "Canales diferenciados para ayuda, reclamos y privacidad.",
                30,
                "docs/content/ecuvel/contacto.html",
                (
                    ("ayuda-general", "Ayuda general"),
                    ("reclamos", "Reclamos"),
                    ("privacidad-y-datos", "Privacidad y datos"),
                    ("telefono-institucional", "Teléfono institucional"),
                ),
            ),
            _draft(
                "ecuvel",
                "seguridad",
                "Seguridad",
                "Recomendaciones de seguridad y protección de la cuenta.",
                40,
                template_name="docs/content/ecuvel/seguridad.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("credenciales-y-cuenta", "Credenciales y cuenta"),
                    ("mensajes-y-suplantacion", "Mensajes y suplantación"),
                    ("pagos-y-comprobantes", "Pagos y comprobantes"),
                    ("archivos-y-dispositivos", "Archivos y dispositivos"),
                    ("actividad-sospechosa", "Actividad sospechosa"),
                    ("canales-y-reporte", "Canales y reporte"),
                ),
            ),
        ),
    ),
    FamilyDefinition(
        slug="compradores",
        title="COMPRADORES",
        description="Documentación para comprar, pagar, retirar y ejercer derechos.",
        navigation_order=20,
        documents=(
            _draft(
                "compradores",
                "terminos-y-condiciones",
                "Términos y Condiciones",
                "Reglas generales aplicables al uso de ECUVEL.",
                10,
                template_name="docs/content/compradores/terminos_y_condiciones.html",
                sections=(
                    ("estado-del-borrador", "Estado y alcance del borrador"),
                    ("identificacion-y-definiciones", "Identificación y definiciones"),
                    ("acceso-y-cuentas", "Acceso, registro y cuentas"),
                    ("capacidad-y-edad", "Capacidad y reglas de edad"),
                    ("modelo-marketplace", "Modelo de marketplace y vendedores"),
                    ("publicaciones-y-ofertas", "Productos, ofertas, precios y disponibilidad"),
                    ("carrito-y-pedidos", "Carrito y formación de pedidos"),
                    ("pago-y-comprobante", "Transferencia, comprobante y revisión"),
                    ("estados-y-cancelacion", "Estados, fallos y cancelación"),
                    ("preparacion-y-retiro", "Preparación y retiro"),
                    ("devoluciones-reembolsos-garantias", "Devoluciones, reembolsos y garantías"),
                    ("reclamos-y-derechos", "Reclamos y derechos del consumidor"),
                    ("productos-restringidos", "Productos restringidos y controles futuros"),
                    ("resenas-y-contenido", "Reseñas, contenido y moderación"),
                    ("propiedad-intelectual", "Propiedad intelectual"),
                    ("uso-aceptable", "Uso aceptable, fraude y suspensión"),
                    ("comunicaciones-y-privacidad", "Comunicaciones y privacidad"),
                    ("cambios-y-evidencia", "Cambios, versiones y evidencia electrónica"),
                    ("responsabilidad-y-fuerza-mayor", "Responsabilidad y fuerza mayor"),
                    ("ley-y-controversias", "Ley aplicable y controversias"),
                    ("contacto-y-disposiciones-finales", "Contacto y disposiciones finales"),
                ),
                requires_acceptance=True,
            ),
            _draft(
                "compradores",
                "condiciones-de-compra",
                "Condiciones de compra",
                "Formación y ejecución de compras en la plataforma.",
                20,
                template_name="docs/content/compradores/condiciones_de_compra.html",
                sections=(
                    ("estado-y-relacion", "Estado y relación con los Términos"),
                    ("capacidad-y-partes", "Quién puede comprar y quién vende"),
                    ("oferta-precio-y-stock", "Oferta, precio y disponibilidad"),
                    ("carrito-y-checkout", "Carrito y validación en checkout"),
                    ("pedido-y-subpedidos", "Pedido, Subpedidos y registro electrónico"),
                    ("pago-y-confirmacion", "Pago y confirmación"),
                    ("preparacion-y-cumplimiento", "Preparación y cumplimiento"),
                    ("retiro-y-custodia", "Retiro y custodia"),
                    ("cancelaciones-y-reembolsos", "Cancelaciones y reembolsos"),
                    ("facturacion-y-constancias", "Factura y constancia de compra"),
                    ("reclamos-y-derechos", "Reclamos y derechos"),
                    ("documentos-relacionados", "Documentos relacionados"),
                ),
            ),
            _draft(
                "compradores",
                "pagos",
                "Pagos",
                "Métodos, comprobantes y revisión de pagos.",
                30,
                template_name="docs/content/compradores/pagos.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("metodo-y-datos-bancarios", "Método y datos bancarios"),
                    ("importe-plazo-y-reserva", "Importe, plazo y reserva"),
                    ("carga-del-comprobante", "Carga del comprobante"),
                    ("seguridad-e-integridad", "Seguridad e integridad"),
                    ("preanalisis-ocr-y-qr", "Preanálisis, OCR y QR"),
                    ("revision-y-decision", "Revisión y decisión"),
                    ("vencimientos-y-discrepancias", "Vencimientos y discrepancias"),
                    ("fraude-y-duplicidad", "Fraude y duplicidad"),
                    ("comprobante-y-factura", "Recibo y factura"),
                    ("ayuda-y-reembolsos", "Ayuda y reembolsos"),
                ),
            ),
            _draft(
                "compradores",
                "entregas",
                "Entregas",
                "Preparación, conservación y retiro de pedidos.",
                40,
                template_name="docs/content/compradores/entregas.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("modelo-de-cumplimiento", "Modelo de cumplimiento"),
                    ("preparacion-de-la-tienda", "Preparación de la Tienda"),
                    ("recepcion-y-custodia-ecuvel", "Recepción y custodia de ECUVEL"),
                    ("pedidos-multitienda", "Pedidos con varias Tiendas"),
                    ("aviso-y-punto-de-retiro", "Aviso y punto de retiro"),
                    ("retiro-del-pedido", "Retiro del Pedido"),
                    ("plazo-de-siete-dias", "Plazo de siete días"),
                    ("demoras-danos-y-perdidas", "Demoras, daños y pérdidas"),
                    ("reclamos-y-reembolsos", "Reclamos y reembolsos"),
                ),
            ),
            _draft(
                "compradores",
                "devoluciones-y-reembolsos",
                "Devoluciones y reembolsos",
                "Condiciones y procedimientos aplicables.",
                50,
                template_name="docs/content/compradores/devoluciones_y_reembolsos.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("categorias-de-caso", "Categorías de caso"),
                    ("falta-de-stock", "Falta de stock físico"),
                    ("pedido-no-retirado", "Pedido no retirado"),
                    ("devolucion-o-cambio-legal", "Devolución o cambio legal"),
                    ("defectos-y-garantias", "Defectos y garantías"),
                    ("errores-de-pago", "Errores y duplicidad de pago"),
                    ("cancelacion-antes-del-cumplimiento", "Cancelación antes del cumplimiento"),
                    ("importe-y-plazo-del-reembolso", "Importe y plazo del reembolso"),
                    ("datos-para-el-reembolso", "Datos para el reembolso"),
                    ("pedidos-multitienda", "Pedidos con varias Tiendas"),
                    ("solicitud-y-derechos", "Solicitud y derechos"),
                ),
            ),
            _draft(
                "compradores",
                "garantias",
                "Garantías",
                "Cobertura y ejercicio de garantías.",
                60,
                template_name="docs/content/compradores/garantias.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("garantias-aplicables", "Garantías aplicables"),
                    ("defectos-y-no-conformidad", "Defectos y falta de conformidad"),
                    ("inicio-del-reclamo", "Inicio del reclamo"),
                    ("evidencia-del-comprador", "Evidencia del Comprador"),
                    ("coordinacion-de-ecuvel", "Coordinación de ECUVEL"),
                    ("atencion-de-la-tienda", "Atención de la Tienda"),
                    ("evaluacion-y-remedios", "Evaluación y remedios"),
                    ("plazos-y-trazabilidad", "Plazos y trazabilidad"),
                    ("retroalimentacion-seller", "Retroalimentación sobre la Tienda"),
                    ("derechos-seguridad-y-contacto", "Derechos, seguridad y contacto"),
                ),
            ),
            _draft(
                "compradores",
                "reclamos",
                "Reclamos",
                "Canal y procedimiento para reclamos de consumo.",
                70,
                template_name="docs/content/compradores/reclamos.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("materias-de-reclamo", "Materias de reclamo"),
                    ("como-presentarlo", "Cómo presentar un reclamo"),
                    ("ecuvel-y-la-tienda", "ECUVEL y la Tienda"),
                    ("evidencia-y-seguimiento", "Evidencia y seguimiento"),
                    ("pedidos-multitienda", "Pedidos con varias Tiendas"),
                    ("canales-diferenciados", "Canales diferenciados"),
                    ("controles-y-derechos", "Controles y derechos"),
                ),
            ),
            _draft(
                "compradores",
                "productos-restringidos",
                "Productos restringidos",
                "Categorías y productos no admitidos o sujetos a controles.",
                80,
                template_name="docs/content/compradores/productos_restringidos.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("clasificacion", "Clasificación"),
                    ("prohibidos", "Productos prohibidos"),
                    ("no-soportados", "Productos no soportados actualmente"),
                    ("soporte-condicional", "Soporte regulado o condicional futuro"),
                    ("cosmeticos", "Cosméticos"),
                    ("mercancia-general", "Mercancía general"),
                    ("edad-y-controles", "Edad y controles"),
                    ("seguridad-y-reclamos", "Seguridad y reclamos"),
                ),
            ),
        ),
    ),
    FamilyDefinition(
        slug="privacidad",
        title="PRIVACIDAD Y DATOS",
        description="Información sobre datos personales, tecnologías y derechos.",
        navigation_order=30,
        documents=(
            _draft(
                "privacidad",
                "politica-de-privacidad",
                "Política de Privacidad",
                "Información sobre los tratamientos de datos personales de ECUVEL.",
                10,
                template_name="docs/content/privacidad/politica_de_privacidad.html",
                sections=(
                    ("estado-y-responsable", "Estado, alcance y responsable"),
                    ("principios-y-fuentes", "Principios y fuentes"),
                    ("datos-que-tratamos", "Datos que tratamos"),
                    ("finalidades-y-bases", "Finalidades y bases jurídicas"),
                    ("cuenta-perfil-y-edad", "Cuenta, perfil y edad"),
                    ("compras-y-pagos", "Compras, pagos y comprobantes"),
                    ("ocr-y-decisiones", "OCR, preanálisis y decisiones"),
                    ("cumplimiento-y-resenas", "Cumplimiento, reseñas y moderación"),
                    ("vendedores-y-personal", "Vendedores y personal"),
                    ("telemetria-y-ranking", "Telemetría y ranking"),
                    ("comunicaciones", "Comunicaciones"),
                    ("cookies-y-sesion", "Cookies y tecnologías de sesión"),
                    ("destinatarios-y-encargados", "Destinatarios y encargados"),
                    ("transferencias", "Transferencias de datos"),
                    ("conservacion", "Conservación"),
                    ("seguridad", "Seguridad"),
                    ("derechos", "Derechos de las personas"),
                    ("ejercicio-de-derechos", "Cómo ejercer sus derechos"),
                    ("menores", "Niñas, niños y adolescentes"),
                    ("cambios-y-reclamos", "Cambios y reclamos"),
                ),
            ),
            _draft(
                "privacidad",
                "cookies-y-tecnologias-similares",
                "Cookies y tecnologías similares",
                "Tecnologías utilizadas por la plataforma y sus finalidades.",
                20,
                template_name="docs/content/privacidad/cookies_y_tecnologias_similares.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("conceptos", "Cookies y otras tecnologías"),
                    ("estado-necesario", "Estado estrictamente necesario"),
                    ("identificadores-y-contextos", "Identificadores y contextos"),
                    ("telemetria", "Telemetría de catálogo"),
                    ("recursos-externos", "Recursos externos"),
                    ("duracion-y-control", "Duración y control"),
                    ("eleccion-previa", "Elección previa"),
                ),
            ),
            _draft(
                "privacidad",
                "derechos-del-titular",
                "Derechos del titular",
                "Cómo ejercer derechos relacionados con datos personales.",
                30,
                template_name="docs/content/privacidad/derechos_del_titular.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("derechos", "Derechos reconocidos"),
                    ("solicitud", "Cómo presentar una solicitud"),
                    ("verificacion-y-representacion", "Verificación y representación"),
                    ("plazos", "Plazos legales"),
                    ("rectificacion-fecha-nacimiento", "Rectificación de fecha de nacimiento"),
                    ("cierre-y-conservacion", "Cierre y conservación"),
                    ("limites-y-reclamo", "Límites y reclamo"),
                ),
            ),
            _draft(
                "privacidad",
                "comunicaciones-y-marketing",
                "Comunicaciones y marketing",
                "Diferencia entre mensajes operativos y comunicaciones comerciales.",
                40,
                template_name="docs/content/privacidad/comunicaciones_y_marketing.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("comunicaciones-necesarias", "Comunicaciones necesarias"),
                    ("marketing", "Marketing"),
                    ("consentimiento-por-canal", "Consentimiento por canal"),
                    ("retiro", "Retiro del consentimiento"),
                    ("genero-y-menores", "Género y menores"),
                    ("canales", "Canales"),
                ),
            ),
        ),
    ),
    FamilyDefinition(
        slug="vendedores",
        title="VENDEDORES",
        description="Guías y políticas del ecosistema ECUVEL Partners.",
        navigation_order=40,
        documents=(
            _draft("vendedores", "como-vender", "Cómo vender en ECUVEL", "Introducción al proceso para vendedores.", 10),
            _draft("vendedores", "politicas", "Políticas para vendedores", "Políticas operativas de la plataforma para tiendas.", 20),
            _draft("vendedores", "productos-permitidos-y-prohibidos", "Productos permitidos y prohibidos", "Reglas del catálogo para vendedores.", 30),
            _draft("vendedores", "comisiones", "Comisiones", "Estructura y aplicación de comisiones.", 40),
            _draft("vendedores", "pagos-y-liquidaciones", "Pagos y liquidaciones", "Proceso de liquidación a vendedores.", 50),
            _draft("vendedores", "logistica", "Logística", "Preparación y entrega de productos a ECUVEL.", 60),
            _draft("vendedores", "contrato", "Contrato de vendedor", "Acceso y relación con el contrato comercial separado de Partners.", 70),
            _draft("vendedores", "proteccion-de-datos-seller", "Protección de datos Seller", "Tratamientos asociados al onboarding y operación de tiendas.", 80),
        ),
    ),
    FamilyDefinition(
        slug="plataforma",
        title="PLATAFORMA",
        description="Reglas para proteger la comunidad y el contenido de ECUVEL.",
        navigation_order=50,
        documents=(
            _draft(
                "plataforma", "uso-aceptable", "Uso aceptable",
                "Conductas permitidas y prohibidas en ECUVEL.", 10,
                template_name="docs/content/plataforma/uso_aceptable.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("uso-licito-y-cuentas", "Uso lícito y cuentas"),
                    ("seguridad-y-archivos", "Seguridad y archivos"),
                    ("sistemas-y-transacciones", "Sistemas y transacciones"),
                    ("contenido-e-interacciones", "Contenido e interacciones"),
                    ("medidas-y-revision", "Medidas y revisión"),
                ),
            ),
            _draft(
                "plataforma", "resenas-y-contenido", "Reseñas y contenido",
                "Publicación, revisión y moderación de contenido.", 20,
                template_name="docs/content/plataforma/resenas_y_contenido.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("elegibilidad", "Elegibilidad"),
                    ("contenido-permitido", "Contenido permitido"),
                    ("contenido-no-admitido", "Contenido no admitido"),
                    ("moderacion", "Moderación"),
                    ("correcciones-y-respuestas", "Correcciones y respuestas"),
                    ("licencia-y-derechos", "Licencia y derechos"),
                    ("conservacion-y-retiro", "Conservación y retiro"),
                    ("garantias-y-contacto", "Garantías y contacto"),
                ),
            ),
            _draft(
                "plataforma", "propiedad-intelectual", "Propiedad intelectual",
                "Uso de marcas, imágenes, textos y otros contenidos.", 30,
                template_name="docs/content/plataforma/propiedad_intelectual.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("titularidad", "Titularidad"),
                    ("uso-permitido", "Uso permitido"),
                    ("publicaciones-y-falsificaciones", "Publicaciones y falsificaciones"),
                    ("reporte", "Reporte de infracciones"),
                    ("revision-y-medidas", "Revisión y medidas"),
                ),
            ),
            _draft(
                "plataforma", "fraude-y-abuso", "Fraude y abuso",
                "Prevención y respuesta ante usos abusivos.", 40,
                template_name="docs/content/plataforma/fraude_y_abuso.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("conductas", "Conductas prohibidas o revisables"),
                    ("pagos-y-operaciones", "Pagos y operaciones"),
                    ("cuentas-contenido-y-archivos", "Cuentas, contenido y archivos"),
                    ("revision-y-medidas", "Revisión y medidas"),
                    ("derechos-y-reporte", "Derechos y reporte"),
                ),
            ),
            _draft(
                "plataforma", "suspension-de-cuentas", "Suspensión de cuentas",
                "Causas y efectos de las restricciones de acceso.", 50,
                template_name="docs/content/plataforma/suspension_de_cuentas.html",
                sections=(
                    ("estado-y-alcance", "Estado y alcance"),
                    ("estados-de-cuenta", "Estados de cuenta"),
                    ("motivos", "Motivos de restricción"),
                    ("efectos", "Efectos"),
                    ("aviso-y-revision", "Aviso y revisión"),
                    ("obligaciones-vigentes", "Obligaciones vigentes"),
                    ("datos-y-reactivacion", "Datos y reactivación"),
                ),
            ),
        ),
    ),
    FamilyDefinition(
        slug="legal",
        title="LEGAL",
        description="Identificación de ECUVEL y archivo de publicaciones legales.",
        navigation_order=60,
        documents=(
            _published(
                "legal",
                "operador",
                "Datos del operador",
                "Identificación y canales institucionales informados por ECUVEL.",
                10,
                "docs/content/legal/operador.html",
                (
                    ("identificacion", "Identificación"),
                    ("canales-institucionales", "Canales institucionales"),
                    ("responsable-del-tratamiento", "Responsable del tratamiento"),
                ),
            ),
            _published(
                "legal",
                "versiones",
                "Versiones anteriores",
                "Archivo de documentos legales publicados y sustituidos.",
                20,
                "docs/versions.html",
                (),
                kind="archive",
            ),
        ),
    ),
)


_FAMILY_BY_SLUG = {family.slug: family for family in FAMILIES}
_DOCUMENT_BY_PATH = {
    (document.family, document.slug): document
    for family in FAMILIES
    for document in family.documents
}
_SAFE_TEMPLATE = re.compile(r"^docs/[a-z0-9_/-]+\.html$")


def _validate_registry() -> None:
    if len(_FAMILY_BY_SLUG) != len(FAMILIES):
        raise RuntimeError("Las familias de Docs deben tener slugs únicos.")
    document_count = sum(len(family.documents) for family in FAMILIES)
    if len(_DOCUMENT_BY_PATH) != document_count:
        raise RuntimeError("Los documentos de Docs deben tener rutas únicas.")
    for family in FAMILIES:
        if tuple(sorted(family.documents, key=lambda item: item.navigation_order)) != family.documents:
            raise RuntimeError("Los documentos de Docs deben conservar un orden determinista.")
        for document in family.documents:
            if document.family != family.slug:
                raise RuntimeError("Un documento de Docs pertenece a una familia incorrecta.")
            if document.is_published and document.template_name is None:
                raise RuntimeError("Un documento publicado requiere una plantilla registrada.")
            if document.status == DocumentStatus.DRAFT and any(
                (
                    document.version_identifier,
                    document.published_at,
                    document.effective_at,
                    document.historical_versions,
                )
            ):
                raise RuntimeError(
                    "Un borrador de Docs no puede aparentar versión o vigencia oficial."
                )
            if document.template_name is not None and (
                ".." in document.template_name
                or not _SAFE_TEMPLATE.fullmatch(document.template_name)
            ):
                raise RuntimeError("La plantilla registrada para Docs no es segura.")
            anchors = [section.anchor for section in document.sections]
            if len(anchors) != len(set(anchors)):
                raise RuntimeError("Los anchors de un documento deben ser únicos.")


_validate_registry()


def all_families() -> tuple[FamilyDefinition, ...]:
    return FAMILIES


def family_by_slug(slug: str) -> FamilyDefinition | None:
    return _FAMILY_BY_SLUG.get(slug)


def document_by_path(
    family_slug: str,
    document_slug: str,
    *,
    published_only: bool = True,
) -> DocumentDefinition | None:
    document = _DOCUMENT_BY_PATH.get((family_slug, document_slug))
    if document is None:
        return None
    if published_only and not document.is_published:
        return None
    return document


def published_legal_history() -> tuple[tuple[DocumentDefinition, HistoricalVersion], ...]:
    return tuple(
        (document, version)
        for family in FAMILIES
        for document in family.documents
        for version in document.historical_versions
        if document.is_published
    )
