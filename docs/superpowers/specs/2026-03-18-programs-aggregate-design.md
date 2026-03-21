# Aggregate Programs — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

Clients have corporate-level insurance programs (D&O, Property) that span multiple carriers and layers. Currently each layer must be entered as a separate policy and there's no way to view the program as a single aggregate unit. The account executive needs to see total program premium, total limits, and participating carriers at a glance — and reconcile carrier statements where individual layer premiums map to the same program.

---

## Solution

A program is a policy record with `is_program=1`, storing aggregate totals (total premium, total limit) and a list of participating carriers. Programs appear in their own "Corporate Programs" section on the client detail page, above project/location groups. The reconciler allows multiple statement lines to match against one program record, tracking partial reconciliation.

---

## Data Model

### New migration: `052_add_program_fields.sql`

```sql
ALTER TABLE policies ADD COLUMN is_program INTEGER NOT NULL DEFAULT 0;
ALTER TABLE policies ADD COLUMN program_carriers TEXT;
ALTER TABLE policies ADD COLUMN program_carrier_count INTEGER;
```

- `is_program` — flag, like `is_opportunity`
- `program_carriers` — comma-separated carrier list (e.g., "AIG, Chubb, Zurich, Markel")
- `program_carrier_count` — integer count of participating carriers

The existing `carrier` field stores the lead/primary carrier. `premium` stores total aggregate premium. `limit_amount` stores total aggregate limit. `effective_date` and `expiration_date` store the program term.

### Programs excluded from

Programs follow the same exclusion pattern as opportunities:
- Renewal pipeline views (they don't renew individually — the program renews as a unit)
- Suggested follow-ups (manual tracking only)
- Client summary policy counts (separate count: `program_count`)

Update `v_client_summary` to add: `COUNT(CASE WHEN p.is_program = 1 THEN 1 END) AS program_count`

Update `v_policy_status`, `v_renewal_pipeline`, `v_schedule` WHERE clauses to add: `AND (p.is_program = 0 OR p.is_program IS NULL)` — OR include programs in `v_schedule` with a "PROGRAM" indicator (decision: include in schedule with indicator, exclude from renewal pipeline).

---

## Client Detail Page — Programs Section

### Location

New collapsible `<details>` section in `detail.html`, rendered **above** the project/location policy groups and below the Account Pulse.

### Display

```
▶ Corporate Programs · 2 programs · $380K total premium

  [PROGRAM] D&O Program          $20M limit · 3 carriers    $180,000   Apr 1 – Apr 1
  [PROGRAM] Property Program     $50M limit · 4 carriers    $200,000   Jul 1 – Jul 1
```

Each program row shows:
- Blue "PROGRAM" badge
- Program name (= `policy_type`, e.g., "D&O Program")
- Total limit + carrier count
- Total premium (formatted with `| currency`)
- Term dates

Clicking the program name links to `/policies/{uid}/edit` (same edit page as any policy).

### Route changes

In `client_detail()`, query programs separately:
```python
programs = [dict(r) for r in conn.execute(
    """SELECT * FROM v_policy_status
       WHERE client_id = ? AND is_program = 1""",
    (client_id,),
).fetchall()]
```
Pass as `programs` to template context.

### Template

New partial `src/policydb/web/templates/clients/_programs.html` included in `detail.html` before the project groups loop.

---

## Policy Edit Page — Program Fields

When editing a program (`policy.is_program`), show additional fields:

- **Participating Carriers** — textarea or comma-separated input for `program_carriers`
- **Carrier Count** — auto-computed from carriers list (or manual override)
- **"This is a program"** toggle — `is_program` flag (checkbox or toggle switch)

The existing fields (premium, limit, deductible, effective/expiration, description, notes) all apply as-is.

---

## New Policy / Create Program

On the new policy page (`/policies/new`), add a "Program" checkbox alongside the existing "Opportunity" checkbox. When checked:
- Shows the program-specific fields (carriers list, carrier count)
- Labels change: "Premium" → "Total Program Premium", "Limit" → "Total Program Limit"
- `carrier` field label → "Lead Carrier"

---

## Reconciliation

### Matching

The fuzzy matcher (`reconciler.py`) matches statement lines to programs the same way as regular policies — client name + policy type. A statement line for "D&O" would match a program with `policy_type = "D&O Program"` or `policy_type = "Directors & Officers"`.

### Multiple matches to one program

When multiple statement lines from different carriers match the same program:
- Each match is recorded normally in the reconciliation results
- The UI shows the running total: "3 of 4 carriers reconciled · $140K of $180K matched"
- The program is fully reconciled when matched premium ≥ total premium (within tolerance)

### Partial reconciliation tracking

Add to the reconciliation results display:
- For program matches, show a progress indicator (matched/total premium)
- Group statement lines that matched the same program together in the review UI

### Import

No changes to the import flow. Statement lines import as usual. The matching logic handles the rest.

---

## Exports

- **Schedule of Insurance:** Include programs with a "PROGRAM" indicator in the Line of Business column (e.g., "D&O Program [PROGRAM]")
- **Full XLSX:** Include in the Policies sheet with `is_program` column
- **LLM export:** Include programs in the "Insurance Program" section with carrier list

---

## Files

| Action | File |
|--------|------|
| Create | `src/policydb/migrations/052_add_program_fields.sql` |
| Create | `src/policydb/web/templates/clients/_programs.html` |
| Modify | `src/policydb/db.py` (migration runner) |
| Modify | `src/policydb/views.py` (v_client_summary program_count, v_renewal_pipeline exclusion) |
| Modify | `src/policydb/web/routes/clients.py` (query programs, pass to context) |
| Modify | `src/policydb/web/templates/clients/detail.html` (include _programs.html) |
| Modify | `src/policydb/web/templates/policies/edit.html` (program fields) |
| Modify | `src/policydb/web/templates/policies/new.html` (program checkbox + fields) |
| Modify | `src/policydb/reconciler.py` (program-aware matching, partial reconciliation) |

---

## Verification

1. `policydb serve` — migration runs without error
2. Create a new program: "D&O Program", $180K premium, $20M limit, carriers "AIG, Chubb, Zurich"
3. Client detail page shows "Corporate Programs" section above project groups
4. Program row shows PROGRAM badge, metrics, carrier count
5. Edit the program — carriers list and carrier count fields visible
6. Run reconciliation with a statement containing 3 D&O line items from different carriers
7. All 3 match to the same program record
8. Review UI shows "3 of 3 carriers reconciled · $180K of $180K"
9. Schedule export includes the program with PROGRAM indicator
10. Programs excluded from renewal pipeline view
11. Client summary shows program count separately
