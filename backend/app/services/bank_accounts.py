from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import StoreBankAccountVersion, StoreOnboarding
from app.models.enums import (
    BankAccountType,
    BankAccountVersionStatus,
)
from app.services.admin_permissions import user_has_permission
from app.services.bank_account_crypto import (
    BankAccountCryptoError,
    configured_bank_account_crypto,
)
from app.services.financial_audit import (
    BANK_ACCOUNT_VERSION_APPROVED,
    BANK_ACCOUNT_VERSION_CREATED,
    BANK_ACCOUNT_VERSION_SUPERSEDED,
    record_financial_audit,
)


BANK_ACCOUNT_USABILITY_DELAY = timedelta(hours=48)
LIVE_BANK_VERSION_STATUSES = (
    BankAccountVersionStatus.PENDING_REVIEW,
    BankAccountVersionStatus.APPROVED,
)


class BankAccountVersionError(Exception):
    pass


class BankAccountAccessError(BankAccountVersionError):
    pass


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
