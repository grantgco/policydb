"""Shared utilities."""

from __future__ import annotations

import re


# ─── STATUS COLORS ───────────────────────────────────────────────────────────

_STATUS_COLORS: dict[str, tuple[str, str, str]] = {
    "Not Started": ("gray-100", "gray-600", "gray-300"),
    "In Progress": ("blue-100", "blue-700", "blue-300"),
    "Quoted": ("purple-100", "purple-700", "purple-300"),
    "Pending Bind": ("amber-100", "amber-700", "amber-300"),
    "Bound": ("green-100", "green-700", "green-300"),
}

_COLOR_PALETTE: list[tuple[str, str, str]] = [
    ("pink-100", "pink-700", "pink-300"),
    ("sky-100", "sky-700", "sky-300"),
    ("yellow-100", "yellow-700", "yellow-300"),
    ("rose-100", "rose-700", "rose-300"),
    ("teal-100", "teal-700", "teal-300"),
    ("indigo-100", "indigo-700", "indigo-300"),
    ("orange-100", "orange-700", "orange-300"),
    ("lime-100", "lime-700", "lime-300"),
]


def get_status_color(status: str, all_statuses: list | None = None) -> tuple[str, str, str]:
    """Return (bg, text, border) Tailwind color classes for a renewal status."""
    if status in _STATUS_COLORS:
        return _STATUS_COLORS[status]
    if all_statuses:
        custom = [s for s in all_statuses if s not in _STATUS_COLORS]
        try:
            idx = custom.index(status)
            return _COLOR_PALETTE[idx % len(_COLOR_PALETTE)]
        except ValueError:
            pass
    return ("gray-100", "gray-600", "gray-300")


# ─── CARRIER ALIASES ──────────────────────────────────────────────────────────
# Flat lookup dict built from config carrier_aliases on module load.
# Maps lowercased alias/variation → canonical carrier name.
_CARRIER_ALIASES: dict[str, str] = {}
_BASE_CARRIER_ALIASES: dict[str, str] = {}  # populated on first rebuild_carrier_aliases() call


def rebuild_carrier_aliases() -> None:
    """Rebuild _CARRIER_ALIASES from config with snapshot-then-merge pattern.

    Merge order (later overrides earlier):
    1. Base carrier aliases from config carrier_aliases (snapshotted on first call)
    2. carriers config list — each entry self-references as canonical
    3. carrier_aliases config — user-learned mappings layered on top
    """
    global _CARRIER_ALIASES, _BASE_CARRIER_ALIASES
    from policydb import config as cfg
    aliases = cfg.get("carrier_aliases", {})
    # Snapshot base on first call so repeated rebuilds always start from original
    if not _BASE_CARRIER_ALIASES:
        base: dict[str, str] = {}
        for canonical, variations in aliases.items():
            base[canonical.lower()] = canonical
            for v in variations:
                base[v.strip().lower()] = canonical
        _BASE_CARRIER_ALIASES = base
    # Start from snapshot
    merged = dict(_BASE_CARRIER_ALIASES)
    # Layer carriers config list as self-referencing canonicals
    for entry in cfg.get("carriers", []):
        merged[entry.strip().lower()] = entry.strip()
    # Layer user-learned aliases on top
    for canonical, variations in aliases.items():
        merged[canonical.lower()] = canonical
        for v in variations:
            merged[v.strip().lower()] = canonical
    _CARRIER_ALIASES = merged


def normalize_carrier(raw: str) -> str:
    """Normalize a carrier name to its canonical parent company name.

    Uses _CARRIER_ALIASES built from config. Preserves original casing
    for unknown carriers (unlike coverage types which title-case).
    Returns empty string for blank input.

    Examples:
        "Travelers Insurance"  → "Travelers"
        "ACE American"         → "Chubb"
        "Some Obscure Carrier" → "Some Obscure Carrier"
        ""                     → ""
    """
    if not raw or not raw.strip():
        return ""
    cleaned = raw.strip()
    key = cleaned.lower()
    if key in _CARRIER_ALIASES:
        return _CARRIER_ALIASES[key]
    return cleaned


