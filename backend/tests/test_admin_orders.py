from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    InventoryReservation,
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    PaymentNotificationOutbox,
    PaymentProof,
    SellerOrder,
    StaffProfile,
    StoreMember,
    User,
)
from app.models.enums import (
    OrderStatus,
    PackageStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
    StoreMemberRole,
    UserStatus,
)
from app.services.admin_orders import get_admin_orders_page
from app.services.admin_permissions import user_has_permission
from app.services.private_storage import private_file_path
from tests.factories import create_catalog_and_stock, create_order_items, reserve_item


pytestmark = pytest.mark.integration
PNG = b"\x89PNG\r\n\x1a\nadmin-proof"


@pytest.fixture
def client(app):
    yield app.test_client()
    db.session.remove()


def _staff(session, *, staff=True):
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"ADM-{token}",
        email=f"admin-{token}@test.local",
        password_hash="test",
        full_name="Operador Admin",
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=staff,
    )
    session.add(user)
    session.flush()
    return user


def _staff_with_role(session, role: StaffRole):
    user = _staff(session)
    session.add(StaffProfile(
        user=user,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"ID-{uuid.uuid4().hex[:12]}",
        nationality_code="ECU",
        role=role,
        employment_status=StaffEmploymentStatus.ACTIVE,
        employment_started_at=date.today(),
    ))
    session.flush()
    return user


