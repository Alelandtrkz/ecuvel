from __future__ import annotations

import click
from flask.cli import with_appcontext

from app.extensions import db
from app.services.bank_accounts import (
    BankAccountVersionError,
    backfill_legacy_bank_account_versions,
)


@click.group("bank-accounts")
def bank_accounts_command() -> None:
    """Operaciones seguras sobre versiones bancarias de tiendas."""


@bank_accounts_command.command("backfill-versions")
@with_appcontext
def backfill_versions_command() -> None:
    """Importa idempotentemente cuentas legacy sin mostrar datos sensibles."""

    try:
        result = backfill_legacy_bank_account_versions(db.session)
        db.session.commit()
    except BankAccountVersionError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(
        "eligible={eligible} created={created} existing={existing} skipped={skipped}".format(
            eligible=result.eligible,
            created=result.created,
            existing=result.existing,
            skipped=result.skipped,
        )
    )
