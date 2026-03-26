# Exposure-Policy Linkage Design

**Date:** 2026-03-26
**Status:** Draft
**Scope:** Connect `client_exposures` to policies for rate calculation, display, and tracking

---

## Problem

PolicyDB tracks exposure data (payroll, revenue, square footage, etc.) in two disconnected systems:

1. **Per-policy fields** on the `policies` table: `exposure_basis`, `exposure_amount`, `exposure_unit`, `exposure_address/city/state/zip`
2. **Client/location-level rows** in the `client_exposures` table: annual tracking with YoY comparison, scoped to corporate or location level

These systems run in parallel with no bridge. The `policy_id` FK on `client_exposures` exists but nothing reads it. There is no rate calculation anywhere in the system. Users cannot see what rate they're paying per unit of exposure, nor track how rates change year-over-year as exposures shift.

## Solution

A junction table (`policy_exposure_links`) that connects policies to specific `client_exposures` rows. Each link stores a cached rate calculated from the policy's premium and the exposure's amount/denominator. One exposure per policy is designated as the **primary rating basis**; additional exposures can be linked for context tracking.

---

## Data Model

### New Table: `policy_exposure_links`

```sql
CREATE TABLE IF NOT EXISTS policy_exposure_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    exposure_id     INTEGER NOT NULL REFERENCES client_exposures(id) ON DELETE CASCADE,
    is_primary      INTEGER NOT NULL DEFAULT 0,
    rate            REAL,
    rate_updated_at DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_uid, exposure_id)
);

CREATE INDEX idx_pel_policy ON policy_exposure_links(policy_uid);
CREATE INDEX idx_pel_exposure ON policy_exposure_links(exposure_id);
CREATE INDEX idx_pel_primary ON policy_exposure_links(policy_uid, is_primary) WHERE is_primary = 1;
```

### Modify: `client_exposures`

```sql
ALTER TABLE client_exposures ADD COLUMN denominator INTEGER NOT NULL DEFAULT 1;
```

The `denominator` column stores the numeric "per X" value (1, 100, 1000). The existing `unit` text column remains for display label purposes.

### Constraint

Only one row per `policy_uid` may have `is_primary = 1`. Enforced in application code: when setting a link as primary, clear `is_primary` on all other links for that policy first.

### Config Addition

```yaml
exposure_denominators:
  - 1
  - 100
  - 1000
```

Added to `_DEFAULTS` in `config.py` and `EDITABLE_LISTS` in `settings.py`. The denominator input in the exposure matrix offers these as suggestions but accepts custom numeric values.

---

## Rate Calculation

### Formula

```
rate = premium / (exposure_amount / denominator)
```

**Example:** $50,000 premium, $10,000,000 payroll, denominator 100:
`50000 / (10000000 / 100)` = **$0.50 per $100 of payroll**

### Null Handling

If premium is NULL/0 or exposure amount is NULL/0, rate is stored as `NULL`. This distinguishes "no data available" from a calculated zero.

### Recalc Triggers

| Trigger | Action |
|---------|--------|
| Policy premium PATCH | Recalc rate on all `policy_exposure_links` rows for that `policy_uid` |
| Exposure amount PATCH (in `client_exposures`) | Recalc rate on all `policy_exposure_links` rows pointing to that `exposure_id` |
| Exposure denominator PATCH | Recalc all linked policy rates for that `exposure_id` |
| Link created or `exposure_id` changed | Calc rate for the new/updated link |
| Link deleted | No action (row is gone) |

### Recalc Function

Located in `queries.py` (or a new `exposures.py` module):

```python
def recalc_exposure_rate(conn, *, link_id=None, policy_uid=None, exposure_id=None):
    """Recalculate cached rate on policy_exposure_links rows.

    Pass one of:
    - link_id: recalc a single link
    - policy_uid: recalc all links for a policy (e.g., premium changed)
    - exposure_id: recalc all links to an exposure (e.g., amount changed)
    """
```

The function JOINs `policy_exposure_links` to `policies` (for premium) and `client_exposures` (for amount + denominator), computes the rate, and UPDATEs `rate` + `rate_updated_at`.

