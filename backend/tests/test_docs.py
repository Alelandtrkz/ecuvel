from __future__ import annotations

import re

import pytest
from sqlalchemy import event

from app.extensions import db
from app.services.legal_documents import (
    DocumentStatus,
    all_families,
    document_by_path,
)


pytestmark = pytest.mark.integration


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
        "/docs/compradores/devoluciones-y-reembolsos",
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
    assert "Edison Alejandro Campos Leines" in operator

    privacy_section = contact.split('id="privacidad-y-datos"', 1)[1].split("</section>", 1)[0]
    assert "ecuvel.privacidad@hotmail.com" in privacy_section
    assert "ecuvel.help@hotmail.com" not in privacy_section
    assert "ecuvel.reclamos@hotmail.com" not in privacy_section


def test_registry_owns_all_template_resolution():
    documents = [document for family in all_families() for document in family.documents]

    assert document_by_path("ecuvel", "../config") is None
    assert document_by_path("../ecuvel", "contacto") is None
    assert all(
        document.template_name is None
        for document in documents
        if document.status == DocumentStatus.DRAFT
    )
    assert all(
        document.template_name
        and document.template_name.startswith("docs/")
        and ".." not in document.template_name
        for document in documents
        if document.status == DocumentStatus.PUBLISHED
    )


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
