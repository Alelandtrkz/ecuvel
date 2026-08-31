from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import (
    Category,
    InventoryBalance,
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    PaymentProof,
    ProductDraft,
    SellerInboundPackage,
    SellerOrder,
    SellerPayout,
    Store,
    StoreMember,
    StoreOnboarding,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    OrderStatus,
    PackageStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ProductDraftStatus,
    SellerCommissionType,
    SellerInboundPackageStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreMemberRole,
    StoreOnboardingStatus,
    StoreStatus,
    UserStatus,
)
from app.services.admin_operations import (
    ecuador_comparison_windows,
    get_admin_operations_page,
    search_admin_records,
)
from tests.factories import (
    create_approved_bank_version,
    create_catalog_and_stock,
    create_order_items,
)


@pytest.fixture
def client(app):
    return app.test_client()


def _token() -> str:
    return uuid.uuid4().hex[:12]


def _user(
    session,
    *,
    status: UserStatus = UserStatus.ACTIVE,
    is_active: bool = True,
    is_staff: bool = False,
    name: str = "Admin Test",
) -> User:
    token = _token()
    user = User(
        public_code=f"USR-{token}",
        email=f"{token}@test.local",
        password_hash="test",
        full_name=name,
        status=status,
        is_active=is_active,
        is_ecuvel_staff=is_staff,
    )
    session.add(user)
    session.flush()
    return user


