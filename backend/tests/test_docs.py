from __future__ import annotations

import re

import pytest
from sqlalchemy import event

from app.extensions import db
from app.services.legal_documents import (
    DocumentStatus,
    OPERATOR_INFORMATION,
    all_families,
    document_by_path,
)


pytestmark = pytest.mark.integration


LR5_2B_DOCUMENTS = {
    "condiciones-de-compra": "docs/content/compradores/condiciones_de_compra.html",
    "pagos": "docs/content/compradores/pagos.html",
    "entregas": "docs/content/compradores/entregas.html",
    "devoluciones-y-reembolsos": (
        "docs/content/compradores/devoluciones_y_reembolsos.html"
    ),
    "garantias": "docs/content/compradores/garantias.html",
}

LR5_2C_DOCUMENTS = {
    ("ecuvel", "seguridad"): "docs/content/ecuvel/seguridad.html",
    ("compradores", "reclamos"): "docs/content/compradores/reclamos.html",
    ("compradores", "productos-restringidos"): (
        "docs/content/compradores/productos_restringidos.html"
    ),
    ("privacidad", "cookies-y-tecnologias-similares"): (
        "docs/content/privacidad/cookies_y_tecnologias_similares.html"
    ),
    ("privacidad", "derechos-del-titular"): (
        "docs/content/privacidad/derechos_del_titular.html"
    ),
    ("privacidad", "comunicaciones-y-marketing"): (
        "docs/content/privacidad/comunicaciones_y_marketing.html"
    ),
    ("plataforma", "uso-aceptable"): "docs/content/plataforma/uso_aceptable.html",
    ("plataforma", "resenas-y-contenido"): (
        "docs/content/plataforma/resenas_y_contenido.html"
    ),
    ("plataforma", "propiedad-intelectual"): (
        "docs/content/plataforma/propiedad_intelectual.html"
    ),
    ("plataforma", "fraude-y-abuso"): "docs/content/plataforma/fraude_y_abuso.html",
    ("plataforma", "suspension-de-cuentas"): (
        "docs/content/plataforma/suspension_de_cuentas.html"
    ),
}

LR5_2D_DOCUMENTS = {
    "como-vender": "docs/content/vendedores/como_vender.html",
    "politicas": "docs/content/vendedores/politicas.html",
    "productos-permitidos-y-prohibidos": (
        "docs/content/vendedores/productos_permitidos_y_prohibidos.html"
    ),
    "comisiones": "docs/content/vendedores/comisiones.html",
    "pagos-y-liquidaciones": (
        "docs/content/vendedores/pagos_y_liquidaciones.html"
    ),
    "logistica": "docs/content/vendedores/logistica.html",
    "contrato": "docs/content/vendedores/contrato.html",
    "proteccion-de-datos-seller": (
        "docs/content/vendedores/proteccion_de_datos_seller.html"
    ),
}


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def test_docs_home_is_public_and_has_unique_metadata(client):
    response = client.get("/docs")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers.get("Location") is None
    assert "<title>ECUVEL Docs</title>" in body
    assert '<meta name="description" content="Información, políticas y documentación de ECUVEL.">' in body
    assert '<link rel="canonical" href="https://ecuvel.test/docs">' in body
    assert "Documentación en preparación" in body
    assert "catalog-telemetry.js" not in body
    assert "unpkg.com" not in body


@pytest.mark.parametrize(
    "path,title",
    [
        ("/docs/ecuvel/que-es-ecuvel", "Qué es ECUVEL"),
        ("/docs/ecuvel/como-funciona", "Cómo funciona"),
        ("/docs/ecuvel/contacto", "Contacto"),
        ("/docs/legal/operador", "Datos del operador"),
    ],
)
def test_published_informational_articles_are_public(client, path, title):
    response = client.get(path)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert f"<title>{title} | ECUVEL Docs</title>" in body
    assert '<meta name="description"' in body
    assert 'aria-label="Ruta de navegación"' in body


