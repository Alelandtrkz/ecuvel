from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryReservation,
    Order,
    PaymentAttempt,
    Product,
    ProductVariant,
    SellerOffer,
    User,
)
from app.models.enums import OfferStatus, PaymentStatus
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _login_as(client, user_id) -> None:
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user_id)
        browser_session["_fresh"] = True


def _prepare_demo_buyer(session: Session, base: BaseData, app, client) -> None:
    buyer = session.get(User, base.buyer_id)
    assert buyer is not None
    buyer.email = app.config["CHECKOUT_DEMO_BUYER_EMAIL"]
    buyer.email_verified_at = datetime.now(timezone.utc)
    _login_as(client, buyer.id)


def _additional_offer(
    session: Session, base: BaseData
) -> tuple[SellerOffer, InventoryBalance]:
    original_offer = session.get(SellerOffer, base.offer_id)
    assert original_offer is not None
    original_variant = session.get(ProductVariant, original_offer.variant_id)
    assert original_variant is not None
    original_product = session.get(Product, original_variant.product_id)
    assert original_product is not None
    token = uuid.uuid4().hex[:12]
    product = Product(
        category_id=original_product.category_id,
        title="Unselected Product",
        slug=f"unselected-{token}",
        is_active=True,
    )
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"UNSELECTED-{token}",
        attributes={},
        is_active=True,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=base.store_id,
        variant_id=variant.id,
        seller_sku=f"UNSELECTED-SELL-{token}",
        currency="USD",
        price=Decimal("7.00"),
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE,
    )
    session.add(offer)
    session.flush()
    balance = InventoryBalance(
        offer_id=offer.id,
        location_id=base.storage_location_id,
        on_hand_quantity=10,
        reserved_quantity=0,
        blocked_quantity=0,
    )
    session.add(balance)
    session.flush()
    return offer, balance


def _set_cart(client, *items: tuple[uuid.UUID, int, bool]) -> None:
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {
                str(offer_id): {
                    "quantity": quantity,
                    "selected": selected,
                }
                for offer_id, quantity, selected in items
            },
        }


def _cart_items(client) -> dict:
    with client.session_transaction() as browser_session:
        return browser_session.get("cart", {}).get("items", {})


def _checkout_token(client) -> str:
    response = client.get("/checkout")
    assert response.status_code == 200
    with client.session_transaction() as browser_session:
        return browser_session["checkout_draft"]["token"]


def _submit(client, token: str, **extra):
    return client.post(
        "/checkout",
        data={
            "checkout_token": token,
            "payment_method": "BANK_TRANSFER",
            **extra,
        },
    )


def test_checkout_redirects_when_cart_is_empty(client):
    response = client.get("/checkout")
    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_checkout_renders_only_selected_cart_items(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=10)
    second, _balance = _additional_offer(session, base)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 2, True), (second.id, 1, False))

    response = client.get("/checkout")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Product Test" in body
    assert "Unselected Product" not in body
    assert "Checkout protegido" in body
    assert 'name="csrf_token"' in body


def test_checkout_get_does_not_write_to_database(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=10)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 1, True))
    before = {
        "orders": session.scalar(select(func.count(Order.id))),
        "payments": session.scalar(select(func.count(PaymentAttempt.id))),
        "reservations": session.scalar(
            select(func.count(InventoryReservation.id))
        ),
    }

    assert client.get("/checkout").status_code == 200
    session.expire_all()
    assert session.scalar(select(func.count(Order.id))) == before["orders"]
    assert session.scalar(select(func.count(PaymentAttempt.id))) == before["payments"]
    assert (
        session.scalar(select(func.count(InventoryReservation.id)))
        == before["reservations"]
    )


def test_successful_checkout_removes_only_purchased_items(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=10)
    second, _balance = _additional_offer(session, base)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 2, True), (second.id, 1, False))
    token = _checkout_token(client)

    response = _submit(client, token)

    assert response.status_code == 302
    assert "/checkout/transferencia/" in response.headers["Location"]
    assert list(_cart_items(client)) == [str(second.id)]
    attempt = session.scalar(select(PaymentAttempt))
    assert attempt is not None
    assert attempt.status == PaymentStatus.AWAITING_PROOF
    assert client.get(response.headers["Location"]).status_code == 200


def test_failed_checkout_keeps_cart(client, app, session: Session):
    base = create_catalog_and_stock(session, stock=2)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 2, True))
    token = _checkout_token(client)
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    balance.reserved_quantity = 2
    session.commit()

    response = _submit(client, token)

    assert response.status_code == 302
    assert str(base.offer_id) in _cart_items(client)
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 0


def test_card_checkout_is_disabled_without_provider(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=2)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 1, True))
    token = _checkout_token(client)
    body = client.get("/checkout").get_data(as_text=True)
    assert 'value="CARD" disabled' in body

    response = client.post(
        "/checkout",
        data={"checkout_token": token, "payment_method": "CARD"},
    )
    assert response.status_code == 302
    assert session.scalar(select(func.count(Order.id))) == 0


def test_transfer_page_is_private_to_creating_session(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=2)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 1, True))
    location = _submit(client, _checkout_token(client)).headers["Location"]

    other_client = app.test_client()
    assert client.get(location).status_code == 200
    assert other_client.get(location).status_code == 302


def test_checkout_ignores_client_supplied_totals(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    offer.price = Decimal("13.25")
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 2, True))

    response = _submit(
        client,
        _checkout_token(client),
        price="0.01",
        total="0.01",
        buyer_id=str(uuid.uuid4()),
    )

    assert response.status_code == 302
    order = session.scalar(select(Order))
    attempt = session.scalar(select(PaymentAttempt))
    assert order is not None and attempt is not None
    assert order.grand_total == Decimal("26.50")
    assert attempt.amount == Decimal("26.50")


def test_csrf_rejects_mutation_without_token(
    client, app, session: Session
):
    base = create_catalog_and_stock(session)
    session.commit()
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            "/carrito/agregar",
            data={"offer_id": str(base.offer_id), "quantity": "1"},
        )
        assert response.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous


def test_checkout_still_rejects_when_stock_changes_after_cart_validation(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    _prepare_demo_buyer(session, base, app, client)
    session.commit()
    _set_cart(client, (base.offer_id, 3, True))
    token = _checkout_token(client)

    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    balance.reserved_quantity = 2
    session.commit()

    response = _submit(client, token)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/checkout")
    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 3
    assert session.scalar(select(func.count(Order.id))) == 0
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 0
