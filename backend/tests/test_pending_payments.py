from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
)
from app.services import pending_payments as pending_service
from app.services.pending_payments import (
    InvalidPendingPaymentTransitionError,
    cancel_pending_bank_transfer_order,
    expire_pending_bank_transfer_payment,
    expire_pending_bank_transfer_payments,
)
from tests.factories import (
    create_catalog_and_stock,
    create_order_items,
    reserve_item,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _payment_graph(
    session: Session,
    *,
    quantities: list[int] | None = None,
    expires_at: datetime | None = None,
    status: PaymentStatus = PaymentStatus.AWAITING_PROOF,
    proof: bool = False,
):
    quantities = quantities or [2]
    base = create_catalog_and_stock(session, stock=20)
    order_id, order_number, item_ids = create_order_items(
        session,
        base,
        quantities,
    )
    expires = expires_at or datetime.now(timezone.utc) + timedelta(minutes=20)
    reservation_expires_at = (
        expires
        if expires > datetime.now(timezone.utc)
        else datetime.now(timezone.utc) + timedelta(minutes=20)
    )
    reservation_ids: list[uuid.UUID] = []
    for item_id in item_ids:
        reservation_ids.extend(
            reserve_item(
                session,
                base,
                item_id,
                expires_at=reservation_expires_at,
            )
        )
    attempt = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=status,
        amount=Decimal(sum(quantities) * 10),
        currency="USD",
        idempotency_key=f"checkout:{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=expires,
    )
    session.add(attempt)
    session.flush()
    for reservation_id in reservation_ids:
        session.get(InventoryReservation, reservation_id).expires_at = expires
    if proof:
        session.add(
            PaymentProof(
                payment_attempt_id=attempt.id,
                storage_key=f"proofs/{uuid.uuid4().hex}.png",
                original_filename="proof.png",
                media_type="image/png",
                size_bytes=16,
                sha256="a" * 64,
                upload_idempotency_key=f"upload:{uuid.uuid4().hex}",
                uploaded_by_user_id=base.buyer_id,
            )
        )
        session.flush()
    return base, order_id, order_number, tuple(reservation_ids), attempt


def _authorize(client, session: Session, order_id: uuid.UUID) -> None:
    order = session.get(Order, order_id)
    assert order is not None
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(order.buyer_id)
        browser_session["_fresh"] = True
        browser_session["checkout_order_ids"] = [str(order_id)]


def test_cancel_pending_order_releases_inventory_once(session: Session):
    base, order_id, _order_number, reservation_ids, attempt = _payment_graph(session)
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    before_on_hand = balance.on_hand_quantity

    result = cancel_pending_bank_transfer_order(
        session=session,
        payment_attempt_id=attempt.id,
        actor_user_id=base.buyer_id,
    )

    assert result.payment_status == PaymentStatus.CANCELLED
    assert result.order_status == OrderStatus.CANCELLED
    assert result.seller_order_status == SellerOrderStatus.CANCELLED
    assert result.released_reservations == len(reservation_ids)
    assert session.get(Order, order_id).status == OrderStatus.CANCELLED
    assert session.scalar(select(SellerOrder.status)) == SellerOrderStatus.CANCELLED
    assert {session.get(InventoryReservation, rid).status for rid in reservation_ids} == {
        ReservationStatus.RELEASED
    }
    assert balance.on_hand_quantity == before_on_hand
    assert balance.reserved_quantity == 0
    assert session.scalar(select(func.count(InventoryMovement.id))) == 2

    replay = cancel_pending_bank_transfer_order(
        session=session,
        payment_attempt_id=attempt.id,
        actor_user_id=base.buyer_id,
    )
    assert replay.replayed
    assert session.scalar(select(func.count(InventoryMovement.id))) == 2


def test_cancel_rejects_payment_with_proof(session: Session):
    base, _order_id, _order_number, _reservation_ids, attempt = _payment_graph(
        session,
        proof=True,
    )

    with pytest.raises(InvalidPendingPaymentTransitionError):
        cancel_pending_bank_transfer_order(
            session=session,
            payment_attempt_id=attempt.id,
            actor_user_id=base.buyer_id,
        )


