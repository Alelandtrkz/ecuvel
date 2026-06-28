from __future__ import annotations

import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.payment_precheck.types import ImageVariant, PreparedImageSet


class ImagePreparationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def prepare_image_variants(
    *, file_path: Path, max_pixels: int, max_dimension: int
) -> PreparedImageSet:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file_path) as source:
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise ImagePreparationError(
                        "IMAGE_PIXEL_LIMIT", "La imagen supera el límite seguro de píxeles."
                    )
                source.load()
                try:
                    normalized = ImageOps.exif_transpose(source).convert("RGB")
                except Exception as exc:
                    raise ImagePreparationError(
                        "IMAGE_ORIENTATION_ERROR", "No se pudo normalizar la orientación."
                    ) from exc
    except ImagePreparationError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImagePreparationError(
            "IMAGE_DECOMPRESSION_BOMB", "La imagen excede los límites de descompresión."
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImagePreparationError(
            "IMAGE_DECODE_ERROR", "El comprobante no puede decodificarse como imagen."
        ) from exc

    if max(normalized.size) > max_dimension:
        scale = max_dimension / max(normalized.size)
        normalized = normalized.resize(
            (max(1, int(normalized.width * scale)), max(1, int(normalized.height * scale))),
            Image.Resampling.LANCZOS,
        )

    rgb = np.asarray(normalized)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if float(np.std(gray)) < 1.0:
        raise ImagePreparationError(
            "IMAGE_NO_CONTENT", "La imagen no contiene contraste suficiente para analizarse."
        )
    variants: list[ImageVariant] = [
        ImageVariant("normalized", rgb),
        ImageVariant("grayscale", gray),
    ]
    if max(normalized.size) < 1400:
        scale = min(2.0, max_dimension / max(normalized.size))
        enlarged = cv2.resize(
            gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        )
        variants.append(ImageVariant("upscaled", enlarged))
    contrast = cv2.equalizeHist(gray)
    variants.append(ImageVariant("contrast", contrast))
    threshold = cv2.adaptiveThreshold(
        contrast,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    if len(variants) < 4:
        variants.append(ImageVariant("threshold", threshold))
    else:
        variants[-1] = ImageVariant("threshold", threshold)
    return PreparedImageSet(
        variants=tuple(variants[:4]), width=normalized.width, height=normalized.height
    )
