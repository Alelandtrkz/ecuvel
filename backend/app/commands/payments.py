from __future__ import annotations

import uuid

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Order,
    PaymentAttempt,
    PaymentProof,
    PaymentProofAnalysis,
    User,
)
from app.models.enums import (
    PaymentStatus,
    PaymentProofAnalysisStatus,
    PaymentProofStatus,
    UserStatus,
)
from app.services.pending_payments import (
    InvalidPendingPaymentTransitionError,
    PendingPaymentServiceError,
    cancel_pending_bank_transfer_order,
    expirable_bank_transfer_payment_ids,
    expire_pending_bank_transfer_payments,
)
from app.services.payment_precheck import (
    PaymentPrecheckConfig,
    analyze_payment_proof,
)
from app.services.payment_precheck.analyzer import PaymentPrecheckError
from app.services.payment_proofs import (
    PaymentProofServiceError,
    review_payment_proof,
)


@click.command("list-pending-payment-proofs")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@with_appcontext
def list_pending_payment_proofs(limit: int) -> None:
    rows = db.session.execute(
        select(PaymentProof, PaymentAttempt, Order, PaymentProofAnalysis)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentProof.payment_attempt_id)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .outerjoin(
            PaymentProofAnalysis,
            PaymentProofAnalysis.payment_proof_id == PaymentProof.id,
        )
        .where(PaymentProof.status == PaymentProofStatus.PENDING_REVIEW)
        .order_by(PaymentProof.created_at, PaymentProof.id)
        .limit(limit)
    ).all()
    if not rows:
        click.echo("No hay comprobantes pendientes.")
        return
    for proof, attempt, order, analysis in rows:
        precheck = (
            f"{analysis.processing_status.value}/"
            f"{analysis.outcome.value if analysis.outcome else '-'}"
            if analysis
            else "SIN_ANALISIS"
        )
        click.echo(
            f"{proof.id} | {order.order_number} | "
            f"{attempt.currency} {attempt.amount:.2f} | "
            f"{proof.created_at.isoformat()} | {proof.status.value} | {precheck}"
        )
        if analysis:
            click.echo(
                "  banco="
                f"{analysis.bank_name_detected or '-'} "
                f"reconocido={analysis.bank_is_recognized} "
                f"monto={analysis.amount_detected if analysis.amount_detected is not None else '-'} "
                "cuenta="
                f"{'****' + analysis.destination_account_suffix if analysis.destination_account_suffix else '-'} "
                f"fecha={analysis.transaction_at_detected.isoformat() if analysis.transaction_at_detected else '-'} "
                f"comprobante={analysis.receipt_number_detected or '-'} "
                f"duplicado={analysis.receipt_appears_unique is False} "
                f"confianza={analysis.ocr_mean_confidence if analysis.ocr_mean_confidence is not None else '-'}"
            )
            click.echo(
                "  hallazgos="
                + ",".join(item.get("code", "") for item in analysis.findings)
            )
        click.echo("  Aprobación manual requerida: sí")


