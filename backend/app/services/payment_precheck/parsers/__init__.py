from app.services.payment_precheck.parsers.banco_pichincha import (
    BancoPichinchaParser,
)
from app.services.payment_precheck.parsers.generic import (
    GenericPaymentProofParser,
)


def select_parser(*, qr_payload: str | None, ocr_text: str):
    specialized = BancoPichinchaParser()
    if specialized.can_parse(qr_payload=qr_payload, ocr_text=ocr_text):
        return specialized
    return GenericPaymentProofParser()


__all__ = ["select_parser"]
