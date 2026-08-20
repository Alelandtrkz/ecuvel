from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Store,
    StoreContractAcceptance,
    StoreOnboarding,
    StoreOnboardingDocument,
    StoreVerificationReview,
    User,
)
from app.models.enums import (
    StoreOnboardingDocumentStatus,
    StoreOnboardingStatus,
    StoreStatus,
    StoreVerificationDecision,
)
from app.services.partner_onboarding import (
    ADMIN_APPROVAL_CHECKS,
    CORRECTION_REASON_LABELS,
    DOCUMENT_TYPES,
    PartnerOnboardingValidationError,
    review_onboarding,
)


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
TAB_STATUSES = {
    "pending": (StoreOnboardingStatus.SUBMITTED,),
    "corrections": (StoreOnboardingStatus.CORRECTIONS_REQUESTED,),
    "contract": (
        StoreOnboardingStatus.APPROVED,
        StoreOnboardingStatus.CONTRACT_PENDING,
    ),
    "active": (StoreOnboardingStatus.COMPLETED,),
    "all": (
        StoreOnboardingStatus.SUBMITTED,
        StoreOnboardingStatus.CORRECTIONS_REQUESTED,
        StoreOnboardingStatus.APPROVED,
        StoreOnboardingStatus.CONTRACT_PENDING,
        StoreOnboardingStatus.COMPLETED,
        StoreOnboardingStatus.REJECTED,
    ),
}
STATUS_LABELS = {
    StoreOnboardingStatus.SUBMITTED: "Pendiente de revisión",
    StoreOnboardingStatus.CORRECTIONS_REQUESTED: "Correcciones solicitadas",
    StoreOnboardingStatus.APPROVED: "Verificación aprobada",
    StoreOnboardingStatus.CONTRACT_PENDING: "Firma pendiente",
    StoreOnboardingStatus.COMPLETED: "Activa",
    StoreOnboardingStatus.REJECTED: "Solicitud cerrada",
}
DOCUMENT_STATUS_LABELS = {
    StoreOnboardingDocumentStatus.PENDING_REVIEW: "Pendiente de revisión",
    StoreOnboardingDocumentStatus.APPROVED: "Aprobado",
    StoreOnboardingDocumentStatus.REJECTED: "Requiere reemplazo",
}
CORRECTION_REASONS = {
    "DOCUMENT_UNREADABLE": ("DOCUMENT", None, 4, "El documento no es legible. Sube un archivo donde todos los datos se vean claramente."),
    "DOCUMENT_INCOMPLETE": ("DOCUMENT", None, 4, "El documento está incompleto. Sube todas las páginas o caras necesarias."),
    "DOCUMENT_INCORRECT": ("DOCUMENT", None, 4, "El documento no corresponde con la información de la solicitud."),
    "LEGAL_ID_MISMATCH": ("FIELD", "legal_id_number", 1, "La identificación o RUC no coincide. Verifica y corrige el dato."),
    "ADDRESS_INCOMPLETE": ("FIELD", "address", 2, "La dirección está incompleta. Añade una referencia suficientemente precisa."),
    "CONTACT_INCORRECT": ("FIELD", "whatsapp_or_nickname", 3, "Los datos de contacto no son válidos. Verifícalos."),
    "BANK_DATA_INCORRECT": ("FIELD", "bank_account_number", 5, "Los datos bancarios no coinciden. Verifica el número de cuenta."),
    "BANK_OWNER_MISMATCH": ("FIELD", "bank_account_owner", 5, "La información del titular de la cuenta no coincide."),
    "OTHER": ("FIELD", "store_name", 1, "Revisa la observación del equipo ECUVEL y corrige la información indicada."),
}
APPROVAL_CHECK_LABELS = {
    "identity": "Datos de identificación revisados",
    "address_contact": "Dirección y contacto revisados",
    "banking": "Información bancaria revisada",
    "documents": "Documentación revisada",
    "no_pending_corrections": "Sin correcciones pendientes",
}
MAX_CORRECTION_ISSUES = 20
DOCUMENT_CORRECTION_REASON_LABELS = {
    code: CORRECTION_REASON_LABELS[code]
    for code, spec in CORRECTION_REASONS.items()
    if spec[0] == "DOCUMENT"
}
FIELD_CORRECTION_REASON_LABELS = {
    code: CORRECTION_REASON_LABELS[code]
    for code, spec in CORRECTION_REASONS.items()
    if spec[0] == "FIELD"
}


@dataclass(frozen=True, slots=True)
class AdminStoreRow:
    onboarding: StoreOnboarding
    document_count: int
    approved_documents: int
    pending_documents: int
    replacement_documents: int
    status_label: str


