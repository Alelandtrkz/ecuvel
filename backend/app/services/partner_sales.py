from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import Date, String, cast, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Order,
    OrderItem,
    PaymentAttempt,
    SellerOrder,
    SellerPayout,
    SellerPayoutItem,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
)
from app.models.enums import (
    OrderStatus,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStatus,
    StoreStatus,
)
from app.services.partner_order_workflow import resolve_partner_order_workflow
from app.services.payment_reporting import approved_payment_dates_subquery


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
ZERO = Decimal("0.00")
SALES_PERIODS = ("this_month", "previous_month", "last_90_days")
SALES_PERIOD_LABELS = {
    "this_month": "Este mes",
    "previous_month": "Mes anterior",
    "last_90_days": "Últimos 90 días",
}
PAYOUT_STATUS_LABELS = {
    SellerPayoutStatus.SCHEDULED: "Programado",
    SellerPayoutStatus.PAID: "Pagado",
    SellerPayoutStatus.ON_HOLD: "En revisión",
    SellerPayoutStatus.CANCELLED: "Cancelado",
}
PAYOUT_STATUS_TONES = {
    SellerPayoutStatus.SCHEDULED: "scheduled",
    SellerPayoutStatus.PAID: "paid",
    SellerPayoutStatus.ON_HOLD: "review",
    SellerPayoutStatus.CANCELLED: "cancelled",
}
SALES_ROLES = {
    StoreMemberRole.OWNER,
    StoreMemberRole.ADMINISTRATOR,
    StoreMemberRole.FINANCE_OPERATOR,
}
MONTH_ABBR_ES = (
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
)


class PartnerSalesError(Exception):
    pass


class PartnerSalesAccessError(PartnerSalesError):
    pass


class PartnerPayoutNotFoundError(PartnerSalesAccessError):
    pass


@dataclass(frozen=True, slots=True)
class PartnerSalesStoreAccess:
    store_id: uuid.UUID
    store_name: str
    role: StoreMemberRole


@dataclass(frozen=True, slots=True)
class SalesPeriod:
    key: str
    label: str
    starts_at: datetime
    ends_at: datetime
    comparison_starts_at: datetime
    comparison_ends_at: datetime


@dataclass(frozen=True, slots=True)
class SalesMetrics:
    gross_sales: Decimal
    seller_net: Decimal
    paid_by_ecuvel: Decimal
    pending_payout: Decimal
    order_count: int
    units_sold: int
    average_ticket: Decimal
    commission_total: Decimal
    gross_change_percent: Decimal | None
    next_payout_at: datetime | None

    @property
    def next_payout_label(self) -> str | None:
        if self.next_payout_at is None:
            return None
        return _date_label(self.next_payout_at)


@dataclass(frozen=True, slots=True)
class SalesChartPoint:
    key: str
    label: str
    gross: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class TopProductView:
    offer_id: uuid.UUID
    product_name: str
    seller_sku: str
    variant_label: str | None
    image_url: str
    units: int
    net_revenue: Decimal


@dataclass(frozen=True, slots=True)
class RecentSaleView:
    seller_order_number: str
    relative_date: str
    status_label: str
    status_tone: str
    total: Decimal


@dataclass(frozen=True, slots=True)
class PartnerPayoutRowView:
    payout_id: uuid.UUID | None
    reference: str | None
    order_count: int
    net_total: Decimal
    date_label: str
    status_key: str
    status_label: str
    status_tone: str
    can_open: bool


@dataclass(frozen=True, slots=True)
class PartnerPayoutDetailView:
    payout_id: uuid.UUID
    reference: str
    status: str
    status_label: str
    status_tone: str
    date_label: str
    destination_label: str
    order_count: int
    currency: str
    gross_sales_total: Decimal
    discount_total: Decimal
    commission_total: Decimal
    net_total: Decimal
    receipt_available: bool


@dataclass(frozen=True, slots=True)
class SalesExportRow:
    seller_order_number: str
    approved_at: datetime
    products: str
    units: int
    subtotal: Decimal
    discounts: Decimal
    commission: Decimal
    net: Decimal
    logistics_status: str
    payout_status: str
    eligible_at: datetime | None
    payout_reference: str | None
    paid_at: datetime | None


