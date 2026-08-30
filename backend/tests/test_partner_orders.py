from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductMedia,
    ProductVariant,
    SellerOffer,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
    InventoryMovementType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReservationStatus,
    SellerCommissionType,
    SellerInboundPackageStatus,
    SellerOrderDecisionStatus,
    SellerOrderRejectionReason,
    SellerOrderStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    UserStatus,
)
from app.services.checkout import create_checkout_order
from app.services.inventory import (
    InvalidReservationTransitionError,
    consume_inventory_reservation,
    pick_inventory_reservation,
)
from app.services.partner_orders import (
    PartnerOrderAccessError,
    PartnerOrderConflictError,
    PartnerOrderNotFoundError,
    PartnerOrderValidationError,
    approve_partner_order,
    get_partner_order_detail,
    get_partner_orders_page,
    reject_partner_order,
)
from app.services.partner_order_workflow import (
    PartnerOrderWorkflowStage,
    resolve_partner_order_workflow,
)
from app.services.seller_inbound_packages import (
    SellerInboundPackageReceptionAccessError,
    create_partner_inbound_package,
    get_partner_inbound_package_label,
    mark_partner_inbound_package_ready,
    receive_seller_inbound_package,
)
from app.services.seller_order_logistics import build_seller_order_delivery_window
from tests.factories import create_catalog_and_stock, create_order_items, reserve_item


pytestmark = pytest.mark.integration
PASSWORD = "safe orders password"


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _enable_owner(session: Session, base) -> User:
    owner = session.get(User, base.operator_id)
    owner.email = f"orders-{uuid.uuid4().hex[:8]}@test.local"
    owner.email_normalized = owner.email.casefold()
    owner.password_hash = generate_password_hash(PASSWORD)
    owner.email_verified_at = datetime.now(timezone.utc)
    owner.status = UserStatus.ACTIVE
    owner.is_active = True
    onboarding = StoreOnboarding(
        user_id=owner.id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=session.get(SellerOffer, base.offer_id).store.name,
        legal_id_number="210049391",
        completed_at=datetime.now(timezone.utc),
    )
    session.add_all([
        onboarding,
        StoreMember(
            store_id=base.store_id,
            user_id=owner.id,
            role=StoreMemberRole.OWNER,
            is_active=True,
        ),
    ])
    session.flush()
    session.add(
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="orders-v1",
            annex_version="orders-a1",
            status=StoreContractAcceptanceStatus.ACCEPTED,
            accepted_terms=True,
            otp_verified=True,
            accepted_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return owner


def _member(session: Session, base, role: StoreMemberRole) -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}",
        email=f"orders-member-{token}@test.local",
        email_normalized=f"orders-member-{token}@test.local",
        password_hash=generate_password_hash(PASSWORD),
        full_name=f"Miembro {role.value}",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(StoreMember(
        store_id=base.store_id,
        user_id=user.id,
        role=role,
        is_active=True,
    ))
    session.flush()
    return user


