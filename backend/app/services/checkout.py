from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Cart,
    Category,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductMedia,
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
    SellerCommissionType,
    StoreStatus,
    UserStatus,
)
from app.services.age_eligibility import is_at_least_18
from app.services.adult_access_policy import adult_public_product_clause
from app.services.cart import get_cart_state
from app.services.cart_storage import consume_checkout_items, load_cart_state_for_user
from app.services.inventory import (
    InventoryServiceError,
    get_sellable_quantities_by_warehouse_for_offers,
    reserve_inventory,
)
from app.services.product_media import (
    select_product_media,
    variant_media_binding,
)
from app.services.financial_reconciliation import reconcile_seller_order


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
    offer_updated_at: datetime
    product_id: uuid.UUID
    variant_id: uuid.UUID
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
    image_url: str | None
    issue: str | None


def _checkout_image_url(
    product: Product,
    variant: ProductVariant,
    media: list[ProductMedia],
) -> str | None:
    visual_key, visual_value = variant_media_binding(
        product.variant_configuration or {},
        variant.attributes or {},
    )
    selected = select_product_media(
        media,
        variant_axis_key=visual_key,
        variant_value_key=visual_value,
    )
    if selected is None:
        return None
    return f"/productos/{product.slug}/media/{selected.public_id}"


def _commission_snapshot(
    row: _CheckoutRow,
) -> tuple[SellerCommissionType, Decimal | None, Decimal | None, Decimal]:
    gross = (row.offer.price * row.quantity).quantize(
        MONEY_QUANTUM, rounding=ROUND_HALF_UP
    )
    if row.offer.commission_type == SellerCommissionType.FIXED:
        if (
            row.offer.commission_fixed_amount is None
            or row.offer.commission_fixed_amount <= ZERO
            or row.offer.commission_currency != "USD"
        ):
            raise CheckoutPriceError("La oferta no tiene una tarifa fija válida.")
        amount = (row.offer.commission_fixed_amount * row.quantity).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )
        if amount > gross:
            raise CheckoutPriceError("La oferta no tiene una tarifa fija válida.")
        return (
            SellerCommissionType.FIXED,
            None,
            row.offer.commission_fixed_amount,
            amount,
        )
    if row.offer.commission_type != SellerCommissionType.PERCENTAGE:
        raise CheckoutPriceError("La oferta no tiene un tipo de comisión válido.")
    rate = row.offer.commission_rate
    if rate is None or rate < ZERO or rate > Decimal("100"):
        raise CheckoutPriceError("La oferta no tiene una comisión válida.")
    amount = (gross * rate / Decimal("100")).quantize(
        MONEY_QUANTUM, rounding=ROUND_HALF_UP
    )
    return SellerCommissionType.PERCENTAGE, rate, None, amount


def _line_commission(row: _CheckoutRow) -> Decimal:
    return _commission_snapshot(row)[3]


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


def _lock_checkout_catalog(session: Session, offer_ids: set[uuid.UUID]) -> None:
    # Seguir las FK bajo locks compartidos estabiliza también cambios de
    # variante, producto o tienda. Otros compradores pueden leer las mismas filas.
    offers = session.execute(
        select(SellerOffer.variant_id, SellerOffer.store_id)
        .where(SellerOffer.id.in_(offer_ids)).order_by(SellerOffer.id).with_for_update(read=True)
    ).all()
    variants = {row.variant_id for row in offers}
    products = session.scalars(
        select(ProductVariant.product_id).where(ProductVariant.id.in_(variants))
        .order_by(ProductVariant.id).with_for_update(read=True)
    ).all()
    categories = session.scalars(
        select(Product.category_id).where(Product.id.in_(products))
        .order_by(Product.id).with_for_update(read=True)
    ).all()
    parents = session.scalars(
        select(Category.parent_id).where(Category.id.in_(categories))
        .order_by(Category.id).with_for_update(read=True)
    ).all()
    # La política Adult también consulta la categoría padre.
    parent_ids = {key for key in parents if key is not None}
    if parent_ids:
        session.execute(
            select(Category.id).where(Category.id.in_(parent_ids))
            .order_by(Category.id).with_for_update(read=True)
        ).all()
    stores = {row.store_id for row in offers}
    session.execute(
        select(Store.id).where(Store.id.in_(stores))
        .order_by(Store.id).with_for_update(read=True)
    ).all()


