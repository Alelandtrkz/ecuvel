from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
TEMPLATE = (
    APP_ROOT / "templates" / "components" / "purchase_card.html"
).read_text(encoding="utf-8")
STYLES = (APP_ROOT / "static" / "css" / "product-detail.css").read_text(
    encoding="utf-8"
)
SCRIPT = (APP_ROOT / "static" / "js" / "product-detail.js").read_text(
    encoding="utf-8"
)


def test_purchase_card_uses_one_cart_form_for_both_intents():
    live_template = TEMPLATE.split("{% else %}", maxsplit=1)[1]
    cart_form = live_template.split("</form>", maxsplit=1)[0]

    assert cart_form.count('name="offer_id"') == 1
    assert cart_form.count('name="quantity"') == 1
    assert cart_form.count('name="csrf_token"') == 1
    assert 'name="intent"' in cart_form
    assert 'value="add_to_cart"' in cart_form
    assert 'value="buy_now"' in cart_form
    assert "data-variant-buy-now" in cart_form
    assert "La compra directa estará disponible próximamente." not in TEMPLATE
    assert 'data-notice="La compra directa' not in TEMPLATE


def test_favorite_active_state_reuses_catalog_danger_token_and_fill():
    assert ".purchase-card__favorite.is-active" in STYLES
    assert "color: var(--color-danger);" in STYLES
    assert ".purchase-card__favorite.is-active svg" in STYLES
    assert "fill: currentColor;" in STYLES
    assert 'aria-pressed="{{ \'true\' if product.is_favorite else \'false\' }}"' in TEMPLATE
    assert "Guardado en favoritos" in TEMPLATE


def test_tablet_layout_has_compact_areas_without_row_spanning_action():
    tablet = STYLES.split("@media (max-width: 1199px)", maxsplit=1)[1].split(
        "@media (max-width: 900px)", maxsplit=1
    )[0]

    assert '"cart actions"' in tablet
    assert "grid-area: actions" in tablet
    assert "align-self: start" in tablet
    assert "grid-row: 1 / span 4" not in tablet


def test_cart_javascript_forwards_submit_intent_and_guards_double_submit():
    assert 'form.dataset.submitting === "true"' in SCRIPT
    assert "event.submitter" in SCRIPT
    assert "formData.set(submitter.name, submitter.value)" in SCRIPT
    assert 'form.setAttribute("aria-busy", "true")' in SCRIPT
    assert "button.disabled = true" in SCRIPT
    assert 'credentials: "same-origin"' in SCRIPT
