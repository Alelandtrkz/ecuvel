from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Product,
    ProductReview,
    ProductReviewReply,
    ProductVariant,
    SellerOffer,
    StoreContractAcceptance,
    StoreMember,
    StoreOnboarding,
    User,
)
from app.models.enums import (
    ProductReviewStatus,
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    UserStatus,
)
from app.services.partner_reviews import (
    PartnerReviewConflictError,
    PartnerReviewValidationError,
    get_partner_reviews_page,
    save_partner_review_reply,
)
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _product(session: Session, offer_id) -> Product:
    product = session.scalar(
        select(Product)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .join(SellerOffer, SellerOffer.variant_id == ProductVariant.id)
        .where(SellerOffer.id == offer_id)
    )
    assert product is not None
    return product


def _enable_partner(
    session: Session,
    base,
    *,
    role: StoreMemberRole = StoreMemberRole.OWNER,
    email: str | None = None,
) -> User:
    user = session.get(User, base.operator_id)
    assert user is not None
    user.email = email or f"reviews-{uuid.uuid4().hex[:8]}@test.local"
    user.email_normalized = user.email.casefold()
    user.password_hash = generate_password_hash("safe review password")
    user.email_verified_at = datetime.now(timezone.utc)
    user.status = UserStatus.ACTIVE
    user.is_active = True
    onboarding = StoreOnboarding(
        user_id=user.id,
        store_id=base.store_id,
        status=StoreOnboardingStatus.COMPLETED,
        current_stage=StoreOnboardingStage.PRODUCTS,
        current_step=5,
        store_name=session.get(SellerOffer, base.offer_id).store.name,
        legal_id_number="210049391",
        completed_at=datetime.now(timezone.utc),
    )
    session.add_all(
        [
            onboarding,
            StoreMember(
                store_id=base.store_id,
                user_id=user.id,
                role=role,
                is_active=True,
            ),
        ]
    )
    session.flush()
    session.add(
        StoreContractAcceptance(
            onboarding_id=onboarding.id,
            contract_version="reviews-v1",
            annex_version="reviews-a1",
            status=StoreContractAcceptanceStatus.ACCEPTED,
            accepted_terms=True,
            otp_verified=True,
            accepted_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return user


def _login(client, user: User):
    response = client.post(
        "/iniciar-sesion",
        data={"email": user.email, "password": "safe review password"},
    )
    assert response.status_code == 302


def _store_member_user(session: Session, base, role: StoreMemberRole) -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}",
        email=f"member-{token}@test.local",
        email_normalized=f"member-{token}@test.local",
        password_hash=generate_password_hash("safe review password"),
        full_name=f"Miembro {role.value}",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(
        StoreMember(
            store_id=base.store_id,
            user_id=user.id,
            role=role,
            is_active=True,
        )
    )
    session.flush()
    return user


def _review(
    session: Session,
    base,
    *,
    rating: int = 5,
    body: str = "Comentario verificado de una compra real.",
    status: ProductReviewStatus = ProductReviewStatus.PUBLISHED,
    published_at: datetime | None = None,
) -> ProductReview:
    product = _product(session, base.offer_id)
    order_id, _order_number, item_ids = create_order_items(session, base, [1])
    review = ProductReview(
        user_id=base.buyer_id,
        order_id=order_id,
        order_item_id=item_ids[0],
        product_id=product.id,
        rating=rating,
        body=body,
        status=status,
        published_at=(published_at or datetime.now(timezone.utc))
        if status == ProductReviewStatus.PUBLISHED
        else None,
    )
    session.add(review)
    session.flush()
    return review


def test_partner_reviews_page_uses_store_published_reviews_and_safe_identity(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    buyer = session.get(User, base.buyer_id)
    buyer.full_name = "María Compradora"
    buyer.email = "maria-privada@test.local"
    published = _review(session, base, rating=5, body="Excelente compra y producto de calidad.")
    _review(
        session,
        base,
        rating=1,
        body="Comentario pendiente que la tienda no debe conocer.",
        status=ProductReviewStatus.PENDING_REVIEW,
    )
    foreign = create_catalog_and_stock(session)
    _review(session, foreign, body="Comentario de otra tienda.")
    session.commit()
    _login(client, owner)

    response = client.get("/partners/reviews")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Reseñas y comentarios" in body
    assert "Excelente compra y producto de calidad." in body
    assert "María C." in body
    assert "maria-privada@test.local" not in body
    assert "Comentario pendiente" not in body
    assert "Comentario de otra tienda" not in body
    assert str(published.id) in body
    assert "1 nuevas esta semana" in body
    assert "0%" in body


def test_partner_reviews_filters_search_sort_and_metrics_are_server_side(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    newest = _review(session, base, rating=2, body="Problema especial con el empaque.")
    answered = _review(
        session,
        base,
        rating=5,
        body="Producto excelente y atención correcta.",
        published_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    save_partner_review_reply(
        session,
        user_id=owner.id,
        review_id=answered.id,
        body="Gracias por compartir tu experiencia.",
        expected_updated_at=None,
    )
    session.flush()

    filtered = get_partner_reviews_page(
        session,
        user_id=owner.id,
        query="empaque",
        statuses=["unanswered"],
        ratings=["2", "1"],
        sort="rating_low",
        page=999,
    )

    assert [item.review_id for item in filtered.reviews] == [newest.id]
    assert filtered.total_items == 1
    assert filtered.page == 1
    assert filtered.metrics.total_reviews == 2
    assert filtered.metrics.answered_reviews == 1
    assert filtered.metrics.response_rate == 50
    assert filtered.status_counts == {"unanswered": 1, "answered": 1}
    assert filtered.rating_counts[2] == 1
    assert filtered.rating_counts[5] == 1
    assert filtered.reviews[0].quick_replies[0].startswith("Lamentamos")


def test_partner_reviews_paginate_twenty_rows_and_clamp_out_of_range(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    for index in range(21):
        _review(
            session,
            base,
            rating=(index % 5) + 1,
            body=f"Comentario verificado número {index:02d}.",
        )

    first = get_partner_reviews_page(
        session,
        user_id=owner.id,
        query=None,
        statuses=None,
        ratings=None,
        sort="newest",
        page=1,
    )
    last = get_partner_reviews_page(
        session,
        user_id=owner.id,
        query=None,
        statuses=None,
        ratings=None,
        sort="newest",
        page=999,
    )

    assert len(first.reviews) == 20
    assert first.has_next is True
    assert last.page == 2
    assert len(last.reviews) == 1
    assert last.has_previous is True


def test_reply_route_creates_edits_and_publicly_renders_official_store_response(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    review = _review(session, base)
    product = _product(session, base.offer_id)
    store_name = session.get(SellerOffer, base.offer_id).store.name
    session.commit()
    _login(client, owner)

    created = client.post(
        f"/partners/reviews/{review.id}/reply",
        data={"body": "Gracias por confiar en nuestra tienda.", "expected_updated_at": ""},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    created_payload = created.get_json()

    assert created.status_code == 200
    assert created_payload["created"] is True
    assert created_payload["metrics"]["response_rate"] == 100
    reply = session.scalar(select(ProductReviewReply).where(ProductReviewReply.review_id == review.id))
    assert reply is not None
    assert reply.created_by_user_id == owner.id

    updated = client.post(
        f"/partners/reviews/{review.id}/reply",
        data={
            "body": "Gracias por confiar en nuestra tienda. Esperamos atenderte nuevamente.",
            "expected_updated_at": created_payload["reply"]["version"],
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["reply"]["is_edited"] is True

    client.post("/cerrar-sesion")
    public = client.get(f"/productos/{product.slug}")
    public_body = public.get_data(as_text=True)
    assert public.status_code == 200
    assert f"Respuesta de {store_name}" in public_body
    assert "Esperamos atenderte nuevamente." in public_body
    assert "Editada" in public_body
    assert owner.full_name not in public_body


def test_reply_service_rejects_stale_version_and_invalid_body(session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    review = _review(session, base)
    created = save_partner_review_reply(
        session,
        user_id=owner.id,
        review_id=review.id,
        body="Primera respuesta pública.",
        expected_updated_at=None,
    )
    session.flush()

    with pytest.raises(PartnerReviewConflictError):
        save_partner_review_reply(
            session,
            user_id=owner.id,
            review_id=review.id,
            body="Edición con versión obsoleta.",
            expected_updated_at="2020-01-01T00:00:00+00:00",
        )
    with pytest.raises(PartnerReviewValidationError):
        save_partner_review_reply(
            session,
            user_id=owner.id,
            review_id=review.id,
            body="x" * 501,
            expected_updated_at=created.reply.version,
        )


def test_public_reply_escapes_html(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    review = _review(session, base)
    product = _product(session, base.offer_id)
    save_partner_review_reply(
        session,
        user_id=owner.id,
        review_id=review.id,
        body="Gracias <script>alert('tienda')</script> por tu compra.",
        expected_updated_at=None,
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script>alert('tienda')</script>" not in body
    assert "&lt;script&gt;alert" in body


def test_reply_route_blocks_pending_foreign_and_non_catalog_roles(client, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    pending = _review(session, base, status=ProductReviewStatus.PENDING_REVIEW)
    published = _review(session, base)
    foreign = create_catalog_and_stock(session)
    foreign_review = _review(session, foreign)
    operator = _store_member_user(session, base, StoreMemberRole.ORDER_OPERATOR)
    administrator = _store_member_user(session, base, StoreMemberRole.ADMINISTRATOR)
    session.commit()
    _login(client, owner)

    for review_id in (pending.id, foreign_review.id):
        response = client.post(
            f"/partners/reviews/{review_id}/reply",
            data={"body": "Respuesta no autorizada.", "expected_updated_at": ""},
        )
        assert response.status_code == 404

    client.post("/cerrar-sesion")
    _login(client, operator)
    assert client.get("/partners/reviews").status_code == 302
    assert client.post(
        f"/partners/reviews/{published.id}/reply",
        data={"body": "Operador sin permisos.", "expected_updated_at": ""},
    ).status_code == 404

    client.post("/cerrar-sesion")
    _login(client, administrator)
    allowed = client.post(
        f"/partners/reviews/{published.id}/reply",
        data={"body": "Respuesta válida del administrador.", "expected_updated_at": ""},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    assert allowed.status_code == 200


def test_reply_endpoint_requires_csrf_when_enabled(client, app, session: Session):
    base = create_catalog_and_stock(session)
    owner = _enable_partner(session, base)
    review = _review(session, base)
    session.commit()
    _login(client, owner)

    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            f"/partners/reviews/{review.id}/reply",
            data={"body": "Respuesta sin token CSRF.", "expected_updated_at": ""},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False

    assert response.status_code == 400
    assert session.scalar(select(ProductReviewReply)) is None
