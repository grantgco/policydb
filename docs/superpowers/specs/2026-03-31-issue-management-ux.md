# Issue Management UX Improvements

**Date:** 2026-03-31
**Status:** Spec approved, pending implementation

---

## Context

Issues are a first-class feature in PolicyDB — they track renewal blockers, coverage gaps, and ad-hoc problems across clients and policies. But the system has uneven visibility: the Action Center Issues tab is comprehensive, while the rest of the app (dashboard, nav, policy/client detail, opportunity rows) barely surfaces issues at all.

This spec addresses 8 targeted improvements that make issues visible and actionable from every context where users already work — without requiring a trip to Action Center.

---

## Design Decisions Summary

| # | Feature | Decision |
|---|---------|----------|
| 1 | Dashboard widget | Top alert block above Follow-ups section; appears only when issues exist |
| 2 | Nav badge | Red dot on Action Center nav link when any open issue exists (no count) |
| 3 | Opportunity row badges | Same `_issue_badge.html` component as policy rows; attach highest-severity open issue per opp |
| 4 | Opp-aware issue detail | Replace green renewal milestones block with purple pipeline context block when linked to opp |
| 5 | Policy pulse — all issues | Replace standalone renewal banner with unified "Issues" section listing all open issues |
| 7 | Unlink activity | × button on each linked activity row in issue detail timeline |
| 8 | Client sidebar issues | "Issues" subsection in Key Dates panel; one row per open issue with severity dot + name |
| 10 | Conversion auto-promote | On opp→policy conversion, silently promote open manual issues to renewal issues |

---

## Feature Specs

### #1 — Dashboard Issues Widget

**Behavior:** A collapsible alert block appears at the top of the dashboard, above the Follow-ups section, whenever any open issues exist for any client. If zero open issues, the block is hidden entirely.

**Content:**
- Header: "Issues — N open · X past SLA" (red if SLA breaches exist, amber otherwise)
- Up to 3 rows: severity dot + subject (truncated) + client name + age
- "View all →" link to `/action-center?tab=issues`
- "+New Issue" button (opens create slideover)

**Query:** Lightweight version of `_issues_ctx()` — fetch open issues ordered by severity (Critical first), limit 3, plus total count and SLA breach count. No pagination needed.

**Files to modify:**
- `src/policydb/web/routes/dashboard.py` — add `issues_widget` context (count, sla_count, top_issues list)
- `src/policydb/web/templates/dashboard.html` — insert widget partial above follow-ups section
- `src/policydb/web/templates/issues/_dashboard_widget.html` — new partial

**Query to add in `queries.py`:**
```python
def get_dashboard_issues_widget(conn, limit=3):
    """Returns top N open issues by severity + counts for dashboard widget."""
```

---

### #2 — Global Nav Issues Dot

**Behavior:** A small red dot appears on the "Action Center" nav link in `base.html` when any open issue exists for any client. No count number — just presence. Disappears when zero open issues.

**Implementation:** HTMX lazy-load to avoid adding a DB query to every route's context. A tiny endpoint returns the dot markup or empty string.

**Files to modify:**
- `src/policydb/web/templates/base.html` — add `<span hx-get="/api/nav/issues-dot" hx-trigger="load" hx-swap="outerHTML">` inside the Action Center nav link
- `src/policydb/web/routes/dashboard.py` (or a new `nav.py`) — add `GET /api/nav/issues-dot` endpoint; returns `<span class="w-2 h-2 rounded-full bg-red-500 inline-block ml-1"></span>` if open issues exist, else empty `<span>`

---

### #3 — Issue Badges on Opportunity Rows

**Behavior:** Opportunity rows in `_opp_row.html` and `_opp_client_row.html` show an issue badge (same `_issue_badge.html` component as policy rows) when an open issue is linked to the opportunity. Shows the highest-severity open issue.

**New query helper in `queries.py`:**
```python
def attach_open_issues(conn, rows, policy_id_field='id'):
    """
    For each row, find the highest-severity open issue linked via policy_id.
    Sets: issue_uid, issue_severity, issue_subject on each row dict.
    Used for opportunities (which don't have renewal_term_keys).
    """
```
Looks up `activity_log WHERE item_kind='issue' AND policy_id IN (...) AND issue_status NOT IN ('Resolved','Closed')`, takes worst severity per policy.

**Files to modify:**
- `src/policydb/queries.py` — add `attach_open_issues()`
- `src/policydb/web/routes/dashboard.py` — call `attach_open_issues(conn, opportunities)` after fetching `open_opportunities`
- `src/policydb/web/routes/clients.py` — call `attach_open_issues(conn, opps)` in the opportunities tab handler
- `src/policydb/web/templates/policies/_opp_row.html` — add issue badge block (same pattern as `_policy_row.html` lines 53–55)
- `src/policydb/web/templates/policies/_opp_client_row.html` — same

