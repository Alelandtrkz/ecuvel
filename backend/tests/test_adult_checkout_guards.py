from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import InventoryReservation, Order, User
from app.models.enums import PaymentMethod
from app.services.checkout import CheckoutItemUnavailableError, build_checkout_preview, create_checkout_order
from app.services.cart_storage import load_cart_state_for_identity
from tests.test_adult_access_policy import adult_case, enable_public, login_as


pytestmark = pytest.mark.integration


def cart_state(case):
    return {"version": 1, "items": {str(case.offer.id): {"quantity": 1, "selected": True}}}


def test_existing_cart_add_and_quantity_mutations_recheck_disabled_flag(adult_case, app, monkeypatch):
    case = adult_case
    enable_public(app, monkeypatch, case)
    client = app.test_client()
    login_as(client, case.buyer)
    headers = {"Accept": "application/json"}
    assert client.post("/carrito/agregar", data={"offer_id": case.offer.id, "quantity": 1}, headers=headers).status_code == 200
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    assert client.post("/carrito/agregar", data={"offer_id": case.offer.id, "quantity": 1}, headers=headers).status_code == 404
    assert client.post(f"/carrito/items/{case.offer.id}/cantidad", data={"quantity": 2}, headers=headers).status_code == 404
    body = client.get("/carrito").get_data(as_text=True)
    assert case.product.title not in body and case.product.slug not in body
    assert case.media.public_id not in body


def test_restored_guest_cart_does_not_disclose_adult_ids(adult_case, app):
    case = adult_case
    client = app.test_client()
    with client.session_transaction() as browser:
        browser["cart"] = cart_state(case)
    response = client.get("/carrito")
    assert response.status_code == 200
    assert str(case.offer.id) not in response.get_data(as_text=True)
    assert case.product.title not in response.get_data(as_text=True)


def test_checkout_service_rejects_disabled_adult_even_with_active_categories(adult_case, app, session):
    case = adult_case
    assert case.parent.is_active and case.leaf.is_active
    preview = build_checkout_preview(session=session, cart_state=cart_state(case))
    assert preview.lines == () and not preview.can_submit
    assert all(case.product.title not in issue.message for issue in preview.issues)
    with pytest.raises(CheckoutItemUnavailableError):
        create_checkout_order(session=session, buyer_id=case.buyer.id, cart_state=cart_state(case),
            payment_method=PaymentMethod.BANK_TRANSFER, idempotency_key=str(uuid.uuid4()),
            reservation_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20))
    assert session.scalar(select(func.count()).select_from(Order)) == 0
    assert session.scalar(select(func.count()).select_from(InventoryReservation)) == 0


def test_authorized_checkout_revalidates_release_and_can_create_order(adult_case, app, session, monkeypatch):
    case = adult_case
    enable_public(app, monkeypatch, case)
    result = create_checkout_order(session=session, buyer_id=case.buyer.id, cart_state=cart_state(case),
        payment_method=PaymentMethod.BANK_TRANSFER, idempotency_key=str(uuid.uuid4()),
        reservation_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20))
    assert session.get(Order, result.order_id).buyer_id == case.buyer.id
    assert session.scalar(select(func.count()).select_from(InventoryReservation)) == 1
    session.commit()
    monkeypatch.setitem(app.config, "ADULT_PUBLIC_ENABLED", False)
    client = app.test_client()
    login_as(client, case.buyer)
    history = client.get(f"/pedidos/{result.order_number}")
    assert history.status_code == 200
    assert case.product.title in history.get_data(as_text=True)
    assert client.get(f"/productos/{case.product.slug}").status_code == 404
    outsider = app.test_client().get(f"/pedidos/{result.order_number}")
    assert outsider.status_code in (302, 404)
    assert case.product.title not in outsider.get_data(as_text=True)
