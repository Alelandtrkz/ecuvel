from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, MutableMapping

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import CatalogInteractionEvent
from app.models.catalog_interaction import CATALOG_EVENT_SURFACES, CATALOG_EVENT_TYPES


logger = logging.getLogger(__name__)
ANONYMOUS_SESSION_KEY = "catalog_anonymous_session_id"
RANKING_CONTEXT_SALT = "ecuvel-catalog-ranking-context-v1"
CLIENT_EVENT_TYPES = frozenset({"IMPRESSION", "CLICK"})


class CatalogTelemetryError(Exception):
    pass


class InvalidRankingContextError(CatalogTelemetryError):
    pass


@dataclass(frozen=True, slots=True)
class RankingContext:
    ranking_request_id: uuid.UUID
    surface: str
    listing_key: str
    product_id: uuid.UUID
    variant_id: uuid.UUID
    offer_id: uuid.UUID
    served_ranker: str
    served_position: int
    shadow_ranker: str | None = None
    shadow_position: int | None = None
    shadow_score: Decimal | None = None


def anonymous_session_id(browser_session: MutableMapping[str, Any]) -> uuid.UUID:
    raw_value = browser_session.get(ANONYMOUS_SESSION_KEY)
    try:
        value = uuid.UUID(str(raw_value))
    except (TypeError, ValueError):
        value = uuid.uuid4()
        browser_session[ANONYMOUS_SESSION_KEY] = str(value)
    return value


def sign_ranking_context(secret_key: str, context: RankingContext) -> str:
    payload = asdict(context)
    for key in ("ranking_request_id", "product_id", "variant_id", "offer_id"):
        payload[key] = str(payload[key])
    if payload["shadow_score"] is not None:
        payload["shadow_score"] = str(payload["shadow_score"])
    return _serializer(secret_key).dumps(payload)


def load_ranking_context(
    secret_key: str,
    token: str,
    *,
    max_age_seconds: int,
) -> RankingContext:
    try:
        payload = _serializer(secret_key).loads(
            token,
            max_age=max_age_seconds,
        )
        shadow_score = payload.get("shadow_score")
        context = RankingContext(
            ranking_request_id=uuid.UUID(str(payload["ranking_request_id"])),
            surface=str(payload["surface"]),
            listing_key=str(payload["listing_key"]),
            product_id=uuid.UUID(str(payload["product_id"])),
            variant_id=uuid.UUID(str(payload["variant_id"])),
            offer_id=uuid.UUID(str(payload["offer_id"])),
            served_ranker=str(payload["served_ranker"]),
            served_position=int(payload["served_position"]),
            shadow_ranker=(
                str(payload["shadow_ranker"])
                if payload.get("shadow_ranker")
                else None
            ),
            shadow_position=(
                int(payload["shadow_position"])
                if payload.get("shadow_position") is not None
                else None
            ),
            shadow_score=(
                Decimal(str(shadow_score)) if shadow_score is not None else None
            ),
        )
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise InvalidRankingContextError("Ranking context inválido o expirado.") from exc
    if (
        context.surface not in CATALOG_EVENT_SURFACES
        or not context.listing_key.strip()
        or context.served_position <= 0
        or (context.shadow_position is not None and context.shadow_position <= 0)
    ):
        raise InvalidRankingContextError("Ranking context inválido o expirado.")
    return context


def record_context_event(
    session: Session,
    *,
    event_type: str,
    context: RankingContext,
    actor_user_id: uuid.UUID | None,
    anonymous_id: uuid.UUID | None,
) -> bool:
    if event_type not in CATALOG_EVENT_TYPES:
        raise CatalogTelemetryError("Tipo de evento de catálogo no permitido.")
    if context.surface not in CATALOG_EVENT_SURFACES:
        raise CatalogTelemetryError("Superficie de catálogo no permitida.")
    event = CatalogInteractionEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        surface=context.surface,
        listing_key=context.listing_key,
        product_id=context.product_id,
        variant_id=context.variant_id,
        offer_id=context.offer_id,
        actor_user_id=actor_user_id,
        anonymous_session_id=anonymous_id,
        ranking_request_id=context.ranking_request_id,
        served_ranker=context.served_ranker,
        served_position=context.served_position,
        shadow_ranker=context.shadow_ranker,
        shadow_position=context.shadow_position,
        shadow_score=context.shadow_score,
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        if event_type == "IMPRESSION":
            return False
        raise
    return True


def record_context_event_best_effort(
    session: Session,
    **kwargs: Any,
) -> bool:
    try:
        recorded = record_context_event(session, **kwargs)
        session.commit()
        return recorded
    except SQLAlchemyError:
        session.rollback()
        logger.warning("Catalog telemetry write failed", exc_info=True)
        return False


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=RANKING_CONTEXT_SALT)
