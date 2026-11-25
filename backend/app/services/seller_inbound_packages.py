from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.services.admin_operating_context import operational_scope
from app.services.admin_permissions import require_actor_permission
from app.models import (
    InventoryReservation,
    Order,
    OrderItem,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    User,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    ReservationStatus,
    SellerInboundPackageStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    UserStatus,
)
from app.services.partner_orders import (
    PartnerOrderConflictError,
    PartnerOrderNotFoundError,
    PartnerOrderValidationError,
    require_partner_order_store,
)
from app.services.inventory import InventoryServiceError, pick_inventory_reservation
from app.services.logistics_tracking import register_received_inbound_package
from app.services.order_custody import active_pickup_point, paid_order_condition, received_allocations
from app.services.public_identifiers import format_seller_inbound_package_code


PACKAGE_STATUS_LABELS = {
    SellerInboundPackageStatus.CREATED: "Etiqueta lista",
    SellerInboundPackageStatus.READY_FOR_DROPOFF: "Listo para entregar a Ecuvel",
    SellerInboundPackageStatus.RECEIVED_BY_ECUVEL: "Recibido por Ecuvel",
    SellerInboundPackageStatus.CANCELLED: "Cancelado",
}


class SellerInboundPackageReceptionAccessError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PartnerInboundPackageItemResult:
    order_item_id: uuid.UUID
    product_name: str
    sku: str
    quantity: int


@dataclass(frozen=True, slots=True)
class PartnerInboundPackageResult:
    package_id: uuid.UUID
    seller_order_id: uuid.UUID
    package_code: str
    barcode: str
    status: SellerInboundPackageStatus
    status_label: str
    items: tuple[PartnerInboundPackageItemResult, ...]
    ready_for_dropoff_at: datetime | None
    received_at: datetime | None
    received_location_id: uuid.UUID | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class PartnerInboundPackageLabel:
    package_id: uuid.UUID
    package_code: str
    barcode: str
    seller_order_id: uuid.UUID
    seller_order_number: str
    order_number: str


def _result(
    package: SellerInboundPackage,
    *,
    order_items: dict[uuid.UUID, OrderItem],
    replayed: bool,
) -> PartnerInboundPackageResult:
    return PartnerInboundPackageResult(
        package_id=package.id,
        seller_order_id=package.seller_order_id,
        package_code=package.package_code,
        barcode=package.barcode,
        status=package.status,
        status_label=PACKAGE_STATUS_LABELS[package.status],
        items=tuple(
            PartnerInboundPackageItemResult(
                order_item_id=link.order_item_id,
                product_name=order_items[link.order_item_id].product_name_snapshot,
                sku=order_items[link.order_item_id].seller_sku_snapshot,
                quantity=link.quantity,
            )
            for link in sorted(package.items, key=lambda item: str(item.order_item_id))
        ),
        ready_for_dropoff_at=package.ready_for_dropoff_at,
        received_at=package.received_at,
        received_location_id=package.received_location_id,
        replayed=replayed,
    )


def _locked_partner_order(
    session: Session,
    *,
    seller_order_id: uuid.UUID,
    store_id: uuid.UUID,
) -> SellerOrder:
    seller_order = session.scalar(
        select(SellerOrder)
        .where(
            SellerOrder.id == seller_order_id,
            SellerOrder.store_id == store_id,
        )
        .with_for_update().execution_options(populate_existing=True)
    )
    if seller_order is None:
        raise PartnerOrderNotFoundError("No existe el pedido solicitado.")
    return seller_order


def _locked_partner_package(
    session: Session,
    *,
    package_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    store_id: uuid.UUID,
) -> tuple[SellerInboundPackage, SellerOrder]:
    seller_order = _locked_partner_order(
        session, seller_order_id=seller_order_id, store_id=store_id
    )
    package = session.scalar(
        select(SellerInboundPackage)
        .options(selectinload(SellerInboundPackage.items))
        .where(
            SellerInboundPackage.id == package_id,
            SellerInboundPackage.seller_order_id == seller_order.id,
        )
        .with_for_update().execution_options(populate_existing=True)
    )
    if package is None:
        raise PartnerOrderNotFoundError("No existe el paquete solicitado.")
    return package, seller_order