def _load_rows(
    session: Session,
    selected: dict[uuid.UUID, int],
    *,
    lock_catalog: bool,
    user=None,
) -> list[_CheckoutRow]:
    offer_ids = set(selected)
    if lock_catalog:
        _lock_checkout_catalog(session, offer_ids)
    statement = (
        select(SellerOffer, ProductVariant, Product, Category, Store)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .join(Store, Store.id == SellerOffer.store_id)
        .where(SellerOffer.id.in_(offer_ids))
        .where(adult_public_product_clause(user=user))
        .order_by(SellerOffer.id)
    )
    statement = statement.execution_options(populate_existing=True)

    entities = {
        offer.id: (offer, variant, product, category, store)
        for offer, variant, product, category, store in session.execute(
            statement
        ).all()
    }
    availability = _warehouse_availability(session, offer_ids)
    product_ids = {product.id for _, _, product, _, _ in entities.values()}
    media_by_product: dict[uuid.UUID, list[ProductMedia]] = {
        product_id: [] for product_id in product_ids
    }
    if product_ids:
        for media in session.scalars(
            select(ProductMedia)
            .where(
                ProductMedia.product_id.in_(product_ids),
                ProductMedia.is_active.is_(True),
            )
            .order_by(
                ProductMedia.product_id,
                ProductMedia.is_cover.desc(),
                ProductMedia.position,
                ProductMedia.id,
            )
        ):
            media_by_product.setdefault(media.product_id, []).append(media)
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
                store.is_verified,
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
                image_url=_checkout_image_url(
                    product, variant, media_by_product.get(product.id, [])
                ),
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
        offer_updated_at=row.offer.updated_at,
        product_id=row.product.id,
        variant_id=row.variant.id,
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
        image_url=row.image_url,
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
        selected, _load_rows(session, selected, lock_catalog=False)
    )


@dataclass(frozen=True, slots=True)
class CheckoutConfirmation:
    key: str
    buyer_id: str
    cart_id: str
    revision: str
    selection: str
    prices: str


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _selection_digest(selected: dict[uuid.UUID, int], preview: CheckoutPreview) -> str:
    quantities = [(str(key), qty) for key, qty in sorted(selected.items())]
    identities = [
        (str(line.offer_id), str(line.product_id), str(line.variant_id), str(line.store_id),
         line.offer_updated_at.astimezone(timezone.utc).isoformat())
        for line in preview.lines
    ]
    return _digest([quantities, identities])


def _price_digest(preview: CheckoutPreview) -> str:
    lines = [(str(line.offer_id), str(line.unit_price)) for line in preview.lines]
    return _digest(["USD", str(preview.total), lines])


def _cart_revision(cart: Cart) -> str:
    return cart.updated_at.astimezone(timezone.utc).isoformat()


