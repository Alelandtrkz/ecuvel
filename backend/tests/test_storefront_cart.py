from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Product,
    ProductVariant,
    SellerOffer,
)
from app.models.enums import OfferStatus
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _entities(
    session: Session,
    base: BaseData,
) -> tuple[Product, ProductVariant, SellerOffer, InventoryBalance]:
    offer = session.get(SellerOffer, base.offer_id)
    balance = session.get(InventoryBalance, base.balance_id)
    assert offer is not None and balance is not None
    variant = session.get(ProductVariant, offer.variant_id)
    assert variant is not None
    product = session.get(Product, variant.product_id)
    assert product is not None
    return product, variant, offer, balance


def _create_additional_offer(
    session: Session,
    base: BaseData,
    *,
    title: str = "Second Product",
    price: Decimal = Decimal("12.00"),
    stock: int = 10,
) -> tuple[Product, SellerOffer, InventoryBalance]:
    original_product, _variant, _offer, _balance = _entities(session, base)
    token = uuid.uuid4().hex[:12]
    product = Product(
        category_id=original_product.category_id,
        title=title,
        slug=f"cart-product-{token}",
        is_active=True,
    )
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"CART-SKU-{token}",
        attributes={},
        is_active=True,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=base.store_id,
        variant_id=variant.id,
        seller_sku=f"CART-SELL-{token}",
        currency="USD",
        price=price,
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE,
    )
    session.add(offer)
    session.flush()
    balance = InventoryBalance(
        offer_id=offer.id,
        location_id=base.storage_location_id,
        on_hand_quantity=stock,
        reserved_quantity=0,
        blocked_quantity=0,
    )
    session.add(balance)
    session.flush()
    return product, offer, balance


def _add(client, offer_id: uuid.UUID, quantity: str = "1", **extra):
    data = {
        "offer_id": str(offer_id),
        "quantity": quantity,
        "next": "/carrito",
        **extra,
    }
    return client.post("/carrito/agregar", data=data)


def _cart_items(client) -> dict:
    with client.session_transaction() as browser_session:
        return browser_session.get("cart", {}).get("items", {})


def _set_cart_item(
    client,
    offer_id: uuid.UUID,
    quantity: int,
    selected: bool = True,
) -> None:
    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": 1,
            "items": {
                str(offer_id): {
                    "quantity": quantity,
                    "selected": selected,
                }
            },
        }


def test_empty_cart_returns_200(client):
    response = client.get("/carrito")
    assert response.status_code == 200
    assert "Tu carrito está vacío" in response.get_data(as_text=True)
    assert "Resumen del carrito" not in response.get_data(as_text=True)

    with client.session_transaction() as browser_session:
        browser_session["cart"] = {
            "version": "manipulated",
            "items": {"not-a-uuid": {"quantity": "x"}},
        }
    repaired = client.get("/carrito")
    assert repaired.status_code == 200
    assert _cart_items(client) == {}


