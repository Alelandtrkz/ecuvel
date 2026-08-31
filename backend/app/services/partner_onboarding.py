from __future__ import annotations

import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from flask import current_app
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from app.models import (
    Store,
    StoreContractAcceptance,
    StoreContractOtpChallenge,
    StoreMember,
    StoreOnboarding,
    StoreOnboardingDocument,
    StoreVerificationReview,
    StoreBankAccountVersion,
    User,
)
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreContractOtpChannel,
    StoreMemberRole,
    StoreOnboardingDocumentStatus,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    StoreStatus,
    StoreVerificationDecision,
)
from app.services.mail import OutgoingMail, mail_service
from app.services.bank_accounts import (
    BankAccountVersionError,
    approve_latest_onboarding_bank_version,
    create_store_bank_account_version,
    onboarding_bank_account_version,
)
from app.services.phone_otp import get_phone_otp_sender, mask_phone
from app.services.marketplace_policy import ensure_store_inventory_location
from app.services.private_storage import (
    PrivateStorageError,
    StagedPrivateFile,
    delete_private_file,
    private_file_path,
    promote_private_file,
    stage_private_upload,
)


class PartnerOnboardingError(Exception):
    pass


class PartnerOnboardingValidationError(PartnerOnboardingError):
    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = dict(errors or {})


class PartnerOnboardingStateError(PartnerOnboardingError):
    pass


@dataclass(frozen=True, slots=True)
class StepSpec:
    step: int
    title: str
    subtitle: str
    fields: tuple[str, ...]


STEPS: dict[int, StepSpec] = {
    1: StepSpec(1, "Revisión de detalles", "Paso 1 de 5", ("store_name", "legal_id_number")),
    2: StepSpec(2, "Agregue la dirección de su entidad legal", "Paso 2 de 5", ("province", "city", "address")),
    3: StepSpec(3, "Añadir información de contacto", "Paso 3 de 5", ("whatsapp_or_nickname",)),
    4: StepSpec(4, "Añadir documentos", "Paso 4 de 5", ()),
    5: StepSpec(5, "Añadir datos para el pago", "Paso 5 de 5", ("bank_email",)),
}

BANK_INPUT_FIELDS = (
    "bank_account_owner",
    "bank_account_number",
    "bank_name",
    "bank_id_number",
)
BANK_CORRECTION_FIELDS = frozenset(BANK_INPUT_FIELDS)

DOCUMENT_TYPES = {
    "IDENTITY_OR_BUSINESS": "Documento de identidad / empresa",
    "IDENTITY_CARD": "Cédula de identidad",
    "PASSPORT": "Pasaporte",
    "COMPANY_REGISTRATION": "Registro mercantil",
    "CORPORATE_ACTS_CERTIFICATE": "Certificado de actos societarios",
    "BANK_CERTIFICATE": "Certificado bancario",
    "OTHER": "Otro documento",
}

ADMIN_APPROVAL_CHECKS = (
    "identity",
    "address_contact",
    "banking",
    "documents",
    "no_pending_corrections",
)

CORRECTION_REASON_LABELS = {
    "DOCUMENT_UNREADABLE": "Documento ilegible",
    "DOCUMENT_INCOMPLETE": "Documento incompleto",
    "DOCUMENT_INCORRECT": "Documento incorrecto",
    "LEGAL_ID_MISMATCH": "Identificación/RUC no coincide",
    "ADDRESS_INCOMPLETE": "Dirección incompleta",
    "CONTACT_INCORRECT": "Datos de contacto incorrectos",
    "BANK_DATA_INCORRECT": "Datos bancarios incorrectos",
    "BANK_OWNER_MISMATCH": "Información del titular no coincide",
    "OTHER": "Otro",
}


def get_or_create_onboarding(session: Session, user_id) -> StoreOnboarding:
    onboarding = session.scalar(
        select(StoreOnboarding).where(StoreOnboarding.user_id == user_id).with_for_update()
    )
    if onboarding is not None:
        return onboarding
    onboarding = StoreOnboarding(user_id=user_id)
    session.add(onboarding)
    session.flush()
    return onboarding


def get_onboarding(session: Session, user_id) -> StoreOnboarding | None:
    return session.scalar(select(StoreOnboarding).where(StoreOnboarding.user_id == user_id))


def can_edit(onboarding: StoreOnboarding) -> bool:
    return onboarding.status in {
        StoreOnboardingStatus.DRAFT,
        StoreOnboardingStatus.CORRECTIONS_REQUESTED,
    }