# Build on module load — wrapped in try/except since config may not be
# available during unit tests that import utils directly.
try:
    rebuild_carrier_aliases()
except Exception:
    pass


# ─── COVERAGE ALIASES ─────────────────────────────────────────────────────────
# Maps common AMS abbreviations and alternate names to canonical PolicyDB values.
# Applied before fuzzy matching so "CGL" vs "General Liability" score 100, not 40.
# Moved here from reconciler.py so it can be shared with save-path normalization.

_BASE_COVERAGE_ALIASES: dict[str, str] = {}  # populated on first rebuild_coverage_aliases() call


def rebuild_coverage_aliases() -> None:
    """Merge config coverage_aliases with hardcoded _COVERAGE_ALIASES.

    Merge order (later overrides earlier):
    1. Base hardcoded aliases (snapshotted on first call)
    2. policy_types config list — each entry self-references as canonical
    3. coverage_aliases config — user-learned mappings layered on top
    """
    global _COVERAGE_ALIASES, _BASE_COVERAGE_ALIASES
    if not _BASE_COVERAGE_ALIASES:
        _BASE_COVERAGE_ALIASES = dict(_COVERAGE_ALIASES)
    from policydb import config as cfg
    merged = dict(_BASE_COVERAGE_ALIASES)
    # Layer policy_types config list as self-referencing canonicals
    for entry in cfg.get("policy_types", []):
        merged[entry.strip().lower()] = entry.strip()
    # Layer user-learned aliases on top
    config_aliases = cfg.get("coverage_aliases", {})
    for canonical, variations in config_aliases.items():
        merged[canonical.lower()] = canonical
        for v in variations:
            merged[v.strip().lower()] = canonical
    _COVERAGE_ALIASES = merged


