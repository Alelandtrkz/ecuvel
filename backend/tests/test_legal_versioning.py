from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import LegalDocumentVersion, User, UserLegalAcceptance
from app.models.enums import (
    LegalAcceptanceAction,
    LegalAcceptanceMode,
    LegalAcceptanceSource,
    UserStatus,
)
from app.services.legal_documents import DocumentStatus, all_families, document_by_path
from app.services.legal_versioning import (
    CANONICAL_VERSION_PREFIXES,
    DOCUMENT_VERSION_PREFIXES,
    DynamicLegalTemplateError,
    InvalidLegalAcceptanceError,
    InvalidVersionIdentifierError,
    UnknownLegalDocumentError,
    acceptance_mode_for,
    build_version_identifier,
    canonical_snapshot_and_sha256,
    canonicalize_legal_content,
    content_sha256,
    create_legal_document_version,
    current_legal_version,
    current_required_user_versions,
    has_user_legal_evidence,
    legal_version_by_identifier,
    legal_version_history,
    missing_user_legal_requirements,
    publication_date_in_ecuador,
    record_user_legal_evidence,
    validate_version_identifier,
    version_prefix_for,
)


pytestmark = pytest.mark.integration

BASE_PUBLISHED_AT = datetime(2020, 9, 20, 15, 0, tzinfo=timezone.utc)
FUTURE_PUBLISHED_AT = datetime(2099, 9, 20, 15, 0, tzinfo=timezone.utc)
TERMS_KEY = ("compradores", "terminos-y-condiciones")
PRIVACY_KEY = ("privacidad", "politica-de-privacidad")


def _create_user(session, *, label: str = "user") -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"L53-{token}",
        email=f"{label}-{token}@test.local",
        password_hash="test-only",
        full_name="LR5.3 Test User",
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    return user


def _create_version(
    session,
    *,
    family: str = TERMS_KEY[0],
    slug: str = TERMS_KEY[1],
    published_at: datetime = BASE_PUBLISHED_AT,
    effective_at: datetime | None = None,
    content: str = "<section><h2>Legal v1</h2></section>\n",
) -> LegalDocumentVersion:
    return create_legal_document_version(
        session,
        family=family,
        slug=slug,
        content=content,
        published_at=published_at,
        effective_at=effective_at,
        source_revision="18ab5c1c6c354e65597c0ea2ef3b0992e0da26a9",
    )


def _raw_version(**overrides) -> LegalDocumentVersion:
    snapshot, digest = canonical_snapshot_and_sha256("<p>raw evidence</p>\n")
    values = {
        "family": TERMS_KEY[0],
        "slug": TERMS_KEY[1],
        "version_prefix": "terms",
        "version_identifier": "terms-2020-09-20-v1",
        "version_number": 1,
        "title_snapshot": "Terms",
        "template_name_snapshot": "docs/content/compradores/terminos_y_condiciones.html",
        "content_snapshot": snapshot,
        "content_sha256": digest,
        "acceptance_mode": LegalAcceptanceMode.ACCEPTED,
        "published_at": BASE_PUBLISHED_AT,
        "effective_at": BASE_PUBLISHED_AT,
    }
    values.update(overrides)
    return LegalDocumentVersion(**values)


def _record(session, user: User, version: LegalDocumentVersion, **overrides):
    values = {
        "user_id": user.id,
        "legal_document_version_id": version.id,
        "action": LegalAcceptanceAction.ACCEPTED,
        "source": LegalAcceptanceSource.REGISTER,
        "ip_address": "2001:0db8:0:0:0:0:0:1",
        "user_agent": "LR5.3 test agent",
    }
    values.update(overrides)
    return record_user_legal_evidence(session, **values)


def test_migration_introduces_no_legal_version_or_acceptance_rows(session):
    assert session.scalar(select(func.count(LegalDocumentVersion.id))) == 0
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_legal_document_version_persists_complete_published_evidence(session):
    version = _create_version(session)
    session.commit()

    stored = session.get(LegalDocumentVersion, version.id)
    assert stored.version_identifier == "terms-2020-09-20-v1"
    assert stored.version_number == 1
    assert stored.version_prefix == "terms"
    assert stored.acceptance_mode == LegalAcceptanceMode.ACCEPTED
    assert stored.content_sha256 == content_sha256(stored.content_snapshot)
    assert stored.created_at is not None


