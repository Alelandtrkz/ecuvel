from __future__ import annotations

from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db, limiter
from app.models import PhoneOtpChallenge
from app.models.enums import PhoneOtpPurpose, UserAccountTokenPurpose
from app.services.account_tokens import create_account_token
from app.services.authentication import (
    AuthenticationError,
    authenticate_customer,
    register_customer,
    request_password_reset,
    reset_password,
    verify_customer_email,
)
from app.services.mail import OutgoingMail, mail_service
from app.services.phone_otp import (
    InvalidPhoneOtpError,
    PhoneOtpCooldownError,
    PhoneOtpError,
    PhoneRegistrationError,
    link_verified_phone,
    mask_phone,
    register_phone_user,
    request_phone_otp,
    verify_phone_otp,
)
from app.services.session_order_claims import (
    claim_session_orders,
    normalize_session_order_ids,
)


auth = Blueprint("auth", __name__)

PHONE_CHALLENGE_SESSION_KEY = "phone_otp_challenge_id"
PHONE_PURPOSE_SESSION_KEY = "phone_otp_purpose"
PHONE_NEXT_SESSION_KEY = "phone_otp_next"
PHONE_REGISTRATION_CHALLENGE_SESSION_KEY = "phone_registration_challenge_id"


def _safe_next(value: str | None) -> str:
    if not value:
        return url_for("storefront.home")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return url_for("storefront.home")
    return value


def _claim_orders_for_user(user_id, database_session=None) -> None:
    order_ids = normalize_session_order_ids(
        flask_session.get("checkout_order_ids")
    )
    if not order_ids:
        return
    claimed = claim_session_orders(
        session=database_session or db.session,
        order_ids=order_ids,
        target_user_id=user_id,
        demo_buyer_email=current_app.config["CHECKOUT_DEMO_BUYER_EMAIL"],
    )
    if claimed:
        flash(
            f"Asociamos {claimed} pedido(s) de esta sesión a tu cuenta.",
            "success",
        )


def _login_user_preserving_session(user, *, remember: bool = False) -> None:
    preserved = {
        "cart": flask_session.get("cart"),
        "checkout_order_ids": flask_session.get("checkout_order_ids"),
    }
    flask_session.clear()
    for key, value in preserved.items():
        if value is not None:
            flask_session[key] = value
    login_user(user, remember=remember)


def _send_verification_email(email: str, token: str) -> None:
    link = url_for("auth.verify_email", token=token, _external=True)
    mail_service.send(
        OutgoingMail(
            to=email,
            subject="Verifica tu correo en Ecuvel",
            body=(
                "Confirma tu correo para activar tu cuenta de Ecuvel:\n"
                f"{link}"
            ),
        )
    )


def _send_password_reset_email(email: str, token: str) -> None:
    link = url_for("auth.reset_password_form", token=token, _external=True)
    mail_service.send(
        OutgoingMail(
            to=email,
            subject="Restablece tu contraseña de Ecuvel",
            body=(
                "Si solicitaste restablecer tu contraseña, usa este enlace:\n"
                f"{link}"
            ),
        )
    )


def _store_phone_challenge(challenge_id, purpose: PhoneOtpPurpose, next_url: str) -> None:
    flask_session[PHONE_CHALLENGE_SESSION_KEY] = str(challenge_id)
    flask_session[PHONE_PURPOSE_SESSION_KEY] = purpose.value
    flask_session[PHONE_NEXT_SESSION_KEY] = next_url


def _clear_phone_challenge() -> None:
    flask_session.pop(PHONE_CHALLENGE_SESSION_KEY, None)
    flask_session.pop(PHONE_PURPOSE_SESSION_KEY, None)
    flask_session.pop(PHONE_NEXT_SESSION_KEY, None)


@auth.get("/registro")
def register_form():
    if current_user.is_authenticated:
        return redirect(url_for("account.profile"))
    return render_template(
        "auth/register.html",
        next_url=_safe_next(request.args.get("next")),
        form={},
    )


