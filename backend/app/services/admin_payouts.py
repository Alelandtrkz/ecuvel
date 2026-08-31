from __future__ import annotations

import math
import re
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal

from sqlalchemy import and_, exists, extract, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AdminAuditEvent,
    Order,
    PaymentAttempt,
    SellerOrder,
    SellerPayout,
    SellerPayoutItem,
    Store,
    StoreBankAccountVersion,
    User,
)
from app.models.enums import (
    BankAccountVersionStatus,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
)
from app.services.bank_accounts import bank_account_summary
from app.services.financial_audit import (
    PAYOUT_CANCELLED,
    PAYOUT_HELD,
    PAYOUT_PAID,
    PAYOUT_RELEASED,
    PAYOUT_SCHEDULED,
)
from app.services.payout_calendar import (
    PAYOUT_TIMEZONE,
    PayoutCycleKind,
    PayoutCycleWindow,
    payout_cycle_window,
    payout_cycle_windows,
)
from app.services.seller_payouts import preview_payout_cycle


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_QUERY_LENGTH = 120
ZERO = Decimal("0.00")

PAYOUT_STATUS_LABELS = {
    SellerPayoutStatus.SCHEDULED: "Programada",
    SellerPayoutStatus.ON_HOLD: "En hold",
    SellerPayoutStatus.PAID: "Pagada",
    SellerPayoutStatus.CANCELLED: "Cancelada",
}
PAYOUT_TAB_STATUSES = {
    "all": None,
    "scheduled": SellerPayoutStatus.SCHEDULED,
    "on_hold": SellerPayoutStatus.ON_HOLD,
    "paid": SellerPayoutStatus.PAID,
    "cancelled": SellerPayoutStatus.CANCELLED,
}
VALID_SORT_FIELDS = frozenset({"scheduled_for", "created_at", "net_total", "status", "paid_at"})
VALID_SORT_DIRECTIONS = frozenset({"asc", "desc"})
_PAY_RE = re.compile(r"^PAY-\d{1,12}$", re.IGNORECASE)


class AdminPayoutQueryError(ValueError):
    pass


class AdminPayoutNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class AdminPayoutKpis:
    pending_amount: Decimal
    pending_store_count: int
    scheduled_amount: Decimal
    scheduled_count: int
    on_hold_amount: Decimal
    on_hold_count: int
    paid_period_amount: Decimal
    paid_period_count: int
    cancelled_period_amount: Decimal
    cancelled_period_count: int


@dataclass(frozen=True, slots=True)
class AdminPayoutListItem:
    payout_number: str
    store_name: str
    store_public_code: str
    item_count: int
    gross_total: Decimal
    discount_total: Decimal
    commission_total: Decimal
    net_total: Decimal
    status: str
    status_label: str
    scheduled_for: datetime
    bank_name: str | None
    account_last4: str | None


@dataclass(frozen=True, slots=True)
class AdminPayoutPage:
    items: tuple[AdminPayoutListItem, ...]
    page: int
    per_page: int
    total: int
    pages: int
    has_prev: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class AdminPayoutTimelineEvent:
    action: str
    label: str
    timestamp: datetime | None
    actor_name: str


@dataclass(frozen=True, slots=True)
class AdminPayoutOrderItem:
    seller_order_number: str
    order_number: str
    delivered_at: datetime | None
    eligible_at: datetime
    net_total: Decimal


@dataclass(frozen=True, slots=True)
class AdminPayoutBankSummary:
    bank_name: str
    account_type: str
    account_last4: str
    holder_name: str
    holder_identification_masked: str
    version: int
    status: str


@dataclass(frozen=True, slots=True)
class AdminPayoutDetail:
    payout_number: str
    status: str
    status_label: str
    store_name: str
    store_public_code: str
    gross_total: Decimal
    discount_total: Decimal
    commission_total: Decimal
    net_total: Decimal
    scheduled_for: datetime
    cutoff_local: datetime
    cycle_kind: str
    cycle_label: str
    paid_at: datetime | None
    cancelled_at: datetime | None
    external_reference: str | None
    receipt_original_filename: str | None
    has_receipt: bool
    bank: AdminPayoutBankSummary
    timeline: tuple[AdminPayoutTimelineEvent, ...]
    orders: tuple[AdminPayoutOrderItem, ...]
    item_count: int


