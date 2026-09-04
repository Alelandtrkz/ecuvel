from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Cart, CartAdoption, CartItem
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "6499defb2c52"
C1_HEAD = "7c1a9e4d2b6f"
TABLES = {"carts", "cart_items", "cart_adoptions"}


def _migrations_dir(app) -> Path:
    return Path(app.root_path).parent / "migrations"


def _migrate(app, revision: str, *, down: bool = False) -> None:
    with app.app_context():
        db.session.remove()
        operation = downgrade if down else upgrade
        operation(revision=revision, directory=str(_migrations_dir(app)))


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _tables(engine) -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def test_c1_upgrade_is_schema_only_and_empty_downgrade_is_allowed(
    app,
    engine,
    session,
):
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert TABLES.isdisjoint(_tables(engine))
        _migrate(app, C1_HEAD)
        assert _version(engine) == C1_HEAD
        assert TABLES <= _tables(engine)
        with engine.connect() as connection:
            for table_name in TABLES:
                assert connection.execute(
                    text(f"SELECT count(*) FROM {table_name}")
                ).scalar_one() == 0
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert TABLES.isdisjoint(_tables(engine))
    finally:
        if _version(engine) != C1_HEAD:
            _migrate(app, "head")


def test_c1_constraints_enforce_cart_and_receipt_identity(session):
    base = create_catalog_and_stock(session)
    session.commit()
    first_cart = Cart(user_id=base.buyer_id)
    session.add(first_cart)
    session.commit()

    session.add(Cart(user_id=base.buyer_id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(
        CartItem(
            cart_id=first_cart.id,
            seller_offer_id=base.offer_id,
            quantity=1,
            selected=True,
        )
    )
    session.commit()
    session.add(
        CartItem(
            cart_id=first_cart.id,
            seller_offer_id=base.offer_id,
            quantity=2,
            selected=False,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    invalid = CartItem(
        cart_id=first_cart.id,
        seller_offer_id=base.offer_id,
        quantity=100,
        selected=True,
    )
    session.add(invalid)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    merge_token = uuid.uuid4().hex + uuid.uuid4().hex
    session.add(
        CartAdoption(
            merge_token=merge_token,
            claimed_user_id=base.buyer_id,
        )
    )
    session.commit()
    session.add(
        CartAdoption(
            merge_token=merge_token,
            claimed_user_id=None,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_c1_downgrade_fails_closed_when_cart_data_exists(
    app,
    engine,
    session,
):
    base = create_catalog_and_stock(session)
    cart = Cart(user_id=base.buyer_id)
    session.add(cart)
    session.flush()
    session.add(
        CartItem(
            cart_id=cart.id,
            seller_offer_id=base.offer_id,
            quantity=1,
            selected=True,
        )
    )
    session.commit()
    session.close()

    with pytest.raises(SystemExit) as blocked:
        _migrate(app, PREVIOUS_HEAD, down=True)
    assert blocked.value.code == 1
    assert _version(engine) == C1_HEAD
    assert TABLES <= _tables(engine)