@auth.post("/registro")
@limiter.limit("5 per minute")
def register():
    next_url = _safe_next(request.form.get("next"))
    form = {
        "email": request.form.get("email", "").strip(),
        "full_name": request.form.get("full_name", "").strip(),
    }
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = register_customer(
                session=database_session,
                email=form["email"],
                full_name=form["full_name"],
                password=request.form.get("password", ""),
                password_confirmation=request.form.get(
                    "password_confirmation", ""
                ),
                password_min_length=current_app.config[
                    "AUTH_PASSWORD_MIN_LENGTH"
                ],
                verification_ttl_minutes=current_app.config[
                    "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"
                ],
            )
            _claim_orders_for_user(result.user.id, database_session)
        _login_user_preserving_session(result.user)
        _send_verification_email(result.user.email, result.verification_token)
        flash("Cuenta creada. Revisa tu correo para verificarla.", "success")
        if current_app.config["AUTH_REQUIRE_EMAIL_VERIFICATION"]:
            return redirect(url_for("auth.verification_pending"))
        return redirect(next_url)
    except AuthenticationError as exc:
        flash(str(exc), "error")
        return render_template(
            "auth/register.html",
            next_url=next_url,
            form=form,
        ), 400


@auth.get("/iniciar-sesion")
def login_form():
    if current_user.is_authenticated:
        return redirect(url_for("account.profile"))
    return render_template(
        "auth/login.html",
        next_url=_safe_next(request.args.get("next")),
        form={},
    )


@auth.post("/iniciar-sesion")
@limiter.limit("5 per minute")
def login():
    next_url = _safe_next(request.form.get("next"))
    form = {"email": request.form.get("email", "").strip()}
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            user = authenticate_customer(
                session=database_session,
                email=form["email"],
                password=request.form.get("password", ""),
            )
            _claim_orders_for_user(user.id, database_session)
        _login_user_preserving_session(
            user,
            remember=request.form.get("remember") == "1",
        )
        flash("Sesión iniciada.", "success")
        return redirect(next_url)
    except AuthenticationError:
        flash("Correo o contraseña incorrectos.", "error")
        return render_template(
            "auth/login.html",
            next_url=next_url,
            form=form,
        ), 400


@auth.post("/cerrar-sesion")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("storefront.home"))


@auth.get("/verificacion-pendiente")
@login_required
def verification_pending():
    return render_template("auth/verify_email_pending.html")


@auth.post("/reenviar-verificacion")
@login_required
@limiter.limit("3 per minute")
def resend_verification():
    if not current_user.email:
        flash("Añade un correo electrónico antes de reenviar la verificación.", "warning")
        return redirect(url_for("account.profile"))
    if current_user.email_verified_at is not None:
        flash("Tu correo ya está verificado.", "success")
        return redirect(url_for("account.profile"))
    with db.session.begin():
        token = create_account_token(
            session=db.session,
            user_id=current_user.id,
            purpose=UserAccountTokenPurpose.VERIFY_EMAIL,
            ttl_minutes=current_app.config[
                "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"
            ],
        )
    _send_verification_email(current_user.email, token.token)
    flash("Enviamos un nuevo enlace de verificación.", "success")
    return redirect(url_for("auth.verification_pending"))


@auth.get("/verificar-correo/<string:token>")
def verify_email(token: str):
    try:
        with db.session.begin():
            user = verify_customer_email(session=db.session, token=token)
        _login_user_preserving_session(user)
        flash("Correo verificado. Tu cuenta está activa.", "success")
        return redirect(url_for("account.profile"))
    except AuthenticationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("auth.verification_pending"))


@auth.get("/recuperar-contrasena")
def forgot_password_form():
    return render_template("auth/forgot_password.html", form={})


@auth.post("/recuperar-contrasena")
@limiter.limit("5 per minute")
def forgot_password():
    email = request.form.get("email", "").strip()
    db.session.remove()
    database_session = db.session()
    with database_session.begin():
        result = request_password_reset(
            session=database_session,
            email=email,
            ttl_minutes=current_app.config[
                "PASSWORD_RESET_TOKEN_TTL_MINUTES"
            ],
        )
    if result is not None:
        user, token = result
        _send_password_reset_email(user.email, token)
    flash(
        "Si existe una cuenta asociada, enviaremos instrucciones.",
        "success",
    )
    return redirect(url_for("auth.login_form"))


