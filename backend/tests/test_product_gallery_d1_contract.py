from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
SCRIPT = (APP_ROOT / "static" / "js" / "product-detail.js").read_text(
    encoding="utf-8"
)
STYLES = (APP_ROOT / "static" / "css" / "product-detail.css").read_text(
    encoding="utf-8"
)
TEMPLATE = (
    APP_ROOT / "templates" / "components" / "product_gallery.html"
).read_text(encoding="utf-8")


def test_gallery_dynamic_thumbnails_use_safe_dom_and_bounded_fallback():
    assert "image.src = item.thumbnailUrl" in SCRIPT
    assert "button.replaceChildren(image)" in SCRIPT
    assert 'innerHTML = `<img src="${' not in SCRIPT
    assert "galleryFallbackStep" in SCRIPT
    assert "galleryFallbackUrl" in SCRIPT


def test_gallery_zoom_pointer_keyboard_and_reset_contract_is_present():
    for token in (
        'addEventListener("pointerdown"',
        'addEventListener("pointermove"',
        'addEventListener("pointercancel"',
        "setPointerCapture",
        "pointerDistance",
        'event.key === "0"',
        '["+", "="]',
        'event.key === "-"',
        "resetZoom();",
        "Math.min(2, zoom.maxZoom)",
    ):
        assert token in SCRIPT


def test_gallery_template_exposes_accessible_controls_and_real_dimensions():
    for token in (
        'aria-labelledby="product-gallery-lightbox-title"',
        'aria-label="Acercar imagen"',
        'aria-label="Alejar imagen"',
        'aria-label="Ajustar imagen"',
        'role="status"',
        "image.master_width",
        "image.thumbnail_width",
        "image.master_url",
        "image.thumbnail_url",
    ):
        assert token in TEMPLATE
    assert 'width="1200" height="1200"' not in TEMPLATE


def test_gallery_responsive_stage_uses_overlay_navigation_and_scoped_touch_action():
    assert ".product-gallery-lightbox__navigation {" in STYLES
    assert "position: absolute;" in STYLES
    assert ".product-gallery-lightbox__viewport" in STYLES
    assert "touch-action: none;" in STYLES
    assert "100dvh" in STYLES
    assert "env(safe-area-inset-left)" in STYLES
    assert ".product-gallery-lightbox__stage > img" not in STYLES
