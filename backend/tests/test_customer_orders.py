from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PackageStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
)
from app.services.customer_orders import (
    CustomerOrderDisplayCode,
    get_customer_order_detail,
    get_customer_orders_page,
    resolve_customer_order_status,
)
from tests.factories import (
    BaseData,
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


def _authorize(client, session: Session, *order_ids: uuid.UUID) -> None:
    first_order = session.get(Order, order_ids[0])
    assert first_order is not None
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(first_order.buyer_id)
        browser_session["_fresh"] = True
        browser_session["checkout_order_ids"] = [str(order_id) for order_id in order_ids]


def _order_graph(
    session: Session,
    *,
    base: BaseData | None = None,
    quantity: int = 1,
    payment_status: PaymentStatus = PaymentStatus.AWAITING_PROOF,
    order_status: OrderStatus = OrderStatus.PENDING_PAYMENT,
    seller_status: SellerOrderStatus = SellerOrderStatus.PENDING_PAYMENT,
    proof_status: PaymentProofStatus | None = None,
    expires_at: datetime | None = None,
    package_status: PackageStatus | None = None,
    image_url: str | None = "/static/test-product.png",
):
    base = base or create_catalog_and_stock(session, stock=30)
    order_id, order_number, item_ids = create_order_items(session, base, [quantity])
    order = session.get(Order, order_id)
    assert order is not None
    order.status = order_status
    seller_order = session.get(OrderItem, item_ids[0]).seller_order
    seller_order.status = seller_status
    item = session.get(OrderItem, item_ids[0])
    item.image_url_snapshot = image_url
    expires = expires_at or datetime.now(timezone.utc) + timedelta(minutes=20)
    reserve_expires_at = (
        expires
        if expires > datetime.now(timezone.utc)
        else datetime.now(timezone.utc) + timedelta(minutes=20)
    )
    reservation_ids = reserve_item(
        session,
        base,
        item_ids[0],
        expires_at=reserve_expires_at,
    )
    for reservation_id in reservation_ids:
        session.get(InventoryReservation, reservation_id).expires_at = expires
    attempt = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=payment_status,
        amount=Decimal(quantity * 10),
        currency="USD",
        idempotency_key=f"checkout:{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=expires,
        approved_at=(
            datetime.now(timezone.utc)
            if payment_status == PaymentStatus.APPROVED
            else None
        ),
    )
    session.add(attempt)
    session.flush()
    proof = None
    if proof_status is not None:
        proof = PaymentProof(
            payment_attempt_id=attempt.id,
            storage_key=f"proofs/{uuid.uuid4().hex}.png",
            original_filename="proof.png",
            media_type="image/png",
            size_bytes=16,
            sha256="b" * 64,
            status=proof_status,
            upload_idempotency_key=f"upload:{uuid.uuid4().hex}",
            uploaded_by_user_id=base.buyer_id,
            rejection_reason="Comprobante no corresponde" if proof_status == PaymentProofStatus.REJECTED else None,
        )
        session.add(proof)
        session.flush()
    package = None
    if package_status is not None:
        now = datetime.now(timezone.utc)
        package = OrderPackage(
            order_item_id=item_ids[0],
            package_code=f"PKG-{uuid.uuid4().hex[:20]}",
            barcode=f"BAR-{uuid.uuid4().hex[:20]}",
            quantity=quantity,
            status=package_status,
            packed_at=now if package_status in {PackageStatus.PACKED, PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER} else None,
            ready_at=now if package_status in {PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER} else None,
            handed_over_at=now if package_status == PackageStatus.HANDED_OVER else None,
        )
        session.add(package)
        session.flush()
    return base, order, seller_order, item, attempt, proof, package


def _page(session: Session, order_ids: set[uuid.UUID], estado="por-entregar"):
    return get_customer_orders_page(
        session=session,
        order_ids=order_ids,
        active_filter=estado,
        page=1,
        page_size=10,
        pickup_point_name="Punto de entrega Ecuvel",
        pickup_point_address="DirecciÃ³n pendiente de configuraciÃ³n",
    )


def test_orders_page_lists_only_session_owned_orders(client, session: Session):
    _base, owned, *_ = _order_graph(session)
    _base2, other, *_ = _order_graph(session)
    session.commit()
    _authorize(client, session, owned.id)

    body = client.get("/pedidos").get_data(as_text=True)

    assert owned.order_number in body
    assert other.order_number not in body


