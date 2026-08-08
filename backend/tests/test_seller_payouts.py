from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Order,
    PaymentAttempt,
    SellerOrder,
    SellerPayout,
    SellerPayoutItem,
    Store,
    StoreOnboarding,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
    SellerPayoutStatus,
    StoreOnboardingStage,
    StoreOnboardingStatus,
)
from app.services.seller_payouts import (
    SellerPayoutEligibilityError,
    SellerPayoutTransitionError,
    eligible_seller_orders,
    mark_seller_payout_paid,
    schedule_seller_payout,
)
from tests.factories import (
    create_catalog_and_stock,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


def _bank_onboarding(session: Session, base):
    onboarding = StoreOnboarding(
        user_id=base.operator_id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=session.get(Store, base.store_id).name,
        bank_account_owner="Tienda Test",
        bank_account_number="001-0000-4567",
        bank_name="Pichincha",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    return onboarding


def _approved_payment(session: Session, order: Order, approved_at: datetime):
    order.status = OrderStatus.FULFILLING
    attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=f"payout-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=approved_at + timedelta(hours=1),
        approved_at=approved_at,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _delivered_order(session: Session):
    base = create_catalog_and_stock(session)
    _bank_onboarding(session, base)
    ready = create_ready_for_pickup_order(session, base, [2])
    order = session.get(Order, ready.order_id)
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order.id)
    )
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    _approved_payment(session, order, datetime.now(timezone.utc) - timedelta(days=20))
    handover_ready_order(session, base, ready)
    session.flush()
    return base, order, seller_order


def test_handover_sets_delivery_and_exact_fifteen_day_eligibility(session: Session):
    base, order, seller_order = _delivered_order(session)
    assert seller_order.status == SellerOrderStatus.COMPLETED
    assert seller_order.delivered_at is not None
    assert seller_order.payout_eligible_at == seller_order.delivered_at + timedelta(days=15)
    delivered_at = seller_order.delivered_at
    eligible_at = seller_order.payout_eligible_at
    package_codes = tuple(
        item.package.package_code for item in seller_order.items
    )
    from app.services.fulfillment import handover_order_packages

    replay = handover_order_packages(
        session=session,
        order_number=order.order_number,
        scanned_codes=package_codes,
        actor_user_id=base.operator_id,
    )
    assert replay.replayed
    assert seller_order.delivered_at == delivered_at
    assert seller_order.payout_eligible_at == eligible_at
    assert not eligible_seller_orders(
        session,
        store_id=base.store_id,
        now=seller_order.delivered_at + timedelta(days=14, hours=23),
    )
    eligible = eligible_seller_orders(
        session,
        store_id=base.store_id,
        now=seller_order.delivered_at + timedelta(days=15),
    )
    assert [row.seller_order_id for row in eligible] == [seller_order.id]