_COVERAGE_ALIASES: dict[str, str] = {
    # ── General Liability ────────────────────────────────────────────────────
    "cgl": "General Liability",
    "gl": "General Liability",
    "general liability": "General Liability",
    "commercial general liability": "General Liability",
    "premises liability": "General Liability",
    "general liability (part of package)": "General Liability",
    "gl (part of package)": "General Liability",

    # ── Property / Builders Risk ─────────────────────────────────────────────
    # All these map to the combined canonical "Property / Builders Risk"
    "prop": "Property / Builders Risk",
    "bop": "Business Owners Policy",
    "commercial property": "Property / Builders Risk",
    "building": "Property / Builders Risk",
    "property": "Property / Builders Risk",
    "all risks property": "Property / Builders Risk",
    "all risk property": "Property / Builders Risk",
    "difference in conditions": "Property / Builders Risk",
    "dic": "Property / Builders Risk",
    "inland marine": "Inland Marine",
    "special risk": "Property / Builders Risk",
    "blended program / package": "Property / Builders Risk",
    "blended program/package": "Property / Builders Risk",
    "blended program": "Property / Builders Risk",
    "package policy": "Property / Builders Risk",

    # ── Builders Risk (Standalone) ───────────────────────────────────────────
    "builders risk": "Builders Risk (Standalone)",
    "builders all risks": "Builders Risk (Standalone)",
    "builders all risk": "Builders Risk (Standalone)",
    "installation floater": "Builders Risk (Standalone)",
    "builders risk (standalone)": "Builders Risk (Standalone)",

    # ── Equipment Breakdown ───────────────────────────────────────────────────
    "boiler & machinery": "Equipment Breakdown",
    "boiler and machinery": "Equipment Breakdown",
    "equipment breakdown": "Equipment Breakdown",

    # ── Umbrella / Excess ────────────────────────────────────────────────────
    "umb": "Umbrella / Excess",
    "excess": "Umbrella / Excess",
    "umbrella": "Umbrella / Excess",
    "excess liability": "Umbrella / Excess",
    "umbrella liability": "Umbrella / Excess",
    "excess/umbrella": "Umbrella / Excess",
    "umbrella / excess": "Umbrella / Excess",
    "umbrella and/or bumbershoot liability": "Umbrella / Excess",
    "bumbershoot": "Umbrella / Excess",
    "marine excess liability": "Umbrella / Excess",

    # ── Workers Compensation ─────────────────────────────────────────────────
    "wc": "Workers Compensation",
    "workers comp": "Workers Compensation",
    "workers' comp": "Workers Compensation",
    "workers' compensation": "Workers Compensation",
    "work comp": "Workers Compensation",
    "workers compensation": "Workers Compensation",

    # ── Commercial Auto ──────────────────────────────────────────────────────
    "ca": "Commercial Auto",
    "bap": "Commercial Auto",
    "commercial auto": "Commercial Auto",
    "business auto": "Commercial Auto",
    "hired & non-owned auto": "Commercial Auto",
    "hired and non-owned": "Commercial Auto",
    "automobile liability/physical damage (part of package)": "Commercial Auto",
    "automobile liability/physical damage": "Commercial Auto",
    "automobile/motor liability & physical damage": "Commercial Auto",
    "automobile/motor liability and physical damage": "Commercial Auto",
    "automobile/motor non-owned": "Commercial Auto",
    "auto liability": "Commercial Auto",
    "commercial automobile": "Commercial Auto",

    # ── Cyber / Tech E&O ─────────────────────────────────────────────────────
    "cyber": "Cyber / Tech E&O",
    "cyber liability": "Cyber / Tech E&O",
    "cyber / tech e&o": "Cyber / Tech E&O",
    "cyber, tech, media programs": "Cyber / Tech E&O",
    "cyber tech media": "Cyber / Tech E&O",
    "technology e&o": "Cyber / Tech E&O",
    "tech e&o": "Cyber / Tech E&O",
    "media liability": "Cyber / Tech E&O",
    "network security": "Cyber / Tech E&O",

    # ── Professional Liability / E&O ─────────────────────────────────────────
    "e&o": "Professional Liability / E&O",
    "professional liability": "Professional Liability / E&O",
    "professional liability / e&o": "Professional Liability / E&O",
    "pl": "Professional Liability / E&O",
    "errors & omissions": "Professional Liability / E&O",
    "errors and omissions": "Professional Liability / E&O",
    "professional indemnity": "Professional Liability / E&O",

    # ── Directors & Officers ─────────────────────────────────────────────────
    "d&o": "Directors & Officers",
    "directors & officers": "Directors & Officers",
    "directors and officers": "Directors & Officers",
    "directors & officers liability": "Directors & Officers",
    "directors and officers liability": "Directors & Officers",
    "excess side-a dic d&o": "Directors & Officers",
    "excess side-a d&o": "Directors & Officers",
    "excess directors & officers liability": "Directors & Officers",
    "excess directors and officers liability": "Directors & Officers",
    "excess d&o": "Directors & Officers",
    "side a d&o": "Directors & Officers",
    "fiduciary": "Directors & Officers",
    "fiduciary liability": "Directors & Officers",
    "erisa": "Directors & Officers",

    # ── EPLI ──────────────────────────────────────────────────────────────────
    "epli": "Employment Practices Liability",
    "employment practices": "Employment Practices Liability",
    "employment practices liability": "Employment Practices Liability",
    "epl": "Employment Practices Liability",

    # ── Crime / Fidelity ──────────────────────────────────────────────────────
    "crime": "Crime / Fidelity",
    "fidelity": "Crime / Fidelity",
    "crime / fidelity": "Crime / Fidelity",
    "commercial crime": "Crime / Fidelity",
    "employee dishonesty": "Crime / Fidelity",

    # ── Environmental ─────────────────────────────────────────────────────────
    "env": "Environmental",
    "environmental liability": "Environmental",
    "pollution liability": "Environmental",
    "pollution legal liability": "Environmental",
    "pll": "Environmental",
    "environmental impairment": "Environmental",

    # ── Marine ────────────────────────────────────────────────────────────────
    "marine": "Inland Marine",
    "ocean marine": "Inland Marine",
    "cargo": "Inland Marine",
    "multi-peril marine package": "Inland Marine",
    "marine package": "Inland Marine",

    # ── Railroad Protective ───────────────────────────────────────────────────
    "railroad protective liability": "Railroad Protective",
    "railroad protective": "Railroad Protective",

    # ── OCIP / Wrap-Up ────────────────────────────────────────────────────────
    "ocip": "OCIP",
    "ccip": "OCIP",
    "wrap-up": "OCIP",
    "wrap up": "OCIP",
    "owner controlled insurance program": "OCIP",
    "contractor controlled insurance program": "OCIP",

    # ── Additional GL variants ────────────────────────────────────────────────
    "premises & operations": "General Liability",
    "premises and operations": "General Liability",
    "p&o": "General Liability",

    # ── Additional WC variants ────────────────────────────────────────────────
    "wc/el": "Workers Compensation",
    "el": "Workers Compensation",
    "employer's liability": "Workers Compensation",
    "employers liability": "Workers Compensation",
    "employer liability": "Workers Compensation",

    # ── Additional Property/Package variants ─────────────────────────────────
    "cpp": "Property / Builders Risk",
    "commercial package": "Property / Builders Risk",
    "commercial package policy": "Property / Builders Risk",
    "monoline property": "Property / Builders Risk",
    "businessowners": "Business Owners Policy",
    "businessowners policy": "Business Owners Policy",
    "business owners policy": "Business Owners Policy",

    # ── Additional Excess/Umbrella variants ──────────────────────────────────
    "xs": "Umbrella / Excess",
    "xs liability": "Umbrella / Excess",
    "follow form excess": "Umbrella / Excess",
    "follow form": "Umbrella / Excess",
    "catastrophe excess": "Umbrella / Excess",

    # ── Personal lines ────────────────────────────────────────────────────────
    "personal auto": "Personal Auto",
    "pa": "Personal Auto",
    "homeowners": "Homeowners",
    "ho": "Homeowners",
    "ho-3": "Homeowners",
    "ho3": "Homeowners",

    # ── Additional aliases for common AMS export variants ──────────────────
    "bop policy": "Business Owners Policy",
    "management liability": "Directors & Officers",
    "network security & privacy": "Cyber / Tech E&O",
    "network security and privacy": "Cyber / Tech E&O",
    "privacy liability": "Cyber / Tech E&O",
    "hnoa": "Commercial Auto",
    "hired and non-owned auto": "Commercial Auto",
    "hired & non-owned": "Commercial Auto",
    "garage liability": "Commercial Auto",
    "garagekeepers": "Commercial Auto",
    "liquor liability": "General Liability",
    "pollution": "Environmental",
    "environmental": "Environmental",
    "surety": "Surety Bond",
    "surety bond": "Surety Bond",
    "contract bond": "Surety Bond",
    "performance bond": "Surety Bond",
    "product liability": "General Liability",
    "products liability": "General Liability",
    "products/completed operations": "General Liability",
    "commercial general liability (part of package)": "General Liability",
    "owners & contractors protective": "General Liability",
    "ocp": "General Liability",

    # ── Carrier statement form numbers ──────────────────────────────────────
    "cg 00 01": "General Liability",
    "cg0001": "General Liability",
    "cp 00 10": "Property / Builders Risk",
    "cp0010": "Property / Builders Risk",
    "cp 00 30": "Property / Builders Risk",
    "cp0030": "Property / Builders Risk",
    "ca 00 01": "Commercial Auto",
    "ca0001": "Commercial Auto",
    "wc 00 00": "Workers Compensation",
    "wc0000": "Workers Compensation",
    "im 00 01": "Inland Marine",
    "im0001": "Inland Marine",

    # ── Carrier abbreviations & combined coverages ──────────────────────────
    "cal": "Commercial Auto",
    "prop/cas": "Property / Builders Risk",
    "gl/property package": "Property / Builders Risk",
    "gl/prop package": "Property / Builders Risk",
    "property/casualty": "Property / Builders Risk",
    "commercial lines package": "Property / Builders Risk",
    "smp": "Property / Builders Risk",
    "special multi-peril": "Property / Builders Risk",
    "special multi peril": "Property / Builders Risk",
    "mppl": "Professional Liability / E&O",
}

