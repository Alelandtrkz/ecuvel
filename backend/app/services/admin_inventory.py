from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, joinedload

from app.models import (
    InventoryBalance,
    InventoryMovement,
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    Order,
    OrderItem,
    OrderPackage,
    PhysicalInventoryCount,
    PhysicalInventoryCountExpectedPackage,
    PhysicalInventoryCountScan,
    Product,
    ProductVariant,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOffer,
    SellerOrder,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    InventoryMovementType,
    LocationType,
    LogisticsPackageStatus,
    LogisticsTrackingEventType,
    LogisticsTransferStatus,
    PackageStatus,
)
from app.services.admin_operating_context import (
    AdminOperatingPoint,
    get_operating_point,
    warehouse_options,
)
from app.services.inventory import inventory_available_quantity_expression
from app.services.inventory_counts import (
    PHYSICAL_COUNT_FINALIZED,
    PHYSICAL_COUNT_OPEN,
    PhysicalCountStats,
    get_physical_count_stats,
)


DEFAULT_PAGE_SIZE = 20
MAX_PAGE = 10_000
VALID_PACKAGE_FILTERS = {
    "all", "at-point", "ready", "expected", "deviated", "attention"
}
VALID_MOVEMENT_FILTERS = {
    "all", "entries", "exits", "deliveries", "findings", "differences"
}
ACTIVE_TRANSFER_STATUSES = (
    LogisticsTransferStatus.ASSIGNED,
    LogisticsTransferStatus.IN_TRANSIT,
)
PHYSICAL_STATUSES = (
    LogisticsPackageStatus.AT_POINT,
    LogisticsPackageStatus.ASSIGNED,
    LogisticsPackageStatus.DEVIATED,
)
ATTENTION_LOCATION_TYPES = (LocationType.QUARANTINE, LocationType.DAMAGED)


@dataclass(frozen=True, slots=True)
class AdminInventoryMetrics:
    at_point: int
    expected: int
    ready_for_pickup: int
    attention: int


@dataclass(frozen=True, slots=True)
class AdminInventoryRow:
    kind: str
    package_code: str
    content: str
    order_number: str
    status_label: str
    status_tone: str
    location_label: str
    time_at: datetime | None
    time_label: str
    action_endpoint: str
    action_value: str
    attention_reason: str | None


@dataclass(frozen=True, slots=True)
class AdminInventoryPage:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple
    metrics: AdminInventoryMetrics
    rows: tuple[AdminInventoryRow, ...]
    query: str
    active_filter: str
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    active_tab: str = "packages"


@dataclass(frozen=True, slots=True)
class AdminExpectedMetrics:
    expected: int
    in_transit: int
    awaiting_pickup: int
    overdue: int


@dataclass(frozen=True, slots=True)
class AdminExpectedRow:
    package_code: str
    origin: str
    responsible: str
    status_label: str
    status_tone: str
    eta_at: datetime | None
    last_event: str


@dataclass(frozen=True, slots=True)
class AdminExpectedPage:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple
    metrics: AdminExpectedMetrics
    rows: tuple[AdminExpectedRow, ...]
    page: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class AdminStockRow:
    offer_id: uuid.UUID
    product_title: str
    sku: str
    locations: str
    on_hand: int
    reserved: int
    blocked: int
    available: int


@dataclass(frozen=True, slots=True)
class AdminStockPage:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple
    rows: tuple[AdminStockRow, ...]
    query: str
    page: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    active_tab: str = "stock"


@dataclass(frozen=True, slots=True)
class AdminMovementRow:
    occurred_at: datetime
    category: str
    tone: str
    reference: str
    description: str
    actor: str


@dataclass(frozen=True, slots=True)
class AdminMovementsPage:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple
    rows: tuple[AdminMovementRow, ...]
    active_filter: str
    page: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    active_tab: str = "movements"


@dataclass(frozen=True, slots=True)
class AdminCountListPage:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple
    open_count: PhysicalInventoryCount | None
    counts: tuple[PhysicalInventoryCount, ...]
    locations: tuple[WarehouseLocation, ...]


