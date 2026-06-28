from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderItem,
    OrderPackage,
    PaymentAttempt,
    PaymentProof,
    Product,
    ProductReview,
    ProductVariant,
    SellerOffer,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PackageStatus,
    PaymentProofStatus,
    PaymentStatus,
    ProductReviewStatus,
    SellerOrderStatus,
)


VALID_ORDER_FILTERS = {"por-entregar", "entregado", "otros"}
DEFAULT_ORDER_FILTER = "por-entregar"
MAX_ORDERS_PAGE = 1000


class CustomerOrderDisplayCode(StrEnum):
    WAITING_PROOF = "WAITING_PROOF"
    PROOF_UNDER_REVIEW = "PROOF_UNDER_REVIEW"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    DELIVERED = "DELIVERED"
    PAYMENT_REJECTED = "PAYMENT_REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class CustomerOrderAction:
    label: str
    endpoint: str
    kind: str = "link"
    tone: str = "secondary"


@dataclass(frozen=True, slots=True)
class CustomerOrderStatusView:
    code: CustomerOrderDisplayCode
    label: str
    description: str
    icon: str
    tone: str
    tab: str
    is_active: bool
    is_final: bool
    can_continue_payment: bool
    can_cancel: bool
    can_view_proof: bool
    can_view_payment_status: bool
    show_countdown: bool
    expires_at: datetime | None
    primary_action: CustomerOrderAction | None
    secondary_action: CustomerOrderAction | None


@dataclass(frozen=True, slots=True)
class CustomerOrderCardView:
    order_id: uuid.UUID
    order_number: str
    payment_attempt_id: uuid.UUID
    payment_proof_id: uuid.UUID | None
    display_status: CustomerOrderStatusView
    total: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime
    pickup_point_name: str
    pickup_point_address: str
    total_units: int
    item_count: int
    store_count: int
    first_item_name: str
    first_item_variant: str | None
    first_item_image_url: str | None
    first_item_quantity: int
    first_item_line_total: Decimal
    additional_item_count: int
    detail_url_key: str


@dataclass(frozen=True, slots=True)
class CustomerOrdersPage:
    orders: tuple[CustomerOrderCardView, ...]
    active_filter: str
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_previous: bool
    has_next: bool
    previous_page: int | None
    next_page: int | None


@dataclass(frozen=True, slots=True)
class CustomerOrderLineView:
    order_item_id: uuid.UUID
    product_slug: str | None
    product_name: str
    variant_title: str | None
    seller_sku: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    image_url: str | None
    can_review: bool
    review_status: str | None
    review_label: str
    review_id: uuid.UUID | None
    review_rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class CustomerOrderSellerGroupView:
    seller_order_number: str
    store_name: str
    status: str
    subtotal: Decimal
    total_units: int
    lines: tuple[CustomerOrderLineView, ...]


@dataclass(frozen=True, slots=True)
class CustomerOrderTimelineStep:
    label: str
    description: str
    icon: str
    completed: bool
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class CustomerOrderPaymentView:
    method: str
    status: str
    amount: Decimal
    currency: str
    expires_at: datetime
    proof_id: uuid.UUID | None
    proof_status: str | None
    proof_filename: str | None
    proof_created_at: datetime | None
    public_rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class CustomerOrderDetailView:
    card: CustomerOrderCardView
    payment: CustomerOrderPaymentView
    seller_groups: tuple[CustomerOrderSellerGroupView, ...]
    timeline: tuple[CustomerOrderTimelineStep, ...]


@dataclass(slots=True)
class _OrderAggregate:
    order: Order
    attempt: PaymentAttempt
    proof: PaymentProof | None
    seller_orders: tuple[SellerOrder, ...]
    items: tuple[OrderItem, ...]
    packages: tuple[OrderPackage, ...]