def prepare_checkout(
    session: Session, buyer: User,
) -> tuple[CheckoutPreview, CheckoutConfirmation]:
    # Leer primero la revisión hace que una mutación concurrente invalide el
    # formulario. El GET no crea ni bloquea el carrito.
    cart = session.scalar(
        select(Cart).where(Cart.user_id == buyer.id).execution_options(populate_existing=True)
    )
    selected = _selected_items(load_cart_state_for_user(session, buyer.id, user=buyer))
    if cart is None or not selected:
        raise EmptyCheckoutError("Selecciona al menos un producto antes de continuar.")
    rows = _load_rows(session, selected, lock_catalog=False, user=buyer)
    preview = _preview_from_rows(selected, rows)
    confirmation = CheckoutConfirmation(
        key=uuid.uuid4().hex,
        buyer_id=str(buyer.id),
        cart_id=str(cart.id),
        revision=_cart_revision(cart),
        selection=_selection_digest(selected, preview),
        prices=_price_digest(preview),
    )
    return preview, confirmation


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
    buyer_id: uuid.UUID,
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
    if order is None or order.buyer_id != buyer_id or not offer_ids:
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
    confirmation: CheckoutConfirmation,
    payment_method: PaymentMethod,
    reservation_expires_at: datetime,
    before_create: Callable[[], None] | None = None,
) -> CheckoutCreationResult:
    if confirmation.buyer_id != str(buyer_id):
        raise CheckoutIdempotencyConflictError("El checkout cambió. Revisa tu carrito.")
    normalized_key = confirmation.key.strip()
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

    fingerprint = _digest([asdict(confirmation), payment_method.value])
    payment_key = f"checkout:{normalized_key}"
    session.execute(
        select(func.pg_advisory_xact_lock(_advisory_lock_id(payment_key)))
    )
    existing = session.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.idempotency_key == payment_key
        )
    )
    buyer = session.scalar(
        select(User)
        .where(User.id == buyer_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if buyer is None or not buyer.is_active or buyer.status != UserStatus.ACTIVE:
        raise CheckoutBuyerError("El comprador no está disponible.")
    if not is_at_least_18(buyer.birth_date):
        if buyer.birth_date is None:
            raise CheckoutBuyerError(
                "Para comprar en ECUVEL debes tener al menos 18 años. "
                "Indica tu fecha de nacimiento para continuar."
            )
        raise CheckoutBuyerError(
            "Para comprar en ECUVEL debes tener al menos 18 años."
        )

    if existing is not None:
        return _replay_result(session, existing, fingerprint, buyer_id)

    cart = session.scalar(
        select(Cart)
        .where(Cart.user_id == buyer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    changed = "Tu carrito cambió o la selección ya fue procesada. Revisa el carrito antes de confirmar."
    if (
        cart is None
        or str(cart.id) != confirmation.cart_id
        or _cart_revision(cart) != confirmation.revision
    ):
        raise CheckoutIdempotencyConflictError(changed)
    selected = _selected_items(load_cart_state_for_user(session, buyer_id, user=buyer))
    if not selected:
        raise CheckoutIdempotencyConflictError(changed)

    rows = _load_rows(session, selected, lock_catalog=True, user=buyer)
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

    if _price_digest(preview) != confirmation.prices:
        raise CheckoutPriceError(
            "El precio de uno o más productos cambió. "
            "Revisa los importes actualizados antes de confirmar nuevamente tu pedido."
        )

    if _selection_digest(selected, preview) != confirmation.selection:
        raise CheckoutIdempotencyConflictError(changed)

    if before_create is not None:
        before_create()

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
            (_line_commission(row) for row in store_rows), start=ZERO
        )
        seller_order = SellerOrder(
            seller_order_number=f"{order_number}-S{store_index:02d}",
            order_id=order.id,
            store_id=store_id,
            subtotal=store_subtotal,
            discount_total=ZERO,
            commission_total=commission_total,
            seller_net_total=store_subtotal - commission_total,
            currency="USD",
        )
        session.add(seller_order)
        session.flush()

        for row in sorted(store_rows, key=lambda item: item.offer.id):
            line_total = (row.offer.price * row.quantity).quantize(
                MONEY_QUANTUM
            )
            commission_type, commission_rate, commission_fixed, commission_amount = (
                _commission_snapshot(row)
            )
            order_item = OrderItem(
                seller_order_id=seller_order.id,
                offer_id=row.offer.id,
                store_id_snapshot=store_id,
                quantity=row.quantity,
                unit_price=row.offer.price,
                discount_amount=ZERO,
                tax_amount=ZERO,
                line_total=line_total,
                currency="USD",
                gross_line_amount=line_total,
                product_name_snapshot=row.product.title,
                seller_name_snapshot=row.store.name,
                seller_sku_snapshot=row.offer.seller_sku,
                image_url_snapshot=row.image_url,
                variant_snapshot={
                    "title": row.variant.title,
                    "catalog_sku": row.variant.catalog_sku,
                    "attributes": dict(row.variant.attributes or {}),
                },
                commission_type_snapshot=commission_type,
                commission_rate_snapshot=commission_rate,
                commission_fixed_amount_snapshot=commission_fixed,
                commission_amount_snapshot=commission_amount,
                category_name_snapshot=row.category.name,
                category_code_snapshot=row.category.code,
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

        reconcile_seller_order(
            session,
            seller_order_id=seller_order.id,
            expected_store_id=store_id,
            expected_currency="USD",
        )

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
    consume_checkout_items(session, cart.id, purchased_offer_ids)

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
