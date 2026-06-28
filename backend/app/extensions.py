from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


db = SQLAlchemy(
    model_class=Base,
    session_options={
        "expire_on_commit": False,
    },
)

migrate = Migrate(
    compare_type=True,
    render_as_batch=False,
)

csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "auth.login_form"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "warning"

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
)
