from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    InventoryBalance,
    InventoryMovement,
    LogisticsPackageState,
    LogisticsTrackingEvent,
    Order,
    OrderPackage,
    PaymentProof,
    Product,
    ProductDraft,
    ProductVariant,
    SellerInboundPackage,
    SellerOffer,
    SellerOrder,
    SellerPayout,
    Store,
    StoreOnboarding,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LogisticsTrackingEventType,
    OfferStatus,
    OrderStatus,
    PaymentProofStatus,
    ProductDraftStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreOnboardingStatus,
)
from app.services.partner_order_workflow import (
    partner_order_overdue_predicate,
    partner_order_preparation_predicate,
)
from app.services.fulfillment import order_ready_for_pickup_predicate
from app.services.inventory import (
    SELLABLE_LOCATION_TYPES,
    inventory_available_quantity_expression,
)
from app.services.payment_reporting import approved_payment_dates_subquery
from app.services.public_identifiers import format_store_code, format_user_code


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
ZERO = Decimal("0.00")
EXCLUDED_ORDER_STATUSES = (OrderStatus.CANCELLED, OrderStatus.EXPIRED)
SEARCH_QUERY_MAX_LENGTH = 120
_PUBLIC_USER_RE = re.compile(r"^U-(\d{1,8})$", re.IGNORECASE)
_PUBLIC_STORE_RE = re.compile(r"^([A-Z0-9]{3})-(\d{1,8})$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AdminMetricComparison:
    current: int | Decimal
    previous: int | Decimal
    change_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class AdminOperationsMetrics:
    orders_today: AdminMetricComparison
    sales_today: AdminMetricComparison
    pending_payment_proofs: int
    overdue_payment_proofs: int
    in_preparation: int
    ready_for_pickup: int
    products_pending_review: int


@dataclass(frozen=True, slots=True)
class AdminOrderFlowItem:
    key: str
    label: str
    count: int
    height_percent: int
    tone: str
    destination_url: str


@dataclass(frozen=True, slots=True)
class AdminOperationalAlert:
    key: str
    title: str
    message: str
    count: int
    tone: str
    icon: str
    destination_url: str | None = None


@dataclass(frozen=True, slots=True)
class AdminAttentionItem:
    key: str
    label: str
    count: int
    icon: str
    destination_url: str | None = None


@dataclass(frozen=True, slots=True)
class AdminActivityItem:
    timestamp: datetime
    relative_time: str
    type: str
    label: str
    public_reference: str | None
    tone: str
    destination_url: str | None = None


@dataclass(frozen=True, slots=True)
class AdminOperationsPage:
    generated_at: datetime
    generated_at_label: str
    metrics: AdminOperationsMetrics
    order_flow: tuple[AdminOrderFlowItem, ...]
    alerts: tuple[AdminOperationalAlert, ...]
    attention: tuple[AdminAttentionItem, ...]
    activity: tuple[AdminActivityItem, ...]
    critical_stock_threshold: int


@dataclass(frozen=True, slots=True)
class AdminSearchResult:
    group: str
    label: str
    reference: str
    description: str
    icon: str
    destination_url: str | None = None


@dataclass(frozen=True, slots=True)
class AdminSearchGroup:
    key: str
    label: str
    results: tuple[AdminSearchResult, ...]


@dataclass(frozen=True, slots=True)
class AdminSearchPage:
    query: str
    groups: tuple[AdminSearchGroup, ...]

    @property
    def total_results(self) -> int:
        return sum(len(group.results) for group in self.groups)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ecuador_comparison_windows(
    now: datetime | None = None,
) -> tuple[datetime, datetime, datetime, datetime]:
    """Return today start/now and yesterday's equivalent partial window in UTC."""

    effective_now = _aware_utc(now or datetime.now(timezone.utc))
    local_now = effective_now.astimezone(ECUADOR_TZ)
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_local = today_start_local - timedelta(days=1)
    elapsed = local_now - today_start_local
    yesterday_end_local = yesterday_start_local + elapsed
    return (
        today_start_local.astimezone(timezone.utc),
        effective_now,
        yesterday_start_local.astimezone(timezone.utc),
        yesterday_end_local.astimezone(timezone.utc),
    )


def _percentage_change(
    current: int | Decimal, previous: int | Decimal
) -> Decimal | None:
    current_decimal = Decimal(current)
    previous_decimal = Decimal(previous)
    if previous_decimal == ZERO:
        return ZERO if current_decimal == ZERO else None
    return ((current_decimal - previous_decimal) / previous_decimal * 100).quantize(
        Decimal("0.1")
    )


def _count(session: Session, *criteria) -> int:
    return int(session.scalar(select(func.count()).where(*criteria)) or 0)


def _orders_in_window(session: Session, starts_at: datetime, ends_at: datetime) -> int:
    """Count created Orders, excluding terminal CANCELLED/EXPIRED rows."""

    return _count(
        session,
        Order.created_at >= starts_at,
        Order.created_at < ends_at,
        Order.status.notin_(EXCLUDED_ORDER_STATUSES),
    )


def _sales_in_window(
    session: Session, starts_at: datetime, ends_at: datetime
) -> Decimal:
    """Sum each paid Order once at its first approved payment timestamp."""

    approved_payments = approved_payment_dates_subquery()
    value = session.scalar(
        select(func.coalesce(func.sum(Order.grand_total), ZERO))
        .select_from(Order)
        .join(approved_payments, approved_payments.c.order_id == Order.id)
        .where(
            approved_payments.c.approved_at >= starts_at,
            approved_payments.c.approved_at < ends_at,
        )
    )
    return Decimal(value or ZERO).quantize(Decimal("0.01"))


def _critical_stock_count(session: Session, threshold: int) -> int:
    available_quantity = inventory_available_quantity_expression()
    available_by_offer = (
        select(
            InventoryBalance.offer_id.label("offer_id"),
            func.sum(available_quantity).label("available"),
        )
        .join(
            WarehouseLocation,
            WarehouseLocation.id == InventoryBalance.location_id,
        )
        .join(Warehouse, Warehouse.id == WarehouseLocation.warehouse_id)
        .where(
            Warehouse.is_active.is_(True),
            WarehouseLocation.is_active.is_(True),
            WarehouseLocation.location_type.in_(SELLABLE_LOCATION_TYPES),
        )
        .group_by(InventoryBalance.offer_id)
        .subquery()
    )
    value = session.scalar(
        select(func.count(SellerOffer.id))
        .select_from(SellerOffer)
        .outerjoin(
            available_by_offer,
            available_by_offer.c.offer_id == SellerOffer.id,
        )
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            func.coalesce(available_by_offer.c.available, 0) < threshold,
            SellerOffer.status == OfferStatus.ACTIVE,
            ProductVariant.is_active.is_(True),
            Product.is_active.is_(True),
        )
    )
    return int(value or 0)


