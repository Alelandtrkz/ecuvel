from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    PaymentAttempt,
    ProductReview,
    User,
)
from app.models.enums import PaymentMethod, PaymentStatus, UserStatus
from app.services.product_reviews import create_product_review
from tests.factories import (
    create_catalog_and_stock,
    create_order_items,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app, tmp_path):
    app.config["PRODUCT_REVIEW_UPLOAD_DIR"] = str(tmp_path)
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _prepare_buyer(session, buyer_id, *, email="cliente-review@test.local") -> User:
    buyer = session.get(User, buyer_id)
    assert buyer is not None
    buyer.email = email
    buyer.email_normalized = email.casefold()
    buyer.password_hash = generate_password_hash("correct horse battery staple")
    buyer.full_name = "Cliente Reseñas"
    buyer.status = UserStatus.ACTIVE
    buyer.email_verified_at = datetime.now(timezone.utc)
    buyer.is_active = True
    session.flush()
    return buyer


def _login(client, email="cliente-review@test.local"):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": email,
            "password": "correct horse battery staple",
            "next": "/",
        },
        follow_redirects=False,
    )


def _delivered_order(session):
    base = create_catalog_and_stock(session)
    buyer = _prepare_buyer(session, base.buyer_id)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    _add_payment_attempt(session, ready.order_id, status=PaymentStatus.APPROVED)
    session.commit()
    return base, buyer, ready


def _add_payment_attempt(
    session,
    order_id,
    *,
    status=PaymentStatus.AWAITING_PROOF,
) -> None:
    order = session.get(Order, order_id)
    assert order is not None
    session.add(
        PaymentAttempt(
            order_id=order_id,
            method=PaymentMethod.BANK_TRANSFER,
            status=status,
            amount=Decimal(order.grand_total),
            currency=order.currency,
            idempotency_key=f"test-review-{uuid.uuid4()}",
            request_fingerprint="0" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            approved_at=(
                datetime.now(timezone.utc)
                if status == PaymentStatus.APPROVED
                else None
            ),
        )
    )
    session.flush()


def test_delivered_order_detail_has_modal_trigger_and_single_dialog(client, session):
    _base, buyer, ready = _delivered_order(session)
    assert _login(client, buyer.email).status_code == 302

    response = client.get(f"/pedidos/{ready.order_number}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="button button--primary order-review-action js-review-modal-trigger"' in body
    assert "/resena" in body
    assert "modal=1" in body
    assert 'data-review-url="' in body
    assert "Dejar un comentario" in body
    assert body.count("<dialog") == 1
    assert "data-review-dialog" in body
    assert 'aria-modal="true"' in body
    assert "Cerrar formulario de reseña" in body


def test_not_delivered_order_detail_does_not_show_review_button(client, session):
    base = create_catalog_and_stock(session)
    buyer = _prepare_buyer(session, base.buyer_id)
    order_id, order_number, _item_ids = create_order_items(session, base, [1])
    _add_payment_attempt(session, order_id)
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    response = client.get(f"/pedidos/{order_number}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "order-review-action" not in body
    assert "Disponible al entregar" in body


def test_existing_review_changes_detail_action_to_pending(client, session):
    base, buyer, ready = _delivered_order(session)
    create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=buyer.id,
        rating=5,
        body="Producto correcto y entrega verificada.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    response = client.get(f"/pedidos/{ready.order_number}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Reseña en revisión" in body
    assert "order-review-action" not in body


def test_modal_fragment_and_fallback_share_review_form_partial(client, session):
    _base, buyer, ready = _delivered_order(session)
    assert _login(client, buyer.email).status_code == 302
    url = f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena"

    full_page = client.get(url)
    fragment = client.get(f"{url}?modal=1", headers={"X-Requested-With": "fetch"})
    full_body = full_page.get_data(as_text=True)
    fragment_body = fragment.get_data(as_text=True)

    assert full_page.status_code == 200
    assert fragment.status_code == 200
    assert "review-form-page" in full_body
    assert "review-form-page" not in fragment_body
    for body in (full_body, fragment_body):
        assert 'data-review-form' in body
        assert 'name="csrf_token"' in body
        assert body.count('name="rating"') == 5
        assert 'type="file"' in body
        assert "visually-hidden" in body
        assert "multiple" in body
        assert 'name="body"' in body
        assert "Enviar comentario" in body


def test_modal_fragment_validates_owner_and_eligibility(client, session):
    base, _buyer, ready = _delivered_order(session)
    other = _prepare_buyer(
        session,
        base.operator_id,
        email="otro-review@test.local",
    )
    session.commit()
    assert _login(client, other.email).status_code == 302

    response = client.get(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena?modal=1",
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 404


def test_json_post_rejects_duplicate_with_safe_error(client, session):
    _base, buyer, ready = _delivered_order(session)
    create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=buyer.id,
        rating=4,
        body="Primera reseña pendiente de revisión.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    response = client.post(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena",
        data={
            "rating": "5",
            "body": "Intento duplicado que debe fallar seguro.",
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert "duplicate" not in payload["message"].lower()
    assert "<" not in payload["message"]


def test_review_post_does_not_modify_financial_or_inventory_state(client, session):
    _base, buyer, ready = _delivered_order(session)
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    before = _financial_inventory_snapshot(session)
    response = client.post(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena",
        data={
            "rating": "5",
            "body": "Reseña visual sin tocar pagos ni inventario.",
        },
        headers={"Accept": "application/json"},
    )
    after = _financial_inventory_snapshot(session)

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert after == before
    assert session.scalar(select(func.count()).select_from(ProductReview)) == 1


def _financial_inventory_snapshot(session):
    balance_rows = session.execute(
        select(
            InventoryBalance.id,
            InventoryBalance.on_hand_quantity,
            InventoryBalance.reserved_quantity,
            InventoryBalance.blocked_quantity,
        ).order_by(InventoryBalance.id)
    ).all()
    reservation_rows = session.execute(
        select(
            InventoryReservation.id,
            InventoryReservation.status,
            InventoryReservation.quantity,
            InventoryReservation.released_at,
            InventoryReservation.consumed_at,
        ).order_by(InventoryReservation.id)
    ).all()
    return {
        "orders": session.execute(
            select(Order.id, Order.status).order_by(Order.id)
        ).all(),
        "payments": session.execute(
            select(PaymentAttempt.id, PaymentAttempt.status).order_by(PaymentAttempt.id)
        ).all(),
        "balances": balance_rows,
        "reservations": reservation_rows,
        "movement_count": session.scalar(
            select(func.count()).select_from(InventoryMovement)
        ),
    }
