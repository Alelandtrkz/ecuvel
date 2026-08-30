from __future__ import annotations

import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Order,
    OrderItem,
    PaymentAttempt,
    SellerOrder,
    SellerPayout,
    SellerPayoutItem,
    Store,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    UserStatus,
)
from app.services.partner_sales import (
    PartnerSalesAccessError,
    get_partner_sales_page,
    resolve_sales_period,
)
from app.services.private_storage import private_file_path
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration
PASSWORD = "safe sales password"


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _enable_store(session: Session, base) -> User:
    owner = session.get(User, base.operator_id)
    owner.email = f"sales-{uuid.uuid4().hex[:10]}@test.local"
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
        store_name=session.get(Store, base.store_id).name,
        legal_id_number="1711111111",
        bank_account_owner="Tienda Test",
        bank_account_number="2200004567",
        bank_name="Pichincha",
        completed_at=datetime.now(timezone.utc),
    )
    session.add_all(
        [
            onboarding,
            StoreMember(
                store_id=base.store_id,
                user_id=owner.id,
                role=StoreMemberRole.OWNER,
                is_active=True,
            ),
        ]
    )
    session.flush()
    session.add(
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="sales-v1",
            annex_version="sales-a1",
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
        email=f"sales-member-{token}@test.local",
        email_normalized=f"sales-member-{token}@test.local",
        password_hash=generate_password_hash(PASSWORD),
        full_name=f"Miembro {role.value}",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(
        StoreMember(
            store_id=base.store_id,
            user_id=user.id,
            role=role,
            is_active=True,
        )
    )
    session.flush()
    return user


def _sale(
    session: Session,
    base,
    *,
    approved_at: datetime,
    quantity: int = 2,
    discount: Decimal = Decimal("2.00"),
    commission: Decimal = Decimal("3.00"),
):
    order_id, _number, item_ids = create_order_items(
        session,
        base,
        [quantity],
        discount_total=discount,
        commission_rate=(
            commission / (Decimal(quantity) * Decimal("10.00")) * Decimal("100")
        ),
    )
    order = session.get(Order, order_id)
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    item = session.get(OrderItem, item_ids[0])
    order.status = OrderStatus.CONFIRMED
    seller_order.status = SellerOrderStatus.CONFIRMED
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    assert seller_order.commission_total == commission
    attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"sales-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=approved_at + timedelta(hours=1),
        approved_at=approved_at,
    )
    session.add(attempt)
    session.flush()
    return order, seller_order, item, attempt


def _login(client, user: User):
    response = client.post(
        "/iniciar-sesion", data={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 302


def test_sales_metrics_chart_top_and_pending_use_real_store_snapshots(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_store(session, base)
    now = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)
    _sale(session, base, approved_at=now - timedelta(days=1))

    rejected_base = create_catalog_and_stock(session)
    rejected = _sale(session, rejected_base, approved_at=now - timedelta(days=1))[1]
    rejected.decision_status = SellerOrderDecisionStatus.REJECTED
    rejected.status = SellerOrderStatus.CANCELLED
    session.flush()

    page = get_partner_sales_page(
        session,
        user_id=owner.id,
        period_key="this_month",
        placeholder_image="/placeholder.svg",
        now=now,
    )

    assert page.metrics.gross_sales == Decimal("20.00")
    assert page.metrics.seller_net == Decimal("15.00")
    assert page.metrics.commission_total == Decimal("3.00")
    assert page.metrics.pending_payout == Decimal("15.00")
    assert page.metrics.order_count == 1
    assert page.metrics.units_sold == 2
    assert page.metrics.average_ticket == Decimal("20.00")
    assert page.metrics.gross_change_percent is None
    assert page.chart_payload["day"][0]["gross"] == "20.00"
    assert page.top_products_by_units[0].units == 2
    assert page.top_products_by_units[0].net_revenue == Decimal("15.00")
    assert page.recent_sales[0].seller_order_number


def test_sales_comparison_uses_previous_period_and_paid_is_not_pending(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_store(session, base)
    now = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    _sale(session, base, approved_at=now - timedelta(days=2), quantity=2)
    _sale(
        session,
        base,
        approved_at=datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc),
        quantity=1,
        discount=Decimal("1.00"),
        commission=Decimal("1.00"),
    )
    current_seller_order = session.scalar(
        select(SellerOrder)
        .join(PaymentAttempt, PaymentAttempt.order_id == SellerOrder.order_id)
        .where(PaymentAttempt.approved_at >= datetime(2026, 8, 1, tzinfo=timezone.utc))
    )
    payout = SellerPayout(
        store_id=base.store_id,
        status=SellerPayoutStatus.PAID,
        currency="USD",
        gross_sales_total=current_seller_order.subtotal,
        discount_total=current_seller_order.discount_total,
        commission_total=current_seller_order.commission_total,
        net_total=current_seller_order.seller_net_total,
        scheduled_for=now - timedelta(days=1),
        paid_at=now,
        external_reference="BANK-METRIC",
    )
    session.add(payout)
    session.flush()
    session.add(
        SellerPayoutItem(
            payout_id=payout.id,
            seller_order_id=current_seller_order.id,
            gross_amount_snapshot=current_seller_order.subtotal,
            discount_amount_snapshot=current_seller_order.discount_total,
            commission_amount_snapshot=current_seller_order.commission_total,
            net_amount_snapshot=current_seller_order.seller_net_total,
            eligible_at=now - timedelta(days=2),
        )
    )
    session.flush()

    page = get_partner_sales_page(
        session,
        user_id=owner.id,
        period_key="this_month",
        placeholder_image="/placeholder.svg",
        now=now,
    )
    assert page.metrics.gross_change_percent == Decimal("100.0")
    assert page.metrics.paid_by_ecuvel == Decimal("15.00")
    assert page.metrics.pending_payout == Decimal("8.00")


def test_periods_use_guayaquil_boundaries_and_previous_equivalent():
    now = datetime(2026, 8, 1, 4, 30, tzinfo=timezone.utc)  # 31 jul 23:30 EC
    period = resolve_sales_period("this_month", now=now)
    assert period.starts_at == datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc)
    assert period.comparison_starts_at == datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)
    assert period.comparison_ends_at == period.starts_at


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (StoreMemberRole.ADMINISTRATOR, True),
        (StoreMemberRole.FINANCE_OPERATOR, True),
        (StoreMemberRole.ORDER_OPERATOR, False),
        (StoreMemberRole.INVENTORY_OPERATOR, False),
        (StoreMemberRole.VIEWER, False),
    ],
)
def test_sales_roles_are_financially_scoped(session: Session, role, allowed):
    base = create_catalog_and_stock(session)
    _enable_store(session, base)
    user = _member(session, base, role)
    if allowed:
        page = get_partner_sales_page(
            session,
            user_id=user.id,
            period_key=None,
            placeholder_image="/placeholder.svg",
        )
        assert page.store.store_id == base.store_id
    else:
        with pytest.raises(PartnerSalesAccessError):
            get_partner_sales_page(
                session,
                user_id=user.id,
                period_key=None,
                placeholder_image="/placeholder.svg",
            )


