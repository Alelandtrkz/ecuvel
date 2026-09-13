from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, MutableMapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserPrivacyPreferenceEvent
from app.models.enums import (
    PrivacyPreferenceDecision,
    PrivacyPreferencePurpose,
    PrivacyPreferenceSource,
)
from app.services.legal_product import (
    COOKIES_KEY,
    LegalPublicationConsistencyError,
    SynchronizedLegalPublication,
    resolve_synchronized_legal_publication,
)


PRIVACY_PREFERENCE_SESSION_KEY = "catalog_telemetry_preference"
CATALOG_TELEMETRY_PURPOSE = (
    PrivacyPreferencePurpose.CATALOG_BEHAVIORAL_TELEMETRY
)


class PrivacyPreferenceError(ValueError):
    pass


class PrivacyNoticeUnavailableError(PrivacyPreferenceError):
    pass


class StalePrivacyNoticeError(PrivacyPreferenceError):
    pass


@dataclass(frozen=True, slots=True)
class PrivacyPreferenceState:
    decision: PrivacyPreferenceDecision | None
    source_scope: str | None
    telemetry_allowed: bool
    show_prompt: bool
    current_notice: SynchronizedLegalPublication | None


def _current_cookie_notice(
    session: Session, at: datetime
) -> SynchronizedLegalPublication | None:
    try:
        return resolve_synchronized_legal_publication(
            session, *COOKIES_KEY, at
        )
    except LegalPublicationConsistencyError:
        return None


def _latest_authenticated_event(
    session: Session,
    user_id: uuid.UUID,
) -> UserPrivacyPreferenceEvent | None:
    return session.scalar(
        select(UserPrivacyPreferenceEvent)
        .where(
            UserPrivacyPreferenceEvent.user_id == user_id,
            UserPrivacyPreferenceEvent.purpose == CATALOG_TELEMETRY_PURPOSE,
        )
        .order_by(
            UserPrivacyPreferenceEvent.occurred_at.desc(),
            UserPrivacyPreferenceEvent.created_at.desc(),
            UserPrivacyPreferenceEvent.id.desc(),
        )
        .limit(1)
    )


def _session_choice(
    browser_session: MutableMapping[str, Any],
) -> tuple[PrivacyPreferenceDecision, str | None, str | None] | None:
    value = browser_session.get(PRIVACY_PREFERENCE_SESSION_KEY)
    if not isinstance(value, dict):
        return None
    try:
        decision = PrivacyPreferenceDecision(value.get("decision"))
    except (TypeError, ValueError):
        return None
    identifier = value.get("notice_version_identifier")
    digest = value.get("notice_content_sha256")
    if identifier is not None and not isinstance(identifier, str):
        return None
    if digest is not None and not isinstance(digest, str):
        return None
    if (identifier is None) != (digest is None):
        return None
    if decision == PrivacyPreferenceDecision.GRANTED and identifier is None:
        return None
    return decision, identifier, digest


def _event_choice(
    event: UserPrivacyPreferenceEvent,
    expected_user_id: uuid.UUID,
) -> tuple[PrivacyPreferenceDecision, str | None, str | None]:
    if (
        event.user_id != expected_user_id
        or event.purpose != CATALOG_TELEMETRY_PURPOSE
    ):
        raise PrivacyPreferenceError(
            "Stored privacy preference does not match its user or purpose."
        )
    if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
        raise PrivacyPreferenceError(
            "Stored privacy preference time must include a time zone."
        )
    if (event.notice_version_identifier is None) != (
        event.notice_content_sha256 is None
    ):
        raise PrivacyPreferenceError(
            "Stored privacy preference notice snapshots are inconsistent."
        )
    if (
        event.decision == PrivacyPreferenceDecision.GRANTED
        and event.notice_version_identifier is None
    ):
        raise PrivacyPreferenceError(
            "Stored telemetry grant has no notice snapshot."
        )
    return (
        event.decision,
        event.notice_version_identifier,
        event.notice_content_sha256,
    )


def _store_browser_choice(
    browser_session: MutableMapping[str, Any],
    choice: tuple[PrivacyPreferenceDecision, str | None, str | None],
) -> None:
    browser_session[PRIVACY_PREFERENCE_SESSION_KEY] = {
        "decision": choice[0].value,
        "notice_version_identifier": choice[1],
        "notice_content_sha256": choice[2],
    }


