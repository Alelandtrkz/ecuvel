from __future__ import annotations

from sqlalchemy.exc import IntegrityError


USER_EMAIL_UNIQUE_CONSTRAINTS = frozenset(
    {
        "ix_users_email",
        "ix_users_email_normalized",
    }
)


def is_user_email_unique_violation(exc: IntegrityError) -> bool:
    constraint_name = getattr(
        getattr(exc.orig, "diag", None),
        "constraint_name",
        None,
    )
    return constraint_name in USER_EMAIL_UNIQUE_CONSTRAINTS
