from __future__ import annotations

import hashlib
import ipaddress
import re
import uuid
from datetime import date, datetime, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LegalDocumentVersion, UserLegalAcceptance
from app.models.enums import (
    LegalAcceptanceAction,
    LegalAcceptanceMode,
    LegalAcceptanceSource,
)
from app.services.legal_documents import document_by_path


ECUADOR_TIME_ZONE = ZoneInfo("America/Guayaquil")

DOCUMENT_VERSION_PREFIXES = MappingProxyType(
    {
        ("ecuvel", "seguridad"): "security",
        ("compradores", "terminos-y-condiciones"): "terms",
        ("compradores", "condiciones-de-compra"): "purchase",
        ("compradores", "pagos"): "payments",
        ("compradores", "entregas"): "delivery",
        ("compradores", "devoluciones-y-reembolsos"): "returns",
        ("compradores", "garantias"): "warranty",
        ("compradores", "reclamos"): "claims",
        ("compradores", "productos-restringidos"): "restricted-products",
        ("privacidad", "politica-de-privacidad"): "privacy",
        ("privacidad", "cookies-y-tecnologias-similares"): "cookies",
        ("privacidad", "derechos-del-titular"): "data-rights",
        ("privacidad", "comunicaciones-y-marketing"): "marketing",
        ("plataforma", "uso-aceptable"): "acceptable-use",
        ("plataforma", "resenas-y-contenido"): "reviews-content",
        ("plataforma", "propiedad-intelectual"): "intellectual-property",
        ("plataforma", "fraude-y-abuso"): "fraud-abuse",
        ("plataforma", "suspension-de-cuentas"): "account-suspension",
        ("vendedores", "como-vender"): "seller-how-to-sell",
        ("vendedores", "politicas"): "seller-policies",
        (
            "vendedores",
            "productos-permitidos-y-prohibidos",
        ): "seller-products",
        ("vendedores", "comisiones"): "seller-commissions",
        ("vendedores", "pagos-y-liquidaciones"): "seller-payouts",
        ("vendedores", "logistica"): "seller-logistics",
        ("vendedores", "proteccion-de-datos-seller"): "seller-privacy",
    }
)

RESERVED_VERSION_PREFIXES = frozenset({"seller-contract"})
CANONICAL_VERSION_PREFIXES = frozenset(DOCUMENT_VERSION_PREFIXES.values()).union(
    RESERVED_VERSION_PREFIXES
)

USER_LEGAL_REQUIREMENTS = MappingProxyType(
    {
        ("compradores", "terminos-y-condiciones"): LegalAcceptanceMode.ACCEPTED,
        (
            "privacidad",
            "politica-de-privacidad",
        ): LegalAcceptanceMode.ACKNOWLEDGED,
    }
)