@pytest.mark.parametrize("family", ["ecuvel", "compradores", "privacidad", "vendedores", "plataforma", "legal"])
def test_family_landings_are_public(client, family):
    response = client.get(f"/docs/{family}")

    assert response.status_code == 200
    assert response.headers.get("Location") is None


def test_unknown_family_and_document_return_404(client):
    assert client.get("/docs/desconocida").status_code == 404
    assert client.get("/docs/ecuvel/desconocido").status_code == 404
    assert client.get("/docs/../config").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/docs/compradores/terminos-y-condiciones",
        "/docs/privacidad/politica-de-privacidad",
        "/docs/compradores/condiciones-de-compra",
        "/docs/compradores/pagos",
        "/docs/compradores/entregas",
        "/docs/compradores/devoluciones-y-reembolsos",
        "/docs/compradores/garantias",
        "/docs/vendedores/contrato",
    ],
)
def test_draft_documents_have_no_public_current_route(client, path):
    assert client.get(path).status_code == 404


def test_sidebar_exposes_current_family_and_article(client):
    body = client.get("/docs/ecuvel/contacto").get_data(as_text=True)

    assert re.search(
        r'<details class="docs-tree__family is-current" open>\s*<summary[^>]*>ECUVEL</summary>',
        body,
    )
    assert re.search(
        r'<a href="/docs/ecuvel/contacto" class="is-active" aria-current="page">Contacto</a>',
        body,
    )
    assert "Términos y Condiciones" in body
    assert "En preparación" in body


def test_breadcrumbs_are_metadata_driven_and_end_at_current_page(client):
    body = client.get("/docs/legal/operador").get_data(as_text=True)

    assert '<a href="/docs">ECUVEL Docs</a>' in body
    assert '<a href="/docs/legal">Legal</a>' in body
    assert '<span aria-current="page">Datos del operador</span>' in body


def test_toc_uses_registered_deterministic_anchors(client):
    body = client.get("/docs/ecuvel/como-funciona").get_data(as_text=True)

    for anchor in (
        "explorar",
        "cuenta",
        "compra-y-pago",
        "retiro",
        "vendedores",
    ):
        assert f'href="#{anchor}"' in body
        assert f'id="{anchor}"' in body


def test_version_archive_has_accurate_empty_state(client):
    response = client.get("/docs/legal/versiones")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Aún no hay versiones legales publicadas." in body
    assert "terms-2026" not in body
    assert "privacy-2026" not in body
    assert '<meta name="robots" content="noindex,follow">' in body
    assert client.get("/docs/legal/versiones/terms/fake-version").status_code == 404


def test_contact_channels_remain_distinct(client):
    contact = client.get("/docs/ecuvel/contacto").get_data(as_text=True)
    operator = client.get("/docs/legal/operador").get_data(as_text=True)

    expected = {
        "ecuvel.help@hotmail.com",
        "ecuvel.reclamos@hotmail.com",
        "ecuvel.privacidad@hotmail.com",
    }
    for address in expected:
        assert f'mailto:{address}' in contact
        assert f'mailto:{address}' in operator
    assert "0963267781" in contact
    assert "2100497391001" in operator
    assert "Av. 9 de Octubre y Miguel Gamboa" in operator
    assert "Edison Alejandro Campos Leines" in operator
    assert "Delegado de Protección de Datos" not in operator

    privacy_section = contact.split('id="privacidad-y-datos"', 1)[1].split("</section>", 1)[0]
    assert "ecuvel.privacidad@hotmail.com" in privacy_section
    assert "ecuvel.help@hotmail.com" not in privacy_section
    assert "ecuvel.reclamos@hotmail.com" not in privacy_section


