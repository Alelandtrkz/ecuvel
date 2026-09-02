from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Product,
    ProductDraft,
    ProductDraftPublication,
    ProductVariant,
    SellerOffer,
)
from app.services.offer_preparation import (
    OfferPreparationValidationError,
    normalize_preparation_time_days,
)


class OfferPreparationBackfillError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OfferPreparationBackfillEntry:
    offer_id: uuid.UUID
    product_slug: str
    status: str
    current_value: int | None
    source_value: object | None = None


@dataclass(frozen=True, slots=True)
class OfferPreparationBackfillReport:
    entries: tuple[OfferPreparationBackfillEntry, ...]

    @property
    def scanned_count(self) -> int:
        return len(self.entries)

    @property
    def populated_count(self) -> int:
        return sum(entry.status == "populated" for entry in self.entries)

    @property
    def candidate_count(self) -> int:
        return sum(entry.status == "candidate" for entry in self.entries)

    @property
    def missing_source_count(self) -> int:
        return sum(entry.status == "missing_source" for entry in self.entries)

    @property
    def invalid_source_count(self) -> int:
        return sum(entry.status == "invalid_source" for entry in self.entries)

    @property
    def untraceable_count(self) -> int:
        return sum(
            entry.status in {
                "untraceable",
                "store_mismatch",
                "product_mismatch",
            }
            for entry in self.entries
        )


@dataclass(frozen=True, slots=True)
class OfferPreparationBackfillResult:
    offer_id: uuid.UUID
    status: str
    preparation_time_days: int | None


def inspect_offer_preparation_backfill(
    session: Session,
) -> OfferPreparationBackfillReport:
    rows = session.execute(
        select(
            SellerOffer,
            Product.id.label("product_id"),
            Product.slug.label("product_slug"),
            ProductDraftPublication,
            ProductDraft,
        )
        .join(ProductVariant, ProductVariant.id == SellerOffer.variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(
            ProductDraftPublication,
            ProductDraftPublication.product_id == Product.id,
        )
        .outerjoin(
            ProductDraft,
            ProductDraft.id == ProductDraftPublication.draft_id,
        )
        .order_by(SellerOffer.created_at, SellerOffer.id)
    ).all()
    entries: list[OfferPreparationBackfillEntry] = []
    for offer, product_id, product_slug, mapping, draft in rows:
        source_value = None
        if offer.preparation_time_days is not None:
            status = "populated"
        elif mapping is None:
            status = "untraceable"
        elif mapping.product_id != product_id:
            status = "product_mismatch"
        elif draft is None:
            status = "missing_source"
        elif draft.store_id != offer.store_id:
            status = "store_mismatch"
        else:
            source_value = (draft.inventory_data or {}).get(
                "preparation_time_days"
            )
            if source_value is None or source_value == "":
                status = "missing_source"
            else:
                try:
                    normalize_preparation_time_days(
                        source_value,
                        required=True,
                    )
                except OfferPreparationValidationError:
                    status = "invalid_source"
                else:
                    status = "candidate"
        entries.append(OfferPreparationBackfillEntry(
            offer_id=offer.id,
            product_slug=product_slug,
            status=status,
            current_value=offer.preparation_time_days,
            source_value=source_value,
        ))
    return OfferPreparationBackfillReport(entries=tuple(entries))


def backfill_offer_preparation_time(
    session: Session,
    *,
    offer_id: uuid.UUID,
) -> OfferPreparationBackfillResult:
    offer = session.scalar(
        select(SellerOffer)
        .where(SellerOffer.id == offer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if offer is None:
        raise OfferPreparationBackfillError("La oferta ya no existe.")
    if offer.preparation_time_days is not None:
        return OfferPreparationBackfillResult(
            offer_id=offer.id,
            status="skipped",
            preparation_time_days=offer.preparation_time_days,
        )

    product_row = session.execute(
        select(Product.id, Product.slug)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id == offer.variant_id)
    ).one_or_none()
    if product_row is None:
        raise OfferPreparationBackfillError(
            "La oferta no tiene un producto demostrable."
        )
    product_id, _product_slug = product_row
    mapping = session.scalar(
        select(ProductDraftPublication).where(
            ProductDraftPublication.product_id == product_id
        )
    )
    if mapping is None or mapping.product_id != product_id:
        raise OfferPreparationBackfillError(
            "La oferta no tiene una publicación demostrable."
        )
    draft = session.get(ProductDraft, mapping.draft_id)
    if draft is None:
        raise OfferPreparationBackfillError(
            "El borrador fuente ya no existe."
        )
    if draft.store_id != offer.store_id:
        raise OfferPreparationBackfillError(
            "La tienda de la oferta no coincide con el borrador fuente."
        )
    try:
        value = normalize_preparation_time_days(
            (draft.inventory_data or {}).get("preparation_time_days"),
            required=True,
        )
    except OfferPreparationValidationError as exc:
        raise OfferPreparationBackfillError(str(exc)) from exc
    offer.preparation_time_days = value
    session.flush()
    session.commit()
    return OfferPreparationBackfillResult(
        offer_id=offer.id,
        status="updated",
        preparation_time_days=value,
    )
