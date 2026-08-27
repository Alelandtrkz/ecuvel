from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    Order,
    PaymentAttempt,
    PaymentProof,
    PaymentProofAnalysis,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
    PaymentProofStatus,
    PaymentStatus,
)
from app.services.admin_permissions import user_has_permission


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_QUERY_LENGTH = 120

PAYMENT_STATUS_LABELS_ES = {
    PaymentStatus.AWAITING_PROOF: "Esperando comprobante",
    PaymentStatus.PENDING_PROVIDER: "Esperando proveedor",
    PaymentStatus.PROCESSING: "En revisión",
    PaymentStatus.APPROVED: "Aprobado",
    PaymentStatus.REJECTED: "Rechazado",
    PaymentStatus.FAILED: "Fallido",
    PaymentStatus.CANCELLED: "Cancelado",
    PaymentStatus.EXPIRED: "Expirado",
}

PAYMENT_TAB_STATUSES = {
    "all": None,
    "approved": (PaymentStatus.APPROVED,),
    "rejected_failed": (PaymentStatus.REJECTED, PaymentStatus.FAILED),
    "expired": (PaymentStatus.EXPIRED,),
}
VALID_PAYMENT_TABS = frozenset((*PAYMENT_TAB_STATUSES, "manual_review"))
VALID_SORT_FIELDS = frozenset({"created_at", "amount", "status"})
VALID_SORT_DIRECTIONS = frozenset({"asc", "desc"})

_PAYMENT_CODE_RE = re.compile(r"^PMT-\d{1,12}$", re.IGNORECASE)
_ORDER_CODE_RE = re.compile(r"^(?:ECV|ORD)-[A-Z0-9-]+$", re.IGNORECASE)
_USER_CODE_RE = re.compile(
    r"^(?:U|BUY|USR|ADM|OPS)-[A-Z0-9-]+$", re.IGNORECASE
)
_SAFE_FINDING_CODES = frozenset({
    "AMOUNT_MATCH",
    "AMOUNT_MISMATCH",
    "AMOUNT_NOT_DETECTED",
    "AMOUNT_AMBIGUOUS",
    "DESTINATION_ACCOUNT_MATCH",
    "DESTINATION_ACCOUNT_MISMATCH",
    "DESTINATION_ACCOUNT_NOT_DETECTED",
    "ACCOUNT_VALIDATION_NOT_CONFIGURED",
    "DATE_PLAUSIBLE",
    "DATE_BEFORE_ORDER",
    "DATE_TOO_OLD",
    "DATE_IN_FUTURE",
    "DATE_NOT_DETECTED",
    "RECEIPT_UNIQUE",
    "POSSIBLE_DUPLICATE_RECEIPT",
    "RECEIPT_NOT_DETECTED",
    "BANK_RECOGNIZED",
    "BANK_NOT_RECOGNIZED",
    "QR_NOT_DETECTED",
    "MULTIPLE_QR_CODES",
    "LOW_OCR_CONFIDENCE",
    "OCR_TIMEOUT",
    "QR_OCR_CONSISTENT",
    "QR_OCR_AMOUNT_MISMATCH",
    "QR_OCR_ACCOUNT_MISMATCH",
    "QR_OCR_RECEIPT_MISMATCH",
    "QR_ONLY",
    "OCR_ONLY",
    "NO_STRUCTURED_DATA",
})


class AdminPaymentQueryError(ValueError):
    """A controlled validation error for Admin Payments read queries."""


class AdminPaymentNotFoundError(LookupError):
    """Raised when an Admin Payments detail identifier does not exist."""


@dataclass(frozen=True, slots=True)
class PaymentKpis:
    collected_today_amount: Decimal
    collected_today_count: int
    manual_review_amount: Decimal
    manual_review_count: int
    manual_review_flagged_count: int
    manual_review_oldest_submitted_at: datetime | None
    awaiting_proof_amount: Decimal
    awaiting_proof_count: int
    awaiting_proof_next_expiration_at: datetime | None
    failed_rejected_today_amount: Decimal
    failed_rejected_today_count: int


@dataclass(frozen=True, slots=True)
class AdminPaymentCustomer:
    public_code: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AdminPaymentRow:
    payment_public_code: str
    order_number: str
    customer: AdminPaymentCustomer
    method: str
    amount: Decimal
    currency: str
    status: str
    status_label_es: str
    verification_summary: str
    created_at: datetime
    relevant_at: datetime
    has_proof: bool
    analysis_outcome: str | None
    can_review: bool


