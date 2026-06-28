from __future__ import annotations

import hashlib

import cv2

from app.services.payment_precheck.normalization import structured_qr_fields
from app.services.payment_precheck.types import PreparedImageSet, QRDecodeResult


def decode_qr_from_variants(
    prepared_images: PreparedImageSet, *, max_payload_chars: int
) -> QRDecodeResult:
    detector = cv2.QRCodeDetector()
    payloads: list[str] = []
    detected = False
    attempts = 0
    for variant in prepared_images.variants:
        attempts += 1
        decoded_this_variant = False
        if hasattr(detector, "detectAndDecodeMulti"):
            try:
                ok, values, points, _ = detector.detectAndDecodeMulti(variant.image)
                detected = detected or points is not None
                if ok:
                    for value in values:
                        if value and len(value) <= max_payload_chars and value not in payloads:
                            payloads.append(value)
                            decoded_this_variant = True
            except cv2.error:
                pass
        if not decoded_this_variant:
            try:
                value, points, _ = detector.detectAndDecode(variant.image)
                detected = detected or points is not None
                if value and len(value) <= max_payload_chars and value not in payloads:
                    payloads.append(value)
            except cv2.error:
                continue

    selected: str | None = None
    ambiguous = False
    if len(payloads) == 1:
        selected = payloads[0]
    elif len(payloads) > 1:
        structured = [
            payload
            for payload in payloads
            if structured_qr_fields(payload, max_chars=max_payload_chars)
        ]
        if len(structured) == 1:
            selected = structured[0]
        else:
            ambiguous = True
    payload_hash = (
        hashlib.sha256(selected.encode("utf-8")).hexdigest() if selected else None
    )
    return QRDecodeResult(
        detected=detected or bool(payloads),
        decoded=bool(payloads),
        payloads=tuple(payloads),
        selected_payload=selected,
        payload_sha256=payload_hash,
        attempt_count=attempts,
        multiple_ambiguous=ambiguous,
        error_code="MULTIPLE_QR_CODES" if ambiguous else None,
    )
