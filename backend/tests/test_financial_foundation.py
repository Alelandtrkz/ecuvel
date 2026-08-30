from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AdminAuditEvent,
    Order,
    OrderItem,
    SellerOrder,
    SellerPayout,
    StoreBankAccountVersion,
    StoreOnboarding,
)
from app.models.enums import (
    BankAccountVersionStatus,
    SellerCommissionType,
    SellerOrderStatus,
    SellerPayoutStatus,
    StaffEmploymentStatus,
    StaffRole,
    StoreOnboardingStatus,
)
from app.services.admin_permissions import permissions_for_user
from app.services.bank_account_crypto import (
    BankAccountCrypto,
    BankAccountCryptoError,
    normalize_bank_account_number,
)
from app.services.bank_accounts import (
    BankAccountAccessError,
    approve_store_bank_account_version,
    backfill_legacy_bank_account_versions,
    create_store_bank_account_version,
    decrypt_bank_account_for_staff,
)
from app.services.financial_audit import (
    BANK_ACCOUNT_VERSION_CREATED,
    record_financial_audit,
)
from app.services.financial_reconciliation import (
    FinancialReconciliationError,
    reconcile_seller_order,
)
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


def _test_crypto(*, encryption_byte: int = 1) -> BankAccountCrypto:
    encryption_key = base64.b64encode(bytes([encryption_byte]) * 32).decode("ascii")
    fingerprint_key = base64.b64encode(bytes([9]) * 32).decode("ascii")
    return BankAccountCrypto(
        encryption_keys={"v1": encryption_key},
        active_encryption_version="v1",
        fingerprint_keys={"v1": fingerprint_key},
        active_fingerprint_version="v1",
    )


def test_aes_gcm_hmac_normalization_and_fail_closed() -> None:
    store_id = uuid.uuid4()
    version_id = uuid.uuid4()
    account = "001-234 567890"
    crypto = _test_crypto()
    first = crypto.encrypt(account, store_id=store_id, version_id=version_id)
    second = crypto.encrypt(account, store_id=store_id, version_id=version_id)
    different = crypto.encrypt(
        "001-234 567891", store_id=store_id, version_id=version_id
    )

    assert first.ciphertext != account.encode("ascii")
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != different.fingerprint
    assert first.last4 == "7890"
    assert normalize_bank_account_number(account) == "001234567890"
    assert crypto.decrypt(
        ciphertext=first.ciphertext,
        nonce=first.nonce,
        store_id=store_id,
        version_id=version_id,
        encryption_key_version="v1",
    ) == "001234567890"

    tampered = first.ciphertext[:-1] + bytes([first.ciphertext[-1] ^ 1])
    with pytest.raises(BankAccountCryptoError) as tampered_error:
        crypto.decrypt(
            ciphertext=tampered,
            nonce=first.nonce,
            store_id=store_id,
            version_id=version_id,
            encryption_key_version="v1",
        )
    assert account not in str(tampered_error.value)
    with pytest.raises(BankAccountCryptoError):
        _test_crypto(encryption_byte=2).decrypt(
            ciphertext=first.ciphertext,
            nonce=first.nonce,
            store_id=store_id,
            version_id=version_id,
            encryption_key_version="v1",
        )
    missing = BankAccountCrypto(
        encryption_keys={"v1": None},
        active_encryption_version="v1",
        fingerprint_keys={"v1": None},
        active_fingerprint_version="v1",
    )
    with pytest.raises(BankAccountCryptoError):
        missing.encrypt(account, store_id=store_id, version_id=version_id)
    with pytest.raises(BankAccountCryptoError):
        missing.decrypt(
            ciphertext=first.ciphertext,
            nonce=first.nonce,
            store_id=store_id,
            version_id=version_id,
            encryption_key_version="v1",
        )


def test_reconciliation_uses_only_complete_historical_snapshots(session: Session):
    base = create_catalog_and_stock(session)
    order_id, _number, item_ids = create_order_items(session, base, [2])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    item = session.get(OrderItem, item_ids[0])

    snapshot = reconcile_seller_order(
        session,
        seller_order_id=seller_order.id,
        expected_store_id=base.store_id,
    )
    assert snapshot.subtotal == Decimal("20.00")

    cases = (
        (item, "commission_amount_snapshot", Decimal("1.00")),
        (item, "currency", "EUR"),
        (item, "store_id_snapshot", create_catalog_and_stock(session).store_id),
        (seller_order, "subtotal", Decimal("19.00")),
    )
    for target, field, invalid in cases:
        original = getattr(target, field)
        setattr(target, field, invalid)
        with session.no_autoflush:
            with pytest.raises(FinancialReconciliationError):
                reconcile_seller_order(
                    session,
                    seller_order_id=seller_order.id,
                    expected_store_id=base.store_id,
                )
        setattr(target, field, original)

    item.commission_rate_snapshot = None
    with session.no_autoflush:
        with pytest.raises(FinancialReconciliationError, match="incompleta"):
            reconcile_seller_order(session, seller_order_id=seller_order.id)
    item.commission_rate_snapshot = Decimal("0.00")


