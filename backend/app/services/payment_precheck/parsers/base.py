from __future__ import annotations

from typing import Protocol

from app.services.payment_precheck.types import ParsedPaymentProofData


class PaymentProofParser(Protocol):
    name: str

    def can_parse(self, *, qr_payload: str | None, ocr_text: str) -> bool: ...

    def parse(
        self, *, qr_payload: str | None, ocr_text: str, timezone_name: str,
        max_qr_payload_chars: int,
    ) -> ParsedPaymentProofData: ...
