from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import OrderItem, Product, ProductReview, ProductVariant, SellerOffer, User
from app.models.enums import ProductReviewStatus, UserStatus
from app.services.product_reviews import (
    ProductReviewDuplicateError,
    ProductReviewEligibilityError,
    create_product_review,
    moderate_product_review,
    published_reviews_for_product,
    review_stats_for_product_ids,
)
from tests.factories import (
    create_catalog_and_stock,
    create_order_items,
    create_ready_for_pickup_order,
    handover_ready_order,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app, tmp_path):
    app.config["PRODUCT_REVIEW_UPLOAD_DIR"] = str(tmp_path)
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _prepare_buyer(session, buyer_id, *, email="cliente@test.local") -> User:
    buyer = session.get(User, buyer_id)
    assert buyer is not None
    buyer.email = email
    buyer.email_normalized = email.casefold()
    buyer.password_hash = generate_password_hash("correct horse battery staple")
    buyer.full_name = "Cliente Ecuvel"
    buyer.status = UserStatus.ACTIVE
    buyer.email_verified_at = datetime.now(timezone.utc)
    buyer.is_active = True
    session.flush()
    return buyer


def _login(client, email="cliente@test.local"):
    return client.post(
        "/iniciar-sesion",
        data={
            "email": email,
            "password": "correct horse battery staple",
            "next": "/",
        },
        follow_redirects=False,
    )


def _product(session, offer_id) -> Product:
    product = session.scalar(
        select(Product)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .join(SellerOffer, SellerOffer.variant_id == ProductVariant.id)
        .where(SellerOffer.id == offer_id)
    )
    assert product is not None
    return product


def _png_file(name="review.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer, name


def test_service_rejects_order_item_not_delivered(session):
    base = create_catalog_and_stock(session)
    _order_id, order_number, item_ids = create_order_items(session, base, [1])

    with pytest.raises(ProductReviewEligibilityError):
        create_product_review(
            session=session,
            order_number=order_number,
            order_item_id=item_ids[0],
            user_id=base.buyer_id,
            rating=5,
            body="Producto correcto y bien recibido.",
            staged_images=(),
            min_body_length=10,
            max_body_length=2000,
        )


def test_service_creates_pending_review_once_for_delivered_item(session):
    base = create_catalog_and_stock(session)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)

    result = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=4,
        body="Llegó en buen estado y funciona correctamente.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )

    review = session.get(ProductReview, result.review_id)
    assert review is not None
    assert review.status == ProductReviewStatus.PENDING_REVIEW

    with pytest.raises(ProductReviewDuplicateError):
        create_product_review(
            session=session,
            order_number=ready.order_number,
            order_item_id=ready.order_item_ids[0],
            user_id=base.buyer_id,
            rating=5,
            body="Intento duplicado del mismo artículo.",
            staged_images=(),
            min_body_length=10,
            max_body_length=2000,
        )


def test_moderation_publishes_review_and_public_stats(session):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    created = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=base.buyer_id,
        rating=5,
        body="Excelente producto, compra verificada y entrega correcta.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )

    result = moderate_product_review(
        session=session,
        review_id=created.review_id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )
    replay = moderate_product_review(
        session=session,
        review_id=created.review_id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )

    assert result.status == ProductReviewStatus.PUBLISHED
    assert replay.replayed is True
    stats = review_stats_for_product_ids(session, [product.id])[product.id]
    assert stats.count == 1
    assert stats.average == pytest.approx(5)
    page = published_reviews_for_product(
        session,
        product_id=product.id,
        page=1,
        page_size=10,
    )
    assert page.reviews[0].body.startswith("Excelente producto")


