from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    Order,
    OrderItem,
    Product,
    ProductReview,
    ProductReviewRevision,
    ReviewModerationAssessment,
    ReviewModerationSignal,
    User,
)
from app.models.enums import ProductReviewStatus
from app.services.product_reviews import public_reviewer_label, public_review_variant_label


def _page(value) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def get_admin_reviews_page(
    session: Session,
    *,
    tab: str = "manual",
    q: str = "",
    rating: str = "",
    risk: str = "",
    category: str = "",
    page: int | str = 1,
    page_size: int = 20,
) -> dict:
    status = {
        "approved": ProductReviewStatus.PUBLISHED,
        "not-published": ProductReviewStatus.REJECTED,
    }.get(tab, ProductReviewStatus.PENDING_REVIEW)
    tab = tab if tab in {"manual", "approved", "not-published"} else "manual"
    filters = [ProductReview.status == status]
    clean_q = " ".join((q or "").split())[:120]
    if clean_q:
        pattern = f"%{clean_q}%"
        filters.append(or_(
            Product.title.ilike(pattern),
            OrderItem.product_name_snapshot.ilike(pattern),
            OrderItem.seller_sku_snapshot.ilike(pattern),
            Order.order_number.ilike(pattern),
            User.full_name.ilike(pattern),
            ProductReview.body.ilike(pattern),
        ))
    try:
        clean_rating = int(rating)
    except (TypeError, ValueError):
        clean_rating = 0
    if 1 <= clean_rating <= 5:
        filters.append(ProductReview.rating == clean_rating)
    else:
        clean_rating = 0
    clean_risk = (risk or "").strip().upper()
    if clean_risk in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        filters.append(ProductReview.current_revision_id.in_(
            select(ReviewModerationAssessment.revision_id).where(
                ReviewModerationAssessment.risk == clean_risk
            )
        ))
    else:
        clean_risk = ""
    clean_category = (category or "").strip().upper()[:50]
    if clean_category:
        filters.append(ProductReview.current_revision_id.in_(
            select(ReviewModerationSignal.revision_id).where(
                ReviewModerationSignal.category_code == clean_category
            )
        ))
    base = (
        select(ProductReview)
        .join(User, User.id == ProductReview.user_id)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Product, Product.id == ProductReview.product_id)
    )
    count_base = (
        select(func.count(ProductReview.id))
        .join(User, User.id == ProductReview.user_id)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Product, Product.id == ProductReview.product_id)
    )
    total = int(session.scalar(count_base.where(*filters)) or 0)
    total_pages = max(1, math.ceil(total / page_size))
    current_page = min(_page(page), total_pages)
    reviews = list(session.scalars(
        base.options(
            joinedload(ProductReview.user),
            joinedload(ProductReview.order),
            joinedload(ProductReview.product),
            joinedload(ProductReview.order_item),
            joinedload(ProductReview.current_revision).selectinload(ProductReviewRevision.images),
        )
        .where(*filters)
        .order_by(ProductReview.created_at.desc(), ProductReview.id.desc())
        .offset((current_page - 1) * page_size)
        .limit(page_size)
    ).unique())
    revision_ids = [review.current_revision_id for review in reviews if review.current_revision_id]
    assessments = {
        assessment.revision_id: assessment
        for assessment in session.scalars(
            select(ReviewModerationAssessment)
            .options(selectinload(ReviewModerationAssessment.signals))
            .where(ReviewModerationAssessment.revision_id.in_(revision_ids or [uuid.uuid4()]))
        )
    }
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    total_30 = int(session.scalar(
        select(func.count(ProductReview.id)).where(ProductReview.created_at >= start)
    ) or 0)
    pending = int(session.scalar(
        select(func.count(ProductReview.id)).where(
            ProductReview.status == ProductReviewStatus.PENDING_REVIEW
        )
    ) or 0)
    flagged = int(session.scalar(
        select(func.count(ReviewModerationAssessment.id)).join(
            ProductReview, ProductReview.current_revision_id == ReviewModerationAssessment.revision_id
        ).where(
            ProductReview.status == ProductReviewStatus.PENDING_REVIEW,
            ReviewModerationAssessment.outcome == "FLAG",
        )
    ) or 0)
    category_options = tuple(session.scalars(
        select(ReviewModerationSignal.category_code)
        .distinct()
        .order_by(ReviewModerationSignal.category_code)
    ))
    rows = []
    for review in reviews:
        assessment = assessments.get(review.current_revision_id)
        images = tuple(review.current_revision.images) if review.current_revision else ()
        rows.append({
            "review": review,
            "assessment": assessment,
            "buyer_label": public_reviewer_label(review.user),
            "product_name": review.order_item.product_name_snapshot or review.product.title,
            "variant_label": public_review_variant_label(review.order_item.variant_snapshot),
            "sku": review.order_item.seller_sku_snapshot,
            "product_image_url": review.order_item.image_url_snapshot,
        })
    return {
        "rows": rows,
        "tab": tab,
        "q": clean_q,
        "rating": clean_rating,
        "risk": clean_risk,
        "category": clean_category,
        "category_options": category_options,
        "page": current_page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "kpis": {"total_30": total_30, "pending": pending, "flagged": flagged},
    }


def get_admin_review_detail(session: Session, review_id: uuid.UUID) -> dict | None:
    review = session.scalar(
        select(ProductReview)
        .options(
            joinedload(ProductReview.user),
            joinedload(ProductReview.order),
            joinedload(ProductReview.product),
            joinedload(ProductReview.order_item),
            joinedload(ProductReview.current_revision).selectinload(ProductReviewRevision.images),
        )
        .where(ProductReview.id == review_id)
    )
    if review is None or review.current_revision is None:
        return None
    assessment = session.scalar(
        select(ReviewModerationAssessment)
        .options(selectinload(ReviewModerationAssessment.signals))
        .where(ReviewModerationAssessment.revision_id == review.current_revision_id)
    )
    return {
        "review": review,
        "revision": review.current_revision,
        "assessment": assessment,
        "signals": tuple(assessment.signals) if assessment else (),
        "buyer_label": public_reviewer_label(review.user),
        "product_name": review.order_item.product_name_snapshot or review.product.title,
        "variant_label": public_review_variant_label(review.order_item.variant_snapshot),
        "sku": review.order_item.seller_sku_snapshot,
        "images": tuple(review.current_revision.images),
    }