@dataclass(frozen=True, slots=True)
class AdminCountDetailPage:
    operating_point: AdminOperatingPoint
    count: PhysicalInventoryCount
    stats: PhysicalCountStats
    scans: tuple[PhysicalInventoryCountScan, ...]
    missing: tuple[PhysicalInventoryCountExpectedPackage, ...]


def _page(value) -> int:
    try:
        return min(MAX_PAGE, max(1, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _query(value: str | None) -> str:
    return " ".join((value or "").strip().split())[:120]


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _duration(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "Pendiente"
    start = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now - start.astimezone(timezone.utc)).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "Ahora"
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h" if not remaining else f"{hours} h {remaining} min"
    days, hours = divmod(hours, 24)
    return f"{days} d" if not hours else f"{days} d {hours} h"


def _empty_metrics() -> AdminInventoryMetrics:
    return AdminInventoryMetrics(0, 0, 0, 0)


def _physical_inbound_condition(warehouse_id: uuid.UUID):
    return (
        LogisticsPackageState.current_warehouse_id == warehouse_id,
        LogisticsPackageState.custodian_warehouse_id == warehouse_id,
        LogisticsPackageState.status.in_(PHYSICAL_STATUSES),
    )


def _inventory_metrics(session: Session, warehouse_id: uuid.UUID) -> AdminInventoryMetrics:
    inbound_at_point = int(
        session.scalar(
            select(func.count(LogisticsPackageState.id)).where(
                *_physical_inbound_condition(warehouse_id)
            )
        ) or 0
    )
    ready = int(
        session.scalar(
            select(func.count(OrderPackage.id))
            .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
            .where(
                WarehouseLocation.warehouse_id == warehouse_id,
                OrderPackage.status == PackageStatus.READY_FOR_PICKUP,
            )
        ) or 0
    )
    expected = int(
        session.scalar(
            select(func.count(LogisticsTransfer.id)).where(
                LogisticsTransfer.destination_warehouse_id == warehouse_id,
                LogisticsTransfer.status.in_(ACTIVE_TRANSFER_STATUSES),
            )
        ) or 0
    )
    attention = int(
        session.scalar(
            select(func.count(LogisticsPackageState.id))
            .outerjoin(
                WarehouseLocation,
                WarehouseLocation.id == LogisticsPackageState.current_location_id,
            )
            .where(
                *_physical_inbound_condition(warehouse_id),
                or_(
                    LogisticsPackageState.is_deviated.is_(True),
                    LogisticsPackageState.current_location_id.is_(None),
                    WarehouseLocation.location_type.in_(ATTENTION_LOCATION_TYPES),
                ),
            )
        ) or 0
    )
    return AdminInventoryMetrics(inbound_at_point + ready, expected, ready, attention)


def _package_union(session: Session, warehouse_id: uuid.UUID, query: str, active_filter: str):
    inbound_content = (
        select(func.min(OrderItem.product_name_snapshot))
        .select_from(SellerInboundPackageItem)
        .join(OrderItem, OrderItem.id == SellerInboundPackageItem.order_item_id)
        .where(SellerInboundPackageItem.package_id == SellerInboundPackage.id)
        .correlate(SellerInboundPackage)
        .scalar_subquery()
    )
    attention_reason = case(
        (LogisticsPackageState.is_deviated.is_(True), "Paquete desviado"),
        (LogisticsPackageState.current_location_id.is_(None), "Sin ubicación interna"),
        (WarehouseLocation.location_type == LocationType.QUARANTINE, "En cuarentena"),
        (WarehouseLocation.location_type == LocationType.DAMAGED, "En zona de dañados"),
        else_=literal(None, type_=String()),
    )
    inbound = (
        select(
            literal("INBOUND").label("kind"),
            SellerInboundPackage.package_code.label("package_code"),
            func.coalesce(inbound_content, literal("Paquete de tienda")).label("content"),
            Order.order_number.label("order_number"),
            cast(LogisticsPackageState.status, String()).label("status_code"),
            func.coalesce(WarehouseLocation.code, literal("Sin ubicación")).label("location_label"),
            LogisticsPackageState.last_event_at.label("time_at"),
            attention_reason.label("attention_reason"),
        )
        .join(LogisticsPackageState, LogisticsPackageState.seller_inbound_package_id == SellerInboundPackage.id)
        .join(SellerOrder, SellerOrder.id == SellerInboundPackage.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(User, User.id == Order.buyer_id)
        .outerjoin(WarehouseLocation, WarehouseLocation.id == LogisticsPackageState.current_location_id)
        .where(*_physical_inbound_condition(warehouse_id))
    )
    customer = (
        select(
            literal("CUSTOMER").label("kind"),
            OrderPackage.package_code.label("package_code"),
            OrderItem.product_name_snapshot.label("content"),
            Order.order_number.label("order_number"),
            literal("READY_FOR_PICKUP").label("status_code"),
            WarehouseLocation.code.label("location_label"),
            OrderPackage.ready_at.label("time_at"),
            literal(None, type_=String()).label("attention_reason"),
        )
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(User, User.id == Order.buyer_id)
        .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
        .where(
            WarehouseLocation.warehouse_id == warehouse_id,
            OrderPackage.status == PackageStatus.READY_FOR_PICKUP,
        )
    )
    transfer = (
        select(
            literal("EXPECTED").label("kind"),
            SellerInboundPackage.package_code.label("package_code"),
            func.coalesce(inbound_content, literal("Paquete esperado")).label("content"),
            Order.order_number.label("order_number"),
            cast(LogisticsTransfer.status, String()).label("status_code"),
            Warehouse.name.label("location_label"),
            LogisticsTransfer.assigned_at.label("time_at"),
            literal(None, type_=String()).label("attention_reason"),
        )
        .join(LogisticsPackageState, LogisticsPackageState.id == LogisticsTransfer.package_state_id)
        .join(SellerInboundPackage, SellerInboundPackage.id == LogisticsPackageState.seller_inbound_package_id)
        .join(SellerOrder, SellerOrder.id == SellerInboundPackage.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(User, User.id == Order.buyer_id)
        .join(Warehouse, Warehouse.id == LogisticsTransfer.destination_warehouse_id)
        .where(
            LogisticsTransfer.destination_warehouse_id == warehouse_id,
            LogisticsTransfer.status.in_(ACTIVE_TRANSFER_STATUSES),
        )
    )
    if query:
        pattern = f"%{_escaped(query)}%"
        inbound = inbound.where(or_(SellerInboundPackage.package_code.ilike(pattern, escape="\\"), Order.order_number.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"), inbound_content.ilike(pattern, escape="\\")))
        customer = customer.where(or_(OrderPackage.package_code.ilike(pattern, escape="\\"), Order.order_number.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"), OrderItem.product_name_snapshot.ilike(pattern, escape="\\")))
        transfer = transfer.where(or_(SellerInboundPackage.package_code.ilike(pattern, escape="\\"), Order.order_number.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"), inbound_content.ilike(pattern, escape="\\")))
    statements = []
    if active_filter in ("all", "at-point", "deviated", "attention"):
        if active_filter == "deviated":
            inbound = inbound.where(LogisticsPackageState.is_deviated.is_(True))
        elif active_filter == "attention":
            inbound = inbound.where(or_(LogisticsPackageState.is_deviated.is_(True), LogisticsPackageState.current_location_id.is_(None), WarehouseLocation.location_type.in_(ATTENTION_LOCATION_TYPES)))
        statements.append(inbound)
    if active_filter in ("all", "at-point", "ready"):
        statements.append(customer)
    if active_filter in ("all", "expected"):
        statements.append(transfer)
    return union_all(*statements).subquery()


def get_admin_inventory_page(
    session: Session,
    *,
    warehouse_id,
    query=None,
    active_filter=None,
    page=None,
    now: datetime | None = None,
) -> AdminInventoryPage:
    point = get_operating_point(session, warehouse_id)
    options = warehouse_options(session)
    normalized_query = _query(query)
    normalized_filter = active_filter if active_filter in VALID_PACKAGE_FILTERS else "all"
    current_page = _page(page)
    if point is None:
        return AdminInventoryPage(None, options, _empty_metrics(), (), normalized_query, normalized_filter, 1, DEFAULT_PAGE_SIZE, 0, 1, False, False)
    source = _package_union(session, point.id, normalized_query, normalized_filter)
    total = int(session.scalar(select(func.count()).select_from(source)) or 0)
    total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE))
    current_page = min(current_page, total_pages)
    records = session.execute(
        select(source)
        .order_by(source.c.time_at.desc(), source.c.package_code)
        .offset((current_page - 1) * DEFAULT_PAGE_SIZE)
        .limit(DEFAULT_PAGE_SIZE)
    ).all()
    effective_now = now or datetime.now(timezone.utc)
    status_map = {
        "AT_POINT": ("En el punto", "neutral"),
        "ASSIGNED": ("Asignado", "warning"),
        "DEVIATED": ("Desviado", "danger"),
        "READY_FOR_PICKUP": ("Listo para retirar", "success"),
        "IN_TRANSIT": ("En tránsito", "info"),
    }
    rows = []
    for record in records:
        status_label, status_tone = status_map.get(record.status_code, (record.status_code.replace("_", " ").title(), "neutral"))
        endpoint = "admin.order_detail" if record.kind == "CUSTOMER" else "admin.fulfillment_detail"
        rows.append(AdminInventoryRow(record.kind, record.package_code, record.content, record.order_number, status_label, status_tone, record.location_label, record.time_at, _duration(record.time_at, effective_now), endpoint, record.order_number if record.kind == "CUSTOMER" else record.package_code, record.attention_reason))
    return AdminInventoryPage(point, options, _inventory_metrics(session, point.id), tuple(rows), normalized_query, normalized_filter, current_page, DEFAULT_PAGE_SIZE, total, total_pages, current_page > 1, current_page < total_pages)


def get_admin_expected_page(session: Session, *, warehouse_id, page=None, now=None) -> AdminExpectedPage:
    point = get_operating_point(session, warehouse_id)
    options = warehouse_options(session)
    current_page = _page(page)
    if point is None:
        return AdminExpectedPage(None, options, AdminExpectedMetrics(0, 0, 0, 0), (), 1, 0, 1, False, False)
    base = (
        select(LogisticsTransfer)
        .options(
            joinedload(LogisticsTransfer.package_state).joinedload(
                LogisticsPackageState.seller_inbound_package
            ),
            joinedload(LogisticsTransfer.origin_warehouse),
            joinedload(LogisticsTransfer.assigned_user),
        )
        .where(LogisticsTransfer.destination_warehouse_id == point.id, LogisticsTransfer.status.in_(ACTIVE_TRANSFER_STATUSES))
    )
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    effective_now = now or datetime.now(timezone.utc)
    in_transit = int(session.scalar(select(func.count(LogisticsTransfer.id)).where(LogisticsTransfer.destination_warehouse_id == point.id, LogisticsTransfer.status == LogisticsTransferStatus.IN_TRANSIT)) or 0)
    awaiting = total - in_transit
    overdue = int(session.scalar(select(func.count(LogisticsTransfer.id)).where(LogisticsTransfer.destination_warehouse_id == point.id, LogisticsTransfer.status.in_(ACTIVE_TRANSFER_STATUSES), LogisticsTransfer.eta_at.is_not(None), LogisticsTransfer.eta_at < effective_now)) or 0)
    total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE)); current_page = min(current_page, total_pages)
    transfers = session.scalars(base.order_by(LogisticsTransfer.assigned_at.desc()).offset((current_page - 1) * DEFAULT_PAGE_SIZE).limit(DEFAULT_PAGE_SIZE)).all()
    rows = []
    for item in transfers:
        package = item.package_state.seller_inbound_package
        rows.append(AdminExpectedRow(package.package_code, item.origin_warehouse.name, item.assigned_user.full_name, "En tránsito" if item.status == LogisticsTransferStatus.IN_TRANSIT else "Esperando recogida", "info" if item.status == LogisticsTransferStatus.IN_TRANSIT else "warning", item.eta_at, _duration(item.package_state.last_event_at, effective_now)))
    return AdminExpectedPage(point, options, AdminExpectedMetrics(total, in_transit, awaiting, overdue), tuple(rows), current_page, total, total_pages, current_page > 1, current_page < total_pages)


def get_admin_stock_page(session: Session, *, warehouse_id, query=None, page=None) -> AdminStockPage:
    point = get_operating_point(session, warehouse_id); options = warehouse_options(session)
    normalized_query = _query(query); current_page = _page(page)
    if point is None:
        return AdminStockPage(None, options, (), normalized_query, 1, 0, 1, False, False)
    available = inventory_available_quantity_expression()
    statement = (
        select(
            SellerOffer.id.label("offer_id"), Product.title.label("product_title"),
            ProductVariant.catalog_sku.label("sku"),
            func.string_agg(WarehouseLocation.code, ", ").label("locations"),
            func.sum(InventoryBalance.on_hand_quantity).label("on_hand"),
            func.sum(InventoryBalance.reserved_quantity).label("reserved"),
            func.sum(InventoryBalance.blocked_quantity).label("blocked"),
            func.sum(available).label("available"),
        )
        .join(WarehouseLocation, WarehouseLocation.id == InventoryBalance.location_id)
        .join(SellerOffer, SellerOffer.id == InventoryBalance.offer_id)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(WarehouseLocation.warehouse_id == point.id)
        .group_by(SellerOffer.id, Product.title, ProductVariant.catalog_sku)
    )
    if normalized_query:
        pattern = f"%{_escaped(normalized_query)}%"
        statement = statement.where(or_(Product.title.ilike(pattern, escape="\\"), ProductVariant.catalog_sku.ilike(pattern, escape="\\"), SellerOffer.seller_sku.ilike(pattern, escape="\\")))
    source = statement.subquery(); total = int(session.scalar(select(func.count()).select_from(source)) or 0)
    total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE)); current_page = min(current_page, total_pages)
    records = session.execute(select(source).order_by(source.c.product_title, source.c.sku).offset((current_page - 1) * DEFAULT_PAGE_SIZE).limit(DEFAULT_PAGE_SIZE)).all()
    rows = tuple(AdminStockRow(row.offer_id, row.product_title, row.sku, row.locations, int(row.on_hand), int(row.reserved), int(row.blocked), int(row.available)) for row in records)
    return AdminStockPage(point, options, rows, normalized_query, current_page, total, total_pages, current_page > 1, current_page < total_pages)


def _movement_category(raw_type: str, source: str) -> tuple[str, str]:
    if source == "COUNT":
        return (
            "Diferencia" if raw_type == "MISSING" else "Hallazgo",
            "danger" if raw_type in {"MISSING", "UNEXPECTED"} else "success",
        )
    if source == "HANDOVER":
        return "Entrega", "success"
    if raw_type in {"RECEIVE", "MOVE_IN", "RESTOCK", "ADJUSTMENT_IN", "RECEIVED_AT_POINT", "RECEIVED_AT_DESTINATION"}:
        return "Entrada", "success"
    if raw_type in {"PICK", "MOVE_OUT", "ADJUSTMENT_OUT", "PICKED_UP"}:
        return "Salida", "neutral"
    return "Movimiento", "info"


def get_admin_movements_page(session: Session, *, warehouse_id, active_filter=None, page=None) -> AdminMovementsPage:
    point = get_operating_point(session, warehouse_id); options = warehouse_options(session)
    normalized_filter = active_filter if active_filter in VALID_MOVEMENT_FILTERS else "all"; current_page = _page(page)
    if point is None:
        return AdminMovementsPage(None, options, (), normalized_filter, 1, 0, 1, False, False)
    logistics = (
        select(LogisticsTrackingEvent.occurred_at.label("occurred_at"), literal("LOGISTICS").label("source"), cast(LogisticsTrackingEvent.event_type, String()).label("raw_type"), SellerInboundPackage.package_code.label("reference"), func.coalesce(LogisticsTrackingEvent.notes, literal("Evento de trazabilidad")).label("description"), func.coalesce(User.full_name, literal("Sistema ECUVEL")).label("actor"))
        .join(LogisticsPackageState, LogisticsPackageState.id == LogisticsTrackingEvent.package_state_id)
        .join(SellerInboundPackage, SellerInboundPackage.id == LogisticsPackageState.seller_inbound_package_id)
        .outerjoin(User, User.id == LogisticsTrackingEvent.actor_user_id)
        .where(LogisticsTrackingEvent.warehouse_id == point.id)
    )
    commercial = (
        select(InventoryMovement.created_at.label("occurred_at"), literal("INVENTORY").label("source"), cast(InventoryMovement.movement_type, String()).label("raw_type"), ProductVariant.catalog_sku.label("reference"), func.coalesce(InventoryMovement.notes, literal("Movimiento de existencias")).label("description"), func.coalesce(User.full_name, literal("Sistema ECUVEL")).label("actor"))
        .join(InventoryBalance, InventoryBalance.id == InventoryMovement.balance_id)
        .join(WarehouseLocation, WarehouseLocation.id == InventoryBalance.location_id)
        .join(SellerOffer, SellerOffer.id == InventoryBalance.offer_id)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .outerjoin(User, User.id == InventoryMovement.actor_user_id)
        .where(WarehouseLocation.warehouse_id == point.id)
    )
    handover = (
        select(OrderPackage.handed_over_at.label("occurred_at"), literal("HANDOVER").label("source"), literal("HANDOVER").label("raw_type"), OrderPackage.package_code.label("reference"), literal("Entrega confirmada al comprador").label("description"), func.coalesce(User.full_name, literal("Sistema ECUVEL")).label("actor"))
        .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
        .outerjoin(User, User.id == OrderPackage.handed_over_by_user_id)
        .where(WarehouseLocation.warehouse_id == point.id, OrderPackage.status == PackageStatus.HANDED_OVER, OrderPackage.handed_over_at.is_not(None))
    )
    count_scans = (
        select(PhysicalInventoryCountScan.scanned_at.label("occurred_at"), literal("COUNT").label("source"), PhysicalInventoryCountScan.classification.label("raw_type"), PhysicalInventoryCountScan.scanned_code.label("reference"), literal("Observación de conteo físico").label("description"), User.full_name.label("actor"))
        .join(PhysicalInventoryCount, PhysicalInventoryCount.id == PhysicalInventoryCountScan.count_id)
        .join(User, User.id == PhysicalInventoryCountScan.scanned_by_user_id)
        .where(
            PhysicalInventoryCount.warehouse_id == point.id,
            PhysicalInventoryCountScan.classification == "UNEXPECTED",
        )
    )
    matching_scan = exists(
        select(PhysicalInventoryCountScan.id).where(
            PhysicalInventoryCountScan.count_id == PhysicalInventoryCount.id,
            PhysicalInventoryCountScan.classification == "EXPECTED",
            PhysicalInventoryCountScan.package_kind
            == PhysicalInventoryCountExpectedPackage.package_kind,
            PhysicalInventoryCountScan.package_id
            == PhysicalInventoryCountExpectedPackage.package_id,
        )
    )
    count_missing = (
        select(
            PhysicalInventoryCount.finalized_at.label("occurred_at"),
            literal("COUNT").label("source"),
            literal("MISSING").label("raw_type"),
            PhysicalInventoryCountExpectedPackage.package_code_snapshot.label("reference"),
            literal("Paquete faltante al finalizar conteo físico").label("description"),
            User.full_name.label("actor"),
        )
        .join(
            PhysicalInventoryCount,
            PhysicalInventoryCount.id
            == PhysicalInventoryCountExpectedPackage.count_id,
        )
        .join(User, User.id == PhysicalInventoryCount.finalized_by_user_id)
        .where(
            PhysicalInventoryCount.warehouse_id == point.id,
            PhysicalInventoryCount.status == PHYSICAL_COUNT_FINALIZED,
            ~matching_scan,
        )
    )
    source = union_all(
        logistics, commercial, handover, count_scans, count_missing
    ).subquery()
    if normalized_filter != "all":
        allowed = {
            "entries": ("RECEIVE", "MOVE_IN", "RESTOCK", "ADJUSTMENT_IN", "RECEIVED_AT_POINT", "RECEIVED_AT_DESTINATION"),
            "exits": ("PICK", "MOVE_OUT", "ADJUSTMENT_OUT", "PICKED_UP"),
            "deliveries": ("HANDOVER",),
            "findings": ("UNEXPECTED",),
            "differences": ("MISSING",),
        }[normalized_filter]
        filtered = select(source).where(source.c.raw_type.in_(allowed)).subquery()
    else:
        filtered = source
    total = int(session.scalar(select(func.count()).select_from(filtered)) or 0); total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE)); current_page = min(current_page, total_pages)
    records = session.execute(select(filtered).order_by(filtered.c.occurred_at.desc()).offset((current_page - 1) * DEFAULT_PAGE_SIZE).limit(DEFAULT_PAGE_SIZE)).all()
    rows = []
    for record in records:
        category, tone = _movement_category(record.raw_type, record.source)
        rows.append(AdminMovementRow(record.occurred_at, category, tone, record.reference, record.description, record.actor))
    return AdminMovementsPage(point, options, tuple(rows), normalized_filter, current_page, total, total_pages, current_page > 1, current_page < total_pages)


def get_admin_count_list_page(session: Session, *, warehouse_id) -> AdminCountListPage:
    point = get_operating_point(session, warehouse_id); options = warehouse_options(session)
    if point is None:
        return AdminCountListPage(None, options, None, (), ())
    counts = tuple(session.scalars(select(PhysicalInventoryCount).where(PhysicalInventoryCount.warehouse_id == point.id).order_by(PhysicalInventoryCount.started_at.desc()).limit(30)).all())
    open_count = next((item for item in counts if item.status == PHYSICAL_COUNT_OPEN), None)
    locations = tuple(session.scalars(select(WarehouseLocation).where(WarehouseLocation.warehouse_id == point.id, WarehouseLocation.is_active.is_(True)).order_by(WarehouseLocation.code)).all())
    return AdminCountListPage(point, options, open_count, counts, locations)


def get_admin_count_detail_page(session: Session, *, count_id, warehouse_id) -> AdminCountDetailPage | None:
    point = get_operating_point(session, warehouse_id)
    if point is None:
        return None
    try:
        parsed_id = uuid.UUID(str(count_id))
    except (TypeError, ValueError):
        return None
    count = session.scalar(select(PhysicalInventoryCount).where(PhysicalInventoryCount.id == parsed_id, PhysicalInventoryCount.warehouse_id == point.id))
    if count is None:
        return None
    scans = tuple(session.scalars(select(PhysicalInventoryCountScan).where(PhysicalInventoryCountScan.count_id == count.id).order_by(PhysicalInventoryCountScan.scanned_at.desc())).all())
    scanned_match = exists(
        select(PhysicalInventoryCountScan.id).where(
            PhysicalInventoryCountScan.count_id == count.id,
            PhysicalInventoryCountScan.classification == "EXPECTED",
            PhysicalInventoryCountScan.package_kind
            == PhysicalInventoryCountExpectedPackage.package_kind,
            PhysicalInventoryCountScan.package_id
            == PhysicalInventoryCountExpectedPackage.package_id,
        )
    )
    missing = (
        tuple(
            session.scalars(
                select(PhysicalInventoryCountExpectedPackage).where(
                    PhysicalInventoryCountExpectedPackage.count_id == count.id,
                    ~scanned_match,
                )
            ).all()
        )
        if count.status == PHYSICAL_COUNT_FINALIZED
        else ()
    )
    return AdminCountDetailPage(point, count, get_physical_count_stats(session, count.id), scans, missing)
