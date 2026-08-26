from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from flask import abort
from flask_login import current_user

from app.models.enums import StaffEmploymentStatus, StaffRole
from app.services.admin_access import ecuvel_staff_required


P = ParamSpec("P")
R = TypeVar("R")

ALL_PERMISSIONS = frozenset({
    "admin.users.view", "admin.users.manage", "admin.staff.view", "admin.staff.manage",
    "scanner.use", "scanner.receive_package", "scanner.dispatch_package",
    "inventory.view_assigned_point", "fulfillment.view_assigned",
    "fulfillment.operate_assigned", "orders.view_related", "products.moderate",
    "stores.moderate", "operations.supervise",
    "reviews.view", "reviews.moderate",
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
    if not getattr(user, "is_ecuvel_staff", False):
        return frozenset()
    profile = getattr(user, "staff_profile", None)
    # Legacy staff existed before StaffProfile. Preserve its current admin access
    # until an explicit profile is adopted; never fabricate identity data.
    if profile is None:
        return ALL_PERMISSIONS
    if profile.employment_status != StaffEmploymentStatus.ACTIVE:
        return frozenset()
    return ROLE_PERMISSIONS.get(profile.role, frozenset())


def user_has_permission(user, permission: str) -> bool:
    return permission in permissions_for_user(user)


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
