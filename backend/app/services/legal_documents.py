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
        "No corresponde según la determinación actual de Ecuvel."
    ),
)


def _draft(
    family: str,
    slug: str,
    title: str,
    description: str,
    order: int,
    *,
    requires_acceptance: bool = False,
) -> DocumentDefinition:
    return DocumentDefinition(
        family=family,
        slug=slug,
        title=title,
        description=description,
        status=DocumentStatus.DRAFT,
        navigation_order=order,
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
            ),
        ),
    ),
    FamilyDefinition(
        slug="compradores",
        title="COMPRADORES",
        description="Documentación para comprar, pagar, retirar y ejercer derechos.",
        navigation_order=20,
        documents=(
            _draft("compradores", "terminos-y-condiciones", "Términos y Condiciones", "Reglas generales aplicables al uso de ECUVEL.", 10, requires_acceptance=True),
            _draft("compradores", "condiciones-de-compra", "Condiciones de compra", "Formación y ejecución de compras en la plataforma.", 20),
            _draft("compradores", "pagos", "Pagos", "Métodos, comprobantes y revisión de pagos.", 30),
            _draft("compradores", "entregas", "Entregas", "Preparación, conservación y retiro de pedidos.", 40),
            _draft("compradores", "devoluciones-y-reembolsos", "Devoluciones y reembolsos", "Condiciones y procedimientos aplicables.", 50),
            _draft("compradores", "garantias", "Garantías", "Cobertura y ejercicio de garantías.", 60),
            _draft("compradores", "reclamos", "Reclamos", "Canal y procedimiento para reclamos de consumo.", 70),
            _draft("compradores", "productos-restringidos", "Productos restringidos", "Categorías y productos no admitidos o sujetos a controles.", 80),
        ),
    ),
    FamilyDefinition(
        slug="privacidad",
        title="PRIVACIDAD Y DATOS",
        description="Información sobre datos personales, tecnologías y derechos.",
        navigation_order=30,
        documents=(
            _draft("privacidad", "politica-de-privacidad", "Política de Privacidad", "Información sobre los tratamientos de datos personales de ECUVEL.", 10),
            _draft("privacidad", "cookies-y-tecnologias-similares", "Cookies y tecnologías similares", "Tecnologías utilizadas por la plataforma y sus finalidades.", 20),
            _draft("privacidad", "derechos-del-titular", "Derechos del titular", "Cómo ejercer derechos relacionados con datos personales.", 30),
            _draft("privacidad", "comunicaciones-y-marketing", "Comunicaciones y marketing", "Diferencia entre mensajes operativos y comunicaciones comerciales.", 40),
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
            _draft("plataforma", "uso-aceptable", "Uso aceptable", "Conductas permitidas y prohibidas en ECUVEL.", 10),
            _draft("plataforma", "resenas-y-contenido", "Reseñas y contenido", "Publicación, revisión y moderación de contenido.", 20),
            _draft("plataforma", "propiedad-intelectual", "Propiedad intelectual", "Uso de marcas, imágenes, textos y otros contenidos.", 30),
            _draft("plataforma", "fraude-y-abuso", "Fraude y abuso", "Prevención y respuesta ante usos abusivos.", 40),
            _draft("plataforma", "suspension-de-cuentas", "Suspensión de cuentas", "Causas y efectos de las restricciones de acceso.", 50),
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