def _insert_duplicate_order_item(session: Session, source_id: uuid.UUID) -> None:
    session.execute(
        text(
            """
            INSERT INTO order_items (
                id, seller_order_id, offer_id, store_id_snapshot, quantity,
                unit_price, discount_amount, tax_amount, line_total, currency,
                gross_line_amount, product_name_snapshot, seller_name_snapshot,
                seller_sku_snapshot, image_url_snapshot, variant_snapshot,
                commission_type_snapshot, commission_rate_snapshot,
                commission_fixed_amount_snapshot, commission_amount_snapshot,
                category_name_snapshot, category_code_snapshot
            )
            SELECT
                :new_id, seller_order_id, offer_id, store_id_snapshot, quantity,
                unit_price, discount_amount, tax_amount, line_total, currency,
                gross_line_amount, product_name_snapshot, seller_name_snapshot,
                seller_sku_snapshot, image_url_snapshot, variant_snapshot,
                commission_type_snapshot, commission_rate_snapshot,
                commission_fixed_amount_snapshot, commission_amount_snapshot,
                category_name_snapshot, category_code_snapshot
            FROM order_items WHERE id = :source_id
            """
        ),
        {"new_id": uuid.uuid4(), "source_id": source_id},
    )


@pytest.mark.parametrize("operation", ("insert", "delete"))
def test_composition_tampering_fails_closed(
    session: Session, operation: str
) -> None:
    base = create_catalog_and_stock(session)
    order_id, _number, item_ids = create_order_items(session, base, [1, 1])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )

    if operation == "insert":
        _insert_duplicate_order_item(session, item_ids[0])
    else:
        session.execute(
            text("DELETE FROM order_items WHERE id = :id"),
            {"id": item_ids[0]},
        )

    with pytest.raises(FinancialReconciliationError):
        reconcile_seller_order(session, seller_order_id=seller_order.id)


def test_locked_reconciliation_blocks_concurrent_composition_changes(
    session: Session, session_factory
) -> None:
    base = create_catalog_and_stock(session)
    order_id, _number, item_ids = create_order_items(session, base, [1, 1])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    seller_order_id = seller_order.id
    source_id = item_ids[0]
    session.commit()

    locker = session_factory()
    attacker = session_factory()
    try:
        locker.begin()
        reconcile_seller_order(
            locker,
            seller_order_id=seller_order_id,
            expected_store_id=base.store_id,
            lock=True,
        )

        attacker.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(DBAPIError):
            attacker.execute(
                text("DELETE FROM order_items WHERE id = :id"),
                {"id": source_id},
            )
        attacker.rollback()

        attacker.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(DBAPIError):
            _insert_duplicate_order_item(attacker, source_id)
        attacker.rollback()
    finally:
        attacker.close()
        locker.rollback()
        locker.close()


def test_financial_triggers_block_orm_bulk_and_direct_sql_but_allow_operations(
    session: Session,
) -> None:
    base = create_catalog_and_stock(session)
    order_id, _number, item_ids = create_order_items(session, base, [1])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    item_id = item_ids[0]
    session.commit()

    seller_order.subtotal = Decimal("9.00")
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    seller_order = session.get(SellerOrder, seller_order.id)
    seller_order.status = SellerOrderStatus.CONFIRMED
    session.commit()
    assert seller_order.status == SellerOrderStatus.CONFIRMED

    with pytest.raises(IntegrityError):
        session.execute(
            update(OrderItem)
            .where(OrderItem.id == item_id)
            .values(commission_amount_snapshot=Decimal("1.00"))
        )
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE order_items SET unit_price = 9.00 WHERE id = :id"),
            {"id": item_id},
        )
    session.rollback()

    order = session.get(Order, order_id)
    session.delete(order)
    session.commit()
    assert session.get(OrderItem, item_id) is None


