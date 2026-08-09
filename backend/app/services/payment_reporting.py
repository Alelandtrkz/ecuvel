from __future__ import annotations

from sqlalchemy import func, select

from app.models import PaymentAttempt
from app.models.enums import PaymentStatus


def approved_payment_dates_subquery():
    """Return one stable approval timestamp per paid order.

    The schema permits multiple payment attempts for one order. Reporting uses
    the first successful approval as the canonical buyer-to-ECUVEL payment so a
    retried or duplicated approved attempt cannot count the order twice.
    """

    return (
        select(
            PaymentAttempt.order_id.label("order_id"),
            func.min(PaymentAttempt.approved_at).label("approved_at"),
        )
        .where(
            PaymentAttempt.status == PaymentStatus.APPROVED,
            PaymentAttempt.approved_at.is_not(None),
        )
        .group_by(PaymentAttempt.order_id)
        .subquery("approved_payments")
    )