def test_rejected_refund_and_not_delivered_orders_are_not_eligible(session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    seller_order.requires_refund_resolution = True
    assert not eligible_seller_orders(session, store_id=base.store_id, now=now)
    seller_order.requires_refund_resolution = False
    seller_order.decision_status = SellerOrderDecisionStatus.REJECTED
    assert not eligible_seller_orders(session, store_id=base.store_id, now=now)
    seller_order.decision_status = SellerOrderDecisionStatus.APPROVED
    seller_order.delivered_at = None
    seller_order.payout_eligible_at = None
    assert not eligible_seller_orders(session, store_id=base.store_id, now=now)


def test_schedule_snapshots_totals_bank_and_prevents_double_payout(session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    scheduled_for = now + timedelta(days=2)
    result = schedule_seller_payout(
        session,
        store_id=base.store_id,
        scheduled_for=scheduled_for,
        now=now,
    )
    session.flush()
    payout = result.payout
    assert payout.status == SellerPayoutStatus.SCHEDULED
    assert payout.payout_number.startswith("PAY-")
    assert payout.net_total == seller_order.seller_net_total
    assert payout.net_total == (
        payout.gross_sales_total - payout.discount_total - payout.commission_total
    )
    assert payout.destination_bank_name_snapshot == "Pichincha"
    assert payout.destination_account_last4 == "4567"
    item = session.scalar(
        select(SellerPayoutItem).where(
            SellerPayoutItem.seller_order_id == seller_order.id
        )
    )
    assert item.net_amount_snapshot == seller_order.seller_net_total
    with pytest.raises(SellerPayoutEligibilityError):
        schedule_seller_payout(
            session,
            store_id=base.store_id,
            scheduled_for=scheduled_for,
            now=now,
        )


def test_mark_paid_is_idempotent_only_for_identical_real_data(session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    payout = schedule_seller_payout(
        session,
        store_id=base.store_id,
        scheduled_for=now + timedelta(days=1),
        now=now,
        status=SellerPayoutStatus.ON_HOLD,
    ).payout
    assert payout.status == SellerPayoutStatus.ON_HOLD
    paid_at = now + timedelta(days=1, hours=2)
    first = mark_seller_payout_paid(
        session,
        payout_number=payout.payout_number,
        external_reference="BANK-8891",
        paid_at=paid_at,
    )
    assert not first.replayed
    assert first.payout.status == SellerPayoutStatus.PAID
    second = mark_seller_payout_paid(
        session,
        payout_number=payout.payout_number,
        external_reference="BANK-8891",
        paid_at=paid_at,
    )
    assert second.replayed
    with pytest.raises(SellerPayoutTransitionError):
        mark_seller_payout_paid(
            session,
            payout_number=payout.payout_number,
            external_reference="DIFFERENT",
            paid_at=paid_at,
        )


def test_payout_items_sum_exactly_to_parent(session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    payout = schedule_seller_payout(
        session,
        store_id=base.store_id,
        scheduled_for=now + timedelta(days=1),
        now=now,
    ).payout
    total = session.scalar(
        select(func.sum(SellerPayoutItem.net_amount_snapshot)).where(
            SellerPayoutItem.payout_id == payout.id
        )
    )
    assert total == payout.net_total


def test_concurrent_schedule_cannot_pay_the_same_seller_order_twice(
    session: Session, session_factory, concurrent_runner
):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    store_id = base.store_id
    session.commit()

    def worker(barrier):
        worker_session = session_factory()
        try:
            barrier.wait()
            result = schedule_seller_payout(
                worker_session,
                store_id=store_id,
                scheduled_for=now + timedelta(days=1),
                now=now,
            )
            worker_session.commit()
            return result.payout.id
        except SellerPayoutEligibilityError:
            worker_session.rollback()
            return None
        finally:
            worker_session.close()

    results, errors = concurrent_runner([worker, worker])
    assert not errors
    assert len([result for result in results if result is not None]) == 1
    assert session.scalar(select(func.count(SellerPayoutItem.seller_order_id))) == 1


def test_financial_snapshots_cannot_be_edited_after_schedule(session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = seller_order.payout_eligible_at + timedelta(seconds=1)
    payout = schedule_seller_payout(
        session,
        store_id=base.store_id,
        scheduled_for=now + timedelta(days=1),
        now=now,
    ).payout
    session.flush()
    payout.net_total = Decimal("1.00")
    with pytest.raises(ValueError, match="inmutables"):
        session.flush()


def test_payout_cli_previews_schedules_and_marks_real_payment(app, session: Session):
    base, _order, seller_order = _delivered_order(session)
    now = datetime.now(timezone.utc)
    seller_order.delivered_at = now - timedelta(days=16)
    seller_order.payout_eligible_at = now - timedelta(days=1)
    store_code = session.get(Store, base.store_id).public_code
    session.commit()
    runner = app.test_cli_runner()

    preview = runner.invoke(
        args=["seller-payouts", "preview", "--store", store_code]
    )
    assert preview.exit_code == 0
    assert seller_order.seller_order_number in preview.output
    scheduled_for = now + timedelta(days=1)
    scheduled = runner.invoke(
        args=[
            "seller-payouts",
            "schedule",
            "--store",
            store_code,
            "--scheduled-for",
            scheduled_for.isoformat(),
        ]
    )
    assert scheduled.exit_code == 0, scheduled.output
    session.expire_all()
    payout = session.scalar(select(SellerPayout))
    paid_at = scheduled_for + timedelta(hours=2)
    paid = runner.invoke(
        args=[
            "seller-payouts",
            "mark-paid",
            "--payout",
            payout.payout_number,
            "--reference",
            "BANK-CLI-1",
            "--paid-at",
            paid_at.isoformat(),
        ]
    )
    assert paid.exit_code == 0, paid.output
    session.expire_all()
    assert session.get(SellerPayout, payout.id).status == SellerPayoutStatus.PAID
