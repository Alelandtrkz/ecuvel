from __future__ import annotations

import uuid

import click
from flask.cli import with_appcontext
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import StoreOnboarding, StoreVerificationReview, User
from app.services.partner_onboarding import PartnerOnboardingError, review_onboarding


@click.command("list-store-onboardings")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@with_appcontext
def list_store_onboardings_command(limit: int) -> None:
    onboardings = db.session.scalars(
        select(StoreOnboarding)
        .options(selectinload(StoreOnboarding.user), selectinload(StoreOnboarding.store))
        .order_by(StoreOnboarding.created_at.desc(), StoreOnboarding.id)
        .limit(limit)
    ).all()
    if not onboardings:
        click.echo("No hay solicitudes de partners.")
        return
    for onboarding in onboardings:
        identity = onboarding.user.email or onboarding.user.phone_normalized or onboarding.user.public_code
        store = onboarding.store.name if onboarding.store else onboarding.store_name or "Sin tienda creada"
        click.echo(
            f"{onboarding.id} | {onboarding.status.value} | paso {onboarding.current_step} | "
            f"{identity} | {store}"
        )


@click.command("show-store-onboarding")
@click.option("--onboarding-id", type=click.UUID, required=True)
@with_appcontext
def show_store_onboarding_command(onboarding_id: uuid.UUID) -> None:
    onboarding = db.session.scalar(
        select(StoreOnboarding)
        .options(
            selectinload(StoreOnboarding.user),
            selectinload(StoreOnboarding.store),
            selectinload(StoreOnboarding.documents),
            selectinload(StoreOnboarding.reviews).selectinload(StoreVerificationReview.reviewer),
        )
        .where(StoreOnboarding.id == onboarding_id)
    )
    if onboarding is None:
        raise click.ClickException("No se encontró la solicitud.")
    click.echo(f"Solicitud: {onboarding.id}")
    click.echo(f"Estado: {onboarding.status.value}")
    click.echo(f"Etapa: {onboarding.current_stage.value}; paso {onboarding.current_step}")
    click.echo(f"Usuario: {onboarding.user.email or onboarding.user.phone_normalized or onboarding.user.public_code}")
    click.echo(f"Tienda: {onboarding.store_name or '-'}")
    click.echo(f"Identificación: {onboarding.legal_id_number or '-'}")
    click.echo(f"Ubicación: {onboarding.province or '-'} / {onboarding.city or '-'}")
    click.echo(f"Documentos: {len(onboarding.documents)}")
    for document in onboarding.documents:
        click.echo(f"  - {document.id} | {document.file_name} | {document.mime_type} | {document.size_bytes} bytes")
    if onboarding.reviews:
        click.echo("Revisiones:")
        for review in sorted(onboarding.reviews, key=lambda item: item.created_at):
            reviewer = review.reviewer.email if review.reviewer else "sistema"
            click.echo(f"  - {review.decision.value} | {reviewer} | {review.comments or '-'}")


@click.command("review-store-onboarding")
@click.option("--onboarding-id", type=click.UUID, required=True)
@click.option("--decision", type=click.Choice(["approve", "corrections", "reject"]), required=True)
@click.option("--comments", default=None, help="Comentarios para auditoría o correcciones.")
@click.option("--reviewer-email", default="admin@ecuvel.local", show_default=True)
@with_appcontext
def review_store_onboarding_command(
    onboarding_id: uuid.UUID,
    decision: str,
    comments: str | None,
    reviewer_email: str,
) -> None:
    try:
        with db.session.begin():
            reviewer = db.session.scalar(select(User).where(User.email == reviewer_email))
            onboarding = review_onboarding(
                session=db.session,
                onboarding_id=onboarding_id,
                reviewer_user_id=reviewer.id if reviewer else None,
                decision=decision,
                comments=comments,
            )
    except PartnerOnboardingError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Solicitud {onboarding.id}: {onboarding.status.value}.")
