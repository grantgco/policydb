# Carrier Normalization — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Normalize carrier names on save using a config-managed alias mapping. Combobox enforcement on carrier input fields. Hygiene migration for existing data. Settings UI for managing aliases.

---

## Problem Statement

Carrier names are stored inconsistently: "Travelers", "Travelers Insurance", "Travelers Indemnity Co.", "The Travelers Companies" are all the same parent carrier but treated as distinct values in reports. When meeting with an underwriter, it's difficult to pull "all my clients with Travelers" because the data doesn't group cleanly.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Normalization approach | Alias dict + combobox, same pattern as coverage types | Proven pattern, prevents new dirty data AND cleans imports |
| Alias storage | Config-managed (not hardcoded) | User can add/edit aliases from Settings |
| Pre-seeded aliases | Yes, top 30+ carriers | Saves discovery effort |
| Unknown carriers | Preserve original casing, no rejection | Company names have specific capitalization |
| Reconciler impact | Normalize before matching (no reconciler changes) | Both sides canonical → fuzzy match works naturally |
| Hierarchy | Not tracked | User cares about parent roll-up, not entity details |

---

## 1. Normalization Function

### `normalize_carrier(raw: str) -> str`

Added to `src/policydb/utils.py`:

```python
def normalize_carrier(raw: str) -> str:
    """Normalize a carrier name to its canonical parent company name.

    Uses _CARRIER_ALIASES built from config. Preserves original casing
    for unknown carriers (unlike coverage types which title-case).
    """
    if not raw or not raw.strip():
        return ""
    cleaned = raw.strip()
    key = cleaned.lower()
    if key in _CARRIER_ALIASES:
        return _CARRIER_ALIASES[key]
    return cleaned
```

### `_CARRIER_ALIASES` dict

Built from config on module load:

```python
def _build_carrier_aliases() -> dict[str, str]:
    """Build flat alias lookup from config carrier_aliases."""
    from policydb import config as cfg
    aliases = cfg.get("carrier_aliases", {})
    result = {}
    for canonical, variations in aliases.items():
        result[canonical.lower()] = canonical
        for v in variations:
            result[v.lower()] = canonical
    return result

_CARRIER_ALIASES = _build_carrier_aliases()
```

Note: Must be rebuilt when config changes. The `reload_carrier_aliases()` function is called after Settings saves.

---

## 2. Configuration

### New config key: `carrier_aliases`

Added to `_DEFAULTS` in `src/policydb/config.py`:

```python
"carrier_aliases": {
    "Travelers": ["Travelers Insurance", "The Travelers Companies", "Travelers Indemnity",
                   "Travelers Indemnity Co", "Travelers Casualty", "St Paul Fire",
                   "St. Paul Fire & Marine", "Travelers Casualty & Surety"],
    "Chubb": ["Chubb Limited", "ACE American", "ACE American Insurance",
              "Federal Insurance", "Federal Insurance Company", "Chubb Insurance"],
    "AIG": ["American International Group", "AIG Insurance", "National Union Fire",
            "Lexington Insurance", "AIG Property Casualty"],
    "Hartford": ["The Hartford", "Hartford Fire", "Hartford Financial",
                 "Hartford Fire Insurance", "Hartford Casualty"],
    "Liberty Mutual": ["Liberty Mutual Insurance", "Liberty Mutual Fire",
                       "Liberty Mutual Group"],
    "Zurich": ["Zurich Insurance", "Zurich American", "Zurich North America",
               "Zurich American Insurance"],
    "CNA": ["CNA Insurance", "CNA Financial", "Continental Casualty"],
    "Markel": ["Markel Corporation", "Markel Insurance", "Markel Specialty"],
    "Berkshire Hathaway": ["Berkshire Hathaway Insurance", "BHSI",
                           "Berkshire Hathaway Specialty Insurance"],
    "Nationwide": ["Nationwide Insurance", "Nationwide Mutual",
                   "Allied Insurance", "Nationwide Mutual Insurance"],
    "Progressive": ["Progressive Insurance", "Progressive Casualty",
                    "Progressive Commercial"],
    "Employers": ["Employers Insurance", "Employers Holdings",
                  "Employers Compensation Insurance"],
    "FM Global": ["Factory Mutual", "FM Insurance", "Factory Mutual Insurance"],
    "Everest": ["Everest Re", "Everest Insurance", "Everest National Insurance"],
    "RLI": ["RLI Insurance", "RLI Corp"],
    "Coalition": ["Coalition Insurance", "Coalition Inc"],
    "Berkley": ["W.R. Berkley", "WR Berkley", "Berkley Insurance",
                "Berkley One", "W. R. Berkley Corporation"],
    "Tokio Marine": ["Tokio Marine HCC", "HCC Insurance", "Tokio Marine America"],
    "Hanover": ["The Hanover", "Hanover Insurance", "Hanover Insurance Group"],
    "Arch": ["Arch Insurance", "Arch Capital", "Arch Insurance Group"],
    "Great American": ["Great American Insurance", "Great American Insurance Company"],
    "Sompo": ["Sompo International", "Endurance Specialty"],
    "Argo": ["Argo Group", "Argo Insurance"],
    "Aspen": ["Aspen Insurance", "Aspen Specialty"],
    "Axis": ["AXIS Insurance", "AXIS Capital"],
    "Cincinnati Financial": ["Cincinnati Insurance", "The Cincinnati Insurance Company"],
    "Erie": ["Erie Insurance", "Erie Indemnity"],
    "Intact": ["Intact Insurance", "OneBeacon"],
    "QBE": ["QBE Insurance", "QBE North America"],
    "Starr": ["Starr Insurance", "Starr Companies", "Starr Indemnity"],
},
```