@dataclass(frozen=True, slots=True)
class AdminPayoutCycleOption:
    window: PayoutCycleWindow
    executable: bool


@dataclass(frozen=True, slots=True)
class AdminPayoutOmittedStore:
    store_public_code: str
    store_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class AdminPayoutCyclePreview:
    window: PayoutCycleWindow
    store_count: int
    order_count: int
    gross_total: Decimal
    discount_total: Decimal
    commission_total: Decimal
    net_total: Decimal
    omitted: tuple[AdminPayoutOmittedStore, ...]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    local = (_utc(now) or now).astimezone(PAYOUT_TIMEZONE)
    start = datetime(local.year, local.month, 1, tzinfo=PAYOUT_TIMEZONE)
    if local.month == 12:
        end = datetime(local.year + 1, 1, 1, tzinfo=PAYOUT_TIMEZONE)
    else:
        end = datetime(local.year, local.month + 1, 1, tzinfo=PAYOUT_TIMEZONE)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _date_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=PAYOUT_TIMEZONE)
    end = datetime.combine(value.fromordinal(value.toordinal() + 1), time.min, tzinfo=PAYOUT_TIMEZONE)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _normalize_date(value: date | str | None, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise AdminPayoutQueryError(f"La fecha {label} no es válida.") from exc


def _normalize_page(page, per_page) -> tuple[int, int]:
    try:
        page_value, size_value = int(page), int(per_page)
    except (TypeError, ValueError) as exc:
        raise AdminPayoutQueryError("La paginación no es válida.") from exc
    if page_value < 1 or not 1 <= size_value <= MAX_PAGE_SIZE:
        raise AdminPayoutQueryError("La paginación no es válida.")
    return page_value, size_value


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pending_predicate(now: datetime):
    active_assignment = exists(select(SellerPayoutItem.payout_id).where(
        SellerPayoutItem.seller_order_id == SellerOrder.id,
        SellerPayoutItem.released_at.is_(None),
    ))
    approved_payment = exists(select(PaymentAttempt.id).where(
        PaymentAttempt.order_id == SellerOrder.order_id,
        PaymentAttempt.status == PaymentStatus.APPROVED,
        PaymentAttempt.approved_at.is_not(None),
    ))
    return and_(
        SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
        SellerOrder.status == SellerOrderStatus.COMPLETED,
        SellerOrder.requires_refund_resolution.is_(False),
        SellerOrder.delivered_at.is_not(None),
        SellerOrder.payout_eligible_at.is_not(None),
        SellerOrder.payout_eligible_at <= _utc(now),
        SellerOrder.currency == "USD",
        approved_payment,
        ~active_assignment,
    )


def get_admin_payout_kpis(session: Session, *, now: datetime | None = None) -> AdminPayoutKpis:
    effective_now = _utc(now) or datetime.now(timezone.utc)
    pending_amount, pending_stores = session.execute(select(
        func.coalesce(func.sum(SellerOrder.seller_net_total), ZERO),
        func.count(func.distinct(SellerOrder.store_id)),
    ).where(_pending_predicate(effective_now))).one()

    period_start, period_end = _month_bounds(effective_now)
    aggregates = {status: (ZERO, 0) for status in SellerPayoutStatus}
    for status, amount, count in session.execute(select(
        SellerPayout.status,
        func.coalesce(func.sum(SellerPayout.net_total), ZERO),
        func.count(SellerPayout.id),
    ).where(or_(
        SellerPayout.status.in_((SellerPayoutStatus.SCHEDULED, SellerPayoutStatus.ON_HOLD)),
        and_(SellerPayout.status == SellerPayoutStatus.PAID, SellerPayout.paid_at >= period_start, SellerPayout.paid_at < period_end),
        and_(SellerPayout.status == SellerPayoutStatus.CANCELLED, SellerPayout.cancelled_at >= period_start, SellerPayout.cancelled_at < period_end),
    )).group_by(SellerPayout.status)):
        aggregates[status] = (Decimal(amount), int(count))
    scheduled = aggregates[SellerPayoutStatus.SCHEDULED]
    held = aggregates[SellerPayoutStatus.ON_HOLD]
    paid = aggregates[SellerPayoutStatus.PAID]
    cancelled = aggregates[SellerPayoutStatus.CANCELLED]
    return AdminPayoutKpis(Decimal(pending_amount), int(pending_stores), scheduled[0], scheduled[1], held[0], held[1], paid[0], paid[1], cancelled[0], cancelled[1])


def list_admin_payouts(
    session: Session, *, tab="all", query=None, status=None, cycle=None,
    date_from=None, date_to=None, store=None, page=1, per_page=DEFAULT_PAGE_SIZE,
    sort_by="scheduled_for", sort_direction="desc",
) -> AdminPayoutPage:
    normalized_tab = str(tab or "all").strip().lower()
    if normalized_tab not in PAYOUT_TAB_STATUSES:
        raise AdminPayoutQueryError("La pestaña de liquidaciones no es válida.")
    try:
        normalized_status = SellerPayoutStatus(str(status).strip().upper()) if status else None
    except ValueError as exc:
        raise AdminPayoutQueryError("El estado no es válido.") from exc
    normalized_cycle = str(cycle or "").strip().upper() or None
    if normalized_cycle not in (None, PayoutCycleKind.MID_MONTH.value, PayoutCycleKind.MONTH_END.value):
        raise AdminPayoutQueryError("El ciclo no es válido.")
    from_date, to_date = _normalize_date(date_from, "desde"), _normalize_date(date_to, "hasta")
    if from_date and to_date and from_date > to_date:
        raise AdminPayoutQueryError("La fecha desde no puede superar la fecha hasta.")
    normalized_sort = str(sort_by or "scheduled_for").strip().lower()
    normalized_direction = str(sort_direction or "desc").strip().lower()
    if normalized_sort not in VALID_SORT_FIELDS or normalized_direction not in VALID_SORT_DIRECTIONS:
        raise AdminPayoutQueryError("El orden no es válido.")
    page_value, size_value = _normalize_page(page, per_page)

    filters = []
    if PAYOUT_TAB_STATUSES[normalized_tab]:
        filters.append(SellerPayout.status == PAYOUT_TAB_STATUSES[normalized_tab])
    if normalized_status:
        filters.append(SellerPayout.status == normalized_status)
    if normalized_cycle:
        local_day = extract("day", func.timezone("America/Guayaquil", SellerPayout.scheduled_for))
        filters.append(local_day == 15 if normalized_cycle == PayoutCycleKind.MID_MONTH.value else local_day != 15)
    if from_date:
        filters.append(SellerPayout.scheduled_for >= _date_bounds(from_date)[0])
    if to_date:
        filters.append(SellerPayout.scheduled_for < _date_bounds(to_date)[1])
    if store:
        filters.append(func.upper(Store.public_code) == str(store).strip().upper())
    normalized_query = " ".join(str(query or "").split())
    if len(normalized_query) > MAX_QUERY_LENGTH:
        raise AdminPayoutQueryError(f"La búsqueda no puede superar {MAX_QUERY_LENGTH} caracteres.")
    if normalized_query:
        upper = normalized_query.upper()
        if _PAY_RE.fullmatch(normalized_query):
            filters.append(func.upper(SellerPayout.payout_number) == upper)
        else:
            pattern = f"%{_escaped(normalized_query)}%"
            order_match = exists(select(SellerPayoutItem.payout_id).join(
                SellerOrder, SellerOrder.id == SellerPayoutItem.seller_order_id
            ).join(Order, Order.id == SellerOrder.order_id).where(
                SellerPayoutItem.payout_id == SellerPayout.id,
                or_(SellerOrder.seller_order_number.ilike(pattern, escape="\\"), Order.order_number.ilike(pattern, escape="\\")),
            ))
            filters.append(or_(
                func.upper(SellerPayout.payout_number) == upper,
                Store.name.ilike(pattern, escape="\\"),
                Store.public_code.ilike(pattern, escape="\\"),
                order_match,
            ))

    count_statement = select(func.count(SellerPayout.id)).join(Store, Store.id == SellerPayout.store_id).where(*filters)
    total = int(session.scalar(count_statement) or 0)
    pages = math.ceil(total / size_value) if total else 0
    item_count = select(func.count(SellerPayoutItem.seller_order_id)).where(SellerPayoutItem.payout_id == SellerPayout.id).correlate(SellerPayout).scalar_subquery()
    sort_column = {
        "scheduled_for": SellerPayout.scheduled_for,
        "created_at": SellerPayout.created_at,
        "net_total": SellerPayout.net_total,
        "status": SellerPayout.status,
        "paid_at": SellerPayout.paid_at,
    }[normalized_sort]
    order_expression = sort_column.asc() if normalized_direction == "asc" else sort_column.desc()
    rows = session.execute(select(
        SellerPayout.payout_number, Store.name, Store.public_code, item_count,
        SellerPayout.gross_sales_total, SellerPayout.discount_total,
        SellerPayout.commission_total, SellerPayout.net_total, SellerPayout.status,
        SellerPayout.scheduled_for, SellerPayout.destination_bank_name_snapshot,
        SellerPayout.destination_account_last4,
    ).join(Store, Store.id == SellerPayout.store_id).where(*filters).order_by(
        order_expression, SellerPayout.id.desc()
    ).offset((page_value - 1) * size_value).limit(size_value)).all()
    items = tuple(AdminPayoutListItem(
        row[0], row[1], row[2], int(row[3] or 0), Decimal(row[4]), Decimal(row[5]),
        Decimal(row[6]), Decimal(row[7]), row[8].value, PAYOUT_STATUS_LABELS[row[8]],
        row[9], row[10], row[11],
    ) for row in rows)
    return AdminPayoutPage(items, page_value, size_value, total, pages, page_value > 1, page_value < pages)


def list_payout_stores(session: Session) -> tuple[tuple[str, str], ...]:
    return tuple(session.execute(select(Store.public_code, Store.name).join(
        SellerPayout, SellerPayout.store_id == Store.id
    ).distinct().order_by(Store.name)).all())


def _cycle_from_scheduled(value: datetime) -> PayoutCycleWindow:
    return payout_cycle_window((_utc(value) or value).astimezone(PAYOUT_TIMEZONE).date())


def _timeline(session: Session, payout: SellerPayout) -> tuple[AdminPayoutTimelineEvent, ...]:
    labels = {
        PAYOUT_SCHEDULED: "Programada", PAYOUT_HELD: "En hold",
        PAYOUT_RELEASED: "Reanudada", PAYOUT_PAID: "Pagada",
        PAYOUT_CANCELLED: "Cancelada",
    }
    events = session.execute(select(AdminAuditEvent.action, AdminAuditEvent.created_at, User.full_name).outerjoin(
        User, User.id == AdminAuditEvent.actor_user_id
    ).where(
        AdminAuditEvent.action.in_(tuple(labels)),
        AdminAuditEvent.metadata_json["payout_id"].astext == str(payout.id),
    ).order_by(AdminAuditEvent.created_at, AdminAuditEvent.id)).all()
    values = [AdminPayoutTimelineEvent(action, labels[action], created_at, actor or "Sistema") for action, created_at, actor in events]
    if not values:
        values.append(AdminPayoutTimelineEvent(PAYOUT_SCHEDULED, "Programada", payout.created_at or payout.scheduled_for, "Sistema"))
        if payout.status == SellerPayoutStatus.PAID:
            values.append(AdminPayoutTimelineEvent(PAYOUT_PAID, "Pagada", payout.paid_at, "Sistema"))
        elif payout.status == SellerPayoutStatus.CANCELLED:
            values.append(AdminPayoutTimelineEvent(PAYOUT_CANCELLED, "Cancelada", payout.cancelled_at, "Sistema"))
        elif payout.status == SellerPayoutStatus.ON_HOLD:
            values.append(AdminPayoutTimelineEvent(PAYOUT_HELD, "En hold", None, "Sistema"))
    return tuple(values)


def get_admin_payout_detail(session: Session, payout_number: str, *, order_limit: int = 5) -> AdminPayoutDetail:
    normalized = " ".join((payout_number or "").split()).upper()
    row = session.execute(select(SellerPayout, Store, StoreBankAccountVersion).join(
        Store, Store.id == SellerPayout.store_id
    ).join(StoreBankAccountVersion, StoreBankAccountVersion.id == SellerPayout.bank_account_version_id).where(
        SellerPayout.payout_number == normalized
    )).one_or_none()
    if row is None:
        raise AdminPayoutNotFoundError("La liquidación no existe.")
    payout, store, version = row
    bank_safe = bank_account_summary(version)
    orders = tuple(AdminPayoutOrderItem(*values) for values in session.execute(select(
        SellerOrder.seller_order_number, Order.order_number, SellerOrder.delivered_at,
        SellerPayoutItem.eligible_at, SellerPayoutItem.net_amount_snapshot,
    ).join(SellerOrder, SellerOrder.id == SellerPayoutItem.seller_order_id).join(
        Order, Order.id == SellerOrder.order_id
    ).where(SellerPayoutItem.payout_id == payout.id).order_by(
        SellerPayoutItem.eligible_at, SellerOrder.seller_order_number
    ).limit(order_limit)).all())
    item_count = int(session.scalar(select(func.count(SellerPayoutItem.seller_order_id)).where(SellerPayoutItem.payout_id == payout.id)) or 0)
    window = _cycle_from_scheduled(payout.scheduled_for)
    account_type = getattr(version.account_type, "value", str(version.account_type)).replace("_", " ").title()
    bank = AdminPayoutBankSummary(
        payout.destination_bank_name_snapshot or bank_safe.bank_name,
        account_type, payout.destination_account_last4 or bank_safe.account_last4,
        bank_safe.holder_name, bank_safe.holder_identification_masked,
        bank_safe.version, bank_safe.status.value,
    )
    return AdminPayoutDetail(
        payout.payout_number, payout.status.value, PAYOUT_STATUS_LABELS[payout.status],
        store.name, store.public_code, Decimal(payout.gross_sales_total), Decimal(payout.discount_total),
        Decimal(payout.commission_total), Decimal(payout.net_total), payout.scheduled_for,
        window.cutoff_local, window.cycle_kind.value,
        "Día 15" if window.cycle_kind == PayoutCycleKind.MID_MONTH else "Fin de mes",
        payout.paid_at, payout.cancelled_at, payout.external_reference,
        payout.receipt_original_filename, bool(payout.receipt_storage_key), bank,
        _timeline(session, payout), orders, item_count,
    )


def cycle_options(*, now: datetime | None = None) -> tuple[AdminPayoutCycleOption, ...]:
    effective = _utc(now) or datetime.now(timezone.utc)
    local_today = effective.astimezone(PAYOUT_TIMEZONE).date()
    values: list[PayoutCycleWindow] = []
    year, month = local_today.year, local_today.month
    for _ in range(2):
        values.extend(payout_cycle_windows(year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    future = [window for window in values if window.cycle_date_local >= local_today]
    selected = future[:2] if future else values[-2:]
    return tuple(AdminPayoutCycleOption(window, window.cycle_date_local <= local_today) for window in selected)


def calendar_months(*, now: datetime | None = None) -> tuple[tuple[str, tuple[PayoutCycleWindow, ...]], ...]:
    effective = _utc(now) or datetime.now(timezone.utc)
    local = effective.astimezone(PAYOUT_TIMEZONE)
    months = []
    year, month = local.year, local.month
    names = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
    for _ in range(2):
        months.append((f"{names[month - 1].capitalize()} {year}", payout_cycle_windows(year, month)))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(months)


def get_admin_cycle_preview(session: Session, *, cycle_date: date, now: datetime) -> AdminPayoutCyclePreview:
    window, previews = preview_payout_cycle(session, cycle_date=cycle_date, now=now)
    store_ids = tuple(item.store_id for item in previews)
    usable_ids = set(session.scalars(select(StoreBankAccountVersion.store_id).where(
        StoreBankAccountVersion.store_id.in_(store_ids),
        StoreBankAccountVersion.status == BankAccountVersionStatus.APPROVED,
        StoreBankAccountVersion.currency == "USD",
        StoreBankAccountVersion.usable_from.is_not(None),
        StoreBankAccountVersion.usable_from <= _utc(now),
    )).all()) if store_ids else set()
    stores = {row.id: row for row in session.scalars(select(Store).where(Store.id.in_(store_ids))).all()} if store_ids else {}
    ready = [item for item in previews if item.store_id in usable_ids]
    omitted = tuple(AdminPayoutOmittedStore(
        item.store_public_code, stores[item.store_id].name, "Cuenta bancaria no utilizable"
    ) for item in previews if item.store_id not in usable_ids)
    return AdminPayoutCyclePreview(
        window, len(ready), sum(item.order_count for item in ready),
        sum((item.gross_total for item in ready), ZERO),
        sum((item.discount_total for item in ready), ZERO),
        sum((item.commission_total for item in ready), ZERO),
        sum((item.net_total for item in ready), ZERO), omitted,
    )
