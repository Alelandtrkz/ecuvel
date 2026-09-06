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
    if current_app.config.get("ECUVEL_PRODUCTION"):
        raise MailConfigurationError(
            "PUBLIC_BASE_URL debe configurarse para generar enlaces de correo "
            "en producción."
        )
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
    idempotency_key: str | None = None,
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
        idempotency_key=idempotency_key,
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


def email_change_mail(
    *, to: str, action_url: str, expiration_minutes: int | None
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Confirma tu nuevo correo de ECUVEL",
        template_name="email_change",
        mail_type="CHANGE_EMAIL",
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


def payment_approved_mail(
    *,
    to: str,
    action_url: str,
    buyer_name: str,
    payment_public_code: str,
    order_number: str,
    amount: str,
    currency: str,
    idempotency_key: str | None = None,
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="Hemos confirmado tu pago de ECUVEL",
        template_name="payment_approved",
        mail_type="PAYMENT_APPROVED",
        idempotency_key=idempotency_key,
        action_url=action_url,
        buyer_name=buyer_name,
        payment_public_code=payment_public_code,
        order_number=order_number,
        amount=amount,
        currency=currency,
    )


def payment_rejected_mail(
    *,
    to: str,
    action_url: str,
    buyer_name: str,
    payment_public_code: str,
    order_number: str,
    amount: str,
    currency: str,
    public_reason: str,
    idempotency_key: str | None = None,
) -> OutgoingMail:
    return _mail(
        to=to,
        subject="No pudimos aprobar tu pago de ECUVEL",
        template_name="payment_rejected",
        mail_type="PAYMENT_REJECTED",
        idempotency_key=idempotency_key,
        action_url=action_url,
        buyer_name=buyer_name,
        payment_public_code=payment_public_code,
        order_number=order_number,
        amount=amount,
        currency=currency,
        public_reason=public_reason,
    )
