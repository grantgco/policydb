"""Shared utilities."""

from __future__ import annotations

import re


def build_ref_tag(cn_number: str = "", client_id: int = 0,
                  policy_uid: str = "", project_id: int = 0,
                  activity_id: int = 0, thread_id: int = 0) -> str:
    """Build hierarchical email reference tag.

    Hierarchy: Client → Location → Policy → Activity/Correspondence
    Format:    CN{number}-L{project_id}-{policy_uid}-A{activity_id}
               CN{number}-L{project_id}-{policy_uid}-COR{thread_id}

    When thread_id is provided, it replaces the activity_id suffix (the thread
    IS the activity chain, so individual activity ID is redundant).

    Examples:
        build_ref_tag(cn_number="123456789")                          → "CN123456789"
        build_ref_tag(cn_number="CN123456789")                        → "CN123456789"
        build_ref_tag(cn_number="123456789", project_id=5)            → "CN123456789-L5"
        build_ref_tag(cn_number="123456789", project_id=5,
                      policy_uid="POL-042")                           → "CN123456789-L5-POL042"
        build_ref_tag(cn_number="123456789", project_id=5,
                      policy_uid="POL-042", activity_id=789)          → "CN123456789-L5-POL042-A789"
        build_ref_tag(cn_number="123456789", policy_uid="POL-042",
                      thread_id=42)                                   → "CN123456789-POL042-COR42"
        build_ref_tag(cn_number="123456789", policy_uid="POL-042",
                      activity_id=789, thread_id=42)                  → "CN123456789-POL042-COR42"
    """
    # Guard against the string "None" (from str(None) data corruption)
    if cn_number in (None, "None", "none", ""):
        cn_number = ""
    # Strip leading "CN" prefix if already present to avoid duplication
    cn_clean = re.sub(r'^[Cc][Nn]', '', cn_number) if cn_number else ""
    tag = f"CN{cn_clean}" if cn_clean else f"C{client_id}"
    if project_id:
        tag += f"-L{project_id}"
    if policy_uid:
        tag += f"-{policy_uid.replace('-', '')}"
    # Thread ID takes precedence over activity ID
    if thread_id:
        tag += f"-COR{thread_id}"
    elif activity_id:
        tag += f"-A{activity_id}"
    return tag


def round_duration(raw) -> float | None:
    """Round a duration value up to the nearest 0.1 hour.

    Examples:
        "0.33"  → 0.4
        "1.25"  → 1.3
        "0.1"   → 0.1
        "2.0"   → 2.0
        ""      → None
    """
    if not raw or not str(raw).strip():
        return None
    try:
        import math
        v = float(raw)
        if v <= 0:
            return None
        return math.ceil(v * 10) / 10
    except (TypeError, ValueError):
        return None


def format_fein(raw: str) -> str:
    """
    Parse and format a Federal Employer Identification Number (FEIN / EIN).

    Strips all non-digit characters, then formats as XX-XXXXXXX.
    Returns empty string for blank input. Returns the cleaned digits
    with the dash if exactly 9 digits are found; otherwise returns
    whatever digits were extracted (partial input preserved).

    Examples:
        "12-3456789"    → "12-3456789"
        "123456789"     → "12-3456789"
        "12 345 6789"   → "12-3456789"
        "12.345.6789"   → "12-3456789"
        "EIN: 12-3456789" → "12-3456789"
        ""              → ""
        "abc"           → ""
    """
    if not raw or not raw.strip():
        return ""
    digits = re.sub(r'\D', '', raw.strip())
    if not digits:
        return ""
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:]}"
    # Partial / invalid length — return digits as-is so user sees what was saved
    return digits


def clean_email(raw: str) -> str:
    """
    Extract a clean email address from a pasted string.

    Handles common formats from Outlook, Gmail, etc.:
        "Jane Doe <jane@example.com>"  → "jane@example.com"
        "<jane@example.com>"           → "jane@example.com"
        "mailto:jane@example.com"      → "jane@example.com"
        "jane@example.com;"            → "jane@example.com"
        " jane@example.com "           → "jane@example.com"
        '"jane@example.com"'           → "jane@example.com"
    """
    if not raw or not raw.strip():
        return raw
    s = raw.strip()
    # Strip mailto: prefix
    if s.lower().startswith("mailto:"):
        s = s[7:]
    # Extract email from angle brackets: "Name <email>" or "<email>"
    match = re.search(r'<([^>]+@[^>]+)>', s)
    if match:
        s = match.group(1)
    # Try to extract a bare email address from surrounding text like "(e) user@domain.com"
    email_match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', s)
    if email_match:
        return email_match.group(0).lower()
    # Strip surrounding quotes, semicolons, commas, whitespace
    s = s.strip(' \t\n\r"\';,<>()')
    # Final sanity: only return if it looks like an email
    if "@" in s and "." in s.split("@")[-1]:
        return s.lower()
    # Fallback: return cleaned string as-is
    return s.strip().lower() if s.strip() else raw


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
            # Use NATIONAL for domestic (US/CA), INTERNATIONAL for foreign numbers
            if parsed.country_code in (1,):  # NANP (US, CA, etc.)
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.NATIONAL
                )
            else:
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
    except Exception:
        pass
    return raw.strip()


def csv_response(rows: list[dict], filename: str, columns: list[str] | None = None):
    """Build a CSV download Response from a list of dicts.

    Args:
        rows: list of dicts (each dict = one row)
        filename: download filename (e.g. "AcmeCorp_Activities_2026-03-18.csv")
        columns: optional ordered list of column names. If None, uses keys from first row.
    """
    import csv
    import io
    from fastapi.responses import Response

    buf = io.StringIO()
    if not rows:
        buf.write("No data\n")
    else:
        headers = columns or list(rows[0].keys())
        writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