def _print_analysis_summary(proof_id: uuid.UUID) -> None:
    row = db.session.execute(
        select(PaymentProofAnalysis, PaymentAttempt, Order)
        .join(PaymentProof, PaymentProof.id == PaymentProofAnalysis.payment_proof_id)
        .join(PaymentAttempt, PaymentAttempt.id == PaymentProof.payment_attempt_id)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .where(PaymentProofAnalysis.payment_proof_id == proof_id)
    ).one_or_none()
    click.echo("Prevalidación automática")
    if row is None:
        click.echo("Estado: sin análisis")
        click.echo("Aprobación manual requerida: sí")
        return
    analysis, attempt, order = row
    yes_no = {True: "sí", False: "no", None: "no determinado"}
    click.echo(f"Estado: {analysis.processing_status.value}")
    click.echo(f"Resultado preliminar: {analysis.outcome.value if analysis.outcome else '-'}")
    click.echo(f"Versión del analizador: {analysis.analyzer_version}")
    click.echo(f"QR detectado: {'sí' if analysis.qr_detected else 'no'}")
    click.echo(
        "Banco detectado: "
        + (analysis.bank_name_detected or "no detectado")
    )
    click.echo(f"Banco reconocido: {yes_no[analysis.bank_is_recognized]}")
    click.echo(f"Monto esperado: {attempt.currency} {attempt.amount:.2f}")
    click.echo(
        "Monto detectado: "
        + (f"{attempt.currency} {analysis.amount_detected:.2f}" if analysis.amount_detected is not None else "no detectado")
    )
    expected = current_app.config.get("BANK_TRANSFER_ACCOUNT_LAST4")
    click.echo(
        f"Cuenta esperada: {'****' + expected if expected else 'no configurada'}"
    )
    click.echo(
        "Cuenta detectada: "
        + ("****" + analysis.destination_account_suffix if analysis.destination_account_suffix else "no detectada")
    )
    click.echo(f"Monto coincide: {yes_no[analysis.amount_matches]}")
    click.echo(f"Cuenta coincide: {yes_no[analysis.destination_account_matches]}")
    click.echo(
        "Fecha detectada: "
        + (
            analysis.transaction_at_detected.isoformat()
            if analysis.transaction_at_detected
            else "no detectada"
        )
    )
    click.echo(f"Fecha plausible: {yes_no[analysis.date_is_plausible]}")
    click.echo(
        "Comprobante: "
        + (analysis.receipt_number_detected or "no detectado")
    )
    click.echo(
        f"Referencia sin duplicado conocido: "
        f"{yes_no[analysis.receipt_appears_unique]}"
    )
    click.echo(f"Pedido: {order.order_number}")
    click.echo(
        "Confianza OCR: "
        + (f"{analysis.ocr_mean_confidence:.2f}" if analysis.ocr_mean_confidence is not None else "no disponible")
    )
    click.echo(
        "Hallazgos: "
        + ", ".join(item.get("code", "") for item in analysis.findings)
    )
    click.echo("Aprobación manual requerida: sí")


@click.command("analyze-payment-proof")
@click.option("--proof-id", type=click.UUID, required=True)
@click.option("--force", is_flag=True)
@with_appcontext
def analyze_payment_proof_command(proof_id: uuid.UUID, force: bool) -> None:
    db.session.remove()
    try:
        result = analyze_payment_proof(
            session_factory=db.session,
            payment_proof_id=proof_id,
            config=PaymentPrecheckConfig.from_mapping(current_app.config),
            force=force,
        )
    except PaymentPrecheckError as exc:
        raise click.ClickException(str(exc)) from exc
    _print_analysis_summary(proof_id)
    click.echo(f"Ejecución repetida: {'sí' if result.replayed else 'no'}")


@click.command("analyze-pending-payment-proofs")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@click.option("--retry-failed", is_flag=True)
@with_appcontext
def analyze_pending_payment_proofs(limit: int, retry_failed: bool) -> None:
    statement = (
        select(PaymentProof.id)
        .outerjoin(
            PaymentProofAnalysis,
            PaymentProofAnalysis.payment_proof_id == PaymentProof.id,
        )
        .where(PaymentProof.status == PaymentProofStatus.PENDING_REVIEW)
        .order_by(PaymentProof.created_at, PaymentProof.id)
        .limit(limit)
    )
    eligible = PaymentProofAnalysis.id.is_(None)
    if retry_failed:
        eligible = eligible | (
            PaymentProofAnalysis.processing_status
            == PaymentProofAnalysisStatus.FAILED
        )
    statement = statement.where(eligible)
    proof_ids = list(db.session.scalars(statement))
    config = PaymentPrecheckConfig.from_mapping(current_app.config)
    for proof_id in proof_ids:
        db.session.remove()
        try:
            result = analyze_payment_proof(
                session_factory=db.session,
                payment_proof_id=proof_id,
                config=config,
                force=retry_failed,
            )
            click.echo(
                f"{proof_id}: {result.processing_status.value}/"
                f"{result.outcome.value if result.outcome else '-'}"
            )
        except PaymentPrecheckError as exc:
            click.echo(f"{proof_id}: error: {exc}", err=True)


