from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from flask import render_template_string
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import Category, InventoryBalance, Product, ProductMedia, ProductVariant, SellerOffer, Store
from app.models.enums import OfferStatus, StoreStatus
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


def _variant_payload(body: str) -> dict:
    matched = re.search(
        r'<script type="application/json" data-product-variant-payload>(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert matched is not None
    return json.loads(matched.group(1))


def test_product_detail_returns_200(client, session: Session):
    base = create_catalog_and_stock(session, stock=8)
    product, variant, offer = _catalog_entities(session, base)
    product.title = "Cámara de prueba"
    product.description = "Descripción real del producto."
    variant.title = "Variante principal"
    variant.attributes = {"resolution": "4 MP"}
    offer.price = Decimal("45.00")
    offer.compare_at_price = Decimal("60.00")
    offer.preparation_time_days = 1
    session.commit()

    product_updated_at = product.updated_at
    balance_snapshot = (8, 0, 0)
    response = client.get(f"/productos/{product.slug}")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<h1" in body
    assert "Cámara de prueba" in body
    assert "$45.00" in body
    assert "Entrega estimada mañana" in body
    assert 'class="purchase-card__delivery"' in body
    assert 'data-lucide="truck"' in body
    assert 'data-variant-delivery-label' in body
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


def test_product_detail_renders_buyer_specs_without_mutating_operational_data(
    client,
    session: Session,
):
    base = create_catalog_and_stock(session, stock=8)
    product, variant, offer = _catalog_entities(session, base)
    category = session.get(Category, product.category_id)
    assert category is not None
    category.code = "ELECTRONICS_PHONES"
    product.brand = "Apple"
    product.model_number = "iPhone 17 Pro Max"
    variant.manufacturer_barcode = "CRI-00000002-000042"
    variant.weight_grams = 500
    variant.attributes = {
        "condition": "NEW",
        "country_origin": "Estados Unidos",
        "tipo_producto": "Smartphone",
        "ram_gb": "8",
        "almacenamiento_gb": "512",
        "pantalla_pulgadas": "6.7",
        "camara_principal_mp": "48",
        "bateria_mah": "5647",
        "warranty": {"type": "Garantía de tienda", "duration": "3", "unit": "meses"},
        "package_contents": ["Teléfono", "Cable USB-C"],
        "highlights": ["Acabado resistente", "<script>alert('d2')</script>"],
        "variant_options": {"color_principal": "Naranja"},
        "unknown_private_key": {"storage_key": "private/secret.jpg"},
    }
    operational_snapshot = {
        "catalog_sku": variant.catalog_sku,
        "seller_sku": offer.seller_sku,
        "combination_key": variant.combination_key,
        "manufacturer_barcode": variant.manufacturer_barcode,
        "attributes": deepcopy(variant.attributes),
    }
    session.commit()

    body = client.get(f"/productos/{product.slug}").get_data(as_text=True)

    assert body.count("Ficha técnica") == 1
    assert body.count(">Especificaciones</h2>") == 1
    assert "Información general" not in body
    assert "Especificaciones técnicas" not in body
    assert "Batería y energía" not in body
    assert re.search(r"<dt>\s*Estado\s*</dt>", body) is None
    assert re.search(r"<dd[^>]*>\s*Nuevo\s*</dd>", body) is None
    assert "País de origen" in body and "Estados Unidos" in body
    assert "8 GB" in body and "512 GB" in body
    assert "6,7 pulgadas" in body and "48 MP" in body and "5647 mAh" in body
    assert "Garantía de tienda · 3 meses" in body
    assert "Contenido del paquete" in body and "Cable USB-C" in body
    assert "Características destacadas" in body and "Acabado resistente" in body
    assert "&lt;script&gt;alert" in body
    assert "<script>alert('d2')</script>" not in body
    assert "SKU del vendedor" not in body
    assert "SKU del catálogo" not in body
    assert "<span>SKU:" not in body
    assert "Variant options" not in body
    assert "Unknown private key" not in body
    assert "private/secret.jpg" not in body
    assert "CRI-00000002-000042" not in body
    assert "[&quot;" not in body and "{&quot;" not in body
    assert 'class="product-specs product-specs--buyer"' in body
    assert "product-specification-section" not in body

    session.expire_all()
    refreshed_variant = session.get(ProductVariant, variant.id)
    refreshed_offer = session.get(SellerOffer, offer.id)
    assert refreshed_variant is not None and refreshed_offer is not None
    assert {
        "catalog_sku": refreshed_variant.catalog_sku,
        "seller_sku": refreshed_offer.seller_sku,
        "combination_key": refreshed_variant.combination_key,
        "manufacturer_barcode": refreshed_variant.manufacturer_barcode,
        "attributes": refreshed_variant.attributes,
    } == operational_snapshot


def test_product_detail_recommendations_use_compact_offer_eta(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=8)
    product, _variant, selected_offer = _catalog_entities(session, base)
    selected_offer.preparation_time_days = 1
    recommended_product = _create_product_offer(
        session,
        base,
        category_id=product.category_id,
        title="Recommended ETA product",
    )
    recommended_offer = session.scalar(
        select(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .where(ProductVariant.product_id == recommended_product.id)
    )
    assert recommended_offer is not None
    recommended_offer.preparation_time_days = 2
    session.add(InventoryBalance(
        offer_id=recommended_offer.id,
        location_id=base.storage_location_id,
        on_hand_quantity=4,
        reserved_quantity=0,
        blocked_quantity=0,
    ))
    session.commit()

    body = client.get(f"/productos/{product.slug}").get_data(as_text=True)
    assert "Entrega estimada mañana" in body
    assert "Recommended ETA product" in body
    assert "Pasado mañana" in body


def test_product_detail_returns_404_for_unknown_product(client):
    response = client.get("/productos/producto-desconocido")

    assert response.status_code == 404
    assert "No encontramos este producto" in response.get_data(as_text=True)


def test_product_detail_legacy_null_keeps_honest_delivery_fallback(
    client, session: Session
):
    base = create_catalog_and_stock(session, stock=3)
    product, _variant, offer = _catalog_entities(session, base)
    assert offer.preparation_time_days is None
    session.commit()
    body = client.get(f"/productos/{product.slug}").get_data(as_text=True)
    assert "Información de entrega próximamente" in body
    assert 'data-lucide="truck"' in body


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


def test_product_detail_variant_selector_stays_in_opened_store(client, session: Session):
    base = create_catalog_and_stock(session, stock=5)
    product, first_variant, first_offer = _catalog_entities(session, base)
    category = session.get(Category, product.category_id)
    assert category is not None
    category.code = "ELECTRONICS_PHONES"
    product.brand = "Marca D3"
    product.model_number = "Modelo D3"
    first_variant.title = "Negro / 128 GB"
    first_offer.preparation_time_days = 1
    first_variant.combination_key = "color_principal=negro|almacenamiento_gb=128"
    first_variant.attributes = {
        "color_principal": "Negro",
        "almacenamiento_gb": "128",
        "tipo_producto": "Smartphone",
        "ram_gb": "8",
        "camara_principal_mp": "12",
        "warranty": {"type": "Garantía de tienda", "duration": "3", "unit": "meses"},
        "highlights": ["Resumen negro"],
        "variant_options": {"color_principal": "Negro", "almacenamiento_gb": "128"},
    }
    product.variant_configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "visual_axis_key": "color_principal",
        "default_combination_key": first_variant.combination_key,
        "axes": [
            {
                "key": "color_principal",
                "label": "Color",
                "is_visual": True,
                "unit": "",
                "values": [
                    {"key": "negro", "label": "Negro", "swatch": "#111827"},
                    {"key": "azul", "label": "Azul", "swatch": "#2563EB"},
                    {"key": "rojo", "label": "Rojo", "swatch": "#DC2626"},
                ],
            },
            {
                "key": "almacenamiento_gb",
                "label": "Almacenamiento",
                "is_visual": False,
                "unit": "GB",
                "values": [{"key": "128", "label": "128", "swatch": None}],
            },
        ],
    }
    second_variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"{first_variant.catalog_sku}-BLUE",
        title="Azul / 128 GB",
        combination_key="color_principal=azul|almacenamiento_gb=128",
        attributes={
            "color_principal": "Azul",
            "almacenamiento_gb": "128",
            "tipo_producto": "Smartphone",
            "ram_gb": "16",
            "camara_principal_mp": "48",
            "warranty": {
                "type": "Garantía de tienda",
                "duration": "12",
                "unit": "meses",
                "responsible": "ECUVEL",
            },
            "package_contents": ["Teléfono azul", "Cable USB-C"],
            "highlights": ["Resumen azul"],
            "variant_options": {"color_principal": "Azul", "almacenamiento_gb": "128"},
        },
        is_active=True,
    )
    session.add(second_variant)
    session.flush()
    second_offer = SellerOffer(
        store_id=base.store_id,
        variant_id=second_variant.id,
        seller_sku=f"{first_offer.seller_sku}-BLUE",
        currency="USD",
        price=Decimal("12.00"),
        commission_rate=Decimal("0.00"),
        preparation_time_days=2,
        status=OfferStatus.ACTIVE,
    )
    sold_out_variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"{first_variant.catalog_sku}-RED",
        title="Rojo / 128 GB",
        combination_key="color_principal=rojo|almacenamiento_gb=128",
        attributes={
            "color_principal": "Rojo",
            "almacenamiento_gb": "128",
            "tipo_producto": "Smartphone",
            "ram_gb": "32",
            "highlights": ["Resumen agotado"],
            "variant_options": {"color_principal": "Rojo", "almacenamiento_gb": "128"},
        },
        is_active=True,
    )
    session.add(sold_out_variant)
    session.flush()
    sold_out_offer = SellerOffer(
        store_id=base.store_id,
        variant_id=sold_out_variant.id,
        seller_sku=f"{first_offer.seller_sku}-RED",
        currency="USD",
        price=Decimal("15.00"),
        commission_rate=Decimal("0.00"),
        preparation_time_days=2,
        status=OfferStatus.ACTIVE,
    )
    other_store = Store(
        public_code=f"STR-{uuid.uuid4().hex[:12]}",
        name="Otra tienda",
        slug=f"otra-{uuid.uuid4().hex[:12]}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    foreign_variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"{first_variant.catalog_sku}-FOREIGN",
        title="Azul extranjero",
        attributes={"color_principal": "Azul", "almacenamiento_gb": "128"},
        is_active=True,
    )
    session.add_all([second_offer, sold_out_offer, other_store, foreign_variant])
    session.flush()
    foreign_offer = SellerOffer(
        store_id=other_store.id,
        variant_id=foreign_variant.id,
        seller_sku=f"{first_offer.seller_sku}-FOREIGN",
        currency="USD",
        price=Decimal("99.00"),
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE,
    )
    session.add_all(
        [
            foreign_offer,
            InventoryBalance(
                offer_id=second_offer.id,
                location_id=base.storage_location_id,
                on_hand_quantity=3,
                reserved_quantity=0,
                blocked_quantity=0,
            ),
        ]
    )
    session.commit()

    response = client.get(f"/productos/{product.slug}?variant={second_variant.catalog_sku}")
    body = response.get_data(as_text=True)
    payload = _variant_payload(body)
    by_sku = {variant["catalog_sku"]: variant for variant in payload["variants"]}

    assert response.status_code == 200
    assert "data-product-variant-selector" in body
    assert second_variant.catalog_sku in body
    assert first_variant.catalog_sku in body
    assert foreign_variant.catalog_sku not in body
    assert f'value="{second_offer.id}" data-variant-offer-id' in body
    assert "Entrega estimada pasado mañana" in body
    assert 'Entrega estimada ma\\u00f1ana' in body
    assert 'Entrega estimada pasado ma\\u00f1ana' in body
    assert f"{product.title} — Azul / 128 GB" in body
    assert payload["selected_catalog_sku"] == second_variant.catalog_sku
    assert [variant["catalog_sku"] for variant in payload["variants"]] == [
        second_variant.catalog_sku,
        first_variant.catalog_sku,
        sold_out_variant.catalog_sku,
    ]
    assert by_sku[first_variant.catalog_sku]["seller_sku"] == first_offer.seller_sku
    assert by_sku[first_variant.catalog_sku]["combination_key"] == first_variant.combination_key
    assert by_sku[first_variant.catalog_sku]["attributes"]["variant_options"] == {
        "color_principal": "Negro",
        "almacenamiento_gb": "128",
    }
    assert by_sku[second_variant.catalog_sku]["offer_id"] == str(second_offer.id)
    assert {item["value"] for item in by_sku[first_variant.catalog_sku]["public_summary"]} >= {
        "8 GB",
    }
    assert {item["value"] for item in by_sku[second_variant.catalog_sku]["public_summary"]} >= {
        "16 GB",
    }
    assert {
        "label": "Garantía",
        "value": "Garantía de tienda · 12 meses",
        "kind": "multiline",
        "list_items": ["Responsable: ECUVEL"],
    } in by_sku[second_variant.catalog_sku]["public_specifications"]
    assert by_sku[second_variant.catalog_sku]["public_seller_highlights"] == [
        "Resumen azul"
    ]
    assert by_sku[sold_out_variant.catalog_sku]["is_available"] is False
    assert by_sku[sold_out_variant.catalog_sku]["max_quantity"] == 0
    assert by_sku[sold_out_variant.catalog_sku]["availability_label"] == "Producto agotado"

    summary_html = body.split("data-product-summary", maxsplit=1)[1].split(
        "</section>", maxsplit=1
    )[0]
    specifications_html = body.split(
        "data-product-specifications", maxsplit=1
    )[1].split('id="product-reviews"', maxsplit=1)[0]
    assert "<dd>16 GB</dd>" in summary_html
    assert "<dd>8 GB</dd>" not in summary_html
    assert "Garantía de tienda · 12 meses" in specifications_html
    assert "Responsable: ECUVEL" in specifications_html
    assert "Teléfono azul" in specifications_html

    added = client.post(
        "/carrito/agregar",
        data={
            "offer_id": str(second_offer.id),
            "quantity": "1",
            "next": f"/productos/{product.slug}?variant={second_variant.catalog_sku}",
            "intent": "buy_now",
        },
    )
    assert added.status_code == 302
    assert added.headers["Location"].endswith("/carrito")
    with client.session_transaction() as browser_session:
        assert browser_session["cart"]["items"][str(second_offer.id)] == {
            "quantity": 1,
            "selected": True,
        }

    invalid = client.get(f"/productos/{product.slug}?variant=SKU-INEXISTENTE")
    invalid_body = invalid.get_data(as_text=True)
    assert invalid.status_code == 200
    assert "SKU-INEXISTENTE" not in invalid_body
    assert invalid_body.count("data-variant-offer-id") == 1


