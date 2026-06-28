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
    User,
)
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreContractOtpChannel,
    StoreMemberRole,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    StoreStatus,
    StoreVerificationDecision,
)
from app.services.mail import OutgoingMail, mail_service
from app.services.phone_otp import get_phone_otp_sender, mask_phone
from app.services.private_storage import (
    PrivateStorageError,
    StagedPrivateFile,
    delete_private_file,
    private_file_path,
    promote_private_file,
    stage_payment_proof,
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
    5: StepSpec(5, "Añadir datos para el pago", "Paso 5 de 5", ("bank_account_owner", "bank_account_number", "bank_name", "bank_id_number", "bank_email")),
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
    validate_step(onboarding, step, data, require_documents=False)
    for field in STEPS[step].fields:
        setattr(onboarding, field, _clean(data.get(field)))
    if step == 4 and staged_documents:
        if storage_root is None:
            raise PartnerOnboardingStateError("No se configuró el almacenamiento de documentos.")
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
                    document_type="IDENTITY_OR_BUSINESS",
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
        for field, message in {
            "bank_account_owner": "Ingresa el nombre del titular.",
            "bank_account_number": "Ingresa el número de cuenta.",
            "bank_name": "Ingresa el nombre del banco.",
            "bank_id_number": "Ingresa la cédula del titular.",
            "bank_email": "Ingresa el correo electrónico bancario.",
        }.items():
            _required(errors, values, field, message)
        if values.get("bank_email") and "@" not in values["bank_email"]:
            errors["bank_email"] = "Ingresa un correo electrónico válido."
    if errors:
        raise PartnerOnboardingValidationError("Revisa los datos del paso.", errors)


def submit_for_review(session: Session, user_id) -> StoreOnboarding:
    onboarding = get_or_create_onboarding(session, user_id)
    if not can_edit(onboarding):
        raise PartnerOnboardingStateError("La solicitud ya fue enviada.")
    for step in range(1, 6):
        validate_step(onboarding, step, require_documents=True)
    now = datetime.now(timezone.utc)
    store = onboarding.store
    if store is None:
        store = Store(
            public_code=_public_store_code(),
            name=onboarding.store_name,
            slug=_unique_store_slug(session, onboarding.store_name),
            legal_name=onboarding.store_name,
            tax_id=onboarding.legal_id_number,
            status=StoreStatus.PENDING_REVIEW,
            is_verified=False,
        )
        session.add(store)
        session.flush()
        onboarding.store_id = store.id
        session.add(
            StoreMember(
                store_id=store.id,
                user_id=user_id,
                role=StoreMemberRole.OWNER,
                is_active=True,
            )
        )
    else:
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
) -> StoreOnboarding:
    onboarding = session.get(StoreOnboarding, _uuid(onboarding_id), with_for_update=True)
    if onboarding is None:
        raise PartnerOnboardingStateError("No se encontró la solicitud.")
    now = datetime.now(timezone.utc)
    normalized = decision.strip().lower()
    if normalized == "approve":
        onboarding.status = StoreOnboardingStatus.APPROVED
        onboarding.current_stage = StoreOnboardingStage.CONTRACT_ACCEPTANCE
        onboarding.approved_at = now
        if onboarding.store:
            onboarding.store.status = StoreStatus.PENDING_REVIEW
        db_decision = StoreVerificationDecision.APPROVED
    elif normalized == "corrections":
        onboarding.status = StoreOnboardingStatus.CORRECTIONS_REQUESTED
        onboarding.current_stage = StoreOnboardingStage.VERIFY_DATA
        onboarding.current_step = 1
        onboarding.correction_requested_at = now
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
        )
    )
    session.flush()
    return onboarding


def stage_partner_document(uploaded_file: FileStorage, *, root: str | Path, max_bytes: int) -> StagedPrivateFile:
    try:
        return stage_payment_proof(uploaded_file, root=root, max_bytes=max_bytes)
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
