from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from flask import abort
from flask_login import current_user, login_required

from app.models.enums import UserStatus


P = ParamSpec("P")
R = TypeVar("R")


def ecuvel_staff_required(view: Callable[P, R]) -> Callable[P, R]:
    """Require an active internal ECUVEL account for an admin endpoint."""

    @wraps(view)
    @login_required
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        if (
            not current_user.is_active
            or current_user.status != UserStatus.ACTIVE
            or not current_user.is_ecuvel_staff
        ):
            abort(403)
        return view(*args, **kwargs)

    return wrapped
