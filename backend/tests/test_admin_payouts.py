from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models import (
    Order, OrderPackage, PaymentAttempt, SellerOrder, SellerPayout,
    StaffProfile, Store, StoreBankAccountVersion, User,
)
from app.models.enums import (
    BankAccountType,
    BankAccountVersionStatus,
    SellerPayoutStatus,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
    StoreStatus,
    UserStatus,
)
from app.services.admin_payouts import (
    AdminPayoutQueryError,
    cycle_options,
    get_admin_payout_detail,
    get_admin_payout_kpis,
    list_admin_payouts,
)
from tests.factories import (
    create_approved_bank_version,
    create_catalog_and_stock,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    return app.test_client()


def _staff(session) -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"ADM-{token}", email=f"{token}@test.local",
        email_normalized=f"{token}@test.local", password_hash="test",
        full_name="Admin Finanzas", status=UserStatus.ACTIVE,
        is_ecuvel_staff=True,
    )
    session.add(user)
    session.flush()
    return user


def _super_admin(session) -> User:
    user = _staff(session)
    session.add(StaffProfile(
        user_id=user.id, identification_type=StaffIdentificationType.ECUADOR_CEDULA,
        identification_number_normalized=f"TEST{uuid.uuid4().hex[:12]}",
        nationality_code="ECU", role=StaffRole.SUPER_ADMIN,
        employment_status=StaffEmploymentStatus.ACTIVE,
    ))
    session.flush()
    return user


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _store_and_bank(session, *, name="Tienda Liquidaciones"):
    token = uuid.uuid4().hex[:10]
    store = Store(
        public_code=f"STR-{token}", name=name, slug=f"store-{token}",
        status=StoreStatus.ACTIVE, is_verified=True,
    )
    session.add(store)
    session.flush()
    version = StoreBankAccountVersion(
        store_id=store.id, version=1, holder_name="Titular Seguro",
        holder_identification="179999990001", bank_name="Banco Seguro",
        account_type=BankAccountType.CHECKING, currency="USD",
        encrypted_account_number=b"x" * 17, encryption_nonce=b"n" * 12,
        account_last4="4452", account_fingerprint=b"f" * 32,
        encryption_key_version="v1", fingerprint_key_version="v1",
        status=BankAccountVersionStatus.APPROVED,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=3),
        usable_from=datetime.now(timezone.utc) - timedelta(days=1),
    )
    session.add(version)
    session.flush()
    return store, version


def _payout(session, store, version, *, status=SellerPayoutStatus.SCHEDULED, number=None, net=Decimal("90.00")):
    payout = SellerPayout(
        payout_number=number or f"PAY-{uuid.uuid4().int % 100_000_000:08d}",
        store_id=store.id, bank_account_version_id=version.id,
        status=status, currency="USD", gross_sales_total=Decimal("100.00"),
        discount_total=Decimal("2.00"), commission_total=Decimal("8.00"),
        net_total=net, scheduled_for=datetime(2026, 8, 15, 5, tzinfo=timezone.utc),
        destination_bank_name_snapshot="Banco Seguro",
        destination_account_last4="4452",
    )
    if status == SellerPayoutStatus.PAID:
        payout.paid_at = datetime(2026, 8, 16, 15, tzinfo=timezone.utc)
        payout.external_reference = f"REF-{uuid.uuid4().hex[:10]}"
    if status == SellerPayoutStatus.CANCELLED:
        payout.cancelled_at = datetime(2026, 8, 16, 15, tzinfo=timezone.utc)
    session.add(payout)
    session.flush()
    return payout


def test_admin_payouts_empty_state_and_navigation(client, session):
    staff = _staff(session)
    session.commit()
    _login(client, staff)
    response = client.get("/admin/payouts")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No se han generado liquidaciones todavía." in body
    assert 'href="/admin/payouts"' in body
    assert "data-admin-sidebar-toggle" in body
    assert 'aria-label="Contraer barra lateral"' in body
    assert 'aria-label="Liquidaciones"' in body
    assert 'data-admin-tooltip="Liquidaciones"' in body
    assert 'class="is-active"\n                   href="/admin/payouts"' in body
    assert "Programación automática" not in body


