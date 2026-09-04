from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Cart, CartAdoption, CartItem, SellerOffer, User
from app.models.enums import OfferStatus, UserStatus
from app.services.cart import add_cart_item
from app.services.cart_storage import (
    _merge_cart_states,
    adopt_guest_cart_for_user,
    load_cart_state_for_user,
    mutate_cart_for_user,
)
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _state(offer_id, quantity=1, selected=True):
    return {
        "version": 1,
        "items": {
            str(offer_id): {
                "quantity": quantity,
                "selected": selected,
            }
        },
    }


def _second_user(session):
    token = uuid.uuid4().hex[:12]
    user = User(
        public_code=f"CART-{token}",
        email=f"cart-{token}@test.local",
        password_hash="test",
        full_name="Second cart user",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def test_merge_uses_sum_or_caps_and_preserves_unrelated_lines():
    collision = uuid.uuid4()
    existing_only = uuid.uuid4()
    guest_only = uuid.uuid4()
    merged = _merge_cart_states(
        {
            "version": 1,
            "items": {
                str(collision): {"quantity": 90, "selected": False},
                str(existing_only): {"quantity": 4, "selected": False},
            },
        },
        {
            "version": 1,
            "items": {
                str(collision): {"quantity": 20, "selected": True},
                str(guest_only): {"quantity": 2, "selected": False},
            },
        },
    )

    assert merged["items"][str(collision)] == {
        "quantity": 99,
        "selected": True,
    }
    assert merged["items"][str(existing_only)] == {
        "quantity": 4,
        "selected": False,
    }
    assert merged["items"][str(guest_only)] == {
        "quantity": 2,
        "selected": False,
    }


def test_same_token_replay_is_global_noop(session):
    base = create_catalog_and_stock(session, stock=20)
    other = _second_user(session)
    session.commit()
    merge_token = "a" * 64

    assert adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id, 2),
        merge_token=merge_token,
    )
    assert not adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id, 2),
        merge_token=merge_token,
    )
    assert not adopt_guest_cart_for_user(
        session,
        user_id=other.id,
        guest_state=_state(base.offer_id, 2),
        merge_token=merge_token,
    )

    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"] == 2
    assert load_cart_state_for_user(session, other.id)["items"] == {}
    assert session.scalar(select(func.count(CartAdoption.id))) == 1


def test_adoption_reconciles_merged_quantity_to_stock_and_preserves_selection(
    session,
):
    base = create_catalog_and_stock(session, stock=2)
    session.commit()
    mutate_cart_for_user(
        session,
        base.buyer_id,
        lambda _current: _state(base.offer_id, 1, selected=False),
    )
    merge_token = "e" * 64

    assert adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id, 2, selected=True),
        merge_token=merge_token,
    )
    assert not adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id, 2, selected=True),
        merge_token=merge_token,
    )

    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ] == {"quantity": 2, "selected": True}
    assert session.scalar(select(func.count(CartAdoption.id))) == 1


