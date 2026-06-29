from __future__ import annotations

from io import BytesIO

from app.services.public_identifiers import is_product_code


class BarcodeRenderError(Exception):
    pass


def render_product_code128_svg(product_code: str) -> bytes:
    if not is_product_code(product_code):
        raise BarcodeRenderError("El código de producto no es válido.")
    try:
        from barcode import Code128
        from barcode.writer import SVGWriter
    except ModuleNotFoundError as exc:
        raise BarcodeRenderError(
            "El generador de código de barras no está instalado."
        ) from exc
    output = BytesIO()
    Code128(product_code, writer=SVGWriter()).write(
        output,
        options={
            "write_text": True,
            "module_height": 12,
            "font_size": 10,
            "text_distance": 4,
            "quiet_zone": 2,
        },
    )
    return output.getvalue()