---

## UI Integration

### 1. Exposure Matrix (Primary Linkage Point)

**File:** `templates/clients/_exposure_matrix.html`, `_exposure_matrix_row.html`

New columns added to the existing exposure matrix:

| Column | Behavior |
|--------|----------|
| **Per** | Numeric input for denominator. Suggestions from `exposure_denominators` config. Saves on blur via PATCH. |
| **Policy** | Existing combobox column. Selecting a policy creates a `policy_exposure_links` row. Clearing it deletes the link. |
| **★** | Primary toggle. Click filled star (★) to set as primary; click again to unset. Only one primary per policy enforced. |
| **Rate** | Auto-calculated display. Green badge for primary exposures, gray text for context exposures. |

**Behavior:**
- Selecting a policy in the combobox POSTs to create a `policy_exposure_links` row
- Changing the star toggles `is_primary` via PATCH
- Rate recalculates and flashes green when premium or exposure amount changes

### 2. Policy Detail Page — Exposure Card

**File:** `templates/policies/` (policy detail/edit page)

Read-only card on the policy page showing linked exposures:

- **Primary rating basis:** Highlighted green card with star, exposure type, location, year, denominator, amount, and calculated rate displayed prominently
- **Context exposures:** Listed below in gray cards with same data in subdued styling
- **No links placeholder:** If the policy has no exposure links, show "No rating basis linked" in an amber placeholder with a link to the exposure matrix

Editing happens in the exposure matrix, not on the policy page.

### 3. Schedule of Insurance View

**View:** `v_schedule` in `views.py`

Updated to LEFT JOIN through `policy_exposure_links` (where `is_primary = 1`) to `client_exposures`. New columns in the schedule:

| Column | Source |
|--------|--------|
| Exposure | `client_exposures.exposure_type` + `/` + `client_exposures.denominator` (e.g., "Payroll /100") |
| Amount | `client_exposures.amount` |
| Rate | `policy_exposure_links.rate` |

Policies without a primary exposure link show dashes (`—`) in these columns.

### 4. Unlinked Policy Indicators

Visual flags at four touchpoints for policies with no primary exposure link:

| Location | Indicator |
|----------|-----------|
| **Exposure matrix** | Policies in the combobox dropdown that have no exposure links show a small dot or "unlinked" badge |
| **Policy detail page** | Amber placeholder card: "No rating basis linked" with link to exposure matrix |
| **Schedule of insurance** | Dashes in exposure/rate columns (already natural) |
| **Location assignment board** | After assigning a policy to a location that has exposures, show a small amber indicator on the policy card if it has no exposure links |

---

## Legacy Field Handling

The existing `exposure_basis`, `exposure_amount`, `exposure_unit`, `exposure_address`, `exposure_city`, `exposure_state`, `exposure_zip` columns on the `policies` table:

- **Stay in the schema** — SQLite cannot drop columns cleanly
- **Write-ignored** — new code does not write to these columns
- **Read fallback** — views and queries check `policy_exposure_links` first; if no link exists, fall back to legacy columns for backward compatibility
- **No bulk migration** — existing data sits inert; new links are created going forward as users interact with the exposure matrix

The `policy_id` column on `client_exposures` becomes redundant (replaced by the junction table) but remains in the schema for the same reason.

---

## Scope Behavior

| Exposure Scope | Links To |
|----------------|----------|
| **Location exposures** (`project_id IS NOT NULL`) | Policies assigned to that location (`policies.project_id = client_exposures.project_id`) |
| **Corporate exposures** (`project_id IS NULL`) | Program master policies or company-wide policies not assigned to a specific location |

The UI should guide this naturally: the exposure matrix for a location shows that location's exposures and the policies assigned to it. The corporate-level exposure matrix shows corporate exposures and program/unassigned policies.

---

## Auto-Linking

None. Policies are linked to exposures manually via the exposure matrix. When a policy is assigned to a location through the assignment board, no automatic exposure links are created. The unlinked policy indicators (Section 4 above) ensure nothing gets forgotten.

---

## LLM Import Integration