def test_approved_product_media_uses_safe_public_route(client, app, session: Session, tmp_path):
    base = create_catalog_and_stock(session)
    product, _variant, _offer = _catalog_entities(session, base)
    media_root = tmp_path / "catalog-media"
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(media_root)
    storage_key = "approved/product-image.png"
    stored = media_root / storage_key
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"approved-image")
    media = ProductMedia(
        product_id=product.id,
        storage_key=storage_key,
        media_type="image/png",
        size_bytes=len(b"approved-image"),
        position=0,
        is_cover=True,
        is_active=True,
    )
    session.add(media)
    session.commit()

    response = client.get(f"/productos/{product.slug}/media/{media.public_id}")

    assert response.status_code == 200
    assert response.data == b"approved-image"
    assert response.headers["Cache-Control"].startswith("public")
    assert client.get(f"/productos/slug-ajeno/media/{media.public_id}").status_code == 404


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

    assert len(re.findall(r"<button[^>]+data-gallery-thumbnail(?:\s|>)", body)) == 3
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


def _gallery_media(
    *,
    public_id: str,
    sha: str | None,
    thumbnail_sha: str | None = None,
    binding: str | None = "azul",
) -> ProductMedia:
    return ProductMedia(
        product_id=uuid.uuid4(),
        public_id=public_id,
        storage_key=f"products/demo/{public_id}/master.webp",
        media_type="image/webp",
        size_bytes=100,
        width=1000,
        height=800,
        content_sha256=sha,
        thumbnail_storage_key=(
            f"products/demo/{public_id}/thumbnail.webp" if thumbnail_sha else None
        ),
        thumbnail_media_type="image/webp" if thumbnail_sha else None,
        thumbnail_size_bytes=50 if thumbnail_sha else None,
        thumbnail_width=640 if thumbnail_sha else None,
        thumbnail_height=512 if thumbnail_sha else None,
        thumbnail_sha256=thumbnail_sha,
        processing_version=1 if sha else None,
        variant_axis_key="color" if binding else None,
        variant_value_key=binding,
        position=0,
        is_cover=True,
        is_active=True,
    )


