from __future__ import annotations

from urllib.parse import unquote, urlsplit, urlunsplit


def safe_local_redirect(value: str | None, *, fallback: str) -> str:
    """Return a local redirect target, preserving its query string."""
    candidate = (value or "").strip()
    if not candidate or not candidate.startswith("/"):
        return fallback

    decoded = candidate
    for _ in range(3):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded

    if (
        candidate.startswith("//")
        or decoded.startswith("//")
        or "\\" in candidate
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        return fallback

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return fallback
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return fallback
    return urlunsplit(("", "", parsed.path, parsed.query, ""))
