from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep
import uuid

import pytest
from sqlalchemy import delete, event, func, select, text

from app.models import (
    CartItem, Category, InventoryBalance, InventoryReservation, Order, OrderItem,
    PaymentAttempt, PaymentProof, Product, ProductVariant, SellerOffer, SellerOrder, Store, User,
)
from app.models.enums import OfferStatus, StoreStatus
from app.services.cart_storage import mutate_cart_for_user
from tests.factories import create_catalog_and_stock
from tests.test_checkout_service import _create_offer
from tests.test_storefront_checkout import _checkout_token, _login_as, _submit


pytestmark = pytest.mark.integration


def put_cart(session, buyer_id, *lines):
    items = {str(offer): {"quantity": qty, "selected": selected} for offer, qty, selected in lines}
    mutate_cart_for_user(session, buyer_id, lambda _: {"version": 1, "items": items})


@pytest.fixture
def purchase(app, session):
    base = create_catalog_and_stock(session, stock=10)
    session.get(User, base.buyer_id).email_verified_at = datetime.now(timezone.utc)
    put_cart(session, base.buyer_id, (base.offer_id, 1, True))
    client = app.test_client()
    _login_as(client, base.buyer_id)
    return base, client


def financial_state(session):
    models = (Order, SellerOrder, OrderItem, PaymentAttempt, PaymentProof, InventoryReservation)
    counts = tuple(session.scalar(select(func.count()).select_from(model)) for model in models)
    return (*counts, session.scalar(select(func.sum(InventoryBalance.reserved_quantity))))


def cart_lines(session):
    return session.execute(select(
        CartItem.id, CartItem.cart_id, CartItem.seller_offer_id, CartItem.quantity, CartItem.selected
    ).order_by(CartItem.id)).all()


