from __future__ import annotations

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from werkzeug.datastructures import MultiDict

from app.catalog.product_templates import (
    PRODUCT_TEMPLATES,
    condition_applies,
    get_product_template_for_category_code,
    template_key_for_category_code,
    validate_attributes,
    validate_template_registry,
    variant_axes_for_attributes,
)
from app.commands.marketplace_policy import INITIAL_CATEGORY_RATES, PROPOSED_ADULT_CATEGORY_RATES
from app.commands.seed import _PRODUCT_CATEGORY_TREE
from app.models import Category, Product, ProductVariant, SellerOffer, Store
from app.models.enums import SellerCommissionType
from app.services.catalog_listings import load_public_listings
from app.services.catalog_feed import CatalogFeedCursor, catalog_feed_context_hash, sign_catalog_feed_cursor
from app.services.catalog_ranking import SURFACE_SEARCH, ranking_day
from app.services.marketplace_policy import resolve_marketplace_commission
from app.services.partner_product_categories import list_main_categories
from app.services.product_drafts import _apply_form_to_draft
from app.services.product_publication import (
    MODERATION_CHECKS,
    ProductModerationValidationError,
    publish_product_draft,
)
from app.services.product_specifications import build_product_specification_presentation
from app.services.product_variant_builder import build_variant_state
from tests.factories import create_catalog_and_stock
from tests.product_moderation_helpers import (
    create_complete_simple_draft,
    create_store,
    create_user,
)


CANONICAL_ADULT = {
    "ADULT_CONDOMS": ("adult_condoms", "Preservativos", "preservativos", "10.00"),
    "ADULT_LUBRICANTS": ("adult_lubricants", "Lubricantes íntimos", "lubricantes-intimos", "10.00"),
    "ADULT_TOYS": ("adult_toys", "Juguetes íntimos", "juguetes-intimos", "12.00"),
    "ADULT_ANAL": ("adult_anal", "Accesorios anales", "accesorios-anales", "12.00"),
    "ADULT_BDSM": ("adult_bdsm", "Accesorios BDSM", "accesorios-bdsm", "12.00"),
    "ADULT_LINGERIE": ("adult_lingerie", "Lencería y disfraces", "lenceria-y-disfraces", "12.00"),
    "ADULT_INTIMATE_CARE": ("adult_intimate_care", "Cuidado íntimo", "cuidado-intimo", "10.00"),
    "ADULT_GAMES": ("adult_games", "Juegos y artículos para adultos", "juegos-y-articulos-para-adultos", "10.00"),
}


def _fields(key):
    return {field.key: field for field in PRODUCT_TEMPLATES[key].fields}


def _applicable(key, product_type, **attributes):
    values = {"tipo_producto": product_type, **attributes}
    return {
        field.key for field in PRODUCT_TEMPLATES[key].fields
        if condition_applies(field.condition, values)
    }


def _axes(key, product_type):
    return {
        axis.key for axis in variant_axes_for_attributes(
            PRODUCT_TEMPLATES[key], {"tipo_producto": product_type}
        )
    }


def _spec_items(code, attributes):
    row = SimpleNamespace(
        category_code=code, category_name="Productos para adultos",
        product_brand="Marca", product_model_number="Modelo",
        manufacturer_barcode=None, weight_grams=None, length_mm=None,
        width_mm=None, height_mm=None, variant_attributes=attributes,
    )
    return {
        item.key: item
        for section in build_product_specification_presentation(row).sections
        for item in section.items
    }


def test_adult_registry_seed_order_and_proposed_rates():
    validate_template_registry()
    assert len(PRODUCT_TEMPLATES) == 57
    assert template_key_for_category_code("ADULT") is None
    assert "adult" not in PRODUCT_TEMPLATES
    parent, children = next(entry for entry in _PRODUCT_CATEGORY_TREE if entry[0]["code"] == "ADULT")
    assert parent == {
        "code": "ADULT", "name": "Productos para adultos", "slug": "productos-para-adultos",
        "sort_order": 7, "is_active": False,
    }
    assert [child["code"] for child in children] == list(CANONICAL_ADULT)
    assert set(PROPOSED_ADULT_CATEGORY_RATES) == set(CANONICAL_ADULT)
    for order, (code, (key, name, slug, rate)) in enumerate(CANONICAL_ADULT.items(), 1):
        assert children[order - 1] == {
            "code": code, "name": name, "slug": slug, "sort_order": order, "is_active": False,
        }
        template = PRODUCT_TEMPLATES[key]
        assert get_product_template_for_category_code(code) is template
        assert template.category_code == "ADULT"
        assert template.subcategory_code == code
        assert template.required_documents == ()
        assert _fields(key)["tipo_producto"].required is True
        assert INITIAL_CATEGORY_RATES[code] == PROPOSED_ADULT_CATEGORY_RATES[code] == Decimal(rate)


