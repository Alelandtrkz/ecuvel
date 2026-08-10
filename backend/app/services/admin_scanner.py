from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    Order,
    OrderItem,
    OrderPackage,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    Store,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    LogisticsPackageStatus,
    LogisticsTransferStatus,
    PackageStatus,
    SellerInboundPackageStatus,
)
from app.services.fulfillment import order_ready_for_pickup_predicate


_PUBLIC_USER_RE = re.compile(r"^U-(\d{1,8})$", re.IGNORECASE)
_ACTIVE_TRANSFER_STATUSES = (
    LogisticsTransferStatus.ASSIGNED,
    LogisticsTransferStatus.IN_TRANSIT,
)


class AdminScannerError(Exception):
    pass


class AdminScannerValidationError(AdminScannerError):
    pass


@dataclass(frozen=True, slots=True)
class AdminScannerOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class AdminOperatingPoint:
    id: uuid.UUID
    code: str
    name: str
    city: str
    receiving_locations: tuple[AdminScannerOption, ...]

    @property
    def label(self) -> str:
        return f"{self.name} · {self.city}"


@dataclass(frozen=True, slots=True)
class AdminScannerHome:
    operating_point: AdminOperatingPoint | None
    warehouse_options: tuple[AdminScannerOption, ...]


@dataclass(frozen=True, slots=True)
class AdminInboundContentItem:
    code: str
    product_name: str
    quantity: int


@dataclass(frozen=True, slots=True)
class AdminInboundReceptionView:
    package_code: str
    barcode: str
    store_name: str
    order_number: str
    seller_order_number: str
    status_label: str
    status_tone: str
    current_location: str
    destination_name: str
    content: tuple[AdminInboundContentItem, ...]
    total_units: int
    receiving_locations: tuple[AdminScannerOption, ...]
    selected_location_id: str
    can_confirm: bool
    tracking_available: bool


@dataclass(frozen=True, slots=True)
class AdminTransportPickupView:
    package_code: str
    order_number: str
    status_label: str
    status_tone: str
    current_point: str
    transfer_code: str | None
    origin_name: str
    destination_name: str
    responsible_name: str
    responsible_code: str | None
    vehicle_code: str | None
    eta_at: datetime | None
    can_confirm: bool
    validation_message: str | None


@dataclass(frozen=True, slots=True)
class AdminTransferReceptionView:
    package_code: str
    order_number: str
    status_label: str
    status_tone: str
    transfer_code: str | None
    responsible_name: str
    origin_name: str
    destination_name: str
    operating_point_name: str
    is_deviation: bool
    receiving_locations: tuple[AdminScannerOption, ...]
    selected_location_id: str
    can_confirm: bool
    validation_message: str | None


@dataclass(frozen=True, slots=True)
class AdminHandoverPackageView:
    package_code: str
    barcode: str
    product_name: str
    location_code: str


@dataclass(frozen=True, slots=True)
class AdminHandoverOrderView:
    order_number: str
    packages: tuple[AdminHandoverPackageView, ...]


@dataclass(frozen=True, slots=True)
class AdminCustomerHandoverView:
    buyer_name: str
    buyer_code: str
    orders: tuple[AdminHandoverOrderView, ...]
    selected_order: AdminHandoverOrderView | None


@dataclass(frozen=True, slots=True)
class AdminPackageLookupView:
    kind: str
    type_label: str
    package_code: str
    barcode: str
    order_number: str
    product_name: str | None
    store_name: str | None
    status_label: str
    status_tone: str
    current_location: str
    custodian_name: str | None
    destination_name: str | None
    last_event: str | None
    next_movement: str | None
    is_deviated: bool
    fulfillment_available: bool


def normalize_scanned_code(value: str | None) -> str:
    return " ".join((value or "").strip().upper().split())[:120]


def _warehouse_options(session: Session) -> tuple[AdminScannerOption, ...]:
    return tuple(
        AdminScannerOption(str(warehouse.id), f"{warehouse.name} · {warehouse.city}")
        for warehouse in session.scalars(
            select(Warehouse)
            .where(Warehouse.is_active.is_(True))
            .order_by(Warehouse.name, Warehouse.code)
        )
    )