def _login(client, user):
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _pending_proof_graph(session, app, tmp_path, *, quantities=(1, 1)):
    app.config["PAYMENT_PROOF_UPLOAD_DIR"] = str(tmp_path)
    base = create_catalog_and_stock(session, stock=20)
    order_id, order_number, item_ids = create_order_items(session, base, list(quantities))
    order = session.get(Order, order_id)
    order.subtotal = Decimal(sum(quantities) * 10)
    order.grand_total = order.subtotal
    for item_id in item_ids:
        reserve_item(session, base, item_id)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.PROCESSING,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"pay-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=expires_at,
    )
    session.add(attempt)
    session.flush()
    key = f"2026/08/{uuid.uuid4().hex}.png"
    path = private_file_path(tmp_path, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    proof = PaymentProof(
        payment_attempt_id=attempt.id,
        storage_key=key,
        original_filename="comprobante.png",
        media_type="image/png",
        size_bytes=len(PNG),
        sha256=hashlib.sha256(PNG).hexdigest(),
        status=PaymentProofStatus.PENDING_REVIEW,
        upload_idempotency_key=f"upload-{uuid.uuid4().hex}",
        uploaded_by_user_id=base.buyer_id,
    )
    session.add(proof)
    session.flush()
    return base, order, item_ids, attempt, proof


def _display_order(
    session,
    base,
    *,
    suffix,
    order_status,
    seller_status,
    payment_status,
    package_status=None,
):
    now = datetime.now(timezone.utc)
    order = Order(
        order_number=f"ECV-STATE-{suffix}",
        buyer_id=base.buyer_id,
        status=order_status,
        currency="USD",
        subtotal=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        shipping_total=Decimal("0.00"),
        tax_total=Decimal("0.00"),
        grand_total=Decimal("10.00"),
    )
    session.add(order)
    session.flush()
    seller_order = SellerOrder(
        seller_order_number=f"SO-STATE-{suffix}",
        order_id=order.id,
        store_id=base.store_id,
        status=seller_status,
        decision_status=(
            SellerOrderDecisionStatus.PENDING
            if seller_status in {
                SellerOrderStatus.PENDING_PAYMENT,
                SellerOrderStatus.CONFIRMED,
            }
            else SellerOrderDecisionStatus.APPROVED
        ),
        subtotal=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        seller_net_total=Decimal("10.00"),
        approved_at=(
            now
            if seller_status not in {
                SellerOrderStatus.PENDING_PAYMENT,
                SellerOrderStatus.CONFIRMED,
            }
            else None
        ),
    )
    session.add(seller_order)
    session.flush()
    item = OrderItem(
        seller_order_id=seller_order.id,
        offer_id=base.offer_id,
        quantity=1,
        unit_price=Decimal("10.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        line_total=Decimal("10.00"),
        product_name_snapshot=f"Producto {suffix}",
        seller_name_snapshot="Tienda estados",
        seller_sku_snapshot=f"SKU-{suffix}",
        variant_snapshot={},
    )
    session.add(item)
    session.flush()
    session.add(PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=payment_status,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"state-{suffix}-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=now + timedelta(hours=1),
        approved_at=(now if payment_status == PaymentStatus.APPROVED else None),
        rejected_at=(now if payment_status == PaymentStatus.REJECTED else None),
    ))
    if package_status is not None:
        session.add(OrderPackage(
            package_code=f"PKG-STATE-{suffix}",
            barcode=f"BAR-STATE-{suffix}",
            order_item_id=item.id,
            quantity=1,
            status=package_status,
            packed_at=(now if package_status != PackageStatus.CREATED else None),
            ready_at=(
                now
                if package_status in {
                    PackageStatus.READY_FOR_PICKUP,
                    PackageStatus.HANDED_OVER,
                }
                else None
            ),
            handed_over_at=(now if package_status == PackageStatus.HANDED_OVER else None),
        ))
    session.flush()
    return order


def test_all_admin_order_routes_require_internal_staff(session, client, app, tmp_path):
    base, order, _, _, _ = _pending_proof_graph(session, app, tmp_path)
    outsider = _staff(session, staff=False)
    session.commit()
    urls = (
        "/admin/orders",
        f"/admin/orders/{order.order_number}",
        f"/admin/orders/{order.order_number}/payment",
        f"/admin/orders/{order.order_number}/payment-proof",
    )
    for url in urls:
        assert client.get(url).status_code == 302
    for suffix in ("approve", "reject"):
        assert client.post(f"/admin/orders/{order.order_number}/payment/{suffix}").status_code == 302
    _login(client, outsider)
    for url in urls:
        assert client.get(url).status_code == 403
    for suffix in ("approve", "reject"):
        assert client.post(f"/admin/orders/{order.order_number}/payment/{suffix}").status_code == 403

    for role in (StoreMemberRole.OWNER, StoreMemberRole.ADMINISTRATOR):
        seller = _staff(session, staff=False)
        session.add(StoreMember(store_id=base.store_id, user_id=seller.id, role=role, is_active=True))
        session.commit()
        _login(client, seller)
        for url in urls:
            assert client.get(url).status_code == 403

    for status, is_active in (
        (UserStatus.BLOCKED, True),
        (UserStatus.SUSPENDED, True),
        (UserStatus.ACTIVE, False),
    ):
        disabled = _staff(session)
        disabled.status = status
        disabled.is_active = is_active
        session.commit()
        _login(client, disabled)
        for url in urls:
            assert client.get(url).status_code == 403


@pytest.mark.parametrize(
    "role",
    [
        StaffRole.OPERATIONS_SUPERVISOR,
        StaffRole.POINT_OPERATOR,
        StaffRole.DELIVERY,
        StaffRole.TRANSPORT_OPERATOR,
        StaffRole.SUPPORT,
    ],
)
def test_payment_review_routes_deny_non_financial_staff_without_mutating_state(
    session, client, app, tmp_path, role
):
    _, order, _, attempt, proof = _pending_proof_graph(session, app, tmp_path)
    reviewer = _staff_with_role(session, role)
    session.commit()
    _login(client, reviewer)

    assert not user_has_permission(reviewer, "payments.view")
    assert not user_has_permission(reviewer, "payments.review")
    assert client.get(f"/admin/orders/{order.order_number}/payment").status_code == 403
    assert client.get(f"/admin/orders/{order.order_number}/payment-proof").status_code == 403
    assert client.post(
        f"/admin/orders/{order.order_number}/payment/approve"
    ).status_code == 403
    assert client.post(
        f"/admin/orders/{order.order_number}/payment/reject",
        data={"reason_code": "OTHER", "reason": "No autorizado"},
    ).status_code == 403

    session.expire_all()
    assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING
    assert session.get(Order, order.id).status == OrderStatus.PENDING_PAYMENT


def test_payment_permission_matrix_allows_only_super_admin_and_legacy_staff(session):
    super_admin = _staff_with_role(session, StaffRole.SUPER_ADMIN)
    legacy_admin = _staff(session)
    assert user_has_permission(super_admin, "payments.view")
    assert user_has_permission(super_admin, "payments.review")
    assert user_has_permission(legacy_admin, "payments.view")
    assert user_has_permission(legacy_admin, "payments.review")


def test_orders_list_is_one_row_per_order_and_supports_filters(session, client, app, tmp_path):
    base, order, _, _, _ = _pending_proof_graph(session, app, tmp_path)
    staff = _staff(session)
    buyer = session.get(User, base.buyer_id)
    buyer.full_name = "Elena Rodríguez"
    session.commit()
    _login(client, staff)

    response = client.get("/admin/orders?payment=review")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert body.count(order.order_number) >= 1
    assert "Elena Rodríguez" in body
    assert buyer.public_account_code in body
    assert "2 artículos" in body
    assert "En revisión" in body
    assert str(order.id) not in body

    assert client.get(f"/admin/orders?q={order.order_number}").status_code == 200
    assert client.get(f"/admin/orders?q={buyer.public_account_code}").status_code == 200
    assert client.get("/admin/orders?status=invalid&page=nope&page_size=999").status_code == 200
    page = get_admin_orders_page(session, payment="review")
    assert len(page.rows) == 1
    assert page.rows[0].expected_package_count == 2
    assert page.rows[0].package_count == 0


def test_list_distinguishes_units_from_expected_packages(session, app, tmp_path):
    _, order, _, _, _ = _pending_proof_graph(
        session,
        app,
        tmp_path,
        quantities=(2, 3),
    )
    page = get_admin_orders_page(session)
    row = next(item for item in page.rows if item.order_number == order.order_number)
    assert row.item_count == 5
    assert row.expected_package_count == 2


def test_payment_filter_uses_only_the_canonical_attempt(session, app, tmp_path):
    _, order, _, current_attempt, _ = _pending_proof_graph(session, app, tmp_path)
    old_attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"old-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        approved_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(old_attempt)
    session.flush()

    review_page = get_admin_orders_page(session, payment="review")
    approved_page = get_admin_orders_page(session, payment="approved")
    assert [row.order_number for row in review_page.rows] == [order.order_number]
    assert review_page.rows[0].payment.attempt_id == current_attempt.id
    assert approved_page.total_items == 0


def test_tabs_and_fulfillment_filters_use_domain_state(session):
    base = create_catalog_and_stock(session, stock=50)
    expected = {
        "pending-payment": _display_order(
            session, base, suffix="PENDING",
            order_status=OrderStatus.PENDING_PAYMENT,
            seller_status=SellerOrderStatus.PENDING_PAYMENT,
            payment_status=PaymentStatus.AWAITING_PROOF,
        ),
        "confirmed": _display_order(
            session, base, suffix="CONFIRMED",
            order_status=OrderStatus.CONFIRMED,
            seller_status=SellerOrderStatus.CONFIRMED,
            payment_status=PaymentStatus.APPROVED,
        ),
        "preparing": _display_order(
            session, base, suffix="PICKING",
            order_status=OrderStatus.FULFILLING,
            seller_status=SellerOrderStatus.PICKING,
            payment_status=PaymentStatus.APPROVED,
        ),
        "ready": _display_order(
            session, base, suffix="READY",
            order_status=OrderStatus.READY_FOR_PICKUP,
            seller_status=SellerOrderStatus.READY_FOR_PICKUP,
            payment_status=PaymentStatus.APPROVED,
            package_status=PackageStatus.READY_FOR_PICKUP,
        ),
        "delivered": _display_order(
            session, base, suffix="DELIVERED",
            order_status=OrderStatus.COMPLETED,
            seller_status=SellerOrderStatus.COMPLETED,
            payment_status=PaymentStatus.APPROVED,
            package_status=PackageStatus.HANDED_OVER,
        ),
        "cancelled": _display_order(
            session, base, suffix="CANCELLED",
            order_status=OrderStatus.CANCELLED,
            seller_status=SellerOrderStatus.CANCELLED,
            payment_status=PaymentStatus.REJECTED,
        ),
    }
    packed = _display_order(
        session, base, suffix="PACKED",
        order_status=OrderStatus.FULFILLING,
        seller_status=SellerOrderStatus.PACKED,
        payment_status=PaymentStatus.APPROVED,
        package_status=PackageStatus.PACKED,
    )

    for tab, order in expected.items():
        page = get_admin_orders_page(session, tab=tab)
        assert order.order_number in {row.order_number for row in page.rows}
    picking_page = get_admin_orders_page(session, fulfillment="picking")
    assert [row.fulfillment.label for row in picking_page.rows] == ["Picking"]
    packed_page = get_admin_orders_page(session, fulfillment="packed")
    assert [row.order_number for row in packed_page.rows] == [packed.order_number]
    assert packed_page.rows[0].fulfillment.label == "Empacado"


def test_date_search_package_pagination_and_ecuador_label(session, app, tmp_path):
    _, order, item_ids, _, _ = _pending_proof_graph(session, app, tmp_path)
    order.created_at = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    package = OrderPackage(
        package_code="PKG-ADMIN-SEARCH",
        barcode="BAR-ADMIN-SEARCH",
        order_item_id=item_ids[0],
        quantity=1,
        status=PackageStatus.CREATED,
    )
    session.add(package)
    for index in range(26):
        session.add(Order(
            order_number=f"ECV-PAGE-{index:03d}",
            buyer_id=order.buyer_id,
            status=OrderStatus.PENDING_PAYMENT,
            currency="USD",
            subtotal=Decimal("1.00"),
            discount_total=Decimal("0.00"),
            shipping_total=Decimal("0.00"),
            tax_total=Decimal("0.00"),
            grand_total=Decimal("1.00"),
            created_at=datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc),
        ))
    session.flush()

    search_page = get_admin_orders_page(session, query="PKG-ADMIN-SEARCH")
    assert [row.order_number for row in search_page.rows] == [order.order_number]
    today_page = get_admin_orders_page(
        session,
        date="today",
        now=datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc),
    )
    assert [row.order_number for row in today_page.rows] == [order.order_number]
    assert today_page.rows[0].created_at_label == "08 Ago 2026 · 01:00"
    second_page = get_admin_orders_page(session, page=2, page_size=25)
    assert second_page.page == 2
    assert second_page.total_items == 27
    assert len(second_page.rows) == 2


