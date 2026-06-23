from __future__ import annotations

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import User
from app.services.fulfillment import (
    FulfillmentServiceError,
    create_packages_for_order,
    pack_order_package,
    stage_order_package_for_pickup,
)


@click.command("create-demo-packages")
@click.option(
    "--order-number",
    required=True,
    help="Número del pedido recogido que será empaquetado.",
)
@with_appcontext
def create_demo_packages(
    order_number: str,
) -> None:
    """Crea un paquete independiente por artículo recogido."""

    session = db.session()

    try:
        with session.begin():
            result = create_packages_for_order(
                session=session,
                order_number=order_number,
            )

    except FulfillmentServiceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Paquetes procesados correctamente.")
    click.echo(f"Pedido: {result.order_number}")
    click.echo(f"Total de paquetes: {len(result.packages)}")
    click.echo(f"Paquetes nuevos: {result.created_count}")

    for package in result.packages:
        click.echo(
            f"{package.package_code} | "
            f"barcode: {package.barcode} | "
            f"{package.product_name} | "
            f"cantidad: {package.quantity} | "
            f"estado: {package.status.value} | "
            f"repetida: "
            f"{'sí' if package.replayed else 'no'}"
        )


@click.command("pack-demo-package")
@click.option(
    "--package-code",
    required=True,
    help="Código del paquete que será empacado.",
)
@click.option(
    "--notes",
    default=None,
    help="Notas opcionales del proceso de empaque.",
)
@with_appcontext
def pack_demo_package(
    package_code: str,
    notes: str | None,
) -> None:
    """Marca un paquete como empacado."""

    session = db.session()

    try:
        with session.begin():
            actor = session.scalar(
                select(User).where(
                    User.email == "admin@ecuvel.local"
                )
            )

            if actor is None:
                raise click.ClickException(
                    "No existe el usuario de demostración."
                )

            result = pack_order_package(
                session=session,
                package_code=package_code,
                actor_user_id=actor.id,
                notes=notes,
            )

    except FulfillmentServiceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Paquete empacado correctamente.")
    click.echo(f"Código: {result.package_code}")
    click.echo(f"Producto: {result.product_name}")
    click.echo(f"Cantidad: {result.quantity}")
    click.echo(f"Estado: {result.status.value}")
    click.echo(f"Empacado: {result.packed_at.isoformat()}")
    click.echo(
        "Operación repetida: "
        f"{'sí' if result.replayed else 'no'}"
    )


@click.command("stage-demo-package")
@click.option(
    "--package-code",
    required=True,
    help="Código del paquete que será preparado para retiro.",
)
@click.option(
    "--location-code",
    default="PICKUP-01",
    show_default=True,
    help="Código de la ubicación de retiro.",
)
@click.option(
    "--notes",
    default=None,
    help="Notas opcionales del traslado al área de retiro.",
)
@with_appcontext
def stage_demo_package(
    package_code: str,
    location_code: str,
    notes: str | None,
) -> None:
    """Prepara un paquete empacado para su retiro."""

    session = db.session()

    try:
        with session.begin():
            actor = session.scalar(
                select(User).where(
                    User.email == "admin@ecuvel.local"
                )
            )

            if actor is None:
                raise click.ClickException(
                    "No existe el usuario de demostración."
                )

            result = stage_order_package_for_pickup(
                session=session,
                package_code=package_code,
                pickup_location_code=location_code,
                actor_user_id=actor.id,
                notes=notes,
            )

    except FulfillmentServiceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Paquete preparado para retiro.")
    click.echo(f"Código: {result.package_code}")
    click.echo(f"Producto: {result.product_name}")
    click.echo(f"Cantidad: {result.quantity}")
    click.echo(f"Estado: {result.status.value}")
    click.echo(f"Ubicación: {result.pickup_location_code}")
    click.echo(f"Preparado: {result.ready_at.isoformat()}")
    click.echo(
        "Operación repetida: "
        f"{'sí' if result.replayed else 'no'}"
    )