@auth.get("/restablecer-contrasena/<string:token>")
def reset_password_form(token: str):
    return render_template("auth/reset_password.html", token=token)


@auth.post("/restablecer-contrasena/<string:token>")
@limiter.limit("5 per minute")
def reset_password_post(token: str):
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            reset_password(
                session=database_session,
                token=token,
                password=request.form.get("password", ""),
                password_confirmation=request.form.get(
                    "password_confirmation", ""
                ),
                password_min_length=current_app.config[
                    "AUTH_PASSWORD_MIN_LENGTH"
                ],
            )
        flash("Contraseña actualizada. Inicia sesión nuevamente.", "success")
        return redirect(url_for("auth.login_form"))
    except AuthenticationError as exc:
        flash(str(exc), "error")
        return render_template("auth/reset_password.html", token=token), 400


@auth.get("/ingresar-telefono")
def phone_login_form():
    if current_user.is_authenticated:
        return redirect(url_for("account.profile"))
    return render_template(
        "auth/phone_request.html",
        next_url=_safe_next(request.args.get("next")),
        form={},
    )


@auth.post("/ingresar-telefono")
@limiter.limit(lambda: current_app.config["PHONE_OTP_REQUEST_RATE_LIMIT"])
def phone_login_request():
    next_url = _safe_next(request.form.get("next"))
    form = {"phone": request.form.get("phone", "").strip()}
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = request_phone_otp(
                session=database_session,
                phone=form["phone"],
                purpose=PhoneOtpPurpose.LOGIN_OR_REGISTER,
            )
        _store_phone_challenge(
            result.challenge.id,
            PhoneOtpPurpose.LOGIN_OR_REGISTER,
            next_url,
        )
        flash("Enviamos un cÃ³digo al nÃºmero indicado.", "success")
        return redirect(url_for("auth.phone_verify_form"))
    except PhoneOtpCooldownError as exc:
        flash(str(exc), "error")
    except PhoneOtpError:
        flash("Ingresa un nÃºmero telefÃ³nico vÃ¡lido.", "error")
    return render_template(
        "auth/phone_request.html",
        next_url=next_url,
        form=form,
    ), 400


@auth.get("/verificar-telefono")
def phone_verify_form():
    challenge_id = flask_session.get(PHONE_CHALLENGE_SESSION_KEY)
    if not challenge_id:
        return redirect(url_for("auth.phone_login_form"))
    return render_template("auth/phone_verify.html")


@auth.post("/verificar-telefono")
@limiter.limit(lambda: current_app.config["PHONE_OTP_VERIFY_RATE_LIMIT"])
def phone_verify_post():
    challenge_id = flask_session.get(PHONE_CHALLENGE_SESSION_KEY)
    purpose_value = flask_session.get(PHONE_PURPOSE_SESSION_KEY)
    if not challenge_id or not purpose_value:
        return redirect(url_for("auth.phone_login_form"))
    purpose = PhoneOtpPurpose(purpose_value)
    database_session = None
    try:
        db.session.remove()
        database_session = db.session()
        verified = verify_phone_otp(
            session=database_session,
            challenge_id=challenge_id,
            code=request.form.get("code", ""),
            expected_purpose=purpose,
        )
        if purpose == PhoneOtpPurpose.LOGIN_OR_REGISTER and verified.existing_user:
            _claim_orders_for_user(verified.existing_user.id, database_session)
            user = verified.existing_user
        elif purpose in {PhoneOtpPurpose.LINK_PHONE, PhoneOtpPurpose.CHANGE_PHONE}:
            link_verified_phone(
                session=database_session,
                user_id=current_user.id,
                challenge_id=challenge_id,
            )
            user = current_user
        else:
            user = None
        database_session.commit()
        if purpose == PhoneOtpPurpose.LOGIN_OR_REGISTER and user is not None:
            next_url = _safe_next(flask_session.get(PHONE_NEXT_SESSION_KEY))
            _clear_phone_challenge()
            _login_user_preserving_session(user)
            flash("SesiÃ³n iniciada.", "success")
            return redirect(next_url)
        if purpose == PhoneOtpPurpose.LOGIN_OR_REGISTER:
            flask_session[PHONE_REGISTRATION_CHALLENGE_SESSION_KEY] = str(challenge_id)
            _clear_phone_challenge()
            return redirect(url_for("auth.complete_phone_registration_form"))
        _clear_phone_challenge()
        flash("TelÃ©fono verificado y vinculado.", "success")
        return redirect(url_for("account.profile"))
    except InvalidPhoneOtpError as exc:
        if database_session is not None:
            database_session.commit()
        flash(str(exc), "error")
        return render_template("auth/phone_verify.html"), 400
    except PhoneOtpError as exc:
        if database_session is not None:
            database_session.rollback()
        flash(str(exc), "error")
        return render_template("auth/phone_verify.html"), 400


