from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    MarketplaceCommissionRule,
    Store,
    StoreInventoryLocation,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import LocationType, SellerCommissionType
from app.services.inventory import SELLABLE_LOCATION_TYPES


LOW_PRICE_THRESHOLD = Decimal("3.00")
LOW_PRICE_FIXED_FEE = Decimal("0.25")
MINIMUM_SELLER_PRICE = LOW_PRICE_FIXED_FEE
COMMISSION_CURRENCY = "USD"
MONEY_QUANTUM = Decimal("0.01")
COMMISSION_SNAPSHOT_VERSION = 1
MINIMUM_PRICE_MESSAGE = (
    "El precio debe ser mayor a USD 0.25 porque los productos menores a "
    "USD 3.00 tienen una tarifa fija de servicio de USD 0.25."
)


class MarketplacePolicyError(Exception):
    """Base error for marketplace publication policies."""


class InvalidSellerPriceError(MarketplacePolicyError):
    pass


class CommissionRuleMissingError(MarketplacePolicyError):
    pass


class CommissionSnapshotError(MarketplacePolicyError):
    pass


class StoreInventoryLocationMissingError(MarketplacePolicyError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedSellerCommission:
    mode: SellerCommissionType
    currency: str
    price: Decimal
    category_id: uuid.UUID
    category_path: tuple[str, ...]
    category_labels: tuple[str, ...]
    rate_percent: Decimal | None
    fixed_amount: Decimal | None
    commission_amount: Decimal
    seller_net_amount: Decimal
    rule_id: uuid.UUID | None
    source: str

    @property
    def rate(self) -> Decimal:
        """Backward-compatible percentage accessor."""
        return self.rate_percent or Decimal("0.00")

    @property
    def scope(self) -> str:
        """Backward-compatible policy source accessor."""
        return self.source

    def as_snapshot(self, *, captured_at: str) -> dict[str, Any]:
        return {
            "version": COMMISSION_SNAPSHOT_VERSION,
            "captured_at": captured_at,
            "category_id": str(self.category_id),
            "category_path": list(self.category_path),
            "category_labels": list(self.category_labels),
            "price": _money_text(self.price),
            "currency": self.currency,
            "mode": self.mode.value,
            "rate_percent": (
                _decimal_text(self.rate_percent)
                if self.rate_percent is not None else None
            ),
            "fixed_amount": (
                _money_text(self.fixed_amount)
                if self.fixed_amount is not None else None
            ),
            "commission_amount": _money_text(self.commission_amount),
            "seller_net_amount": _money_text(self.seller_net_amount),
            "rule_id": str(self.rule_id) if self.rule_id else None,
            "source": self.source,
        }


# Compatibility for internal imports written before the richer value object.
ResolvedCommission = ResolvedSellerCommission


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return format(_money(value), ".2f")


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def _decimal(value: Any, *, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidSellerPriceError(f"{label} no es válido.") from exc
    if not parsed.is_finite():
        raise InvalidSellerPriceError(f"{label} no es válido.")
    return parsed


def _category_lineage(session: Session, category_id: uuid.UUID) -> tuple[Category, ...]:
    lineage: list[Category] = []
    current_id: uuid.UUID | None = category_id
    seen: set[uuid.UUID] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        category = session.get(Category, current_id)
        if category is None:
            break
        lineage.append(category)
        current_id = category.parent_id
    return tuple(lineage)


def resolve_marketplace_commission(
    session: Session,
    *,
    category_id: uuid.UUID,
    price: Decimal | str,
    store_id: uuid.UUID | None = None,
) -> ResolvedSellerCommission:
    """Resolve one sellable presentation from price and category only.

    ``store_id`` remains accepted temporarily so old callers do not break, but
    it is deliberately ignored: ECUVEL has no negotiated per-store rates.
    """

    del store_id
    normalized_price = _money(_decimal(price, label="El precio"))
    if normalized_price <= MINIMUM_SELLER_PRICE:
        raise InvalidSellerPriceError(MINIMUM_PRICE_MESSAGE)

    lineage = _category_lineage(session, category_id)
    if not lineage or lineage[0].id != category_id:
        raise CommissionRuleMissingError("La categoría del producto no existe.")
    category_path = tuple(category.code for category in reversed(lineage))
    category_labels = tuple(category.name for category in reversed(lineage))

    if normalized_price < LOW_PRICE_THRESHOLD:
        commission_amount = LOW_PRICE_FIXED_FEE
        return ResolvedSellerCommission(
            mode=SellerCommissionType.FIXED,
            currency=COMMISSION_CURRENCY,
            price=normalized_price,
            category_id=category_id,
            category_path=category_path,
            category_labels=category_labels,
            rate_percent=None,
            fixed_amount=LOW_PRICE_FIXED_FEE,
            commission_amount=commission_amount,
            seller_net_amount=_money(normalized_price - commission_amount),
            rule_id=None,
            source="LOW_PRICE_FIXED",
        )

    rules = session.scalars(
        select(MarketplaceCommissionRule).where(
            MarketplaceCommissionRule.is_active.is_(True),
            MarketplaceCommissionRule.store_id.is_(None),
        )
    ).all()
    by_category = {rule.category_id: rule for rule in rules}
    selected_rule = next(
        (by_category.get(category.id) for category in lineage if by_category.get(category.id)),
        None,
    )
    source = "CATEGORY"
    if selected_rule is None:
        selected_rule = by_category.get(None)
        source = "GLOBAL"
    if selected_rule is None:
        raise CommissionRuleMissingError(
            "No existe una comisión configurada para esta categoría."
        )

    rate = Decimal(selected_rule.commission_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    commission_amount = _money(normalized_price * rate / Decimal("100"))
    return ResolvedSellerCommission(
        mode=SellerCommissionType.PERCENTAGE,
        currency=COMMISSION_CURRENCY,
        price=normalized_price,
        category_id=category_id,
        category_path=category_path,
        category_labels=category_labels,
        rate_percent=rate,
        fixed_amount=None,
        commission_amount=commission_amount,
        seller_net_amount=_money(normalized_price - commission_amount),
        rule_id=selected_rule.id,
        source=source,
    )


def commission_from_snapshot(
    snapshot: Mapping[str, Any] | None,
    *,
    expected_price: Decimal | str,
    expected_category_id: uuid.UUID,
) -> ResolvedSellerCommission:
    """Validate a frozen submission snapshot without consulting live rules."""

    missing_message = (
        "El producto no tiene una comisión fijada al momento del envío. "
        "Devuélvelo al vendedor para volver a enviarlo."
    )
    if not isinstance(snapshot, Mapping) or snapshot.get("version") != 1:
        raise CommissionSnapshotError(missing_message)
    try:
        mode = SellerCommissionType(str(snapshot.get("mode")))
        category_id = uuid.UUID(str(snapshot.get("category_id")))
        price = _money(_decimal(snapshot.get("price"), label="El precio fijado"))
        commission_amount = _money(
            _decimal(snapshot.get("commission_amount"), label="La comisión fijada")
        )
        seller_net = _money(
            _decimal(snapshot.get("seller_net_amount"), label="El ingreso fijado")
        )
        rate = (
            _decimal(snapshot.get("rate_percent"), label="El porcentaje fijado")
            if snapshot.get("rate_percent") is not None else None
        )
        fixed = (
            _money(_decimal(snapshot.get("fixed_amount"), label="La tarifa fijada"))
            if snapshot.get("fixed_amount") is not None else None
        )
        rule_id = (
            uuid.UUID(str(snapshot["rule_id"])) if snapshot.get("rule_id") else None
        )
    except (ValueError, InvalidSellerPriceError) as exc:
        raise CommissionSnapshotError("El snapshot de comisión no es válido.") from exc

    expected = _money(_decimal(expected_price, label="El precio"))
    if category_id != expected_category_id or price != expected:
        raise CommissionSnapshotError(
            "El snapshot de comisión no corresponde al precio o categoría enviados."
        )
    if price <= MINIMUM_SELLER_PRICE:
        raise CommissionSnapshotError("Los importes del snapshot de comisión no son válidos.")
    if snapshot.get("currency") != COMMISSION_CURRENCY:
        raise CommissionSnapshotError("La moneda del snapshot de comisión no es válida.")
    if mode == SellerCommissionType.FIXED:
        valid = rate is None and fixed == LOW_PRICE_FIXED_FEE and price < LOW_PRICE_THRESHOLD
        calculated = fixed
    else:
        valid = (
            price >= LOW_PRICE_THRESHOLD
            and fixed is None
            and rate is not None
            and Decimal("0") <= rate <= Decimal("100")
        )
        calculated = _money(price * rate / Decimal("100")) if rate is not None else None
    if (
        not valid
        or calculated != commission_amount
        or seller_net != _money(price - commission_amount)
        or seller_net < Decimal("0")
    ):
        raise CommissionSnapshotError("Los importes del snapshot de comisión no son válidos.")

    return ResolvedSellerCommission(
        mode=mode,
        currency=COMMISSION_CURRENCY,
        price=price,
        category_id=category_id,
        category_path=tuple(str(item) for item in snapshot.get("category_path") or ()),
        category_labels=tuple(str(item) for item in snapshot.get("category_labels") or ()),
        rate_percent=rate,
        fixed_amount=fixed,
        commission_amount=commission_amount,
        seller_net_amount=seller_net,
        rule_id=rule_id,
        source=str(snapshot.get("source") or "SNAPSHOT"),
    )


def ensure_store_inventory_location(
    session: Session,
    *,
    store: Store,
) -> WarehouseLocation:
    """Provision exactly one default seller stock location, idempotently."""

    locked_store = session.scalar(
        select(Store).where(Store.id == store.id).with_for_update()
    )
    if locked_store is None:
        raise StoreInventoryLocationMissingError("La tienda no existe.")

    existing = session.scalar(
        select(WarehouseLocation)
        .join(StoreInventoryLocation, StoreInventoryLocation.location_id == WarehouseLocation.id)
        .join(Warehouse, Warehouse.id == WarehouseLocation.warehouse_id)
        .where(
            StoreInventoryLocation.store_id == store.id,
            StoreInventoryLocation.is_default.is_(True),
            Warehouse.seller_store_id == store.id,
        )
        .with_for_update()
    )
    if existing is not None:
        existing.is_active = True
        existing.warehouse.is_active = True
        mapping = session.scalar(
            select(StoreInventoryLocation).where(
                StoreInventoryLocation.store_id == store.id,
                StoreInventoryLocation.location_id == existing.id,
            )
        )
        if mapping:
            mapping.is_active = True
            mapping.is_default = True
        return existing

    technical_code = re.sub(r"[^A-Z0-9-]", "-", locked_store.public_code.upper())
    canonical_warehouse_code = f"SELLER-{technical_code}"[:30]
    warehouse = session.scalar(
        select(Warehouse).where(
            Warehouse.seller_store_id == store.id,
            Warehouse.code == canonical_warehouse_code,
        ).with_for_update()
    )
    if warehouse is None:
        warehouse = Warehouse(
            code=canonical_warehouse_code,
            name=f"Bodega vendedor · {locked_store.name}"[:150],
            address_line="Inventario comercial del vendedor",
            city="No aplica",
            country_code="EC",
            is_active=True,
            seller_store_id=locked_store.id,
        )
        session.add(warehouse)
        session.flush()
    else:
        warehouse.is_active = True

    location = session.scalar(
        select(WarehouseLocation).where(
            WarehouseLocation.warehouse_id == warehouse.id,
            WarehouseLocation.code == "STOCK",
        ).with_for_update()
    )
    if location is None:
        location = WarehouseLocation(
            warehouse_id=warehouse.id,
            code="STOCK",
            barcode=f"SELLER-{locked_store.id}-STOCK",
            name="Stock comercial",
            location_type=LocationType.STORAGE,
            capacity_units=None,
            allows_mixed_offers=True,
            is_active=True,
        )
        session.add(location)
        session.flush()
    else:
        location.is_active = True

    mapping = session.scalar(
        select(StoreInventoryLocation).where(
            StoreInventoryLocation.store_id == store.id,
            StoreInventoryLocation.location_id == location.id,
        ).with_for_update()
    )
    if mapping is None:
        mapping = StoreInventoryLocation(
            store_id=store.id,
            location_id=location.id,
            is_default=True,
            is_active=True,
        )
        session.add(mapping)
    else:
        mapping.location_id = location.id
        mapping.is_default = True
        mapping.is_active = True
    session.flush()
    return location


def resolve_default_store_inventory_location(
    session: Session,
    *,
    store_id: uuid.UUID,
) -> WarehouseLocation:
    """Return the explicit seller location; never use an ECUVEL operating point."""

    location = session.scalar(
        select(WarehouseLocation)
        .join(StoreInventoryLocation, StoreInventoryLocation.location_id == WarehouseLocation.id)
        .join(Warehouse, Warehouse.id == WarehouseLocation.warehouse_id)
        .where(
            StoreInventoryLocation.store_id == store_id,
            StoreInventoryLocation.is_default.is_(True),
            StoreInventoryLocation.is_active.is_(True),
            WarehouseLocation.is_active.is_(True),
            WarehouseLocation.location_type.in_(SELLABLE_LOCATION_TYPES),
            Warehouse.is_active.is_(True),
            Warehouse.seller_store_id == store_id,
        )
        .with_for_update()
    )
    if location is None:
        raise StoreInventoryLocationMissingError(
            "No se pudo publicar el producto porque la tienda no tiene una "
            "ubicación de inventario configurada."
        )
    return location