def _ecuvel_staff(session: Session) -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"ECU-{token}",
        email=f"ecuvel-operator-{token}@test.local",
        email_normalized=f"ecuvel-operator-{token}@test.local",
        full_name="Operador interno Ecuvel",
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User) -> None:
    response = client.post(
        "/iniciar-sesion", data={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 302


def _paid_pending_order(
    session: Session,
    base,
    *,
    quantities=(2,),
    approved_at: datetime | None = None,
):
    order_id, order_number, item_ids = create_order_items(
        session, base, list(quantities)
    )
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    order = session.get(Order, order_id)
    approved_at = approved_at or datetime.now(timezone.utc)
    window = build_seller_order_delivery_window(approved_at)
    order.status = OrderStatus.CONFIRMED
    seller_order.status = SellerOrderStatus.CONFIRMED
    seller_order.decision_status = SellerOrderDecisionStatus.PENDING
    seller_order.decision_available_at = window.decision_available_at
    seller_order.ship_by_at = window.ship_by_at
    seller_order.estimated_delivery_from = window.estimated_delivery_from
    seller_order.estimated_delivery_to = window.estimated_delivery_to
    attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"paid-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=approved_at + timedelta(hours=1),
        approved_at=approved_at,
    )
    session.add(attempt)
    reservation_ids = []
    for item_id in item_ids:
        for reservation_id in reserve_item(session, base, item_id):
            consume_inventory_reservation(
                session=session, reservation_id=reservation_id
            )
            reservation_ids.append(reservation_id)
    session.flush()
    return order, seller_order, tuple(item_ids), tuple(reservation_ids), attempt


def test_page_lists_only_paid_store_orders_and_supports_search(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    buyer = session.get(User, base.buyer_id)
    buyer.full_name = "Carlos Mendoza"
    paid, seller_order, *_ = _paid_pending_order(session, base)
    unpaid_id, unpaid_number, _ = create_order_items(session, base, [1])
    foreign = create_catalog_and_stock(session)
    _paid_pending_order(session, foreign)
    session.flush()

    page = get_partner_orders_page(
        session, user_id=owner.id, query="Carlos", tab="pending"
    )
    assert page.metrics.pending == 1
    assert page.total_items == 1
    assert page.rows[0].seller_order_id == seller_order.id
    assert page.rows[0].order_number == paid.order_number
    assert unpaid_number not in {row.order_number for row in page.rows}
    assert page.rows[0].payment_label == "Pagado"


@pytest.mark.parametrize(
    ("role", "can_manage"),
    [
        (StoreMemberRole.ADMINISTRATOR, True),
        (StoreMemberRole.ORDER_OPERATOR, True),
        (StoreMemberRole.VIEWER, False),
    ],
)
def test_partner_order_roles(session: Session, role, can_manage):
    base = create_catalog_and_stock(session)
    _enable_owner(session, base)
    user = _member(session, base, role)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    page = get_partner_orders_page(session, user_id=user.id)
    assert page.store.can_manage is can_manage
    assert page.rows[0].can_decide is can_manage
    if can_manage:
        result = approve_partner_order(
            session, user_id=user.id, seller_order_id=seller_order.id
        )
        assert result.decision_status == SellerOrderDecisionStatus.APPROVED
    else:
        with pytest.raises(PartnerOrderAccessError):
            approve_partner_order(
                session, user_id=user.id, seller_order_id=seller_order.id
            )


def test_foreign_order_is_not_disclosed_by_service_or_route(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    foreign = create_catalog_and_stock(session)
    _order, foreign_seller, *_ = _paid_pending_order(session, foreign)
    session.commit()
    with pytest.raises(PartnerOrderNotFoundError):
        get_partner_order_detail(
            session,
            user_id=owner.id,
            seller_order_id=foreign_seller.id,
            buyer_pickup_point_name="Punto ECUVEL",
            buyer_pickup_point_address="Quito",
        )
    _login(client, owner)
    response = client.get(f"/partners/orders/{foreign_seller.id}/detail")
    assert response.status_code == 404


def test_approve_is_idempotent_and_does_not_touch_inventory(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    _order, seller_order, _items, reservations, attempt = _paid_pending_order(
        session, base
    )
    balance = session.get(InventoryBalance, base.balance_id)
    before = (balance.on_hand_quantity, balance.reserved_quantity)
    first = approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    approved_at = seller_order.approved_at
    second = approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    assert not first.replayed and second.replayed
    assert seller_order.approved_at == approved_at
    assert (balance.on_hand_quantity, balance.reserved_quantity) == before
    assert all(
        session.get(InventoryReservation, rid).status == ReservationStatus.CONSUMED
        for rid in reservations
    )
    assert seller_order.ship_by_at == attempt.approved_at + timedelta(hours=24)
    assert seller_order.estimated_delivery_to == attempt.approved_at + timedelta(hours=48)


def test_reject_requires_reason_and_comment_and_releases_reservation(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    _order, seller_order, _items, reservations, _attempt = _paid_pending_order(
        session, base
    )
    balance = session.get(InventoryBalance, base.balance_id)
    before_on_hand = balance.on_hand_quantity
    with pytest.raises(PartnerOrderValidationError):
        reject_partner_order(
            session, user_id=owner.id, seller_order_id=seller_order.id,
            reason=None, comment="detalle",
        )
    with pytest.raises(PartnerOrderValidationError):
        reject_partner_order(
            session, user_id=owner.id, seller_order_id=seller_order.id,
            reason="OUT_OF_STOCK", comment=" ",
        )
    with pytest.raises(PartnerOrderValidationError):
        reject_partner_order(
            session, user_id=owner.id, seller_order_id=seller_order.id,
            reason="OUT_OF_STOCK", comment="x" * 301,
        )
    result = reject_partner_order(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        reason="OUT_OF_STOCK",
        comment="El inventario físico no está disponible.",
    )
    assert result.decision_status == SellerOrderDecisionStatus.REJECTED
    assert seller_order.status == SellerOrderStatus.CANCELLED
    assert seller_order.requires_refund_resolution
    assert seller_order.rejection_reason == SellerOrderRejectionReason.OUT_OF_STOCK
    assert balance.on_hand_quantity == before_on_hand
    assert balance.reserved_quantity == 0
    assert all(
        session.get(InventoryReservation, rid).status == ReservationStatus.RELEASED
        for rid in reservations
    )
    movement = session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.reference_type == "SELLER_ORDER_REJECTION"
        )
    )
    assert movement is not None
    assert movement.movement_type == InventoryMovementType.RELEASE
    assert movement.delta_on_hand == 0
    detail = get_partner_order_detail(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        buyer_pickup_point_name="Punto Ecuvel",
        buyer_pickup_point_address="Quito",
    )
    assert detail.rejection_reason_label == "Stock agotado / No actualizado"
    assert detail.rejection_comment == "El inventario físico no está disponible."
    assert detail.rejected_at_label
    assert detail.rejected_by_name == owner.full_name


def test_rejected_seller_order_does_not_cancel_other_store_or_global_order(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    order, seller_order, *_ = _paid_pending_order(session, base)
    foreign = create_catalog_and_stock(session)
    foreign_order = SellerOrder(
        seller_order_number=f"{order.order_number}-S99",
        order_id=order.id,
        store_id=foreign.store_id,
        status=SellerOrderStatus.CONFIRMED,
        decision_status=SellerOrderDecisionStatus.PENDING,
        subtotal=Decimal("10.00"), discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"), seller_net_total=Decimal("10.00"),
        currency="USD",
    )
    session.add(foreign_order)
    session.flush()
    session.add(OrderItem(
        seller_order_id=foreign_order.id,
        offer_id=foreign.offer_id,
        store_id_snapshot=foreign.store_id,
        quantity=1, unit_price=Decimal("10.00"),
        discount_amount=Decimal("0.00"), tax_amount=Decimal("0.00"),
        line_total=Decimal("10.00"), product_name_snapshot="Producto otra tienda",
        currency="USD", gross_line_amount=Decimal("10.00"),
        seller_name_snapshot="Otra tienda", seller_sku_snapshot="OTHER-1",
        variant_snapshot={},
        commission_type_snapshot=SellerCommissionType.PERCENTAGE,
        commission_rate_snapshot=Decimal("0.00"),
        commission_amount_snapshot=Decimal("0.00"),
    ))
    order.subtotal += Decimal("10.00")
    order.grand_total += Decimal("10.00")
    session.flush()
    reject_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id,
        reason="OTHER", comment="La tienda no puede cumplir esta parte.",
    )
    assert order.status == OrderStatus.CONFIRMED
    assert foreign_order.status == SellerOrderStatus.CONFIRMED
    assert foreign_order.decision_status == SellerOrderDecisionStatus.PENDING


def test_pending_or_rejected_order_cannot_start_picking(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, seller_order, _items, reservations, _attempt = _paid_pending_order(
        session, base
    )
    with pytest.raises(InvalidReservationTransitionError):
        pick_inventory_reservation(
            session=session,
            reservation_id=reservations[0],
            actor_user_id=owner.id,
        )
    reject_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id,
        reason="DAMAGED_OR_UNSHIPPABLE", comment="Producto dañado.",
    )
    with pytest.raises(InvalidReservationTransitionError):
        pick_inventory_reservation(
            session=session,
            reservation_id=reservations[0],
            actor_user_id=owner.id,
        )


def test_checkout_snapshots_commission_category_and_image(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    offer = session.get(SellerOffer, base.offer_id)
    offer.price = Decimal("19.99")
    offer.commission_rate = Decimal("8.25")
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    media = ProductMedia(
        product_id=product.id,
        public_id=uuid.uuid4().hex,
        storage_key=f"test/{uuid.uuid4().hex}.png",
        media_type="image/png", size_bytes=100,
        position=0, is_cover=True, is_active=True,
    )
    session.add(media)
    session.flush()
    result = create_checkout_order(
        session=session,
        buyer_id=base.buyer_id,
        cart_state={"version": 1, "items": {str(base.offer_id): {"quantity": 3, "selected": True}}},
        payment_method=PaymentMethod.BANK_TRANSFER,
        idempotency_key=f"commission-{uuid.uuid4().hex}",
        reservation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    item = session.scalar(
        select(OrderItem)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(SellerOrder.order_id == result.order_id)
    )
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == result.order_id)
    )
    assert item.commission_rate_snapshot == Decimal("8.25")
    assert item.commission_amount_snapshot == Decimal("4.95")
    assert seller_order.commission_total == Decimal("4.95")
    assert seller_order.seller_net_total == Decimal("55.02")
    assert item.category_name_snapshot
    assert item.category_code_snapshot
    assert item.image_url_snapshot == f"/productos/{product.slug}/media/{media.public_id}"


def test_complete_financial_snapshot_builds_detail_without_image(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, seller_order, item_ids, *_ = _paid_pending_order(session, base)
    item = session.get(OrderItem, item_ids[0])
    item.image_url_snapshot = None
    detail = get_partner_order_detail(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        buyer_pickup_point_name="Punto de entrega Ecuvel",
        buyer_pickup_point_address="Dirección configurada",
        placeholder_image="/placeholder.svg",
    )
    assert detail.lines[0].image_url == "/placeholder.svg"
    assert detail.commission_breakdown_available
    assert detail.buyer_pickup_point_name == "Punto de entrega Ecuvel"


def _worker(session_factory, operation):
    def run(barrier):
        session = session_factory()
        try:
            with session.begin():
                barrier.wait()
                return operation(session)
        finally:
            session.close()
    return run


def test_concurrent_approval_is_idempotent(session_factory, concurrent_runner):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session)
        owner = _enable_owner(session, base)
        _order, seller_order, *_ = _paid_pending_order(session, base)
        owner_id, seller_id = owner.id, seller_order.id
    operation = lambda session: approve_partner_order(
        session, user_id=owner_id, seller_order_id=seller_id
    )
    results, errors = concurrent_runner([
        _worker(session_factory, operation), _worker(session_factory, operation)
    ])
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]


def test_concurrent_rejection_releases_inventory_once(session_factory, concurrent_runner):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        owner = _enable_owner(session, base)
        _order, seller_order, *_ = _paid_pending_order(session, base)
        owner_id, seller_id, balance_id = owner.id, seller_order.id, base.balance_id
    operation = lambda session: reject_partner_order(
        session, user_id=owner_id, seller_order_id=seller_id,
        reason="OUT_OF_STOCK", comment="Sin unidades disponibles.",
    )
    results, errors = concurrent_runner([
        _worker(session_factory, operation), _worker(session_factory, operation)
    ])
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]
    with session_factory() as session:
        balance = session.get(InventoryBalance, balance_id)
        assert balance.on_hand_quantity == 10
        assert balance.reserved_quantity == 0
        assert session.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.reference_type == "SELLER_ORDER_REJECTION"
        )) == 1


