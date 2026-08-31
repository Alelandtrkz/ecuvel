from __future__ import annotations

import mimetypes
from datetime import date, datetime, timezone
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import or_, select
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Store
from app.services.payout_calendar import PAYOUT_TIMEZONE, payout_cycle_windows
from app.services.private_storage import (
    StagedPrivateFile, delete_private_file, private_file_path, stage_private_upload,
)
from app.services.seller_payouts import (
    SellerPayoutError, backfill_seller_order_deliveries, cancel_seller_payout,
    hold_seller_payout, mark_seller_payout_paid, preview_payout_cycle,
    resume_seller_payout, schedule_payout_cycle,
)


def _parse_datetime(value: str, *, option_name: str) -> datetime:
    candidate = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter("usa ISO 8601 con zona horaria", param_hint=option_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise click.BadParameter("debe incluir zona horaria", param_hint=option_name)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str, *, option_name: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise click.BadParameter("usa YYYY-MM-DD", param_hint=option_name) from exc


def _store(value: str | None) -> Store | None:
    if value is None:
        return None
    normalized = value.strip()
    store = db.session.scalar(select(Store).where(
        or_(Store.public_code == normalized, Store.slug == normalized)
    ))
    if store is None:
        raise click.ClickException("No existe la tienda indicada.")
    return store


@click.group("seller-payouts")
def seller_payouts_command() -> None:
    """Operaciones financieras administrativas ECUVEL → vendedor."""


@seller_payouts_command.command("calendar")
@click.option("--month", required=True, help="Mes YYYY-MM.")
@with_appcontext
def payout_calendar_command(month: str) -> None:
    try:
        parsed = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise click.BadParameter("usa YYYY-MM", param_hint="--month") from exc
    click.echo(f"Timezone: {PAYOUT_TIMEZONE.key}")
    for window in payout_cycle_windows(parsed.year, parsed.month):
        click.echo(
            f"{window.cycle_kind.value} | ciclo {window.cycle_date_local.isoformat()} | "
            f"cutoff {window.cutoff_local.isoformat()}"
        )


def _echo_previews(cycle_date: date, store: Store | None) -> int:
    window, previews = preview_payout_cycle(
        db.session, cycle_date=cycle_date, now=datetime.now(timezone.utc),
        store_id=store.id if store else None,
    )
    click.echo(
        f"Ciclo {window.cycle_date_local.isoformat()} | "
        f"cutoff {window.cutoff_local.isoformat()} | {PAYOUT_TIMEZONE.key}"
    )
    for row in previews:
        click.echo(
            f"{row.store_public_code} | {row.order_count} pedidos | {row.currency} | "
            f"gross {row.gross_total:.2f} | discount {row.discount_total:.2f} | "
            f"commission {row.commission_total:.2f} | net {row.net_total:.2f}"
        )
    if not previews:
        click.echo("No existen pedidos elegibles para este ciclo.")
    return len(previews)


@seller_payouts_command.command("preview-cycle")
@click.option("--date", "cycle_date_value", required=True, help="Ciclo YYYY-MM-DD.")
@click.option("--store", "store_value", default=None, help="Código público o slug.")
@with_appcontext
def preview_cycle_command(cycle_date_value: str, store_value: str | None) -> None:
    cycle_date = _parse_date(cycle_date_value, option_name="--date")
    try:
        _echo_previews(cycle_date, _store(store_value))
    except SellerPayoutError as exc:
        raise click.ClickException(str(exc)) from exc


@seller_payouts_command.command("schedule-cycle")
@click.option("--date", "cycle_date_value", required=True, help="Ciclo YYYY-MM-DD.")
@click.option("--store", "store_value", default=None, help="Código público o slug.")
@click.option("--apply", "apply_changes", is_flag=True, help="Crear los payouts.")
@with_appcontext
def schedule_cycle_command(
    cycle_date_value: str, store_value: str | None, apply_changes: bool
) -> None:
    cycle_date = _parse_date(cycle_date_value, option_name="--date")
    store = _store(store_value)
    if not apply_changes:
        click.echo("DRY RUN")
        try:
            _echo_previews(cycle_date, store)
        except SellerPayoutError as exc:
            raise click.ClickException(str(exc)) from exc
        return
    try:
        result = schedule_payout_cycle(
            db.session, cycle_date=cycle_date, now=datetime.now(timezone.utc),
            store_id=store.id if store else None,
        )
        db.session.commit()
    except SellerPayoutError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    for scheduled in result.payouts:
        click.echo(
            f"{scheduled.payout.payout_number} creado: {scheduled.order_count} pedidos, "
            f"USD {scheduled.payout.net_total:.2f}."
        )
    click.echo(
        f"Resultado: {len(result.payouts)} payouts; "
        f"{len(result.skipped_store_ids)} tiendas omitidas."
    )


def _transition(command, payout_number: str) -> None:
    try:
        result = command(db.session, payout_number=payout_number)
        db.session.commit()
    except SellerPayoutError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{result.payout.payout_number}: {result.previous_status.value} → "
        f"{result.payout.status.value}"
        + (" (idempotente)" if result.replayed else "")
    )