def test_registry_owns_all_template_resolution():
    documents = [document for family in all_families() for document in family.documents]
    prepared_drafts = {
        ("compradores", "terminos-y-condiciones"),
        ("privacidad", "politica-de-privacidad"),
        *(("compradores", slug) for slug in LR5_2B_DOCUMENTS),
        *LR5_2C_DOCUMENTS,
        *(("vendedores", slug) for slug in LR5_2D_DOCUMENTS),
    }

    assert document_by_path("ecuvel", "../config") is None
    assert document_by_path("../ecuvel", "contacto") is None
    assert all(
        document.template_name is None
        for document in documents
        if document.status == DocumentStatus.DRAFT
        and (document.family, document.slug) not in prepared_drafts
    )
    assert all(
        document.template_name
        and document.template_name.startswith("docs/")
        and ".." not in document.template_name
        for document in documents
        if document.status == DocumentStatus.PUBLISHED
    )


def test_lr5_2a_prepared_legal_documents_remain_non_public_drafts(app, client):
    terms = document_by_path(
        "compradores", "terminos-y-condiciones", published_only=False
    )
    privacy = document_by_path(
        "privacidad", "politica-de-privacidad", published_only=False
    )

    assert terms is not None
    assert terms.status == DocumentStatus.DRAFT
    assert terms.template_name == "docs/content/compradores/terminos_y_condiciones.html"
    assert terms.requires_acceptance is True
    assert len(terms.sections) == 21

    assert privacy is not None
    assert privacy.status == DocumentStatus.DRAFT
    assert privacy.template_name == "docs/content/privacidad/politica_de_privacidad.html"
    assert privacy.requires_acceptance is False
    assert len(privacy.sections) == 20

    for document in (terms, privacy):
        assert document.version_identifier is None
        assert document.published_at is None
        assert document.effective_at is None
        assert document.historical_versions == ()
        assert document_by_path(document.family, document.slug) is None
        assert client.get(f"/docs/{document.family}/{document.slug}").status_code == 404
        with app.app_context():
            app.jinja_env.get_template(document.template_name)
            source, _filename, _uptodate = app.jinja_env.loader.get_source(
                app.jinja_env, document.template_name
            )
        for section in document.sections:
            assert f'id="{section.anchor}"' in source


def test_operator_registry_keeps_exact_owner_supplied_identity():
    assert OPERATOR_INFORMATION.name == "Ecuvel"
    assert OPERATOR_INFORMATION.ruc == "2100497391001"
    assert OPERATOR_INFORMATION.legal_address == "Av. 9 de Octubre y Miguel Gamboa"
    assert OPERATOR_INFORMATION.legal_phone == "0963267781"
    assert OPERATOR_INFORMATION.help_email == "ecuvel.help@hotmail.com"
    assert OPERATOR_INFORMATION.claims_email == "ecuvel.reclamos@hotmail.com"
    assert OPERATOR_INFORMATION.privacy_email == "ecuvel.privacidad@hotmail.com"
    assert OPERATOR_INFORMATION.controller_representative == "Edison Alejandro Campos Leines"
    assert "no corresponde a la operación actual" in OPERATOR_INFORMATION.data_protection_officer
    assert "debe reevaluarse" in OPERATOR_INFORMATION.data_protection_officer


def test_lr5_2a_reconciled_terms_source_matches_approved_target_policy(app):
    template = "docs/content/compradores/terminos_y_condiciones.html"
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, template
        )

    assert "aceptación del Subpedido por la Tienda" not in source
    assert "deciden si aceptan o rechazan sus Subpedidos" not in source
    assert "Cuando una Tienda rechaza un Subpedido" not in source
    assert "queda confirmado automáticamente" in source
    assert "siete días calendario" in source
    assert "reembolso completo" in source
    assert "100 % del valor pagado" in source
    assert "quince días hábiles" in source
    assert "le corresponde emitir la factura tributaria" in source
    assert "no reemplaza la factura de la Tienda" in source
    assert "bienes o servicios por cualquier medio" in source
    assert "por medios distintos a una venta directa presencial" not in source


