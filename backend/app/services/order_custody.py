"""Physical evidence shared by seller reception and customer fulfillment."""

from sqlalchemy import and_, func, or_, select

from app.models import (
    LogisticsPackageState, Order, OrderPackage, PaymentAttempt, SellerInboundPackage,
    SellerInboundPackageItem, Warehouse, WarehouseLocation,
)
from app.models.enums import (
    LocationType, LogisticsPackageStatus, OrderStatus, PackageStatus, PaymentStatus, SellerInboundPackageStatus,
)


def paid_order_condition():
    return and_(
        Order.status.in_((OrderStatus.CONFIRMED, OrderStatus.FULFILLING, OrderStatus.READY_FOR_PICKUP)),
        select(PaymentAttempt.id).where(
            PaymentAttempt.order_id == Order.id,
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
            PaymentAttempt.amount == Order.grand_total,
            PaymentAttempt.currency == Order.currency,
        ).correlate(Order).exists(),
    )


def _received_lines(item_id, seller_order_id):
    return (
        select(SellerInboundPackageItem.quantity, LogisticsPackageState)
        .join(SellerInboundPackage, SellerInboundPackage.id == SellerInboundPackageItem.package_id)
        .join(LogisticsPackageState, LogisticsPackageState.seller_inbound_package_id == SellerInboundPackage.id)
        .where(
            SellerInboundPackageItem.order_item_id == item_id,
            SellerInboundPackage.seller_order_id == seller_order_id,
            SellerInboundPackage.status == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
            SellerInboundPackage.received_location_id.is_not(None),
            SellerInboundPackage.received_by_user_id.is_not(None),
        )
    )


def received_allocations(session, item, *, for_pickup_at=None):
    statement = _received_lines(item.id, item.seller_order_id).order_by(LogisticsPackageState.id)
    if for_pickup_at is not None:
        statement = statement.where(custody_at_destination(for_pickup_at))
        statement = statement.with_for_update(of=LogisticsPackageState).execution_options(populate_existing=True)
    return session.execute(statement).all()


def custody_at_destination(destination_id):
    state = LogisticsPackageState
    usable_location = state.current_location.has(and_(
        WarehouseLocation.warehouse_id == state.current_warehouse_id,
        WarehouseLocation.is_active.is_(True),
        WarehouseLocation.location_type.not_in((LocationType.DAMAGED, LocationType.QUARANTINE, LocationType.RETURNS)),
    ))
    return and_(
        state.status == LogisticsPackageStatus.AT_POINT, state.is_deviated.is_(False),
        state.current_warehouse_id == destination_id, state.custodian_warehouse_id == destination_id,
        state.custodian_user_id.is_(None),
        or_(state.expected_destination_warehouse_id.is_(None), state.expected_destination_warehouse_id == destination_id),
        or_(state.current_location_id.is_(None), usable_location),
    )


def units_in_pickup_custody(item, order):
    return (
        _received_lines(item.id, item.seller_order_id)
        .with_only_columns(func.coalesce(func.sum(SellerInboundPackageItem.quantity), 0))
        .where(custody_at_destination(order.pickup_warehouse_id))
        .correlate(item, order).scalar_subquery()
    )


def outbound_started(inbound_id):
    return (
        select(SellerInboundPackageItem.package_id)
        .join(OrderPackage, OrderPackage.order_item_id == SellerInboundPackageItem.order_item_id)
        .where(
            SellerInboundPackageItem.package_id == inbound_id,
            OrderPackage.status.in_((PackageStatus.PACKED, PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER)),
        )
        .correlate_except(SellerInboundPackageItem, OrderPackage).exists()
    )


def fully_staged_inbound(inbound_id):
    links = select(SellerInboundPackageItem.package_id).where(
        SellerInboundPackageItem.package_id == inbound_id
    ).correlate_except(SellerInboundPackageItem)
    missing = links.outerjoin(
        OrderPackage, OrderPackage.order_item_id == SellerInboundPackageItem.order_item_id
    ).where(
        (OrderPackage.id.is_(None)) | (OrderPackage.status != PackageStatus.READY_FOR_PICKUP)
    ).correlate_except(SellerInboundPackageItem, OrderPackage)
    return and_(links.exists(), ~missing.exists())


def active_pickup_point(session, warehouse_id):
    return session.scalar(
        select(Warehouse.id).where(
            Warehouse.id == warehouse_id, Warehouse.is_active.is_(True), Warehouse.seller_store_id.is_(None)
        ).with_for_update(read=True)
    )
