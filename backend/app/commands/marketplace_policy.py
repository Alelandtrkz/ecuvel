from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import Category, MarketplaceCommissionRule, Store, User
from app.models.enums import StoreStatus
from app.services.marketplace_policy import ensure_store_inventory_location
from app.services.product_publication import (
    MODERATION_CHECKS,
    ProductModerationError,
    record_moderation_decision,
)


# Only codes that exist in the beta taxonomy are included. More-specific matrix
# entries must wait until those leaf categories exist; names are never matched.
INITIAL_CATEGORY_RATES = {
    "ELECTRONICS_PHONES": Decimal("6.00"),
    "ELECTRONICS_COMPUTERS": Decimal("6.00"),
    "ELECTRONICS_HEADPHONES": Decimal("9.00"),
    "ELECTRONICS_CAMERAS": Decimal("8.00"),
    "BEAUTY_COSMETICS": Decimal("8.00"),
    "BEAUTY_SKINCARE": Decimal("10.00"),
    "BEAUTY_PERSONAL_CARE": Decimal("10.00"),
    "HOME_KITCHEN_TOOLS": Decimal("10.00"),
    "HOME_DECORATION": Decimal("12.00"),
    "FASHION_MEN": Decimal("12.00"),
    "FASHION_WOMEN": Decimal("12.00"),
    "FASHION_SHOES": Decimal("12.00"),
    "FASHION_ACCESSORIES": Decimal("12.00"),
    "BABIES_TOYS": Decimal("10.00"),
    "BABIES_CARE": Decimal("10.00"),
    "BABIES_CLOTHING": Decimal("12.00"),
    "AUTOMOTIVE_ACCESSORIES": Decimal("8.00"),
    "AUTOMOTIVE_BASIC_PARTS": Decimal("8.00"),
    "AUTOMOTIVE_TOOLS": Decimal("8.00"),
    "HOME_CLEANING": Decimal("10.00"),
}


@click.group("marketplace-policy")
def marketplace_policy_command() -> None:
    """Administra reglas de comisión y bodegas seller sin usar nombres visibles."""


@marketplace_policy_command.command("list")
@with_appcontext
def list_rules() -> None:
    rows = db.session.execute(
        select(MarketplaceCommissionRule, Category)
        .outerjoin(Category, Category.id == MarketplaceCommissionRule.category_id)
        .where(MarketplaceCommissionRule.store_id.is_(None))
        .order_by(Category.code.nulls_last())
    ).all()
    if not rows:
        click.echo("No hay reglas globales o por categoría.")
        return
    for rule, category in rows:
        click.echo(
            f"{category.code if category else 'GLOBAL'}: "
            f"{rule.commission_rate}% ({'activa' if rule.is_active else 'inactiva'})"
        )


def _upsert_rate(code: str, rate: Decimal) -> str:
    category = db.session.scalar(select(Category).where(Category.code == code))
    if category is None:
        return "missing"
    rule = db.session.scalar(
        select(MarketplaceCommissionRule).where(
            MarketplaceCommissionRule.category_id == category.id,
            MarketplaceCommissionRule.store_id.is_(None),
        )
    )
    if rule is None:
        db.session.add(MarketplaceCommissionRule(
            category_id=category.id,
            store_id=None,
            commission_rate=rate,
            is_active=True,
        ))
        return "created"
    rule.commission_rate = rate
    rule.is_active = True
    return "updated"


@marketplace_policy_command.command("set-category")
@click.argument("category_code")
@click.argument("rate")
@with_appcontext
def set_category_rule(category_code: str, rate: str) -> None:
    try:
        parsed = Decimal(rate)
    except InvalidOperation as exc:
        raise click.ClickException("El porcentaje no es válido.") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > 100:
        raise click.ClickException("El porcentaje debe estar entre 0 y 100.")
    result = _upsert_rate(category_code.strip().upper(), parsed)
    if result == "missing":
        raise click.ClickException("No existe una categoría con ese código.")
    db.session.commit()
    click.echo(f"Regla {result}: {category_code.upper()} = {parsed}%")


@marketplace_policy_command.command("bootstrap")
@with_appcontext
def bootstrap_rules() -> None:
    results = {"created": 0, "updated": 0, "missing": []}
    for code, rate in INITIAL_CATEGORY_RATES.items():
        outcome = _upsert_rate(code, rate)
        if outcome == "missing":
            results["missing"].append(code)
        else:
            results[outcome] += 1
    db.session.commit()
    click.echo(
        f"Reglas creadas: {results['created']}; actualizadas: {results['updated']}."
    )
    if results["missing"]:
        click.echo("Categorías ausentes (no creadas): " + ", ".join(results["missing"]))
    click.echo(
        "La taxonomía beta ELECTRONICS_PHONES agrupa teléfonos y accesorios; "
        "se aplica 6% hasta que existan hojas separadas para accesorios."
    )


@marketplace_policy_command.command("provision-store-inventory")
@click.option("--store-code")
@with_appcontext
def provision_store_inventory(store_code: str | None) -> None:
    query = select(Store).where(
        Store.status == StoreStatus.ACTIVE,
        Store.is_verified.is_(True),
    )
    if store_code:
        query = query.where(Store.public_code == store_code)
    stores = list(db.session.scalars(query))
    if store_code and not stores:
        raise click.ClickException("No existe una tienda activa y verificada con ese código.")
    for store in stores:
        ensure_store_inventory_location(db.session, store=store)
    db.session.commit()
    click.echo(f"Bodegas seller verificadas/provisionadas: {len(stores)}.")


@marketplace_policy_command.command("return-draft-for-commission")
@click.argument("draft_id")
@click.option("--actor-email", required=True)
@with_appcontext
def return_draft_for_commission(draft_id: str, actor_email: str) -> None:
    """Return one legacy SUBMITTED draft so the seller can accept a snapshot."""

    try:
        parsed_draft_id = uuid.UUID(draft_id)
    except ValueError as exc:
        raise click.ClickException("El identificador del borrador no es válido.") from exc
    actor = db.session.scalar(
        select(User).where(User.email_normalized == actor_email.strip().casefold())
    )
    if actor is None or not actor.is_ecuvel_staff or not actor.is_active:
        raise click.ClickException("El actor debe ser un administrador ECUVEL activo.")
    try:
        record_moderation_decision(
            db.session,
            draft_id=parsed_draft_id,
            actor_user_id=actor.id,
            decision="CHANGES_REQUESTED",
            checklist={key: False for key in MODERATION_CHECKS},
            reason_code="OTHER",
            note=(
                "La arquitectura de comisiones cambió. Abre el producto, revisa la "
                "comisión ECUVEL y vuelve a enviarlo para fijarla en esta revisión."
            ),
        )
        db.session.commit()
    except ProductModerationError as exc:
        db.session.rollback()
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Borrador {parsed_draft_id} devuelto a CHANGES_REQUESTED sin recalcular "
        "la comisión silenciosamente."
    )