@click.command("cancel-pending-order")
@click.option("--order-number", required=True, type=str)
@click.option(
    "--reason",
    default="Pedido cancelado desde CLI antes del comprobante.",
    show_default=True,
)
@with_appcontext
def cancel_pending_order_command(order_number: str, reason: str) -> None:
    row = db.session.execute(
        select(PaymentAttempt, Order)
        .join(Order, Order.id == PaymentAttempt.order_id)
        .where(Order.order_number == order_number)
    ).one_or_none()
    if row is None:
        raise click.ClickException("No existe el pedido indicado.")
    attempt, _order = row
    db.session.remove()
    try:
        with db.session.begin():
            result = cancel_pending_bank_transfer_order(
                session=db.session,
                payment_attempt_id=attempt.id,
                reason=reason,
            )
    except PendingPaymentServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Pedido {result.order_number}: {result.payment_status.value}; "
        f"reservas liberadas {result.released_reservations}; "
        f"repetida: {'sí' if result.replayed else 'no'}."
    )


@click.command("expire-pending-bank-transfer-payments")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--dry-run", is_flag=True)
@with_appcontext
def expire_pending_bank_transfer_payments_command(limit: int, dry_run: bool) -> None:
    if dry_run:
        attempt_ids = expirable_bank_transfer_payment_ids(
            session=db.session,
            limit=limit,
            lock=False,
        )
        if not attempt_ids:
            click.echo("No hay pagos vencidos pendientes de expirar.")
            return
        rows = db.session.execute(
            select(PaymentAttempt, Order)
            .join(Order, Order.id == PaymentAttempt.order_id)
            .where(PaymentAttempt.id.in_(attempt_ids))
            .order_by(PaymentAttempt.expires_at, PaymentAttempt.id)
        ).all()
        for attempt, order in rows:
            click.echo(
                f"{order.order_number} | {attempt.id} | "
                f"{attempt.status.value} | vence {attempt.expires_at.isoformat()}"
            )
        click.echo(f"Dry-run: {len(rows)} candidato(s).")
        return

    db.session.remove()
    try:
        with db.session.begin():
            result = expire_pending_bank_transfer_payments(
                session=db.session,
                limit=limit,
            )
    except PendingPaymentServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Procesados {result.processed}; expirados {result.expired}; "
        f"omitidos {result.skipped}."
    )
    for item in result.results:
        click.echo(
            f"{item.order_number}: {item.payment_status.value}; "
            f"reservas liberadas {item.released_reservations}; "
            f"repetida: {'sí' if item.replayed else 'no'}."
        )


@click.command("review-payment-proof")
@click.option("--proof-id", type=click.UUID, required=True)
@click.option(
    "--decision",
    type=click.Choice(["approve", "reject"], case_sensitive=False),
    required=True,
)
@click.option("--reason", type=str)
@click.option("--notes", type=str)
@with_appcontext
def review_payment_proof_command(
    proof_id: uuid.UUID,
    decision: str,
    reason: str | None,
    notes: str | None,
) -> None:
    _print_analysis_summary(proof_id)
    db.session.remove()
    try:
        with db.session.begin():
            reviewer = db.session.scalar(
                select(User).where(
                    User.email == "admin@ecuvel.local",
                    User.status == UserStatus.ACTIVE,
                )
            )
            if reviewer is None:
                raise PaymentProofServiceError(
                    "No existe el revisor activo admin@ecuvel.local."
                )
            result = review_payment_proof(
                session=db.session,
                proof_id=proof_id,
                decision=decision,
                reviewer_user_id=reviewer.id,
                storage_root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
                reason=reason,
                notes=notes,
            )
    except PaymentProofServiceError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Pedido {result.order_number}: {result.proof_status.value}; "
        f"reservas {result.reservation_count}; "
        f"repetida: {'sí' if result.replayed else 'no'}."
    )
