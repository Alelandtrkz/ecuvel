from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import PaymentAttempt, PaymentNotificationOutbox
from app.models.enums import PaymentNotificationStatus, PaymentStatus
from app.services.mail import mail_service
from app.services.transactional_mail import (
    build_mail_action_url,
    payment_approved_mail,
    payment_rejected_mail,
)


PAYMENT_NOTIFICATION_MAX_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_message(event: PaymentNotificationOutbox):
    attempt = event.payment_attempt
    order = event.order
    user = event.user
    if (
        attempt.order_id != order.id
        or order.buyer_id != user.id
        or not user.email
    ):
        raise ValueError("PAYMENT_NOTIFICATION_RECIPIENT_INVALID")

    action_url = build_mail_action_url(
        "storefront.order_detail", order_number=order.order_number
    )
    common = {
        "to": user.email,
        "action_url": action_url,
        "buyer_name": user.full_name,
        "payment_public_code": attempt.public_code,
        "order_number": order.order_number,
        "amount": f"{attempt.amount:.2f}",
        "currency": attempt.currency,
        "idempotency_key": f"payment-notification/{event.id}",
    }
    if event.event_type == "PAYMENT_APPROVED":
        if attempt.status != PaymentStatus.APPROVED:
            raise ValueError("PAYMENT_NOTIFICATION_STATE_INVALID")
        return payment_approved_mail(**common)
    if event.event_type == "PAYMENT_REJECTED":
        proof = attempt.proof
        if attempt.status != PaymentStatus.REJECTED or proof is None:
            raise ValueError("PAYMENT_NOTIFICATION_STATE_INVALID")
        public_reason = (proof.rejection_reason or "").strip()
        if not public_reason:
            raise ValueError("PAYMENT_NOTIFICATION_PUBLIC_REASON_MISSING")
        return payment_rejected_mail(
            **common,
            public_reason=public_reason,
        )
    raise ValueError("PAYMENT_NOTIFICATION_EVENT_UNSUPPORTED")


def dispatch_payment_notifications(
    session: Session, *, limit: int = 50, now: datetime | None = None
) -> dict[str, int]:
    effective_now = now or utcnow()
    rows = list(
        session.scalars(
            select(PaymentNotificationOutbox)
            .options(
                joinedload(PaymentNotificationOutbox.user),
                joinedload(PaymentNotificationOutbox.order),
                joinedload(PaymentNotificationOutbox.payment_attempt).joinedload(
                    PaymentAttempt.proof
                ),
            )
            .where(
                or_(
                    PaymentNotificationOutbox.status
                    == PaymentNotificationStatus.PENDING.value,
                    and_(
                        PaymentNotificationOutbox.status
                        == PaymentNotificationStatus.RETRY.value,
                        or_(
                            PaymentNotificationOutbox.next_attempt_at.is_(None),
                            PaymentNotificationOutbox.next_attempt_at
                            <= effective_now,
                        ),
                    ),
                )
            )
            .order_by(
                PaymentNotificationOutbox.created_at,
                PaymentNotificationOutbox.id,
            )
            .with_for_update(of=PaymentNotificationOutbox, skip_locked=True)
            .limit(max(1, min(limit, 500)))
        )
    )
    sent = failed = 0
    for event in rows:
        try:
            mail_service.send(_build_message(event))
        except Exception as exc:  # La entrega nunca revierte la decisión financiera.
            event.attempts += 1
            event.last_error = type(exc).__name__[:500]
            if (
                isinstance(exc, ValueError)
                or event.attempts >= PAYMENT_NOTIFICATION_MAX_ATTEMPTS
            ):
                event.status = PaymentNotificationStatus.FAILED.value
                event.next_attempt_at = None
            else:
                event.status = PaymentNotificationStatus.RETRY.value
                event.next_attempt_at = effective_now + timedelta(
                    minutes=min(60, 2 ** min(event.attempts, 6))
                )
            failed += 1
        else:
            event.attempts += 1
            event.status = PaymentNotificationStatus.SENT.value
            event.sent_at = effective_now
            event.next_attempt_at = None
            event.last_error = None
            sent += 1
    session.flush()
    return {"claimed": len(rows), "sent": sent, "failed": failed}