def _package_order_items(
    session: Session, package: SellerInboundPackage
) -> dict[uuid.UUID, OrderItem]:
    item_ids = [link.order_item_id for link in package.items]
    return {
        item.id: item
        for item in session.scalars(
            select(OrderItem).where(OrderItem.id.in_(item_ids))
        )
    }


def _require_fulfillable(session, seller_order):
    if (
        seller_order.status not in (SellerOrderStatus.CONFIRMED, SellerOrderStatus.PICKING,
                                    SellerOrderStatus.PACKED, SellerOrderStatus.READY_FOR_PICKUP)
        or seller_order.decision_status != SellerOrderDecisionStatus.APPROVED
        or not session.scalar(select(Order.id).where(Order.id == seller_order.order_id, paid_order_condition()))
    ):
        raise PartnerOrderConflictError("El pedido requiere pago confirmado y aprobación de la tienda.")


def create_partner_inbound_package(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    allocations: Sequence[tuple[uuid.UUID, int]] | None = None,
    request_id: uuid.UUID | None = None,
) -> PartnerInboundPackageResult:
    access = require_partner_order_store(session, user_id, require_manage=True)
    seller_order = _locked_partner_order(
        session,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
    )
    _require_fulfillable(session, seller_order)

    order_items = list(
        session.scalars(
            select(OrderItem)
            .where(OrderItem.seller_order_id == seller_order.id)
            .order_by(OrderItem.created_at, OrderItem.id)
            .with_for_update()
        )
    )
    if not order_items:
        raise PartnerOrderConflictError("El pedido no contiene productos.")
    items_by_id = {item.id: item for item in order_items}
    assigned = {
        order_item_id: int(quantity)
        for order_item_id, quantity in session.execute(
            select(
                SellerInboundPackageItem.order_item_id,
                func.sum(SellerInboundPackageItem.quantity),
            )
            .join(
                SellerInboundPackage,
                SellerInboundPackage.id == SellerInboundPackageItem.package_id,
            )
            .where(
                SellerInboundPackage.seller_order_id == seller_order.id,
                SellerInboundPackage.status != SellerInboundPackageStatus.CANCELLED,
            )
            .group_by(SellerInboundPackageItem.order_item_id)
        )
    }
    remaining = {
        item.id: item.quantity - assigned.get(item.id, 0) for item in order_items
    }
    if any(quantity < 0 for quantity in remaining.values()):
        raise PartnerOrderConflictError(
            "Las cantidades ya asignadas a paquetes son inconsistentes."
        )

    # Two identical partial allocations can be two real boxes. The request ID identifies the box on retry.
    if allocations is not None and not isinstance(request_id, uuid.UUID):
        raise PartnerOrderValidationError("La preparación parcial requiere un identificador de solicitud.")
    package_id = uuid.uuid5(seller_order.id, str(request_id)) if request_id else uuid.uuid4()
    existing = session.get(SellerInboundPackage, package_id) if request_id else None
    if allocations is None:
        if existing is not None:
            return _result(existing, order_items=items_by_id, replayed=True)
        normalized = tuple(
            (item.id, remaining[item.id])
            for item in order_items
            if remaining[item.id] > 0
        )
    else:
        normalized_values: list[tuple[uuid.UUID, int]] = []
        seen: set[uuid.UUID] = set()
        for raw_item_id, raw_quantity in allocations:
            try:
                item_id = uuid.UUID(str(raw_item_id))
                if isinstance(raw_quantity, bool) or not str(raw_quantity).strip().isdigit():
                    raise ValueError
                quantity = int(raw_quantity)
            except (TypeError, ValueError) as exc:
                raise PartnerOrderValidationError(
                    "Las cantidades del paquete no son válidas."
                ) from exc
            if item_id in seen:
                raise PartnerOrderValidationError(
                    "Una línea no puede repetirse dentro del mismo paquete."
                )
            seen.add(item_id)
            if item_id not in items_by_id:
                raise PartnerOrderValidationError(
                    "Uno de los productos no pertenece a este pedido."
                )
            if quantity <= 0:
                raise PartnerOrderValidationError(
                    "Cada cantidad del paquete debe ser mayor que cero."
                )
            if existing is None and quantity > remaining[item_id]:
                raise PartnerOrderValidationError(
                    "La cantidad asignada supera las unidades pendientes del pedido."
                )
            normalized_values.append((item_id, quantity))
        normalized = tuple(normalized_values)
        if existing is not None:
            if dict(normalized) != {link.order_item_id: link.quantity for link in existing.items}:
                raise PartnerOrderConflictError("La solicitud ya fue usada con otras cantidades.")
            return _result(existing, order_items=items_by_id, replayed=True)

    if not normalized:
        raise PartnerOrderConflictError(
            "Todos los productos del pedido ya están asignados a paquetes activos."
        )

    sequence_value = session.scalar(
        text("SELECT nextval('seller_inbound_package_public_seq')")
    )
    package_code = format_seller_inbound_package_code(int(sequence_value))
    package = SellerInboundPackage(
        id=package_id,
        seller_order_id=seller_order.id,
        package_code=package_code,
        barcode=package_code,
        status=SellerInboundPackageStatus.CREATED,
        created_by_user_id=user_id,
    )
    session.add(package)
    session.flush()
    for item_id, quantity in normalized:
        session.add(
            SellerInboundPackageItem(
                package_id=package.id,
                order_item_id=item_id,
                quantity=quantity,
            )
        )
    session.flush()
    session.refresh(package, attribute_names=["items"])
    return _result(package, order_items=items_by_id, replayed=False)