def test_orders_routes_render_drawer_and_apply_decision(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    session.commit()
    _login(client, owner)
    page = client.get("/partners/orders")
    assert page.status_code == 200
    assert "Pedidos" in page.get_data(as_text=True)
    detail = client.get(f"/partners/orders/{seller_order.id}/detail")
    assert detail.status_code == 200
    assert detail.get_json()["order"]["seller_order_number"] == seller_order.seller_order_number
    approved = client.post(f"/partners/orders/{seller_order.id}/approve")
    assert approved.status_code == 200
    assert approved.get_json()["order"]["decision_status"] == "APPROVED"


def test_rejected_cannot_be_approved_and_approved_cannot_be_rejected(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, rejected_order, *_ = _paid_pending_order(session, base)
    reject_partner_order(
        session, user_id=owner.id, seller_order_id=rejected_order.id,
        reason="OTHER", comment="No puede procesarse.",
    )
    with pytest.raises(PartnerOrderConflictError):
        approve_partner_order(
            session, user_id=owner.id, seller_order_id=rejected_order.id
        )

    _order2, approved_order, *_ = _paid_pending_order(session, base)
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=approved_order.id
    )
    with pytest.raises(PartnerOrderConflictError):
        reject_partner_order(
            session, user_id=owner.id, seller_order_id=approved_order.id,
            reason="OTHER", comment="Cambio tardío.",
        )


def test_tabs_dates_and_server_side_pagination(session: Session):
    base = create_catalog_and_stock(session, stock=100)
    owner = _enable_owner(session, base)
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    for _ in range(21):
        _paid_pending_order(session, base, quantities=(1,))
    _old_order, old_seller, *_ = _paid_pending_order(
        session, base, quantities=(1,), approved_at=old_time
    )
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=old_seller.id
    )
    approved = get_partner_orders_page(
        session, user_id=owner.id, tab="approved"
    )
    assert approved.tab == "preparation"
    assert approved.total_items == 1
    assert approved.rows[0].seller_order_id == old_seller.id
    recent = get_partner_orders_page(
        session, user_id=owner.id, tab="all", date_filter="7d"
    )
    assert recent.total_items == 21
    first_page = get_partner_orders_page(
        session, user_id=owner.id, tab="pending", page=1
    )
    second_page = get_partner_orders_page(
        session, user_id=owner.id, tab="pending", page=2
    )
    assert len(first_page.rows) == 20
    assert len(second_page.rows) == 1
    assert first_page.has_next and second_page.has_previous


