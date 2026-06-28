from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, PaymentAttempt, PaymentProof, PaymentProofAnalysis
from app.models.enums import (
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
)
from app.services.payment_precheck.comparison import compare_payment_proof
from app.services.payment_precheck.image_processing import (
    ImagePreparationError,
    prepare_image_variants,
)
from app.services.payment_precheck.ocr_engine import run_ocr
from app.services.payment_precheck.parsers import select_parser
from app.services.payment_precheck.qr_decoder import decode_qr_from_variants
from app.services.payment_precheck.types import (
    PaymentPrecheckConfig,
    PaymentProofAnalysisResult,
)
from app.services.private_storage import PrivateStorageError, verify_private_file


logger = logging.getLogger(__name__)


class PaymentPrecheckError(Exception):
    pass


class PaymentProofForAnalysisNotFoundError(PaymentPrecheckError):
    pass


@dataclass(frozen=True, slots=True)
class _AnalysisClaim:
    analysis_id: uuid.UUID
    proof_id: uuid.UUID
    token: uuid.UUID
    media_type: str
    storage_key: str
    size_bytes: int
    sha256: str
    expected_amount: Decimal
    order_created_at: datetime
    run_count: int


@dataclass(frozen=True, slots=True)
class _AnalysisPayload:
    processing_status: PaymentProofAnalysisStatus
    outcome: PaymentProofPrecheckOutcome
    qr_detected: bool = False
    qr_decoded: bool = False
    qr_payload_sha256: str | None = None
    bank_name_detected: str | None = None
    amount_detected: Decimal | None = None
    transaction_at_detected: datetime | None = None
    destination_account_suffix: str | None = None
    receipt_number_detected: str | None = None
    transaction_reference_detected: str | None = None
    ocr_mean_confidence: Decimal | None = None
    ocr_word_count: int = 0
    amount_matches: bool | None = None
    destination_account_matches: bool | None = None
    date_is_plausible: bool | None = None
    receipt_appears_unique: bool | None = None
    qr_ocr_are_consistent: bool | None = None
    bank_is_recognized: bool | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    findings: tuple[dict[str, str], ...] = ()


def _as_result(
    analysis: PaymentProofAnalysis, *, replayed: bool, executed: bool
) -> PaymentProofAnalysisResult:
    return PaymentProofAnalysisResult(
        analysis_id=analysis.id,
        proof_id=analysis.payment_proof_id,
        processing_status=analysis.processing_status,
        outcome=analysis.outcome,
        run_count=analysis.run_count,
        replayed=replayed,
        executed=executed,
    )


def _claim_analysis(
    session: Session,
    *,
    payment_proof_id: uuid.UUID,
    config: PaymentPrecheckConfig,
    force: bool,
    now: datetime,
) -> tuple[_AnalysisClaim | None, PaymentProofAnalysisResult | None]:
    proof = session.scalar(
        select(PaymentProof)
        .where(PaymentProof.id == payment_proof_id)
        .with_for_update()
    )
    if proof is None:
        raise PaymentProofForAnalysisNotFoundError(
            "No existe el comprobante indicado."
        )
    attempt = session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.id == proof.payment_attempt_id)
    )
    order = session.scalar(select(Order).where(Order.id == attempt.order_id)) if attempt else None
    if attempt is None or order is None:
        raise PaymentPrecheckError("El comprobante no tiene un pago y pedido válidos.")
    analysis = session.scalar(
        select(PaymentProofAnalysis)
        .where(PaymentProofAnalysis.payment_proof_id == proof.id)
        .with_for_update()
    )
    if analysis is None:
        analysis = PaymentProofAnalysis(
            payment_proof_id=proof.id,
            processing_status=PaymentProofAnalysisStatus.PENDING,
            analyzer_version=config.analyzer_version,
            run_count=0,
        )
        session.add(analysis)
        session.flush()
    if (
        analysis.processing_status == PaymentProofAnalysisStatus.COMPLETED
        and analysis.analyzer_version == config.analyzer_version
        and not force
    ):
        return None, _as_result(analysis, replayed=True, executed=False)
    if analysis.processing_status == PaymentProofAnalysisStatus.PROCESSING:
        stale = (
            analysis.started_at is None
            or analysis.started_at <= now - timedelta(seconds=config.stale_seconds)
        )
        if not stale:
            return None, _as_result(analysis, replayed=True, executed=False)

    token = uuid.uuid4()
    analysis.processing_status = PaymentProofAnalysisStatus.PROCESSING
    analysis.outcome = None
    analysis.analyzer_version = config.analyzer_version
    analysis.run_count += 1
    analysis.processing_token = token
    analysis.started_at = now
    analysis.completed_at = None
    analysis.failure_code = None
    analysis.failure_message = None
    analysis.findings = []
    session.flush()
    return _AnalysisClaim(
        analysis_id=analysis.id,
        proof_id=proof.id,
        token=token,
        media_type=proof.media_type,
        storage_key=proof.storage_key,
        size_bytes=proof.size_bytes,
        sha256=proof.sha256,
        expected_amount=attempt.amount,
        order_created_at=order.created_at,
        run_count=analysis.run_count,
    ), None


