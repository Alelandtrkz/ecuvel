from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import ProductReview, ReviewNotificationOutbox
from app.models.enums import ReviewNotificationStatus
from app.services.mail import OutgoingMail, mail_service


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dispatch_review_notifications(
    session: Session, *, limit: int = 50, now: datetime | None = None
) -> dict[str, int]:
    effective_now = now or utcnow()
    rows = list(session.scalars(
        select(ReviewNotificationOutbox)
        .options(
            joinedload(ReviewNotificationOutbox.user),
            joinedload(ReviewNotificationOutbox.review).joinedload(ProductReview.product),
        )
        .where(
            ReviewNotificationOutbox.status.in_((
                ReviewNotificationStatus.PENDING.value,
                ReviewNotificationStatus.FAILED.value,
                ReviewNotificationStatus.RETRY.value,
            )),
            or_(
                ReviewNotificationOutbox.next_attempt_at.is_(None),
                ReviewNotificationOutbox.next_attempt_at <= effective_now,
            ),
        )
        .order_by(ReviewNotificationOutbox.created_at, ReviewNotificationOutbox.id)
        # Lock only the outbox rows. Eager-loading the optional relationships
        # adds outer joins, which PostgreSQL cannot lock as a whole.
        .with_for_update(of=ReviewNotificationOutbox, skip_locked=True)
        .limit(max(1, min(limit, 500)))
    ))
    sent = failed = 0
    for event in rows:
        if event.event_type != "REVIEW_REJECTED":
            event.status = ReviewNotificationStatus.FAILED.value
            event.last_error = "EVENT_TYPE_UNSUPPORTED"
            failed += 1
            continue
        try:
            mail_service.send(OutgoingMail(
                to=event.user.email,
                subject="Tu reseña necesita cambios para publicarse en ECUVEL",
                body=(
                    f"Tu reseña de {event.review.product.title} no fue publicada.\n\n"
                    f"Motivo: {event.review.public_rejection_reason}\n\n"
                    "Ingresa a tus pedidos en ECUVEL para editarla y reenviarla."
                ),
            ))
        except Exception as exc:  # El correo nunca revierte la moderación.
            event.attempts += 1
            event.status = ReviewNotificationStatus.RETRY.value
            event.next_attempt_at = effective_now + timedelta(minutes=min(60, 2 ** event.attempts))
            event.last_error = type(exc).__name__[:500]
            failed += 1
        else:
            event.attempts += 1
            event.status = ReviewNotificationStatus.SENT.value
            event.sent_at = effective_now
            event.next_attempt_at = None
            event.last_error = None
            sent += 1
    session.flush()
    return {"claimed": len(rows), "sent": sent, "failed": failed}
