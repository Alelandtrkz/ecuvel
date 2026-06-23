from __future__ import annotations

import uuid

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import (
    SellerOffer,
    User,
    WarehouseLocation,
)
from app.services.inventory import receive_inventory


@click.command("receive-demo-stock")
@click.option(
    "--quantity",
    type=click.IntRange(min=1),
    default=5,
    show_default=True,
    help="Cantidad de unidades que serán recibidas.",
)
@click.option(
    "--idempotency-key",
    default="demo-receive-001",
    show_default=True,
    help="Identificador único de la operación.",
)
@with_appcontext
def receive_demo_stock(
    quantity: int,
    idempotency_key: str,
) -> None:
    """Recibe inventario de prueba en la ubicación REC-01."""

    session = db.session()

    with session.begin():
        offer = session.scalar(
            select(SellerOffer).where(
                SellerOffer.seller_sku == "HIK-DEMO-001"
            )
        )

        location = session.scalar(
            select(WarehouseLocation).where(
                WarehouseLocation.code == "REC-01"
            )
        )

        actor = session.scalar(
            select(User).where(
                User.email == "admin@ecuvel.local"
            )
        )

        if offer is None:
            raise click.ClickException(
                "No existe la oferta HIK-DEMO-001. "
                "Ejecuta primero seed-demo."
            )

        if location is None:
            raise click.ClickException(
                "No existe la ubicación REC-01. "
                "Ejecuta primero seed-demo."
            )

        if actor is None:
            raise click.ClickException(
                "No existe el usuario de demostración."
            )

        reference_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ecuvel:receipt:{idempotency_key}",
        )

        result = receive_inventory(
            session=session,
            offer_id=offer.id,
            location_id=location.id,
            quantity=quantity,
            reference_type="DEMO_RECEIPT",
            reference_id=reference_id,
            idempotency_key=idempotency_key,
            actor_user_id=actor.id,
            notes="Recepción de mercancía para pruebas.",
        )

    click.echo("Recepción procesada correctamente.")
    click.echo(f"Movimiento: {result.movement_id}")
    click.echo(f"Existencia física: {result.on_hand_quantity}")
    click.echo(f"Reservado: {result.reserved_quantity}")
    click.echo(f"Bloqueado: {result.blocked_quantity}")
    click.echo(f"Disponible: {result.available_quantity}")
    click.echo(
        "Operación repetida: "
        f"{'sí' if result.replayed else 'no'}"
    )