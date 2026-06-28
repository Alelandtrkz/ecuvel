from __future__ import annotations

from datetime import datetime, timezone

import click
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import User
from app.models.enums import UserStatus
from app.models.user import normalize_email
from app.services.authentication import public_user_code


@click.command("create-customer-user")
@click.option("--email", required=True)
@click.option("--name", required=True)
@click.option("--verified", is_flag=True)
def create_customer_user_command(email: str, name: str, verified: bool) -> None:
    password = click.prompt(
        "Contraseña",
        hide_input=True,
        confirmation_prompt=True,
    )
    with db.session.begin():
        normalized = normalize_email(email)
        existing = db.session.scalar(
            select(User).where(User.email_normalized == normalized)
        )
        if existing is not None:
            raise click.ClickException("Ya existe una cuenta con ese correo.")
        user = User(
            public_code=public_user_code(),
            email=email.strip(),
            email_normalized=normalized,
            password_hash=generate_password_hash(password),
            full_name=" ".join(name.strip().split()),
            status=UserStatus.ACTIVE if verified else UserStatus.PENDING_VERIFICATION,
            email_verified_at=datetime.now(timezone.utc) if verified else None,
            is_active=True,
        )
        db.session.add(user)
    click.echo("Usuario creado.")


@click.command("verify-user-email")
@click.option("--email", required=True)
def verify_user_email_command(email: str) -> None:
    with db.session.begin():
        user = db.session.scalar(
            select(User).where(User.email_normalized == normalize_email(email))
        )
        if user is None:
            raise click.ClickException("Usuario no encontrado.")
        user.email_verified_at = datetime.now(timezone.utc)
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
    click.echo("Correo verificado.")


@click.command("list-unverified-users")
@click.option("--limit", default=50, type=click.IntRange(1, 500))
def list_unverified_users_command(limit: int) -> None:
    users = db.session.scalars(
        select(User)
        .where(User.email_verified_at.is_(None))
        .order_by(User.created_at.desc())
        .limit(limit)
    ).all()
    for user in users:
        click.echo(f"{user.email} | {user.full_name} | {user.status.value}")
