from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    ProductReview,
    ReviewModerationDecision,
    ReviewModerationTerm,
    ReviewNotificationOutbox,
    StaffProfile,
    User,
)
from app.models.enums import (
    ProductReviewStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
)
from app.services.product_reviews import StagedProductReviewImage, create_product_review
from app.services.review_moderation import normalize_review_text
from tests.factories import (
    create_catalog_and_stock,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app, tmp_path):
    app.config["PRODUCT_REVIEW_UPLOAD_DIR"] = str(tmp_path)
    return app.test_client()


def _login(client, user):
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _flagged_review(session, *, with_image=False, tmp_path=None):
    term = ReviewModerationTerm(
        stable_key=f"admin-test-{uuid.uuid4().hex}",
        language_code="es",
        pattern="frase de revisión fixture",
        normalized_pattern=normalize_review_text("frase de revisión fixture"),
        match_mode="PHRASE",
        category_code="TEST_BLOCKED",
        severity="HIGH",
        is_active=True,
    )
    session.add(term)
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    staged = ()
    if with_image:
        public_id = uuid.uuid4().hex
        staged = (StagedProductReviewImage(
            temporary_path=tmp_path / "staged.png",
            storage_key=f"reviews/{public_id}.png",
            public_id=public_id,
            original_filename="review.png",
            media_type="image/png",
            size_bytes=8,
            width=2,
            height=2,
            sort_order=0,
        ),)
    created = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=5,
        body="Excelente producto con frase de revisión fixture.",
        staged_images=staged,
        min_body_length=10,
        max_body_length=2000,
    )
    review = session.get(ProductReview, created.review_id)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    staff.is_active = True
    session.flush()
    return base, staff, review, staged[0] if staged else None


def test_admin_review_listing_uses_real_pending_data_and_permissions(session, client):
    base, staff, review, _image = _flagged_review(session)
    buyer = session.get(User, base.buyer_id)
    session.commit()

    _login(client, buyer)
    assert client.get("/admin/reviews").status_code == 403

    _login(client, staff)
    response = client.get(f"/admin/reviews?tab=manual&detail={review.id}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Moderación de reseñas" in body
    assert "Excelente producto con frase de revisión fixture." in body
    assert "Reglas ECUVEL" in body
    assert "Inteligencia Artificial" not in body


def test_admin_can_approve_and_stale_second_decision_returns_conflict(session, client):
    _base, staff, review, _image = _flagged_review(session)
    revision_id = review.current_revision_id
    review_id = review.id
    session.commit()
    _login(client, staff)

    approved = client.post(
        f"/admin/reviews/{review_id}/decision",
        data={
            "expected_revision_id": str(revision_id),
            "action": "APPROVE",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert approved.status_code == 302

    stale = client.post(
        f"/admin/reviews/{review_id}/decision",
        data={
            "expected_revision_id": str(revision_id),
            "action": "REJECT",
            "reason_code": "OTHER",
            "public_reason": "Decisión tardía.",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    session.expire_all()
    assert stale.status_code == 409
    assert session.get(ProductReview, review_id).status == ProductReviewStatus.PUBLISHED
    decisions = list(session.scalars(
        select(ReviewModerationDecision).where(
            ReviewModerationDecision.review_id == review_id
        )
    ))
    assert len(decisions) == 1
    assert decisions[0].source == "MANUAL"
    assert decisions[0].actor_user_id == staff.id


def test_admin_rejection_requires_reason_and_creates_outbox(session, client):
    _base, staff, review, _image = _flagged_review(session)
    revision_id = review.current_revision_id
    review_id = review.id
    session.commit()
    _login(client, staff)

    missing = client.post(
        f"/admin/reviews/{review_id}/decision",
        data={
            "expected_revision_id": str(revision_id),
            "action": "REJECT",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert missing.status_code == 302
    session.expire_all()
    assert session.get(ProductReview, review_id).status == ProductReviewStatus.PENDING_REVIEW

    rejected = client.post(
        f"/admin/reviews/{review_id}/decision",
        data={
            "expected_revision_id": str(revision_id),
            "action": "REJECT",
            "reason_code": "OTHER",
            "public_reason": "El comentario debe corregirse antes de publicarse.",
            "internal_notes": "Validación de soporte.",
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert rejected.status_code == 302
    session.expire_all()
    current = session.get(ProductReview, review_id)
    assert current.status == ProductReviewStatus.REJECTED
    assert current.rejection_reason_code == "OTHER"
    assert session.scalar(
        select(ReviewNotificationOutbox).where(
            ReviewNotificationOutbox.review_id == review_id
        )
    ) is not None


def test_private_admin_review_image_requires_permission_and_has_no_store_headers(
    session, client, tmp_path
):
    base, staff, review, image = _flagged_review(
        session, with_image=True, tmp_path=tmp_path
    )
    path = tmp_path / image.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"private-image")
    buyer = session.get(User, base.buyer_id)
    denied = User(
        public_code=f"USR-{uuid.uuid4().hex[:10]}",
        email=f"denied-{uuid.uuid4().hex[:8]}@test.local",
        password_hash="test",
        full_name="Operador sin permiso",
        status="ACTIVE",
        is_active=True,
        is_ecuvel_staff=True,
    )
    session.add(denied)
    session.flush()
    session.add(StaffProfile(
        user_id=denied.id,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"ID-{uuid.uuid4().hex[:10]}",
        nationality_code="ECU",
        role=StaffRole.POINT_OPERATOR,
        employment_status=StaffEmploymentStatus.ACTIVE,
        employment_started_at=date.today(),
    ))
    session.commit()

    _login(client, buyer)
    assert client.get(f"/admin/reviews/images/{image.public_id}").status_code == 403
    _login(client, denied)
    assert client.get(f"/admin/reviews/images/{image.public_id}").status_code == 403
    _login(client, staff)
    response = client.get(f"/admin/reviews/images/{image.public_id}")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/admin/reviews/images/unknown-public-id").status_code == 404