def test_commission_snapshots_support_multiple_rates_in_one_seller_order(session: Session):
    base = create_catalog_and_stock(session, stock=20)
    first_offer = session.get(SellerOffer, base.offer_id)
    first_offer.price = Decimal("10.00")
    first_offer.commission_rate = Decimal("8.00")
    first_variant = session.get(ProductVariant, first_offer.variant_id)
    product = session.get(Product, first_variant.product_id)
    token = uuid.uuid4().hex[:10]
    second_variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"RATE-{token}",
        title="Segunda variante",
        attributes={"size": "large"},
        is_active=True,
    )
    session.add(second_variant)
    session.flush()
    second_offer = SellerOffer(
        store_id=base.store_id,
        variant_id=second_variant.id,
        seller_sku=f"RATE-SELL-{token}",
        currency="USD",
        price=Decimal("20.00"),
        commission_rate=Decimal("12.50"),
        status=first_offer.status,
    )
    session.add(second_offer)
    session.flush()
    session.add(InventoryBalance(
        offer_id=second_offer.id,
        location_id=base.storage_location_id,
        on_hand_quantity=20,
        reserved_quantity=0,
        blocked_quantity=0,
    ))
    session.flush()
    result = create_checkout_order(
        session=session,
        buyer_id=base.buyer_id,
        cart_state={"version": 1, "items": {
            str(first_offer.id): {"quantity": 2, "selected": True},
            str(second_offer.id): {"quantity": 3, "selected": True},
        }},
        payment_method=PaymentMethod.BANK_TRANSFER,
        idempotency_key=f"rates-{uuid.uuid4().hex}",
        reservation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == result.order_id)
    )
    items = list(session.scalars(
        select(OrderItem).where(OrderItem.seller_order_id == seller_order.id)
        .order_by(OrderItem.commission_rate_snapshot)
    ))
    assert [item.commission_rate_snapshot for item in items] == [Decimal("8.00"), Decimal("12.50")]
    assert [item.commission_amount_snapshot for item in items] == [Decimal("1.60"), Decimal("7.50")]
    assert seller_order.commission_total == Decimal("9.10")
    assert seller_order.seller_net_total == Decimal("70.90")


