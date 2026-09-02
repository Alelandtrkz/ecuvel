from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.models import (
    Category,
    MarketplaceCommissionRule,
    ProductDraft,
    ProductDraftFile,
    Store,
    StoreInventoryLocation,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    ProductDraftFileKind,
    ProductDraftFileStatus,
    ProductDraftStatus,
    StoreStatus,
    UserStatus,
)


def token() -> str:
    return uuid.uuid4().hex[:12]


def create_user(
    session: Session,
    *,
    staff: bool = False,
    active: bool = True,
    name: str = "Usuario de prueba",
) -> User:
    suffix = token()
    user = User(
        public_code=f"USR-{suffix}",
        email=f"{suffix}@test.local",
        email_normalized=f"{suffix}@test.local",
        password_hash="test",
        full_name=name,
        status=UserStatus.ACTIVE if active else UserStatus.BLOCKED,
        is_active=active,
        is_ecuvel_staff=staff,
    )
    session.add(user)
    session.flush()
    return user


def create_store(session: Session, *, name: str = "Tienda publicación") -> Store:
    suffix = token()
    store = Store(
        public_code=f"STR-{suffix}",
        name=name,
        slug=f"tienda-{suffix}",
        status=StoreStatus.ACTIVE,
        is_verified=True,
    )
    session.add(store)
    session.flush()
    return store


def create_phone_categories(session: Session) -> tuple[Category, Category]:
    suffix = token()
    parent = Category(
        code=f"ELECTRONICS-{suffix}",
        name="Electrónicos",
        slug=f"electronicos-{suffix}",
        is_active=True,
        sort_order=1,
    )
    child = Category(
        code="ELECTRONICS_PHONES",
        name="Teléfonos y Accesorios",
        slug=f"telefonos-{suffix}",
        parent=parent,
        is_active=True,
        sort_order=1,
    )
    session.add_all((parent, child))
    session.flush()
    return parent, child


def create_seller_location(
    session: Session,
    store: Store,
) -> tuple[Warehouse, WarehouseLocation, StoreInventoryLocation]:
    suffix = token()
    warehouse = Warehouse(
        code=f"SELL-{suffix}",
        name=f"Bodega de {store.name}",
        address_line="Dirección comercial",
        city="Guayaquil",
        country_code="EC",
        is_active=True,
        seller_store_id=store.id,
    )
    session.add(warehouse)
    session.flush()
    location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"STO-{suffix}",
        barcode=f"LOC-{suffix}",
        name="Stock vendible",
        location_type=LocationType.STORAGE,
        capacity_units=10000,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(location)
    session.flush()
    mapping = StoreInventoryLocation(
        store_id=store.id,
        location_id=location.id,
        is_default=True,
        is_active=True,
    )
    session.add(mapping)
    session.flush()
    return warehouse, location, mapping


def create_network_warehouse(session: Session, *, name: str = "Punto A") -> Warehouse:
    suffix = token()
    warehouse = Warehouse(
        code=f"ECU-{suffix}",
        name=name,
        address_line="Red ECUVEL",
        city="Guayaquil",
        country_code="EC",
        is_active=True,
        seller_store_id=None,
    )
    session.add(warehouse)
    session.flush()
    return warehouse


def create_commission_rule(
    session: Session,
    *,
    rate: str,
    category: Category | None = None,
    store: Store | None = None,
) -> MarketplaceCommissionRule:
    rule = MarketplaceCommissionRule(
        category_id=category.id if category else None,
        store_id=store.id if store else None,
        commission_rate=Decimal(rate),
        is_active=True,
    )
    session.add(rule)
    session.flush()
    return rule


def _png_bytes(path: Path, color: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, format="PNG")
    return path.read_bytes()


