from __future__ import annotations

import uuid

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from app.extensions import db
from app.models import Order, Product, ProductReview, User
from app.models.enums import ProductReviewStatus
from app.services.product_reviews import (
    ProductReviewModerationError,
    ProductReviewNotFoundError,
    moderate_product_review,
)


@click.command("list-pending-product-reviews")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@with_appcontext
def list_pending_product_reviews(limit: int) -> None:
    rows = db.session.execute(
        select(ProductReview, Order, Product, User)
        .join(Order, Order.id == ProductReview.order_id)
        .join(Product, Product.id == ProductReview.product_id)
        .join(User, User.id == ProductReview.user_id)
        .where(ProductReview.status == ProductReviewStatus.PENDING_REVIEW)
        .order_by(ProductReview.created_at, ProductReview.id)
        .limit(limit)
    ).all()
    if not rows:
        click.echo("No hay reseñas de producto pendientes.")
        return
    for review, order, product, user in rows:
        click.echo(
            f"{review.id} | {order.order_number} | {product.title} | "
            f"{review.rating}/5 | {user.email or user.phone_normalized or user.public_code} | "
            f"{review.created_at.isoformat()}"
        )
        click.echo(f"  {review.body[:180]}")


@click.command("review-product-review")
@click.option("--review-id", type=click.UUID, required=True)
@click.option(
    "--decision",
    type=click.Choice(["approve", "reject"]),
    required=True,
)
@click.option("--reason", default=None, help="Motivo público requerido al rechazar.")
@click.option("--notes", default=None, help="Notas internas opcionales.")
@with_appcontext
def review_product_review_command(
    review_id: uuid.UUID,
    decision: str,
    reason: str | None,
    notes: str | None,
) -> None:
    try:
        with db.session.begin():
            reviewer = db.session.scalar(
                select(User).where(User.email == "admin@ecuvel.local")
            )
            if reviewer is None:
                raise ProductReviewModerationError("No existe admin@ecuvel.local.")
            result = moderate_product_review(
                session=db.session,
                review_id=review_id,
                decision=decision,
                moderator_user_id=reviewer.id,
                reason=reason,
                notes=notes,
            )
    except (ProductReviewModerationError, ProductReviewNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Reseña {result.review_id}: {result.status.value}; "
        f"repetida: {'sí' if result.replayed else 'no'}."
    )