def test_collapsible_sidebar_is_shared_and_assets_load(client, session):
    staff = _staff(session)
    session.commit()
    _login(client, staff)
    payments = client.get("/admin/payments")
    assert payments.status_code == 200
    assert "data-admin-sidebar-toggle" in payments.get_data(as_text=True)

    script = client.get("/static/js/admin.js")
    stylesheet = client.get("/static/css/admin.css")
    assert script.status_code == stylesheet.status_code == 200
    assert "ecuvel.admin.sidebar.collapsed" in script.get_data(as_text=True)
    css = stylesheet.get_data(as_text=True)
    assert "--admin-sidebar-collapsed-width: 72px" in css
    assert ":root.admin-sidebar-collapsed" in css


def test_legacy_staff_can_view_but_cannot_schedule(client, session):
    staff = _staff(session)
    session.commit()
    _login(client, staff)
    assert client.get("/admin/payouts").status_code == 200
    assert client.post("/admin/payouts/schedule", data={"cycle_date": "2026-08-31"}).status_code == 403


def test_rbac_protects_read_and_every_mutation(client, session):
    buyer = User(
        public_code=f"USR-{uuid.uuid4().hex[:10]}",
        email=f"{uuid.uuid4().hex[:10]}@test.local", password_hash="test",
        full_name="Comprador", status=UserStatus.ACTIVE,
    )
    legacy = _staff(session)
    session.add(buyer)
    session.commit()
    _login(client, buyer)
    assert client.get("/admin/payouts").status_code == 403

    _login(client, legacy)
    for url, data in (
        ("/admin/payouts/schedule", {"cycle_date": "2026-08-31"}),
        ("/admin/payouts/PAY-00000001/hold", {}),
        ("/admin/payouts/PAY-00000001/resume", {}),
        ("/admin/payouts/PAY-00000001/cancel", {}),
        ("/admin/payouts/PAY-00000001/pay", {"external_reference": "X", "paid_at": "2026-08-31T10:00"}),
    ):
        assert client.post(url, data=data).status_code == 403


def test_future_and_arbitrary_cycle_cannot_be_scheduled(client, session):
    admin = _super_admin(session)
    session.commit()
    _login(client, admin)
    for cycle_date in ("2099-08-15", "2026-08-17"):
        response = client.post(
            "/admin/payouts/schedule", data={"cycle_date": cycle_date},
            follow_redirects=True,
        )
        assert response.status_code == 200
    assert session.scalar(select(SellerPayout.id)) is None


def test_list_search_filters_sort_and_detail_are_masked(session):
    store, version = _store_and_bank(session, name="Electro Hogar")
    payout = _payout(session, store, version, number="PAY-00000041")
    session.flush()
    page = list_admin_payouts(session, query="Electro", status="SCHEDULED", sort_by="net_total")
    assert page.total == 1
    assert page.items[0].payout_number == payout.payout_number
    assert page.items[0].account_last4 == "4452"
    detail = get_admin_payout_detail(session, payout.payout_number)
    assert detail.bank.account_last4 == "4452"
    assert detail.bank.holder_identification_masked.endswith("0001")
    assert not hasattr(detail.bank, "encrypted_account_number")


