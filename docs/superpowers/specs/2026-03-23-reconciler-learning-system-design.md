# Reconciler Learning System & Full Reconciliation

**Date:** 2026-03-23
**Status:** Draft

## Context

PolicyDB's reconciler has three disconnected systems for managing coverage types:

1. **`policy_types` config** — canonical names shown in dropdowns (Settings UI)
2. **`_COVERAGE_ALIASES` hardcoded dict** — ~260 entries mapping abbreviations to canonical names (utils.py)
3. **`coverage_aliases` config** — user-learned aliases from "Remember" clicks (config.yaml)

These don't talk to each other:
- Adding a coverage type in Settings doesn't make it recognized by the reconciler
- The reference guide only shows hardcoded aliases, not learned ones
- Learning only happens via manual "Remember" clicks — never automatically

The same disconnect exists for carriers — `carriers` config, `_CARRIER_ALIASES` hardcoded, and `carrier_aliases` config are separate systems.

Additionally, the reconciler's field-level diff system is incomplete:
- Policy number diffs are scored but never surfaced for update
- Several importable fields (FNI, underwriter, placement colleague, address) aren't tracked
- No bulk accept — every field on every pair requires individual clicks

## Features

### Feature 1: Unified Alias Registries (Coverage + Carrier)

**Goal:** All alias sources merge into one system per type. The reference guide shows everything.

#### Coverage: Changes to `rebuild_coverage_aliases()` in `src/policydb/utils.py`

Add a third merge step after hardcoded and config aliases:

```
Merge order (later overrides earlier):
1. _BASE_COVERAGE_ALIASES (hardcoded ~260 entries)
2. policy_types from cfg.get("policy_types", []) — each entry self-references as canonical
3. coverage_aliases from cfg.get("coverage_aliases", {}) — user-learned mappings
```

For step 2: iterate `policy_types` and add `entry.lower() → entry` for each. This means adding "Drone Liability" in Settings immediately makes `"drone liability"` a recognized canonical name in the reconciler.

#### Carrier: Changes to `rebuild_carrier_aliases()` in `src/policydb/utils.py`

Same pattern — add a merge step for `carriers` config list. **Note:** `_BASE_CARRIER_ALIASES` does not currently exist. Create it as a new module-level global (initially empty dict) and restructure `rebuild_carrier_aliases()` to use the snapshot-then-merge pattern, mirroring `rebuild_coverage_aliases()` at line 101 of utils.py.

```
Merge order:
1. _BASE_CARRIER_ALIASES (snapshot of carrier_aliases config on first call)
2. carriers from cfg.get("carriers", []) — each entry self-references as canonical
3. carrier_aliases from cfg.get("carrier_aliases", {}) — user-learned mappings
```

Adding "Acme Insurance" in Settings → reconciler recognizes "acme insurance" immediately.

#### Reference Guide — No Template Change Needed

The `/reconcile/reference-guide` endpoint already reads `_COVERAGE_ALIASES` and groups by canonical name. Since `rebuild_coverage_aliases()` now merges all three sources into that dict, learned aliases automatically appear in the reference guide. Same for carrier aliases — the endpoint already reads `carrier_aliases` from config.

---

### Feature 2: Auto-Learn on Confirm (Coverage + Carrier)

**Goal:** When a user confirms a reconcile pair where a coverage or carrier alias was applied during scoring, the system automatically saves that alias — no "Remember" click needed. The user sees clear feedback when learning happens.

#### Required: Add carrier fields to both `ScoreBreakdown` AND `ReconcileRow`

`coverage_alias_applied`, `ext_type_raw`, and `ext_type_normalized` already exist on both `ScoreBreakdown` (namedtuple) and `ReconcileRow` (dataclass). `carrier_alias_applied` does **not** exist on either and must be added to both:

