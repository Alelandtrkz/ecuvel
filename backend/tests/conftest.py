from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app import create_app
from app.extensions import db


def _database_name() -> str:
    database_url = os.environ.get("DATABASE_URL", "")

    if not database_url:
        pytest.exit("DATABASE_URL no está configurada.", returncode=2)

    database_name = make_url(database_url).database or ""

    if not database_name.endswith("_test"):
        pytest.exit(
            "La suite se negó a ejecutarse porque la base "
            "de datos no es una base de pruebas.",
            returncode=2,
        )

    return database_name


def pytest_sessionstart(session: pytest.Session) -> None:
    _database_name()


@pytest.fixture(scope="session")
def app():
    expected_database = _database_name()
    application = create_app()
    application.config["TESTING"] = True

    with application.app_context():
        actual_database = db.session.execute(
            text("SELECT current_database()")
        ).scalar_one()

        if actual_database != expected_database:
            pytest.exit(
                "La base PostgreSQL real no coincide con "
                "la base de pruebas configurada.",
                returncode=2,
            )

        migrations_dir = Path(application.root_path).parent / "migrations"
        upgrade(directory=str(migrations_dir))
        yield application
        db.session.remove()


@pytest.fixture(scope="session")
def engine(app) -> Engine:
    with app.app_context():
        yield db.engine


@pytest.fixture(scope="session")
def session_factory(engine: Engine):
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )


def _truncate_application_tables(engine: Engine) -> None:
    if not (engine.url.database or "").endswith("_test"):
        pytest.exit(
            "Se bloqueó una limpieza fuera de la base de pruebas.",
            returncode=2,
        )

    with engine.begin() as connection:
        actual_database = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

        if not actual_database.endswith("_test"):
            pytest.exit(
                "Se bloqueó una limpieza fuera de la base de pruebas.",
                returncode=2,
            )

        table_names = [
            table_name
            for table_name in inspect(connection).get_table_names()
            if table_name != "alembic_version"
        ]

        if table_names:
            quote = connection.dialect.identifier_preparer.quote
            tables = ", ".join(
                quote(table_name)
                for table_name in table_names
            )
            connection.execute(
                text(
                    f"TRUNCATE TABLE {tables} "
                    "RESTART IDENTITY CASCADE"
                )
            )


@pytest.fixture(autouse=True)
def clean_database(engine: Engine):
    _truncate_application_tables(engine)
    yield
    _truncate_application_tables(engine)


@pytest.fixture
def session(session_factory) -> Session:
    database_session = session_factory()

    try:
        yield database_session
    finally:
        database_session.rollback()
        database_session.close()


def run_concurrently(
    workers: Sequence[Callable[[Barrier], Any]],
) -> tuple[list[Any], list[BaseException]]:
    barrier = Barrier(len(workers), timeout=10)
    results: list[Any] = []
    errors: list[BaseException] = []

    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        futures = [
            executor.submit(worker, barrier)
            for worker in workers
        ]

        for future in futures:
            try:
                results.append(future.result(timeout=20))
            except BaseException as exc:
                errors.append(exc)

    return results, errors


@pytest.fixture
def concurrent_runner():
    return run_concurrently
