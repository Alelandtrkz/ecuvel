from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    OrderItem,
    Product,
    ProductMedia,
    ProductReview,
    ProductReviewReply,
    SellerOrder,
    User,
)
from app.models.enums import ProductReviewStatus
from app.services.partner_product_categories import (
    PartnerProductAccessError,
    PartnerStoreAccess,
    require_partner_catalog_store,
)
from app.services.product_reviews import (
    public_review_date_label,
    public_review_variant_label,
    public_reviewer_label,
)


PARTNER_REVIEWS_PAGE_SIZE = 20
PARTNER_REVIEW_REPLY_MAX_LENGTH = 500
PARTNER_REVIEW_STATUSES = ("unanswered", "answered")
PARTNER_REVIEW_SORTS = ("newest", "oldest", "rating_high", "rating_low")


class PartnerReviewError(Exception):
    """No fue posible gestionar la reseña de la tienda."""


class PartnerReviewAccessError(PartnerReviewError):
    """La reseña no pertenece a la tienda o el usuario no puede administrarla."""


class PartnerReviewValidationError(PartnerReviewError):
    """La respuesta no cumple las reglas públicas."""


class PartnerReviewConflictError(PartnerReviewError):
    """La respuesta cambió mientras otro administrador la editaba."""


@dataclass(frozen=True, slots=True)
class PartnerReviewMetrics:
    average_rating: Decimal | None
    total_reviews: int
    new_this_week: int
    answered_reviews: int
    unanswered_reviews: int
    response_rate: int


@dataclass(frozen=True, slots=True)
class PartnerReviewImageView:
    url: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PartnerReviewReplyView:
    body: str
    created_date_label: str
    updated_date_label: str
    is_edited: bool
    version: str


@dataclass(frozen=True, slots=True)
class PartnerReviewCardView:
    review_id: uuid.UUID
    product_id: uuid.UUID
    product_title: str
    product_slug: str
    seller_sku: str
    variant_label: str | None
    thumbnail_url: str | None
    rating: int
    body: str
    buyer_name: str
    published_date_label: str
    images: tuple[PartnerReviewImageView, ...]
    reply: PartnerReviewReplyView | None
    quick_replies: tuple[str, ...]

    @property
    def is_answered(self) -> bool:
        return self.reply is not None


@dataclass(frozen=True, slots=True)
class PartnerReviewsPage:
    store: PartnerStoreAccess
    metrics: PartnerReviewMetrics
    reviews: tuple[PartnerReviewCardView, ...]
    query: str
    selected_statuses: tuple[str, ...]
    selected_ratings: tuple[int, ...]
    selected_sort: str
    status_counts: dict[str, int]
    rating_counts: dict[int, int]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool

    @property
    def first_item(self) -> int:
        return 0 if not self.total_items else ((self.page - 1) * self.page_size) + 1

    @property
    def last_item(self) -> int:
        return min(self.page * self.page_size, self.total_items)