@pytest.mark.parametrize(("key", "expected"), (
    ("adult_condoms", ("Preservativo externo", "Preservativo interno", "Otro tipo de preservativo")),
    ("adult_lubricants", ("Lubricante base agua", "Lubricante base silicona", "Lubricante híbrido", "Otro lubricante íntimo")),
    ("adult_toys", ("Vibrador", "Masajeador íntimo", "Estimulador", "Masturbador", "Consolador", "Anillo vibrador", "Juguete para parejas", "Muñeca o figura adulta", "Otro")),
    ("adult_anal", ("Plug", "Dilatador", "Set de accesorios", "Estimulador", "Otro")),
    ("adult_bdsm", ("Antifaz", "Esposas o sujeciones", "Kit de accesorios", "Accesorio decorativo", "Otros accesorios no peligrosos")),
    ("adult_lingerie", ("Conjunto de lencería", "Body", "Babydoll", "Corsé", "Medias", "Disfraz para adultos", "Accesorio de vestuario", "Otro")),
    ("adult_intimate_care", ("Limpiador íntimo", "Gel de higiene", "Limpiador de juguetes", "Accesorio de limpieza", "Bolsa o estuche", "Otro")),
    ("adult_games", ("Juego de cartas", "Juego de mesa", "Dados", "Kit de regalo", "Artículo recreativo", "Otro")),
))
def test_exact_adult_product_types_use_stable_field_types(key, expected):
    template = PRODUCT_TEMPLATES[key]
    assert _fields(key)["tipo_producto"].options == expected
    assert {field.type for field in template.fields}.isdisjoint({
        "repeater", "compatibility_table", "size_table", "file", "document", "date",
    })


@pytest.mark.parametrize(("key", "product_type", "included", "excluded", "axes"), (
    ("adult_condoms", "Preservativo externo", {"material", "ancho_nominal_mm", "cantidad_paquete", "lubricado", "fabricante"}, {"volumen_ml", "fecha_caducidad", "lote"}, {"cantidad_paquete", "ancho_nominal_mm"}),
    ("adult_condoms", "Preservativo interno", {"material", "cantidad_paquete"}, {"ancho_nominal_mm"}, {"cantidad_paquete"}),
    ("adult_condoms", "Otro tipo de preservativo", {"material", "cantidad_paquete"}, {"ancho_nominal_mm"}, set()),
    ("adult_lubricants", "Lubricante base agua", {"base_lubricante", "volumen_ml", "ingredientes", "compatibilidad_declarada"}, {"cantidad_piezas", "voltaje_v"}, {"volumen_ml"}),
    ("adult_toys", "Vibrador", {"fuente_energia", "recargable", "modos_funcionamiento", "longitud_cm", "diametro_mm"}, {"tamano"}, {"color_principal"}),
    ("adult_toys", "Consolador", {"tamano", "longitud_cm", "diametro_mm"}, {"fuente_energia", "modos_funcionamiento", "voltaje_v", "tipo_carga"}, {"color_principal", "tamano"}),
    ("adult_toys", "Muñeca o figura adulta", {"material_principal", "cantidad_piezas"}, {"fuente_energia", "diametro_mm", "tamano"}, {"color_principal"}),
    ("adult_anal", "Plug", {"tamano", "longitud_cm", "diametro_max_mm", "tipo_base"}, {"voltaje_v"}, {"color_principal", "tamano"}),
    ("adult_anal", "Set de accesorios", {"cantidad_piezas", "instrucciones_cuidado"}, {"tamano", "longitud_cm", "tipo_base"}, set()),
    ("adult_bdsm", "Antifaz", {"talla", "ajustable"}, {"longitud_cm"}, {"color_principal", "talla"}),
    ("adult_bdsm", "Kit de accesorios", {"cantidad_piezas"}, {"talla", "ajustable", "longitud_cm"}, {"color_principal"}),
    ("adult_lingerie", "Body", {"talla", "genero", "composicion", "cuidados", "incluye_piezas"}, {"voltaje_v", "volumen_ml"}, {"color_principal", "talla"}),
    ("adult_intimate_care", "Gel de higiene", {"volumen_ml", "ingredientes", "aroma", "presentacion"}, {"material_principal", "dimensiones", "cantidad_piezas"}, {"volumen_ml"}),
    ("adult_intimate_care", "Bolsa o estuche", {"material_principal", "dimensiones", "cantidad_piezas"}, {"volumen_ml", "ingredientes", "aroma", "presentacion"}, set()),
    ("adult_games", "Juego de cartas", {"numero_jugadores", "cantidad_piezas", "idioma", "contenido_paquete"}, {"volumen_ml", "voltaje_v"}, set()),
    ("adult_games", "Kit de regalo", {"cantidad_piezas", "contenido_paquete"}, {"numero_jugadores"}, set()),
))
def test_family_field_and_variant_matrix(key, product_type, included, excluded, axes):
    applicable = _applicable(key, product_type)
    assert included <= applicable
    assert excluded.isdisjoint(applicable)
    assert _axes(key, product_type) == axes