def _failure_payload(code: str, message: str) -> _AnalysisPayload:
    return _AnalysisPayload(
        processing_status=PaymentProofAnalysisStatus.FAILED,
        outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW,
        failure_code=code,
        failure_message=message[:500],
        findings=(
            {
                "code": code,
                "severity": "warning",
                "message": "El análisis automático no pudo completarse.",
            },
        ),
    )


def _receipt_is_unique(
    session_factory: Callable[[], Session],
    *, analysis_id: uuid.UUID, bank: str | None, receipt: str | None,
) -> bool | None:
    if not bank or not receipt:
        return None
    session = session_factory()
    try:
        count = session.scalar(
            select(func.count(PaymentProofAnalysis.id)).where(
                PaymentProofAnalysis.id != analysis_id,
                PaymentProofAnalysis.bank_name_detected == bank,
                PaymentProofAnalysis.receipt_number_detected == receipt,
                PaymentProofAnalysis.processing_status
                == PaymentProofAnalysisStatus.COMPLETED,
            )
        )
        return int(count or 0) == 0
    finally:
        session.close()


def _run_pipeline(
    session_factory: Callable[[], Session],
    *, claim: _AnalysisClaim, config: PaymentPrecheckConfig,
) -> _AnalysisPayload:
    try:
        file_path = verify_private_file(
            root=config.storage_root,
            storage_key=claim.storage_key,
            size_bytes=claim.size_bytes,
            sha256=claim.sha256,
        )
    except PrivateStorageError as exc:
        return _failure_payload("PRIVATE_FILE_INTEGRITY_ERROR", str(exc))
    if claim.media_type == "application/pdf":
        return _AnalysisPayload(
            processing_status=PaymentProofAnalysisStatus.COMPLETED,
            outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW,
            findings=(
                {
                    "code": "PDF_REQUIRES_MANUAL_REVIEW",
                    "severity": "warning",
                    "message": "Los PDF requieren revisión manual en esta versión.",
                },
            ),
        )
    if claim.media_type not in {"image/png", "image/jpeg"}:
        return _failure_payload(
            "UNSUPPORTED_MEDIA_TYPE", "El tipo de archivo no admite análisis automático."
        )
    try:
        prepared = prepare_image_variants(
            file_path=file_path,
            max_pixels=config.max_image_pixels,
            max_dimension=config.max_dimension,
        )
    except ImagePreparationError as exc:
        return _failure_payload(exc.code, str(exc))

    qr_result = decode_qr_from_variants(
        prepared, max_payload_chars=config.max_qr_payload_chars
    )
    ocr_result = run_ocr(
        prepared_images=prepared,
        languages=config.tesseract_languages,
        timeout_seconds=config.tesseract_timeout_seconds,
        min_word_confidence=config.ocr_min_word_confidence,
    )
    parser = select_parser(
        qr_payload=qr_result.selected_payload, ocr_text=ocr_result.text
    )
    parsed = parser.parse(
        qr_payload=qr_result.selected_payload,
        ocr_text=ocr_result.text,
        timezone_name=config.timezone_name,
        max_qr_payload_chars=config.max_qr_payload_chars,
    )
    receipt_unique = _receipt_is_unique(
        session_factory,
        analysis_id=claim.analysis_id,
        bank=parsed.bank_name,
        receipt=parsed.receipt_number,
    )
    comparison = compare_payment_proof(
        parsed_data=parsed,
        expected_amount=claim.expected_amount,
        order_created_at=claim.order_created_at,
        expected_account_last4=config.expected_account_last4,
        allowed_banks=config.allowed_banks,
        receipt_is_unique=receipt_unique,
        qr_result=qr_result,
        ocr_result=ocr_result,
        config=config,
    )
    return _AnalysisPayload(
        processing_status=PaymentProofAnalysisStatus.COMPLETED,
        outcome=comparison.outcome,
        qr_detected=qr_result.detected,
        qr_decoded=qr_result.decoded,
        qr_payload_sha256=qr_result.payload_sha256,
        bank_name_detected=parsed.bank_name,
        amount_detected=parsed.amount,
        transaction_at_detected=parsed.transaction_at,
        destination_account_suffix=parsed.destination_account_suffix,
        receipt_number_detected=parsed.receipt_number,
        transaction_reference_detected=parsed.transaction_reference,
        ocr_mean_confidence=ocr_result.mean_confidence,
        ocr_word_count=ocr_result.word_count,
        amount_matches=comparison.amount_matches,
        destination_account_matches=comparison.destination_account_matches,
        date_is_plausible=comparison.date_is_plausible,
        receipt_appears_unique=comparison.receipt_appears_unique,
        qr_ocr_are_consistent=comparison.qr_ocr_are_consistent,
        bank_is_recognized=comparison.bank_is_recognized,
        findings=comparison.findings,
    )