The LLM import system (`llm_schemas.py`) currently extracts `exposure_basis`, `exposure_amount`, and exposure address fields from insurance documents and writes them to the legacy policy columns. With the new linkage model, `client_exposures` is the single source of truth — the LLM import must route exposure data there.

### Updated LLM Import Flow

When `parse_llm_json()` extracts exposure data for a policy:

1. **Extract fields:** LLM returns `exposure_basis` (type), `exposure_amount` (value), and optionally `exposure_unit` (from which denominator can be parsed)
2. **Determine scope:** Use the policy's `project_id` to resolve location scope. If the policy is assigned to a location, the exposure is location-scoped. If not, it's corporate-scoped.
3. **Determine year:** Use the policy's `effective_date` year (or current year as fallback)
4. **Find or create `client_exposures` row:**
   - Search for existing row matching: `client_id` + `COALESCE(project_id, 0)` + `exposure_type` + `year`
   - **If found:** Use that row. If `amount` differs from extracted value, flag the diff for user review (do not auto-overwrite — the user may have manually entered a more accurate figure)
   - **If not found:** Create a new `client_exposures` row with the extracted type, amount, year, and scope
5. **Create `policy_exposure_links` row:** Link the policy to the exposure row, set `is_primary = 1` (if no other primary exists for this policy)
6. **Calculate rate:** Run `recalc_exposure_rate()` for the new link
7. **Legacy columns:** Still populate `policies.exposure_basis` and `policies.exposure_amount` for backward compatibility during the transition period

### Schema Changes

Add `exposure_denominator` field to `POLICY_EXTRACTION_SCHEMA` in `llm_schemas.py`:

```python
{
    "key": "exposure_denominator",
    "label": "Exposure Denominator",
    "type": "number",
    "required": False,
    "description": "Rating unit denominator (e.g., 100 for 'per $100 of payroll', 1000 for 'per $1,000 of revenue')",
    "example": "100",
}
```

The prompt instructs the LLM to extract the denominator from rate expressions on the document (e.g., "$0.50 per $100 of payroll" → denominator = 100).

### Conflict Handling

If the extracted exposure amount differs from an existing `client_exposures` row for the same type/year/scope:

- The import review panel shows both values with a diff indicator
- User chooses which value to keep (extracted vs existing)
- No silent overwrite — the exposure matrix is the canonical editing surface

### CSV Importer

Add exposure column aliases to `PolicyImporter.ALIASES` in `importer.py`:

```python
"exposure_basis": ["exposure basis", "rating basis", "exposure type"],
"exposure_amount": ["exposure amount", "exposure value", "exposure"],
"exposure_denominator": ["denominator", "per", "rating unit"],
```

CSV import follows the same find-or-create flow as LLM import for routing exposure data to `client_exposures`.

---

## Files Affected

| File | Change |
|------|--------|
| `migrations/0XX_policy_exposure_links.sql` | New table + denominator column |
| `db.py` | Wire migration into `init_db()` |
| `config.py` | Add `exposure_denominators` to `_DEFAULTS` |
| `settings.py` | Add `exposure_denominators` to `EDITABLE_LISTS` |
| `queries.py` (or new `exposures.py`) | `recalc_exposure_rate()`, link CRUD queries |
| `views.py` | Update `v_schedule`, `v_policy_status` to JOIN through links |
| `routes/clients.py` | Exposure matrix endpoints: create/delete link, toggle primary, denominator PATCH |
| `routes/policies.py` | Policy detail: exposure card partial, premium PATCH triggers recalc |
| `templates/clients/_exposure_matrix.html` | New Per, ★, Rate columns |
| `templates/clients/_exposure_matrix_row.html` | New cells per row |
| `templates/policies/_exposure_card.html` | New partial for policy detail page |
| `templates/policies/detail.html` (or edit) | Include exposure card |
| `email_templates.py` | Add rate/exposure tokens to `policy_context()` and `CONTEXT_TOKENS` |
| `llm_schemas.py` | Add `exposure_denominator` to `POLICY_EXTRACTION_SCHEMA`, update parse flow to route exposure data to `client_exposures` + create links |
| `importer.py` | Add exposure column aliases, route CSV exposure data through find-or-create flow |
