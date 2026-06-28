from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductVariant,
    SellerOffer,
    SellerOrder,
    Store,
    User,
)
from app.models.enums import (
    OfferStatus,
    PaymentMethod,
    PaymentStatus,
    StoreStatus,
    UserStatus,
)
from app.services.cart import get_cart_state
from app.services.inventory import (
    InventoryServiceError,
    get_sellable_quantities_by_warehouse_for_offers,
    reserve_inventory,
)


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class CheckoutServiceError(Exception):
    """Error base del checkout."""


class EmptyCheckoutError(CheckoutServiceError):
    """No hay artículos seleccionados para comprar."""


class CheckoutItemUnavailableError(CheckoutServiceError):
    """Un artículo ya no puede comprarse o reservarse."""


class CheckoutPriceError(CheckoutServiceError):
    """El precio o la moneda no son válidos para el checkout."""


class UnsupportedPaymentMethodError(CheckoutServiceError):
    """El método de pago no está habilitado."""


class CheckoutIdempotencyConflictError(CheckoutServiceError):
    """La clave idempotente ya representa otra compra."""


class CheckoutBuyerError(CheckoutServiceError):
    """El comprador no existe o no está activo."""


class CheckoutWarehouseError(CheckoutServiceError):
    """No se puede determinar un almacén inequívoco."""


@dataclass(frozen=True, slots=True)
class CheckoutIssue:
    offer_id: uuid.UUID
    message: str


@dataclass(frozen=True, slots=True)
class CheckoutLine:
    offer_id: uuid.UUID
    store_id: uuid.UUID
    store_name: str
    product_name: str
    variant_name: str | None
    seller_sku: str
    quantity: int
    unit_price: Decimal
    compare_at_price: Decimal | None
    line_total: Decimal
    savings: Decimal
    image_url: str | None


@dataclass(frozen=True, slots=True)
class CheckoutStoreGroup:
    store_id: uuid.UUID
    store_name: str
    lines: tuple[CheckoutLine, ...]
    total_units: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class CheckoutPreview:
    lines: tuple[CheckoutLine, ...]
    groups: tuple[CheckoutStoreGroup, ...]
    issues: tuple[CheckoutIssue, ...]
    total_units: int
    display_subtotal: Decimal
    savings: Decimal
    total: Decimal

    @property
    def can_submit(self) -> bool:
        return bool(self.lines) and not self.issues


@dataclass(frozen=True, slots=True)
class CheckoutCreationResult:
    order_id: uuid.UUID
    order_number: str
    payment_attempt_id: uuid.UUID
    payment_status: PaymentStatus
    total: Decimal
    reservation_expires_at: datetime
    purchased_offer_ids: tuple[uuid.UUID, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class _CheckoutRow:
    offer: SellerOffer
    variant: ProductVariant
    product: Product
    category: Category
    store: Store
    quantity: int
    warehouse_id: uuid.UUID | None
    issue: str | None


def _selected_items(cart_state: object) -> dict[uuid.UUID, int]:
    normalized = get_cart_state(cart_state)
    return {
        uuid.UUID(offer_id): int(item["quantity"])
        for offer_id, item in normalized["items"].items()
        if item["selected"]
    }


def _warehouse_availability(
    session: Session, offer_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[uuid.UUID, int]]]:
    grouped = get_sellable_quantities_by_warehouse_for_offers(
        session=session,
        offer_ids=offer_ids,
    )
    return {
        offer_id: list(warehouse_quantities.items())
        for offer_id, warehouse_quantities in grouped.items()
    }