1. Add `carrier_alias_applied`, `ext_carrier_raw`, `ext_carrier_normalized` fields to `ScoreBreakdown` namedtuple in `reconciler.py`
2. Add the same three fields to `ReconcileRow` dataclass in `reconciler.py`
3. In `_score_pair()`, after carrier normalization (~lines 354-375), set `carrier_alias_applied = True` when `raw_carrier.lower() != normalized_carrier.lower()`, and store the raw/normalized values
4. In `_build_reconcile_row()` (~line 488), copy the three new carrier fields from `ScoreBreakdown` to `ReconcileRow` (matching the existing pattern for coverage fields)

#### Changes to `/reconcile/confirm/{idx}` in `src/policydb/web/routes/reconcile.py`

After confirming the pair, check the `ReconcileRow` (not ScoreBreakdown — the confirm endpoint works with ReconcileRow objects):

1. **Coverage auto-learn:** If `coverage_alias_applied` is true:
   - Read `ext_type_raw` and `ext_type_normalized` from the ReconcileRow
   - Check if `ext_type_raw.lower()` already exists in `_BASE_COVERAGE_ALIASES` (hardcoded) — skip if so
   - Otherwise, save to `coverage_aliases` config (same logic as existing `learn-coverage-alias` endpoint)
   - Call `rebuild_coverage_aliases()`

2. **Carrier auto-learn:** If `carrier_alias_applied` is true:
   - Read `ext_carrier_raw` and `ext_carrier_normalized` from the ReconcileRow
   - Check if `ext_carrier_raw.lower()` already exists in `_BASE_CARRIER_ALIASES` — skip if so
   - Otherwise, save to `carrier_aliases` config (same logic as existing `learn-carrier-alias` endpoint)
   - Call `rebuild_carrier_aliases()`

#### Visible Learning Feedback

When aliases are auto-learned on confirm, the user must see what was learned. Two feedback mechanisms:

1. **Per-confirm toast:** When the confirm endpoint auto-learns an alias, include a learning summary in the returned HTML via an OOB swap to a toast container:
   ```html
   <div id="learn-toast" hx-swap-oob="innerHTML">
     <div class="... bg-blue-50 text-blue-800 ...">
       Learned: "work comp" → Workers Compensation
     </div>
   </div>
   ```
   Toast auto-fades after 3 seconds. Multiple learns show stacked toasts.

2. **Reconcile-all summary:** When the bulk reconcile-all completes, the refreshed board includes a summary banner at the top:
   ```
   ✓ Reconciled 23 pairs · Applied 47 field updates · Learned 3 new aliases:
     "work comp" → Workers Compensation
     "Trav Ins Co" → Travelers
     "cyber ins" → Cyber / Tech E&O
   ```
   This banner is dismissible and uses `bg-blue-50` (info tone, not success green — learning is informational).

#### Deduplication

When bulk-confirming multiple pairs (Feature 4B), the same alias may appear on many pairs. Collect unique aliases first, then write config once — not N times.

---

### Feature 3: Expanded Field-Level Diffs

**Goal:** Track and surface diffs for all importable fields, not just the current subset.

#### Changes to `_score_pair()` in `src/policydb/reconciler.py`

Add diff tracking for these fields after the existing scoring blocks. All comparisons use **normalized** forms (stripped, lowercased) to determine real vs cosmetic diffs:

| Field | Comparison | Diff Type |
|-------|-----------|-----------|
| `policy_number` | `normalize_policy_number_for_matching()` on both | `diff_fields` if normalized forms differ; `cosmetic_diffs` if raw differs but normalized matches |
| `first_named_insured` | `.strip().lower()` compare | `diff_fields` if different; `fillable_fields` if only ext has value |
| `placement_colleague` | `.strip().lower()` compare | `diff_fields` if different; `fillable_fields` if only ext has value |
| `underwriter_name` | `.strip().lower()` compare | `diff_fields` if different; `fillable_fields` if only ext has value |
| `exposure_address` | `.strip().lower()` compare | `diff_fields` if different; `fillable_fields` if only ext has value |

