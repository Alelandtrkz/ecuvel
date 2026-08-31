from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order, OrderPackage, PaymentAttempt, SellerOrder, SellerPayout,
    SellerPayoutItem, Store, StoreOnboarding, StoreBankAccountVersion,
)
from app.models.enums import (
    OrderStatus, PaymentMethod, PaymentStatus, SellerOrderDecisionStatus,
    SellerOrderStatus, SellerPayoutStatus, StoreOnboardingStage,
    StoreOnboardingStatus,
)
from app.services.bank_accounts import (
    approve_store_bank_account_version, create_store_bank_account_version,
)
from app.services.payout_calendar import (
    PAYOUT_TIMEZONE, PayoutCycleKind, is_payout_cycle_date, last_business_day,
    payout_cycle_window,
)
from app.services.seller_payouts import (
    SellerPayoutEligibilityError, SellerPayoutTransitionError,
    _lock_store_payout_scheduling, cancel_seller_payout,
    eligible_seller_orders, hold_seller_payout,
    mark_seller_payout_paid, resume_seller_payout, schedule_payout_cycle,
    schedule_seller_payout,
)
from app.services.private_storage import StagedPrivateFile
from tests.factories import (
    create_catalog_and_stock, create_ready_for_pickup_order, handover_ready_order,
)


pytestmark = pytest.mark.integration

