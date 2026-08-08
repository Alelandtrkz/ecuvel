from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
    PaymentMethod,
    PaymentStatus,
    SellerInboundPackageStatus,
    SellerOrderDecisionStatus,
    SellerOrderRejectionReason,
    SellerOrderStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStatus,
    StoreStatus,
)
from app.services.inventory import (
    InventoryServiceError,
    release_consumed_reservation_for_seller_rejection,
)
from app.services.partner_order_workflow import (
    PartnerOrderPrimaryAction,
    PartnerOrderWorkflowStage,
    resolve_partner_order_workflow,
)


PAGE_SIZE = 20
MAX_PAGE = 1000
VALID_TABS = {
    "pending",
    "preparation",
    "logistics",
    "completed",
    "rejected",
    "all",
}
VALID_DATE_FILTERS = {"", "today", "7d", "30d"}
ECUADOR_TZ = ZoneInfo("America/Guayaquil")
REJECTION_REASON_LABELS = {
    SellerOrderRejectionReason.OUT_OF_STOCK: "Stock agotado / No actualizado",
    SellerOrderRejectionReason.DAMAGED_OR_UNSHIPPABLE: "Producto dañado / No apto para envío",
    SellerOrderRejectionReason.OTHER: "Otro motivo",
}
STATUS_LABELS = {
    SellerOrderStatus.CONFIRMED: "Confirmado",
    SellerOrderStatus.PICKING: "En preparación",
    SellerOrderStatus.PACKED: "Empacado",
    SellerOrderStatus.READY_FOR_PICKUP: "Listo para retiro",
    SellerOrderStatus.COMPLETED: "Completado",
    SellerOrderStatus.CANCELLED: "Cancelado",
    SellerOrderStatus.PENDING_PAYMENT: "Pago pendiente",
}


class PartnerOrderError(Exception):
    pass


class PartnerOrderAccessError(PartnerOrderError):
    pass


class PartnerOrderNotFoundError(PartnerOrderAccessError):
    pass


class PartnerOrderValidationError(PartnerOrderError):
    pass


class PartnerOrderConflictError(PartnerOrderError):
    pass


@dataclass(frozen=True, slots=True)
class PartnerOrderStoreAccess:
    store_id: uuid.UUID
    store_name: str
    role: StoreMemberRole
    can_manage: bool


@dataclass(frozen=True, slots=True)
class PartnerOrderMetrics:
    pending: int
    preparation: int
    logistics: int
    completed: int
    rejected: int
    total: int


@dataclass(frozen=True, slots=True)
class PartnerOrderRowView:
    seller_order_id: uuid.UUID
    seller_order_number: str
    order_number: str
    buyer_name: str
    buyer_initials: str
    item_count: int
    total_units: int
    total: Decimal
    currency: str
    payment_label: str
    payment_method_label: str
    decision_status: str
    decision_label: str
    decision_tone: str
    logistical_status: str
    logistical_label: str
    workflow_stage: str
    workflow_label: str
    workflow_tone: str
    primary_action: str
    package_count: int
    packages_received_count: int
    can_prepare: bool
    can_print_label: bool
    can_mark_ready: bool
    date_label: str
    time_label: str
    can_decide: bool
    detail_url: str


