from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from flask import render_template_string
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import InventoryBalance, Product, ProductVariant, SellerOffer
from app.models.enums import OfferStatus
from app.storefront import _build_product_gallery_images
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client

    db.session.remove()


def _catalog_entities(
    session: Session,
    base: BaseData,
) -> tuple[Product, ProductVariant, SellerOffer]:
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    variant = session.get(ProductVariant, offer.variant_id)
    assert variant is not None
    product = session.get(Product, variant.product_id)
    assert product is not None
    return product, variant, offer


def _create_product_offer(
    session: Session,
    base: BaseData,
    *,
    category_id: uuid.UUID,
    title: str,
    status: OfferStatus = OfferStatus.ACTIVE,
) -> Product:
    token = uuid.uuid4().hex[:12]
    product = Product(
        category_id=category_id,
        title=title,
        slug=f"product-{token}",
        description=f"Description for {title}",
        is_active=True,
    )
    session.add(product)
    session.flush()

    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"SKU-{token}",
        attributes={"color": "blue"},
        is_active=True,
    )
    session.add(variant)
    session.flush()

    offer = SellerOffer(
        store_id=base.store_id,
        variant_id=variant.id,
        seller_sku=f"SELL-{token}",
        currency="USD",
        price=Decimal("12.00"),
        commission_rate=Decimal("0.00"),
        status=status,
    )
    session.add(offer)
    session.flush()
    return product


def _render_gallery(app, images, product_name: str = "Product Test") -> str:
    with app.test_request_context():
        return render_template_string(
            """
            {% from "components/product_gallery.html" import product_gallery %}
            {{ product_gallery(images, product_name, placeholder_url) }}
            """,
            images=images,
            product_name=product_name,
            placeholder_url=(
                "/static/images/placeholders/product-placeholder.svg"
            ),
        )


def test_product_detail_returns_200(client, session: Session):
    base = create_catalog_and_stock(session, stock=8)
    product, variant, offer = _catalog_entities(session, base)
    product.title = "Cámara de prueba"
    product.description = "Descripción real del producto."
    variant.title = "Variante principal"
    variant.attributes = {"resolution": "4 MP"}
    offer.price = Decimal("45.00")
    offer.compare_at_price = Decimal("60.00")
    session.commit()

    product_updated_at = product.updated_at
    balance_snapshot = (8, 0, 0)
    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<h1" in body
    assert "Cámara de prueba" in body
    assert "$45.00" in body
    assert "Store " in body
    assert "Category Test" in body
    assert f'/productos/{product.slug}' in client.get("/").get_data(
        as_text=True
    )

    session.expire_all()
    refreshed_product = session.get(Product, product.id)
    assert refreshed_product is not None
    assert refreshed_product.updated_at == product_updated_at

    balance = session.get(InventoryBalance, base.balance_id)
    assert balance is not None
    assert (
        balance.on_hand_quantity,
        balance.reserved_quantity,
        balance.blocked_quantity,
    ) == balance_snapshot


def test_product_detail_returns_404_for_unknown_product(client):
    response = client.get("/productos/producto-desconocido")

    assert response.status_code == 404
    assert "No encontramos este producto" in response.get_data(as_text=True)


def test_product_detail_hides_or_rejects_product_without_active_offer(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    product, _variant, offer = _catalog_entities(session, base)
    offer.status = OfferStatus.PAUSED
    session.commit()

    response = client.get(f"/productos/{product.slug}")

    assert response.status_code == 404
    assert "$10.00" not in response.get_data(as_text=True)


def test_product_detail_excludes_current_product_from_recommendations(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    product, _variant, _offer = _catalog_entities(session, base)
    recommended = _create_product_offer(
        session,
        base,
        category_id=product.category_id,
        title="Recommended Product",
    )
    hidden = _create_product_offer(
        session,
        base,
        category_id=product.category_id,
        title="Hidden Product",
        status=OfferStatus.PAUSED,
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    recommendations = response.get_data(as_text=True).split(
        'id="product-recommendations"',
        maxsplit=1,
    )[1].split("</section>", maxsplit=1)[0]

    assert response.status_code == 200
    assert product.title not in recommendations
    assert recommended.title in recommendations
    assert hidden.title not in recommendations
    assert f"/productos/{recommended.slug}" in recommendations


def test_product_detail_renders_empty_review_state(client, session: Session):
    base = create_catalog_and_stock(session)
    product, _variant, _offer = _catalog_entities(session, base)
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "0.0" in body
    assert "0 opiniones" in body
    assert "Este producto todavía no tiene reseñas." in body


def test_product_gallery_uses_single_placeholder_when_no_images_exist(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session)
    product, _variant, _offer = _catalog_entities(session, base)
    session.commit()

    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.count("data-gallery-main-image") == 1
    assert body.count("data-gallery-dialog-image") == 1
    assert f"Imagen de {product.title} próximamente" in body
    assert "data-gallery-thumbnail" not in body
    assert "data-gallery-previous" not in body
    assert "data-gallery-next" not in body
    assert "data-gallery-counter" not in body
    assert 'src=""' not in body


def test_product_gallery_renders_single_image_without_navigation(app):
    images = _build_product_gallery_images(
        "Product Test",
        ["/media/product-one.jpg"],
    )

    body = _render_gallery(app, images)

    assert body.count("data-gallery-main-image") == 1
    assert 'src="/media/product-one.jpg"' in body
    assert "data-gallery-thumbnail" not in body
    assert "data-gallery-previous" not in body
    assert "data-gallery-next" not in body
    assert "data-gallery-counter" not in body
    assert "data-gallery-open" in body


def test_product_gallery_renders_all_product_images(app):
    image_urls = [
        "/media/product-one.jpg",
        "/media/product-two.jpg",
        "/media/product-three.jpg",
    ]
    images = _build_product_gallery_images("Product Test", image_urls)

    body = _render_gallery(app, images)

    assert body.count("data-gallery-thumbnail") == 3
    assert 'data-gallery-index="0"' in body
    assert 'data-gallery-index="1"' in body
    assert 'data-gallery-index="2"' in body
    assert "data-gallery-previous" in body
    assert "data-gallery-next" in body
    assert "1 / 3" in body
    for image_url in image_urls:
        assert image_url in body


def test_product_gallery_deduplicates_repeated_image_urls():
    images = _build_product_gallery_images(
        "Product Test",
        [
            "/media/product-one.jpg",
            " /media/product-one.jpg ",
            None,
            "",
            "/media/product-two.jpg",
        ],
    )

    assert [image.url for image in images] == [
        "/media/product-one.jpg",
        "/media/product-two.jpg",
    ]
    assert images[0].is_primary is True
    assert images[1].is_primary is False


def test_product_gallery_uses_accessible_alt_text(app):
    images = _build_product_gallery_images(
        "Cámara Hikvision Demo",
        ["/media/front.jpg", "/media/side.jpg"],
    )

    body = _render_gallery(app, images, "Cámara Hikvision Demo")

    assert images[0].alt == "Cámara Hikvision Demo, vista 1"
    assert images[1].alt == "Cámara Hikvision Demo, vista 2"
    assert 'alt="Cámara Hikvision Demo, vista 1"' in body