def test_bank_versions_are_versioned_and_payout_fk_enforces_store(session: Session):
    first = create_catalog_and_stock(session)
    second = create_catalog_and_stock(session)
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=3)
    version_one, created = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder One",
        holder_identification="ID-ONE",
        bank_name="Bank One",
        account_number="001-0000-1111",
    )
    assert created
    approve_store_bank_account_version(
        session,
        version=version_one,
        reviewed_at=reviewed_at,
        reviewer_user_id=None,
    )
    old_ciphertext = version_one.encrypted_account_number
    version_two, created = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder One",
        holder_identification="ID-ONE",
        bank_name="Bank Two",
        account_number="001-0000-2222",
    )
    assert created and version_two.version == version_one.version + 1
    approve_store_bank_account_version(
        session,
        version=version_two,
        reviewed_at=reviewed_at + timedelta(hours=1),
        reviewer_user_id=None,
    )
    assert version_one.status == BankAccountVersionStatus.SUPERSEDED
    assert version_one.encrypted_account_number == old_ciphertext
    assert version_two.usable_from == reviewed_at + timedelta(hours=49)

    payout = SellerPayout(
        store_id=second.store_id,
        bank_account_version_id=version_two.id,
        status=SellerPayoutStatus.SCHEDULED,
        currency="USD",
        gross_sales_total=Decimal("1.00"),
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        net_total=Decimal("1.00"),
        scheduled_for=datetime.now(timezone.utc),
    )
    session.add(payout)
    with pytest.raises(IntegrityError):
        session.flush()


def test_bank_version_reuse_respects_current_lifecycle(session: Session):
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=4)

    first = create_catalog_and_stock(session)
    approved_a, _ = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder",
        holder_identification="ID-A",
        bank_name="Bank A",
        account_number="001-0000-1111",
    )
    approve_store_bank_account_version(
        session,
        version=approved_a,
        reviewed_at=reviewed_at,
        reviewer_user_id=None,
    )
    pending_b, _ = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder",
        holder_identification="ID-B",
        bank_name="Bank B",
        account_number="001-0000-2222",
    )

    same_pending, created = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder",
        holder_identification="ID-B",
        bank_name="Bank B",
        account_number="001-0000-2222",
    )
    assert not created and same_pending.id == pending_b.id
    assert same_pending.status == BankAccountVersionStatus.PENDING_REVIEW

    same_approved, created = create_store_bank_account_version(
        session,
        store_id=first.store_id,
        holder_name="Holder",
        holder_identification="ID-A",
        bank_name="Bank A",
        account_number="001-0000-1111",
    )
    assert not created and same_approved.id == approved_a.id
    assert same_approved.status == BankAccountVersionStatus.APPROVED
    assert pending_b.status == BankAccountVersionStatus.SUPERSEDED

    second = create_catalog_and_stock(session)
    historical_a, _ = create_store_bank_account_version(
        session,
        store_id=second.store_id,
        holder_name="Holder",
        holder_identification="ID-A",
        bank_name="Bank A",
        account_number="001-0000-3333",
    )
    approve_store_bank_account_version(
        session,
        version=historical_a,
        reviewed_at=reviewed_at,
        reviewer_user_id=None,
    )
    approved_b, _ = create_store_bank_account_version(
        session,
        store_id=second.store_id,
        holder_name="Holder",
        holder_identification="ID-B",
        bank_name="Bank B",
        account_number="001-0000-4444",
    )
    approve_store_bank_account_version(
        session,
        version=approved_b,
        reviewed_at=reviewed_at + timedelta(hours=1),
        reviewer_user_id=None,
    )

    new_a, created = create_store_bank_account_version(
        session,
        store_id=second.store_id,
        holder_name="Holder",
        holder_identification="ID-A",
        bank_name="Bank A",
        account_number="001-0000-3333",
    )
    assert created and new_a.version == 3
    assert new_a.id != historical_a.id
    assert new_a.status == BankAccountVersionStatus.PENDING_REVIEW
    assert historical_a.status == BankAccountVersionStatus.SUPERSEDED
    assert approved_b.status == BankAccountVersionStatus.APPROVED