def _complete_analysis(
    session: Session,
    *, claim: _AnalysisClaim, payload: _AnalysisPayload, completed_at: datetime,
) -> PaymentProofAnalysisResult:
    analysis = session.scalar(
        select(PaymentProofAnalysis)
        .where(PaymentProofAnalysis.id == claim.analysis_id)
        .with_for_update()
    )
    if analysis is None:
        raise PaymentPrecheckError("El análisis desapareció durante el procesamiento.")
    if analysis.processing_token != claim.token:
        return _as_result(analysis, replayed=True, executed=False)
    for field in (
        "processing_status", "outcome", "qr_detected", "qr_decoded",
        "qr_payload_sha256", "bank_name_detected", "amount_detected",
        "transaction_at_detected", "destination_account_suffix",
        "receipt_number_detected", "transaction_reference_detected",
        "ocr_mean_confidence", "ocr_word_count", "amount_matches",
        "destination_account_matches", "date_is_plausible",
        "receipt_appears_unique", "qr_ocr_are_consistent",
        "bank_is_recognized", "failure_code", "failure_message",
    ):
        setattr(analysis, field, getattr(payload, field))
    analysis.findings = list(payload.findings)
    analysis.processing_token = None
    analysis.completed_at = completed_at
    session.flush()
    return _as_result(analysis, replayed=False, executed=True)


def analyze_payment_proof(
    *,
    session_factory: Callable[[], Session],
    payment_proof_id: uuid.UUID,
    config: PaymentPrecheckConfig,
    force: bool = False,
) -> PaymentProofAnalysisResult:
    started_clock = time.monotonic()
    now = datetime.now(timezone.utc)
    claim_session = session_factory()
    try:
        with claim_session.begin():
            claim, existing_result = _claim_analysis(
                claim_session,
                payment_proof_id=payment_proof_id,
                config=config,
                force=force,
                now=now,
            )
        if existing_result is not None:
            return existing_result
    finally:
        claim_session.close()

    assert claim is not None
    try:
        payload = _run_pipeline(session_factory, claim=claim, config=config)
    except Exception:
        logger.error(
            "payment precheck failed proof_id=%s analysis_id=%s",
            claim.proof_id,
            claim.analysis_id,
        )
        payload = _failure_payload(
            "ANALYSIS_INTERNAL_ERROR", "El análisis automático produjo un error interno."
        )

    complete_session = session_factory()
    try:
        with complete_session.begin():
            result = _complete_analysis(
                complete_session,
                claim=claim,
                payload=payload,
                completed_at=datetime.now(timezone.utc),
            )
        logger.info(
            "payment precheck proof_id=%s analysis_id=%s status=%s outcome=%s duration_ms=%d finding_codes=%s",
            claim.proof_id,
            claim.analysis_id,
            result.processing_status.value,
            result.outcome.value if result.outcome else None,
            int((time.monotonic() - started_clock) * 1000),
            [item["code"] for item in payload.findings],
        )
        return result
    finally:
        complete_session.close()
