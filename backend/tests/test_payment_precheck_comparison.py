from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.enums import PaymentProofPrecheckOutcome
from app.services.payment_precheck.comparison import compare_payment_proof
from app.services.payment_precheck.types import (
    OCRResult,
    ParsedPaymentProofData,
    QRDecodeResult,
)
from tests.payment_precheck_helpers import config_for


NOW = datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc)


def _parsed(**changes):
    value = ParsedPaymentProofData(
        parser_name="generic", bank_name="BANCO_PICHINCHA",
        amount=Decimal("20.00"), transaction_at=NOW - timedelta(minutes=1),
        destination_account_suffix="6672", receipt_number="027756482",
        transaction_reference=None, qr_amount=Decimal("20.00"),
        ocr_amount=Decimal("20.00"), qr_account_suffix="6672",
        ocr_account_suffix="6672", qr_receipt_number="027756482",
        ocr_receipt_number="027756482", warnings=(),
    )
    return replace(value, **changes)


def _compare(tmp_path, parsed=None, **kwargs):
    return compare_payment_proof(
        parsed_data=parsed or _parsed(), expected_amount=Decimal("20.00"),
        order_created_at=kwargs.pop("order_created_at", NOW - timedelta(minutes=5)),
        expected_account_last4=kwargs.pop("expected_account_last4", "6672"),
        allowed_banks=("BANCO PICHINCHA",),
        receipt_is_unique=kwargs.pop("receipt_is_unique", True),
        qr_result=QRDecodeResult(True, True, ("x",), "x", "a" * 64, 1, False, None),
        ocr_result=OCRResult("text", Decimal("90"), 10, False, 2, None),
        config=config_for(tmp_path), now=NOW, **kwargs,
    )


def _codes(result):
    return {finding["code"] for finding in result.findings}


def test_precheck_passes_when_required_fields_match(tmp_path):
    assert _compare(tmp_path).outcome == PaymentProofPrecheckOutcome.PASSED


def test_amount_mismatch_produces_failed_outcome(tmp_path):
    result = _compare(tmp_path, _parsed(amount=Decimal("2.75"), qr_amount=Decimal("2.75"), ocr_amount=Decimal("2.75")))
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED
    assert "AMOUNT_MISMATCH" in _codes(result)


def test_destination_account_mismatch_produces_failed_outcome(tmp_path):
    result = _compare(tmp_path, _parsed(destination_account_suffix="9999", qr_account_suffix="9999", ocr_account_suffix="9999"))
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED


def test_missing_amount_requires_manual_review(tmp_path):
    result = _compare(tmp_path, _parsed(amount=None, qr_amount=None, ocr_amount=None))
    assert result.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW


def test_missing_account_requires_manual_review(tmp_path):
    result = _compare(tmp_path, _parsed(destination_account_suffix=None, qr_account_suffix=None, ocr_account_suffix=None))
    assert result.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW


def test_unconfigured_account_can_never_pass(tmp_path):
    result = _compare(tmp_path, expected_account_last4=None)
    assert result.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW
    assert "ACCOUNT_VALIDATION_NOT_CONFIGURED" in _codes(result)


def test_date_before_order_is_flagged(tmp_path):
    result = _compare(tmp_path, _parsed(transaction_at=NOW - timedelta(hours=2)), order_created_at=NOW)
    assert "DATE_BEFORE_ORDER" in _codes(result)
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED


def test_date_too_old_is_flagged(tmp_path):
    result = _compare(tmp_path, _parsed(transaction_at=NOW - timedelta(days=8)), order_created_at=NOW - timedelta(days=9))
    assert "DATE_TOO_OLD" in _codes(result)


def test_future_date_is_flagged(tmp_path):
    result = _compare(tmp_path, _parsed(transaction_at=NOW + timedelta(hours=1)))
    assert "DATE_IN_FUTURE" in _codes(result)


def test_possible_duplicate_receipt_requires_manual_review(tmp_path):
    result = _compare(tmp_path, receipt_is_unique=False)
    assert result.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW
    assert "POSSIBLE_DUPLICATE_RECEIPT" in _codes(result)


def test_qr_and_ocr_amount_mismatch_is_flagged(tmp_path):
    result = _compare(tmp_path, _parsed(warnings=("QR_OCR_AMOUNT_MISMATCH",)))
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED


def test_qr_and_ocr_account_mismatch_is_flagged(tmp_path):
    result = _compare(tmp_path, _parsed(warnings=("QR_OCR_ACCOUNT_MISMATCH",)))
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED
