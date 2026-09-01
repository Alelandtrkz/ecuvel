from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from app.models import Product, ProductMedia, ProductVariant, SellerOffer
from app.services.product_image_processing import ProductImageProcessingConfig
from app.services.product_media_optimization import (
    ProductMediaOptimizationError,
    inspect_legacy_product_media,
    optimize_product_media,
)
from tests.factories import BaseData, create_catalog_and_stock


pytestmark = pytest.mark.integration


def _config() -> ProductImageProcessingConfig:
    return ProductImageProcessingConfig()


def _product(session: Session, base: BaseData) -> Product:
    offer = session.get(SellerOffer, base.offer_id)
    assert offer is not None
    variant = session.get(ProductVariant, offer.variant_id)
    assert variant is not None
    product = session.get(Product, variant.product_id)
    assert product is not None
    return product


def _legacy_media(
    session: Session,
    root: Path,
    product: Product,
    *,
    exists: bool = True,
    media_type: str = "image/jpeg",
) -> tuple[ProductMedia, Path]:
    public_id = uuid.uuid4().hex
    storage_key = f"legacy/{public_id}.jpg"
    path = root / storage_key
    if exists:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1200, 800), (10, 50, 90))
        image.save(path, format="JPEG", quality=90)
        image.close()
    size = path.stat().st_size if path.exists() else 100
    media = ProductMedia(
        product_id=product.id,
        public_id=public_id,
        storage_key=storage_key,
        media_type=media_type,
        size_bytes=size,
        width=1200,
        height=800,
        position=3,
        is_cover=True,
        variant_axis_key="color",
        variant_value_key="blue",
        is_active=True,
    )
    session.add(media)
    session.flush()
    return media, path


def test_dry_run_inspects_without_database_or_filesystem_mutation(
    session: Session, tmp_path
):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    media, source = _legacy_media(session, tmp_path, product)
    session.commit()
    before_files = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    report = inspect_legacy_product_media(
        session,
        media_root=tmp_path,
        config=_config(),
    )

    assert report.legacy_count == 1
    assert report.processable_count == 1
    assert report.failure_count == 0
    assert report.legacy_bytes == media.size_bytes
    assert source.is_file()
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before_files
    session.refresh(media)
    assert media.content_sha256 is None
    assert media.thumbnail_storage_key is None


def test_apply_preserves_identity_is_idempotent_and_purges_original(
    session: Session, tmp_path
):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    media, source = _legacy_media(session, tmp_path, product)
    identity = (
        media.id,
        media.public_id,
        media.product_id,
        media.variant_axis_key,
        media.variant_value_key,
        media.position,
        media.is_cover,
    )
    session.commit()

    first = optimize_product_media(
        session,
        media_id=media.id,
        media_root=tmp_path,
        config=_config(),
    )
    refreshed = session.get(ProductMedia, media.id)
    assert refreshed is not None
    assert first.status == "processed"
    assert first.original_deleted is True
    assert not source.exists()
    assert refreshed.media_type == "image/webp"
    assert refreshed.storage_key.endswith("/master.webp")
    assert refreshed.thumbnail_storage_key.endswith("/thumbnail.webp")
    assert len(refreshed.content_sha256) == 64
    assert len(refreshed.thumbnail_sha256) == 64
    assert (tmp_path / refreshed.storage_key).is_file()
    assert (tmp_path / refreshed.thumbnail_storage_key).is_file()
    assert identity == (
        refreshed.id,
        refreshed.public_id,
        refreshed.product_id,
        refreshed.variant_axis_key,
        refreshed.variant_value_key,
        refreshed.position,
        refreshed.is_cover,
    )
    files_after_first = {
        path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()
    }

    second = optimize_product_media(
        session,
        media_id=media.id,
        media_root=tmp_path,
        config=_config(),
    )
    assert second.status == "skipped"
    assert {
        path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()
    } == files_after_first
    assert len(files_after_first) == 2
    assert not list((tmp_path / ".staging").glob("product-media-*"))


def test_missing_source_fails_without_database_mutation(session: Session, tmp_path):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    media, _source = _legacy_media(session, tmp_path, product, exists=False)
    old_key = media.storage_key
    session.commit()

    with pytest.raises(ProductMediaOptimizationError):
        optimize_product_media(
            session,
            media_id=media.id,
            media_root=tmp_path,
            config=_config(),
        )
    session.rollback()
    refreshed = session.get(ProductMedia, media.id)
    assert refreshed.storage_key == old_key
    assert refreshed.content_sha256 is None
    assert not list((tmp_path / ".staging").glob("product-media-*"))


def test_commit_failure_removes_derivatives_and_preserves_original(
    session: Session, tmp_path, monkeypatch
):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    media, source = _legacy_media(session, tmp_path, product)
    old_key = media.storage_key
    session.commit()

    def fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        optimize_product_media(
            session,
            media_id=media.id,
            media_root=tmp_path,
            config=_config(),
        )
    assert source.is_file()
    session.expire_all()
    refreshed = session.get(ProductMedia, media.id)
    assert refreshed.storage_key == old_key
    assert refreshed.content_sha256 is None
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == {source}


def test_original_delete_failure_leaves_safe_orphan_after_verified_commit(
    session: Session, tmp_path, monkeypatch
):
    base = create_catalog_and_stock(session)
    product = _product(session, base)
    media, source = _legacy_media(session, tmp_path, product)
    session.commit()
    original_unlink = Path.unlink

    def fail_original(path, *args, **kwargs):
        if path == source:
            raise OSError("simulated cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_original)
    result = optimize_product_media(
        session,
        media_id=media.id,
        media_root=tmp_path,
        config=_config(),
    )
    refreshed = session.get(ProductMedia, media.id)
    assert result.status == "processed"
    assert result.original_deleted is False
    assert source.is_file()
    assert (tmp_path / refreshed.storage_key).is_file()
    assert (tmp_path / refreshed.thumbnail_storage_key).is_file()


def test_cli_is_dry_run_by_default_and_apply_is_idempotent(app, session, tmp_path):
    previous_root = app.config["PRODUCT_CATALOG_MEDIA_DIR"]
    app.config["PRODUCT_CATALOG_MEDIA_DIR"] = str(tmp_path)
    try:
        base = create_catalog_and_stock(session)
        product = _product(session, base)
        media, source = _legacy_media(session, tmp_path, product)
        session.commit()
        runner = app.test_cli_runner()

        dry_run = runner.invoke(args=["product-media", "optimize-legacy"])
        assert dry_run.exit_code == 0
        assert "DRY RUN" in dry_run.output
        assert "processable=1" in dry_run.output
        assert source.is_file()
        session.expire_all()
        assert session.get(ProductMedia, media.id).content_sha256 is None

        applied = runner.invoke(
            args=["product-media", "optimize-legacy", "--apply"]
        )
        assert applied.exit_code == 0
        assert "completed successes=1" in applied.output
        assert not source.exists()

        repeated = runner.invoke(
            args=["product-media", "optimize-legacy", "--apply"]
        )
        assert repeated.exit_code == 0
        assert "successes=0 skipped=1 failures=0" in repeated.output
    finally:
        app.config["PRODUCT_CATALOG_MEDIA_DIR"] = previous_root