def test_version_identifier_is_globally_unique(session):
    _create_version(session)
    session.add(_raw_version(family="another", slug="document"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_logical_document_version_number_is_unique(session):
    _create_version(session)
    next_day = BASE_PUBLISHED_AT + timedelta(days=1)
    session.add(
        _raw_version(
            version_identifier="terms-2020-09-21-v1",
            published_at=next_day,
            effective_at=next_day,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_database_rejects_nonpositive_version_number(session):
    session.add(
        _raw_version(
            version_identifier="terms-2026-09-20-v0",
            version_number=0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "identifier",
    [
        "terms-final",
        "terms-latest",
        "terminos_final-2026-09-20-v1",
        "terms-2026-02-30-v1",
        "terms-2026-09-20-v0",
    ],
)
def test_malformed_version_identifiers_are_rejected(identifier):
    with pytest.raises(InvalidVersionIdentifierError):
        validate_version_identifier(identifier)


def test_xx_placeholder_version_identifier_is_rejected():
    with pytest.raises(InvalidVersionIdentifierError):
        validate_version_identifier("terms-2026-09-XX-v1")


def test_wrong_prefix_is_rejected_by_service_and_database(session):
    with pytest.raises(InvalidVersionIdentifierError):
        validate_version_identifier(
            "privacy-2026-09-20-v1", expected_prefix="terms"
        )

    session.add(
        _raw_version(
            version_prefix="privacy",
            version_identifier="terms-2026-09-20-v1",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_canonical_snapshot_normalizes_crlf_and_cr_to_lf():
    assert canonicalize_legal_content("uno\r\ndos\rtres\n") == "uno\ndos\ntres\n"


def test_same_canonical_content_has_same_sha256():
    assert content_sha256("uno\r\ndos\n") == content_sha256("uno\ndos\n")


def test_different_content_changes_sha256():
    assert content_sha256("contenido A") != content_sha256("contenido B")


def test_unresolved_jinja_is_rejected_from_snapshot():
    with pytest.raises(DynamicLegalTemplateError):
        canonicalize_legal_content("<p>{{ current_user.email }}</p>")


def test_current_version_resolution_and_exact_identifier_lookup(session):
    v1 = _create_version(session)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=10),
        content="<p>Legal v2</p>",
    )
    assert current_legal_version(
        session, *TERMS_KEY, BASE_PUBLISHED_AT + timedelta(days=5)
    ).id == v1.id
    assert current_legal_version(
        session, *TERMS_KEY, BASE_PUBLISHED_AT + timedelta(days=11)
    ).id == v2.id
    assert legal_version_by_identifier(session, v1.version_identifier).id == v1.id


def test_future_effective_version_does_not_replace_current_early(session):
    v1 = _create_version(session)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=10),
        effective_at=BASE_PUBLISHED_AT + timedelta(days=20),
        content="<p>Future effective v2</p>",
    )

    assert current_legal_version(
        session, *TERMS_KEY, BASE_PUBLISHED_AT + timedelta(days=15)
    ).id == v1.id
    assert current_legal_version(
        session, *TERMS_KEY, BASE_PUBLISHED_AT + timedelta(days=20)
    ).id == v2.id


def test_later_version_cannot_publish_before_previous_version(session):
    _create_version(session)

    with pytest.raises(ValueError, match="published_at cannot precede"):
        _create_version(
            session,
            published_at=BASE_PUBLISHED_AT - timedelta(days=1),
        )


def test_later_version_cannot_be_effective_before_scheduled_previous_version(
    session,
):
    _create_version(session)
    _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        effective_at=BASE_PUBLISHED_AT + timedelta(days=20),
        content="<p>Scheduled v2</p>",
    )

    with pytest.raises(ValueError, match="effective_at cannot precede"):
        _create_version(
            session,
            published_at=BASE_PUBLISHED_AT + timedelta(days=2),
            effective_at=BASE_PUBLISHED_AT + timedelta(days=10),
            content="<p>Regressive v3</p>",
        )


def test_same_effective_time_prefers_later_sequential_version(session):
    v1 = _create_version(session)
    shared_effective_at = BASE_PUBLISHED_AT + timedelta(days=10)
    _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        effective_at=shared_effective_at,
        content="<p>Scheduled v2</p>",
    )
    v3 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=2),
        effective_at=shared_effective_at,
        content="<p>Corrective v3</p>",
    )

    assert current_legal_version(
        session, *TERMS_KEY, shared_effective_at - timedelta(seconds=1)
    ).id == v1.id
    assert current_legal_version(
        session, *TERMS_KEY, shared_effective_at
    ).id == v3.id