def test_lr5_2b_documents_are_complete_non_public_drafts(app, client):
    for slug, template_name in LR5_2B_DOCUMENTS.items():
        document = document_by_path("compradores", slug, published_only=False)

        assert document is not None
        assert document.status == DocumentStatus.DRAFT
        assert document.template_name == template_name
        assert document.requires_acceptance is False
        assert document.version_identifier is None
        assert document.published_at is None
        assert document.effective_at is None
        assert document.historical_versions == ()
        assert document.sections
        assert tuple(section.anchor for section in document.sections) == tuple(
            dict.fromkeys(section.anchor for section in document.sections)
        )
        assert document_by_path("compradores", slug) is None
        assert client.get(f"/docs/compradores/{slug}").status_code == 404

        with app.app_context():
            source, _filename, _uptodate = app.jinja_env.loader.get_source(
                app.jinja_env, template_name
            )
        for section in document.sections:
            assert f'id="{section.anchor}"' in source


def test_lr5_2b_purchase_and_payment_policies_match_current_payment_model(app):
    with app.app_context():
        purchase, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2B_DOCUMENTS["condiciones-de-compra"]
        )
        payments, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2B_DOCUMENTS["pagos"]
        )

    for source in (purchase, payments):
        assert "único método" in source
        assert "transferencia bancaria" in source
        assert "factura" in source
    assert "no reemplaza" in purchase
    assert "no sustituye" in payments
    assert "no ofrece actualmente pagos con tarjeta" in payments
    assert "no aprueba ni rechaza el pago por sí solo" in payments
    assert "Personal ECUVEL autorizado" in payments
    assert "automáticamente" in purchase
    assert "segunda aceptación discrecional" in purchase


def test_lr5_2b_delivery_and_refund_policies_keep_approved_limits(app):
    with app.app_context():
        delivery, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2B_DOCUMENTS["entregas"]
        )
        refunds, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2B_DOCUMENTS["devoluciones-y-reembolsos"]
        )

    delivery_non_pickup = delivery.split('id="plazo-de-siete-dias"', 1)[1].split(
        "</section>", 1
    )[0]
    stock_refund = refunds.split('id="falta-de-stock"', 1)[1].split(
        "</section>", 1
    )[0]
    non_pickup_refund = refunds.split('id="pedido-no-retirado"', 1)[1].split(
        "</section>", 1
    )[0]
    statutory_return = refunds.split(
        'id="devolucion-o-cambio-legal"', 1
    )[1].split("</section>", 1)[0]

    assert "siete días calendario" in delivery_non_pickup
    assert "no ofrece en este documento entrega a domicilio" in delivery
    assert "reembolso completo" in delivery_non_pickup
    assert "100 % del valor pagado" in delivery_non_pickup
    assert "plazo máximo" in delivery_non_pickup
    assert "quince días hábiles" in delivery_non_pickup
    assert "penalidad ni deducción" in delivery_non_pickup

    for operational_cause in (stock_refund, non_pickup_refund):
        assert "100 % del valor pagado" in operational_cause
        assert "plazo máximo" in operational_cause
        assert "quince días hábiles" in operational_cause
    assert "penalidad ni deducción" in stock_refund
    assert "cargo de almacenamiento, reposición, penalidad ni deducción" in non_pickup_refund

    assert "bienes o servicios por cualquier medio" in statutory_return
    assert "quince días posteriores a la recepción" in statutory_return
    assert "quince días hábiles" not in statutory_return
    assert "plazo operativo final permanece pendiente" not in refunds
    assert "debe confirmarse con el propietario" not in refunds


def test_lr5_2b_warranty_policy_keeps_ecuvel_in_the_case(app):
    with app.app_context():
        warranty, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2B_DOCUMENTS["garantias"]
        )

    assert "ECUVEL actúa como punto de contacto y coordinación" in warranty
    assert "ECUVEL registra el caso" in warranty
    assert "reseña pública" in warranty
    assert "métrica interna" in warranty
    assert "aprobación automática" in warranty


