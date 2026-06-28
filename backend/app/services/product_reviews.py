from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.models import (
    Order,
    OrderItem,
    OrderPackage,
    Product,
    ProductReview,
    ProductReviewImage,
    ProductVariant,
    SellerOffer,
    SellerOrder,
    User,
)
from app.models.enums import PackageStatus, ProductReviewStatus, UserStatus
from app.services.private_storage import (
    PrivateStorageError,
    delete_private_file,
    private_file_path,
)


class ProductReviewServiceError(Exception):
    """No fue posible procesar la reseña."""


class ProductReviewNotFoundError(ProductReviewServiceError):
    """La reseña no existe o no pertenece al usuario indicado."""


class ProductReviewEligibilityError(ProductReviewServiceError):
    """El comprador todavía no puede reseñar este producto."""


class ProductReviewDuplicateError(ProductReviewServiceError):
    """El artículo ya tiene una reseña del comprador."""


class ProductReviewImageError(ProductReviewServiceError):
    """Una imagen de reseña no cumple las reglas permitidas."""


class ProductReviewModerationError(ProductReviewServiceError):
    """No fue posible moderar la reseña."""


@dataclass(frozen=True, slots=True)
class ProductReviewImageConfig:
    root: str
    max_images: int
    max_bytes: int
    total_max_bytes: int
    max_pixels: int
    max_dimension: int


@dataclass(frozen=True, slots=True)
class StagedProductReviewImage:
    temporary_path: Path
    storage_key: str
    public_id: str
    original_filename: str
    media_type: str
    size_bytes: int
    width: int
    height: int
    sort_order: int


@dataclass(frozen=True, slots=True)
class ProductReviewCreateResult:
    review_id: uuid.UUID
    product_id: uuid.UUID
    order_item_id: uuid.UUID
    status: ProductReviewStatus


@dataclass(frozen=True, slots=True)
class ProductReviewModerationResult:
    review_id: uuid.UUID
    status: ProductReviewStatus
    replayed: bool


@dataclass(frozen=True, slots=True)
class PublicReviewImageView:
    public_id: str
    url: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PublicProductReviewView:
    review_public_id: str
    rating: int
    body: str
    public_reviewer_name: str
    public_reviewer_initial: str
    created_at: datetime
    published_at: datetime | None
    published_date_label: str
    verified_purchase: bool
    variant_label: str | None
    images: tuple[PublicReviewImageView, ...]


@dataclass(frozen=True, slots=True)
class ProductReviewSummary:
    average: Decimal | None
    count: int
    distribution: dict[int, int]


@dataclass(frozen=True, slots=True)
class ProductReviewsPage:
    summary: ProductReviewSummary
    reviews: tuple[PublicProductReviewView, ...]
    page: int
    page_size: int
    total_pages: int
    has_previous: bool
    has_next: bool


@dataclass(frozen=True, slots=True)
class OwnProductReviewView:
    review_id: uuid.UUID
    order_number: str
    order_item_id: uuid.UUID
    product_name: str
    product_slug: str
    rating: int
    body: str
    status: ProductReviewStatus
    public_rejection_reason: str | None
    created_at: datetime
    images: tuple[ProductReviewImage, ...]


@dataclass(frozen=True, slots=True)
class ProductReviewTarget:
    order_number: str
    order_item_id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_name: str
    variant_title: str | None
    image_url: str | None
    delivered: bool
    existing_review_id: uuid.UUID | None
    existing_status: ProductReviewStatus | None