def test_concurrent_same_token_applies_sum_once(
    session,
    session_factory,
    concurrent_runner,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()
    merge_token = "b" * 64

    def worker(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            return adopt_guest_cart_for_user(
                worker_session,
                user_id=base.buyer_id,
                guest_state=_state(base.offer_id, 2),
                merge_token=merge_token,
            )
        finally:
            worker_session.close()

    results, errors = concurrent_runner([worker, worker])

    assert errors == []
    assert sorted(results) == [False, True]
    session.expire_all()
    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"] == 2
    assert session.scalar(select(func.count(CartAdoption.id))) == 1


def test_receipt_and_merge_rollback_together(session, monkeypatch):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()

    def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("forced persistence failure")

    monkeypatch.setattr(
        "app.services.cart_storage._persist_cart_state",
        fail_persistence,
    )
    with pytest.raises(RuntimeError, match="forced persistence failure"):
        adopt_guest_cart_for_user(
            session,
            user_id=base.buyer_id,
            guest_state=_state(base.offer_id, 2),
            merge_token="c" * 64,
        )

    assert session.scalar(select(func.count(CartAdoption.id))) == 0
    assert session.scalar(select(func.count(Cart.id))) == 0
    assert session.scalar(select(func.count(CartItem.id))) == 0


def test_failed_authenticated_adoption_keeps_guest_session(
    client,
    session,
    monkeypatch,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
        browser_session["cart"] = {
            "version": 2,
            "merge_token": "9" * 64,
            "items": _state(base.offer_id, 2)["items"],
        }

    def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("forced persistence failure")

    monkeypatch.setattr(
        "app.services.cart_storage._persist_cart_state",
        fail_persistence,
    )
    with pytest.raises(RuntimeError, match="forced persistence failure"):
        client.get("/carrito")

    with client.session_transaction() as browser_session:
        assert browser_session["cart"]["merge_token"] == "9" * 64
        assert str(base.offer_id) in browser_session["cart"]["items"]
    assert session.scalar(select(func.count(CartAdoption.id))) == 0
    assert session.scalar(select(func.count(CartItem.id))) == 0


def test_receipt_survives_claimed_user_deletion_and_stays_consumed(session):
    base = create_catalog_and_stock(session, stock=20)
    other = _second_user(session)
    session.commit()
    merge_token = "d" * 64
    assert adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id),
        merge_token=merge_token,
    )

    claimed_user = session.get(User, base.buyer_id)
    session.delete(claimed_user)
    session.commit()
    receipt = session.scalar(
        select(CartAdoption).where(CartAdoption.merge_token == merge_token)
    )
    assert receipt is not None
    assert receipt.claimed_user_id is None
    assert not adopt_guest_cart_for_user(
        session,
        user_id=other.id,
        guest_state=_state(base.offer_id),
        merge_token=merge_token,
    )


def test_two_concurrent_first_adds_create_one_cart_and_one_item(
    session,
    session_factory,
    concurrent_runner,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()

    def worker(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            state = mutate_cart_for_user(
                worker_session,
                base.buyer_id,
                lambda current: add_cart_item(current, base.offer_id, 1),
            )
            return state["items"][str(base.offer_id)]["quantity"]
        finally:
            worker_session.close()

    results, errors = concurrent_runner([worker, worker])

    assert errors == []
    assert sorted(results) == [1, 2]
    session.expire_all()
    assert session.scalar(select(func.count(Cart.id))) == 1
    assert session.scalar(select(func.count(CartItem.id))) == 1
    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"] == 2


def test_guest_v2_token_is_stable_and_logout_starts_new_lifecycle(
    client,
    session,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()

    client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "1", "next": "/"},
    )
    with client.session_transaction() as browser_session:
        first_token = browser_session["cart"]["merge_token"]
        assert browser_session["cart"]["version"] == 2

    client.post(
        "/carrito/seleccion",
        data={"selected": "0"},
    )
    client.post(
        f"/carrito/items/{base.offer_id}/cantidad",
        data={"quantity": "2"},
    )
    client.post(f"/carrito/items/{base.offer_id}/eliminar")
    client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "1", "next": "/"},
    )
    with client.session_transaction() as browser_session:
        assert browser_session["cart"]["merge_token"] == first_token
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True

    client.get("/carrito")
    with client.session_transaction() as browser_session:
        assert "cart" not in browser_session
    assert session.scalar(
        select(CartAdoption).where(CartAdoption.merge_token == first_token)
    ) is not None

    client.post("/cerrar-sesion")
    client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "1", "next": "/"},
    )
    with client.session_transaction() as browser_session:
        assert browser_session["cart"]["merge_token"] != first_token


def test_legacy_authenticated_cookie_replay_does_not_repeat_sum(
    client,
    app,
    session,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
        browser_session["cart"] = _state(base.offer_id, 2)
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    legacy_cookie = client.get_cookie(cookie_name).value

    assert client.get("/carrito").status_code == 200
    first_quantity = load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"]

    client.set_cookie(cookie_name, legacy_cookie)
    assert client.get("/carrito").status_code == 200
    second_quantity = load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"]

    assert first_quantity == second_quantity == 2
    assert session.scalar(select(func.count(CartAdoption.id))) == 1


def test_authenticated_cart_is_user_owned_cross_client_and_logout_safe(
    app,
    session,
):
    base = create_catalog_and_stock(session, stock=20)
    other = _second_user(session)
    session.commit()
    first_client = app.test_client()
    second_client = app.test_client()

    with first_client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
    response = first_client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "2", "next": "/"},
    )
    assert response.status_code == 302
    with first_client.session_transaction() as browser_session:
        assert "cart" not in browser_session

    with second_client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
    second_body = second_client.get("/carrito").get_data(as_text=True)
    assert "Product Test" in second_body
    assert 'class="header-cart-badge">2<' in second_body

    assert first_client.post("/cerrar-sesion").status_code == 302
    with first_client.session_transaction() as browser_session:
        assert "cart" not in browser_session
    assert "Tu carrito está vacío" in first_client.get(
        "/carrito"
    ).get_data(as_text=True)

    first_client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "3", "next": "/"},
    )
    with first_client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(other.id)
        browser_session["_fresh"] = True
    assert first_client.get("/carrito").status_code == 200

    session.expire_all()
    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ]["quantity"] == 2
    assert load_cart_state_for_user(session, other.id)["items"][
        str(base.offer_id)
    ]["quantity"] == 3
    with first_client.session_transaction() as browser_session:
        assert "cart" not in browser_session