def _load_rows(
    session: Session,
    selected: dict[uuid.UUID, int],
    *,
    lock_offers: bool,
) -> list[_CheckoutRow]:
    offer_ids = set(selected)
    statement = (
        select(SellerOffer, ProductVariant, Product, Category, Store)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(SellerOffer.id.in_(offer_ids))
        .order_by(SellerOffer.id)
    )
    if lock_offers:
        statement = statement.with_for_update(of=SellerOffer)

    entities = {
        offer.id: (offer, variant, product, category, store)
        for offer, variant, product, category, store in session.execute(
            statement
        ).all()
    }
    availability = _warehouse_availability(session, offer_ids)
    result: list[_CheckoutRow] = []

    for offer_id, quantity in sorted(selected.items(), key=lambda item: item[0]):
        entity = entities.get(offer_id)
        if entity is None:
            continue
        offer, variant, product, category, store = entity
        issue: str | None = None
        warehouse_id: uuid.UUID | None = None
        visible = all(
            (
                offer.status == OfferStatus.ACTIVE,
                variant.is_active,
                product.is_active,
                category.is_active,
                store.status == StoreStatus.ACTIVE,
            )
        )
        if not visible:
            issue = f"{product.title} ya no está disponible."
        elif offer.currency != "USD" or offer.price <= ZERO:
            issue = f"{product.title} no tiene un precio válido en USD."
        else:
            capable = [
                candidate_id
                for candidate_id, available in availability.get(offer_id, [])
                if available >= quantity
            ]
            if not capable:
                issue = f"No hay stock suficiente de {product.title}."
            elif len(capable) > 1:
                issue = (
                    f"{product.title} tiene stock en varios almacenes y "
                    "requiere una regla logística explícita."
                )
            else:
                warehouse_id = capable[0]
        result.append(
            _CheckoutRow(
                offer=offer,
                variant=variant,
                product=product,
                category=category,
                store=store,
                quantity=quantity,
                warehouse_id=warehouse_id,
                issue=issue,
            )
        )
    return result


def _line_from_row(row: _CheckoutRow) -> CheckoutLine:
    compare_at = row.offer.compare_at_price
    if compare_at is not None and compare_at <= row.offer.price:
        compare_at = None
    line_total = (row.offer.price * row.quantity).quantize(MONEY_QUANTUM)
    display_total = ((compare_at or row.offer.price) * row.quantity).quantize(
        MONEY_QUANTUM
    )
    return CheckoutLine(
        offer_id=row.offer.id,
        store_id=row.store.id,
        store_name=row.store.name,
        product_name=row.product.title,
        variant_name=row.variant.title,
        seller_sku=row.offer.seller_sku,
        quantity=row.quantity,
        unit_price=row.offer.price,
        compare_at_price=compare_at,
        line_total=line_total,
        savings=display_total - line_total,
        image_url=None,
    )


def _preview_from_rows(
    selected: dict[uuid.UUID, int], rows: list[_CheckoutRow]
) -> CheckoutPreview:
    found_ids = {row.offer.id for row in rows}
    issues = [
        CheckoutIssue(offer_id, "La oferta seleccionada ya no existe.")
        for offer_id in sorted(set(selected) - found_ids)
    ]
    issues.extend(
        CheckoutIssue(row.offer.id, row.issue)
        for row in rows
        if row.issue is not None
    )
    lines = tuple(_line_from_row(row) for row in rows if row.issue is None)
    grouped: dict[uuid.UUID, list[CheckoutLine]] = {}
    for line in lines:
        grouped.setdefault(line.store_id, []).append(line)
    groups = tuple(
        CheckoutStoreGroup(
            store_id=store_id,
            store_name=store_lines[0].store_name,
            lines=tuple(store_lines),
            total_units=sum(line.quantity for line in store_lines),
            total=sum((line.line_total for line in store_lines), start=ZERO),
        )
        for store_id, store_lines in sorted(grouped.items(), key=lambda item: item[0])
    )
    total = sum((line.line_total for line in lines), start=ZERO)
    savings = sum((line.savings for line in lines), start=ZERO)
    return CheckoutPreview(
        lines=lines,
        groups=groups,
        issues=tuple(issues),
        total_units=sum(line.quantity for line in lines),
        display_subtotal=total + savings,
        savings=savings,
        total=total,
    )