def test_add_valid_offer_to_cart(client, session: Session):
    base = create_catalog_and_stock(session, stock=20)
    _product, _variant, offer, balance = _entities(session, base)
    before = (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    )
    session.commit()

    response = _add(
        client,
        offer.id,
        "2",
        price="0.01",
        next="https://example.com/phishing",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/carrito")
    stored_item = _cart_items(client)[str(offer.id)]
    assert stored_item == {"quantity": 2, "selected": True}
    assert "price" not in stored_item
    session.expire_all()
    refreshed = session.get(InventoryBalance, balance.id)
    assert refreshed is not None
    assert (
        refreshed.on_hand_quantity,
        refreshed.reserved_quantity,
        refreshed.blocked_quantity,
    ) == before


def test_adding_same_offer_increments_quantity(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id, "2")
    _add(client, base.offer_id, "1")

    items = _cart_items(client)
    assert len(items) == 1
    assert items[str(base.offer_id)]["quantity"] == 3


def test_add_rejects_invalid_quantity(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()

    for invalid_quantity in ["0", "-1", "1.5", "texto", "1000"]:
        response = _add(client, base.offer_id, invalid_quantity)
        assert response.status_code == 302
        assert str(base.offer_id) not in _cart_items(client)

    invalid_offer = client.post(
        "/carrito/agregar",
        data={"offer_id": "not-a-uuid", "quantity": "1"},
    )
    assert invalid_offer.status_code == 302


def test_update_cart_item_quantity(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id, "3")

    response = client.post(
        f"/carrito/items/{base.offer_id}/cantidad",
        data={"quantity": "2"},
    )

    assert response.status_code == 302
    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 2


def test_remove_cart_item(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id)

    response = client.post(
        f"/carrito/items/{base.offer_id}/eliminar"
    )

    assert response.status_code == 302
    assert _cart_items(client) == {}


def test_cart_item_selection_changes_summary(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id, "2")

    client.post(f"/carrito/items/{base.offer_id}/seleccion", data={})
    response = client.get("/carrito")
    body = response.get_data(as_text=True)

    assert _cart_items(client)[str(base.offer_id)]["selected"] is False
    assert "0 unidades" in body
    assert "Pago total" in body
    assert "$0.00" in body


def test_select_all_cart_items(client, session: Session):
    base = create_catalog_and_stock(session)
    _product, second_offer, _balance = _create_additional_offer(session, base)
    session.commit()
    _add(client, base.offer_id)
    _add(client, second_offer.id)
    client.post(f"/carrito/items/{second_offer.id}/seleccion", data={})

    response = client.post("/carrito/seleccion", data={"selected": "1"})

    assert response.status_code == 302
    assert all(item["selected"] for item in _cart_items(client).values())


def test_remove_selected_cart_items(client, session: Session):
    base = create_catalog_and_stock(session)
    _product, second_offer, _balance = _create_additional_offer(session, base)
    session.commit()
    _add(client, base.offer_id)
    _add(client, second_offer.id)
    client.post(f"/carrito/items/{second_offer.id}/seleccion", data={})

    response = client.post("/carrito/eliminar-seleccionados")

    assert response.status_code == 302
    assert list(_cart_items(client)) == [str(second_offer.id)]


def test_cart_totals_are_calculated_from_database_prices(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    _product, _variant, offer, _balance = _entities(session, base)
    offer.price = Decimal("10.00")
    offer.compare_at_price = Decimal("15.00")
    session.commit()

    _add(client, offer.id, "2", price="9999.99", total="0.01")
    body = client.get("/carrito").get_data(as_text=True)
    summary = body.split('class="cart-summary"', maxsplit=1)[1]

    assert "$30.00" in summary
    assert "-$10.00" in summary
    assert "$20.00" in summary
    assert "$9,999.99" not in body


def test_inactive_offer_is_not_checkout_eligible(client, session: Session):
    base = create_catalog_and_stock(session)
    product, _variant, offer, _balance = _entities(session, base)
    session.commit()
    _add(client, offer.id)
    offer.status = OfferStatus.PAUSED
    session.commit()

    body = client.get("/carrito").get_data(as_text=True)

    assert product.title in body
    assert "No disponible" in body
    assert "0 unidades" in body
    assert _cart_items(client)[str(offer.id)]["selected"] is False


def test_header_cart_badge_uses_total_quantity(client, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id, "3")

    body = client.get("/").get_data(as_text=True)

    assert 'class="header-cart-badge">3<' in body
    assert "Carrito, 3 productos" in body


def test_cart_is_isolated_between_clients(app, session: Session):
    base = create_catalog_and_stock(session)
    session.commit()
    first_client = app.test_client()
    second_client = app.test_client()

    _add(first_client, base.offer_id, "2")

    assert str(base.offer_id) in _cart_items(first_client)
    assert _cart_items(second_client) == {}
    db.session.remove()


def test_cart_operations_do_not_reserve_or_reduce_inventory(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session, stock=20)
    _product, _variant, offer, balance = _entities(session, base)
    session.commit()
    before_balance = (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    )

    _add(client, offer.id, "2")
    client.post(
        f"/carrito/items/{offer.id}/cantidad",
        data={"quantity": "3"},
    )
    client.post(f"/carrito/items/{offer.id}/seleccion", data={})
    client.post(f"/carrito/items/{offer.id}/eliminar")

    session.expire_all()
    refreshed = session.get(InventoryBalance, balance.id)
    assert refreshed is not None
    assert (
        refreshed.on_hand_quantity,
        refreshed.reserved_quantity,
        refreshed.blocked_quantity,
    ) == before_balance
    assert session.scalar(select(func.count(InventoryMovement.id))) == 0
    assert session.scalar(select(func.count(InventoryReservation.id))) == 0


def test_cart_get_does_not_write_to_database(client, session: Session):
    base = create_catalog_and_stock(session)
    product, _variant, _offer, balance = _entities(session, base)
    session.commit()
    product_updated_at = product.updated_at
    balance_updated_at = balance.updated_at
    _add(client, base.offer_id)

    response = client.get("/carrito")

    assert response.status_code == 200
    session.expire_all()
    assert session.get(Product, product.id).updated_at == product_updated_at
    assert (
        session.get(InventoryBalance, balance.id).updated_at
        == balance_updated_at
    )


def test_cart_uses_placeholder_for_product_without_image(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    session.commit()
    _add(client, base.offer_id)

    body = client.get("/carrito").get_data(as_text=True)

    assert "images/placeholders/product-placeholder.svg" in body
    assert "Imagen provisional de Product Test" in body


def test_cart_recommendations_exclude_cart_products(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    product, _variant, _offer, _balance = _entities(session, base)
    recommended, _offer_two, _balance_two = _create_additional_offer(
        session,
        base,
        title="Recommended Cart Product",
    )
    session.commit()
    _add(client, base.offer_id)

    body = client.get("/carrito").get_data(as_text=True)
    recommendations = body.split(
        'class="cart-recommendations"',
        maxsplit=1,
    )[1]

    assert recommended.title in recommendations
    assert product.title not in recommendations
    assert f"/productos/{recommended.slug}" in recommendations


def test_cart_allows_quantity_equal_to_available_stock(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()

    response = _add(client, base.offer_id, "3")

    assert response.status_code == 302
    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 3


def test_cart_rejects_add_quantity_above_available_stock(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()

    response = _add(client, base.offer_id, "4")

    assert response.status_code == 302
    assert str(base.offer_id) not in _cart_items(client)


def test_cart_rejects_increment_when_total_exceeds_stock(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()
    _add(client, base.offer_id, "2")

    response = _add(client, base.offer_id, "2")

    assert response.status_code == 302
    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 2


def test_cart_rejects_quantity_update_above_available_stock(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()
    _add(client, base.offer_id, "3")

    response = client.post(
        f"/carrito/items/{base.offer_id}/cantidad",
        data={"quantity": "48"},
    )

    assert response.status_code == 302
    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 3


def test_cart_renders_real_maximum_and_disables_increment(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=8)
    _product, _variant, offer, _balance = _entities(session, base)
    offer.price = Decimal("45.00")
    offer.compare_at_price = Decimal("60.00")
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    balance.reserved_quantity = 5
    session.commit()
    _add(client, base.offer_id, "3")

    body = client.get("/carrito").get_data(as_text=True)

    assert 'max="3"' in body
    assert 'data-max-quantity="3"' in body
    assert 'data-cart-quantity-increase disabled aria-disabled="true"' in body
    assert "Solo quedan 3 unidades disponibles." in body
    assert "$135.00" in body


def test_cart_clamps_stale_quantity_to_available_stock(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()
    _set_cart_item(client, base.offer_id, 48)

    body = client.get("/carrito").get_data(as_text=True)

    assert _cart_items(client)[str(base.offer_id)]["quantity"] == 3
    assert "48 a 3 porque cambi" in body
    assert "3 unidades" in body
    second_body = client.get("/carrito").get_data(as_text=True)
    assert "48 a 3 porque cambi" not in second_body


def test_cart_unselects_item_when_stock_becomes_zero(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()
    _add(client, base.offer_id, "2")
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    balance.reserved_quantity = 3
    session.commit()

    body = client.get("/carrito").get_data(as_text=True)

    stored = _cart_items(client)[str(base.offer_id)]
    assert stored == {"quantity": 2, "selected": False}
    assert "Producto agotado." in body
    assert "0 unidades" in body
    assert 'name="quantity"' in body and "disabled" in body


def test_product_detail_uses_available_stock_limit(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=8)
    product, _variant, _offer, balance = _entities(session, base)
    balance.reserved_quantity = 5
    session.commit()

    body = client.get(f"/productos/{product.slug}").get_data(as_text=True)

    assert 'max="3"' in body
    assert 'data-max-quantity="3"' in body
    assert "Solo quedan 3 unidades disponibles." in body


def test_stock_rejection_does_not_modify_inventory(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    before = (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    )
    session.commit()

    _add(client, base.offer_id, "48")
    client.get("/carrito")

    session.expire_all()
    refreshed = session.get(InventoryBalance, base.balance_id)
    assert refreshed is not None
    assert (
        refreshed.on_hand_quantity,
        refreshed.reserved_quantity,
        refreshed.blocked_quantity,
    ) == before
    assert session.scalar(select(func.count(InventoryReservation.id))) == 0
    assert session.scalar(select(func.count(InventoryMovement.id))) == 0


def test_cart_returns_json_conflict_for_manipulated_quantity(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()

    response = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(base.offer_id),
            "quantity": "48",
            "next": "/carrito",
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "ok": False,
        "error": "insufficient_stock",
        "available_quantity": 3,
        "current_cart_quantity": 0,
        "requested_quantity": 48,
        "max_quantity": 3,
        "message": "Solo hay 3 unidades disponibles de Product Test.",
    }
    assert _cart_items(client) == {}


def test_cart_uses_one_inventory_query_for_multiple_offers(
    client, app, session: Session
):
    base = create_catalog_and_stock(session, stock=10)
    _product, second_offer, _balance = _create_additional_offer(session, base)
    session.commit()
    _add(client, base.offer_id)
    _add(client, second_offer.id)
    inventory_queries: list[str] = []

    def record_query(_conn, _cursor, statement, _parameters, _context, _many):
        if "inventory_balances" in statement:
            inventory_queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", record_query)
    try:
        response = client.get("/carrito")
    finally:
        event.remove(db.engine, "before_cursor_execute", record_query)

    assert response.status_code == 200
    assert len(inventory_queries) == 1