def add_draft_images(
    session: Session,
    draft: ProductDraft,
    root: Path,
    bindings: list[tuple[str | None, str | None]],
) -> tuple[ProductDraftFile, ...]:
    colors = ("#085df8", "#005146", "#f59e0b", "#e11d48", "#7c3aed")
    rows: list[ProductDraftFile] = []
    group_positions: dict[tuple[str | None, str | None], int] = {}
    for index, binding in enumerate(bindings):
        position = group_positions.get(binding, 0)
        group_positions[binding] = position + 1
        storage_key = f"drafts/{draft.id}/image-{index}.png"
        content = _png_bytes(root / storage_key, colors[index % len(colors)])
        row = ProductDraftFile(
            draft_id=draft.id,
            kind=ProductDraftFileKind.IMAGE,
            status=ProductDraftFileStatus.ACTIVE,
            storage_key=storage_key,
            original_filename=f"image-{index}.png",
            media_type="image/png",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            position=position,
            is_cover=position == 0,
            width=8,
            height=8,
            variant_axis_key=binding[0],
            variant_value_key=binding[1],
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return tuple(rows)


def create_complete_simple_draft(
    session: Session,
    *,
    seller: User,
    store: Store,
    category: Category,
    subcategory: Category,
    media_root: Path,
    sku: str | None = None,
    status: ProductDraftStatus = ProductDraftStatus.SUBMITTED,
) -> ProductDraft:
    suffix = token()
    code = sku or f"CRI-{suffix}"
    draft = ProductDraft(
        store_id=store.id,
        created_by_user_id=seller.id,
        category_id=category.id,
        subcategory_id=subcategory.id,
        template_key="electronics_phones",
        title="Smartphone de prueba",
        brand="Marca real",
        model_number="Modelo 1",
        seller_sku=code,
        barcode=code,
        condition="NEW",
        country_origin="Ecuador",
        description="Descripción completa y válida para una publicación de prueba.",
        attributes={"tipo_producto": "Smartphone", "material": "Aluminio"},
        variant_configuration={"version": 4, "enabled": False, "mode": "single"},
        variants=[],
        pricing_data={"price": "45.00", "compare_at_price": "55.00"},
        inventory_data={
            "stock_quantity": 20,
            "preparation_time_days": 1,
        },
        dimensions_data={"product_weight_kg": "0.5"},
        warranty_data={"type": "Garantía de tienda", "duration": "12", "unit": "meses"},
        status=status,
        completion_percentage=100,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    session.flush()
    add_draft_images(session, draft, media_root, [(None, None)] * 3)
    return draft


def create_complete_family_draft(
    session: Session,
    *,
    seller: User,
    store: Store,
    category: Category,
    subcategory: Category,
    media_root: Path,
) -> ProductDraft:
    suffix = token()
    default_id = str(uuid.uuid4())
    rows = [
        {
            "variant_id": default_id,
            "combination_key": "color_principal=negro|almacenamiento_gb=128",
            "name": "Negro / 128 GB",
            "sku": f"CRI-{suffix}-V01",
            "attributes": {"color_principal": "Negro", "almacenamiento_gb": "128"},
            "options": {"color_principal": "negro", "almacenamiento_gb": "128"},
            "price": "499.00",
            "compare_at_price": "549.00",
            "stock": 7,
            "enabled": True,
        },
        {
            "variant_id": str(uuid.uuid4()),
            "combination_key": "color_principal=azul|almacenamiento_gb=256",
            "name": "Azul / 256 GB",
            "sku": f"CRI-{suffix}-V02",
            "attributes": {"color_principal": "Azul", "almacenamiento_gb": "256"},
            "options": {"color_principal": "azul", "almacenamiento_gb": "256"},
            "price": "599.00",
            "compare_at_price": "649.00",
            "stock": 0,
            "enabled": True,
        },
        {
            "variant_id": str(uuid.uuid4()),
            "combination_key": "color_principal=blanco|almacenamiento_gb=512",
            "name": "Blanco / 512 GB",
            "sku": f"CRI-{suffix}-V03",
            "attributes": {"color_principal": "Blanco", "almacenamiento_gb": "512"},
            "options": {"color_principal": "blanco", "almacenamiento_gb": "512"},
            "price": "699.00",
            "stock": 5,
            "enabled": False,
        },
    ]
    configuration = {
        "version": 4,
        "enabled": True,
        "mode": "family",
        "visual_axis_key": "color_principal",
        "default_variant_id": default_id,
        "axes": [
            {
                "key": "color_principal",
                "source_field": "color_principal",
                "label": "Color",
                "values": [
                    {"key": "negro", "label": "Negro"},
                    {"key": "azul", "label": "Azul"},
                ],
            },
            {
                "key": "almacenamiento_gb",
                "source_field": "almacenamiento_gb",
                "label": "Almacenamiento",
                "unit": "GB",
                "values": [
                    {"key": "128", "label": "128"},
                    {"key": "256", "label": "256"},
                ],
            },
        ],
    }
    draft = ProductDraft(
        store_id=store.id,
        created_by_user_id=seller.id,
        category_id=category.id,
        subcategory_id=subcategory.id,
        template_key="electronics_phones",
        title="Teléfono familiar",
        brand="Marca real",
        model_number="Family 1",
        seller_sku=f"CRI-{suffix}",
        barcode=f"CRI-{suffix}",
        condition="NEW",
        description="Descripción completa de una familia real con variantes.",
        attributes={"tipo_producto": "Smartphone", "material": "Aluminio"},
        variant_configuration=configuration,
        variants=rows,
        pricing_data={},
        inventory_data={"preparation_time_days": 2},
        dimensions_data={"product_weight_kg": "0.5"},
        status=ProductDraftStatus.SUBMITTED,
        completion_percentage=100,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    session.flush()
    add_draft_images(
        session,
        draft,
        media_root,
        [
            ("color_principal", "negro"),
            ("color_principal", "negro"),
            ("color_principal", "azul"),
        ],
    )
    return draft
