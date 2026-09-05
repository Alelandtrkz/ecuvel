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
from app.models import User
from app.models.enums import PhoneOtpPurpose
from app.services.age_eligibility import is_at_least_18
from app.services.mail import MailError, mail_service
from app.services.phone_otp import (
    PhoneOtpCooldownError,
    PhoneOtpError,
    request_phone_otp,
)
from app.services.safe_redirects import safe_local_redirect
from app.services.transactional_mail import (
    build_mail_action_url,
    email_change_mail,
)
from app.services.user_profiles import (
    ProfileError,
    change_password,
    confirm_email_change,
    create_password,
    register_birth_date,
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


def _send_email_change(
    new_email: str,
    token: str,
    *,
    expiration_minutes: int,
) -> None:
    link = build_mail_action_url(
        "account.confirm_email_change_route",
        token=token,
    )
    mail_service.send(
        email_change_mail(
            to=new_email,
            action_url=link,
            expiration_minutes=expiration_minutes,
        )
    )


def _age_gate_destinations() -> tuple[str, str]:
    return (
        safe_local_redirect(
            request.values.get("next"),
            fallback=url_for("storefront.home"),
        ),
        safe_local_redirect(
            request.values.get("back"),
            fallback=url_for("storefront.cart"),
        ),
    )


def _render_age_gate(
    *,
    user: User,
    next_url: str,
    back_url: str,
    error: str | None = None,
    birth_date_value: str = "",
    status: int = 200,
):
    return (
        render_template(
            "account/age_gate.html",
            blocked=(
                user.birth_date is not None
                and not is_at_least_18(user.birth_date)
            ),
            next_url=next_url,
            back_url=back_url,
            error=error,
            birth_date_value=birth_date_value,
        ),
        status,
    )


@account.get("/verificar-edad")
@login_required
def age_gate():
    next_url, back_url = _age_gate_destinations()
    if is_at_least_18(current_user.birth_date):
        return redirect(next_url)
    return _render_age_gate(
        user=current_user,
        next_url=next_url,
        back_url=back_url,
    )


@account.post("/verificar-edad")
@login_required
def age_gate_submit():
    next_url, back_url = _age_gate_destinations()
    user_id = current_user.id
    birth_date_value = (request.form.get("birth_date") or "").strip()
    try:
        if not birth_date_value:
            raise ProfileError("Indica tu fecha de nacimiento para continuar.")
        try:
            submitted_birth_date = date.fromisoformat(birth_date_value)
        except ValueError as exc:
            raise ProfileError("Ingresa una fecha de nacimiento válida.") from exc

        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            register_birth_date(
                session=database_session,
                user_id=user_id,
                birth_date=submitted_birth_date,
            )
        return redirect(next_url)
    except ProfileError as exc:
        db.session.remove()
        user = db.session.get(User, user_id)
        if user is None:
            return redirect(url_for("auth.login_form"))
        return _render_age_gate(
            user=user,
            next_url=next_url,
            back_url=back_url,
            error=str(exc),
            birth_date_value=(
                "" if user.birth_date is not None else birth_date_value
            ),
            status=400,
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
    ttl_minutes = current_app.config[
        "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"
    ]
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            _user, token = request_email_change(
                session=database_session,
                user_id=user_id,
                new_email=new_email,
                current_password=request.form.get("current_password", ""),
                ttl_minutes=ttl_minutes,
            )
    except ProfileError as exc:
        flash(str(exc), "error")
        return render_template("account/change_email.html"), 400
    try:
        _send_email_change(
            new_email,
            token,
            expiration_minutes=ttl_minutes,
        )
    except MailError as exc:
        current_app.logger.warning(
            "event=mail_failed mail_type=CHANGE_EMAIL error=%s",
            type(exc).__name__,
        )
        flash(
            "No pudimos enviar el correo de confirmación en este momento. "
            "Inténtalo nuevamente más tarde.",
            "warning",
        )
        return redirect(url_for("account.profile"))
    flash(
        "Enviamos un enlace de confirmación al nuevo correo.",
        "success",
    )
    return redirect(url_for("account.profile"))


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


def _phone_otp_disabled_profile_redirect():
    if current_app.config.get("PHONE_OTP_ENABLED", False):
        return None
    from app.blueprints.auth import clear_phone_otp_session_state

    clear_phone_otp_session_state()
    flash(
        "La verificación telefónica estará disponible próximamente.",
        "warning",
    )
    return redirect(url_for("account.profile"))


@account.get("/perfil/agregar-telefono")
@login_required
def add_phone_form():
    disabled_redirect = _phone_otp_disabled_profile_redirect()
    if disabled_redirect is not None:
        return disabled_redirect
    return render_template("account/add_phone.html", form={})


@account.post("/perfil/agregar-telefono")
@login_required
@limiter.limit(lambda: current_app.config["PHONE_OTP_REQUEST_RATE_LIMIT"])
def add_phone_post():
    disabled_redirect = _phone_otp_disabled_profile_redirect()
    if disabled_redirect is not None:
        return disabled_redirect
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
        flash("Enviamos un código al número indicado.", "success")
        return redirect(url_for("auth.phone_verify_form"))
    except PhoneOtpCooldownError as exc:
        flash(str(exc), "error")
    except PhoneOtpError:
        flash("Ingresa un número telefónico válido.", "error")
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
