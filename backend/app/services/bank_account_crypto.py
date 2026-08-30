from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app


NONCE_BYTES = 12
KEY_BYTES = 32
_SEPARATORS = re.compile(r"[\s-]+")


class BankAccountCryptoError(Exception):
    """Error seguro que nunca incorpora el dato bancario ni material de clave."""


@dataclass(frozen=True, slots=True)
class EncryptedBankAccount:
    ciphertext: bytes
    nonce: bytes
    fingerprint: bytes
    last4: str
    encryption_key_version: str
    fingerprint_key_version: str


def normalize_bank_account_number(value: str) -> str:
    raw = str(value or "").strip()
    normalized = _SEPARATORS.sub("", raw)
    if not normalized.isdigit() or not 6 <= len(normalized) <= 34:
        raise BankAccountCryptoError("El número de cuenta bancaria no es válido.")
    return normalized


def _decode_key(encoded: str | None) -> bytes:
    try:
        key = base64.b64decode((encoded or "").strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BankAccountCryptoError(
            "La configuración criptográfica bancaria no es válida."
        ) from exc
    if len(key) != KEY_BYTES:
        raise BankAccountCryptoError(
            "La configuración criptográfica bancaria no es válida."
        )
    return key


def bank_account_aad(store_id: uuid.UUID, version_id: uuid.UUID) -> bytes:
    return b"ecuvel.bank-account.v1|" + store_id.bytes + version_id.bytes


class BankAccountCrypto:
    def __init__(
        self,
        *,
        encryption_keys: Mapping[str, str | None],
        active_encryption_version: str,
        fingerprint_keys: Mapping[str, str | None],
        active_fingerprint_version: str,
    ) -> None:
        self._encryption_keys = dict(encryption_keys)
        self._active_encryption_version = active_encryption_version
        self._fingerprint_keys = dict(fingerprint_keys)
        self._active_fingerprint_version = active_fingerprint_version

    def encrypt(
        self,
        account_number: str,
        *,
        store_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> EncryptedBankAccount:
        normalized = normalize_bank_account_number(account_number)
        encryption_key = _decode_key(
            self._encryption_keys.get(self._active_encryption_version)
        )
        fingerprint_key = _decode_key(
            self._fingerprint_keys.get(self._active_fingerprint_version)
        )
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(encryption_key).encrypt(
            nonce,
            normalized.encode("ascii"),
            bank_account_aad(store_id, version_id),
        )
        fingerprint = hmac.new(
            fingerprint_key, normalized.encode("ascii"), hashlib.sha256
        ).digest()
        return EncryptedBankAccount(
            ciphertext=ciphertext,
            nonce=nonce,
            fingerprint=fingerprint,
            last4=normalized[-4:],
            encryption_key_version=self._active_encryption_version,
            fingerprint_key_version=self._active_fingerprint_version,
        )

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        store_id: uuid.UUID,
        version_id: uuid.UUID,
        encryption_key_version: str,
    ) -> str:
        key = _decode_key(self._encryption_keys.get(encryption_key_version))
        try:
            plaintext = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                bank_account_aad(store_id, version_id),
            )
            return plaintext.decode("ascii")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise BankAccountCryptoError(
                "No se pudo autenticar el dato bancario cifrado."
            ) from exc


def configured_bank_account_crypto() -> BankAccountCrypto:
    encryption_version = str(
        current_app.config.get("BANK_ACCOUNT_ENCRYPTION_KEY_VERSION") or "v1"
    )
    fingerprint_version = str(
        current_app.config.get("BANK_ACCOUNT_FINGERPRINT_KEY_VERSION") or "v1"
    )
    return BankAccountCrypto(
        encryption_keys={
            encryption_version: current_app.config.get(
                "BANK_ACCOUNT_ENCRYPTION_KEY"
            )
        },
        active_encryption_version=encryption_version,
        fingerprint_keys={
            fingerprint_version: current_app.config.get(
                "BANK_ACCOUNT_FINGERPRINT_KEY"
            )
        },
        active_fingerprint_version=fingerprint_version,
    )
