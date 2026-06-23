from decimal import Decimal

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Category,
    InventoryBalance,
    Product,
    ProductVariant,
    SellerOffer,
    Store,
    StoreMember,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    OfferStatus,
    StoreMemberRole,
    StoreStatus,
    UserStatus,
)


@click.command("seed-demo")
@with_appcontext
def seed_demo() -> None:
    """Crea datos básicos para desarrollo local."""

    try:
        user = db.session.scalar(
            select(User).where(User.email == "admin@ecuvel.local")
        )

        if user is None:
            user = User(
                public_code="USR-DEMO-001",
                email="admin@ecuvel.local",
                password_hash="NO_USAR_EN_PRODUCCION",
                full_name="Administrador Ecuvel",
                phone="0999999999",
                status=UserStatus.ACTIVE,
            )
            db.session.add(user)
            db.session.flush()

        store = db.session.scalar(
            select(Store).where(Store.public_code == "STR-DEMO-001")
        )

        if store is None:
            store = Store(
                public_code="STR-DEMO-001",
                name="Tienda Demo Ecuvel",
                slug="tienda-demo-ecuvel",
                legal_name="Tienda Demo Ecuvel",
                tax_id="9999999999001",
                status=StoreStatus.ACTIVE,
                is_verified=True,
            )
            db.session.add(store)
            db.session.flush()

        membership = db.session.scalar(
            select(StoreMember).where(
                StoreMember.store_id == store.id,
                StoreMember.user_id == user.id,
            )
        )

        if membership is None:
            membership = StoreMember(
                store_id=store.id,
                user_id=user.id,
                role=StoreMemberRole.OWNER,
                is_active=True,
            )
            db.session.add(membership)

        category = db.session.scalar(
            select(Category).where(Category.code == "ELECTRONICS")
        )

        if category is None:
            category = Category(
                code="ELECTRONICS",
                name="Electrónicos",
                slug="electronicos",
                is_active=True,
                sort_order=1,
            )
            db.session.add(category)
            db.session.flush()

        product = db.session.scalar(
            select(Product).where(
                Product.slug == "camara-hikvision-demo"
            )
        )

        if product is None:
            product = Product(
                category_id=category.id,
                title="Cámara Hikvision Demo",
                slug="camara-hikvision-demo",
                brand="Hikvision",
                model_number="DS-DEMO-001",
                description="Producto de prueba para inventario.",
                is_active=True,
            )
            db.session.add(product)
            db.session.flush()

        variant = db.session.scalar(
            select(ProductVariant).where(
                ProductVariant.catalog_sku == "ECV-HIK-DEMO-001"
            )
        )

        if variant is None:
            variant = ProductVariant(
                product_id=product.id,
                catalog_sku="ECV-HIK-DEMO-001",
                title="Blanca, lente 2.8 mm",
                manufacturer_barcode="789000000001",
                attributes={
                    "color": "blanco",
                    "lente": "2.8 mm",
                    "resolucion": "4 MP",
                },
                weight_grams=300,
                length_mm=180,
                width_mm=80,
                height_mm=80,
                is_active=True,
            )
            db.session.add(variant)
            db.session.flush()

        offer = db.session.scalar(
            select(SellerOffer).where(
                SellerOffer.store_id == store.id,
                SellerOffer.seller_sku == "HIK-DEMO-001",
            )
        )

        if offer is None:
            offer = SellerOffer(
                store_id=store.id,
                variant_id=variant.id,
                seller_sku="HIK-DEMO-001",
                currency="USD",
                price=Decimal("45.00"),
                compare_at_price=Decimal("60.00"),
                commission_rate=Decimal("10.00"),
                status=OfferStatus.ACTIVE,
            )
            db.session.add(offer)
            db.session.flush()

        warehouse = db.session.scalar(
            select(Warehouse).where(
                Warehouse.code == "WH-ECUVEL-01"
            )
        )

        if warehouse is None:
            warehouse = Warehouse(
                code="WH-ECUVEL-01",
                name="Almacén principal Ecuvel",
                address_line="Av. 9 de Octubre y Miguel Gamboa",
                city="Quito",
                country_code="EC",
                is_active=True,
            )
            db.session.add(warehouse)
            db.session.flush()

        receiving_location = db.session.scalar(
            select(WarehouseLocation).where(
                WarehouseLocation.warehouse_id == warehouse.id,
                WarehouseLocation.code == "REC-01",
            )
        )

        if receiving_location is None:
            receiving_location = WarehouseLocation(
                warehouse_id=warehouse.id,
                code="REC-01",
                barcode="LOC-REC-01",
                name="Recepción principal",
                location_type=LocationType.RECEIVING,
                capacity_units=1000,
                allows_mixed_offers=True,
                is_active=True,
            )
            db.session.add(receiving_location)

        storage_location = db.session.scalar(
            select(WarehouseLocation).where(
                WarehouseLocation.warehouse_id == warehouse.id,
                WarehouseLocation.code == "A01-R01-N01-B01",
            )
        )

        if storage_location is None:
            storage_location = WarehouseLocation(
                warehouse_id=warehouse.id,
                code="A01-R01-N01-B01",
                barcode="LOC-A01-R01-N01-B01",
                name="Zona A, estante 1, nivel 1, contenedor 1",
                location_type=LocationType.STORAGE,
                capacity_units=100,
                allows_mixed_offers=True,
                is_active=True,
            )
            db.session.add(storage_location)
            db.session.flush()

        pickup_location = db.session.scalar(
            select(WarehouseLocation).where(
                WarehouseLocation.warehouse_id == warehouse.id,
                WarehouseLocation.code == "PICKUP-01",
            )
        )

        if pickup_location is None:
            pickup_location = WarehouseLocation(
                warehouse_id=warehouse.id,
                code="PICKUP-01",
                barcode="LOC-PICKUP-01",
                name="Área principal de retiro",
                location_type=LocationType.PICKUP_STAGING,
                capacity_units=100,
                allows_mixed_offers=True,
                is_active=True,
            )
            db.session.add(pickup_location)

        balance = db.session.scalar(
            select(InventoryBalance).where(
                InventoryBalance.offer_id == offer.id,
                InventoryBalance.location_id == storage_location.id,
            )
        )

        if balance is None:
            balance = InventoryBalance(
                offer_id=offer.id,
                location_id=storage_location.id,
                on_hand_quantity=10,
                reserved_quantity=0,
                blocked_quantity=0,
            )
            db.session.add(balance)

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    click.echo("Datos de demostración creados correctamente.")
    click.echo(f"Usuario: {user.email}")
    click.echo(f"Tienda: {store.name}")
    click.echo(f"Oferta: {offer.seller_sku}")
    click.echo(f"Almacén: {warehouse.code}")
    click.echo(
        "Inventario inicial: "
        f"{balance.on_hand_quantity} unidades"
    )