def test_order_mutation_requires_csrf_when_enabled(client, app, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    session.commit()
    _login(client, owner)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(f"/partners/orders/{seller_order.id}/approve")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 400
    session.expire_all()
    assert session.get(SellerOrder, seller_order.id).decision_status == SellerOrderDecisionStatus.PENDING


def test_approval_moves_order_from_pending_to_preparation_without_side_effects(
    session: Session,
):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    order, seller_order, _items, _reservations, payment = _paid_pending_order(
        session, base, quantities=(2,)
    )
    balance = session.get(InventoryBalance, base.balance_id)
    inventory_before = (balance.on_hand_quantity, balance.reserved_quantity)
    payment_before = (payment.status, payment.approved_at, payment.amount)

    result = approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )

    assert result.workflow_stage == PartnerOrderWorkflowStage.PREPARATION.value
    assert get_partner_orders_page(
        session, user_id=owner.id, tab="pending"
    ).total_items == 0
    preparation = get_partner_orders_page(
        session, user_id=owner.id, tab="preparation"
    )
    assert preparation.total_items == 1
    assert preparation.metrics.preparation == 1
    assert preparation.rows[0].workflow_label == "Aprobado · Pendiente de preparar"
    assert (balance.on_hand_quantity, balance.reserved_quantity) == inventory_before
    assert (payment.status, payment.approved_at, payment.amount) == payment_before
    assert order.status == OrderStatus.CONFIRMED
    assert seller_order.status == SellerOrderStatus.CONFIRMED