_VERSION_IDENTIFIER = re.compile(
    r"^(?P<prefix>[a-z0-9]+(?:-[a-z0-9]+)*)-"
    r"(?P<publication_date>[0-9]{4}-[0-9]{2}-[0-9]{2})-"
    r"v(?P<version_number>[1-9][0-9]*)$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_JINJA_MARKERS = ("{{", "{%", "{#")


class LegalVersioningError(ValueError):
    pass


class UnknownLegalDocumentError(LegalVersioningError):
    pass


class InvalidVersionIdentifierError(LegalVersioningError):
    pass


class DynamicLegalTemplateError(LegalVersioningError):
    pass


class InvalidLegalAcceptanceError(LegalVersioningError):
    pass


def version_prefix_for(family: str, slug: str) -> str:
    try:
        return DOCUMENT_VERSION_PREFIXES[(family, slug)]
    except KeyError as exc:
        if (family, slug) == ("vendedores", "contrato"):
            raise UnknownLegalDocumentError(
                "The informational Seller contract article is not the canonical "
                "Partners contract and cannot use seller-contract."
            ) from exc
        raise UnknownLegalDocumentError(
            f"No legal version prefix is registered for {family}/{slug}."
        ) from exc


def acceptance_mode_for(family: str, slug: str) -> LegalAcceptanceMode:
    version_prefix_for(family, slug)
    return USER_LEGAL_REQUIREMENTS.get(
        (family, slug), LegalAcceptanceMode.NONE
    )


def canonicalize_legal_content(content: str) -> str:
    if not isinstance(content, str):
        raise LegalVersioningError("Legal content must be text.")
    if any(marker in content for marker in _JINJA_MARKERS):
        raise DynamicLegalTemplateError(
            "Versionable legal body templates must be deterministic and contain "
            "no unresolved Jinja expressions."
        )
    canonical = content.replace("\r\n", "\n").replace("\r", "\n")
    if not canonical:
        raise LegalVersioningError("Legal content cannot be empty.")
    canonical.encode("utf-8")
    return canonical


def content_sha256(content_snapshot: str) -> str:
    canonical = canonicalize_legal_content(content_snapshot)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_snapshot_and_sha256(content: str) -> tuple[str, str]:
    canonical = canonicalize_legal_content(content)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LegalVersioningError(f"{field_name} must include a time zone.")
    return value


def publication_date_in_ecuador(published_at: datetime) -> date:
    return _aware(published_at, "published_at").astimezone(
        ECUADOR_TIME_ZONE
    ).date()


def build_version_identifier(
    version_prefix: str,
    published_at: datetime,
    version_number: int,
) -> str:
    if version_prefix not in CANONICAL_VERSION_PREFIXES:
        raise InvalidVersionIdentifierError("Unknown canonical version prefix.")
    if (
        not isinstance(version_number, int)
        or isinstance(version_number, bool)
        or version_number <= 0
    ):
        raise InvalidVersionIdentifierError("Version number must be positive.")
    local_date = publication_date_in_ecuador(published_at).isoformat()
    return f"{version_prefix}-{local_date}-v{version_number}"


def validate_version_identifier(
    version_identifier: str,
    *,
    expected_prefix: str | None = None,
    expected_version_number: int | None = None,
    published_at: datetime | None = None,
) -> tuple[str, date, int]:
    match = _VERSION_IDENTIFIER.fullmatch(version_identifier or "")
    if match is None or "XX" in version_identifier.upper():
        raise InvalidVersionIdentifierError("Malformed legal version identifier.")
    prefix = match.group("prefix")
    if prefix not in CANONICAL_VERSION_PREFIXES:
        raise InvalidVersionIdentifierError("Unknown legal version prefix.")
    try:
        publication_date = date.fromisoformat(match.group("publication_date"))
    except ValueError as exc:
        raise InvalidVersionIdentifierError(
            "Invalid publication date in legal version identifier."
        ) from exc
    version_number = int(match.group("version_number"))
    if expected_prefix is not None and prefix != expected_prefix:
        raise InvalidVersionIdentifierError(
            "Legal version identifier does not match its document prefix."
        )
    if (
        expected_version_number is not None
        and version_number != expected_version_number
    ):
        raise InvalidVersionIdentifierError(
            "Legal version identifier does not match its version number."
        )
    if (
        published_at is not None
        and publication_date != publication_date_in_ecuador(published_at)
    ):
        raise InvalidVersionIdentifierError(
            "Legal version identifier date does not match Ecuador publication date."
        )
    return prefix, publication_date, version_number


def _advisory_lock_id(namespace: str, *values: object) -> int:
    key = ":".join((namespace, *(str(value) for value in values)))
    return int.from_bytes(
        hashlib.sha256(key.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _lock_logical_document(session: Session, family: str, slug: str) -> None:
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                _advisory_lock_id("legal-version", family, slug)
            )
        )
    )


def create_legal_document_version(
    session: Session,
    *,
    family: str,
    slug: str,
    content: str,
    published_at: datetime,
    effective_at: datetime | None = None,
    source_revision: str | None = None,
) -> LegalDocumentVersion:
    document = document_by_path(family, slug, published_only=False)
    if document is None or document.template_name is None:
        raise UnknownLegalDocumentError(
            f"No versionable legal document is registered for {family}/{slug}."
        )
    version_prefix = version_prefix_for(family, slug)
    acceptance_mode = acceptance_mode_for(family, slug)
    published_at = _aware(published_at, "published_at")
    effective_at = _aware(
        effective_at if effective_at is not None else published_at,
        "effective_at",
    )
    if effective_at < published_at:
        raise LegalVersioningError(
            "effective_at cannot precede published_at."
        )
    normalized_revision = source_revision.strip() if source_revision else None
    if normalized_revision is not None and len(normalized_revision) > 64:
        raise LegalVersioningError("source_revision is too long.")
    snapshot, digest = canonical_snapshot_and_sha256(content)

    _lock_logical_document(session, family, slug)
    previous = session.scalar(
        select(LegalDocumentVersion)
        .where(
            LegalDocumentVersion.family == family,
            LegalDocumentVersion.slug == slug,
        )
        .order_by(LegalDocumentVersion.version_number.desc())
        .limit(1)
    )
    if previous is not None:
        if published_at < previous.published_at:
            raise LegalVersioningError(
                "published_at cannot precede the previous legal version."
            )
        if effective_at < previous.effective_at:
            raise LegalVersioningError(
                "effective_at cannot precede the previous legal version."
            )
    version_number = (previous.version_number if previous is not None else 0) + 1
    identifier = build_version_identifier(
        version_prefix, published_at, version_number
    )
    version = LegalDocumentVersion(
        family=family,
        slug=slug,
        version_prefix=version_prefix,
        version_identifier=identifier,
        version_number=version_number,
        title_snapshot=document.title,
        template_name_snapshot=document.template_name,
        content_snapshot=snapshot,
        content_sha256=digest,
        acceptance_mode=acceptance_mode,
        published_at=published_at,
        effective_at=effective_at,
        source_revision=normalized_revision,
    )
    session.add(version)
    session.flush()
    return version


def current_legal_version(
    session: Session,
    family: str,
    slug: str,
    at: datetime | None = None,
) -> LegalDocumentVersion | None:
    instant = _aware(at or datetime.now(timezone.utc), "at")
    version = session.scalar(
        select(LegalDocumentVersion)
        .where(
            LegalDocumentVersion.family == family,
            LegalDocumentVersion.slug == slug,
            LegalDocumentVersion.published_at <= instant,
            LegalDocumentVersion.effective_at <= instant,
        )
        .order_by(
            LegalDocumentVersion.effective_at.desc(),
            LegalDocumentVersion.published_at.desc(),
            LegalDocumentVersion.version_number.desc(),
            LegalDocumentVersion.id.desc(),
        )
        .limit(1)
    )
    if version is not None:
        _validate_version_evidence(version)
    return version


def legal_version_history(
    session: Session, family: str, slug: str
) -> tuple[LegalDocumentVersion, ...]:
    versions = tuple(
        session.scalars(
            select(LegalDocumentVersion)
            .where(
                LegalDocumentVersion.family == family,
                LegalDocumentVersion.slug == slug,
            )
            .order_by(
                LegalDocumentVersion.version_number.asc(),
                LegalDocumentVersion.published_at.asc(),
                LegalDocumentVersion.id.asc(),
            )
        )
    )
    for version in versions:
        _validate_version_evidence(version)
    return versions


def legal_version_by_identifier(
    session: Session, version_identifier: str
) -> LegalDocumentVersion | None:
    version = session.scalar(
        select(LegalDocumentVersion).where(
            LegalDocumentVersion.version_identifier == version_identifier
        )
    )
    if version is not None:
        _validate_version_evidence(version)
    return version


def _coerce_action(
    action: LegalAcceptanceAction | str,
) -> LegalAcceptanceAction:
    try:
        return LegalAcceptanceAction(action)
    except ValueError as exc:
        raise InvalidLegalAcceptanceError("Unsupported legal evidence action.") from exc


def _coerce_source(
    source: LegalAcceptanceSource | str,
) -> LegalAcceptanceSource:
    try:
        return LegalAcceptanceSource(source)
    except ValueError as exc:
        raise InvalidLegalAcceptanceError("Unsupported legal evidence source.") from exc


def _normalize_ip(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return ipaddress.ip_address(normalized).compressed
    except ValueError as exc:
        raise InvalidLegalAcceptanceError("Invalid IP address.") from exc


def _normalize_user_agent(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:500] or None


def _validate_version_evidence(version: LegalDocumentVersion) -> None:
    expected_prefix = version_prefix_for(version.family, version.slug)
    expected_mode = acceptance_mode_for(version.family, version.slug)
    validate_version_identifier(
        version.version_identifier,
        expected_prefix=expected_prefix,
        expected_version_number=version.version_number,
        published_at=version.published_at,
    )
    if version.acceptance_mode != expected_mode:
        raise InvalidLegalAcceptanceError(
            "Legal version acceptance mode does not match the approved mapping."
        )
    if not _SHA256.fullmatch(version.content_sha256 or ""):
        raise InvalidLegalAcceptanceError("Legal version hash is malformed.")
    if content_sha256(version.content_snapshot) != version.content_sha256:
        raise InvalidLegalAcceptanceError(
            "Legal version content does not match its integrity digest."
        )


def _validate_user_legal_evidence(
    evidence: UserLegalAcceptance,
    version: LegalDocumentVersion,
    *,
    expected_user_id: uuid.UUID | None = None,
) -> None:
    _validate_version_evidence(version)
    if evidence.legal_document_version_id != version.id:
        raise InvalidLegalAcceptanceError(
            "Legal evidence does not match its referenced version."
        )
    if expected_user_id is not None and evidence.user_id != expected_user_id:
        raise InvalidLegalAcceptanceError(
            "Legal evidence does not match its referenced user."
        )
    expected_actions = {
        LegalAcceptanceMode.ACCEPTED: LegalAcceptanceAction.ACCEPTED,
        LegalAcceptanceMode.ACKNOWLEDGED: LegalAcceptanceAction.ACKNOWLEDGED,
    }
    expected_action = expected_actions.get(version.acceptance_mode)
    if expected_action is None or evidence.action != expected_action:
        raise InvalidLegalAcceptanceError(
            "Legal evidence action does not match the version acceptance mode."
        )
    if evidence.version_identifier_snapshot != version.version_identifier:
        raise InvalidLegalAcceptanceError(
            "Legal evidence version identifier snapshot is inconsistent."
        )
    if evidence.content_sha256_snapshot != version.content_sha256:
        raise InvalidLegalAcceptanceError(
            "Legal evidence content hash snapshot is inconsistent."
        )
    occurred_at = evidence.occurred_at
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise InvalidLegalAcceptanceError(
            "Legal evidence occurrence time must include a time zone."
        )
    if occurred_at < version.published_at:
        raise InvalidLegalAcceptanceError(
            "Legal evidence cannot precede version publication."
        )
    if occurred_at < version.effective_at:
        raise InvalidLegalAcceptanceError(
            "Legal evidence cannot precede version effectiveness."
        )


def record_user_legal_evidence(
    session: Session,
    *,
    user_id: uuid.UUID,
    legal_document_version_id: uuid.UUID,
    action: LegalAcceptanceAction | str,
    source: LegalAcceptanceSource | str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> UserLegalAcceptance:
    occurred_at = datetime.now(timezone.utc)
    version = session.get(LegalDocumentVersion, legal_document_version_id)
    if version is None:
        raise InvalidLegalAcceptanceError("Legal version does not exist.")
    _validate_version_evidence(version)
    required_mode = USER_LEGAL_REQUIREMENTS.get((version.family, version.slug))
    normalized_action = _coerce_action(action)
    if required_mode is None or version.acceptance_mode == LegalAcceptanceMode.NONE:
        raise InvalidLegalAcceptanceError(
            "This document is not eligible for generic user legal evidence."
        )
    if normalized_action.value != required_mode.value:
        raise InvalidLegalAcceptanceError(
            "Evidence action does not match the legal version acceptance mode."
        )
    current = current_legal_version(
        session,
        version.family,
        version.slug,
        occurred_at,
    )
    if current is None or current.id != version.id:
        raise InvalidLegalAcceptanceError(
            "Legal evidence may only be recorded for the current published and "
            "effective version."
        )
    normalized_source = _coerce_source(source)
    normalized_ip = _normalize_ip(ip_address)
    normalized_user_agent = _normalize_user_agent(user_agent)

    session.execute(
        select(
            func.pg_advisory_xact_lock(
                _advisory_lock_id(
                    "user-legal-evidence", user_id, legal_document_version_id
                )
            )
        )
    )
    existing = session.scalar(
        select(UserLegalAcceptance).where(
            UserLegalAcceptance.user_id == user_id,
            UserLegalAcceptance.legal_document_version_id
            == legal_document_version_id,
        )
    )
    if existing is not None:
        _validate_user_legal_evidence(
            existing,
            version,
            expected_user_id=user_id,
        )
        return existing

    evidence = UserLegalAcceptance(
        user_id=user_id,
        legal_document_version_id=version.id,
        action=normalized_action,
        occurred_at=occurred_at,
        source=normalized_source,
        ip_address=normalized_ip,
        user_agent=normalized_user_agent,
        version_identifier_snapshot=version.version_identifier,
        content_sha256_snapshot=version.content_sha256,
    )
    session.add(evidence)
    session.flush()
    return evidence


def has_user_legal_evidence(
    session: Session,
    user_id: uuid.UUID,
    legal_document_version_id: uuid.UUID,
) -> bool:
    evidence = session.scalar(
        select(UserLegalAcceptance)
        .where(
            UserLegalAcceptance.user_id == user_id,
            UserLegalAcceptance.legal_document_version_id
            == legal_document_version_id,
        )
        .limit(1)
    )
    if evidence is None:
        return False
    version = session.get(
        LegalDocumentVersion, evidence.legal_document_version_id
    )
    if version is None:
        raise InvalidLegalAcceptanceError(
            "Legal evidence references a missing version."
        )
    _validate_user_legal_evidence(
        evidence,
        version,
        expected_user_id=user_id,
    )
    return True


def current_required_user_versions(
    session: Session,
    at: datetime | None = None,
) -> tuple[LegalDocumentVersion, ...]:
    instant = _aware(at or datetime.now(timezone.utc), "at")
    current = []
    for family, slug in USER_LEGAL_REQUIREMENTS:
        version = current_legal_version(session, family, slug, instant)
        if version is not None:
            current.append(version)
    return tuple(current)


def missing_user_legal_requirements(
    session: Session,
    user_id: uuid.UUID,
    at: datetime | None = None,
) -> tuple[LegalDocumentVersion, ...]:
    required = current_required_user_versions(session, at)
    if not required:
        return ()
    evidence_by_version_id = {
        evidence.legal_document_version_id: evidence
        for evidence in session.scalars(
            select(UserLegalAcceptance).where(
                UserLegalAcceptance.user_id == user_id,
                UserLegalAcceptance.legal_document_version_id.in_(
                    version.id for version in required
                ),
            )
        )
    }
    for version in required:
        evidence = evidence_by_version_id.get(version.id)
        if evidence is not None:
            _validate_user_legal_evidence(
                evidence,
                version,
                expected_user_id=user_id,
            )
    return tuple(
        version for version in required if version.id not in evidence_by_version_id
    )