_IMAGE_FORMATS = {
    "jpg": ("image/jpeg", "JPEG", "jpg"),
    "jpeg": ("image/jpeg", "JPEG", "jpg"),
    "png": ("image/png", "PNG", "png"),
    "webp": ("image/webp", "WEBP", "webp"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_page(page: int | str | None) -> int:
    try:
        value = int(page or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


_SPANISH_MONTHS_ABBR = {
    1: "ene.",
    2: "feb.",
    3: "mar.",
    4: "abr.",
    5: "may.",
    6: "jun.",
    7: "jul.",
    8: "ago.",
    9: "sept.",
    10: "oct.",
    11: "nov.",
    12: "dic.",
}


def _public_date_label(value: datetime | None) -> str:
    if value is None:
        return ""
    return f"{value.day} {_SPANISH_MONTHS_ABBR[value.month]} {value.year}"


def _public_reviewer_identity(user: User) -> tuple[str, str]:
    name = (user.full_name or "").strip()
    if name:
        parts = name.split()
        if len(parts) > 1:
            label = f"{parts[0]} {parts[-1][0]}."
        else:
            label = parts[0]
    else:
        label = "Comprador verificado"

    initial = next((char.upper() for char in label if char.isalnum()), "C")
    return label, initial


def _public_variant_label(snapshot: dict | None) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    title = str(snapshot.get("title") or "").strip()
    if title:
        return f"Variante: {title}"
    attributes = snapshot.get("attributes")
    if not isinstance(attributes, dict):
        return None
    pairs: list[str] = []
    for key, value in sorted(attributes.items()):
        if value in (None, ""):
            continue
        label = str(key).replace("_", " ").strip().capitalize()
        pairs.append(f"{label}: {value}")
        if len(pairs) == 3:
            break
    return " · ".join(pairs) if pairs else None


def _public_review_images(
    images: Iterable[ProductReviewImage],
) -> tuple[PublicReviewImageView, ...]:
    return tuple(
        PublicReviewImageView(
            public_id=image.public_id,
            url=f"/resenas/imagenes/{image.public_id}",
            width=image.width,
            height=image.height,
        )
        for image in images
    )


def _clean_body(body: str, *, min_length: int, max_length: int) -> str:
    normalized = " ".join((body or "").split())
    if len(normalized) < min_length:
        raise ProductReviewServiceError(
            f"La reseña debe tener al menos {min_length} caracteres."
        )
    if len(normalized) > max_length:
        raise ProductReviewServiceError(
            f"La reseña no puede superar {max_length} caracteres."
        )
    return normalized


def _clean_rating(value: object) -> int:
    try:
        rating = int(value)
    except (TypeError, ValueError) as exc:
        raise ProductReviewServiceError("Selecciona una calificación válida.") from exc
    if not 1 <= rating <= 5:
        raise ProductReviewServiceError("La calificación debe estar entre 1 y 5.")
    return rating


def _non_empty_uploads(files: Iterable[FileStorage]) -> list[FileStorage]:
    return [
        file
        for file in files
        if file and (file.filename or "").strip()
    ]


def stage_product_review_images(
    files: Iterable[FileStorage],
    *,
    config: ProductReviewImageConfig,
) -> tuple[StagedProductReviewImage, ...]:
    uploads = _non_empty_uploads(files)
    if len(uploads) > config.max_images:
        raise ProductReviewImageError(
            f"Puedes subir como máximo {config.max_images} fotos."
        )
    root_path = Path(config.root).resolve()
    staging_dir = root_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(staging_dir, 0o700)
    except OSError:
        pass

    staged: list[StagedProductReviewImage] = []
    total_size = 0
    for index, uploaded_file in enumerate(uploads):
        filename = secure_filename(uploaded_file.filename or "")[:255]
        if not filename or "." not in filename:
            raise ProductReviewImageError("Selecciona imágenes JPEG, PNG o WebP válidas.")
        extension = filename.rsplit(".", 1)[1].lower()
        expected = _IMAGE_FORMATS.get(extension)
        if expected is None:
            raise ProductReviewImageError("Solo se aceptan imágenes JPEG, PNG y WebP.")
        expected_media_type, pil_format, final_extension = expected
        if (uploaded_file.mimetype or "").lower() != expected_media_type:
            raise ProductReviewImageError(
                "La extensión y el tipo de una imagen no coinciden."
            )

        raw = uploaded_file.stream.read(config.max_bytes + 1)
        if not raw:
            raise ProductReviewImageError("Una imagen está vacía.")
        if len(raw) > config.max_bytes:
            raise ProductReviewImageError("Una imagen supera el tamaño permitido.")
        total_size += len(raw)
        if total_size > config.total_max_bytes:
            raise ProductReviewImageError(
                "El total de imágenes supera el tamaño permitido."
            )

        temporary_path = staging_dir / f"{uuid.uuid4().hex}.{final_extension}.tmp"
        try:
            from io import BytesIO

            with Image.open(BytesIO(raw)) as source:
                if source.format != pil_format:
                    raise ProductReviewImageError(
                        "El contenido de una imagen no coincide con su formato."
                    )
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ProductReviewImageError("Una imagen no tiene dimensiones válidas.")
                if width * height > config.max_pixels:
                    raise ProductReviewImageError("Una imagen es demasiado grande.")
                if width > config.max_dimension or height > config.max_dimension:
                    raise ProductReviewImageError("Una imagen supera las dimensiones permitidas.")

                cleaned = ImageOps.exif_transpose(source)
                if expected_media_type == "image/png":
                    output = cleaned.convert("RGBA")
                else:
                    output = cleaned.convert("RGB")
                output.save(temporary_path, format=pil_format, optimize=True)
        except ProductReviewImageError:
            temporary_path.unlink(missing_ok=True)
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            temporary_path.unlink(missing_ok=True)
            raise ProductReviewImageError("No fue posible leer una imagen.") from exc

        size_bytes = temporary_path.stat().st_size
        storage_key = f"{_utcnow():%Y/%m}/{uuid.uuid4().hex}.{final_extension}"
        staged.append(
            StagedProductReviewImage(
                temporary_path=temporary_path,
                storage_key=storage_key,
                public_id=secrets.token_urlsafe(24),
                original_filename=filename,
                media_type=expected_media_type,
                size_bytes=size_bytes,
                width=width,
                height=height,
                sort_order=index,
            )
        )

    return tuple(staged)


def cleanup_staged_product_review_images(
    staged_images: Iterable[StagedProductReviewImage],
    *,
    storage_root: str,
    include_final: bool = True,
) -> None:
    for staged in staged_images:
        delete_private_file(staged.temporary_path)
        if include_final:
            try:
                delete_private_file(private_file_path(storage_root, staged.storage_key))
            except PrivateStorageError:
                pass


def promote_product_review_images(
    staged_images: Iterable[StagedProductReviewImage],
    *,
    storage_root: str,
) -> None:
    for staged in staged_images:
        destination = private_file_path(storage_root, staged.storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise ProductReviewImageError("La clave privada de imagen ya existe.")
        os.replace(staged.temporary_path, destination)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass


def _eligible_order_item_row(
    session: Session,
    *,
    order_number: str,
    order_item_id: uuid.UUID,
    user_id: uuid.UUID,
    lock: bool = False,
):
    statement = (
        select(Order, SellerOrder, OrderItem, SellerOffer, ProductVariant, Product, OrderPackage)
        .join(SellerOrder, SellerOrder.order_id == Order.id)
        .join(OrderItem, OrderItem.seller_order_id == SellerOrder.id)
        .join(SellerOffer, SellerOffer.id == OrderItem.offer_id)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(OrderPackage, OrderPackage.order_item_id == OrderItem.id)
        .where(
            Order.order_number == order_number,
            Order.buyer_id == user_id,
            OrderItem.id == order_item_id,
        )
    )
    if lock:
        statement = statement.with_for_update(of=OrderItem)
    return session.execute(statement).one_or_none()


def create_product_review(
    *,
    session: Session,
    order_number: str,
    order_item_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: object,
    body: str,
    staged_images: tuple[StagedProductReviewImage, ...],
    min_body_length: int,
    max_body_length: int,
) -> ProductReviewCreateResult:
    clean_rating = _clean_rating(rating)
    clean_body = _clean_body(
        body,
        min_length=min_body_length,
        max_length=max_body_length,
    )
    row = _eligible_order_item_row(
        session,
        order_number=order_number,
        order_item_id=order_item_id,
        user_id=user_id,
        lock=True,
    )
    if row is None:
        raise ProductReviewEligibilityError("No encontramos un artículo reseñable.")
    order, _seller_order, item, _offer, _variant, product, package = row
    if package is None or package.status != PackageStatus.HANDED_OVER or package.handed_over_at is None:
        raise ProductReviewEligibilityError(
            "Solo puedes reseñar productos que ya fueron entregados."
        )
    existing = session.scalar(
        select(ProductReview).where(
            ProductReview.user_id == user_id,
            ProductReview.order_item_id == item.id,
        )
    )
    if existing is not None:
        raise ProductReviewDuplicateError("Este producto del pedido ya tiene reseña.")

    review = ProductReview(
        user_id=user_id,
        order_id=order.id,
        order_item_id=item.id,
        product_id=product.id,
        rating=clean_rating,
        body=clean_body,
    )
    session.add(review)
    session.flush()
    for staged in staged_images:
        session.add(
            ProductReviewImage(
                review_id=review.id,
                public_id=staged.public_id,
                storage_key=staged.storage_key,
                original_filename=staged.original_filename,
                media_type=staged.media_type,
                size_bytes=staged.size_bytes,
                width=staged.width,
                height=staged.height,
                sort_order=staged.sort_order,
            )
        )
    try:
        session.flush()
    except IntegrityError as exc:
        raise ProductReviewDuplicateError("Este producto del pedido ya tiene reseña.") from exc
    return ProductReviewCreateResult(
        review_id=review.id,
        product_id=product.id,
        order_item_id=item.id,
        status=review.status,
    )


def own_review_for_order_item(
    *,
    session: Session,
    order_number: str,
    order_item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OwnProductReviewView:
    row = session.execute(
        select(ProductReview, Order, OrderItem, Product)
        .join(Order, Order.id == ProductReview.order_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(Product, Product.id == ProductReview.product_id)
        .where(
            Order.order_number == order_number,
            Order.buyer_id == user_id,
            ProductReview.order_item_id == order_item_id,
        )
    ).one_or_none()
    if row is None:
        raise ProductReviewNotFoundError("No encontramos la reseña solicitada.")
    review, order, item, product = row
    return OwnProductReviewView(
        review_id=review.id,
        order_number=order.order_number,
        order_item_id=item.id,
        product_name=item.product_name_snapshot,
        product_slug=product.slug,
        rating=review.rating,
        body=review.body,
        status=review.status,
        public_rejection_reason=review.public_rejection_reason,
        created_at=review.created_at,
        images=tuple(review.images),
    )


def review_target_for_order_item(
    *,
    session: Session,
    order_number: str,
    order_item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ProductReviewTarget:
    row = _eligible_order_item_row(
        session,
        order_number=order_number,
        order_item_id=order_item_id,
        user_id=user_id,
    )
    if row is None:
        raise ProductReviewEligibilityError("No encontramos un artículo reseñable.")
    order, _seller_order, item, _offer, _variant, product, package = row
    review = session.scalar(
        select(ProductReview).where(
            ProductReview.user_id == user_id,
            ProductReview.order_item_id == item.id,
        )
    )
    delivered = (
        package is not None
        and package.status == PackageStatus.HANDED_OVER
        and package.handed_over_at is not None
    )
    return ProductReviewTarget(
        order_number=order.order_number,
        order_item_id=item.id,
        product_id=product.id,
        product_slug=product.slug,
        product_name=item.product_name_snapshot,
        variant_title=(item.variant_snapshot or {}).get("title"),
        image_url=item.image_url_snapshot,
        delivered=delivered,
        existing_review_id=review.id if review else None,
        existing_status=review.status if review else None,
    )


def review_states_for_order_items(
    session: Session,
    *,
    user_id: uuid.UUID,
    order_item_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, ProductReview]:
    ids = tuple(order_item_ids)
    if not ids:
        return {}
    reviews = session.scalars(
        select(ProductReview).where(
            ProductReview.user_id == user_id,
            ProductReview.order_item_id.in_(ids),
        )
    )
    return {review.order_item_id: review for review in reviews}


def review_stats_for_product_ids(
    session: Session,
    product_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, ProductReviewSummary]:
    ids = tuple(product_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(
            ProductReview.product_id,
            func.count(ProductReview.id),
            func.avg(ProductReview.rating),
            func.sum(case((ProductReview.rating == 1, 1), else_=0)),
            func.sum(case((ProductReview.rating == 2, 1), else_=0)),
            func.sum(case((ProductReview.rating == 3, 1), else_=0)),
            func.sum(case((ProductReview.rating == 4, 1), else_=0)),
            func.sum(case((ProductReview.rating == 5, 1), else_=0)),
        )
        .where(
            ProductReview.product_id.in_(ids),
            ProductReview.status == ProductReviewStatus.PUBLISHED,
        )
        .group_by(ProductReview.product_id)
    )
    stats: dict[uuid.UUID, ProductReviewSummary] = {}
    for product_id, count, average, one, two, three, four, five in rows:
        stats[product_id] = ProductReviewSummary(
            average=Decimal(str(average)).quantize(Decimal("0.1")) if average is not None else None,
            count=int(count or 0),
            distribution={
                1: int(one or 0),
                2: int(two or 0),
                3: int(three or 0),
                4: int(four or 0),
                5: int(five or 0),
            },
        )
    return stats


def review_stats_for_store_ids(
    session: Session,
    store_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, ProductReviewSummary]:
    ids = tuple(store_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(
            SellerOrder.store_id,
            func.count(ProductReview.id),
            func.avg(ProductReview.rating),
            func.sum(case((ProductReview.rating == 1, 1), else_=0)),
            func.sum(case((ProductReview.rating == 2, 1), else_=0)),
            func.sum(case((ProductReview.rating == 3, 1), else_=0)),
            func.sum(case((ProductReview.rating == 4, 1), else_=0)),
            func.sum(case((ProductReview.rating == 5, 1), else_=0)),
        )
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
        .where(
            SellerOrder.store_id.in_(ids),
            ProductReview.status == ProductReviewStatus.PUBLISHED,
        )
        .group_by(SellerOrder.store_id)
    )
    stats: dict[uuid.UUID, ProductReviewSummary] = {}
    for store_id, count, average, one, two, three, four, five in rows:
        stats[store_id] = ProductReviewSummary(
            average=Decimal(str(average)).quantize(Decimal("0.1")) if average is not None else None,
            count=int(count or 0),
            distribution={
                1: int(one or 0),
                2: int(two or 0),
                3: int(three or 0),
                4: int(four or 0),
                5: int(five or 0),
            },
        )
    return stats


def published_reviews_for_product(
    session: Session,
    *,
    product_id: uuid.UUID,
    page: int | str | None,
    page_size: int,
) -> ProductReviewsPage:
    normalized_page = _normalize_page(page)
    summary = review_stats_for_product_ids(session, (product_id,)).get(
        product_id,
        ProductReviewSummary(None, 0, {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}),
    )
    total_pages = max(1, (summary.count + page_size - 1) // page_size)
    normalized_page = min(normalized_page, total_pages)
    rows = session.execute(
        select(ProductReview, User, OrderItem)
        .join(User, User.id == ProductReview.user_id)
        .join(OrderItem, OrderItem.id == ProductReview.order_item_id)
        .options(selectinload(ProductReview.images))
        .where(
            ProductReview.product_id == product_id,
            ProductReview.status == ProductReviewStatus.PUBLISHED,
        )
        .order_by(ProductReview.published_at.desc().nullslast(), ProductReview.created_at.desc(), ProductReview.id.desc())
        .offset((normalized_page - 1) * page_size)
        .limit(page_size)
    ).all()
    review_views: list[PublicProductReviewView] = []
    for review, user, item in rows:
        author_label, author_initial = _public_reviewer_identity(user)
        review_views.append(
            PublicProductReviewView(
                review_public_id=str(review.id),
                rating=review.rating,
                body=review.body,
                public_reviewer_name=author_label,
                public_reviewer_initial=author_initial,
                created_at=review.created_at,
                published_at=review.published_at,
                published_date_label=_public_date_label(
                    review.published_at or review.created_at
                ),
                verified_purchase=True,
                variant_label=_public_variant_label(item.variant_snapshot),
                images=_public_review_images(review.images),
            )
        )
    return ProductReviewsPage(
        summary=summary,
        reviews=tuple(review_views),
        page=normalized_page,
        page_size=page_size,
        total_pages=total_pages,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
    )


def public_reviewer_label(user: User) -> str:
    return _public_reviewer_identity(user)[0]


def moderate_product_review(
    *,
    session: Session,
    review_id: uuid.UUID,
    decision: str,
    moderator_user_id: uuid.UUID,
    reason: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> ProductReviewModerationResult:
    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        raise ProductReviewModerationError("La decisión debe ser approve o reject.")
    moderator = session.scalar(
        select(User).where(User.id == moderator_user_id).with_for_update()
    )
    if moderator is None or moderator.status != UserStatus.ACTIVE:
        raise ProductReviewModerationError("El moderador no está activo.")
    review = session.get(ProductReview, review_id, with_for_update=True)
    if review is None:
        raise ProductReviewNotFoundError("No existe la reseña indicada.")
    target_status = (
        ProductReviewStatus.PUBLISHED
        if normalized_decision == "approve"
        else ProductReviewStatus.REJECTED
    )
    if review.status == target_status:
        return ProductReviewModerationResult(review.id, review.status, True)
    if review.status != ProductReviewStatus.PENDING_REVIEW:
        raise ProductReviewModerationError(
            "La reseña ya tiene una decisión distinta."
        )
    effective_now = now or _utcnow()
    if target_status == ProductReviewStatus.REJECTED:
        public_reason = " ".join((reason or "").split())
        if not public_reason:
            raise ProductReviewModerationError("Indica un motivo público de rechazo.")
        review.public_rejection_reason = public_reason[:500]
    else:
        review.published_at = effective_now
        review.public_rejection_reason = None
    review.status = target_status
    review.moderated_by_user_id = moderator.id
    review.moderated_at = effective_now
    review.moderation_notes = " ".join((notes or "").split())[:1000] or None
    session.flush()
    return ProductReviewModerationResult(review.id, review.status, False)