**Badge pattern to add:**
```html
{% if o.issue_uid %}
<div class="mt-1">
  {% with linked_issue_uid=o.issue_uid, linked_issue_subject=o.issue_subject|default('Issue'), linked_issue_severity=o.issue_severity %}
    {% include "_issue_badge.html" %}
  {% endwith %}
</div>
{% endif %}
```

---

### #4 — Opportunity-Aware Issue Detail Page

**Behavior:** When the issue's linked policy has `is_opportunity=1`, the green "Timeline Milestones / Renewal Issue" block is replaced by a purple "Opportunity — Pipeline Context" block. A purple "Opportunity" badge is added to the status/severity pill row.

**Context block content:** opportunity status, target effective date, estimated premium, coverage type (policy_type), link to the opportunity page.

**Route change in `src/policydb/web/routes/issues.py`:**
In `GET /issues/{issue_uid}`, after fetching the linked policy, check `policy.get('is_opportunity')`. If true, fetch opportunity-specific fields (status, target_effective_date, premium, policy_type) and pass `linked_opp=...` to template context.

**Template change in `src/policydb/web/templates/issues/detail.html`:**
```html
{% if linked_opp %}
  <!-- Purple opportunity context block -->
  <div class="card p-4 border-l-4 border-violet-400 bg-violet-50/40">
    <div class="text-[9px] text-violet-800 uppercase tracking-wide font-semibold mb-2">Opportunity — Pipeline Context</div>
    <!-- status, target date, premium, coverage, link -->
  </div>
{% elif issue.is_renewal_issue and timeline_milestones %}
  <!-- existing green renewal block -->
{% endif %}
```

---

### #5 — Policy Pulse — Unified Issues Section

**Behavior:** The standalone renewal issue banner in `_tab_pulse.html` is replaced by a unified "Issues" section that lists ALL open issues for the policy sorted by severity. Each row has severity dot, label, subject (truncated), days open, and a link. The renewal issue gets a "Renewal" tag. If no issues exist, the section is hidden. A "+ New Issue" button is included.

**Route change in `src/policydb/web/routes/policies.py`:**
In the Pulse tab handler, replace the single `get_renewal_issue_for_policy()` call with a direct DB query for all open issues on the policy: `SELECT * FROM activity_log WHERE policy_id=? AND item_kind='issue' AND issue_status NOT IN ('Resolved','Closed') ORDER BY CASE issue_severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Normal' THEN 3 ELSE 4 END`. Pass result as `open_issues` to the template. Do not call the HTTP `/issues/for-policy/` endpoint from the route handler.

**Template change in `src/policydb/web/templates/policies/_tab_pulse.html`:**
Replace lines 59–81 (renewal issue banner) with:
```html
{% if open_issues %}
<div class="card p-3">
  <div class="flex items-center justify-between mb-2">
    <span class="text-xs font-semibold text-gray-700">Issues ({{ open_issues|length }} open)</span>
    <button onclick="openIssueCreateSlideover({client_id: {{ policy.client_id }}, policy_id: {{ policy.id }}})">+ New Issue</button>
  </div>
  {% for issue in open_issues %}
    <!-- severity dot + [Renewal badge if is_renewal_issue] + subject + days open + link -->
  {% endfor %}
</div>
{% endif %}
```

---

### #7 — Unlink Activity from Issue

**Behavior:** Each linked activity row in the issue detail page's timeline shows a small × button. Clicking it unlinks the activity (sets `issue_id = NULL` on the `activity_log` row) and removes the row from the issue timeline via HTMX swap.

**New endpoint in `src/policydb/web/routes/issues.py`:**
```python
DELETE /issues/{issue_id}/unlink-activity/{activity_id}
```
Sets `activity_log.issue_id = NULL` where `id = activity_id AND issue_id = issue_id`. Returns 200 or OOB activity count update.

**Template change in `src/policydb/web/templates/issues/detail.html`:**
Add × button to each activity row in the linked activities timeline:
```html
<button hx-delete="/issues/{{ issue.id }}/unlink-activity/{{ act.id }}"
        hx-target="#activity-row-{{ act.id }}"
        hx-swap="outerHTML"
        class="text-gray-300 hover:text-red-500 text-xs ml-auto">×</button>
```

---

### #8 — Client Sidebar Issues Count

**Behavior:** A small "Issues" subsection is added to the Key Dates panel in the client sidebar. Shows one row per open issue: severity dot + issue subject (truncated). Clicking a row navigates to `/issues/{issue_uid}`. Hidden when no open issues.

**Route change in `src/policydb/web/routes/clients.py`:**
Pass `sidebar_issues` (open issues for the client) to the client detail template context. Reuse the existing `get_issues_for_client(conn, client_id)` query from `issues.py` (filter to open only, limit 5 for sidebar).

