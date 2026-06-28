from flask import Flask, g

from app.config import Config
from app.extensions import csrf, db, limiter, login_manager, migrate


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    # Registra todos los modelos en SQLAlchemy.
    from app import models  # noqa: F401
    from app.commands.fulfillment import (
        create_demo_packages,
        handover_demo_order,
        pack_demo_package,
        stage_demo_package,
    )
    from app.commands.inventory import (
        consume_demo_reservation,
        expire_demo_reservations,
        pick_demo_order,
        putaway_demo_stock,
        receive_demo_stock,
        release_demo_reservation,
        reserve_demo_stock,
    )
    from app.commands.seed import seed_demo, seed_product_categories
    from app.commands.payments import (
        analyze_payment_proof_command,
        analyze_pending_payment_proofs,
        cancel_pending_order_command,
        expire_pending_bank_transfer_payments_command,
        list_pending_payment_proofs,
        review_payment_proof_command,
    )
    from app.commands.product_reviews import (
        list_pending_product_reviews,
        review_product_review_command,
    )
    from app.commands.partners import (
        list_store_onboardings_command,
        review_store_onboarding_command,
        show_store_onboarding_command,
    )
    from app.commands.users import (
        create_customer_user_command,
        list_unverified_users_command,
        verify_user_email_command,
    )
    from app.blueprints.auth import auth
    from app.blueprints.account import account
    from app.blueprints.partners import partners
    from app.storefront import storefront

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        try:
            import uuid

            return db.session.get(User, uuid.UUID(user_id))
        except (TypeError, ValueError):
            return None

    @app.before_request
    def clear_cached_login_user() -> None:
        g.pop("_login_user", None)

    app.cli.add_command(seed_demo)
    app.cli.add_command(seed_product_categories)
    app.cli.add_command(receive_demo_stock)
    app.cli.add_command(putaway_demo_stock)
    app.cli.add_command(reserve_demo_stock)
    app.cli.add_command(release_demo_reservation)
    app.cli.add_command(consume_demo_reservation)
    app.cli.add_command(expire_demo_reservations)
    app.cli.add_command(pick_demo_order)
    app.cli.add_command(create_demo_packages)
    app.cli.add_command(pack_demo_package)
    app.cli.add_command(stage_demo_package)
    app.cli.add_command(handover_demo_order)
    app.cli.add_command(list_pending_payment_proofs)
    app.cli.add_command(review_payment_proof_command)
    app.cli.add_command(list_pending_product_reviews)
    app.cli.add_command(review_product_review_command)
    app.cli.add_command(list_store_onboardings_command)
    app.cli.add_command(show_store_onboarding_command)
    app.cli.add_command(review_store_onboarding_command)
    app.cli.add_command(analyze_payment_proof_command)
    app.cli.add_command(analyze_pending_payment_proofs)
    app.cli.add_command(cancel_pending_order_command)
    app.cli.add_command(expire_pending_bank_transfer_payments_command)
    app.cli.add_command(create_customer_user_command)
    app.cli.add_command(verify_user_email_command)
    app.cli.add_command(list_unverified_users_command)

    app.register_blueprint(auth)
    app.register_blueprint(account)
    app.register_blueprint(partners)
    app.register_blueprint(storefront)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