# Build coverage aliases on module load (after dict is defined).
try:
    rebuild_coverage_aliases()
except Exception:
    pass


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


_MATCHING_SUFFIX_RE = re.compile(
    r'\s*(,?\s*)?(LLC|LLP|LP|PLLC|Inc\.?|Corp\.?|Corporation|Co\.?|'
    r'Ltd\.?|Limited|Company|Enterprises?)\b[.,]?$',
    re.IGNORECASE,
)


def normalize_client_name_for_matching(raw: str | None) -> str:
    """Strip legal suffixes entirely, collapse whitespace, title case.

    For fuzzy matching comparison only — never write result to DB.
    "Acme Corp." → "Acme", "AVALONBAY COMMUNITIES INC" → "Avalonbay Communities"
    """
    if not raw or not str(raw).strip():
        return ""
    name = _MATCHING_SUFFIX_RE.sub("", str(raw).strip()).strip()
    name = " ".join(name.split())  # collapse whitespace
    words = name.split()
    return " ".join(w if (len(w) <= 3 and w.isupper()) else w.capitalize() for w in words)


_PLACEHOLDER_POLICY_NUMBERS = {
    "999", "TBD", "TBA", "PENDING", "NA", "N/A", "NONE", "XXX", "000", "123",
    "NEW", "RENEWAL", "RENEW", "QUOTE", "QUOTED", "APPLIED",
}


