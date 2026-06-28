from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductVariant,
    SellerOffer,
    SellerOrder,
    Store,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    InventoryMovementType,
    LocationType,
    OfferStatus,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    StoreStatus,
)
from app.services.checkout import (
    CheckoutIdempotencyConflictError,
    CheckoutItemUnavailableError,
    CheckoutWarehouseError,
    build_checkout_preview,
    create_checkout_order,
)
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


def _cart(*items: tuple[uuid.UUID, int, bool]) -> dict:
    return {
        "version": 1,
        "items": {
            str(offer_id): {"quantity": quantity, "selected": selected}
            for offer_id, quantity, selected in items
        },
    }


def _expires() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=30)


def _create_offer(
    session: Session,
    base: BaseData,
    *,
    stock: int = 10,
    price: Decimal = Decimal("12.00"),
    second_store: bool = False,
) -> tuple[SellerOffer, InventoryBalance]:
    token = uuid.uuid4().hex[:12]
    original_offer = session.get(SellerOffer, base.offer_id)
    assert original_offer is not None
    original_variant = session.get(ProductVariant, original_offer.variant_id)
    assert original_variant is not None
    original_product = session.get(Product, original_variant.product_id)
    assert original_product is not None

    store_id = base.store_id
    if second_store:
        store = Store(
            public_code=f"STR-{token}",
            name=f"Second Store {token}",
            slug=f"second-store-{token}",
            status=StoreStatus.ACTIVE,
            is_verified=True,
        )
        session.add(store)
        session.flush()
        store_id = store.id

    product = Product(
        category_id=original_product.category_id,
        title=f"Checkout Product {token}",
        slug=f"checkout-product-{token}",
        is_active=True,
    )
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"CHECKOUT-SKU-{token}",
        title="Checkout Variant",
        attributes={"color": "blue"},
        is_active=True,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=store_id,
        variant_id=variant.id,
        seller_sku=f"CHECKOUT-SELL-{token}",
        currency="USD",
        price=price,
        compare_at_price=price + Decimal("3.00"),
        commission_rate=Decimal("10.00"),
        status=OfferStatus.ACTIVE,
    )
    session.add(offer)
    session.flush()
    balance = InventoryBalance(
        offer_id=offer.id,
        location_id=base.storage_location_id,
        on_hand_quantity=stock,
        reserved_quantity=0,
        blocked_quantity=0,
    )
    session.add(balance)
    session.flush()
    return offer, balance


def _checkout(
    session: Session,
    base: BaseData,
    cart: dict,
    key: str,
):
    return create_checkout_order(
        session=session,
        buyer_id=base.buyer_id,
        cart_state=cart,
        payment_method=PaymentMethod.BANK_TRANSFER,
        idempotency_key=key,
        reservation_expires_at=_expires(),
    )


def test_preview_uses_selected_items_and_current_database_prices(
    session: Session,
):
    base = create_catalog_and_stock(session, stock=20)
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    offer.price = Decimal("10.00")
    offer.compare_at_price = Decimal("15.00")
    second, _balance = _create_offer(session, base)
    session.commit()

    preview = build_checkout_preview(
        session=session,
        cart_state=_cart((base.offer_id, 2, True), (second.id, 1, False)),
    )

    assert len(preview.lines) == 1
    assert preview.total_units == 2
    assert preview.display_subtotal == Decimal("30.00")
    assert preview.savings == Decimal("10.00")
    assert preview.total == Decimal("20.00")


def test_bank_transfer_checkout_creates_complete_pending_order(
    session: Session,
):
    base = create_catalog_and_stock(session, stock=10)
    session.commit()
    before_on_hand = 10

    with session.begin():
        result = _checkout(
            session, base, _cart((base.offer_id, 2, True)), "create-order"
        )

    order = session.get(Order, result.order_id)
    attempt = session.get(PaymentAttempt, result.payment_attempt_id)
    balance = session.get(InventoryBalance, base.balance_id)
    reservation = session.scalar(select(InventoryReservation))
    assert order is not None and attempt is not None and balance is not None
    assert reservation is not None
    assert order.status == OrderStatus.PENDING_PAYMENT
    assert order.subtotal == order.grand_total == Decimal("20.00")
    assert order.discount_total == Decimal("0.00")
    assert attempt.status == PaymentStatus.AWAITING_PROOF
    assert attempt.amount == order.grand_total
    assert reservation.status == ReservationStatus.ACTIVE
    assert balance.on_hand_quantity == before_on_hand
    assert balance.reserved_quantity == 2
    assert session.scalar(select(func.count(SellerOrder.id))) == 1
    assert session.scalar(select(func.count(OrderItem.id))) == 1


