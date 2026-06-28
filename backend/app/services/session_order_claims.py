from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, User


def normalize_session_order_ids(values: object) -> set[uuid.UUID]:
    result: set[uuid.UUID] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        try:
            result.add(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            continue
    return result


def claim_session_orders(
    *,
    session: Session,
    order_ids: set[uuid.UUID],
    target_user_id: uuid.UUID,
    demo_buyer_email: str,
) -> int:
    if not order_ids:
        return 0
    demo_user = session.scalar(select(User).where(User.email == demo_buyer_email))
    demo_user_id = demo_user.id if demo_user is not None else None
    orders = session.scalars(
        select(Order)
        .where(Order.id.in_(order_ids))
        .order_by(Order.id)
        .with_for_update()
    ).all()
    claimed = 0
    for order in orders:
        if order.buyer_id == target_user_id:
            continue
        if demo_user_id is not None and order.buyer_id == demo_user_id:
            order.buyer_id = target_user_id
            claimed += 1
    session.flush()
    return claimed
