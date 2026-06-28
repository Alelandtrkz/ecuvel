from __future__ import annotations

import hashlib
import io
import socket

from PIL import Image

from app.services.payment_precheck.image_processing import prepare_image_variants
from app.services.payment_precheck.normalization import structured_qr_fields
from app.services.payment_precheck.parsers import select_parser
from app.services.payment_precheck.qr_decoder import decode_qr_from_variants
from tests.payment_precheck_helpers import synthetic_qr_png, synthetic_receipt_png


def _decode(tmp_path, data: bytes):
    path = tmp_path / "qr.png"
    path.write_bytes(data)
    prepared = prepare_image_variants(
        file_path=path, max_pixels=20_000_000, max_dimension=4096
    )
    return decode_qr_from_variants(prepared, max_payload_chars=4096)


PAYLOAD = "bank=BANCO_PICHINCHA&amount=2.75&date=2026-06-22T10:30:00-05:00&destination_account_suffix=6672&receipt_number=027756482"


def test_decodes_valid_qr(tmp_path):
    result = _decode(tmp_path, synthetic_qr_png(PAYLOAD))
    assert result.detected and result.decoded
    assert result.selected_payload == PAYLOAD


def test_qr_payload_is_hashed(tmp_path):
    result = _decode(tmp_path, synthetic_qr_png(PAYLOAD))
    assert result.payload_sha256 == hashlib.sha256(PAYLOAD.encode()).hexdigest()


def test_qr_amount_is_parsed_as_decimal(tmp_path):
    result = _decode(tmp_path, synthetic_qr_png(PAYLOAD))
    parsed = select_parser(qr_payload=result.selected_payload, ocr_text="").parse(
        qr_payload=result.selected_payload, ocr_text="",
        timezone_name="America/Guayaquil", max_qr_payload_chars=4096,
    )
    assert str(parsed.amount) == "2.75"


def test_qr_account_suffix_is_extracted():
    fields = structured_qr_fields(PAYLOAD, max_chars=4096)
    assert fields["destination_account_suffix"] == "6672"


def test_qr_receipt_number_preserves_leading_zeroes(tmp_path):
    parsed = select_parser(qr_payload=PAYLOAD, ocr_text="").parse(
        qr_payload=PAYLOAD, ocr_text="", timezone_name="America/Guayaquil",
        max_qr_payload_chars=4096,
    )
    assert parsed.receipt_number == "027756482"


def test_missing_qr_requires_no_decoded_payload(tmp_path):
    result = _decode(tmp_path, synthetic_receipt_png())
    assert not result.decoded and result.selected_payload is None


def test_multiple_qr_codes_are_ambiguous(tmp_path):
    left = Image.open(io.BytesIO(synthetic_qr_png("amount=2.75&bank=A"))).convert("RGB")
    right = Image.open(io.BytesIO(synthetic_qr_png("amount=9.00&bank=B"))).convert("RGB")
    canvas = Image.new("RGB", (left.width * 2 + 80, left.height + 40), "white")
    canvas.paste(left, (20, 20)); canvas.paste(right, (left.width + 60, 20))
    output = io.BytesIO(); canvas.save(output, format="PNG")
    result = _decode(tmp_path, output.getvalue())
    assert result.multiple_ambiguous
    assert result.error_code == "MULTIPLE_QR_CODES"


def test_qr_url_is_not_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    payload = "https://bank.invalid/proof?amount=2.75&destination_account_suffix=6672"
    result = _decode(tmp_path, synthetic_qr_png(payload))
    assert result.decoded
    assert structured_qr_fields(result.selected_payload, max_chars=4096)["amount"] == "2.75"