def ensure_onboarding_store(
    session: Session,
    onboarding: StoreOnboarding,
    *,
    user_id,
) -> Store:
    onboarding = session.scalar(
        select(StoreOnboarding)
        .where(StoreOnboarding.id == onboarding.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if onboarding is None:
        raise PartnerOnboardingStateError("No se encontró la solicitud.")
    store = onboarding.store
    if store is None:
        if not _clean(onboarding.store_name) or not _clean(onboarding.legal_id_number):
            raise PartnerOnboardingValidationError(
                "Completa los datos generales antes de registrar la cuenta bancaria."
            )
        store = Store(
            public_code=_public_store_code(),
            name=onboarding.store_name,
            slug=_unique_store_slug(session, onboarding.store_name),
            legal_name=onboarding.store_name,
            tax_id=onboarding.legal_id_number,
            status=StoreStatus.DRAFT,
            is_verified=False,
        )
        session.add(store)
        session.flush()
        onboarding.store_id = store.id
    member = session.scalar(
        select(StoreMember)
        .where(
            StoreMember.store_id == store.id,
            StoreMember.user_id == user_id,
        )
        .with_for_update()
    )
    if member is None:
        session.add(
            StoreMember(
                store_id=store.id,
                user_id=user_id,
                role=StoreMemberRole.OWNER,
                is_active=True,
            )
        )
    return store


def save_step(
    *,
    session: Session,
    user_id,
    step: int,
    data: Mapping[str, str],
    staged_documents: tuple[StagedPrivateFile, ...] = (),
    storage_root: str | Path | None = None,
) -> StoreOnboarding:
    onboarding = get_or_create_onboarding(session, user_id)
    if not can_edit(onboarding):
        raise PartnerOnboardingStateError("La solicitud ya fue enviada y no puede editarse libremente.")
    if step not in STEPS:
        raise PartnerOnboardingValidationError("Paso no válido.")
    if step > onboarding.current_step + 1:
        raise PartnerOnboardingStateError("Completa los pasos anteriores antes de continuar.")
    if step == 5:
        ensure_onboarding_store(session, onboarding, user_id=user_id)
        current_version = onboarding_bank_account_version(session, onboarding, lock=True)
        correction_requires_replacement = bank_correction_requires_replacement(
            onboarding,
            current_version,
        )
        raw_action = _clean(data.get("bank_action")).lower()
        if raw_action not in {"", "keep", "replace"}:
            raise PartnerOnboardingValidationError(
                "Selecciona si deseas mantener o reemplazar los datos bancarios.",
                {"bank_action": "Selecciona una opción válida."},
            )
        has_bank_bundle = any(_clean(data.get(field)) for field in BANK_INPUT_FIELDS)
        replace_bank = (
            current_version is None
            or raw_action == "replace"
            or (raw_action == "" and has_bank_bundle)
        )
        if correction_requires_replacement and not replace_bank:
            raise PartnerOnboardingValidationError(
                "La corrección bancaria requiere registrar datos nuevos.",
                {"bank_action": "Selecciona cambiar datos bancarios."},
            )
        errors = _bank_step_errors(
            onboarding,
            data,
            require_bundle=replace_bank,
        )
        if errors:
            raise PartnerOnboardingValidationError("Revisa los datos del paso.", errors)
        email = _clean(data.get("bank_email")) or _clean(onboarding.bank_email)
        onboarding.bank_email = email
        if replace_bank:
            try:
                version, _created = create_store_bank_account_version(
                    session,
                    store_id=onboarding.store_id,
                    holder_name=data.get("bank_account_owner", ""),
                    holder_identification=data.get("bank_id_number", ""),
                    bank_name=data.get("bank_name", ""),
                    account_number=data.get("bank_account_number", ""),
                    source_onboarding_id=onboarding.id,
                    actor_user_id=user_id,
                )
            except BankAccountVersionError as exc:
                raise PartnerOnboardingValidationError(
                    str(exc),
                    {"bank_account_number": "Revisa los datos bancarios."},
                ) from exc
            if correction_requires_replacement and current_version is not None and version.id == current_version.id:
                raise PartnerOnboardingValidationError(
                    "La corrección bancaria requiere datos diferentes a los observados.",
                    {"bank_account_number": "Registra una cuenta bancaria distinta."},
                )
    else:
        validate_step(onboarding, step, data, require_documents=False)
        for field in STEPS[step].fields:
            setattr(onboarding, field, _clean(data.get(field)))
    if step == 5:
        try:
            if onboarding_bank_account_version(session, onboarding) is None:
                raise BankAccountVersionError(
                    "No existe una versión bancaria vigente para la solicitud."
                )
        except BankAccountVersionError as exc:
            raise PartnerOnboardingStateError(str(exc)) from exc
    if step == 4 and staged_documents:
        if storage_root is None:
            raise PartnerOnboardingStateError("No se configuró el almacenamiento de documentos.")
        replacement = None
        replacement_id = _optional_uuid(data.get("replaces_document_id"))
        if replacement_id is not None:
            if len(staged_documents) != 1:
                raise PartnerOnboardingValidationError(
                    "Carga un único archivo para reemplazar el documento indicado.",
                    {"documents": "Selecciona un solo archivo de reemplazo."},
                )
            replacement = session.get(StoreOnboardingDocument, replacement_id)
            if (
                replacement is None
                or replacement.onboarding_id != onboarding.id
                or replacement.status != StoreOnboardingDocumentStatus.REJECTED
            ):
                raise PartnerOnboardingValidationError(
                    "El documento a reemplazar no es válido.",
                    {"documents": "Actualiza la página y vuelve a intentarlo."},
                )
        requested_type = _clean(data.get("document_type")).upper()
        document_type = (
            replacement.document_type
            if replacement is not None
            else requested_type if requested_type in DOCUMENT_TYPES else "IDENTITY_OR_BUSINESS"
        )
        for staged in staged_documents:
            promote_private_file(staged, root=storage_root)
            session.add(
                StoreOnboardingDocument(
                    onboarding_id=onboarding.id,
                    storage_key=staged.storage_key,
                    file_name=staged.original_filename,
                    mime_type=staged.media_type,
                    size_bytes=staged.size_bytes,
                    sha256=staged.sha256,
                    document_type=document_type,
                    replaces_document_id=replacement.id if replacement is not None else None,
                )
            )
    onboarding.current_step = max(onboarding.current_step, min(step + 1, 5))
    session.flush()
    return onboarding


def validate_step(
    onboarding: StoreOnboarding,
    step: int,
    data: Mapping[str, str] | None = None,
    *,
    require_documents: bool,
    bank_version: StoreBankAccountVersion | None = None,
) -> None:
    values = {field: getattr(onboarding, field, None) for field in STEPS[step].fields}
    for key, value in (data or {}).items():
        values[key] = _clean(value)
    errors: dict[str, str] = {}
    if step == 1:
        _required(errors, values, "store_name", "Ingresa el nombre de la tienda.")
        _required(errors, values, "legal_id_number", "Ingresa el número de cédula, RUC o identificación.")
        if values.get("legal_id_number") and len(values["legal_id_number"]) < 6:
            errors["legal_id_number"] = "La identificación debe tener una longitud razonable."
    elif step == 2:
        for field, message in {
            "province": "Ingresa la provincia.",
            "city": "Ingresa la ciudad.",
            "address": "Ingresa la dirección de la tienda.",
        }.items():
            _required(errors, values, field, message)
    elif step == 3:
        _required(errors, values, "whatsapp_or_nickname", "Ingresa un WhatsApp o nickname de contacto.")
        contact = values.get("whatsapp_or_nickname") or ""
        if contact and not (_is_reasonable_phone(contact) or re.fullmatch(r"[\w.@-]{3,80}", contact)):
            errors["whatsapp_or_nickname"] = "Ingresa un teléfono ecuatoriano razonable o un nickname válido."
    elif step == 4 and require_documents and not onboarding.documents:
        errors["documents"] = "Sube al menos un documento."
    elif step == 5:
        _required(
            errors,
            values,
            "bank_email",
            "Ingresa el correo electrónico bancario.",
        )
        if values.get("bank_email") and "@" not in values["bank_email"]:
            errors["bank_email"] = "Ingresa un correo electrónico válido."
        if bank_version is None:
            errors["bank_account_number"] = "Registra una cuenta bancaria vigente."
    if errors:
        raise PartnerOnboardingValidationError("Revisa los datos del paso.", errors)


def submit_for_review(session: Session, user_id) -> StoreOnboarding:
    onboarding = get_or_create_onboarding(session, user_id)
    if not can_edit(onboarding):
        raise PartnerOnboardingStateError("La solicitud ya fue enviada.")
    for step in range(1, 5):
        validate_step(onboarding, step, require_documents=True)
    if onboarding.store is None:
        raise PartnerOnboardingStateError(
            "La solicitud no tiene una tienda bancaria asociada. Completa el paso 5."
        )
    bank_version = onboarding_bank_account_version(session, onboarding, lock=True)
    validate_step(
        onboarding,
        5,
        require_documents=True,
        bank_version=bank_version,
    )
    _validate_requested_corrections_resolved(onboarding, bank_version=bank_version)
    now = datetime.now(timezone.utc)
    store = onboarding.store
    store.name = onboarding.store_name
    store.legal_name = onboarding.store_name
    store.tax_id = onboarding.legal_id_number
    store.status = StoreStatus.PENDING_REVIEW
    store.is_verified = False
    onboarding.status = StoreOnboardingStatus.SUBMITTED
    onboarding.current_stage = StoreOnboardingStage.WAITING_VERIFICATION
    onboarding.submitted_at = now
    session.add(
        StoreVerificationReview(
            onboarding_id=onboarding.id,
            decision=StoreVerificationDecision.PENDING,
            comments="Solicitud enviada por el partner.",
        )
    )
    session.flush()
    return onboarding


def review_onboarding(
    *,
    session: Session,
    onboarding_id,
    reviewer_user_id,
    decision: str,
    comments: str | None = None,
    issues: list[dict] | None = None,
    checklist: dict[str, bool] | None = None,
    expected_updated_at: str | None = None,
    require_checklist: bool = False,
) -> StoreOnboarding:
    onboarding = session.get(StoreOnboarding, _uuid(onboarding_id), with_for_update=True)
    if onboarding is None:
        raise PartnerOnboardingStateError("No se encontró la solicitud.")
    if onboarding.status != StoreOnboardingStatus.SUBMITTED:
        raise PartnerOnboardingStateError(
            "La solicitud cambió mientras la revisabas. Actualiza la página."
        )
    if expected_updated_at and onboarding.updated_at.isoformat() != expected_updated_at:
        raise PartnerOnboardingStateError(
            "La solicitud cambió mientras la revisabas. Actualiza la página."
        )
    now = datetime.now(timezone.utc)
    normalized = decision.strip().lower()
    if normalized == "approve":
        if require_checklist and (
            checklist is None
            or any(checklist.get(key) is not True for key in ADMIN_APPROVAL_CHECKS)
        ):
            raise PartnerOnboardingValidationError(
                "Confirma todos los puntos del checklist antes de aprobar."
            )
        if onboarding.store is None:
            raise PartnerOnboardingValidationError(
                "La solicitud no tiene una tienda asociada y no puede aprobarse."
            )
        current_documents = [
            document
            for document in onboarding.documents
            if document.status != StoreOnboardingDocumentStatus.REJECTED
        ]
        if not current_documents:
            raise PartnerOnboardingValidationError(
                "La solicitud no tiene documentación vigente para aprobar."
            )
        unresolved = _unresolved_document_issues(onboarding)
        if unresolved:
            raise PartnerOnboardingValidationError(
                "Aún existen documentos que requieren reemplazo."
            )
        onboarding.status = StoreOnboardingStatus.APPROVED
        onboarding.current_stage = StoreOnboardingStage.CONTRACT_ACCEPTANCE
        onboarding.approved_at = now
        try:
            approve_latest_onboarding_bank_version(
                session,
                onboarding,
                reviewed_at=now,
                reviewer_user_id=_optional_uuid(reviewer_user_id),
            )
        except BankAccountVersionError as exc:
            raise PartnerOnboardingStateError(str(exc)) from exc
        if onboarding.store:
            onboarding.store.status = StoreStatus.PENDING_REVIEW
        for document in onboarding.documents:
            if document.status == StoreOnboardingDocumentStatus.PENDING_REVIEW:
                document.status = StoreOnboardingDocumentStatus.APPROVED
        db_decision = StoreVerificationDecision.APPROVED
    elif normalized == "corrections":
        normalized_issues = _normalize_issues(issues)
        if not normalized_issues:
            raise PartnerOnboardingValidationError(
                "Selecciona al menos una corrección específica."
            )
        document_messages: dict[uuid.UUID, list[str]] = {}
        documents_by_id = {document.id: document for document in onboarding.documents}
        for issue in normalized_issues:
            if issue.get("target_type") != "DOCUMENT":
                continue
            document_id = _optional_uuid(issue.get("target_id"))
            document = documents_by_id.get(document_id)
            if (
                document is None
                or document.onboarding_id != onboarding.id
                or document.status == StoreOnboardingDocumentStatus.REJECTED
            ):
                raise PartnerOnboardingValidationError(
                    "Uno de los documentos seleccionados ya no está disponible."
                )
            messages = document_messages.setdefault(document.id, [])
            message = _clean(issue.get("message"))[:500]
            if message and message not in messages:
                messages.append(message)
        onboarding.status = StoreOnboardingStatus.CORRECTIONS_REQUESTED
        onboarding.current_stage = StoreOnboardingStage.VERIFY_DATA
        onboarding.correction_requested_at = now
        for document_id, messages in document_messages.items():
            document = documents_by_id[document_id]
            document.status = StoreOnboardingDocumentStatus.REJECTED
            document.admin_comment = "\n".join(messages)[:500] or None
        db_decision = StoreVerificationDecision.CORRECTIONS_REQUESTED
    elif normalized == "reject":
        onboarding.status = StoreOnboardingStatus.REJECTED
        onboarding.current_stage = StoreOnboardingStage.WAITING_VERIFICATION
        onboarding.rejected_at = now
        if onboarding.store:
            onboarding.store.status = StoreStatus.REJECTED
        db_decision = StoreVerificationDecision.REJECTED
    else:
        raise PartnerOnboardingValidationError("Decisión no válida.")
    session.add(
        StoreVerificationReview(
            onboarding_id=onboarding.id,
            reviewer_user_id=reviewer_user_id,
            decision=db_decision,
            comments=_clean(comments),
            issues_snapshot=normalized_issues if normalized == "corrections" else None,
            checklist_snapshot=dict(checklist or {}) or None,
        )
    )
    session.flush()
    return onboarding


def stage_partner_document(uploaded_file: FileStorage, *, root: str | Path, max_bytes: int) -> StagedPrivateFile:
    try:
        return stage_private_upload(
            uploaded_file,
            root=root,
            max_bytes=max_bytes,
            allowed_extensions={"jpg", "jpeg", "png", "pdf"},
            storage_prefix="onboarding-documents",
        )
    except PrivateStorageError as exc:
        raise PartnerOnboardingValidationError("El documento no cumple el formato permitido.", {"documents": str(exc)}) from exc


def request_contract_otp(session: Session, user_id) -> StoreContractOtpChallenge:
    onboarding = _approved_onboarding(session, user_id, lock=True)
    user = session.get(User, user_id)
    now = datetime.now(timezone.utc)
    if onboarding.status == StoreOnboardingStatus.APPROVED:
        onboarding.status = StoreOnboardingStatus.CONTRACT_PENDING
    latest = session.scalar(
        select(StoreContractOtpChallenge)
        .where(StoreContractOtpChallenge.onboarding_id == onboarding.id, StoreContractOtpChallenge.consumed_at.is_(None))
        .order_by(StoreContractOtpChallenge.created_at.desc())
        .with_for_update()
    )
    cooldown = current_app.config["PARTNER_CONTRACT_OTP_RESEND_COOLDOWN_SECONDS"]
    if latest and latest.last_sent_at + timedelta(seconds=cooldown) > now:
        raise PartnerOnboardingStateError("Espera antes de solicitar un nuevo código.")
    for challenge in onboarding.contract_otps:
        if challenge.consumed_at is None:
            challenge.consumed_at = now
    code = f"{secrets.randbelow(1_000_000):06d}"
    channel, destination, masked = _contract_destination(user)
    challenge = StoreContractOtpChallenge(
        onboarding_id=onboarding.id,
        channel=channel,
        destination_masked=masked,
        code_hash=_hash_contract_otp(onboarding.id, code),
        expires_at=now + timedelta(seconds=current_app.config["PARTNER_CONTRACT_OTP_TTL_SECONDS"]),
        max_attempts=current_app.config["PARTNER_CONTRACT_OTP_MAX_ATTEMPTS"],
        last_sent_at=now,
    )
    session.add(challenge)
    session.flush()
    if channel == StoreContractOtpChannel.PHONE:
        get_phone_otp_sender().send_code(phone=destination, code=code)
    else:
        mail_service.send(OutgoingMail(to=destination, subject="Código para aceptar contrato ECUVEL Partners", body=f"Tu código es: {code}"))
    return challenge


def accept_contract(
    *,
    session: Session,
    user_id,
    code: str,
    declarations: Mapping[str, str],
    ip_address: str | None,
    user_agent: str | None,
    storage_root: str | Path,
) -> StoreContractAcceptance:
    onboarding = session.scalar(
        select(StoreOnboarding).where(StoreOnboarding.user_id == user_id).with_for_update()
    )
    if (
        onboarding is not None
        and onboarding.status == StoreOnboardingStatus.COMPLETED
        and onboarding.contract_acceptance is not None
        and onboarding.contract_acceptance.status == StoreContractAcceptanceStatus.ACCEPTED
    ):
        return onboarding.contract_acceptance
    if onboarding is None or onboarding.status not in {
        StoreOnboardingStatus.APPROVED,
        StoreOnboardingStatus.CONTRACT_PENDING,
    }:
        raise PartnerOnboardingStateError("El contrato estará disponible cuando la tienda sea aprobada.")
    required = ("truthful", "terms", "fees", "obligations")
    if any(declarations.get(item) != "1" for item in required):
        raise PartnerOnboardingValidationError("Debes aceptar todas las declaraciones.")
    challenge = session.scalar(
        select(StoreContractOtpChallenge)
        .where(
            StoreContractOtpChallenge.onboarding_id == onboarding.id,
            StoreContractOtpChallenge.consumed_at.is_(None),
        )
        .order_by(StoreContractOtpChallenge.created_at.desc())
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.expires_at <= now
        or challenge.attempt_count >= challenge.max_attempts
    ):
        raise PartnerOnboardingValidationError("El código no es válido o ya caducó.")
    if not hmac.compare_digest(challenge.code_hash, _hash_contract_otp(onboarding.id, (code or "").strip())):
        challenge.attempt_count += 1
        raise PartnerOnboardingValidationError("El código no es válido o ya caducó.")
    challenge.verified_at = now
    challenge.consumed_at = now
    acceptance = onboarding.contract_acceptance
    if acceptance is None:
        acceptance = StoreContractAcceptance(onboarding_id=onboarding.id, contract_version=current_app.config["PARTNER_CONTRACT_VERSION"], annex_version=current_app.config["PARTNER_CONTRACT_ANNEX_VERSION"])
        session.add(acceptance)
    acceptance.status = StoreContractAcceptanceStatus.ACCEPTED
    acceptance.accepted_terms = True
    acceptance.otp_verified = True
    acceptance.accepted_at = now
    acceptance.accepted_ip = ip_address
    acceptance.accepted_user_agent = (user_agent or "")[:500] or None
    acceptance.pdf_storage_key = _write_contract_pdf(onboarding, storage_root)
    onboarding.status = StoreOnboardingStatus.COMPLETED
    onboarding.current_stage = StoreOnboardingStage.PRODUCTS
    onboarding.completed_at = now
    if onboarding.store:
        onboarding.store.status = StoreStatus.ACTIVE
        onboarding.store.is_verified = True
        ensure_store_inventory_location(session, store=onboarding.store)
    session.flush()
    return acceptance


def contract_pdf_bytes(onboarding: StoreOnboarding) -> bytes:
    import io

    buffer = io.BytesIO()
    _draw_contract_pdf(buffer, onboarding)
    return buffer.getvalue()


def _write_contract_pdf(onboarding: StoreOnboarding, root: str | Path) -> str:
    storage_key = f"{datetime.now(timezone.utc):%Y/%m}/{uuid.uuid4().hex}.pdf"
    destination = private_file_path(root, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open("wb") as handle:
        _draw_contract_pdf(handle, onboarding)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return storage_key


def _draw_contract_pdf(handle, onboarding: StoreOnboarding) -> None:
    pdf = canvas.Canvas(handle, pagesize=letter)
    text = pdf.beginText(72, 740)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Contrato ECUVEL Partners")
    text.setFont("Helvetica", 10)
    lines = [
        f"Versión: {getattr(current_app, 'config', {}).get('PARTNER_CONTRACT_VERSION', '2026-06')}",
        f"Tienda: {onboarding.store_name or ''}",
        f"Identificación: {onboarding.legal_id_number or ''}",
        "",
        "El partner acepta publicar productos respetando las políticas de ECUVEL,",
        "mantener información veraz y cumplir las tarifas y anexos aplicables.",
    ]
    for line in lines:
        text.textLine(line)
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()


def _approved_onboarding(session: Session, user_id, *, lock: bool) -> StoreOnboarding:
    statement = select(StoreOnboarding).where(StoreOnboarding.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    onboarding = session.scalar(statement)
    if onboarding is None or onboarding.status not in {StoreOnboardingStatus.APPROVED, StoreOnboardingStatus.CONTRACT_PENDING}:
        raise PartnerOnboardingStateError("El contrato estará disponible cuando la tienda sea aprobada.")
    return onboarding


def _contract_destination(user: User) -> tuple[StoreContractOtpChannel, str, str]:
    if user.phone_normalized and user.phone_verified_at:
        return StoreContractOtpChannel.PHONE, user.phone_normalized, mask_phone(user.phone_normalized)
    if user.email and user.email_verified_at:
        return StoreContractOtpChannel.EMAIL, user.email, _mask_email(user.email)
    raise PartnerOnboardingStateError("Verifica un teléfono o correo antes de aceptar el contrato.")


def _hash_contract_otp(onboarding_id, code: str) -> str:
    pepper = current_app.config["PHONE_OTP_PEPPER"]
    return hmac.new(pepper.encode("utf-8"), f"partner-contract:{onboarding_id}:{code}".encode("utf-8"), "sha256").hexdigest()


def _required(errors: dict[str, str], values: Mapping[str, str | None], field: str, message: str) -> None:
    if not _clean(values.get(field)):
        errors[field] = message


def _bank_step_errors(
    onboarding: StoreOnboarding,
    data: Mapping[str, str],
    *,
    require_bundle: bool,
) -> dict[str, str]:
    values = {key: _clean(data.get(key)) for key in (*BANK_INPUT_FIELDS, "bank_email")}
    values["bank_email"] = values["bank_email"] or _clean(onboarding.bank_email)
    errors: dict[str, str] = {}
    if require_bundle:
        for field, message in {
            "bank_account_owner": "Ingresa el nombre del titular.",
            "bank_account_number": "Ingresa el número de cuenta.",
            "bank_name": "Ingresa el nombre del banco.",
            "bank_id_number": "Ingresa la cédula del titular.",
        }.items():
            _required(errors, values, field, message)
    _required(
        errors,
        values,
        "bank_email",
        "Ingresa el correo electrónico bancario.",
    )
    if values.get("bank_email") and "@" not in values["bank_email"]:
        errors["bank_email"] = "Ingresa un correo electrónico válido."
    return errors


def _clean(value) -> str:
    return " ".join(str(value or "").strip().split())


def _is_reasonable_phone(value: str) -> bool:
    digits = "".join(char for char in value if char.isdigit())
    return len(digits) >= 9 and len(digits) <= 15


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return text.strip("-") or f"tienda-{uuid.uuid4().hex[:8]}"


def _unique_store_slug(session: Session, name: str) -> str:
    base = _slugify(name)
    candidate = base
    index = 2
    while session.scalar(select(Store).where(Store.slug == candidate)):
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _public_store_code() -> str:
    return f"STR-{uuid.uuid4().hex[:10].upper()}"


def _uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _optional_uuid(value) -> uuid.UUID | None:
    try:
        return _uuid(value) if value else None
    except (TypeError, ValueError):
        return None


def _normalize_issues(issues: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in issues or []:
        if not isinstance(raw, dict):
            continue
        target_type = _clean(raw.get("target_type")).upper()
        reason_code = _clean(raw.get("reason_code")).upper()[:80]
        message = _clean(raw.get("message"))[:500]
        if target_type not in {"DOCUMENT", "FIELD"} or not reason_code or not message:
            continue
        item = {
            "target_type": target_type,
            "reason_code": reason_code,
            "message": message,
        }
        if target_type == "DOCUMENT":
            target_id = _optional_uuid(raw.get("target_id"))
            if target_id is None:
                continue
            item["target_id"] = str(target_id)
            identity = (target_type, str(target_id), reason_code)
        else:
            field = _clean(raw.get("field"))[:80]
            try:
                step = int(raw.get("step"))
            except (TypeError, ValueError):
                continue
            if not field or step not in STEPS:
                continue
            item.update({"field": field, "step": step})
            identity = (target_type, field, reason_code)
            if field in BANK_CORRECTION_FIELDS:
                version_id = _optional_uuid(raw.get("bank_account_version_id"))
                if version_id is not None:
                    item["bank_account_version_id"] = str(version_id)
            elif "previous_value" in raw:
                item["previous_value"] = _clean(raw.get("previous_value"))
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(item)
    return normalized


def latest_correction_review(onboarding: StoreOnboarding) -> StoreVerificationReview | None:
    return next(
        (
            review
            for review in reversed(onboarding.reviews)
            if review.decision == StoreVerificationDecision.CORRECTIONS_REQUESTED
        ),
        None,
    )


def _unresolved_document_issues(onboarding: StoreOnboarding) -> list[dict]:
    review = latest_correction_review(onboarding)
    unresolved: list[dict] = []
    for issue in (review.issues_snapshot if review else None) or []:
        if issue.get("target_type") != "DOCUMENT":
            continue
        target_id = _optional_uuid(issue.get("target_id"))
        replacement = next(
            (
                document
                for document in onboarding.documents
                if document.replaces_document_id == target_id
                and document.status != StoreOnboardingDocumentStatus.REJECTED
            ),
            None,
        )
        if replacement is None:
            unresolved.append(issue)
    return unresolved


def bank_correction_requires_replacement(
    onboarding: StoreOnboarding,
    bank_version: StoreBankAccountVersion | None,
) -> bool:
    review = latest_correction_review(onboarding)
    for issue in (review.issues_snapshot if review else None) or []:
        if (
            issue.get("target_type") != "FIELD"
            or issue.get("field") not in BANK_CORRECTION_FIELDS
        ):
            continue
        baseline_id = _optional_uuid(issue.get("bank_account_version_id"))
        if baseline_id is not None:
            if bank_version is None or bank_version.id == baseline_id:
                return True
            continue
        if (
            bank_version is None
            or onboarding.correction_requested_at is None
            or bank_version.created_at <= onboarding.correction_requested_at
        ):
            return True
    return False


def unresolved_correction_issues(
    onboarding: StoreOnboarding,
    *,
    bank_version: StoreBankAccountVersion | None = None,
) -> list[dict]:
    review = latest_correction_review(onboarding)
    unresolved = list(_unresolved_document_issues(onboarding))
    for issue in (review.issues_snapshot if review else None) or []:
        if issue.get("target_type") != "FIELD" or "previous_value" not in issue:
            continue
        if _clean(getattr(onboarding, issue.get("field", ""), None)) == _clean(issue.get("previous_value")):
            unresolved.append(issue)
    if bank_correction_requires_replacement(onboarding, bank_version):
        unresolved.extend(
            issue
            for issue in (review.issues_snapshot if review else None) or []
            if issue.get("target_type") == "FIELD"
            and issue.get("field") in BANK_CORRECTION_FIELDS
        )
    return unresolved


def _validate_requested_corrections_resolved(
    onboarding: StoreOnboarding,
    *,
    bank_version: StoreBankAccountVersion | None,
) -> None:
    if onboarding.status != StoreOnboardingStatus.CORRECTIONS_REQUESTED:
        return
    unresolved = _unresolved_document_issues(onboarding)
    if unresolved:
        count = len(unresolved)
        raise PartnerOnboardingValidationError(
            f"Queda {count} corrección pendiente."
            if count == 1
            else f"Quedan {count} correcciones pendientes.",
            {
                "documents": "Aún hay un documento que requiere reemplazo."
                if count == 1
                else f"Aún hay {count} documentos que requieren reemplazo."
            },
        )
    review = latest_correction_review(onboarding)
    unchanged: list[dict] = []
    for issue in (review.issues_snapshot if review else None) or []:
        if issue.get("target_type") != "FIELD" or "previous_value" not in issue:
            continue
        if _clean(getattr(onboarding, issue.get("field", ""), None)) == _clean(issue.get("previous_value")):
            unchanged.append(issue)
    if unchanged:
        steps = sorted({str(item.get("step")) for item in unchanged})
        raise PartnerOnboardingValidationError(
            "Actualiza la información solicitada antes de reenviar.",
            {"corrections": f"Revisa los pasos {', '.join(steps)}."},
        )
    if bank_correction_requires_replacement(onboarding, bank_version):
        raise PartnerOnboardingValidationError(
            "Actualiza la información bancaria solicitada antes de reenviar.",
            {"corrections": "Revisa el paso 5."},
        )