def mark_partner_inbound_package_ready(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    package_id: uuid.UUID,
    now: datetime | None = None,
) -> PartnerInboundPackageResult:
    access = require_partner_order_store(session, user_id, require_manage=True)
    package, seller_order = _locked_partner_package(
        session,
        package_id=package_id,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
    )
    _require_fulfillable(session, seller_order)
    if package.status == SellerInboundPackageStatus.READY_FOR_DROPOFF:
        return _result(
            package,
            order_items=_package_order_items(session, package),
            replayed=True,
        )
    if package.status != SellerInboundPackageStatus.CREATED:
        raise PartnerOrderConflictError(
            "Este paquete ya no puede marcarse como listo para entrega."
        )
    package.status = SellerInboundPackageStatus.READY_FOR_DROPOFF
    package.ready_for_dropoff_at = now or datetime.now(timezone.utc)
    package.ready_for_dropoff_by_user_id = user_id
    session.flush()
    return _result(
        package,
        order_items=_package_order_items(session, package),
        replayed=False,
    )


def get_partner_inbound_package_label(
    session: Session,
    *,
    user_id: uuid.UUID,
    seller_order_id: uuid.UUID,
    package_id: uuid.UUID,
) -> PartnerInboundPackageLabel:
    access = require_partner_order_store(session, user_id, require_manage=True)
    package, seller_order = _locked_partner_package(
        session,
        package_id=package_id,
        seller_order_id=seller_order_id,
        store_id=access.store_id,
    )
    order = session.get(Order, seller_order.order_id)
    if order is None:
        raise PartnerOrderNotFoundError("No existe el pedido del paquete.")
    return PartnerInboundPackageLabel(
        package_id=package.id,
        package_code=package.package_code,
        barcode=package.barcode,
        seller_order_id=seller_order.id,
        seller_order_number=seller_order.seller_order_number,
        order_number=order.order_number,
    )