@dataclass(frozen=True, slots=True)
class PartnerOrdersPage:
    store: PartnerOrderStoreAccess
    metrics: PartnerOrderMetrics
    rows: tuple[PartnerOrderRowView, ...]
    tab: str
    query: str
    selected_status: str
    selected_date: str
    status_options: tuple[tuple[str, str], ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    range_start: int
    range_end: int
    has_previous: bool
    has_next: bool
    page_window: tuple[int | None, ...]


@dataclass(frozen=True, slots=True)
class PartnerOrderLineView:
    product_name: str
    sku: str
    variant_name: str | None
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    image_url: str | None


@dataclass(frozen=True, slots=True)
class PartnerOrderCommissionLineView:
    product_name: str
    category_name: str | None
    rate: Decimal | None
    amount: Decimal | None


@dataclass(frozen=True, slots=True)
class PartnerInboundPackageView:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    status: str
    status_label: str
    label_url: str
    can_print: bool
    can_mark_ready: bool
    ready_for_dropoff_label: str | None
    received_label: str | None
    received_location: str | None


@dataclass(frozen=True, slots=True)
class PartnerOrderTimelineStepView:
    label: str
    is_complete: bool
    date_label: str | None = None


@dataclass(frozen=True, slots=True)
class PartnerOrderDetailView:
    seller_order_id: uuid.UUID
    seller_order_number: str
    order_number: str
    buyer_name: str
    buyer_phone: str | None
    payment_confirmed_label: str
    ship_by_label: str | None
    delivery_window_label: str | None
    is_dispatch_overdue: bool
    delivery_method_label: str
    buyer_pickup_point_name: str
    buyer_pickup_point_address: str
    lines: tuple[PartnerOrderLineView, ...]
    product_subtotal: Decimal
    commission_total: Decimal
    seller_net_total: Decimal
    currency: str
    commission_lines: tuple[PartnerOrderCommissionLineView, ...]
    commission_breakdown_available: bool
    workflow_stage: str
    workflow_label: str
    workflow_tone: str
    inbound_packages: tuple[PartnerInboundPackageView, ...]
    timeline: tuple[PartnerOrderTimelineStepView, ...]
    can_prepare: bool
    decision_status: str
    decision_label: str
    logistical_status: str
    logistical_label: str
    rejection_reason_label: str | None
    rejection_comment: str | None
    rejected_at_label: str | None
    rejected_by_name: str | None
    requires_refund_resolution: bool
    can_approve: bool
    can_reject: bool


@dataclass(frozen=True, slots=True)
class PartnerOrderDecisionResult:
    seller_order_id: uuid.UUID
    decision_status: SellerOrderDecisionStatus
    decision_label: str
    logistical_status: SellerOrderStatus
    logistical_label: str
    workflow_stage: str
    workflow_label: str
    workflow_tone: str
    metrics: PartnerOrderMetrics
    replayed: bool


_READ_ROLES = {
    StoreMemberRole.OWNER,
    StoreMemberRole.ADMINISTRATOR,
    StoreMemberRole.ORDER_OPERATOR,
    StoreMemberRole.VIEWER,
}
_MANAGE_ROLES = {
    StoreMemberRole.OWNER,
    StoreMemberRole.ADMINISTRATOR,
    StoreMemberRole.ORDER_OPERATOR,
}


def require_partner_order_store(
    session: Session,
    user_id: uuid.UUID,
    *,
    require_manage: bool = False,
) -> PartnerOrderStoreAccess:
    roles = _MANAGE_ROLES if require_manage else _READ_ROLES
    row = session.execute(
        select(Store, StoreMember, StoreOnboarding, StoreContractAcceptance)
        .join(StoreMember, StoreMember.store_id == Store.id)
        .join(StoreOnboarding, StoreOnboarding.store_id == Store.id)
        .join(
            StoreContractAcceptance,
            StoreContractAcceptance.onboarding_id == StoreOnboarding.id,
        )
        .where(
            StoreMember.user_id == user_id,
            StoreMember.is_active.is_(True),
            StoreMember.role.in_(roles),
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
            StoreContractAcceptance.status == StoreContractAcceptanceStatus.ACCEPTED,
        )
        .order_by(Store.created_at, Store.id)
        .limit(1)
    ).first()
    if row is None:
        raise PartnerOrderAccessError(
            "No tienes acceso operativo a los pedidos de una tienda habilitada."
        )
    store, member, _onboarding, _acceptance = row
    return PartnerOrderStoreAccess(
        store_id=store.id,
        store_name=store.name,
        role=member.role,
        can_manage=member.role in _MANAGE_ROLES,
    )


def _normalize_page(value: str | int | None) -> int:
    try:
        return min(MAX_PAGE, max(1, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _normalize_tab(value: str | None) -> str:
    candidate = (value or "pending").strip().lower()
    if candidate == "approved":
        return "preparation"
    return candidate if candidate in VALID_TABS else "pending"


def _workflow_conditions():
    active_package_exists = exists(
        select(SellerInboundPackage.id).where(
            SellerInboundPackage.seller_order_id == SellerOrder.id,
            SellerInboundPackage.status != SellerInboundPackageStatus.CANCELLED,
        )
    )
    unreceived_package_exists = exists(
        select(SellerInboundPackage.id).where(
            SellerInboundPackage.seller_order_id == SellerOrder.id,
            SellerInboundPackage.status.in_(
                {
                    SellerInboundPackageStatus.CREATED,
                    SellerInboundPackageStatus.READY_FOR_DROPOFF,
                }
            ),
        )
    )
    pending = and_(
        SellerOrder.decision_status == SellerOrderDecisionStatus.PENDING,
        SellerOrder.status == SellerOrderStatus.CONFIRMED,
    )
    rejected = SellerOrder.decision_status == SellerOrderDecisionStatus.REJECTED
    completed = and_(
        SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
        SellerOrder.status == SellerOrderStatus.COMPLETED,
    )
    operational_approved = and_(
        SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
        SellerOrder.status.not_in(
            {SellerOrderStatus.COMPLETED, SellerOrderStatus.CANCELLED}
        ),
    )
    logistics = and_(
        operational_approved,
        active_package_exists,
        ~unreceived_package_exists,
    )
    preparation = and_(
        operational_approved,
        or_(~active_package_exists, unreceived_package_exists),
    )
    return {
        "pending": pending,
        "preparation": preparation,
        "logistics": logistics,
        "completed": completed,
        "rejected": rejected,
        "all": or_(pending, preparation, logistics, completed, rejected),
    }


def _local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ECUADOR_TZ)


_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _date_label(value: datetime | None, *, include_time: bool = True) -> str:
    local = _local(value)
    if local is None:
        return "Pendiente"
    base = f"{local.day:02d} {_MONTHS[local.month - 1]} {local.year}"
    return f"{base} · {local:%H:%M}" if include_time else base


def _delivery_range(start: datetime | None, end: datetime | None) -> str | None:
    first, last = _local(start), _local(end)
    if first is None or last is None:
        return None
    if first.date() == last.date():
        return _date_label(first, include_time=False)
    if first.month == last.month and first.year == last.year:
        return f"{first.day:02d}–{last.day:02d} {_MONTHS[first.month - 1]} {first.year}"
    return (
        f"{first.day:02d} {_MONTHS[first.month - 1]}–"
        f"{last.day:02d} {_MONTHS[last.month - 1]} {last.year}"
    )


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "CL"


def _decision_presentation(
    status: SellerOrderDecisionStatus | None,
) -> tuple[str, str, str]:
    if status == SellerOrderDecisionStatus.APPROVED:
        return "approved", "Aprobado", "approved"
    if status == SellerOrderDecisionStatus.REJECTED:
        return "rejected", "Rechazado", "rejected"
    return "pending", "Pendiente", "pending"


def _base_paid_statement(store_id: uuid.UUID):
    return (
        select(SellerOrder, Order, PaymentAttempt, User)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(
            PaymentAttempt,
            PaymentAttempt.order_id == Order.id,
        )
        .join(User, User.id == Order.buyer_id)
        .where(
            SellerOrder.store_id == store_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
            SellerOrder.decision_status.is_not(None),
        )
    )


def _metrics(session: Session, store_id: uuid.UUID) -> PartnerOrderMetrics:
    conditions = _workflow_conditions()
    pending, preparation, logistics, completed, rejected = session.execute(
        select(
            func.count(SellerOrder.id).filter(conditions["pending"]),
            func.count(SellerOrder.id).filter(conditions["preparation"]),
            func.count(SellerOrder.id).filter(conditions["logistics"]),
            func.count(SellerOrder.id).filter(conditions["completed"]),
            func.count(SellerOrder.id).filter(conditions["rejected"]),
        )
        .join(Order, Order.id == SellerOrder.order_id)
        .join(PaymentAttempt, PaymentAttempt.order_id == Order.id)
        .where(
            SellerOrder.store_id == store_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            SellerOrder.decision_status.is_not(None),
        )
    ).one()
    pending_count = int(pending or 0)
    preparation_count = int(preparation or 0)
    logistics_count = int(logistics or 0)
    completed_count = int(completed or 0)
    rejected_count = int(rejected or 0)
    return PartnerOrderMetrics(
        pending=pending_count,
        preparation=preparation_count,
        logistics=logistics_count,
        completed=completed_count,
        rejected=rejected_count,
        total=(
            pending_count
            + preparation_count
            + logistics_count
            + completed_count
            + rejected_count
        ),
    )


def _page_window(current: int, total: int) -> tuple[int | None, ...]:
    if total <= 7:
        return tuple(range(1, total + 1))
    values: list[int | None] = [1]
    start, end = max(2, current - 1), min(total - 1, current + 1)
    if start > 2:
        values.append(None)
    values.extend(range(start, end + 1))
    if end < total - 1:
        values.append(None)
    values.append(total)
    return tuple(values)


def get_partner_orders_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    tab: str | None = None,
    query: str | None = None,
    status: str | None = None,
    date_filter: str | None = None,
    page: str | int | None = None,
    now: datetime | None = None,
) -> PartnerOrdersPage:
    access = require_partner_order_store(session, user_id)
    active_tab = _normalize_tab(tab)
    normalized_query = " ".join((query or "").split())[:160]
    selected_status = (status or "").strip().upper()
    valid_statuses = {member.value for member in SellerOrderStatus}
    if selected_status not in valid_statuses:
        selected_status = ""
    selected_date = (date_filter or "").strip().lower()
    if selected_date not in VALID_DATE_FILTERS:
        selected_date = ""

    conditions = _workflow_conditions()
    statement = _base_paid_statement(access.store_id).where(conditions[active_tab])
    if selected_status:
        statement = statement.where(SellerOrder.status == SellerOrderStatus(selected_status))
    if normalized_query:
        escaped_query = normalized_query.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"
        statement = statement.where(
            or_(
                Order.order_number.ilike(pattern, escape="\\"),
                SellerOrder.seller_order_number.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
            )
        )
    effective_now = now or datetime.now(timezone.utc)
    if selected_date:
        local_now = _local(effective_now)
        if selected_date == "today":
            local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            threshold = local_start.astimezone(timezone.utc)
        elif selected_date == "7d":
            threshold = effective_now - timedelta(days=7)
        else:
            threshold = effective_now - timedelta(days=30)
        statement = statement.where(PaymentAttempt.approved_at >= threshold)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total_items = int(session.scalar(count_statement) or 0)
    total_pages = max(1, math.ceil(total_items / PAGE_SIZE))
    current_page = min(_normalize_page(page), total_pages)
    records = session.execute(
        statement.order_by(
            PaymentAttempt.approved_at.desc(), SellerOrder.id.desc()
        ).offset((current_page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()

    seller_ids = [seller_order.id for seller_order, *_ in records]
    item_stats: dict[uuid.UUID, tuple[int, int]] = {}
    packages_by_seller: dict[uuid.UUID, list[SellerInboundPackage]] = {
        seller_id: [] for seller_id in seller_ids
    }
    assigned_units: dict[uuid.UUID, int] = {seller_id: 0 for seller_id in seller_ids}
    if seller_ids:
        item_stats = {
            seller_id: (int(count), int(units))
            for seller_id, count, units in session.execute(
                select(
                    OrderItem.seller_order_id,
                    func.count(OrderItem.id),
                    func.coalesce(func.sum(OrderItem.quantity), 0),
                )
                .where(OrderItem.seller_order_id.in_(seller_ids))
                .group_by(OrderItem.seller_order_id)
            )
        }
        page_packages = list(
            session.scalars(
                select(SellerInboundPackage)
                .where(
                    SellerInboundPackage.seller_order_id.in_(seller_ids),
                    SellerInboundPackage.status
                    != SellerInboundPackageStatus.CANCELLED,
                )
                .order_by(
                    SellerInboundPackage.seller_order_id,
                    SellerInboundPackage.created_at,
                    SellerInboundPackage.id,
                )
            )
        )
        for package in page_packages:
            packages_by_seller[package.seller_order_id].append(package)
        assigned_units.update(
            {
                seller_id: int(units)
                for seller_id, units in session.execute(
                    select(
                        SellerInboundPackage.seller_order_id,
                        func.coalesce(
                            func.sum(SellerInboundPackageItem.quantity), 0
                        ),
                    )
                    .join(
                        SellerInboundPackageItem,
                        SellerInboundPackageItem.package_id
                        == SellerInboundPackage.id,
                    )
                    .where(
                        SellerInboundPackage.seller_order_id.in_(seller_ids),
                        SellerInboundPackage.status
                        != SellerInboundPackageStatus.CANCELLED,
                    )
                    .group_by(SellerInboundPackage.seller_order_id)
                )
            }
        )

    rows: list[PartnerOrderRowView] = []
    for seller_order, order, payment, buyer in records:
        decision_code, decision_label, tone = _decision_presentation(
            seller_order.decision_status
        )
        local_payment = _local(payment.approved_at)
        item_count, total_units = item_stats.get(seller_order.id, (0, 0))
        inbound_packages = packages_by_seller.get(seller_order.id, [])
        workflow = resolve_partner_order_workflow(
            seller_order, inbound_packages, effective_now
        )
        can_operate_preparation = (
            access.can_manage
            and workflow.stage == PartnerOrderWorkflowStage.PREPARATION
        )
        rows.append(
            PartnerOrderRowView(
                seller_order_id=seller_order.id,
                seller_order_number=seller_order.seller_order_number,
                order_number=order.order_number,
                buyer_name=buyer.full_name,
                buyer_initials=_initials(buyer.full_name),
                item_count=item_count,
                total_units=total_units,
                total=seller_order.subtotal - seller_order.discount_total,
                currency=order.currency,
                payment_label="Pagado",
                payment_method_label=(
                    "Transferencia bancaria"
                    if payment.method == PaymentMethod.BANK_TRANSFER else "Tarjeta"
                ),
                decision_status=decision_code,
                decision_label=decision_label,
                decision_tone=tone,
                logistical_status=seller_order.status.value,
                logistical_label=STATUS_LABELS[seller_order.status],
                workflow_stage=workflow.stage.value,
                workflow_label=workflow.label,
                workflow_tone=workflow.tone,
                primary_action=workflow.primary_action.value,
                package_count=workflow.package_count,
                packages_received_count=workflow.packages_received_count,
                can_prepare=(
                    can_operate_preparation
                    and assigned_units.get(seller_order.id, 0) < total_units
                ),
                can_print_label=(
                    can_operate_preparation and workflow.package_count > 0
                ),
                can_mark_ready=(
                    can_operate_preparation and workflow.has_created_packages
                ),
                date_label=(
                    f"{local_payment.day:02d} {_MONTHS[local_payment.month - 1]} "
                    f"{local_payment.year}" if local_payment else "Pendiente"
                ),
                time_label=f"{local_payment:%H:%M}" if local_payment else "",
                can_decide=(
                    access.can_manage
                    and seller_order.decision_status == SellerOrderDecisionStatus.PENDING
                    and seller_order.status == SellerOrderStatus.CONFIRMED
                ),
                detail_url=f"/partners/orders/{seller_order.id}/detail",
            )
        )

    start = (current_page - 1) * PAGE_SIZE + 1 if total_items else 0
    end = min(current_page * PAGE_SIZE, total_items)
    return PartnerOrdersPage(
        store=access,
        metrics=_metrics(session, access.store_id),
        rows=tuple(rows),
        tab=active_tab,
        query=normalized_query,
        selected_status=selected_status,
        selected_date=selected_date,
        status_options=tuple(
            (member.value, STATUS_LABELS[member])
            for member in SellerOrderStatus
            if member != SellerOrderStatus.PENDING_PAYMENT
        ),
        page=current_page,
        page_size=PAGE_SIZE,
        total_items=total_items,
        total_pages=total_pages,
        range_start=start,
        range_end=end,
        has_previous=current_page > 1,
        has_next=current_page < total_pages,
        page_window=_page_window(current_page, total_pages),
    )


def _authorized_order_record(
    session: Session,
    *,
    seller_order_id: uuid.UUID,
    store_id: uuid.UUID,
    lock: bool = False,
):
    statement = (
        select(SellerOrder, Order, PaymentAttempt, User)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(PaymentAttempt, PaymentAttempt.order_id == Order.id)
        .join(User, User.id == Order.buyer_id)
        .options(selectinload(SellerOrder.items))
        .where(
            SellerOrder.id == seller_order_id,
            SellerOrder.store_id == store_id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
        )
    )
    if lock:
        statement = statement.with_for_update(of=SellerOrder)
    record = session.execute(statement).first()
    if record is None:
        raise PartnerOrderNotFoundError("No existe el pedido solicitado.")
    return record


def _timeline(
    seller_order: SellerOrder,
    packages: list[SellerInboundPackage],
    payment: PaymentAttempt,
) -> tuple[PartnerOrderTimelineStepView, ...]:
    active = [
        package
        for package in packages
        if package.status != SellerInboundPackageStatus.CANCELLED
    ]
    prepared = bool(active) and all(
        package.status
        in {
            SellerInboundPackageStatus.READY_FOR_DROPOFF,
            SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
        }
        for package in active
    )
    received = bool(active) and all(
        package.status == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
        for package in active
    )
    distribution = seller_order.status in {
        SellerOrderStatus.PICKING,
        SellerOrderStatus.PACKED,
        SellerOrderStatus.READY_FOR_PICKUP,
        SellerOrderStatus.COMPLETED,
    }
    ecuvel_prepared = seller_order.status in {
        SellerOrderStatus.PACKED,
        SellerOrderStatus.READY_FOR_PICKUP,
        SellerOrderStatus.COMPLETED,
    }
    available = seller_order.status in {
        SellerOrderStatus.READY_FOR_PICKUP,
        SellerOrderStatus.COMPLETED,
    }
    completed = seller_order.status == SellerOrderStatus.COMPLETED
    latest_ready = max(
        (package.ready_for_dropoff_at for package in active if package.ready_for_dropoff_at),
        default=None,
    )
    latest_received = max(
        (package.received_at for package in active if package.received_at),
        default=None,
    )
    return (
        PartnerOrderTimelineStepView(
            "Pago confirmado", True, _date_label(payment.approved_at)
        ),
        PartnerOrderTimelineStepView(
            "Pedido aprobado",
            seller_order.decision_status == SellerOrderDecisionStatus.APPROVED,
            _date_label(seller_order.approved_at) if seller_order.approved_at else None,
        ),
        PartnerOrderTimelineStepView(
            "Preparado por la tienda",
            prepared,
            _date_label(latest_ready) if latest_ready else None,
        ),
        PartnerOrderTimelineStepView(
            "Recibido por Ecuvel",
            received,
            _date_label(latest_received) if latest_received else None,
        ),
        PartnerOrderTimelineStepView("En centro de distribución", distribution),
        PartnerOrderTimelineStepView("Preparado por Ecuvel", ecuvel_prepared),
        PartnerOrderTimelineStepView("Disponible para retiro", available),
        PartnerOrderTimelineStepView("Entregado", completed),
    )


def get_partner_order_detail(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    buyer_pickup_point_name: str,
    buyer_pickup_point_address: str,
    placeholder_image: str | None = None,
    now: datetime | None = None,
) -> PartnerOrderDetailView:
    access = require_partner_order_store(session, user_id)
    seller_order, order, payment, buyer = _authorized_order_record(
        session,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
    )
    inbound_packages = list(
        session.scalars(
            select(SellerInboundPackage)
            .options(selectinload(SellerInboundPackage.received_location))
            .where(SellerInboundPackage.seller_order_id == seller_order.id)
            .order_by(SellerInboundPackage.created_at, SellerInboundPackage.id)
        )
    )
    lines = tuple(
        PartnerOrderLineView(
            product_name=item.product_name_snapshot,
            sku=item.seller_sku_snapshot,
            variant_name=(item.variant_snapshot or {}).get("title"),
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
            image_url=item.image_url_snapshot or placeholder_image,
        )
        for item in sorted(seller_order.items, key=lambda item: (item.created_at, item.id))
    )
    commission_lines = tuple(
        PartnerOrderCommissionLineView(
            product_name=item.product_name_snapshot,
            category_name=item.category_name_snapshot,
            rate=item.commission_rate_snapshot,
            amount=item.commission_amount_snapshot,
        )
        for item in sorted(seller_order.items, key=lambda item: (item.created_at, item.id))
    )
    breakdown_available = bool(commission_lines) and all(
        line.rate is not None and line.amount is not None
        for line in commission_lines
    )
    effective_now = now or datetime.now(timezone.utc)
    workflow = resolve_partner_order_workflow(
        seller_order, inbound_packages, effective_now
    )
    _, decision_label, _ = _decision_presentation(seller_order.decision_status)
    can_decide = (
        access.can_manage
        and seller_order.decision_status == SellerOrderDecisionStatus.PENDING
        and seller_order.status == SellerOrderStatus.CONFIRMED
    )
    assigned_units = int(
        session.scalar(
            select(func.coalesce(func.sum(SellerInboundPackageItem.quantity), 0))
            .join(
                SellerInboundPackage,
                SellerInboundPackage.id == SellerInboundPackageItem.package_id,
            )
            .where(
                SellerInboundPackage.seller_order_id == seller_order.id,
                SellerInboundPackage.status != SellerInboundPackageStatus.CANCELLED,
            )
        )
        or 0
    )
    package_views = tuple(
        PartnerInboundPackageView(
            package_id=package.id,
            package_code=package.package_code,
            barcode=package.barcode,
            status=package.status.value,
            status_label=(
                "Etiqueta lista"
                if package.status == SellerInboundPackageStatus.CREATED
                else (
                    "Listo para entregar a Ecuvel"
                    if package.status == SellerInboundPackageStatus.READY_FOR_DROPOFF
                    else (
                        "Recibido por Ecuvel"
                        if package.status
                        == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
                        else "Cancelado"
                    )
                )
            ),
            label_url=(
                f"/partners/orders/{seller_order.id}/packages/{package.id}/label"
            ),
            can_print=(
                access.can_manage
                and package.status != SellerInboundPackageStatus.CANCELLED
            ),
            can_mark_ready=(
                access.can_manage
                and package.status == SellerInboundPackageStatus.CREATED
            ),
            ready_for_dropoff_label=(
                _date_label(package.ready_for_dropoff_at)
                if package.ready_for_dropoff_at else None
            ),
            received_label=(
                _date_label(package.received_at) if package.received_at else None
            ),
            received_location=(
                package.received_location.name
                if package.received_location is not None else None
            ),
        )
        for package in inbound_packages
    )
    rejected_by_name = (
        session.scalar(
            select(User.full_name).where(
                User.id == seller_order.rejected_by_user_id
            )
        )
        if seller_order.rejected_by_user_id is not None
        else None
    )
    return PartnerOrderDetailView(
        seller_order_id=seller_order.id,
        seller_order_number=seller_order.seller_order_number,
        order_number=order.order_number,
        buyer_name=buyer.full_name,
        buyer_phone=buyer.phone,
        payment_confirmed_label=_date_label(payment.approved_at),
        ship_by_label=_date_label(seller_order.ship_by_at) if seller_order.ship_by_at else None,
        delivery_window_label=_delivery_range(
            seller_order.estimated_delivery_from,
            seller_order.estimated_delivery_to,
        ),
        is_dispatch_overdue=workflow.is_overdue,
        delivery_method_label="Retiro gratuito en punto ECUVEL",
        buyer_pickup_point_name=buyer_pickup_point_name,
        buyer_pickup_point_address=buyer_pickup_point_address,
        lines=lines,
        product_subtotal=seller_order.subtotal - seller_order.discount_total,
        commission_total=seller_order.commission_total,
        seller_net_total=seller_order.seller_net_total,
        currency=order.currency,
        commission_lines=commission_lines,
        commission_breakdown_available=breakdown_available,
        workflow_stage=workflow.stage.value,
        workflow_label=workflow.label,
        workflow_tone=workflow.tone,
        inbound_packages=package_views,
        timeline=_timeline(seller_order, inbound_packages, payment),
        can_prepare=(
            access.can_manage
            and workflow.stage == PartnerOrderWorkflowStage.PREPARATION
            and assigned_units < sum(item.quantity for item in seller_order.items)
        ),
        decision_status=(seller_order.decision_status.value if seller_order.decision_status else ""),
        decision_label=decision_label,
        logistical_status=seller_order.status.value,
        logistical_label=STATUS_LABELS[seller_order.status],
        rejection_reason_label=(
            REJECTION_REASON_LABELS.get(seller_order.rejection_reason)
            if seller_order.rejection_reason else None
        ),
        rejection_comment=seller_order.rejection_comment,
        rejected_at_label=(
            _date_label(seller_order.rejected_at)
            if seller_order.rejected_at else None
        ),
        rejected_by_name=rejected_by_name,
        requires_refund_resolution=seller_order.requires_refund_resolution,
        can_approve=can_decide,
        can_reject=can_decide,
    )


def _decision_result(
    session: Session,
    access: PartnerOrderStoreAccess,
    seller_order: SellerOrder,
    *,
    replayed: bool,
) -> PartnerOrderDecisionResult:
    _, label, _ = _decision_presentation(seller_order.decision_status)
    packages = list(
        session.scalars(
            select(SellerInboundPackage).where(
                SellerInboundPackage.seller_order_id == seller_order.id
            )
        )
    )
    workflow = resolve_partner_order_workflow(seller_order, packages)
    return PartnerOrderDecisionResult(
        seller_order_id=seller_order.id,
        decision_status=seller_order.decision_status,
        decision_label=label,
        logistical_status=seller_order.status,
        logistical_label=STATUS_LABELS[seller_order.status],
        workflow_stage=workflow.stage.value,
        workflow_label=workflow.label,
        workflow_tone=workflow.tone,
        metrics=_metrics(session, access.store_id),
        replayed=replayed,
    )


def approve_partner_order(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    now: datetime | None = None,
) -> PartnerOrderDecisionResult:
    access = require_partner_order_store(session, user_id, require_manage=True)
    seller_order, _order, _payment, _buyer = _authorized_order_record(
        session,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
        lock=True,
    )
    if seller_order.decision_status == SellerOrderDecisionStatus.APPROVED:
        return _decision_result(session, access, seller_order, replayed=True)
    if seller_order.decision_status != SellerOrderDecisionStatus.PENDING:
        raise PartnerOrderConflictError(
            "Este pedido ya fue rechazado y no puede aprobarse."
        )
    if seller_order.status != SellerOrderStatus.CONFIRMED:
        raise PartnerOrderConflictError(
            "El pedido ya inició otra etapa logística y no admite esta decisión."
        )
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    seller_order.approved_at = now or datetime.now(timezone.utc)
    seller_order.approved_by_user_id = user_id
    session.flush()
    return _decision_result(session, access, seller_order, replayed=False)


def reject_partner_order(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    reason: str | None,
    comment: str | None,
    now: datetime | None = None,
) -> PartnerOrderDecisionResult:
    try:
        normalized_reason = SellerOrderRejectionReason((reason or "").strip())
    except ValueError as exc:
        raise PartnerOrderValidationError("Selecciona un motivo de rechazo válido.") from exc
    normalized_comment = (comment or "").strip()
    if not normalized_comment:
        raise PartnerOrderValidationError("Describe el motivo del rechazo.")
    if len(normalized_comment) > 300:
        raise PartnerOrderValidationError(
            "El comentario no puede superar 300 caracteres."
        )

    access = require_partner_order_store(session, user_id, require_manage=True)
    seller_order, _order, _payment, _buyer = _authorized_order_record(
        session,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
        lock=True,
    )
    if seller_order.decision_status == SellerOrderDecisionStatus.REJECTED:
        if (
            seller_order.rejection_reason == normalized_reason
            and seller_order.rejection_comment == normalized_comment
        ):
            return _decision_result(session, access, seller_order, replayed=True)
        raise PartnerOrderConflictError("Este pedido ya fue rechazado con otro motivo.")
    if seller_order.decision_status != SellerOrderDecisionStatus.PENDING:
        raise PartnerOrderConflictError(
            "Este pedido ya fue aprobado y no puede rechazarse."
        )
    if seller_order.status != SellerOrderStatus.CONFIRMED:
        raise PartnerOrderConflictError(
            "El pedido ya inició fulfillment y requiere resolución operativa."
        )

    effective_now = now or datetime.now(timezone.utc)
    reservations = list(
        session.scalars(
            select(InventoryReservation)
            .join(OrderItem, OrderItem.id == InventoryReservation.order_item_id)
            .where(OrderItem.seller_order_id == seller_order.id)
            .order_by(InventoryReservation.balance_id, InventoryReservation.id)
            .with_for_update()
        )
    )
    try:
        for reservation in reservations:
            release_consumed_reservation_for_seller_rejection(
                session=session,
                reservation_id=reservation.id,
                seller_order_id=seller_order.id,
                actor_user_id=user_id,
                now=effective_now,
            )
    except InventoryServiceError as exc:
        raise PartnerOrderConflictError(str(exc)) from exc

    seller_order.decision_status = SellerOrderDecisionStatus.REJECTED
    seller_order.status = SellerOrderStatus.CANCELLED
    seller_order.rejection_reason = normalized_reason
    seller_order.rejection_comment = normalized_comment
    seller_order.rejected_at = effective_now
    seller_order.rejected_by_user_id = user_id
    seller_order.requires_refund_resolution = True
    session.flush()
    return _decision_result(session, access, seller_order, replayed=False)
