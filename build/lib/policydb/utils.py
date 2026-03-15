"""Shared utilities."""

from __future__ import annotations


def format_phone(raw: str, default_region: str = "US") -> str:
    """
    Parse and format a phone number string to E.164-adjacent national format.

    Returns the formatted string, or the original stripped string if parsing fails.
    Examples:
        "5125551234"       → "(512) 555-1234"
        "512-555-1234"     → "(512) 555-1234"
        "+1 512 555 1234"  → "(512) 555-1234"
        "invalid"          → "invalid"
    """
    if not raw or not raw.strip():
        return raw
    try:
        import phonenumbers
        parsed = phonenumbers.parse(raw.strip(), default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
    except Exception:
        pass
    return raw.strip()