These are simple string comparisons — no scoring weight, just diff detection for the update UI.

#### Changes to `_ALLOWED_FIELDS` in `src/policydb/web/routes/reconcile.py`

Expand from current 8 to 13:
```python
_ALLOWED_FIELDS = {
    "policy_type", "carrier", "policy_number",
    "effective_date", "expiration_date",
    "premium", "limit_amount", "deductible",
    "first_named_insured", "placement_colleague",
    "underwriter_name", "exposure_address",
}
```

**Currency fix:** The existing `apply-field` endpoint uses `float()` for currency fields — violates CLAUDE.md. Change to `parse_currency_with_magnitude()`. The new text fields (`first_named_insured`, etc.) are NOT currency fields and save as plain text.

#### Field Display Names

The score breakdown template renders field names from `diff_fields` directly. Snake_case names like `first_named_insured` won't look good. Add a display name map:

```python
_FIELD_DISPLAY = {
    "policy_type": "Coverage Type",
    "carrier": "Carrier",
    "policy_number": "Policy Number",
    "effective_date": "Effective Date",
    "expiration_date": "Expiration Date",
    "premium": "Premium",
    "limit_amount": "Limit",
    "deductible": "Deductible",
    "first_named_insured": "First Named Insured",
    "placement_colleague": "Placement Colleague",
    "underwriter_name": "Underwriter",
    "exposure_address": "Address",
}
```

Pass this to the score breakdown template and use it for display labels.

#### UI — Minimal Template Change

The `_score_breakdown.html` template already renders `diff_fields` and `fillable_fields` generically. New fields will appear automatically with Accept/Fill buttons. Only change: use `_FIELD_DISPLAY` for labels instead of raw field names.

---

### Feature 4: Bulk Accept

**Goal:** Accept all diffs across pairs without clicking each field individually.

#### 4A: Per-Pair "Accept All"

**New endpoint:** `PATCH /reconcile/accept-all-fields/{idx}`

Uses `idx` (pair index) to match the existing `confirm/{idx}` and `break/{idx}` pattern. Requires `token` form parameter (same as all board-cache endpoints).

- Looks up the pair by `idx` from `_BOARD_CACHE[token]`
- Reads the pair's `diff_fields` and `fillable_fields`
- For each field: applies the ext value to the DB
  - Currency fields use `parse_currency_with_magnitude()`
  - Text fields save directly
- For program carrier rows: applies to the `program_carriers` table, then recalculates parent totals using the `_update_program_totals()` function in `policies.py` (line 1807) or equivalent inline SQL
- Returns updated score breakdown HTML (all fields now green with checkmarks)

**UI:** Add an "Accept All" button at the top of each pair's score breakdown panel. Styled as a green button: "Accept All Diffs (N fields)".

#### 4B: Global "Reconcile All"

Two-step flow:

1. **Preview** (`GET /reconcile/reconcile-all-preview`):
   - Requires `token` parameter
   - Scans all unconfirmed pairs in `_BOARD_CACHE[token]`
   - Counts: total pairs to confirm, total field updates, total fillable fields
   - Collects unique coverage/carrier aliases that will be auto-learned
   - Returns summary HTML panel

2. **Execute** (`POST /reconcile/reconcile-all`):
   - Requires `token` parameter
   - Validates the session cache still exists (return error if expired)
   - Wraps all DB updates in a single transaction
   - Confirms all unconfirmed pairs
   - Applies all `diff_fields` and `fillable_fields` on every pair
   - Auto-learns unique coverage/carrier aliases (single config write, not per-pair)
   - Returns refreshed pairing board with learning summary banner (see Feature 2 feedback)

**UI:** Button at top of pairing board: "Reconcile All". Clicking shows the preview summary with a confirm prompt: "Apply N field updates across M pairs? This will also confirm all unconfirmed pairs."

