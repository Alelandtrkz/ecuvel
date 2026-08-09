from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterable

from sqlalchemy import and_, exists, select

from app.models.enums import (
    SellerInboundPackageStatus,
    SellerOrderDecisionStatus,
    SellerOrderStatus,
)


class PartnerOrderWorkflowStage(StrEnum):
    PENDING = "PENDING"
    PREPARATION = "PREPARATION"
    LOGISTICS = "LOGISTICS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class PartnerOrderPrimaryAction(StrEnum):
    DECIDE = "DECIDE"
    PREPARE = "PREPARE"
    PRINT_LABEL = "PRINT_LABEL"
    VIEW = "VIEW"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class PartnerOrderWorkflow:
    stage: PartnerOrderWorkflowStage
    label: str
    tone: str
    primary_action: PartnerOrderPrimaryAction
    is_overdue: bool
    package_count: int
    packages_received_count: int
    has_created_packages: bool


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_partner_order_workflow(
    seller_order,
    inbound_packages: Iterable,
    now: datetime | None = None,
) -> PartnerOrderWorkflow:
    """Resolve the seller-facing stage from persisted, auditable facts."""

    effective_now = _aware_utc(now or datetime.now(timezone.utc))
    active_packages = tuple(
        package
        for package in inbound_packages
        if package.status != SellerInboundPackageStatus.CANCELLED
    )
    received_count = sum(
        package.status == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
        for package in active_packages
    )
    all_received = bool(active_packages) and received_count == len(active_packages)
    overdue = bool(
        seller_order.ship_by_at
        and effective_now > _aware_utc(seller_order.ship_by_at)
        and not all_received
        and seller_order.decision_status == SellerOrderDecisionStatus.APPROVED
        and seller_order.status != SellerOrderStatus.COMPLETED
    )

    if seller_order.decision_status == SellerOrderDecisionStatus.REJECTED:
        return PartnerOrderWorkflow(
            PartnerOrderWorkflowStage.REJECTED,
            "Rechazado",
            "rejected",
            PartnerOrderPrimaryAction.VIEW,
            False,
            len(active_packages),
            received_count,
            False,
        )

    if seller_order.status == SellerOrderStatus.COMPLETED:
        return PartnerOrderWorkflow(
            PartnerOrderWorkflowStage.COMPLETED,
            "Entregado",
            "completed",
            PartnerOrderPrimaryAction.VIEW,
            False,
            len(active_packages),
            received_count,
            False,
        )

    if seller_order.decision_status != SellerOrderDecisionStatus.APPROVED:
        return PartnerOrderWorkflow(
            PartnerOrderWorkflowStage.PENDING,
            "Pendiente de aprobación",
            "pending",
            PartnerOrderPrimaryAction.DECIDE,
            False,
            len(active_packages),
            received_count,
            False,
        )

    if all_received:
        labels = {
            SellerOrderStatus.PICKING: "En centro de distribución",
            SellerOrderStatus.PACKED: "Preparado por Ecuvel",
            SellerOrderStatus.READY_FOR_PICKUP: "Disponible para retiro",
        }
        return PartnerOrderWorkflow(
            PartnerOrderWorkflowStage.LOGISTICS,
            labels.get(seller_order.status, "Recibido por Ecuvel"),
            "logistics",
            PartnerOrderPrimaryAction.VIEW,
            False,
            len(active_packages),
            received_count,
            False,
        )

    created = any(
        package.status == SellerInboundPackageStatus.CREATED
        for package in active_packages
    )
    ready = any(
        package.status == SellerInboundPackageStatus.READY_FOR_DROPOFF
        for package in active_packages
    )
    if overdue:
        label, tone = "Entrega atrasada", "overdue"
    elif ready:
        label, tone = "Listo para entregar a Ecuvel", "ready"
    elif created:
        label, tone = "Etiqueta lista", "label"
    else:
        label, tone = "Aprobado · Pendiente de preparar", "preparation"
    return PartnerOrderWorkflow(
        PartnerOrderWorkflowStage.PREPARATION,
        label,
        tone,
        (
            PartnerOrderPrimaryAction.PRINT_LABEL
            if active_packages
            else PartnerOrderPrimaryAction.PREPARE
        ),
        overdue,
        len(active_packages),
        received_count,
        created,
    )


def partner_order_all_inbound_packages_received_predicate():
    """SQL equivalent of the persisted-package portion of the workflow."""

    from app.models.order import SellerOrder
    from app.models.seller_inbound import SellerInboundPackage

    active_package_exists = exists(
        select(SellerInboundPackage.id).where(
            SellerInboundPackage.seller_order_id == SellerOrder.id,
            SellerInboundPackage.status != SellerInboundPackageStatus.CANCELLED,
        )
    )
    unreceived_package_exists = exists(
        select(SellerInboundPackage.id).where(
            SellerInboundPackage.seller_order_id == SellerOrder.id,
            SellerInboundPackage.status.notin_(
                (
                    SellerInboundPackageStatus.CANCELLED,
                    SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
                )
            ),
        )
    )
    return and_(active_package_exists, ~unreceived_package_exists)


def partner_order_preparation_predicate():
    """Select SellerOrders that resolve to the seller PREPARATION stage."""

    from app.models.order import SellerOrder

    return and_(
        SellerOrder.decision_status == SellerOrderDecisionStatus.APPROVED,
        SellerOrder.status != SellerOrderStatus.COMPLETED,
        ~partner_order_all_inbound_packages_received_predicate(),
    )


def partner_order_overdue_predicate(now: datetime):
    """Select PREPARATION rows whose real seller delivery SLA expired."""

    from app.models.order import SellerOrder

    return and_(
        partner_order_preparation_predicate(),
        SellerOrder.ship_by_at.is_not(None),
        SellerOrder.ship_by_at < _aware_utc(now),
    )