def test_bank_version_identity_is_immutable_but_lifecycle_remains_mutable(
    session: Session,
):
    base = create_catalog_and_stock(session)
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=4)
    first, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Holder",
        holder_identification="ID-ONE",
        bank_name="Bank One",
        account_number="001-0000-5555",
    )
    session.commit()

    first.bank_name = "Mutated Bank"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET holder_name = 'Mutated Holder' WHERE id = :id"
            ),
            {"id": first.id},
        )
    session.rollback()

    original_id = first.id
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET id = :new_id WHERE id = :id"
            ),
            {"id": original_id, "new_id": uuid.uuid4()},
        )
    session.rollback()
    assert session.get(StoreBankAccountVersion, original_id) is not None

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET created_at = created_at + INTERVAL '1 second' WHERE id = :id"
            ),
            {"id": original_id},
        )
    session.rollback()

    session.execute(
        text(
            "UPDATE store_bank_account_versions "
            "SET updated_at = updated_at + INTERVAL '1 second' WHERE id = :id"
        ),
        {"id": original_id},
    )
    session.commit()

    first = session.get(StoreBankAccountVersion, first.id)
    approve_store_bank_account_version(
        session,
        version=first,
        reviewed_at=reviewed_at,
        reviewer_user_id=None,
    )
    session.commit()
    assert first.status == BankAccountVersionStatus.APPROVED
    assert first.reviewed_at is not None and first.usable_from is not None

    second, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Holder",
        holder_identification="ID-TWO",
        bank_name="Bank Two",
        account_number="001-0000-6666",
    )
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET status = 'APPROVED', reviewed_at = :reviewed_at, "
                "usable_from = :usable_from WHERE id = :id"
            ),
            {
                "id": second.id,
                "reviewed_at": reviewed_at,
                "usable_from": reviewed_at + timedelta(hours=48),
            },
        )
    session.rollback()

    first = session.get(StoreBankAccountVersion, first.id)
    second = session.get(StoreBankAccountVersion, second.id)
    approve_store_bank_account_version(
        session,
        version=second,
        reviewed_at=reviewed_at + timedelta(hours=1),
        reviewer_user_id=None,
    )
    session.commit()
    assert first.status == BankAccountVersionStatus.SUPERSEDED
    assert first.superseded_at is not None
    assert second.status == BankAccountVersionStatus.APPROVED


def test_bank_provenance_is_restricted_and_usability_delay_is_enforced(
    session: Session,
):
    base = create_catalog_and_stock(session)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        bank_account_owner="Holder",
        bank_account_number="001-0000-6677",
        bank_name="Bank",
        bank_id_number="ID",
        approved_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    version, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Holder",
        holder_identification="ID",
        bank_name="Bank",
        account_number="001-0000-6677",
        source_onboarding_id=onboarding.id,
    )
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text("DELETE FROM store_onboardings WHERE id = :id"),
            {"id": onboarding.id},
        )
    session.rollback()

    reviewed_at = datetime.now(timezone.utc)
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET status = 'APPROVED', reviewed_at = :reviewed_at, "
                "usable_from = :usable_from WHERE id = :id"
            ),
            {
                "id": version.id,
                "reviewed_at": reviewed_at,
                "usable_from": reviewed_at + timedelta(hours=47, minutes=59),
            },
        )
    session.rollback()

    session.execute(
        text(
            "UPDATE store_bank_account_versions "
            "SET status = 'APPROVED', reviewed_at = :reviewed_at, "
            "usable_from = :usable_from WHERE id = :id"
        ),
        {
            "id": version.id,
            "reviewed_at": reviewed_at,
            "usable_from": reviewed_at + timedelta(hours=48),
        },
    )
    session.commit()

    session.execute(
        text(
            "UPDATE store_bank_account_versions "
            "SET usable_from = :usable_from, updated_at = :updated_at WHERE id = :id"
        ),
        {
            "id": version.id,
            "usable_from": reviewed_at + timedelta(hours=49),
            "updated_at": reviewed_at + timedelta(hours=1),
        },
    )
    session.commit()


def test_payout_remains_bound_to_exact_immutable_bank_version(session: Session):
    base = create_catalog_and_stock(session)
    version, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Holder",
        holder_identification="ID-ONE",
        bank_name="Bank One",
        account_number="001-0000-6767",
    )
    approve_store_bank_account_version(
        session,
        version=version,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=4),
        reviewer_user_id=None,
    )
    payout = SellerPayout(
        store_id=base.store_id,
        bank_account_version_id=version.id,
        status=SellerPayoutStatus.SCHEDULED,
        currency="USD",
        gross_sales_total=Decimal("1.00"),
        discount_total=Decimal("0.00"),
        commission_total=Decimal("0.00"),
        net_total=Decimal("1.00"),
        scheduled_for=datetime.now(timezone.utc),
    )
    session.add(payout)
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "UPDATE store_bank_account_versions "
                "SET bank_name = 'Mutated Bank' WHERE id = :id"
            ),
            {"id": version.id},
        )
    session.rollback()
    assert session.get(SellerPayout, payout.id).bank_account_version_id == version.id