def _order_flow(session: Session) -> tuple[AdminOrderFlowItem, ...]:
    """Build comparable flow bars using SellerOrder for every stage."""

    definitions = (
        ("confirmed", "Confirmados", SellerOrderStatus.CONFIRMED, "neutral", "/admin/orders?status=confirmed"),
        ("picking", "Picking", SellerOrderStatus.PICKING, "neutral", "/admin/orders?fulfillment=picking"),
        ("packed", "Empacados", SellerOrderStatus.PACKED, "neutral", "/admin/orders?fulfillment=packed"),
        ("ready", "Listos", SellerOrderStatus.READY_FOR_PICKUP, "highlight", "/admin/orders?status=ready"),
        ("completed", "Entregados", SellerOrderStatus.COMPLETED, "neutral", "/admin/orders?status=delivered"),
    )
    statuses = tuple(status for _, _, status, _, _ in definitions)
    counts = {status: 0 for status in statuses}
    rows = session.execute(
        select(SellerOrder.status, func.count(SellerOrder.id))
        .where(SellerOrder.status.in_(statuses))
        .group_by(SellerOrder.status)
    ).all()
    counts.update({status: int(count) for status, count in rows})
    maximum = max(counts.values(), default=0)
    return tuple(
        AdminOrderFlowItem(
            key=key,
            label=label,
            count=counts[status],
            height_percent=(
                max(5, round(counts[status] / maximum * 100))
                if maximum and counts[status]
                else 0
            ),
            tone=tone,
            destination_url=destination_url,
        )
        for key, label, status, tone, destination_url in definitions
    )