def test_product_gallery_media_contract_uses_separate_versioned_derivatives(
    app, tmp_path,
):
    master_sha = hashlib.sha256(b"master").hexdigest()
    thumbnail_sha = hashlib.sha256(b"thumbnail").hexdigest()
    media = _gallery_media(
        public_id="media-contract",
        sha=master_sha,
        thumbnail_sha=thumbnail_sha,
    )
    thumbnail_path = tmp_path / str(media.thumbnail_storage_key)
    thumbnail_path.parent.mkdir(parents=True)
    thumbnail_path.write_bytes(b"thumbnail")
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path)

    with app.test_request_context():
        images = _build_product_gallery_images(
            "Producto Demo",
            [media],
            product_slug="producto-demo",
        )
        body = _render_gallery(app, images, "Producto Demo")

    assert len(images) == 1
    image = images[0]
    assert image.master_url.endswith(f"/media/{media.public_id}?v={master_sha}")
    assert image.thumbnail_url.endswith(
        f"/media/{media.public_id}/thumbnail?v={thumbnail_sha}"
    )
    assert (image.master_width, image.master_height) == (1000, 800)
    assert (image.thumbnail_width, image.thumbnail_height) == (640, 512)
    assert f'src="{image.master_url}"' in body
    assert f'src="{image.thumbnail_url}"' not in body  # one image has no rail
    assert body.count('width="1000" height="800"') == 2
    assert 'width="1200" height="1200"' not in body
    assert str(media.storage_key) not in body


