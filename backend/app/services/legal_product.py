from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.orm import Session

from app.models import LegalDocumentVersion
from app.services import legal_documents
from app.services.legal_documents import DocumentDefinition, DocumentStatus
from app.services.legal_versioning import (
    LegalVersioningError,
    canonical_snapshot_and_sha256,
    current_legal_version,
    validate_version_identifier,
    version_prefix_for,
)


TERMS_KEY = ("compradores", "terminos-y-condiciones")
PRIVACY_KEY = ("privacidad", "politica-de-privacidad")
COOKIES_KEY = ("privacidad", "cookies-y-tecnologias-similares")
REQUIRED_USER_LEGAL_KEYS = (TERMS_KEY, PRIVACY_KEY)


class LegalPublicationConsistencyError(LegalVersioningError):
    pass


@dataclass(frozen=True, slots=True)
class SynchronizedLegalPublication:
    document: DocumentDefinition
    version: LegalDocumentVersion


def _registry_timestamp(value: str | None, field_name: str) -> datetime:
    if not value:
        raise LegalPublicationConsistencyError(
            f"Published registry document is missing {field_name}."
        )
    normalized = value.strip().replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LegalPublicationConsistencyError(
            f"Published registry document has invalid {field_name}."
        ) from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise LegalPublicationConsistencyError(
            f"Published registry document {field_name} must be timezone-aware."
        )
    return timestamp.astimezone(timezone.utc)


def resolve_synchronized_legal_publication(
    session: Session,
    family: str,
    slug: str,
    at: datetime | None = None,
) -> SynchronizedLegalPublication | None:
    document = legal_documents.document_by_path(
        family, slug, published_only=False
    )
    if document is None or document.status != DocumentStatus.PUBLISHED:
        return None
    public_document = legal_documents.document_by_path(family, slug)
    if (
        public_document is None
        or public_document.kind != "article"
        or public_document.template_name is None
        or legal_documents.family_by_slug(family) is None
    ):
        raise LegalPublicationConsistencyError(
            "Published registry document has no public article route."
        )
    if not document.version_identifier:
        raise LegalPublicationConsistencyError(
            "Published registry document has no version identifier."
        )

    instant = at or datetime.now(timezone.utc)
    try:
        version = current_legal_version(session, family, slug, instant)
        validate_version_identifier(
            document.version_identifier,
            expected_prefix=version_prefix_for(family, slug),
        )
    except LegalVersioningError as exc:
        raise LegalPublicationConsistencyError(
            "Published registry and immutable legal version are inconsistent."
        ) from exc
    if version is None:
        raise LegalPublicationConsistencyError(
            "Published registry document has no current immutable version."
        )
    if (
        version.family != family
        or version.slug != slug
        or version.version_identifier != document.version_identifier
    ):
        raise LegalPublicationConsistencyError(
            "Published registry identifier does not match the current version."
        )
    if _registry_timestamp(
        document.published_at, "published_at"
    ) != version.published_at.astimezone(timezone.utc):
        raise LegalPublicationConsistencyError(
            "Published registry publication time does not match the current version."
        )
    if _registry_timestamp(
        document.effective_at, "effective_at"
    ) != version.effective_at.astimezone(timezone.utc):
        raise LegalPublicationConsistencyError(
            "Published registry effective time does not match the current version."
        )
    if (
        version.template_name_snapshot != document.template_name
        or version.title_snapshot != document.title
    ):
        raise LegalPublicationConsistencyError(
            "Published registry template metadata does not match the immutable version."
        )
    try:
        source, _filename, _uptodate = current_app.jinja_env.loader.get_source(
            current_app.jinja_env,
            document.template_name,
        )
        snapshot, digest = canonical_snapshot_and_sha256(source)
    except Exception as exc:
        raise LegalPublicationConsistencyError(
            "Published legal content cannot be verified."
        ) from exc
    if (
        snapshot != version.content_snapshot
        or digest != version.content_sha256
    ):
        raise LegalPublicationConsistencyError(
            "Published template content does not match the immutable version."
        )
    return SynchronizedLegalPublication(document=document, version=version)


def resolve_required_legal_publications(
    session: Session,
    at: datetime | None = None,
) -> dict[tuple[str, str], SynchronizedLegalPublication]:
    instant = at or datetime.now(timezone.utc)
    publications = {}
    for family, slug in REQUIRED_USER_LEGAL_KEYS:
        publication = resolve_synchronized_legal_publication(
            session, family, slug, instant
        )
        if publication is None:
            raise LegalPublicationConsistencyError(
                f"Required legal publication is unavailable: {family}/{slug}."
            )
        publications[(family, slug)] = publication
    return publications