def _store(session, *, name: str = "Tienda Admin") -> Store:
    token = _token()
    store = Store(
        public_code=f"STR-{token}",
        name=name,
        slug=f"store-{token}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add(store)
    session.flush()
    return store


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _order(
    session,
    *,
    buyer_id,
    created_at: datetime,
    status: OrderStatus = OrderStatus.CONFIRMED,
    number: str | None = None,
    total: Decimal = Decimal("10.00"),
) -> Order:
    token = _token()
    order = Order(
        order_number=number or f"ECV-{token}",
        buyer_id=buyer_id,
        status=status,
        currency="USD",
        subtotal=total,
        discount_total=Decimal("0.00"),
        shipping_total=Decimal("0.00"),
        tax_total=Decimal("0.00"),
        grand_total=total,
        created_at=created_at,
    )
    session.add(order)
    session.flush()
    return order


def _seller_order(
    session,
    *,
    order: Order,
    store: Store,
    status: SellerOrderStatus,
    decision: SellerOrderDecisionStatus = SellerOrderDecisionStatus.APPROVED,
    ship_by_at: datetime | None = None,
) -> SellerOrder:
    seller_order = SellerOrder(
        seller_order_number=f"SO-{_token()}",
        order_id=order.id,
        store_id=store.id,
        status=status,
        decision_status=decision,
        ship_by_at=ship_by_at,
        subtotal=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        seller_net_total=Decimal("10.00"),
        currency="USD",
    )
    session.add(seller_order)
    session.flush()
    return seller_order


def _order_item(session, *, seller_order: SellerOrder, offer_id, suffix: str) -> OrderItem:
    item = OrderItem(
        seller_order_id=seller_order.id,
        offer_id=offer_id,
        store_id_snapshot=seller_order.store_id,
        quantity=1,
        unit_price=Decimal("10.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        line_total=Decimal("10.00"),
        currency="USD",
        gross_line_amount=Decimal("10.00"),
        product_name_snapshot=f"Producto {suffix}",
        seller_name_snapshot="Tienda Admin",
        seller_sku_snapshot=f"SKU-{suffix}-{_token()}",
        variant_snapshot={},
        commission_type_snapshot=SellerCommissionType.PERCENTAGE,
        commission_rate_snapshot=Decimal("0.00"),
        commission_amount_snapshot=Decimal("0.00"),
    )
    session.add(item)
    session.flush()
    return item


def _outbound_package(
    session,
    *,
    item: OrderItem,
    status: PackageStatus,
    now: datetime,
) -> OrderPackage:
    package = OrderPackage(
        package_code=f"OUT-{_token()}",
        barcode=f"BAR-{_token()}",
        order_item_id=item.id,
        quantity=item.quantity,
        status=status,
        packed_at=(now if status != PackageStatus.CREATED else None),
        ready_at=(
            now
            if status in {PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER}
            else None
        ),
        handed_over_at=(now if status == PackageStatus.HANDED_OVER else None),
    )
    session.add(package)
    session.flush()
    return package


def test_admin_requires_active_ecuvel_staff(session, client):
    assert client.get("/admin").status_code == 302
    assert client.get("/admin/search?q=ECV-").status_code == 302

    buyer = _user(session, name="Comprador")
    session.commit()
    _login(client, buyer)
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/search?q=ECV-").status_code == 403

    store = _store(session)
    for role in (StoreMemberRole.OWNER, StoreMemberRole.ADMINISTRATOR):
        seller = _user(session, name=f"Seller {role.value}")
        session.add(
            StoreMember(
                store_id=store.id,
                user_id=seller.id,
                role=role,
                is_active=True,
            )
        )
        session.commit()
        _login(client, seller)
        assert client.get("/admin").status_code == 403
        assert client.get("/admin/search?q=ECV-").status_code == 403

    staff = _user(session, is_staff=True)
    session.commit()
    _login(client, staff)
    response = client.get("/admin")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Centro de Operaciones" in body
    assert "/admin/orders?date=today" in body
    assert "/admin/orders?payment=review" in body
    assert "/admin/orders?status=preparing" in body
    assert "/admin/orders?status=ready" in body
    assert "/admin/orders?fulfillment=picking" in body
    assert client.get("/admin/search?q=ECV-").status_code == 200
    assert client.post("/admin").status_code == 405

    for status, active in (
        (UserStatus.PENDING_VERIFICATION, True),
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.ACTIVE, False),
    ):
        denied = _user(session, status=status, is_active=active, is_staff=True)
        session.commit()
        _login(client, denied)
        assert client.get("/admin").status_code == 403
        assert client.get("/admin/search?q=ECV-").status_code == 403


def test_ecuador_partial_day_windows_and_real_daily_metrics(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    today_start, today_end, yesterday_start, yesterday_end = (
        ecuador_comparison_windows(now)
    )
    assert today_start == datetime(2026, 8, 8, 5, 0, tzinfo=timezone.utc)
    assert today_end == now
    assert yesterday_start == datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc)
    assert yesterday_end == datetime(2026, 8, 7, 15, 30, tzinfo=timezone.utc)

    buyer = _user(session)
    today = _order(
        session, buyer_id=buyer.id,
        created_at=datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc),
        total=Decimal("19.95"),
    )
    yesterday = _order(
        session, buyer_id=buyer.id,
        created_at=datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc),
    )
    _order(
        session, buyer_id=buyer.id,
        created_at=datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc),
    )
    _order(
        session, buyer_id=buyer.id,
        created_at=datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc),
        status=OrderStatus.CANCELLED,
    )

    session.add_all(
        [
            PaymentAttempt(
                order_id=today.id,
                method=PaymentMethod.BANK_TRANSFER,
                status=PaymentStatus.APPROVED,
                amount=Decimal("19.95"),
                currency="USD",
                idempotency_key=f"pay-{_token()}",
                request_fingerprint="a" * 64,
                expires_at=now + timedelta(hours=1),
                approved_at=datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),
            ),
            PaymentAttempt(
                order_id=yesterday.id,
                method=PaymentMethod.BANK_TRANSFER,
                status=PaymentStatus.APPROVED,
                amount=Decimal("10.00"),
                currency="USD",
                idempotency_key=f"pay-{_token()}",
                request_fingerprint="b" * 64,
                expires_at=now + timedelta(hours=1),
                approved_at=datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc),
            ),
            PaymentAttempt(
                order_id=today.id,
                method=PaymentMethod.BANK_TRANSFER,
                status=PaymentStatus.PROCESSING,
                amount=Decimal("999.00"),
                currency="USD",
                idempotency_key=f"pay-{_token()}",
                request_fingerprint="c" * 64,
                expires_at=now + timedelta(hours=1),
            ),
        ]
    )
    session.flush()

    page = get_admin_operations_page(session, now=now)
    assert page.metrics.orders_today.current == 1
    assert page.metrics.orders_today.previous == 1
    assert page.metrics.sales_today.current == Decimal("19.95")
    assert page.metrics.sales_today.previous == Decimal("10.00")
    assert page.metrics.sales_today.change_percent == Decimal("99.5")


