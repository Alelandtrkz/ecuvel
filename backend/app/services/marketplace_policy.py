from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    MarketplaceCommissionRule,
    StoreInventoryLocation,
    Warehouse,
    WarehouseLocation,
)
from app.services.inventory import SELLABLE_LOCATION_TYPES


class MarketplacePolicyError(Exception):
    """Base error for marketplace publication policies."""


class CommissionRuleMissingError(MarketplacePolicyError):
    pass


class StoreInventoryLocationMissingError(MarketplacePolicyError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedCommission:
    rate: Decimal
    rule_id: uuid.UUID
    scope: str


def _category_lineage(session: Session, category_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    lineage: list[uuid.UUID] = []
    current_id: uuid.UUID | None = category_id
    seen: set[uuid.UUID] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        category = session.get(Category, current_id)
        if category is None:
            break
        lineage.append(category.id)
        current_id = category.parent_id
    return tuple(lineage)


def resolve_marketplace_commission(
    session: Session,
    *,
    store_id: uuid.UUID,
    category_id: uuid.UUID,
) -> ResolvedCommission:
    """Resolve Store+Category, Category, then Global commission policy."""

    lineage = _category_lineage(session, category_id)
    rules = session.scalars(
        select(MarketplaceCommissionRule).where(
            MarketplaceCommissionRule.is_active.is_(True)
        )
    ).all()
    by_scope = {(rule.store_id, rule.category_id): rule for rule in rules}

    for current_category_id in lineage:
        rule = by_scope.get((store_id, current_category_id))
        if rule is not None:
            return ResolvedCommission(
                rate=Decimal(rule.commission_rate), rule_id=rule.id,
                scope="STORE_CATEGORY",
            )
    for current_category_id in lineage:
        rule = by_scope.get((None, current_category_id))
        if rule is not None:
            return ResolvedCommission(
                rate=Decimal(rule.commission_rate), rule_id=rule.id,
                scope="CATEGORY",
            )
    rule = by_scope.get((None, None))
    if rule is not None:
        return ResolvedCommission(
            rate=Decimal(rule.commission_rate), rule_id=rule.id, scope="GLOBAL",
        )
    raise CommissionRuleMissingError(
        "No existe una comisión configurada para esta tienda/categoría."
    )


def resolve_default_store_inventory_location(
    session: Session,
    *,
    store_id: uuid.UUID,
) -> WarehouseLocation:
    """Return the explicit seller location; never use an ECUVEL operating point."""

    location = session.scalar(
        select(WarehouseLocation)
        .join(
            StoreInventoryLocation,
            StoreInventoryLocation.location_id == WarehouseLocation.id,
        )
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
