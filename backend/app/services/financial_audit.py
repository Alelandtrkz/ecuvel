from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.models import AdminAuditEvent


PAYOUT_CREATED = "PAYOUT_CREATED"
PAYOUT_SCHEDULED = "PAYOUT_SCHEDULED"
PAYOUT_HELD = "PAYOUT_HELD"
PAYOUT_RELEASED = "PAYOUT_RELEASED"
PAYOUT_CANCELLED = "PAYOUT_CANCELLED"
PAYOUT_PAID = "PAYOUT_PAID"
BANK_ACCOUNT_VERSION_CREATED = "BANK_ACCOUNT_VERSION_CREATED"
BANK_ACCOUNT_VERSION_APPROVED = "BANK_ACCOUNT_VERSION_APPROVED"
BANK_ACCOUNT_VERSION_SUPERSEDED = "BANK_ACCOUNT_VERSION_SUPERSEDED"

FINANCIAL_AUDIT_ACTIONS = frozenset(
    {
        PAYOUT_CREATED,
        PAYOUT_SCHEDULED,
        PAYOUT_HELD,
        PAYOUT_RELEASED,
        PAYOUT_CANCELLED,
        PAYOUT_PAID,
        BANK_ACCOUNT_VERSION_CREATED,
        BANK_ACCOUNT_VERSION_APPROVED,
        BANK_ACCOUNT_VERSION_SUPERSEDED,
    }
)

_ALLOWED_METADATA = frozenset(
    {"store_id", "payout_id", "bank_account_version_id", "status"}
)


def record_financial_audit(
    session: Session,
    *,
    action: str,
    actor_user_id=None,
    metadata: Mapping[str, str] | None = None,
) -> AdminAuditEvent:
    if action not in FINANCIAL_AUDIT_ACTIONS:
        raise ValueError("La acción de auditoría financiera no es válida.")
    safe_metadata = dict(metadata or {})
    if not set(safe_metadata).issubset(_ALLOWED_METADATA):
        raise ValueError("La metadata de auditoría financiera no es segura.")
    event = AdminAuditEvent(
        actor_user_id=actor_user_id,
        action=action,
        metadata_json=safe_metadata or None,
    )
    session.add(event)
    return event