def test_lr5_2b_does_not_publish_or_add_acceptance_to_seller_contract_document():
    seller_contract = document_by_path(
        "vendedores", "contrato", published_only=False
    )

    assert seller_contract is not None
    assert seller_contract.status == DocumentStatus.DRAFT
    assert seller_contract.template_name == "docs/content/vendedores/contrato.html"
    assert seller_contract.requires_acceptance is False


def test_lr5_2a_reconciled_privacy_source_is_specific_and_not_universal(app):
    template = "docs/content/privacidad/politica_de_privacidad.html"
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, template
        )

    assert "Hostinger" in source
    assert "Estados Unidos" in source
    assert "Microsoft/Hotmail" in source
    assert "canal principal" in source
    assert "se reciben exclusivamente" not in source
    assert "no se aplica de forma general a todos los datos" in source
    assert "períodos más breves definidos por su finalidad" in source
    assert "no una aceptación contractual ni un consentimiento general" in source
    assert "Consentimiento previo, opcional y revocable" in source
    assert "no adopta interés legítimo como base para esta finalidad" in source
    assert "UUID seudónimo de catálogo" in source
    assert "UUID anónimo de catálogo" not in source


def test_lr5_2c_documents_are_complete_non_public_drafts(app, client):
    for (family, slug), template_name in LR5_2C_DOCUMENTS.items():
        document = document_by_path(family, slug, published_only=False)

        assert document is not None
        assert document.status == DocumentStatus.DRAFT
        assert document.template_name == template_name
        assert document.requires_acceptance is False
        assert document.version_identifier is None
        assert document.published_at is None
        assert document.effective_at is None
        assert document.historical_versions == ()
        assert document.sections
        assert tuple(section.anchor for section in document.sections) == tuple(
            dict.fromkeys(section.anchor for section in document.sections)
        )
        assert document_by_path(family, slug) is None
        assert client.get(f"/docs/{family}/{slug}").status_code == 404

        with app.app_context():
            source, _filename, _uptodate = app.jinja_env.loader.get_source(
                app.jinja_env, template_name
            )
        assert "Borrador para revisión; no vigente" in source
        for section in document.sections:
            assert f'id="{section.anchor}"' in source


def test_lr5_2c_cookie_policy_matches_real_mechanisms_and_approved_choice(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("privacidad", "cookies-y-tecnologias-similares")],
        )

    assert "Cookie de sesión de ECUVEL" in source
    assert "Cookie de recuerdo" in source
    assert "UUID de catálogo" in source
    assert "valores firmados" in source
    assert "no cookies independientes" in source
    assert "telemetría" in source
    assert "desactivada por defecto" in source
    assert "aceptar o rechazar" in source
    assert "No existe actualmente ese control" in source
    assert "banner" in source
    assert "Esto no significa por sí solo que instalen una cookie de ECUVEL" in source


def test_lr5_2c_privacy_rights_and_marketing_are_separate_and_accurate(app):
    with app.app_context():
        rights, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("privacidad", "derechos-del-titular")],
        )
        marketing, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("privacidad", "comunicaciones-y-marketing")],
        )

    assert "canal principal habilitado" in rights
    assert "ecuvel.privacidad@hotmail.com" in rights
    assert "canal exclusivo" not in rights
    for right in (
        "información",
        "acceso",
        "rectificación",
        "eliminación",
        "oposición",
        "suspensión",
        "portabilidad",
        "decisiones automatizadas",
    ):
        assert right in rights
    assert "quince días" in rights
    assert "Cerrar o eliminar una cuenta no significa borrar inmediatamente" in rights
    assert "hasta por siete años el subconjunto necesario" in rights

    assert "no tiene confirmadas campañas ni una captura activa" in marketing
    assert "Comunicaciones necesarias" in marketing
    assert "Marketing" in marketing
    assert "no autoriza ese uso" in marketing
    assert "no preseleccionada" in marketing
    assert "No existe segmentación de marketing activa" in marketing


