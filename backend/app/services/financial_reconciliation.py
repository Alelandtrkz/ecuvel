from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import OrderItem, SellerOrder
from app.models.enums import SellerCommissionType


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class FinancialReconciliationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SellerOrderFinancialSnapshot:
    seller_order_id: uuid.UUID
    store_id: uuid.UUID
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    commission_total: Decimal
    seller_net_total: Decimal


def _money(value) -> Decimal:
    if value is None:
        raise FinancialReconciliationError("El snapshot financiero está incompleto.")
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def reconcile_seller_order(
    session: Session,
    *,
    seller_order_id: uuid.UUID,
    expected_store_id: uuid.UUID | None = None,
    expected_currency: str = "USD",
    lock: bool = False,
) -> SellerOrderFinancialSnapshot:
    statement = select(SellerOrder).options(
        selectinload(SellerOrder.order),
    ).where(SellerOrder.id == seller_order_id)
    if lock:
        statement = statement.with_for_update(of=SellerOrder)
    else:
        statement = statement.options(selectinload(SellerOrder.items))
    seller_order = session.scalar(statement)
    if seller_order is None:
        raise FinancialReconciliationError("La venta no tiene líneas financieras.")
    items = (
        session.scalars(
            select(OrderItem)
            .where(OrderItem.seller_order_id == seller_order.id)
            .order_by(OrderItem.id)
            .with_for_update()
        ).all()
        if lock
        else seller_order.items
    )
    if not items:
        raise FinancialReconciliationError("La venta no tiene líneas financieras.")
    if expected_store_id is not None and seller_order.store_id != expected_store_id:
        raise FinancialReconciliationError("La venta pertenece a otra tienda.")
    if (
        seller_order.currency != expected_currency
        or seller_order.order.currency != expected_currency
    ):
        raise FinancialReconciliationError("La moneda financiera no coincide.")

    subtotal = discount_total = commission_total = ZERO
    for item in items:
        if (
            item.store_id_snapshot is None
            or item.currency is None
            or item.gross_line_amount is None
            or item.commission_type_snapshot is None
            or item.commission_amount_snapshot is None
        ):
            raise FinancialReconciliationError("El snapshot financiero está incompleto.")
        if item.store_id_snapshot != seller_order.store_id:
            raise FinancialReconciliationError("La línea pertenece a otra tienda.")
        if item.currency != expected_currency:
            raise FinancialReconciliationError("La moneda de la línea no coincide.")

        gross = _money(Decimal(item.unit_price) * item.quantity)
        if _money(item.gross_line_amount) != gross:
            raise FinancialReconciliationError("El importe bruto de la línea no coincide.")
        discount = _money(item.discount_amount)
        if item.commission_type_snapshot == SellerCommissionType.PERCENTAGE:
            if (
                item.commission_rate_snapshot is None
                or item.commission_fixed_amount_snapshot is not None
            ):
                raise FinancialReconciliationError("La comisión porcentual está incompleta.")
            expected_commission = (
                gross * Decimal(item.commission_rate_snapshot) / Decimal("100")
            ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        elif item.commission_type_snapshot == SellerCommissionType.FIXED:
            if (
                item.commission_fixed_amount_snapshot is None
                or item.commission_rate_snapshot is not None
            ):
                raise FinancialReconciliationError("La comisión fija está incompleta.")
            expected_commission = (
                Decimal(item.commission_fixed_amount_snapshot) * item.quantity
            ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        else:
            raise FinancialReconciliationError("El tipo de comisión no es válido.")
        if _money(item.commission_amount_snapshot) != expected_commission:
            raise FinancialReconciliationError("La comisión congelada no coincide.")
        subtotal += gross
        discount_total += discount
        commission_total += expected_commission

    subtotal = _money(subtotal)
    discount_total = _money(discount_total)
    commission_total = _money(commission_total)
    seller_net_total = _money(subtotal - discount_total - commission_total)
    if seller_net_total < ZERO:
        raise FinancialReconciliationError("El neto de la venta no es válido.")
    comparisons = (
        (_money(seller_order.subtotal), subtotal),
        (_money(seller_order.discount_total), discount_total),
        (_money(seller_order.commission_total), commission_total),
        (_money(seller_order.seller_net_total), seller_net_total),
    )
    if any(actual != expected for actual, expected in comparisons):
        raise FinancialReconciliationError(
            "Los agregados de la venta no coinciden con sus líneas."
        )
    return SellerOrderFinancialSnapshot(
        seller_order_id=seller_order.id,
        store_id=seller_order.store_id,
        currency=seller_order.currency,
        subtotal=subtotal,
        discount_total=discount_total,
        commission_total=commission_total,
        seller_net_total=seller_net_total,
    )
