from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.models import (
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    Order,
    SellerInboundPackage,
    SellerOrder,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LogisticsPackageStatus,
    LogisticsTrackingEventType,
    LogisticsTransferStatus,
    UserStatus,
)


DEFAULT_PAGE_SIZE = 25
ALLOWED_PAGE_SIZES = (25, 50)
MAX_QUERY_LENGTH = 120
MAX_PAGE = 10_000
VALID_STATUSES = ("all", "awaiting-pickup", "in-transit", "at-point", "deviated", "incidents")
VALID_AGES = ("", "1h", "6h", "24h")
ACTIVE_TRANSFER_STATUSES = (
    LogisticsTransferStatus.ASSIGNED,
    LogisticsTransferStatus.IN_TRANSIT,
)

EVENT_LABELS = {
    LogisticsTrackingEventType.RECEIVED_AT_POINT: "Recibido en punto ECUVEL",
    LogisticsTrackingEventType.TRANSFER_ASSIGNED: "Traslado asignado",
    LogisticsTrackingEventType.TRANSFER_REASSIGNED: "Responsable reasignado",
    LogisticsTrackingEventType.PICKED_UP: "Paquete recogido",
    LogisticsTrackingEventType.ARRIVAL_SCAN: "Escaneo de llegada",
    LogisticsTrackingEventType.RECEIVED_AT_DESTINATION: "Recibido en destino",
    LogisticsTrackingEventType.DEVIATION_DETECTED: "Desviación detectada",
    LogisticsTrackingEventType.CORRECTIVE_TRANSFER_CREATED: "Traslado correctivo creado",
    LogisticsTrackingEventType.INCIDENT_REPORTED: "Incidencia reportada",
}


@dataclass(frozen=True, slots=True)
class AdminFulfillmentStatusView:
    code: str
    label: str
    tone: str


@dataclass(frozen=True, slots=True)
class AdminFulfillmentMetrics:
    total: int
    awaiting_pickup: int
    in_transit: int
    at_point: int
    deviated: int
    incidents: int


@dataclass(frozen=True, slots=True)
class AdminFulfillmentRow:
    package_code: str
    order_number: str
    origin_name: str
    destination_name: str
    current_location: str
    current_route: str | None
    custodian_name: str
    custodian_code: str | None
    status: AdminFulfillmentStatusView
    last_event_label: str
    last_event_context: str | None
    last_event_at: datetime
    time_in_state: str
    action_label: str
    action_tone: str


@dataclass(frozen=True, slots=True)
class AdminFulfillmentOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class AdminFulfillmentPage:
    rows: tuple[AdminFulfillmentRow, ...]
    metrics: AdminFulfillmentMetrics
    active_status: str
    query: str
    point_filter: str
    destination_filter: str
    custodian_filter: str
    deviated_filter: bool
    age_filter: str
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    point_options: tuple[AdminFulfillmentOption, ...]
    staff_options: tuple[AdminFulfillmentOption, ...]


@dataclass(frozen=True, slots=True)
class AdminTransferView:
    transfer_code: str
    origin_name: str
    destination_name: str
    responsible_name: str
    responsible_code: str
    vehicle_code: str | None
    status: AdminFulfillmentStatusView
    is_corrective: bool
    assigned_at: datetime
    picked_up_at: datetime | None
    received_at: datetime | None
    eta_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminTrackingEventView:
    event_type: str
    label: str
    occurred_at: datetime
    point_name: str | None
    actor_name: str | None
    actor_code: str | None
    custody_change: str | None
    transfer_code: str | None
    notes: str | None
    tone: str


