from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from app.config import derive_bank_account_last4
from app.models.enums import (
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
)


@dataclass(frozen=True, slots=True)
class PaymentPrecheckConfig:
    enabled: bool
    analyzer_version: str
    tesseract_languages: str
    tesseract_timeout_seconds: int
    max_image_pixels: int
    max_dimension: int
    ocr_min_word_confidence: int
    low_confidence_threshold: int
    max_age_days: int
    future_tolerance_minutes: int
    timezone_name: str
    max_qr_payload_chars: int
    stale_seconds: int
    storage_root: Path
    expected_account_last4: str | None
    allowed_banks: tuple[str, ...]

    @classmethod
    def from_mapping(cls, values: Any) -> "PaymentPrecheckConfig":
        banks = tuple(values.get("BANK_TRANSFER_ALLOWED_BANKS", ()))
        configured_bank = values.get("BANK_TRANSFER_BANK_NAME")
        if not banks and configured_bank:
            banks = (configured_bank,)
        last4 = derive_bank_account_last4(
            values.get("BANK_TRANSFER_ACCOUNT_NUMBER"),
            values.get("BANK_TRANSFER_ACCOUNT_LAST4"),
        )
        return cls(
            enabled=bool(values["PAYMENT_PRECHECK_ENABLED"]),
            analyzer_version=str(values["PAYMENT_PRECHECK_ANALYZER_VERSION"]),
            tesseract_languages=str(values["PAYMENT_PRECHECK_TESSERACT_LANG"]),
            tesseract_timeout_seconds=int(
                values["PAYMENT_PRECHECK_TESSERACT_TIMEOUT_SECONDS"]
            ),
            max_image_pixels=int(values["PAYMENT_PRECHECK_MAX_IMAGE_PIXELS"]),
            max_dimension=int(values["PAYMENT_PRECHECK_MAX_DIMENSION"]),
            ocr_min_word_confidence=int(
                values["PAYMENT_PRECHECK_OCR_MIN_CONFIDENCE"]
            ),
            low_confidence_threshold=int(
                values["PAYMENT_PRECHECK_LOW_CONFIDENCE_THRESHOLD"]
            ),
            max_age_days=int(values["PAYMENT_PRECHECK_MAX_AGE_DAYS"]),
            future_tolerance_minutes=int(
                values["PAYMENT_PRECHECK_FUTURE_TOLERANCE_MINUTES"]
            ),
            timezone_name=str(values["PAYMENT_PRECHECK_TIMEZONE"]),
            max_qr_payload_chars=int(
                values["PAYMENT_PRECHECK_MAX_QR_PAYLOAD_CHARS"]
            ),
            stale_seconds=int(values["PAYMENT_PRECHECK_STALE_SECONDS"]),
            storage_root=Path(values["PAYMENT_PROOF_UPLOAD_DIR"]),
            expected_account_last4=last4,
            allowed_banks=banks,
        )


@dataclass(frozen=True, slots=True)
class ImageVariant:
    name: str
    image: np.ndarray


@dataclass(frozen=True, slots=True)
class PreparedImageSet:
    variants: tuple[ImageVariant, ...]
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class QRDecodeResult:
    detected: bool
    decoded: bool
    payloads: tuple[str, ...]
    selected_payload: str | None
    payload_sha256: str | None
    attempt_count: int
    multiple_ambiguous: bool
    error_code: str | None


@dataclass(frozen=True, slots=True)
class OCRResult:
    text: str
    mean_confidence: Decimal | None
    word_count: int
    timed_out: bool
    attempt_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ParsedPaymentProofData:
    parser_name: str
    bank_name: str | None
    amount: Decimal | None
    transaction_at: datetime | None
    destination_account_suffix: str | None
    receipt_number: str | None
    transaction_reference: str | None
    qr_amount: Decimal | None
    ocr_amount: Decimal | None
    qr_account_suffix: str | None
    ocr_account_suffix: str | None
    qr_receipt_number: str | None
    ocr_receipt_number: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaymentPrecheckResult:
    outcome: PaymentProofPrecheckOutcome
    findings: tuple[dict[str, str], ...]
    amount_matches: bool | None
    destination_account_matches: bool | None
    date_is_plausible: bool | None
    receipt_appears_unique: bool | None
    qr_ocr_are_consistent: bool | None
    bank_is_recognized: bool | None


@dataclass(frozen=True, slots=True)
class PaymentProofAnalysisResult:
    analysis_id: uuid.UUID
    proof_id: uuid.UUID
    processing_status: PaymentProofAnalysisStatus
    outcome: PaymentProofPrecheckOutcome | None
    run_count: int
    replayed: bool
    executed: bool
