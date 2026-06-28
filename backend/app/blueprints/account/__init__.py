from __future__ import annotations

from datetime import date

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db, limiter
from app.models.enums import PhoneOtpPurpose
from app.services.phone_otp import (
    PhoneOtpCooldownError,
    PhoneOtpError,
    request_phone_otp,
)
from app.services.mail import OutgoingMail, mail_service
from app.services.user_profiles import (
    ProfileError,
    change_password,
    confirm_email_change,
    create_password,
    request_email_change,
    update_profile,
)


account = Blueprint("account", __name__)


GENDER_OPTIONS = (
    ("", "Sin especificar"),
    ("male", "Masculino"),
    ("female", "Femenino"),
    ("other", "Otro"),
    ("prefer_not_to_say", "Prefiero no decirlo"),
)


def _parse_birth_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _send_email_change(new_email: str, token: str) -> None:
    link = url_for("account.confirm_email_change_route", token=token, _external=True)
    mail_service.send(
        OutgoingMail(
            to=new_email,
            subject="Confirma tu nuevo correo en Ecuvel",
            body=f"Confirma el cambio de correo con este enlace:\n{link}",
        )
    )


@account.get("/perfil")
@login_required
def profile():
    return render_template(
        "account/profile.html",
        gender_options=GENDER_OPTIONS,
        current_section="profile",
    )


@account.post("/perfil/datos")
@login_required
def update_profile_route():
    user_id = current_user.id
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            update_profile(
                session=database_session,
                user_id=user_id,
                full_name=request.form.get("full_name", ""),
                phone=request.form.get("phone"),
                birth_date=_parse_birth_date(request.form.get("birth_date")),
                gender=request.form.get("gender"),
            )
        flash("Perfil actualizado.", "success")
    except (ProfileError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("account.profile"))


@account.get("/perfil/cambiar-correo")
@login_required
def change_email_form():
    return render_template("account/change_email.html")


@account.post("/perfil/cambiar-correo")
@login_required
@limiter.limit("5 per minute")
def change_email_post():
    new_email = request.form.get("new_email", "").strip()
    user_id = current_user.id
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            _user, token = request_email_change(
                session=database_session,
                user_id=user_id,
                new_email=new_email,
                current_password=request.form.get("current_password", ""),
                ttl_minutes=current_app.config[
                    "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"
                ],
            )
        _send_email_change(new_email, token)
        flash(
            "Enviamos un enlace de confirmación al nuevo correo.",
            "success",
        )
        return redirect(url_for("account.profile"))
    except ProfileError as exc:
        flash(str(exc), "error")
        return render_template("account/change_email.html"), 400


@account.get("/perfil/confirmar-correo/<string:token>")
def confirm_email_change_route(token: str):
    try:
        with db.session.begin():
            confirm_email_change(session=db.session, token=token)
        flash("Correo actualizado y verificado.", "success")
        return redirect(url_for("account.profile"))
    except ProfileError as exc:
        flash(str(exc), "error")
        return redirect(url_for("account.change_email_form"))


@account.get("/perfil/cambiar-contrasena")
@login_required
def change_password_form():
    return render_template("account/change_password.html")


@account.post("/perfil/cambiar-contrasena")
@login_required
@limiter.limit("5 per minute")
def change_password_post():
    user_id = current_user.id
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            change_password(
                session=database_session,
                user_id=user_id,
                current_password=request.form.get("current_password", ""),
                new_password=request.form.get("new_password", ""),
                new_password_confirmation=request.form.get(
                    "new_password_confirmation",
                    "",
                ),
                password_min_length=current_app.config[
                    "AUTH_PASSWORD_MIN_LENGTH"
                ],
            )
        flash("Contraseña actualizada.", "success")
        return redirect(url_for("account.profile"))
    except Exception as exc:
        flash(str(exc), "error")
        return render_template("account/change_password.html"), 400


@account.get("/perfil/agregar-telefono")
@login_required
def add_phone_form():
    return render_template("account/add_phone.html", form={})


@account.post("/perfil/agregar-telefono")
@login_required
@limiter.limit(lambda: current_app.config["PHONE_OTP_REQUEST_RATE_LIMIT"])
def add_phone_post():
    phone = request.form.get("phone", "").strip()
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = request_phone_otp(
                session=database_session,
                phone=phone,
                purpose=PhoneOtpPurpose.LINK_PHONE,
                user_id=current_user.id,
            )
        from app.blueprints.auth import (
            PHONE_CHALLENGE_SESSION_KEY,
            PHONE_NEXT_SESSION_KEY,
            PHONE_PURPOSE_SESSION_KEY,
        )
        from flask import session as flask_session

        flask_session[PHONE_CHALLENGE_SESSION_KEY] = str(result.challenge.id)
        flask_session[PHONE_PURPOSE_SESSION_KEY] = PhoneOtpPurpose.LINK_PHONE.value
        flask_session[PHONE_NEXT_SESSION_KEY] = url_for("account.profile")
        flash("Enviamos un cÃ³digo al nÃºmero indicado.", "success")
        return redirect(url_for("auth.phone_verify_form"))
    except PhoneOtpCooldownError as exc:
        flash(str(exc), "error")
    except PhoneOtpError:
        flash("Ingresa un nÃºmero telefÃ³nico vÃ¡lido.", "error")
    return render_template("account/add_phone.html", form={"phone": phone}), 400


@account.get("/perfil/crear-contrasena")
@login_required
def create_password_form():
    return render_template("account/create_password.html")


@account.post("/perfil/crear-contrasena")
@login_required
def create_password_post():
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            create_password(
                session=database_session,
                user_id=current_user.id,
                new_password=request.form.get("new_password", ""),
                new_password_confirmation=request.form.get(
                    "new_password_confirmation",
                    "",
                ),
                password_min_length=current_app.config[
                    "AUTH_PASSWORD_MIN_LENGTH"
                ],
            )
        flash("ContraseÃ±a creada.", "success")
        return redirect(url_for("account.profile"))
    except Exception as exc:
        flash(str(exc), "error")
        return render_template("account/create_password.html"), 400
