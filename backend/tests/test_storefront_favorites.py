from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Favorite, InventoryBalance, Product, ProductVariant, SellerOffer, User
from app.models.enums import UserStatus
from app.services.favorites import (
    add_favorite_by_slug,
    favorite_count_for_user,
    favorite_product_ids_for_user,
    remove_favorite_by_slug,
)
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _user(
    session,
    *,
    email: str = "cliente@test.local",
    name: str = "Cliente Ecuvel",
) -> User:
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash("correct horse battery staple"),
        full_name=name,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, email: str = "cliente@test.local"):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": email,
            "password": "correct horse battery staple",
            "next": "/",
        },
        follow_redirects=False,
    )


def _product(session, base) -> Product:
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    product = session.scalar(
        select(Product)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id == offer.variant_id)
    )
    assert product is not None
    return product


def test_favorite_unique_for_user_but_shared_between_users(session):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    user_a = session.get(User, base.buyer_id)
    user_b = session.get(User, base.operator_id)
    assert user_a and user_b

    session.add_all(
        [
            Favorite(user_id=user_a.id, product_id=product.id),
            Favorite(user_id=user_b.id, product_id=product.id),
        ]
    )
    session.flush()

    session.add(Favorite(user_id=user_a.id, product_id=product.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_service_add_remove_idempotent_without_inventory_changes(session):
    base = create_catalog_and_stock(session, stock=7)
    product = _product(session, base)
    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    before = (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    )

    result = add_favorite_by_slug(
        session,
        user_id=base.buyer_id,
        product_slug=product.slug,
    )
    replay = add_favorite_by_slug(
        session,
        user_id=base.buyer_id,
        product_slug=product.slug,
    )
    assert result.is_favorite is True
    assert replay.replayed is True
    assert favorite_count_for_user(session, base.buyer_id) == 1
    assert favorite_product_ids_for_user(
        session,
        base.buyer_id,
        {product.id},
    ) == {product.id}

    removed = remove_favorite_by_slug(
        session,
        user_id=base.buyer_id,
        product_slug=product.slug,
    )
    replay_remove = remove_favorite_by_slug(
        session,
        user_id=base.buyer_id,
        product_slug=product.slug,
    )
    assert removed.is_favorite is False
    assert replay_remove.replayed is True
    assert favorite_count_for_user(session, base.buyer_id) == 0

    session.refresh(balance)
    assert (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    ) == before


def test_favorites_require_login_for_page_and_mutation(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    session.commit()

    assert client.get("/favoritos").status_code == 302
    response = client.post(
        f"/favoritos/productos/{product.slug}/agregar",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "login_required"


def test_routes_add_remove_json_and_render_page(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    _user(session)
    session.commit()
    assert _login(client).status_code == 302

    response = client.post(
        f"/favoritos/productos/{product.slug}/agregar",
        data={"next": "/favoritos"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["is_favorite"] is True
    assert payload["favorite_count"] == 1

    page = client.get("/favoritos")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert product.title in html
    assert "Mis favoritos" in html
    assert "preparación" not in html

    response = client.post(
        f"/favoritos/productos/{product.slug}/eliminar",
        data={"next": "/favoritos"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["is_favorite"] is False
    assert payload["favorite_count"] == 0


def test_user_isolation_in_favorites_page(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    user_a = _user(session, email="a@test.local", name="Nombre Largo " * 8)
    _user(session, email="b@test.local")
    session.add(Favorite(user_id=user_a.id, product_id=product.id))
    session.commit()

    assert _login(client, "b@test.local").status_code == 302
    page = client.get("/favoritos")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert product.title not in html
    assert "Aún no tienes favoritos" in html
    assert "Favoritos" in html


def test_home_and_detail_mark_existing_favorite_without_mojibake(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    user = _user(session, name="NombreExtremadamenteLargoSinEspacios" * 3)
    session.add(Favorite(user_id=user.id, product_id=product.id))
    session.commit()
    assert _login(client).status_code == 302

    home = client.get("/")
    detail = client.get(f"/productos/{product.slug}")

    assert home.status_code == 200
    assert detail.status_code == 200
    combined = home.get_data(as_text=True) + detail.get_data(as_text=True)
    assert 'aria-pressed="true"' in combined
    assert "Información" in combined
    assert "contraseña" not in combined
    assert "Ã" not in combined
    assert "Â" not in combined