### Existing `carriers` config list

The existing `carriers` config list (used for combobox suggestions) continues to work as-is. The alias mapping is separate — it maps variations to canonical names. The canonical names should be IN the carriers list.

---

## 3. Save Path Integration

Apply `normalize_carrier()` everywhere `normalize_coverage_type()` is applied:

| File | Save paths |
|------|-----------|
| `src/policydb/web/routes/policies.py` | `policy_edit_post`, `policy_new_post`, `policy_cell_save` (PATCH) |
| `src/policydb/web/routes/reconcile.py` | `reconcile_create`, `batch_create`, `batch_create_program` |
| `src/policydb/importer.py` | `PolicyImporter` after column mapping |

Pattern:
```python
carrier = normalize_carrier(carrier) if carrier else ""
```

---

## 4. Settings UI — Carrier Aliases Management

### Location

New section on Settings page below the existing `carriers` list card.

### UI

```
┌──────────────────────────────────────────────────────────┐
│ Carrier Aliases                                           │
│ Map carrier name variations to canonical parent names     │
├──────────────────────────────────────────────────────────┤
│ Travelers                                                │
│   Travelers Insurance, The Travelers Companies,          │
│   Travelers Indemnity, St. Paul Fire & Marine            │
│   [+ Add alias]                              [Remove]    │
│                                                          │
│ Chubb                                                    │
│   Chubb Limited, ACE American, Federal Insurance         │
│   [+ Add alias]                              [Remove]    │
│                                                          │
│ ... more carriers ...                                    │
│                                                          │
│ [+ Add Carrier Group]                                    │
└──────────────────────────────────────────────────────────┘
```

### Endpoints

- `POST /settings/carrier-aliases/add-group` — add a new canonical carrier with empty aliases
- `POST /settings/carrier-aliases/add-alias` — add an alias to an existing group
- `POST /settings/carrier-aliases/remove-alias` — remove one alias
- `POST /settings/carrier-aliases/remove-group` — remove an entire carrier group

After any change, rebuild `_CARRIER_ALIASES` dict.

---

## 5. Hygiene Migration

One-time normalization of existing `policies.carrier` values, same pattern as migration 062:

```python
def _normalize_carriers(conn):
    from policydb.utils import normalize_carrier
    changed = 0
    for r in conn.execute("SELECT id, carrier FROM policies WHERE carrier IS NOT NULL AND carrier != ''").fetchall():
        n = normalize_carrier(r["carrier"])
        if n != r["carrier"]:
            conn.execute("UPDATE policies SET carrier = ? WHERE id = ?", (n, r["id"]))
            changed += 1
    if changed:
        print(f"[hygiene] Normalized {changed} carrier names")
    conn.commit()
```

Run as part of the startup hygiene (idempotent — reruns are no-ops once data is clean).

Also normalize `program_carriers` table carrier column.

---

## 6. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Unknown carrier not in aliases | Stored as-is with original casing. No rejection. |
| Carrier in aliases but not in config `carriers` list | Alias takes precedence. Canonical name stored. User can add to config list. |
| User adds new alias in Settings | `_CARRIER_ALIASES` rebuilt immediately. New data normalized on save. Existing data normalized on next hygiene run or manual trigger. |
| Carrier field empty | Returns empty string. No normalization. |
| "AIG" entered as "aig" | Alias lookup is case-insensitive. Returns "AIG". |
| Import with "TRAVELERS INDEMNITY CO." | Normalized to "Travelers" on import. |
| Reconciler matching | Both sides normalized before matching → fuzzy WRatio scores high naturally. No reconciler code changes. |
| Remove a carrier group from Settings | Aliases removed. Existing policies keep their (already normalized) carrier values. Future entries of that carrier are stored as-is. |