def test_order_detail_uses_snapshots_and_real_financials(session, client, app, tmp_path):
    _, order, item_ids, _, _ = _pending_proof_graph(session, app, tmp_path)
    first = session.get(OrderItem, item_ids[0])
    first.product_name_snapshot = "Auriculares Snapshot"
    first.seller_sku_snapshot = "SKU-HISTORICO"
    first.variant_snapshot = {"options": {"color": "Negro", "storage": "256 GB"}}
    staff = _staff(session)
    session.commit()
    _login(client, staff)

    response = client.get(f"/admin/orders/{order.order_number}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Auriculares Snapshot" in body
    assert "SKU-HISTORICO" in body
    assert "Negro / 256 GB" in body
    assert "Resumen financiero" in body
    assert "Tiendas involucradas" in body
    assert "no conserva una dirección histórica" in body
    assert str(order.id) not in body
    assert client.get("/admin/orders/NO-EXISTE").status_code == 404


def test_private_proof_checks_staff_integrity_and_order_binding(session, client, app, tmp_path):
    _, order, _, _, _ = _pending_proof_graph(session, app, tmp_path)
    _, other, _, _, _ = _pending_proof_graph(session, app, tmp_path)
    staff = _staff(session)
    session.commit()
    _login(client, staff)

    response = client.get(f"/admin/orders/{order.order_number}/payment-proof")
    assert response.status_code == 200
    assert response.data == PNG
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(f"/admin/orders/{other.order_number}/payment").status_code == 200

    proof = session.scalar(
        select(PaymentProof)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentProof.payment_attempt_id)
        .where(PaymentAttempt.order_id == order.id)
    )
    private_file_path(tmp_path, proof.storage_key).write_bytes(PNG + b"tampered")
    assert client.get(f"/admin/orders/{order.order_number}/payment-proof").status_code == 404


