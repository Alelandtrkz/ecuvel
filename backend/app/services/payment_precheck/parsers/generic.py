from __future__ import annotations

import re

from app.services.payment_precheck.normalization import (
    extract_account_suffix,
    extract_labeled_amount_result,
    extract_receipt_number,
    normalize_account_suffix,
    normalize_bank,
    normalize_identifier,
    parse_money,
    parse_transaction_date,
    structured_qr_fields,
)
from app.services.payment_precheck.types import ParsedPaymentProofData


def _first(fields: dict[str, str], *names: str) -> str | None:
    return next((fields[name] for name in names if fields.get(name)), None)


class GenericPaymentProofParser:
    name = "generic"

    def can_parse(self, *, qr_payload: str | None, ocr_text: str) -> bool:
        return True

    def parse(
        self, *, qr_payload: str | None, ocr_text: str, timezone_name: str,
        max_qr_payload_chars: int,
    ) -> ParsedPaymentProofData:
        fields = structured_qr_fields(
            qr_payload or "", max_chars=max_qr_payload_chars
        )
        qr_amount = parse_money(
            _first(fields, "amount", "monto", "value", "valor", "total")
        )
        ocr_amount, amount_ambiguous = extract_labeled_amount_result(ocr_text)
        qr_account = normalize_account_suffix(
            _first(
                fields,
                "destination_account_suffix",
                "account_suffix",
                "cuenta_destino",
            )
        )
        ocr_account = extract_account_suffix(ocr_text)
        qr_receipt = normalize_identifier(
            _first(fields, "receipt_number", "comprobante"), max_length=100
        )
        ocr_receipt = extract_receipt_number(ocr_text)
        bank = normalize_bank(
            _first(fields, "bank", "banco"), explicit_field=True
        )
        if bank is None:
            for line in ocr_text.splitlines():
                bank_match = re.search(
                    r"(?i)\b(?:banco|bank)\s*:?[ ]*"
                    r"([A-Za-zÁÉÍÓÚÑáéíóúñ .]{2,50})",
                    line,
                )
                if bank_match:
                    bank = normalize_bank(
                        bank_match.group(0), explicit_field=True
                    )
                    break
        qr_date = parse_transaction_date(
            _first(fields, "date", "fecha", "transaction_at"), timezone_name
        )
        ocr_date = parse_transaction_date(ocr_text, timezone_name)
        reference = normalize_identifier(
            _first(
                fields,
                "reference",
                "referencia",
                "transaction",
                "transaccion",
            ),
            max_length=150,
        )
        warnings: list[str] = []
        if amount_ambiguous:
            warnings.append("AMOUNT_AMBIGUOUS")
        if qr_amount is not None and ocr_amount is not None and qr_amount != ocr_amount:
            warnings.append("QR_OCR_AMOUNT_MISMATCH")
        if qr_account and ocr_account and qr_account != ocr_account:
            warnings.append("QR_OCR_ACCOUNT_MISMATCH")
        if qr_receipt and ocr_receipt and qr_receipt != ocr_receipt:
            warnings.append("QR_OCR_RECEIPT_MISMATCH")
        return ParsedPaymentProofData(
            parser_name=self.name,
            bank_name=bank,
            amount=qr_amount if qr_amount is not None else ocr_amount,
            transaction_at=qr_date if qr_date is not None else ocr_date,
            destination_account_suffix=qr_account or ocr_account,
            receipt_number=qr_receipt or ocr_receipt,
            transaction_reference=reference,
            qr_amount=qr_amount,
            ocr_amount=ocr_amount,
            qr_account_suffix=qr_account,
            ocr_account_suffix=ocr_account,
            qr_receipt_number=qr_receipt,
            ocr_receipt_number=ocr_receipt,
            warnings=tuple(warnings),
        )