def test_product_gallery_falls_back_to_master_when_thumbnail_is_unavailable(
    app, tmp_path,
):
    master_sha = hashlib.sha256(b"master-only").hexdigest()
    thumbnail_sha = hashlib.sha256(b"missing-thumbnail").hexdigest()
    media = _gallery_media(
        public_id="missing-thumbnail",
        sha=master_sha,
        thumbnail_sha=thumbnail_sha,
    )
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path)

    with app.test_request_context():
        image = _build_product_gallery_images(
            "Producto Demo",
            [media],
            product_slug="producto-demo",
        )[0]

    assert image.thumbnail_url == image.master_url
    assert (image.thumbnail_width, image.thumbnail_height) == (1000, 800)


def test_product_gallery_rail_uses_thumbnail_hash_and_dimensions(app, tmp_path):
    media_rows = []
    for index in range(2):
        master_sha = hashlib.sha256(f"master-{index}".encode()).hexdigest()
        thumbnail_sha = hashlib.sha256(f"thumbnail-{index}".encode()).hexdigest()
        media = _gallery_media(
            public_id=f"rail-{index}",
            sha=master_sha,
            thumbnail_sha=thumbnail_sha,
        )
        thumbnail_path = tmp_path / str(media.thumbnail_storage_key)
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_path.write_bytes(f"thumbnail-{index}".encode())
        media_rows.append(media)
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path)

    with app.test_request_context():
        images = _build_product_gallery_images(
            "Producto Demo",
            media_rows,
            product_slug="producto-demo",
        )
        body = _render_gallery(app, images, "Producto Demo")

    for image in images:
        assert f'src="{image.thumbnail_url}"' in body
        assert f'data-gallery-master-url="{image.master_url}"' in body
    assert body.count('width="640" height="512"') == 2
    assert all(image.thumbnail_url != image.master_url for image in images)


