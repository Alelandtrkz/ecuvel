from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ProductReview,
    ProductReviewImage,
    ProductReviewRevision,
    ReviewModerationAssessment,
    ReviewModerationDecision,
    ReviewModerationSignal,
    ReviewModerationTerm,
    ReviewNotificationOutbox,
)
from app.models.enums import (
    ProductReviewStatus,
    ReviewModerationDecisionAction,
    ReviewModerationDecisionSource,
    ReviewModerationMatchMode,
    ReviewModerationOutcome,
    ReviewModerationProcessingStatus,
    ReviewModerationRisk,
    ReviewModerationSeverity,
    ReviewNotificationStatus,
)


ENGINE_NAME = "ECUVEL_REVIEW_RULES"
ENGINE_VERSION = "1.0.0"
LEXICON_RESOURCE = Path(__file__).resolve().parents[1] / "data" / "review_moderation_es_v1.json"

REJECTION_REASON_LABELS = {
    "OFFENSIVE_LANGUAGE": "Lenguaje ofensivo o insultos dirigidos.",
    "HARASSMENT": "Acoso o ataque personal.",
    "HATE_OR_DISCRIMINATION": "Contenido discriminatorio o de odio.",
    "THREAT": "Amenazas o intimidación.",
    "SPAM": "Publicidad, enlaces o contenido repetitivo no relacionado.",
    "PERSONAL_DATA": "La reseña expone datos personales.",
    "SEXUAL_CONTENT": "Contenido sexual no permitido.",
    "VIOLENT_CONTENT": "Contenido violento no permitido.",
    "UNRELATED_CONTENT": "El comentario no está relacionado con la compra.",
    "OTHER": "La reseña no cumple las políticas de publicación.",
}


class ReviewModerationError(Exception):
    pass


class ReviewModerationConflictError(ReviewModerationError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    created: int
    updated: int
    unchanged: int
    version: str


@dataclass(frozen=True, slots=True)
class AssessmentResult:
    assessment: ReviewModerationAssessment
    auto_published: bool


@dataclass(frozen=True, slots=True)
class _Signal:
    surface: str
    category: str
    severity: str
    value: str | None = None
    start: int | None = None
    end: int | None = None
    metadata: dict | None = None
    matched_term_id: uuid.UUID | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def review_content_hash(rating: int, body: str) -> str:
    return hashlib.sha256(f"{rating}\0{body}".encode("utf-8")).hexdigest()


def normalize_review_text(value: str) -> str:
    compatibility = unicodedata.normalize("NFKC", value or "").casefold()
    decomposed = unicodedata.normalize("NFKD", compatibility)
    accentless = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(accentless.split())


def _resource_payload(path: Path = LEXICON_RESOURCE) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("terms"), list):
        raise ReviewModerationError("El recurso versionado de moderación es inválido.")
    return payload


def bootstrap_review_moderation_terms(
    session: Session, *, path: Path = LEXICON_RESOURCE
) -> BootstrapResult:
    payload = _resource_payload(path)
    created = updated = unchanged = 0
    for item in payload["terms"]:
        stable_key = str(item.get("stable_key") or "").strip()
        pattern = str(item.get("pattern") or "").strip()
        match_mode = str(item.get("match_mode") or "").strip().upper()
        category = str(item.get("category_code") or "").strip().upper()
        severity = str(item.get("severity") or "").strip().upper()
        if (
            not stable_key or not pattern
            or match_mode not in {mode.value for mode in ReviewModerationMatchMode}
            or severity not in {level.value for level in ReviewModerationSeverity}
            or not category
        ):
            raise ReviewModerationError(f"Término inválido: {stable_key or '<sin clave>'}.")
        values = {
            "language_code": str(item.get("language") or payload.get("language") or "es")[:10],
            "pattern": pattern[:160],
            "normalized_pattern": normalize_review_text(pattern)[:160],
            "match_mode": match_mode,
            "category_code": category[:50],
            "severity": severity,
            "is_active": bool(item.get("active", True)),
            "notes": (str(item.get("notes") or "").strip()[:500] or None),
        }
        term = session.scalar(
            select(ReviewModerationTerm).where(ReviewModerationTerm.stable_key == stable_key)
        )
        if term is None:
            session.add(ReviewModerationTerm(stable_key=stable_key[:100], **values))
            created += 1
        elif any(getattr(term, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(term, key, value)
            updated += 1
        else:
            unchanged += 1
    session.flush()
    return BootstrapResult(created, updated, unchanged, str(payload.get("version") or "unknown"))


def _lexicon_hash(terms: list[ReviewModerationTerm]) -> str:
    rows = [
        f"{term.stable_key}|{term.normalized_pattern}|{term.match_mode}|"
        f"{term.category_code}|{term.severity}"
        for term in sorted(terms, key=lambda item: item.stable_key)
    ]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _term_signals(body: str, terms: list[ReviewModerationTerm]) -> list[_Signal]:
    signals: list[_Signal] = []
    for term in terms:
        pattern = term.normalized_pattern
        if not pattern:
            continue
        matcher = re.compile(rf"(?<!\w){re.escape(pattern)}(?!\w)", re.UNICODE)
        match = matcher.search(body)
        if match:
            signals.append(_Signal(
                "TEXT", term.category_code, term.severity, term.pattern,
                match.start(), match.end(), {"term_key": term.stable_key, "mode": term.match_mode},
                term.id,
            ))
    return signals


_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])", re.UNICODE)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>{}\[\]]+")
_PHONE_RE = re.compile(r"(?<![\w-])(?:\+?\d[\s().-]*){9,15}(?![\w-])")
_TOKEN_RE = re.compile(r"\b[\wáéíóúüñ]+\b", re.IGNORECASE | re.UNICODE)


