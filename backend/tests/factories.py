from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Category,
    InventoryBalance,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    SellerOffer,
    SellerOrder,
    Store,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    OfferStatus,
    StoreStatus,
    UserStatus,
)
from app.services.fulfillment import (
    create_packages_for_order,
    handover_order_packages,
    pack_order_package,
    stage_order_package_for_pickup,
)
from app.services.inventory import (
    consume_inventory_reservation,
    pick_inventory_reservation,
    reserve_inventory,
)


@dataclass(frozen=True, slots=True)
class BaseData:
    buyer_id: uuid.UUID
    operator_id: uuid.UUID
    store_id: uuid.UUID
    offer_id: uuid.UUID
    warehouse_id: uuid.UUID
    receiving_location_id: uuid.UUID
    storage_location_id: uuid.UUID
    pickup_location_id: uuid.UUID
    balance_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ReadyOrderData:
    order_id: uuid.UUID
    order_number: str
    order_item_ids: tuple[uuid.UUID, ...]
    package_codes: tuple[str, ...]
    barcodes: tuple[str, ...]


def _token() -> str:
    return uuid.uuid4().hex[:12]


def create_catalog_and_stock(
    session: Session,
    *,
    stock: int = 20,
) -> BaseData:
    token = _token()
    buyer = User(
        public_code=f"BUY-{token}",
        email=f"buyer-{token}@test.local",
        password_hash="test",
        full_name="Buyer Test",
        status=UserStatus.ACTIVE,
    )
    operator = User(
        public_code=f"OPS-{token}",
        email=f"operator-{token}@test.local",
        password_hash="test",
        full_name="Operator Test",
        status=UserStatus.ACTIVE,
    )
    store = Store(
        public_code=f"STR-{token}",
        name=f"Store {token}",
        slug=f"store-{token}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    category = Category(
        code=f"CAT-{token}",
        name="Category Test",
        slug=f"category-{token}",
        is_active=True,
        sort_order=1,
    )
    session.add_all([buyer, operator, store, category])
    session.flush()

    product = Product(
        category_id=category.id,
        title="Product Test",
        slug=f"product-{token}",
        is_active=True,
    )
    session.add(product)
    session.flush()

    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"SKU-{token}",
        attributes={},
        is_active=True,
    )
    session.add(variant)
    session.flush()

    offer = SellerOffer(
        store_id=store.id,
        variant_id=variant.id,
        seller_sku=f"SELL-{token}",
        currency="USD",
        price=Decimal("10.00"),
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE,
    )
    warehouse = Warehouse(
        code=f"WH-{token}",
        name="Warehouse Test",
        address_line="Test address",
        city="Quito",
        country_code="EC",
        is_active=True,
    )
    session.add_all([offer, warehouse])
    session.flush()

    receiving = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"REC-{token}",
        barcode=f"LOC-REC-{token}",
        name="Receiving Test",
        location_type=LocationType.RECEIVING,
        capacity_units=1000,
        allows_mixed_offers=True,
        is_active=True,
    )
    storage = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"STO-{token}",
        barcode=f"LOC-STO-{token}",
        name="Storage Test",
        location_type=LocationType.STORAGE,
        capacity_units=1000,
        allows_mixed_offers=True,
        is_active=True,
    )
    pickup = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"PUP-{token}",
        barcode=f"LOC-PUP-{token}",
        name="Pickup Test",
        location_type=LocationType.PICKUP_STAGING,
        capacity_units=1000,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add_all([receiving, storage, pickup])
    session.flush()

    balance = InventoryBalance(
        offer_id=offer.id,
        location_id=storage.id,
        on_hand_quantity=stock,
        reserved_quantity=0,
        blocked_quantity=0,
    )
    session.add(balance)
    session.flush()

    return BaseData(
        buyer_id=buyer.id,
        operator_id=operator.id,
        store_id=store.id,
        offer_id=offer.id,
        warehouse_id=warehouse.id,
        receiving_location_id=receiving.id,
        storage_location_id=storage.id,
        pickup_location_id=pickup.id,
        balance_id=balance.id,
    )


