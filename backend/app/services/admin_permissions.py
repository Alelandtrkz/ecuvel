from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from flask import abort
from flask_login import current_user
from sqlalchemy.orm import Session
from werkzeug.exceptions import Forbidden

from app.models import StaffProfile, User
from app.models.enums import StaffEmploymentStatus, StaffRole, UserStatus
from app.services.admin_access import ecuvel_staff_required


P = ParamSpec("P")
R = TypeVar("R")

PAYOUT_PERMISSIONS = frozenset({
    "payouts.view", "payouts.schedule", "payouts.hold", "payouts.pay",
    "payouts.cancel", "payouts.proof.view",
})
BANK_SENSITIVE_PERMISSION = "bank_accounts.sensitive.view"

ALL_PERMISSIONS = frozenset({
    "admin.users.view", "admin.users.manage", "admin.staff.view", "admin.staff.manage",
    "scanner.use", "scanner.receive_package", "scanner.dispatch_package",
    "inventory.view_assigned_point", "fulfillment.view_assigned",
    "fulfillment.operate_assigned", "orders.view_related", "products.moderate",
    "stores.moderate", "operations.supervise",
    "reviews.view", "reviews.moderate",
    "payments.view", "payments.review",
}) | PAYOUT_PERMISSIONS | {BANK_SENSITIVE_PERMISSION}

# Conservar los permisos históricos explícitos hasta la migración de perfiles.
# Un permiso nuevo requiere delegación explícita y nunca se hereda por ser legacy.
LEGACY_PERMISSIONS = frozenset({
    "admin.users.view", "admin.users.manage", "admin.staff.view", "admin.staff.manage",
    "scanner.use", "scanner.receive_package", "scanner.dispatch_package",
    "inventory.view_assigned_point", "fulfillment.view_assigned",
    "fulfillment.operate_assigned", "orders.view_related", "operations.supervise",
    "products.moderate", "stores.moderate", "reviews.view", "reviews.moderate",
    "payments.view", "payments.review", "payouts.view", "payouts.proof.view",
})

ROLE_PERMISSIONS: dict[StaffRole, frozenset[str]] = {
    StaffRole.SUPER_ADMIN: ALL_PERMISSIONS,
    StaffRole.OPERATIONS_SUPERVISOR: frozenset({
        "admin.staff.view", "scanner.use", "scanner.receive_package",
        "scanner.dispatch_package", "inventory.view_assigned_point",
        "fulfillment.view_assigned", "fulfillment.operate_assigned",
        "orders.view_related", "operations.supervise",
    }),
    StaffRole.POINT_OPERATOR: frozenset({
        "scanner.use", "scanner.receive_package", "scanner.dispatch_package",
        "inventory.view_assigned_point", "fulfillment.view_assigned",
        "fulfillment.operate_assigned", "orders.view_related",
    }),
    StaffRole.DELIVERY: frozenset({
        "scanner.use", "fulfillment.view_assigned", "fulfillment.operate_assigned",
        "orders.view_related",
    }),
    StaffRole.TRANSPORT_OPERATOR: frozenset({
        "scanner.use", "fulfillment.view_assigned", "fulfillment.operate_assigned",
    }),
    StaffRole.SUPPORT: frozenset({
        "admin.users.view", "orders.view_related", "reviews.view", "reviews.moderate",
    }),
}


def permissions_for_user(user) -> frozenset[str]:
    if (
        not getattr(user, "is_ecuvel_staff", False)
        or not user.is_active
        or user.status != UserStatus.ACTIVE
    ):
        return frozenset()
    profile = getattr(user, "staff_profile", None)
    # Legacy staff existed before StaffProfile. Preserve its current admin access
    # until an explicit profile is adopted, except new sensitive bank access.
    if profile is None:
        return LEGACY_PERMISSIONS
    if profile.employment_status != StaffEmploymentStatus.ACTIVE:
        return frozenset()
    return ROLE_PERMISSIONS.get(profile.role, frozenset())


def user_has_permission(user, permission: str) -> bool:
    return permission in permissions_for_user(user)


class AdminAuthorizationError(Forbidden):
    description = "No tienes autorización para esta operación."


def require_actor_permission(session: Session, actor_user_id, permission: str) -> User:
    if actor_user_id is None:
        raise AdminAuthorizationError()
    actor = session.get(User, actor_user_id, populate_existing=True, with_for_update={"read": True})
    if actor and actor.staff_profile:
        session.get(StaffProfile, actor.staff_profile.id, populate_existing=True, with_for_update={"read": True})
    if not user_has_permission(actor, permission):
        raise AdminAuthorizationError()
    return actor


def delegable_staff_roles(actor) -> frozenset[StaffRole]:
    permissions = permissions_for_user(actor)
    if "admin.staff.manage" not in permissions:
        return frozenset()
    return frozenset(role for role, grants in ROLE_PERMISSIONS.items() if grants <= permissions)


def admin_permission_required(permission: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(view: Callable[P, R]) -> Callable[P, R]:
        @wraps(view)
        @ecuvel_staff_required
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            if not user_has_permission(current_user, permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
