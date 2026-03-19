# Field-Level Data Normalization — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** Normalize-on-save for coverage types, policy numbers, client names, addresses. Fuzzy duplicate detection for clients and contacts. Data hygiene migration for existing records.

---

## Problem Statement

PolicyDB normalizes phone numbers and emails on save (`format_phone()`, `clean_email()`) but most other fields are stored as-is. This creates data inconsistency:

- Coverage types: `"cgl"`, `"CGL"`, `"General Liability"` all stored as distinct values
- Client names: `"Acme Corp"` and `"Acme Corporation"` become separate clients on import
- Policy numbers: `"pol-123"` and `"POL-123"` stored differently
- Addresses: ZIP codes accept any text, state codes not validated server-side, no formatting
- No duplicate detection for clients or contacts on create

The reconciler handles some of this at match time (250+ coverage aliases, client name normalization, policy number stripping) but none of it is applied at save time — so the database accumulates dirty data that views, reports, and grouping queries treat as distinct.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Normalization strategy | Normalize once on save, store clean | Prevents dirty data from entering. Same pattern as format_phone() |
| Feedback | Flash cell green when server reformats (existing pattern) | Visual feedback, no audit trail overhead |
| Coverage type normalization | Auto-normalize via alias lookup on all save paths | 250+ aliases already exist in reconciler; apply them at write time |
| Policy number normalization | Uppercase + trim only | Carrier formatting (dashes, dots) is meaningful; don't strip it |
| Client name normalization | Trim, collapse spaces, title-case, normalize legal suffixes | Clean formatting without stripping legal entity identifiers |
| Address normalization | Best-effort format + flash, never reject | Insurance data is often partial; clean what you can, don't block |
| Duplicate detection | Fuzzy warning (WRatio >= 85), don't block | False merges are dangerous; warnings are cheap and catch obvious dupes |
| Contact duplicates | Same pattern as clients | User flagged this as equally important |
| Coverage alias location | Move `_COVERAGE_ALIASES` from reconciler.py to utils.py | Clean dependency direction; one source of truth |
| Existing data | One-time hygiene migration | Bring existing records in line with new normalization rules |

---

## 1. New Normalization Functions

All added to `src/policydb/utils.py`, following the same contract as `format_phone()`: `str in → str out`, never raise, never reject.

### `normalize_coverage_type(raw: str) -> str`

```python
def normalize_coverage_type(raw: str) -> str:
    """Normalize a coverage/policy type to its canonical name.

    Uses _COVERAGE_ALIASES (250+ mappings) for known aliases,
    falls back to title-casing unknown values.
    """
    if not raw or not raw.strip():
        return ""
    cleaned = raw.strip()
    key = cleaned.lower()
    if key in _COVERAGE_ALIASES:
        return _COVERAGE_ALIASES[key]
    return cleaned.title()
```

### `normalize_policy_number(raw: str) -> str`

```python
def normalize_policy_number(raw: str) -> str:
    """Uppercase and trim a policy number. Preserves carrier formatting."""
    if not raw or not raw.strip():
        return ""
    return raw.strip().upper()
```

### `normalize_client_name(raw: str) -> str`

```python
import re

_LEGAL_SUFFIX_MAP = {
    "inc": "Inc.", "inc.": "Inc.", "incorporated": "Inc.",
    "llc": "LLC", "l.l.c.": "LLC", "l.l.c": "LLC",
    "corp": "Corp.", "corp.": "Corp.", "corporation": "Corp.",
    "ltd": "Ltd.", "ltd.": "Ltd.", "limited": "Ltd.",
    "co": "Co.", "co.": "Co.", "company": "Co.",
    "lp": "LP", "l.p.": "LP",
    "llp": "LLP", "l.l.p.": "LLP",
    "pllc": "PLLC", "pc": "PC", "p.c.": "PC",
    "na": "N.A.", "n.a.": "N.A.",
}

def normalize_client_name(raw: str) -> str:
    """Normalize a client/company name: trim, collapse spaces,
    title-case words, normalize legal suffixes."""
    if not raw or not raw.strip():
        return ""
    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', raw.strip())
    # Split into words, title-case each, but preserve short all-caps (2-4 chars)
    words = []
    for w in name.split():
        low = w.lower().rstrip('.,')
        if low in _LEGAL_SUFFIX_MAP:
            words.append(_LEGAL_SUFFIX_MAP[low])
        elif w.isupper() and len(w) <= 4:
            words.append(w)  # preserve acronyms like "ABC", "US"
        else:
            words.append(w.title() if w.islower() else w)
    return " ".join(words)
```

