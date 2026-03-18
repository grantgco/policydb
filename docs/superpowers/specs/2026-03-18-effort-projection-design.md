# Effort Projection & Cost-to-Serve — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

Account executives need to quantify how much time an account will require going forward — especially around renewal windows — to justify internal resource requests. Currently, the briefing page shows historical hours but offers no forward-looking projection, cost estimate, or copy-paste narrative for resource planning.

---

## Solution

Add an "Effort Forecast" section to the client briefing page (`/briefing/client/{id}`) with a bar chart showing historical actuals + projected months, and an auto-generated narrative paragraph with cost-to-serve metrics. The hourly rate is configurable per client (with a global default fallback).

---

## Data Model

### New migration: `051_add_hourly_rate.sql`

```sql
ALTER TABLE clients ADD COLUMN hourly_rate REAL;
```

Nullable — when NULL, falls back to `default_hourly_rate` from config.

### New config key

Add `default_hourly_rate` to `_DEFAULTS` in `src/policydb/config.py`:
```python
"default_hourly_rate": 150,
```

Editable via Settings page (`/settings`) — add to the numeric settings section.

### Client edit page

Add `hourly_rate` field to `src/policydb/web/templates/clients/edit.html` in the financials section, near `broker_fee`. Currency input pattern (text + hidden), label: "Hourly Rate ($/hr)" with placeholder showing the global default.

---

## Projection Model

Implemented as a pure Python function in a new module or in `src/policydb/queries.py`:

```python
def build_effort_projection(conn, client_id, months_back=6, months_forward=6):
```

### Inputs
- `activity_log` rows with `duration_hours > 0` for the client, grouped by `strftime('%Y-%m', activity_date)`
- `policies.expiration_date` for the client (renewal months)
- `client.hourly_rate` or `cfg.get("default_hourly_rate", 150)` fallback

### Algorithm

1. **Historical monthly hours:** Query last `months_back` months of `activity_log`, GROUP BY year-month. Fill missing months with 0.

2. **Weighted average:** Recent months weighted more heavily. Weight = `1 + (i / months_back)` where `i` is 0 for oldest, `months_back-1` for most recent. Compute `weighted_avg = sum(hours * weight) / sum(weights)`.

3. **Renewal months:** Query `SELECT CAST(strftime('%m', expiration_date) AS INTEGER) AS month, COUNT(*) AS cnt FROM policies WHERE client_id = ? AND archived = 0 AND (is_opportunity = 0 OR is_opportunity IS NULL) AND expiration_date IS NOT NULL GROUP BY month`. Build a set of renewal months.

4. **Projected months:** For each of the next `months_forward` months:
   - Base = `weighted_avg`
   - If month number is in renewal months: multiply by `cfg.get("renewal_effort_multiplier", 1.5)`
   - Round to 1 decimal

5. **Annual estimate:** `sum(all 12 projected months)` (use actuals for past months in the current year, projected for future months).

6. **Cost estimate:** `annual_hours * hourly_rate`

7. **Revenue ratio:** `total_revenue / cost_estimate` (from `v_client_summary.total_revenue`)

### Return value

```python
{
    "actuals": [{"month": "2025-12", "label": "Dec", "hours": 1.0}, ...],
    "projected": [{"month": "2026-04", "label": "Apr", "hours": 12.0, "is_renewal": True}, ...],
    "weighted_avg": 3.2,
    "annual_hours": 42.0,
    "hourly_rate": 150,
    "annual_cost": 6300,
    "total_revenue": 142000,
    "revenue_ratio": 22.5,
    "renewal_months": [4, 7, 10],
    "narrative": "Based on 4 months of history, Acme Corp requires approximately ..."
}
```

---

## Narrative Generation

Auto-generated text, suitable for copy-paste into internal resource requests:

> Based on {months_of_data} months of history, {client_name} requires approximately **{weighted_avg} hours/month** on average. With {renewal_count} policies renewing in {next_renewal_month}, effort is projected to spike to **~{peak_hours} hours** that month. Estimated annual effort: **~{annual_hours} hours** (${annual_cost:,.0f} at ${hourly_rate}/hr). Current revenue: ${total_revenue:,.0f} ({revenue_ratio:.1f}x cost-to-serve ratio).

If `revenue_ratio < 5`: append "This account may warrant a profitability review."
If `revenue_ratio > 20`: append "Strong cost-to-serve ratio — current staffing adequate."

---

## UI — Client Briefing Page

### Location

New section in `src/policydb/web/templates/briefing_client.html`, after the existing "Time Summary" section and before "Notes."

### Chart

Horizontal bar chart rendered with inline CSS (no chart library — same pattern as existing time-by-policy bars in the briefing). Each bar = one month.

- **Actuals** (past months): solid `bg-marsh` bars
- **Projected renewal months:** `bg-amber-400` bars with `opacity-70`
- **Projected baseline months:** `bg-blue-300` bars with `opacity-70`
- **Dashed vertical line** separating actuals from projections
- Y-axis labels: month abbreviation (Dec, Jan, Feb...)
- X-axis: hours scale (auto from max value)
- Legend below chart: Actual / Projected (renewal) / Projected (baseline)

### Metrics row

Below chart, compact row of 4 stats:
- Avg/Month: `{weighted_avg}h`
- Est. Annual: `~{annual_hours}h`
- Est. Cost: `${annual_cost:,.0f}`
- Revenue Ratio: `{revenue_ratio:.1f}x`

### Narrative block

Below metrics, `bg-gray-50` rounded box with the generated narrative text. "Copy narrative" button that copies text to clipboard.

---

## Route Changes

### `src/policydb/web/routes/briefing.py` — `briefing_client()` function

Add after existing `time_summary` computation:

```python
from policydb.queries import build_effort_projection
effort_projection = build_effort_projection(conn, client_id)
```

Pass `effort_projection` to template context.

### `src/policydb/web/routes/settings.py`

Add `default_hourly_rate` to the numeric settings section (alongside existing numeric configs). Display as a currency input.

---

## Files

| Action | File |
|--------|------|
| Create | `src/policydb/migrations/051_add_hourly_rate.sql` |
| Modify | `src/policydb/config.py` (`_DEFAULTS` — add `default_hourly_rate`) |
| Modify | `src/policydb/queries.py` (add `build_effort_projection()`) |
| Modify | `src/policydb/web/routes/briefing.py` (add projection to client briefing context) |
| Modify | `src/policydb/web/templates/briefing_client.html` (add forecast section) |
| Modify | `src/policydb/web/templates/clients/edit.html` (add hourly_rate field) |
| Modify | `src/policydb/web/templates/settings.html` (add default_hourly_rate) |

---

## Verification

1. `policydb serve` — verify migrations run without error
2. Open Settings → confirm "Default Hourly Rate" field appears, defaults to $150
3. Open a client's edit page → confirm "Hourly Rate" field appears
4. Set a custom rate on one client, leave another at default
5. Open `/briefing/client/{id}` for a client with activity history
6. Confirm chart shows past months as solid bars, future months as lighter bars
7. Renewal months show in amber, baseline in blue
8. Metrics row shows avg, annual, cost, ratio
9. Narrative text is readable and accurate
10. "Copy narrative" button copies to clipboard
11. Client with custom rate shows that rate in the narrative; client without shows global default
12. Client with zero activity history → section shows "Not enough data for projection" message