def test_expire_pending_order_marks_order_expired_and_sellers_cancelled(
    session: Session,
):
    base, order_id, _order_number, reservation_ids, attempt = _payment_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    result = expire_pending_bank_transfer_payment(
        session=session,
        payment_attempt_id=attempt.id,
    )

    assert result.payment_status == PaymentStatus.EXPIRED
    assert result.order_status == OrderStatus.EXPIRED
    assert session.get(Order, order_id).status == OrderStatus.EXPIRED
    assert session.scalar(select(SellerOrder.status)) == SellerOrderStatus.CANCELLED
    assert {session.get(InventoryReservation, rid).status for rid in reservation_ids} == {
        ReservationStatus.RELEASED
    }
    assert session.get(InventoryBalance, base.balance_id).reserved_quantity == 0


def test_expire_skips_not_due_and_with_proof(session: Session):
    _base, _order_id, _order_number, _reservation_ids, attempt = _payment_graph(
        session,
    )
    with pytest.raises(InvalidPendingPaymentTransitionError):
        expire_pending_bank_transfer_payment(
            session=session,
            payment_attempt_id=attempt.id,
        )

    _base2, _order_id2, _order_number2, _reservation_ids2, attempt2 = _payment_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        proof=True,
    )
    with pytest.raises(InvalidPendingPaymentTransitionError):
        expire_pending_bank_transfer_payment(
            session=session,
            payment_attempt_id=attempt2.id,
        )


def test_expiration_batch_is_idempotent(session: Session):
    _base, _order_id, _order_number, _reservation_ids, _attempt = _payment_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    first = expire_pending_bank_transfer_payments(session=session, limit=100)
    second = expire_pending_bank_transfer_payments(session=session, limit=100)

    assert first.processed == 1
    assert first.expired == 1
    assert second.processed == 0
    assert second.expired == 0


def test_expiration_rolls_back_when_release_fails(session: Session, monkeypatch):
    base, order_id, _order_number, reservation_ids, attempt = _payment_graph(
        session,
        quantities=[1, 1],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    original = pending_service.release_inventory_reservation
    calls = {"count": 0}
    session.commit()

    def flaky_release(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise pending_service.InventoryServiceError("boom")
        return original(**kwargs)

    monkeypatch.setattr(pending_service, "release_inventory_reservation", flaky_release)

    with pytest.raises(pending_service.PendingPaymentIntegrityError):
        with session.begin():
            expire_pending_bank_transfer_payment(
                session=session,
                payment_attempt_id=attempt.id,
            )

    session.expire_all()
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.AWAITING_PROOF
    assert {session.get(InventoryReservation, rid).status for rid in reservation_ids} == {
        ReservationStatus.ACTIVE
    }
    assert session.get(InventoryBalance, base.balance_id).reserved_quantity == 2


def test_orders_page_is_private_and_shows_pending_actions(
    client,
    app,
    session: Session,
):
    _base, order_id, order_number, _reservation_ids, _attempt = _payment_graph(session)
    session.commit()
    _authorize(client, session, order_id)

    body = client.get("/pedidos").get_data(as_text=True)
    assert "Esperando comprobante" in body
    assert order_number in body
    assert "Continuar pago" in body
    assert "Cancelar pedido" in body

    other_client = app.test_client()
    other_body = other_client.get("/pedidos").get_data(as_text=True)
    assert order_number not in other_body


def test_cancel_route_requires_session_ownership(client, app, session: Session):
    _base, order_id, order_number, _reservation_ids, attempt = _payment_graph(session)
    session.commit()

    assert app.test_client().post(f"/pedidos/{order_number}/cancelar").status_code == 302

    _authorize(client, session, order_id)
    response = client.post(f"/pedidos/{order_number}/cancelar")
    assert response.status_code == 302
    session.expire_all()
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.CANCELLED


def test_transfer_page_expires_stale_order_before_upload(client, session: Session):
    _base, order_id, order_number, _reservation_ids, attempt = _payment_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    session.commit()
    _authorize(client, session, order_id)

    response = client.get(f"/checkout/transferencia/{order_number}")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/pedidos")
    session.expire_all()
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.EXPIRED


@pytest.mark.concurrency
def test_concurrent_expiration_only_releases_once(
    session: Session,
    session_factory,
    concurrent_runner,
):
    _base, _order_id, _order_number, _reservation_ids, attempt = _payment_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )
    session.commit()

    def worker(barrier):
        database_session = session_factory()
        try:
            barrier.wait()
            with database_session.begin():
                return expire_pending_bank_transfer_payment(
                    session=database_session,
                    payment_attempt_id=attempt.id,
                )
        finally:
            database_session.close()

    results, errors = concurrent_runner([worker, worker])
    assert not errors
    assert sorted(result.replayed for result in results) == [False, True]

    check = session_factory()
    try:
        assert check.scalar(select(func.count(InventoryMovement.id))) == 2
    finally:
        check.close()