def test_history_preserves_all_versions_in_sequence(session):
    v1 = _create_version(session)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        content="<p>v2</p>",
    )
    assert [version.id for version in legal_version_history(session, *TERMS_KEY)] == [
        v1.id,
        v2.id,
    ]


def test_terms_acceptance_is_valid_and_copies_server_side_snapshots(session):
    user = _create_user(session)
    version = _create_version(session)
    evidence = _record(session, user, version)

    assert evidence.action == LegalAcceptanceAction.ACCEPTED
    assert evidence.version_identifier_snapshot == version.version_identifier
    assert evidence.content_sha256_snapshot == version.content_sha256
    assert evidence.ip_address == "2001:db8::1"
    assert evidence.occurred_at.tzinfo is not None


def test_future_published_terms_version_rejects_evidence(session):
    user = _create_user(session)
    version = _create_version(session, published_at=FUTURE_PUBLISHED_AT)

    with pytest.raises(InvalidLegalAcceptanceError, match="current published"):
        _record(session, user, version)


def test_future_effective_terms_version_rejects_evidence_while_v1_remains_current(
    session,
):
    user = _create_user(session)
    v1 = _create_version(session)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        effective_at=FUTURE_PUBLISHED_AT,
        content="<p>Future effective Terms v2</p>",
    )

    with pytest.raises(InvalidLegalAcceptanceError, match="current published"):
        _record(session, user, v2)
    assert _record(session, user, v1).legal_document_version_id == v1.id


def test_old_version_rejects_new_evidence_after_next_version_becomes_current(session):
    original_user = _create_user(session, label="original")
    later_user = _create_user(session, label="later")
    v1 = _create_version(session)
    original_evidence = _record(session, original_user, v1)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        content="<p>Current Terms v2</p>",
    )

    with pytest.raises(InvalidLegalAcceptanceError, match="current published"):
        _record(session, later_user, v1)
    current_evidence = _record(session, later_user, v2)

    assert current_evidence.legal_document_version_id == v2.id
    assert session.get(UserLegalAcceptance, original_evidence.id) is original_evidence


def test_terms_acknowledged_action_is_rejected(session):
    user = _create_user(session)
    version = _create_version(session)
    with pytest.raises(InvalidLegalAcceptanceError):
        _record(
            session,
            user,
            version,
            action=LegalAcceptanceAction.ACKNOWLEDGED,
        )


def test_privacy_acknowledgment_is_valid_and_is_not_consent(session):
    user = _create_user(session)
    version = _create_version(session, family=PRIVACY_KEY[0], slug=PRIVACY_KEY[1])
    evidence = _record(
        session,
        user,
        version,
        action=LegalAcceptanceAction.ACKNOWLEDGED,
        source=LegalAcceptanceSource.PRIVACY_NOTICE,
    )
    assert version.acceptance_mode == LegalAcceptanceMode.ACKNOWLEDGED
    assert evidence.action == LegalAcceptanceAction.ACKNOWLEDGED


def test_future_privacy_version_rejects_acknowledgment(session):
    user = _create_user(session)
    version = _create_version(
        session,
        family=PRIVACY_KEY[0],
        slug=PRIVACY_KEY[1],
        published_at=FUTURE_PUBLISHED_AT,
    )

    with pytest.raises(InvalidLegalAcceptanceError, match="current published"):
        _record(
            session,
            user,
            version,
            action=LegalAcceptanceAction.ACKNOWLEDGED,
            source=LegalAcceptanceSource.PRIVACY_NOTICE,
        )


def test_privacy_accepted_action_is_rejected(session):
    user = _create_user(session)
    version = _create_version(session, family=PRIVACY_KEY[0], slug=PRIVACY_KEY[1])
    with pytest.raises(InvalidLegalAcceptanceError):
        _record(session, user, version)


def test_none_policy_needs_no_evidence_and_rejects_fake_acceptance(session):
    user = _create_user(session)
    version = _create_version(
        session,
        family="privacidad",
        slug="cookies-y-tecnologias-similares",
    )
    assert version.acceptance_mode == LegalAcceptanceMode.NONE
    assert version not in current_required_user_versions(
        session, BASE_PUBLISHED_AT + timedelta(days=1)
    )
    with pytest.raises(InvalidLegalAcceptanceError):
        _record(session, user, version)


def test_same_user_and_version_evidence_is_idempotent(session):
    user = _create_user(session)
    version = _create_version(session)
    first = _record(session, user, version)
    replay = _record(
        session,
        user,
        version,
        ip_address="127.0.0.1",
        user_agent="replayed request",
    )
    assert replay.id == first.id
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 1
    assert replay.ip_address == "2001:db8::1"


