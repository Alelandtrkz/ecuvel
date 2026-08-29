from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from html import escape
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app


class MailError(RuntimeError):
    """Base error for the interchangeable mail infrastructure."""


class MailConfigurationError(MailError):
    """Raised when the selected provider is not configured safely."""


class MailDeliveryError(MailError):
    """A sanitized provider failure safe to expose to internal callers."""


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: str
    subject: str
    body: str | None = None
    text_body: str | None = None
    html_body: str | None = None
    from_address: str | None = None
    reply_to: str | None = None
    tags: Mapping[str, str] | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        # ``body`` remains a compatibility alias while ``text_body`` is the
        # canonical plain-text representation.
        plain = self.text_body if self.text_body is not None else self.body
        if not self.to.strip() or not self.subject.strip() or not (plain or "").strip():
            raise ValueError("El correo requiere destinatario, asunto y texto.")
        object.__setattr__(self, "text_body", plain)
        object.__setattr__(self, "body", plain)
        if self.idempotency_key is not None:
            clean_key = self.idempotency_key.strip()
            if (
                not clean_key
                or len(clean_key) > 256
                or "\r" in clean_key
                or "\n" in clean_key
            ):
                raise ValueError("La clave de idempotencia del correo no es válida.")
            object.__setattr__(self, "idempotency_key", clean_key)
        if self.html_body is None:
            safe_text = escape(plain or "").replace("\n", "<br>")
            object.__setattr__(
                self,
                "html_body",
                "<div style=\"font-family:Arial,Helvetica,sans-serif;"
                "line-height:1.6\">" + safe_text + "</div>",
            )


@dataclass(frozen=True, slots=True)
class MailSendResult:
    message_id: str | None
    provider: str
    accepted: bool


def _recipient_domain(address: str) -> str:
    _, separator, domain = address.rpartition("@")
    return domain.casefold() if separator else "unknown"


def _validated_tags(tags: Mapping[str, str] | None) -> list[dict[str, str]]:
    if not tags:
        return []
    result: list[dict[str, str]] = []
    for name, value in list(tags.items())[:10]:
        clean_name = "".join(
            char for char in str(name) if char.isalnum() or char in "_-"
        )[:50]
        clean_value = str(value).strip()[:256]
        if clean_name and clean_value:
            result.append({"name": clean_name, "value": clean_value})
    return result


def validate_mail_configuration(config: Mapping[str, object]) -> None:
    backend = str(config.get("MAIL_BACKEND") or "console").strip().lower()
    if backend not in {"memory", "console", "resend"}:
        raise MailConfigurationError(
            "MAIL_BACKEND debe ser uno de: console, memory, resend."
        )
    if backend == "resend":
        if not str(config.get("RESEND_API_KEY") or "").strip():
            raise MailConfigurationError(
                "RESEND_API_KEY es obligatoria cuando MAIL_BACKEND=resend."
            )
        if not str(config.get("MAIL_FROM") or "").strip():
            raise MailConfigurationError(
                "MAIL_FROM es obligatorio cuando MAIL_BACKEND=resend."
            )
    if bool(config.get("ECUVEL_PRODUCTION")) and backend == "console":
        raise MailConfigurationError(
            "MAIL_BACKEND=console no está permitido en producción."
        )


class MailService:
    def __init__(self) -> None:
        self.outbox: list[OutgoingMail] = []

    def send(self, message: OutgoingMail) -> MailSendResult:
        backend = str(
            current_app.config.get("MAIL_BACKEND", "console")
        ).strip().lower()
        if backend == "memory":
            self.outbox.append(message)
            return MailSendResult(
                message_id=f"memory-{len(self.outbox)}",
                provider="memory",
                accepted=True,
            )
        if backend == "console":
            current_app.logger.info(
                "event=mail_sent provider=console mail_type=%s recipient_domain=%s",
                (message.tags or {}).get("mail_type", "TRANSACTIONAL"),
                _recipient_domain(message.to),
            )
            return MailSendResult(None, "console", True)
        if backend == "resend":
            return self._send_resend(message)
        raise MailConfigurationError(
            "MAIL_BACKEND debe ser uno de: console, memory, resend."
        )

    def _send_resend(self, message: OutgoingMail) -> MailSendResult:
        api_key = str(
            current_app.config.get("RESEND_API_KEY") or ""
        ).strip()
        from_address = str(
            message.from_address or current_app.config.get("MAIL_FROM") or ""
        ).strip()
        reply_to = str(
            message.reply_to or current_app.config.get("MAIL_REPLY_TO") or ""
        ).strip()
        if not api_key:
            raise MailConfigurationError(
                "RESEND_API_KEY es obligatoria cuando MAIL_BACKEND=resend."
            )
        if not from_address:
            raise MailConfigurationError(
                "MAIL_FROM es obligatorio cuando MAIL_BACKEND=resend."
            )
        base_url = str(
            current_app.config.get("RESEND_API_BASE_URL")
            or "https://api.resend.com"
        ).rstrip("/")
        timeout = float(current_app.config.get("RESEND_TIMEOUT_SECONDS") or 8)
        payload: dict[str, object] = {
            "from": from_address,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text_body,
        }
        if message.html_body:
            payload["html"] = message.html_body
        if reply_to:
            payload["reply_to"] = reply_to
        tags = _validated_tags(message.tags)
        if tags:
            payload["tags"] = tags
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "ECUVEL-MailService/1.0",
        }
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        request = Request(
            f"{base_url}/emails",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw_response = response.read()
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            raise MailDeliveryError(
                f"El proveedor de correo rechazó el envío (estado {exc.code})."
            ) from None
        except (TimeoutError, socket.timeout):
            raise MailDeliveryError(
                "El proveedor de correo no respondió dentro del tiempo esperado."
            ) from None
        except URLError:
            raise MailDeliveryError(
                "No fue posible contactar al proveedor de correo."
            ) from None
        except OSError:
            raise MailDeliveryError(
                "No fue posible completar el envío de correo."
            ) from None
        if not 200 <= status < 300:
            raise MailDeliveryError(
                f"El proveedor de correo rechazó el envío (estado {status})."
            )
        try:
            response_payload = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MailDeliveryError(
                "El proveedor de correo devolvió una respuesta inválida."
            ) from None
        message_id = (
            response_payload.get("id")
            if isinstance(response_payload, dict)
            else None
        )
        if not isinstance(message_id, str) or not message_id.strip():
            raise MailDeliveryError(
                "El proveedor de correo no confirmó el identificador del envío."
            )
        current_app.logger.info(
            "event=mail_sent provider=resend mail_type=%s "
            "provider_message_id=%s recipient_domain=%s",
            (message.tags or {}).get("mail_type", "TRANSACTIONAL"),
            message_id,
            _recipient_domain(message.to),
        )
        return MailSendResult(message_id, "resend", True)


mail_service = MailService()
