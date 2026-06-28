from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.enums import PaymentProofPrecheckOutcome
from app.services.payment_precheck.normalization import normalize_bank
from app.services.payment_precheck.types import (
    OCRResult,
    ParsedPaymentProofData,
    PaymentPrecheckConfig,
    PaymentPrecheckResult,
    QRDecodeResult,
)


_MESSAGES = {
    "AMOUNT_MATCH": ("info", "El monto detectado coincide con el pedido."),
    "AMOUNT_MISMATCH": ("error", "El monto detectado no coincide con el pedido."),
    "AMOUNT_NOT_DETECTED": ("warning", "No se pudo detectar el monto."),
    "AMOUNT_AMBIGUOUS": ("warning", "Se detectaron varios montos con evidencia equivalente."),
    "DESTINATION_ACCOUNT_MATCH": ("info", "La terminación de cuenta coincide."),
    "DESTINATION_ACCOUNT_MISMATCH": ("error", "La terminación de cuenta no coincide."),
    "DESTINATION_ACCOUNT_NOT_DETECTED": ("warning", "No se detectó la cuenta destino."),
    "ACCOUNT_VALIDATION_NOT_CONFIGURED": ("warning", "La validación de cuenta no está configurada."),
    "DATE_PLAUSIBLE": ("info", "La fecha detectada es plausible."),
    "DATE_BEFORE_ORDER": ("error", "La fecha detectada es anterior al pedido."),
    "DATE_TOO_OLD": ("error", "La fecha detectada es demasiado antigua."),
    "DATE_IN_FUTURE": ("error", "La fecha detectada está en el futuro."),
    "DATE_NOT_DETECTED": ("warning", "No se pudo detectar la fecha."),
    "RECEIPT_UNIQUE": ("info", "No se encontró otro comprobante con la misma referencia."),
    "POSSIBLE_DUPLICATE_RECEIPT": ("warning", "La referencia podría haberse utilizado anteriormente."),
    "RECEIPT_NOT_DETECTED": ("warning", "No se detectó un número de comprobante."),
    "BANK_RECOGNIZED": ("info", "El banco detectado está configurado."),
    "BANK_NOT_RECOGNIZED": ("warning", "El banco no está reconocido en la configuración."),
    "QR_NOT_DETECTED": ("warning", "No se detectó un código QR."),
    "MULTIPLE_QR_CODES": ("warning", "Se detectaron varios códigos QR ambiguos."),
    "LOW_OCR_CONFIDENCE": ("warning", "La confianza media del OCR es baja."),
    "OCR_TIMEOUT": ("warning", "El OCR alcanzó el tiempo máximo permitido."),
    "QR_OCR_CONSISTENT": ("info", "QR y OCR no presentan contradicciones."),
    "QR_OCR_AMOUNT_MISMATCH": ("error", "QR y OCR muestran montos diferentes."),
    "QR_OCR_ACCOUNT_MISMATCH": ("error", "QR y OCR muestran cuentas diferentes."),
    "QR_OCR_RECEIPT_MISMATCH": ("error", "QR y OCR muestran referencias diferentes."),
    "QR_ONLY": ("warning", "Solo el QR aportó datos estructurados."),
    "OCR_ONLY": ("warning", "Solo el OCR aportó datos estructurados."),
    "NO_STRUCTURED_DATA": ("warning", "No se obtuvieron datos estructurados suficientes."),
}


def _finding(code: str) -> dict[str, str]:
    severity, message = _MESSAGES[code]
    return {"code": code, "severity": severity, "message": message}