def receive_seller_inbound_package(
    session: Session,
    *,
    package_code: str,
    received_location_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    verified_product_codes: Sequence[str],
    expected_warehouse_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> PartnerInboundPackageResult:
    """Internal ECUVEL operation. It is deliberately not exposed to Partners."""

    actor = session.scalar(
        select(User).where(User.id == actor_user_id).with_for_update(read=True)
    )
    if (
        actor is None
        or actor.status != UserStatus.ACTIVE
        or not actor.is_active
        or not actor.is_ecuvel_staff
    ):
        raise SellerInboundPackageReceptionAccessError(
            "Solo un operador interno de Ecuvel puede confirmar la recepción."
        )
    actor = require_actor_permission(session, actor_user_id, "scanner.receive_package")
    location = session.scalar(
        select(WarehouseLocation)
        .where(WarehouseLocation.id == received_location_id)
        .with_for_update(read=True).execution_options(populate_existing=True)
    )
    if (
        location is None
        or not location.is_active
        or location.location_type != LocationType.RECEIVING
        or not active_pickup_point(session, location.warehouse_id)
    ):
        raise PartnerOrderValidationError(
            "La ubicación de recepción de Ecuvel no es válida."
        )
    if (
        expected_warehouse_id is not None
        and location.warehouse_id != expected_warehouse_id
    ):
        raise PartnerOrderValidationError(
            "La ubicación de recepción no pertenece al punto operativo actual."
        )
    scope = operational_scope(session, actor)
    scope.require_point(location.warehouse_id)
    normalized_code = (package_code or "").strip().upper()
    order = session.scalar(
        select(Order).join(SellerOrder).join(SellerInboundPackage)
        .where(SellerInboundPackage.package_code == normalized_code)
        .with_for_update(of=Order)
    )
    if order is None:
        raise PartnerOrderNotFoundError("No existe el paquete escaneado.")
    seller_order = session.scalar(
        select(SellerOrder).join(SellerInboundPackage)
        .where(SellerInboundPackage.package_code == normalized_code)
        .with_for_update(of=SellerOrder).execution_options(populate_existing=True)
    )
    package = session.scalar(
        select(SellerInboundPackage)
        .options(selectinload(SellerInboundPackage.items))
        .where(SellerInboundPackage.package_code == normalized_code)
        .with_for_update().execution_options(populate_existing=True)
    )
    if package is None:
        raise PartnerOrderNotFoundError("No existe el paquete escaneado.")
    if package.received_location_id and scope.warehouse_ids is not None:
        recorded = session.get(WarehouseLocation, package.received_location_id)
        scope.require_point(recorded.warehouse_id if recorded else None)
    order_items = _package_order_items(session, package)
    if not order_items or any(
        item.seller_order_id != seller_order.id or item.store_id_snapshot != seller_order.store_id
        for item in order_items.values()
    ):
        raise PartnerOrderConflictError("El contenido no corresponde al subpedido.")
    expected_codes: Counter[str] = Counter()
    for link in package.items:
        expected_codes[order_items[link.order_item_id].seller_sku_snapshot] += (
            link.quantity
        )
    scanned_codes = Counter(
        code.strip()
        for code in verified_product_codes
        if isinstance(code, str) and code.strip()
    )
    if scanned_codes != expected_codes:
        raise PartnerOrderValidationError(
            "Los productos escaneados no coinciden con el contenido esperado."
        )
    replayed = package.status == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
    if replayed and location.id != package.received_location_id:
        raise PartnerOrderConflictError("La recepción original corresponde a otra ubicación.")
    if not replayed and package.status != SellerInboundPackageStatus.READY_FOR_DROPOFF:
        raise PartnerOrderConflictError("El paquete debe estar listo para Drop-off antes de recibirlo.")
    if replayed and seller_order.status == SellerOrderStatus.COMPLETED:
        return _result(package, order_items=order_items, replayed=True)
    _require_fulfillable(session, seller_order)
    with session.begin_nested():
        if not replayed:
            package.status = SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
            package.received_at = now or datetime.now(timezone.utc)
            package.received_by_user_id = actor.id
            package.received_location_id = location.id
            session.flush()
        register_received_inbound_package(
            session, package_id=package.id, location_id=location.id,
            actor_user_id=actor.id, now=package.received_at,
            idempotency_key=f"seller-reception:{package.id}",
        )
        _pick_received_lines(session, order_items, package.package_code, actor.id)
    return _result(package, order_items=order_items, replayed=replayed)


def _pick_received_lines(session, order_items, package_code, actor_id):
    # Reservations are indivisible. Keep a split line encumbered until its last verified unit arrives.
    reservations = session.scalars(
        select(InventoryReservation).where(InventoryReservation.order_item_id.in_(order_items))
        .order_by(InventoryReservation.balance_id, InventoryReservation.id).with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    complete_items = set()
    for item in order_items.values():
        received = sum(quantity for quantity, _ in received_allocations(session, item))
        item_reservations = [r for r in reservations if r.order_item_id == item.id]
        if (
            received > item.quantity or sum(r.quantity for r in item_reservations) != item.quantity
            or any(r.status != ReservationStatus.CONSUMED for r in item_reservations)
        ):
            raise PartnerOrderConflictError("La reserva pagada no coincide con las unidades del pedido.")
        if received == item.quantity:
            complete_items.add(item.id)
    try:
        for reservation in reservations:
            if reservation.order_item_id in complete_items:
                pick_inventory_reservation(
                    session=session, reservation_id=reservation.id, actor_user_id=actor_id,
                    notes=f"Recepción verificada {package_code}",
                )
    except InventoryServiceError as exc:
        raise PartnerOrderConflictError(str(exc)) from exc