@dataclass(frozen=True, slots=True)
class AdminPaymentListResult:
    items: tuple[AdminPaymentRow, ...]
    page: int
    per_page: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool


@dataclass(frozen=True, slots=True)
class PaymentFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class AdminPaymentProofDetail:
    proof_id: uuid.UUID
    original_filename: str
    media_type: str
    size_bytes: int
    created_at: datetime
    status: str
    reviewed_at: datetime | None
    reviewer_name: str | None
    reviewer_public_code: str | None
    rejection_reason_code: str | None
    rejection_reason: str | None
    internal_review_notes: str | None


@dataclass(frozen=True, slots=True)
class AdminPaymentAnalysisDetail:
    processing_status: str
    outcome: str | None
    bank_name_detected: str | None
    bank_is_recognized: bool | None
    amount_detected: Decimal | None
    amount_matches: bool | None
    destination_account_suffix: str | None
    destination_account_matches: bool | None
    transaction_at_detected: datetime | None
    date_is_plausible: bool | None
    receipt_number_detected: str | None
    transaction_reference_detected: str | None
    receipt_appears_unique: bool | None
    ocr_mean_confidence: Decimal | None
    failure_code: str | None
    failure_message: str | None
    findings: tuple[PaymentFinding, ...]
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaymentTimelineEntry:
    key: str
    label: str
    timestamp: datetime
    tone: str
    is_derived: bool = False


@dataclass(frozen=True, slots=True)
class PaymentAttemptHistoryItem:
    payment_public_code: str
    status: str
    status_label_es: str
    method: str
    amount: Decimal
    currency: str
    created_at: datetime
    terminal_at: datetime | None
    is_current: bool


@dataclass(frozen=True, slots=True)
class AdminPaymentDetail:
    payment_public_code: str
    status: str
    status_label_es: str
    order_number: str
    customer: AdminPaymentCustomer
    amount: Decimal
    currency: str
    method: str
    provider: str | None
    provider_reference: str | None
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None
    failed_at: datetime | None
    proof: AdminPaymentProofDetail | None
    analysis: AdminPaymentAnalysisDetail | None
    timeline: tuple[PaymentTimelineEntry, ...]
    attempt_history: tuple[PaymentAttemptHistoryItem, ...]
    can_review: bool


@dataclass(frozen=True, slots=True)
class _PaymentRecord:
    attempt: PaymentAttempt
    order: Order
    buyer: User
    proof: PaymentProof | None
    analysis: PaymentProofAnalysis | None
    reviewer: User | None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _effective_now(value: datetime | None) -> datetime:
    return _utc(value) or datetime.now(timezone.utc)


def ecuador_day_utc_bounds(
    *, day: date | None = None, now: datetime | None = None
) -> tuple[datetime, datetime]:
    effective_now = _effective_now(now)
    local_day = day or effective_now.astimezone(ECUADOR_TZ).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=ECUADOR_TZ)
    end_local = datetime.combine(
        local_day.fromordinal(local_day.toordinal() + 1),
        time.min,
        tzinfo=ECUADOR_TZ,
    )
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _decimal(value: Decimal | None) -> Decimal:
    return Decimal("0.00") if value is None else Decimal(value)


def _status_label(status: PaymentStatus) -> str:
    return PAYMENT_STATUS_LABELS_ES.get(status, status.value)


def _can_review(
    attempt: PaymentAttempt,
    proof: PaymentProof | None,
    current_user: User | None,
) -> bool:
    return bool(
        current_user is not None
        and user_has_permission(current_user, "payments.review")
        and attempt.status == PaymentStatus.PROCESSING
        and proof is not None
        and proof.status == PaymentProofStatus.PENDING_REVIEW
    )


def _payment_relevant_at(
    attempt: PaymentAttempt, proof: PaymentProof | None
) -> datetime:
    candidate = (
        attempt.approved_at
        or attempt.rejected_at
        or attempt.failed_at
        or (proof.reviewed_at if proof else None)
        or (proof.created_at if proof else None)
        or attempt.updated_at
        or attempt.created_at
    )
    return _utc(candidate) or datetime.min.replace(tzinfo=timezone.utc)


