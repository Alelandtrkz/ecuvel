from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import or_, select
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Store
from app.models.enums import SellerPayoutStatus
from app.services.private_storage import (
    StagedPrivateFile,
    delete_private_file,
    private_file_path,
    stage_private_upload,
)
from app.services.seller_payouts import (
    SellerPayoutError,
    backfill_seller_order_deliveries,
    eligible_seller_orders,
    mark_seller_payout_paid,
    schedule_seller_payout,
)


def _parse_datetime(value: str, *, option_name: str) -> datetime:
    candidate = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter(
            "usa ISO 8601, por ejemplo 2026-08-25T14:00:00-05:00",
            param_hint=option_name,
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise click.BadParameter(
            "debe incluir zona horaria", param_hint=option_name
        )
    return parsed.astimezone(timezone.utc)


def _store(value: str) -> Store:
    normalized = (value or "").strip()
    store = db.session.scalar(
        select(Store).where(
            or_(Store.public_code == normalized, Store.slug == normalized)
        )
    )
    if store is None:
        raise click.ClickException("No existe la tienda indicada.")
    return store


@click.group("seller-payouts")
def seller_payouts_command() -> None:
    """Operaciones financieras administrativas ECUVEL → vendedor."""


@seller_payouts_command.command("preview")
@click.option("--store", "store_value", required=True, help="Código público o slug.")
@click.option("--limit", type=click.IntRange(1, 10_000), default=1000, show_default=True)
@with_appcontext
def preview_seller_payouts(store_value: str, limit: int) -> None:
    store = _store(store_value)
    rows = eligible_seller_orders(
        db.session, store_id=store.id, limit=limit
    )
    if not rows:
        click.echo("No existen pedidos elegibles para liquidar.")
        return
    for row in rows:
        click.echo(
            f"{row.seller_order_number} | {row.currency} {row.net_amount:.2f} | "
            f"elegible {row.eligible_at.isoformat()}"
        )
    click.echo(
        f"Total: {len(rows)} pedidos | {rows[0].currency} "
        f"{sum((row.net_amount for row in rows), start=rows[0].net_amount * 0):.2f}"
    )


@seller_payouts_command.command("schedule")
@click.option("--store", "store_value", required=True, help="Código público o slug.")
@click.option("--scheduled-for", required=True, help="Fecha ISO 8601 con zona horaria.")
@click.option("--limit", type=click.IntRange(1, 10_000), default=1000, show_default=True)
@click.option("--on-hold", is_flag=True, help="Crear el lote en revisión financiera.")
@click.option("--notes", type=str, default=None)
@with_appcontext
def schedule_payout_command(
    store_value: str,
    scheduled_for: str,
    limit: int,
    on_hold: bool,
    notes: str | None,
) -> None:
    store = _store(store_value)
    scheduled_at = _parse_datetime(scheduled_for, option_name="--scheduled-for")
    try:
        result = schedule_seller_payout(
            db.session,
            store_id=store.id,
            scheduled_for=scheduled_at,
            limit=limit,
            status=(
                SellerPayoutStatus.ON_HOLD
                if on_hold
                else SellerPayoutStatus.SCHEDULED
            ),
            notes=notes,
        )
        db.session.commit()
    except SellerPayoutError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{result.payout.payout_number} creado: {result.order_count} pedidos, "
        f"{result.payout.currency} {result.payout.net_total:.2f}, "
        f"estado {result.payout.status.value}."
    )


@seller_payouts_command.command("mark-paid")
@click.option("--payout", "payout_number", required=True)
@click.option("--reference", required=True, help="Referencia bancaria o externa.")
@click.option("--paid-at", required=True, help="Fecha real ISO 8601 con zona horaria.")
@click.option(
    "--receipt",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Comprobante privado JPEG, PNG o PDF.",
)
@with_appcontext
def mark_paid_command(
    payout_number: str,
    reference: str,
    paid_at: str,
    receipt: Path | None,
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
                    FileStorage(
                        stream=stream,
                        filename=receipt.name,
                        content_type=media_type,
                    ),
                    root=receipt_root,
                    max_bytes=current_app.config["SELLER_PAYOUT_RECEIPT_MAX_BYTES"],
                    allowed_extensions={"jpg", "jpeg", "png", "pdf"},
                    storage_prefix="payout-receipts",
                )
            promoted_path = private_file_path(receipt_root, staged.storage_key)
        result = mark_seller_payout_paid(
            db.session,
            payout_number=payout_number,
            external_reference=reference,
            paid_at=effective_paid_at,
            staged_receipt=staged,
            receipt_root=receipt_root,
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
        f"{result.payout.payout_number} pagado con referencia "
        f"{result.payout.external_reference}."
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