def _structural_signals(original: str, normalized: str) -> list[_Signal]:
    signals: list[_Signal] = []
    email = _EMAIL_RE.search(original)
    if email:
        signals.append(_Signal("TEXT", "PERSONAL_DATA", "HIGH", "correo electrónico"))
    phone = _PHONE_RE.search(original)
    if phone and 9 <= len(re.sub(r"\D", "", phone.group(0))) <= 15:
        signals.append(_Signal("TEXT", "PERSONAL_DATA", "HIGH", "número telefónico"))
    urls = _URL_RE.findall(original)
    if urls:
        signals.append(_Signal("TEXT", "PERSONAL_DATA", "MEDIUM", "enlace"))
    if len(urls) > 1:
        signals.append(_Signal("TEXT", "SPAM", "HIGH", "múltiples enlaces"))
    tokens = [token for token in _TOKEN_RE.findall(normalized) if len(token) > 2]
    if tokens:
        max_repetition = max(tokens.count(token) for token in set(tokens))
        if max_repetition >= 6:
            signals.append(_Signal("TEXT", "SPAM", "MEDIUM", "repetición anormal"))
    return signals


def _risk_for(signals: list[_Signal]) -> str:
    order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    return max((signal.severity for signal in signals), key=lambda value: order[value], default="NONE")


def assess_review_revision(
    session: Session,
    *,
    review_id: uuid.UUID,
    revision_id: uuid.UUID,
    now: datetime | None = None,
) -> AssessmentResult:
    existing = session.scalar(
        select(ReviewModerationAssessment)
        .options(selectinload(ReviewModerationAssessment.signals))
        .where(ReviewModerationAssessment.revision_id == revision_id)
    )
    if existing is not None:
        existing_review = session.get(ProductReview, review_id)
        return AssessmentResult(
            existing,
            existing.outcome == ReviewModerationOutcome.PASS.value
            and existing.processing_status == ReviewModerationProcessingStatus.COMPLETED.value
            and existing_review is not None
            and existing_review.current_revision_id == revision_id
            and existing_review.status == ProductReviewStatus.PUBLISHED,
        )
    review = session.get(ProductReview, review_id, with_for_update=True)
    revision = session.get(ProductReviewRevision, revision_id)
    if review is None or revision is None or revision.review_id != review.id:
        raise ReviewModerationError("La revisión de reseña no existe.")
    if review.current_revision_id != revision.id:
        raise ReviewModerationConflictError("La reseña tiene una revisión más reciente.")
    terms = list(session.scalars(
        select(ReviewModerationTerm)
        .where(ReviewModerationTerm.is_active.is_(True))
        .order_by(ReviewModerationTerm.stable_key)
    ))
    lexicon_hash = _lexicon_hash(terms)
    assessment = ReviewModerationAssessment(
        review_id=review.id,
        revision_id=revision.id,
        processing_status=ReviewModerationProcessingStatus.PROCESSING.value,
        risk=ReviewModerationRisk.NONE.value,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        lexicon_hash=lexicon_hash,
        content_hash=revision.content_hash,
    )
    session.add(assessment)
    session.flush()
    effective_now = now or utcnow()
    try:
        if not terms:
            raise ReviewModerationError("No existe un léxico activo configurado.")
        normalized = normalize_review_text(revision.body)
        signals = _term_signals(normalized, terms) + _structural_signals(revision.body, normalized)
        image_count = session.scalar(
            select(func.count(ProductReviewImage.id)).where(
                ProductReviewImage.revision_id == revision.id
            )
        ) or 0
        if image_count:
            signals.append(_Signal(
                "IMAGE", "IMAGE_REQUIRES_REVIEW", "MEDIUM", None,
                metadata={"image_count": int(image_count)},
            ))
        for signal in signals:
            session.add(ReviewModerationSignal(
                assessment_id=assessment.id, review_id=review.id, revision_id=revision.id,
                surface=signal.surface, category_code=signal.category,
                severity=signal.severity, matched_value=signal.value,
                matched_term_id=signal.matched_term_id,
                start_offset=signal.start, end_offset=signal.end,
                metadata_json=signal.metadata,
            ))
        text_signals = [signal for signal in signals if signal.surface == "TEXT"]
        if text_signals:
            outcome = ReviewModerationOutcome.FLAG.value
        elif image_count:
            outcome = ReviewModerationOutcome.MANUAL_REQUIRED.value
        else:
            outcome = ReviewModerationOutcome.PASS.value
        assessment.normalized_body = normalized
        assessment.outcome = outcome
        assessment.risk = _risk_for(signals)
        assessment.processing_status = ReviewModerationProcessingStatus.COMPLETED.value
        assessment.completed_at = effective_now
        session.flush()
        auto_published = False
        if outcome == ReviewModerationOutcome.PASS.value:
            apply_review_moderation_decision(
                session,
                review_id=review.id,
                revision_id=revision.id,
                action=ReviewModerationDecisionAction.APPROVE.value,
                source=ReviewModerationDecisionSource.AUTOMATIC.value,
                actor_user_id=None,
                assessment_id=assessment.id,
                idempotency_key=f"auto:{review.id}:{revision.id}:approve",
                now=effective_now,
            )
            auto_published = True
        return AssessmentResult(assessment, auto_published)
    except Exception as exc:
        assessment.processing_status = ReviewModerationProcessingStatus.FAILED.value
        assessment.outcome = None
        assessment.risk = ReviewModerationRisk.NONE.value
        assessment.completed_at = effective_now
        assessment.error_code = type(exc).__name__[:80]
        assessment.error_message = "La prevalidación no pudo completarse de forma segura."
        review.status = ProductReviewStatus.PENDING_REVIEW
        review.moderation_source = None
        session.flush()
        return AssessmentResult(assessment, False)


