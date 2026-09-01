from __future__ import annotations

import hashlib
import os
import shutil
import uuid
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.private_storage import private_file_path


PRODUCT_IMAGE_PROCESSING_VERSION = 1
PRODUCT_IMAGE_INPUT_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class ProductImageProcessingError(Exception):
    """The source cannot be converted into verified storefront derivatives."""


class ProductImageFormatError(ProductImageProcessingError):
    pass


class ProductImageDecodeError(ProductImageProcessingError):
    pass


class ProductImageLimitError(ProductImageProcessingError):
    pass


class ProductImagePromotionError(ProductImageProcessingError):
    pass


@dataclass(frozen=True, slots=True)
class ProductImageProcessingConfig:
    max_source_bytes: int = 5 * 1024 * 1024
    max_source_pixels: int = 50_000_000
    max_source_edge: int = 12_000
    master_max_edge: int = 2_000
    master_quality: int = 82
    thumbnail_max_edge: int = 640
    thumbnail_quality: int = 80
    webp_method: int = 6


@dataclass(frozen=True, slots=True)
class ProductImageInspection:
    width: int
    height: int
    normalized_width: int
    normalized_height: int
    has_alpha: bool
    source_format: str


@dataclass(frozen=True, slots=True)
class ProductImageDerivative:
    path: Path
    media_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProcessedProductImage:
    master: ProductImageDerivative
    thumbnail: ProductImageDerivative
    staging_directory: Path
    processing_version: int = PRODUCT_IMAGE_PROCESSING_VERSION


def product_image_processing_config(
    values: Mapping[str, object],
) -> ProductImageProcessingConfig:
    return ProductImageProcessingConfig(
        max_source_bytes=int(values["PRODUCT_IMAGE_MAX_BYTES"]),
        max_source_pixels=int(values["PRODUCT_IMAGE_MAX_PIXELS"]),
        max_source_edge=int(values["PRODUCT_IMAGE_MAX_EDGE"]),
        master_max_edge=int(values["PRODUCT_IMAGE_MASTER_MAX_EDGE"]),
        master_quality=int(values["PRODUCT_IMAGE_MASTER_QUALITY"]),
        thumbnail_max_edge=int(values["PRODUCT_IMAGE_THUMBNAIL_MAX_EDGE"]),
        thumbnail_quality=int(values["PRODUCT_IMAGE_THUMBNAIL_QUALITY"]),
        webp_method=int(values["PRODUCT_IMAGE_WEBP_METHOD"]),
    )


def product_derivative_storage_keys(
    product_id: uuid.UUID,
    public_id: str,
) -> tuple[str, str]:
    base = f"products/{product_id}/{public_id}"
    return f"{base}/master.webp", f"{base}/thumbnail.webp"


def _validate_source_metadata(
    image: Image.Image,
    *,
    declared_media_type: str,
    config: ProductImageProcessingConfig,
) -> None:
    expected_format = PRODUCT_IMAGE_INPUT_FORMATS.get(declared_media_type.lower())
    if expected_format is None or image.format != expected_format:
        raise ProductImageFormatError(
            "La imagen no coincide con un formato JPEG, PNG o WebP permitido."
        )
    if bool(getattr(image, "is_animated", False)) or int(
        getattr(image, "n_frames", 1)
    ) != 1:
        raise ProductImageFormatError("Las imágenes WebP animadas no están permitidas.")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ProductImageDecodeError("La imagen no tiene dimensiones válidas.")
    if width > config.max_source_edge or height > config.max_source_edge:
        raise ProductImageLimitError("La imagen supera la dimensión máxima permitida.")
    if width * height > config.max_source_pixels:
        raise ProductImageLimitError("La imagen supera el límite seguro de píxeles.")


