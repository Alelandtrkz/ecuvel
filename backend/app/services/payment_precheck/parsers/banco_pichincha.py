from __future__ import annotations

from dataclasses import replace

from app.services.payment_precheck.normalization import (
    normalize_bank,
    structured_qr_fields,
)
from app.services.payment_precheck.parsers.generic import GenericPaymentProofParser
from app.services.payment_precheck.types import ParsedPaymentProofData


class BancoPichinchaParser(GenericPaymentProofParser):
    name = "banco_pichincha"

    def can_parse(self, *, qr_payload: str | None, ocr_text: str) -> bool:
        fields = structured_qr_fields(qr_payload or "", max_chars=4096)
        bank = normalize_bank(
            fields.get("bank") or fields.get("banco"), explicit_field=True
        )
        visible = normalize_bank(ocr_text) or ""
        return bank == "BANCO_PICHINCHA" or "BANCO_PICHINCHA" in visible

    def parse(
        self, *, qr_payload: str | None, ocr_text: str, timezone_name: str,
        max_qr_payload_chars: int,
    ) -> ParsedPaymentProofData:
        parsed = super().parse(
            qr_payload=qr_payload,
            ocr_text=ocr_text,
            timezone_name=timezone_name,
            max_qr_payload_chars=max_qr_payload_chars,
        )
        return replace(parsed, parser_name=self.name, bank_name="BANCO_PICHINCHA")
