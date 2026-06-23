from flask import Flask

from app.config import Config
from app.extensions import db, migrate


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)

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
    from app.commands.seed import seed_demo

    app.cli.add_command(seed_demo)
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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