def test_scheduled_and_on_hold_drawers_enforce_actions(client, session):
    admin = _super_admin(session)
    store, version = _store_and_bank(session)
    payout = _payout(session, store, version, number="PAY-00000077")
    session.commit()
    _login(client, admin)

    response = client.get(f"/admin/payouts?detail={payout.payout_number}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Marcar como pagada" in body
    assert "Poner en hold" in body
    assert "••••4452" in body
    assert "179999990001" not in body

    response = client.post(
        f"/admin/payouts/{payout.payout_number}/hold",
        data={"tab": "scheduled", "page": "1"}, follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Reanudar liquidación" in body
    assert "Marcar como pagada" not in body
    session.refresh(payout)
    assert payout.status == SellerPayoutStatus.ON_HOLD

    response = client.post(
        f"/admin/payouts/{payout.payout_number}/cancel",
        data={"tab": "on_hold"}, follow_redirects=True,
    )
    assert response.status_code == 200
    assert "El historial de este PAY se conserva" in response.get_data(as_text=True)
    session.refresh(payout)
    assert payout.status == SellerPayoutStatus.CANCELLED


def test_invalid_query_contract_is_controlled(session):
    with pytest.raises(AdminPayoutQueryError):
        list_admin_payouts(session, sort_by="drop table")
    with pytest.raises(AdminPayoutQueryError):
        list_admin_payouts(session, query="x" * 121)
    with pytest.raises(AdminPayoutQueryError):
        list_admin_payouts(session, per_page=101)


def test_kpis_use_current_month_and_status_aggregates(session):
    store, version = _store_and_bank(session)
    _payout(session, store, version, status=SellerPayoutStatus.SCHEDULED)
    _payout(session, store, version, status=SellerPayoutStatus.ON_HOLD)
    _payout(session, store, version, status=SellerPayoutStatus.PAID)
    _payout(session, store, version, status=SellerPayoutStatus.CANCELLED)
    kpis = get_admin_payout_kpis(session, now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert kpis.scheduled_count == 1
    assert kpis.on_hold_count == 1
    assert kpis.paid_period_count == 1
    assert kpis.cancelled_period_count == 1


def test_cycle_options_do_not_authorize_future_execution():
    options = cycle_options(now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc))
    assert options[0].window.cycle_date_local.isoformat() == "2026-08-15"
    assert options[0].executable is False


def test_web_schedule_mark_paid_and_private_receipt(client, session, app, tmp_path):
    admin = _super_admin(session)
    base = create_catalog_and_stock(session)
    create_approved_bank_version(
        session, base, reviewed_at=datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    ready = create_ready_for_pickup_order(session, base, [1])
    order = session.get(Order, ready.order_id)
    seller_order = session.scalar(select(SellerOrder).where(SellerOrder.order_id == order.id))
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    order.status = OrderStatus.FULFILLING
    session.add(PaymentAttempt(
        order_id=order.id, method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED, amount=order.grand_total,
        currency="USD", idempotency_key=f"web-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        approved_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
    ))
    handover_ready_order(session, base, ready)
    delivered = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for package in session.scalars(select(OrderPackage).join(OrderPackage.order_item).where(
        OrderPackage.order_item.has(seller_order_id=seller_order.id)
    )):
        package.handed_over_at = delivered
    seller_order.delivered_at = delivered
    seller_order.payout_eligible_at = delivered + timedelta(days=4)
    seller_order.status = SellerOrderStatus.COMPLETED
    session.commit()
    _login(client, admin)

    response = client.post(
        "/admin/payouts/schedule", data={"cycle_date": "2026-08-15"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    payout = session.scalar(select(SellerPayout))
    assert payout is not None
    assert payout.status == SellerPayoutStatus.SCHEDULED

    previous_root = app.config["SELLER_PAYOUT_RECEIPT_DIR"]
    app.config["SELLER_PAYOUT_RECEIPT_DIR"] = str(tmp_path)
    response = client.post(
        f"/admin/payouts/{payout.payout_number}/pay",
        data={
            "external_reference": "WEB-TRANSFER-001",
            "paid_at": "2026-08-31T10:00",
            "receipt": (io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "comprobante.pdf", "application/pdf"),
        }, content_type="multipart/form-data", follow_redirects=True,
    )
    assert response.status_code == 200
    session.refresh(payout)
    assert payout.status == SellerPayoutStatus.PAID
    assert payout.external_reference == "WEB-TRANSFER-001"
    receipt = client.get(f"/admin/payouts/{payout.payout_number}/receipt")
    assert receipt.status_code == 200
    assert receipt.headers["Cache-Control"] == "private, no-store"
    assert receipt.headers["Pragma"] == "no-cache"
    assert receipt.headers["X-Content-Type-Options"] == "nosniff"
    app.config["SELLER_PAYOUT_RECEIPT_DIR"] = previous_root


def test_list_query_count_does_not_grow_with_rows(session, engine):
    store, version = _store_and_bank(session)
    _payout(session, store, version)
    session.flush()
    counts = []

    def count_queries(_conn, _cursor, _statement, _parameters, _context, _many):
        counts[-1] += 1

    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        counts.append(0)
        list_admin_payouts(session, per_page=1)
        first = counts[-1]
        for _ in range(5):
            _payout(session, store, version)
        session.flush()
        counts.append(0)
        list_admin_payouts(session, per_page=20)
        many = counts[-1]
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    assert first == many == 2
