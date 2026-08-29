from __future__ import annotations

import json
import socket
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.services.mail import (
    MailConfigurationError,
    MailDeliveryError,
    MailService,
    OutgoingMail,
    validate_mail_configuration,
)
from app.services.transactional_mail import (
    password_reset_mail,
    review_rejected_mail,
    staff_invitation_mail,
    verification_mail,
)


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


def _resend_config(app) -> None:
    app.config.update(
        MAIL_BACKEND="resend",
        MAIL_FROM="ECUVEL <no-reply@example.test>",
        MAIL_REPLY_TO="",
        RESEND_API_KEY="unit-test-credential",
        RESEND_API_BASE_URL="https://provider.invalid",
        RESEND_TIMEOUT_SECONDS=7,
    )


def _message() -> OutgoingMail:
    return OutgoingMail(
        to="recipient@example.test",
        subject="Verificación de correo ECUVEL",
        text_body=(
            "¡Hola! Confirma tu dirección electrónica. Tu código expirará "
            "según la política configurada. á é í ó ú ñ Ñ ¿ ¡ $"
        ),
        html_body="<p>¡Hola! Verificación á é í ó ú ñ Ñ ¿ ¡ $</p>",
        tags={"mail_type": "VERIFY_EMAIL"},
    )


def test_memory_backend_preserves_body_alias_and_returns_result(app):
    service = MailService()
    app.config["MAIL_BACKEND"] = "memory"

    result = service.send(OutgoingMail(
        to="recipient@example.test", subject="Asunto", body="Texto",
    ))

    assert result.accepted is True
    assert result.provider == "memory"
    assert result.message_id == "memory-1"
    assert service.outbox[0].body == service.outbox[0].text_body == "Texto"
    assert "Texto" in service.outbox[0].html_body


def test_console_backend_logs_metadata_but_not_content_or_address(app, caplog):
    service = MailService()
    app.config["MAIL_BACKEND"] = "console"
    caplog.set_level("INFO")
    message = OutgoingMail(
        to="private.person@example.test",
        subject="private subject",
        body="private token and verification URL",
        tags={"mail_type": "VERIFY_EMAIL"},
    )

    result = service.send(message)

    assert result.provider == "console"
    assert "recipient_domain=example.test" in caplog.text
    assert "private.person" not in caplog.text
    assert "private token" not in caplog.text


def test_resend_success_returns_provider_id_and_sends_utf8(app, monkeypatch):
    _resend_config(app)
    service = MailService()
    captured = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(b'{"id":"provider-message-123"}')

    monkeypatch.setattr("app.services.mail.urlopen", fake_open)
    source_message = _message()
    message = OutgoingMail(
        to=source_message.to,
        subject=source_message.subject,
        text_body=source_message.text_body,
        html_body=source_message.html_body,
        tags=source_message.tags,
        idempotency_key="payment-notification/stable-outbox-id",
    )
    result = service.send(message)
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))

    assert result.accepted is True
    assert result.provider == "resend"
    assert result.message_id == "provider-message-123"
    assert captured["timeout"] == 7
    assert payload["subject"] == "Verificación de correo ECUVEL"
    assert payload["text"] == _message().text_body
    assert payload["html"] == _message().html_body
    assert payload["tags"] == [{"name": "mail_type", "value": "VERIFY_EMAIL"}]
    headers = {name.casefold(): value for name, value in request.header_items()}
    assert headers["idempotency-key"] == (
        "payment-notification/stable-outbox-id"
    )
    assert request.data.decode("utf-8").count("�") == 0


def test_optional_idempotency_key_is_preserved_by_memory_backend(app):
    service = MailService()
    app.config["MAIL_BACKEND"] = "memory"

    service.send(OutgoingMail(
        to="recipient@example.test",
        subject="Asunto",
        body="Texto",
        idempotency_key="payment-notification/stable-outbox-id",
    ))

    assert service.outbox[0].idempotency_key == (
        "payment-notification/stable-outbox-id"
    )


@pytest.mark.parametrize("status", (401, 403, 429, 500))
def test_resend_http_errors_are_sanitized(app, monkeypatch, status):
    _resend_config(app)
    service = MailService()

    def fail(_request, timeout):
        assert timeout == 7
        raise HTTPError(
            "https://provider.invalid/emails",
            status,
            "provider response unit-test-credential",
            {},
            BytesIO(b'{"message":"unit-test-credential"}'),
        )

    monkeypatch.setattr("app.services.mail.urlopen", fail)
    with pytest.raises(MailDeliveryError) as raised:
        service.send(_message())

    assert f"estado {status}" in str(raised.value)
    assert "unit-test-credential" not in str(raised.value)
    assert "provider.invalid" not in str(raised.value)


def test_resend_timeout_is_sanitized(app, monkeypatch):
    _resend_config(app)

    def fail(_request, timeout):
        assert timeout == 7
        raise socket.timeout("unit-test-credential")

    monkeypatch.setattr("app.services.mail.urlopen", fail)
    with pytest.raises(MailDeliveryError) as raised:
        MailService().send(_message())
    assert "tiempo esperado" in str(raised.value)
    assert "unit-test-credential" not in str(raised.value)


@pytest.mark.parametrize("response", (b"not-json", b'{"accepted":true}'))
def test_resend_rejects_unconfirmed_responses(app, monkeypatch, response):
    _resend_config(app)
    monkeypatch.setattr(
        "app.services.mail.urlopen",
        lambda _request, timeout: _Response(response),
    )

    with pytest.raises(MailDeliveryError):
        MailService().send(_message())


@pytest.mark.parametrize(
    ("missing", "expected"),
    (("RESEND_API_KEY", "RESEND_API_KEY"), ("MAIL_FROM", "MAIL_FROM")),
)
def test_resend_requires_key_and_sender(app, missing, expected):
    _resend_config(app)
    app.config[missing] = ""

    with pytest.raises(MailConfigurationError, match=expected):
        MailService().send(_message())


def test_configuration_rejects_unknown_backend_and_console_production():
    with pytest.raises(MailConfigurationError, match="MAIL_BACKEND"):
        validate_mail_configuration({"MAIL_BACKEND": "unknown"})
    with pytest.raises(MailConfigurationError, match="producción"):
        validate_mail_configuration({
            "MAIL_BACKEND": "console", "ECUVEL_PRODUCTION": True,
        })


def test_transactional_templates_render_text_html_and_only_public_review_reason(app):
    action_url = "https://ecuvel.test/accion/opaque-token"
    messages = (
        verification_mail(
            to="one@example.test", action_url=action_url,
            expiration_minutes=30,
        ),
        password_reset_mail(
            to="two@example.test", action_url=action_url,
            expiration_minutes=30,
        ),
        staff_invitation_mail(
            to="three@example.test", action_url=action_url,
            full_name="Ana Ñíguez", employee_code="EMP-000005",
            role="Operadora de punto", expiration_minutes=60,
        ),
        review_rejected_mail(
            to="four@example.test", action_url=action_url,
            product_title="Cámara edición Ñ",
            public_reason="El comentario necesita cambios públicos.",
        ),
    )

    for message in messages:
        assert action_url in message.text_body
        assert action_url in message.html_body
        assert message.text_body
        assert message.html_body
    assert "EMP-000005" in messages[2].text_body
    assert "Operadora de punto" in messages[2].html_body
    assert "cambios públicos" in messages[3].text_body
    assert "internal_notes" not in messages[3].text_body