def test_inbound_package_can_contain_multiple_order_items(session: Session):
    base = create_catalog_and_stock(session, stock=20)
    owner = _enable_owner(session, base)
    _order, seller_order, item_ids, *_ = _paid_pending_order(
        session, base, quantities=(1, 3)
    )
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    balance = session.get(InventoryBalance, base.balance_id)
    inventory_before = (balance.on_hand_quantity, balance.reserved_quantity)

    package = create_partner_inbound_package(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
    )

    assert package.package_code.startswith("PKG-")
    assert len(package.package_code) == 16
    assert package.barcode == package.package_code
    assert {item.order_item_id for item in package.items} == set(item_ids)
    assert sorted(item.quantity for item in package.items) == [1, 3]
    assert session.scalar(
        select(func.count()).select_from(SellerInboundPackage)
    ) == 1
    assert session.scalar(
        select(func.count()).select_from(SellerInboundPackageItem)
    ) == 2
    assert (balance.on_hand_quantity, balance.reserved_quantity) == inventory_before


def test_inbound_packages_support_split_quantities_and_reject_overallocation(
    session: Session,
):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    _order, seller_order, (item_id,), *_ = _paid_pending_order(
        session, base, quantities=(2,)
    )
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    first = create_partner_inbound_package(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        allocations=((item_id, 1),),
    )
    second = create_partner_inbound_package(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        allocations=((item_id, 1),),
    )
    assert first.package_code != second.package_code
    assert first.barcode == first.package_code
    assert second.barcode == second.package_code
    with pytest.raises(PartnerOrderValidationError):
        create_partner_inbound_package(
            session,
            user_id=owner.id,
            seller_order_id=seller_order.id,
            allocations=((item_id, 1),),
        )


def test_inbound_package_requires_approved_owned_order_and_manage_role(
    session: Session,
):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    viewer = _member(session, base, StoreMemberRole.VIEWER)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    with pytest.raises(PartnerOrderConflictError):
        create_partner_inbound_package(
            session, user_id=owner.id, seller_order_id=seller_order.id
        )
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    with pytest.raises(PartnerOrderAccessError):
        create_partner_inbound_package(
            session, user_id=viewer.id, seller_order_id=seller_order.id
        )

    _rejected_order, rejected_seller, *_ = _paid_pending_order(session, base)
    reject_partner_order(
        session,
        user_id=owner.id,
        seller_order_id=rejected_seller.id,
        reason="OTHER",
        comment="No será posible preparar este pedido.",
    )
    with pytest.raises(PartnerOrderConflictError):
        create_partner_inbound_package(
            session, user_id=owner.id, seller_order_id=rejected_seller.id
        )

    foreign = create_catalog_and_stock(session)
    foreign_owner = _enable_owner(session, foreign)
    _foreign_order, foreign_seller, *_ = _paid_pending_order(session, foreign)
    approve_partner_order(
        session, user_id=foreign_owner.id, seller_order_id=foreign_seller.id
    )
    with pytest.raises(PartnerOrderNotFoundError):
        create_partner_inbound_package(
            session, user_id=owner.id, seller_order_id=foreign_seller.id
        )