def test_approve_web_uses_domain_service_and_is_not_available_by_get(session, client, app, tmp_path):
    _, order, _, attempt, proof = _pending_proof_graph(session, app, tmp_path)
    staff = _staff(session)
    session.commit()
    _login(client, staff)

    endpoint = f"/admin/orders/{order.order_number}/payment/approve"
    assert client.get(endpoint).status_code == 405
    response = client.post(endpoint, data={"notes": "Verificado"}, follow_redirects=True)
    assert response.status_code == 200
    session.expire_all()
    assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.APPROVED
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.APPROVED
    assert session.get(Order, order.id).status == OrderStatus.CONFIRMED
    assert session.get(PaymentProof, proof.id).reviewed_by_user_id == staff.id
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 1
    assert session.scalar(select(PaymentNotificationOutbox.event_type)) == "PAYMENT_APPROVED"
    seller_orders = session.scalars(select(SellerOrder).where(SellerOrder.order_id == order.id)).all()
    assert all(item.status == SellerOrderStatus.CONFIRMED for item in seller_orders)
    assert all(
        reservation.status == ReservationStatus.CONSUMED
        for reservation in session.scalars(select(InventoryReservation))
    )
    assert "Decisión registrada" in response.get_data(as_text=True)

    # Repetir la misma decisión y luego intentar la opuesta no altera el resultado.
    assert client.post(endpoint).status_code == 302
    assert client.post(
        f"/admin/orders/{order.order_number}/payment/reject",
        data={"reason_code": "OTHER", "reason": "Decisión opuesta"},
    ).status_code == 302
    session.expire_all()
    assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.APPROVED
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 1


