"""Shared utilities."""

from __future__ import annotations

import re


# ─── COVERAGE ALIASES ─────────────────────────────────────────────────────────
# Placeholder; full dict is populated in Task 2 (moved from reconciler.py).
# Maps lowercase alias → canonical policy type name.
_COVERAGE_ALIASES: dict[str, str] = {}


# ─── LEGAL SUFFIX MAP ─────────────────────────────────────────────────────────
# Maps lowercase suffix keyword → canonical formatted suffix (with or without period).
_LEGAL_SUFFIX_MAP: dict[str, str] = {
    "inc": "Inc.",
    "llc": "LLC",
    "corp": "Corp.",
    "ltd": "Ltd.",
    "co": "Co.",
    "lp": "LP",
    "llp": "LLP",
    "pllc": "PLLC",
    "pc": "PC",
    "na": "N.A.",
}

# ─── STATE NAME → ABBREVIATION ────────────────────────────────────────────────
_STATE_NAME_TO_ABBR: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}


def normalize_coverage_type(raw: str) -> str:
    """Normalize a policy type / line of business name to a canonical form.

    Looks up the lowercase-stripped value in _COVERAGE_ALIASES. If found,
    returns the canonical name. If not found, title-cases the stripped value.
    Returns empty string for blank input.

    Examples:
        "cgl"              → "General Liability"
        "CGL"              → "General Liability"
        "cyber liability"  → "Cyber / Tech E&O"  (after Task 2 aliases loaded)
        ""                 → ""
    """
    if not raw or not raw.strip():
        return ""
    key = raw.strip().lower()
    return _COVERAGE_ALIASES.get(key, raw.strip().title())


def normalize_policy_number(raw: str) -> str:
    """Uppercase and strip a policy number. Preserves all formatting characters.

    Examples:
        "pol-123"      → "POL-123"
        "  abc.456  "  → "ABC.456"
        ""             → ""

    Note: This is distinct from the reconciler's _normalize_policy_number, which
    strips formatting for fuzzy matching. This function only uppercases + trims.
    """
    if not raw or not raw.strip():
        return ""
    return raw.strip().upper()


def normalize_client_name(raw: str) -> str:
    """Collapse whitespace, title-case words, and normalize legal suffixes.

    Rules:
    - Collapse multiple spaces to one; strip leading/trailing whitespace.
    - Title-case each word UNLESS the word is 2-3 characters and already all-uppercase
      (preserves acronyms like "US", "ABC", "LLC").
    - Recognize the last word as a legal suffix (inc, llc, corp, etc.) and
      replace it with the canonical formatted form from _LEGAL_SUFFIX_MAP.

    Examples:
        "acme corp"             → "Acme Corp."
        "ACME HOLDINGS"         → "Acme Holdings"
        "US  Steel  inc"        → "US Steel Inc."
        "  delta   services   llc  " → "Delta Services LLC"
        "ABC Corp"              → "ABC Corp."
        "US Steel"              → "US Steel"
        ""                      → ""
    """
    if not raw or not raw.strip():
        return ""

    # Collapse internal whitespace
    words = raw.strip().split()

    # Check if the last word is a known legal suffix
    last_lower = words[-1].lower().rstrip(".,")
    suffix_canonical = _LEGAL_SUFFIX_MAP.get(last_lower)

    # Determine range of words to title-case (all except last if it's a suffix)
    if suffix_canonical is not None:
        body_words = words[:-1]
    else:
        body_words = words
        suffix_canonical = None

    def _title_word(w: str) -> str:
        """Title-case a single word, preserving all-uppercase acronyms of 2-3 chars."""
        if len(w) <= 3 and w.isupper():
            return w
        return w.capitalize()

    titled = [_title_word(w) for w in body_words]

    if suffix_canonical is not None:
        titled.append(suffix_canonical)

    return " ".join(titled)


def format_zip(raw: str) -> str:
    """Strip non-digits and format as 5-digit or 5+4-digit ZIP code.

    Examples:
        "78701"      → "78701"
        "787014567"  → "78701-4567"
        "787"        → "787"          (partial, returned as-is)
        "78701-AB"   → "78701"        (non-digits stripped)
        ""           → ""
    """
    if not raw or not raw.strip():
        return ""
    digits = re.sub(r'\D', '', raw.strip())
    if not digits:
        return ""
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    if len(digits) == 5:
        return digits
    # Partial or non-standard length — return whatever digits we have
    return digits


def format_state(raw: str) -> str:
    """Normalize a US state to its 2-letter uppercase abbreviation.

    Accepts 2-letter abbreviations (any case) or full state names.
    Unknown 2-letter codes are returned uppercased. Unknown full names
    are returned as-is (uppercased). Empty input returns empty string.

    Examples:
        "TX"     → "TX"
        "tx"     → "TX"
        "Texas"  → "TX"
        "texas"  → "TX"
        "XX"     → "XX"
        ""       → ""
    """
    if not raw or not raw.strip():
        return ""
    stripped = raw.strip()
    # 2-letter abbreviation
    if len(stripped) == 2:
        return stripped.upper()
    # Full name lookup
    lookup = stripped.lower()
    return _STATE_NAME_TO_ABBR.get(lookup, stripped.upper())


def format_city(raw: str) -> str:
    """Title-case a city name and collapse internal whitespace.

    Examples:
        "austin"         → "Austin"
        "  san   antonio  " → "San Antonio"
        "NEW YORK"       → "New York"
        ""               → ""
    """
    if not raw or not raw.strip():
        return ""
    words = raw.strip().split()
    return " ".join(w.capitalize() for w in words)


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


def parse_currency_with_magnitude(raw) -> float:
    """Parse a currency value with optional magnitude suffix (K, M, B).

    Examples:
        "$15M" → 15000000.0
        "$800K" → 800000.0
        "$1.2B" → 1200000000.0
        "$15,000,000" → 15000000.0
    """
    if not raw:
        return 0.0
    s = str(raw).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    multiplier = 1
    if s[-1].upper() == "K":
        multiplier = 1_000
        s = s[:-1]
    elif s[-1].upper() == "M":
        multiplier = 1_000_000
        s = s[:-1]
    elif s[-1].upper() == "B":
        multiplier = 1_000_000_000
        s = s[:-1]
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return 0.0


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
