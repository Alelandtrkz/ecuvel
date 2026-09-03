from __future__ import annotations

import json
import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session as flask_session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db, limiter
from app.models import (
    Category,
    Order,
    OrderItem,
    PaymentAttempt,
    PaymentProof,
    Product,
    ProductMedia,
    ProductReviewImage,
    ProductVariant,
    SellerOffer,
    SellerOrder,
    Store,
    User,
)
from app.models.enums import (
    OfferStatus,
    PaymentMethod,
    PaymentStatus,
    ProductReviewStatus,
    StoreStatus,
)
from app.services.cart import (
    CART_LOW_STOCK_THRESHOLD,
    MAX_CART_QUANTITY,
    CartServiceError,
    add_cart_item,
    get_cart_item_count,
    get_cart_state,
    remove_cart_item,
    remove_selected_cart_items,
    set_all_cart_items_selected,
    set_cart_item_quantity,
    set_cart_item_selected,
)
from app.services.checkout import (
    CheckoutServiceError,
    EmptyCheckoutError,
    build_checkout_preview,
    create_checkout_order,
)
from app.services.customer_orders import (
    get_customer_order_detail,
    get_customer_orders_page,
    normalize_orders_filter,
    normalize_page,
)
from app.services.catalog_listings import load_public_listings
from app.services.catalog_ranking import (
    LIVE_RANKER_VERSION,
    SHADOW_RANKER_VERSION,
    SURFACE_CATEGORY,
    SURFACE_HOME,
    SURFACE_RECOMMENDATIONS,
    SURFACE_SEARCH,
    SURFACE_STORE,
    apply_soft_diversity,
    private_search_context,
    ranking_day,
    rank_listings_v1,
)
from app.services.catalog_feed import (
    CATALOG_FEED_CURSOR_VERSION,
    CatalogFeedCursor,
    InvalidCatalogFeedCursorError,
    catalog_feed_context_hash,
    load_catalog_feed_cursor,
    sign_catalog_feed_cursor,
)
from app.services.catalog_shadow_ranking import (
    load_listing_event_aggregates,
    shadow_rank_listings,
)
from app.services.catalog_telemetry import (
    CLIENT_EVENT_TYPES,
    InvalidRankingContextError,
    RankingContext,
    anonymous_session_id,
    load_ranking_context,
    record_context_event_best_effort,
    sign_ranking_context,
)
from app.services.delivery_eta import (
    delivery_eta_compact_label,
    delivery_eta_full_label,
)
from app.services.favorites import (
    FavoriteListItem,
    FavoriteProductNotFoundError,
    add_favorite_by_slug,
    favorite_count_for_user,
    favorite_product_ids_for_user,
    get_favorites_page,
    normalize_favorites_page,
    remove_favorite_by_slug,
)
from app.services.inventory import get_sellable_quantities_for_offers
from app.services.payment_proofs import (
    PaymentProofExpiredError,
    PaymentProofServiceError,
    submit_bank_transfer_proof,
)
from app.services.pending_payments import (
    InvalidPendingPaymentTransitionError,
    PendingPaymentServiceError,
    cancel_pending_bank_transfer_order,
    expire_pending_bank_transfer_payment,
)
from app.services.public_stores import (
    get_public_store_catalog,
    get_public_store_information,
    get_public_store_products_summary,
    get_public_store_rating_summary,
)
from app.services.product_reviews import (
    ProductReviewDuplicateError,
    ProductReviewEligibilityError,
    ProductReviewImageConfig,
    ProductReviewImageError,
    ProductReviewNotFoundError,
    ProductReviewServiceError,
    cleanup_staged_product_review_images,
    create_product_review,
    own_review_for_order_item,
    promote_product_review_images,
    published_reviews_for_product,
    review_stats_for_product_ids,
    review_stats_for_store_ids,
    review_target_for_order_item,
    resubmit_product_review,
    stage_product_review_images,
)
from app.services.product_specifications import (
    ProductSpecificationItemViewModel,
    ProductSpecificationSectionViewModel,
    build_product_specification_presentation,
)
from app.services.product_media import (
    has_complete_product_thumbnail,
    load_product_card_media,
    ordered_product_media,
    product_thumbnail_file_exists,
    variant_media_binding,
    variant_value_key,
)
from app.services.payment_precheck import (
    PaymentPrecheckConfig,
    analyze_payment_proof,
)
from app.services.private_storage import (
    PrivateStorageError,
    delete_private_file,
    private_file_path,
    stage_payment_proof,
    verify_private_file,
)
from werkzeug.exceptions import RequestEntityTooLarge


storefront = Blueprint("storefront", __name__)
logger = logging.getLogger(__name__)

MAX_SEARCH_LENGTH = 100
MAX_CATEGORY_LENGTH = 140
MAX_RECOMMENDATIONS = 10
CART_SESSION_KEY = "cart"
CHECKOUT_DRAFT_SESSION_KEY = "checkout_draft"
CHECKOUT_ORDERS_SESSION_KEY = "checkout_order_ids"
COMPLETED_CHECKOUTS_SESSION_KEY = "completed_checkouts"
MAX_SESSION_CHECKOUT_ORDERS = 10
PAYMENT_PROOF_UPLOADS_SESSION_KEY = "payment_proof_uploads"


@dataclass(frozen=True, slots=True)
class ProductCardViewModel:
    product_slug: str
    offer_id: uuid.UUID | None
    product_url: str | None
    image_url: str
    image_srcset: str | None
    image_sizes: str | None
    image_width: int
    image_height: int
    image_alt: str
    title: str
    current_price: str
    compare_at_price: str | None
    rating: str | None
    review_count: int | None
    is_favorite: bool
    delivery_label: str
    is_available: bool
    listing_key: str | None
    listing_label: str | None
    ranking_context: str | None = None


@dataclass(frozen=True, slots=True)
class ProductGalleryImageViewModel:
    master_url: str
    thumbnail_url: str
    alt: str
    master_width: int | None
    master_height: int | None
    thumbnail_width: int | None
    thumbnail_height: int | None
    identity: str
    is_primary: bool

    @property
    def url(self) -> str:
        """Backward-compatible alias for the master presentation URL."""
        return self.master_url


@dataclass(frozen=True, slots=True)
class ProductDetailViewModel:
    offer_id: uuid.UUID
    product_id: uuid.UUID
    public_identifier: str
    name: str
    description: str | None
    category_name: str
    category_url: str
    store_name: str
    store_url: str | None
    store_is_verified: bool
    store_rating: Decimal | None
    store_review_count: int
    current_price: Decimal
    compare_at_price: Decimal | None
    currency: str
    seller_sku: str
    catalog_sku: str
    variant_name: str | None
    offer_status: OfferStatus
    gallery_images: tuple[ProductGalleryImageViewModel, ...]
    gallery_placeholder_url: str
    specifications: tuple[ProductSpecificationSectionViewModel, ...]
    highlights: tuple[ProductSpecificationItemViewModel, ...]
    rating: Decimal | None
    review_count: int
    availability_label: str
    is_available: bool
    available_quantity: int
    max_quantity: int
    quantity_limit_reached: bool
    low_stock: bool
    availability_message: str
    delivery_label: str
    is_favorite: bool
    variant_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CartLineViewModel:
    offer_id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_url: str
    product_name: str
    variant_name: str | None
    store_name: str
    image_url: str
    quantity: int
    selected: bool
    unit_price: Decimal
    compare_at_price: Decimal | None
    line_total: Decimal
    available: bool
    availability_label: str
    available_quantity: int
    max_quantity: int
    quantity_limit_reached: bool
    low_stock: bool
    availability_message: str
    is_favorite: bool


@dataclass(frozen=True, slots=True)
class CartSummaryViewModel:
    total_lines: int
    total_units: int
    selected_lines: int
    selected_units: int
    subtotal: Decimal
    savings: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class CartPageViewModel:
    items: tuple[CartLineViewModel, ...]
    summary: CartSummaryViewModel
    all_selected: bool
    has_unavailable_items: bool


@dataclass(frozen=True, slots=True)
class CheckoutBuyerViewModel:
    full_name: str
    phone: str | None


@dataclass(frozen=True, slots=True)
class PendingOrderViewModel:
    order_id: uuid.UUID
    buyer_id: uuid.UUID
    payment_attempt_id: uuid.UUID
    order_number: str
    total: Decimal
    currency: str
    payment_status: str
    expires_at: datetime
    proof_id: uuid.UUID | None
    proof_status: str | None
    proof_filename: str | None
    proof_size_bytes: int | None
    proof_created_at: datetime | None

    @property
    def is_awaiting_proof(self) -> bool:
        return self.payment_status == PaymentStatus.AWAITING_PROOF.value and self.proof_id is None

    @property
    def is_expired(self) -> bool:
        return self.payment_status == PaymentStatus.EXPIRED.value

    @property
    def is_cancelled(self) -> bool:
        return self.payment_status == PaymentStatus.CANCELLED.value

    @property
    def can_continue_payment(self) -> bool:
        return self.is_awaiting_proof and _aware_utc(self.expires_at) > datetime.now(timezone.utc)

    @property
    def can_cancel(self) -> bool:
        return self.can_continue_payment


@dataclass(frozen=True, slots=True)
class SessionOrderItemViewModel:
    product_name: str
    quantity: int
    line_total: Decimal


@dataclass(frozen=True, slots=True)
class SessionOrderViewModel:
    order: PendingOrderViewModel
    items: tuple[SessionOrderItemViewModel, ...]
    status_label: str
    status_description: str
    status_icon: str


def _normalize_query_parameter(value: str | None, max_length: int) -> str:
    return " ".join((value or "").split())[:max_length]


def _format_price(amount: Decimal | None, currency: str) -> str | None:
    if amount is None:
        return None

    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{prefix}{amount:,.2f}"


@storefront.app_template_filter("money")
def money_filter(amount: Decimal | None, currency: str = "USD") -> str:
    return _format_price(amount, currency) or ""


@storefront.app_context_processor
def cart_header_context() -> dict[str, int]:
    favorite_count = 0
    if current_user.is_authenticated:
        favorite_count = favorite_count_for_user(db.session, current_user.id)
    return {
        "header_cart_count": get_cart_item_count(
            flask_session.get(CART_SESSION_KEY)
        ),
        "header_favorite_count": favorite_count,
        "nav_categories": _load_nav_categories(),
    }


def _load_categories():
    return db.session.execute(
        select(Category.name, Category.slug)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
    ).all()