def test_orders_filters_and_invalid_filter_fallback(client, session: Session):
    base, active, *_ = _order_graph(session)
    _base2, delivered, *_ = _order_graph(
        session,
        base=base,
        payment_status=PaymentStatus.APPROVED,
        order_status=OrderStatus.CONFIRMED,
        seller_status=SellerOrderStatus.CONFIRMED,
        package_status=PackageStatus.HANDED_OVER,
    )
    _base3, cancelled, *_ = _order_graph(
        session,
        base=base,
        payment_status=PaymentStatus.CANCELLED,
        order_status=OrderStatus.CANCELLED,
        seller_status=SellerOrderStatus.CANCELLED,
    )
    session.commit()
    _authorize(client, session, active.id, delivered.id, cancelled.id)

    active_body = client.get("/pedidos").get_data(as_text=True)
    delivered_body = client.get("/pedidos?estado=entregado").get_data(as_text=True)
    other_body = client.get("/pedidos?estado=otros").get_data(as_text=True)
    invalid_body = client.get("/pedidos?estado=zzz").get_data(as_text=True)

    assert active.order_number in active_body
    assert delivered.order_number not in active_body
    assert delivered.order_number in delivered_body
    assert cancelled.order_number in other_body
    assert active.order_number in invalid_body


def test_orders_page_paginates_results(client, session: Session, app):
    previous = app.config["CUSTOMER_ORDERS_PAGE_SIZE"]
    app.config["CUSTOMER_ORDERS_PAGE_SIZE"] = 2
    try:
        base = create_catalog_and_stock(session, stock=30)
        orders = [_order_graph(session, base=base)[1] for _ in range(3)]
        session.commit()
        _authorize(client, session, *(order.id for order in orders))

        body = client.get("/pedidos?page=2").get_data(as_text=True)

        assert "Página 2 de 2" in body
    finally:
        app.config["CUSTOMER_ORDERS_PAGE_SIZE"] = previous


@pytest.mark.parametrize(
    ("payment_status", "order_status", "proof_status", "seller_status", "package_status", "expected"),
    [
        (PaymentStatus.AWAITING_PROOF, OrderStatus.PENDING_PAYMENT, None, SellerOrderStatus.PENDING_PAYMENT, None, CustomerOrderDisplayCode.WAITING_PROOF),
        (PaymentStatus.PROCESSING, OrderStatus.PENDING_PAYMENT, PaymentProofStatus.PENDING_REVIEW, SellerOrderStatus.PENDING_PAYMENT, None, CustomerOrderDisplayCode.PROOF_UNDER_REVIEW),
        (PaymentStatus.APPROVED, OrderStatus.CONFIRMED, PaymentProofStatus.APPROVED, SellerOrderStatus.CONFIRMED, None, CustomerOrderDisplayCode.PAYMENT_CONFIRMED),
        (PaymentStatus.APPROVED, OrderStatus.FULFILLING, PaymentProofStatus.APPROVED, SellerOrderStatus.PICKING, None, CustomerOrderDisplayCode.PREPARING),
        (PaymentStatus.APPROVED, OrderStatus.CONFIRMED, PaymentProofStatus.APPROVED, SellerOrderStatus.CONFIRMED, PackageStatus.READY_FOR_PICKUP, CustomerOrderDisplayCode.READY_FOR_PICKUP),
        (PaymentStatus.APPROVED, OrderStatus.CONFIRMED, PaymentProofStatus.APPROVED, SellerOrderStatus.CONFIRMED, PackageStatus.HANDED_OVER, CustomerOrderDisplayCode.DELIVERED),
        (PaymentStatus.REJECTED, OrderStatus.CANCELLED, PaymentProofStatus.REJECTED, SellerOrderStatus.CANCELLED, None, CustomerOrderDisplayCode.PAYMENT_REJECTED),
        (PaymentStatus.CANCELLED, OrderStatus.CANCELLED, None, SellerOrderStatus.CANCELLED, None, CustomerOrderDisplayCode.CANCELLED),
    ],
)
def test_customer_order_status_mapping(
    session: Session,
    payment_status,
    order_status,
    proof_status,
    seller_status,
    package_status,
    expected,
):
    _base, order, seller_order, _item, attempt, proof, package = _order_graph(
        session,
        payment_status=payment_status,
        order_status=order_status,
        seller_status=seller_status,
        proof_status=proof_status,
        package_status=package_status,
    )

    status = resolve_customer_order_status(
        order=order,
        payment_attempt=attempt,
        payment_proof=proof,
        seller_orders=(seller_order,),
        packages=(package,) if package else (),
        now=datetime.now(timezone.utc),
    )

    assert status.code == expected


def test_expired_waiting_payment_is_visual_only_on_orders_page(client, session: Session):
    _base, order, _seller, _item, attempt, _proof, _package = _order_graph(
        session,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=3),
    )
    session.commit()
    _authorize(client, session, order.id)

    before = (order.status, attempt.status)
    body = client.get("/pedidos?estado=otros").get_data(as_text=True)
    session.expire_all()

    assert "Pago expirado" in body
    assert (session.get(Order, order.id).status, session.get(PaymentAttempt, attempt.id).status) == before