def test_concurrent_inbound_package_codes_are_unique(
    session_factory, concurrent_runner
):
    with session_factory.begin() as session:
        base = create_catalog_and_stock(session, stock=10)
        owner = _enable_owner(session, base)
        _order, seller_order, (item_id,), *_ = _paid_pending_order(
            session, base, quantities=(2,)
        )
        approve_partner_order(
            session, user_id=owner.id, seller_order_id=seller_order.id
        )
        owner_id, seller_id = owner.id, seller_order.id

    operation = lambda session: create_partner_inbound_package(
        session,
        user_id=owner_id,
        seller_order_id=seller_id,
        allocations=((item_id, 1),),
    )
    results, errors = concurrent_runner(
        [_worker(session_factory, operation), _worker(session_factory, operation)]
    )
    assert not errors
    assert len({result.package_code for result in results}) == 2
    assert all(result.barcode == result.package_code for result in results)


def test_ready_and_ecuvel_receipt_drive_sla_and_logistics(session: Session):
    base = create_catalog_and_stock(session, stock=10)
    owner = _enable_owner(session, base)
    staff = _ecuvel_staff(session)
    approved_at = datetime.now(timezone.utc) - timedelta(hours=25)
    _order, seller_order, (item_id,), *_ = _paid_pending_order(
        session, base, quantities=(2,), approved_at=approved_at
    )
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id,
        now=approved_at,
    )
    first = create_partner_inbound_package(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        allocations=((item_id, 1),),
    )
    second = create_partner_inbound_package(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        allocations=((item_id, 1),),
    )
    for package in (first, second):
        ready = mark_partner_inbound_package_ready(
            session,
            user_id=owner.id,
            seller_order_id=seller_order.id,
            package_id=package.package_id,
        )
        replay = mark_partner_inbound_package_ready(
            session,
            user_id=owner.id,
            seller_order_id=seller_order.id,
            package_id=package.package_id,
        )
        assert ready.status == SellerInboundPackageStatus.READY_FOR_DROPOFF
        assert replay.replayed
        stored = session.get(SellerInboundPackage, package.package_id)
        assert stored.received_location_id is None
        assert stored.received_at is None

    packages = list(session.scalars(
        select(SellerInboundPackage).where(
            SellerInboundPackage.seller_order_id == seller_order.id
        )
    ))
    overdue = resolve_partner_order_workflow(
        seller_order, packages, datetime.now(timezone.utc)
    )
    assert overdue.stage == PartnerOrderWorkflowStage.PREPARATION
    assert overdue.is_overdue
    product_code = session.get(OrderItem, item_id).seller_sku_snapshot

    with pytest.raises(SellerInboundPackageReceptionAccessError):
        receive_seller_inbound_package(
            session,
            package_code=first.package_code,
            received_location_id=base.receiving_location_id,
            actor_user_id=owner.id,
            verified_product_codes=(product_code,),
        )
    with pytest.raises(PartnerOrderValidationError):
        receive_seller_inbound_package(
            session,
            package_code=first.package_code,
            received_location_id=base.receiving_location_id,
            actor_user_id=staff.id,
            verified_product_codes=(),
        )
    receive_seller_inbound_package(
        session,
        package_code=first.package_code,
        received_location_id=base.receiving_location_id,
        actor_user_id=staff.id,
        verified_product_codes=(product_code,),
    )
    packages = list(session.scalars(
        select(SellerInboundPackage).where(
            SellerInboundPackage.seller_order_id == seller_order.id
        )
    ))
    partial = resolve_partner_order_workflow(seller_order, packages)
    assert partial.stage == PartnerOrderWorkflowStage.PREPARATION
    assert partial.packages_received_count == 1

    received = receive_seller_inbound_package(
        session,
        package_code=second.package_code,
        received_location_id=base.receiving_location_id,
        actor_user_id=staff.id,
        verified_product_codes=(product_code,),
    )
    replay = receive_seller_inbound_package(
        session,
        package_code=second.package_code,
        received_location_id=base.receiving_location_id,
        actor_user_id=staff.id,
        verified_product_codes=(product_code,),
    )
    assert received.received_location_id == base.receiving_location_id
    assert replay.replayed
    stored_second = session.get(SellerInboundPackage, second.package_id)
    assert stored_second.received_by_user_id == staff.id
    assert stored_second.received_at is not None
    packages = list(session.scalars(
        select(SellerInboundPackage).where(
            SellerInboundPackage.seller_order_id == seller_order.id
        )
    ))
    logistics = resolve_partner_order_workflow(seller_order, packages)
    assert logistics.stage == PartnerOrderWorkflowStage.LOGISTICS
    assert not logistics.is_overdue
    assert get_partner_orders_page(
        session, user_id=owner.id, tab="logistics"
    ).total_items == 1
    assert get_partner_orders_page(
        session, user_id=owner.id, tab="preparation"
    ).total_items == 0

    seller_order.status = SellerOrderStatus.COMPLETED
    session.flush()
    completed = get_partner_orders_page(
        session, user_id=owner.id, tab="completed"
    )
    assert completed.total_items == 1
    assert completed.rows[0].workflow_label == "Entregado"