def _load_nav_categories():
    """Top-level categories with their active subcategories for the header dropdown."""
    return db.session.scalars(
        select(Category)
        .where(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
        .options(selectinload(Category.children))
    ).all()


def _visible_compare_at_price(row: Any) -> Decimal | None:
    compare_at_price = row.compare_at_price
    if compare_at_price is None or compare_at_price <= row.price:
        return None
    return compare_at_price


DELIVERY_INFORMATION_FALLBACK = "Información de entrega próximamente"
CARD_DELIVERY_INFORMATION_FALLBACK = "Entrega próximamente"
PRODUCT_CARD_IMAGE_SIZES = (
    "(max-width: 359px) calc(100vw - 64px), "
    "(max-width: 767px) calc((100vw - 76px) / 2), "
    "(max-width: 1023px) calc((100vw - 112px) / 3), "
    "(max-width: 1199px) calc((100vw - 160px) / 4), "
    "calc((min(100vw, 1440px) - 192px) / 5)"
)


def _compact_delivery_label(preparation_time_days: int | None) -> str:
    return (
        delivery_eta_compact_label(preparation_time_days)
        or CARD_DELIVERY_INFORMATION_FALLBACK
    )


def _full_delivery_label(preparation_time_days: int | None) -> str:
    return (
        delivery_eta_full_label(preparation_time_days)
        or DELIVERY_INFORMATION_FALLBACK
    )


def _favorite_ids_for_product_ids(product_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not current_user.is_authenticated:
        return set()
    return favorite_product_ids_for_user(
        db.session,
        current_user.id,
        product_ids,
    )


def _versioned_media_url(
    endpoint: str,
    *,
    product_slug: str,
    media: ProductMedia,
    version: str | None,
) -> str:
    values: dict[str, str] = {
        "product_slug": product_slug,
        "public_id": media.public_id,
    }
    if version:
        values["v"] = version
    return url_for(endpoint, **values)


def _card_media_presentation(
    *,
    product_slug: str,
    media: ProductMedia | None,
    placeholder_image: str,
    title: str,
    listing_label: str | None,
) -> tuple[str, str | None, str | None, int, int, str]:
    alt = f"{title}, {listing_label}" if listing_label else title
    if media is None:
        return (
            placeholder_image,
            None,
            None,
            320,
            320,
            f"Imagen provisional de {alt}",
        )

    master_url = _versioned_media_url(
        "storefront.product_media",
        product_slug=product_slug,
        media=media,
        version=media.content_sha256,
    )
    has_thumbnail = product_thumbnail_file_exists(
        media,
        media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
    )
    if has_thumbnail:
        thumbnail_url = _versioned_media_url(
            "storefront.product_media_thumbnail",
            product_slug=product_slug,
            media=media,
            version=media.thumbnail_sha256,
        )
        srcset_entries = [f"{thumbnail_url} {media.thumbnail_width}w"]
        if media.width and media.width != media.thumbnail_width:
            srcset_entries.append(f"{master_url} {media.width}w")
        return (
            thumbnail_url,
            ", ".join(srcset_entries),
            PRODUCT_CARD_IMAGE_SIZES,
            int(media.thumbnail_width or 320),
            int(media.thumbnail_height or 320),
            alt,
        )
    return (
        master_url,
        None,
        None,
        int(media.width or 320),
        int(media.height or 320),
        alt,
    )


def _card_media_url(
    *,
    product_slug: str,
    media: ProductMedia | None,
    placeholder_image: str,
) -> str:
    return _card_media_presentation(
        product_slug=product_slug,
        media=media,
        placeholder_image=placeholder_image,
        title="Producto",
        listing_label=None,
    )[0]


def _card_from_row(
    row: Any,
    placeholder_image: str,
    favorite_product_ids: set[uuid.UUID] | None = None,
    availability_by_offer_id: dict[uuid.UUID, int] | None = None,
    review_stats_by_product_id: dict[uuid.UUID, Any] | None = None,
    media_by_variant_id: dict[uuid.UUID, ProductMedia] | None = None,
) -> ProductCardViewModel:
    is_available = getattr(row, "is_available", None)
    if is_available is None:
        is_available = (
            True
            if availability_by_offer_id is None
            else max(0, availability_by_offer_id.get(row.offer_id, 0)) > 0
        )
    review_stats = (review_stats_by_product_id or {}).get(row.product_id)
    media = (media_by_variant_id or {}).get(row.variant_id)
    (
        image_url,
        image_srcset,
        image_sizes,
        image_width,
        image_height,
        image_alt,
    ) = _card_media_presentation(
        product_slug=row.product_slug,
        media=media,
        placeholder_image=placeholder_image,
        title=row.product_title,
        listing_label=getattr(row, "listing_label", None),
    )
    return ProductCardViewModel(
        product_slug=row.product_slug,
        offer_id=row.offer_id,
        product_url=url_for(
            "storefront.product_detail",
            product_slug=row.product_slug,
            variant=row.catalog_sku,
        ),
        image_url=image_url,
        image_srcset=image_srcset,
        image_sizes=image_sizes,
        image_width=image_width,
        image_height=image_height,
        image_alt=image_alt,
        title=row.product_title,
        current_price=_format_price(row.price, row.currency) or "",
        compare_at_price=_format_price(
            _visible_compare_at_price(row),
            row.currency,
        ),
        rating=(
            f"{review_stats.average:.1f}"
            if review_stats and review_stats.average is not None
            else None
        ),
        review_count=review_stats.count if review_stats else None,
        is_favorite=row.product_id in (favorite_product_ids or set()),
        delivery_label=(
            _compact_delivery_label(
                getattr(row, "preparation_time_days", None)
            )
            if is_available
            else "Agotado"
        ),
        is_available=is_available,
        listing_key=getattr(row, "listing_key", None),
        listing_label=getattr(row, "listing_label", None),
    )


def _cards_from_rows(
    rows: list[Any],
    placeholder_image: str,
) -> list[ProductCardViewModel]:
    favorite_ids = _favorite_ids_for_product_ids(
        {row.product_id for row in rows}
    )
    if all(hasattr(row, "available_quantity") for row in rows):
        availability = {
            row.offer_id: max(0, row.available_quantity) for row in rows
        }
    else:
        availability = _availability_by_offer_ids({row.offer_id for row in rows})
    review_stats = review_stats_for_product_ids(
        db.session,
        {row.product_id for row in rows},
    )
    media_by_variant_id = load_product_card_media(
        db.session,
        {(row.product_id, row.variant_id) for row in rows},
        media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
    )
    return [
        _card_from_row(
            row,
            placeholder_image,
            favorite_ids,
            availability,
            review_stats,
            media_by_variant_id,
        )
        for row in rows
    ]


def _cards_with_ranking_context(
    cards: list[ProductCardViewModel],
    listings: list[Any],
    *,
    surface: str,
    ranking_request_id: uuid.UUID | None = None,
    position_offset: int = 0,
    shadow_listings: list[Any] | None = None,
) -> list[ProductCardViewModel]:
    if not cards:
        return cards
    ranking_request_id = ranking_request_id or uuid.uuid4()
    shadow_candidates = shadow_listings if shadow_listings is not None else listings
    shadow_by_listing: dict[str, Any] = {}
    if current_app.config["CATALOG_SHADOW_RANKING_ENABLED"]:
        try:
            aggregates = load_listing_event_aggregates(
                db.session,
                {listing.listing_key for listing in shadow_candidates},
            )
            shadow_by_listing = {
                result.listing_key: result
                for result in shadow_rank_listings(shadow_candidates, aggregates)
            }
        except Exception:
            logger.warning("Catalog shadow ranking failed", exc_info=True)

    enriched: list[ProductCardViewModel] = []
    for position, (card, listing) in enumerate(
        zip(cards, listings, strict=True),
        start=position_offset + 1,
    ):
        shadow = shadow_by_listing.get(listing.listing_key)
        context = RankingContext(
            ranking_request_id=ranking_request_id,
            surface=surface,
            listing_key=listing.listing_key,
            product_id=listing.product_id,
            variant_id=listing.variant_id,
            offer_id=listing.offer_id,
            served_ranker=LIVE_RANKER_VERSION,
            served_position=position,
            shadow_ranker=SHADOW_RANKER_VERSION if shadow else None,
            shadow_position=shadow.position if shadow else None,
            shadow_score=(
                Decimal(str(round(shadow.score, 8))) if shadow else None
            ),
        )
        enriched.append(
            replace(
                card,
                ranking_context=sign_ranking_context(
                    current_app.config["SECRET_KEY"],
                    context,
                ),
            )
        )
    return enriched


def _submitted_ranking_context() -> RankingContext | None:
    token = _request_value("ranking_context")
    if not token:
        return None
    try:
        return load_ranking_context(
            current_app.config["SECRET_KEY"],
            token,
            max_age_seconds=current_app.config[
                "CATALOG_RANKING_CONTEXT_TTL_SECONDS"
            ],
        )
    except InvalidRankingContextError:
        return None


def _record_server_action_event(
    event_type: str,
    context: RankingContext | None,
) -> None:
    if context is None:
        return
    actor_id = current_user.id if current_user.is_authenticated else None
    anonymous_id = (
        None
        if current_user.is_authenticated
        else anonymous_session_id(flask_session)
    )
    try:
        record_context_event_best_effort(
            db.session,
            event_type=event_type,
            context=context,
            actor_user_id=actor_id,
            anonymous_id=anonymous_id,
        )
    except Exception:
        logger.warning("Catalog action telemetry failed", exc_info=True)


def _store_modal_context(template_name: str, **context: Any) -> str:
    if request.args.get("modal") == "1":
        return render_template(
            template_name,
            is_fragment=True,
            **context,
        )
    return render_template(
        "storefront/store_dialog_page.html",
        content_template=template_name,
        is_fragment=False,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        **context,
    )


def _card_from_favorite_item(
    item: FavoriteListItem,
    placeholder_image: str,
    review_stats_by_product_id: dict[uuid.UUID, Any] | None = None,
    media_by_variant_id: dict[uuid.UUID, ProductMedia] | None = None,
) -> ProductCardViewModel:
    visible_compare_at = (
        item.compare_at_price
        if item.compare_at_price is not None
        and item.price is not None
        and item.compare_at_price > item.price
        else None
    )
    review_stats = (review_stats_by_product_id or {}).get(item.product_id)
    media = (
        (media_by_variant_id or {}).get(item.variant_id)
        if item.variant_id is not None
        else None
    )
    (
        image_url,
        image_srcset,
        image_sizes,
        image_width,
        image_height,
        image_alt,
    ) = _card_media_presentation(
        product_slug=item.product_slug,
        media=media,
        placeholder_image=placeholder_image,
        title=item.product_title,
        listing_label=item.listing_label,
    )
    return ProductCardViewModel(
        product_slug=item.product_slug,
        offer_id=item.offer_id if item.is_available else None,
        product_url=(
            url_for(
                "storefront.product_detail",
                product_slug=item.product_slug,
                variant=item.catalog_sku,
            )
            if item.is_catalog_visible
            else None
        ),
        image_url=image_url,
        image_srcset=image_srcset,
        image_sizes=image_sizes,
        image_width=image_width,
        image_height=image_height,
        image_alt=image_alt,
        title=item.product_title,
        current_price=(
            _format_price(item.price, item.currency or "USD")
            if item.price is not None
            else "Producto no disponible"
        )
        or "",
        compare_at_price=_format_price(
            visible_compare_at,
            item.currency or "USD",
        ),
        rating=(
            f"{review_stats.average:.1f}"
            if review_stats and review_stats.average is not None
            else None
        ),
        review_count=review_stats.count if review_stats else None,
        is_favorite=True,
        delivery_label=(
            _compact_delivery_label(item.preparation_time_days)
            if item.is_available
            else "Producto no disponible"
        ),
        is_available=item.is_available,
        listing_key=item.listing_key,
        listing_label=item.listing_label,
    )


def _build_product_gallery_images(
    product_name: str,
    image_sources: Iterable[str | None | ProductMedia],
    *,
    product_slug: str | None = None,
) -> tuple[ProductGalleryImageViewModel, ...]:
    images: list[ProductGalleryImageViewModel] = []
    seen_images: set[tuple[str, ...]] = set()

    for source in image_sources:
        if isinstance(source, ProductMedia):
            if not product_slug:
                raise ValueError("product_slug is required for ProductMedia galleries")
            master_url = _versioned_media_url(
                "storefront.product_media",
                product_slug=product_slug,
                media=source,
                version=source.content_sha256,
            )
            has_thumbnail = product_thumbnail_file_exists(
                source,
                media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
            )
            thumbnail_url = (
                _versioned_media_url(
                    "storefront.product_media_thumbnail",
                    product_slug=product_slug,
                    media=source,
                    version=source.thumbnail_sha256,
                )
                if has_thumbnail
                else master_url
            )
            master_width = source.width
            master_height = source.height
            thumbnail_width = source.thumbnail_width if has_thumbnail else source.width
            thumbnail_height = source.thumbnail_height if has_thumbnail else source.height
            identity = source.public_id
            dedupe_key = (
                (
                    "sha256",
                    source.content_sha256,
                    source.variant_axis_key or "",
                    source.variant_value_key or "",
                )
                if source.content_sha256
                else ("legacy-url", master_url)
            )
        else:
            master_url = (source or "").strip()
            if not master_url:
                continue
            thumbnail_url = master_url
            master_width = None
            master_height = None
            thumbnail_width = None
            thumbnail_height = None
            identity = master_url
            dedupe_key = ("url", master_url)

        if dedupe_key in seen_images:
            continue

        seen_images.add(dedupe_key)
        image_number = len(images) + 1
        images.append(
            ProductGalleryImageViewModel(
                master_url=master_url,
                thumbnail_url=thumbnail_url,
                alt=f"{product_name}, vista {image_number}",
                master_width=master_width,
                master_height=master_height,
                thumbnail_width=thumbnail_width,
                thumbnail_height=thumbnail_height,
                identity=identity,
                is_primary=image_number == 1,
            )
        )

    return tuple(images)


def _variant_value_key(
    configuration: dict[str, Any],
    attributes: dict[str, Any],
    axis_key: str,
) -> str | None:
    return variant_value_key(configuration, attributes, axis_key)


def _media_for_variant(
    *,
    product: Product,
    attributes: dict[str, Any],
) -> tuple[ProductMedia, ...]:
    visual_key, value_key = variant_media_binding(
        product.variant_configuration or {},
        attributes or {},
    )
    return ordered_product_media(
        product.media,
        variant_axis_key=visual_key,
        variant_value_key=value_key,
    )


def _media_urls_for_variant(
    *,
    product: Product,
    attributes: dict[str, Any],
) -> tuple[str, ...]:
    """Return versioned master URLs for non-gallery compatibility callers."""
    return tuple(
        _versioned_media_url(
            "storefront.product_media",
            product_slug=product.slug,
            media=media,
            version=media.content_sha256,
        )
        for media in _media_for_variant(product=product, attributes=attributes)
    )


def _media_payload_for_variant(
    *,
    product: Product,
    attributes: dict[str, Any],
) -> list[dict[str, Any]]:
    images = _build_product_gallery_images(
        product.title,
        _media_for_variant(product=product, attributes=attributes),
        product_slug=product.slug,
    )
    return [
        {
            "master_url": image.master_url,
            "thumbnail_url": image.thumbnail_url,
            "master_width": image.master_width,
            "master_height": image.master_height,
            "thumbnail_width": image.thumbnail_width,
            "thumbnail_height": image.thumbnail_height,
            "alt": image.alt,
            "identity": image.identity,
        }
        for image in images
    ]


def _build_variant_payload(
    *,
    product: Product,
    rows: list[Any],
    availability: dict[uuid.UUID, int],
    selected_catalog_sku: str,
) -> dict[str, Any]:
    variants = []
    for row in rows:
        quantity = max(0, availability.get(row.offer_id, 0))
        max_quantity, low_stock, availability_label, availability_message = _stock_presentation(quantity)
        variants.append({
            "catalog_sku": row.catalog_sku,
            "combination_key": row.combination_key,
            "attributes": dict(row.variant_attributes or {}),
            "name": row.variant_title or "",
            "seller_sku": row.seller_sku,
            "offer_id": str(row.offer_id),
            "currency": row.currency,
            "price": str(row.price),
            "compare_at_price": str(_visible_compare_at_price(row)) if _visible_compare_at_price(row) is not None else None,
            "available_quantity": quantity,
            "max_quantity": max_quantity,
            "is_available": quantity > 0,
            "low_stock": low_stock,
            "availability_label": availability_label,
            "availability_message": availability_message,
            "delivery_label": _full_delivery_label(
                row.preparation_time_days
            ),
            "images": _media_payload_for_variant(
                product=product,
                attributes=row.variant_attributes or {},
            ),
        })
    return {
        "base_title": product.title,
        "axes": list((product.variant_configuration or {}).get("axes") or []),
        "visual_axis_key": (product.variant_configuration or {}).get("visual_axis_key"),
        "selected_catalog_sku": selected_catalog_sku,
        "variants": variants,
    }


def _availability_by_offer_ids(
    offer_ids: set[uuid.UUID],
) -> dict[uuid.UUID, int]:
    return get_sellable_quantities_for_offers(
        session=db.session,
        offer_ids=offer_ids,
    )


def _stock_presentation(
    available_quantity: int,
) -> tuple[int, bool, str, str]:
    max_quantity = min(MAX_CART_QUANTITY, max(0, available_quantity))
    low_stock = 0 < available_quantity <= CART_LOW_STOCK_THRESHOLD
    if available_quantity <= 0:
        return 0, False, "Producto agotado", "Producto agotado."
    if low_stock:
        message = f"Solo quedan {available_quantity} unidades disponibles."
        return max_quantity, True, "Disponible para ordenar", message
    return (
        max_quantity,
        False,
        "Disponible para ordenar",
        f"{available_quantity} unidades disponibles.",
    )


def _cart_offer_rows(offer_ids: set[uuid.UUID]):
    if not offer_ids:
        return []

    return db.session.execute(
        select(
            SellerOffer.id.label("offer_id"),
            SellerOffer.price.label("price"),
            SellerOffer.compare_at_price.label("compare_at_price"),
            SellerOffer.currency.label("currency"),
            SellerOffer.status.label("offer_status"),
            ProductVariant.id.label("variant_id"),
            ProductVariant.title.label("variant_title"),
            ProductVariant.is_active.label("variant_is_active"),
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            Product.is_active.label("product_is_active"),
            Category.id.label("category_id"),
            Category.is_active.label("category_is_active"),
            Store.name.label("store_name"),
            Store.status.label("store_status"),
            Store.is_verified.label("store_is_verified"),
        )
        .select_from(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(SellerOffer.id.in_(offer_ids))
    ).all()


def _save_cart_state(state: dict[str, Any]) -> None:
    flask_session[CART_SESSION_KEY] = state
    flask_session.modified = True


def _rehydrate_cart() -> tuple[
    CartPageViewModel,
    set[uuid.UUID],
    set[uuid.UUID],
]:
    raw_state = flask_session.get(CART_SESSION_KEY)
    state = get_cart_state(raw_state)
    item_states = state["items"]
    offer_ids = {uuid.UUID(offer_id) for offer_id in item_states}
    rows_by_offer_id = {
        row.offer_id: row for row in _cart_offer_rows(offer_ids)
    }
    availability = _availability_by_offer_ids(set(rows_by_offer_id))
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    favorite_product_ids = _favorite_ids_for_product_ids(
        {row.product_id for row in rows_by_offer_id.values()}
    )
    media_by_variant_id = load_product_card_media(
        db.session,
        {
            (row.product_id, row.variant_id)
            for row in rows_by_offer_id.values()
        },
        media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
    )

    lines: list[CartLineViewModel] = []
    clean_items: dict[str, dict[str, int | bool]] = {}
    category_ids: set[uuid.UUID] = set()
    product_ids: set[uuid.UUID] = set()

    for stored_offer_id, item_state in item_states.items():
        offer_id = uuid.UUID(stored_offer_id)
        row = rows_by_offer_id.get(offer_id)
        if row is None:
            continue

        is_visible = all(
            (
                row.offer_status == OfferStatus.ACTIVE,
                row.currency == "USD",
                row.variant_is_active,
                row.product_is_active,
                row.category_is_active,
                row.store_status == StoreStatus.ACTIVE,
            )
        )
        available_quantity = (
            max(0, availability.get(offer_id, 0)) if is_visible else 0
        )
        (
            max_quantity,
            low_stock,
            availability_label,
            availability_message,
        ) = _stock_presentation(available_quantity)
        available = is_visible and available_quantity > 0
        if not is_visible:
            availability_label = "No disponible"
            availability_message = "Este producto ya no está disponible."
            low_stock = False

        original_quantity = int(item_state["quantity"])
        quantity = original_quantity
        if available and quantity > max_quantity:
            quantity = max_quantity
            flash(
                f"La cantidad de {row.product_title} se ajustó de "
                f"{original_quantity} a {quantity} porque cambió la "
                "disponibilidad.",
                "warning",
            )
        selected = bool(item_state["selected"]) and available
        clean_items[stored_offer_id] = {
            "quantity": quantity,
            "selected": selected,
        }
        compare_at_price = _visible_compare_at_price(row)
        media = media_by_variant_id.get(row.variant_id)
        image_url = _card_media_url(
            product_slug=row.product_slug,
            media=media,
            placeholder_image=placeholder_image,
        )
        lines.append(
            CartLineViewModel(
                offer_id=offer_id,
                product_id=row.product_id,
                product_slug=row.product_slug,
                product_url=url_for(
                    "storefront.product_detail",
                    product_slug=row.product_slug,
                ),
                product_name=row.product_title,
                variant_name=row.variant_title,
                store_name=row.store_name,
                image_url=image_url,
                quantity=quantity,
                selected=selected,
                unit_price=row.price,
                compare_at_price=compare_at_price,
                line_total=row.price * quantity,
                available=available,
                availability_label=availability_label,
                available_quantity=available_quantity,
                max_quantity=max_quantity,
                quantity_limit_reached=(
                    available and quantity >= max_quantity
                ),
                low_stock=low_stock,
                availability_message=availability_message,
                is_favorite=row.product_id in favorite_product_ids,
            )
        )
        category_ids.add(row.category_id)
        product_ids.add(row.product_id)

    state["items"] = clean_items
    if raw_state != state:
        _save_cart_state(state)

    eligible_selected = [
        line for line in lines if line.available and line.selected
    ]
    zero = Decimal("0.00")
    total = sum(
        (line.line_total for line in eligible_selected),
        start=zero,
    )
    subtotal = sum(
        (
            (line.compare_at_price or line.unit_price) * line.quantity
            for line in eligible_selected
        ),
        start=zero,
    )
    summary = CartSummaryViewModel(
        total_lines=len(lines),
        total_units=sum(line.quantity for line in lines),
        selected_lines=len(eligible_selected),
        selected_units=sum(line.quantity for line in eligible_selected),
        subtotal=subtotal,
        savings=subtotal - total,
        total=total,
    )
    available_lines = [line for line in lines if line.available]
    cart = CartPageViewModel(
        items=tuple(lines),
        summary=summary,
        all_selected=(
            bool(available_lines)
            and all(line.selected for line in available_lines)
        ),
        has_unavailable_items=any(not line.available for line in lines),
    )
    return cart, category_ids, product_ids


def _cart_recommendations(
    category_ids: set[uuid.UUID],
    product_ids: set[uuid.UUID],
) -> list[ProductCardViewModel]:
    listings = load_public_listings(
        db.session,
        exclude_product_ids=product_ids,
    )
    if category_ids:
        listings = [
            listing for listing in listings
            if listing.category_id in category_ids
        ]
    rows = rank_listings_v1(
        listings,
        surface=SURFACE_RECOMMENDATIONS,
        context="cart",
    )[:MAX_RECOMMENDATIONS]
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    return _cards_with_ranking_context(
        _cards_from_rows(rows, placeholder_image),
        rows,
        surface=SURFACE_RECOMMENDATIONS,
    )


def _parse_quantity(value: str | None) -> int:
    try:
        quantity = int(value or "")
    except (TypeError, ValueError) as exc:
        raise CartServiceError(
            "La cantidad debe ser un número entero."
        ) from exc
    if str(quantity) != (value or "").strip():
        raise CartServiceError("La cantidad debe ser un número entero.")
    return quantity


def _form_selected() -> bool:
    return request.form.get("selected", "").lower() in {
        "1",
        "true",
        "on",
    }


def _safe_next_url(value: str | None) -> str:
    fallback = url_for("storefront.cart")
    if not value or any(character in value for character in "\r\n\\"):
        return fallback
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback
    return value


def _request_value(name: str) -> str | None:
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            value = payload.get(name)
            return None if value is None else str(value)
    return request.form.get(name)


def _wants_json_response() -> bool:
    return request.is_json or (
        request.accept_mimetypes.best == "application/json"
    )


def _validated_cart_quantity(value: str | None) -> int:
    quantity = _parse_quantity(value)
    if quantity < 1 or quantity > MAX_CART_QUANTITY:
        raise CartServiceError(
            f"La cantidad debe estar entre 1 y {MAX_CART_QUANTITY}."
        )
    return quantity


def _cart_error_response(
    *,
    message: str,
    error: str,
    status: int,
    redirect_url: str,
    **details: object,
):
    if _wants_json_response():
        return jsonify(
            ok=False,
            error=error,
            message=message,
            **details,
        ), status
    flash(message, "error")
    return redirect(redirect_url)


def _cart_success_response(
    *,
    message: str,
    redirect_url: str,
    quantity: int,
    max_quantity: int,
):
    if _wants_json_response():
        flash(message, "success")
        return jsonify(
            ok=True,
            message=message,
            quantity=quantity,
            max_quantity=max_quantity,
            redirect_url=redirect_url,
        )
    flash(message, "success")
    return redirect(redirect_url)


def _favorite_redirect_url(product_slug: str | None = None) -> str:
    fallback = (
        url_for("storefront.product_detail", product_slug=product_slug)
        if product_slug
        else url_for("storefront.favorites")
    )
    return _safe_next_url(_request_value("next") or fallback)


def _favorite_login_response(redirect_url: str):
    if _wants_json_response():
        return jsonify(
            ok=False,
            error="login_required",
            message="Inicia sesión para guardar favoritos.",
            login_url=url_for("auth.login_form", next=redirect_url),
        ), 401
    flash("Inicia sesión para guardar favoritos.", "warning")
    return redirect(url_for("auth.login_form", next=redirect_url))


def _favorite_response(
    *,
    result,
    message: str,
    redirect_url: str,
):
    if _wants_json_response():
        return jsonify(
            ok=True,
            is_favorite=result.is_favorite,
            favorite_count=result.favorite_count,
            product_slug=result.product_slug,
            message=message,
        )
    flash(message, "success")
    return redirect(redirect_url)


def _product_review_image_config() -> ProductReviewImageConfig:
    return ProductReviewImageConfig(
        root=current_app.config["PRODUCT_REVIEW_UPLOAD_DIR"],
        max_images=current_app.config["PRODUCT_REVIEW_MAX_IMAGES"],
        max_bytes=current_app.config["PRODUCT_REVIEW_IMAGE_MAX_BYTES"],
        total_max_bytes=current_app.config[
            "PRODUCT_REVIEW_IMAGES_TOTAL_MAX_BYTES"
        ],
        max_pixels=current_app.config["PRODUCT_REVIEW_IMAGE_MAX_PIXELS"],
        max_dimension=current_app.config["PRODUCT_REVIEW_IMAGE_MAX_DIMENSION"],
    )


def _checkout_cart_signature(cart_state: object) -> str:
    state = get_cart_state(cart_state)
    selected = [
        [offer_id, int(item["quantity"])]
        for offer_id, item in sorted(state["items"].items())
        if item["selected"]
    ]
    encoded = json.dumps(selected, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkout_buyer() -> User | None:
    if current_user.is_authenticated:
        return db.session.get(User, current_user.id)
    if not current_app.config["ALLOW_DEMO_CHECKOUT"]:
        return None
    return db.session.scalar(
        select(User).where(
            User.email == current_app.config["CHECKOUT_DEMO_BUYER_EMAIL"]
        )
    )


def _requires_verified_identity():
    if not current_user.is_authenticated:
        return redirect(
            url_for(
                "auth.login_form",
                next=request.full_path if request.query_string else request.path,
            )
        )
    if (
        current_app.config["AUTH_REQUIRE_EMAIL_VERIFICATION"]
        and current_user.email_verified_at is None
        and current_user.phone_verified_at is None
    ):
        flash("Verifica tu correo o teléfono antes de continuar.", "warning")
        return redirect(url_for("auth.verification_pending"))
    return None


def _remember_checkout_order(order_id: uuid.UUID) -> None:
    values = [
        value
        for value in flask_session.get(CHECKOUT_ORDERS_SESSION_KEY, [])
        if value != str(order_id)
    ]
    values.append(str(order_id))
    flask_session[CHECKOUT_ORDERS_SESSION_KEY] = values[
        -MAX_SESSION_CHECKOUT_ORDERS:
    ]


def _allowed_checkout_order_ids() -> set[uuid.UUID]:
    allowed_ids: set[uuid.UUID] = set()
    if current_user.is_authenticated:
        return set(
            db.session.scalars(
                select(Order.id).where(Order.buyer_id == current_user.id)
            ).all()
        )
    if not current_app.config["ALLOW_DEMO_CHECKOUT"]:
        return allowed_ids
    for value in flask_session.get(CHECKOUT_ORDERS_SESSION_KEY, []):
        try:
            allowed_ids.add(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            continue
    return allowed_ids


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _maybe_expire_authorized_order(order_number: str) -> bool:
    allowed_ids = _allowed_checkout_order_ids()
    if not allowed_ids:
        return False
    row = db.session.execute(
        select(Order.id, PaymentAttempt.id, PaymentAttempt.status, PaymentAttempt.expires_at)
        .join(PaymentAttempt, PaymentAttempt.order_id == Order.id)
        .where(Order.order_number == order_number, Order.id.in_(allowed_ids))
    ).one_or_none()
    if row is None:
        return False
    _order_id, attempt_id, status, expires_at = row
    if status != PaymentStatus.AWAITING_PROOF or _aware_utc(expires_at) > datetime.now(timezone.utc):
        return False
    db.session.remove()
    database_session = db.session()
    try:
        with database_session.begin():
            expire_pending_bank_transfer_payment(
                session=database_session,
                payment_attempt_id=attempt_id,
            )
        return True
    except PendingPaymentServiceError:
        current_app.logger.info(
            "No se pudo expirar automáticamente el pedido %s",
            order_number,
            exc_info=True,
        )
        return False
    finally:
        db.session.remove()


def _authorized_pending_order(order_number: str) -> PendingOrderViewModel:
    allowed_ids = _allowed_checkout_order_ids()
    if not allowed_ids:
        abort(404)
    row = db.session.execute(
        select(Order, PaymentAttempt)
        .join(PaymentAttempt, PaymentAttempt.order_id == Order.id)
        .where(Order.order_number == order_number, Order.id.in_(allowed_ids))
    ).one_or_none()
    if row is None:
        abort(404)
    order, attempt = row
    proof = db.session.scalar(
        select(PaymentProof).where(
            PaymentProof.payment_attempt_id == attempt.id
        )
    )
    return PendingOrderViewModel(
        order_id=order.id,
        buyer_id=order.buyer_id,
        payment_attempt_id=attempt.id,
        order_number=order.order_number,
        total=order.grand_total,
        currency=order.currency,
        payment_status=attempt.status.value,
        expires_at=attempt.expires_at,
        proof_id=proof.id if proof else None,
        proof_status=proof.status.value if proof else None,
        proof_filename=proof.original_filename if proof else None,
        proof_size_bytes=proof.size_bytes if proof else None,
        proof_created_at=proof.created_at if proof else None,
    )


def _session_order_status(order: PendingOrderViewModel) -> tuple[str, str, str]:
    if order.payment_status == PaymentStatus.AWAITING_PROOF.value and order.proof_id is None:
        if _aware_utc(order.expires_at) <= datetime.now(timezone.utc):
            return (
                "Pago expirado",
                "La reserva venció y se liberará automáticamente.",
                "timer-off",
            )
        return (
            "Esperando comprobante",
            "Puedes continuar con la transferencia o cancelar antes del vencimiento.",
            "clock-3",
        )
    if order.payment_status == PaymentStatus.PROCESSING.value or order.proof_id:
        return (
            "Comprobante en revisión",
            "Recibimos tu archivo. La aprobación sigue siendo manual.",
            "file-check-2",
        )
    if order.payment_status == PaymentStatus.APPROVED.value:
        return (
            "Pago confirmado",
            "Tu pago fue aprobado y el pedido seguirá su preparación.",
            "circle-check",
        )
    if order.payment_status == PaymentStatus.EXPIRED.value:
        return (
            "Pago expirado",
            "La reserva venció y las unidades fueron liberadas.",
            "timer-off",
        )
    if order.payment_status == PaymentStatus.CANCELLED.value:
        return (
            "Pedido cancelado",
            "Cancelaste el pedido antes de enviar el comprobante.",
            "x-circle",
        )
    return (
        "Estado del pedido",
        "Estamos actualizando la información de este pedido.",
        "package",
    )


def _session_order_items(order_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[SessionOrderItemViewModel, ...]]:
    if not order_ids:
        return {}
    rows = db.session.execute(
        select(
            SellerOrder.order_id,
            OrderItem.product_name_snapshot,
            OrderItem.quantity,
            OrderItem.line_total,
        )
        .join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        .where(SellerOrder.order_id.in_(order_ids))
        .order_by(SellerOrder.order_id, OrderItem.id)
    ).all()
    grouped: dict[uuid.UUID, list[SessionOrderItemViewModel]] = {}
    for order_id, product_name, quantity, line_total in rows:
        grouped.setdefault(order_id, []).append(
            SessionOrderItemViewModel(
                product_name=product_name,
                quantity=quantity,
                line_total=line_total,
            )
        )
    return {order_id: tuple(items) for order_id, items in grouped.items()}


def _home_catalog_sequence(
    *,
    query_text: str,
    selected_category: str,
    day: date,
) -> tuple[str, str, str, list[Any]]:
    listings = load_public_listings(
        db.session,
        category_slug=selected_category or None,
    )
    if query_text:
        surface = SURFACE_SEARCH
        ranking_context = private_search_context(query_text)
    elif selected_category:
        surface = SURFACE_CATEGORY
        ranking_context = selected_category
    else:
        surface = SURFACE_HOME
        ranking_context = ""
    ranked = rank_listings_v1(
        listings,
        surface=surface,
        context=ranking_context,
        query=query_text or None,
        day=day,
    )
    sequence = apply_soft_diversity(
        ranked,
        limit=len(ranked),
        max_per_product=current_app.config["HOME_MAX_LISTINGS_PER_PRODUCT"],
        max_per_store=current_app.config["HOME_MAX_LISTINGS_PER_STORE"],
    )
    context_hash = catalog_feed_context_hash(
        surface=surface,
        query=query_text,
        category_slug=selected_category,
    )
    return surface, ranking_context, context_hash, sequence


def _signed_feed_cursor(
    *,
    day: date,
    surface: str,
    context_hash: str,
    category_slug: str | None,
    store_slug: str | None,
    next_position: int,
    ranking_request_id: uuid.UUID,
    batch_size: int,
) -> str:
    return sign_catalog_feed_cursor(
        current_app.config["SECRET_KEY"],
        CatalogFeedCursor(
            version=CATALOG_FEED_CURSOR_VERSION,
            ranking_day=day,
            surface=surface,
            context_hash=context_hash,
            category_slug=category_slug or None,
            store_slug=store_slug or None,
            next_position=next_position,
            ranking_request_id=ranking_request_id,
            batch_size=batch_size,
        ),
    )


def _invalid_feed_cursor_response(message: str):
    return jsonify(
        ok=False,
        error="invalid_cursor",
        message=message,
    ), 400


@storefront.get("/")
def home() -> str:
    query_text = _normalize_query_parameter(
        request.args.get("q"),
        MAX_SEARCH_LENGTH,
    )
    selected_category = _normalize_query_parameter(
        request.args.get("category"),
        MAX_CATEGORY_LENGTH,
    )

    feed_day = ranking_day()
    surface, _context, context_hash, sequence = _home_catalog_sequence(
        query_text=query_text,
        selected_category=selected_category,
        day=feed_day,
    )
    batch_size = current_app.config["CATALOG_FEED_BATCH_SIZE"]
    rows = sequence[:batch_size]
    ranking_request_id = uuid.uuid4()
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    products = _cards_from_rows(rows, placeholder_image)
    products = _cards_with_ranking_context(
        products,
        rows,
        surface=surface,
        ranking_request_id=ranking_request_id,
        shadow_listings=sequence,
    )
    next_cursor = (
        _signed_feed_cursor(
            day=feed_day,
            surface=surface,
            context_hash=context_hash,
            category_slug=selected_category or None,
            store_slug=None,
            next_position=len(rows),
            ranking_request_id=ranking_request_id,
            batch_size=batch_size,
        )
        if len(rows) < len(sequence)
        else None
    )

    return render_template(
        "storefront/home.html",
        products=products,
        categories=_load_categories(),
        query_text=query_text,
        selected_category=selected_category,
        placeholder_count=max(0, 5 - len(products)),
        total_results=len(sequence),
        feed_surface=surface,
        feed_context_hash=context_hash,
        feed_next_cursor=next_cursor,
        feed_has_more=next_cursor is not None,
        feed_loaded_count=len(rows),
        current_section=(
            "catalog" if query_text or selected_category else "home"
        ),
    )


@storefront.get("/catalogo/feed")
def catalog_feed():
    token = (request.args.get("cursor") or "").strip()
    if not token:
        return _invalid_feed_cursor_response(
            "Se requiere un cursor para continuar el catálogo."
        )
    try:
        cursor = load_catalog_feed_cursor(
            current_app.config["SECRET_KEY"],
            token,
            max_age_seconds=current_app.config[
                "CATALOG_FEED_CURSOR_TTL_SECONDS"
            ],
        )
    except InvalidCatalogFeedCursorError as exc:
        return _invalid_feed_cursor_response(str(exc))

    query_text = _normalize_query_parameter(
        request.args.get("q"),
        MAX_SEARCH_LENGTH,
    )
    selected_category = _normalize_query_parameter(
        request.args.get("category"),
        MAX_CATEGORY_LENGTH,
    )
    store_slug = _normalize_query_parameter(
        request.args.get("store"),
        MAX_CATEGORY_LENGTH,
    )

    if cursor.surface == SURFACE_STORE:
        expected_hash = catalog_feed_context_hash(
            surface=SURFACE_STORE,
            store_slug=store_slug,
        )
        if (
            not store_slug
            or store_slug != cursor.store_slug
            or selected_category
            or query_text
            or expected_hash != cursor.context_hash
        ):
            return _invalid_feed_cursor_response(
                "El cursor no corresponde a esta tienda."
            )
        catalog = get_public_store_catalog(
            db.session,
            store_slug=store_slug,
            day=cursor.ranking_day,
        )
        if catalog is None:
            abort(404)
        sequence = list(catalog.rows)
    else:
        if store_slug:
            return _invalid_feed_cursor_response(
                "El cursor no corresponde a esta superficie."
            )
        surface, _context, expected_hash, sequence = _home_catalog_sequence(
            query_text=query_text,
            selected_category=selected_category,
            day=cursor.ranking_day,
        )
        if (
            surface != cursor.surface
            or (selected_category or None) != cursor.category_slug
            or expected_hash != cursor.context_hash
        ):
            return _invalid_feed_cursor_response(
                "El cursor no corresponde a estos filtros."
            )

    start = min(cursor.next_position, len(sequence))
    end = min(start + cursor.batch_size, len(sequence))
    rows = sequence[start:end]
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    products = _cards_from_rows(rows, placeholder_image)
    products = _cards_with_ranking_context(
        products,
        rows,
        surface=cursor.surface,
        ranking_request_id=cursor.ranking_request_id,
        position_offset=start,
        shadow_listings=sequence,
    )
    if cursor.surface == SURFACE_STORE:
        card_next_url = url_for(
            "storefront.store_page",
            store_slug=cursor.store_slug,
        )
    else:
        next_values: dict[str, str] = {}
        if query_text:
            next_values["q"] = query_text
        if selected_category:
            next_values["category"] = selected_category
        card_next_url = url_for("storefront.home", **next_values)
    has_more = end < len(sequence)
    next_cursor = (
        _signed_feed_cursor(
            day=cursor.ranking_day,
            surface=cursor.surface,
            context_hash=cursor.context_hash,
            category_slug=cursor.category_slug,
            store_slug=cursor.store_slug,
            next_position=end,
            ranking_request_id=cursor.ranking_request_id,
            batch_size=cursor.batch_size,
        )
        if has_more
        else None
    )
    return jsonify(
        ok=True,
        html=render_template(
            "components/product_cards_fragment.html",
            products=products,
            card_next_url=card_next_url,
        ),
        next_cursor=next_cursor,
        has_more=has_more,
        loaded_count=end,
    )


@storefront.get("/carrito")
def cart() -> str:
    cart_view, category_ids, product_ids = _rehydrate_cart()
    return render_template(
        "storefront/cart.html",
        cart=cart_view,
        recommendations=_cart_recommendations(
            category_ids,
            product_ids,
        ),
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="cart",
    )


@storefront.get("/favoritos")
@login_required
def favorites() -> str:
    page = normalize_favorites_page(request.args.get("page"))
    favorites_page = get_favorites_page(
        db.session,
        user_id=current_user.id,
        page=page,
        page_size=current_app.config["FAVORITES_PAGE_SIZE"],
    )
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    review_stats = review_stats_for_product_ids(
        db.session,
        {item.product_id for item in favorites_page.items},
    )
    media_by_variant_id = load_product_card_media(
        db.session,
        {
            (item.product_id, item.variant_id)
            for item in favorites_page.items
            if item.variant_id is not None
        },
        media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
    )
    products = [
        _card_from_favorite_item(
            item,
            placeholder_image,
            review_stats,
            media_by_variant_id,
        )
        for item in favorites_page.items
    ]
    return render_template(
        "storefront/favorites.html",
        favorites_page=favorites_page,
        products=products,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="favorites",
    )


@storefront.get("/checkout")
def checkout() -> str:
    auth_redirect = _requires_verified_identity()
    if auth_redirect is not None:
        return auth_redirect
    cart_state = get_cart_state(flask_session.get(CART_SESSION_KEY))
    try:
        preview = build_checkout_preview(
            session=db.session, cart_state=cart_state
        )
    except EmptyCheckoutError as exc:
        flash(str(exc), "error")
        return redirect(url_for("storefront.cart"))

    buyer = _checkout_buyer()
    if buyer is None:
        flash(
            "No existe el comprador de demostración. Ejecuta seed-demo.",
            "error",
        )
        return redirect(url_for("storefront.cart"))

    signature = _checkout_cart_signature(cart_state)
    draft = flask_session.get(CHECKOUT_DRAFT_SESSION_KEY)
    if not isinstance(draft, dict) or draft.get("signature") != signature:
        draft = {
            "token": secrets.token_urlsafe(32),
            "signature": signature,
        }
        flask_session[CHECKOUT_DRAFT_SESSION_KEY] = draft

    return render_template(
        "storefront/checkout.html",
        preview=preview,
        buyer=CheckoutBuyerViewModel(buyer.full_name, buyer.phone),
        checkout_token=draft["token"],
        pickup_name=current_app.config["ECUVEL_PICKUP_POINT_NAME"],
        pickup_address=current_app.config["ECUVEL_PICKUP_POINT_ADDRESS"],
        order_hold_days=current_app.config["ECUVEL_ORDER_HOLD_DAYS"],
        pickup_is_free=current_app.config["ECUVEL_PICKUP_IS_FREE"],
        placeholder_image=url_for(
            "static",
            filename="images/placeholders/product-placeholder.svg",
        ),
    )


@storefront.post("/checkout")
def create_checkout():
    auth_redirect = _requires_verified_identity()
    if auth_redirect is not None:
        return auth_redirect
    token = (request.form.get("checkout_token") or "").strip()
    completed = flask_session.get(COMPLETED_CHECKOUTS_SESSION_KEY, {})
    if isinstance(completed, dict) and token in completed:
        value = completed[token]
        if isinstance(value, dict) and value.get("order_number"):
            return redirect(
                url_for(
                    "storefront.bank_transfer",
                    order_number=value["order_number"],
                )
            )

    draft = flask_session.get(CHECKOUT_DRAFT_SESSION_KEY)
    cart_state = get_cart_state(flask_session.get(CART_SESSION_KEY))
    signature = _checkout_cart_signature(cart_state)
    if (
        not isinstance(draft, dict)
        or not token
        or not hmac.compare_digest(str(draft.get("token", "")), token)
        or not hmac.compare_digest(str(draft.get("signature", "")), signature)
    ):
        flash(
            "El checkout cambió o caducó. Revísalo antes de continuar.",
            "error",
        )
        return redirect(url_for("storefront.checkout"))

    try:
        payment_method = PaymentMethod(request.form.get("payment_method", ""))
    except ValueError:
        flash("Selecciona un método de pago válido.", "error")
        return redirect(url_for("storefront.checkout"))

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=current_app.config["BANK_TRANSFER_PAYMENT_TIMEOUT_MINUTES"]
    )
    # El POST no realiza lecturas SQL antes de este punto. Removemos
    # cualquier sesión de lectura preservada por el contexto de pruebas
    # para garantizar una transacción de escritura nueva y única.
    db.session.remove()
    database_session = db.session()
    try:
        with database_session.begin():
            if current_user.is_authenticated:
                buyer = database_session.get(User, current_user.id)
            elif current_app.config["ALLOW_DEMO_CHECKOUT"]:
                buyer = database_session.scalar(
                    select(User).where(
                        User.email
                        == current_app.config["CHECKOUT_DEMO_BUYER_EMAIL"]
                    )
                )
            else:
                buyer = None
            if buyer is None:
                raise CheckoutServiceError(
                    "Inicia sesión para realizar el pedido."
                )
            result = create_checkout_order(
                session=database_session,
                buyer_id=buyer.id,
                cart_state=cart_state,
                payment_method=payment_method,
                idempotency_key=token,
                reservation_expires_at=expires_at,
            )
    except CheckoutServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("storefront.checkout"))

    updated_cart = cart_state
    for offer_id in result.purchased_offer_ids:
        updated_cart = remove_cart_item(updated_cart, offer_id)
    _save_cart_state(updated_cart)
    _remember_checkout_order(result.order_id)

    completed = (
        completed if isinstance(completed, dict) else {}
    )
    completed[token] = {
        "order_id": str(result.order_id),
        "order_number": result.order_number,
    }
    flask_session[COMPLETED_CHECKOUTS_SESSION_KEY] = dict(
        list(completed.items())[-5:]
    )
    flask_session.pop(CHECKOUT_DRAFT_SESSION_KEY, None)
    flash("Pedido creado. Completa la transferencia antes del vencimiento.", "success")
    return redirect(
        url_for(
            "storefront.bank_transfer",
            order_number=result.order_number,
        )
    )


@storefront.get("/checkout/transferencia/<string:order_number>")
@login_required
def bank_transfer(order_number: str) -> str:
    if _maybe_expire_authorized_order(order_number):
        flash("El tiempo para enviar el comprobante venció y la reserva fue liberada.", "warning")
        return redirect(url_for("storefront.orders"))
    order = _authorized_pending_order(order_number)
    upload_tokens = flask_session.get(PAYMENT_PROOF_UPLOADS_SESSION_KEY, {})
    upload_tokens = upload_tokens if isinstance(upload_tokens, dict) else {}
    if order.proof_id is None and order.payment_status == "AWAITING_PROOF":
        upload_token = upload_tokens.get(str(order.payment_attempt_id))
        if not isinstance(upload_token, str) or not upload_token:
            upload_token = secrets.token_urlsafe(32)
            upload_tokens[str(order.payment_attempt_id)] = upload_token
            flask_session[PAYMENT_PROOF_UPLOADS_SESSION_KEY] = dict(
                list(upload_tokens.items())[-MAX_SESSION_CHECKOUT_ORDERS:]
            )
    else:
        upload_token = None
    bank_details = {
        "bank_name": current_app.config.get("BANK_TRANSFER_BANK_NAME"),
        "account_holder": current_app.config.get(
            "BANK_TRANSFER_ACCOUNT_HOLDER"
        ),
        "account_number": current_app.config.get(
            "BANK_TRANSFER_ACCOUNT_NUMBER"
        ),
        "holder_id": current_app.config.get("BANK_TRANSFER_HOLDER_ID"),
        "email": current_app.config.get("BANK_TRANSFER_EMAIL"),
        "qr_image": current_app.config.get("BANK_TRANSFER_QR_IMAGE"),
    }
    configured = all(
        bank_details[key]
        for key in (
            "bank_name",
            "account_holder",
            "account_number",
            "holder_id",
            "email",
        )
    )
    return render_template(
        "storefront/bank_transfer.html",
        order=order,
        bank=bank_details,
        bank_configured=configured,
        upload_token=upload_token,
        proof_max_bytes=current_app.config["PAYMENT_PROOF_MAX_BYTES"],
    )


@storefront.post(
    "/checkout/transferencia/<string:order_number>/comprobante"
)
@login_required
def upload_payment_proof(order_number: str):
    if _maybe_expire_authorized_order(order_number):
        flash("La reserva venció; no es posible cargar el comprobante.", "error")
        return redirect(url_for("storefront.orders"))
    order = _authorized_pending_order(order_number)
    if order.proof_id is not None or order.payment_status != PaymentStatus.AWAITING_PROOF.value:
        flash("Este pago ya no admite una nueva carga de comprobante.", "error")
        return redirect(url_for("storefront.orders"))
    tokens = flask_session.get(PAYMENT_PROOF_UPLOADS_SESSION_KEY, {})
    provided_token = (request.form.get("upload_token") or "").strip()
    expected_token = (
        tokens.get(str(order.payment_attempt_id))
        if isinstance(tokens, dict)
        else None
    )
    if (
        not isinstance(expected_token, str)
        or not provided_token
        or not hmac.compare_digest(expected_token, provided_token)
    ):
        flash("La carga caducó. Selecciona el archivo nuevamente.", "error")
        return redirect(
            url_for("storefront.bank_transfer", order_number=order_number)
        )
    uploaded_file = request.files.get("proof_file")
    if uploaded_file is None:
        flash("Selecciona un comprobante JPEG, PNG o PDF.", "error")
        return redirect(
            url_for("storefront.bank_transfer", order_number=order_number)
        )

    staged = None
    promoted_path = None
    try:
        staged = stage_payment_proof(
            uploaded_file,
            root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            max_bytes=current_app.config["PAYMENT_PROOF_MAX_BYTES"],
            allowed_extensions=current_app.config[
                "PAYMENT_PROOF_ALLOWED_EXTENSIONS"
            ],
            allowed_media_types=current_app.config[
                "PAYMENT_PROOF_ALLOWED_MEDIA_TYPES"
            ],
        )
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = submit_bank_transfer_proof(
                session=database_session,
                payment_attempt_id=order.payment_attempt_id,
                staged_file=staged,
                upload_idempotency_key=provided_token,
                storage_root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
                uploaded_by_user_id=order.buyer_id,
            )
            if not result.replayed:
                promoted_path = result.storage_path
    except PaymentProofExpiredError as exc:
        delete_private_file(staged.temporary_path if staged else None)
        delete_private_file(promoted_path)
        _maybe_expire_authorized_order(order_number)
        flash(str(exc), "error")
        return redirect(url_for("storefront.orders"))
    except (PrivateStorageError, PaymentProofServiceError) as exc:
        delete_private_file(staged.temporary_path if staged else None)
        delete_private_file(promoted_path)
        flash(str(exc), "error")
        return redirect(
            url_for("storefront.bank_transfer", order_number=order_number)
        )
    except Exception:
        delete_private_file(staged.temporary_path if staged else None)
        delete_private_file(promoted_path)
        current_app.logger.exception("Falló la carga privada del comprobante")
        flash("No pudimos guardar el comprobante. Inténtalo nuevamente.", "error")
        return redirect(
            url_for("storefront.bank_transfer", order_number=order_number)
        )

    flash("Comprobante recibido. Está en revisión.", "success")
    if current_app.config["PAYMENT_PRECHECK_ENABLED"]:
        db.session.remove()
        try:
            analyze_payment_proof(
                session_factory=db.session,
                payment_proof_id=result.proof_id,
                config=PaymentPrecheckConfig.from_mapping(current_app.config),
            )
        except Exception:
            current_app.logger.error(
                "No se pudo completar la prevalidación proof_id=%s",
                result.proof_id,
            )
    return redirect(
        url_for("storefront.payment_pending", order_number=order_number)
    )


@storefront.get("/pagos/comprobantes/<uuid:proof_id>/archivo")
@login_required
def private_payment_proof(proof_id: uuid.UUID):
    allowed_ids = _allowed_checkout_order_ids()
    row = db.session.execute(
        select(PaymentProof, Order)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentProof.payment_attempt_id)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .where(PaymentProof.id == proof_id, Order.id.in_(allowed_ids))
    ).one_or_none()
    if row is None:
        abort(404)
    proof, _ = row
    try:
        path = verify_private_file(
            root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            storage_key=proof.storage_key,
            size_bytes=proof.size_bytes,
            sha256=proof.sha256,
        )
    except PrivateStorageError:
        abort(404)
    response = send_file(
        path,
        mimetype=proof.media_type,
        as_attachment=False,
        download_name=proof.original_filename,
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@storefront.get("/checkout/pendiente/<string:order_number>")
@login_required
def payment_pending(order_number: str) -> str:
    _maybe_expire_authorized_order(order_number)
    return render_template(
        "storefront/payment_pending.html",
        order=_authorized_pending_order(order_number),
    )


@storefront.get("/pedidos")
@login_required
def orders() -> str:
    allowed_ids = _allowed_checkout_order_ids()
    active_filter = normalize_orders_filter(request.args.get("estado"))
    page = normalize_page(request.args.get("page"))
    orders_page = get_customer_orders_page(
        session=db.session,
        order_ids=allowed_ids,
        active_filter=active_filter,
        page=page,
        page_size=current_app.config["CUSTOMER_ORDERS_PAGE_SIZE"],
        pickup_point_name=current_app.config["ECUVEL_PICKUP_POINT_NAME"],
        pickup_point_address=current_app.config["ECUVEL_PICKUP_POINT_ADDRESS"],
    )
    return render_template(
        "storefront/orders.html",
        orders_page=orders_page,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="orders",
        placeholder_image=url_for(
            "static",
            filename="images/placeholders/product-placeholder.svg",
        ),
    )


@storefront.get("/pedidos/<string:order_number>")
@login_required
def order_detail(order_number: str) -> str:
    detail = get_customer_order_detail(
        session=db.session,
        order_number=order_number,
        order_ids=_allowed_checkout_order_ids(),
        pickup_point_name=current_app.config["ECUVEL_PICKUP_POINT_NAME"],
        pickup_point_address=current_app.config["ECUVEL_PICKUP_POINT_ADDRESS"],
    )
    if detail is None:
        abort(404)
    return render_template(
        "storefront/order_detail.html",
        detail=detail,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="orders",
        placeholder_image=url_for(
            "static",
            filename="images/placeholders/product-placeholder.svg",
        ),
    )


@storefront.get("/pedidos/<string:order_number>/productos/<uuid:order_item_id>/resena")
@login_required
def product_review_form(order_number: str, order_item_id: uuid.UUID) -> str:
    wants_modal = (
        request.args.get("modal") == "1"
        or request.headers.get("X-Requested-With") == "fetch"
    )
    try:
        target = review_target_for_order_item(
            session=db.session,
            order_number=order_number,
            order_item_id=order_item_id,
            user_id=current_user.id,
        )
    except ProductReviewEligibilityError:
        abort(404)
    if target.existing_review_id is not None:
        if wants_modal:
            return ("Este producto ya tiene una reseña.", 409)
        return redirect(
            url_for(
                "storefront.my_product_review",
                order_number=order_number,
                order_item_id=order_item_id,
            )
        )
    if not target.delivered:
        if wants_modal:
            return ("Solo puedes reseñar productos que ya fueron entregados.", 403)
        flash("Solo puedes reseñar productos que ya fueron entregados.", "warning")
        return redirect(url_for("storefront.order_detail", order_number=order_number))
    template = (
        "reviews/_product_review_form.html"
        if wants_modal
        else "storefront/product_review_form.html"
    )
    return render_template(
        template,
        target=target,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="orders",
        placeholder_image=url_for(
            "static",
            filename="images/placeholders/product-placeholder.svg",
        ),
        max_images=current_app.config["PRODUCT_REVIEW_MAX_IMAGES"],
        min_body_length=current_app.config["PRODUCT_REVIEW_MIN_BODY_LENGTH"],
        max_body_length=current_app.config["PRODUCT_REVIEW_MAX_BODY_LENGTH"],
    )


@storefront.post("/pedidos/<string:order_number>/productos/<uuid:order_item_id>/resena")
@login_required
@limiter.limit("5 per minute")
def submit_product_review(order_number: str, order_item_id: uuid.UUID):
    user_id = current_user.id
    config = _product_review_image_config()
    staged_images = ()
    promoted = False
    wants_json = "application/json" in (request.headers.get("Accept") or "")
    try:
        staged_images = stage_product_review_images(
            request.files.getlist("images"),
            config=config,
        )
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = create_product_review(
                session=database_session,
                order_number=order_number,
                order_item_id=order_item_id,
                user_id=user_id,
                rating=request.form.get("rating"),
                body=request.form.get("body", ""),
                staged_images=staged_images,
                min_body_length=current_app.config["PRODUCT_REVIEW_MIN_BODY_LENGTH"],
                max_body_length=current_app.config["PRODUCT_REVIEW_MAX_BODY_LENGTH"],
            )
            promote_product_review_images(
                staged_images,
                storage_root=config.root,
            )
            promoted = True
    except (
        ProductReviewDuplicateError,
        ProductReviewEligibilityError,
        ProductReviewImageError,
        ProductReviewServiceError,
        PrivateStorageError,
    ) as exc:
        cleanup_staged_product_review_images(
            staged_images,
            storage_root=config.root,
            include_final=promoted,
        )
        db.session.remove()
        if wants_json:
            return jsonify(ok=False, message=str(exc)), 400
        flash(str(exc), "error")
        return redirect(
            url_for(
                "storefront.product_review_form",
                order_number=order_number,
                order_item_id=order_item_id,
            )
        )
    except Exception:
        cleanup_staged_product_review_images(
            staged_images,
            storage_root=config.root,
            include_final=promoted,
        )
        db.session.remove()
        raise
    finally:
        db.session.remove()
    if wants_json:
        message = (
            "Reseña publicada correctamente."
            if result.status == ProductReviewStatus.PUBLISHED
            else "Reseña enviada a revisión manual."
        )
        return jsonify(
            ok=True,
            review_status=result.status.value,
            message=message,
        )
    flash(
        "Tu reseña ya está publicada."
        if result.status == ProductReviewStatus.PUBLISHED
        else "Recibimos tu reseña para revisión manual.",
        "success",
    )
    return redirect(
        url_for(
            "storefront.my_product_review",
            order_number=order_number,
            order_item_id=result.order_item_id,
        )
    )


@storefront.get("/pedidos/<string:order_number>/productos/<uuid:order_item_id>/mi-resena")
@login_required
def my_product_review(order_number: str, order_item_id: uuid.UUID) -> str:
    try:
        review = own_review_for_order_item(
            session=db.session,
            order_number=order_number,
            order_item_id=order_item_id,
            user_id=current_user.id,
        )
    except ProductReviewNotFoundError:
        abort(404)
    return render_template(
        "storefront/my_product_review.html",
        review=review,
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="orders",
    )


@storefront.post("/pedidos/<string:order_number>/productos/<uuid:order_item_id>/mi-resena/reenviar")
@login_required
@limiter.limit("5 per minute")
def resubmit_own_product_review(order_number: str, order_item_id: uuid.UUID):
    config = _product_review_image_config()
    staged_images = ()
    promoted = False
    try:
        own_review = own_review_for_order_item(
            session=db.session,
            order_number=order_number,
            order_item_id=order_item_id,
            user_id=current_user.id,
        )
        staged_images = stage_product_review_images(
            request.files.getlist("images"), config=config
        )
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = resubmit_product_review(
                session=database_session,
                review_id=own_review.review_id,
                user_id=current_user.id,
                rating=request.form.get("rating"),
                body=request.form.get("body", ""),
                staged_images=staged_images,
                min_body_length=current_app.config["PRODUCT_REVIEW_MIN_BODY_LENGTH"],
                max_body_length=current_app.config["PRODUCT_REVIEW_MAX_BODY_LENGTH"],
            )
            promote_product_review_images(staged_images, storage_root=config.root)
            promoted = True
    except (ProductReviewServiceError, PrivateStorageError) as exc:
        cleanup_staged_product_review_images(
            staged_images, storage_root=config.root, include_final=promoted
        )
        db.session.remove()
        flash(str(exc), "error")
    except Exception:
        cleanup_staged_product_review_images(
            staged_images, storage_root=config.root, include_final=promoted
        )
        db.session.remove()
        raise
    else:
        flash(
            "Tu reseña corregida ya está publicada."
            if result.status == ProductReviewStatus.PUBLISHED
            else "Tu reseña corregida fue enviada a revisión manual.",
            "success",
        )
    finally:
        db.session.remove()
    return redirect(url_for(
        "storefront.my_product_review",
        order_number=order_number,
        order_item_id=order_item_id,
    ))


@storefront.get("/resenas/imagenes/<string:public_id>")
def product_review_image(public_id: str):
    image = db.session.scalar(
        select(ProductReviewImage).where(ProductReviewImage.public_id == public_id)
    )
    if image is None:
        abort(404)
    review = image.review
    is_current = image.revision_id == review.current_revision_id
    is_published = review.status == ProductReviewStatus.PUBLISHED and is_current
    is_owner = current_user.is_authenticated and review.user_id == current_user.id
    if not is_current or (not is_published and not is_owner):
        abort(404)
    try:
        path = private_file_path(
            current_app.config["PRODUCT_REVIEW_UPLOAD_DIR"],
            image.storage_key,
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        mimetype=image.media_type,
        as_attachment=False,
        download_name=image.original_filename,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@storefront.post("/pedidos/<string:order_number>/cancelar")
@login_required
def cancel_order(order_number: str):
    order = _authorized_pending_order(order_number)
    if not order.can_cancel:
        flash("Este pedido ya no puede cancelarse desde aquí.", "error")
        return redirect(url_for("storefront.orders"))
    db.session.remove()
    database_session = db.session()
    try:
        with database_session.begin():
            cancel_pending_bank_transfer_order(
                session=database_session,
                payment_attempt_id=order.payment_attempt_id,
                actor_user_id=order.buyer_id,
                reason="Pedido cancelado por el comprador antes del comprobante.",
            )
    except InvalidPendingPaymentTransitionError as exc:
        flash(str(exc), "error")
    except PendingPaymentServiceError as exc:
        flash(str(exc), "error")
    else:
        flash("Pedido cancelado. Liberamos la reserva de inventario.", "success")
    finally:
        db.session.remove()
    return redirect(url_for("storefront.orders"))


@storefront.app_errorhandler(RequestEntityTooLarge)
def payment_proof_too_large(_error):
    flash("El comprobante no puede superar 10 MiB.", "error")
    if request.endpoint == "storefront.upload_payment_proof":
        return redirect(
            url_for(
                "storefront.bank_transfer",
                order_number=request.view_args.get("order_number", ""),
            )
        )
    return "La solicitud supera el tamaño permitido.", 413


@storefront.post("/carrito/agregar")
def add_to_cart():
    next_url = _safe_next_url(_request_value("next"))
    try:
        offer_id = uuid.UUID(_request_value("offer_id") or "")
        quantity = _validated_cart_quantity(_request_value("quantity"))
    except (ValueError, CartServiceError) as exc:
        return _cart_error_response(
            message=str(exc) or "Los datos del producto no son válidos.",
            error="invalid_quantity",
            status=422,
            redirect_url=next_url,
        )

    rows = _cart_offer_rows({offer_id})
    if not rows:
        return _cart_error_response(
            message="La oferta solicitada ya no existe.",
            error="offer_not_found",
            status=404,
            redirect_url=next_url,
        )
    row = rows[0]
    is_visible = all(
        (
            row.offer_status == OfferStatus.ACTIVE,
            row.currency == "USD",
            row.variant_is_active,
            row.product_is_active,
            row.category_is_active,
            row.store_status == StoreStatus.ACTIVE,
            row.store_is_verified,
        )
    )
    if not is_visible:
        return _cart_error_response(
            message="Este producto ya no está disponible.",
            error="offer_unavailable",
            status=409,
            redirect_url=next_url,
        )

    available_quantity = max(
        0, _availability_by_offer_ids({offer_id}).get(offer_id, 0)
    )
    max_quantity = min(MAX_CART_QUANTITY, available_quantity)
    state_before = get_cart_state(flask_session.get(CART_SESSION_KEY))
    existing_item = state_before["items"].get(str(offer_id))
    current_quantity = (
        int(existing_item["quantity"]) if existing_item is not None else 0
    )
    requested_total = current_quantity + quantity
    if requested_total > max_quantity:
        if current_quantity:
            message = (
                f"Solo hay {available_quantity} unidades disponibles de "
                f"{row.product_title}. Ya tienes {current_quantity} "
                "unidades en el carrito."
            )
        else:
            message = (
                f"Solo hay {available_quantity} unidades disponibles de "
                f"{row.product_title}."
            )
        return _cart_error_response(
            message=message,
            error="insufficient_stock",
            status=409,
            redirect_url=next_url,
            available_quantity=available_quantity,
            current_cart_quantity=current_quantity,
            requested_quantity=quantity,
            max_quantity=max_quantity,
        )

    try:
        state = add_cart_item(
            state_before,
            offer_id,
            quantity,
        )
    except CartServiceError as exc:
        return _cart_error_response(
            message=str(exc),
            error="invalid_quantity",
            status=422,
            redirect_url=next_url,
        )

    _save_cart_state(state)
    ranking_context = _submitted_ranking_context()
    if ranking_context is not None and ranking_context.offer_id == offer_id:
        _record_server_action_event("ADD_TO_CART", ranking_context)
    return _cart_success_response(
        message="Producto añadido al carrito.",
        redirect_url=next_url,
        quantity=requested_total,
        max_quantity=max_quantity,
    )


@storefront.post("/carrito/items/<uuid:offer_id>/cantidad")
def update_cart_quantity(offer_id: uuid.UUID):
    redirect_url = url_for("storefront.cart")
    state_before = get_cart_state(flask_session.get(CART_SESSION_KEY))
    existing_item = state_before["items"].get(str(offer_id))
    current_quantity = (
        int(existing_item["quantity"]) if existing_item is not None else 0
    )
    try:
        quantity = _validated_cart_quantity(_request_value("quantity"))
    except CartServiceError as exc:
        return _cart_error_response(
            message=str(exc),
            error="invalid_quantity",
            status=422,
            redirect_url=redirect_url,
            current_cart_quantity=current_quantity,
        )

    rows = _cart_offer_rows({offer_id})
    if existing_item is None or not rows:
        return _cart_error_response(
            message="El producto ya no está en el carrito.",
            error="cart_item_not_found",
            status=404,
            redirect_url=redirect_url,
        )
    row = rows[0]
    is_visible = all(
        (
            row.offer_status == OfferStatus.ACTIVE,
            row.currency == "USD",
            row.variant_is_active,
            row.product_is_active,
            row.category_is_active,
            row.store_status == StoreStatus.ACTIVE,
            row.store_is_verified,
        )
    )
    if not is_visible:
        return _cart_error_response(
            message="Este producto ya no está disponible.",
            error="offer_unavailable",
            status=409,
            redirect_url=redirect_url,
            current_cart_quantity=current_quantity,
        )
    available_quantity = max(
        0, _availability_by_offer_ids({offer_id}).get(offer_id, 0)
    )
    max_quantity = min(MAX_CART_QUANTITY, available_quantity)
    if quantity > max_quantity:
        return _cart_error_response(
            message=(
                f"Solo hay {available_quantity} unidades disponibles de "
                f"{row.product_title}."
            ),
            error="insufficient_stock",
            status=409,
            redirect_url=redirect_url,
            available_quantity=available_quantity,
            current_cart_quantity=current_quantity,
            requested_quantity=quantity,
            max_quantity=max_quantity,
        )

    try:
        state = set_cart_item_quantity(
            state_before,
            offer_id,
            quantity,
        )
    except CartServiceError as exc:
        return _cart_error_response(
            message=str(exc),
            error="invalid_quantity",
            status=422,
            redirect_url=redirect_url,
            current_cart_quantity=current_quantity,
        )

    _save_cart_state(state)
    return _cart_success_response(
        message="Cantidad actualizada.",
        redirect_url=redirect_url,
        quantity=quantity,
        max_quantity=max_quantity,
    )


@storefront.post("/carrito/items/<uuid:offer_id>/seleccion")
def update_cart_selection(offer_id: uuid.UUID):
    state = set_cart_item_selected(
        flask_session.get(CART_SESSION_KEY),
        offer_id,
        _form_selected(),
    )
    _save_cart_state(state)
    return redirect(url_for("storefront.cart"))


@storefront.post("/carrito/seleccion")
def update_all_cart_selection():
    state = set_all_cart_items_selected(
        flask_session.get(CART_SESSION_KEY),
        _form_selected(),
    )
    _save_cart_state(state)
    return redirect(url_for("storefront.cart"))


@storefront.post("/carrito/items/<uuid:offer_id>/eliminar")
def delete_cart_item(offer_id: uuid.UUID):
    state = remove_cart_item(
        flask_session.get(CART_SESSION_KEY),
        offer_id,
    )
    _save_cart_state(state)
    flash("Producto eliminado del carrito.", "success")
    return redirect(url_for("storefront.cart"))


@storefront.post("/carrito/eliminar-seleccionados")
def delete_selected_cart_items():
    state = remove_selected_cart_items(
        flask_session.get(CART_SESSION_KEY)
    )
    _save_cart_state(state)
    flash("Productos seleccionados eliminados.", "success")
    return redirect(url_for("storefront.cart"))


@storefront.post("/catalogo/interacciones")
@limiter.limit("120 per minute")
def catalog_interaction():
    event_type = (_request_value("event_type") or "").strip().upper()
    token = _request_value("ranking_context") or ""
    if event_type not in CLIENT_EVENT_TYPES:
        return jsonify(ok=False, error="event_not_allowed"), 422
    try:
        context = load_ranking_context(
            current_app.config["SECRET_KEY"],
            token,
            max_age_seconds=current_app.config[
                "CATALOG_RANKING_CONTEXT_TTL_SECONDS"
            ],
        )
    except InvalidRankingContextError:
        return jsonify(ok=False, error="invalid_context"), 400

    actor_id = current_user.id if current_user.is_authenticated else None
    anonymous_id = (
        None
        if current_user.is_authenticated
        else anonymous_session_id(flask_session)
    )
    try:
        recorded = record_context_event_best_effort(
            db.session,
            event_type=event_type,
            context=context,
            actor_user_id=actor_id,
            anonymous_id=anonymous_id,
        )
    except Exception:
        logger.warning("Catalog client telemetry failed", exc_info=True)
        recorded = False
    return jsonify(ok=True, recorded=recorded), 202


@storefront.post("/favoritos/productos/<string:product_slug>/agregar")
def add_favorite(product_slug: str):
    redirect_url = _favorite_redirect_url(product_slug)
    if not current_user.is_authenticated:
        return _favorite_login_response(redirect_url)
    try:
        result = add_favorite_by_slug(
            db.session,
            user_id=current_user.id,
            product_slug=product_slug,
        )
        db.session.commit()
    except FavoriteProductNotFoundError as exc:
        db.session.rollback()
        if _wants_json_response():
            return jsonify(
                ok=False,
                error="product_unavailable",
                message=str(exc),
            ), 404
        flash(str(exc), "error")
        return redirect(redirect_url)

    ranking_context = _submitted_ranking_context()
    if ranking_context is not None and ranking_context.product_id == result.product_id:
        _record_server_action_event("FAVORITE", ranking_context)

    return _favorite_response(
        result=result,
        message="Producto guardado en favoritos.",
        redirect_url=redirect_url,
    )


@storefront.post("/favoritos/productos/<string:product_slug>/eliminar")
def remove_favorite(product_slug: str):
    redirect_url = _favorite_redirect_url()
    if not current_user.is_authenticated:
        return _favorite_login_response(redirect_url)
    try:
        result = remove_favorite_by_slug(
            db.session,
            user_id=current_user.id,
            product_slug=product_slug,
        )
        db.session.commit()
    except FavoriteProductNotFoundError as exc:
        db.session.rollback()
        if _wants_json_response():
            return jsonify(
                ok=False,
                error="product_not_found",
                message=str(exc),
            ), 404
        flash(str(exc), "error")
        return redirect(redirect_url)

    return _favorite_response(
        result=result,
        message="Producto eliminado de favoritos.",
        redirect_url=redirect_url,
    )


@storefront.get("/tiendas/<string:store_slug>")
def store_page(store_slug: str) -> str:
    feed_day = ranking_day()
    catalog = get_public_store_catalog(
        db.session,
        store_slug=store_slug,
        day=feed_day,
    )
    if catalog is None:
        abort(404)

    batch_size = current_app.config["CATALOG_FEED_BATCH_SIZE"]
    sequence = list(catalog.rows)
    rows = sequence[:batch_size]
    ranking_request_id = uuid.uuid4()
    context_hash = catalog_feed_context_hash(
        surface=SURFACE_STORE,
        store_slug=catalog.store.slug,
    )
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    products = _cards_from_rows(rows, placeholder_image)
    products = _cards_with_ranking_context(
        products,
        rows,
        surface=SURFACE_STORE,
        ranking_request_id=ranking_request_id,
        shadow_listings=sequence,
    )
    next_cursor = (
        _signed_feed_cursor(
            day=feed_day,
            surface=SURFACE_STORE,
            context_hash=context_hash,
            category_slug=None,
            store_slug=catalog.store.slug,
            next_position=len(rows),
            ranking_request_id=ranking_request_id,
            batch_size=batch_size,
        )
        if len(rows) < len(sequence)
        else None
    )
    return render_template(
        "storefront/store.html",
        store=catalog.store,
        products=products,
        total_results=len(sequence),
        feed_surface=SURFACE_STORE,
        feed_context_hash=context_hash,
        feed_next_cursor=next_cursor,
        feed_has_more=next_cursor is not None,
        feed_loaded_count=len(rows),
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="catalog",
    )


@storefront.get("/tiendas/<string:store_slug>/informacion")
def store_information(store_slug: str) -> str:
    information = get_public_store_information(
        db.session,
        store_slug=store_slug,
    )
    if information is None:
        abort(404)
    return _store_modal_context(
        "stores/_store_information.html",
        store_slug=store_slug,
        information=information,
    )


@storefront.get("/tiendas/<string:store_slug>/calificacion")
def store_rating(store_slug: str) -> str:
    rating = get_public_store_rating_summary(
        db.session,
        store_slug=store_slug,
    )
    if rating is None:
        abort(404)
    return _store_modal_context(
        "stores/_store_rating.html",
        store_slug=store_slug,
        rating=rating,
    )


@storefront.get("/tiendas/<string:store_slug>/productos/resumen")
def store_products_summary(store_slug: str) -> str:
    products_summary = get_public_store_products_summary(
        db.session,
        store_slug=store_slug,
    )
    if products_summary is None:
        abort(404)
    return _store_modal_context(
        "stores/_store_products_summary.html",
        store_slug=store_slug,
        products_summary=products_summary,
    )


@storefront.get("/productos/<string:product_slug>")
def product_detail(product_slug: str) -> str:
    product_record = db.session.scalar(
        select(Product)
        .options(selectinload(Product.media))
        .where(
            Product.slug == product_slug,
            Product.is_active.is_(True),
        )
    )
    if product_record is None:
        abort(404)

    product_listings = load_public_listings(
        db.session,
        product_id=product_record.id,
    )
    if not product_listings:
        abort(404)
    all_members = [
        member
        for listing in product_listings
        for member in listing.members
    ]
    requested_sku = (request.args.get("variant") or "").strip()
    requested_member = next(
        (member for member in all_members if member.catalog_sku == requested_sku),
        None,
    )
    selected_store_id = (
        requested_member.store_id
        if requested_member is not None
        else product_listings[0].store_id
    )
    variant_rows = sorted(
        (member for member in all_members if member.store_id == selected_store_id),
        key=lambda item: (
            item.combination_key or "",
            item.catalog_sku,
            str(item.offer_id),
        ),
    )
    availability_by_offer = {
        item.offer_id: item.available_quantity for item in variant_rows
    }
    default_key = (product_record.variant_configuration or {}).get(
        "default_combination_key"
    )
    selected_row = next(
        (item for item in variant_rows if item.catalog_sku == requested_sku),
        None,
    )
    if selected_row is None and default_key:
        selected_row = next(
            (
                item
                for item in variant_rows
                if item.combination_key == default_key
                and availability_by_offer.get(item.offer_id, 0) > 0
            ),
            None,
        )
    if selected_row is None:
        selected_row = next(
            (
                item
                for item in variant_rows
                if availability_by_offer.get(item.offer_id, 0) > 0
            ),
            variant_rows[0],
        )
    row = selected_row

    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    available_quantity = max(
        0, availability_by_offer.get(row.offer_id, 0)
    )
    (
        max_quantity,
        low_stock,
        availability_label,
        availability_message,
    ) = _stock_presentation(available_quantity)
    is_available = available_quantity > 0
    specification_presentation = build_product_specification_presentation(row)
    favorite_ids = _favorite_ids_for_product_ids({row.product_id})
    reviews_page = published_reviews_for_product(
        db.session,
        product_id=row.product_id,
        page=request.args.get("reviews_page"),
        page_size=current_app.config["PRODUCT_REVIEWS_PAGE_SIZE"],
    )
    store_stats = review_stats_for_store_ids(db.session, {row.store_id}).get(row.store_id)
    family_title = (
        f"{row.product_title} — {row.variant_title}"
        if (product_record.variant_configuration or {}).get("mode") == "family"
        and row.variant_title
        else row.product_title
    )
    product = ProductDetailViewModel(
        offer_id=row.offer_id,
        product_id=row.product_id,
        public_identifier=row.product_slug,
        name=family_title,
        description=row.product_description,
        category_name=row.category_name,
        category_url=url_for(
            "storefront.home",
            category=row.category_slug,
        ),
        store_name=row.store_name,
        store_url=url_for("storefront.store_page", store_slug=row.store_slug),
        store_is_verified=row.store_is_verified,
        store_rating=store_stats.average if store_stats else None,
        store_review_count=store_stats.count if store_stats else 0,
        current_price=row.price,
        compare_at_price=_visible_compare_at_price(row),
        currency=row.currency,
        seller_sku=row.seller_sku,
        catalog_sku=row.catalog_sku,
        variant_name=row.variant_title,
        offer_status=row.offer_status,
        gallery_images=_build_product_gallery_images(
            row.product_title,
            _media_for_variant(
                product=product_record,
                attributes=row.variant_attributes or {},
            ),
            product_slug=product_record.slug,
        ),
        gallery_placeholder_url=placeholder_image,
        specifications=specification_presentation.sections,
        highlights=specification_presentation.highlights,
        rating=reviews_page.summary.average,
        review_count=reviews_page.summary.count,
        availability_label=availability_label,
        is_available=is_available,
        available_quantity=available_quantity,
        max_quantity=max_quantity,
        quantity_limit_reached=(is_available and max_quantity == 1),
        low_stock=low_stock,
        availability_message=availability_message,
        delivery_label=_full_delivery_label(row.preparation_time_days),
        is_favorite=row.product_id in favorite_ids,
        variant_payload=_build_variant_payload(
            product=product_record,
            rows=variant_rows,
            availability=availability_by_offer,
            selected_catalog_sku=row.catalog_sku,
        ),
    )

    recommendation_rows = rank_listings_v1(
        load_public_listings(
            db.session,
            category_id=row.category_id,
            exclude_product_ids={row.product_id},
        ),
        surface=SURFACE_RECOMMENDATIONS,
        context=str(row.category_id),
    )[:MAX_RECOMMENDATIONS]
    recommendations = _cards_from_rows(
        recommendation_rows,
        placeholder_image,
    )
    recommendations = _cards_with_ranking_context(
        recommendations,
        recommendation_rows,
        surface=SURFACE_RECOMMENDATIONS,
    )

    return render_template(
        "storefront/product_detail.html",
        product=product,
        recommendations=recommendations,
        reviews_page=reviews_page,
        recommendation_placeholder_count=max(
            0,
            5 - len(recommendations),
        ),
        categories=_load_categories(),
        query_text="",
        selected_category="",
        current_section="catalog",
    )


@storefront.get("/productos/<string:product_slug>/media/<string:public_id>")
def product_media(product_slug: str, public_id: str):
    media = db.session.scalar(
        select(ProductMedia)
        .join(Product, Product.id == ProductMedia.product_id)
        .where(
            Product.slug == product_slug,
            Product.is_active.is_(True),
            ProductMedia.public_id == public_id,
            ProductMedia.is_active.is_(True),
        )
    )
    if media is None:
        abort(404)
    requested_version = (request.args.get("v") or "").strip()
    if requested_version and requested_version != media.content_sha256:
        abort(404)
    try:
        path = private_file_path(
            current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
            media.storage_key,
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=media.media_type, conditional=True, max_age=31536000)
    response.cache_control.public = True
    response.cache_control.max_age = 31536000
    response.cache_control.immutable = bool(
        requested_version and requested_version == media.content_sha256
    )
    return response


@storefront.get(
    "/productos/<string:product_slug>/media/<string:public_id>/thumbnail"
)
def product_media_thumbnail(product_slug: str, public_id: str):
    media = db.session.scalar(
        select(ProductMedia)
        .join(Product, Product.id == ProductMedia.product_id)
        .where(
            Product.slug == product_slug,
            Product.is_active.is_(True),
            ProductMedia.public_id == public_id,
            ProductMedia.is_active.is_(True),
        )
    )
    if media is None or not has_complete_product_thumbnail(media):
        abort(404)
    requested_version = (request.args.get("v") or "").strip()
    if requested_version and requested_version != media.thumbnail_sha256:
        abort(404)
    try:
        path = private_file_path(
            current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
            str(media.thumbnail_storage_key),
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        mimetype=media.thumbnail_media_type,
        conditional=True,
        max_age=31536000,
    )
    response.cache_control.public = True
    response.cache_control.max_age = 31536000
    response.cache_control.immutable = bool(
        requested_version and requested_version == media.thumbnail_sha256
    )
    return response


@storefront.app_errorhandler(404)
def page_not_found(_error):
    return render_template("errors/404.html"), 404

