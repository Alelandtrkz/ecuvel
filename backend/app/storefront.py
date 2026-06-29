from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db, limiter
from app.models import (
    Category,
    Order,
    OrderItem,
    PaymentAttempt,
    PaymentProof,
    Product,
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
    get_public_store_information,
    get_public_store_products_page,
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
    stage_product_review_images,
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
)
from werkzeug.exceptions import RequestEntityTooLarge


storefront = Blueprint("storefront", __name__)

MAX_SEARCH_LENGTH = 100
MAX_CATEGORY_LENGTH = 140
MAX_HOME_OFFERS = 20
MAX_RECOMMENDATIONS = 10
STORE_PUBLIC_PRODUCTS_PER_PAGE = 20
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
    title: str
    current_price: str
    compare_at_price: str | None
    rating: str | None
    review_count: int | None
    is_favorite: bool
    delivery_label: str
    is_available: bool


@dataclass(frozen=True, slots=True)
class ProductGalleryImageViewModel:
    url: str
    thumbnail_url: str
    alt: str
    width: int | None
    height: int | None
    is_primary: bool


@dataclass(frozen=True, slots=True)
class ProductSpecificationViewModel:
    label: str
    value: str


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
    specifications: tuple[ProductSpecificationViewModel, ...]
    highlights: tuple[ProductSpecificationViewModel, ...]
    rating: Decimal | None
    review_count: int
    availability_label: str
    is_available: bool
    available_quantity: int
    max_quantity: int
    quantity_limit_reached: bool
    low_stock: bool
    availability_message: str
    is_favorite: bool


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


def _canonical_offers_subquery():
    return (
        select(
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            Product.title.label("product_title"),
            Product.description.label("product_description"),
            Product.brand.label("product_brand"),
            Product.model_number.label("product_model_number"),
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            ProductVariant.id.label("variant_id"),
            ProductVariant.title.label("variant_title"),
            ProductVariant.catalog_sku.label("catalog_sku"),
            ProductVariant.manufacturer_barcode.label(
                "manufacturer_barcode"
            ),
            ProductVariant.attributes.label("variant_attributes"),
            ProductVariant.weight_grams.label("weight_grams"),
            ProductVariant.length_mm.label("length_mm"),
            ProductVariant.width_mm.label("width_mm"),
            ProductVariant.height_mm.label("height_mm"),
            SellerOffer.id.label("offer_id"),
            SellerOffer.seller_sku.label("seller_sku"),
            SellerOffer.currency.label("currency"),
            SellerOffer.price.label("price"),
            SellerOffer.compare_at_price.label("compare_at_price"),
            SellerOffer.status.label("offer_status"),
            Store.id.label("store_id"),
            Store.name.label("store_name"),
            Store.slug.label("store_slug"),
            Store.is_verified.label("store_is_verified"),
            func.row_number()
            .over(
                partition_by=Product.id,
                order_by=(SellerOffer.price, SellerOffer.id),
            )
            .label("offer_rank"),
        )
        .select_from(SellerOffer)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(
            SellerOffer.status == OfferStatus.ACTIVE,
            ProductVariant.is_active.is_(True),
            Product.is_active.is_(True),
            Category.is_active.is_(True),
            Store.status == StoreStatus.ACTIVE,
        )
        .subquery()
    )


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


def _favorite_ids_for_product_ids(product_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not current_user.is_authenticated:
        return set()
    return favorite_product_ids_for_user(
        db.session,
        current_user.id,
        product_ids,
    )


def _card_from_row(
    row: Any,
    placeholder_image: str,
    favorite_product_ids: set[uuid.UUID] | None = None,
    availability_by_offer_id: dict[uuid.UUID, int] | None = None,
    review_stats_by_product_id: dict[uuid.UUID, Any] | None = None,
) -> ProductCardViewModel:
    is_available = (
        True
        if availability_by_offer_id is None
        else max(0, availability_by_offer_id.get(row.offer_id, 0)) > 0
    )
    review_stats = (review_stats_by_product_id or {}).get(row.product_id)
    return ProductCardViewModel(
        product_slug=row.product_slug,
        offer_id=row.offer_id,
        product_url=url_for(
            "storefront.product_detail",
            product_slug=row.product_slug,
        ),
        image_url=placeholder_image,
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
            "Información de entrega próximamente"
            if is_available
            else "Producto agotado"
        ),
        is_available=is_available,
    )


