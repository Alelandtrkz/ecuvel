from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from app.services import legal_documents
from app.services.legal_documents import DocumentStatus
from app.services.legal_versioning import create_legal_document_version


PAST_PUBLICATION = datetime(2020, 9, 20, 15, 0, tzinfo=timezone.utc)


def patch_legal_registry(monkeypatch, families) -> None:
    family_by_slug = {family.slug: family for family in families}
    document_by_path = {
        (document.family, document.slug): document
        for family in families
        for document in family.documents
    }
    monkeypatch.setattr(legal_documents, "FAMILIES", tuple(families))
    monkeypatch.setattr(legal_documents, "_FAMILY_BY_SLUG", family_by_slug)
    monkeypatch.setattr(legal_documents, "_DOCUMENT_BY_PATH", document_by_path)


def create_legal_version_from_template(
    app,
    session,
    key: tuple[str, str],
    *,
    published_at: datetime = PAST_PUBLICATION,
    effective_at: datetime | None = None,
):
    document = legal_documents.document_by_path(*key, published_only=False)
    assert document is not None and document.template_name is not None
    source, _filename, _uptodate = app.jinja_env.loader.get_source(
        app.jinja_env,
        document.template_name,
    )
    return create_legal_document_version(
        session,
        family=key[0],
        slug=key[1],
        content=source,
        published_at=published_at,
        effective_at=effective_at,
        source_revision="lr5.4-test",
    )


def publish_registry_version(monkeypatch, version) -> None:
    families = []
    found = False
    for family in legal_documents.FAMILIES:
        documents = []
        for document in family.documents:
            if (document.family, document.slug) == (
                version.family,
                version.slug,
            ):
                found = True
                document = replace(
                    document,
                    status=DocumentStatus.PUBLISHED,
                    version_identifier=version.version_identifier,
                    published_at=version.published_at.isoformat(),
                    effective_at=version.effective_at.isoformat(),
                )
            documents.append(document)
        families.append(replace(family, documents=tuple(documents)))
    assert found
    patch_legal_registry(monkeypatch, tuple(families))


def create_and_publish_required_versions(app, session, monkeypatch):
    from app.services.legal_product import PRIVACY_KEY, TERMS_KEY

    terms = create_legal_version_from_template(app, session, TERMS_KEY)
    privacy = create_legal_version_from_template(app, session, PRIVACY_KEY)
    publish_registry_version(monkeypatch, terms)
    publish_registry_version(monkeypatch, privacy)
    return terms, privacy
