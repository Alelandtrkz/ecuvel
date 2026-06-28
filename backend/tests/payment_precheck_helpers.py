from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from app.models import PaymentAttempt
from app.models.enums import PaymentMethod, PaymentStatus
from app.services.payment_precheck.types import PaymentPrecheckConfig
from app.services.payment_proofs import submit_bank_transfer_proof
from app.services.private_storage import stage_payment_proof
from tests.factories import create_catalog_and_stock, create_order_items, reserve_item


def synthetic_receipt_png(
    *, text: str = "BANCO PICHINCHA\nMonto: USD 20.00\nCuenta destino: ****6672\nComprobante: 027756482",
    qr_payload: str | None = None,
    size: tuple[int, int] = (1400, 900),
) -> bytes:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42
        )
    except OSError:
        font = ImageFont.load_default()
    draw.multiline_text((60, 60), text, fill="black", font=font, spacing=20)
    if qr_payload:
        qr = qrcode.make(qr_payload).convert("RGB").resize((420, 420))
        image.paste(qr, (size[0] - 470, size[1] - 470))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def synthetic_qr_png(payload: str) -> bytes:
    output = io.BytesIO()
    qrcode.make(payload).save(output, format="PNG")
    return output.getvalue()


def config_for(root: Path, **overrides) -> PaymentPrecheckConfig:
    values = {
        "enabled": True,
        "analyzer_version": "2",
        "tesseract_languages": "spa+eng",
        "tesseract_timeout_seconds": 5,
        "max_image_pixels": 20_000_000,
        "max_dimension": 4096,
        "ocr_min_word_confidence": 20,
        "low_confidence_threshold": 45,
        "max_age_days": 7,
        "future_tolerance_minutes": 10,
        "timezone_name": "America/Guayaquil",
        "max_qr_payload_chars": 4096,
        "stale_seconds": 120,
        "storage_root": root,
        "expected_account_last4": "6672",
        "allowed_banks": ("BANCO PICHINCHA",),
    }
    values.update(overrides)
    return PaymentPrecheckConfig(**values)


def create_proof(
    session: Session,
    root: Path,
    *,
    data: bytes | None = None,
    filename: str = "proof.png",
    media_type: str = "image/png",
):
    base = create_catalog_and_stock(session, stock=8)
    order_id, order_number, item_ids = create_order_items(session, base, [2])
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    reservation_ids = reserve_item(
        session, base, item_ids[0], expires_at=expires
    )
    attempt = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_PROOF,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"checkout-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=expires,
    )
    session.add(attempt)
    session.flush()
    content = data if data is not None else synthetic_receipt_png()
    staged = stage_payment_proof(
        FileStorage(
            stream=io.BytesIO(content),
            filename=filename,
            content_type=media_type,
        ),
        root=root,
        max_bytes=10 * 1024 * 1024,
    )
    result = submit_bank_transfer_proof(
        session=session,
        payment_attempt_id=attempt.id,
        staged_file=staged,
        upload_idempotency_key=f"upload-{uuid.uuid4().hex}",
        storage_root=root,
        uploaded_by_user_id=base.buyer_id,
    )
    return base, order_id, order_number, reservation_ids, attempt.id, result.proof_id