def _cards_from_rows(
    rows: list[Any],
    placeholder_image: str,
) -> list[ProductCardViewModel]:
    favorite_ids = _favorite_ids_for_product_ids(
        {row.product_id for row in rows}
    )
    availability = _availability_by_offer_ids({row.offer_id for row in rows})
    review_stats = review_stats_for_product_ids(
        db.session,
        {row.product_id for row in rows},
    )
    return [
        _card_from_row(row, placeholder_image, favorite_ids, availability, review_stats)
        for row in rows
    ]


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
) -> ProductCardViewModel:
    visible_compare_at = (
        item.compare_at_price
        if item.compare_at_price is not None
        and item.price is not None
        and item.compare_at_price > item.price
        else None
    )
    review_stats = (review_stats_by_product_id or {}).get(item.product_id)
    return ProductCardViewModel(
        product_slug=item.product_slug,
        offer_id=item.offer_id if item.is_available else None,
        product_url=(
            url_for(
                "storefront.product_detail",
                product_slug=item.product_slug,
            )
            if item.is_catalog_visible
            else None
        ),
        image_url=placeholder_image,
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
            "Información de entrega próximamente"
            if item.is_available
            else "Producto no disponible"
        ),
        is_available=item.is_available,
    )


def _display_attribute(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        )
    return str(value)


def _build_product_gallery_images(
    product_name: str,
    image_urls: Iterable[str | None],
) -> tuple[ProductGalleryImageViewModel, ...]:
    images: list[ProductGalleryImageViewModel] = []
    seen_urls: set[str] = set()

    for image_url in image_urls:
        normalized_url = (image_url or "").strip()
        if not normalized_url or normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)
        image_number = len(images) + 1
        images.append(
            ProductGalleryImageViewModel(
                url=normalized_url,
                thumbnail_url=normalized_url,
                alt=f"{product_name}, vista {image_number}",
                width=None,
                height=None,
                is_primary=image_number == 1,
            )
        )

    return tuple(images)


def _build_specifications(
    row: Any,
) -> tuple[ProductSpecificationViewModel, ...]:
    specifications: list[ProductSpecificationViewModel] = []

    def add(label: str, value: object) -> None:
        displayed_value = _display_attribute(value)
        if displayed_value:
            specifications.append(
                ProductSpecificationViewModel(label, displayed_value)
            )

    add("Categoría", row.category_name)
    add("Marca", row.product_brand)
    add("Modelo", row.product_model_number)
    add("Variante", row.variant_title)
    add("SKU del catálogo", row.catalog_sku)
    add("SKU del vendedor", row.seller_sku)
    add("Código de barras", row.manufacturer_barcode)
    add("Peso", f"{row.weight_grams} g" if row.weight_grams else None)
    add("Largo", f"{row.length_mm} mm" if row.length_mm else None)
    add("Ancho", f"{row.width_mm} mm" if row.width_mm else None)
    add("Alto", f"{row.height_mm} mm" if row.height_mm else None)

    for key, value in sorted((row.variant_attributes or {}).items()):
        add(str(key).replace("_", " ").capitalize(), value)

    return tuple(specifications)