def test_dashboard_workflow_alerts_stock_attention_and_activity(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    base = create_catalog_and_stock(session, stock=4)
    buyer = session.get(User, base.buyer_id)
    store = session.get(Store, base.store_id)
    balance = session.get(InventoryBalance, base.balance_id)
    balance.reserved_quantity = 1
    balance.blocked_quantity = 1

    warehouse = session.get(Warehouse, base.warehouse_id)
    second_location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"ST2-{_token()}",
        barcode=f"LOC-{_token()}",
        name="Storage second",
        location_type=LocationType.STORAGE,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(second_location)
    session.flush()
    session.add(
        InventoryBalance(
            offer_id=base.offer_id,
            location_id=second_location.id,
            on_hand_quantity=3,
            reserved_quantity=1,
            blocked_quantity=1,
        )
    )

    attempt_order = _order(session, buyer_id=buyer.id, created_at=now)
    attempt = PaymentAttempt(
        order_id=attempt_order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.PROCESSING,
        amount=Decimal("10.00"),
        currency="USD",
        idempotency_key=f"pay-{_token()}",
        request_fingerprint="d" * 64,
        expires_at=now + timedelta(hours=1),
    )
    session.add(attempt)
    session.flush()
    session.add(
        PaymentProof(
            payment_attempt_id=attempt.id,
            storage_key=f"proofs/{_token()}.png",
            original_filename="proof.png",
            media_type="image/png",
            size_bytes=100,
            sha256="e" * 64,
            status=PaymentProofStatus.PENDING_REVIEW,
            upload_idempotency_key=f"upload-{_token()}",
            created_at=now - timedelta(minutes=31),
        )
    )

    prep_order = _order(session, buyer_id=buyer.id, created_at=now)
    overdue = _seller_order(
        session,
        order=prep_order,
        store=store,
        status=SellerOrderStatus.CONFIRMED,
        ship_by_at=now - timedelta(minutes=1),
    )
    for status in (
        SellerOrderStatus.PICKING,
        SellerOrderStatus.PACKED,
        SellerOrderStatus.READY_FOR_PICKUP,
        SellerOrderStatus.COMPLETED,
    ):
        order = _order(session, buyer_id=buyer.id, created_at=now)
        _seller_order(session, order=order, store=store, status=status)

    category = Category(
        code=f"CAT-{_token()}", name="Admin category", slug=f"cat-{_token()}"
    )
    session.add(category)
    session.flush()
    draft = ProductDraft(
        store_id=store.id,
        created_by_user_id=buyer.id,
        category_id=category.id,
        subcategory_id=category.id,
        template_key="electronics_phones",
        seller_sku=f"CRI-{_token()}",
        barcode=None,
        status=ProductDraftStatus.SUBMITTED,
        submitted_at=now - timedelta(minutes=4),
    )
    draft.barcode = draft.seller_sku
    session.add(draft)

    session.add(
        SellerPayout(
            store_id=store.id,
            bank_account_version_id=create_approved_bank_version(session, base).id,
            status=SellerPayoutStatus.ON_HOLD,
            currency="USD",
            gross_sales_total=Decimal("0.00"),
            discount_total=Decimal("0.00"),
            commission_total=Decimal("0.00"),
            net_total=Decimal("0.00"),
            scheduled_for=now + timedelta(days=1),
        )
    )
    onboarding_user = _user(session, name="Nueva tienda")
    session.add(
        StoreOnboarding(
            user_id=onboarding_user.id,
            store_id=store.id,
            status=StoreOnboardingStatus.SUBMITTED,
            store_name=store.name,
            submitted_at=now - timedelta(minutes=2),
        )
    )
    session.flush()

    page = get_admin_operations_page(session, now=now)
    assert page.metrics.pending_payment_proofs == 1
    assert page.metrics.overdue_payment_proofs == 1
    assert page.metrics.in_preparation >= 4
    # The ready SellerOrder feeds the flow bar, but no complete outbound
    # package set exists yet, so the customer-facing KPI remains zero.
    assert page.metrics.ready_for_pickup == 0
    assert page.metrics.products_pending_review == 1
    assert {item.key: item.count for item in page.order_flow} == {
        "confirmed": 1,
        "picking": 1,
        "packed": 1,
        "ready": 1,
        "completed": 1,
    }
    alerts = {alert.key: alert.count for alert in page.alerts}
    assert alerts["seller_sla"] == 1
    assert alerts["payments"] == 1
    assert alerts["stock"] == 1
    assert alerts["payouts"] == 1
    attention = {item.key: item.count for item in page.attention}
    assert attention["stores"] == 1
    assert attention["products"] == 1
    assert attention["payouts"] == 1
    assert len(page.activity) <= 6
    assert tuple(item.timestamp for item in page.activity) == tuple(
        sorted((item.timestamp for item in page.activity), reverse=True)
    )
    assert all(str(overdue.id) not in (item.public_reference or "") for item in page.activity)


def test_ready_for_pickup_counts_only_complete_unique_orders(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    first_catalog = create_catalog_and_stock(session, stock=50)
    second_catalog = create_catalog_and_stock(session, stock=50)
    buyer = session.get(User, first_catalog.buyer_id)
    first_store = session.get(Store, first_catalog.store_id)
    second_store = session.get(Store, second_catalog.store_id)

    complete = _order(session, buyer_id=buyer.id, created_at=now)
    complete_seller = _seller_order(
        session,
        order=complete,
        store=first_store,
        status=SellerOrderStatus.READY_FOR_PICKUP,
    )
    complete_item = _order_item(
        session,
        seller_order=complete_seller,
        offer_id=first_catalog.offer_id,
        suffix="complete",
    )
    _outbound_package(
        session,
        item=complete_item,
        status=PackageStatus.READY_FOR_PICKUP,
        now=now,
    )

    partial = _order(session, buyer_id=buyer.id, created_at=now)
    partial_seller = _seller_order(
        session,
        order=partial,
        store=first_store,
        status=SellerOrderStatus.PACKED,
    )
    for index, status in enumerate(
        (
            PackageStatus.READY_FOR_PICKUP,
            PackageStatus.READY_FOR_PICKUP,
            PackageStatus.PACKED,
        )
    ):
        item = _order_item(
            session,
            seller_order=partial_seller,
            offer_id=first_catalog.offer_id,
            suffix=f"partial-{index}",
        )
        _outbound_package(session, item=item, status=status, now=now)

    multi_store = _order(session, buyer_id=buyer.id, created_at=now)
    for index, (store, offer_id) in enumerate(
        (
            (first_store, first_catalog.offer_id),
            (second_store, second_catalog.offer_id),
        )
    ):
        seller_order = _seller_order(
            session,
            order=multi_store,
            store=store,
            status=SellerOrderStatus.READY_FOR_PICKUP,
        )
        item = _order_item(
            session,
            seller_order=seller_order,
            offer_id=offer_id,
            suffix=f"multi-{index}",
        )
        _outbound_package(
            session,
            item=item,
            status=PackageStatus.READY_FOR_PICKUP,
            now=now,
        )

    session.flush()
    page = get_admin_operations_page(session, now=now)
    assert page.metrics.ready_for_pickup == 2


def test_critical_stock_aggregates_sellable_locations_by_offer(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    base = create_catalog_and_stock(session, stock=2)
    warehouse = session.get(Warehouse, base.warehouse_id)
    second_location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"ST2-{_token()}",
        barcode=f"LOC-{_token()}",
        name="Storage second",
        location_type=LocationType.STORAGE,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(second_location)
    session.flush()
    second_balance = InventoryBalance(
        offer_id=base.offer_id,
        location_id=second_location.id,
        on_hand_quantity=4,
        reserved_quantity=0,
        blocked_quantity=0,
    )
    session.add(second_balance)
    session.flush()

    page = get_admin_operations_page(session, now=now, critical_stock_threshold=5)
    assert all(alert.key != "stock" for alert in page.alerts)

    second_balance.blocked_quantity = 2
    session.flush()
    page = get_admin_operations_page(session, now=now, critical_stock_threshold=5)
    assert {alert.key: alert.count for alert in page.alerts}["stock"] == 1


def test_payment_review_metrics_ignore_reviewed_proofs(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    buyer = _user(session)
    proof_statuses = (
        (PaymentProofStatus.PENDING_REVIEW, now - timedelta(minutes=10)),
        (PaymentProofStatus.APPROVED, now - timedelta(hours=2)),
        (PaymentProofStatus.REJECTED, now - timedelta(hours=2)),
    )
    for index, (proof_status, created_at) in enumerate(proof_statuses):
        order = _order(session, buyer_id=buyer.id, created_at=now)
        attempt = PaymentAttempt(
            order_id=order.id,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.PROCESSING,
            amount=order.grand_total,
            currency=order.currency,
            idempotency_key=f"pay-{_token()}",
            request_fingerprint=str(index) * 64,
            expires_at=now + timedelta(hours=1),
        )
        session.add(attempt)
        session.flush()
        session.add(
            PaymentProof(
                payment_attempt_id=attempt.id,
                storage_key=f"proofs/{_token()}.png",
                original_filename="proof.png",
                media_type="image/png",
                size_bytes=100,
                sha256=str(index + 1) * 64,
                status=proof_status,
                upload_idempotency_key=f"upload-{_token()}",
                created_at=created_at,
            )
        )
    session.flush()

    page = get_admin_operations_page(session, now=now)
    assert page.metrics.pending_payment_proofs == 1
    assert page.metrics.overdue_payment_proofs == 0
    assert all(alert.key != "payments" for alert in page.alerts)


def test_received_inbound_package_leaves_preparation_stage(session):
    now = datetime(2026, 8, 8, 15, 30, tzinfo=timezone.utc)
    base = create_catalog_and_stock(session)
    buyer = session.get(User, base.buyer_id)
    store = session.get(Store, base.store_id)
    receiving = session.get(WarehouseLocation, base.receiving_location_id)
    order = _order(session, buyer_id=buyer.id, created_at=now)
    seller_order = _seller_order(
        session,
        order=order,
        store=store,
        status=SellerOrderStatus.PICKING,
        ship_by_at=now - timedelta(hours=1),
    )
    session.add(
        SellerInboundPackage(
            seller_order_id=seller_order.id,
            package_code="PKG-000000000001",
            barcode="PKG-000000000001",
            status=SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
            ready_for_dropoff_at=now - timedelta(hours=2),
            received_at=now - timedelta(minutes=30),
            received_location_id=receiving.id,
        )
    )
    session.flush()

    page = get_admin_operations_page(session, now=now)
    assert page.metrics.in_preparation == 0
    assert all(alert.key != "seller_sla" for alert in page.alerts)


def test_admin_search_uses_public_references_and_group_limits(session):
    base = create_catalog_and_stock(session)
    buyer = session.get(User, base.buyer_id)
    store = session.get(Store, base.store_id)
    order_id, order_number, item_ids = create_order_items(session, base, [1])
    order = session.get(Order, order_id)
    seller_order = order.seller_orders[0]
    seller_order.seller_order_number = "ECUVEL-SELLER-10482"
    order.created_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    _order(
        session,
        buyer_id=buyer.id,
        created_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        number=f"{order_number}-EXTRA",
    )

    inbound = SellerInboundPackage(
        seller_order_id=seller_order.id,
        package_code="PKG-000000010482",
        barcode="PKG-000000010482",
    )
    outbound = OrderPackage(
        package_code="OUT-10482",
        barcode="OUT-10482",
        order_item_id=item_ids[0],
        quantity=1,
        status=PackageStatus.CREATED,
    )
    session.add_all([inbound, outbound])
    session.flush()

    by_order = search_admin_records(session, query=order_number[:8])
    assert any(
        result.reference == order_number
        for group in by_order.groups
        for result in group.results
    )
    exact_order = search_admin_records(session, query=order_number)
    assert exact_order.groups[0].results[0].reference == order_number
    by_user = search_admin_records(session, query=buyer.public_account_code)
    assert by_user.groups[0].results[0].reference == buyer.public_account_code
    by_package = search_admin_records(session, query="PKG-000000010")
    assert by_package.groups[0].results[0].reference == inbound.package_code
    by_store = search_admin_records(session, query=store.public_store_code)
    assert by_store.groups[0].results[0].reference == store.public_store_code
    assert all(
        len(group.results) <= 5
        for page in (by_order, by_user, by_package, by_store)
        for group in page.groups
    )
    assert str(order.id) not in repr((by_order, by_user, by_package, by_store))

    empty = search_admin_records(session, query="   ")
    assert empty.query == ""
    assert empty.groups == ()
    assert empty.total_results == 0


def test_admin_search_scanner_and_placeholders_require_staff(session, client):
    staff = _user(session, is_staff=True)
    session.commit()
    _login(client, staff)
    assert client.get("/admin/search?q=PKG-").status_code == 200
    scanner = client.get("/admin/scanner")
    assert scanner.status_code == 200
    assert "Recepción en punto" in scanner.get_data(as_text=True)
    assert client.get("/admin/modules/scanner").status_code == 404
    assert client.get("/admin/modules/unknown").status_code == 404