**Template change:** Find the Key Dates sidebar partial (likely in `clients/detail.html` or a `_sidebar.html` partial) and add:
```html
{% if sidebar_issues %}
<div class="mt-3 pt-3 border-t border-gray-100">
  <div class="text-[9px] text-gray-400 uppercase tracking-wide font-semibold mb-1">Issues</div>
  {% for issue in sidebar_issues %}
  <a href="/issues/{{ issue.issue_uid }}" class="flex items-center gap-1.5 text-xs text-gray-600 hover:text-marsh py-0.5">
    <span class="w-1.5 h-1.5 rounded-full flex-shrink-0 {% if issue.issue_severity == 'Critical' %}bg-red-500{% elif issue.issue_severity == 'High' %}bg-amber-500{% elif issue.issue_severity == 'Normal' %}bg-blue-500{% else %}bg-gray-400{% endif %}"></span>
    <span class="truncate">{{ issue.subject[:28] }}{% if issue.subject|length > 28 %}…{% endif %}</span>
  </a>
  {% endfor %}
</div>
{% endif %}
```

---

### #10 — Auto-Promote Issue on Opportunity Conversion

**Behavior:** When `POST /{policy_uid}/convert` runs and sets `is_opportunity=0`, the conversion handler checks for any open manual issues linked to that policy (`activity_log WHERE policy_id = policy.id AND item_kind='issue' AND issue_status NOT IN ('Resolved','Closed') AND (is_renewal_issue IS NULL OR is_renewal_issue = 0)`). For each found, silently:
1. Set `is_renewal_issue = 1`
2. Set `renewal_term_key = policy_uid`
3. Call `sync_renewal_issue_severity(conn, policy_uid)` to update severity from timeline health

No user-facing prompt. The issue carries over its activity history intact.

**New helper in `src/policydb/renewal_issues.py`:**
```python
def promote_issue_to_renewal(conn, policy_id, policy_uid):
    """
    After opp→policy conversion: find any open manual issues linked to this policy
    and promote them to renewal issues (set is_renewal_issue=1, renewal_term_key=policy_uid).
    """
```

**Route change in `src/policydb/web/routes/policies.py`:**
In the `convert` endpoint, after setting `is_opportunity=0` and generating timeline, call:
```python
from policydb.renewal_issues import promote_issue_to_renewal
promote_issue_to_renewal(conn, policy_id=policy.id, policy_uid=policy_uid)
```

---

## Data Changes

No new migrations required. All changes use existing columns:
- `activity_log.is_renewal_issue` — already exists (migration 113)
- `activity_log.renewal_term_key` — already exists (migration 113)
- `activity_log.issue_id` — already exists (links activities to issues)

---

## Key Files Reference

| File | Changes |
|------|---------|
| `src/policydb/queries.py` | Add `get_dashboard_issues_widget()`, `attach_open_issues()` |
| `src/policydb/renewal_issues.py` | Add `promote_issue_to_renewal()` |
| `src/policydb/web/routes/dashboard.py` | Add widget context, nav dot endpoint, attach issues to opps |
| `src/policydb/web/routes/policies.py` | Pulse tab: all issues; conversion: call promote_issue_to_renewal |
| `src/policydb/web/routes/clients.py` | Sidebar issues context; attach issues to opp tab |
| `src/policydb/web/routes/issues.py` | Add `DELETE /issues/{id}/unlink-activity/{id}`; opp context in detail |
| `src/policydb/web/templates/base.html` | Nav dot HTMX element |
| `src/policydb/web/templates/dashboard.html` | Issues widget partial include |
| `src/policydb/web/templates/issues/_dashboard_widget.html` | New partial |
| `src/policydb/web/templates/issues/detail.html` | Opp context block; unlink button on activities |
| `src/policydb/web/templates/policies/_tab_pulse.html` | Unified issues section |
| `src/policydb/web/templates/policies/_opp_row.html` | Issue badge |
| `src/policydb/web/templates/policies/_opp_client_row.html` | Issue badge |
| `src/policydb/web/templates/clients/detail.html` or sidebar partial | Issues subsection in Key Dates |

---

## Verification

1. **Dashboard widget:** Create a test issue → reload dashboard → widget appears above follow-ups. Resolve issue → widget disappears.
2. **Nav dot:** Open issue exists → red dot on Action Center link. Resolve all → dot gone.
3. **Opp badges:** Link a manual issue to an opportunity → opp row in dashboard and client detail shows badge.
4. **Opp issue detail:** View issue linked to opportunity → purple pipeline block visible, no renewal milestones.
5. **Policy pulse unified:** Policy with both a renewal issue and a manual issue → pulse tab shows both in unified section with "Renewal" tag on the renewal one.
6. **Unlink activity:** Open issue with linked activities → × button on each activity → click × → activity row removed from issue timeline.
7. **Client sidebar:** Client with open issues → Key Dates panel shows Issues subsection with severity dots.
8. **Conversion promote:** Opportunity with open manual issue → convert to policy → issue gains `is_renewal_issue=1` and `renewal_term_key=policy_uid` in DB. Verify in Pulse tab that it now shows as the renewal issue.