def _build_highlights(
    row: Any,
) -> tuple[ProductSpecificationViewModel, ...]:
    highlights: list[ProductSpecificationViewModel] = []

    def add(label: str, value: object) -> None:
        displayed_value = _display_attribute(value)
        if displayed_value and len(highlights) < 6:
            highlights.append(
                ProductSpecificationViewModel(label, displayed_value)
            )

    add("Categoría", row.category_name)
    add("Marca", row.product_brand)
    add("Modelo", row.product_model_number)
    add("Variante", row.variant_title)
    for key, value in sorted((row.variant_attributes or {}).items()):
        add(str(key).replace("_", " ").capitalize(), value)

    return tuple(highlights)


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
                image_url=placeholder_image,
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
    canonical_offers = _canonical_offers_subquery()
    statement = select(canonical_offers).where(
        canonical_offers.c.offer_rank == 1
    )
    if category_ids:
        statement = statement.where(
            canonical_offers.c.category_id.in_(category_ids)
        )
    if product_ids:
        statement = statement.where(
            canonical_offers.c.product_id.not_in(product_ids)
        )

    rows = db.session.execute(
        statement.order_by(
            canonical_offers.c.product_title,
            canonical_offers.c.product_id,
        ).limit(MAX_RECOMMENDATIONS)
    ).all()
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    return _cards_from_rows(list(rows), placeholder_image)


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

    canonical_offers = _canonical_offers_subquery()
    statement = select(canonical_offers).where(
        canonical_offers.c.offer_rank == 1
    )

    if query_text:
        escaped_query = (
            query_text.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_query}%"
        statement = statement.where(
            or_(
                canonical_offers.c.product_title.ilike(
                    pattern,
                    escape="\\",
                ),
                canonical_offers.c.variant_title.ilike(
                    pattern,
                    escape="\\",
                ),
                canonical_offers.c.seller_sku.ilike(
                    pattern,
                    escape="\\",
                ),
            )
        )

    if selected_category:
        statement = statement.where(
            canonical_offers.c.category_slug == selected_category
        )

    rows = db.session.execute(
        statement.order_by(
            canonical_offers.c.product_title,
            canonical_offers.c.product_id,
        ).limit(MAX_HOME_OFFERS)
    ).all()
    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    products = _cards_from_rows(list(rows), placeholder_image)

    return render_template(
        "storefront/home.html",
        products=products,
        categories=_load_categories(),
        query_text=query_text,
        selected_category=selected_category,
        placeholder_count=max(0, 5 - len(products)),
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
    products = [
        _card_from_favorite_item(item, placeholder_image, review_stats)
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
        path = private_file_path(
            current_app.config["PAYMENT_PROOF_UPLOAD_DIR"], proof.storage_key
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
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
        return jsonify(
            ok=True,
            review_status=result.status.value,
            message="Reseña enviada. Está pendiente de revisión.",
        )
    flash("Recibimos tu reseña. Se publicará después de revisión.", "success")
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


@storefront.get("/resenas/imagenes/<string:public_id>")
def product_review_image(public_id: str):
    image = db.session.scalar(
        select(ProductReviewImage).where(ProductReviewImage.public_id == public_id)
    )
    if image is None:
        abort(404)
    review = image.review
    is_published = review.status == ProductReviewStatus.PUBLISHED
    is_owner = current_user.is_authenticated and review.user_id == current_user.id
    if not is_published and not is_owner:
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
    products_page = get_public_store_products_page(
        db.session,
        store_slug=store_slug,
        page=request.args.get("page"),
        page_size=STORE_PUBLIC_PRODUCTS_PER_PAGE,
    )
    if products_page is None:
        abort(404)

    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    products = _cards_from_rows(list(products_page.rows), placeholder_image)
    return render_template(
        "storefront/store.html",
        store_page=products_page,
        products=products,
        categories=_load_categories(),
        query_text="",
        selected_category="",
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
    canonical_offers = _canonical_offers_subquery()
    row = db.session.execute(
        select(canonical_offers).where(
            canonical_offers.c.offer_rank == 1,
            canonical_offers.c.product_slug == product_slug,
        )
    ).one_or_none()

    if row is None:
        abort(404)

    placeholder_image = url_for(
        "static",
        filename="images/placeholders/product-placeholder.svg",
    )
    available_quantity = max(
        0, _availability_by_offer_ids({row.offer_id}).get(row.offer_id, 0)
    )
    (
        max_quantity,
        low_stock,
        availability_label,
        availability_message,
    ) = _stock_presentation(available_quantity)
    is_available = available_quantity > 0
    specifications = _build_specifications(row)
    favorite_ids = _favorite_ids_for_product_ids({row.product_id})
    reviews_page = published_reviews_for_product(
        db.session,
        product_id=row.product_id,
        page=request.args.get("reviews_page"),
        page_size=current_app.config["PRODUCT_REVIEWS_PAGE_SIZE"],
    )
    store_stats = review_stats_for_store_ids(db.session, {row.store_id}).get(row.store_id)
    product = ProductDetailViewModel(
        offer_id=row.offer_id,
        product_id=row.product_id,
        public_identifier=row.product_slug,
        name=row.product_title,
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
            (),
        ),
        gallery_placeholder_url=placeholder_image,
        specifications=specifications,
        highlights=_build_highlights(row),
        rating=reviews_page.summary.average,
        review_count=reviews_page.summary.count,
        availability_label=availability_label,
        is_available=is_available,
        available_quantity=available_quantity,
        max_quantity=max_quantity,
        quantity_limit_reached=(is_available and max_quantity == 1),
        low_stock=low_stock,
        availability_message=availability_message,
        is_favorite=row.product_id in favorite_ids,
    )

    recommendation_rows = db.session.execute(
        select(canonical_offers)
        .where(
            canonical_offers.c.offer_rank == 1,
            canonical_offers.c.category_id == row.category_id,
            canonical_offers.c.product_id != row.product_id,
        )
        .order_by(
            canonical_offers.c.product_title,
            canonical_offers.c.product_id,
        )
        .limit(MAX_RECOMMENDATIONS)
    ).all()
    recommendations = _cards_from_rows(
        list(recommendation_rows),
        placeholder_image,
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
    )


@storefront.app_errorhandler(404)
def page_not_found(_error):
    return render_template("errors/404.html"), 404

