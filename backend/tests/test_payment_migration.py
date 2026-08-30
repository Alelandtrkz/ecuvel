from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from app.extensions import db
from app.models import PaymentAttempt
from app.models.enums import PaymentMethod, PaymentStatus
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


def test_payment_identity_migration_backfills_legacy_rows_deterministically(
    app, engine, session
):
    base = create_catalog_and_stock(session, stock=10)
    first_order_id, _, _ = create_order_items(session, base, [1])
    second_order_id, _, _ = create_order_items(session, base, [1])
    first_created_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    second_created_at = first_created_at + timedelta(minutes=1)
    attempts = [
        PaymentAttempt(
            order_id=first_order_id,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.AWAITING_PROOF,
            amount=Decimal("10.00"),
            currency="USD",
            idempotency_key=f"legacy-{uuid.uuid4().hex}",
            request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
            expires_at=first_created_at + timedelta(hours=1),
            created_at=first_created_at,
            updated_at=first_created_at,
        ),
        PaymentAttempt(
            order_id=second_order_id,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.PROCESSING,
            amount=Decimal("10.00"),
            currency="USD",
            idempotency_key=f"legacy-{uuid.uuid4().hex}",
            request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
            expires_at=second_created_at + timedelta(hours=1),
            created_at=second_created_at,
            updated_at=second_created_at,
        ),
    ]
    session.add_all(attempts)
    session.commit()
    attempt_ids = [attempt.id for attempt in attempts]
    session.close()

    common_columns = (
        "id, order_id, status::text, amount, currency, created_at, updated_at"
    )
    with engine.connect() as connection:
        before = connection.execute(text(
            f"SELECT {common_columns} FROM payment_attempts "
            "ORDER BY created_at ASC, id ASC"
        )).all()

    migrations_dir = Path(app.root_path).parent / "migrations"
    try:
        with app.app_context():
            db.session.remove()
            downgrade(
                revision="a1b2c3d4e5f6",
                directory=str(migrations_dir),
            )
        with engine.connect() as connection:
            legacy = connection.execute(text(
                f"SELECT {common_columns} FROM payment_attempts "
                "ORDER BY created_at ASC, id ASC"
            )).all()
            assert connection.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'payment_attempts' "
                "AND column_name = 'public_code'"
            )).scalar_one() == 0

        # L1A was intentionally applied after the controlled cleanup left zero
        # OrderItems. These rows are only scaffolding for the payment migration
        # test, so preserve its real precondition instead of manufacturing a
        # LEGACY_INCOMPLETE financial cohort during the re-upgrade.
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM order_items"))

        with app.app_context():
            upgrade(directory=str(migrations_dir))

        with engine.connect() as connection:
            after = connection.execute(text(
                f"SELECT {common_columns}, public_code FROM payment_attempts "
                "ORDER BY created_at ASC, id ASC"
            )).all()
    finally:
        with app.app_context():
            db.session.remove()
            upgrade(directory=str(migrations_dir))

    assert before == legacy
    assert [row[:7] for row in after] == before
    assert [row.id for row in after] == attempt_ids
    assert [row.public_code for row in after] == ["PMT-00000001", "PMT-00000002"]
