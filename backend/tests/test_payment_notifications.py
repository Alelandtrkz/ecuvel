from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import (
    AdminAuditEvent,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentNotificationOutbox,
    PaymentProof,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PaymentNotificationStatus,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    StaffRole,
)
from app.services.mail import mail_service
from app.services.payment_notifications import (
    PAYMENT_NOTIFICATION_MAX_ATTEMPTS,
    dispatch_payment_notifications,
)
from app.services.payment_proofs import (
    InvalidPaymentProofTransitionError,
    review_payment_proof,
)
from app.services.pending_payments import expire_pending_bank_transfer_payment
from tests.test_payment_proofs import _assign_staff_profile, _graph, _submit


pytestmark = pytest.mark.integration


def _review(
    session,
    tmp_path,
    *,
    decision: str,
    reason: str | None = None,
    notes: str | None = None,
):
    base, order_id, order_number, _reservations, attempt, admin = _graph(session)
    _assign_staff_profile(session, admin, role=StaffRole.SUPER_ADMIN)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    result = review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision=decision,
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
        reason_code="OTHER" if decision == "reject" else None,
        reason=reason,
        notes=notes,
    )
    return base, order_id, order_number, attempt, admin, proof_result, result


@pytest.mark.parametrize(
    ("decision", "event_type", "payment_status", "order_status"),
    (
        ("approve", "PAYMENT_APPROVED", PaymentStatus.APPROVED, OrderStatus.CONFIRMED),
        ("reject", "PAYMENT_REJECTED", PaymentStatus.REJECTED, OrderStatus.CANCELLED),
    ),
)
def test_real_decision_creates_one_atomic_outbox_event(
    session,
    tmp_path,
    decision,
    event_type,
    payment_status,
    order_status,
):
    base, order_id, _order_number, attempt, _admin, proof_result, result = _review(
        session,
        tmp_path,
        decision=decision,
        reason="El comprobante no es válido" if decision == "reject" else None,
        notes="Nota privada del operador",
    )

    event = session.scalar(select(PaymentNotificationOutbox))
    assert event is not None
    assert event.payment_attempt_id == attempt.id
    assert event.order_id == order_id
    assert event.user_id == base.buyer_id
    assert event.event_type == event_type
    assert event.status == PaymentNotificationStatus.PENDING.value
    assert event.attempts == 0
    assert event.next_attempt_at is None
    assert event.last_error is None
    assert event.sent_at is None
    assert result.payment_status == payment_status
    assert session.get(Order, order_id).status == order_status
    assert session.get(PaymentProof, proof_result.proof_id).status == result.proof_status
    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 1

    replay = review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision=decision,
        reviewer_user_id=_admin.id,
        storage_root=tmp_path,
        reason_code="OTHER" if decision == "reject" else None,
        reason="El comprobante no es válido" if decision == "reject" else None,
    )
    assert replay.replayed
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 1
    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 1


def test_opposite_decision_does_not_create_another_event(session, tmp_path):
    _base, _order_id, _order_number, _attempt, admin, proof_result, _result = _review(
        session,
        tmp_path,
        decision="approve",
    )

    with pytest.raises(InvalidPaymentProofTransitionError):
        review_payment_proof(
            session=session,
            proof_id=proof_result.proof_id,
            decision="reject",
            reviewer_user_id=admin.id,
            storage_root=tmp_path,
            reason_code="OTHER",
            reason="Decisión opuesta",
        )

    events = list(session.scalars(select(PaymentNotificationOutbox)))
    assert [event.event_type for event in events] == ["PAYMENT_APPROVED"]


