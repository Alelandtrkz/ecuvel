from __future__ import annotations

from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import SellerOffer
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "c9d0e1f2a3b4"
H3_HEAD = "da1b2c3d4e5f"
CURRENT_HEAD = "8d4e5f6a7b9c"
COLUMN = "preparation_time_days"


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


def _columns(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            column["name"]
            for column in inspect(connection).get_columns("seller_offers")
        }


def test_h3_upgrade_preserves_legacy_offer_with_null_preparation(
    app, engine, session
):
    base = create_catalog_and_stock(session)
    offer = session.get(SellerOffer, base.offer_id)
    identity = (offer.id, offer.store_id, offer.variant_id, offer.seller_sku)
    session.commit()
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert _version(engine) == PREVIOUS_HEAD
        assert COLUMN not in _columns(engine)
        with engine.connect() as connection:
            legacy = connection.execute(
                text(
                    "SELECT id, store_id, variant_id, seller_sku "
                    "FROM seller_offers WHERE id = :offer_id"
                ),
                {"offer_id": identity[0]},
            ).one()
        assert tuple(legacy) == identity

        _migrate(app, H3_HEAD)
        assert _version(engine) == H3_HEAD
        assert COLUMN in _columns(engine)
        with engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT preparation_time_days FROM seller_offers "
                    "WHERE id = :offer_id"
                ),
                {"offer_id": identity[0]},
            ).scalar_one()
        assert value is None
    finally:
        if _version(engine) != CURRENT_HEAD:
            _migrate(app, "head")


def test_h3_constraint_allows_null_one_two_and_rejects_zero_three(session):
    base = create_catalog_and_stock(session)
    offer = session.get(SellerOffer, base.offer_id)
    for value in (None, 1, 2):
        offer.preparation_time_days = value
        session.flush()
    session.commit()

    for value in (0, 3):
        offer = session.get(SellerOffer, base.offer_id)
        offer.preparation_time_days = value
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_h3_downgrade_works_when_all_values_are_null(app, engine, session):
    create_catalog_and_stock(session)
    session.commit()
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert _version(engine) == PREVIOUS_HEAD
        assert COLUMN not in _columns(engine)
    finally:
        _migrate(app, "head")


def test_h3_downgrade_fails_closed_for_populated_offer(
    app, engine, session
):
    base = create_catalog_and_stock(session)
    offer = session.get(SellerOffer, base.offer_id)
    offer.preparation_time_days = 2
    session.commit()
    session.close()

    with pytest.raises(SystemExit) as blocked:
        _migrate(app, PREVIOUS_HEAD, down=True)
    assert blocked.value.code == 1
    assert _version(engine) == CURRENT_HEAD
    assert COLUMN in _columns(engine)


def test_fresh_database_upgrades_from_base_to_h3_head(
    app, engine, session
):
    session.close()
    try:
        _migrate(app, "base", down=True)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        assert "seller_offers" not in tables
        _migrate(app, "head")
        assert _version(engine) == CURRENT_HEAD
        assert COLUMN in _columns(engine)
    finally:
        if _version(engine) != CURRENT_HEAD:
            _migrate(app, "head")
