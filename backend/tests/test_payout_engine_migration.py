from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app.extensions import db


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "a7b8c9d0e1f2"
L2_HEAD = "b8c9d0e1f2a3"
CURRENT_HEAD = "7c1a9e4d2b6f"


def _migrations_dir(app) -> Path:
    return Path(app.root_path).parent / "migrations"


def _upgrade(app, revision: str = "head") -> None:
    with app.app_context():
        db.session.remove()
        upgrade(revision=revision, directory=str(_migrations_dir(app)))


def _downgrade(app) -> None:
    with app.app_context():
        db.session.remove()
        downgrade(revision=PREVIOUS_HEAD, directory=str(_migrations_dir(app)))


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns(table)}


def test_l2_clean_upgrade_partial_unique_triggers_and_schema_downgrade(app, engine):
    try:
        _downgrade(app)
        assert _version(engine) == PREVIOUS_HEAD
        assert "cancelled_at" not in _columns(engine, "seller_payouts")
        assert "released_at" not in _columns(engine, "seller_payout_items")
        _upgrade(app, L2_HEAD)
        assert _version(engine) == L2_HEAD
        assert "cancelled_at" in _columns(engine, "seller_payouts")
        assert "released_at" in _columns(engine, "seller_payout_items")
        with engine.connect() as connection:
            partial = connection.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE "
                "indexname = 'uq_seller_payout_items_active_seller_order'"
            )).scalar_one()
            assert "UNIQUE" in partial and "released_at IS NULL" in partial
            trigger_count = connection.execute(text(
                "SELECT count(*) FROM information_schema.triggers WHERE "
                "event_object_table IN ('seller_payouts', 'seller_payout_items')"
            )).scalar_one()
            assert trigger_count >= 3
        _downgrade(app)
        assert "cancelled_at" not in _columns(engine, "seller_payouts")
        assert "released_at" not in _columns(engine, "seller_payout_items")
        _upgrade(app, L2_HEAD)
    finally:
        _upgrade(app)


def test_l2_upgrade_fails_closed_for_cancelled_history(app, engine, session):
    session.close()
    try:
        _downgrade(app)
        with engine.begin() as connection:
            store_id = connection.execute(text(
                "INSERT INTO stores (id, public_code, product_code_prefix, name, slug, status, is_verified) "
                "VALUES (gen_random_uuid(), 'STR-L2-MIG', 'L2M', 'Migration Store', "
                "'migration-store', 'ACTIVE', true) RETURNING id"
            )).scalar_one()
            connection.execute(text(
                "INSERT INTO seller_payouts "
                "(id, store_id, status, currency, gross_sales_total, discount_total, "
                "commission_total, net_total, scheduled_for) VALUES "
                "(gen_random_uuid(), :store_id, 'CANCELLED', 'USD', 0, 0, 0, 0, :at)"
            ), {"store_id": store_id, "at": datetime.now(timezone.utc)})
        with pytest.raises(SystemExit) as blocked:
            _upgrade(app, L2_HEAD)
        assert blocked.value.code == 1
        assert _version(engine) == PREVIOUS_HEAD
        assert "cancelled_at" not in _columns(engine, "seller_payouts")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM seller_payouts WHERE store_id = :store_id"),
                {"store_id": store_id},
            )
            connection.execute(
                text("DELETE FROM stores WHERE id = :store_id"),
                {"store_id": store_id},
            )
        _upgrade(app, L2_HEAD)
    finally:
        if _version(engine) == PREVIOUS_HEAD:
            with engine.begin() as connection:
                connection.execute(text(
                    "DELETE FROM seller_payouts WHERE store_id IN "
                    "(SELECT id FROM stores WHERE public_code = 'STR-L2-MIG')"
                ))
                connection.execute(text(
                    "DELETE FROM stores WHERE public_code = 'STR-L2-MIG'"
                ))
        _upgrade(app)


def test_l2_downgrade_blocks_duplicate_cancel_repayout_history(
    app, engine, session
):
    from tests.test_seller_payouts import (
        EXECUTED_AT, _delivered_order, _schedule,
    )
    from app.services.seller_payouts import cancel_seller_payout

    base, _order, _seller_order = _delivered_order(session)
    first = _schedule(session, base)
    cancel_seller_payout(
        session, payout_number=first.payout_number, cancelled_at=EXECUTED_AT
    )
    _schedule(session, base)
    session.commit()
    session.close()
    with pytest.raises(SystemExit) as blocked:
        _downgrade(app)
    assert blocked.value.code == 1
    # PostgreSQL rolls the multi-revision downgrade back as one transaction,
    # so a fail-closed L2 downgrade preserves the repository's current head.
    assert _version(engine) == CURRENT_HEAD
    assert "released_at" in _columns(engine, "seller_payout_items")