def normalize_policy_number_for_matching(raw: str | None) -> str:
    """Strip formatting and placeholders for fuzzy matching comparison only.

    Never write result to DB — use normalize_policy_number() for that.
    "POL-GL-2025-441" → "POLGL2025441"
    """
    if not raw or not str(raw).strip():
        return ""
    cleaned = re.sub(r'[\s\-/.]', '', str(raw).strip()).upper()
    cleaned = cleaned.lstrip('0') or ''
    if cleaned in _PLACEHOLDER_POLICY_NUMBERS:
        return ""
    return cleaned


def parse_currency(value) -> float:
    """Strip currency symbols, commas; return float. Returns 0.0 on error.

    Promoted from importer._parse_currency — shared by reconciler and importer.
    """
    if not value or not str(value).strip():
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


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
                  activity_id: int = 0, thread_id: int = 0,
                  rfi_uid: str = "") -> str:
    """Build hierarchical email reference tag.

    Hierarchy: Client → Location → Policy → Activity/Correspondence/RFI
    Format:    CN{number}-L{project_id}-{policy_uid}-A{activity_id}
               CN{number}-L{project_id}-{policy_uid}-COR{thread_id}
               CN{number}-RFI{nn}

    Priority: rfi_uid > thread_id > activity_id

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
        build_ref_tag(cn_number="123456789",
                      rfi_uid="CN123456789-RFI01")                    → "CN123456789-RFI01"
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
    # RFI UID takes precedence, then thread ID, then activity ID
    if rfi_uid:
        # Extract RFI suffix from full UID (e.g., "CN123-RFI01" → "RFI01")
        rfi_match = re.search(r'(RFI\d+)', rfi_uid, re.IGNORECASE)
        if rfi_match:
            tag += f"-{rfi_match.group(1).upper()}"
    elif thread_id:
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