@pytest.mark.parametrize("product_type", ("Limpiador íntimo", "Gel de higiene", "Limpiador de juguetes", "Accesorio de limpieza", "Bolsa o estuche", "Otro"))
def test_all_intimate_care_types_keep_physical_and_formulation_fields_separate(product_type):
    applicable = _applicable("adult_intimate_care", product_type)
    formulation = {"volumen_ml", "ingredientes", "aroma", "presentacion"}
    physical = {"material_principal", "dimensiones", "cantidad_piezas"}
    if product_type in {"Limpiador íntimo", "Gel de higiene", "Limpiador de juguetes"}:
        assert formulation <= applicable
        assert physical.isdisjoint(applicable)
    else:
        assert physical <= applicable
        assert formulation.isdisjoint(applicable)


@pytest.mark.parametrize(("source", "voltage", "charge"), (
    (None, False, False), ("No requiere", False, False), ("Pilas", False, False),
    ("Batería recargable", True, True), ("Red eléctrica", True, False),
))
def test_toy_electrical_fields_follow_energy_source(source, voltage, charge):
    applicable = _applicable("adult_toys", "Vibrador", fuente_energia=source)
    assert ("voltaje_v" in applicable) is voltage
    assert ("tipo_carga" in applicable) is charge
    assert {"voltaje_v", "tipo_carga"}.isdisjoint(_axes("adult_toys", "Vibrador"))


def test_lingerie_reuses_fashion_vocabularies():
    fields = _fields("adult_lingerie")
    fashion = _fields("fashion_clothing")
    for key in ("genero", "sistema_talla", "material_principal", "cuidados"):
        assert fields[key].options == fashion[key].options
    assert fields["cuidados"].type == "multiselect"


@pytest.mark.parametrize(("key", "product_type", "field_key", "unit"), (
    ("adult_condoms", "Preservativo externo", "ancho_nominal_mm", "mm"),
    ("adult_lubricants", "Lubricante base agua", "volumen_ml", "ml"),
    ("adult_toys", "Consolador", "longitud_cm", "cm"),
    ("adult_anal", "Plug", "diametro_max_mm", "mm"),
    ("adult_intimate_care", "Gel de higiene", "volumen_ml", "ml"),
    ("adult_intimate_care", "Bolsa o estuche", "dimensiones", "cm"),
))
def test_positive_decimal_contracts(key, product_type, field_key, unit):
    template = PRODUCT_TEMPLATES[key]
    assert _fields(key)[field_key].unit == unit
    assert f"attributes.{field_key}" in validate_attributes(template, {"tipo_producto": product_type, field_key: "0"}, final=True)
    assert validate_attributes(template, {"tipo_producto": product_type, field_key: "0.01"}, final=True) == {}


def test_integer_bounds_voltage_and_no_automatic_compatibility_claims():
    assert "attributes.cantidad_paquete" in validate_attributes(
        PRODUCT_TEMPLATES["adult_condoms"], {"tipo_producto": "Preservativo externo", "cantidad_paquete": "0"}, final=True,
    )
    assert "attributes.numero_jugadores" in validate_attributes(
        PRODUCT_TEMPLATES["adult_games"], {"tipo_producto": "Juego de mesa", "numero_jugadores": "1.5"}, final=True,
    )
    assert _fields("adult_toys")["voltaje_v"].unit == "V"
    assert "attributes.voltaje_v" in validate_attributes(
        PRODUCT_TEMPLATES["adult_toys"], {"tipo_producto": "Vibrador", "fuente_energia": "Red eléctrica", "voltaje_v": "0"}, final=True,
    )
    items = _spec_items("ADULT_LUBRICANTS", {"tipo_producto": "Lubricante base agua", "base_lubricante": "Agua"})
    assert "compatibilidad_declarada" not in items


