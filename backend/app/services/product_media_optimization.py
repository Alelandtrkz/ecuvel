from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductMedia
from app.services.private_storage import PrivateStorageError, private_file_path
from app.services.product_image_processing import (
    ProductImageProcessingConfig,
    ProductImageProcessingError,
    cleanup_processed_product_image,
    inspect_product_image,
    process_product_image,
    product_derivative_storage_keys,
    promote_processed_product_image,
    verify_product_image_derivative,
)


logger = logging.getLogger(__name__)


class ProductMediaOptimizationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ProductMediaOptimizationEntry:
    media_id: uuid.UUID
    public_id: str
    product_slug: str
    media_type: str
    size_bytes: int
    width: int | None
    height: int | None
    status: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ProductMediaOptimizationReport:
    entries: tuple[ProductMediaOptimizationEntry, ...]

    @property
    def legacy_count(self) -> int:
        return sum(entry.status != "processed" for entry in self.entries)

    @property
    def processed_count(self) -> int:
        return sum(entry.status == "processed" for entry in self.entries)

    @property
    def processable_count(self) -> int:
        return sum(entry.status == "processable" for entry in self.entries)

    @property
    def failure_count(self) -> int:
        return sum(
            entry.status in {"missing", "unsupported", "invalid"}
            for entry in self.entries
        )

    @property
    def legacy_bytes(self) -> int:
        return sum(
            entry.size_bytes
            for entry in self.entries
            if entry.status != "processed"
        )


@dataclass(frozen=True, slots=True)
class ProductMediaOptimizationResult:
    media_id: uuid.UUID
    public_id: str
    status: str
    original_size_bytes: int
    master_size_bytes: int | None = None
    thumbnail_size_bytes: int | None = None
    original_deleted: bool = False


def product_media_is_processed(media: ProductMedia) -> bool:
    return all(
        value is not None
        for value in (
            media.content_sha256,
            media.processing_version,
            media.thumbnail_storage_key,
            media.thumbnail_media_type,
            media.thumbnail_size_bytes,
            media.thumbnail_width,
            media.thumbnail_height,
            media.thumbnail_sha256,
        )
    )


def inspect_legacy_product_media(
    session: Session,
    *,
    media_root: str | Path,
    config: ProductImageProcessingConfig,
) -> ProductMediaOptimizationReport:
    rows = session.execute(
        select(ProductMedia, Product.slug)
        .join(Product, Product.id == ProductMedia.product_id)
        .order_by(ProductMedia.created_at, ProductMedia.id)
    ).all()
    entries: list[ProductMediaOptimizationEntry] = []
    for media, product_slug in rows:
        if product_media_is_processed(media):
            entries.append(ProductMediaOptimizationEntry(
                media_id=media.id,
                public_id=media.public_id,
                product_slug=product_slug,
                media_type=media.media_type,
                size_bytes=media.size_bytes,
                width=media.width,
                height=media.height,
                status="processed",
            ))
            continue
        if media.media_type.lower() not in {
            "image/jpeg", "image/png", "image/webp"
        }:
            status = "unsupported"
            message = "Formato no compatible."
        else:
            try:
                path = private_file_path(media_root, media.storage_key)
                if not path.is_file():
                    status = "missing"
                    message = "Archivo publicado ausente."
                else:
                    inspect_product_image(
                        path,
                        declared_media_type=media.media_type,
                        config=config,
                    )
                    status = "processable"
                    message = None
            except PrivateStorageError:
                status = "missing"
                message = "Clave de almacenamiento inválida."
            except ProductImageProcessingError as exc:
                status = "invalid"
                message = str(exc)
        entries.append(ProductMediaOptimizationEntry(
            media_id=media.id,
            public_id=media.public_id,
            product_slug=product_slug,
            media_type=media.media_type,
            size_bytes=media.size_bytes,
            width=media.width,
            height=media.height,
            status=status,
            message=message,
        ))
    return ProductMediaOptimizationReport(entries=tuple(entries))