### `format_zip(raw: str) -> str`

```python
def format_zip(raw: str) -> str:
    """Format a ZIP code: strip to digits, format as 5 or 9 digit."""
    if not raw or not raw.strip():
        return ""
    digits = re.sub(r'\D', '', raw.strip())
    if len(digits) == 5:
        return digits
    elif len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    elif len(digits) > 0:
        return digits  # partial — return what we have
    return raw.strip()  # no digits found — return as-is
```

### `format_state(raw: str) -> str`

```python
_STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

def format_state(raw: str) -> str:
    """Normalize state to 2-letter abbreviation."""
    if not raw or not raw.strip():
        return ""
    cleaned = raw.strip()
    # Already a 2-letter code
    if len(cleaned) == 2:
        return cleaned.upper()
    # Full state name lookup
    lookup = cleaned.lower()
    if lookup in _STATE_NAME_TO_ABBR:
        return _STATE_NAME_TO_ABBR[lookup]
    return cleaned.upper()
```

### `format_city(raw: str) -> str`

```python
def format_city(raw: str) -> str:
    """Title-case and clean a city name."""
    if not raw or not raw.strip():
        return ""
    return re.sub(r'\s+', ' ', raw.strip()).title()
```

---

## 2. Coverage Aliases Relocation

**Move `_COVERAGE_ALIASES` from `src/policydb/reconciler.py` to `src/policydb/utils.py`.**

The dict (250+ entries) moves to utils.py as a module-level constant. The reconciler's `_normalize_coverage()` function is updated to import from utils:

```python
# In reconciler.py — replace the local dict with:
from policydb.utils import _COVERAGE_ALIASES, normalize_coverage_type

def _normalize_coverage(value: str) -> str:
    return normalize_coverage_type(value)
```

This makes the reconciler a consumer of the shared normalization, not the owner.

---

## 3. Save Path Integration

Every save path runs the appropriate normalizer before INSERT/UPDATE. Follows the existing `format_phone()` pattern — normalize in Python, save the clean value, return `{"ok": true, "formatted": "..."}` for PATCH endpoints.

### Policies

**Files:** `src/policydb/web/routes/policies.py`

| Field | Normalizer | Save paths |
|-------|-----------|------------|
| `policy_type` | `normalize_coverage_type()` | `policy_new_post`, `policy_edit_post`, inline row edit PATCH |
| `policy_number` | `normalize_policy_number()` | Same |
| `exposure_address` | `.strip()` (trim only) | `policy_new_post`, `policy_edit_post`, project header save |
| `exposure_city` | `format_city()` | Same |
| `exposure_state` | `format_state()` | Same |
| `exposure_zip` | `format_zip()` | Same |

### Clients

**Files:** `src/policydb/web/routes/clients.py`

| Field | Normalizer | Save paths |
|-------|-----------|------------|
| `name` | `normalize_client_name()` | Client create, client edit |
| `exposure_city` | `format_city()` | Project header save |
| `exposure_state` | `format_state()` | Project header save |
| `exposure_zip` | `format_zip()` | Project header save |

### Reconcile

**Files:** `src/policydb/web/routes/reconcile.py`

| Field | Normalizer | Save paths |
|-------|-----------|------------|
| `policy_type` | `normalize_coverage_type()` | `reconcile_create`, `batch_create`, `batch_create_program` |
| `policy_number` | `normalize_policy_number()` | `reconcile_create`, `batch_create` |

### Importer

**Files:** `src/policydb/importer.py`