def test_decision_rollback_removes_financial_and_notification_changes(
    session, tmp_path
):
    base, order_id, _order_number, _reservations, attempt, admin = _graph(session)
    _assign_staff_profile(session, admin, role=StaffRole.SUPER_ADMIN)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    session.commit()

    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="approve",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
    )
    session.rollback()

    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING
    assert session.get(PaymentProof, proof_result.proof_id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 0
    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 0


def test_dispatch_approved_sends_once_with_expected_content(
    session, tmp_path
):
    base, _order_id, order_number, attempt, _admin, _proof, _result = _review(
        session,
        tmp_path,
        decision="approve",
    )
    session.commit()

    result = dispatch_payment_notifications(session, limit=10)
    session.commit()

    assert result == {"claimed": 1, "sent": 1, "failed": 0}
    assert len(mail_service.outbox) == 1
    message = mail_service.outbox[0]
    event = session.scalar(select(PaymentNotificationOutbox))
    assert message.to == event.user.email
    assert message.subject == "Hemos confirmado tu pago de ECUVEL"
    assert message.tags == {"mail_type": "PAYMENT_APPROVED"}
    assert message.idempotency_key == f"payment-notification/{event.id}"
    for value in (attempt.public_code, order_number, "USD 20.00", f"/pedidos/{order_number}"):
        assert value in message.text_body
        assert value in message.html_body
    assert event.status == PaymentNotificationStatus.SENT.value
    assert event.attempts == 1
    assert event.sent_at is not None
    assert dispatch_payment_notifications(session, limit=10) == {
        "claimed": 0,
        "sent": 0,
        "failed": 0,
    }
    assert len(mail_service.outbox) == 1
    assert event.user_id == base.buyer_id


def test_dispatch_rejected_uses_only_public_reason_and_excludes_private_data(
    session, tmp_path
):
    public_reason = "El comprobante no permite verificar el pago"
    private_note = "INTERNAL-NOTE-DO-NOT-SEND"
    _base, _order_id, _order_number, attempt, admin, proof_result, _result = _review(
        session,
        tmp_path,
        decision="reject",
        reason=public_reason,
        notes=private_note,
    )
    attempt.provider_reference = "BANK-PRIVATE-REFERENCE"
    session.commit()

    dispatch_payment_notifications(session, limit=10)
    session.commit()

    assert len(mail_service.outbox) == 1
    message = mail_service.outbox[0]
    proof = session.get(PaymentProof, proof_result.proof_id)
    combined = f"{message.subject}\n{message.text_body}\n{message.html_body}"
    assert message.tags == {"mail_type": "PAYMENT_REJECTED"}
    assert public_reason in combined
    for private_value in (
        private_note,
        proof.storage_key,
        proof.sha256,
        proof.rejection_reason_code,
        attempt.provider_reference,
        admin.email,
        admin.full_name,
    ):
        assert private_value not in combined


@pytest.mark.parametrize(
    (
        "decision",
        "payment_status",
        "proof_status",
        "order_status",
        "seller_order_status",
        "reservation_status",
        "audit_action",
    ),
    (
        (
            "approve",
            PaymentStatus.APPROVED,
            PaymentProofStatus.APPROVED,
            OrderStatus.CONFIRMED,
            SellerOrderStatus.CONFIRMED,
            ReservationStatus.CONSUMED,
            "PAYMENT_APPROVED",
        ),
        (
            "reject",
            PaymentStatus.REJECTED,
            PaymentProofStatus.REJECTED,
            OrderStatus.CANCELLED,
            SellerOrderStatus.CANCELLED,
            ReservationStatus.RELEASED,
            "PAYMENT_REJECTED",
        ),
    ),
)
def test_dispatch_failure_is_sanitized_retried_and_does_not_revert_decision(
    session,
    tmp_path,
    monkeypatch,
    decision,
    payment_status,
    proof_status,
    order_status,
    seller_order_status,
    reservation_status,
    audit_action,
):
    _base, order_id, _order_number, attempt, _admin, proof_result, _result = _review(
        session,
        tmp_path,
        decision=decision,
        reason=(
            "El comprobante no permite verificar el pago"
            if decision == "reject"
            else None
        ),
    )
    session.commit()
    fixed_now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    original_send = mail_service.send
    attempted_keys = []

    def fail_delivery(message):
        attempted_keys.append(message.idempotency_key)
        raise RuntimeError("SECRET provider response and recipient")

    monkeypatch.setattr(mail_service, "send", fail_delivery)
    failed = dispatch_payment_notifications(session, limit=10, now=fixed_now)
    session.commit()

    event = session.scalar(select(PaymentNotificationOutbox))
    assert failed == {"claimed": 1, "sent": 0, "failed": 1}
    assert event.status == PaymentNotificationStatus.RETRY.value
    assert event.attempts == 1
    assert event.next_attempt_at == fixed_now + timedelta(minutes=2)
    assert event.last_error == "RuntimeError"
    assert "SECRET" not in event.last_error
    assert session.get(PaymentAttempt, attempt.id).status == payment_status
    assert session.get(PaymentProof, proof_result.proof_id).status == proof_status
    assert session.get(Order, order_id).status == order_status
    assert all(
        seller_order.status == seller_order_status
        for seller_order in session.scalars(
            select(SellerOrder).where(SellerOrder.order_id == order_id)
        )
    )
    assert all(
        reservation.status == reservation_status
        for reservation in session.scalars(select(InventoryReservation))
    )
    assert session.scalar(
        select(AdminAuditEvent.action).where(AdminAuditEvent.action == audit_action)
    ) == audit_action
    assert not mail_service.outbox

    monkeypatch.setattr(mail_service, "send", original_send)
    retried = dispatch_payment_notifications(
        session,
        limit=10,
        now=fixed_now + timedelta(minutes=3),
    )
    session.commit()

    assert retried == {"claimed": 1, "sent": 1, "failed": 0}
    assert event.status == PaymentNotificationStatus.SENT.value
    assert event.attempts == 2
    assert event.last_error is None
    assert event.next_attempt_at is None
    assert len(mail_service.outbox) == 1
    assert attempted_keys == [mail_service.outbox[0].idempotency_key]
    assert attempted_keys[0] == f"payment-notification/{event.id}"


def test_dispatch_marks_dead_letter_at_max_attempts_and_never_reclaims_it(
    session, tmp_path, monkeypatch
):
    _review(session, tmp_path, decision="approve")
    session.commit()
    fixed_now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    event = session.scalar(select(PaymentNotificationOutbox))
    event.status = PaymentNotificationStatus.RETRY.value
    event.attempts = PAYMENT_NOTIFICATION_MAX_ATTEMPTS - 1
    event.next_attempt_at = fixed_now
    session.commit()

    attempted_keys = []

    def fail_delivery(message):
        attempted_keys.append(message.idempotency_key)
        raise RuntimeError("temporary provider failure")

    monkeypatch.setattr(mail_service, "send", fail_delivery)
    result = dispatch_payment_notifications(session, limit=10, now=fixed_now)
    session.commit()

    assert result == {"claimed": 1, "sent": 0, "failed": 1}
    assert event.status == PaymentNotificationStatus.FAILED.value
    assert event.attempts == PAYMENT_NOTIFICATION_MAX_ATTEMPTS
    assert event.next_attempt_at is None
    assert event.sent_at is None
    assert attempted_keys == [f"payment-notification/{event.id}"]
    assert dispatch_payment_notifications(
        session,
        limit=10,
        now=fixed_now + timedelta(days=1),
    ) == {"claimed": 0, "sent": 0, "failed": 0}


def test_dispatch_retry_after_uncommitted_success_reuses_provider_key(
    session, tmp_path
):
    _review(session, tmp_path, decision="approve")
    session.commit()
    event = session.scalar(select(PaymentNotificationOutbox))

    first = dispatch_payment_notifications(session, limit=10)
    assert first == {"claimed": 1, "sent": 1, "failed": 0}
    assert len(mail_service.outbox) == 1
    first_key = mail_service.outbox[0].idempotency_key
    session.rollback()  # Simula caída antes de confirmar el estado SENT.

    second = dispatch_payment_notifications(session, limit=10)
    session.commit()

    assert second == {"claimed": 1, "sent": 1, "failed": 0}
    assert len(mail_service.outbox) == 2
    assert first_key == mail_service.outbox[1].idempotency_key
    assert first_key == f"payment-notification/{event.id}"


def test_cli_dispatches_pending_notification(app, session, tmp_path):
    _review(session, tmp_path, decision="approve")
    session.commit()

    response = app.test_cli_runner().invoke(
        args=["payment-notifications", "dispatch", "--limit", "10"]
    )

    assert response.exit_code == 0
    assert "claimed=1 sent=1 failed=0" in response.output
    session.expire_all()
    assert session.scalar(select(PaymentNotificationOutbox.status)) == PaymentNotificationStatus.SENT.value
    assert len(mail_service.outbox) == 1


def test_expiration_does_not_create_payment_notification(session):
    _base, _order_id, _order_number, _reservations, attempt, _admin = _graph(
        session,
        expired=True,
    )

    result = expire_pending_bank_transfer_payment(
        session=session,
        payment_attempt_id=attempt.id,
    )

    assert result.payment_status == PaymentStatus.EXPIRED
    assert session.scalar(select(func.count(PaymentNotificationOutbox.id))) == 0