def test_new_terms_version_requires_new_acceptance_and_preserves_old(session):
    user = _create_user(session)
    v1 = _create_version(session)
    old_evidence = _record(session, user, v1)
    v2 = _create_version(
        session,
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        content="<p>Terms v2</p>",
    )

    missing = missing_user_legal_requirements(
        session, user.id, BASE_PUBLISHED_AT + timedelta(days=2)
    )
    assert [version.id for version in missing] == [v2.id]
    assert session.get(UserLegalAcceptance, old_evidence.id).legal_document_version_id == v1.id


def test_new_privacy_version_requires_new_acknowledgment(session):
    user = _create_user(session)
    v1 = _create_version(session, family=PRIVACY_KEY[0], slug=PRIVACY_KEY[1])
    old_evidence = _record(
        session,
        user,
        v1,
        action=LegalAcceptanceAction.ACKNOWLEDGED,
        source=LegalAcceptanceSource.PRIVACY_NOTICE,
    )
    v2 = _create_version(
        session,
        family=PRIVACY_KEY[0],
        slug=PRIVACY_KEY[1],
        published_at=BASE_PUBLISHED_AT + timedelta(days=1),
        content="<p>Privacy v2</p>",
    )

    missing = missing_user_legal_requirements(
        session, user.id, BASE_PUBLISHED_AT + timedelta(days=2)
    )
    assert [version.id for version in missing] == [v2.id]
    assert session.get(UserLegalAcceptance, old_evidence.id).action == (
        LegalAcceptanceAction.ACKNOWLEDGED
    )


def test_marketing_document_is_excluded_from_generic_legal_acceptance(session):
    user = _create_user(session)
    version = _create_version(
        session,
        family="privacidad",
        slug="comunicaciones-y-marketing",
    )
    assert version.acceptance_mode == LegalAcceptanceMode.NONE
    with pytest.raises(InvalidLegalAcceptanceError):
        _record(session, user, version)


def test_seller_contract_namespace_is_reserved_and_informational_page_excluded(session):
    assert "seller-contract" in CANONICAL_VERSION_PREFIXES
    with pytest.raises(UnknownLegalDocumentError):
        version_prefix_for("vendedores", "contrato")
    with pytest.raises(UnknownLegalDocumentError):
        _create_version(session, family="vendedores", slug="contrato")


def test_existing_users_are_not_backfilled(session):
    _create_user(session)
    _create_version(session)
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 0


def test_no_published_version_means_no_current_user_requirement(session):
    user = _create_user(session)
    assert current_required_user_versions(session, BASE_PUBLISHED_AT) == ()
    assert missing_user_legal_requirements(session, user.id, BASE_PUBLISHED_AT) == ()


def test_has_user_legal_evidence_uses_exact_immutable_version(session):
    user = _create_user(session)
    version = _create_version(session)
    assert not has_user_legal_evidence(session, user.id, version.id)
    _record(session, user, version)
    assert has_user_legal_evidence(session, user.id, version.id)


def test_inconsistent_direct_inserted_evidence_fails_closed_on_all_service_paths(
    session,
):
    user = _create_user(session)
    version = _create_version(session)
    evidence = UserLegalAcceptance(
        user_id=user.id,
        legal_document_version_id=version.id,
        action=LegalAcceptanceAction.ACCEPTED,
        occurred_at=datetime.now(timezone.utc),
        source=LegalAcceptanceSource.REGISTER,
        version_identifier_snapshot="terms-2000-01-01-v999",
        content_sha256_snapshot=version.content_sha256,
    )
    session.add(evidence)
    session.flush()

    with pytest.raises(InvalidLegalAcceptanceError, match="snapshot is inconsistent"):
        has_user_legal_evidence(session, user.id, version.id)
    with pytest.raises(InvalidLegalAcceptanceError, match="snapshot is inconsistent"):
        missing_user_legal_requirements(session, user.id)
    with pytest.raises(InvalidLegalAcceptanceError, match="snapshot is inconsistent"):
        _record(session, user, version)