def test_checkout_creates_one_seller_order_per_store(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    second, _balance = _create_offer(session, base, second_store=True)
    session.commit()

    with session.begin():
        result = _checkout(
            session,
            base,
            _cart((base.offer_id, 1, True), (second.id, 2, True)),
            "multi-store",
        )

    seller_orders = session.scalars(
        select(SellerOrder)
        .where(SellerOrder.order_id == result.order_id)
        .order_by(SellerOrder.seller_order_number)
    ).all()
    assert len(seller_orders) == 2
    assert len({item.store_id for item in seller_orders}) == 2
    assert all(item.status == SellerOrderStatus.PENDING_PAYMENT for item in seller_orders)


def test_repeated_checkout_returns_same_order(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    cart = _cart((base.offer_id, 2, True))
    session.commit()

    with session.begin():
        first = _checkout(session, base, cart, "same-checkout")
    with session.begin():
        replay = _checkout(session, base, cart, "same-checkout")

    assert replay.replayed is True
    assert replay.order_id == first.order_id
    assert session.scalar(select(func.count(Order.id))) == 1
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 1
    assert session.scalar(select(func.count(InventoryReservation.id))) == 1


def test_idempotency_key_rejects_changed_cart(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    session.commit()
    with session.begin():
        _checkout(
            session, base, _cart((base.offer_id, 1, True)), "conflict"
        )

    with pytest.raises(CheckoutIdempotencyConflictError):
        with session.begin():
            _checkout(
                session, base, _cart((base.offer_id, 2, True)), "conflict"
            )
    assert session.scalar(select(func.count(Order.id))) == 1


def test_checkout_rolls_back_when_one_item_is_unavailable(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    unavailable, unavailable_balance = _create_offer(session, base, stock=0)
    session.commit()
    before = (
        session.get(InventoryBalance, base.balance_id).reserved_quantity,
        unavailable_balance.reserved_quantity,
    )
    session.rollback()

    with pytest.raises(CheckoutItemUnavailableError):
        with session.begin():
            _checkout(
                session,
                base,
                _cart((base.offer_id, 1, True), (unavailable.id, 1, True)),
                "rollback",
            )

    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert session.scalar(select(func.count(InventoryReservation.id))) == 0
    assert (
        session.get(InventoryBalance, base.balance_id).reserved_quantity,
        session.get(InventoryBalance, unavailable_balance.id).reserved_quantity,
    ) == before


def test_checkout_rejects_ambiguous_warehouse(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    token = uuid.uuid4().hex[:12]
    warehouse = Warehouse(
        code=f"WH-SECOND-{token}",
        name="Second Warehouse",
        address_line="Test",
        city="Quito",
        country_code="EC",
        is_active=True,
    )
    session.add(warehouse)
    session.flush()
    location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"STO-{token}",
        barcode=f"STO-{token}",
        name="Second storage",
        location_type=LocationType.STORAGE,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(location)
    session.flush()
    session.add(
        InventoryBalance(
            offer_id=base.offer_id,
            location_id=location.id,
            on_hand_quantity=10,
            reserved_quantity=0,
            blocked_quantity=0,
        )
    )
    session.commit()

    with pytest.raises(CheckoutWarehouseError):
        with session.begin():
            _checkout(
                session,
                base,
                _cart((base.offer_id, 1, True)),
                "ambiguous",
            )


def test_checkout_does_not_consume_reservations(session: Session):
    base = create_catalog_and_stock(session, stock=5)
    session.commit()
    with session.begin():
        _checkout(
            session, base, _cart((base.offer_id, 2, True)), "not-consumed"
        )

    assert set(session.scalars(select(InventoryReservation.status))) == {
        ReservationStatus.ACTIVE
    }
    movement_types = set(session.scalars(select(InventoryMovement.movement_type)))
    assert movement_types == {InventoryMovementType.RESERVE}


@pytest.mark.concurrency
def test_concurrent_checkout_cannot_oversell_inventory(
    session: Session, session_factory, concurrent_runner
):
    base = create_catalog_and_stock(session, stock=1)
    session.commit()

    def worker(key: str):
        def run(barrier):
            worker_session = session_factory()
            try:
                barrier.wait()
                with worker_session.begin():
                    return _checkout(
                        worker_session,
                        base,
                        _cart((base.offer_id, 1, True)),
                        key,
                    )
            finally:
                worker_session.close()

        return run

    results, errors = concurrent_runner(
        [worker("race-one"), worker("race-two")]
    )

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], CheckoutItemUnavailableError)
    session.expire_all()
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    assert balance.reserved_quantity == 1
    assert session.scalar(select(func.count(Order.id))) == 1
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 1
