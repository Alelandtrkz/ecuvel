from __future__ import annotations

import click
from flask import current_app

from app.extensions import db
from app.services.product_image_processing import product_image_processing_config
from app.services.product_media_optimization import (
    ProductMediaOptimizationError,
    inspect_legacy_product_media,
    optimize_product_media,
)


@click.group("product-media")
def product_media_command() -> None:
    """Inspect and optimize published product media."""


@product_media_command.command("optimize-legacy")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Procesar y persistir; sin esta opción el comando es dry-run.",
)
def optimize_legacy_product_media_command(apply_changes: bool) -> None:
    config = product_image_processing_config(current_app.config)
    root = current_app.config["PRODUCT_CATALOG_MEDIA_DIR"]
    report = inspect_legacy_product_media(
        db.session,
        media_root=root,
        config=config,
    )
    click.echo("APPLY" if apply_changes else "DRY RUN")
    click.echo(
        f"legacy={report.legacy_count} processed={report.processed_count} "
        f"processable={report.processable_count} failures={report.failure_count} "
        f"legacy_bytes={report.legacy_bytes}"
    )
    for entry in report.entries:
        click.echo(
            f"{entry.public_id} product={entry.product_slug} "
            f"type={entry.media_type} size={entry.size_bytes} "
            f"dimensions={entry.width}x{entry.height} status={entry.status}"
        )
    if not apply_changes:
        return

    successes = 0
    skipped = 0
    for entry in report.entries:
        if entry.status == "processed":
            skipped += 1
            continue
        try:
            result = optimize_product_media(
                db.session,
                media_id=entry.media_id,
                media_root=root,
                config=config,
            )
        except ProductMediaOptimizationError as exc:
            db.session.rollback()
            click.echo(
                f"failed public_id={entry.public_id}: {exc}",
                err=True,
            )
            raise click.ClickException(
                f"Backfill detenido: successes={successes} failures=1."
            ) from exc
        if result.status == "skipped":
            skipped += 1
        else:
            successes += 1
            click.echo(
                f"optimized public_id={result.public_id} "
                f"master_bytes={result.master_size_bytes} "
                f"thumbnail_bytes={result.thumbnail_size_bytes} "
                f"original_deleted={str(result.original_deleted).lower()}"
            )
    click.echo(f"completed successes={successes} skipped={skipped} failures=0")
