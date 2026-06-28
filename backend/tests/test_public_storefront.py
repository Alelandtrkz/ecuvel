from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select

from app.extensions import db
from app.models import (
    Category,
    InventoryBalance,
    Order,
    PaymentAttempt,
    Product,
    ProductReview,
    ProductVariant,
    SellerOffer,
    Store,
)
from app.models.enums import OfferStatus, ProductReviewStatus, StoreStatus
from tests.factories import create_catalog_and_stock, create_order_items


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    with app.test_client() as test_client:
        yield test_client
    db.session.remove()


def _base_store(session, stock=20):
    base = create_catalog_and_stock(session, stock=stock)
    store = session.get(Store, base.store_id)
    product = _product_for_offer(session, base.offer_id)
    return base, store, product


def _product_for_offer(session, offer_id):
    product = session.scalar(
        select(Product)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .join(SellerOffer, SellerOffer.variant_id == ProductVariant.id)
        .where(SellerOffer.id == offer_id)
    )
    assert product is not None
    return product


def _create_store_product(
    session,
    store,
    *,
    title="Visible product",
    active_offer=True,
    active_product=True,
    active_variant=True,
    active_category=True,
    price=Decimal("12.00"),
):
    token = uuid.uuid4().hex[:10]
    category = Category(
        code=f"CAT-{token}",
        name=f"Category {token}",
        slug=f"category-{token}",
        is_active=active_category,
        sort_order=1,
    )
    product = Product(
        category_id=None,
        title=title,
        slug=f"product-{token}",
        is_active=active_product,
    )
    session.add(category)
    session.flush()
    product.category_id = category.id
    session.add(product)
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        catalog_sku=f"SKU-{token}",
        title=f"Variant {token}",
        attributes={},
        is_active=active_variant,
    )
    session.add(variant)
    session.flush()
    offer = SellerOffer(
        store_id=store.id,
        variant_id=variant.id,
        seller_sku=f"SELL-{token}",
        currency="USD",
        price=price,
        commission_rate=Decimal("0.00"),
        status=OfferStatus.ACTIVE if active_offer else OfferStatus.PAUSED,
    )
    session.add(offer)
    session.flush()
    return product, offer


def _add_review(session, base, product, *, rating=5, status=ProductReviewStatus.PUBLISHED):
    order_id, _order_number, item_ids = create_order_items(session, base, [1])
    review = ProductReview(
        user_id=base.buyer_id,
        order_id=order_id,
        order_item_id=item_ids[0],
        product_id=product.id,
        rating=rating,
        body="Reseña pública de prueba.",
        status=status,
        published_at=datetime.now(timezone.utc)
        if status == ProductReviewStatus.PUBLISHED
        else None,
    )
    session.add(review)
    session.flush()
    return review