**Error handling:**
- If session cache has expired: return error message asking user to re-run match
- All DB updates are transactional — if any fail, all roll back
- Preview counts are informational — execute re-reads the cache to ensure consistency

---

## Files Modified

| File | Changes |
|------|---------|
| `src/policydb/utils.py` | `rebuild_coverage_aliases()` — add `policy_types` merge step; `rebuild_carrier_aliases()` — add `carriers` merge step |
| `src/policydb/reconciler.py` | Add `carrier_alias_applied`, `ext_carrier_raw`, `ext_carrier_normalized` to both `ScoreBreakdown` and `ReconcileRow`; update `_build_reconcile_row()` to copy new fields; `_score_pair()` — add carrier alias tracking + policy_number/FNI/colleague/underwriter/address diff tracking |
| `src/policydb/web/routes/reconcile.py` | Auto-learn on confirm (coverage + carrier) with toast feedback; `accept-all-fields/{idx}` endpoint; `reconcile-all` + preview endpoints; expand `_ALLOWED_FIELDS`; add `_FIELD_DISPLAY` map; fix currency parsing to use `parse_currency_with_magnitude()` |
| `src/policydb/web/templates/reconcile/_score_breakdown.html` | "Accept All" button per pair; use `_FIELD_DISPLAY` for labels |
| `src/policydb/web/templates/reconcile/_pairing_board.html` | "Reconcile All" button + preview summary panel; learning summary banner; toast container for auto-learn feedback |

## Verification Plan

### Coverage Alias Learning
1. Start server, open Settings → add "Drone Liability" to policy_types
2. Upload CSV with "drone liability" as a coverage type → validation panel shows it green (recognized)
3. Upload CSV with "cyber ins" (unknown) → amber in validation → select "Cyber / Tech E&O" → click Remember
4. Open `/reconcile/reference-guide` → verify "cyber ins" appears under Cyber / Tech E&O, "Drone Liability" appears as its own entry

### Carrier Alias Learning
5. Add "Acme Insurance" in Settings carriers list
6. Upload CSV with "acme insurance" → validation panel shows green (recognized)
7. Upload CSV with "TRAV" (unknown carrier) → amber → select "Travelers" → click Remember
8. Reference guide → "TRAV" appears under Travelers

### Auto-Learn on Confirm (Coverage + Carrier)
9. Upload CSV with hardcoded alias "WC" → run match → confirm pair → check config → should NOT save (already hardcoded) → no learning toast
10. Upload CSV with novel alias "work comp" → run match → confirm pair → **blue toast appears: 'Learned: "work comp" → Workers Compensation'** → check config → alias saved
11. Upload CSV with novel carrier alias "Trav Ins Co" → confirm pair → **blue toast: 'Learned: "Trav Ins Co" → Travelers'** → check config → alias saved

### Policy Number Diffs
12. Upload CSV with different policy number than DB record → pair matches → open score breakdown → see "Policy Number" in diff section with Accept button
13. Click Accept → DB policy_number updated

### Expanded Fields
14. Upload CSV with different underwriter name → pair matches → score breakdown shows "Underwriter" diff (not "underwriter_name") with Accept
15. Upload CSV with placement_colleague where DB has none → shows as fillable field with Fill button

### Bulk Accept
16. Upload CSV with multiple diffs across a pair → click "Accept All" on score breakdown → all fields update at once
17. Upload CSV with multiple pairs and diffs → click "Reconcile All" → see preview summary ("Apply 47 field updates across 23 pairs · Will learn 3 aliases") → confirm → all pairs confirmed, diffs applied, **learning summary banner shows learned aliases**

### Program Support
18. Upload CSV with program carrier rows → pairs match carrier rows → bulk accept applies diffs to `program_carriers` table → parent totals recalculated

### Error Cases
19. Let session cache expire → click "Reconcile All" → see error message, not a crash
20. Delete a policy mid-reconcile → click Accept on that pair → graceful error
21. Submit accept-all-fields without `token` parameter → 400 error with message