def apply_review_moderation_decision(
    session: Session,
    *,
    review_id: uuid.UUID,
    revision_id: uuid.UUID,
    action: str,
    source: str,
    actor_user_id: uuid.UUID | None,
    idempotency_key: str,
    assessment_id: uuid.UUID | None = None,
    reason_code: str | None = None,
    public_reason: str | None = None,
    internal_notes: str | None = None,
    now: datetime | None = None,
) -> ReviewModerationDecision:
    clean_idempotency_key = (idempotency_key or "").strip()[:120]
    if not clean_idempotency_key:
        raise ReviewModerationError("Falta la clave idempotente de la decisión.")
    existing = session.scalar(
        select(ReviewModerationDecision).where(
            ReviewModerationDecision.idempotency_key == clean_idempotency_key
        )
    )
    if existing is not None:
        return existing
    if action not in {item.value for item in ReviewModerationDecisionAction}:
        raise ReviewModerationError("Decisión de moderación inválida.")
    if source not in {item.value for item in ReviewModerationDecisionSource}:
        raise ReviewModerationError("Fuente de moderación inválida.")
    if source == ReviewModerationDecisionSource.MANUAL.value and actor_user_id is None:
        raise ReviewModerationError("Una decisión manual requiere operador.")
    review = session.get(ProductReview, review_id, with_for_update=True)
    if review is None:
        raise ReviewModerationError("La reseña no existe.")
    if review.current_revision_id != revision_id:
        raise ReviewModerationConflictError("La reseña cambió mientras era revisada.")
    if review.status != ProductReviewStatus.PENDING_REVIEW:
        raise ReviewModerationConflictError("La reseña ya tiene una decisión vigente.")
    effective_now = now or utcnow()
    if assessment_id is None:
        assessment_id = session.scalar(
            select(ReviewModerationAssessment.id).where(
                ReviewModerationAssessment.revision_id == revision_id
            )
        )
    clean_reason_code = (reason_code or "").strip().upper() or None
    clean_public_reason = " ".join((public_reason or "").split())[:500] or None
    if action == ReviewModerationDecisionAction.REJECT.value:
        if clean_reason_code not in REJECTION_REASON_LABELS:
            raise ReviewModerationError("Selecciona un motivo de no publicación válido.")
        if clean_reason_code == "OTHER" and not clean_public_reason:
            raise ReviewModerationError("Explica el motivo público de no publicación.")
        clean_public_reason = clean_public_reason or REJECTION_REASON_LABELS[clean_reason_code]
        review.status = ProductReviewStatus.REJECTED
        review.public_rejection_reason = clean_public_reason
        review.rejection_reason_code = clean_reason_code
        review.published_at = None
    else:
        review.status = ProductReviewStatus.PUBLISHED
        review.published_at = effective_now
        review.public_rejection_reason = None
        review.rejection_reason_code = None
    review.moderated_by_user_id = actor_user_id
    review.moderated_at = effective_now
    review.moderation_notes = " ".join((internal_notes or "").split())[:1000] or None
    review.moderation_source = source
    decision = ReviewModerationDecision(
        review_id=review.id, revision_id=revision_id, assessment_id=assessment_id,
        action=action, source=source,
        reason_code=clean_reason_code, public_reason=clean_public_reason,
        internal_notes=review.moderation_notes, actor_user_id=actor_user_id,
        idempotency_key=clean_idempotency_key, created_at=effective_now,
    )
    session.add(decision)
    if action == ReviewModerationDecisionAction.REJECT.value:
        session.add(ReviewNotificationOutbox(
            review_id=review.id, revision_id=revision_id, user_id=review.user_id,
            event_type="REVIEW_REJECTED", status=ReviewNotificationStatus.PENDING.value,
            next_attempt_at=effective_now,
        ))
    session.flush()
    return decision