def test_authenticated_update_selection_and_remove_persist_only_in_db(
    client,
    session,
):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True

    client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "2", "next": "/"},
    )
    client.post(
        f"/carrito/items/{base.offer_id}/cantidad",
        data={"quantity": "3"},
    )
    client.post(f"/carrito/items/{base.offer_id}/seleccion", data={})
    state = load_cart_state_for_user(session, base.buyer_id)
    assert state["items"][str(base.offer_id)] == {
        "quantity": 3,
        "selected": False,
    }
    with client.session_transaction() as browser_session:
        assert "cart" not in browser_session

    client.post(f"/carrito/items/{base.offer_id}/eliminar")
    assert load_cart_state_for_user(session, base.buyer_id)["items"] == {}


def test_authenticated_cart_get_persists_current_stock_normalization(
    client,
    session,
):
    base = create_catalog_and_stock(session, stock=3)
    session.commit()
    mutate_cart_for_user(
        session,
        base.buyer_id,
        lambda _current: _state(base.offer_id, 8, selected=True),
    )
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True

    assert client.get("/carrito").status_code == 200

    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ] == {"quantity": 3, "selected": True}

def test_merge_overflow_keeps_user_lines_then_guest_stable_order():
    existing_ids = [uuid.uuid4() for _ in range(49)]
    guest_ids = [uuid.uuid4(), uuid.uuid4()]
    user_state = {
        "version": 1,
        "items": {
            str(offer_id): {"quantity": 1, "selected": False}
            for offer_id in existing_ids
        },
    }
    guest_state = {
        "version": 1,
        "items": {
            str(offer_id): {"quantity": 1, "selected": True}
            for offer_id in guest_ids
        },
    }

    merged = _merge_cart_states(user_state, guest_state)

    assert list(merged["items"]) == [
        *(str(offer_id) for offer_id in existing_ids),
        str(guest_ids[0]),
    ]
    assert str(guest_ids[1]) not in merged["items"]


def test_explicit_login_adopts_guest_cart_and_preserves_receipt(
    client,
    session,
):
    base = create_catalog_and_stock(session, stock=20)
    user = session.get(User, base.buyer_id)
    user.email_normalized = user.email.casefold()
    user.password_hash = generate_password_hash("correct horse battery staple")
    user.email_verified_at = user.created_at
    session.commit()

    client.post(
        "/carrito/agregar",
        data={"offer_id": str(base.offer_id), "quantity": "2", "next": "/"},
    )
    with client.session_transaction() as browser_session:
        merge_token = browser_session["cart"]["merge_token"]
    response = client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": "correct horse battery staple",
            "next": "/carrito",
        },
    )

    assert response.status_code == 302
    with client.session_transaction() as browser_session:
        assert "cart" not in browser_session
    assert load_cart_state_for_user(session, user.id)["items"][
        str(base.offer_id)
    ]["quantity"] == 2
    assert session.scalar(
        select(CartAdoption).where(CartAdoption.merge_token == merge_token)
    ) is not None


def test_remember_restore_adopts_pending_guest_cart(app, session):
    base = create_catalog_and_stock(session, stock=20)
    user = session.get(User, base.buyer_id)
    user.email_normalized = user.email.casefold()
    user.password_hash = generate_password_hash("correct horse battery staple")
    user.email_verified_at = user.created_at
    session.commit()
    login_client = app.test_client()
    login_response = login_client.post(
        "/iniciar-sesion",
        data={
            "email": user.email,
            "password": "correct horse battery staple",
            "remember": "1",
        },
    )
    assert login_response.status_code == 302
    remember_name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    remember_cookie = login_client.get_cookie(remember_name)
    assert remember_cookie is not None

    restored_client = app.test_client()
    restored_client.set_cookie(remember_name, remember_cookie.value)
    with restored_client.session_transaction() as browser_session:
        browser_session["cart"] = _state(base.offer_id, 3)

    assert restored_client.get("/carrito").status_code == 200
    with restored_client.session_transaction() as browser_session:
        assert "cart" not in browser_session
    assert load_cart_state_for_user(session, user.id)["items"][
        str(base.offer_id)
    ]["quantity"] == 3


def test_adoption_discards_missing_offer_but_keeps_receipt(session):
    base = create_catalog_and_stock(session, stock=20)
    session.commit()
    token = "e" * 64

    assert adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(uuid.uuid4(), 2),
        merge_token=token,
    )

    assert load_cart_state_for_user(session, base.buyer_id)["items"] == {}
    assert session.scalar(
        select(CartAdoption).where(CartAdoption.merge_token == token)
    ) is not None


def test_adoption_reuses_current_unavailable_offer_semantics(session):
    base = create_catalog_and_stock(session, stock=20)
    offer = session.get(SellerOffer, base.offer_id)
    offer.status = OfferStatus.PAUSED
    session.commit()

    assert adopt_guest_cart_for_user(
        session,
        user_id=base.buyer_id,
        guest_state=_state(base.offer_id, 4, selected=True),
        merge_token="f" * 64,
    )

    assert load_cart_state_for_user(session, base.buyer_id)["items"][
        str(base.offer_id)
    ] == {"quantity": 4, "selected": False}
