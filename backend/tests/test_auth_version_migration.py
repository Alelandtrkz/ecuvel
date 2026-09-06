from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app.extensions import db


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "7c1a9e4d2b6f"
LR11_HEAD = "8d4e5f6a7b9c"


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


def _schema(engine) -> tuple[set[str], dict[str, set[str]]]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        columns = {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in tables
        }
    return tables, columns


def test_auth_version_upgrade_backfills_and_downgrade_is_clean(
    app,
    engine,
    session,
):
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        tables_before, columns_before = _schema(engine)
        assert "auth_version" not in columns_before["users"]
        legacy_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, public_code, email, email_normalized, password_hash, "
                    "full_name, status, is_active, is_ecuvel_staff) VALUES "
                    "(:id, :code, :email, :email, :password_hash, :full_name, "
                    "'ACTIVE', true, false)"
                ),
                {
                    "id": legacy_id,
                    "code": f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
                    "email": f"migration-{uuid.uuid4().hex}@test.local",
                    "password_hash": "not-a-real-hash",
                    "full_name": "Migration User",
                },
            )

        _migrate(app, LR11_HEAD)
        assert _version(engine) == LR11_HEAD
        tables_after, columns_after = _schema(engine)
        assert tables_after == tables_before
        assert columns_after["users"] - columns_before["users"] == {
            "auth_version"
        }
        assert all(
            columns_after[table] == columns_before[table]
            for table in tables_before - {"users"}
        )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT auth_version FROM users WHERE id = :id"),
                {"id": legacy_id},
            ).scalar_one() == 1
            auth_column = next(
                column
                for column in inspect(connection).get_columns("users")
                if column["name"] == "auth_version"
            )
        assert auth_column["nullable"] is False
        assert str(auth_column["default"]).strip("()'") == "1"

        _migrate(app, PREVIOUS_HEAD, down=True)
        assert _version(engine) == PREVIOUS_HEAD
        tables_downgraded, columns_downgraded = _schema(engine)
        assert tables_downgraded == tables_before
        assert columns_downgraded == columns_before
    finally:
        if _version(engine) != LR11_HEAD:
            _migrate(app, "head")