| Field | Normalizer | Applied in |
|-------|-----------|------------|
| `policy_type` | `normalize_coverage_type()` | `PolicyImporter.import_file()` after column mapping |
| `policy_number` | `normalize_policy_number()` | Same |
| `client_name` | `normalize_client_name()` | Same (before client lookup/creation) |

### PATCH Cell-Save Endpoints

All contenteditable matrix PATCH endpoints that save these fields should run the normalizer and return `{"ok": true, "formatted": "..."}` so `flashCell()` triggers when the value changes.

---

## 4. Duplicate Detection

### Client Duplicate Warning

**When:** Creating a new client (manual via `/clients/new` or during import)

**Logic:**
```python
from rapidfuzz import fuzz

def find_similar_clients(conn, name: str, threshold: int = 85) -> list[dict]:
    """Find existing clients with names similar to the given name."""
    normalized = normalize_client_name(name)
    existing = conn.execute(
        "SELECT id, name, industry_segment FROM clients WHERE archived = 0"
    ).fetchall()
    matches = []
    for r in existing:
        score = fuzz.WRatio(normalized, r["name"])
        if score >= threshold:
            matches.append({"id": r["id"], "name": r["name"],
                           "industry": r["industry_segment"], "score": score})
    return sorted(matches, key=lambda x: -x["score"])
```

**Manual create flow:**
1. User submits client creation form
2. Server runs `find_similar_clients()`
3. If matches found → return warning partial via HTMX:
   - "Similar clients found: Acme Corporation (92% match). Use existing? Or Create anyway?"
   - "Use existing" links to the existing client
   - "Create anyway" resubmits with `?force=1` to bypass the check
4. If no matches or `force=1` → create normally

**Import flow:**
1. During import, before creating a new client, run `find_similar_clients()`
2. If matches found → flag the import row with warning: "Possible duplicate: Acme Corporation"
3. Don't auto-merge — import creates the client but the warning is visible in results
4. User can merge manually after import

### Contact Duplicate Warning

**When:** Creating a new contact (manual via contact matrix add-row, or during import)

**Logic:** Same pattern as clients, using `contacts` table:

```python
def find_similar_contacts(conn, name: str, threshold: int = 85) -> list[dict]:
    """Find existing contacts with names similar to the given name."""
    existing = conn.execute(
        "SELECT id, name, email, phone FROM contacts"
    ).fetchall()
    matches = []
    for r in existing:
        score = fuzz.WRatio(name.strip(), r["name"])
        if score >= threshold:
            matches.append({"id": r["id"], "name": r["name"],
                           "email": r["email"], "phone": r["phone"], "score": score})
    return sorted(matches, key=lambda x: -x["score"])
```

