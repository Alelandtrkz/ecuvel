from __future__ import annotations

from flask import current_app, has_request_context, url_for

from app.services.mail import MailConfigurationError, OutgoingMail


def build_mail_action_url(endpoint: str, **values) -> str:
    configured_base = str(
        current_app.config.get("PUBLIC_BASE_URL") or ""
    ).strip().rstrip("/")
    if configured_base:
        with current_app.test_request_context(base_url=f"{configured_base}/"):
            return url_for(endpoint, _external=True, **values)
    if has_request_context():
        return url_for(endpoint, _external=True, **values)
    raise MailConfigurationError(
        "PUBLIC_BASE_URL debe configurarse para generar enlaces de correo "
        "fuera de una solicitud web."
    )


def _mail(
    *,
    to: str,
    subject: str,
    template_name: str,
    mail_type: str,
    **context,
) -> OutgoingMail:
    template_context = {"subject": subject, **context}
    text_template = current_app.jinja_env.get_template(
        f"email/{template_name}.txt"
    )
    html_template = current_app.jinja_env.get_template(
        f"email/{template_name}.html"
    )
    return OutgoingMail(
        to=to,
        subject=subject,
        text_body=text_template.render(**template_context).strip(),
        html_body=html_template.render(**template_context).strip(),
        tags={"mail_type": mail_type},
    )


def verification_mail(
    *, to: str, action_url: str, expiration_minutes: int | None
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Verifica tu correo",
        template_name="auth_verify",
        mail_type="VERIFY_EMAIL",
        action_url=action_url,
        expiration_minutes=expiration_minutes,
    )


def password_reset_mail(
    *, to: str, action_url: str, expiration_minutes: int | None
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Restablece tu contraseña de ECUVEL",
        template_name="password_reset",
        mail_type="PASSWORD_RESET",
        action_url=action_url,
        expiration_minutes=expiration_minutes,
    )


def staff_invitation_mail(
    *,
    to: str,
    action_url: str,
    full_name: str | None,
    employee_code: str | None,
    role: str | None,
    expiration_minutes: int | None,
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Te invitaron a formar parte del equipo ECUVEL",
        template_name="staff_invitation",
        mail_type="STAFF_INVITATION",
        action_url=action_url,
        full_name=full_name,
        employee_code=employee_code,
        role=role,
        expiration_minutes=expiration_minutes,
    )


def review_rejected_mail(
    *, to: str, action_url: str, product_title: str, public_reason: str
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Tu reseña necesita cambios para publicarse en ECUVEL",
        template_name="review_rejected",
        mail_type="REVIEW_REJECTED",
        action_url=action_url,
        product_title=product_title,
        public_reason=public_reason,
    )
