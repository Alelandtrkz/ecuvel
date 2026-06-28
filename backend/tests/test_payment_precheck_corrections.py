from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from flask import render_template_string

from app.config import derive_bank_account_last4
from app.models.enums import PaymentProofPrecheckOutcome
from app.services.payment_precheck.comparison import compare_payment_proof
from app.services.payment_precheck.normalization import (
    extract_labeled_amount,
    extract_labeled_amount_result,
    extract_receipt_number,
    normalize_bank,
)
from app.services.payment_precheck.parsers.generic import GenericPaymentProofParser
from app.services.payment_precheck.types import OCRResult, QRDecodeResult
from tests.payment_precheck_helpers import config_for


NOW = datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("account", "legacy", "expected"),
    [
        ("00-1234-5678", None, "5678"),
        ("000 000 2608", "2608", "2608"),
        ("AB001234", None, "1234"),
    ],
)
def test_account_suffix_is_derived_from_full_synthetic_account(
    account, legacy, expected
):
    assert derive_bank_account_last4(account, legacy) == expected


@pytest.mark.parametrize(
    ("account", "legacy"),
    [(None, "2608"), ("0012342608", "9999"), ("12345", None)],
)
def test_invalid_or_contradictory_bank_configuration_fails(account, legacy):
    with pytest.raises(RuntimeError):
        derive_bank_account_last4(account, legacy)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Banco Pichincha C.A.", "BANCO_PICHINCHA"),
        ("Banco de Guayaquil S.A.", "BANCO_DE_GUAYAQUIL"),
        ("Banco del Pacífico", "BANCO_DEL_PACIFICO"),
        ("Produbanco", "PRODUBANCO"),
        ("Promérica", "PROMERICA"),
        ("Banco Internacional", "BANCO_INTERNACIONAL"),
        ("Banco Bolivariano", "BANCO_BOLIVARIANO"),
        ("Banco Solidario", "BANCO_SOLIDARIO"),
        ("Banco del Austro", "BANCO_DEL_AUSTRO"),
    ],
)
def test_configured_ecuadorian_banks_have_stable_canonical_names(source, expected):
    assert normalize_bank(source, explicit_field=True) == expected


def test_produbanco_and_promerica_are_distinct():
    assert normalize_bank("Produbanco") != normalize_bank("Promerica")


def test_ambiguous_short_bank_alias_requires_explicit_field():
    assert normalize_bank("Internacional") == "INTERNACIONAL"
    assert normalize_bank("Internacional", explicit_field=True) == "BANCO_INTERNACIONAL"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Monto: $2.75", Decimal("2.75")),
        ("Monto: $ 2.75", Decimal("2.75")),
        ("Valor: USD 2,75", Decimal("2.75")),
        ("Total USD 1,250.50", Decimal("1250.50")),
        ("Total USD 1.250,50", Decimal("1250.50")),
        ("Monto\n$ 1 250,50", Decimal("1250.50")),
    ],
)
def test_amount_extraction_supports_expected_formats(source, expected):
    assert extract_labeled_amount(source) == expected


def test_amount_extraction_reports_equally_supported_ambiguity():
    amount, ambiguous = extract_labeled_amount_result(
        "Monto: $ 2.75\nTotal: $ 3.00"
    )
    assert amount is None
    assert ambiguous is True


def test_account_identifiers_are_not_mistaken_for_amounts():
    assert extract_labeled_amount(
        "Cuenta destino: 0012345678\nCédula: 0912345678"
    ) is None


@pytest.mark.parametrize(
    "label",
    ["N.º", "N°", "Nº", "Nro.", "No.", "Número", "Referencia"],
)
def test_receipt_variants_preserve_leading_zeroes(label):
    assert extract_receipt_number(
        f"{label} de comprobante: 0027756482"
        if label != "Referencia"
        else f"{label}: 0027756482"
    ) == "0027756482"


def test_receipt_value_can_be_on_following_line():
    assert extract_receipt_number("Número de transacción\n0027756482") == "0027756482"


def test_unlabeled_account_and_identity_are_not_receipts():
    assert extract_receipt_number("Cuenta 0012345678\nCédula 0912345678") is None


def test_synthetic_pichincha_style_parser_extracts_structured_evidence():
    parsed = GenericPaymentProofParser().parse(
        qr_payload=None,
        ocr_text=(
            "BANCO PICHINCHA\nTransferencia exitosa\n$ 2.75\n"
            "Cuenta destino: ****6872\nFecha: 22/06/2026\n"
            "N.º de comprobante\n027756482"
        ),
        timezone_name="America/Guayaquil",
        max_qr_payload_chars=4096,
    )
    assert parsed.bank_name == "BANCO_PICHINCHA"
    assert parsed.amount == Decimal("2.75")
    assert parsed.destination_account_suffix == "6872"
    assert parsed.receipt_number == "027756482"
    assert parsed.transaction_at is not None


def test_qr_ocr_receipt_mismatch_is_objective_failure(tmp_path):
    parsed = GenericPaymentProofParser().parse(
        qr_payload=(
            "bank=BANCO_PICHINCHA&amount=20.00&"
            "destination_account_suffix=6672&receipt_number=0001"
        ),
        ocr_text=(
            "BANCO PICHINCHA\nMonto: USD 20.00\n"
            "Cuenta destino: ****6672\nComprobante: 0002"
        ),
        timezone_name="America/Guayaquil",
        max_qr_payload_chars=4096,
    )
    result = compare_payment_proof(
        parsed_data=parsed,
        expected_amount=Decimal("20.00"),
        order_created_at=NOW - timedelta(minutes=5),
        expected_account_last4="6672",
        allowed_banks=("Banco Pichincha",),
        receipt_is_unique=True,
        qr_result=QRDecodeResult(True, True, ("x",), "x", "a" * 64, 1, False, None),
        ocr_result=OCRResult("text", Decimal("90"), 8, False, 2, None),
        config=config_for(tmp_path),
        now=NOW,
    )
    assert result.outcome == PaymentProofPrecheckOutcome.FAILED
    assert any(
        item["code"] == "QR_OCR_RECEIPT_MISMATCH"
        and item["severity"] == "error"
        for item in result.findings
    )


def test_reviewer_macro_only_renders_redacted_structured_values(app):
    with app.test_request_context("/"):
        rendered = render_template_string(
            "{% from 'components/payment_precheck_summary.html' import payment_precheck_summary %}"
            "{{ payment_precheck_summary('FAILED', 'BANCO_PICHINCHA', true, "
            "'USD 45.00', 'USD 2.75', '****2608', '****6872', "
            "'2026-06-22', '027756482', true, "
            "[{'message': 'El monto no coincide.'}]) }}"
        )
    assert "****2608" in rendered and "****6872" in rendered
    assert "Aprobación manual requerida" in rendered
    assert "OCR" not in rendered and "payload" not in rendered