@dataclass(frozen=True, slots=True)
class AdminPackageTrackingDetail:
    package_code: str
    barcode: str
    order_number: str
    seller_order_number: str
    status: AdminFulfillmentStatusView
    current_location: str
    state_since: str
    current_point_name: str | None
    current_internal_location: str | None
    origin_name: str | None
    destination_name: str | None
    custodian_name: str
    custodian_code: str | None
    custodian_type: str
    custody_package_count: int | None
    vehicle_code: str | None
    transfer: AdminTransferView | None
    next_title: str
    next_description: str
    next_tone: str
    events: tuple[AdminTrackingEventView, ...]
    warehouses: tuple[AdminFulfillmentOption, ...]
    staff: tuple[AdminFulfillmentOption, ...]
    can_assign: bool
    can_correct: bool
    can_reassign: bool
    can_pickup: bool
    can_receive: bool
    can_print_label: bool


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _page(value: str | int | None) -> int:
    try:
        return min(MAX_PAGE, max(1, int(value or 1)))
    except (TypeError, ValueError):
        return 1


def _page_size(value: str | int | None) -> int:
    try:
        normalized = int(value or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return normalized if normalized in ALLOWED_PAGE_SIZES else DEFAULT_PAGE_SIZE


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration(value: datetime, now: datetime) -> str:
    seconds = max(0, int((_aware(now) - _aware(value)).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "Ahora"
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h" if not remaining else f"{hours} h {remaining} min"
    days, remaining_hours = divmod(hours, 24)
    return f"{days} d" if not remaining_hours else f"{days} d {remaining_hours} h"


def _status(state: LogisticsPackageState) -> AdminFulfillmentStatusView:
    if state.is_deviated:
        return AdminFulfillmentStatusView("deviated", "Desviado", "danger")
    values = {
        LogisticsPackageStatus.AT_POINT: ("at-point", "En punto ECUVEL", "success"),
        LogisticsPackageStatus.ASSIGNED: ("awaiting-pickup", "Por recoger", "warning"),
        LogisticsPackageStatus.IN_TRANSIT: ("in-transit", "En tránsito", "info"),
        LogisticsPackageStatus.DEVIATED: ("deviated", "Desviado", "danger"),
        LogisticsPackageStatus.DELIVERED: ("delivered", "Entregado", "success"),
    }
    code, label, tone = values[state.status]
    return AdminFulfillmentStatusView(code, label, tone)


def _transfer_status(value: LogisticsTransferStatus) -> AdminFulfillmentStatusView:
    values = {
        LogisticsTransferStatus.ASSIGNED: ("assigned", "Asignado", "warning"),
        LogisticsTransferStatus.IN_TRANSIT: ("in-transit", "En tránsito", "info"),
        LogisticsTransferStatus.RECEIVED: ("received", "Recibido", "success"),
        LogisticsTransferStatus.DEVIATED: ("deviated", "Desviado", "danger"),
        LogisticsTransferStatus.CANCELLED: ("cancelled", "Cancelado", "muted"),
    }
    code, label, tone = values[value]
    return AdminFulfillmentStatusView(code, label, tone)


def _metrics(session: Session) -> AdminFulfillmentMetrics:
    rows = session.execute(
        select(
            LogisticsPackageState.status,
            LogisticsPackageState.is_deviated,
            func.count(LogisticsPackageState.id),
        ).group_by(LogisticsPackageState.status, LogisticsPackageState.is_deviated)
    ).all()
    total = awaiting = transit = point = deviated = 0
    for status, is_deviated, count in rows:
        count = int(count)
        total += count
        if is_deviated:
            deviated += count
        if status == LogisticsPackageStatus.ASSIGNED:
            awaiting += count
        elif status == LogisticsPackageStatus.IN_TRANSIT:
            transit += count
        elif status == LogisticsPackageStatus.AT_POINT and not is_deviated:
            point += count
    incidents = int(
        session.scalar(
            select(func.count(LogisticsTrackingEvent.id)).where(
                LogisticsTrackingEvent.event_type
                == LogisticsTrackingEventType.INCIDENT_REPORTED
            )
        )
        or 0
    )
    return AdminFulfillmentMetrics(total, awaiting, transit, point, deviated, incidents)


def _warehouse_options(session: Session) -> tuple[AdminFulfillmentOption, ...]:
    return tuple(
        AdminFulfillmentOption(str(identifier), f"{code} · {name}")
        for identifier, code, name in session.execute(
            select(Warehouse.id, Warehouse.code, Warehouse.name)
            .where(Warehouse.is_active.is_(True))
            .order_by(Warehouse.name, Warehouse.code)
        )
    )


def _staff_options(session: Session) -> tuple[AdminFulfillmentOption, ...]:
    return tuple(
        AdminFulfillmentOption(str(identifier), f"{name} · {public_code}")
        for identifier, name, public_code in session.execute(
            select(User.id, User.full_name, User.public_code)
            .where(
                User.is_ecuvel_staff.is_(True),
                User.is_active.is_(True),
                User.status == UserStatus.ACTIVE,
            )
            .order_by(User.full_name, User.public_code)
        )
    )


def _latest_transfer_map(
    session: Session, state_ids: tuple[uuid.UUID, ...]
) -> dict[uuid.UUID, LogisticsTransfer]:
    if not state_ids:
        return {}
    rows = session.scalars(
        select(LogisticsTransfer)
        .options(
            joinedload(LogisticsTransfer.origin_warehouse),
            joinedload(LogisticsTransfer.destination_warehouse),
            joinedload(LogisticsTransfer.assigned_user),
        )
        .where(LogisticsTransfer.package_state_id.in_(state_ids))
        .order_by(
            LogisticsTransfer.package_state_id,
            LogisticsTransfer.assigned_at.desc(),
            LogisticsTransfer.id.desc(),
        )
    )
    result: dict[uuid.UUID, LogisticsTransfer] = {}
    for transfer in rows:
        result.setdefault(transfer.package_state_id, transfer)
    return result


def _latest_event_map(
    session: Session, state_ids: tuple[uuid.UUID, ...]
) -> dict[uuid.UUID, LogisticsTrackingEvent]:
    if not state_ids:
        return {}
    rows = session.scalars(
        select(LogisticsTrackingEvent)
        .options(joinedload(LogisticsTrackingEvent.warehouse))
        .where(LogisticsTrackingEvent.package_state_id.in_(state_ids))
        .order_by(
            LogisticsTrackingEvent.package_state_id,
            LogisticsTrackingEvent.occurred_at.desc(),
            LogisticsTrackingEvent.id.desc(),
        )
    )
    result: dict[uuid.UUID, LogisticsTrackingEvent] = {}
    for event in rows:
        result.setdefault(event.package_state_id, event)
    return result


def get_admin_fulfillment_page(
    session: Session,
    *,
    status: str | None = None,
    query: str | None = None,
    point: str | None = None,
    destination: str | None = None,
    custodian: str | None = None,
    deviated: str | None = None,
    age: str | None = None,
    page: str | int | None = None,
    page_size: str | int | None = None,
    now: datetime | None = None,
) -> AdminFulfillmentPage:
    effective_now = _aware(now or datetime.now(timezone.utc))
    active_status = (status or "all").strip().lower()
    if active_status not in VALID_STATUSES:
        active_status = "all"
    normalized_query = " ".join((query or "").strip().split())[:MAX_QUERY_LENGTH]
    point_filter = (point or "").strip()
    destination_filter = (destination or "").strip()
    custodian_filter = " ".join((custodian or "").strip().split())[:MAX_QUERY_LENGTH]
    deviated_filter = (deviated or "").strip().lower() in {"1", "true", "yes"}
    age_filter = (age or "").strip().lower()
    if age_filter not in VALID_AGES:
        age_filter = ""
    current_page = _page(page)
    current_page_size = _page_size(page_size)

    current_point = aliased(Warehouse)
    expected_destination = aliased(Warehouse)
    custodian_warehouse = aliased(Warehouse)
    custodian_user = aliased(User)
    active_transfer = aliased(LogisticsTransfer)
    assigned_user = aliased(User)
    origin_warehouse = aliased(Warehouse)
    transfer_destination = aliased(Warehouse)

    statement = (
        select(LogisticsPackageState)
        .join(
            SellerInboundPackage,
            SellerInboundPackage.id
            == LogisticsPackageState.seller_inbound_package_id,
        )
        .join(SellerOrder, SellerOrder.id == SellerInboundPackage.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .outerjoin(current_point, current_point.id == LogisticsPackageState.current_warehouse_id)
        .outerjoin(expected_destination, expected_destination.id == LogisticsPackageState.expected_destination_warehouse_id)
        .outerjoin(custodian_warehouse, custodian_warehouse.id == LogisticsPackageState.custodian_warehouse_id)
        .outerjoin(custodian_user, custodian_user.id == LogisticsPackageState.custodian_user_id)
        .outerjoin(
            active_transfer,
            (active_transfer.package_state_id == LogisticsPackageState.id)
            & active_transfer.status.in_(ACTIVE_TRANSFER_STATUSES),
        )
        .outerjoin(assigned_user, assigned_user.id == active_transfer.assigned_user_id)
        .outerjoin(origin_warehouse, origin_warehouse.id == active_transfer.origin_warehouse_id)
        .outerjoin(transfer_destination, transfer_destination.id == active_transfer.destination_warehouse_id)
        .options(
            joinedload(LogisticsPackageState.seller_inbound_package)
            .joinedload(SellerInboundPackage.seller_order)
            .joinedload(SellerOrder.order),
            joinedload(LogisticsPackageState.current_warehouse),
            joinedload(LogisticsPackageState.current_location),
            joinedload(LogisticsPackageState.custodian_warehouse),
            joinedload(LogisticsPackageState.custodian_user),
            joinedload(LogisticsPackageState.expected_destination),
        )
    )
    criteria = []
    if active_status == "awaiting-pickup":
        criteria.append(LogisticsPackageState.status == LogisticsPackageStatus.ASSIGNED)
    elif active_status == "in-transit":
        criteria.append(LogisticsPackageState.status == LogisticsPackageStatus.IN_TRANSIT)
    elif active_status == "at-point":
        criteria.extend((
            LogisticsPackageState.status == LogisticsPackageStatus.AT_POINT,
            LogisticsPackageState.is_deviated.is_(False),
        ))
    elif active_status == "deviated":
        criteria.append(LogisticsPackageState.is_deviated.is_(True))
    elif active_status == "incidents":
        criteria.append(literal(False))
    if deviated_filter:
        criteria.append(LogisticsPackageState.is_deviated.is_(True))
    point_id = None
    if point_filter:
        try:
            point_id = uuid.UUID(point_filter)
        except ValueError:
            criteria.append(func.lower(current_point.code) == point_filter.casefold())
    if point_id:
        criteria.append(LogisticsPackageState.current_warehouse_id == point_id)
    destination_id = None
    if destination_filter:
        try:
            destination_id = uuid.UUID(destination_filter)
        except ValueError:
            criteria.append(
                func.lower(expected_destination.code)
                == destination_filter.casefold()
            )
    if destination_id:
        criteria.append(LogisticsPackageState.expected_destination_warehouse_id == destination_id)
    if custodian_filter:
        contains = f"%{_escaped(custodian_filter)}%"
        criteria.append(or_(
            custodian_user.full_name.ilike(contains, escape="\\"),
            custodian_user.public_code.ilike(contains, escape="\\"),
            custodian_warehouse.name.ilike(contains, escape="\\"),
            custodian_warehouse.code.ilike(contains, escape="\\"),
            assigned_user.full_name.ilike(contains, escape="\\"),
            assigned_user.public_code.ilike(contains, escape="\\"),
        ))
    if normalized_query:
        contains = f"%{_escaped(normalized_query)}%"
        criteria.append(or_(
            SellerInboundPackage.package_code.ilike(contains, escape="\\"),
            Order.order_number.ilike(contains, escape="\\"),
            current_point.name.ilike(contains, escape="\\"),
            current_point.code.ilike(contains, escape="\\"),
            expected_destination.name.ilike(contains, escape="\\"),
            expected_destination.code.ilike(contains, escape="\\"),
            origin_warehouse.name.ilike(contains, escape="\\"),
            transfer_destination.name.ilike(contains, escape="\\"),
            assigned_user.full_name.ilike(contains, escape="\\"),
            custodian_user.full_name.ilike(contains, escape="\\"),
        ))
    if age_filter:
        hours = {"1h": 1, "6h": 6, "24h": 24}[age_filter]
        criteria.append(
            LogisticsPackageState.last_event_at
            <= effective_now - timedelta(hours=hours)
        )
    if criteria:
        statement = statement.where(*criteria)
    total_items = int(
        session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        or 0
    )
    total_pages = max(1, math.ceil(total_items / current_page_size))
    current_page = min(current_page, total_pages)
    if normalized_query:
        normalized_lower = normalized_query.casefold()
        escaped_prefix = f"{_escaped(normalized_lower)}%"
        match_priority = case(
            (
                or_(
                    func.lower(SellerInboundPackage.package_code) == normalized_lower,
                    func.lower(Order.order_number) == normalized_lower,
                ),
                0,
            ),
            (
                or_(
                    func.lower(SellerInboundPackage.package_code).like(
                        escaped_prefix, escape="\\"
                    ),
                    func.lower(Order.order_number).like(
                        escaped_prefix, escape="\\"
                    ),
                ),
                1,
            ),
            else_=2,
        )
    else:
        match_priority = literal(0)
    states = tuple(
        session.scalars(
            statement.order_by(
                match_priority,
                LogisticsPackageState.last_event_at.desc(),
                SellerInboundPackage.package_code,
            )
            .offset((current_page - 1) * current_page_size)
            .limit(current_page_size)
        ).unique()
    )
    state_ids = tuple(state.id for state in states)
    transfers = _latest_transfer_map(session, state_ids)
    events = _latest_event_map(session, state_ids)
    rows: list[AdminFulfillmentRow] = []
    for state in states:
        package = state.seller_inbound_package
        order = package.seller_order.order
        transfer = transfers.get(state.id)
        event = events.get(state.id)
        origin_name = (
            transfer.origin_warehouse.name
            if transfer
            else (state.current_warehouse.name if state.current_warehouse else "Sin origen")
        )
        destination_name = (
            state.expected_destination.name
            if state.expected_destination
            else "Sin destino asignado"
        )
        if state.status == LogisticsPackageStatus.IN_TRANSIT:
            current_location = "En tránsito"
            current_route = (
                f"{transfer.origin_warehouse.name} → {transfer.destination_warehouse.name}"
                if transfer
                else None
            )
        else:
            current_location = (
                state.current_warehouse.name
                if state.current_warehouse
                else "Ubicación no disponible"
            )
            current_route = state.current_location.name if state.current_location else None
        if state.custodian_user:
            custodian_name = state.custodian_user.full_name
            custodian_code = state.custodian_user.public_account_code
        elif state.custodian_warehouse:
            custodian_name = state.custodian_warehouse.name
            custodian_code = state.custodian_warehouse.code
        else:
            custodian_name, custodian_code = "Sin custodio", None
        event_label = EVENT_LABELS.get(event.event_type, event.event_type.value) if event else "Sin eventos"
        event_context = event.warehouse.name if event and event.warehouse else None
        if state.is_deviated:
            action_label, action_tone = "Resolver traslado", "danger"
        elif state.status == LogisticsPackageStatus.AT_POINT:
            action_label, action_tone = "Asignar transporte", "primary"
        else:
            action_label, action_tone = "Ver paquete", "neutral"
        rows.append(AdminFulfillmentRow(
            package.package_code,
            order.order_number,
            origin_name,
            destination_name,
            current_location,
            current_route,
            custodian_name,
            custodian_code,
            _status(state),
            event_label,
            event_context,
            event.occurred_at if event else state.last_event_at,
            _duration(state.last_event_at, effective_now),
            action_label,
            action_tone,
        ))
    options = _warehouse_options(session)
    return AdminFulfillmentPage(
        tuple(rows),
        _metrics(session),
        active_status,
        normalized_query,
        point_filter,
        destination_filter,
        custodian_filter,
        deviated_filter,
        age_filter,
        current_page,
        current_page_size,
        total_items,
        total_pages,
        current_page > 1,
        current_page < total_pages,
        options,
        _staff_options(session),
    )


def _custody_name(
    warehouse: Warehouse | None, user: User | None
) -> str | None:
    if user:
        return user.full_name
    if warehouse:
        return warehouse.name
    return None


def get_admin_fulfillment_detail(
    session: Session,
    *,
    package_code: str,
    now: datetime | None = None,
) -> AdminPackageTrackingDetail | None:
    effective_now = _aware(now or datetime.now(timezone.utc))
    state = session.scalar(
        select(LogisticsPackageState)
        .join(
            SellerInboundPackage,
            SellerInboundPackage.id
            == LogisticsPackageState.seller_inbound_package_id,
        )
        .options(
            joinedload(LogisticsPackageState.seller_inbound_package)
            .joinedload(SellerInboundPackage.seller_order)
            .joinedload(SellerOrder.order),
            joinedload(LogisticsPackageState.current_warehouse),
            joinedload(LogisticsPackageState.current_location),
            joinedload(LogisticsPackageState.custodian_warehouse),
            joinedload(LogisticsPackageState.custodian_user),
            joinedload(LogisticsPackageState.expected_destination),
        )
        .where(
            SellerInboundPackage.package_code
            == (package_code or "").strip().upper()
        )
    )
    if state is None:
        return None
    package = state.seller_inbound_package
    latest_transfer = _latest_transfer_map(session, (state.id,)).get(state.id)
    transfers = tuple(
        session.scalars(
            select(LogisticsTransfer)
            .options(
                joinedload(LogisticsTransfer.origin_warehouse),
                joinedload(LogisticsTransfer.destination_warehouse),
                joinedload(LogisticsTransfer.assigned_user),
            )
            .where(LogisticsTransfer.package_state_id == state.id)
            .order_by(LogisticsTransfer.assigned_at.desc(), LogisticsTransfer.id.desc())
        )
    )
    events = tuple(
        session.scalars(
            select(LogisticsTrackingEvent)
            .options(
                joinedload(LogisticsTrackingEvent.warehouse),
                joinedload(LogisticsTrackingEvent.actor),
                joinedload(LogisticsTrackingEvent.transfer),
                joinedload(LogisticsTrackingEvent.previous_custodian_warehouse),
                joinedload(LogisticsTrackingEvent.previous_custodian_user),
                joinedload(LogisticsTrackingEvent.new_custodian_warehouse),
                joinedload(LogisticsTrackingEvent.new_custodian_user),
            )
            .where(LogisticsTrackingEvent.package_state_id == state.id)
            .order_by(LogisticsTrackingEvent.occurred_at.desc(), LogisticsTrackingEvent.id.desc())
        )
    )
    event_views: list[AdminTrackingEventView] = []
    for event in events:
        previous = _custody_name(
            event.previous_custodian_warehouse,
            event.previous_custodian_user,
        )
        new = _custody_name(
            event.new_custodian_warehouse,
            event.new_custodian_user,
        )
        custody_change = (
            f"{previous or 'Sin custodio'} → {new}"
            if new and previous != new
            else None
        )
        tone = (
            "danger"
            if event.event_type == LogisticsTrackingEventType.DEVIATION_DETECTED
            else "success"
            if event.event_type == LogisticsTrackingEventType.RECEIVED_AT_DESTINATION
            else "primary"
        )
        event_views.append(AdminTrackingEventView(
            event.event_type.value,
            EVENT_LABELS.get(event.event_type, event.event_type.value),
            event.occurred_at,
            event.warehouse.name if event.warehouse else None,
            event.actor.full_name if event.actor else None,
            event.actor.public_account_code if event.actor else None,
            custody_change,
            event.transfer.transfer_code if event.transfer else None,
            event.notes,
            tone,
        ))
    transfer_view = None
    if latest_transfer:
        transfer_view = AdminTransferView(
            latest_transfer.transfer_code,
            latest_transfer.origin_warehouse.name,
            latest_transfer.destination_warehouse.name,
            latest_transfer.assigned_user.full_name,
            latest_transfer.assigned_user.public_account_code,
            latest_transfer.vehicle_code,
            _transfer_status(latest_transfer.status),
            latest_transfer.is_corrective,
            latest_transfer.assigned_at,
            latest_transfer.picked_up_at,
            latest_transfer.received_at,
            latest_transfer.eta_at,
        )
    if state.is_deviated:
        next_title = "Corrección de ruta requerida"
        next_description = (
            f"Crear un traslado desde {state.current_warehouse.name} hacia "
            f"{state.expected_destination.name}."
            if state.current_warehouse and state.expected_destination
            else "Definir un traslado correctivo desde la ubicación actual."
        )
        next_tone = "danger"
    elif state.status == LogisticsPackageStatus.AT_POINT:
        next_title = "Asignar próximo traslado"
        next_description = "Selecciona un destino y un responsable ECUVEL activo."
        next_tone = "primary"
    elif state.status == LogisticsPackageStatus.ASSIGNED:
        next_title = "Confirmar recogida"
        next_description = "La custodia permanece en el punto hasta confirmar la recogida."
        next_tone = "warning"
    elif state.status == LogisticsPackageStatus.IN_TRANSIT:
        next_title = "Recepción mediante escaneo"
        next_description = (
            f"Esperando llegada a {state.expected_destination.name}."
            if state.expected_destination
            else "Esperando llegada al destino del traslado."
        )
        next_tone = "info"
    else:
        next_title, next_description, next_tone = (
            "Sin movimiento pendiente",
            "El paquete no requiere una acción logística inmediata.",
            "muted",
        )
    if state.custodian_user:
        custody_count = int(
            session.scalar(
                select(func.count(LogisticsPackageState.id)).where(
                    LogisticsPackageState.custodian_user_id
                    == state.custodian_user_id
                )
            )
            or 0
        )
        custodian_name = state.custodian_user.full_name
        custodian_code = state.custodian_user.public_account_code
        custodian_type = "Personal ECUVEL"
    else:
        custody_count = None
        custodian_name = (
            state.custodian_warehouse.name
            if state.custodian_warehouse
            else "Sin custodio"
        )
        custodian_code = (
            state.custodian_warehouse.code if state.custodian_warehouse else None
        )
        custodian_type = "Punto ECUVEL"
    origin_name = latest_transfer.origin_warehouse.name if latest_transfer else (
        state.current_warehouse.name if state.current_warehouse else None
    )
    active = latest_transfer and latest_transfer.status in ACTIVE_TRANSFER_STATUSES
    return AdminPackageTrackingDetail(
        package.package_code,
        package.barcode,
        package.seller_order.order.order_number,
        package.seller_order.seller_order_number,
        _status(state),
        (
            f"En tránsito hacia {state.expected_destination.name}"
            if state.status == LogisticsPackageStatus.IN_TRANSIT
            and state.expected_destination
            else state.current_warehouse.name
            if state.current_warehouse
            else "Ubicación no disponible"
        ),
        _duration(state.last_event_at, effective_now),
        state.current_warehouse.name if state.current_warehouse else None,
        state.current_location.name if state.current_location else None,
        origin_name,
        state.expected_destination.name if state.expected_destination else None,
        custodian_name,
        custodian_code,
        custodian_type,
        custody_count,
        latest_transfer.vehicle_code if latest_transfer else None,
        transfer_view,
        next_title,
        next_description,
        next_tone,
        tuple(event_views),
        tuple(
            option
            for option in _warehouse_options(session)
            if option.value != str(state.current_warehouse_id)
        ),
        _staff_options(session),
        state.status == LogisticsPackageStatus.AT_POINT and not state.is_deviated,
        state.is_deviated and state.current_warehouse_id is not None,
        bool(active and latest_transfer.status == LogisticsTransferStatus.ASSIGNED),
        bool(active and latest_transfer.status == LogisticsTransferStatus.ASSIGNED),
        bool(active and latest_transfer.status == LogisticsTransferStatus.IN_TRANSIT),
        True,
    )