def test_prefix_mapping_is_complete_unique_and_keeps_contract_exception():
    assert len(DOCUMENT_VERSION_PREFIXES) == 25
    assert len(set(DOCUMENT_VERSION_PREFIXES.values())) == 25
    assert DOCUMENT_VERSION_PREFIXES[TERMS_KEY] == "terms"
    assert DOCUMENT_VERSION_PREFIXES[PRIVACY_KEY] == "privacy"
    assert ("vendedores", "contrato") not in DOCUMENT_VERSION_PREFIXES
    assert acceptance_mode_for(*TERMS_KEY) == LegalAcceptanceMode.ACCEPTED
    assert acceptance_mode_for(*PRIVACY_KEY) == LegalAcceptanceMode.ACKNOWLEDGED
    for key in DOCUMENT_VERSION_PREFIXES:
        assert document_by_path(*key, published_only=False) is not None


def test_ecuador_local_publication_date_drives_identifier_near_utc_midnight():
    instant = datetime(2026, 9, 21, 2, 30, tzinfo=timezone.utc)
    assert publication_date_in_ecuador(instant).isoformat() == "2026-09-20"
    assert build_version_identifier("terms", instant, 1) == "terms-2026-09-20-v1"


def test_effective_date_cannot_precede_publication_and_timestamps_must_be_aware(session):
    with pytest.raises(ValueError):
        _create_version(
            session,
            effective_at=BASE_PUBLISHED_AT - timedelta(seconds=1),
        )
    with pytest.raises(ValueError):
        _create_version(session, published_at=datetime(2026, 9, 20, 10, 0))


def test_ip_validation_and_user_agent_bounding(session):
    user = _create_user(session)
    version = _create_version(session)
    with pytest.raises(InvalidLegalAcceptanceError):
        _record(session, user, version, ip_address="not-an-ip")
    evidence = _record(session, user, version, user_agent="x" * 700)
    assert len(evidence.user_agent) == 500


def test_version_rows_are_database_append_only(session):
    version = _create_version(session)
    session.commit()
    with pytest.raises(DBAPIError):
        session.execute(
            update(LegalDocumentVersion)
            .where(LegalDocumentVersion.id == version.id)
            .values(title_snapshot="mutated")
        )


def test_acceptance_rows_are_database_append_only(session):
    user = _create_user(session)
    version = _create_version(session)
    evidence = _record(session, user, version)
    session.commit()
    with pytest.raises(DBAPIError):
        session.execute(
            update(UserLegalAcceptance)
            .where(UserLegalAcceptance.id == evidence.id)
            .values(user_agent="mutated")
        )


def test_user_deletion_is_restricted_while_legal_evidence_exists(session):
    user = _create_user(session)
    version = _create_version(session)
    _record(session, user, version)
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(User.__table__.delete().where(User.id == user.id))


def test_version_creation_is_concurrency_safe(session_factory, concurrent_runner):
    def worker(barrier):
        with session_factory() as worker_session:
            barrier.wait()
            version = _create_version(
                worker_session,
                content=f"<p>{uuid.uuid4()}</p>",
            )
            worker_session.commit()
            return version.version_number

    results, errors = concurrent_runner([worker, worker])
    assert errors == []
    assert sorted(results) == [1, 2]


def test_duplicate_evidence_is_concurrency_safe(
    session, session_factory, concurrent_runner
):
    user = _create_user(session)
    version = _create_version(session)
    session.commit()

    def worker(barrier):
        with session_factory() as worker_session:
            barrier.wait()
            evidence = record_user_legal_evidence(
                worker_session,
                user_id=user.id,
                legal_document_version_id=version.id,
                action=LegalAcceptanceAction.ACCEPTED,
                source=LegalAcceptanceSource.CHECKOUT,
            )
            worker_session.commit()
            return evidence.id

    results, errors = concurrent_runner([worker, worker])
    session.expire_all()
    assert errors == []
    assert len(set(results)) == 1
    assert session.scalar(select(func.count(UserLegalAcceptance.id))) == 1


def test_all_versionable_bodies_are_static_and_lr52_metadata_remains_draft(app):
    before = {}
    for family in all_families():
        for document in family.documents:
            before[(document.family, document.slug)] = (
                document.status,
                document.version_identifier,
                document.published_at,
                document.effective_at,
                document.historical_versions,
            )

    with app.app_context():
        for key in DOCUMENT_VERSION_PREFIXES:
            document = document_by_path(*key, published_only=False)
            source, _filename, _uptodate = app.jinja_env.loader.get_source(
                app.jinja_env, document.template_name
            )
            canonicalize_legal_content(source)
            assert document.status == DocumentStatus.DRAFT

    after = {
        (document.family, document.slug): (
            document.status,
            document.version_identifier,
            document.published_at,
            document.effective_at,
            document.historical_versions,
        )
        for family in all_families()
        for document in family.documents
    }
    assert after == before
