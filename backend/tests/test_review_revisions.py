from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    ProductReview,
    ProductReviewRevision,
    ReviewModerationDecision,
    ReviewModerationTerm,
)
from app.models.enums import ProductReviewStatus
from app.services.product_reviews import (
    StagedProductReviewImage,
    create_product_review,
    moderate_product_review,
    resubmit_product_review,
)
from app.services.review_moderation import (
    bootstrap_review_moderation_terms,
    normalize_review_text,
)
from tests.factories import (
    create_catalog_and_stock,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


def _staged(tmp_path: Path, name: str, sort_order: int = 0):
    public_id = uuid.uuid4().hex
    storage_key = f"reviews/{public_id}.png"
    return StagedProductReviewImage(
        temporary_path=tmp_path / f"staged-{name}.png",
        storage_key=storage_key,
        public_id=public_id,
        original_filename=f"{name}.png",
        media_type="image/png",
        size_bytes=8,
        width=2,
        height=2,
        sort_order=sort_order,
    )


def _review_context(session, *, body: str, image=None):
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    result = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=5,
        body=body,
        staged_images=(image,) if image else (),
        min_body_length=10,
        max_body_length=2000,
    )
    return base, ready, session.get(ProductReview, result.review_id)


def test_rejected_review_resubmit_preserves_revision_and_decision_history(session):
    bootstrap_review_moderation_terms(session)
    term = ReviewModerationTerm(
        stable_key="test-revision-blocked",
        language_code="es",
        pattern="frase fixture bloqueada",
        normalized_pattern=normalize_review_text("frase fixture bloqueada"),
        match_mode="PHRASE",
        category_code="TEST_BLOCKED",
        severity="HIGH",
        is_active=True,
    )
    session.add(term)
    session.flush()
    base, _ready, review = _review_context(
        session,
        body="Comentario positivo con frase fixture bloqueada.",
    )
    revision_one_id = review.current_revision_id
    moderate_product_review(
        session=session,
        review_id=review.id,
        decision="reject",
        moderator_user_id=base.operator_id,
        reason="El comentario contiene lenguaje no permitido.",
    )

    result = resubmit_product_review(
        session=session,
        review_id=review.id,
        user_id=base.buyer_id,
        rating=1,
        body="El producto llegó roto y no lo recomiendo.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    session.flush()

    revisions = list(session.scalars(
        select(ProductReviewRevision)
        .where(ProductReviewRevision.review_id == review.id)
        .order_by(ProductReviewRevision.revision_number)
    ))
    decisions = list(session.scalars(
        select(ReviewModerationDecision)
        .where(ReviewModerationDecision.review_id == review.id)
        .order_by(ReviewModerationDecision.created_at)
    ))
    assert result.status == ProductReviewStatus.PUBLISHED
    assert review.current_revision_id != revision_one_id
    assert [(item.revision_number, item.body) for item in revisions] == [
        (1, "Comentario positivo con frase fixture bloqueada."),
        (2, "El producto llegó roto y no lo recomiendo."),
    ]
    assert [(item.action, item.source) for item in decisions] == [
        ("REJECT", "MANUAL"),
        ("APPROVE", "AUTOMATIC"),
    ]


def test_only_current_revision_images_can_be_served(session, app, tmp_path):
    app.config["PRODUCT_REVIEW_UPLOAD_DIR"] = str(tmp_path)
    bootstrap_review_moderation_terms(session)
    image_a = _staged(tmp_path, "old")
    base, _ready, review = _review_context(
        session,
        body="Primera versión con una imagen para revisión manual.",
        image=image_a,
    )
    old_revision_id = review.current_revision_id
    moderate_product_review(
        session=session,
        review_id=review.id,
        decision="reject",
        moderator_user_id=base.operator_id,
        reason="La imagen adjunta no cumple las políticas.",
    )

    image_b = _staged(tmp_path, "current")
    resubmit_product_review(
        session=session,
        review_id=review.id,
        user_id=base.buyer_id,
        rating=4,
        body="Segunda versión corregida con evidencia actual.",
        staged_images=(image_b,),
        min_body_length=10,
        max_body_length=2000,
    )
    moderate_product_review(
        session=session,
        review_id=review.id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )
    (tmp_path / image_a.storage_key).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / image_a.storage_key).write_bytes(b"old-image")
    (tmp_path / image_b.storage_key).write_bytes(b"new-image")
    session.commit()

    with app.test_client() as client:
        old_response = client.get(f"/resenas/imagenes/{image_a.public_id}")
        current_response = client.get(f"/resenas/imagenes/{image_b.public_id}")

    assert review.current_revision_id != old_revision_id
    assert old_response.status_code == 404
    assert current_response.status_code == 200
