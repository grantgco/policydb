# Client Detail Sidebar Enrichment — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

The right sidebar panel on the client detail page (`/clients/{id}`) only shows CN Number, FEIN, Onboarded date, and Review status. For an account executive preparing for a client call or managing their book, the sidebar should surface the financial context, key dates, and quick actions without scrolling.

---

## Solution

Enrich the sidebar card with 6 new sections, ordered context-first (Option B from brainstorm): financial snapshot and key dates at top for pre-call prep, quick actions below, then existing metadata, renewal calendar, and location map at bottom. Also add a clickable website link.

---

## Section Order (top to bottom)

### 1. Financial Snapshot

Three inline metrics:
- **Premium** — `summary.total_premium` formatted with `humanize.intcomma` (e.g. `$1,245,000`)
- **Revenue** — `summary.total_revenue` formatted same way
- **Broker Fee** — `client.broker_fee` formatted same way (show `—` if null/0)

All three already exist in the template context (`summary` from `v_client_summary`, `client` from `get_client_by_id`). No new queries needed.

### 2. Key Dates

Four rows in a 2-column grid (label left, value right):
- **Client Since** — `client.client_since` (show year only, e.g. "2019"). Already on clients table.
- **Renewal Month** — `client.renewal_month` (integer 1–12 → month name, e.g. "April"). Already on clients table.
- **Last Activity** — Computed from `activities` list already in context: `activities[0].activity_date` if any exist. Display as relative (e.g. "3 days ago") using `dateparser.parse()` + humanize.
- **Next Follow-Up** — New query: `MIN(follow_up_date)` from `activity_log` where `client_id = ? AND follow_up_done = 0 AND follow_up_date >= date('now')`. Display date + relative days in amber if within 7 days.

### 3. Website

Clickable link to `client.website` if set. Display domain only (strip protocol). Open in new tab. Show nothing if field is empty.

### 4. Quick Actions

2×2 grid of small buttons linking to existing routes:
- **+ Log Activity** → `/activities?client_id={id}` with a focus-on-form anchor, or inline HTMX trigger
- **+ Follow-Up** → same as Log Activity but pre-selects follow-up type
- **Export Schedule** → `/clients/{id}/export/schedule?fmt=xlsx` (direct download)
- **View Briefing** → `/briefing/client/{id}`

All routes already exist. No backend changes needed.

### 5. Existing Metadata (compact)

Compact horizontal layout combining CN Number, FEIN (if set), and Onboarded on fewer lines than currently.

### 6. Review Section

Same existing functionality (cycle dropdown, last reviewed date, Mark Reviewed button) but in a more compact single-row layout.

### 7. Renewal Calendar

12-month grid showing policy count per expiration month. Months with renewals are highlighted (amber for upcoming within 90 days, blue for later). Months without renewals are dimmed.

**New query** added to the client detail route:
```python
renewal_months = conn.execute(
    """SELECT CAST(strftime('%m', expiration_date) AS INTEGER) AS month,
              COUNT(*) AS cnt
       FROM policies
       WHERE client_id = ? AND archived = 0
         AND (is_opportunity = 0 OR is_opportunity IS NULL)
       GROUP BY month ORDER BY month""",
    (client_id,),
).fetchall()
```

Passed to template as `renewal_month_counts` dict: `{4: 3, 7: 1, 10: 2}`.

### 8. Location Map

OpenStreetMap iframe embed using `client.address` field (single unstructured TEXT column). If address is empty, hide this section entirely.

Embed URL pattern: `https://www.openstreetmap.org/export/embed.html?bbox=...` — however, for a simple address lookup, use the Nominatim search embed:
```html
<iframe src="https://www.openstreetmap.org/export/embed.html?layer=mapnik"
  style="width:100%;height:120px;border:0;border-radius:6px"></iframe>
```

**Alternative (simpler, no geocoding):** Link to `https://www.openstreetmap.org/search?query={address}` instead of an iframe. This avoids geocoding complexity and opens the full map when clicked. Display the address text with a map pin icon and "View on map →" link.

**Decision:** Use the link approach (no iframe). An iframe requires geocoding coordinates which adds complexity. A "View on map →" link is simple, works with any address format, and keeps the sidebar lightweight.

---

## Data Layer

**No migration required.** All fields already exist.

**Route changes** (`src/policydb/web/routes/clients.py`, `client_detail` function):
1. Add `renewal_month_counts` query (see Section 7 above)
2. Add `next_followup_date` query:
   ```python
   next_fu = conn.execute(
       """SELECT MIN(follow_up_date) AS dt FROM activity_log
          WHERE client_id = ? AND follow_up_done = 0
            AND follow_up_date >= date('now')""",
       (client_id,),
   ).fetchone()
   ```
3. Compute `last_activity_date` from existing `activities` list: `activities[0]["activity_date"] if activities else None`
4. Pass all three to template context

**Template changes** — single file: `src/policydb/web/templates/clients/detail.html` (the sidebar `<div class="card p-4 text-sm flex flex-col gap-3">` block, lines 106–156).

---

## Files

| Action | File |
|--------|------|
| Modify | `src/policydb/web/routes/clients.py` (client_detail route — add 2 queries + 3 context vars) |
| Modify | `src/policydb/web/templates/clients/detail.html` (rewrite sidebar card) |

---

## Verification

1. `policydb serve`, open a client with policies, contacts, and activities
2. Sidebar shows: Financials → Key Dates → Website → Quick Actions → CN/FEIN/Onboarded → Review → Renewal Calendar → Map link
3. Financials display formatted currency from `v_client_summary`
4. Key Dates: "Client Since" shows year, "Renewal Month" shows month name, "Last Activity" shows relative time, "Next Follow-Up" shows date with amber highlight if within 7 days
5. Website link opens in new tab (or hidden if not set)
6. Quick Actions: all 4 buttons navigate to correct routes
7. Renewal Calendar: months with policies are highlighted with counts
8. Map link: shows address text + "View on map →" linking to OpenStreetMap search
9. Client with no address → map section hidden
10. Client with no follow-ups → "Next Follow-Up" shows "—"
