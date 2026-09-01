from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from app.services.product_image_processing import (
    ProductImageFormatError,
    ProductImageLimitError,
    ProductImageProcessingConfig,
    ProductImageProcessingError,
    cleanup_processed_product_image,
    process_product_image,
    product_derivative_storage_keys,
    promote_processed_product_image,
)


def _save_image(
    path: Path,
    *,
    size: tuple[int, int] = (1200, 800),
    mode: str = "RGB",
    image_format: str = "JPEG",
    color=None,
    **save_options,
) -> Path:
    if color is None:
        color = (20, 80, 140, 0) if "A" in mode else (20, 80, 140)
    image = Image.new(mode, size, color)
    image.save(path, format=image_format, **save_options)
    image.close()
    return path


def _config(**overrides) -> ProductImageProcessingConfig:
    values = {
        "max_source_bytes": 8 * 1024 * 1024,
        "max_source_pixels": 50_000_000,
        "max_source_edge": 12_000,
        "master_max_edge": 2_000,
        "master_quality": 82,
        "thumbnail_max_edge": 640,
        "thumbnail_quality": 80,
        "webp_method": 6,
    }
    values.update(overrides)
    return ProductImageProcessingConfig(**values)


@pytest.mark.parametrize(
    ("image_format", "media_type", "mode"),
    [
        ("JPEG", "image/jpeg", "RGB"),
        ("PNG", "image/png", "RGB"),
        ("WEBP", "image/webp", "RGB"),
        ("JPEG", "image/jpeg", "CMYK"),
    ],
)
def test_supported_static_inputs_become_verified_webp_derivatives(
    tmp_path, image_format, media_type, mode
):
    source = _save_image(
        tmp_path / f"source.{image_format.lower()}",
        size=(2400, 1200),
        mode=mode,
        image_format=image_format,
        color=(0, 100, 100, 0) if mode == "CMYK" else None,
    )
    processed = process_product_image(
        source,
        declared_media_type=media_type,
        staging_root=tmp_path / "catalog",
        config=_config(),
    )
    try:
        assert processed.master.media_type == "image/webp"
        assert (processed.master.width, processed.master.height) == (2000, 1000)
        assert (processed.thumbnail.width, processed.thumbnail.height) == (640, 320)
        assert len(processed.master.sha256) == 64
        assert len(processed.thumbnail.sha256) == 64
        assert processed.master.sha256 == hashlib.sha256(
            processed.master.path.read_bytes()
        ).hexdigest()
        with Image.open(processed.master.path) as master:
            master.load()
            assert master.format == "WEBP"
            assert not master.getexif()
            assert "icc_profile" not in master.info
    finally:
        cleanup_processed_product_image(processed)


def test_small_image_is_not_upscaled(tmp_path):
    source = _save_image(tmp_path / "small.png", size=(500, 400), image_format="PNG")
    processed = process_product_image(
        source,
        declared_media_type="image/png",
        staging_root=tmp_path / "catalog",
        config=_config(),
    )
    try:
        assert (processed.master.width, processed.master.height) == (500, 400)
        assert (processed.thumbnail.width, processed.thumbnail.height) == (500, 400)
    finally:
        cleanup_processed_product_image(processed)


def test_transparent_png_preserves_alpha_in_master_and_thumbnail(tmp_path):
    source = _save_image(
        tmp_path / "transparent.png",
        size=(800, 400),
        mode="RGBA",
        image_format="PNG",
        color=(25, 50, 75, 0),
    )
    processed = process_product_image(
        source,
        declared_media_type="image/png",
        staging_root=tmp_path / "catalog",
        config=_config(),
    )
    try:
        for derivative in (processed.master, processed.thumbnail):
            with Image.open(derivative.path) as image:
                image.load()
                assert image.mode == "RGBA"
                assert image.getpixel((0, 0))[3] == 0
    finally:
        cleanup_processed_product_image(processed)


def test_exif_orientation_is_applied_and_metadata_is_stripped(tmp_path):
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (40, 20), (10, 20, 30))
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "Private Camera Make"
    exif[272] = "Private Camera Model"
    exif[34853] = {1: "N", 2: (1.0, 2.0, 3.0)}
    image.save(source, format="JPEG", exif=exif)
    image.close()

    processed = process_product_image(
        source,
        declared_media_type="image/jpeg",
        staging_root=tmp_path / "catalog",
        config=_config(),
    )
    try:
        assert (processed.master.width, processed.master.height) == (20, 40)
        with Image.open(processed.master.path) as master:
            master.load()
            assert not master.getexif()
            assert "exif" not in master.info
    finally:
        cleanup_processed_product_image(processed)


def test_animated_webp_is_rejected_without_derivatives(tmp_path):
    source = tmp_path / "animated.webp"
    frames = [
        Image.new("RGB", (20, 20), "red"),
        Image.new("RGB", (20, 20), "blue"),
    ]
    frames[0].save(
        source,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    for frame in frames:
        frame.close()

    with pytest.raises(ProductImageFormatError):
        process_product_image(
            source,
            declared_media_type="image/webp",
            staging_root=tmp_path / "catalog",
            config=_config(),
        )
    assert not list((tmp_path / "catalog" / ".staging").glob("product-media-*"))


def test_corrupt_full_decode_and_domain_limit_fail_closed(tmp_path):
    valid = _save_image(tmp_path / "valid.jpg", size=(100, 100))
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(valid.read_bytes()[:100])
    with pytest.raises(ProductImageProcessingError):
        process_product_image(
            corrupt,
            declared_media_type="image/jpeg",
            staging_root=tmp_path / "catalog",
            config=_config(),
        )
    with pytest.raises(ProductImageLimitError):
        process_product_image(
            valid,
            declared_media_type="image/jpeg",
            staging_root=tmp_path / "catalog",
            config=_config(max_source_pixels=9_999),
        )
    assert not list((tmp_path / "catalog" / ".staging").glob("product-media-*"))


def test_thumbnail_save_failure_removes_partial_staging(tmp_path, monkeypatch):
    source = _save_image(tmp_path / "source.jpg")
    original_save = Image.Image.save

    def fail_thumbnail(image, fp, *args, **kwargs):
        if Path(fp).name == "thumbnail.webp":
            raise OSError("simulated thumbnail failure")
        return original_save(image, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_thumbnail)
    with pytest.raises(ProductImageProcessingError):
        process_product_image(
            source,
            declared_media_type="image/jpeg",
            staging_root=tmp_path / "catalog",
            config=_config(),
        )
    assert source.is_file()
    assert not list((tmp_path / "catalog" / ".staging").glob("product-media-*"))


def test_promotion_failure_removes_both_partial_derivatives(tmp_path, monkeypatch):
    source = _save_image(tmp_path / "source.jpg")
    root = tmp_path / "catalog"
    processed = process_product_image(
        source,
        declared_media_type="image/jpeg",
        staging_root=root,
        config=_config(),
    )
    master_key, thumbnail_key = product_derivative_storage_keys(
        __import__("uuid").uuid4(),
        "public-id",
    )
    original_replace = __import__("os").replace
    calls = 0

    def fail_second_replace(source_path, destination_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated promotion failure")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr("app.services.product_image_processing.os.replace", fail_second_replace)
    with pytest.raises(ProductImageProcessingError):
        promote_processed_product_image(
            processed,
            media_root=root,
            master_storage_key=master_key,
            thumbnail_storage_key=thumbnail_key,
        )
    assert not (root / master_key).exists()
    assert not (root / thumbnail_key).exists()
    assert source.is_file()