def test_lr5_2c_claims_and_restricted_products_keep_approved_boundaries(app):
    with app.app_context():
        claims, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2C_DOCUMENTS[("compradores", "reclamos")]
        )
        restricted, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("compradores", "productos-restringidos")],
        )

    assert "ecuvel.reclamos@hotmail.com" in claims
    assert "no queda obligado a gestionar solo con la Tienda" in claims
    assert "no es una instancia administrativa ni judicial obligatoria" in claims
    assert "Ayuda general y asistencia sobre factura" in claims
    assert "Derechos y asuntos de datos personales" in claims

    for classification in (
        "Prohibido:",
        "No soportado actualmente:",
        "Soporte regulado o condicional futuro:",
        "Mercancía general ordinaria:",
    ):
        assert classification in restricted
    assert "política comercial de lanzamiento de ECUVEL" in restricted
    assert "no significa que todas esas categorías sean intrínsecamente ilegales" in restricted
    assert "Los cosméticos forman parte del catálogo previsto" in restricted
    assert "no opera actualmente categorías para adultos" in restricted
    assert "flujo de verificación correspondiente" in restricted


def test_lr5_2c_review_ip_fraud_and_suspension_match_product_truth(app):
    with app.app_context():
        reviews, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("plataforma", "resenas-y-contenido")],
        )
        intellectual_property, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("plataforma", "propiedad-intelectual")],
        )
        fraud, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2C_DOCUMENTS[("plataforma", "fraude-y-abuso")]
        )
        suspension, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2C_DOCUMENTS[("plataforma", "suspension-de-cuentas")],
        )

    assert "reglas determinísticas" in reviews
    assert "no son inteligencia artificial" in reviews
    assert "corrección/reenvío real" not in reviews
    assert "corrija y vuelva a enviarla" in reviews
    assert "función distinta y no modifica las estrellas" in reviews
    assert "no transfiere la propiedad" in reviews

    assert "no transfiere su propiedad a la plataforma" in intellectual_property
    assert "no es un procedimiento DMCA" in intellectual_property
    assert "ecuvel.help@hotmail.com" in intellectual_property

    assert "no revela umbrales" in fraud
    assert "una señal técnica no implica por sí sola culpabilidad" in fraud
    assert "ACTIVE" in suspension
    assert "BLOCKED" in suspension
    assert "SUSPENDED" in suspension
    assert "no extingue Pedidos ya pagados" in suspension
    assert "reembolsos, garantías, reclamos" in suspension
    assert "derechos de privacidad" in suspension


def test_lr5_2c_does_not_publish_seller_docs_or_change_acceptance_model():
    terms = document_by_path(
        "compradores", "terminos-y-condiciones", published_only=False
    )
    privacy = document_by_path(
        "privacidad", "politica-de-privacidad", published_only=False
    )

    assert terms is not None and terms.requires_acceptance is True
    assert privacy is not None and privacy.requires_acceptance is False
    for slug in (
        "como-vender",
        "politicas",
        "productos-permitidos-y-prohibidos",
        "contrato",
    ):
        seller_document = document_by_path("vendedores", slug, published_only=False)
        assert seller_document is not None
        assert seller_document.status == DocumentStatus.DRAFT
        assert seller_document.template_name == LR5_2D_DOCUMENTS[slug]
        assert seller_document.historical_versions == ()


def test_lr5_2d_documents_are_complete_non_public_drafts(app, client):
    seller_family = next(family for family in all_families() if family.slug == "vendedores")

    assert tuple(document.slug for document in seller_family.documents) == tuple(
        LR5_2D_DOCUMENTS
    )
    assert tuple(document.navigation_order for document in seller_family.documents) == (
        10, 20, 30, 40, 50, 60, 70, 80
    )

    for slug, template_name in LR5_2D_DOCUMENTS.items():
        document = document_by_path("vendedores", slug, published_only=False)

        assert document is not None
        assert document.status == DocumentStatus.DRAFT
        assert document.template_name == template_name
        assert document.requires_acceptance is False
        assert document.version_identifier is None
        assert document.published_at is None
        assert document.effective_at is None
        assert document.historical_versions == ()
        assert document.sections
        assert tuple(section.anchor for section in document.sections) == tuple(
            dict.fromkeys(section.anchor for section in document.sections)
        )
        assert document_by_path("vendedores", slug) is None
        assert client.get(f"/docs/vendedores/{slug}").status_code == 404

        with app.app_context():
            source, _filename, _uptodate = app.jinja_env.loader.get_source(
                app.jinja_env, template_name
            )
        assert "Borrador para revisión; no vigente" in source
        for section in document.sections:
            assert f'id="{section.anchor}"' in source