def test_reject_requires_reason_and_releases_reservations(session, client, app, tmp_path):
    _, order, _, attempt, proof = _pending_proof_graph(session, app, tmp_path)
    staff = _staff(session)
    session.commit()
    _login(client, staff)
    endpoint = f"/admin/orders/{order.order_number}/payment/reject"

    response = client.post(endpoint, data={"reason": ""}, follow_redirects=True)
    assert "requiere una razón" in response.get_data(as_text=True)
    session.expire_all()
    assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.PENDING_REVIEW

    client.post(
        endpoint,
        data={
            "reason_code": "AMOUNT_MISMATCH",
            "reason": "El monto no coincide",
        },
    )
    session.expire_all()
    assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.REJECTED
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.REJECTED
    assert session.get(Order, order.id).status == OrderStatus.CANCELLED
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 1
    assert session.scalar(select(PaymentNotificationOutbox.event_type)) == "PAYMENT_REJECTED"
    assert all(
        reservation.status == ReservationStatus.RELEASED
        for reservation in session.scalars(select(InventoryReservation))
    )


def test_payment_mutations_require_csrf_when_enabled(session, client, app, tmp_path):
    _, order, _, _, proof = _pending_proof_graph(session, app, tmp_path)
    staff = _staff(session)
    session.commit()
    _login(client, staff)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(f"/admin/orders/{order.order_number}/payment/approve")
        assert response.status_code == 400
        session.expire_all()
        assert session.get(PaymentProof, proof.id).status == PaymentProofStatus.PENDING_REVIEW
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