def simultaneous_posts(engine, clients, tokens):
    ready = Barrier(2, timeout=10)

    def before_lock(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            ready.wait()

    event.listen(engine, "before_cursor_execute", before_lock)
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(_submit, client, token) for client, token in zip(clients, tokens)]
            locations = [future.result(timeout=20).location for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", before_lock)
    return locations


@pytest.mark.parametrize("same_key", [False, True])
def test_two_browsers_one_selection(purchase, app, session, engine, same_key):
    base, first = purchase
    second = app.test_client()
    _login_as(second, base.buyer_id)
    tokens = [_checkout_token(browser) for browser in (first, second)]
    assert tokens[0] != tokens[1]
    if same_key:
        tokens[1] = tokens[0]
    locations = simultaneous_posts(engine, (first, second), tokens)
    assert financial_state(session) == (1, 1, 1, 1, 0, 1, 1)
    assert sum("/checkout/transferencia/" in loc for loc in locations) == (2 if same_key else 1)
    if same_key:
        assert locations[0] == locations[1]
    assert cart_lines(session) == []


@pytest.mark.parametrize("change", ["unverified", "price_up", "price_down"])
def test_changed_terms_require_review(purchase, session, change):
    base, client = purchase
    token = _checkout_token(client)
    if change == "unverified":
        session.get(Store, base.store_id).is_verified = False
    else:
        session.get(SellerOffer, base.offer_id).price = Decimal("12.00" if change == "price_up" else "8.00")
    session.commit()
    before = financial_state(session), cart_lines(session)
    response = _submit(client, token)
    assert (financial_state(session), cart_lines(session)) == before
    assert response.location.endswith("/checkout")
    review = client.get(response.location).get_data(as_text=True)
    if change == "unverified":
        assert "ya no está disponible" in review
    else:
        assert "precio" in review.lower() and "cambió" in review
        assert ("$12.00" if change == "price_up" else "$8.00") in review
        assert "/checkout/transferencia/" in _submit(client, _checkout_token(client)).location
        assert session.scalar(select(OrderItem.unit_price)) == (Decimal("12.00") if change == "price_up" else Decimal("8.00"))


@pytest.mark.parametrize("stock", [1, 10])
def test_two_buyers_share_stock(purchase, app, session, engine, stock, monkeypatch):
    from app.services import checkout as checkout_service

    ready_to_reserve = Barrier(2, timeout=10)
    reserve = checkout_service.reserve_inventory

    def reserve_together(**kwargs):
        # Both buyers hold their cart and shared catalog locks at this point.
        ready_to_reserve.wait()
        return reserve(**kwargs)

    monkeypatch.setattr(checkout_service, "reserve_inventory", reserve_together)
    base, first = purchase
    other = create_catalog_and_stock(session, stock=1)
    session.get(User, other.buyer_id).email_verified_at = datetime.now(timezone.utc)
    session.get(InventoryBalance, base.balance_id).on_hand_quantity = stock
    put_cart(session, other.buyer_id, (base.offer_id, 1, True))
    second = app.test_client()
    _login_as(second, other.buyer_id)
    clients = (first, second)
    locations = simultaneous_posts(engine, clients, [_checkout_token(c) for c in clients])
    successes = min(stock, 2)
    assert sum("/checkout/transferencia/" in loc for loc in locations) == successes
    assert financial_state(session) == (successes,) * 4 + (0, successes, successes)
    assert len(set(session.scalars(select(Order.buyer_id)))) == successes
    assert len(cart_lines(session)) == 2 - successes


def test_lost_response_then_repurchase(purchase, app, session):
    base, first = purchase
    second = app.test_client()
    _login_as(second, base.buyer_id)
    original = _checkout_token(first)
    old_tab = _checkout_token(second)
    assert original != old_tab
    # The second browser received neither the first response nor its Set-Cookie.
    transfer = _submit(first, original).location
    assert "/checkout/transferencia/" in transfer
    assert _submit(second, original).location == transfer
    extra, _ = _create_offer(session, base)
    put_cart(session, base.buyer_id, (base.offer_id, 1, True), (extra.id, 1, False))
    before = financial_state(session), cart_lines(session)
    assert _submit(second, original).location == transfer
    assert _submit(second, old_tab).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before
    new_transfer = _submit(second, _checkout_token(second)).location
    assert "/checkout/transferencia/" in new_transfer and new_transfer != transfer
    assert financial_state(session) == (2, 2, 2, 2, 0, 2, 2)
    remaining, = cart_lines(session)
    assert remaining.seller_offer_id == extra.id and not remaining.selected


def test_offsetting_price_changes(purchase, session):
    base, client = purchase
    second, _ = _create_offer(session, base, price=Decimal("12.00"))
    put_cart(session, base.buyer_id, (base.offer_id, 1, True), (second.id, 1, True))
    token = _checkout_token(client)
    session.get(SellerOffer, base.offer_id).price = Decimal("11.00")
    second.price = Decimal("11.00")
    session.commit()
    before = financial_state(session), cart_lines(session)
    assert _submit(client, token, total="22.00").location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before
    assert "/checkout/transferencia/" in _submit(client, _checkout_token(client)).location
    assert list(session.scalars(select(OrderItem.unit_price))) == [Decimal("11.00")] * 2


@pytest.mark.parametrize("change", [
    "quantity", "variant", "archived", "removed", "product", "inactive_variant",
    "category", "currency", "zero_price", "store", "mixed",
])
def test_stale_or_ineligible_lines_are_atomic(purchase, session, change):
    base, client = purchase
    if change == "mixed":
        second, _ = _create_offer(session, base, second_store=True)
        put_cart(session, base.buyer_id, (base.offer_id, 1, True), (second.id, 1, True))
    token = _checkout_token(client)
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    if change == "quantity":
        put_cart(session, base.buyer_id, (base.offer_id, 2, True))
    elif change == "variant":
        replacement = ProductVariant(product_id=variant.product_id, catalog_sku=uuid.uuid4().hex,
                                     attributes={}, is_active=True)
        session.add(replacement)
        session.flush()
        offer.variant_id = replacement.id
    elif change == "archived":
        offer.status = OfferStatus.ARCHIVED
    elif change == "removed":
        put_cart(session, base.buyer_id)
        session.execute(delete(InventoryBalance).where(InventoryBalance.offer_id == offer.id))
        session.delete(offer)
    elif change == "product":
        session.get(Product, variant.product_id).is_active = False
    elif change == "inactive_variant":
        variant.is_active = False
    elif change == "category":
        product = session.get(Product, variant.product_id)
        session.get(Category, product.category_id).is_active = False
    elif change == "currency":
        offer.currency = "EUR"
    elif change == "zero_price":
        offer.price = Decimal("0.00")
    elif change == "store":
        session.get(Store, base.store_id).status = StoreStatus.SUSPENDED
    else:
        session.get(Store, second.store_id).is_verified = False
    session.commit()
    before = financial_state(session), cart_lines(session)
    assert _submit(client, token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before


def test_unverified_before_preview(purchase, session):
    base, client = purchase
    session.get(Store, base.store_id).is_verified = False
    session.commit()
    before = financial_state(session), cart_lines(session)
    page = client.get("/checkout")
    assert page.status_code == 200
    assert "ya no está disponible" in page.get_data(as_text=True)
    assert _submit(client, _checkout_token(client)).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before


def test_foreign_token_and_invalid_signature(purchase, app, session):
    base, buyer = purchase
    token = _checkout_token(buyer)
    other = create_catalog_and_stock(session)
    session.get(User, other.buyer_id).email_verified_at = datetime.now(timezone.utc)
    put_cart(session, other.buyer_id, (base.offer_id, 1, True))
    stranger = app.test_client()
    _login_as(stranger, other.buyer_id)
    before = financial_state(session), cart_lines(session)
    assert _submit(stranger, token).location.endswith("/checkout")
    assert _submit(buyer, "x" + token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before
    assert "/checkout/transferencia/" in _submit(buyer, token).location
    after = financial_state(session), cart_lines(session)
    assert _submit(stranger, token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == after


def test_disabled_account_cannot_submit(purchase, session):
    base, client = purchase
    token = _checkout_token(client)
    user = session.get(User, base.buyer_id)
    user.is_active = False
    session.commit()
    before = financial_state(session), cart_lines(session)
    assert "/checkout/transferencia/" not in _submit(client, token).location
    assert (financial_state(session), cart_lines(session)) == before


def test_failure_after_consumption_rolls_back(purchase, session, monkeypatch):
    from app.services import checkout as checkout_service

    _, client = purchase
    token = _checkout_token(client)
    before = financial_state(session), cart_lines(session)

    consume = checkout_service.consume_checkout_items

    def fail_after_order(writer, cart_id, offers):
        consume(writer, cart_id, offers)
        assert financial_state(writer) == (1, 1, 1, 1, 0, 1, 1)
        assert cart_lines(writer) == []
        raise checkout_service.CheckoutServiceError("No se pudo confirmar.")

    monkeypatch.setattr(checkout_service, "consume_checkout_items", fail_after_order)
    assert _submit(client, token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before


def wait_for_lock_contention(observer, holder_pid, waiter):
    deadline = monotonic() + 10
    statement = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
            WHERE NOT granted AND :holder = ANY(pg_blocking_pids(pid))
        )
    """)
    while monotonic() < deadline:
        if observer.scalar(statement, {"holder": holder_pid}):
            return
        if waiter.done():
            waiter.result()
            pytest.fail("La operación terminó sin esperar el bloqueo.")
        sleep(0.01)
    pytest.fail("PostgreSQL no registró al checkout esperando la transacción escritora.")


@pytest.mark.parametrize("change", ["price", "verification", "cart"])
def test_concurrent_change_is_revalidated(purchase, session, session_factory, change):
    base, client = purchase
    extra, _ = _create_offer(session, base)
    session.commit()
    token = _checkout_token(client)
    initial = financial_state(session), cart_lines(session)
    with session_factory() as writer, ThreadPoolExecutor(1) as pool:
        holder = writer.scalar(select(func.pg_backend_pid()))
        if change == "cart":
            def add_line(state):
                # mutate_cart_for_user calls this after locking the persistent cart.
                nonlocal checkout
                checkout = pool.submit(_submit, client, token)
                wait_for_lock_contention(session, holder, checkout)
                state["items"][str(extra.id)] = {"quantity": 1, "selected": True}
                return state

            checkout = None
            mutate_cart_for_user(writer, base.buyer_id, add_line)
        else:
            if change == "price":
                writer.get(SellerOffer, base.offer_id).price = Decimal("14.00")
            else:
                writer.get(Store, base.store_id).is_verified = False
            writer.flush()
            checkout = pool.submit(_submit, client, token)
            try:
                wait_for_lock_contention(session, holder, checkout)
            finally:
                writer.commit()
        response = checkout.result(timeout=15)
    assert response.location.endswith("/checkout")
    assert financial_state(session) == initial[0]
    if change == "cart":
        assert set(row.seller_offer_id for row in cart_lines(session)) == {base.offer_id, extra.id}
        assert initial[1][0] in cart_lines(session)
    else:
        assert cart_lines(session) == initial[1]


def test_cart_change_and_undo_requires_new_confirmation(purchase, session):
    base, client = purchase
    token = _checkout_token(client)
    put_cart(session, base.buyer_id, (base.offer_id, 2, True))
    put_cart(session, base.buyer_id, (base.offer_id, 1, True))
    before = financial_state(session), cart_lines(session)
    assert _submit(client, token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before


def test_checkout_post_requires_csrf(purchase, session, app, monkeypatch):
    _, client = purchase
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    token = _checkout_token(client)
    before = financial_state(session), cart_lines(session)
    assert _submit(client, token).status_code == 400
    assert (financial_state(session), cart_lines(session)) == before


def test_historical_token_only_recovers_its_committed_order(purchase, session, app):
    base, buyer = purchase
    legacy_key = "A" * 43
    before = financial_state(session), cart_lines(session)
    assert _submit(buyer, legacy_key).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before
    transfer = _submit(buyer, _checkout_token(buyer)).location
    attempt = session.scalar(select(PaymentAttempt))
    attempt.idempotency_key = f"checkout:{legacy_key}"
    other = create_catalog_and_stock(session)
    session.get(User, other.buyer_id).email_verified_at = datetime.now(timezone.utc)
    put_cart(session, base.buyer_id, (base.offer_id, 1, True))
    stranger = app.test_client()
    _login_as(stranger, other.buyer_id)
    after = financial_state(session), cart_lines(session)
    assert _submit(buyer, legacy_key).location == transfer
    assert _submit(stranger, legacy_key).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == after


def test_reverted_price_does_not_revive_old_confirmation(purchase, session):
    base, client = purchase
    token = _checkout_token(client)
    offer = session.get(SellerOffer, base.offer_id)
    offer.price = Decimal("14.00")
    session.commit()
    assert _submit(client, token).location.endswith("/checkout")
    offer.price = Decimal("10.00")
    session.commit()
    before = financial_state(session), cart_lines(session)
    assert _submit(client, token).location.endswith("/checkout")
    assert (financial_state(session), cart_lines(session)) == before
    assert "/checkout/transferencia/" in _submit(client, _checkout_token(client)).location


def test_addition_waiting_for_checkout_survives_commit(purchase, session, session_factory, monkeypatch):
    from app.services import checkout as checkout_service

    base, client = purchase
    extra, _ = _create_offer(session, base)
    put_cart(session, base.buyer_id, (base.offer_id, 1, True), (extra.id, 1, False))
    token = _checkout_token(client)
    holders = Queue()
    finish_order = Event()
    consume = checkout_service.consume_checkout_items

    def consume_then_hold(writer, cart_id, offers):
        consume(writer, cart_id, offers)
        holders.put(writer.scalar(select(func.pg_backend_pid())))
        assert finish_order.wait(timeout=15)

    def readd_purchased_offer():
        with session_factory() as writer:
            def add_line(state):
                assert str(base.offer_id) not in state["items"]
                state["items"][str(base.offer_id)] = {"quantity": 2, "selected": True}
                return state

            mutate_cart_for_user(writer, base.buyer_id, add_line)

    monkeypatch.setattr(checkout_service, "consume_checkout_items", consume_then_hold)
    with ThreadPoolExecutor(2) as pool:
        checkout = pool.submit(_submit, client, token)
        holder = holders.get(timeout=10)
        mutation = pool.submit(readd_purchased_offer)
        try:
            wait_for_lock_contention(session, holder, mutation)
        finally:
            finish_order.set()
        transfer = checkout.result(timeout=10).location
        mutation.result(timeout=10)
    assert "/checkout/transferencia/" in transfer
    assert financial_state(session) == (1, 1, 1, 1, 0, 1, 1)
    assert {(row.seller_offer_id, row.quantity, row.selected) for row in cart_lines(session)} == {
        (base.offer_id, 2, True), (extra.id, 1, False),
    }