@dataclass(frozen=True, slots=True)
class PartnerSalesPage:
    store: PartnerSalesStoreAccess
    period: SalesPeriod
    period_options: tuple[tuple[str, str], ...]
    metrics: SalesMetrics
    chart_payload: dict[str, list[dict[str, str]]]
    top_products_by_units: tuple[TopProductView, ...]
    top_products_by_revenue: tuple[TopProductView, ...]
    recent_sales: tuple[RecentSaleView, ...]
    payout_rows: tuple[PartnerPayoutRowView, ...]
    payout_counts: dict[str, int]

    @property
    def has_sales(self) -> bool:
        return self.metrics.order_count > 0


def require_partner_sales_store(
    session: Session, user_id: uuid.UUID
) -> PartnerSalesStoreAccess:
    row = session.execute(
        select(Store, StoreMember)
        .join(StoreMember, StoreMember.store_id == Store.id)
        .join(StoreOnboarding, StoreOnboarding.store_id == Store.id)
        .join(
            StoreContractAcceptance,
            StoreContractAcceptance.onboarding_id == StoreOnboarding.id,
        )
        .where(
            StoreMember.user_id == user_id,
            StoreMember.is_active.is_(True),
            StoreMember.role.in_(SALES_ROLES),
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
            StoreContractAcceptance.status == StoreContractAcceptanceStatus.ACCEPTED,
        )
        .order_by(Store.created_at, Store.id)
        .limit(1)
    ).first()
    if row is None:
        raise PartnerSalesAccessError(
            "No tienes acceso financiero a una tienda habilitada."
        )
    store, member = row
    return PartnerSalesStoreAccess(
        store_id=store.id, store_name=store.name, role=member.role
    )


def _start_of_month(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month_start(value: datetime) -> datetime:
    return _start_of_month(_start_of_month(value) - timedelta(days=1))


def resolve_sales_period(
    value: str | None, *, now: datetime | None = None
) -> SalesPeriod:
    key = (value or "this_month").strip().lower()
    if key not in SALES_PERIODS:
        key = "this_month"
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None or effective_now.utcoffset() is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    local_now = effective_now.astimezone(ECUADOR_TZ)
    month_start = _start_of_month(local_now)
    if key == "this_month":
        start_local = month_start
        end_local = _start_of_month(month_start + timedelta(days=32))
        comparison_start = _previous_month_start(month_start)
        comparison_end = month_start
    elif key == "previous_month":
        start_local = _previous_month_start(month_start)
        end_local = month_start
        comparison_end = start_local
        comparison_start = _previous_month_start(start_local)
    else:
        end_local = local_now + timedelta(microseconds=1)
        start_local = end_local - timedelta(days=90)
        comparison_end = start_local
        comparison_start = comparison_end - timedelta(days=90)
    return SalesPeriod(
        key=key,
        label=SALES_PERIOD_LABELS[key],
        starts_at=start_local.astimezone(timezone.utc),
        ends_at=end_local.astimezone(timezone.utc),
        comparison_starts_at=comparison_start.astimezone(timezone.utc),
        comparison_ends_at=comparison_end.astimezone(timezone.utc),
    )


def _valid_sale_conditions(store_id: uuid.UUID):
    return (
        SellerOrder.store_id == store_id,
        SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
        SellerOrder.status != SellerOrderStatus.CANCELLED,
        Order.status.not_in({OrderStatus.CANCELLED, OrderStatus.EXPIRED}),
    )


def _sales_totals(
    session: Session,
    *,
    store_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[Decimal, Decimal, Decimal, int]:
    payments = approved_payment_dates_subquery()
    row = session.execute(
        select(
            func.coalesce(func.sum(SellerOrder.subtotal), ZERO),
            func.coalesce(func.sum(SellerOrder.seller_net_total), ZERO),
            func.coalesce(func.sum(SellerOrder.commission_total), ZERO),
            func.count(SellerOrder.id),
        )
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            payments.c.approved_at >= starts_at,
            payments.c.approved_at < ends_at,
        )
    ).one()
    return Decimal(row[0]), Decimal(row[1]), Decimal(row[2]), int(row[3] or 0)


def _units_sold(
    session: Session,
    *,
    store_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> int:
    payments = approved_payment_dates_subquery()
    value = session.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            payments.c.approved_at >= starts_at,
            payments.c.approved_at < ends_at,
        )
    )
    return int(value or 0)


def _paid_total(
    session: Session,
    *,
    store_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(SellerPayout.net_total), ZERO)).where(
            SellerPayout.store_id == store_id,
            SellerPayout.status == SellerPayoutStatus.PAID,
            SellerPayout.paid_at >= starts_at,
            SellerPayout.paid_at < ends_at,
        )
    )
    return Decimal(value or ZERO)