def test_lr5_2d_onboarding_and_seller_obligations_match_product(app):
    with app.app_context():
        onboarding, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["como-vender"]
        )
        policies, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["politicas"]
        )

    for expected in (
        "Revisión de detalles",
        "Dirección de la entidad legal",
        "Contacto:",
        "Documentos:",
        "Datos para pago:",
        "CORRECTIONS_REQUESTED",
        "CONTRACT_PENDING",
        "COMPLETED",
        "ACTIVE",
    ):
        assert expected in onboarding
    assert "personas naturales o jurídicas" in onboarding
    assert "no significa que toda Tienda deba presentar exactamente los mismos" in onboarding
    assert "únicamente en ECUVEL Partners" in onboarding

    for obligation in (
        "licitud, autenticidad, seguridad y conformidad",
        "stock físico real",
        "factura o comprobante de la Tienda",
        "garantías",
        "Fraude",
        "colaborar con ECUVEL",
    ):
        assert obligation in policies
    assert "sanciones automáticas" in policies
    assert "Tienda sea la vendedora" in policies
    assert "recibo o registro de compra de ECUVEL no reemplaza" in policies


def test_lr5_2d_restricted_products_preserve_four_classes_and_arcsa_boundary(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env,
            LR5_2D_DOCUMENTS["productos-permitidos-y-prohibidos"],
        )

    for classification in (
        "Prohibido:",
        "No soportado actualmente:",
        "Soporte regulado o condicional futuro:",
        "Mercancía general ordinaria:",
    ):
        assert classification in source
    assert "política comercial de lanzamiento" in source
    assert "no afirma que toda la categoría sea intrínsecamente ilegal" in source
    assert "Notificación Sanitaria Obligatoria" in source
    assert "sólo podrá habilitarse después de definir y verificar" in source
    assert "Los documentos exigibles dependerán del producto" in source
    assert "no convierte un requisito de cosméticos" in source
    assert "producto ilícito o inseguro" in source


def test_lr5_2d_commission_policy_matches_resolver(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["comisiones"]
        )

    assert "superior a USD 0,25" in source
    assert "menor a USD 3,00" in source
    assert "USD 0,25" in source
    assert "Desde <strong>USD 3,00</strong>" in source
    assert "categoría concreta o su linaje" in source
    assert "regla global activa" in source
    assert "ignora el identificador de Tienda" in source
    assert "no aplica tarifas negociadas individualmente" in source
    for snapshot_field in (
        "categoría y su ruta",
        "precio",
        "modo fijo o porcentual",
        "porcentaje o tarifa fija",
        "comisión calculada",
        "neto Seller",
        "identificador de regla",
        "origen",
    ):
        assert snapshot_field in source
    assert "neto = bruto − descuentos − comisión" in source


def test_lr5_2d_payout_policy_protects_pmt_pay_and_real_calendar(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["pagos-y-liquidaciones"]
        )

    pmt_pay = source.split('id="pmt-y-pay"', 1)[1].split("</section>", 1)[0]
    assert "PMT-" in pmt_pay and "pago del Comprador" in pmt_pay
    assert "PAY-" in pmt_pay and "liquidación de ECUVEL a una Tienda" in pmt_pay
    assert "entrega completa" in source
    assert "cuatro días después" in source
    assert "PMT- aprobado" in source
    assert "resolución de reembolso pendiente" in source
    assert "versión bancaria aprobada" in source
    assert "día 15" in source
    assert "último día hábil del mes" in source
    assert "America/Guayaquil" in source
    assert "hasta el día 14" in source
    assert "lunes a viernes" in source
    assert "feriados bancarios ecuatorianos <strong>no están modelados</strong>" in source
    assert "no garantizan que el banco acredite inmediatamente" in source
    assert "política de Devoluciones y reembolsos aplicable" in source
    assert "no crea un plazo adicional de reintegro a cargo de la Tienda" in source