def get_operating_point(
    session: Session, warehouse_id: str | uuid.UUID | None
) -> AdminOperatingPoint | None:
    if not warehouse_id:
        return None
    try:
        parsed_id = uuid.UUID(str(warehouse_id))
    except (TypeError, ValueError):
        return None
    warehouse = session.scalar(
        select(Warehouse)
        .options(selectinload(Warehouse.locations))
        .where(Warehouse.id == parsed_id, Warehouse.is_active.is_(True))
    )
    if warehouse is None:
        return None
    receiving = tuple(
        AdminScannerOption(str(location.id), f"{location.name} · {location.code}")
        for location in sorted(
            (
                location
                for location in warehouse.locations
                if location.is_active
                and location.location_type == LocationType.RECEIVING
            ),
            key=lambda value: (value.name, value.code),
        )
    )
    return AdminOperatingPoint(
        warehouse.id,
        warehouse.code,
        warehouse.name,
        warehouse.city,
        receiving,
    )


def require_active_operating_point(
    session: Session, warehouse_id: str | uuid.UUID | None, *, lock: bool = False
) -> Warehouse:
    try:
        parsed_id = uuid.UUID(str(warehouse_id))
    except (TypeError, ValueError) as exc:
        raise AdminScannerValidationError(
            "Selecciona un punto operativo válido."
        ) from exc
    statement = select(Warehouse).where(Warehouse.id == parsed_id)
    if lock:
        statement = statement.with_for_update()
    warehouse = session.scalar(statement)
    if warehouse is None or not warehouse.is_active:
        raise AdminScannerValidationError(
            "El punto operativo seleccionado no está disponible."
        )
    return warehouse


def require_receiving_location(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    location_id: str | uuid.UUID | None,
    lock: bool = False,
) -> WarehouseLocation:
    try:
        parsed_id = uuid.UUID(str(location_id))
    except (TypeError, ValueError) as exc:
        raise AdminScannerValidationError(
            "Selecciona una ubicación de recepción válida."
        ) from exc
    statement = select(WarehouseLocation).where(WarehouseLocation.id == parsed_id)
    if lock:
        statement = statement.with_for_update()
    location = session.scalar(statement)
    if (
        location is None
        or not location.is_active
        or location.location_type != LocationType.RECEIVING
        or location.warehouse_id != warehouse_id
    ):
        raise AdminScannerValidationError(
            "La ubicación de recepción no pertenece al punto operativo actual."
        )
    return location


def get_admin_scanner_home(
    session: Session, *, warehouse_id: str | uuid.UUID | None
) -> AdminScannerHome:
    return AdminScannerHome(
        get_operating_point(session, warehouse_id),
        _warehouse_options(session),
    )