@dataclass(frozen=True, slots=True)
class AdminStoresPage:
    rows: tuple[AdminStoreRow, ...]
    tab: str
    query: str
    province: str
    city: str
    document_state: str
    date_from: str
    date_to: str
    page: int
    pages: int
    total: int
    counts: dict[str, int]
    requests_today: int
    average_first_review_minutes: int | None
    provinces: tuple[str, ...]
    cities: tuple[str, ...]


def _options():
    return (
        selectinload(StoreOnboarding.user),
        selectinload(StoreOnboarding.store),
        selectinload(StoreOnboarding.documents),
        selectinload(StoreOnboarding.reviews).selectinload(StoreVerificationReview.reviewer),
        selectinload(StoreOnboarding.contract_acceptance),
    )


def _row(onboarding: StoreOnboarding) -> AdminStoreRow:
    statuses = [item.status for item in onboarding.documents]
    return AdminStoreRow(
        onboarding=onboarding,
        document_count=len(statuses),
        approved_documents=statuses.count(StoreOnboardingDocumentStatus.APPROVED),
        pending_documents=statuses.count(StoreOnboardingDocumentStatus.PENDING_REVIEW),
        replacement_documents=statuses.count(StoreOnboardingDocumentStatus.REJECTED),
        status_label=STATUS_LABELS.get(onboarding.status, onboarding.status.value),
    )


def _parse_date(value: str, *, end: bool) -> datetime | None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    local = datetime.combine(parsed, time.max if end else time.min, tzinfo=ECUADOR_TZ)
    return local.astimezone(timezone.utc)