def test_backfill_skip_has_no_bank_or_audit_side_effect(session: Session):
    base = create_catalog_and_stock(session)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        bank_account_owner="Legacy Holder",
        bank_account_number="001-0000-7777",
        bank_name="Legacy Bank",
        bank_id_number="LEGACY-ID",
        approved_at=None,
        completed_at=None,
    )
    session.add(onboarding)
    session.flush()

    result = backfill_legacy_bank_account_versions(session)

    assert (result.eligible, result.created, result.existing, result.skipped) == (
        1,
        0,
        0,
        1,
    )
    assert list(session.scalars(select(StoreBankAccountVersion))) == []
    assert list(
        session.scalars(
            select(AdminAuditEvent).where(
                AdminAuditEvent.action == BANK_ACCOUNT_VERSION_CREATED
            )
        )
    ) == []


def test_bank_backfill_is_idempotent_and_keeps_plaintext(session: Session):
    base = create_catalog_and_stock(session)
    account = "001-0000-9876"
    now = datetime.now(timezone.utc) - timedelta(days=5)
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        bank_account_owner="Legacy Holder",
        bank_account_number=account,
        bank_name="Legacy Bank",
        bank_id_number="LEGACY-ID",
        approved_at=now,
        completed_at=now + timedelta(hours=1),
    )
    session.add(onboarding)
    session.flush()

    first = backfill_legacy_bank_account_versions(session)
    second = backfill_legacy_bank_account_versions(session)
    version = session.scalar(
        select(StoreBankAccountVersion).where(
            StoreBankAccountVersion.source_onboarding_id == onboarding.id
        )
    )
    assert (first.created, second.created) == (1, 0)
    assert second.existing == 1
    assert version.encrypted_account_number != account.encode("ascii")
    assert len(version.encryption_nonce) == 12
    assert len(version.account_fingerprint) == 32
    assert version.account_last4 == "9876"
    assert version.usable_from == now + timedelta(hours=48)
    assert onboarding.bank_account_number == account


def test_sensitive_bank_access_and_financial_audit_are_conservative(session: Session):
    base = create_catalog_and_stock(session)
    version, _ = create_store_bank_account_version(
        session,
        store_id=base.store_id,
        holder_name="Sensitive Holder",
        holder_identification="SENSITIVE-ID",
        bank_name="Sensitive Bank",
        account_number="001-0000-4444",
    )
    support = SimpleNamespace(
        is_ecuvel_staff=True,
        staff_profile=SimpleNamespace(
            role=StaffRole.SUPPORT,
            employment_status=StaffEmploymentStatus.ACTIVE,
        ),
    )
    super_admin = SimpleNamespace(
        is_ecuvel_staff=True,
        staff_profile=SimpleNamespace(
            role=StaffRole.SUPER_ADMIN,
            employment_status=StaffEmploymentStatus.ACTIVE,
        ),
    )
    legacy = SimpleNamespace(is_ecuvel_staff=True, staff_profile=None)
    assert "payments.review" not in permissions_for_user(support)
    assert "payouts.pay" not in permissions_for_user(support)
    assert "payments.review" in permissions_for_user(legacy)
    assert "payouts.pay" not in permissions_for_user(legacy)
    assert "bank_accounts.sensitive.view" not in permissions_for_user(legacy)
    assert "bank_accounts.sensitive.view" in permissions_for_user(super_admin)
    with pytest.raises(BankAccountAccessError):
        decrypt_bank_account_for_staff(version, staff_user=support)
    assert decrypt_bank_account_for_staff(
        version, staff_user=super_admin
    ).endswith("4444")

    with pytest.raises(ValueError, match="segura"):
        record_financial_audit(
            session,
            action=BANK_ACCOUNT_VERSION_CREATED,
            metadata={"account_number": "forbidden"},
        )
    record_financial_audit(
        session,
        action=BANK_ACCOUNT_VERSION_CREATED,
        metadata={
            "store_id": str(base.store_id),
            "bank_account_version_id": str(version.id),
        },
    )
    session.flush()
    assert session.scalar(
        select(AdminAuditEvent).where(
            AdminAuditEvent.action == BANK_ACCOUNT_VERSION_CREATED
        )
    ) is not None