def _pending_total(session: Session, *, store_id: uuid.UUID) -> Decimal:
    paid_item = exists(
        select(SellerPayoutItem.seller_order_id)
        .join(SellerPayout, SellerPayout.id == SellerPayoutItem.payout_id)
        .where(
            SellerPayoutItem.seller_order_id == SellerOrder.id,
            SellerPayout.status == SellerPayoutStatus.PAID,
        )
    )
    approved_payment = exists(
        select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == SellerOrder.order_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
        )
    )
    value = session.scalar(
        select(func.coalesce(func.sum(SellerOrder.seller_net_total), ZERO))
        .join(Order, Order.id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            approved_payment,
            ~paid_item,
        )
    )
    return Decimal(value or ZERO)


def _next_payout(session: Session, *, store_id: uuid.UUID) -> datetime | None:
    return session.scalar(
        select(func.min(SellerPayout.scheduled_for)).where(
            SellerPayout.store_id == store_id,
            SellerPayout.status == SellerPayoutStatus.SCHEDULED,
        )
    )


def _metrics(
    session: Session, *, store_id: uuid.UUID, period: SalesPeriod
) -> SalesMetrics:
    gross, net, commission, order_count = _sales_totals(
        session,
        store_id=store_id,
        starts_at=period.starts_at,
        ends_at=period.ends_at,
    )
    previous_gross, _previous_net, _previous_commission, _previous_count = _sales_totals(
        session,
        store_id=store_id,
        starts_at=period.comparison_starts_at,
        ends_at=period.comparison_ends_at,
    )
    change = None
    if previous_gross > ZERO:
        change = (((gross - previous_gross) / previous_gross) * Decimal("100")).quantize(
            Decimal("0.1")
        )
    units = _units_sold(
        session,
        store_id=store_id,
        starts_at=period.starts_at,
        ends_at=period.ends_at,
    )
    average = (gross / order_count).quantize(Decimal("0.01")) if order_count else ZERO
    return SalesMetrics(
        gross_sales=gross,
        seller_net=net,
        paid_by_ecuvel=_paid_total(
            session,
            store_id=store_id,
            starts_at=period.starts_at,
            ends_at=period.ends_at,
        ),
        pending_payout=_pending_total(session, store_id=store_id),
        order_count=order_count,
        units_sold=units,
        average_ticket=average,
        commission_total=commission,
        gross_change_percent=change,
        next_payout_at=_next_payout(session, store_id=store_id),
    )


def _bucket_label(value: datetime, granularity: str) -> str:
    local = value.replace(tzinfo=ECUADOR_TZ) if value.tzinfo is None else value.astimezone(ECUADOR_TZ)
    if granularity == "month":
        return f"{MONTH_ABBR_ES[local.month - 1]} {local.year}"
    if granularity == "week":
        return f"Semana {local.day:02d} {MONTH_ABBR_ES[local.month - 1]}"
    return f"{local.day:02d} {MONTH_ABBR_ES[local.month - 1]}"


def _chart_points(
    session: Session,
    *,
    store_id: uuid.UUID,
    period: SalesPeriod,
    granularity: str,
) -> tuple[SalesChartPoint, ...]:
    payments = approved_payment_dates_subquery()
    local_payment = func.timezone("America/Guayaquil", payments.c.approved_at)
    bucket = func.date_trunc(granularity, local_payment).label("bucket")
    rows = session.execute(
        select(
            bucket,
            func.coalesce(func.sum(SellerOrder.subtotal), ZERO),
            func.coalesce(func.sum(SellerOrder.seller_net_total), ZERO),
        )
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            payments.c.approved_at >= period.starts_at,
            payments.c.approved_at < period.ends_at,
        )
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    return tuple(
        SalesChartPoint(
            key=bucket_value.isoformat(),
            label=_bucket_label(bucket_value, granularity),
            gross=Decimal(gross),
            net=Decimal(net),
        )
        for bucket_value, gross, net in rows
    )


def _variant_label(value: dict | None) -> str | None:
    if not isinstance(value, dict):
        return None
    options = value.get("options") if isinstance(value.get("options"), dict) else value
    labels = []
    for option in options.values():
        if isinstance(option, dict):
            label = option.get("label") or option.get("value")
        else:
            label = option
        if label not in (None, ""):
            labels.append(str(label))
    return " / ".join(labels) or None


