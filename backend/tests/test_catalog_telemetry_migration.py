from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import CatalogInteractionEvent, User
from app.models.enums import UserStatus


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "da1b2c3d4e5f"
H4_HEAD = "6499defb2c52"
TABLE = "catalog_interaction_events"


def _migrations_dir(app) -> Path:
    return Path(app.root_path).parent / "migrations"


def _migrate(app, revision: str, *, down: bool = False) -> None:
    with app.app_context():
        db.session.remove()
        operation = downgrade if down else upgrade
        operation(revision=revision, directory=str(_migrations_dir(app)))


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _tables(engine) -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def _event(**overrides):
    values = {
        "event_type": "IMPRESSION",
        "surface": "HOME",
        "listing_key": "lst_migration_test",
        "ranking_request_id": uuid.uuid4(),
        "served_ranker": "v1",
        "served_position": 1,
    }
    values.update(overrides)
    return CatalogInteractionEvent(**values)


def test_h4_upgrade_is_schema_only_and_empty_downgrade_is_allowed(app, engine, session):
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert _version(engine) == PREVIOUS_HEAD
        assert TABLE not in _tables(engine)
        _migrate(app, H4_HEAD)
        assert _version(engine) == H4_HEAD
        assert TABLE in _tables(engine)
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one() == 0
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert TABLE not in _tables(engine)
    finally:
        if _version(engine) != H4_HEAD:
            _migrate(app, "head")


def test_h4_constraints_and_partial_impression_uniqueness(session):
    request_id = uuid.uuid4()
    session.add(_event(ranking_request_id=request_id))
    session.flush()
    session.add(_event(ranking_request_id=request_id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    for invalid in (
        _event(event_type="ARBITRARY"),
        _event(surface="ARBITRARY"),
        _event(listing_key=" "),
        _event(served_position=0),
        _event(shadow_position=-1),
    ):
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_h4_history_foreign_key_sets_actor_to_null(session):
    user = User(
        public_code=f"H4-{uuid.uuid4().hex[:12]}",
        email=f"h4-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="test",
        full_name="History actor",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    event = _event(actor_user_id=user.id)
    session.add(event)
    session.commit()

    session.delete(user)
    session.commit()
    session.refresh(event)
    assert event.actor_user_id is None


def test_h4_downgrade_fails_closed_when_events_exist(app, engine, session):
    session.add(_event())
    session.commit()
    session.close()

    with pytest.raises(SystemExit) as blocked:
        _migrate(app, PREVIOUS_HEAD, down=True)
    assert blocked.value.code == 1
    assert _version(engine) == H4_HEAD
    assert TABLE in _tables(engine)


def test_fresh_database_upgrades_from_base_to_h4_head(app, engine, session):
    session.close()
    try:
        _migrate(app, "base", down=True)
        assert TABLE not in _tables(engine)
        _migrate(app, "head")
        assert _version(engine) == H4_HEAD
        assert TABLE in _tables(engine)
    finally:
        if _version(engine) != H4_HEAD:
            _migrate(app, "head")
