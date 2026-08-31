from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    StoreBankAccountVersion,
    StoreOnboarding,
    StoreVerificationReview,
)
from app.models.enums import (
    BankAccountType,
    BankAccountVersionStatus,
    StoreOnboardingStatus,
    StoreVerificationDecision,
)
from app.services.admin_permissions import user_has_permission
from app.services.bank_account_crypto import (
    BankAccountCryptoError,
    configured_bank_account_crypto,
    normalize_bank_account_number,
)
from app.services.financial_audit import (
    BANK_ACCOUNT_LEGACY_PLAINTEXT_PURGED,
    BANK_ACCOUNT_VERSION_APPROVED,
    BANK_ACCOUNT_VERSION_CREATED,
    BANK_ACCOUNT_VERSION_SUPERSEDED,
    record_financial_audit,
)


BANK_ACCOUNT_USABILITY_DELAY = timedelta(hours=48)
LEGACY_BANK_FIELDS = (
    "bank_account_owner",
    "bank_account_number",
    "bank_name",
    "bank_id_number",
)
LIVE_BANK_VERSION_STATUSES = (
    BankAccountVersionStatus.PENDING_REVIEW,
    BankAccountVersionStatus.APPROVED,
)


class BankAccountVersionError(Exception):
    pass


class BankAccountAccessError(BankAccountVersionError):
    pass


@dataclass(frozen=True, slots=True)
class BankAccountBackfillResult:
    eligible: int
    created: int
    existing: int
    skipped: int


@dataclass(frozen=True, slots=True)
class BankAccountSummary:
    id: uuid.UUID
    bank_name: str
    holder_name: str
    holder_identification_masked: str
    account_last4: str
    version: int
    status: BankAccountVersionStatus
    reviewed_at: datetime | None
    usable_from: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyBankCleanupReport:
    legacy_onboarding_rows: int
    legacy_complete_rows: int
    rows_with_store: int
    rows_with_matching_bank_version: int
    rows_without_bank_version: int
    provenance_mismatch_rows: int
    store_mismatch_rows: int
    pending_versions: int
    approved_versions: int
    superseded_versions: int
    crypto_fields_missing: int
    authenticated_decrypt_success: int
    account_mismatch_rows: int
    identity_mismatch_rows: int
    reviews_with_issues: int
    bank_correction_issues: int
    sensitive_snapshot_previous_values: int
    account_number_snapshot_previous_values: int
    rows_eligible_for_purge: int
    blocked_rows: int
    purged_rows: int = 0
    sanitized_snapshot_previous_values: int = 0
    sanitized_reviews: int = 0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def mask_bank_holder_identification(value: str) -> str:
    cleaned = _clean(value, 40)
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return f"{'*' * (len(cleaned) - 4)}{cleaned[-4:]}"


def bank_account_summary(version: StoreBankAccountVersion | None) -> BankAccountSummary | None:
    if version is None:
        return None
    return BankAccountSummary(
        id=version.id,
        bank_name=version.bank_name,
        holder_name=version.holder_name,
        holder_identification_masked=mask_bank_holder_identification(
            version.holder_identification
        ),
        account_last4=version.account_last4,
        version=version.version,
        status=version.status,
        reviewed_at=version.reviewed_at,
        usable_from=version.usable_from,
    )