@dataclass(frozen=True, slots=True)
class PartnerReviewReplyResult:
    review_id: uuid.UUID
    reply: PartnerReviewReplyView
    created: bool
    metrics: PartnerReviewMetrics


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _week_start(now: datetime) -> datetime:
    normalized = now.astimezone(timezone.utc)
    return (normalized - timedelta(days=normalized.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _normalize_page(value: int | str | None) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _normalize_statuses(values: Iterable[str] | None) -> tuple[str, ...]:
    selected = {str(value).strip().lower() for value in (values or ())}
    return tuple(value for value in PARTNER_REVIEW_STATUSES if value in selected)


def _normalize_ratings(values: Iterable[int | str] | None) -> tuple[int, ...]:
    selected: set[int] = set()
    for raw_value in values or ():
        try:
            rating = int(raw_value)
        except (TypeError, ValueError):
            continue
        if 1 <= rating <= 5:
            selected.add(rating)
    return tuple(sorted(selected, reverse=True))


def _normalize_sort(value: str | None) -> str:
    normalized = (value or "newest").strip().lower()
    return normalized if normalized in PARTNER_REVIEW_SORTS else "newest"


def _search_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _reply_version(reply: ProductReviewReply) -> str:
    return reply.updated_at.isoformat()


def _reply_view(reply: ProductReviewReply) -> PartnerReviewReplyView:
    return PartnerReviewReplyView(
        body=reply.body,
        created_date_label=public_review_date_label(reply.created_at),
        updated_date_label=public_review_date_label(reply.updated_at),
        is_edited=reply.updated_at > reply.created_at,
        version=_reply_version(reply),
    )


def _quick_replies(rating: int, store_name: str) -> tuple[str, ...]:
    if rating >= 4:
        return (
            "¡Gracias por tu compra! Nos alegra saber que el producto cumplió tus expectativas.",
            "Gracias por compartir tu experiencia. Esperamos volver a atenderte pronto.",
            f"Agradecemos tu confianza en {store_name}. ¡Disfruta tu producto!",
        )
    if rating == 3:
        return (
            "Gracias por compartir tu experiencia. Tomaremos en cuenta tus comentarios para mejorar.",
            "Agradecemos tu opinión. Seguiremos trabajando para ofrecerte una mejor experiencia.",
            "Gracias por tu compra. Si necesitas ayuda, puedes comunicarte con soporte de ECUVEL.",
        )
    return (
        "Lamentamos que tu experiencia no haya sido la esperada. Queremos ayudarte a resolverlo.",
        "Gracias por informarnos. Comunícate mediante soporte de ECUVEL para que podamos revisar tu caso.",
        "Tomamos muy en serio tus comentarios y los usaremos para mejorar nuestro servicio.",
    )


def _store_review_metrics(
    session: Session,
    *,
    store_id: uuid.UUID,
    now: datetime | None = None,
) -> PartnerReviewMetrics:
    effective_now = now or _utcnow()
    reply_join = and_(
        ProductReviewReply.review_id == ProductReview.id,
        ProductReviewReply.store_id == store_id,
    )
    row = session.execute(
        select(
            func.count(ProductReview.id),
            func.avg(ProductReview.rating),
            func.sum(case((ProductReviewReply.id.is_not(None), 1), else_=0)),
            func.sum(
                case(
                    (
                        func.coalesce(ProductReview.published_at, ProductReview.created_at)
                        >= _week_start(effective_now),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .outerjoin(ProductReviewReply, reply_join)
        .where(
            SellerOrder.store_id == store_id,
            ProductReview.status == ProductReviewStatus.PUBLISHED,
        )
    ).one()
    total = int(row[0] or 0)
    average = (
        Decimal(str(row[1])).quantize(Decimal("0.1"))
        if row[1] is not None
        else None
    )
    answered = int(row[2] or 0)
    return PartnerReviewMetrics(
        average_rating=average,
        total_reviews=total,
        new_this_week=int(row[3] or 0),
        answered_reviews=answered,
        unanswered_reviews=max(0, total - answered),
        response_rate=round((answered / total) * 100) if total else 0,
    )


def _rating_counts(session: Session, *, store_id: uuid.UUID) -> dict[int, int]:
    rows = session.execute(
        select(ProductReview.rating, func.count(ProductReview.id))
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(
            SellerOrder.store_id == store_id,
            ProductReview.status == ProductReviewStatus.PUBLISHED,
        )
        .group_by(ProductReview.rating)
    )
    counts = {rating: 0 for rating in range(1, 6)}
    for rating, count in rows:
        counts[int(rating)] = int(count or 0)
    return counts


def _thumbnail_fallbacks(
    session: Session,
    product_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, str]:
    ids = tuple(set(product_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(ProductMedia, Product.slug)
        .join(Product, Product.id == ProductMedia.product_id)
        .where(
            ProductMedia.product_id.in_(ids),
            ProductMedia.is_active.is_(True),
        )
        .order_by(
            ProductMedia.product_id,
            ProductMedia.is_cover.desc(),
            ProductMedia.position,
            ProductMedia.id,
        )
    )
    thumbnails: dict[uuid.UUID, str] = {}
    for media, product_slug in rows:
        thumbnails.setdefault(
            media.product_id,
            f"/productos/{product_slug}/media/{media.public_id}",
        )
    return thumbnails


def get_partner_reviews_page(
    session: Session,
    *,
    user_id: uuid.UUID,
    query: str | None,
    statuses: Iterable[str] | None,
    ratings: Iterable[int | str] | None,
    sort: str | None,
    page: int | str | None,
    now: datetime | None = None,
) -> PartnerReviewsPage:
    try:
        store = require_partner_catalog_store(session, user_id)
    except PartnerProductAccessError as exc:
        raise PartnerReviewAccessError(str(exc)) from exc

    normalized_query = " ".join((query or "").split())[:160]
    selected_statuses = _normalize_statuses(statuses)
    selected_ratings = _normalize_ratings(ratings)
    selected_sort = _normalize_sort(sort)
    metrics = _store_review_metrics(session, store_id=store.store_id, now=now)
    rating_counts = _rating_counts(session, store_id=store.store_id)

    reply_join = and_(
        ProductReviewReply.review_id == ProductReview.id,
        ProductReviewReply.store_id == store.store_id,
    )
    conditions = [
        SellerOrder.store_id == store.store_id,
        ProductReview.status == ProductReviewStatus.PUBLISHED,
    ]
    if normalized_query:
        pattern = _search_pattern(normalized_query)
        conditions.append(
            or_(
                Product.title.ilike(pattern, escape="\\"),
                OrderItem.product_name_snapshot.ilike(pattern, escape="\\"),
                OrderItem.seller_sku_snapshot.ilike(pattern, escape="\\"),
                ProductReview.body.ilike(pattern, escape="\\"),
            )
        )
    if selected_ratings:
        conditions.append(ProductReview.rating.in_(selected_ratings))
    if len(selected_statuses) == 1:
        if selected_statuses[0] == "answered":
            conditions.append(ProductReviewReply.id.is_not(None))
        else:
            conditions.append(ProductReviewReply.id.is_(None))

    filtered_count = session.scalar(
        select(func.count(ProductReview.id))
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Product, Product.id == ProductReview.product_id)
        .outerjoin(ProductReviewReply, reply_join)
        .where(*conditions)
    ) or 0
    total_items = int(filtered_count)
    total_pages = max(1, (total_items + PARTNER_REVIEWS_PAGE_SIZE - 1) // PARTNER_REVIEWS_PAGE_SIZE)
    normalized_page = min(_normalize_page(page), total_pages)

    effective_date = func.coalesce(ProductReview.published_at, ProductReview.created_at)
    sort_columns = {
        "newest": (effective_date.desc(), ProductReview.id.desc()),
        "oldest": (effective_date.asc(), ProductReview.id.asc()),
        "rating_high": (ProductReview.rating.desc(), effective_date.desc(), ProductReview.id.desc()),
        "rating_low": (ProductReview.rating.asc(), effective_date.desc(), ProductReview.id.desc()),
    }
    rows = session.execute(
        select(ProductReview, User, OrderItem, Product, ProductReviewReply)
        .join(User, User.id == ProductReview.user_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .join(Product, Product.id == ProductReview.product_id)
        .outerjoin(ProductReviewReply, reply_join)
        .options(selectinload(ProductReview.images))
        .where(*conditions)
        .order_by(*sort_columns[selected_sort])
        .offset((normalized_page - 1) * PARTNER_REVIEWS_PAGE_SIZE)
        .limit(PARTNER_REVIEWS_PAGE_SIZE)
    ).all()
    thumbnails = _thumbnail_fallbacks(
        session,
        (product.id for _review, _user, _item, product, _reply in rows),
    )

    cards: list[PartnerReviewCardView] = []
    for review, buyer, item, product, reply in rows:
        cards.append(
            PartnerReviewCardView(
                review_id=review.id,
                product_id=product.id,
                product_title=item.product_name_snapshot or product.title,
                product_slug=product.slug,
                seller_sku=item.seller_sku_snapshot,
                variant_label=public_review_variant_label(item.variant_snapshot),
                thumbnail_url=item.image_url_snapshot or thumbnails.get(product.id),
                rating=review.rating,
                body=review.body,
                buyer_name=public_reviewer_label(buyer),
                published_date_label=public_review_date_label(
                    review.published_at or review.created_at
                ),
                images=tuple(
                    PartnerReviewImageView(
                        url=f"/resenas/imagenes/{image.public_id}",
                        width=image.width,
                        height=image.height,
                    )
                    for image in review.images
                ),
                reply=_reply_view(reply) if reply else None,
                quick_replies=_quick_replies(review.rating, store.store_name),
            )
        )

    return PartnerReviewsPage(
        store=store,
        metrics=metrics,
        reviews=tuple(cards),
        query=normalized_query,
        selected_statuses=selected_statuses,
        selected_ratings=selected_ratings,
        selected_sort=selected_sort,
        status_counts={
            "unanswered": metrics.unanswered_reviews,
            "answered": metrics.answered_reviews,
        },
        rating_counts=rating_counts,
        page=normalized_page,
        page_size=PARTNER_REVIEWS_PAGE_SIZE,
        total_items=total_items,
        total_pages=total_pages,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
    )


def save_partner_review_reply(
    session: Session,
    *,
    user_id: uuid.UUID,
    review_id: uuid.UUID,
    body: str | None,
    expected_updated_at: str | None,
    now: datetime | None = None,
) -> PartnerReviewReplyResult:
    try:
        store = require_partner_catalog_store(session, user_id)
    except PartnerProductAccessError as exc:
        raise PartnerReviewAccessError(str(exc)) from exc

    normalized_body = (body or "").strip()
    if not normalized_body:
        raise PartnerReviewValidationError("Escribe una respuesta antes de publicarla.")
    if len(normalized_body) > PARTNER_REVIEW_REPLY_MAX_LENGTH:
        raise PartnerReviewValidationError(
            f"La respuesta no puede superar {PARTNER_REVIEW_REPLY_MAX_LENGTH} caracteres."
        )

    review = session.scalar(
        select(ProductReview)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(
            ProductReview.id == review_id,
            SellerOrder.store_id == store.store_id,
        )
        .with_for_update()
    )
    if review is None:
        raise PartnerReviewAccessError("No existe una reseña publicada de esta tienda con ese identificador.")
    if review.status != ProductReviewStatus.PUBLISHED:
        raise PartnerReviewAccessError("La tienda solo puede responder reseñas publicadas.")

    reply = session.scalar(
        select(ProductReviewReply)
        .where(ProductReviewReply.review_id == review.id)
        .with_for_update()
    )
    created = reply is None
    effective_now = now or _utcnow()
    if reply is None:
        if (expected_updated_at or "").strip():
            raise PartnerReviewConflictError(
                "La reseña ya no está en el estado esperado. Actualiza la página e inténtalo nuevamente."
            )
        reply = ProductReviewReply(
            review_id=review.id,
            store_id=store.store_id,
            body=normalized_body,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        session.add(reply)
    else:
        if reply.store_id != store.store_id:
            raise PartnerReviewAccessError("La respuesta registrada no pertenece a esta tienda.")
        if not expected_updated_at or expected_updated_at != _reply_version(reply):
            raise PartnerReviewConflictError(
                "Otro administrador actualizó esta respuesta. Recarga la página antes de editarla."
            )
        reply.body = normalized_body
        reply.updated_by_user_id = user_id
        reply.updated_at = effective_now

    session.flush()
    session.refresh(reply)
    return PartnerReviewReplyResult(
        review_id=review.id,
        reply=_reply_view(reply),
        created=created,
        metrics=_store_review_metrics(session, store_id=store.store_id, now=effective_now),
    )