@pytest.mark.parametrize(("code", "attributes", "expected", "excluded"), (
    ("ADULT_CONDOMS", {"tipo_producto": "Preservativo externo", "ancho_nominal_mm": "52", "cantidad_paquete": 3}, {"ancho_nominal_mm", "cantidad_paquete"}, {"lote"}),
    ("ADULT_LUBRICANTS", {"tipo_producto": "Lubricante híbrido", "volumen_ml": "50", "compatibilidad_declarada": "Declaración del fabricante"}, {"volumen_ml", "compatibilidad_declarada"}, set()),
    ("ADULT_TOYS", {"tipo_producto": "Vibrador", "fuente_energia": "No requiere", "voltaje_v": "5", "modos_funcionamiento": ["Modo 1"]}, {"modos_funcionamiento"}, {"voltaje_v"}),
    ("ADULT_ANAL", {"tipo_producto": "Plug", "diametro_max_mm": "25"}, {"diametro_max_mm"}, set()),
    ("ADULT_BDSM", {"tipo_producto": "Antifaz", "talla": "M", "ajustable": True}, {"talla", "ajustable"}, set()),
    ("ADULT_LINGERIE", {"tipo_producto": "Body", "talla": "M", "cuidados": ["Lavar a mano"]}, {"talla", "cuidados"}, set()),
    ("ADULT_INTIMATE_CARE", {"tipo_producto": "Bolsa o estuche", "dimensiones": "20", "volumen_ml": "50", "ingredientes": "stale"}, {"dimensiones"}, {"volumen_ml", "ingredientes"}),
    ("ADULT_GAMES", {"tipo_producto": "Juego de mesa", "numero_jugadores": 2, "contenido_paquete": ["Tablero"]}, {"numero_jugadores", "contenido_paquete"}, set()),
))
def test_specification_metadata_resolves_without_a_public_listing(code, attributes, expected, excluded):
    items = _spec_items(code, attributes)
    assert expected <= items.keys()
    assert excluded.isdisjoint(items)


@pytest.mark.parametrize(("key", "product_type", "stale_axis"), (
    ("adult_condoms", "Preservativo interno", "ancho_nominal_mm"),
    ("adult_toys", "Vibrador", "tamano"),
    ("adult_anal", "Set de accesorios", "tamano"),
    ("adult_bdsm", "Kit de accesorios", "talla"),
    ("adult_intimate_care", "Bolsa o estuche", "volumen_ml"),
))
def test_type_changes_reject_stale_variant_axes(key, product_type, stale_axis):
    configuration, rows, errors = build_variant_state(
        form=MultiDict({"has_variants": "1", "variant_configuration": json.dumps({"version": 3, "axes": [{"key": stale_axis}]})}),
        template=PRODUCT_TEMPLATES[key], attributes={"tipo_producto": product_type},
        product_code="TEST-ADULT", existing_configuration=None, existing_variants=None, final=False,
    )
    assert configuration == {}
    assert rows == []
    assert "no permitido" in errors["variants"]


@pytest.mark.integration
def test_seed_is_idempotent_and_keeps_all_adult_categories_inactive(app, session):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-product-categories"])
    assert first.exit_code == 0, first.output
    rows = session.scalars(select(Category).where(Category.code.in_({"ADULT", *CANONICAL_ADULT}))).all()
    assert len(rows) == 9
    parent = next(row for row in rows if row.code == "ADULT")
    assert all(not row.is_active for row in rows)
    assert all(row.parent_id == parent.id for row in rows if row.code != "ADULT")
    assert "ADULT" not in {category.code for category in list_main_categories(session)}
    # Repeated bootstrap must never accidentally activate the staged category.
    second = runner.invoke(args=["seed-product-categories"])
    assert second.exit_code == 0, second.output
    session.expire_all()
    assert all(not row.is_active for row in rows)


@pytest.mark.integration
def test_proposed_commissions_use_existing_percentage_and_minimum_rules(app, session):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    assert runner.invoke(args=["marketplace-policy", "bootstrap"]).exit_code == 0
    for code, (*_metadata, rate) in CANONICAL_ADULT.items():
        category = session.scalar(select(Category).where(Category.code == code))
        normal = resolve_marketplace_commission(session, category_id=category.id, price="10.00")
        assert normal.mode == SellerCommissionType.PERCENTAGE
        assert normal.rate_percent == Decimal(rate)
        assert normal.commission_amount == Decimal(rate) / 10
        minimum = resolve_marketplace_commission(session, category_id=category.id, price="0.26")
        assert minimum.mode == SellerCommissionType.FIXED
        assert minimum.commission_amount == Decimal("0.25")
        assert category.is_active is False


