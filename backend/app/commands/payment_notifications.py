from __future__ import annotations

import click
from flask.cli import with_appcontext

from app.extensions import db
from app.services.payment_notifications import dispatch_payment_notifications


@click.group("payment-notifications")
def payment_notifications_command() -> None:
    """Despacha notificaciones durables del dominio Payments."""


@payment_notifications_command.command("dispatch")
@click.option("--limit", type=click.IntRange(1, 500), default=50, show_default=True)
@with_appcontext
def dispatch_command(limit: int) -> None:
    result = dispatch_payment_notifications(db.session, limit=limit)
    db.session.commit()
    click.echo(" ".join(f"{key}={value}" for key, value in result.items()))
