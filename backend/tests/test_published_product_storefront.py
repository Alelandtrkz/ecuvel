from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Product
from app.storefront import _media_urls_for_variant, _variant_value_key
from app.services.product_drafts import capture_submission_commission_snapshots
from app.services.product_publication import (
    MODERATION_CHECKS,
    publish_product_draft,
)
from tests.product_moderation_helpers import (
    create_commission_rule,
    create_complete_family_draft,
    create_complete_simple_draft,
    create_phone_categories,
    create_seller_location,
    create_store,
    create_user,
)


def _publish(app, session, tmp_path, *, family: bool):
    seller = create_user(session)
    moderator = create_user(session, staff=True)
    store = create_store(session, name="Tienda pública real")
    category, subcategory = create_phone_categories(session)
    create_seller_location(session, store)
    create_commission_rule(session, rate="8.00", category=category)
    source_root = tmp_path / "drafts"
    factory = create_complete_family_draft if family else create_complete_simple_draft
    draft = factory(
        session,
        seller=seller,
        store=store,
        category=category,
        subcategory=subcategory,
        media_root=source_root,
    )
    capture_submission_commission_snapshots(session, draft)
    session.commit()
    result = publish_product_draft(
        session,
        draft_id=draft.id,
        actor_user_id=moderator.id,
        checklist={key: True for key in MODERATION_CHECKS},
        source_media_root=source_root,
        catalog_media_root=tmp_path / "catalog",
    )
    session.commit()
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path / "catalog")
    product = session.scalar(
        select(Product)
        .options(selectinload(Product.media), selectinload(Product.variants))
        .where(Product.id == result.product.id)
    )
    return draft, product


def test_simple_approval_is_visible_on_home_detail_and_public_media(
    app, session, tmp_path,
):
    draft, product = _publish(app, session, tmp_path, family=False)
    client = app.test_client()

    home = client.get("/")
    assert home.status_code == 200
    assert draft.title in home.get_data(as_text=True)

    detail = client.get(f"/productos/{product.slug}")
    body = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert draft.title in body
    assert draft.seller_sku in body
    assert "$45.00" in body

    media = product.media[0]
    image = client.get(f"/productos/{product.slug}/media/{media.public_id}")
    assert image.status_code == 200
    assert image.mimetype == "image/webp"
    assert image.cache_control.public is True
    assert image.cache_control.max_age == 31536000
    thumbnail_url = f"/productos/{product.slug}/media/{media.public_id}/thumbnail"
    thumbnail = client.get(thumbnail_url)
    assert thumbnail.status_code == 200
    assert thumbnail.mimetype == "image/webp"
    assert thumbnail.cache_control.public is True
    assert thumbnail_url in home.get_data(as_text=True)


def test_family_detail_exposes_only_active_variants_and_switches_by_sku(
    app, session, tmp_path,
):
    draft, product = _publish(app, session, tmp_path, family=True)
    enabled = [row for row in draft.variants if row["enabled"]]
    disabled = next(row for row in draft.variants if not row["enabled"])
    client = app.test_client()

    first = client.get(
        f"/productos/{product.slug}?variant={enabled[0]['sku']}"
    )
    first_body = first.get_data(as_text=True)
    assert first.status_code == 200
    assert enabled[0]["sku"] in first_body
    assert enabled[0]["name"] in first_body

    sold_out = client.get(
        f"/productos/{product.slug}?variant={enabled[1]['sku']}"
    )
    sold_out_body = sold_out.get_data(as_text=True)
    assert sold_out.status_code == 200
    assert enabled[1]["sku"] in sold_out_body
    assert "Producto agotado" in sold_out_body
    assert disabled["sku"] not in sold_out_body

    by_color = {}
    for media in product.media:
        by_color.setdefault(media.variant_value_key, set()).add(media.public_id)
    assert set(by_color) == {"negro", "azul"}
    with app.test_request_context():
        for variant in product.variants:
            color = _variant_value_key(
                product.variant_configuration or {},
                variant.attributes or {},
                "color_principal",
            )
            urls = _media_urls_for_variant(
                product=product,
                attributes=variant.attributes or {},
            )
            public_ids = {url.rsplit("/", 1)[-1] for url in urls}
            assert public_ids == by_color[color]
            assert public_ids.isdisjoint(
                set().union(
                    *(ids for key, ids in by_color.items() if key != color)
                )
            )