@auth.post("/reenviar-codigo-telefono")
@limiter.limit(lambda: current_app.config["PHONE_OTP_RESEND_RATE_LIMIT"])
def phone_resend_code():
    challenge_id = flask_session.get(PHONE_CHALLENGE_SESSION_KEY)
    purpose_value = flask_session.get(PHONE_PURPOSE_SESSION_KEY)
    if not challenge_id or not purpose_value:
        return redirect(url_for("auth.phone_login_form"))
    try:
        purpose = PhoneOtpPurpose(purpose_value)
        old_challenge = db.session.get(PhoneOtpChallenge, challenge_id)
        if old_challenge is None:
            return redirect(url_for("auth.phone_login_form"))
        phone_normalized = old_challenge.phone_normalized
        user_id = old_challenge.user_id
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = request_phone_otp(
                session=database_session,
                phone=phone_normalized,
                purpose=purpose,
                user_id=user_id,
            )
        _store_phone_challenge(
            result.challenge.id,
            purpose,
            _safe_next(flask_session.get(PHONE_NEXT_SESSION_KEY)),
        )
        flash("Enviamos un cÃ³digo al nÃºmero indicado.", "success")
    except PhoneOtpCooldownError as exc:
        flash(str(exc), "error")
    return redirect(url_for("auth.phone_verify_form"))


@auth.get("/registro/telefono/completar")
def complete_phone_registration_form():
    if not flask_session.get(PHONE_REGISTRATION_CHALLENGE_SESSION_KEY):
        return redirect(url_for("auth.phone_login_form"))
    return render_template("auth/phone_complete_registration.html", form={})


@auth.post("/registro/telefono/completar")
def complete_phone_registration_post():
    challenge_id = flask_session.get(PHONE_REGISTRATION_CHALLENGE_SESSION_KEY)
    if not challenge_id:
        return redirect(url_for("auth.phone_login_form"))
    form = {
        "full_name": request.form.get("full_name", "").strip(),
        "email": request.form.get("email", "").strip(),
    }
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            result = register_phone_user(
                session=database_session,
                challenge_id=challenge_id,
                full_name=form["full_name"],
                email=form["email"],
                verification_ttl_minutes=current_app.config[
                    "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES"
                ],
            )
            _claim_orders_for_user(result.user.id, database_session)
        flask_session.pop(PHONE_REGISTRATION_CHALLENGE_SESSION_KEY, None)
        _login_user_preserving_session(result.user)
        if result.verification_token and result.user.email:
            _send_verification_email(result.user.email, result.verification_token)
            flash("Cuenta creada. TambiÃ©n enviamos verificaciÃ³n a tu correo.", "success")
        else:
            flash("Cuenta creada con tu telÃ©fono verificado.", "success")
        return redirect(url_for("account.profile"))
    except PhoneRegistrationError as exc:
        flash(str(exc), "error")
        return render_template(
            "auth/phone_complete_registration.html",
            form=form,
        ), 400