def test_routes_create_review_with_private_image_and_hide_until_published(client, session, tmp_path):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    buyer = _prepare_buyer(session, base.buyer_id)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    image = _png_file()
    response = client.post(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena",
        data={
            "rating": "5",
            "body": "Muy buen producto, entrega completa y verificada.",
            "images": image,
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    review = session.scalar(select(ProductReview))
    assert review is not None
    assert review.status == ProductReviewStatus.PENDING_REVIEW
    assert len(review.images) == 1

    detail = client.get(f"/productos/{product.slug}")
    assert "Muy buen producto" not in detail.get_data(as_text=True)

    own_image = client.get(f"/resenas/imagenes/{review.images[0].public_id}")
    assert own_image.status_code == 200


def test_published_review_renders_on_product_detail(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    buyer = _prepare_buyer(session, base.buyer_id)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    created = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=buyer.id,
        rating=5,
        body="Texto visible solo después de aprobación manual.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    moderate_product_review(
        session=session,
        review_id=created.review_id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Texto visible solo después de aprobación manual." in body
    assert "Compra verificada" in body


def test_review_routes_require_owner(client, session):
    base = create_catalog_and_stock(session)
    buyer = _prepare_buyer(session, base.buyer_id)
    other = _prepare_buyer(
        session,
        base.operator_id,
        email="otro@test.local",
    )
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    session.commit()

    assert _login(client, other.email).status_code == 302
    response = client.get(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena"
    )
    assert response.status_code == 404


def test_public_review_uses_safe_identity_date_variant_and_horizontal_stars(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    buyer = _prepare_buyer(session, base.buyer_id, email="private-user@test.local")
    buyer.full_name = ""
    ready = create_ready_for_pickup_order(session, base, [1])
    item = session.get(OrderItem, ready.order_item_ids[0])
    assert item is not None
    item.variant_snapshot = {"title": "Azul demo"}
    handover_ready_order(session, base, ready)
    created = create_product_review(
        session=session,
        order_number=ready.order_number,
        order_item_id=ready.order_item_ids[0],
        user_id=buyer.id,
        rating=5,
        body="Comentario público seguro y compacto.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    moderate_product_review(
        session=session,
        review_id=created.review_id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Comprador verificado" in body
    assert "private-user@test.local" not in body
    assert str(buyer.id) not in body
    assert "Compra verificada" in body
    assert "Variante: Azul demo" in body
    assert "review-card__rating" in body
    assert 'aria-label="5 de 5 estrellas"' in body
    assert "jun." in body or "ene." in body or "feb." in body or "mar." in body
    assert "review-card__gallery" not in body


def test_public_review_with_image_renders_gallery_without_private_storage(client, session, tmp_path):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    buyer = _prepare_buyer(session, base.buyer_id)
    ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, ready)
    session.commit()
    assert _login(client, buyer.email).status_code == 302

    response = client.post(
        f"/pedidos/{ready.order_number}/productos/{ready.order_item_ids[0]}/resena",
        data={
            "rating": "4",
            "body": "Incluye una foto limpia para la galería pública.",
            "images": _png_file("galeria.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    review = session.scalar(select(ProductReview))
    assert review is not None
    storage_key = review.images[0].storage_key
    moderate_product_review(
        session=session,
        review_id=review.id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )
    session.commit()

    detail = client.get(f"/productos/{product.slug}")
    body = detail.get_data(as_text=True)

    assert detail.status_code == 200
    assert "review-card__gallery" in body
    assert "data-review-lightbox-item" in body
    assert "data-review-lightbox" in body
    assert f"/resenas/imagenes/{review.images[0].public_id}" in body
    assert storage_key not in body
    assert "storage_key" not in body


def test_public_review_body_is_escaped_and_summary_uses_only_published(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    buyer = _prepare_buyer(session, base.buyer_id)

    published_ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, published_ready)
    published = create_product_review(
        session=session,
        order_number=published_ready.order_number,
        order_item_id=published_ready.order_item_ids[0],
        user_id=buyer.id,
        rating=5,
        body="<script>alert(1)</script> producto correcto.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    moderate_product_review(
        session=session,
        review_id=published.review_id,
        decision="approve",
        moderator_user_id=base.operator_id,
    )

    pending_ready = create_ready_for_pickup_order(session, base, [1])
    handover_ready_order(session, base, pending_ready)
    create_product_review(
        session=session,
        order_number=pending_ready.order_number,
        order_item_id=pending_ready.order_item_ids[0],
        user_id=buyer.id,
        rating=1,
        body="Esta reseña pendiente no debe contar públicamente.",
        staged_images=(),
        min_body_length=10,
        max_body_length=2000,
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "5.0" in body
    assert "1 opinión" in body
    assert "5 estrellas: 1 opinión, 100 %" in body
    assert "1 estrella" in body
    assert 'aria-valuenow="0"' in body
    assert "Esta reseña pendiente" not in body


def test_public_reviews_empty_state_remains_honest(client, session):
    base = create_catalog_and_stock(session)
    product = _product(session, base.offer_id)
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "0.0" in body
    assert "0 opiniones" in body
    assert "Este producto todavía no tiene reseñas." in body