def test_lr5_2d_logistics_marks_future_auto_confirmation_and_separate_remedies(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["logistica"]
        )

    assert "Cuando esta política entre en vigor" in source
    assert "sin un veto tardío discrecional del Seller" in source
    assert "siete días calendario" in source
    assert "producto vuelve a la Tienda" in source
    assert "reembolso completo" in source
    assert "penalidad ni deducción" in source
    assert "política de Devoluciones y reembolsos aplicable" in source
    assert "no establece un plazo de pago o reintegro a cargo de la Tienda" in source
    assert "derecho legal de devolución o cambio del artículo 45" in source
    assert "garantía por defecto" in source


def test_lr5_2d_contract_is_informational_and_not_a_second_acceptance(app):
    document = document_by_path("vendedores", "contrato", published_only=False)
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["contrato"]
        )

    assert document is not None and document.requires_acceptance is False
    assert "exclusivamente informativo" in source
    assert "no crea una segunda aceptación" in source
    assert "únicamente al validar el OTP" in source
    assert "Docs no presenta un checkbox" in source
    assert "el texto contractual presentado en ECUVEL Partners" in source
    assert "su copia descargable y la versión registrada" in source
    assert "misma versión contractual aplicable" in source
    assert "no reproduce esos identificadores ni les asigna vigencia" in source


def test_lr5_2d_seller_privacy_has_differentiated_retention_and_channels(app):
    with app.app_context():
        source, _filename, _uptodate = app.jinja_env.loader.get_source(
            app.jinja_env, LR5_2D_DOCUMENTS["proteccion-de-datos-seller"]
        )

    for category in (
        "cédula, RUC u otra identificación",
        "datos bancarios",
        "miembros y roles",
        "productos",
        "PAY-",
        "auditoría",
        "IP, agente de usuario",
    ):
        assert category in source
    assert "No todos los datos se conservan siete años" in source
    for short_lived in ("OTP vencidos", "sesiones", "tokens", "carritos", "telemetría"):
        assert short_lived in source
    assert "canal principal habilitado" in source
    assert "no el único medio legalmente válido" in source
    assert "ecuvel.help@hotmail.com" in source
    assert "ecuvel.reclamos@hotmail.com" in source
    assert "ecuvel.privacidad@hotmail.com" in source


def test_lr5_2d_public_templates_hide_internal_review_labels(app):
    forbidden = (
        "PENDING COUNSEL VALIDATION",
        "PENDING PRODUCT/LEGAL DESIGN",
        "Seller Contract Canonicalization",
        "SellerOrderDecisionStatus.APPROVED",
        "LR5.2C",
        "decisión de producto",
    )

    with app.app_context():
        sources = [
            app.jinja_env.loader.get_source(app.jinja_env, template_name)[0]
            for template_name in LR5_2D_DOCUMENTS.values()
        ]

    for source in sources:
        assert all(label not in source for label in forbidden)


def test_docs_requests_do_not_mutate_database(app, client):
    statements: list[str] = []

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().upper())

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            assert client.get("/docs").status_code == 200
            assert client.get("/docs/ecuvel/contacto").status_code == 200
            assert client.get("/docs/legal/versiones").status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

    assert not any(
        statement.startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE"))
        for statement in statements
    )


def test_docs_is_read_only(client):
    assert client.post("/docs").status_code == 405
    assert client.post("/docs/ecuvel/contacto").status_code == 405


def test_existing_auth_and_storefront_get_routes_still_respond(client):
    assert client.get("/registro").status_code == 200
    assert client.get("/").status_code == 200
