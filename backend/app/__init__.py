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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app