def _top_products(
    session: Session,
    *,
    store_id: uuid.UUID,
    period: SalesPeriod,
    placeholder_image: str,
    order_by: str,
) -> tuple[TopProductView, ...]:
    payments = approved_payment_dates_subquery()
    commission = func.coalesce(OrderItem.commission_amount_snapshot, ZERO)
    net_line = (
        OrderItem.unit_price * OrderItem.quantity
        - OrderItem.discount_amount
        - commission
    )
    aggregate = (
        select(
            OrderItem.offer_id,
            func.sum(OrderItem.quantity).label("units"),
            func.sum(net_line).label("net_revenue"),
        )
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            payments.c.approved_at >= period.starts_at,
            payments.c.approved_at < period.ends_at,
        )
        .group_by(OrderItem.offer_id)
    )
    sort_column = (
        func.sum(net_line).desc()
        if order_by == "revenue"
        else func.sum(OrderItem.quantity).desc()
    )
    totals = session.execute(
        aggregate.order_by(sort_column, OrderItem.offer_id).limit(5)
    ).all()
    if not totals:
        return ()
    offer_ids = [row.offer_id for row in totals]
    snapshots = session.execute(
        select(
            OrderItem.offer_id,
            OrderItem.product_name_snapshot,
            OrderItem.seller_sku_snapshot,
            OrderItem.variant_snapshot,
            OrderItem.image_url_snapshot,
        )
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(
            *_valid_sale_conditions(store_id),
            OrderItem.offer_id.in_(offer_ids),
            payments.c.approved_at >= period.starts_at,
            payments.c.approved_at < period.ends_at,
        )
        .distinct(OrderItem.offer_id)
        .order_by(OrderItem.offer_id, OrderItem.created_at.desc(), OrderItem.id.desc())
    ).all()
    snapshot_by_offer = {row.offer_id: row for row in snapshots}
    views = []
    for total in totals:
        snapshot = snapshot_by_offer[total.offer_id]
        views.append(
            TopProductView(
                offer_id=total.offer_id,
                product_name=snapshot.product_name_snapshot,
                seller_sku=snapshot.seller_sku_snapshot,
                variant_label=_variant_label(snapshot.variant_snapshot),
                image_url=snapshot.image_url_snapshot or placeholder_image,
                units=int(total.units or 0),
                net_revenue=Decimal(total.net_revenue or ZERO),
            )
        )
    return tuple(views)


