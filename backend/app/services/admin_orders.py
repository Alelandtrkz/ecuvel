from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    LogisticsPackageState,
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    PaymentProof,
    PaymentProofAnalysis,
    SellerInboundPackage,
    SellerOrder,
    Store,
    User,
)
from app.models.enums import (
    LogisticsPackageStatus,
    OrderStatus,
    PackageStatus,
    PaymentProofStatus,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerInboundPackageStatus,
)
from app.services.admin_operations import ecuador_comparison_windows
from app.services.admin_payments import (
    relevant_payment_attempt_id_subquery,
    select_relevant_payment_record,
)
from app.services.fulfillment import order_ready_for_pickup_predicate
from app.services.partner_order_workflow import partner_order_overdue_predicate


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
DEFAULT_PAGE_SIZE = 25
ALLOWED_PAGE_SIZES = (25, 50)
MAX_PAGE = 10_000
MAX_QUERY_LENGTH = 120
VALID_TABS = (
    "all",
    "pending-payment",
    "confirmed",
    "preparing",
    "ready",
    "delivered",
    "cancelled",
)
VALID_PAYMENT_FILTERS = ("", "review", "approved", "rejected", "awaiting")
VALID_FULFILLMENT_FILTERS = ("", "picking", "packed")
VALID_ATTENTION_FILTERS = ("", "inbound-overdue")
_PUBLIC_USER_RE = re.compile(r"^U-(\d{1,8})$", re.IGNORECASE)
_SPANISH_MONTHS = (
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
)


@dataclass(frozen=True, slots=True)
class AdminStatusView:
    code: str
    label: str
    tone: str


@dataclass(frozen=True, slots=True)
class AdminPaymentView:
    attempt_id: uuid.UUID | None
    proof_id: uuid.UUID | None
    method: str | None
    status: str
    status_label: str
    status_tone: str
    amount: Decimal | None
    currency: str | None
    provider_reference: str | None
    proof_status: str | None
    proof_filename: str | None
    proof_media_type: str | None
    proof_size_bytes: int | None
    reported_at: datetime | None
    approved_at: datetime | None
    rejected_at: datetime | None
    reviewed_at: datetime | None
    reviewer_name: str | None
    rejection_reason: str | None
    review_notes: str | None
    can_review: bool


@dataclass(frozen=True, slots=True)
class AdminOrderRow:
    order_number: str
    customer_name: str
    customer_code: str
    item_count: int
    store_count: int
    grand_total: Decimal
    currency: str
    payment: AdminPaymentView
    fulfillment: AdminStatusView
    package_count: int
    expected_package_count: int
    completed_package_count: int
    created_at: datetime
    created_at_label: str


@dataclass(frozen=True, slots=True)
class AdminOrderTab:
    key: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class AdminOrdersPage:
    rows: tuple[AdminOrderRow, ...]
    tabs: tuple[AdminOrderTab, ...]
    active_tab: str
    query: str
    payment_filter: str
    fulfillment_filter: str
    attention_filter: str
    date_filter: str
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class AdminOrderLineView:
    product_name: str
    seller_name: str
    seller_sku: str
    image_url: str | None
    variant_title: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal


@dataclass(frozen=True, slots=True)
class AdminStoreOrderView:
    name: str
    public_code: str
    item_count: int
    status: AdminStatusView


@dataclass(frozen=True, slots=True)
class AdminPackageView:
    package_code: str
    barcode: str
    status: AdminStatusView
    product_name: str
    variant_title: str | None
    quantity: int
    location_code: str | None
    packed_at: datetime | None
    ready_at: datetime | None
    handed_over_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminInboundPackageView:
    package_code: str
    barcode: str
    store_name: str
    status: AdminStatusView
    current_location: str
    custodian_name: str
    tracking_available: bool


@dataclass(frozen=True, slots=True)
class AdminTimelineStep:
    key: str
    label: str
    icon: str
    reached: bool
    active: bool
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class AdminHistoryItem:
    timestamp: datetime
    label: str
    actor: str | None = None


@dataclass(frozen=True, slots=True)
class AdminOrderDetail:
    order_number: str
    status: AdminStatusView
    created_at: datetime
    customer_name: str
    customer_code: str
    customer_email: str | None
    customer_phone: str | None
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    shipping_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    total_units: int
    lines: tuple[AdminOrderLineView, ...]
    stores: tuple[AdminStoreOrderView, ...]
    inbound_packages: tuple[AdminInboundPackageView, ...]
    packages: tuple[AdminPackageView, ...]
    payment: AdminPaymentView
    timeline: tuple[AdminTimelineStep, ...]
    history: tuple[AdminHistoryItem, ...]


