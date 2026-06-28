from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo


_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_QR_KEYS = {
    "bank", "banco", "amount", "monto", "value", "valor", "total",
    "date", "fecha", "transaction_at", "destination_account_suffix",
    "account_suffix", "cuenta_destino", "receipt_number", "comprobante",
    "reference", "referencia", "transaction", "transaccion",
}
_BANK_ALIASES = {
    "BANCO_PICHINCHA": "BANCO_PICHINCHA",
    "PICHINCHA": "BANCO_PICHINCHA",
    "BANCO_DE_GUAYAQUIL": "BANCO_DE_GUAYAQUIL",
    "BANCO_GUAYAQUIL": "BANCO_DE_GUAYAQUIL",
    "GUAYAQUIL": "BANCO_DE_GUAYAQUIL",
    "BANCO_DEL_PACIFICO": "BANCO_DEL_PACIFICO",
    "BANCO_PACIFICO": "BANCO_DEL_PACIFICO",
    "PACIFICO": "BANCO_DEL_PACIFICO",
    "PRODUBANCO": "PRODUBANCO",
    "BANCO_PRODUBANCO": "PRODUBANCO",
    "PROMERICA": "PROMERICA",
    "BANCO_PROMERICA": "PROMERICA",
    "BANCO_INTERNACIONAL": "BANCO_INTERNACIONAL",
    "BANCO_BOLIVARIANO": "BANCO_BOLIVARIANO",
    "BOLIVARIANO": "BANCO_BOLIVARIANO",
    "BANCO_SOLIDARIO": "BANCO_SOLIDARIO",
    "BANCO_DEL_AUSTRO": "BANCO_DEL_AUSTRO",
    "BANCO_AUSTRO": "BANCO_DEL_AUSTRO",
    "AUSTRO": "BANCO_DEL_AUSTRO",
}
_EXPLICIT_BANK_ALIASES = {
    "INTERNACIONAL": "BANCO_INTERNACIONAL",
    "SOLIDARIO": "BANCO_SOLIDARIO",
}


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())


def normalize_bank(value: str | None, *, explicit_field: bool = False) -> str | None:
    if not value:
        return None
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        char for char in ascii_value if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")
    normalized = re.sub(r"_(?:C_A|S_A)$", "", normalized)
    canonical = _BANK_ALIASES.get(normalized)
    if canonical is None and explicit_field:
        canonical = _EXPLICIT_BANK_ALIASES.get(normalized)
    return (canonical or normalized)[:100] or None


def normalize_identifier(value: str | None, *, max_length: int) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", value)
    return normalized[:max_length] or None