def reconcile_browser_preference_after_account_login(
    session: Session,
    browser_session: MutableMapping[str, Any],
    *,
    user_id: uuid.UUID,
) -> None:
    """Prevent an account rejection from revealing an older browser grant."""
    event = _latest_authenticated_event(session, user_id)
    if event is None:
        return
    choice = _event_choice(event, user_id)
    if choice[0] == PrivacyPreferenceDecision.REJECTED:
        _store_browser_choice(browser_session, choice)


def resolve_privacy_preference_state(
    session: Session,
    browser_session: MutableMapping[str, Any],
    *,
    user_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> PrivacyPreferenceState:
    instant = at or datetime.now(timezone.utc)
    current_notice = _current_cookie_notice(session, instant)
    choice = None
    source_scope = None
    if user_id is not None:
        event = _latest_authenticated_event(session, user_id)
        if event is not None:
            choice = _event_choice(event, user_id)
            source_scope = "ACCOUNT"
            if choice[0] == PrivacyPreferenceDecision.REJECTED:
                _store_browser_choice(browser_session, choice)
    if choice is None:
        choice = _session_choice(browser_session)
        if choice is not None:
            source_scope = "BROWSER"

    decision = choice[0] if choice is not None else None
    telemetry_allowed = False
    if decision == PrivacyPreferenceDecision.GRANTED and current_notice is not None:
        telemetry_allowed = (
            choice[1] == current_notice.version.version_identifier
            and choice[2] == current_notice.version.content_sha256
        )
    show_prompt = current_notice is not None and (
        decision is None
        or (
            decision == PrivacyPreferenceDecision.GRANTED
            and not telemetry_allowed
        )
    )
    return PrivacyPreferenceState(
        decision=decision,
        source_scope=source_scope,
        telemetry_allowed=telemetry_allowed,
        show_prompt=show_prompt,
        current_notice=current_notice,
    )


def is_optional_catalog_telemetry_allowed(
    session: Session,
    browser_session: MutableMapping[str, Any],
    *,
    user_id: uuid.UUID | None = None,
) -> bool:
    return resolve_privacy_preference_state(
        session,
        browser_session,
        user_id=user_id,
    ).telemetry_allowed


def record_privacy_preference(
    session: Session,
    browser_session: MutableMapping[str, Any],
    *,
    decision: PrivacyPreferenceDecision | str,
    source: PrivacyPreferenceSource | str,
    presented_notice_identifier: str | None,
    user_id: uuid.UUID | None = None,
) -> UserPrivacyPreferenceEvent | None:
    try:
        normalized_decision = PrivacyPreferenceDecision(decision)
        normalized_source = PrivacyPreferenceSource(source)
    except (TypeError, ValueError) as exc:
        raise PrivacyPreferenceError("Unsupported privacy preference.") from exc
    occurred_at = datetime.now(timezone.utc)
    notice = _current_cookie_notice(session, occurred_at)
    if normalized_decision == PrivacyPreferenceDecision.GRANTED:
        if notice is None:
            raise PrivacyNoticeUnavailableError(
                "Optional telemetry cannot be enabled until its notice is published."
            )
        if presented_notice_identifier != notice.version.version_identifier:
            raise StalePrivacyNoticeError(
                "The telemetry notice changed and must be reviewed again."
            )

    identifier = notice.version.version_identifier if notice is not None else None
    digest = notice.version.content_sha256 if notice is not None else None
    if user_id is None:
        _store_browser_choice(
            browser_session,
            (normalized_decision, identifier, digest),
        )
        return None

    event = UserPrivacyPreferenceEvent(
        user_id=user_id,
        purpose=CATALOG_TELEMETRY_PURPOSE,
        decision=normalized_decision,
        occurred_at=occurred_at,
        source=normalized_source,
        notice_version_identifier=identifier,
        notice_content_sha256=digest,
    )
    session.add(event)
    session.flush()
    if normalized_decision == PrivacyPreferenceDecision.REJECTED:
        _store_browser_choice(
            browser_session,
            (normalized_decision, identifier, digest),
        )
    return event