def _inbound_record(session: Session, code: str):
    normalized = normalize_scanned_code(code)
    if not normalized:
        return None
    return session.execute(
        select(SellerInboundPackage, SellerOrder, Order, Store, LogisticsPackageState)
        .join(SellerOrder, SellerOrder.id == SellerInboundPackage.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(Store, Store.id == SellerOrder.store_id)
        .outerjoin(
            LogisticsPackageState,
            LogisticsPackageState.seller_inbound_package_id
            == SellerInboundPackage.id,
        )
        .options(
            joinedload(LogisticsPackageState.current_warehouse),
            joinedload(LogisticsPackageState.current_location),
            joinedload(LogisticsPackageState.custodian_warehouse),
            joinedload(LogisticsPackageState.custodian_user),
            joinedload(LogisticsPackageState.expected_destination),
        )
        .where(
            or_(
                SellerInboundPackage.package_code == normalized,
                SellerInboundPackage.barcode == normalized,
            )
        )
    ).one_or_none()


def _active_transfer(session: Session, state_id: uuid.UUID) -> LogisticsTransfer | None:
    return session.scalar(
        select(LogisticsTransfer)
        .options(
            joinedload(LogisticsTransfer.origin_warehouse),
            joinedload(LogisticsTransfer.destination_warehouse),
            joinedload(LogisticsTransfer.assigned_user),
        )
        .where(
            LogisticsTransfer.package_state_id == state_id,
            LogisticsTransfer.status.in_(_ACTIVE_TRANSFER_STATUSES),
        )
        .order_by(LogisticsTransfer.assigned_at.desc(), LogisticsTransfer.id.desc())
    )


def _latest_transfer(session: Session, state_id: uuid.UUID) -> LogisticsTransfer | None:
    return session.scalar(
        select(LogisticsTransfer)
        .options(
            joinedload(LogisticsTransfer.origin_warehouse),
            joinedload(LogisticsTransfer.destination_warehouse),
            joinedload(LogisticsTransfer.assigned_user),
        )
        .where(LogisticsTransfer.package_state_id == state_id)
        .order_by(LogisticsTransfer.assigned_at.desc(), LogisticsTransfer.id.desc())
        .limit(1)
    )


def _inbound_status(package: SellerInboundPackage) -> tuple[str, str]:
    return {
        SellerInboundPackageStatus.CREATED: ("Etiqueta creada", "muted"),
        SellerInboundPackageStatus.READY_FOR_DROPOFF: (
            "Listo para entregar a ECUVEL",
            "warning",
        ),
        SellerInboundPackageStatus.RECEIVED_BY_ECUVEL: (
            "Recibido por ECUVEL",
            "success",
        ),
        SellerInboundPackageStatus.CANCELLED: ("Cancelado", "muted"),
    }[package.status]


def _logistics_status(state: LogisticsPackageState) -> tuple[str, str]:
    if state.is_deviated:
        return "Fuera de ruta", "danger"
    return {
        LogisticsPackageStatus.AT_POINT: ("En punto ECUVEL", "success"),
        LogisticsPackageStatus.ASSIGNED: ("Esperando recogida", "warning"),
        LogisticsPackageStatus.IN_TRANSIT: ("En tránsito", "info"),
        LogisticsPackageStatus.DEVIATED: ("Fuera de ruta", "danger"),
        LogisticsPackageStatus.DELIVERED: ("Entregado", "success"),
    }[state.status]


def _inbound_content(
    session: Session, package_id: uuid.UUID
) -> tuple[AdminInboundContentItem, ...]:
    rows = session.execute(
        select(SellerInboundPackageItem, OrderItem)
        .join(OrderItem, OrderItem.id == SellerInboundPackageItem.order_item_id)
        .where(SellerInboundPackageItem.package_id == package_id)
        .order_by(OrderItem.product_name_snapshot, OrderItem.id)
    ).all()
    return tuple(
        AdminInboundContentItem(
            item.seller_sku_snapshot,
            item.product_name_snapshot,
            link.quantity,
        )
        for link, item in rows
    )


def _selected_receiving_location(point: AdminOperatingPoint | None) -> str:
    if point and len(point.receiving_locations) == 1:
        return point.receiving_locations[0].value
    return ""


def get_admin_inbound_reception(
    session: Session,
    *,
    code: str,
    operating_point: AdminOperatingPoint | None,
) -> AdminInboundReceptionView | None:
    record = _inbound_record(session, code)
    if record is None:
        return None
    package, seller_order, order, store, state = record
    status_label, status_tone = (
        _logistics_status(state) if state else _inbound_status(package)
    )
    content = _inbound_content(session, package.id)
    locations = operating_point.receiving_locations if operating_point else ()
    selected = _selected_receiving_location(operating_point)
    return AdminInboundReceptionView(
        package.package_code,
        package.barcode,
        store.name,
        order.order_number,
        seller_order.seller_order_number,
        status_label,
        status_tone,
        (
            state.current_warehouse.name
            if state and state.current_warehouse
            else "Pendiente de recepción"
        ),
        (
            state.expected_destination.name
            if state and state.expected_destination
            else "Sin destino asignado"
        ),
        content,
        sum(item.quantity for item in content),
        locations,
        selected,
        bool(
            operating_point
            and locations
            and package.status == SellerInboundPackageStatus.READY_FOR_DROPOFF
        ),
        state is not None,
    )


def get_admin_transport_pickup(
    session: Session,
    *,
    code: str,
    operating_point: AdminOperatingPoint | None,
) -> AdminTransportPickupView | None:
    record = _inbound_record(session, code)
    if record is None:
        return None
    package, _seller_order, order, _store, state = record
    if state is None:
        return AdminTransportPickupView(
            package.package_code, order.order_number, "Sin trazabilidad", "muted",
            "Pendiente de recepción", None, "Sin origen", "Sin destino",
            "Sin responsable", None, None, None, False,
            "El paquete todavía no fue recibido por ECUVEL.",
        )
    transfer = _active_transfer(session, state.id)
    status_label, status_tone = _logistics_status(state)
    can_confirm = bool(
        operating_point
        and transfer
        and transfer.status == LogisticsTransferStatus.ASSIGNED
        and transfer.origin_warehouse_id == operating_point.id
        and state.current_warehouse_id == operating_point.id
    )
    if operating_point is None:
        message = "Selecciona el punto operativo antes de confirmar una salida."
    elif transfer is None:
        message = "El paquete no tiene un traslado asignado."
    elif transfer.status != LogisticsTransferStatus.ASSIGNED:
        message = "El traslado ya no está esperando recogida."
    elif (
        transfer.origin_warehouse_id != operating_point.id
        or state.current_warehouse_id != operating_point.id
    ):
        message = "El paquete no se encuentra en el punto operativo actual."
    else:
        message = None
    return AdminTransportPickupView(
        package.package_code,
        order.order_number,
        status_label,
        status_tone,
        state.current_warehouse.name if state.current_warehouse else "En tránsito",
        transfer.transfer_code if transfer else None,
        transfer.origin_warehouse.name if transfer else "Sin origen",
        transfer.destination_warehouse.name if transfer else "Sin destino",
        transfer.assigned_user.full_name if transfer else "Sin responsable",
        transfer.assigned_user.public_account_code if transfer else None,
        transfer.vehicle_code if transfer else None,
        transfer.eta_at if transfer else None,
        can_confirm,
        message,
    )


def get_admin_transfer_reception(
    session: Session,
    *,
    code: str,
    operating_point: AdminOperatingPoint | None,
) -> AdminTransferReceptionView | None:
    record = _inbound_record(session, code)
    if record is None:
        return None
    package, _seller_order, order, _store, state = record
    transfer = _active_transfer(session, state.id) if state else None
    if state is None or transfer is None:
        return AdminTransferReceptionView(
            package.package_code, order.order_number, "Sin traslado activo", "muted",
            None, "Sin responsable", "Sin origen", "Sin destino",
            operating_point.label if operating_point else "Sin punto operativo",
            False, operating_point.receiving_locations if operating_point else (),
            _selected_receiving_location(operating_point), False,
            "El paquete no tiene un traslado activo.",
        )
    status_label, status_tone = _logistics_status(state)
    locations = operating_point.receiving_locations if operating_point else ()
    can_confirm = bool(
        operating_point
        and locations
        and transfer.status == LogisticsTransferStatus.IN_TRANSIT
        and state.status == LogisticsPackageStatus.IN_TRANSIT
    )
    if operating_point is None:
        message = "Selecciona el punto operativo antes de registrar la llegada."
    elif not locations:
        message = "Este punto no tiene una ubicación de recepción activa."
    elif (
        transfer.status != LogisticsTransferStatus.IN_TRANSIT
        or state.status != LogisticsPackageStatus.IN_TRANSIT
    ):
        message = "El paquete no está en tránsito."
    else:
        message = None
    return AdminTransferReceptionView(
        package.package_code,
        order.order_number,
        status_label,
        status_tone,
        transfer.transfer_code,
        transfer.assigned_user.full_name,
        transfer.origin_warehouse.name,
        transfer.destination_warehouse.name,
        operating_point.label if operating_point else "Sin punto operativo",
        bool(operating_point and operating_point.id != transfer.destination_warehouse_id),
        locations,
        _selected_receiving_location(operating_point),
        can_confirm,
        message,
    )


def find_buyer_by_public_code(session: Session, code: str) -> User | None:
    normalized = normalize_scanned_code(code)
    match = _PUBLIC_USER_RE.fullmatch(normalized)
    if match is None:
        return None
    return session.scalar(
        select(User).where(User.registration_number == int(match.group(1)))
    )


def _ready_orders_for_buyer(
    session: Session,
    *,
    buyer_id: uuid.UUID,
    warehouse_id: uuid.UUID,
) -> tuple[AdminHandoverOrderView, ...]:
    orders = tuple(
        session.scalars(
            select(Order)
            .where(
                Order.buyer_id == buyer_id,
                order_ready_for_pickup_predicate(),
            )
            .order_by(Order.created_at.desc(), Order.id.desc())
        )
    )
    if not orders:
        return ()
    order_ids = tuple(order.id for order in orders)
    rows = session.execute(
        select(Order, OrderPackage, OrderItem, WarehouseLocation)
        .join(SellerOrder, SellerOrder.order_id == Order.id)
        .join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        .join(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
        .join(WarehouseLocation, WarehouseLocation.id == OrderPackage.pickup_location_id)
        .where(Order.id.in_(order_ids))
        .order_by(Order.created_at.desc(), OrderPackage.created_at, OrderPackage.id)
    ).all()
    packages_by_order: dict[uuid.UUID, list[AdminHandoverPackageView]] = {}
    valid_by_order = {order.id: True for order in orders}
    for order, package, item, location in rows:
        if (
            package.status != PackageStatus.READY_FOR_PICKUP
            or location.warehouse_id != warehouse_id
            or not location.is_active
        ):
            valid_by_order[order.id] = False
        packages_by_order.setdefault(order.id, []).append(
            AdminHandoverPackageView(
                package.package_code,
                package.barcode,
                item.product_name_snapshot,
                location.code,
            )
        )
    return tuple(
        AdminHandoverOrderView(order.order_number, tuple(packages_by_order[order.id]))
        for order in orders
        if valid_by_order.get(order.id) and packages_by_order.get(order.id)
    )


def get_admin_customer_handover(
    session: Session,
    *,
    buyer_code: str,
    operating_point: AdminOperatingPoint | None,
    order_number: str | None = None,
) -> AdminCustomerHandoverView | None:
    buyer = find_buyer_by_public_code(session, buyer_code)
    if buyer is None:
        return None
    orders = (
        _ready_orders_for_buyer(
            session,
            buyer_id=buyer.id,
            warehouse_id=operating_point.id,
        )
        if operating_point
        else ()
    )
    selected = None
    normalized_order = (order_number or "").strip().upper()
    if normalized_order:
        selected = next(
            (
                order
                for order in orders
                if order.order_number.upper() == normalized_order
            ),
            None,
        )
    elif len(orders) == 1:
        selected = orders[0]
    return AdminCustomerHandoverView(
        buyer.full_name,
        buyer.public_account_code,
        orders,
        selected,
    )


def get_admin_package_lookup(
    session: Session, *, code: str
) -> AdminPackageLookupView | None:
    normalized = normalize_scanned_code(code)
    if not normalized:
        return None
    inbound = _inbound_record(session, normalized)
    outbound_records = session.execute(
        select(OrderPackage, OrderItem, SellerOrder, Order, Store)
        .join(OrderItem, OrderItem.id == OrderPackage.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Order, Order.id == SellerOrder.order_id)
        .join(Store, Store.id == SellerOrder.store_id)
        .options(joinedload(OrderPackage.pickup_location))
        .where(
            or_(
                OrderPackage.package_code == normalized,
                OrderPackage.barcode == normalized,
            )
        )
    ).all()
    outbound = outbound_records[0] if len(outbound_records) == 1 else None
    if len(outbound_records) > 1 or (inbound and outbound):
        return AdminPackageLookupView(
            "ambiguous", "Código ambiguo", normalized, normalized, "—", None,
            None, "Requiere revisión", "danger", "No disponible", None, None,
            None, None, False, False,
        )
    if inbound:
        package, _seller_order, order, store, state = inbound
        status_label, status_tone = (
            _logistics_status(state) if state else _inbound_status(package)
        )
        transfer = _active_transfer(session, state.id) if state else None
        latest = session.scalar(
            select(LogisticsTrackingEvent)
            .where(LogisticsTrackingEvent.package_state_id == state.id)
            .order_by(
                LogisticsTrackingEvent.occurred_at.desc(),
                LogisticsTrackingEvent.id.desc(),
            )
            .limit(1)
        ) if state else None
        return AdminPackageLookupView(
            "inbound",
            "Paquete de entrada / red ECUVEL",
            package.package_code,
            package.barcode,
            order.order_number,
            None,
            store.name,
            status_label,
            status_tone,
            (
                f"En tránsito hacia {state.expected_destination.name}"
                if state
                and state.status == LogisticsPackageStatus.IN_TRANSIT
                and state.expected_destination
                else state.current_warehouse.name
                if state and state.current_warehouse
                else "Pendiente de recepción"
            ),
            (
                state.custodian_user.full_name
                if state and state.custodian_user
                else state.custodian_warehouse.name
                if state and state.custodian_warehouse
                else None
            ),
            state.expected_destination.name if state and state.expected_destination else None,
            latest.event_type.value if latest else None,
            (
                f"Recepción en {transfer.destination_warehouse.name}"
                if transfer and transfer.status == LogisticsTransferStatus.IN_TRANSIT
                else "Confirmar salida"
                if transfer and transfer.status == LogisticsTransferStatus.ASSIGNED
                else "Asignar traslado"
                if state and state.status == LogisticsPackageStatus.AT_POINT
                else None
            ),
            bool(state and state.is_deviated),
            state is not None,
        )
    if outbound:
        package, item, _seller_order, order, store = outbound
        status_values = {
            PackageStatus.CREATED: ("Creado", "muted"),
            PackageStatus.PACKED: ("Empacado", "info"),
            PackageStatus.READY_FOR_PICKUP: ("Listo para retirar", "success"),
            PackageStatus.HANDED_OVER: ("Entregado", "success"),
            PackageStatus.CANCELLED: ("Cancelado", "muted"),
        }
        status_label, status_tone = status_values[package.status]
        return AdminPackageLookupView(
            "outbound",
            "Paquete para retiro",
            package.package_code,
            package.barcode,
            order.order_number,
            item.product_name_snapshot,
            store.name,
            status_label,
            status_tone,
            package.pickup_location.name if package.pickup_location else "Pendiente",
            None,
            None,
            "Preparado para retiro" if package.ready_at else None,
            "Entrega al cliente" if package.status == PackageStatus.READY_FOR_PICKUP else None,
            False,
            False,
        )
    return None
