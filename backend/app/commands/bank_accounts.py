from __future__ import annotations

import click
from dataclasses import asdict
from flask.cli import with_appcontext

from app.extensions import db
from app.services.bank_accounts import (
    BankAccountVersionError,
    backfill_legacy_bank_account_versions,
    cleanup_legacy_onboarding_bank_data,
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


@bank_accounts_command.command("cleanup-legacy-onboarding")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Aplica el purge solo si toda la cohorte supera el gate criptográfico.",
)
@with_appcontext
def cleanup_legacy_onboarding_command(*, apply_changes: bool) -> None:
    """Audita o purga plaintext legacy sin imprimir datos bancarios."""

    try:
        result = cleanup_legacy_onboarding_bank_data(
            db.session,
            apply=apply_changes,
        )
        if apply_changes:
            db.session.commit()
        else:
            db.session.rollback()
    except BankAccountVersionError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    values = asdict(result)
    click.echo(f"mode={'apply' if apply_changes else 'dry-run'}")
    for key in sorted(values):
        click.echo(f"{key}={values[key]}")