def test_product_gallery_deduplicates_processed_content_without_deleting_rows(
    app, tmp_path,
):
    shared_sha = hashlib.sha256(b"shared-master").hexdigest()
    thumbnail_sha = hashlib.sha256(b"shared-thumbnail").hexdigest()
    duplicate_one = _gallery_media(
        public_id="duplicate-one",
        sha=shared_sha,
        thumbnail_sha=thumbnail_sha,
    )
    duplicate_two = _gallery_media(
        public_id="duplicate-two",
        sha=shared_sha,
        thumbnail_sha=thumbnail_sha,
    )
    distinct = _gallery_media(
        public_id="distinct",
        sha=hashlib.sha256(b"distinct-master").hexdigest(),
        thumbnail_sha=thumbnail_sha,
    )
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path)

    with app.test_request_context():
        images = _build_product_gallery_images(
            "Producto Demo",
            [duplicate_one, duplicate_two, distinct],
            product_slug="producto-demo",
        )
        legacy_images = _build_product_gallery_images(
            "Producto Demo",
            [
                _gallery_media(public_id="legacy-one", sha=None),
                _gallery_media(public_id="legacy-two", sha=None),
            ],
            product_slug="producto-demo",
        )

    assert [image.identity for image in images] == ["duplicate-one", "distinct"]
    assert [image.identity for image in legacy_images] == ["legacy-one", "legacy-two"]
    assert duplicate_one.public_id == "duplicate-one"
    assert duplicate_two.public_id == "duplicate-two"


