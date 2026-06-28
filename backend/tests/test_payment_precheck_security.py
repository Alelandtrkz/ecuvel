from __future__ import annotations

import socket

import pytest
from PIL import Image
from sqlalchemy import inspect, select

from app.models import PaymentProof, PaymentProofAnalysis
from app.models.enums import PaymentProofAnalysisStatus
from app.services.payment_precheck import analyze_payment_proof
from app.services.payment_precheck.image_processing import (
    ImagePreparationError,
    prepare_image_variants,
)
from app.services.payment_precheck.normalization import structured_qr_fields
from tests.payment_precheck_helpers import (
    config_for,
    create_proof,
    synthetic_receipt_png,
)


pytestmark = pytest.mark.integration
SECRET_PAYLOAD = "bank=BANCO_PICHINCHA&amount=20.00&destination_account_suffix=6672&receipt_number=SECRET027756482"


def test_analysis_does_not_log_qr_payload(session, session_factory, tmp_path, caplog):
    data = synthetic_receipt_png(qr_payload=SECRET_PAYLOAD)
    _, _, _, _, _, proof_id = create_proof(session, tmp_path, data=data); session.commit()
    analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    assert SECRET_PAYLOAD not in caplog.text


def test_analysis_does_not_log_full_account(session, session_factory, tmp_path, caplog):
    account = "2205306672"
    data = synthetic_receipt_png(text=f"BANCO PICHINCHA\nMonto: 20.00\nCuenta destino: {account}")
    _, _, _, _, _, proof_id = create_proof(session, tmp_path, data=data); session.commit()
    analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    assert account not in caplog.text


def test_analysis_does_not_persist_full_ocr_text(session, session_factory, tmp_path):
    secret = "PERSONAL SECRET OCR CONTENT"
    data = synthetic_receipt_png(text=f"{secret}\nMonto: 20.00")
    _, _, _, _, _, proof_id = create_proof(session, tmp_path, data=data); session.commit()
    analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    session.expire_all(); analysis = session.scalar(select(PaymentProofAnalysis))
    assert "ocr_text" not in inspect(PaymentProofAnalysis).columns
    assert secret not in str(analysis.findings)


def test_large_dimensions_are_downscaled_safely(tmp_path):
    path = tmp_path / "large.png"
    image = Image.new("RGB", (5000, 200), "white")
    for x in range(500, 4500):
        for y in range(70, 130):
            image.putpixel((x, y), (0, 0, 0))
    image.save(path)
    result = prepare_image_variants(file_path=path, max_pixels=2_000_000, max_dimension=1000)
    assert max(result.width, result.height) <= 1000


def test_decompression_bomb_is_handled(tmp_path):
    path = tmp_path / "huge.png"; Image.new("RGB", (100, 100), "gray").save(path)
    with pytest.raises(ImagePreparationError) as captured:
        prepare_image_variants(file_path=path, max_pixels=1000, max_dimension=4096)
    assert captured.value.code == "IMAGE_PIXEL_LIMIT"


def test_qr_http_url_is_not_requested(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")))
    fields = structured_qr_fields("https://bank.invalid/x?amount=2.75", max_chars=4096)
    assert fields == {"amount": "2.75"}


def test_qr_file_url_is_not_opened(tmp_path):
    target = tmp_path / "secret"; target.write_text("do-not-read")
    fields = structured_qr_fields(f"file:///{target}?amount=2.75", max_chars=4096)
    assert fields == {"amount": "2.75"}


def test_unsupported_media_is_not_sent_to_tesseract(session, session_factory, tmp_path, monkeypatch):
    _, _, _, _, _, proof_id = create_proof(session, tmp_path)
    session.get(PaymentProof, proof_id).media_type = "image/gif"
    session.commit()
    monkeypatch.setattr("pytesseract.image_to_data", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("OCR called")))
    result = analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    assert result.processing_status == PaymentProofAnalysisStatus.FAILED