def _relative_date(value: datetime, *, now: datetime | None = None) -> str:
    effective_now = now or datetime.now(timezone.utc)
    local_value = value.astimezone(ECUADOR_TZ)
    delta = effective_now.astimezone(ECUADOR_TZ) - local_value
    if delta < timedelta(hours=24):
        hours = max(1, int(delta.total_seconds() // 3600))
        return f"Hace {hours} h"
    if delta < timedelta(days=2):
        return "Ayer"
    return _date_label(local_value)


def _date_label(value: datetime) -> str:
    local = value.astimezone(ECUADOR_TZ)
    return f"{local.day:02d} {MONTH_ABBR_ES[local.month - 1]} {local.year}"


def _recent_sales(
    session: Session, *, store_id: uuid.UUID
) -> tuple[RecentSaleView, ...]:
    payments = approved_payment_dates_subquery()
    rows = session.execute(
        select(SellerOrder, payments.c.approved_at)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .options(selectinload(SellerOrder.inbound_packages))
        .where(SellerOrder.store_id == store_id)
        .order_by(payments.c.approved_at.desc(), SellerOrder.id.desc())
        .limit(5)
    ).all()
    return tuple(
        RecentSaleView(
            seller_order_number=seller_order.seller_order_number,
            relative_date=_relative_date(approved_at),
            status_label=(workflow := resolve_partner_order_workflow(
                seller_order, seller_order.inbound_packages
            )).label,
            status_tone=workflow.tone,
            total=seller_order.subtotal,
        )
        for seller_order, approved_at in rows
    )


def _payout_rows(
    session: Session, *, store_id: uuid.UUID
) -> tuple[PartnerPayoutRowView, ...]:
    payout_rows = session.execute(
        select(SellerPayout, func.count(SellerPayoutItem.seller_order_id))
        .outerjoin(SellerPayoutItem, SellerPayoutItem.payout_id == SellerPayout.id)
        .where(SellerPayout.store_id == store_id)
        .group_by(SellerPayout.id)
        .order_by(SellerPayout.created_at.desc(), SellerPayout.id.desc())
        .limit(100)
    ).all()
    rows = [
        PartnerPayoutRowView(
            payout_id=payout.id,
            reference=payout.payout_number,
            order_count=int(order_count or 0),
            net_total=payout.net_total,
            date_label=_date_label(
                payout.paid_at
                if payout.status == SellerPayoutStatus.PAID
                else payout.scheduled_for
            ),
            status_key=payout.status.value.lower(),
            status_label=PAYOUT_STATUS_LABELS[payout.status],
            status_tone=PAYOUT_STATUS_TONES[payout.status],
            can_open=True,
        )
        for payout, order_count in payout_rows
    ]

    payments = approved_payment_dates_subquery()
    assigned = exists(
        select(SellerPayoutItem.seller_order_id).where(
            SellerPayoutItem.seller_order_id == SellerOrder.id
        )
    )
    local_eligible_date = cast(
        func.timezone("America/Guayaquil", SellerOrder.payout_eligible_at), Date
    )
    pending_groups = session.execute(
        select(
            local_eligible_date.label("eligible_date"),
            func.count(SellerOrder.id),
            func.coalesce(func.sum(SellerOrder.seller_net_total), ZERO),
        )
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .where(*_valid_sale_conditions(store_id), ~assigned)
        .group_by(local_eligible_date)
        .order_by(local_eligible_date.asc().nullsfirst())
    ).all()
    for eligible_date, count, net_total in pending_groups:
        rows.append(
            PartnerPayoutRowView(
                payout_id=None,
                reference=None,
                order_count=int(count or 0),
                net_total=Decimal(net_total or ZERO),
                date_label=(
                    f"{eligible_date.day:02d} {MONTH_ABBR_ES[eligible_date.month - 1]} {eligible_date.year}"
                    if eligible_date is not None
                    else "Después de la entrega + 15 días"
                ),
                status_key="pending",
                status_label="Pendiente",
                status_tone="pending",
                can_open=False,
            )
        )
    return tuple(rows)


def _payout_counts(rows: Iterable[PartnerPayoutRowView]) -> dict[str, int]:
    counts = {"all": 0, "pending": 0, "scheduled": 0, "paid": 0, "on_hold": 0}
    for row in rows:
        counts["all"] += 1
        if row.status_key in counts:
            counts[row.status_key] += 1
    return counts


def _serialize_chart(points: Iterable[SalesChartPoint]) -> list[dict[str, str]]:
    return [
        {
            "key": point.key,
            "label": point.label,
            "gross": f"{point.gross:.2f}",
            "net": f"{point.net:.2f}",
        }
        for point in points
    ]


def get_partner_sales_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    period_key: str | None,
    placeholder_image: str,
    now: datetime | None = None,
) -> PartnerSalesPage:
    store = require_partner_sales_store(session, user_id)
    period = resolve_sales_period(period_key, now=now)
    chart_payload = {
        granularity: _serialize_chart(
            _chart_points(
                session,
                store_id=store.store_id,
                period=period,
                granularity=granularity,
            )
        )
        for granularity in ("day", "week", "month")
    }
    payout_rows = _payout_rows(session, store_id=store.store_id)
    return PartnerSalesPage(
        store=store,
        period=period,
        period_options=tuple((key, SALES_PERIOD_LABELS[key]) for key in SALES_PERIODS),
        metrics=_metrics(session, store_id=store.store_id, period=period),
        chart_payload=chart_payload,
        top_products_by_units=_top_products(
            session,
            store_id=store.store_id,
            period=period,
            placeholder_image=placeholder_image,
            order_by="units",
        ),
        top_products_by_revenue=_top_products(
            session,
            store_id=store.store_id,
            period=period,
            placeholder_image=placeholder_image,
            order_by="revenue",
        ),
        recent_sales=_recent_sales(session, store_id=store.store_id),
        payout_rows=payout_rows,
        payout_counts=_payout_counts(payout_rows),
    )


def get_partner_payout_detail(
    session: Session,
    *,
    user_id: uuid.UUID,
    payout_id: uuid.UUID,
) -> PartnerPayoutDetailView:
    store = require_partner_sales_store(session, user_id)
    row = session.execute(
        select(SellerPayout, func.count(SellerPayoutItem.seller_order_id))
        .outerjoin(SellerPayoutItem, SellerPayoutItem.payout_id == SellerPayout.id)
        .where(
            SellerPayout.id == payout_id,
            SellerPayout.store_id == store.store_id,
        )
        .group_by(SellerPayout.id)
    ).first()
    if row is None:
        raise PartnerPayoutNotFoundError("La liquidación no existe.")
    payout, order_count = row
    relevant_date = payout.paid_at if payout.status == SellerPayoutStatus.PAID else payout.scheduled_for
    destination = "Cuenta no disponible"
    if payout.destination_account_last4 and payout.destination_bank_name_snapshot:
        destination = (
            f"**** {payout.destination_account_last4} "
            f"({payout.destination_bank_name_snapshot})"
        )
    return PartnerPayoutDetailView(
        payout_id=payout.id,
        reference=payout.payout_number,
        status=payout.status.value,
        status_label=PAYOUT_STATUS_LABELS[payout.status],
        status_tone=PAYOUT_STATUS_TONES[payout.status],
        date_label=_date_label(relevant_date),
        destination_label=destination,
        order_count=int(order_count or 0),
        currency=payout.currency,
        gross_sales_total=payout.gross_sales_total,
        discount_total=payout.discount_total,
        commission_total=payout.commission_total,
        net_total=payout.net_total,
        receipt_available=(
            payout.status == SellerPayoutStatus.PAID
            and payout.receipt_storage_key is not None
        ),
    )


def get_authorized_payout_receipt(
    session: Session,
    *,
    user_id: uuid.UUID,
    payout_id: uuid.UUID,
) -> SellerPayout:
    store = require_partner_sales_store(session, user_id)
    payout = session.scalar(
        select(SellerPayout).where(
            SellerPayout.id == payout_id,
            SellerPayout.store_id == store.store_id,
            SellerPayout.status == SellerPayoutStatus.PAID,
            SellerPayout.receipt_storage_key.is_not(None),
            SellerPayout.receipt_original_filename.is_not(None),
            SellerPayout.receipt_media_type.is_not(None),
            SellerPayout.receipt_size_bytes.is_not(None),
            SellerPayout.receipt_sha256.is_not(None),
        )
    )
    if payout is None:
        raise PartnerPayoutNotFoundError("El comprobante no está disponible.")
    return payout


def get_partner_sales_export(
    session: Session,
    *,
    user_id: uuid.UUID,
    period_key: str | None,
    now: datetime | None = None,
) -> tuple[SalesPeriod, tuple[SalesExportRow, ...]]:
    store = require_partner_sales_store(session, user_id)
    period = resolve_sales_period(period_key, now=now)
    payments = approved_payment_dates_subquery()
    rows = session.execute(
        select(
            SellerOrder,
            payments.c.approved_at,
            func.string_agg(
                OrderItem.product_name_snapshot
                + " × "
                + cast(OrderItem.quantity, String),
                "; ",
            ),
            func.sum(OrderItem.quantity),
            SellerPayout.status,
            SellerPayout.payout_number,
            SellerPayout.paid_at,
        )
        .options(selectinload(SellerOrder.inbound_packages))
        .join(Order, Order.id == SellerOrder.order_id)
        .join(payments, payments.c.order_id == SellerOrder.order_id)
        .join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        .outerjoin(
            SellerPayoutItem,
            SellerPayoutItem.seller_order_id == SellerOrder.id,
        )
        .outerjoin(SellerPayout, SellerPayout.id == SellerPayoutItem.payout_id)
        .where(
            *_valid_sale_conditions(store.store_id),
            payments.c.approved_at >= period.starts_at,
            payments.c.approved_at < period.ends_at,
        )
        .group_by(
            SellerOrder.id,
            payments.c.approved_at,
            SellerPayout.status,
            SellerPayout.payout_number,
            SellerPayout.paid_at,
        )
        .order_by(payments.c.approved_at.desc(), SellerOrder.id.desc())
    ).all()
    exported = []
    for seller_order, approved_at, products, units, payout_status, reference, paid_at in rows:
        workflow = resolve_partner_order_workflow(
            seller_order, seller_order.inbound_packages
        )
        exported.append(
            SalesExportRow(
                seller_order_number=seller_order.seller_order_number,
                approved_at=approved_at,
                products=products or "",
                units=int(units or 0),
                subtotal=seller_order.subtotal,
                discounts=seller_order.discount_total,
                commission=seller_order.commission_total,
                net=seller_order.seller_net_total,
                logistics_status=workflow.label,
                payout_status=(
                    PAYOUT_STATUS_LABELS[payout_status]
                    if payout_status is not None
                    else "Pendiente de liberación"
                ),
                eligible_at=seller_order.payout_eligible_at,
                payout_reference=reference,
                paid_at=paid_at,
            )
        )
    return period, tuple(exported)