def test_waiting_review_and_delivered_actions(client, session: Session):
    base, waiting, *_ = _order_graph(session)
    _base2, review, *_ = _order_graph(
        session,
        base=base,
        payment_status=PaymentStatus.PROCESSING,
        proof_status=PaymentProofStatus.PENDING_REVIEW,
    )
    _base3, delivered, *_ = _order_graph(
        session,
        base=base,
        payment_status=PaymentStatus.APPROVED,
        order_status=OrderStatus.CONFIRMED,
        seller_status=SellerOrderStatus.CONFIRMED,
        proof_status=PaymentProofStatus.APPROVED,
        package_status=PackageStatus.HANDED_OVER,
    )
    session.commit()
    _authorize(client, session, waiting.id, review.id, delivered.id)

    active_body = client.get("/pedidos").get_data(as_text=True)
    delivered_body = client.get("/pedidos?estado=entregado").get_data(as_text=True)

    assert "Continuar pago" in active_body
    assert "Cancelar pedido" in active_body
    assert "Ver estado" in active_body
    assert "Ver comprobante" in active_body
    assert "Dejar comentario" not in delivered_body
    assert "Ver detalle" in delivered_body


def test_customer_can_open_owned_order_detail_and_not_other_session(
    client,
    app,
    session: Session,
):
    _base, order, *_ = _order_graph(session)
    session.commit()
    _authorize(client, session, order.id)

    response = client.get(f"/pedidos/{order.order_number}")
    other = app.test_client().get(f"/pedidos/{order.order_number}")

    assert response.status_code == 200
    assert "Productos" in response.get_data(as_text=True)
    assert other.status_code == 302


def test_order_detail_groups_items_by_seller_order_and_uses_private_proof_route(
    client,
    session: Session,
):
    _base, order, *_rest = _order_graph(
        session,
        payment_status=PaymentStatus.PROCESSING,
        proof_status=PaymentProofStatus.PENDING_REVIEW,
    )
    session.commit()
    proof = session.scalar(select(PaymentProof))
    _authorize(client, session, order.id)

    body = client.get(f"/pedidos/{order.order_number}").get_data(as_text=True)

    assert "Productos" in body
    assert "Store" in body
    assert f"/pagos/comprobantes/{proof.id}/archivo" in body
    assert "storage_key" not in body
    assert "proofs/" not in body


def test_order_card_uses_placeholder_when_image_missing(client, session: Session):
    _base, order, *_ = _order_graph(session, image_url=None)
    session.commit()
    _authorize(client, session, order.id)

    body = client.get("/pedidos").get_data(as_text=True)

    assert "product-placeholder.svg" in body


def test_get_orders_and_detail_do_not_modify_financial_state(client, session: Session):
    base, order, _seller, _item, attempt, _proof, _package = _order_graph(session)
    session.commit()
    _authorize(client, session, order.id)
    before = {
        "order": session.get(Order, order.id).status,
        "attempt": session.get(PaymentAttempt, attempt.id).status,
        "reservations": tuple(session.scalars(select(InventoryReservation.status)).all()),
        "balance": (
            session.get(InventoryBalance, base.balance_id).on_hand_quantity,
            session.get(InventoryBalance, base.balance_id).reserved_quantity,
            session.get(InventoryBalance, base.balance_id).blocked_quantity,
        ),
        "movements": session.scalar(select(func.count(InventoryMovement.id))),
    }

    assert client.get("/pedidos").status_code == 200
    assert client.get(f"/pedidos/{order.order_number}").status_code == 200
    session.expire_all()

    assert session.get(Order, order.id).status == before["order"]
    assert session.get(PaymentAttempt, attempt.id).status == before["attempt"]
    assert tuple(session.scalars(select(InventoryReservation.status)).all()) == before["reservations"]
    assert (
        session.get(InventoryBalance, base.balance_id).on_hand_quantity,
        session.get(InventoryBalance, base.balance_id).reserved_quantity,
        session.get(InventoryBalance, base.balance_id).blocked_quantity,
    ) == before["balance"]
    assert session.scalar(select(func.count(InventoryMovement.id))) == before["movements"]


def test_orders_page_does_not_expose_sensitive_payment_data(client, session: Session):
    _base, order, *_ = _order_graph(
        session,
        payment_status=PaymentStatus.PROCESSING,
        proof_status=PaymentProofStatus.PENDING_REVIEW,
    )
    session.commit()
    _authorize(client, session, order.id)

    body = client.get("/pedidos").get_data(as_text=True)

    assert "sha256" not in body.lower()
    assert "ocr" not in body.lower()
    assert "payload" not in body.lower()
    assert "proofs/" not in body


def test_orders_page_uses_bounded_query_count(client, session: Session, engine):
    orders = [_order_graph(session)[1] for _ in range(5)]
    session.commit()
    _authorize(client, session, *(order.id for order in orders))
    counter = {"count": 0}

    def before_cursor_execute(*_args, **_kwargs):
        counter["count"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        response = client.get("/pedidos")
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert response.status_code == 200
    assert counter["count"] <= 12