def payment_attempt_relevance_key(record) -> tuple[int, float, int]:
    """Canonical relevance ranking shared by Admin Orders and Payments.

    Priority is pending proof, processing, approved/rejected, then every other
    state. Within a priority, the newest relevant timestamp and UUID win.
    """

    attempt = record.attempt
    proof = record.proof
    if proof is not None and proof.status == PaymentProofStatus.PENDING_REVIEW:
        priority, relevant_at = 0, proof.created_at
    elif attempt.status == PaymentStatus.PROCESSING:
        priority, relevant_at = 1, attempt.updated_at
    elif attempt.status in {PaymentStatus.APPROVED, PaymentStatus.REJECTED}:
        priority = 2
        relevant_at = attempt.approved_at or attempt.rejected_at or attempt.updated_at
    else:
        priority, relevant_at = 3, attempt.updated_at or attempt.created_at
    aware = _utc(relevant_at) or datetime.min.replace(tzinfo=timezone.utc)
    return priority, -aware.timestamp(), -attempt.id.int


def select_relevant_payment_record(records: Iterable):
    values = tuple(records)
    return min(values, key=payment_attempt_relevance_key) if values else None


def relevant_payment_attempt_id_subquery(order_id_expression):
    candidate = aliased(PaymentAttempt)
    candidate_proof = aliased(PaymentProof)
    pending_proof_created = (
        select(candidate_proof.created_at)
        .where(
            candidate_proof.payment_attempt_id == candidate.id,
            candidate_proof.status == PaymentProofStatus.PENDING_REVIEW,
        )
        .correlate(candidate)
        .scalar_subquery()
    )
    priority = case(
        (pending_proof_created.is_not(None), 0),
        (candidate.status == PaymentStatus.PROCESSING, 1),
        (candidate.status.in_((PaymentStatus.APPROVED, PaymentStatus.REJECTED)), 2),
        else_=3,
    )
    relevant_at = func.coalesce(
        pending_proof_created,
        candidate.approved_at,
        candidate.rejected_at,
        candidate.updated_at,
        candidate.created_at,
    )
    return (
        select(candidate.id)
        .where(candidate.order_id == order_id_expression)
        .order_by(priority, relevant_at.desc(), candidate.id.desc())
        .limit(1)
        .correlate_except(candidate, candidate_proof)
        .scalar_subquery()
    )


def get_admin_payment_kpis(
    session: Session, *, now: datetime | None = None
) -> PaymentKpis:
    effective_now = _effective_now(now)
    day_start, day_end = ecuador_day_utc_bounds(now=effective_now)

    collected_amount, collected_count = session.execute(
        select(
            func.coalesce(func.sum(Order.grand_total), Decimal("0.00")),
            func.count(PaymentAttempt.id),
        )
        .join(Order, Order.id == PaymentAttempt.order_id)
        .where(
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at >= day_start,
            PaymentAttempt.approved_at < day_end,
        )
    ).one()

    flagged = or_(
        PaymentProofAnalysis.processing_status == PaymentProofAnalysisStatus.FAILED,
        PaymentProofAnalysis.outcome.in_((
            PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW,
            PaymentProofPrecheckOutcome.FAILED,
        )),
    )
    review_amount, review_count, flagged_count, oldest_submitted = session.execute(
        select(
            func.coalesce(func.sum(PaymentAttempt.amount), Decimal("0.00")),
            func.count(PaymentAttempt.id),
            func.coalesce(func.sum(case((flagged, 1), else_=0)), 0),
            func.min(PaymentProof.created_at),
        )
        .join(
            PaymentProof,
            PaymentProof.payment_attempt_id == PaymentAttempt.id,
        )
        .outerjoin(
            PaymentProofAnalysis,
            PaymentProofAnalysis.payment_proof_id == PaymentProof.id,
        )
        .where(
            PaymentAttempt.status == PaymentStatus.PROCESSING,
            PaymentProof.status == PaymentProofStatus.PENDING_REVIEW,
        )
    ).one()

    awaiting_amount, awaiting_count, next_expiration = session.execute(
        select(
            func.coalesce(func.sum(PaymentAttempt.amount), Decimal("0.00")),
            func.count(PaymentAttempt.id),
            func.min(PaymentAttempt.expires_at),
        )
        .join(Order, Order.id == PaymentAttempt.order_id)
        .outerjoin(
            PaymentProof,
            PaymentProof.payment_attempt_id == PaymentAttempt.id,
        )
        .where(
            PaymentAttempt.status == PaymentStatus.AWAITING_PROOF,
            PaymentProof.id.is_(None),
            Order.status == OrderStatus.PENDING_PAYMENT,
            PaymentAttempt.expires_at > effective_now,
        )
    ).one()

    failed_today = or_(
        and_(
            PaymentAttempt.status == PaymentStatus.REJECTED,
            PaymentAttempt.rejected_at >= day_start,
            PaymentAttempt.rejected_at < day_end,
        ),
        and_(
            PaymentAttempt.status == PaymentStatus.FAILED,
            PaymentAttempt.failed_at >= day_start,
            PaymentAttempt.failed_at < day_end,
        ),
    )
    failed_amount, failed_count = session.execute(
        select(
            func.coalesce(func.sum(PaymentAttempt.amount), Decimal("0.00")),
            func.count(PaymentAttempt.id),
        ).where(failed_today)
    ).one()

    return PaymentKpis(
        collected_today_amount=_decimal(collected_amount),
        collected_today_count=int(collected_count or 0),
        manual_review_amount=_decimal(review_amount),
        manual_review_count=int(review_count or 0),
        manual_review_flagged_count=int(flagged_count or 0),
        manual_review_oldest_submitted_at=_utc(oldest_submitted),
        awaiting_proof_amount=_decimal(awaiting_amount),
        awaiting_proof_count=int(awaiting_count or 0),
        awaiting_proof_next_expiration_at=_utc(next_expiration),
        failed_rejected_today_amount=_decimal(failed_amount),
        failed_rejected_today_count=int(failed_count or 0),
    )