@pytest.mark.integration
@pytest.mark.parametrize("code", CANONICAL_ADULT)
def test_inactive_adult_leaf_blocks_public_listing_and_detail(app, session, code):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-product-categories"]).exit_code == 0
    base = create_catalog_and_stock(session, stock=4)
    offer = session.get(SellerOffer, base.offer_id)
    variant = session.get(ProductVariant, offer.variant_id)
    product = session.get(Product, variant.product_id)
    category = session.scalar(select(Category).where(Category.code == code))
    product.category_id = category.id
    product.title = "PP18A_PRIVATE_MARKER"
    session.commit()
    assert load_public_listings(session, product_id=product.id) == []
    client = app.test_client()
    assert client.get(f"/productos/{product.slug}").status_code == 404
    store = session.get(Store, base.store_id)
    for url in ("/", "/?q=PP18A_PRIVATE_MARKER", f"/?category={category.slug}", f"/tiendas/{store.slug}"):
        response = client.get(url)
        assert response.status_code == 200
        assert product.slug not in response.get_data(as_text=True)
    assert "Productos para adultos" not in client.get("/").get_data(as_text=True)
    cursor = sign_catalog_feed_cursor(app.config["SECRET_KEY"], CatalogFeedCursor(
        version=1, ranking_day=ranking_day(), surface=SURFACE_SEARCH,
        context_hash=catalog_feed_context_hash(surface=SURFACE_SEARCH, query="PP18A_PRIVATE_MARKER"),
        category_slug=None, store_slug=None, next_position=0,
        ranking_request_id=uuid.uuid4(), batch_size=20,
    ))
    feed = client.get("/catalogo/feed", query_string={"q": "PP18A_PRIVATE_MARKER", "cursor": cursor})
    assert feed.status_code == 200
    assert product.slug not in feed.get_data(as_text=True)


@pytest.mark.integration
@pytest.mark.parametrize("code", CANONICAL_ADULT)
def test_existing_publication_guard_blocks_adult_drafts(app, session, tmp_path, code):
    assert app.test_cli_runner().invoke(args=["seed-product-categories"]).exit_code == 0
    parent = session.scalar(select(Category).where(Category.code == "ADULT"))
    child = session.scalar(select(Category).where(Category.code == code))
    seller = create_user(session)
    store = create_store(session)
    draft = create_complete_simple_draft(session, seller=seller, store=store, category=parent, subcategory=child, media_root=tmp_path / "drafts")
    draft.template_key = CANONICAL_ADULT[code][0]
    draft.attributes = {"tipo_producto": _fields(draft.template_key)["tipo_producto"].options[0]}
    if draft.template_key == "adult_lingerie":
        draft.attributes = {**draft.attributes, "talla": "M"}
    session.flush()
    with pytest.raises(ProductModerationValidationError, match="categoría seleccionada no está activa"):
        publish_product_draft(
            session, draft_id=draft.id, actor_user_id=seller.id,
            checklist={key: True for key in MODERATION_CHECKS},
            source_media_root=tmp_path / "drafts", catalog_media_root=tmp_path / "catalog",
        )
    assert session.scalar(select(func.count()).select_from(Product)) == 0


@pytest.mark.integration
def test_changing_care_type_clears_posted_formulation_values(app, session, tmp_path):
    assert app.test_cli_runner().invoke(args=["seed-product-categories"]).exit_code == 0
    parent = session.scalar(select(Category).where(Category.code == "ADULT"))
    child = session.scalar(select(Category).where(Category.code == "ADULT_INTIMATE_CARE"))
    seller = create_user(session)
    draft = create_complete_simple_draft(session, seller=seller, store=create_store(session), category=parent, subcategory=child, media_root=tmp_path)
    draft.template_key = "adult_intimate_care"
    draft.attributes = {"tipo_producto": "Gel de higiene", "volumen_ml": "50", "ingredientes": "Declarados"}
    _apply_form_to_draft(draft, PRODUCT_TEMPLATES[draft.template_key], MultiDict({
        "attributes[tipo_producto]": "Bolsa o estuche",
        "attributes[volumen_ml]": "50", "attributes[ingredientes]": "stale",
        "attributes[aroma]": "stale", "attributes[presentacion]": "Botella",
        "attributes[material_principal]": "Tela", "attributes[dimensiones]": "20",
    }), final=False)
    assert draft.attributes["tipo_producto"] == "Bolsa o estuche"
    assert {"volumen_ml", "ingredientes", "aroma", "presentacion"}.isdisjoint(draft.attributes)