@dataclass(frozen=True, slots=True)
class AdminEvidenceRow:
    label: str
    expected: str
    detected: str
    result: str
    tone: str


@dataclass(frozen=True, slots=True)
class AdminPaymentReview:
    order_number: str
    customer_name: str
    customer_code: str
    order_total: Decimal
    currency: str
    payment: AdminPaymentView
    analysis_status: str | None
    analysis_outcome: str | None
    evidence: tuple[AdminEvidenceRow, ...]
    findings: tuple[str, ...]
    bank_name: str | None
    account_suffix: str | None


@dataclass(frozen=True, slots=True)
class _PaymentRecord:
    attempt: PaymentAttempt
    proof: PaymentProof | None
    reviewer_name: str | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_ecuador_datetime(value: datetime | None) -> str:
    if value is None:
        return "Pendiente"
    local = _utc(value).astimezone(ECUADOR_TZ)
    return (
        f"{local.day:02d} {_SPANISH_MONTHS[local.month - 1]} "
        f"{local.year} · {local:%H:%M}"
    )


def _local_label(value: datetime) -> str:
    return format_ecuador_datetime(value)


def _normalize_choice(value: str | None, allowed: Iterable[str], default: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in allowed else default


def _normalize_page(value: str | int | None) -> int:
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return min(MAX_PAGE, max(1, page))


def _normalize_page_size(value: str | int | None) -> int:
    try:
        size = int(value or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in ALLOWED_PAGE_SIZES else DEFAULT_PAGE_SIZE


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def select_relevant_payment(records: Iterable[_PaymentRecord]) -> _PaymentRecord | None:
    return select_relevant_payment_record(records)


def _payment_view(record: _PaymentRecord | None) -> AdminPaymentView:
    if record is None:
        return AdminPaymentView(
            attempt_id=None,
            proof_id=None,
            method=None,
            status="MISSING",
            status_label="Sin intento",
            status_tone="muted",
            amount=None,
            currency=None,
            provider_reference=None,
            proof_status=None,
            proof_filename=None,
            proof_media_type=None,
            proof_size_bytes=None,
            reported_at=None,
            approved_at=None,
            rejected_at=None,
            reviewed_at=None,
            reviewer_name=None,
            rejection_reason=None,
            review_notes=None,
            can_review=False,
        )
    attempt, proof = record.attempt, record.proof
    labels = {
        PaymentStatus.AWAITING_PROOF: ("Esperando comprobante", "warning"),
        PaymentStatus.PENDING_PROVIDER: ("Pendiente de proveedor", "warning"),
        PaymentStatus.PROCESSING: ("En revisión", "warning"),
        PaymentStatus.APPROVED: ("Aprobado", "success"),
        PaymentStatus.REJECTED: ("Rechazado", "danger"),
        PaymentStatus.FAILED: ("Fallido", "danger"),
        PaymentStatus.CANCELLED: ("Cancelado", "muted"),
        PaymentStatus.EXPIRED: ("Expirado", "muted"),
    }
    label, tone = labels.get(attempt.status, (attempt.status.value, "muted"))
    if proof and proof.status == PaymentProofStatus.PENDING_REVIEW:
        label, tone = "En revisión", "warning"
    return AdminPaymentView(
        attempt_id=attempt.id,
        proof_id=proof.id if proof else None,
        method=attempt.method.value,
        status=attempt.status.value,
        status_label=label,
        status_tone=tone,
        amount=attempt.amount,
        currency=attempt.currency,
        provider_reference=attempt.provider_reference,
        proof_status=proof.status.value if proof else None,
        proof_filename=proof.original_filename if proof else None,
        proof_media_type=proof.media_type if proof else None,
        proof_size_bytes=proof.size_bytes if proof else None,
        reported_at=proof.created_at if proof else None,
        approved_at=attempt.approved_at,
        rejected_at=attempt.rejected_at,
        reviewed_at=proof.reviewed_at if proof else None,
        reviewer_name=record.reviewer_name,
        rejection_reason=proof.rejection_reason if proof else None,
        review_notes=proof.review_notes if proof else None,
        can_review=bool(
            proof
            and proof.status == PaymentProofStatus.PENDING_REVIEW
            and attempt.status == PaymentStatus.PROCESSING
        ),
    )


def _payment_records(session: Session, order_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[_PaymentRecord]]:
    ids = tuple(order_ids)
    grouped: dict[uuid.UUID, list[_PaymentRecord]] = defaultdict(list)
    if not ids:
        return grouped
    rows = session.execute(
        select(PaymentAttempt, PaymentProof, User.full_name)
        .outerjoin(PaymentProof, PaymentProof.payment_attempt_id == PaymentAttempt.id)
        .outerjoin(User, User.id == PaymentProof.reviewed_by_user_id)
        .where(PaymentAttempt.order_id.in_(ids))
    ).all()
    for attempt, proof, reviewer_name in rows:
        grouped[attempt.order_id].append(_PaymentRecord(attempt, proof, reviewer_name))
    return grouped


def _delivered_predicate():
    item_count = (
        select(func.count(OrderItem.id))
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    delivered_count = (
        select(func.count(OrderPackage.id))
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(
            SellerOrder.order_id == Order.id,
            OrderPackage.status == PackageStatus.HANDED_OVER,
            OrderPackage.handed_over_at.is_not(None),
        )
        .correlate(Order)
        .scalar_subquery()
    )
    return and_(item_count > 0, delivered_count == item_count)


def _approved_payment_exists():
    relevant_attempt_id = _relevant_payment_attempt_id()
    return exists(
        select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == Order.id,
            PaymentAttempt.id == relevant_attempt_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
        )
    )


def _relevant_payment_attempt_id():
    return relevant_payment_attempt_id_subquery(Order.id)


def _preparing_predicate():
    progressed_seller = exists(
        select(SellerOrder.id).where(
            SellerOrder.order_id == Order.id,
            or_(
                SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
                SellerOrder.status.in_((SellerOrderStatus.PICKING, SellerOrderStatus.PACKED)),
            ),
        )
    )
    outbound_started = exists(
        select(OrderPackage.id)
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(
            SellerOrder.order_id == Order.id,
            OrderPackage.status.in_((PackageStatus.CREATED, PackageStatus.PACKED)),
        )
    )
    return and_(
        _approved_payment_exists(),
        or_(progressed_seller, outbound_started),
        ~order_ready_for_pickup_predicate(),
        ~_delivered_predicate(),
    )


def _confirmed_predicate():
    return and_(
        _approved_payment_exists(),
        ~_preparing_predicate(),
        ~order_ready_for_pickup_predicate(),
        ~_delivered_predicate(),
        Order.status.notin_((OrderStatus.CANCELLED, OrderStatus.EXPIRED)),
    )


def _tab_predicate(tab: str):
    if tab == "pending-payment":
        return Order.status == OrderStatus.PENDING_PAYMENT
    if tab == "confirmed":
        return _confirmed_predicate()
    if tab == "preparing":
        return _preparing_predicate()
    if tab == "ready":
        return order_ready_for_pickup_predicate()
    if tab == "delivered":
        return _delivered_predicate()
    if tab == "cancelled":
        return Order.status.in_((OrderStatus.CANCELLED, OrderStatus.EXPIRED))
    return literal(True)


def _payment_filter_predicate(value: str):
    relevant_attempt_id = _relevant_payment_attempt_id()
    if value == "review":
        return exists(
            select(PaymentProof.id)
            .join(PaymentAttempt, PaymentAttempt.id == PaymentProof.payment_attempt_id)
            .where(
                PaymentAttempt.order_id == Order.id,
                PaymentAttempt.id == relevant_attempt_id,
                PaymentAttempt.status == PaymentStatus.PROCESSING,
                PaymentProof.status == PaymentProofStatus.PENDING_REVIEW,
            )
        )
    target = {
        "approved": PaymentStatus.APPROVED,
        "rejected": PaymentStatus.REJECTED,
        "awaiting": PaymentStatus.AWAITING_PROOF,
    }.get(value)
    if target is None:
        return literal(True)
    return exists(
        select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == Order.id,
            PaymentAttempt.id == relevant_attempt_id,
            PaymentAttempt.status == target,
        )
    )


def _search_predicate(query: str):
    if not query:
        return literal(True)
    contains = f"%{_escaped(query)}%"
    conditions = (
        Order.order_number.ilike(contains, escape="\\"),
        User.full_name.ilike(contains, escape="\\"),
        User.email.ilike(contains, escape="\\"),
        User.public_code.ilike(contains, escape="\\"),
        exists(
            select(SellerOrder.id).where(
                SellerOrder.order_id == Order.id,
                SellerOrder.seller_order_number.ilike(contains, escape="\\"),
            )
        ),
        exists(
            select(OrderPackage.id)
            .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
            .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
            .where(
                SellerOrder.order_id == Order.id,
                or_(
                    OrderPackage.package_code.ilike(contains, escape="\\"),
                    OrderPackage.barcode.ilike(contains, escape="\\"),
                ),
            )
        ),
        exists(
            select(SellerInboundPackage.id)
            .join(SellerOrder, SellerOrder.id == SellerInboundPackage.seller_order_id)
            .where(
                SellerOrder.order_id == Order.id,
                or_(
                    SellerInboundPackage.package_code.ilike(contains, escape="\\"),
                    SellerInboundPackage.barcode.ilike(contains, escape="\\"),
                ),
            )
        ),
    )
    user_match = _PUBLIC_USER_RE.fullmatch(query)
    if user_match:
        return or_(*conditions, User.registration_number == int(user_match.group(1)))
    return or_(*conditions)


def _search_rank(query: str):
    if not query:
        return literal(0)
    prefix = f"{_escaped(query)}%"
    contains = f"%{_escaped(query)}%"
    exact = or_(
        func.lower(Order.order_number) == query.casefold(),
        func.lower(User.public_code) == query.casefold(),
        func.lower(User.email) == query.casefold(),
    )
    starts = or_(
        Order.order_number.ilike(prefix, escape="\\"),
        User.full_name.ilike(prefix, escape="\\"),
        User.email.ilike(prefix, escape="\\"),
        User.public_code.ilike(prefix, escape="\\"),
    )
    return case((exact, 0), (starts, 1), else_=2)


def _filter_predicates(
    *, payment_filter: str, fulfillment_filter: str,
    attention_filter: str, date_filter: str, query: str,
    now: datetime,
) -> tuple:
    values = [_payment_filter_predicate(payment_filter), _search_predicate(query)]
    if fulfillment_filter:
        status = SellerOrderStatus.PICKING if fulfillment_filter == "picking" else SellerOrderStatus.PACKED
        values.append(exists(select(SellerOrder.id).where(SellerOrder.order_id == Order.id, SellerOrder.status == status)))
    if attention_filter == "inbound-overdue":
        values.append(exists(select(SellerOrder.id).where(SellerOrder.order_id == Order.id, partner_order_overdue_predicate(now))))
    if date_filter == "today":
        start, end, _, _ = ecuador_comparison_windows(now)
        values.extend((Order.created_at >= start, Order.created_at < end))
    return tuple(values)


def _fulfillment_view(
    *, order_status: OrderStatus, seller_statuses: tuple[SellerOrderStatus, ...],
    seller_decisions: tuple[SellerOrderDecisionStatus | None, ...],
    package_statuses: tuple[PackageStatus, ...], item_count: int,
) -> AdminStatusView:
    if order_status in {OrderStatus.CANCELLED, OrderStatus.EXPIRED}:
        return AdminStatusView("cancelled", "Cancelado", "muted")
    if order_status == OrderStatus.PENDING_PAYMENT:
        return AdminStatusView("pending-payment", "Pendiente de pago", "warning")
    if item_count and len(package_statuses) == item_count and all(status == PackageStatus.HANDED_OVER for status in package_statuses):
        return AdminStatusView("delivered", "Entregado", "success")
    if item_count and len(package_statuses) == item_count and all(status in {PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER} for status in package_statuses) and any(status == PackageStatus.READY_FOR_PICKUP for status in package_statuses):
        return AdminStatusView("ready", "Listo para retirar", "success")
    if (
        item_count
        and len(package_statuses) == item_count
        and all(
            status in {
                PackageStatus.PACKED,
                PackageStatus.READY_FOR_PICKUP,
                PackageStatus.HANDED_OVER,
            }
            for status in package_statuses
        )
    ):
        return AdminStatusView("packed", "Empacado", "warning")
    if package_statuses or any(
        status == SellerOrderStatus.PICKING for status in seller_statuses
    ):
        return AdminStatusView("picking", "Picking", "warning")
    if seller_statuses and all(
        status in {
            SellerOrderStatus.PACKED,
            SellerOrderStatus.READY_FOR_PICKUP,
            SellerOrderStatus.COMPLETED,
        }
        for status in seller_statuses
    ):
        return AdminStatusView("packed", "Empacado", "warning")
    if any(decision == SellerOrderDecisionStatus.APPROVED for decision in seller_decisions):
        return AdminStatusView("preparing", "En preparación", "warning")
    return AdminStatusView("confirmed", "Confirmado", "info")


def get_admin_orders_page(
    session: Session, *, tab: str | None = None, query: str | None = None,
    payment: str | None = None, fulfillment: str | None = None,
    attention: str | None = None, date: str | None = None,
    page: str | int | None = None, page_size: str | int | None = None,
    now: datetime | None = None,
) -> AdminOrdersPage:
    effective_now = _utc(now or datetime.now(timezone.utc))
    active_tab = _normalize_choice(tab, VALID_TABS, "all")
    normalized_query = " ".join((query or "").strip().split())[:MAX_QUERY_LENGTH]
    payment_filter = _normalize_choice(payment, VALID_PAYMENT_FILTERS, "")
    fulfillment_filter = _normalize_choice(fulfillment, VALID_FULFILLMENT_FILTERS, "")
    attention_filter = _normalize_choice(attention, VALID_ATTENTION_FILTERS, "")
    date_filter = "today" if (date or "").strip().lower() == "today" else ""
    normalized_page = _normalize_page(page)
    normalized_size = _normalize_page_size(page_size)
    common = _filter_predicates(
        payment_filter=payment_filter, fulfillment_filter=fulfillment_filter,
        attention_filter=attention_filter, date_filter=date_filter,
        query=normalized_query, now=effective_now,
    )
    labels = {
        "all": "Todos", "pending-payment": "Pendientes de pago",
        "confirmed": "Confirmados", "preparing": "En preparación",
        "ready": "Listos para retirar", "delivered": "Entregados",
        "cancelled": "Cancelados",
    }
    tabs = tuple(
        AdminOrderTab(key, labels[key], int(session.scalar(
            select(func.count(Order.id)).select_from(Order).join(User, User.id == Order.buyer_id).where(*common, _tab_predicate(key))
        ) or 0))
        for key in VALID_TABS
    )
    total_items = next(item.count for item in tabs if item.key == active_tab)
    total_pages = max(1, math.ceil(total_items / normalized_size))
    normalized_page = min(normalized_page, total_pages)

    line_count_sq = (
        select(func.count(OrderItem.id)).join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id).correlate(Order).scalar_subquery()
    )
    unit_count_sq = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    store_count_sq = (
        select(func.count(func.distinct(SellerOrder.store_id)))
        .where(SellerOrder.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    package_count_sq = (
        select(func.count(OrderPackage.id)).join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id, OrderPackage.status != PackageStatus.CANCELLED)
        .correlate(Order).scalar_subquery()
    )
    completed_count_sq = (
        select(func.count(OrderPackage.id)).join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == Order.id, OrderPackage.status.in_((PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER)))
        .correlate(Order).scalar_subquery()
    )
    records = session.execute(
        select(
            Order,
            User,
            line_count_sq,
            unit_count_sq,
            store_count_sq,
            package_count_sq,
            completed_count_sq,
        )
        .join(User, User.id == Order.buyer_id)
        .where(*common, _tab_predicate(active_tab))
        .order_by(_search_rank(normalized_query), Order.updated_at.desc(), Order.order_number)
        .offset((normalized_page - 1) * normalized_size).limit(normalized_size)
    ).all()
    order_ids = tuple(order.id for order, *_ in records)
    payments = _payment_records(session, order_ids)
    seller_rows = session.execute(select(SellerOrder.order_id, SellerOrder.status, SellerOrder.decision_status).where(SellerOrder.order_id.in_(order_ids))).all() if order_ids else ()
    package_rows = session.execute(
        select(SellerOrder.order_id, OrderPackage.status)
        .join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        .join(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
        .where(SellerOrder.order_id.in_(order_ids), OrderPackage.status != PackageStatus.CANCELLED)
    ).all() if order_ids else ()
    seller_statuses: dict[uuid.UUID, list] = defaultdict(list)
    seller_decisions: dict[uuid.UUID, list] = defaultdict(list)
    package_statuses: dict[uuid.UUID, list] = defaultdict(list)
    for order_id, status, decision in seller_rows:
        seller_statuses[order_id].append(status)
        seller_decisions[order_id].append(decision)
    for order_id, status in package_rows:
        package_statuses[order_id].append(status)
    rows = tuple(
        AdminOrderRow(
            order.order_number, buyer.full_name, buyer.public_account_code,
            int(unit_count or 0), int(store_count or 0), order.grand_total,
            order.currency, _payment_view(select_relevant_payment(payments[order.id])),
            _fulfillment_view(
                order_status=order.status,
                seller_statuses=tuple(seller_statuses[order.id]),
                seller_decisions=tuple(seller_decisions[order.id]),
                package_statuses=tuple(package_statuses[order.id]),
                item_count=int(line_count or 0),
            ),
            int(package_count or 0), int(line_count or 0), int(completed_count or 0),
            order.created_at, _local_label(order.created_at),
        )
        for (
            order,
            buyer,
            line_count,
            unit_count,
            store_count,
            package_count,
            completed_count,
        ) in records
    )
    return AdminOrdersPage(
        rows, tabs, active_tab, normalized_query, payment_filter,
        fulfillment_filter, attention_filter, date_filter,
        normalized_page, normalized_size, total_items, total_pages,
        normalized_page > 1, normalized_page < total_pages,
    )


def _variant_title(snapshot: dict | None) -> str | None:
    data = snapshot or {}
    direct = data.get("title") or data.get("name")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    options = data.get("options")
    if isinstance(options, dict):
        values = [str(value).strip() for value in options.values() if str(value).strip()]
        return " / ".join(values) or None
    return None


def _package_status(status: PackageStatus) -> AdminStatusView:
    values = {
        PackageStatus.CREATED: ("Creado", "info"),
        PackageStatus.PACKED: ("Empacado", "warning"),
        PackageStatus.READY_FOR_PICKUP: ("Listo para retirar", "success"),
        PackageStatus.HANDED_OVER: ("Entregado", "success"),
        PackageStatus.CANCELLED: ("Cancelado", "muted"),
    }
    label, tone = values[status]
    return AdminStatusView(status.value.lower(), label, tone)


def _seller_status(status: SellerOrderStatus) -> AdminStatusView:
    values = {
        SellerOrderStatus.PENDING_PAYMENT: ("Pendiente de pago", "warning"),
        SellerOrderStatus.CONFIRMED: ("Confirmado", "info"),
        SellerOrderStatus.PICKING: ("Picking", "warning"),
        SellerOrderStatus.PACKED: ("Empacado", "warning"),
        SellerOrderStatus.READY_FOR_PICKUP: ("Listo", "success"),
        SellerOrderStatus.COMPLETED: ("Entregado", "success"),
        SellerOrderStatus.CANCELLED: ("Cancelado", "muted"),
    }
    label, tone = values[status]
    return AdminStatusView(status.value.lower(), label, tone)


def _inbound_package_status(
    package: SellerInboundPackage,
    state: LogisticsPackageState | None,
) -> AdminStatusView:
    if state is not None:
        if state.is_deviated or state.status == LogisticsPackageStatus.DEVIATED:
            return AdminStatusView("deviated", "Desviado", "danger")
        values = {
            LogisticsPackageStatus.AT_POINT: ("at_point", "En punto ECUVEL", "success"),
            LogisticsPackageStatus.ASSIGNED: ("assigned", "Por recoger", "warning"),
            LogisticsPackageStatus.IN_TRANSIT: ("in_transit", "En tránsito", "info"),
            LogisticsPackageStatus.DELIVERED: ("delivered", "Entregado", "success"),
        }
        code, label, tone = values[state.status]
        return AdminStatusView(code, label, tone)
    values = {
        SellerInboundPackageStatus.CREATED: ("created", "Creado", "muted"),
        SellerInboundPackageStatus.READY_FOR_DROPOFF: ("ready", "Listo para entregar", "warning"),
        SellerInboundPackageStatus.RECEIVED_BY_ECUVEL: ("received", "Recibido por ECUVEL", "success"),
        SellerInboundPackageStatus.CANCELLED: ("cancelled", "Cancelado", "muted"),
    }
    code, label, tone = values[package.status]
    return AdminStatusView(code, label, tone)


def get_admin_order_detail(session: Session, *, order_number: str) -> AdminOrderDetail | None:
    record = session.execute(
        select(Order, User).join(User, User.id == Order.buyer_id).where(Order.order_number == order_number.strip())
    ).one_or_none()
    if record is None:
        return None
    order, buyer = record
    seller_records = session.execute(
        select(SellerOrder, Store).join(Store, Store.id == SellerOrder.store_id)
        .where(SellerOrder.order_id == order.id).order_by(Store.name, SellerOrder.id)
    ).all()
    seller_ids = tuple(seller.id for seller, _ in seller_records)
    items = list(session.scalars(select(OrderItem).where(OrderItem.seller_order_id.in_(seller_ids)).order_by(OrderItem.created_at, OrderItem.id))) if seller_ids else []
    item_ids = tuple(item.id for item in items)
    package_records = session.execute(
        select(OrderPackage, OrderItem)
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .options(
            selectinload(OrderPackage.pickup_location),
            selectinload(OrderPackage.packed_by),
            selectinload(OrderPackage.ready_by),
            selectinload(OrderPackage.handed_over_by),
        )
        .where(OrderPackage.order_item_id.in_(item_ids)).order_by(OrderPackage.created_at, OrderPackage.id)
    ).all() if item_ids else ()
    inbound_records = session.execute(
        select(SellerInboundPackage, LogisticsPackageState)
        .outerjoin(
            LogisticsPackageState,
            LogisticsPackageState.seller_inbound_package_id
            == SellerInboundPackage.id,
        )
        .options(
            selectinload(SellerInboundPackage.received_location),
            selectinload(LogisticsPackageState.current_warehouse),
            selectinload(LogisticsPackageState.current_location),
            selectinload(LogisticsPackageState.custodian_warehouse),
            selectinload(LogisticsPackageState.custodian_user),
        )
        .where(SellerInboundPackage.seller_order_id.in_(seller_ids))
        .order_by(SellerInboundPackage.created_at, SellerInboundPackage.id)
    ).all() if seller_ids else ()
    records = _payment_records(session, (order.id,))[order.id]
    payment = _payment_view(select_relevant_payment(records))
    by_seller = defaultdict(int)
    for item in items:
        by_seller[item.seller_order_id] += item.quantity
    lines = tuple(AdminOrderLineView(
        item.product_name_snapshot, item.seller_name_snapshot,
        item.seller_sku_snapshot, item.image_url_snapshot,
        _variant_title(item.variant_snapshot), item.unit_price, item.quantity,
        item.line_total,
    ) for item in items)
    stores = tuple(AdminStoreOrderView(
        store.name, store.public_store_code, by_seller[seller.id], _seller_status(seller.status)
    ) for seller, store in seller_records)
    store_names = {seller.id: store.name for seller, store in seller_records}
    inbound_packages = tuple(
        AdminInboundPackageView(
            package.package_code,
            package.barcode,
            store_names.get(package.seller_order_id, "Tienda"),
            _inbound_package_status(package, state),
            (
                state.current_warehouse.name
                if state and state.current_warehouse
                else "En tránsito"
                if state and state.status == LogisticsPackageStatus.IN_TRANSIT
                else package.received_location.code
                if package.received_location
                else "Pendiente de recepción"
            ),
            (
                state.custodian_user.full_name
                if state and state.custodian_user
                else state.custodian_warehouse.name
                if state and state.custodian_warehouse
                else "Sin custodia ECUVEL"
            ),
            state is not None,
        )
        for package, state in inbound_records
    )
    packages = tuple(AdminPackageView(
        package.package_code, package.barcode, _package_status(package.status),
        item.product_name_snapshot, _variant_title(item.variant_snapshot),
        package.quantity,
        package.pickup_location.code if package.pickup_location else None,
        package.packed_at, package.ready_at, package.handed_over_at,
    ) for package, item in package_records)
    seller_status_values = tuple(seller.status for seller, _ in seller_records)
    seller_decisions = tuple(seller.decision_status for seller, _ in seller_records)
    fulfillment = _fulfillment_view(
        order_status=order.status, seller_statuses=seller_status_values,
        seller_decisions=seller_decisions,
        package_statuses=tuple(package.status for package, _ in package_records),
        item_count=len(items),
    )
    paid_at = payment.approved_at
    preparation_values = [seller.approved_at for seller, _ in seller_records if seller.approved_at]
    preparation_at = min(preparation_values) if preparation_values else None
    ready_values = [package.ready_at for package, _ in package_records if package.ready_at]
    ready_at = max(ready_values) if len(ready_values) == len(items) and items else None
    delivered_values = [package.handed_over_at for package, _ in package_records if package.handed_over_at]
    delivered_at = max(delivered_values) if len(delivered_values) == len(items) and items else None
    milestones = (
        ("created", "Creado", "shopping-bag", order.created_at),
        ("paid", "Pago confirmado", "badge-check", paid_at),
        ("preparing", "Preparación", "package-check", preparation_at),
        ("ready", "Listo para retirar", "map-pin-check", ready_at),
        ("delivered", "Entregado", "circle-check", delivered_at),
    )
    reached_indices = [index for index, (_, _, _, value) in enumerate(milestones) if value]
    active_index = max(reached_indices, default=0)
    timeline = tuple(AdminTimelineStep(key, label, icon, value is not None, index == active_index, value) for index, (key, label, icon, value) in enumerate(milestones))
    history_values: list[AdminHistoryItem] = [AdminHistoryItem(order.created_at, "Pedido creado")]
    if paid_at:
        history_values.append(AdminHistoryItem(paid_at, "Pago confirmado"))
    for seller, store in seller_records:
        if seller.approved_at:
            history_values.append(AdminHistoryItem(seller.approved_at, f"Pedido aceptado por {store.name}"))
        if seller.rejected_at:
            history_values.append(AdminHistoryItem(seller.rejected_at, f"Pedido rechazado por {store.name}"))
    for package, _ in package_records:
        if package.packed_at:
            history_values.append(AdminHistoryItem(package.packed_at, f"{package.package_code} empacado", package.packed_by.full_name if package.packed_by else None))
        if package.ready_at:
            history_values.append(AdminHistoryItem(package.ready_at, f"{package.package_code} listo para retirar", package.ready_by.full_name if package.ready_by else None))
        if package.handed_over_at:
            history_values.append(AdminHistoryItem(package.handed_over_at, f"{package.package_code} entregado", package.handed_over_by.full_name if package.handed_over_by else None))
    history_values.sort(key=lambda item: _utc(item.timestamp), reverse=True)
    return AdminOrderDetail(
        order.order_number, fulfillment, order.created_at, buyer.full_name,
        buyer.public_account_code, buyer.email, buyer.phone, order.currency,
        order.subtotal, order.discount_total, order.shipping_total,
        order.tax_total, order.grand_total, sum(item.quantity for item in items),
        lines, stores, inbound_packages, packages, payment,
        timeline, tuple(history_values),
    )


def _evidence_result(value: bool | None) -> tuple[str, str]:
    if value is True:
        return "Coincide", "success"
    if value is False:
        return "Revisar", "danger"
    return "No detectado", "muted"


def get_admin_payment_review(
    session: Session, *, order_number: str, bank_name: str | None,
    account_suffix: str | None,
) -> AdminPaymentReview | None:
    record = session.execute(
        select(Order, User).join(User, User.id == Order.buyer_id).where(Order.order_number == order_number.strip())
    ).one_or_none()
    if record is None:
        return None
    order, buyer = record
    selected = select_relevant_payment(_payment_records(session, (order.id,))[order.id])
    if selected is None or selected.proof is None:
        return None
    payment = _payment_view(selected)
    analysis = session.scalar(select(PaymentProofAnalysis).where(PaymentProofAnalysis.payment_proof_id == selected.proof.id))
    evidence: list[AdminEvidenceRow] = []
    findings: list[str] = []
    if analysis:
        amount_result, amount_tone = _evidence_result(analysis.amount_matches)
        evidence.append(AdminEvidenceRow(
            "Monto", f"{order.grand_total:.2f} {order.currency}",
            f"{analysis.amount_detected:.2f} {order.currency}" if analysis.amount_detected is not None else "No detectado",
            amount_result, amount_tone,
        ))
        bank_result, bank_tone = _evidence_result(analysis.bank_is_recognized)
        evidence.append(AdminEvidenceRow(
            "Banco", bank_name or "No configurado",
            analysis.bank_name_detected or "No detectado", bank_result, bank_tone,
        ))
        account_result, account_tone = _evidence_result(analysis.destination_account_matches)
        evidence.append(AdminEvidenceRow(
            "Cuenta destino", f"Terminación {account_suffix}" if account_suffix else "No configurada",
            f"Terminación {analysis.destination_account_suffix}" if analysis.destination_account_suffix else "No detectado",
            account_result, account_tone,
        ))
        date_result, date_tone = _evidence_result(analysis.date_is_plausible)
        evidence.append(AdminEvidenceRow(
            "Fecha", "Fecha válida para el pedido",
            _local_label(analysis.transaction_at_detected) if analysis.transaction_at_detected else "No detectada",
            date_result, date_tone,
        ))
        for finding in analysis.findings or ():
            if isinstance(finding, dict):
                message = finding.get("message") or finding.get("detail") or finding.get("code")
                if message:
                    findings.append(str(message))
    return AdminPaymentReview(
        order.order_number, buyer.full_name, buyer.public_account_code,
        order.grand_total, order.currency, payment,
        analysis.processing_status.value if analysis else None,
        analysis.outcome.value if analysis and analysis.outcome else None,
        tuple(evidence), tuple(findings), bank_name, account_suffix,
    )