def onboarding_bank_account_version(
    session: Session,
    onboarding: StoreOnboarding,
    *,
    lock: bool = False,
) -> StoreBankAccountVersion | None:
    if onboarding.store_id is None:
        return None
    statement = (
        select(StoreBankAccountVersion)
        .where(
            StoreBankAccountVersion.store_id == onboarding.store_id,
            StoreBankAccountVersion.source_onboarding_id == onboarding.id,
            StoreBankAccountVersion.currency == "USD",
            StoreBankAccountVersion.status.in_(LIVE_BANK_VERSION_STATUSES),
        )
        .order_by(
            case(
                (
                    StoreBankAccountVersion.status
                    == BankAccountVersionStatus.PENDING_REVIEW,
                    0,
                ),
                else_=1,
            ),
            StoreBankAccountVersion.version.desc(),
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _store_lock_id(store_id: uuid.UUID) -> int:
    return int.from_bytes(
        hashlib.sha256(b"bank-version:" + store_id.bytes).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _lock_store_versions(session: Session, store_id: uuid.UUID) -> None:
    session.execute(select(func.pg_advisory_xact_lock(_store_lock_id(store_id))))


def _supersede_pending_versions(
    session: Session,
    *,
    store_id: uuid.UUID,
    superseded_at: datetime,
    actor_user_id: uuid.UUID | None,
) -> None:
    for pending in session.scalars(
        select(StoreBankAccountVersion)
        .where(
            StoreBankAccountVersion.store_id == store_id,
            StoreBankAccountVersion.status
            == BankAccountVersionStatus.PENDING_REVIEW,
        )
        .with_for_update()
    ):
        pending.status = BankAccountVersionStatus.SUPERSEDED
        pending.superseded_at = superseded_at
        record_financial_audit(
            session,
            action=BANK_ACCOUNT_VERSION_SUPERSEDED,
            actor_user_id=actor_user_id,
            metadata={
                "store_id": str(store_id),
                "bank_account_version_id": str(pending.id),
                "status": pending.status.value,
            },
        )
    session.flush()


def create_store_bank_account_version(
    session: Session,
    *,
    store_id: uuid.UUID,
    holder_name: str,
    holder_identification: str,
    bank_name: str,
    account_number: str,
    account_type: BankAccountType = BankAccountType.UNKNOWN,
    source_onboarding_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> tuple[StoreBankAccountVersion, bool]:
    holder = _clean(holder_name, 150)
    identification = _clean(holder_identification, 40)
    bank = _clean(bank_name, 120)
    if not holder or not identification or not bank:
        raise BankAccountVersionError("Los datos bancarios versionados están incompletos.")

    _lock_store_versions(session, store_id)
    version_id = uuid.uuid4()
    try:
        encrypted = configured_bank_account_crypto().encrypt(
            account_number,
            store_id=store_id,
            version_id=version_id,
        )
    except BankAccountCryptoError as exc:
        raise BankAccountVersionError(str(exc)) from exc

    matches = session.scalars(
        select(StoreBankAccountVersion)
        .where(
            StoreBankAccountVersion.store_id == store_id,
            StoreBankAccountVersion.account_fingerprint == encrypted.fingerprint,
            StoreBankAccountVersion.holder_name == holder,
            StoreBankAccountVersion.holder_identification == identification,
            StoreBankAccountVersion.bank_name == bank,
            StoreBankAccountVersion.account_type == account_type,
            StoreBankAccountVersion.currency == "USD",
        )
        .order_by(StoreBankAccountVersion.version.desc())
        .with_for_update()
    ).all()
    effective_now = _utc(created_at or datetime.now(timezone.utc))
    matching_pending = next(
        (
            version
            for version in matches
            if version.status == BankAccountVersionStatus.PENDING_REVIEW
        ),
        None,
    )
    if matching_pending is not None:
        return matching_pending, False

    matching_approved = next(
        (
            version
            for version in matches
            if version.status == BankAccountVersionStatus.APPROVED
        ),
        None,
    )
    if matching_approved is not None:
        _supersede_pending_versions(
            session,
            store_id=store_id,
            superseded_at=effective_now,
            actor_user_id=actor_user_id,
        )
        return matching_approved, False

    # A matching SUPERSEDED row is historical and must never be revived.
    _supersede_pending_versions(
        session,
        store_id=store_id,
        superseded_at=effective_now,
        actor_user_id=actor_user_id,
    )

    next_version = int(
        session.scalar(
            select(func.coalesce(func.max(StoreBankAccountVersion.version), 0)).where(
                StoreBankAccountVersion.store_id == store_id
            )
        )
        or 0
    ) + 1
    version = StoreBankAccountVersion(
        id=version_id,
        store_id=store_id,
        version=next_version,
        holder_name=holder,
        holder_identification=identification,
        bank_name=bank,
        account_type=account_type,
        currency="USD",
        encrypted_account_number=encrypted.ciphertext,
        encryption_nonce=encrypted.nonce,
        account_last4=encrypted.last4,
        account_fingerprint=encrypted.fingerprint,
        encryption_key_version=encrypted.encryption_key_version,
        fingerprint_key_version=encrypted.fingerprint_key_version,
        status=BankAccountVersionStatus.PENDING_REVIEW,
        source_onboarding_id=source_onboarding_id,
        created_at=effective_now,
    )
    session.add(version)
    record_financial_audit(
        session,
        action=BANK_ACCOUNT_VERSION_CREATED,
        actor_user_id=actor_user_id,
        metadata={
            "store_id": str(store_id),
            "bank_account_version_id": str(version.id),
            "status": version.status.value,
        },
    )
    session.flush()
    return version, True


def approve_store_bank_account_version(
    session: Session,
    *,
    version: StoreBankAccountVersion,
    reviewed_at: datetime,
    reviewer_user_id: uuid.UUID | None,
) -> StoreBankAccountVersion:
    effective_reviewed_at = _utc(reviewed_at)
    _lock_store_versions(session, version.store_id)
    version = session.scalar(
        select(StoreBankAccountVersion)
        .where(StoreBankAccountVersion.id == version.id)
        .with_for_update()
    )
    if version is None:
        raise BankAccountVersionError("La versión bancaria no existe.")
    if version.status == BankAccountVersionStatus.APPROVED:
        return version
    if version.status != BankAccountVersionStatus.PENDING_REVIEW:
        raise BankAccountVersionError("La versión bancaria ya no puede aprobarse.")

    for previous in session.scalars(
        select(StoreBankAccountVersion)
        .where(
            StoreBankAccountVersion.store_id == version.store_id,
            StoreBankAccountVersion.id != version.id,
            StoreBankAccountVersion.status.in_(
                (
                    BankAccountVersionStatus.APPROVED,
                    BankAccountVersionStatus.PENDING_REVIEW,
                )
            ),
        )
        .with_for_update()
    ):
        previous.status = BankAccountVersionStatus.SUPERSEDED
        previous.superseded_at = effective_reviewed_at
        record_financial_audit(
            session,
            action=BANK_ACCOUNT_VERSION_SUPERSEDED,
            actor_user_id=reviewer_user_id,
            metadata={
                "store_id": str(version.store_id),
                "bank_account_version_id": str(previous.id),
                "status": previous.status.value,
            },
        )
    # Persist the supersession before promoting the replacement so the
    # database-level single-APPROVED invariant also works under ORM flushes.
    session.flush()

    version.status = BankAccountVersionStatus.APPROVED
    version.reviewed_at = effective_reviewed_at
    version.usable_from = effective_reviewed_at + BANK_ACCOUNT_USABILITY_DELAY
    version.superseded_at = None
    version.reviewed_by_user_id = reviewer_user_id
    record_financial_audit(
        session,
        action=BANK_ACCOUNT_VERSION_APPROVED,
        actor_user_id=reviewer_user_id,
        metadata={
            "store_id": str(version.store_id),
            "bank_account_version_id": str(version.id),
            "status": version.status.value,
        },
    )
    session.flush()
    return version


def sync_bank_version_from_onboarding(
    session: Session,
    onboarding: StoreOnboarding,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[StoreBankAccountVersion, bool] | None:
    if onboarding.store_id is None:
        return None
    required = (
        onboarding.bank_account_owner,
        onboarding.bank_account_number,
        onboarding.bank_name,
        onboarding.bank_id_number,
    )
    if not all(_clean(value, 500) for value in required):
        raise BankAccountVersionError("Los datos bancarios del onboarding están incompletos.")
    return create_store_bank_account_version(
        session,
        store_id=onboarding.store_id,
        holder_name=onboarding.bank_account_owner,
        holder_identification=onboarding.bank_id_number,
        bank_name=onboarding.bank_name,
        account_number=onboarding.bank_account_number,
        account_type=BankAccountType.UNKNOWN,
        source_onboarding_id=onboarding.id,
        actor_user_id=actor_user_id,
    )


def approve_latest_onboarding_bank_version(
    session: Session,
    onboarding: StoreOnboarding,
    *,
    reviewed_at: datetime,
    reviewer_user_id: uuid.UUID | None,
) -> StoreBankAccountVersion:
    version = onboarding_bank_account_version(session, onboarding, lock=True)
    if version is None:
        raise BankAccountVersionError(
            "La solicitud no tiene una versión bancaria vigente verificable."
        )
    return approve_store_bank_account_version(
        session,
        version=version,
        reviewed_at=reviewed_at,
        reviewer_user_id=reviewer_user_id,
    )


def backfill_legacy_bank_account_versions(
    session: Session,
) -> BankAccountBackfillResult:
    onboardings = session.scalars(
        select(StoreOnboarding)
        .where(
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
            StoreOnboarding.store_id.is_not(None),
            StoreOnboarding.bank_account_owner.is_not(None),
            StoreOnboarding.bank_account_number.is_not(None),
            StoreOnboarding.bank_name.is_not(None),
            StoreOnboarding.bank_id_number.is_not(None),
        )
        .order_by(StoreOnboarding.id)
    ).all()
    created = existing = skipped = 0
    for onboarding in onboardings:
        try:
            approval_time = onboarding.approved_at or onboarding.completed_at
            if approval_time is None:
                skipped += 1
                continue
            version, was_created = sync_bank_version_from_onboarding(
                session, onboarding
            ) or (None, False)
            if version is None:
                skipped += 1
                continue
            if was_created:
                approved_review = next(
                    (
                        review
                        for review in reversed(onboarding.reviews)
                        if review.decision == StoreVerificationDecision.APPROVED
                    ),
                    None,
                )
                approve_store_bank_account_version(
                    session,
                    version=version,
                    reviewed_at=approval_time,
                    reviewer_user_id=(
                        approved_review.reviewer_user_id if approved_review else None
                    ),
                )
                created += 1
            else:
                existing += 1
        except BankAccountVersionError:
            raise
    session.flush()
    return BankAccountBackfillResult(
        eligible=len(onboardings),
        created=created,
        existing=existing,
        skipped=skipped,
    )


def cleanup_legacy_onboarding_bank_data(
    session: Session,
    *,
    apply: bool,
) -> LegacyBankCleanupReport:
    statement = (
        select(StoreOnboarding)
        .where(
            or_(
                func.coalesce(StoreOnboarding.bank_account_owner, "") != "",
                func.coalesce(StoreOnboarding.bank_account_number, "") != "",
                func.coalesce(StoreOnboarding.bank_name, "") != "",
                func.coalesce(StoreOnboarding.bank_id_number, "") != "",
            )
        )
        .order_by(StoreOnboarding.id)
    )
    if apply:
        statement = statement.with_for_update()
    onboardings = session.scalars(statement).all()
    crypto = configured_bank_account_crypto()
    legacy_complete = rows_with_store = matching = without_version = 0
    provenance_mismatch = store_mismatch = crypto_missing = decrypt_success = 0
    account_mismatch = identity_mismatch = eligible = 0
    status_counts = {status: 0 for status in BankAccountVersionStatus}
    verified_versions: dict[uuid.UUID, StoreBankAccountVersion] = {}

    for onboarding in onboardings:
        values = tuple(_clean(getattr(onboarding, field), 500) for field in LEGACY_BANK_FIELDS)
        row_blocked = False
        if all(values):
            legacy_complete += 1
        else:
            row_blocked = True
        if onboarding.store_id is not None:
            rows_with_store += 1
        else:
            row_blocked = True

        source_versions = session.scalars(
            select(StoreBankAccountVersion)
            .where(StoreBankAccountVersion.source_onboarding_id == onboarding.id)
            .order_by(StoreBankAccountVersion.version.desc())
        ).all()
        same_store_versions = [
            version
            for version in source_versions
            if version.store_id == onboarding.store_id
        ]
        for related_version in same_store_versions:
            status_counts[related_version.status] += 1
        if source_versions and not same_store_versions:
            store_mismatch += 1
            row_blocked = True
        live_versions = [
            version
            for version in same_store_versions
            if version.status in LIVE_BANK_VERSION_STATUSES and version.currency == "USD"
        ]
        if not live_versions:
            other_live_for_store = (
                session.scalar(
                    select(StoreBankAccountVersion.id).where(
                        StoreBankAccountVersion.store_id == onboarding.store_id,
                        StoreBankAccountVersion.status.in_(LIVE_BANK_VERSION_STATUSES),
                    )
                )
                if onboarding.store_id is not None
                else None
            )
            if other_live_for_store is not None:
                provenance_mismatch += 1
            else:
                without_version += 1
            row_blocked = True
            continue

        version = next(
            (
                item
                for item in live_versions
                if item.status == BankAccountVersionStatus.PENDING_REVIEW
            ),
            live_versions[0],
        )
        matching += 1
        if any(
            not value
            for value in (
                version.encrypted_account_number,
                version.encryption_nonce,
                version.account_fingerprint,
                version.encryption_key_version,
                version.fingerprint_key_version,
            )
        ):
            crypto_missing += 1
            row_blocked = True
        else:
            try:
                decrypted = crypto.decrypt(
                    ciphertext=version.encrypted_account_number,
                    nonce=version.encryption_nonce,
                    store_id=version.store_id,
                    version_id=version.id,
                    encryption_key_version=version.encryption_key_version,
                )
            except BankAccountCryptoError:
                row_blocked = True
            else:
                decrypt_success += 1
                try:
                    accounts_match = normalize_bank_account_number(decrypted) == (
                        normalize_bank_account_number(onboarding.bank_account_number or "")
                    )
                except BankAccountCryptoError:
                    accounts_match = False
                if not accounts_match:
                    account_mismatch += 1
                    row_blocked = True
        if (
            _clean(version.holder_name, 150)
            != _clean(onboarding.bank_account_owner, 150)
            or _clean(version.holder_identification, 40)
            != _clean(onboarding.bank_id_number, 40)
            or _clean(version.bank_name, 120) != _clean(onboarding.bank_name, 120)
        ):
            identity_mismatch += 1
            row_blocked = True
        if not row_blocked:
            eligible += 1
            verified_versions[onboarding.id] = version

    (
        reviews_with_issues,
        bank_correction_issues,
        sensitive_previous_values,
        account_previous_values,
    ) = _bank_review_snapshot_counts(session)
    blocked = len(onboardings) - eligible
    report = LegacyBankCleanupReport(
        legacy_onboarding_rows=len(onboardings),
        legacy_complete_rows=legacy_complete,
        rows_with_store=rows_with_store,
        rows_with_matching_bank_version=matching,
        rows_without_bank_version=without_version,
        provenance_mismatch_rows=provenance_mismatch,
        store_mismatch_rows=store_mismatch,
        pending_versions=status_counts[BankAccountVersionStatus.PENDING_REVIEW],
        approved_versions=status_counts[BankAccountVersionStatus.APPROVED],
        superseded_versions=status_counts[BankAccountVersionStatus.SUPERSEDED],
        crypto_fields_missing=crypto_missing,
        authenticated_decrypt_success=decrypt_success,
        account_mismatch_rows=account_mismatch,
        identity_mismatch_rows=identity_mismatch,
        reviews_with_issues=reviews_with_issues,
        bank_correction_issues=bank_correction_issues,
        sensitive_snapshot_previous_values=sensitive_previous_values,
        account_number_snapshot_previous_values=account_previous_values,
        rows_eligible_for_purge=eligible,
        blocked_rows=blocked,
    )
    if not apply:
        return report
    if blocked:
        raise BankAccountVersionError(
            f"El cleanup bancario está bloqueado para {blocked} fila(s); no se modificó plaintext."
        )

    sanitized_values, sanitized_reviews = _sanitize_bank_review_snapshots(session)
    for onboarding in onboardings:
        version = verified_versions[onboarding.id]
        for field in LEGACY_BANK_FIELDS:
            setattr(onboarding, field, None)
        record_financial_audit(
            session,
            action=BANK_ACCOUNT_LEGACY_PLAINTEXT_PURGED,
            metadata={
                "store_id": str(version.store_id),
                "bank_account_version_id": str(version.id),
                "status": version.status.value,
            },
        )
    session.flush()
    return replace(
        report,
        purged_rows=len(onboardings),
        sanitized_snapshot_previous_values=sanitized_values,
        sanitized_reviews=sanitized_reviews,
    )


def _bank_review_snapshot_counts(session: Session) -> tuple[int, int, int, int]:
    reviews_with_issues = bank_issues = previous_values = account_previous_values = 0
    for snapshot in session.scalars(
        select(StoreVerificationReview.issues_snapshot).where(
            StoreVerificationReview.issues_snapshot.is_not(None)
        )
    ):
        if snapshot:
            reviews_with_issues += 1
        for issue in snapshot or []:
            if not isinstance(issue, dict) or issue.get("field") not in LEGACY_BANK_FIELDS:
                continue
            bank_issues += 1
            if "previous_value" not in issue:
                continue
            previous_values += 1
            if issue.get("field") == "bank_account_number":
                account_previous_values += 1
    return (
        reviews_with_issues,
        bank_issues,
        previous_values,
        account_previous_values,
    )


def _sanitize_bank_review_snapshots(session: Session) -> tuple[int, int]:
    removed = reviews_changed = 0
    reviews = session.scalars(
        select(StoreVerificationReview)
        .where(StoreVerificationReview.issues_snapshot.is_not(None))
        .with_for_update()
    ).all()
    for review in reviews:
        changed = False
        sanitized: list[dict] = []
        for raw_issue in review.issues_snapshot or []:
            issue = dict(raw_issue) if isinstance(raw_issue, dict) else raw_issue
            if (
                isinstance(issue, dict)
                and issue.get("field") in LEGACY_BANK_FIELDS
                and "previous_value" in issue
            ):
                issue.pop("previous_value", None)
                removed += 1
                changed = True
            sanitized.append(issue)
        if changed:
            review.issues_snapshot = sanitized
            reviews_changed += 1
    return removed, reviews_changed


def usable_bank_account_version(
    session: Session,
    *,
    store_id: uuid.UUID,
    at: datetime,
    lock: bool = False,
) -> StoreBankAccountVersion | None:
    statement = (
        select(StoreBankAccountVersion)
        .where(
            StoreBankAccountVersion.store_id == store_id,
            StoreBankAccountVersion.status == BankAccountVersionStatus.APPROVED,
            StoreBankAccountVersion.usable_from.is_not(None),
            StoreBankAccountVersion.usable_from <= _utc(at),
            StoreBankAccountVersion.currency == "USD",
        )
        .order_by(
            StoreBankAccountVersion.usable_from.desc(),
            StoreBankAccountVersion.version.desc(),
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def decrypt_bank_account_for_staff(
    version: StoreBankAccountVersion,
    *,
    staff_user,
) -> str:
    if not user_has_permission(staff_user, "bank_accounts.sensitive.view"):
        raise BankAccountAccessError("No tienes permiso para ver el dato bancario sensible.")
    try:
        return configured_bank_account_crypto().decrypt(
            ciphertext=version.encrypted_account_number,
            nonce=version.encryption_nonce,
            store_id=version.store_id,
            version_id=version.id,
            encryption_key_version=version.encryption_key_version,
        )
    except BankAccountCryptoError as exc:
        raise BankAccountAccessError(str(exc)) from exc
