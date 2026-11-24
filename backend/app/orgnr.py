"""Swedish organisation number helpers."""

from __future__ import annotations

import re

_ORG_LABEL = re.compile(
    r"(?i)\borg(?:anisations)?\.?\s*(?:nr|nummer|no|number)\b\.?\s*:?\s*(\d{6})\s?[-–]?\s?(\d{4})\b"
)


def normalize(value: str) -> str:
    """Return the 10-digit form (digits only). Accepts 'NNNNNN-NNNN' and 12-digit forms."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits[:2] in ("16", "19", "20"):
        digits = digits[2:]
    return digits if len(digits) == 10 else ""


def luhn_ok(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def format_orgnr(digits: str) -> str:
    return f"{digits[:6]}-{digits[6:]}" if len(digits) == 10 else digits


def is_personal_number(digits: str) -> bool | None:
    """True if the number looks like a personnummer (sole trader), False if a legal entity.

    Legal entities have a third digit >= 2, while personnummer use month digits (01-12)
    so the third digit is 0 or 1. Returns None when the number is invalid.
    """
    if not luhn_ok(digits):
        return None
    return digits[2] in ("0", "1")


def find_in_text(text: str) -> str:
    """Find an organisation number that is labelled 'Org.nr' (or similar) in page text."""
    for m in _ORG_LABEL.finditer(text or ""):
        digits = m.group(1) + m.group(2)
        if luhn_ok(digits):
            return digits
    return ""
