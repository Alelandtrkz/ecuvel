from __future__ import annotations

import click
from flask.cli import with_appcontext

from app.extensions import db
from app.services.review_moderation import bootstrap_review_moderation_terms
from app.services.review_notifications import dispatch_review_notifications


@click.group("review-moderation")
def review_moderation_command() -> None:
    """Gestiona las reglas determinísticas de reseñas."""


@review_moderation_command.command("bootstrap")
@with_appcontext
def bootstrap_command() -> None:
    result = bootstrap_review_moderation_terms(db.session)
    db.session.commit()
    click.echo(
        f"version={result.version} created={result.created} "
        f"updated={result.updated} unchanged={result.unchanged}"
    )


@click.group("review-notifications")
def review_notifications_command() -> None:
    """Despacha notificaciones durables del dominio Reviews."""


@review_notifications_command.command("dispatch")
@click.option("--limit", type=click.IntRange(1, 500), default=50, show_default=True)
@with_appcontext
def dispatch_command(limit: int) -> None:
    result = dispatch_review_notifications(db.session, limit=limit)
    db.session.commit()
    click.echo(" ".join(f"{key}={value}" for key, value in result.items()))