def normalize_account_suffix(value: str | None) -> str | None:
    digits = "".join(char for char in (value or "") if char.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def parse_money(value: str | None) -> Decimal | None:
    if not value:
        return None
    candidate = re.sub(r"[^0-9,.-]", "", value).strip("-")
    if not candidate or not re.search(r"\d", candidate):
        return None
    last_comma = candidate.rfind(",")
    last_dot = candidate.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        decimal_mark = "," if last_comma > last_dot else "."
        thousands_mark = "." if decimal_mark == "," else ","
        candidate = candidate.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif last_comma >= 0:
        tail = len(candidate) - last_comma - 1
        candidate = candidate.replace(",", "." if tail in {1, 2} else "")
    elif last_dot >= 0:
        tail = len(candidate) - last_dot - 1
        if tail not in {1, 2}:
            candidate = candidate.replace(".", "")
    try:
        amount = Decimal(candidate).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def parse_transaction_date(value: str | None, timezone_name: str) -> datetime | None:
    if not value:
        return None
    text = normalize_text(value).lower()
    iso_candidate = text.replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed
    except ValueError:
        pass
    month_match = re.search(
        r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b", text
    )
    if month_match:
        normalized_month = normalize_bank(month_match.group(2))
        month = _MONTHS.get(normalized_month.lower()) if normalized_month else None
        if month:
            try:
                return datetime(
                    int(month_match.group(3)), month, int(month_match.group(1)),
                    tzinfo=ZoneInfo(timezone_name),
                )
            except ValueError:
                return None
    for pattern, order in (
        (r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", "ymd"),
        (r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", "dmy"),
    ):
        match = re.search(pattern, text)
        if match:
            values = [int(item) for item in match.groups()]
            year, month, day = (
                values if order == "ymd" else (values[2], values[1], values[0])
            )
            try:
                return datetime(year, month, day, tzinfo=ZoneInfo(timezone_name))
            except ValueError:
                return None
    return None


def structured_qr_fields(payload: str, *, max_chars: int) -> dict[str, str]:
    if not payload or len(payload) > max_chars:
        return {}
    raw: dict[str, object] = {}
    stripped = payload.strip()
    if stripped.startswith("{"):
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, dict):
                raw = decoded
        except (json.JSONDecodeError, ValueError):
            return {}
    else:
        parsed_url = urlsplit(stripped)
        source = parsed_url.query if parsed_url.scheme else stripped
        pairs = parse_qsl(
            source.replace(";", "&").replace("\n", "&"),
            keep_blank_values=False,
        )
        if not pairs and "=" in source:
            pairs = [
                tuple(part.split("=", 1))
                for part in re.split(r"[;&\n]", source)
                if "=" in part
            ]
        raw = dict(pairs)
    result: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = normalize_bank(str(key))
        if (
            normalized_key
            and normalized_key.lower() in _QR_KEYS
            and isinstance(value, (str, int, float))
        ):
            result[normalized_key.lower()] = str(value)[:200]
    return result


_MONEY_NUMBER = r"[0-9]+(?:[ .,'\u00a0][0-9]{2,3})*(?:[.,][0-9]{1,2})?"
_AMOUNT_LABEL = re.compile(
    r"(?i)\b(?:monto|valor|total|importe|pago(?:\s+total)?|transferencia\s+exitosa)\b"
)
_NON_AMOUNT_CONTEXT = re.compile(
    r"(?i)\b(?:cuenta|c[eé]dula|identificaci[oó]n|fecha|comprobante|"
    r"referencia|transacci[oó]n)\b"
)


def extract_labeled_amount_result(text: str) -> tuple[Decimal | None, bool]:
    """Return the best monetary candidate and whether top evidence is ambiguous."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and normalize_text(text):
        lines = [normalize_text(text)]
    scored: list[tuple[int, Decimal]] = []
    for index, line in enumerate(lines):
        label_match = _AMOUNT_LABEL.search(line)
        has_currency = bool(re.search(r"(?i)(?:\bUSD\b|\$)", line))
        sensitive = bool(_NON_AMOUNT_CONTEXT.search(line)) and label_match is None
        sources: list[tuple[str, int]] = []
        if label_match:
            sources.append((line[label_match.end():], 6 + (2 if has_currency else 0)))
            if index + 1 < len(lines):
                sources.append((lines[index + 1], 5))
        if has_currency:
            sources.append((line, 4))
        for source, score in sources:
            if sensitive:
                score -= 8
            for match in re.finditer(_MONEY_NUMBER, source):
                amount = parse_money(match.group(0))
                if amount is not None and score > 0:
                    scored.append((score, amount))
    if not scored:
        return None, False
    highest = max(score for score, _ in scored)
    top = {amount for score, amount in scored if score == highest}
    return (next(iter(top)), False) if len(top) == 1 else (None, True)


def extract_labeled_amount(text: str) -> Decimal | None:
    return extract_labeled_amount_result(text)[0]


def extract_account_suffix(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and normalize_text(text):
        lines = [normalize_text(text)]
    label = re.compile(r"(?i)(?:cuenta\s+(?:destino|receptora)|destino)\b")
    for index, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        candidates = [re.sub(r"^[\s:#.-]+", "", line[match.end():])]
        if index + 1 < len(lines):
            candidates.append(lines[index + 1])
        for candidate in candidates:
            value_match = re.match(r"([xX*\d -]{4,34})", candidate)
            suffix = normalize_account_suffix(
                value_match.group(1) if value_match else None
            )
            if suffix:
                return suffix
    return None


def extract_receipt_number(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines and normalize_text(text):
        lines = [normalize_text(text)]
    label = re.compile(
        r"(?i)(?:(?:n(?:\s*[.º°o])*|nro\.?|no\.?|n[uú]mero)\s*"
        r"(?:de\s*)?(?:comprobante|transacci[oó]n)|comprobante|referencia)\b"
    )
    for index, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        tail = re.sub(r"^[\s:#.-]+", "", line[match.end():])
        candidates = [tail]
        if index + 1 < len(lines):
            candidates.append(lines[index + 1])
        for candidate in candidates:
            value_match = re.match(r"([A-Za-z0-9][A-Za-z0-9_-]{2,99})", candidate)
            if value_match:
                return normalize_identifier(value_match.group(1), max_length=100)
    return None