def test_label_is_private_minimal_and_reprinting_is_idempotent(
    client, session: Session
):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    order, seller_order, *_ = _paid_pending_order(session, base)
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    package = create_partner_inbound_package(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    label = get_partner_inbound_package_label(
        session,
        user_id=owner.id,
        seller_order_id=seller_order.id,
        package_id=package.package_id,
    )
    session.commit()
    _login(client, owner)
    url = (
        f"/partners/orders/{seller_order.id}/packages/"
        f"{package.package_id}/label"
    )
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == second.status_code == 200
    body = first.get_data(as_text=True)
    assert label.package_code in body
    assert order.order_number in body
    assert "data:image/svg+xml;base64" in body
    assert "150mm 100mm" in body
    assert "QR" not in body
    assert "Teléfono" not in body
    assert "Dirección" not in body
    assert "$" not in body
    assert session.scalar(
        select(func.count()).select_from(SellerInboundPackage)
    ) == 1


def test_foreign_package_label_is_404(client, session: Session):
    own = create_catalog_and_stock(session)
    owner = _enable_owner(session, own)
    foreign = create_catalog_and_stock(session)
    foreign_owner = _enable_owner(session, foreign)
    _order, seller_order, *_ = _paid_pending_order(session, foreign)
    approve_partner_order(
        session, user_id=foreign_owner.id, seller_order_id=seller_order.id
    )
    package = create_partner_inbound_package(
        session,
        user_id=foreign_owner.id,
        seller_order_id=seller_order.id,
    )
    session.commit()
    _login(client, owner)
    response = client.get(
        f"/partners/orders/{seller_order.id}/packages/{package.package_id}/label"
    )
    assert response.status_code == 404


def test_viewer_cannot_print_or_mutate_inbound_package(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    viewer = _member(session, base, StoreMemberRole.VIEWER)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    package = create_partner_inbound_package(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    with pytest.raises(PartnerOrderAccessError):
        get_partner_inbound_package_label(
            session,
            user_id=viewer.id,
            seller_order_id=seller_order.id,
            package_id=package.package_id,
        )
    with pytest.raises(PartnerOrderAccessError):
        mark_partner_inbound_package_ready(
            session,
            user_id=viewer.id,
            seller_order_id=seller_order.id,
            package_id=package.package_id,
        )


def test_package_routes_create_and_mark_ready(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_owner(session, base)
    _order, seller_order, *_ = _paid_pending_order(session, base)
    approve_partner_order(
        session, user_id=owner.id, seller_order_id=seller_order.id
    )
    session.commit()
    _login(client, owner)

    created = client.post(f"/partners/orders/{seller_order.id}/packages")
    assert created.status_code == 201
    package_payload = created.get_json()["package"]
    assert package_payload["barcode"] == package_payload["package_code"]
    package_id = package_payload["package_id"]

    ready = client.post(
        f"/partners/orders/{seller_order.id}/packages/{package_id}/ready"
    )
    replay = client.post(
        f"/partners/orders/{seller_order.id}/packages/{package_id}/ready"
    )
    assert ready.status_code == replay.status_code == 200
    assert ready.get_json()["package"]["status"] == "READY_FOR_DROPOFF"
    assert replay.get_json()["package"]["replayed"] is True
