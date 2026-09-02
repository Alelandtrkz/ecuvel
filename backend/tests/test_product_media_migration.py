from __future__ import annotations

from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Product, ProductMedia, ProductVariant, SellerOffer
from tests.factories import create_catalog_and_stock


pytestmark = pytest.mark.integration

PREVIOUS_HEAD = "b8c9d0e1f2a3"
H2_HEAD = "c9d0e1f2a3b4"
CURRENT_HEAD = "da1b2c3d4e5f"
H2_COLUMNS = {
    "content_sha256",
    "thumbnail_storage_key",
    "thumbnail_media_type",
    "thumbnail_size_bytes",
    "thumbnail_width",
    "thumbnail_height",
    "thumbnail_sha256",
    "processing_version",
}


def _migrations_dir(app) -> Path:
    return Path(app.root_path).parent / "migrations"


def _migrate(app, revision: str, *, down: bool = False) -> None:
    with app.app_context():
        db.session.remove()
        operation = downgrade if down else upgrade
        operation(revision=revision, directory=str(_migrations_dir(app)))


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _columns(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            column["name"]
            for column in inspect(connection).get_columns("product_media")
        }


def _legacy_media(session):
    base = create_catalog_and_stock(session)
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    media = ProductMedia(
        product_id=product.id,
        public_id="migration-legacy",
        storage_key="legacy/migration.jpg",
        media_type="image/jpeg",
        size_bytes=123,
        width=40,
        height=30,
        position=2,
        is_cover=True,
        is_active=True,
    )
    session.add(media)
    session.commit()
    return media


def test_h2_upgrade_preserves_legacy_identity_and_adds_nullable_metadata(
    app, engine, session
):
    media = _legacy_media(session)
    identity = (media.id, media.public_id, media.product_id, media.position, media.is_cover)
    session.close()
    try:
        _migrate(app, PREVIOUS_HEAD, down=True)
        assert _version(engine) == PREVIOUS_HEAD
        assert not (H2_COLUMNS & _columns(engine))
        with engine.connect() as connection:
            legacy_identity = connection.execute(text(
                "SELECT id, public_id, product_id, position, is_cover "
                "FROM product_media WHERE id = :media_id"
            ), {"media_id": identity[0]}).one()
        assert tuple(legacy_identity) == identity

        _migrate(app, H2_HEAD)
        assert _version(engine) == H2_HEAD
        assert H2_COLUMNS <= _columns(engine)
        with engine.connect() as connection:
            upgraded = connection.execute(text(
                "SELECT id, public_id, product_id, position, is_cover, "
                "content_sha256, thumbnail_storage_key, processing_version "
                "FROM product_media WHERE id = :media_id"
            ), {"media_id": identity[0]}).one()
        assert tuple(upgraded[:5]) == identity
        assert tuple(upgraded[5:]) == (None, None, None)

        _migrate(app, PREVIOUS_HEAD, down=True)
        assert not (H2_COLUMNS & _columns(engine))
        _migrate(app, H2_HEAD)
    finally:
        if _version(engine) != CURRENT_HEAD:
            _migrate(app, "head")


def test_h2_constraints_reject_partial_or_invalid_derivative_metadata(
    session
):
    media = _legacy_media(session)
    media.thumbnail_storage_key = "partial/thumbnail.webp"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    media = session.get(ProductMedia, media.id)
    media.storage_key = "processed/master.webp"
    media.media_type = "image/webp"
    media.content_sha256 = "not-a-hash"
    media.processing_version = 1
    media.thumbnail_storage_key = "processed/thumbnail.webp"
    media.thumbnail_media_type = "image/webp"
    media.thumbnail_size_bytes = 10
    media.thumbnail_width = 10
    media.thumbnail_height = 10
    media.thumbnail_sha256 = "b" * 64
    with pytest.raises(IntegrityError):
        session.flush()


def test_h2_downgrade_fails_closed_when_verified_derivatives_exist(
    app, engine, session
):
    media = _legacy_media(session)
    media.storage_key = "processed/master.webp"
    media.media_type = "image/webp"
    media.content_sha256 = "a" * 64
    media.processing_version = 1
    media.thumbnail_storage_key = "processed/thumbnail.webp"
    media.thumbnail_media_type = "image/webp"
    media.thumbnail_size_bytes = 10
    media.thumbnail_width = 10
    media.thumbnail_height = 10
    media.thumbnail_sha256 = "b" * 64
    session.commit()
    session.close()

    with pytest.raises(SystemExit) as blocked:
        _migrate(app, PREVIOUS_HEAD, down=True)
    assert blocked.value.code == 1
    assert _version(engine) == CURRENT_HEAD
    assert H2_COLUMNS <= _columns(engine)


def test_fresh_database_upgrades_from_base_to_h2_head(app, engine, session):
    session.close()
    try:
        _migrate(app, "base", down=True)
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        assert "product_media" not in tables
        _migrate(app, H2_HEAD)
        assert _version(engine) == H2_HEAD
        assert H2_COLUMNS <= _columns(engine)
    finally:
        if _version(engine) != CURRENT_HEAD:
            _migrate(app, "head")
