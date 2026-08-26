from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.models import (
    ProductReview,
    ReviewModerationAssessment,
    ReviewModerationDecision,
    ReviewModerationSignal,
    ReviewModerationTerm,
)
from app.models.enums import ProductReviewStatus
from app.services.product_reviews import StagedProductReviewImage, create_product_review
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


def _delivered_review(session, *, rating: int, body: str, images=()):
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    result = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=rating,
        body=body,
        staged_images=tuple(images),
        min_body_length=10,
        max_body_length=2000,
    )
    return base, session.get(ProductReview, result.review_id)


def _term(session, *, key: str, pattern: str, mode="TOKEN", active=True):
    term = ReviewModerationTerm(
        stable_key=key,
        language_code="es",
        pattern=pattern,
        normalized_pattern=normalize_review_text(pattern),
        match_mode=mode,
        category_code="TEST_BLOCKED",
        severity="HIGH",
        is_active=active,
    )
    session.add(term)
    session.flush()
    return term


def test_versioned_bootstrap_is_idempotent_on_a_new_database(session, tmp_path):
    resource = tmp_path / "lexicon.json"
    resource.write_text(json.dumps({
        "version": "test-v1",
        "language": "es",
        "terms": [{
            "stable_key": "fixture.bootstrap",
            "pattern": "término fixture",
            "match_mode": "PHRASE",
            "category_code": "TEST_BLOCKED",
            "severity": "MEDIUM",
        }],
    }), encoding="utf-8")

    first = bootstrap_review_moderation_terms(session, path=resource)
    second = bootstrap_review_moderation_terms(session, path=resource)

    assert (first.created, first.updated, first.unchanged, first.version) == (1, 0, 0, "test-v1")
    assert (second.created, second.updated, second.unchanged, second.version) == (0, 0, 1, "test-v1")
    assert session.scalar(select(ReviewModerationTerm).where(
        ReviewModerationTerm.stable_key == "fixture.bootstrap"
    )).normalized_pattern == "termino fixture"


def test_negative_clean_review_is_automatically_published(session):
    bootstrap_review_moderation_terms(session)

    _base, review = _delivered_review(
        session,
        rating=1,
        body="El producto llegó roto y no lo recomiendo.",
    )

    assessment = session.scalar(
        select(ReviewModerationAssessment).where(
            ReviewModerationAssessment.review_id == review.id
        )
    )
    decision = session.scalar(
        select(ReviewModerationDecision).where(
            ReviewModerationDecision.review_id == review.id
        )
    )
    assert assessment.outcome == "PASS"
    assert assessment.processing_status == "COMPLETED"
    assert decision.action == "APPROVE"
    assert decision.source == "AUTOMATIC"
    assert review.status == ProductReviewStatus.PUBLISHED
    assert review.rating == 1


def test_synthetic_term_flags_positive_review_without_rejecting(session):
    _term(
        session,
        key="term-test-blocked",
        pattern="frase moderada test",
        mode="PHRASE",
    )

    _base, review = _delivered_review(
        session,
        rating=5,
        body="Excelente compra con FRASE MODERADA TEST incluida.",
    )

    assessment = session.scalar(
        select(ReviewModerationAssessment).where(
            ReviewModerationAssessment.review_id == review.id
        )
    )
    signal = session.scalar(
        select(ReviewModerationSignal).where(
            ReviewModerationSignal.review_id == review.id
        )
    )
    assert assessment.outcome == "FLAG"
    assert signal.category_code == "TEST_BLOCKED"
    assert signal.matched_term_id is not None
    assert review.status == ProductReviewStatus.PENDING_REVIEW
    assert session.scalar(
        select(ReviewModerationDecision).where(
            ReviewModerationDecision.review_id == review.id
        )
    ) is None


def test_normalization_boundaries_and_disabled_terms_are_deterministic(session):
    _term(session, key="accent", pattern="término bloqueado", mode="PHRASE")
    _term(session, key="disabled", pattern="otra señal", active=False)

    _base, flagged = _delivered_review(
        session,
        rating=4,
        body="Incluye TERMINO BLOQUEADO en el comentario.",
    )
    assert flagged.status == ProductReviewStatus.PENDING_REVIEW

    # A phrase embedded inside a larger token must not match, and disabled
    # entries never participate in the active lexicon.
    _base, clean = _delivered_review(
        session,
        rating=4,
        body="Dice preterminobloqueadopost y otra señal, sin coincidencia válida.",
    )
    assert clean.status == ProductReviewStatus.PUBLISHED


def test_empty_lexicon_fails_safe_and_keeps_review_pending(session):
    _base, review = _delivered_review(
        session,
        rating=5,
        body="Comentario limpio que no debe publicarse sin reglas activas.",
    )

    assessment = session.scalar(
        select(ReviewModerationAssessment).where(
            ReviewModerationAssessment.review_id == review.id
        )
    )
    assert assessment.processing_status == "FAILED"
    assert assessment.outcome is None
    assert assessment.error_message == "La prevalidación no pudo completarse de forma segura."
    assert review.status == ProductReviewStatus.PENDING_REVIEW


def test_clean_text_with_image_always_requires_manual_review(session, tmp_path):
    bootstrap_review_moderation_terms(session)
    staged = StagedProductReviewImage(
        temporary_path=tmp_path / "staged.png",
        storage_key=f"reviews/{uuid.uuid4().hex}.png",
        public_id=uuid.uuid4().hex,
        original_filename="evidencia.png",
        media_type="image/png",
        size_bytes=64,
        width=8,
        height=8,
        sort_order=0,
    )

    _base, review = _delivered_review(
        session,
        rating=5,
        body="Comentario limpio acompañado por una fotografía válida.",
        images=(staged,),
    )

    assessment = session.scalar(
        select(ReviewModerationAssessment).where(
            ReviewModerationAssessment.review_id == review.id
        )
    )
    signal = session.scalar(
        select(ReviewModerationSignal).where(
            ReviewModerationSignal.review_id == review.id,
            ReviewModerationSignal.surface == "IMAGE",
        )
    )
    assert assessment.outcome == "MANUAL_REQUIRED"
    assert signal.category_code == "IMAGE_REQUIRES_REVIEW"
    assert review.status == ProductReviewStatus.PENDING_REVIEW


@pytest.mark.parametrize(
    "body",
    (
        "Contacta a pruebas@example.com para resolver esta compra.",
        "Visita https://example.test/soporte para resolver la compra.",
        "Mi teléfono para la entrega es +593 99 123 4567.",
    ),
)
def test_personal_data_is_flagged_but_never_auto_rejected(session, body):
    bootstrap_review_moderation_terms(session)
    _base, review = _delivered_review(session, rating=3, body=body)

    assert review.status == ProductReviewStatus.PENDING_REVIEW
    assert session.scalar(
        select(ReviewModerationSignal).where(
            ReviewModerationSignal.review_id == review.id,
            ReviewModerationSignal.category_code == "PERSONAL_DATA",
        )
    ) is not None
    assert session.scalar(
        select(ReviewModerationDecision).where(
            ReviewModerationDecision.review_id == review.id
        )
    ) is None