def optimize_product_media(
    session: Session,
    *,
    media_id: uuid.UUID,
    media_root: str | Path,
    config: ProductImageProcessingConfig,
) -> ProductMediaOptimizationResult:
    media = session.scalar(
        select(ProductMedia)
        .where(ProductMedia.id == media_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if media is None:
        raise ProductMediaOptimizationError("La media ya no existe.")
    if product_media_is_processed(media):
        return ProductMediaOptimizationResult(
            media_id=media.id,
            public_id=media.public_id,
            status="skipped",
            original_size_bytes=media.size_bytes,
            master_size_bytes=media.size_bytes,
            thumbnail_size_bytes=media.thumbnail_size_bytes,
        )
    if media.media_type.lower() not in {"image/jpeg", "image/png", "image/webp"}:
        raise ProductMediaOptimizationError("La media no tiene un formato compatible.")

    original_storage_key = media.storage_key
    original_size_bytes = media.size_bytes
    try:
        original_path = private_file_path(media_root, original_storage_key)
    except PrivateStorageError as exc:
        raise ProductMediaOptimizationError(
            "La clave publicada no es válida."
        ) from exc
    if not original_path.is_file():
        raise ProductMediaOptimizationError("El archivo publicado no existe.")

    master_key, thumbnail_key = product_derivative_storage_keys(
        media.product_id,
        media.public_id,
    )
    processed = None
    promoted = None
    committed = False
    try:
        processed = process_product_image(
            original_path,
            declared_media_type=media.media_type,
            staging_root=media_root,
            config=config,
        )
        promoted = promote_processed_product_image(
            processed,
            media_root=media_root,
            master_storage_key=master_key,
            thumbnail_storage_key=thumbnail_key,
        )
        media.storage_key = master_key
        media.media_type = promoted.master.media_type
        media.size_bytes = promoted.master.size_bytes
        media.width = promoted.master.width
        media.height = promoted.master.height
        media.content_sha256 = promoted.master.sha256
        media.thumbnail_storage_key = thumbnail_key
        media.thumbnail_media_type = promoted.thumbnail.media_type
        media.thumbnail_size_bytes = promoted.thumbnail.size_bytes
        media.thumbnail_width = promoted.thumbnail.width
        media.thumbnail_height = promoted.thumbnail.height
        media.thumbnail_sha256 = promoted.thumbnail.sha256
        media.processing_version = promoted.processing_version
        session.flush()
        session.commit()
        committed = True
    except ProductImageProcessingError as exc:
        session.rollback()
        raise ProductMediaOptimizationError(str(exc)) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        if processed is not None and promoted is None:
            cleanup_processed_product_image(processed)
        if not committed and promoted is not None:
            promoted.master.path.unlink(missing_ok=True)
            promoted.thumbnail.path.unlink(missing_ok=True)

    refreshed = session.scalar(
        select(ProductMedia)
        .where(ProductMedia.id == media_id)
        .execution_options(populate_existing=True)
    )
    if refreshed is None or refreshed.storage_key != master_key:
        raise ProductMediaOptimizationError(
            "La media no pudo reverificarse después del commit."
        )
    master_path = private_file_path(media_root, refreshed.storage_key)
    thumbnail_path = private_file_path(
        media_root,
        str(refreshed.thumbnail_storage_key),
    )
    try:
        verify_product_image_derivative(
            master_path,
            width=int(refreshed.width),
            height=int(refreshed.height),
            sha256=str(refreshed.content_sha256),
        )
        verify_product_image_derivative(
            thumbnail_path,
            width=int(refreshed.thumbnail_width),
            height=int(refreshed.thumbnail_height),
            sha256=str(refreshed.thumbnail_sha256),
        )
    except ProductImageProcessingError as exc:
        raise ProductMediaOptimizationError(
            "Los derivados no superaron la verificación posterior al commit."
        ) from exc

    original_deleted = False
    try:
        original_path.unlink()
        original_deleted = True
    except OSError:
        logger.warning(
            "Legacy product media remained after verified optimization "
            "(media_public_id=%s)",
            refreshed.public_id,
        )
    return ProductMediaOptimizationResult(
        media_id=refreshed.id,
        public_id=refreshed.public_id,
        status="processed",
        original_size_bytes=original_size_bytes,
        master_size_bytes=refreshed.size_bytes,
        thumbnail_size_bytes=refreshed.thumbnail_size_bytes,
        original_deleted=original_deleted,
    )