def normalize_orders_filter(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in VALID_ORDER_FILTERS:
        return DEFAULT_ORDER_FILTER
    return normalized


def normalize_page(value: str | int | None) -> int:
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return min(MAX_ORDERS_PAGE, max(1, page))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _action(label: str, endpoint: str, *, kind: str = "link", tone: str = "secondary") -> CustomerOrderAction:
    return CustomerOrderAction(label=label, endpoint=endpoint, kind=kind, tone=tone)


def resolve_customer_order_status(
    *,
    order: Order,
    payment_attempt: PaymentAttempt,
    payment_proof: PaymentProof | None,
    seller_orders: tuple[SellerOrder, ...],
    packages: tuple[OrderPackage, ...],
    now: datetime,
) -> CustomerOrderStatusView:
    effective_now = _aware_utc(now)
    expires_at = _aware_utc(payment_attempt.expires_at)

    def build(
        code: CustomerOrderDisplayCode,
        label: str,
        description: str,
        icon: str,
        tone: str,
        tab: str,
        *,
        is_final: bool = False,
        can_continue_payment: bool = False,
        can_cancel: bool = False,
        can_view_proof: bool = False,
        can_view_payment_status: bool = False,
        show_countdown: bool = False,
        primary_action: CustomerOrderAction | None = None,
        secondary_action: CustomerOrderAction | None = None,
    ) -> CustomerOrderStatusView:
        return CustomerOrderStatusView(
            code=code,
            label=label,
            description=description,
            icon=icon,
            tone=tone,
            tab=tab,
            is_active=tab == "por-entregar",
            is_final=is_final,
            can_continue_payment=can_continue_payment,
            can_cancel=can_cancel,
            can_view_proof=can_view_proof,
            can_view_payment_status=can_view_payment_status,
            show_countdown=show_countdown,
            expires_at=expires_at,
            primary_action=primary_action,
            secondary_action=secondary_action,
        )

    if (
        payment_attempt.status == PaymentStatus.AWAITING_PROOF
        and payment_proof is None
        and order.status == OrderStatus.PENDING_PAYMENT
        and expires_at > effective_now
    ):
        return build(
            CustomerOrderDisplayCode.WAITING_PROOF,
            "Esperando comprobante",
            "Completa la transferencia antes del vencimiento.",
            "clock-3",
            "warning",
            "por-entregar",
            can_continue_payment=True,
            can_cancel=True,
            show_countdown=True,
            primary_action=_action("Continuar pago", "bank_transfer", tone="primary"),
            secondary_action=_action("Cancelar pedido", "cancel_order", kind="post", tone="ghost"),
        )

    if (
        payment_attempt.status == PaymentStatus.AWAITING_PROOF
        and payment_proof is None
        and expires_at <= effective_now
    ) or payment_attempt.status == PaymentStatus.EXPIRED or order.status == OrderStatus.EXPIRED:
        return build(
            CustomerOrderDisplayCode.EXPIRED,
            "Pago expirado",
            "El plazo para enviar el comprobante terminó y la reserva fue liberada.",
            "timer-off",
            "danger",
            "otros",
            is_final=True,
            primary_action=_action("Ver detalle", "order_detail", tone="primary"),
        )

    if payment_attempt.status == PaymentStatus.REJECTED:
        description = (
            payment_proof.rejection_reason
            if payment_proof and payment_proof.rejection_reason
            else "El comprobante fue rechazado tras la revisión manual."
        )
        return build(
            CustomerOrderDisplayCode.PAYMENT_REJECTED,
            "Pago rechazado",
            description,
            "badge-x",
            "danger",
            "otros",
            is_final=True,
            primary_action=_action("Ver detalle", "order_detail", tone="primary"),
        )

    if payment_attempt.status == PaymentStatus.CANCELLED or order.status == OrderStatus.CANCELLED:
        return build(
            CustomerOrderDisplayCode.CANCELLED,
            "Pedido cancelado",
            "El pedido fue cancelado antes de continuar su preparación.",
            "x-circle",
            "muted",
            "otros",
            is_final=True,
            primary_action=_action("Ver detalle", "order_detail", tone="primary"),
        )

    if (
        payment_attempt.status == PaymentStatus.PROCESSING
        or (
            payment_proof is not None
            and payment_proof.status == PaymentProofStatus.PENDING_REVIEW
        )
    ):
        return build(
            CustomerOrderDisplayCode.PROOF_UNDER_REVIEW,
            "Comprobante en revisión",
            "Recibimos tu archivo. La aprobación sigue siendo manual.",
            "file-check-2",
            "info",
            "por-entregar",
            can_view_proof=payment_proof is not None,
            can_view_payment_status=True,
            primary_action=_action("Ver estado", "order_detail", tone="primary"),
            secondary_action=(
                _action("Ver comprobante", "private_payment_proof")
                if payment_proof is not None
                else None
            ),
        )

    if payment_attempt.status == PaymentStatus.APPROVED or order.status in {
        OrderStatus.CONFIRMED,
        OrderStatus.FULFILLING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.COMPLETED,
    }:
        if packages and all(package.status == PackageStatus.HANDED_OVER for package in packages):
            return build(
                CustomerOrderDisplayCode.DELIVERED,
                "Recogido",
                "El pedido fue entregado correctamente.",
                "circle-check",
                "success",
                "entregado",
                is_final=True,
                primary_action=_action("Ver detalle", "order_detail", tone="primary"),
            )
        if seller_orders and all(
            seller_order.status == SellerOrderStatus.COMPLETED
            for seller_order in seller_orders
        ):
            return build(
                CustomerOrderDisplayCode.DELIVERED,
                "Recogido",
                "El pedido fue entregado correctamente.",
                "circle-check",
                "success",
                "entregado",
                is_final=True,
                primary_action=_action("Ver detalle", "order_detail", tone="primary"),
            )
        if packages and all(
            package.status in {PackageStatus.READY_FOR_PICKUP, PackageStatus.HANDED_OVER}
            and package.ready_at is not None
            for package in packages
        ):
            return build(
                CustomerOrderDisplayCode.READY_FOR_PICKUP,
                "Listo para retirar",
                "Tu pedido está disponible en el punto de entrega.",
                "map-pin-check",
                "success",
                "por-entregar",
                primary_action=_action("Ver detalle", "order_detail", tone="primary"),
            )
        if seller_orders and all(
            seller_order.status == SellerOrderStatus.READY_FOR_PICKUP
            for seller_order in seller_orders
        ):
            return build(
                CustomerOrderDisplayCode.READY_FOR_PICKUP,
                "Listo para retirar",
                "Tu pedido está disponible en el punto de entrega.",
                "map-pin-check",
                "success",
                "por-entregar",
                primary_action=_action("Ver detalle", "order_detail", tone="primary"),
            )
        if packages or any(
            seller_order.status
            in {
                SellerOrderStatus.PICKING,
                SellerOrderStatus.PACKED,
                SellerOrderStatus.READY_FOR_PICKUP,
            }
            for seller_order in seller_orders
        ):
            return build(
                CustomerOrderDisplayCode.PREPARING,
                "Preparando pedido",
                "Estamos preparando tu pedido para el punto de entrega.",
                "package-check",
                "info",
                "por-entregar",
                primary_action=_action("Ver detalle", "order_detail", tone="primary"),
            )
        return build(
            CustomerOrderDisplayCode.PAYMENT_CONFIRMED,
            "Pago confirmado",
            "El pedido fue confirmado y pasará a preparación.",
            "circle-check",
            "success",
            "por-entregar",
            primary_action=_action("Ver detalle", "order_detail", tone="primary"),
        )

    return build(
        CustomerOrderDisplayCode.PAYMENT_CONFIRMED,
        "Pedido recibido",
        "Estamos actualizando la información de este pedido.",
        "package",
        "info",
        "por-entregar",
        primary_action=_action("Ver detalle", "order_detail", tone="primary"),
    )


def _load_aggregates(
    *,
    session: Session,
    order_ids: set[uuid.UUID],
) -> list[_OrderAggregate]:
    if not order_ids:
        return []

    rows = session.execute(
        select(Order, PaymentAttempt, PaymentProof)
        .join(PaymentAttempt, PaymentAttempt.order_id == Order.id)
        .outerjoin(PaymentProof, PaymentProof.payment_attempt_id == PaymentAttempt.id)
        .where(Order.id.in_(order_ids))
        .order_by(Order.created_at.desc(), Order.id.desc())
    ).all()
    orders_by_id: dict[uuid.UUID, tuple[Order, PaymentAttempt, PaymentProof | None]] = {
        order.id: (order, attempt, proof) for order, attempt, proof in rows
    }
    if not orders_by_id:
        return []

    seller_orders = list(
        session.scalars(
            select(SellerOrder)
            .where(SellerOrder.order_id.in_(orders_by_id))
            .order_by(SellerOrder.order_id, SellerOrder.id)
        )
    )
    seller_by_order: dict[uuid.UUID, list[SellerOrder]] = {}
    for seller_order in seller_orders:
        seller_by_order.setdefault(seller_order.order_id, []).append(seller_order)

    seller_order_ids = [seller_order.id for seller_order in seller_orders]
    items = (
        list(
            session.scalars(
                select(OrderItem)
                .where(OrderItem.seller_order_id.in_(seller_order_ids))
                .order_by(OrderItem.seller_order_id, OrderItem.id)
            )
        )
        if seller_order_ids
        else []
    )
    order_by_seller = {
        seller_order.id: seller_order.order_id for seller_order in seller_orders
    }
    items_by_order: dict[uuid.UUID, list[OrderItem]] = {}
    for item in items:
        order_id = order_by_seller[item.seller_order_id]
        items_by_order.setdefault(order_id, []).append(item)

    item_ids = [item.id for item in items]
    packages = (
        list(
            session.scalars(
                select(OrderPackage)
                .where(OrderPackage.order_item_id.in_(item_ids))
                .order_by(OrderPackage.order_item_id, OrderPackage.id)
            )
        )
        if item_ids
        else []
    )
    order_by_item = {
        item.id: order_by_seller[item.seller_order_id] for item in items
    }
    packages_by_order: dict[uuid.UUID, list[OrderPackage]] = {}
    for package in packages:
        order_id = order_by_item[package.order_item_id]
        packages_by_order.setdefault(order_id, []).append(package)

    aggregates: list[_OrderAggregate] = []
    for order_id in order_ids:
        row = orders_by_id.get(order_id)
        if row is None:
            continue
        order, attempt, proof = row
        aggregates.append(
            _OrderAggregate(
                order=order,
                attempt=attempt,
                proof=proof,
                seller_orders=tuple(seller_by_order.get(order.id, ())),
                items=tuple(items_by_order.get(order.id, ())),
                packages=tuple(packages_by_order.get(order.id, ())),
            )
        )
    return aggregates


def _product_slugs_for_items(
    session: Session,
    item_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, str]:
    if not item_ids:
        return {}
    rows = session.execute(
        select(OrderItem.id, Product.slug)
        .join(SellerOffer, SellerOffer.id == OrderItem.offer_id)
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(OrderItem.id.in_(item_ids))
    )
    return {item_id: slug for item_id, slug in rows}


def _reviews_for_items(
    session: Session,
    item_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, ProductReview]:
    if not item_ids:
        return {}
    reviews = session.scalars(
        select(ProductReview).where(ProductReview.order_item_id.in_(item_ids))
    )
    return {review.order_item_id: review for review in reviews}


def _packages_by_item(
    packages: tuple[OrderPackage, ...],
) -> dict[uuid.UUID, OrderPackage]:
    return {package.order_item_id: package for package in packages}


def _line_review_state(
    *,
    item: OrderItem,
    package: OrderPackage | None,
    review: ProductReview | None,
) -> tuple[bool, str | None, str, uuid.UUID | None, str | None]:
    delivered = (
        package is not None
        and package.status == PackageStatus.HANDED_OVER
        and package.handed_over_at is not None
    )
    if review is None:
        return (
            delivered,
            None,
            "Dejar un comentario" if delivered else "Disponible al entregar",
            None,
            None,
        )
    if review.status == ProductReviewStatus.PENDING_REVIEW:
        return (False, review.status.value, "Reseña en revisión", review.id, None)
    if review.status == ProductReviewStatus.PUBLISHED:
        return (False, review.status.value, "Ver mi reseña", review.id, None)
    return (
        False,
        review.status.value,
        "Reseña no publicada",
        review.id,
        review.public_rejection_reason,
    )


def _card_from_aggregate(
    *,
    aggregate: _OrderAggregate,
    pickup_point_name: str,
    pickup_point_address: str,
    now: datetime,
) -> CustomerOrderCardView:
    status = resolve_customer_order_status(
        order=aggregate.order,
        payment_attempt=aggregate.attempt,
        payment_proof=aggregate.proof,
        seller_orders=aggregate.seller_orders,
        packages=aggregate.packages,
        now=now,
    )
    first_item = aggregate.items[0] if aggregate.items else None
    store_ids = {seller_order.store_id for seller_order in aggregate.seller_orders}
    total_units = sum(item.quantity for item in aggregate.items)
    return CustomerOrderCardView(
        order_id=aggregate.order.id,
        order_number=aggregate.order.order_number,
        payment_attempt_id=aggregate.attempt.id,
        payment_proof_id=aggregate.proof.id if aggregate.proof else None,
        display_status=status,
        total=aggregate.order.grand_total,
        currency=aggregate.order.currency,
        created_at=aggregate.order.created_at,
        updated_at=aggregate.order.updated_at,
        pickup_point_name=pickup_point_name,
        pickup_point_address=pickup_point_address,
        total_units=total_units,
        item_count=len(aggregate.items),
        store_count=len(store_ids),
        first_item_name=first_item.product_name_snapshot if first_item else "Pedido",
        first_item_variant=(
            (first_item.variant_snapshot or {}).get("title") if first_item else None
        ),
        first_item_image_url=first_item.image_url_snapshot if first_item else None,
        first_item_quantity=first_item.quantity if first_item else 0,
        first_item_line_total=first_item.line_total if first_item else Decimal("0.00"),
        additional_item_count=max(0, len(aggregate.items) - 1),
        detail_url_key=aggregate.order.order_number,
    )


def _sort_key(card: CustomerOrderCardView) -> tuple[int, float, float]:
    status_priority = {
        CustomerOrderDisplayCode.WAITING_PROOF: 0,
        CustomerOrderDisplayCode.READY_FOR_PICKUP: 1,
        CustomerOrderDisplayCode.PROOF_UNDER_REVIEW: 2,
        CustomerOrderDisplayCode.PAYMENT_CONFIRMED: 3,
        CustomerOrderDisplayCode.PREPARING: 4,
        CustomerOrderDisplayCode.DELIVERED: 0,
        CustomerOrderDisplayCode.PAYMENT_REJECTED: 0,
        CustomerOrderDisplayCode.CANCELLED: 1,
        CustomerOrderDisplayCode.EXPIRED: 2,
    }
    primary = status_priority.get(card.display_status.code, 50)
    if card.display_status.tab == "por-entregar" and card.display_status.expires_at:
        secondary = _aware_utc(card.display_status.expires_at).timestamp()
    elif card.display_status.tab == "entregado":
        secondary = -card.updated_at.timestamp()
    else:
        secondary = -card.updated_at.timestamp()
    return (primary, secondary, -card.created_at.timestamp())


def get_customer_orders_page(
    *,
    session: Session,
    order_ids: set[uuid.UUID],
    active_filter: str,
    page: int,
    page_size: int,
    pickup_point_name: str,
    pickup_point_address: str,
    now: datetime | None = None,
) -> CustomerOrdersPage:
    normalized_filter = normalize_orders_filter(active_filter)
    normalized_page = normalize_page(page)
    effective_now = now or datetime.now(timezone.utc)
    cards = [
        _card_from_aggregate(
            aggregate=aggregate,
            pickup_point_name=pickup_point_name,
            pickup_point_address=pickup_point_address,
            now=effective_now,
        )
        for aggregate in _load_aggregates(session=session, order_ids=order_ids)
    ]
    filtered = [
        card
        for card in cards
        if card.display_status.tab == normalized_filter
    ]
    filtered.sort(key=_sort_key)
    total_items = len(filtered)
    total_pages = max(1, math.ceil(total_items / page_size))
    normalized_page = min(normalized_page, total_pages)
    start = (normalized_page - 1) * page_size
    page_items = tuple(filtered[start:start + page_size])
    return CustomerOrdersPage(
        orders=page_items,
        active_filter=normalized_filter,
        page=normalized_page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_previous=normalized_page > 1,
        has_next=normalized_page < total_pages,
        previous_page=normalized_page - 1 if normalized_page > 1 else None,
        next_page=normalized_page + 1 if normalized_page < total_pages else None,
    )


def _timeline_for_detail(
    *,
    card: CustomerOrderCardView,
    aggregate: _OrderAggregate,
) -> tuple[CustomerOrderTimelineStep, ...]:
    proof = aggregate.proof
    approved_at = aggregate.attempt.approved_at
    ready_at_values = [
        package.ready_at for package in aggregate.packages if package.ready_at
    ]
    handed_over_values = [
        package.handed_over_at
        for package in aggregate.packages
        if package.handed_over_at
    ]
    status = card.display_status.code
    return (
        CustomerOrderTimelineStep(
            "Pedido creado",
            "Recibimos tu pedido.",
            "shopping-bag",
            True,
            aggregate.order.created_at,
        ),
        CustomerOrderTimelineStep(
            "Pago recibido",
            "El pago fue aprobado manualmente.",
            "badge-check",
            status
            in {
                CustomerOrderDisplayCode.PAYMENT_CONFIRMED,
                CustomerOrderDisplayCode.PREPARING,
                CustomerOrderDisplayCode.READY_FOR_PICKUP,
                CustomerOrderDisplayCode.DELIVERED,
            },
            approved_at,
        ),
        CustomerOrderTimelineStep(
            "En preparación",
            "La tienda prepara los productos.",
            "package-check",
            status
            in {
                CustomerOrderDisplayCode.PREPARING,
                CustomerOrderDisplayCode.READY_FOR_PICKUP,
                CustomerOrderDisplayCode.DELIVERED,
            },
            None,
        ),
        CustomerOrderTimelineStep(
            "Listo para retirar",
            "Disponible en el punto de entrega.",
            "map-pin-check",
            status
            in {
                CustomerOrderDisplayCode.READY_FOR_PICKUP,
                CustomerOrderDisplayCode.DELIVERED,
            },
            min(ready_at_values) if ready_at_values else None,
        ),
        CustomerOrderTimelineStep(
            "Recogido",
            "El pedido fue entregado.",
            "circle-check",
            status == CustomerOrderDisplayCode.DELIVERED,
            max(handed_over_values) if handed_over_values else None,
        ),
    )


def get_customer_order_detail(
    *,
    session: Session,
    order_number: str,
    order_ids: set[uuid.UUID],
    pickup_point_name: str,
    pickup_point_address: str,
    now: datetime | None = None,
) -> CustomerOrderDetailView | None:
    aggregates = _load_aggregates(session=session, order_ids=order_ids)
    aggregate = next(
        (
            candidate
            for candidate in aggregates
            if candidate.order.order_number == order_number
        ),
        None,
    )
    if aggregate is None:
        return None
    effective_now = now or datetime.now(timezone.utc)
    card = _card_from_aggregate(
        aggregate=aggregate,
        pickup_point_name=pickup_point_name,
        pickup_point_address=pickup_point_address,
        now=effective_now,
    )
    items_by_seller: dict[uuid.UUID, list[OrderItem]] = {}
    for item in aggregate.items:
        items_by_seller.setdefault(item.seller_order_id, []).append(item)
    item_ids = tuple(item.id for item in aggregate.items)
    product_slugs = _product_slugs_for_items(session, item_ids)
    reviews_by_item = _reviews_for_items(session, item_ids)
    packages_by_item = _packages_by_item(aggregate.packages)
    seller_groups = tuple(
        CustomerOrderSellerGroupView(
            seller_order_number=seller_order.seller_order_number,
            store_name=(
                items_by_seller[seller_order.id][0].seller_name_snapshot
                if items_by_seller.get(seller_order.id)
                else "Tienda"
            ),
            status=seller_order.status.value,
            subtotal=seller_order.subtotal,
            total_units=sum(
                item.quantity for item in items_by_seller.get(seller_order.id, ())
            ),
            lines=tuple(
                (
                    lambda state: CustomerOrderLineView(
                        order_item_id=item.id,
                        product_slug=product_slugs.get(item.id),
                        product_name=item.product_name_snapshot,
                        variant_title=(item.variant_snapshot or {}).get("title"),
                        seller_sku=item.seller_sku_snapshot,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                        line_total=item.line_total,
                        image_url=item.image_url_snapshot,
                        can_review=state[0],
                        review_status=state[1],
                        review_label=state[2],
                        review_id=state[3],
                        review_rejection_reason=state[4],
                    )
                )(
                    _line_review_state(
                        item=item,
                        package=packages_by_item.get(item.id),
                        review=reviews_by_item.get(item.id),
                    )
                )
                for item in items_by_seller.get(seller_order.id, ())
            ),
        )
        for seller_order in aggregate.seller_orders
    )
    payment = CustomerOrderPaymentView(
        method=aggregate.attempt.method.value,
        status=aggregate.attempt.status.value,
        amount=aggregate.attempt.amount,
        currency=aggregate.attempt.currency,
        expires_at=aggregate.attempt.expires_at,
        proof_id=aggregate.proof.id if aggregate.proof else None,
        proof_status=aggregate.proof.status.value if aggregate.proof else None,
        proof_filename=aggregate.proof.original_filename if aggregate.proof else None,
        proof_created_at=aggregate.proof.created_at if aggregate.proof else None,
        public_rejection_reason=(
            aggregate.proof.rejection_reason
            if aggregate.proof and aggregate.proof.rejection_reason
            else None
        ),
    )
    return CustomerOrderDetailView(
        card=card,
        payment=payment,
        seller_groups=seller_groups,
        timeline=_timeline_for_detail(card=card, aggregate=aggregate),
    )
