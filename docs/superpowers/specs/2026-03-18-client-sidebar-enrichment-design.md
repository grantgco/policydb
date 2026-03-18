# Client Detail Sidebar Enrichment — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

The right sidebar panel on the client detail page (`/clients/{id}`) only shows CN Number, FEIN, Onboarded date, and Review status. For an account executive preparing for a client call or managing their book, the sidebar should surface the financial context, key dates, and quick actions without scrolling.

---

## Solution

Enrich the sidebar card with 6 new sections plus 2 reorganized existing sections (8 total), ordered context-first: financial snapshot and key dates at top for pre-call prep, quick actions below, then existing metadata, renewal calendar, and location map at bottom. Also add a clickable website link.

**Note:** The top-level summary cards (Total Premium, Est. Revenue, etc.) already visible at the top of the page are _aggregate_ stats alongside Policies, Carriers, and Next Renewal. The sidebar financial snapshot serves a different purpose: it's the persistent quick-reference panel visible while scrolling through contacts, policies, and activity. The duplication is intentional — similar to how a CRM shows revenue in both a dashboard widget and a detail sidebar.

---

## Section Order (top to bottom)

### 1. Financial Snapshot

Three inline metrics using the existing `| currency` Jinja2 filter:
- **Premium** — `{{ summary.total_premium | currency }}`
- **Revenue** — `{{ summary.total_revenue | currency }}`
- **Broker Fee** — `{{ client.broker_fee | currency }}` (show `—` if null/0)

All three already exist in the template context (`summary` from `v_client_summary`, `client` from `get_client_by_id`). No new queries needed.

### 2. Key Dates

Four rows in a 2-column grid (label left, value right):
- **Client Since** — `client.client_since` (show year only, e.g. "2019"). Already on clients table.
- **Renewal Month** — `client.renewal_month` (integer 1–12 → month name via `calendar.month_name[n]`). Already on clients table.
- **Last Activity** — New query: `SELECT MAX(activity_date) FROM activity_log WHERE client_id = ?` (not derived from the 90-day-windowed `activities` list, which would be misleading for quiet accounts). Compute relative display in the route: `humanize.naturaltime(datetime.now() - dt)`. Pass as `last_activity_relative` string to template.
- **Next Follow-Up** — New query: `SELECT MIN(follow_up_date) AS dt FROM activity_log WHERE client_id = ? AND follow_up_done = 0 AND follow_up_date >= date('now')`. Display date + "(Xd)" in amber if within 7 days. Pass as `next_followup_date` to template.

### 3. Website

Clickable link to `client.website` if set. Display domain only (strip protocol via Jinja2 `replace`). Open in new tab. Hide section entirely if field is empty.

### 4. Quick Actions

2×2 grid of small styled buttons:
- **+ Log Activity** → `/activities?client_id={id}` (navigates to filtered activity list)
- **+ Follow-Up** → `/activities?client_id={id}` (same page — no pre-fill mechanism exists)
- **Export Schedule** → `/clients/{id}/export/schedule?fmt=xlsx` (direct download)
- **View Briefing** → `/briefing/client/{id}`

All routes already exist. No backend changes needed. The existing standalone "Account Briefing" card link (detail.html lines 92–96) will be removed since it's consolidated here.

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
         AND expiration_date IS NOT NULL
       GROUP BY month ORDER BY month""",
    (client_id,),
).fetchall()
```

Passed to template as `renewal_month_counts` dict: `{4: 3, 7: 1, 10: 2}`.

### 8. Location Map

Display `client.address` as text with a map pin icon. Link to `https://www.openstreetmap.org/search?query={address|urlencode}` opening in a new tab ("View on map →"). No iframe, no geocoding, no API key.

If `client.address` is empty, hide this section entirely.

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
3. Add `last_activity_date` query (NOT from the 90-day `activities` list):
   ```python
   last_act = conn.execute(
       "SELECT MAX(activity_date) AS dt FROM activity_log WHERE client_id = ?",
       (client_id,),
   ).fetchone()
   ```
   Compute relative display string using `humanize.naturaltime()` and pass as `last_activity_relative`.
4. Pass `renewal_month_counts`, `next_followup_date`, and `last_activity_relative` to template context

**Template changes:**
- `src/policydb/web/templates/clients/detail.html` — rewrite sidebar card block (lines 106–156); remove standalone Account Briefing card (lines 92–96)

---

## Files

| Action | File |
|--------|------|
| Modify | `src/policydb/web/routes/clients.py` (client_detail route — add 3 queries + 3 context vars) |
| Modify | `src/policydb/web/templates/clients/detail.html` (rewrite sidebar card, remove standalone briefing link) |

---

## Verification

1. `policydb serve`, open a client with policies, contacts, and activities
2. Sidebar shows: Financials → Key Dates → Website → Quick Actions → CN/FEIN/Onboarded → Review → Renewal Calendar → Map link
3. Financials display formatted currency via `| currency` filter
4. Key Dates: "Client Since" shows year, "Renewal Month" shows month name, "Last Activity" shows relative time (even for clients with no activity in 90 days), "Next Follow-Up" shows date with amber highlight if within 7 days
5. Website link opens in new tab showing domain only (or hidden if not set)
6. Quick Actions: all 4 buttons navigate to correct routes; "Export Schedule" triggers XLSX download
7. Standalone "Account Briefing" card no longer appears (consolidated into Quick Actions)
8. Renewal Calendar: months with policies are highlighted with counts; months without are dimmed
9. Map link: shows address text + "View on map →" linking to OpenStreetMap search
10. Client with no address → map section hidden
11. Client with no follow-ups → "Next Follow-Up" shows "—"
12. Client with zero policies → Renewal Calendar shows all months dimmed
13. Prospect client → sidebar renders without errors (financials show $0)