def _relative_time(value: datetime, now: datetime) -> str:
    seconds = max(0, int((_aware_utc(now) - _aware_utc(value)).total_seconds()))
    if seconds < 60:
        return "Ahora"
    minutes = seconds // 60
    if minutes < 60:
        return f"Hace {minutes} min" if minutes == 1 else f"Hace {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"Hace {hours} h"
    days = hours // 24
    return "Ayer" if days == 1 else f"Hace {days} días"


def _recent_activity(
    session: Session,
    *,
    now: datetime,
    limit: int,
    source_limit: int = 5,
) -> tuple[AdminActivityItem, ...]:
    normalized_limit = max(1, min(limit, 6))
    source_limit = max(1, min(source_limit, 5))
    candidates: list[tuple[datetime, str, str, str | None, str]] = []

    logistics_events = session.execute(
        select(
            LogisticsTrackingEvent.occurred_at,
            LogisticsTrackingEvent.event_type,
            SellerInboundPackage.package_code,
        )
        .join(
            LogisticsPackageState,
            LogisticsPackageState.id
            == LogisticsTrackingEvent.package_state_id,
        )
        .join(
            SellerInboundPackage,
            SellerInboundPackage.id
            == LogisticsPackageState.seller_inbound_package_id,
        )
        .order_by(
            LogisticsTrackingEvent.occurred_at.desc(),
            LogisticsTrackingEvent.id.desc(),
        )
        .limit(source_limit)
    ).all()
    logistics_labels = {
        LogisticsTrackingEventType.TRANSFER_ASSIGNED: ("Traslado asignado", "primary"),
        LogisticsTrackingEventType.TRANSFER_REASSIGNED: ("Traslado reasignado", "primary"),
        LogisticsTrackingEventType.PICKED_UP: ("Paquete recogido", "primary"),
        LogisticsTrackingEventType.RECEIVED_AT_DESTINATION: ("Paquete recibido en destino", "success"),
        LogisticsTrackingEventType.DEVIATION_DETECTED: ("Desviación logística detectada", "danger"),
        LogisticsTrackingEventType.CORRECTIVE_TRANSFER_CREATED: ("Traslado correctivo creado", "warning"),
    }
    for timestamp, event_type, code in logistics_events:
        label, tone = logistics_labels.get(
            event_type,
            ("Movimiento logístico registrado", "neutral"),
        )
        candidates.append((timestamp, "logistics", label, code, tone))

    received_packages = session.execute(
        select(SellerInboundPackage.received_at, SellerInboundPackage.package_code)
        .where(SellerInboundPackage.received_at.is_not(None))
        .order_by(SellerInboundPackage.received_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (timestamp, "package_received", "Paquete recibido por ECUVEL", code, "success")
        for timestamp, code in received_packages
        if timestamp is not None
    )

    ready_packages = session.execute(
        select(
            SellerInboundPackage.ready_for_dropoff_at,
            SellerInboundPackage.package_code,
        )
        .where(SellerInboundPackage.ready_for_dropoff_at.is_not(None))
        .order_by(SellerInboundPackage.ready_for_dropoff_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (timestamp, "package_ready", "Paquete marcado como listo", code, "primary")
        for timestamp, code in ready_packages
        if timestamp is not None
    )

    approved_payment_dates = approved_payment_dates_subquery()
    approved_payments = session.execute(
        select(approved_payment_dates.c.approved_at, Order.order_number)
        .join(Order, Order.id == approved_payment_dates.c.order_id)
        .order_by(approved_payment_dates.c.approved_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (timestamp, "payment", "Pago aprobado para pedido", number, "success")
        for timestamp, number in approved_payments
        if timestamp is not None
    )

    movements = session.execute(
        select(
            InventoryMovement.created_at,
            SellerOffer.seller_sku,
            InventoryMovement.delta_on_hand,
        )
        .join(InventoryBalance, InventoryBalance.id == InventoryMovement.balance_id)
        .join(SellerOffer, SellerOffer.id == InventoryBalance.offer_id)
        .order_by(InventoryMovement.created_at.desc())
        .limit(source_limit)
    ).all()
    for timestamp, sku, delta in movements:
        suffix = f" ({delta:+d})" if delta else ""
        candidates.append(
            (timestamp, "inventory", f"Inventario actualizado{suffix}", sku, "neutral")
        )

    users = session.execute(
        select(User.created_at, User.registration_number)
        .order_by(User.created_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (
            timestamp,
            "user",
            "Nuevo usuario registrado",
            format_user_code(registration_number),
            "neutral",
        )
        for timestamp, registration_number in users
    )

    submitted_products = session.execute(
        select(ProductDraft.submitted_at, ProductDraft.seller_sku)
        .where(ProductDraft.submitted_at.is_not(None))
        .order_by(ProductDraft.submitted_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (timestamp, "product", "Producto enviado a revisión", sku, "primary")
        for timestamp, sku in submitted_products
        if timestamp is not None
    )

    paid_payouts = session.execute(
        select(SellerPayout.paid_at, SellerPayout.payout_number)
        .where(SellerPayout.paid_at.is_not(None))
        .order_by(SellerPayout.paid_at.desc())
        .limit(source_limit)
    ).all()
    candidates.extend(
        (timestamp, "payout", "Liquidación marcada como pagada", number, "success")
        for timestamp, number in paid_payouts
        if timestamp is not None
    )

    submitted_stores = session.execute(
        select(
            StoreOnboarding.submitted_at,
            Store.product_code_prefix,
            Store.registration_number,
            StoreOnboarding.store_name,
        )
        .outerjoin(Store, Store.id == StoreOnboarding.store_id)
        .where(StoreOnboarding.submitted_at.is_not(None))
        .order_by(StoreOnboarding.submitted_at.desc())
        .limit(source_limit)
    ).all()
    for timestamp, prefix, registration_number, store_name in submitted_stores:
        if timestamp is None:
            continue
        reference = (
            format_store_code(prefix, registration_number)
            if registration_number
            else (store_name or "Tienda pendiente")
        )
        candidates.append(
            (timestamp, "store", "Tienda enviada a revisión", reference, "primary")
        )

    candidates.sort(key=lambda item: _aware_utc(item[0]), reverse=True)
    return tuple(
        AdminActivityItem(
            timestamp=timestamp,
            relative_time=_relative_time(timestamp, now),
            type=event_type,
            label=label,
            public_reference=reference,
            tone=tone,
            destination_url=(
                f"/admin/orders/{reference}"
                if event_type == "payment" and reference
                else f"/admin/fulfillment/{reference}"
                if event_type in {"logistics", "package_received"} and reference
                else None
            ),
        )
        for timestamp, event_type, label, reference, tone in candidates[
            :normalized_limit
        ]
    )


def get_admin_operations_page(
    session: Session,
    *,
    now: datetime | None = None,
    critical_stock_threshold: int = 5,
    activity_limit: int = 6,
) -> AdminOperationsPage:
    effective_now = _aware_utc(now or datetime.now(timezone.utc))
    today_start, today_end, yesterday_start, yesterday_end = (
        ecuador_comparison_windows(effective_now)
    )

    orders_today = _orders_in_window(session, today_start, today_end)
    orders_yesterday = _orders_in_window(session, yesterday_start, yesterday_end)
    sales_today = _sales_in_window(session, today_start, today_end)
    sales_yesterday = _sales_in_window(session, yesterday_start, yesterday_end)
    payment_cutoff = effective_now - timedelta(minutes=30)

    pending_payment_proofs = _count(
        session, PaymentProof.status == PaymentProofStatus.PENDING_REVIEW
    )
    overdue_payment_proofs = _count(
        session,
        PaymentProof.status == PaymentProofStatus.PENDING_REVIEW,
        PaymentProof.created_at <= payment_cutoff,
    )
    in_preparation = _count(session, partner_order_preparation_predicate())
    ready_for_pickup = _count(
        session,
        Order.status.notin_(EXCLUDED_ORDER_STATUSES),
        order_ready_for_pickup_predicate(),
    )
    products_pending_review = _count(
        session, ProductDraft.status == ProductDraftStatus.SUBMITTED
    )
    overdue_preparation = _count(
        session, partner_order_overdue_predicate(effective_now)
    )
    critical_stock = _critical_stock_count(session, critical_stock_threshold)
    payouts_on_hold = _count(
        session, SellerPayout.status == SellerPayoutStatus.ON_HOLD
    )
    picking = _count(session, SellerOrder.status == SellerOrderStatus.PICKING)
    stores_pending = _count(
        session, StoreOnboarding.status == StoreOnboardingStatus.SUBMITTED
    )
    deviated_packages = int(
        session.scalar(
            select(func.count(LogisticsPackageState.id)).where(
                LogisticsPackageState.is_deviated.is_(True)
            )
        )
        or 0
    )

    alert_candidates = (
        AdminOperationalAlert(
            "seller_sla", "Entrega a ECUVEL atrasada",
            f"{overdue_preparation} pedidos superaron el SLA de entrega del vendedor.",
            overdue_preparation, "danger", "timer", "/admin/orders?attention=inbound-overdue",
        ),
        AdminOperationalAlert(
            "payments", "Pagos pendientes",
            f"{overdue_payment_proofs} comprobantes llevan más de 30 min.",
            overdue_payment_proofs, "warning", "receipt-text", "/admin/orders?payment=review",
        ),
        AdminOperationalAlert(
            "stock", "Stock crítico",
            f"{critical_stock} ofertas tienen menos de {critical_stock_threshold} unidades disponibles.",
            critical_stock, "warning", "package-search",
        ),
        AdminOperationalAlert(
            "payouts", "Liquidaciones en revisión",
            f"{payouts_on_hold} liquidaciones requieren atención.",
            payouts_on_hold, "warning", "wallet-cards",
        ),
        AdminOperationalAlert(
            "logistics", "Desviaciones logísticas",
            f"{deviated_packages} paquetes están fuera de su destino esperado.",
            deviated_packages, "danger", "route-off", "/admin/fulfillment?status=deviated",
        ),
    )
    alert_priority = {
        "logistics": 0,
        "seller_sla": 1,
        "payments": 2,
        "stock": 3,
        "payouts": 4,
    }
    alerts = tuple(sorted(
        (alert for alert in alert_candidates if alert.count > 0),
        key=lambda alert: alert_priority[alert.key],
    ))[:4]

    attention_candidates = (
        AdminAttentionItem("payments", "Pagos", pending_payment_proofs, "banknote", "/admin/orders?payment=review"),
        AdminAttentionItem(
            "preparation", "Preparación atrasada", overdue_preparation, "timer", "/admin/orders?attention=inbound-overdue"
        ),
        AdminAttentionItem("picking", "Picking", picking, "archive-restore", "/admin/orders?fulfillment=picking"),
        AdminAttentionItem("products", "Productos", products_pending_review, "shapes"),
        AdminAttentionItem("stores", "Tiendas", stores_pending, "store"),
        AdminAttentionItem("payouts", "Liquidaciones", payouts_on_hold, "wallet-cards"),
        AdminAttentionItem(
            "fulfillment", "Fulfillment", deviated_packages, "truck",
            "/admin/fulfillment?status=deviated",
        ),
    )

    return AdminOperationsPage(
        generated_at=effective_now,
        generated_at_label="ahora",
        metrics=AdminOperationsMetrics(
            orders_today=AdminMetricComparison(
                orders_today,
                orders_yesterday,
                _percentage_change(orders_today, orders_yesterday),
            ),
            sales_today=AdminMetricComparison(
                sales_today,
                sales_yesterday,
                _percentage_change(sales_today, sales_yesterday),
            ),
            pending_payment_proofs=pending_payment_proofs,
            overdue_payment_proofs=overdue_payment_proofs,
            in_preparation=in_preparation,
            ready_for_pickup=ready_for_pickup,
            products_pending_review=products_pending_review,
        ),
        order_flow=_order_flow(session),
        alerts=alerts,
        attention=tuple(item for item in attention_candidates if item.count > 0),
        activity=_recent_activity(
            session,
            now=effective_now,
            limit=activity_limit,
        ),
        critical_stock_threshold=critical_stock_threshold,
    )


def _escaped_prefix(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def search_admin_records(
    session: Session,
    *,
    query: str,
    limit_per_group: int = 5,
) -> AdminSearchPage:
    normalized = " ".join((query or "").strip().split())[:SEARCH_QUERY_MAX_LENGTH]
    if not normalized:
        return AdminSearchPage(query="", groups=())
    prefix = _escaped_prefix(normalized)

    order_results: list[AdminSearchResult] = []
    orders = session.execute(
        select(Order.order_number, Order.status)
        .where(Order.order_number.ilike(prefix, escape="\\"))
        .order_by(
            case(
                (func.lower(Order.order_number) == normalized.casefold(), 0),
                else_=1,
            ),
            Order.created_at.desc(),
        )
        .limit(limit_per_group)
    ).all()
    order_results.extend(
        AdminSearchResult(
            "orders", "Pedido", number, f"Estado: {status.value}", "shopping-cart",
            f"/admin/orders/{number}",
        )
        for number, status in orders
    )
    remaining = max(0, limit_per_group - len(order_results))
    if remaining:
        seller_orders = session.execute(
            select(SellerOrder.seller_order_number, SellerOrder.status)
            .where(SellerOrder.seller_order_number.ilike(prefix, escape="\\"))
            .order_by(
                case(
                    (
                        func.lower(SellerOrder.seller_order_number)
                        == normalized.casefold(),
                        0,
                    ),
                    else_=1,
                ),
                SellerOrder.created_at.desc(),
            )
            .limit(remaining)
        ).all()
        order_results.extend(
            AdminSearchResult(
                "orders", "Subpedido", number, f"Estado: {status.value}", "package"
            )
            for number, status in seller_orders
        )

    user_conditions = [
        User.full_name.ilike(prefix, escape="\\"),
        User.email_normalized.ilike(prefix.casefold(), escape="\\"),
        User.public_code.ilike(prefix, escape="\\"),
    ]
    user_exact_conditions = [
        func.lower(User.public_code) == normalized.casefold(),
        User.email_normalized == normalized.casefold(),
    ]
    user_match = _PUBLIC_USER_RE.fullmatch(normalized)
    if user_match:
        user_conditions.append(User.registration_number == int(user_match.group(1)))
        user_exact_conditions.append(
            User.registration_number == int(user_match.group(1))
        )
    users = session.execute(
        select(User.registration_number, User.full_name, User.email)
        .where(or_(*user_conditions))
        .order_by(
            case(
                (
                    or_(*user_exact_conditions),
                    0,
                ),
                else_=1,
            ),
            User.created_at.desc(),
        )
        .limit(limit_per_group)
    ).all()
    user_results = tuple(
        AdminSearchResult(
            "users",
            full_name,
            format_user_code(registration_number),
            email or "Sin correo registrado",
            "user-round",
        )
        for registration_number, full_name, email in users
    )

    package_results: list[AdminSearchResult] = []
    inbound_packages = session.execute(
        select(
            SellerInboundPackage.package_code,
            SellerInboundPackage.status,
            LogisticsPackageState.id,
        )
        .outerjoin(
            LogisticsPackageState,
            LogisticsPackageState.seller_inbound_package_id
            == SellerInboundPackage.id,
        )
        .where(SellerInboundPackage.package_code.ilike(prefix, escape="\\"))
        .order_by(
            case(
                (
                    func.lower(SellerInboundPackage.package_code)
                    == normalized.casefold(),
                    0,
                ),
                else_=1,
            ),
            SellerInboundPackage.created_at.desc(),
        )
        .limit(limit_per_group)
    ).all()
    package_results.extend(
        AdminSearchResult(
            "packages", "Paquete tienda → ECUVEL", code,
            f"Estado: {status.value}", "package-open",
            f"/admin/fulfillment/{code}" if state_id else None,
        )
        for code, status, state_id in inbound_packages
    )
    remaining = max(0, limit_per_group - len(package_results))
    if remaining:
        outbound_packages = session.execute(
            select(OrderPackage.package_code, OrderPackage.status)
            .where(OrderPackage.package_code.ilike(prefix, escape="\\"))
            .order_by(
                case(
                    (
                        func.lower(OrderPackage.package_code)
                        == normalized.casefold(),
                        0,
                    ),
                    else_=1,
                ),
                OrderPackage.created_at.desc(),
            )
            .limit(remaining)
        ).all()
        package_results.extend(
            AdminSearchResult(
                "packages", "Paquete ECUVEL → comprador", code,
                f"Estado: {status.value}", "package-check"
            )
            for code, status in outbound_packages
        )

    store_conditions = [
        Store.name.ilike(prefix, escape="\\"),
        Store.public_code.ilike(prefix, escape="\\"),
    ]
    store_exact_conditions = [
        func.lower(Store.public_code) == normalized.casefold(),
        func.lower(Store.name) == normalized.casefold(),
    ]
    store_match = _PUBLIC_STORE_RE.fullmatch(normalized)
    if store_match:
        public_store_match = (
            (Store.product_code_prefix == store_match.group(1).upper())
            & (Store.registration_number == int(store_match.group(2)))
        )
        store_conditions.append(public_store_match)
        store_exact_conditions.append(public_store_match)
    stores = session.execute(
        select(
            Store.product_code_prefix,
            Store.registration_number,
            Store.name,
            Store.status,
        )
        .where(or_(*store_conditions))
        .order_by(
            case(
                (
                    or_(*store_exact_conditions),
                    0,
                ),
                else_=1,
            ),
            Store.created_at.desc(),
        )
        .limit(limit_per_group)
    ).all()
    store_results = tuple(
        AdminSearchResult(
            "stores",
            name,
            format_store_code(prefix_value, registration_number),
            f"Estado: {status.value}",
            "store",
        )
        for prefix_value, registration_number, name, status in stores
    )

    groups = (
        AdminSearchGroup("orders", "Pedidos", tuple(order_results)),
        AdminSearchGroup("users", "Usuarios", user_results),
        AdminSearchGroup("packages", "Paquetes", tuple(package_results)),
        AdminSearchGroup("stores", "Tiendas", store_results),
    )
    return AdminSearchPage(
        query=normalized,
        groups=tuple(group for group in groups if group.results),
    )