@seller_payouts_command.command("hold")
@click.option("--payout", "payout_number", required=True)
@with_appcontext
def hold_command(payout_number: str) -> None:
    _transition(hold_seller_payout, payout_number)


@seller_payouts_command.command("resume")
@click.option("--payout", "payout_number", required=True)
@with_appcontext
def resume_command(payout_number: str) -> None:
    _transition(resume_seller_payout, payout_number)


@seller_payouts_command.command("cancel")
@click.option("--payout", "payout_number", required=True)
@with_appcontext
def cancel_command(payout_number: str) -> None:
    def operation(session, *, payout_number):
        return cancel_seller_payout(
            session, payout_number=payout_number,
            cancelled_at=datetime.now(timezone.utc),
        )
    _transition(operation, payout_number)


@seller_payouts_command.command("mark-paid")
@click.option("--payout", "payout_number", required=True)
@click.option("--reference", required=True, help="Referencia bancaria o externa.")
@click.option("--paid-at", required=True, help="Fecha real ISO 8601 con zona horaria.")
@click.option(
    "--receipt", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None, help="Comprobante privado JPEG, PNG o PDF.",
)
@with_appcontext
def mark_paid_command(
    payout_number: str, reference: str, paid_at: str, receipt: Path | None
) -> None:
    effective_paid_at = _parse_datetime(paid_at, option_name="--paid-at")
    staged: StagedPrivateFile | None = None
    promoted_path: Path | None = None
    receipt_root = current_app.config["SELLER_PAYOUT_RECEIPT_DIR"]
    try:
        if receipt is not None:
            media_type = mimetypes.guess_type(receipt.name)[0] or ""
            with receipt.open("rb") as stream:
                staged = stage_private_upload(
                    FileStorage(stream=stream, filename=receipt.name, content_type=media_type),
                    root=receipt_root,
                    max_bytes=current_app.config["SELLER_PAYOUT_RECEIPT_MAX_BYTES"],
                    allowed_extensions={"jpg", "jpeg", "png", "pdf"},
                    storage_prefix="payout-receipts",
                )
            promoted_path = private_file_path(receipt_root, staged.storage_key)
        result = mark_seller_payout_paid(
            db.session, payout_number=payout_number, external_reference=reference,
            paid_at=effective_paid_at, staged_receipt=staged, receipt_root=receipt_root,
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if staged is not None:
            delete_private_file(staged.temporary_path)
        if promoted_path is not None:
            delete_private_file(promoted_path)
        if isinstance(exc, SellerPayoutError):
            raise click.ClickException(str(exc)) from exc
        raise
    click.echo(
        f"{result.payout.payout_number} pagado."
        + (" Operación idempotente." if result.replayed else "")
    )


@seller_payouts_command.command("backfill-deliveries")
@click.option("--limit", type=click.IntRange(1, 10_000), default=1000, show_default=True)
@with_appcontext
def backfill_deliveries_command(limit: int) -> None:
    try:
        results = backfill_seller_order_deliveries(db.session, limit=limit)
        db.session.commit()
    except SellerPayoutError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{len(results)} SellerOrders sincronizadas desde evidencia real.")
