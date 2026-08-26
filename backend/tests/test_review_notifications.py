from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import ProductReview, ReviewNotificationOutbox
from app.models.enums import ProductReviewStatus
from app.services.product_reviews import create_product_review, moderate_product_review
from app.services.review_notifications import dispatch_review_notifications
from tests.factories import (
    create_catalog_and_stock,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


def _rejected_review(session):
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    created = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=2,
        body="Comentario pendiente para una decisión manual de prueba.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    moderate_product_review(
        session=session,
        review_id=created.review_id,
        decision="reject",
        moderator_user_id=base.operator_id,
        reason="El comentario necesita una corrección antes de publicarse.",
    )
    return session.get(ProductReview, created.review_id)


def test_rejection_creates_one_durable_outbox_event(session):
    review = _rejected_review(session)

    events = list(session.scalars(
        select(ReviewNotificationOutbox).where(
            ReviewNotificationOutbox.review_id == review.id
        )
    ))
    assert review.status == ProductReviewStatus.REJECTED
    assert len(events) == 1
    assert events[0].status == "PENDING"


def test_mail_failure_is_retryable_and_never_reverts_rejection(session, monkeypatch):
    review = _rejected_review(session)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    def fail(_message):
        raise RuntimeError("provider unavailable with no secret payload")

    monkeypatch.setattr("app.services.review_notifications.mail_service.send", fail)
    first = dispatch_review_notifications(session, now=now)
    event = session.scalar(
        select(ReviewNotificationOutbox).where(
            ReviewNotificationOutbox.review_id == review.id
        )
    )
    assert first == {"claimed": 1, "sent": 0, "failed": 1}
    assert event.status == "RETRY"
    assert event.attempts == 1
    assert event.last_error == "RuntimeError"
    assert review.status == ProductReviewStatus.REJECTED

    sent = []
    monkeypatch.setattr(
        "app.services.review_notifications.mail_service.send",
        lambda message: sent.append(message),
    )
    second = dispatch_review_notifications(session, now=event.next_attempt_at)
    assert second == {"claimed": 1, "sent": 1, "failed": 0}
    assert event.status == "SENT"
    assert event.attempts == 2
    assert len(sent) == 1
    assert "necesita cambios" in sent[0].subject

    replay = dispatch_review_notifications(session, now=event.sent_at)
    assert replay == {"claimed": 0, "sent": 0, "failed": 0}