def test_product_detail_gallery_query_count_is_constant_for_media_rows(
    client,
    engine: Engine,
    session: Session,
):
    base = create_catalog_and_stock(session, stock=8)
    product, _variant, _offer = _catalog_entities(session, base)
    session.commit()

    def measured_get() -> int:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _params, _context, _executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            response = client.get(f"/productos/{product.slug}")
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200
        return len(statements)

    without_media = measured_get()
    for index in range(10):
        session.add(
            ProductMedia(
                product_id=product.id,
                public_id=f"query-media-{index}",
                storage_key=f"query/media-{index}.jpg",
                media_type="image/jpeg",
                size_bytes=100,
                position=index,
                is_cover=index == 0,
                is_active=True,
            )
        )
    session.commit()
    with_media = measured_get()

    for index in range(20):
        variant = ProductVariant(
            product_id=product.id,
            catalog_sku=f"QUERY-VARIANT-{index:02d}",
            title=f"Variant {index}",
            combination_key=f"query={index:02d}",
            attributes={"ram_gb": str(index + 1)},
            is_active=True,
        )
        session.add(variant)
        session.flush()
        offer = SellerOffer(
            store_id=base.store_id,
            variant_id=variant.id,
            seller_sku=f"QUERY-SELLER-{index:02d}",
            currency="USD",
            price=Decimal("25.00"),
            commission_rate=Decimal("0.00"),
            status=OfferStatus.ACTIVE,
        )
        session.add(offer)
        session.flush()
        session.add(InventoryBalance(
            offer_id=offer.id,
            location_id=base.storage_location_id,
            on_hand_quantity=2,
            reserved_quantity=0,
            blocked_quantity=0,
        ))
    session.commit()
    with_variants = measured_get()

    assert without_media == with_media == with_variants == 11

    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
    assert measured_get() == 15


def test_product_gallery_uses_accessible_alt_text(app):
    images = _build_product_gallery_images(
        "Cámara Hikvision Demo",
        ["/media/front.jpg", "/media/side.jpg"],
    )

    body = _render_gallery(app, images, "Cámara Hikvision Demo")

    assert images[0].alt == "Cámara Hikvision Demo, vista 1"
    assert images[1].alt == "Cámara Hikvision Demo, vista 2"
    assert 'alt="Cámara Hikvision Demo, vista 1"' in body
