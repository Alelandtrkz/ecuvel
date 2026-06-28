from __future__ import annotations

from dataclasses import dataclass

from flask import current_app


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: str
    subject: str
    body: str


class MailService:
    def __init__(self) -> None:
        self.outbox: list[OutgoingMail] = []

    def send(self, message: OutgoingMail) -> None:
        backend = current_app.config.get("MAIL_BACKEND", "console")
        if backend == "memory":
            self.outbox.append(message)
            return
        current_app.logger.warning(
            "Correo de desarrollo para %s: %s\n%s",
            message.to,
            message.subject,
            message.body,
        )


mail_service = MailService()
