from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Order,
    OrderItem,
    SellerOffer,
    SellerOrder,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.services.inventory import (
    InventoryServiceError,
    putaway_inventory,
    receive_inventory,
    reserve_inventory,
)


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

@click.command("putaway-demo-stock")
@click.option(
    "--quantity",
    type=click.IntRange(min=1),
    default=4,
    show_default=True,
    help="Cantidad que se moverá desde recepción.",
)
@click.option(
    "--idempotency-key",
    default="demo-putaway-001",
    show_default=True,
    help="Identificador único del putaway.",
)
@with_appcontext
def putaway_demo_stock(
    quantity: int,
    idempotency_key: str,
) -> None:
    """Mueve inventario desde REC-01 hacia almacenamiento."""

    session = db.session()

    try:
        with session.begin():
            offer = session.scalar(
                select(SellerOffer).where(
                    SellerOffer.seller_sku == "HIK-DEMO-001"
                )
            )

            source_location = session.scalar(
                select(WarehouseLocation).where(
                    WarehouseLocation.code == "REC-01"
                )
            )

            destination_location = session.scalar(
                select(WarehouseLocation).where(
                    WarehouseLocation.code
                    == "A01-R01-N01-B01"
                )
            )

            actor = session.scalar(
                select(User).where(
                    User.email == "admin@ecuvel.local"
                )
            )

            if offer is None:
                raise click.ClickException(
                    "No existe la oferta HIK-DEMO-001."
                )

            if source_location is None:
                raise click.ClickException(
                    "No existe la ubicación REC-01."
                )

            if destination_location is None:
                raise click.ClickException(
                    "No existe la ubicación de almacenamiento."
                )

            if actor is None:
                raise click.ClickException(
                    "No existe el usuario de demostración."
                )

            reference_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"ecuvel:putaway:{idempotency_key}",
            )

            result = putaway_inventory(
                session=session,
                offer_id=offer.id,
                source_location_id=source_location.id,
                destination_location_id=(
                    destination_location.id
                ),
                quantity=quantity,
                reference_type="DEMO_PUTAWAY",
                reference_id=reference_id,
                idempotency_key=idempotency_key,
                actor_user_id=actor.id,
                notes=(
                    "Traslado de recepción hacia "
                    "almacenamiento para pruebas."
                ),
            )

    except InventoryServiceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Putaway procesado correctamente.")
    click.echo(f"Cantidad trasladada: {result.quantity}")
    click.echo(
        "Origen - existencia: "
        f"{result.source_on_hand_quantity}"
    )
    click.echo(
        "Origen - disponible: "
        f"{result.source_available_quantity}"
    )
    click.echo(
        "Destino - existencia: "
        f"{result.destination_on_hand_quantity}"
    )
    click.echo(
        "Destino - disponible: "
        f"{result.destination_available_quantity}"
    )
    click.echo(
        "Operación repetida: "
        f"{'sí' if result.replayed else 'no'}"
    )

@click.command("reserve-demo-stock")
@click.option(
    "--quantity",
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help="Cantidad solicitada por el cliente.",
)
@click.option(
    "--idempotency-key",
    default="reserve-demo-001",
    show_default=True,
    help="Identificador único del checkout.",
)
@with_appcontext
def reserve_demo_stock(
    quantity: int,
    idempotency_key: str,
) -> None:
    """Crea un pedido de prueba y reserva inventario."""

    session = db.session()

    try:
        with session.begin():
            offer = session.scalar(
                select(SellerOffer).where(
                    SellerOffer.seller_sku
                    == "HIK-DEMO-001"
                )
            )

            buyer = session.scalar(
                select(User).where(
                    User.email
                    == "admin@ecuvel.local"
                )
            )

            warehouse = session.scalar(
                select(Warehouse).where(
                    Warehouse.code
                    == "WH-ECUVEL-01"
                )
            )

            if offer is None:
                raise click.ClickException(
                    "No existe la oferta HIK-DEMO-001."
                )

            if buyer is None:
                raise click.ClickException(
                    "No existe el usuario de demostración."
                )

            if warehouse is None:
                raise click.ClickException(
                    "No existe el almacén de demostración."
                )

            token = hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest()[:10].upper()

            order_number = f"ECV-DEMO-{token}"
            seller_order_number = (
                f"{order_number}-S01"
            )

            order = session.scalar(
                select(Order).where(
                    Order.order_number == order_number
                )
            )

            if order is None:
                subtotal = (
                    offer.price * quantity
                ).quantize(Decimal("0.01"))

                commission_total = (
                    subtotal
                    * offer.commission_rate
                    / Decimal("100")
                ).quantize(Decimal("0.01"))

                seller_net_total = (
                    subtotal - commission_total
                ).quantize(Decimal("0.01"))

                order = Order(
                    order_number=order_number,
                    buyer_id=buyer.id,
                    currency="USD",
                    subtotal=subtotal,
                    discount_total=Decimal("0.00"),
                    shipping_total=Decimal("0.00"),
                    tax_total=Decimal("0.00"),
                    grand_total=subtotal,
                )

                session.add(order)
                session.flush()

                seller_order = SellerOrder(
                    seller_order_number=(
                        seller_order_number
                    ),
                    order_id=order.id,
                    store_id=offer.store_id,
                    subtotal=subtotal,
                    discount_total=Decimal("0.00"),
                    commission_total=(
                        commission_total
                    ),
                    seller_net_total=seller_net_total,
                )

                session.add(seller_order)
                session.flush()

                order_item = OrderItem(
                    seller_order_id=seller_order.id,
                    offer_id=offer.id,
                    quantity=quantity,
                    unit_price=offer.price,
                    discount_amount=Decimal("0.00"),
                    tax_amount=Decimal("0.00"),
                    line_total=subtotal,
                    product_name_snapshot=(
                        offer.variant.product.title
                    ),
                    seller_name_snapshot=(
                        offer.store.name
                    ),
                    seller_sku_snapshot=(
                        offer.seller_sku
                    ),
                    image_url_snapshot=None,
                    variant_snapshot=dict(
                        offer.variant.attributes or {}
                    ),
                )

                session.add(order_item)
                session.flush()

            else:
                order_item = session.scalar(
                    select(OrderItem)
                    .join(
                        SellerOrder,
                        SellerOrder.id
                        == OrderItem.seller_order_id,
                    )
                    .where(
                        SellerOrder.order_id == order.id,
                        OrderItem.offer_id == offer.id,
                    )
                )

                if order_item is None:
                    raise click.ClickException(
                        "El pedido existente no tiene "
                        "un artículo válido."
                    )

                if order_item.quantity != quantity:
                    raise click.ClickException(
                        "La misma clave ya fue usada "
                        "con otra cantidad."
                    )

            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(minutes=30)
            )

            result = reserve_inventory(
                session=session,
                order_item_id=order_item.id,
                warehouse_id=warehouse.id,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
                actor_user_id=buyer.id,
                notes=(
                    "Reserva de inventario para "
                    "checkout de demostración."
                ),
            )

    except InventoryServiceError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Reserva procesada correctamente.")
    click.echo(f"Pedido: {order.order_number}")
    click.echo(
        f"Cantidad reservada: "
        f"{result.total_reserved}"
    )
    click.echo(
        f"Expira: {result.expires_at.isoformat()}"
    )

    for allocation in result.allocations:
        click.echo(
            "Asignación: "
            f"{allocation.location_code} → "
            f"{allocation.quantity} unidades"
        )

    click.echo(
        "Operación repetida: "
        f"{'sí' if result.replayed else 'no'}"
    )
