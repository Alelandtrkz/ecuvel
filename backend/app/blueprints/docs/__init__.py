from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request, url_for

from app.services.legal_documents import (
    OPERATOR_INFORMATION,
    all_families,
    document_by_path,
    family_by_slug,
    published_legal_history,
)


docs = Blueprint("docs", __name__, url_prefix="/docs")


def _canonical_url() -> str | None:
    base_url = str(current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base_url}{request.path}" if base_url else None


def _common_context(**values):
    return {
        "docs_families": all_families(),
        "operator": OPERATOR_INFORMATION,
        "canonical_url": _canonical_url(),
        **values,
    }


@docs.get("")
def home():
    return render_template(
        "docs/index.html",
        **_common_context(
            current_family=None,
            current_document=None,
            page_title="ECUVEL Docs",
            page_description="Información, políticas y documentación de ECUVEL.",
        ),
    )


@docs.get("/legal/versiones")
def versions():
    family = family_by_slug("legal")
    document = document_by_path("legal", "versiones")
    if family is None or document is None:
        abort(404)
    return render_template(
        "docs/versions.html",
        **_common_context(
            current_family=family,
            current_document=document,
            legal_history=published_legal_history(),
            page_title=f"{document.title} | ECUVEL Docs",
            page_description=document.description,
            robots_content="noindex,follow",
            breadcrumbs=(
                {"label": "ECUVEL Docs", "url": url_for("docs.home")},
                {"label": family.title.title(), "url": url_for("docs.family", family_slug=family.slug)},
                {"label": document.title, "url": None},
            ),
        ),
    )


@docs.get("/<string:family_slug>")
def family(family_slug: str):
    family_definition = family_by_slug(family_slug)
    if family_definition is None:
        abort(404)
    return render_template(
        "docs/family.html",
        **_common_context(
            current_family=family_definition,
            current_document=None,
            page_title=f"{family_definition.title.title()} | ECUVEL Docs",
            page_description=family_definition.description,
            breadcrumbs=(
                {"label": "ECUVEL Docs", "url": url_for("docs.home")},
                {"label": family_definition.title.title(), "url": None},
            ),
        ),
    )


@docs.get("/<string:family_slug>/<string:document_slug>")
def article(family_slug: str, document_slug: str):
    family_definition = family_by_slug(family_slug)
    document = document_by_path(family_slug, document_slug)
    if (
        family_definition is None
        or document is None
        or document.kind != "article"
    ):
        abort(404)
    return render_template(
        "docs/article.html",
        **_common_context(
            current_family=family_definition,
            current_document=document,
            page_title=f"{document.title} | ECUVEL Docs",
            page_description=document.description,
            breadcrumbs=(
                {"label": "ECUVEL Docs", "url": url_for("docs.home")},
                {"label": family_definition.title.title(), "url": url_for("docs.family", family_slug=family_definition.slug)},
                {"label": document.title, "url": None},
            ),
        ),
    )