CYCLE_DATE = date(2026, 8, 15)
EXECUTED_AT = datetime(2026, 8, 16, 15, tzinfo=timezone.utc)
DELIVERED_AT = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _bank_onboarding(session: Session, base):
    onboarding = StoreOnboarding(
        user_id=base.operator_id, store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS, current_step=5,
        store_name=session.get(Store, base.store_id).name,
        completed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    version, created = create_store_bank_account_version(
        session, store_id=base.store_id, holder_name="Tienda Test",
        holder_identification="TEST-ID-1", bank_name="Pichincha",
        account_number="001-0000-4567", source_onboarding_id=onboarding.id,
    )
    assert created
    approve_store_bank_account_version(
        session, version=version,
        reviewed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        reviewer_user_id=None,
    )
    return version


def _approved_payment(session: Session, order: Order, approved_at: datetime):
    order.status = OrderStatus.FULFILLING
    attempt = PaymentAttempt(
        order_id=order.id, method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED, amount=order.grand_total,
        currency=order.currency, idempotency_key=f"payout-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=approved_at + timedelta(hours=1), approved_at=approved_at,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _delivered_order_for_base(session: Session, base):
    ready = create_ready_for_pickup_order(session, base, [2])
    order = session.get(Order, ready.order_id)
    seller_order = session.scalar(select(SellerOrder).where(SellerOrder.order_id == order.id))
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    _approved_payment(session, order, DELIVERED_AT - timedelta(days=1))
    handover_ready_order(session, base, ready)
    for package in session.scalars(
        select(OrderPackage).join(OrderPackage.order_item).where(
            OrderPackage.order_item.has(seller_order_id=seller_order.id)
        )
    ):
        package.handed_over_at = DELIVERED_AT
    seller_order.delivered_at = DELIVERED_AT
    seller_order.payout_eligible_at = DELIVERED_AT + timedelta(days=4)
    seller_order.status = SellerOrderStatus.COMPLETED
    session.flush()
    return order, seller_order


def _delivered_order(session: Session):
    base = create_catalog_and_stock(session)
    _bank_onboarding(session, base)
    order, seller_order = _delivered_order_for_base(session, base)
    return base, order, seller_order


def _schedule(session: Session, base):
    return schedule_seller_payout(
        session, store_id=base.store_id, cycle_date=CYCLE_DATE, now=EXECUTED_AT
    ).payout


def _wait_for_lock_wait(engine, backend_pid: int, *, timeout: float = 5.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with engine.connect() as connection:
            wait_type = connection.execute(
                text(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE pid = :pid"
                ),
                {"pid": backend_pid},
            ).scalar_one_or_none()
        if wait_type == "Lock":
            return
    raise AssertionError("La sesión concurrente no esperó por el lock esperado.")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2025, 1, 31), date(2025, 1, 31)),
        (date(2025, 5, 31), date(2025, 5, 30)),
        (date(2025, 8, 31), date(2025, 8, 29)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_last_business_day_matrix(value: date, expected: date):
    assert last_business_day(value.year, value.month) == expected


def test_cycle_windows_use_guayaquil_and_exact_cutoffs():
    mid = payout_cycle_window(date(2026, 8, 15))
    assert mid.cycle_kind == PayoutCycleKind.MID_MONTH
    assert mid.cutoff_local == datetime(2026, 8, 14, 23, 59, 59, tzinfo=PAYOUT_TIMEZONE)
    assert mid.cutoff_local.microsecond == 0
    assert mid.cutoff_utc == datetime(2026, 8, 15, 4, 59, 59, tzinfo=timezone.utc)
    assert mid.scheduled_for_utc == datetime(2026, 8, 15, 5, tzinfo=timezone.utc)
    end = payout_cycle_window(date(2026, 8, 31))
    assert end.cycle_kind == PayoutCycleKind.MONTH_END
    assert end.cutoff_local.date() == date(2026, 8, 30)
    assert is_payout_cycle_date(date(2026, 8, 15))
    assert not is_payout_cycle_date(date(2026, 8, 16))


def test_four_day_hold_exact_boundary(session: Session):
    base, _order, seller_order = _delivered_order(session)
    eligible_at = seller_order.delivered_at + timedelta(days=4)
    assert seller_order.payout_eligible_at == eligible_at
    assert not eligible_seller_orders(
        session, store_id=base.store_id,
        eligible_through=eligible_at - timedelta(microseconds=1),
    )
    assert len(eligible_seller_orders(
        session, store_id=base.store_id, eligible_through=eligible_at
    )) == 1
    assert len(eligible_seller_orders(
        session, store_id=base.store_id,
        eligible_through=eligible_at + timedelta(microseconds=1),
    )) == 1


def test_cutoff_does_not_shorten_hold(session: Session):
    base, _order, seller_order = _delivered_order(session)
    cutoff = payout_cycle_window(CYCLE_DATE).cutoff_utc
    seller_order.payout_eligible_at = cutoff + timedelta(microseconds=1)
    assert not eligible_seller_orders(
        session, store_id=base.store_id, eligible_through=cutoff
    )
    seller_order.payout_eligible_at = cutoff
    assert len(eligible_seller_orders(
        session, store_id=base.store_id, eligible_through=cutoff
    )) == 1


def test_future_and_arbitrary_cycles_are_rejected(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    with pytest.raises(SellerPayoutEligibilityError, match="oficial"):
        schedule_seller_payout(
            session, store_id=base.store_id, cycle_date=date(2026, 8, 16),
            now=EXECUTED_AT,
        )
    with pytest.raises(SellerPayoutEligibilityError, match="futuro"):
        schedule_seller_payout(
            session, store_id=base.store_id, cycle_date=date(2026, 8, 31),
            now=EXECUTED_AT,
        )


def test_eligibility_financial_and_payment_gates(session: Session):
    base, _order, seller_order = _delivered_order(session)
    cutoff = payout_cycle_window(CYCLE_DATE).cutoff_utc
    seller_order.requires_refund_resolution = True
    assert not eligible_seller_orders(session, store_id=base.store_id, eligible_through=cutoff)
    seller_order.requires_refund_resolution = False
    seller_order.decision_status = SellerOrderDecisionStatus.REJECTED
    assert not eligible_seller_orders(session, store_id=base.store_id, eligible_through=cutoff)
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    payment = session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == seller_order.order_id))
    payment.status = PaymentStatus.REJECTED
    assert not eligible_seller_orders(session, store_id=base.store_id, eligible_through=cutoff)


def test_all_eligible_orders_for_store_are_grouped_without_limit(session: Session):
    base = create_catalog_and_stock(session)
    _bank_onboarding(session, base)
    orders = [_delivered_order_for_base(session, base)[1] for _ in range(3)]
    result = schedule_seller_payout(
        session, store_id=base.store_id, cycle_date=CYCLE_DATE, now=EXECUTED_AT
    )
    assert result.order_count == 3
    assert {item.seller_order_id for item in result.payout.items} == {row.id for row in orders}


def test_cycle_separates_stores(session: Session):
    first = create_catalog_and_stock(session)
    second = create_catalog_and_stock(session)
    _bank_onboarding(session, first)
    _bank_onboarding(session, second)
    for _ in range(3):
        _delivered_order_for_base(session, first)
    for _ in range(2):
        _delivered_order_for_base(session, second)
    result = schedule_payout_cycle(session, cycle_date=CYCLE_DATE, now=EXECUTED_AT)
    assert sorted(row.order_count for row in result.payouts) == [2, 3]
    assert len({row.payout.store_id for row in result.payouts}) == 2


def test_cycle_retry_is_idempotent_for_active_assignments(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    first = schedule_payout_cycle(session, cycle_date=CYCLE_DATE, now=EXECUTED_AT)
    second = schedule_payout_cycle(session, cycle_date=CYCLE_DATE, now=EXECUTED_AT)
    assert len(first.payouts) == 1
    assert not second.payouts
    assert session.scalar(select(func.count(SellerPayoutItem.seller_order_id))) == 1


def test_schedule_requires_a_usable_bank_version(session: Session):
    base = create_catalog_and_stock(session)
    _delivered_order_for_base(session, base)
    with pytest.raises(SellerPayoutEligibilityError, match="bancaria"):
        schedule_seller_payout(
            session, store_id=base.store_id,
            cycle_date=CYCLE_DATE, now=EXECUTED_AT,
        )


def test_cancel_preserves_history_releases_and_repayouts(session: Session):
    base, _order, seller_order = _delivered_order(session)
    first = _schedule(session, base)
    first_number = first.payout_number
    cancelled = cancel_seller_payout(
        session, payout_number=first_number,
        cancelled_at=EXECUTED_AT + timedelta(hours=1),
    )
    assert cancelled.payout.status == SellerPayoutStatus.CANCELLED
    old_item = session.scalar(select(SellerPayoutItem).where(
        SellerPayoutItem.payout_id == first.id
    ))
    assert old_item.released_at == first.cancelled_at
    second = _schedule(session, base)
    assert second.payout_number != first_number
    history = session.scalars(select(SellerPayoutItem).where(
        SellerPayoutItem.seller_order_id == seller_order.id
    )).all()
    assert len(history) == 2
    assert sum(item.released_at is None for item in history) == 1


def test_hold_resume_and_on_hold_cannot_pay(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    held = hold_seller_payout(session, payout_number=payout.payout_number)
    assert held.payout.status == SellerPayoutStatus.ON_HOLD
    with pytest.raises(SellerPayoutTransitionError, match="hold"):
        mark_seller_payout_paid(
            session, payout_number=payout.payout_number,
            external_reference="BANK-HOLD", paid_at=EXECUTED_AT,
        )
    resumed = resume_seller_payout(session, payout_number=payout.payout_number)
    assert resumed.payout.status == SellerPayoutStatus.SCHEDULED


def test_paid_and_cancelled_are_terminal_and_cancel_replay_is_idempotent(session: Session):
    first, _order, _seller_order = _delivered_order(session)
    paid = _schedule(session, first)
    mark_seller_payout_paid(
        session, payout_number=paid.payout_number,
        external_reference="TERMINAL-PAID", paid_at=EXECUTED_AT,
    )
    with pytest.raises(SellerPayoutTransitionError):
        cancel_seller_payout(
            session, payout_number=paid.payout_number, cancelled_at=EXECUTED_AT
        )
    second, _order, _seller_order = _delivered_order(session)
    cancelled = _schedule(session, second)
    first_cancel = cancel_seller_payout(
        session, payout_number=cancelled.payout_number, cancelled_at=EXECUTED_AT
    )
    replay = cancel_seller_payout(
        session, payout_number=cancelled.payout_number,
        cancelled_at=EXECUTED_AT + timedelta(hours=1),
    )
    assert replay.replayed
    assert replay.payout.cancelled_at == first_cancel.payout.cancelled_at
    with pytest.raises(SellerPayoutTransitionError):
        resume_seller_payout(session, payout_number=cancelled.payout_number)


def test_mark_paid_revalidates_incident_and_is_exactly_idempotent(session: Session):
    base, _order, seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    seller_order.requires_refund_resolution = True
    with pytest.raises(SellerPayoutTransitionError, match="condiciones"):
        mark_seller_payout_paid(
            session, payout_number=payout.payout_number,
            external_reference="BANK-1", paid_at=EXECUTED_AT,
        )
    seller_order.requires_refund_resolution = False
    first = mark_seller_payout_paid(
        session, payout_number=payout.payout_number,
        external_reference=" BANK-1 ", paid_at=EXECUTED_AT,
    )
    assert first.payout.status == SellerPayoutStatus.PAID
    assert mark_seller_payout_paid(
        session, payout_number=payout.payout_number,
        external_reference="BANK-1", paid_at=EXECUTED_AT,
    ).replayed
    with pytest.raises(SellerPayoutTransitionError, match="diferentes"):
        mark_seller_payout_paid(
            session, payout_number=payout.payout_number,
            external_reference="BANK-2", paid_at=EXECUTED_AT,
        )


def test_external_reference_200_chars_is_preserved_and_replay_is_idempotent(
    session: Session,
):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    reference = "R" * 200
    first = mark_seller_payout_paid(
        session, payout_number=payout.payout_number,
        external_reference=reference, paid_at=EXECUTED_AT,
    )
    assert first.payout.external_reference == reference
    assert len(first.payout.external_reference) == 200
    assert mark_seller_payout_paid(
        session, payout_number=payout.payout_number,
        external_reference=reference, paid_at=EXECUTED_AT,
    ).replayed


def test_external_references_over_200_are_rejected_without_mutation(
    session: Session,
):
    common = "X" * 200
    payouts = []
    for suffix in ("A", "B"):
        base, _order, _seller_order = _delivered_order(session)
        payout = _schedule(session, base)
        payouts.append(payout)
        with pytest.raises(SellerPayoutTransitionError, match="200"):
            mark_seller_payout_paid(
                session, payout_number=payout.payout_number,
                external_reference=common + suffix, paid_at=EXECUTED_AT,
            )
    assert all(payout.status == SellerPayoutStatus.SCHEDULED for payout in payouts)
    assert all(payout.external_reference is None for payout in payouts)


def test_long_external_reference_rejects_before_receipt_promotion(
    session: Session, tmp_path,
):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    temporary = tmp_path / "staged-receipt.tmp"
    temporary.write_bytes(b"private test receipt")
    staged = StagedPrivateFile(
        temporary_path=temporary,
        storage_key="payout-receipts/should-not-exist.pdf",
        original_filename="receipt.pdf",
        media_type="application/pdf",
        size_bytes=20,
        sha256="0" * 64,
    )
    with pytest.raises(SellerPayoutTransitionError, match="200"):
        mark_seller_payout_paid(
            session, payout_number=payout.payout_number,
            external_reference="L" * 201, paid_at=EXECUTED_AT,
            staged_receipt=staged, receipt_root=tmp_path,
        )
    assert temporary.exists()
    assert not (tmp_path / staged.storage_key).exists()
    assert payout.status == SellerPayoutStatus.SCHEDULED
    assert payout.external_reference is None


def test_paid_at_cannot_precede_cycle(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    with pytest.raises(SellerPayoutTransitionError, match="preceder"):
        mark_seller_payout_paid(
            session, payout_number=payout.payout_number,
            external_reference="EARLY", paid_at=payout.scheduled_for - timedelta(seconds=1),
        )


def test_bank_binding_does_not_change_after_schedule(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    bound = payout.bank_account_version_id
    current = session.get(StoreBankAccountVersion, bound)
    current.status = "SUPERSEDED"
    current.superseded_at = EXECUTED_AT
    assert payout.bank_account_version_id == bound


def test_partial_unique_rejects_two_active_assignments(session: Session):
    base, _order, seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    second = SellerPayout(
        store_id=base.store_id, bank_account_version_id=payout.bank_account_version_id,
        status=SellerPayoutStatus.SCHEDULED, currency="USD",
        gross_sales_total=payout.gross_sales_total, discount_total=payout.discount_total,
        commission_total=payout.commission_total, net_total=payout.net_total,
        scheduled_for=payout.scheduled_for,
    )
    session.add(second)
    session.flush()
    session.add(SellerPayoutItem(
        payout_id=second.id, seller_order_id=seller_order.id,
        gross_amount_snapshot=seller_order.subtotal,
        discount_amount_snapshot=seller_order.discount_total,
        commission_amount_snapshot=seller_order.commission_total,
        net_amount_snapshot=seller_order.seller_net_total,
        eligible_at=seller_order.payout_eligible_at,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_db_rejects_illegal_lifecycle_and_fact_mutation(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    session.commit()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text(
            "UPDATE seller_payouts SET net_total = net_total + 1 WHERE id = :id"
        ), {"id": payout.id})
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text(
            "UPDATE seller_payout_items SET eligible_at = eligible_at + interval '1 second' "
            "WHERE payout_id = :id"
        ), {"id": payout.id})
    session.execute(text(
        "UPDATE seller_payouts SET status = 'ON_HOLD' WHERE id = :id"
    ), {"id": payout.id})
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text(
            "UPDATE seller_payouts SET status = 'PAID', paid_at = :paid, "
            "external_reference = 'DIRECT' WHERE id = :id"
        ), {"id": payout.id, "paid": EXECUTED_AT})


def test_db_protects_all_payout_facts_and_one_way_release(session: Session):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    payout_id = payout.id
    payout_number = payout.payout_number
    session.commit()
    payout_mutations = (
        "payout_number = payout_number || '-X'",
        "store_id = gen_random_uuid()",
        "bank_account_version_id = gen_random_uuid()",
        "scheduled_for = scheduled_for + interval '1 second'",
        "gross_sales_total = gross_sales_total + 1",
        "destination_bank_name_snapshot = 'Changed'",
        "destination_account_last4 = '0000'",
        "created_at = created_at + interval '1 second'",
    )
    for assignment in payout_mutations:
        with pytest.raises(IntegrityError):
            with session.bind.begin() as connection:
                connection.execute(text(
                    f"UPDATE seller_payouts SET {assignment} WHERE id = :id"
                ), {"id": payout_id})
    item_mutations = (
        "payout_id = gen_random_uuid()",
        "seller_order_id = gen_random_uuid()",
        "gross_amount_snapshot = gross_amount_snapshot + 1",
        "eligible_at = eligible_at + interval '1 second'",
    )
    for assignment in item_mutations:
        with pytest.raises(IntegrityError):
            with session.bind.begin() as connection:
                connection.execute(text(
                    f"UPDATE seller_payout_items SET {assignment} WHERE payout_id = :id"
                ), {"id": payout_id})
    cancel_seller_payout(
        session, payout_number=payout_number, cancelled_at=EXECUTED_AT
    )
    session.commit()
    for assignment in (
        "released_at = NULL",
        "released_at = released_at + interval '1 second'",
    ):
        with pytest.raises(IntegrityError):
            with session.bind.begin() as connection:
                connection.execute(text(
                    f"UPDATE seller_payout_items SET {assignment} WHERE payout_id = :id"
                ), {"id": payout_id})


def test_concurrent_schedule_assigns_each_order_once(
    session: Session, session_factory, concurrent_runner
):
    base, _order, _seller_order = _delivered_order(session)
    store_id = base.store_id
    session.commit()
    def worker(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            result = schedule_seller_payout(
                worker_session, store_id=store_id,
                cycle_date=CYCLE_DATE, now=EXECUTED_AT,
            )
            worker_session.commit()
            return result.payout.id
        except (SellerPayoutEligibilityError, IntegrityError):
            worker_session.rollback()
            return None
        finally:
            worker_session.close()
    results, errors = concurrent_runner([worker, worker])
    assert not errors
    assert len([result for result in results if result is not None]) == 1
    assert session.scalar(select(func.count(SellerPayoutItem.seller_order_id))) == 1


def test_store_scheduling_lock_forces_one_complete_cohort(
    session: Session, session_factory, engine,
):
    base = create_catalog_and_stock(session)
    _bank_onboarding(session, base)
    seller_orders = [_delivered_order_for_base(session, base)[1] for _ in range(3)]
    store_id = base.store_id
    session.commit()

    owner = session_factory()
    contender_started = Event()
    contender_pid: list[int] = []
    _lock_store_payout_scheduling(owner, store_id)

    def contender():
        worker_session = session_factory()
        try:
            contender_pid.append(worker_session.scalar(select(func.pg_backend_pid())))
            contender_started.set()
            try:
                result = schedule_seller_payout(
                    worker_session, store_id=store_id,
                    cycle_date=CYCLE_DATE, now=EXECUTED_AT,
                )
                worker_session.commit()
                return result.payout.id
            except SellerPayoutEligibilityError:
                worker_session.rollback()
                return None
        finally:
            worker_session.close()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(contender)
    try:
        assert contender_started.wait(timeout=5)
        _wait_for_lock_wait(engine, contender_pid[0])
        winner = schedule_seller_payout(
            owner, store_id=store_id,
            cycle_date=CYCLE_DATE, now=EXECUTED_AT,
        )
        owner.commit()
        assert future.result(timeout=10) is None
    finally:
        owner.rollback()
        owner.close()
        executor.shutdown(wait=True, cancel_futures=True)

    session.expire_all()
    active_items = session.scalars(select(SellerPayoutItem).where(
        SellerPayoutItem.released_at.is_(None)
    )).all()
    assert {item.seller_order_id for item in active_items} == {
        row.id for row in seller_orders
    }
    assert {item.payout_id for item in active_items} == {winner.payout.id}


def test_temporarily_locked_seller_order_is_not_skipped(
    session: Session, session_factory, engine,
):
    base = create_catalog_and_stock(session)
    _bank_onboarding(session, base)
    seller_orders = [_delivered_order_for_base(session, base)[1] for _ in range(2)]
    store_id = base.store_id
    session.commit()

    locker = session_factory()
    locker.scalar(
        select(SellerOrder)
        .where(SellerOrder.id == seller_orders[0].id)
        .with_for_update()
    )
    scheduler_started = Event()
    scheduler_pid: list[int] = []

    def scheduler():
        worker_session = session_factory()
        try:
            scheduler_pid.append(worker_session.scalar(select(func.pg_backend_pid())))
            scheduler_started.set()
            result = schedule_seller_payout(
                worker_session, store_id=store_id,
                cycle_date=CYCLE_DATE, now=EXECUTED_AT,
            )
            worker_session.commit()
            return result.payout.id
        finally:
            worker_session.close()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(scheduler)
    try:
        assert scheduler_started.wait(timeout=5)
        _wait_for_lock_wait(engine, scheduler_pid[0])
        with engine.connect() as observer:
            assert observer.execute(
                text("SELECT count(*) FROM seller_payouts")
            ).scalar_one() == 0
        locker.commit()
        payout_id = future.result(timeout=10)
    finally:
        locker.rollback()
        locker.close()
        executor.shutdown(wait=True, cancel_futures=True)

    session.expire_all()
    items = session.scalars(select(SellerPayoutItem).where(
        SellerPayoutItem.payout_id == payout_id,
        SellerPayoutItem.released_at.is_(None),
    )).all()
    assert {item.seller_order_id for item in items} == {
        row.id for row in seller_orders
    }


def test_concurrent_pay_vs_cancel_has_one_terminal_winner(
    session: Session, session_factory, concurrent_runner
):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    payout_number = payout.payout_number
    payout_id = payout.id
    session.commit()

    def pay(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            mark_seller_payout_paid(
                worker_session, payout_number=payout_number,
                external_reference="RACE-PAY-CANCEL", paid_at=EXECUTED_AT,
            )
            worker_session.commit()
            return "PAID"
        except (SellerPayoutTransitionError, IntegrityError):
            worker_session.rollback()
            return "REJECTED"
        finally:
            worker_session.close()

    def cancel(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            cancel_seller_payout(
                worker_session, payout_number=payout_number,
                cancelled_at=EXECUTED_AT,
            )
            worker_session.commit()
            return "CANCELLED"
        except (SellerPayoutTransitionError, IntegrityError):
            worker_session.rollback()
            return "REJECTED"
        finally:
            worker_session.close()

    results, errors = concurrent_runner([pay, cancel])
    assert not errors
    assert results.count("REJECTED") == 1
    session.expire_all()
    final = session.get(SellerPayout, payout_id)
    assert final.status in {SellerPayoutStatus.PAID, SellerPayoutStatus.CANCELLED}
    assert not (final.status == SellerPayoutStatus.PAID and final.items[0].released_at)


def test_concurrent_pay_vs_hold_has_one_winner(
    session: Session, session_factory, concurrent_runner
):
    base, _order, _seller_order = _delivered_order(session)
    payout = _schedule(session, base)
    payout_number = payout.payout_number
    payout_id = payout.id
    session.commit()

    def pay(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            mark_seller_payout_paid(
                worker_session, payout_number=payout_number,
                external_reference="RACE-PAY-HOLD", paid_at=EXECUTED_AT,
            )
            worker_session.commit()
            return "PAID"
        except SellerPayoutTransitionError:
            worker_session.rollback()
            return "REJECTED"
        finally:
            worker_session.close()

    def hold(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            hold_seller_payout(worker_session, payout_number=payout_number)
            worker_session.commit()
            return "ON_HOLD"
        except SellerPayoutTransitionError:
            worker_session.rollback()
            return "REJECTED"
        finally:
            worker_session.close()

    results, errors = concurrent_runner([pay, hold])
    assert not errors
    assert results.count("REJECTED") == 1
    session.expire_all()
    assert session.get(SellerPayout, payout_id).status in {
        SellerPayoutStatus.PAID, SellerPayoutStatus.ON_HOLD
    }


def test_external_reference_unique_under_concurrency(
    session: Session, session_factory, concurrent_runner
):
    first, _order, _seller_order = _delivered_order(session)
    second, _order, _seller_order = _delivered_order(session)
    payouts = (_schedule(session, first), _schedule(session, second))
    numbers = tuple(payout.payout_number for payout in payouts)
    session.commit()

    def worker(number):
        def operation(barrier):
            worker_session = session_factory()
            try:
                barrier.wait()
                mark_seller_payout_paid(
                    worker_session, payout_number=number,
                    external_reference="UNIQUE-RACE", paid_at=EXECUTED_AT,
                )
                worker_session.commit()
                return "PAID"
            except SellerPayoutTransitionError:
                worker_session.rollback()
                return "REJECTED"
            finally:
                worker_session.close()
        return operation

    results, errors = concurrent_runner([worker(numbers[0]), worker(numbers[1])])
    assert not errors
    assert sorted(results) == ["PAID", "REJECTED"]
    session.expire_all()
    assert session.scalar(select(func.count(SellerPayout.id)).where(
        SellerPayout.external_reference == "UNIQUE-RACE"
    )) == 1


def test_cli_calendar_preview_schedule_and_lifecycle(app, session: Session):
    base, _order, _seller_order = _delivered_order(session)
    store_code = session.get(Store, base.store_id).public_code
    session.commit()
    runner = app.test_cli_runner()
    calendar_result = runner.invoke(args=["seller-payouts", "calendar", "--month", "2026-08"])
    assert calendar_result.exit_code == 0
    assert "America/Guayaquil" in calendar_result.output
    preview = runner.invoke(args=[
        "seller-payouts", "preview-cycle", "--date", "2026-08-15", "--store", store_code,
    ])
    assert preview.exit_code == 0, preview.output
    dry = runner.invoke(args=[
        "seller-payouts", "schedule-cycle", "--date", "2026-08-15", "--store", store_code,
    ])
    assert dry.exit_code == 0 and "DRY RUN" in dry.output
    applied = runner.invoke(args=[
        "seller-payouts", "schedule-cycle", "--date", "2026-08-15",
        "--store", store_code, "--apply",
    ])
    assert applied.exit_code == 0, applied.output
    payout = session.scalar(select(SellerPayout))
    held = runner.invoke(args=["seller-payouts", "hold", "--payout", payout.payout_number])
    assert held.exit_code == 0, held.output
    resumed = runner.invoke(args=["seller-payouts", "resume", "--payout", payout.payout_number])
    assert resumed.exit_code == 0, resumed.output