def create_order_items(
    session: Session,
    base: BaseData,
    quantities: list[int],
) -> tuple[uuid.UUID, str, tuple[uuid.UUID, ...]]:
    token = _token()
    subtotal = Decimal(sum(quantities) * 10)
    order = Order(
        order_number=f"ORD-{token}",
        buyer_id=base.buyer_id,
        currency="USD",
        subtotal=subtotal,
        discount_total=Decimal("0.00"),
        shipping_total=Decimal("0.00"),
        tax_total=Decimal("0.00"),
        grand_total=subtotal,
    )
    session.add(order)
    session.flush()

    seller_order = SellerOrder(
        seller_order_number=f"SORD-{token}",
        order_id=order.id,
        store_id=base.store_id,
        subtotal=subtotal,
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        seller_net_total=subtotal,
    )
    session.add(seller_order)
    session.flush()

    items: list[OrderItem] = []

    for index, quantity in enumerate(quantities):
        total = Decimal(quantity * 10)
        item = OrderItem(
            seller_order_id=seller_order.id,
            offer_id=base.offer_id,
            quantity=quantity,
            unit_price=Decimal("10.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            line_total=total,
            product_name_snapshot=f"Product {index}",
            seller_name_snapshot="Store Test",
            seller_sku_snapshot=f"ITEM-{token}-{index}",
            variant_snapshot={},
        )
        session.add(item)
        items.append(item)

    session.flush()
    return order.id, order.order_number, tuple(item.id for item in items)


def reserve_item(
    session: Session,
    base: BaseData,
    order_item_id: uuid.UUID,
    *,
    key: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[uuid.UUID, ...]:
    result = reserve_inventory(
        session=session,
        order_item_id=order_item_id,
        warehouse_id=base.warehouse_id,
        expires_at=(
            expires_at
            or datetime.now(timezone.utc) + timedelta(hours=1)
        ),
        idempotency_key=key or f"reserve-{_token()}",
        actor_user_id=base.buyer_id,
    )
    return tuple(item.reservation_id for item in result.allocations)


def consume_item_reservations(
    session: Session,
    reservation_ids: tuple[uuid.UUID, ...],
) -> None:
    for reservation_id in reservation_ids:
        consume_inventory_reservation(
            session=session,
            reservation_id=reservation_id,
        )


def pick_item_reservations(
    session: Session,
    reservation_ids: tuple[uuid.UUID, ...],
    actor_user_id: uuid.UUID,
) -> None:
    for reservation_id in reservation_ids:
        pick_inventory_reservation(
            session=session,
            reservation_id=reservation_id,
            actor_user_id=actor_user_id,
        )


def create_picked_order(
    session: Session,
    base: BaseData,
    quantities: list[int],
) -> tuple[uuid.UUID, str, tuple[uuid.UUID, ...]]:
    order_id, order_number, item_ids = create_order_items(
        session, base, quantities
    )

    for item_id in item_ids:
        reservation_ids = reserve_item(session, base, item_id)
        consume_item_reservations(session, reservation_ids)
        pick_item_reservations(
            session,
            reservation_ids,
            base.operator_id,
        )

    return order_id, order_number, item_ids


def create_ready_for_pickup_order(
    session: Session,
    base: BaseData,
    quantities: list[int],
) -> ReadyOrderData:
    order_id, order_number, item_ids = create_picked_order(
        session, base, quantities
    )
    created = create_packages_for_order(
        session=session,
        order_number=order_number,
    )

    for package in created.packages:
        pack_order_package(
            session=session,
            package_code=package.package_code,
            actor_user_id=base.operator_id,
        )
        stage_order_package_for_pickup(
            session=session,
            package_code=package.package_code,
            pickup_location_code=session.get(
                WarehouseLocation,
                base.pickup_location_id,
            ).code,
            actor_user_id=base.operator_id,
        )

    return ReadyOrderData(
        order_id=order_id,
        order_number=order_number,
        order_item_ids=item_ids,
        package_codes=tuple(p.package_code for p in created.packages),
        barcodes=tuple(p.barcode for p in created.packages),
    )


def handover_ready_order(
    session: Session,
    base: BaseData,
    ready: ReadyOrderData,
):
    return handover_order_packages(
        session=session,
        order_number=ready.order_number,
        scanned_codes=ready.package_codes,
        actor_user_id=base.operator_id,
        notes="test handover",
    )