def _normalize_page(page: int, per_page: int) -> tuple[int, int]:
    try:
        normalized_page = int(page)
        normalized_per_page = int(per_page)
    except (TypeError, ValueError) as exc:
        raise AdminPaymentQueryError("La paginación no es válida.") from exc
    if normalized_page < 1:
        raise AdminPaymentQueryError("La página debe ser mayor o igual a 1.")
    if not 1 <= normalized_per_page <= MAX_PAGE_SIZE:
        raise AdminPaymentQueryError(
            f"El tamaño de página debe estar entre 1 y {MAX_PAGE_SIZE}."
        )
    return normalized_page, normalized_per_page


def _normalize_enum(value, enum_type, label: str):
    if value is None or value == "":
        return None
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value).strip().upper())
    except ValueError as exc:
        raise AdminPaymentQueryError(f"El filtro {label} no es válido.") from exc


def _normalize_date(value: date | str | None, label: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise AdminPaymentQueryError(f"La fecha {label} no es válida.") from exc


def _normalize_amount(value, label: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AdminPaymentQueryError(f"El monto {label} no es válido.") from exc
    if not amount.is_finite() or amount < 0:
        raise AdminPaymentQueryError(f"El monto {label} debe ser no negativo.")
    return amount


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_predicate(query: str):
    normalized = " ".join((query or "").split())
    if not normalized:
        return None
    if len(normalized) > MAX_QUERY_LENGTH:
        raise AdminPaymentQueryError(
            f"La búsqueda no puede superar {MAX_QUERY_LENGTH} caracteres."
        )
    upper = normalized.upper()
    exact_references = or_(
        func.upper(PaymentAttempt.provider_reference) == upper,
        func.upper(PaymentProofAnalysis.receipt_number_detected) == upper,
        func.upper(PaymentProofAnalysis.transaction_reference_detected) == upper,
    )
    if _PAYMENT_CODE_RE.fullmatch(normalized):
        return func.upper(PaymentAttempt.public_code) == upper
    if _ORDER_CODE_RE.fullmatch(normalized):
        return func.upper(Order.order_number) == upper
    if _USER_CODE_RE.fullmatch(normalized):
        return func.upper(User.public_code) == upper
    text_pattern = f"%{_escaped(normalized)}%"
    return or_(
        func.upper(PaymentAttempt.public_code) == upper,
        func.upper(Order.order_number) == upper,
        func.upper(User.public_code) == upper,
        User.full_name.ilike(text_pattern, escape="\\"),
        User.email_normalized.ilike(text_pattern, escape="\\"),
        exact_references,
    )


def _list_filters(
    *,
    tab: str,
    query: str | None,
    method,
    status,
    date_from,
    date_to,
    amount_min,
    amount_max,
    analysis,
) -> list:
    normalized_tab = (tab or "all").strip().lower()
    if normalized_tab not in VALID_PAYMENT_TABS:
        raise AdminPaymentQueryError("La pestaña de pagos no es válida.")
    normalized_method = _normalize_enum(method, PaymentMethod, "método")
    normalized_status = _normalize_enum(status, PaymentStatus, "estado")
    from_date = _normalize_date(date_from, "desde")
    to_date = _normalize_date(date_to, "hasta")
    if from_date and to_date and from_date > to_date:
        raise AdminPaymentQueryError("La fecha desde no puede superar la fecha hasta.")
    minimum = _normalize_amount(amount_min, "mínimo")
    maximum = _normalize_amount(amount_max, "máximo")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise AdminPaymentQueryError("El monto mínimo no puede superar el máximo.")

    filters: list = []
    if normalized_tab == "manual_review":
        filters.extend((
            PaymentAttempt.status == PaymentStatus.PROCESSING,
            PaymentProof.status == PaymentProofStatus.PENDING_REVIEW,
        ))
    elif PAYMENT_TAB_STATUSES[normalized_tab]:
        filters.append(PaymentAttempt.status.in_(PAYMENT_TAB_STATUSES[normalized_tab]))
    if normalized_method is not None:
        filters.append(PaymentAttempt.method == normalized_method)
    if normalized_status is not None:
        filters.append(PaymentAttempt.status == normalized_status)
    if from_date is not None:
        start, _ = ecuador_day_utc_bounds(day=from_date)
        filters.append(PaymentAttempt.created_at >= start)
    if to_date is not None:
        _, end = ecuador_day_utc_bounds(day=to_date)
        filters.append(PaymentAttempt.created_at < end)
    if minimum is not None:
        filters.append(PaymentAttempt.amount >= minimum)
    if maximum is not None:
        filters.append(PaymentAttempt.amount <= maximum)
    if analysis:
        normalized_analysis = str(
            analysis.value if isinstance(analysis, PaymentProofPrecheckOutcome) else analysis
        ).strip().upper()
        if normalized_analysis == "NO_ANALYSIS":
            filters.append(PaymentProofAnalysis.id.is_(None))
        else:
            outcome = _normalize_enum(
                normalized_analysis,
                PaymentProofPrecheckOutcome,
                "prevalidación",
            )
            filters.append(PaymentProofAnalysis.outcome == outcome)
    search_filter = _search_predicate(query or "")
    if search_filter is not None:
        filters.append(search_filter)
    return filters


def _verification_summary(record: _PaymentRecord) -> str:
    attempt, proof, analysis = record.attempt, record.proof, record.analysis
    if attempt.status == PaymentStatus.REJECTED or (
        proof is not None and proof.status == PaymentProofStatus.REJECTED
    ):
        return "Rechazado"
    if proof is not None and proof.status == PaymentProofStatus.APPROVED:
        return "Aprobado manualmente"
    if proof is None:
        return "Sin comprobante"
    if analysis is None or analysis.processing_status in {
        PaymentProofAnalysisStatus.PENDING,
        PaymentProofAnalysisStatus.PROCESSING,
    }:
        return "Prevalidación pendiente"
    if (
        analysis.processing_status == PaymentProofAnalysisStatus.FAILED
        or analysis.outcome == PaymentProofPrecheckOutcome.FAILED
    ):
        return "Prevalidación fallida"
    if analysis.outcome == PaymentProofPrecheckOutcome.PASSED:
        return "Prevalidación correcta"
    if analysis.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW:
        return "Requiere revisión manual"
    return "Prevalidación pendiente"


def _as_record(row: Sequence) -> _PaymentRecord:
    return _PaymentRecord(*row)


def _row_view(record: _PaymentRecord, current_user: User | None) -> AdminPaymentRow:
    attempt = record.attempt
    return AdminPaymentRow(
        payment_public_code=attempt.public_code,
        order_number=record.order.order_number,
        customer=AdminPaymentCustomer(
            public_code=record.buyer.public_code,
            display_name=record.buyer.full_name,
        ),
        method=attempt.method.value,
        amount=Decimal(attempt.amount),
        currency=attempt.currency,
        status=attempt.status.value,
        status_label_es=_status_label(attempt.status),
        verification_summary=_verification_summary(record),
        created_at=_utc(attempt.created_at),
        relevant_at=_payment_relevant_at(attempt, record.proof),
        has_proof=record.proof is not None,
        analysis_outcome=(record.analysis.outcome.value if record.analysis and record.analysis.outcome else None),
        can_review=_can_review(attempt, record.proof, current_user),
    )


def list_admin_payments(
    session: Session,
    *,
    current_user: User | None = None,
    tab: str = "all",
    query: str | None = None,
    method: PaymentMethod | str | None = None,
    status: PaymentStatus | str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    amount_min: Decimal | str | None = None,
    amount_max: Decimal | str | None = None,
    analysis: PaymentProofPrecheckOutcome | str | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
    sort_by: str = "created_at",
    sort_direction: str = "desc",
) -> AdminPaymentListResult:
    normalized_page, normalized_per_page = _normalize_page(page, per_page)
    normalized_sort = (sort_by or "created_at").strip().lower()
    normalized_direction = (sort_direction or "desc").strip().lower()
    if normalized_sort not in VALID_SORT_FIELDS:
        raise AdminPaymentQueryError("El ordenamiento solicitado no es válido.")
    if normalized_direction not in VALID_SORT_DIRECTIONS:
        raise AdminPaymentQueryError("La dirección de orden no es válida.")
    filters = _list_filters(
        tab=tab,
        query=query,
        method=method,
        status=status,
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        analysis=analysis,
    )
    reviewer = aliased(User, name="payment_reviewer")
    joins = (
        (Order, Order.id == PaymentAttempt.order_id),
        (User, User.id == Order.buyer_id),
    )
    count_statement = select(func.count(PaymentAttempt.id)).select_from(PaymentAttempt)
    page_statement = select(
        PaymentAttempt,
        Order,
        User,
        PaymentProof,
        PaymentProofAnalysis,
        reviewer,
    ).select_from(PaymentAttempt)
    for model, condition in joins:
        count_statement = count_statement.join(model, condition)
        page_statement = page_statement.join(model, condition)
    count_statement = (
        count_statement
        .outerjoin(PaymentProof, PaymentProof.payment_attempt_id == PaymentAttempt.id)
        .outerjoin(PaymentProofAnalysis, PaymentProofAnalysis.payment_proof_id == PaymentProof.id)
        .where(*filters)
    )
    page_statement = (
        page_statement
        .outerjoin(PaymentProof, PaymentProof.payment_attempt_id == PaymentAttempt.id)
        .outerjoin(PaymentProofAnalysis, PaymentProofAnalysis.payment_proof_id == PaymentProof.id)
        .outerjoin(reviewer, reviewer.id == PaymentProof.reviewed_by_user_id)
        .where(*filters)
    )
    sort_column = {
        "created_at": PaymentAttempt.created_at,
        "amount": PaymentAttempt.amount,
        "status": PaymentAttempt.status,
    }[normalized_sort]
    order = sort_column.asc() if normalized_direction == "asc" else sort_column.desc()
    page_statement = page_statement.order_by(order, PaymentAttempt.id.desc()).limit(
        normalized_per_page
    ).offset((normalized_page - 1) * normalized_per_page)

    total = int(session.scalar(count_statement) or 0)
    records = tuple(_as_record(row) for row in session.execute(page_statement).all())
    pages = math.ceil(total / normalized_per_page) if total else 0
    return AdminPaymentListResult(
        items=tuple(_row_view(record, current_user) for record in records),
        page=normalized_page,
        per_page=normalized_per_page,
        total=total,
        pages=pages,
        has_next=normalized_page < pages,
        has_prev=normalized_page > 1 and pages > 0,
    )


def _clean_text(value: object, *, maximum: int) -> str:
    text_value = " ".join(str(value or "").split())
    return text_value[:maximum]


def _safe_filename(value: str) -> str:
    return _clean_text(PurePath(value or "comprobante").name, maximum=255) or "comprobante"


def _safe_findings(values: object) -> tuple[PaymentFinding, ...]:
    if not isinstance(values, list):
        return ()
    results: list[PaymentFinding] = []
    unknown_seen = False
    for value in values:
        if not isinstance(value, dict):
            unknown_seen = True
            continue
        code = _clean_text(value.get("code"), maximum=80).upper()
        if code not in _SAFE_FINDING_CODES:
            unknown_seen = True
            continue
        severity = _clean_text(value.get("severity"), maximum=16).lower()
        if severity not in {"info", "warning", "error"}:
            severity = "warning"
        message = _clean_text(value.get("message"), maximum=500)
        results.append(PaymentFinding(code, severity, message or "Hallazgo de prevalidación."))
    if unknown_seen:
        results.append(PaymentFinding(
            "UNRECOGNIZED_FINDING",
            "warning",
            "La prevalidación registró un hallazgo no reconocido.",
        ))
    return tuple(results)


def _terminal_at(attempt: PaymentAttempt) -> datetime | None:
    if attempt.status == PaymentStatus.APPROVED:
        return _utc(attempt.approved_at)
    if attempt.status == PaymentStatus.REJECTED:
        return _utc(attempt.rejected_at)
    if attempt.status in {
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    }:
        return _utc(attempt.failed_at)
    return None


def build_payment_timeline(
    attempt: PaymentAttempt,
    proof: PaymentProof | None,
    analysis: PaymentProofAnalysis | None,
) -> tuple[PaymentTimelineEntry, ...]:
    entries = [PaymentTimelineEntry(
        "payment_started",
        "Pago iniciado",
        _utc(attempt.created_at),
        "complete",
    )]
    if attempt.method == PaymentMethod.BANK_TRANSFER:
        entries.append(PaymentTimelineEntry(
            "awaiting_proof",
            "Esperando comprobante",
            _utc(attempt.created_at),
            "complete",
            is_derived=True,
        ))
    if proof is not None:
        entries.append(PaymentTimelineEntry(
            "proof_received",
            "Comprobante recibido",
            _utc(proof.created_at),
            "complete",
        ))
    if analysis is not None and analysis.started_at is not None:
        entries.append(PaymentTimelineEntry(
            "precheck_started",
            "Prevalidación iniciada",
            _utc(analysis.started_at),
            "complete",
        ))
    if (
        analysis is not None
        and analysis.completed_at is not None
        and analysis.processing_status == PaymentProofAnalysisStatus.COMPLETED
    ):
        entries.append(PaymentTimelineEntry(
            "precheck_completed",
            "Prevalidación completada",
            _utc(analysis.completed_at),
            "complete",
        ))
    elif (
        analysis is not None
        and analysis.completed_at is not None
        and analysis.processing_status == PaymentProofAnalysisStatus.FAILED
    ):
        entries.append(PaymentTimelineEntry(
            "precheck_failed",
            "Prevalidación fallida",
            _utc(analysis.completed_at),
            "danger",
        ))
    if proof is not None and proof.reviewed_at is not None:
        entries.append(PaymentTimelineEntry(
            "manual_review",
            "Revisión manual",
            _utc(proof.reviewed_at),
            "complete" if proof.status == PaymentProofStatus.APPROVED else "danger",
        ))
    terminal_events = (
        ("approved", "Aprobado", attempt.approved_at, "success"),
        ("rejected", "Rechazado", attempt.rejected_at, "danger"),
    )
    for key, label, timestamp, tone in terminal_events:
        if timestamp is not None:
            entries.append(PaymentTimelineEntry(key, label, _utc(timestamp), tone))
    if attempt.failed_at is not None and attempt.status in {
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
    }:
        label = {
            PaymentStatus.FAILED: "Fallido",
            PaymentStatus.CANCELLED: "Cancelado",
            PaymentStatus.EXPIRED: "Expirado",
        }[attempt.status]
        entries.append(PaymentTimelineEntry(
            attempt.status.value.lower(), label, _utc(attempt.failed_at), "danger"
        ))
    semantic_order = {
        "payment_started": 10,
        "awaiting_proof": 20,
        "proof_received": 30,
        "precheck_started": 40,
        "precheck_completed": 50,
        "precheck_failed": 50,
        "manual_review": 60,
        "approved": 70,
        "rejected": 70,
        PaymentStatus.FAILED.value.lower(): 70,
        PaymentStatus.CANCELLED.value.lower(): 70,
        PaymentStatus.EXPIRED.value.lower(): 70,
    }
    return tuple(sorted(
        entries,
        key=lambda item: (
            item.timestamp,
            semantic_order.get(item.key, 100),
            item.key,
        ),
    ))


def get_order_payment_attempt_history(
    session: Session,
    order_id: uuid.UUID,
) -> tuple[PaymentAttemptHistoryItem, ...]:
    attempts = tuple(session.scalars(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order_id)
        .order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
    ))
    if not attempts:
        return ()
    proof_by_attempt = {
        proof.payment_attempt_id: proof
        for proof in session.scalars(
            select(PaymentProof).where(
                PaymentProof.payment_attempt_id.in_(attempt.id for attempt in attempts)
            )
        )
    }

    @dataclass(frozen=True, slots=True)
    class Candidate:
        attempt: PaymentAttempt
        proof: PaymentProof | None

    candidates = tuple(Candidate(attempt, proof_by_attempt.get(attempt.id)) for attempt in attempts)
    current = select_relevant_payment_record(candidates)
    return tuple(
        PaymentAttemptHistoryItem(
            payment_public_code=attempt.public_code,
            status=attempt.status.value,
            status_label_es=_status_label(attempt.status),
            method=attempt.method.value,
            amount=Decimal(attempt.amount),
            currency=attempt.currency,
            created_at=_utc(attempt.created_at),
            terminal_at=_terminal_at(attempt),
            is_current=current is not None and current.attempt.id == attempt.id,
        )
        for attempt in attempts
    )


def get_admin_payment_detail(
    session: Session,
    public_code: str,
    *,
    current_user: User | None = None,
) -> AdminPaymentDetail:
    normalized = " ".join((public_code or "").split()).upper()
    if not normalized:
        raise AdminPaymentNotFoundError("No existe el intento de pago indicado.")
    reviewer = aliased(User, name="payment_reviewer")
    row = session.execute(
        select(
            PaymentAttempt,
            Order,
            User,
            PaymentProof,
            PaymentProofAnalysis,
            reviewer,
        )
        .join(Order, Order.id == PaymentAttempt.order_id)
        .join(User, User.id == Order.buyer_id)
        .outerjoin(PaymentProof, PaymentProof.payment_attempt_id == PaymentAttempt.id)
        .outerjoin(PaymentProofAnalysis, PaymentProofAnalysis.payment_proof_id == PaymentProof.id)
        .outerjoin(reviewer, reviewer.id == PaymentProof.reviewed_by_user_id)
        .where(func.upper(PaymentAttempt.public_code) == normalized)
    ).one_or_none()
    if row is None:
        raise AdminPaymentNotFoundError("No existe el intento de pago indicado.")
    record = _as_record(row)
    attempt, proof, analysis = record.attempt, record.proof, record.analysis
    proof_view = None
    if proof is not None:
        proof_view = AdminPaymentProofDetail(
            proof_id=proof.id,
            original_filename=_safe_filename(proof.original_filename),
            media_type=proof.media_type,
            size_bytes=proof.size_bytes,
            created_at=_utc(proof.created_at),
            status=proof.status.value,
            reviewed_at=_utc(proof.reviewed_at),
            reviewer_name=record.reviewer.full_name if record.reviewer else None,
            reviewer_public_code=record.reviewer.public_code if record.reviewer else None,
            rejection_reason_code=proof.rejection_reason_code,
            rejection_reason=proof.rejection_reason,
            internal_review_notes=proof.review_notes,
        )
    analysis_view = None
    if analysis is not None:
        analysis_view = AdminPaymentAnalysisDetail(
            processing_status=analysis.processing_status.value,
            outcome=analysis.outcome.value if analysis.outcome else None,
            bank_name_detected=analysis.bank_name_detected,
            bank_is_recognized=analysis.bank_is_recognized,
            amount_detected=(Decimal(analysis.amount_detected) if analysis.amount_detected is not None else None),
            amount_matches=analysis.amount_matches,
            destination_account_suffix=analysis.destination_account_suffix,
            destination_account_matches=analysis.destination_account_matches,
            transaction_at_detected=_utc(analysis.transaction_at_detected),
            date_is_plausible=analysis.date_is_plausible,
            receipt_number_detected=analysis.receipt_number_detected,
            transaction_reference_detected=analysis.transaction_reference_detected,
            receipt_appears_unique=analysis.receipt_appears_unique,
            ocr_mean_confidence=(Decimal(analysis.ocr_mean_confidence) if analysis.ocr_mean_confidence is not None else None),
            failure_code=analysis.failure_code,
            failure_message=analysis.failure_message,
            findings=_safe_findings(analysis.findings),
            started_at=_utc(analysis.started_at),
            completed_at=_utc(analysis.completed_at),
        )
    return AdminPaymentDetail(
        payment_public_code=attempt.public_code,
        status=attempt.status.value,
        status_label_es=_status_label(attempt.status),
        order_number=record.order.order_number,
        customer=AdminPaymentCustomer(record.buyer.public_code, record.buyer.full_name),
        amount=Decimal(attempt.amount),
        currency=attempt.currency,
        method=attempt.method.value,
        provider=attempt.provider,
        provider_reference=attempt.provider_reference,
        created_at=_utc(attempt.created_at),
        expires_at=_utc(attempt.expires_at),
        approved_at=_utc(attempt.approved_at),
        rejected_at=_utc(attempt.rejected_at),
        failed_at=_utc(attempt.failed_at),
        proof=proof_view,
        analysis=analysis_view,
        timeline=build_payment_timeline(attempt, proof, analysis),
        attempt_history=get_order_payment_attempt_history(session, attempt.order_id),
        can_review=_can_review(attempt, proof, current_user),
    )
