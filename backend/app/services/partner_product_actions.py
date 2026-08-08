from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ProductDraft
from app.models.enums import ProductDraftFileKind, ProductDraftFileStatus, ProductDraftStatus
from app.services.partner_product_categories import require_partner_catalog_store
from app.services.private_storage import private_file_path
from app.services.product_drafts import (
    ProductDraftAccessError,
    ProductDraftStateError,
    ProductDraftValidationError,
    submit_saved_product_draft,
)


MAX_BATCH_DRAFTS = 20
EDITABLE_DRAFT_STATUSES = {
    ProductDraftStatus.DRAFT,
    ProductDraftStatus.INCOMPLETE,
    ProductDraftStatus.READY_FOR_REVIEW,
    ProductDraftStatus.CHANGES_REQUESTED,
}


@dataclass(frozen=True, slots=True)
class DraftActionFailure:
    draft_id: uuid.UUID
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class DraftSubmitBatchResult:
    submitted_ids: tuple[uuid.UUID, ...]
    failures: tuple[DraftActionFailure, ...]


@dataclass(frozen=True, slots=True)
class DraftDeletionSummary:
    draft_ids: tuple[uuid.UUID, ...]
    titles: tuple[str, ...]
    variant_count: int
    image_count: int
    document_count: int


@dataclass(slots=True)
class PreparedDraftDeletion:
    summary: DraftDeletionSummary
    trash_root: Path
    moved_files: list[tuple[Path, Path]]

    def finalize(self) -> None:
        shutil.rmtree(self.trash_root, ignore_errors=True)

    def restore(self) -> None:
        for original, staged in reversed(self.moved_files):
            if not staged.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, original)
        shutil.rmtree(self.trash_root, ignore_errors=True)


def prepare_product_draft_deletion(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_ids: Iterable[uuid.UUID | str],
    root: str | Path,
) -> PreparedDraftDeletion:
    normalized_ids = _normalize_draft_ids(draft_ids)
    store = require_partner_catalog_store(session, user_id)
    drafts = list(
        session.scalars(
            select(ProductDraft)
            .options(selectinload(ProductDraft.files))
            .where(
                ProductDraft.id.in_(normalized_ids),
                ProductDraft.store_id == store.store_id,
            )
            .order_by(ProductDraft.created_at, ProductDraft.id)
            .with_for_update()
        ).all()
    )
    if len(drafts) != len(normalized_ids):
        raise ProductDraftAccessError("No encontramos uno o más borradores seleccionados.")
    blocked = [draft for draft in drafts if draft.status not in EDITABLE_DRAFT_STATUSES]
    if blocked:
        names = ", ".join(_draft_title(draft) for draft in blocked[:3])
        raise ProductDraftStateError(
            f"No se eliminó ningún borrador porque cambió el estado de: {names}."
        )

    root_path = Path(root).resolve()
    trash_root = root_path / ".deleting" / uuid.uuid4().hex
    moved_files: list[tuple[Path, Path]] = []
    image_count = 0
    document_count = 0
    try:
        for draft in drafts:
            for file_record in draft.files:
                if file_record.status == ProductDraftFileStatus.ACTIVE:
                    if file_record.kind == ProductDraftFileKind.IMAGE:
                        image_count += 1
                    else:
                        document_count += 1
                original = private_file_path(root_path, file_record.storage_key)
                if not original.is_file():
                    continue
                staged = trash_root / f"{file_record.id}-{original.name}"
                staged.parent.mkdir(parents=True, exist_ok=True)
                os.replace(original, staged)
                moved_files.append((original, staged))
        summary = DraftDeletionSummary(
            draft_ids=tuple(draft.id for draft in drafts),
            titles=tuple(_draft_title(draft) for draft in drafts),
            variant_count=sum(max(1, len(draft.variants or [])) for draft in drafts),
            image_count=image_count,
            document_count=document_count,
        )
        for draft in drafts:
            session.delete(draft)
        session.flush()
        return PreparedDraftDeletion(
            summary=summary,
            trash_root=trash_root,
            moved_files=moved_files,
        )
    except Exception:
        PreparedDraftDeletion(
            summary=DraftDeletionSummary((), (), 0, 0, 0),
            trash_root=trash_root,
            moved_files=moved_files,
        ).restore()
        raise


def submit_product_draft_batch(
    session: Session,
    *,
    user_id: uuid.UUID,
    draft_ids: Iterable[uuid.UUID | str],
) -> DraftSubmitBatchResult:
    normalized_ids = _normalize_draft_ids(draft_ids)
    store = require_partner_catalog_store(session, user_id)
    drafts = list(
        session.scalars(
            select(ProductDraft).where(
                ProductDraft.id.in_(normalized_ids),
                ProductDraft.store_id == store.store_id,
            )
        ).all()
    )
    if len(drafts) != len(normalized_ids):
        raise ProductDraftAccessError("No encontramos uno o más borradores seleccionados.")
    titles = {draft.id: _draft_title(draft) for draft in drafts}
    submitted: list[uuid.UUID] = []
    failures: list[DraftActionFailure] = []
    for draft_id in normalized_ids:
        try:
            with session.begin_nested():
                draft = submit_saved_product_draft(
                    session,
                    user_id=user_id,
                    draft_id=draft_id,
                )
                session.flush()
                submitted.append(draft.id)
        except ProductDraftValidationError as exc:
            message = next(iter(dict.fromkeys(exc.errors.values())), str(exc))
            failures.append(DraftActionFailure(draft_id, titles[draft_id], message))
        except ProductDraftStateError as exc:
            failures.append(DraftActionFailure(draft_id, titles[draft_id], str(exc)))
    return DraftSubmitBatchResult(tuple(submitted), tuple(failures))


def _normalize_draft_ids(values: Iterable[uuid.UUID | str]) -> tuple[uuid.UUID, ...]:
    normalized: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values:
        try:
            parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ProductDraftAccessError("La selección de borradores no es válida.") from exc
        if parsed not in seen:
            normalized.append(parsed)
            seen.add(parsed)
    if not normalized:
        raise ProductDraftAccessError("Selecciona al menos un borrador.")
    if len(normalized) > MAX_BATCH_DRAFTS:
        raise ProductDraftStateError(
            f"Solo puedes procesar {MAX_BATCH_DRAFTS} borradores por página."
        )
    return tuple(normalized)


def _draft_title(draft: ProductDraft) -> str:
    return str(draft.title or draft.seller_sku or "Producto sin título")
