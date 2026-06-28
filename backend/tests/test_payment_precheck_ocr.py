from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.services.payment_precheck.image_processing import (
    ImagePreparationError,
    prepare_image_variants,
)
from app.services.payment_precheck.normalization import (
    extract_account_suffix,
    extract_labeled_amount,
    extract_receipt_number,
    parse_transaction_date,
)
from app.services.payment_precheck.ocr_engine import run_ocr
from tests.payment_precheck_helpers import synthetic_receipt_png


def test_ocr_extracts_spanish_amount_with_comma():
    assert extract_labeled_amount("Monto: USD 1.250,50") == Decimal("1250.50")


def test_ocr_extracts_amount_with_dot():
    assert extract_labeled_amount("Valor $ 2.75") == Decimal("2.75")


def test_ocr_extracts_spanish_date():
    parsed = parse_transaction_date("22 de junio de 2026", "America/Guayaquil")
    assert parsed == datetime(2026, 6, 22, tzinfo=parsed.tzinfo)


def test_ocr_extracts_destination_account_suffix():
    assert extract_account_suffix("Cuenta destino 2205306672") == "6672"


def test_ocr_extracts_receipt_number():
    assert extract_receipt_number("N.º de comprobante: 027756482") == "027756482"


def test_real_ocr_reads_synthetic_receipt(tmp_path):
    path = tmp_path / "receipt.png"
    path.write_bytes(synthetic_receipt_png(text="BANCO PICHINCHA\nMonto: USD 20.00"))
    prepared = prepare_image_variants(
        file_path=path, max_pixels=20_000_000, max_dimension=4096
    )
    result = run_ocr(
        prepared_images=prepared,
        languages="spa+eng",
        timeout_seconds=5,
        min_word_confidence=10,
    )
    assert result.word_count > 0
    assert "PICHINCHA" in result.text.upper()


def test_ocr_timeout_returns_partial_result(tmp_path, monkeypatch):
    path = tmp_path / "receipt.png"; path.write_bytes(synthetic_receipt_png())
    prepared = prepare_image_variants(
        file_path=path, max_pixels=20_000_000, max_dimension=4096
    )
    monkeypatch.setattr(
        "pytesseract.image_to_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    result = run_ocr(
        prepared_images=prepared, languages="spa+eng", timeout_seconds=1,
        min_word_confidence=30,
    )
    assert result.timed_out and result.error_code == "OCR_TIMEOUT"


def test_ocr_reconstructs_lines_from_tesseract_coordinates(tmp_path, monkeypatch):
    path = tmp_path / "receipt.png"
    path.write_bytes(synthetic_receipt_png())
    prepared = prepare_image_variants(
        file_path=path, max_pixels=20_000_000, max_dimension=4096
    )
    monkeypatch.setattr(
        "pytesseract.image_to_data",
        lambda *_args, **_kwargs: {
            "text": ["Monto:", "$", "2.75", "Comprobante:", "00042"],
            "conf": ["90", "90", "90", "90", "90"],
            "block_num": [1, 1, 1, 1, 1],
            "par_num": [1, 1, 1, 1, 1],
            "line_num": [1, 1, 1, 2, 2],
        },
    )
    result = run_ocr(
        prepared_images=prepared,
        languages="spa+eng",
        timeout_seconds=1,
        min_word_confidence=30,
    )
    assert result.text == "Monto: $ 2.75\nComprobante: 00042"


def test_corrupted_image_marks_preparation_error(tmp_path):
    path = tmp_path / "broken.png"; path.write_bytes(b"not-an-image")
    with pytest.raises(ImagePreparationError) as captured:
        prepare_image_variants(
            file_path=path, max_pixels=20_000_000, max_dimension=4096
        )
    assert captured.value.code == "IMAGE_DECODE_ERROR"