def test_public_store_page_renders_active_store_and_dialog_chips(client, session):
    _base, store, _product = _base_store(session)
    session.commit()

    response = client.get(f"/tiendas/{store.slug}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert store.name in html
    assert f'href="/tiendas/{store.slug}/informacion"' in html
    assert f'data-dialog-url="/tiendas/{store.slug}/informacion?modal=1"' in html
    assert 'aria-haspopup="dialog"' in html
    assert f'href="/tiendas/{store.slug}/calificacion"' in html
    assert f'href="/tiendas/{store.slug}/productos/resumen"' in html
    assert html.count("data-store-dialog") >= 1
    assert "Catálogo de la tienda" in html
    assert "Product Test" in html


def test_unknown_and_suspended_stores_return_404(client, session):
    _base, store, _product = _base_store(session)
    store.status = StoreStatus.SUSPENDED
    session.commit()

    assert client.get("/tiendas/desconocida").status_code == 404
    assert client.get(f"/tiendas/{store.slug}").status_code == 404


def test_store_modal_fragments_and_fallbacks_reuse_public_content(client, session):
    _base, store, _product = _base_store(session)
    session.commit()

    fragment = client.get(f"/tiendas/{store.slug}/informacion?modal=1")
    fallback = client.get(f"/tiendas/{store.slug}/informacion")

    fragment_html = fragment.get_data(as_text=True)
    fallback_html = fallback.get_data(as_text=True)
    assert fragment.status_code == 200
    assert fallback.status_code == 200
    assert "<!doctype html>" not in fragment_html.lower()
    assert "<!doctype html>" in fallback_html.lower()
    assert "Código público Ecuvel" in fragment_html
    assert "Código público Ecuvel" in fallback_html
    assert "Volver a la tienda" in fallback_html


def test_store_modals_do_not_expose_private_fields_or_order_metrics(client, session):
    _base, store, _product = _base_store(session)
    store.legal_name = "Legal Private Name"
    store.tax_id = "0999999999001"
    session.commit()

    page = client.get(f"/tiendas/{store.slug}").get_data(as_text=True)
    info = client.get(f"/tiendas/{store.slug}/informacion?modal=1").get_data(as_text=True)
    products = client.get(
        f"/tiendas/{store.slug}/productos/resumen?modal=1"
    ).get_data(as_text=True)

    combined = page + info + products
    assert "Legal Private Name" not in combined
    assert "0999999999001" not in combined
    assert "Warehouse" not in combined
    assert "ventas" not in products.lower()
    assert "pedidos" not in products.lower()
    assert "Dirección comercial no publicada" in info


def test_store_rating_uses_only_published_product_reviews(client, session):
    base, store, product = _base_store(session)
    _add_review(session, base, product, rating=5, status=ProductReviewStatus.PUBLISHED)
    _add_review(session, base, product, rating=1, status=ProductReviewStatus.PENDING_REVIEW)
    session.commit()

    page = client.get(f"/tiendas/{store.slug}").get_data(as_text=True)
    rating = client.get(f"/tiendas/{store.slug}/calificacion?modal=1").get_data(as_text=True)

    assert "5.0" in page
    assert "5.0" in rating
    assert "1 reseña publicada" in rating
    assert "métricas de pedidos" in rating


def test_store_product_count_is_unique_and_only_public_visible(client, session):
    _base, store, _product = _base_store(session)
    _create_store_product(session, store, title="Second visible product")
    _create_store_product(session, store, title="Inactive offer product", active_offer=False)
    _create_store_product(session, store, title="Inactive product", active_product=False)
    _create_store_product(session, store, title="Inactive variant", active_variant=False)
    _create_store_product(session, store, title="Inactive category", active_category=False)

    other_store = Store(
        public_code=f"STR-{uuid.uuid4().hex[:10]}",
        name="Other Store",
        slug=f"other-{uuid.uuid4().hex[:10]}",
        status=StoreStatus.ACTIVE,
    )
    session.add(other_store)
    session.flush()
    _create_store_product(session, other_store, title="Other store product")
    session.commit()

    page = client.get(f"/tiendas/{store.slug}").get_data(as_text=True)
    summary = client.get(
        f"/tiendas/{store.slug}/productos/resumen?modal=1"
    ).get_data(as_text=True)

    assert "2 productos visibles" in page
    assert "2 productos publicados" in summary
    assert "Second visible product" in page
    assert "Inactive offer product" not in page
    assert "Other store product" not in page


def test_store_catalog_paginates_and_keeps_current_prices(client, session):
    _base, store, _product = _base_store(session)
    for index in range(21):
        _create_store_product(
            session,
            store,
            title=f"Paged product {index:02d}",
            price=Decimal("20.00") + index,
        )
    session.commit()

    first = client.get(f"/tiendas/{store.slug}")
    second = client.get(f"/tiendas/{store.slug}?page=2")

    first_html = first.get_data(as_text=True)
    second_html = second.get_data(as_text=True)
    assert "Página 1 de 2" in first_html
    assert "Siguiente" in first_html
    assert "Página 2 de 2" in second_html
    assert "Anterior" in second_html
    assert "Paged product 20" in second_html
    assert "$40.00" in second_html


def test_public_store_escapes_store_name(client, session):
    _base, store, _product = _base_store(session)
    store.name = '<script>alert("x")</script>'
    session.commit()

    html = client.get(f"/tiendas/{store.slug}").get_data(as_text=True)

    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


def test_public_store_gets_do_not_modify_financial_or_inventory_tables(client, session):
    _base, store, _product = _base_store(session)
    session.commit()

    before = {
        "orders": session.scalar(select(func.count()).select_from(Order)),
        "payments": session.scalar(select(func.count()).select_from(PaymentAttempt)),
        "balances": session.scalar(select(func.count()).select_from(InventoryBalance)),
        "reviews": session.scalar(select(func.count()).select_from(ProductReview)),
    }

    client.get(f"/tiendas/{store.slug}")
    client.get(f"/tiendas/{store.slug}/informacion?modal=1")
    client.get(f"/tiendas/{store.slug}/calificacion?modal=1")
    client.get(f"/tiendas/{store.slug}/productos/resumen?modal=1")

    after = {
        "orders": session.scalar(select(func.count()).select_from(Order)),
        "payments": session.scalar(select(func.count()).select_from(PaymentAttempt)),
        "balances": session.scalar(select(func.count()).select_from(InventoryBalance)),
        "reviews": session.scalar(select(func.count()).select_from(ProductReview)),
    }
    assert after == before


def test_public_store_page_uses_batched_queries(client, session, app):
    _base, store, _product = _base_store(session)
    for index in range(8):
        _create_store_product(session, store, title=f"Batch product {index}")
    session.commit()
    statements = []

    def before_cursor_execute(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            response = client.get(f"/tiendas/{store.slug}")
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

    assert response.status_code == 200
    assert len(statements) <= 12