**UI:** The template `contacts/_duplicate_warning.html` already exists. Extend it to show:
- Contact name + score
- Email and phone (so user can tell if it's the same person)
- Which clients the existing contact is assigned to
- "Use existing" and "Create anyway" actions

---

## 5. Data Hygiene Migration

**Migration file:** `src/policydb/migrations/060_normalize_existing_data.py` (Python migration, not SQL — needs to call the normalization functions)

**Alternative:** Since migrations are SQL files run by `init_db()`, implement as a Python function called from `init_db()` after migration 060's SQL marker is applied. The SQL marker just records the version; the Python function does the work.

**What it normalizes:**

```python
def _run_hygiene_060(conn):
    """One-time normalization of existing data."""
    from policydb.utils import (normalize_coverage_type, normalize_policy_number,
                                 normalize_client_name, format_zip, format_state, format_city)

    changed = {"policy_type": 0, "policy_number": 0, "client_name": 0,
               "zip": 0, "state": 0, "city": 0}

    # Coverage types
    rows = conn.execute("SELECT id, policy_type FROM policies WHERE policy_type IS NOT NULL").fetchall()
    for r in rows:
        normalized = normalize_coverage_type(r["policy_type"])
        if normalized != r["policy_type"]:
            conn.execute("UPDATE policies SET policy_type = ? WHERE id = ?", (normalized, r["id"]))
            changed["policy_type"] += 1

    # Policy numbers
    rows = conn.execute("SELECT id, policy_number FROM policies WHERE policy_number IS NOT NULL AND policy_number != ''").fetchall()
    for r in rows:
        normalized = normalize_policy_number(r["policy_number"])
        if normalized != r["policy_number"]:
            conn.execute("UPDATE policies SET policy_number = ? WHERE id = ?", (normalized, r["id"]))
            changed["policy_number"] += 1

    # Client names
    rows = conn.execute("SELECT id, name FROM clients WHERE name IS NOT NULL").fetchall()
    for r in rows:
        normalized = normalize_client_name(r["name"])
        if normalized != r["name"]:
            conn.execute("UPDATE clients SET name = ? WHERE id = ?", (normalized, r["id"]))
            changed["client_name"] += 1

    # Address fields
    rows = conn.execute("""SELECT id, exposure_zip, exposure_state, exposure_city
                           FROM policies
                           WHERE exposure_zip IS NOT NULL OR exposure_state IS NOT NULL
                              OR exposure_city IS NOT NULL""").fetchall()
    for r in rows:
        updates = {}
        if r["exposure_zip"]:
            fmt = format_zip(r["exposure_zip"])
            if fmt != r["exposure_zip"]:
                updates["exposure_zip"] = fmt
                changed["zip"] += 1
        if r["exposure_state"]:
            fmt = format_state(r["exposure_state"])
            if fmt != r["exposure_state"]:
                updates["exposure_state"] = fmt
                changed["state"] += 1
        if r["exposure_city"]:
            fmt = format_city(r["exposure_city"])
            if fmt != r["exposure_city"]:
                updates["exposure_city"] = fmt
                changed["city"] += 1
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE policies SET {set_clause} WHERE id = ?",
                        (*updates.values(), r["id"]))

    conn.commit()
    # Log results
    total = sum(changed.values())
    if total > 0:
        print(f"[hygiene-060] Normalized {total} fields: {changed}")
```

**Runs once:** The migration marker prevents re-running. Safe to run on empty or populated databases.

---

## 6. Reconciler Impact

The reconciler's `_normalize_coverage()` function becomes a thin wrapper around `normalize_coverage_type()` from utils.py. Since the DB now stores normalized coverage types, the reconciler's fuzzy matching on `policy_type` becomes more reliable — both the DB value and the import value will be normalized to the same canonical name before comparison.

**No scoring changes needed.** The existing scoring weights and bonuses remain the same. The normalization just reduces false negatives (where `"cgl"` in DB didn't match `"General Liability"` in import because the DB value was never normalized).

**`_normalize_policy_number()` in reconciler stays as-is** — it strips formatting for match comparison. The DB stores `"POL-123"` (uppercased), the reconciler strips to `"POL123"` for comparison. These are complementary, not conflicting.

---

## 7. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Unknown coverage type (no alias match) | Title-cased and stored. User can add to config list if it should be canonical. |
| Coverage alias maps to a type not in config list | Alias takes precedence. The canonical name is stored even if not in the dropdown config. User can add it to config. |
| Client name is all-caps ("ACME HOLDINGS") | Title-cased to "Acme Holdings". Acronyms 2-4 chars preserved ("ABC Corp" stays "ABC Corp"). |
| Client name has mixed case ("McDonald's") | Preserved — title-casing only applies to all-lower words. |
| ZIP code is partial ("787") | Stored as "787" — best-effort, no rejection. |
| ZIP code has letters ("78701-AB") | Stripped to digits: "78701". |
| State is full name ("Texas") | Converted to "TX". |
| State is unknown ("XX") | Uppercased and stored as "XX". No rejection. |
| Duplicate client warning on import (100 rows) | Each row checked. Warning badge on flagged rows. No auto-merge. |
| Duplicate contact warning — same name, different company | Warning shows which clients the existing contact is assigned to. User decides. |
| Normalization changes a value the user intended | Flash shows the change. User can edit back. Same UX as phone formatting. |
| Hygiene migration on empty DB | Runs with zero changes. No harm. |
| Hygiene migration on DB with 500 policies | Runs in <1 second (SQLite, local). Prints summary. |
