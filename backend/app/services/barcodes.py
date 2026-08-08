from __future__ import annotations

from io import BytesIO

from app.services.public_identifiers import (
    is_product_code,
    is_seller_inbound_package_code,
)


class BarcodeRenderError(Exception):
    pass


def _render_code128_svg(
    value: str,
    *,
    module_height: int,
    font_size: int,
    text_distance: int,
    write_text: bool,
) -> bytes:
    try:
        from barcode import Code128
        from barcode.writer import SVGWriter
    except ModuleNotFoundError as exc:
        raise BarcodeRenderError(
            "El generador de código de barras no está instalado."
        ) from exc
    output = BytesIO()
    Code128(value, writer=SVGWriter()).write(
        output,
        options={
            "write_text": write_text,
            "module_height": module_height,
            "font_size": font_size,
            "text_distance": text_distance,
            "quiet_zone": 2,
        },
    )
    return output.getvalue()


def render_product_code128_svg(product_code: str) -> bytes:
    if not is_product_code(product_code):
        raise BarcodeRenderError("El código de producto no es válido.")
    return _render_code128_svg(
        product_code,
        module_height=12,
        font_size=10,
        text_distance=4,
        write_text=True,
    )


def render_package_code128_svg(package_code: str) -> bytes:
    if not is_seller_inbound_package_code(package_code):
        raise BarcodeRenderError("El código del paquete no es válido.")
    return _render_code128_svg(
        package_code,
        module_height=28,
        font_size=12,
        text_distance=5,
        write_text=False,
    )