def compare_payment_proof(
    *,
    parsed_data: ParsedPaymentProofData,
    expected_amount: Decimal,
    order_created_at: datetime,
    expected_account_last4: str | None,
    allowed_banks: tuple[str, ...],
    receipt_is_unique: bool | None,
    qr_result: QRDecodeResult,
    ocr_result: OCRResult,
    config: PaymentPrecheckConfig,
    now: datetime | None = None,
) -> PaymentPrecheckResult:
    findings: list[dict[str, str]] = []
    objective_failure = False
    manual_review = False

    amount_matches = (
        parsed_data.amount == expected_amount if parsed_data.amount is not None else None
    )
    amount_code = (
        "AMOUNT_NOT_DETECTED" if amount_matches is None
        else "AMOUNT_MATCH" if amount_matches else "AMOUNT_MISMATCH"
    )
    findings.append(_finding(amount_code))
    objective_failure |= amount_matches is False
    manual_review |= amount_matches is None

    if expected_account_last4 is None:
        account_matches = None
        findings.append(_finding("ACCOUNT_VALIDATION_NOT_CONFIGURED"))
        manual_review = True
    elif parsed_data.destination_account_suffix is None:
        account_matches = None
        findings.append(_finding("DESTINATION_ACCOUNT_NOT_DETECTED"))
        manual_review = True
    else:
        account_matches = parsed_data.destination_account_suffix == expected_account_last4
        findings.append(_finding(
            "DESTINATION_ACCOUNT_MATCH" if account_matches
            else "DESTINATION_ACCOUNT_MISMATCH"
        ))
        objective_failure |= not account_matches

    effective_now = now or datetime.now(timezone.utc)
    tolerance = timedelta(minutes=config.future_tolerance_minutes)
    if parsed_data.transaction_at is None:
        date_plausible = None
        date_code = "DATE_NOT_DETECTED"
        manual_review = True
    else:
        detected = parsed_data.transaction_at.astimezone(ZoneInfo(config.timezone_name))
        created = order_created_at.astimezone(ZoneInfo(config.timezone_name))
        current = effective_now.astimezone(ZoneInfo(config.timezone_name))
        if detected < created - tolerance:
            date_plausible, date_code = False, "DATE_BEFORE_ORDER"
        elif detected < current - timedelta(days=config.max_age_days):
            date_plausible, date_code = False, "DATE_TOO_OLD"
        elif detected > current + tolerance:
            date_plausible, date_code = False, "DATE_IN_FUTURE"
        else:
            date_plausible, date_code = True, "DATE_PLAUSIBLE"
        objective_failure |= date_plausible is False
    findings.append(_finding(date_code))

    if parsed_data.receipt_number is None:
        receipt_unique = None
        findings.append(_finding("RECEIPT_NOT_DETECTED"))
        manual_review = True
    else:
        receipt_unique = receipt_is_unique
        if receipt_unique is False:
            findings.append(_finding("POSSIBLE_DUPLICATE_RECEIPT"))
            manual_review = True
        else:
            receipt_unique = True
            findings.append(_finding("RECEIPT_UNIQUE"))

    normalized_allowed = {
        normalize_bank(bank, explicit_field=True) for bank in allowed_banks
    }
    bank_recognized = (
        parsed_data.bank_name in normalized_allowed
        if parsed_data.bank_name and normalized_allowed
        else None
    )
    if bank_recognized:
        findings.append(_finding("BANK_RECOGNIZED"))
    else:
        findings.append(_finding("BANK_NOT_RECOGNIZED"))
        manual_review = True

    warning_set = set(parsed_data.warnings)
    if "AMOUNT_AMBIGUOUS" in warning_set:
        findings.append(_finding("AMOUNT_AMBIGUOUS"))
        manual_review = True
    consistency = True
    for code in (
        "QR_OCR_AMOUNT_MISMATCH",
        "QR_OCR_ACCOUNT_MISMATCH",
        "QR_OCR_RECEIPT_MISMATCH",
    ):
        if code in warning_set:
            findings.append(_finding(code))
            consistency = False
            objective_failure = True
    if qr_result.decoded and ocr_result.word_count:
        if consistency:
            findings.append(_finding("QR_OCR_CONSISTENT"))
    elif qr_result.decoded:
        findings.append(_finding("QR_ONLY"))
    elif ocr_result.word_count:
        findings.append(_finding("OCR_ONLY"))
        manual_review = True
    else:
        findings.append(_finding("NO_STRUCTURED_DATA"))
        manual_review = True

    if qr_result.multiple_ambiguous:
        findings.append(_finding("MULTIPLE_QR_CODES"))
        manual_review = True
    elif not qr_result.detected:
        findings.append(_finding("QR_NOT_DETECTED"))
        manual_review = True
    if ocr_result.timed_out:
        findings.append(_finding("OCR_TIMEOUT"))
        manual_review = True
    if (
        ocr_result.mean_confidence is not None
        and ocr_result.mean_confidence < config.low_confidence_threshold
    ):
        findings.append(_finding("LOW_OCR_CONFIDENCE"))
        manual_review = True

    outcome = (
        PaymentProofPrecheckOutcome.FAILED if objective_failure
        else PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW if manual_review
        else PaymentProofPrecheckOutcome.PASSED
    )
    return PaymentPrecheckResult(
        outcome=outcome,
        findings=tuple(findings),
        amount_matches=amount_matches,
        destination_account_matches=account_matches,
        date_is_plausible=date_plausible,
        receipt_appears_unique=receipt_unique,
        qr_ocr_are_consistent=consistency if qr_result.decoded or ocr_result.word_count else None,
        bank_is_recognized=bank_recognized,
    )