def build_checkout_preview(
    *, session: Session, cart_state: object
) -> CheckoutPreview:
    selected = _selected_items(cart_state)
    if not selected:
        raise EmptyCheckoutError(
            "Selecciona al menos un producto antes de continuar."
        )
    return _preview_from_rows(
        selected, _load_rows(session, selected, lock_offers=False)
    )


def _request_fingerprint(
    buyer_id: uuid.UUID,
    payment_method: PaymentMethod,
    selected: dict[uuid.UUID, int],
) -> str:
    payload = {
        "buyer_id": str(buyer_id),
        "payment_method": payment_method.value,
        "items": [
            [str(offer_id), quantity]
            for offer_id, quantity in sorted(selected.items(), key=lambda item: item[0])
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _advisory_lock_id(key: str) -> int:
    return int.from_bytes(
        hashlib.sha256(key.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _replay_result(
    session: Session,
    attempt: PaymentAttempt,
    fingerprint: str,
) -> CheckoutCreationResult:
    if attempt.request_fingerprint != fingerprint:
        raise CheckoutIdempotencyConflictError(
            "La clave de checkout ya fue utilizada con otros datos."
        )
    offer_ids = tuple(
        session.scalars(
            select(OrderItem.offer_id)
            .join(SellerOrder, SellerOrder.id == OrderItem.seller_order_id)
            .where(SellerOrder.order_id == attempt.order_id)
            .order_by(OrderItem.offer_id)
        ).all()
    )
    order = session.get(Order, attempt.order_id)
    if order is None or not offer_ids:
        raise CheckoutIdempotencyConflictError(
            "El checkout existente está incompleto."
        )
    return CheckoutCreationResult(
        order_id=order.id,
        order_number=order.order_number,
        payment_attempt_id=attempt.id,
        payment_status=attempt.status,
        total=attempt.amount,
        reservation_expires_at=attempt.expires_at,
        purchased_offer_ids=offer_ids,
        replayed=True,
    )


def create_checkout_order(
    *,
    session: Session,
    buyer_id: uuid.UUID,
    cart_state: object,
    payment_method: PaymentMethod,
    idempotency_key: str,
    reservation_expires_at: datetime,
) -> CheckoutCreationResult:
    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 120:
        raise ValueError("La clave de checkout no es válida.")
    if payment_method != PaymentMethod.BANK_TRANSFER:
        raise UnsupportedPaymentMethodError(
            "El pago con tarjeta estará disponible cuando se configure "
            "la pasarela."
        )
    if (
        reservation_expires_at.tzinfo is None
        or reservation_expires_at.utcoffset() is None
    ):
        raise ValueError("La expiración debe incluir zona horaria.")
    if reservation_expires_at <= datetime.now(reservation_expires_at.tzinfo):
        raise ValueError("La expiración debe estar en el futuro.")

    selected = _selected_items(cart_state)
    if not selected:
        raise EmptyCheckoutError(
            "Selecciona al menos un producto antes de continuar."
        )
    fingerprint = _request_fingerprint(buyer_id, payment_method, selected)
    payment_key = f"checkout:{normalized_key}"
    session.execute(
        select(func.pg_advisory_xact_lock(_advisory_lock_id(payment_key)))
    )
    existing = session.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.idempotency_key == payment_key
        )
    )
    if existing is not None:
        return _replay_result(session, existing, fingerprint)

    buyer = session.scalar(
        select(User)
        .where(User.id == buyer_id)
        .with_for_update()
    )
    if buyer is None or buyer.status != UserStatus.ACTIVE:
        raise CheckoutBuyerError("El comprador no está disponible.")

    rows = _load_rows(session, selected, lock_offers=True)
    preview = _preview_from_rows(selected, rows)
    if preview.issues:
        message = " ".join(issue.message for issue in preview.issues)
        if any("precio" in issue.message for issue in preview.issues):
            raise CheckoutPriceError(message)
        if any("almacenes" in issue.message for issue in preview.issues):
            raise CheckoutWarehouseError(message)
        raise CheckoutItemUnavailableError(message)
    if preview.total <= ZERO:
        raise CheckoutPriceError("El total del pedido debe ser mayor que cero.")

    digest = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
    order_number = f"ECV-{digest[:16].upper()}"
    order = Order(
        order_number=order_number,
        buyer_id=buyer.id,
        currency="USD",
        subtotal=preview.total,
        discount_total=ZERO,
        shipping_total=ZERO,
        tax_total=ZERO,
        grand_total=preview.total,
    )
    session.add(order)
    session.flush()

    rows_by_store: dict[uuid.UUID, list[_CheckoutRow]] = {}
    for row in rows:
        rows_by_store.setdefault(row.store.id, []).append(row)

    purchased_offer_ids: list[uuid.UUID] = []
    for store_index, (store_id, store_rows) in enumerate(
        sorted(rows_by_store.items(), key=lambda item: item[0]), start=1
    ):
        store_subtotal = sum(
            (
                (row.offer.price * row.quantity).quantize(MONEY_QUANTUM)
                for row in store_rows
            ),
            start=ZERO,
        )
        commission_total = sum(
            (
                (
                    row.offer.price
                    * row.quantity
                    * row.offer.commission_rate
                    / Decimal("100")
                ).quantize(MONEY_QUANTUM)
                for row in store_rows
            ),
            start=ZERO,
        )
        seller_order = SellerOrder(
            seller_order_number=f"{order_number}-S{store_index:02d}",
            order_id=order.id,
            store_id=store_id,
            subtotal=store_subtotal,
            discount_total=ZERO,
            commission_total=commission_total,
            seller_net_total=store_subtotal - commission_total,
        )
        session.add(seller_order)
        session.flush()

        for row in sorted(store_rows, key=lambda item: item.offer.id):
            line_total = (row.offer.price * row.quantity).quantize(
                MONEY_QUANTUM
            )
            order_item = OrderItem(
                seller_order_id=seller_order.id,
                offer_id=row.offer.id,
                quantity=row.quantity,
                unit_price=row.offer.price,
                discount_amount=ZERO,
                tax_amount=ZERO,
                line_total=line_total,
                product_name_snapshot=row.product.title,
                seller_name_snapshot=row.store.name,
                seller_sku_snapshot=row.offer.seller_sku,
                image_url_snapshot=None,
                variant_snapshot={
                    "title": row.variant.title,
                    "catalog_sku": row.variant.catalog_sku,
                    "attributes": dict(row.variant.attributes or {}),
                },
            )
            session.add(order_item)
            session.flush()
            try:
                reserve_inventory(
                    session=session,
                    order_item_id=order_item.id,
                    warehouse_id=row.warehouse_id,
                    expires_at=reservation_expires_at,
                    idempotency_key=(
                        f"checkout:{normalized_key}:{order_item.id.hex}"
                    ),
                    actor_user_id=buyer.id,
                    notes="Reserva creada por checkout web.",
                )
            except InventoryServiceError as exc:
                raise CheckoutItemUnavailableError(str(exc)) from exc
            purchased_offer_ids.append(row.offer.id)

    attempt = PaymentAttempt(
        order_id=order.id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_PROOF,
        amount=order.grand_total,
        currency=order.currency,
        idempotency_key=payment_key,
        request_fingerprint=fingerprint,
        expires_at=reservation_expires_at,
    )
    session.add(attempt)
    session.flush()

    return CheckoutCreationResult(
        order_id=order.id,
        order_number=order.order_number,
        payment_attempt_id=attempt.id,
        payment_status=attempt.status,
        total=order.grand_total,
        reservation_expires_at=reservation_expires_at,
        purchased_offer_ids=tuple(purchased_offer_ids),
        replayed=False,
    )
