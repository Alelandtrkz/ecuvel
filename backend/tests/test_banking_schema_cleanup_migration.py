from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, select, text

from app.extensions import db
from app.models import (
    StoreBankAccountVersion,
    StoreOnboarding,
    StoreVerificationReview,
)
from app.models.enums import (
    BankAccountVersionStatus,
    StoreOnboardingStatus,
    StoreVerificationDecision,
)
from app.services.bank_accounts import (
    approve_store_bank_account_version,
    create_store_bank_account_version,
)
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "f6a7b8c9d0e1"
L1B2_HEAD = "a7b8c9d0e1f2"
LEGACY_COLUMNS = {
    "bank_account_owner",
    "bank_account_number",
    "bank_name",
    "bank_id_number",
}


def _migrations_dir(app) -> Path:
    return Path(app.root_path).parent / "migrations"


def _upgrade(app, revision: str = "head") -> None:
    with app.app_context():
        db.session.remove()
        upgrade(revision=revision, directory=str(_migrations_dir(app)))


def _downgrade_to_previous(app) -> None:
    with app.app_context():
        db.session.remove()
        downgrade(revision=PREVIOUS_HEAD, directory=str(_migrations_dir(app)))


def _onboarding_columns(engine) -> dict[str, dict]:
    with engine.connect() as connection:
        return {
            column["name"]: column
            for column in inspect(connection).get_columns("store_onboardings")
        }


def _assert_previous_schema_is_intact(engine) -> None:
    columns = _onboarding_columns(engine)
    assert LEGACY_COLUMNS <= set(columns)
    assert all(columns[name]["nullable"] for name in LEGACY_COLUMNS)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == PREVIOUS_HEAD


def test_l1b2_clean_upgrade_preserves_bank_version_and_schema_only_downgrade(
    app,
    engine,
    session,
) -> None:
    base = create_catalog_and_stock(session)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        bank_email="payments@example.test",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    version, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Migration Holder",
        holder_identification="MIGRATION-ID",
        bank_name="Migration Bank",
        account_number="001-0000-7788",
        source_onboarding_id=onboarding.id,
    )
    approve_store_bank_account_version(
        session,
        version=version,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=3),
        reviewer_user_id=None,
    )
    session.commit()
    onboarding_id = onboarding.id
    version_id = version.id
    session.close()

    try:
        _downgrade_to_previous(app)
        _assert_previous_schema_is_intact(engine)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT bank_account_owner, bank_account_number, bank_name, "
                    "bank_id_number FROM store_onboardings WHERE id = :id"
                ),
                {"id": onboarding_id},
            ).one()
            assert tuple(row) == (None, None, None, None)

        _upgrade(app, L1B2_HEAD)
        columns = _onboarding_columns(engine)
        assert not (LEGACY_COLUMNS & set(columns))
        assert columns["bank_email"]["nullable"] is True
        with engine.connect() as connection:
            preserved = connection.execute(
                text(
                    "SELECT status::text, source_onboarding_id "
                    "FROM store_bank_account_versions WHERE id = :id"
                ),
                {"id": version_id},
            ).one()
            assert preserved.status == BankAccountVersionStatus.APPROVED.value
            assert preserved.source_onboarding_id == onboarding_id

        _downgrade_to_previous(app)
        _assert_previous_schema_is_intact(engine)
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT bank_account_owner, bank_account_number, bank_name, "
                    "bank_id_number FROM store_onboardings WHERE id = :id"
                ),
                {"id": onboarding_id},
            ).one()
            assert tuple(restored) == (None, None, None, None)
        _upgrade(app, L1B2_HEAD)
    finally:
        _upgrade(app)


def test_l1b2_upgrade_fails_closed_before_any_drop_when_plaintext_exists(
    app,
    engine,
    session,
) -> None:
    base = create_catalog_and_stock(session)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.DRAFT,
    )
    session.add(onboarding)
    session.commit()
    onboarding_id = onboarding.id
    session.close()

    try:
        _downgrade_to_previous(app)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE store_onboardings SET bank_account_number = :value "
                    "WHERE id = :id"
                ),
                {"id": onboarding_id, "value": "001-0000-8899"},
            )

        with pytest.raises(SystemExit) as blocked:
            _upgrade(app, L1B2_HEAD)
        assert blocked.value.code == 1
        _assert_previous_schema_is_intact(engine)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE store_onboardings SET bank_account_number = NULL "
                    "WHERE id = :id"
                ),
                {"id": onboarding_id},
            )
        _upgrade(app, L1B2_HEAD)
        assert not (LEGACY_COLUMNS & set(_onboarding_columns(engine)))
    finally:
        with engine.begin() as connection:
            if LEGACY_COLUMNS <= set(_onboarding_columns(engine)):
                connection.execute(
                    text(
                        "UPDATE store_onboardings SET bank_account_owner = NULL, "
                        "bank_account_number = NULL, bank_name = NULL, "
                        "bank_id_number = NULL"
                    )
                )
        _upgrade(app)


def test_l1b2_upgrade_fails_closed_before_any_drop_for_sensitive_jsonb(
    app,
    engine,
    session,
) -> None:
    base = create_catalog_and_stock(session)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.DRAFT,
    )
    session.add(onboarding)
    session.flush()
    review = StoreVerificationReview(
        onboarding_id=onboarding.id,
        decision=StoreVerificationDecision.CORRECTIONS_REQUESTED,
        issues_snapshot=[
            {
                "target_type": "FIELD",
                "field": "bank_account_number",
                "step": 5,
                "reason_code": "BANK_DATA_INCORRECT",
                "message": "Replace the observed bank data.",
                "previous_value": "001-0000-9900",
            }
        ],
    )
    session.add(review)
    session.commit()
    review_id = review.id
    session.close()

    safe_snapshot = [
        {
            "target_type": "FIELD",
            "field": "bank_account_number",
            "step": 5,
            "reason_code": "BANK_DATA_INCORRECT",
            "message": "Replace the observed bank data.",
        }
    ]
    try:
        _downgrade_to_previous(app)
        with pytest.raises(SystemExit) as blocked:
            _upgrade(app, L1B2_HEAD)
        assert blocked.value.code == 1
        _assert_previous_schema_is_intact(engine)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE store_verification_reviews "
                    "SET issues_snapshot = CAST(:snapshot AS jsonb) WHERE id = :id"
                ),
                {"id": review_id, "snapshot": json.dumps(safe_snapshot)},
            )
        _upgrade(app, L1B2_HEAD)
        assert not (LEGACY_COLUMNS & set(_onboarding_columns(engine)))
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE store_verification_reviews "
                    "SET issues_snapshot = CAST(:snapshot AS jsonb) WHERE id = :id"
                ),
                {"id": review_id, "snapshot": json.dumps(safe_snapshot)},
            )
        _upgrade(app)


def test_l1b2_head_has_no_legacy_columns_and_preserves_bank_email(engine) -> None:
    columns = _onboarding_columns(engine)
    assert not (LEGACY_COLUMNS & set(columns))
    assert columns["bank_email"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == L1B2_HEAD