def test_sales_route_export_and_foreign_payout_are_private(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_store(session, base)
    now = datetime.now(timezone.utc)
    _sale(session, base, approved_at=now - timedelta(hours=1))
    foreign_base = create_catalog_and_stock(session)
    foreign_payout = SellerPayout(
        store_id=foreign_base.store_id,
        status=SellerPayoutStatus.SCHEDULED,
        currency="USD",
        gross_sales_total=Decimal("10.00"),
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        net_total=Decimal("10.00"),
        scheduled_for=now + timedelta(days=1),
    )
    session.add(foreign_payout)
    session.commit()
    _login(client, owner)

    response = client.get("/partners/sales")
    assert response.status_code == 200
    assert b"Ventas de productos" in response.data
    assert b"Evoluci" in response.data
    exported = client.get("/partners/sales/export.csv")
    assert exported.status_code == 200
    assert exported.headers["Content-Disposition"].startswith("attachment")
    assert b"SellerOrder" in exported.data
    assert foreign_payout.payout_number.encode() not in exported.data
    assert client.get(
        f"/partners/sales/payouts/{foreign_payout.id}/detail"
    ).status_code == 404


def test_paid_payout_detail_masks_account_and_serves_private_receipt(
    client, app, session: Session
):
    base = create_catalog_and_stock(session)
    owner = _enable_store(session, base)
    now = datetime.now(timezone.utc)
    payload = b"%PDF-1.4\nprivate payout receipt\n%%EOF"
    storage_key = f"payout-receipts/{uuid.uuid4().hex}.pdf"
    path = private_file_path(app.config["SELLER_PAYOUT_RECEIPT_DIR"], storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    payout = SellerPayout(
        store_id=base.store_id,
        status=SellerPayoutStatus.PAID,
        currency="USD",
        gross_sales_total=Decimal("20.00"),
        discount_total=Decimal("2.00"),
        commission_total=Decimal("3.00"),
        net_total=Decimal("15.00"),
        scheduled_for=now - timedelta(days=1),
        paid_at=now,
        external_reference="BANK-001",
        destination_bank_name_snapshot="Pichincha",
        destination_account_last4="4567",
        receipt_storage_key=storage_key,
        receipt_original_filename="comprobante.pdf",
        receipt_media_type="application/pdf",
        receipt_size_bytes=len(payload),
        receipt_sha256=hashlib.sha256(payload).hexdigest(),
    )
    session.add(payout)
    session.commit()
    _login(client, owner)

    detail = client.get(f"/partners/sales/payouts/{payout.id}/detail")
    assert detail.status_code == 200
    body = detail.get_json()["payout"]
    assert body["destination_label"] == "**** 4567 (Pichincha)"
    assert "2200004567" not in detail.get_data(as_text=True)
    receipt = client.get(f"/partners/sales/payouts/{payout.id}/receipt")
    assert receipt.status_code == 200
    assert receipt.data == payload
    path.unlink(missing_ok=True)