def _open_verified_source(
    source_path: Path,
    *,
    declared_media_type: str,
    config: ProductImageProcessingConfig,
) -> Image.Image:
    try:
        source_size = source_path.stat().st_size
    except OSError as exc:
        raise ProductImageDecodeError("No se pudo leer la imagen de origen.") from exc
    if source_size <= 0 or source_size > config.max_source_bytes:
        raise ProductImageLimitError("La imagen supera el tamaño máximo permitido.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source_path) as candidate:
                _validate_source_metadata(
                    candidate,
                    declared_media_type=declared_media_type,
                    config=config,
                )
                candidate.verify()
            with Image.open(source_path) as decoded:
                _validate_source_metadata(
                    decoded,
                    declared_media_type=declared_media_type,
                    config=config,
                )
                decoded.load()
                normalized = ImageOps.exif_transpose(decoded)
                normalized.load()
                return normalized.copy()
    except ProductImageProcessingError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ProductImageLimitError(
            "La imagen supera los límites seguros de decodificación."
        ) from exc
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise ProductImageDecodeError(
            "La imagen no se puede decodificar completamente."
        ) from exc


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or (
        image.mode == "P" and "transparency" in image.info
    )


def _normalize_color_mode(image: Image.Image) -> Image.Image:
    return image.convert("RGBA" if _has_alpha(image) else "RGB")


def inspect_product_image(
    source_path: str | Path,
    *,
    declared_media_type: str,
    config: ProductImageProcessingConfig,
) -> ProductImageInspection:
    source = Path(source_path)
    normalized = _open_verified_source(
        source,
        declared_media_type=declared_media_type,
        config=config,
    )
    try:
        oriented_size = normalized.size
        return ProductImageInspection(
            width=oriented_size[0],
            height=oriented_size[1],
            normalized_width=oriented_size[0],
            normalized_height=oriented_size[1],
            has_alpha=_has_alpha(normalized),
            source_format=PRODUCT_IMAGE_INPUT_FORMATS[declared_media_type.lower()],
        )
    finally:
        normalized.close()


def _resized_copy(image: Image.Image, max_edge: int) -> Image.Image:
    resized = image.copy()
    resized.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return resized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_webp(path: Path, expected_size: tuple[int, int]) -> ProductImageDerivative:
    try:
        with Image.open(path) as image:
            if image.format != "WEBP":
                raise ProductImageFormatError("El derivado no es WebP.")
            if bool(getattr(image, "is_animated", False)) or int(
                getattr(image, "n_frames", 1)
            ) != 1:
                raise ProductImageFormatError("El derivado WebP no es estático.")
            image.load()
            if image.size != expected_size:
                raise ProductImageDecodeError(
                    "Las dimensiones verificadas del derivado no coinciden."
                )
            if image.getexif() or any(
                key in image.info for key in ("exif", "xmp", "icc_profile")
            ):
                raise ProductImageProcessingError(
                    "El derivado conserva metadata no permitida."
                )
    except ProductImageProcessingError:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise ProductImageDecodeError(
            "El derivado WebP no se puede verificar completamente."
        ) from exc
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ProductImageDecodeError("El derivado WebP está vacío.")
    return ProductImageDerivative(
        path=path,
        media_type="image/webp",
        size_bytes=size_bytes,
        width=expected_size[0],
        height=expected_size[1],
        sha256=_sha256(path),
    )


def process_product_image(
    source_path: str | Path,
    *,
    declared_media_type: str,
    staging_root: str | Path,
    config: ProductImageProcessingConfig,
) -> ProcessedProductImage:
    root = Path(staging_root).resolve()
    staging_directory = root / ".staging" / f"product-media-{uuid.uuid4().hex}"
    master_path = staging_directory / "master.webp"
    thumbnail_path = staging_directory / "thumbnail.webp"
    normalized: Image.Image | None = None
    master_image: Image.Image | None = None
    thumbnail_image: Image.Image | None = None
    try:
        staging_directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        normalized = _normalize_color_mode(
            _open_verified_source(
                Path(source_path),
                declared_media_type=declared_media_type,
                config=config,
            )
        )
        master_image = _resized_copy(normalized, config.master_max_edge)
        thumbnail_image = _resized_copy(master_image, config.thumbnail_max_edge)
        master_image.save(
            master_path,
            format="WEBP",
            quality=config.master_quality,
            method=config.webp_method,
        )
        thumbnail_image.save(
            thumbnail_path,
            format="WEBP",
            quality=config.thumbnail_quality,
            method=config.webp_method,
        )
        master = _verify_webp(master_path, master_image.size)
        thumbnail = _verify_webp(thumbnail_path, thumbnail_image.size)
        return ProcessedProductImage(
            master=master,
            thumbnail=thumbnail,
            staging_directory=staging_directory,
        )
    except ProductImageProcessingError:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    except (OSError, ValueError) as exc:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise ProductImageProcessingError(
            "No se pudieron generar ambos derivados WebP."
        ) from exc
    finally:
        for image in (thumbnail_image, master_image, normalized):
            if image is not None:
                image.close()


def cleanup_processed_product_image(processed: ProcessedProductImage) -> None:
    shutil.rmtree(processed.staging_directory, ignore_errors=True)


def promote_processed_product_image(
    processed: ProcessedProductImage,
    *,
    media_root: str | Path,
    master_storage_key: str,
    thumbnail_storage_key: str,
) -> ProcessedProductImage:
    master_destination = private_file_path(media_root, master_storage_key)
    thumbnail_destination = private_file_path(media_root, thumbnail_storage_key)
    promoted: list[Path] = []
    try:
        if master_destination.exists() or thumbnail_destination.exists():
            raise ProductImagePromotionError(
                "No se pudo reservar una clave única para los derivados."
            )
        master_destination.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(processed.master.path, master_destination)
        promoted.append(master_destination)
        os.replace(processed.thumbnail.path, thumbnail_destination)
        promoted.append(thumbnail_destination)
        master = _verify_webp(
            master_destination,
            (processed.master.width, processed.master.height),
        )
        thumbnail = _verify_webp(
            thumbnail_destination,
            (processed.thumbnail.width, processed.thumbnail.height),
        )
        if master.sha256 != processed.master.sha256:
            raise ProductImagePromotionError("El hash final del master no coincide.")
        if thumbnail.sha256 != processed.thumbnail.sha256:
            raise ProductImagePromotionError("El hash final del thumbnail no coincide.")
        shutil.rmtree(processed.staging_directory, ignore_errors=True)
        return replace(processed, master=master, thumbnail=thumbnail)
    except ProductImageProcessingError:
        for path in promoted:
            path.unlink(missing_ok=True)
        shutil.rmtree(processed.staging_directory, ignore_errors=True)
        raise
    except OSError as exc:
        for path in promoted:
            path.unlink(missing_ok=True)
        shutil.rmtree(processed.staging_directory, ignore_errors=True)
        raise ProductImagePromotionError(
            "No se pudieron promover ambos derivados de forma segura."
        ) from exc


def verify_product_image_derivative(
    path: str | Path,
    *,
    width: int,
    height: int,
    sha256: str,
) -> ProductImageDerivative:
    verified = _verify_webp(Path(path), (width, height))
    if verified.sha256 != sha256:
        raise ProductImageProcessingError("El hash persistido del derivado no coincide.")
    return verified