def get_admin_stores_page(
    session: Session,
    *,
    tab: str,
    query: str,
    province: str,
    city: str,
    document_state: str,
    date_from: str,
    date_to: str,
    page: int,
    per_page: int = 20,
) -> AdminStoresPage:
    tab = tab if tab in TAB_STATUSES else "pending"
    query = " ".join((query or "").strip().split())[:160]
    province = " ".join((province or "").strip().split())[:100]
    city = " ".join((city or "").strip().split())[:100]
    document_state = document_state if document_state in {"pending", "approved", "replacement"} else ""
    date_from = (date_from or "")[:10]
    date_to = (date_to or "")[:10]
    page = max(1, page)

    raw_counts = dict(session.execute(
        select(StoreOnboarding.status, func.count(StoreOnboarding.id))
        .where(StoreOnboarding.status.in_(TAB_STATUSES["all"]))
        .group_by(StoreOnboarding.status)
    ).all())
    counts = {
        key: sum(int(raw_counts.get(status, 0)) for status in statuses)
        for key, statuses in TAB_STATUSES.items()
    }
    counts["active"] = int(session.scalar(
        select(func.count(StoreOnboarding.id))
        .join(Store, Store.id == StoreOnboarding.store_id)
        .where(
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
        )
    ) or 0)

    now_local = datetime.now(ECUADOR_TZ)
    day_start = datetime.combine(now_local.date(), time.min, tzinfo=ECUADOR_TZ).astimezone(timezone.utc)
    day_end = datetime.combine(now_local.date(), time.max, tzinfo=ECUADOR_TZ).astimezone(timezone.utc)
    requests_today = int(session.scalar(
        select(func.count(StoreOnboarding.id)).where(
            StoreOnboarding.status == StoreOnboardingStatus.SUBMITTED,
            StoreOnboarding.submitted_at.between(day_start, day_end),
        )
    ) or 0)

    first_submissions = (
        select(
            StoreVerificationReview.onboarding_id.label("onboarding_id"),
            func.min(StoreVerificationReview.created_at).label("submitted_at"),
        )
        .where(StoreVerificationReview.decision == StoreVerificationDecision.PENDING)
        .group_by(StoreVerificationReview.onboarding_id)
        .subquery()
    )
    first_reviews = (
        select(
            StoreVerificationReview.onboarding_id.label("onboarding_id"),
            func.min(StoreVerificationReview.created_at).label("reviewed_at"),
        )
        .where(StoreVerificationReview.decision != StoreVerificationDecision.PENDING)
        .group_by(StoreVerificationReview.onboarding_id)
        .subquery()
    )
    average_seconds = session.scalar(
        select(func.avg(func.extract("epoch", first_reviews.c.reviewed_at - first_submissions.c.submitted_at)))
        .select_from(first_submissions)
        .join(first_reviews, first_reviews.c.onboarding_id == first_submissions.c.onboarding_id)
        .where(first_reviews.c.reviewed_at >= first_submissions.c.submitted_at)
    )
    average_minutes = round(float(average_seconds) / 60) if average_seconds is not None else None

    conditions = [StoreOnboarding.status.in_(TAB_STATUSES[tab])]
    if tab == "active":
        conditions.extend((Store.status == StoreStatus.ACTIVE, Store.is_verified.is_(True)))
    if query:
        term = f"%{query}%"
        conditions.append(or_(
            StoreOnboarding.store_name.ilike(term),
            StoreOnboarding.legal_id_number.ilike(term),
            StoreOnboarding.province.ilike(term),
            StoreOnboarding.city.ilike(term),
            User.email.ilike(term),
            Store.public_code.ilike(term),
        ))
    if province:
        conditions.append(StoreOnboarding.province == province)
    if city:
        conditions.append(StoreOnboarding.city == city)
    start = _parse_date(date_from, end=False)
    end = _parse_date(date_to, end=True)
    if start:
        conditions.append(StoreOnboarding.submitted_at >= start)
    if end:
        conditions.append(StoreOnboarding.submitted_at <= end)
    if document_state:
        wanted = {
            "pending": StoreOnboardingDocumentStatus.PENDING_REVIEW,
            "approved": StoreOnboardingDocumentStatus.APPROVED,
            "replacement": StoreOnboardingDocumentStatus.REJECTED,
        }[document_state]
        conditions.append(StoreOnboarding.documents.any(StoreOnboardingDocument.status == wanted))

    base = (
        select(StoreOnboarding)
        .outerjoin(User, User.id == StoreOnboarding.user_id)
        .outerjoin(Store, Store.id == StoreOnboarding.store_id)
        .where(*conditions)
    )
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    rows = session.scalars(
        base.options(*_options())
        .order_by(StoreOnboarding.submitted_at.desc().nullslast(), StoreOnboarding.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    provinces = tuple(value for value in session.scalars(
        select(StoreOnboarding.province).where(StoreOnboarding.province.is_not(None)).distinct().order_by(StoreOnboarding.province)
    ) if value)
    cities = tuple(value for value in session.scalars(
        select(StoreOnboarding.city).where(StoreOnboarding.city.is_not(None)).distinct().order_by(StoreOnboarding.city)
    ) if value)
    return AdminStoresPage(
        rows=tuple(_row(item) for item in rows),
        tab=tab,
        query=query,
        province=province,
        city=city,
        document_state=document_state,
        date_from=date_from,
        date_to=date_to,
        page=page,
        pages=pages,
        total=total,
        counts=counts,
        requests_today=requests_today,
        average_first_review_minutes=average_minutes,
        provinces=provinces,
        cities=cities,
    )


def get_admin_store_review(session: Session, onboarding_id: uuid.UUID) -> StoreOnboarding | None:
    return session.scalar(
        select(StoreOnboarding).options(*_options()).where(StoreOnboarding.id == onboarding_id)
    )


def build_correction_issues(onboarding: StoreOnboarding, form) -> list[dict]:
    issues: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    current_documents = {
        document.id: document
        for document in onboarding.documents
        if document.status != StoreOnboardingDocumentStatus.REJECTED
    }
    general_comment = _form_text(form.get("comments"), limit=1000, label="observación general")

    document_targets = form.getlist("document_issue_target")
    document_reasons = form.getlist("document_issue_reason")
    document_messages = form.getlist("document_issue_message")
    if len({len(document_targets), len(document_reasons), len(document_messages)}) > 1:
        raise PartnerOnboardingValidationError(
            "Las correcciones documentales están incompletas. Actualiza la página e inténtalo nuevamente."
        )

    document_rows = list(zip(document_targets, document_reasons, document_messages))
    if len(document_rows) > MAX_CORRECTION_ISSUES:
        raise PartnerOnboardingValidationError(
            f"Puedes enviar un máximo de {MAX_CORRECTION_ISSUES} correcciones por revisión."
        )
    for raw_target, raw_reason, raw_message in document_rows:
        target = str(raw_target or "").strip()
        reason_code = str(raw_reason or "").strip().upper()
        message_value = str(raw_message or "").strip()
        if not target and not reason_code and not message_value:
            continue
        _append_document_issue(
            issues,
            seen,
            current_documents=current_documents,
            target=target,
            reason_code=reason_code,
            raw_message=message_value,
        )

    field_codes = list(form.getlist("field_issue_code"))
    legacy_codes = list(form.getlist("issue_code"))
    legacy_document_id = form.get("document_id")
    for raw_code in legacy_codes:
        reason_code = str(raw_code or "").strip().upper()
        spec = CORRECTION_REASONS.get(reason_code)
        if spec is None:
            raise PartnerOnboardingValidationError("Uno de los motivos de corrección no es válido.")
        if spec[0] == "DOCUMENT":
            _append_document_issue(
                issues,
                seen,
                current_documents=current_documents,
                target=str(legacy_document_id or "").strip(),
                reason_code=reason_code,
                raw_message=general_comment,
            )
        else:
            field_codes.append(reason_code)

    for reason_code in dict.fromkeys(str(code or "").strip().upper() for code in field_codes):
        if not reason_code:
            continue
        spec = CORRECTION_REASONS.get(reason_code)
        if spec is None or spec[0] != "FIELD":
            raise PartnerOnboardingValidationError("Uno de los motivos de corrección no es válido.")
        _target_type, field, step, default_message = spec
        identity = ("FIELD", str(field), reason_code)
        if identity in seen:
            raise PartnerOnboardingValidationError("No repitas la misma corrección.")
        seen.add(identity)
        issues.append({
            "target_type": "FIELD",
            "field": field,
            "step": step,
            "reason_code": reason_code,
            "message": general_comment or default_message,
            "previous_value": getattr(onboarding, field, None),
        })

    if len(issues) > MAX_CORRECTION_ISSUES:
        raise PartnerOnboardingValidationError(
            f"Puedes enviar un máximo de {MAX_CORRECTION_ISSUES} correcciones por revisión."
        )
    return issues


def _append_document_issue(
    issues: list[dict],
    seen: set[tuple[str, str, str]],
    *,
    current_documents: dict[uuid.UUID, StoreOnboardingDocument],
    target: str,
    reason_code: str,
    raw_message: str,
) -> None:
    if not target or not reason_code:
        raise PartnerOnboardingValidationError(
            "Completa el documento y el motivo de cada corrección documental."
        )
    spec = CORRECTION_REASONS.get(reason_code)
    if spec is None or spec[0] != "DOCUMENT":
        raise PartnerOnboardingValidationError("Uno de los motivos documentales no es válido.")
    try:
        document_id = uuid.UUID(target)
    except (TypeError, ValueError) as exc:
        raise PartnerOnboardingValidationError("Uno de los documentos seleccionados no es válido.") from exc
    document = current_documents.get(document_id)
    if document is None:
        raise PartnerOnboardingValidationError(
            "Uno de los documentos seleccionados no pertenece a esta solicitud o ya no está vigente."
        )
    identity = ("DOCUMENT", str(document.id), reason_code)
    if identity in seen:
        raise PartnerOnboardingValidationError(
            "No repitas el mismo motivo para un documento."
        )
    seen.add(identity)
    message = _form_text(raw_message, limit=500, label="observación documental") or spec[3]
    issues.append({
        "target_type": "DOCUMENT",
        "target_id": str(document.id),
        "reason_code": reason_code,
        "message": message,
    })


def _form_text(value, *, limit: int, label: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if len(cleaned) > limit:
        raise PartnerOnboardingValidationError(
            f"La {label} no puede superar {limit} caracteres."
        )
    return cleaned


def approve_store_verification(
    session: Session,
    *,
    onboarding_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
    checklist_values: list[str],
    expected_updated_at: str | None,
    comments: str | None,
) -> StoreOnboarding:
    checklist = {key: key in checklist_values for key in ADMIN_APPROVAL_CHECKS}
    return review_onboarding(
        session=session,
        onboarding_id=onboarding_id,
        reviewer_user_id=reviewer_user_id,
        decision="approve",
        comments=comments,
        checklist=checklist,
        expected_updated_at=expected_updated_at,
        require_checklist=True,
    )


def request_store_corrections(
    session: Session,
    *,
    onboarding_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
    form,
) -> StoreOnboarding:
    onboarding = session.get(StoreOnboarding, onboarding_id)
    if onboarding is None:
        raise PartnerOnboardingValidationError("No se encontró la solicitud.")
    issues = build_correction_issues(onboarding, form)
    return review_onboarding(
        session=session,
        onboarding_id=onboarding_id,
        reviewer_user_id=reviewer_user_id,
        decision="corrections",
        comments=_form_text(form.get("comments"), limit=1000, label="observación general"),
        issues=issues,
        expected_updated_at=form.get("expected_updated_at"),
    )


def contract_status_label(onboarding: StoreOnboarding) -> str:
    if onboarding.status == StoreOnboardingStatus.COMPLETED:
        return "Contrato aceptado"
    if onboarding.status == StoreOnboardingStatus.CONTRACT_PENDING:
        return "Código enviado / firma pendiente"
    if onboarding.status == StoreOnboardingStatus.APPROVED:
        return "Pendiente de aceptación por el seller"
    return "Pendiente de aprobación de verificación"


def document_type_label(value: str) -> str:
    return DOCUMENT_TYPES.get(value, value.replace("_", " ").title())


def document_status_label(value: StoreOnboardingDocumentStatus) -> str:
    return DOCUMENT_STATUS_LABELS.get(value, value.value)
