from __future__ import annotations

import click

from app.extensions import db
from app.services.offer_preparation_backfill import (
    OfferPreparationBackfillError,
    backfill_offer_preparation_time,
    inspect_offer_preparation_backfill,
)


@click.group("product-offers")
def product_offers_command() -> None:
    """Maintain published seller offer data."""


@product_offers_command.command("backfill-preparation-time")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Persistir candidatos seguros; sin esta opción el comando es dry-run.",
)
def backfill_preparation_time_command(apply_changes: bool) -> None:
    report = inspect_offer_preparation_backfill(db.session)
    click.echo("APPLY" if apply_changes else "DRY RUN")
    click.echo(
        f"scanned={report.scanned_count} "
        f"populated={report.populated_count} "
        f"candidates={report.candidate_count} "
        f"missing_source={report.missing_source_count} "
        f"invalid_source={report.invalid_source_count} "
        f"untraceable={report.untraceable_count} "
        f"would_update={report.candidate_count}"
    )
    for entry in report.entries:
        click.echo(
            f"offer={entry.offer_id} product={entry.product_slug} "
            f"status={entry.status} current={entry.current_value} "
            f"source={entry.source_value}"
        )
    if not apply_changes:
        return

    successes = 0
    skipped = report.scanned_count - report.candidate_count
    for entry in report.entries:
        if entry.status != "candidate":
            continue
        try:
            result = backfill_offer_preparation_time(
                db.session,
                offer_id=entry.offer_id,
            )
        except OfferPreparationBackfillError as exc:
            db.session.rollback()
            raise click.ClickException(
                f"Backfill detenido: successes={successes} failures=1; {exc}"
            ) from exc
        if result.status == "updated":
            successes += 1
            click.echo(
                f"updated offer={result.offer_id} "
                f"preparation_time_days={result.preparation_time_days}"
            )
        else:
            skipped += 1
    click.echo(
        f"completed successes={successes} skipped={skipped} failures=0"
    )
