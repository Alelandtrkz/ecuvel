from __future__ import annotations

import json

import click

from app.extensions import db
from app.services.catalog_listings import load_public_listings
from app.services.catalog_shadow_ranking import ranking_readiness_report


@click.group("catalog-ranking")
def catalog_ranking_command() -> None:
    """Inspect catalog ranking telemetry without mutating it."""


@catalog_ranking_command.command("readiness")
@click.option("--window-days", default=30, type=click.IntRange(1, 365), show_default=True)
def readiness_command(window_days: int) -> None:
    listings = load_public_listings(db.session)
    report = ranking_readiness_report(
        db.session,
        all_listing_keys={listing.listing_key for listing in listings},
        window_days=window_days,
    )
    click.echo(json.dumps(report, sort_keys=True))
