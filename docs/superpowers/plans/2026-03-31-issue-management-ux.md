# Issue Management UX Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface issues visibly across the entire app — dashboard, nav, opportunity rows, policy pulse tab, client sidebar, and issue detail — plus add unlink-activity and opp→policy issue promotion.

**Architecture:** All 8 features use existing DB columns (`activity_log.is_renewal_issue`, `renewal_term_key`, `issue_id`). No migrations required. New query helpers in `queries.py`, new `promote_issue_to_renewal()` in `renewal_issues.py`, new endpoint for unlink, HTMX lazy-load for nav dot.

**Tech Stack:** FastAPI + Jinja2 + HTMX + SQLite. Tailwind CSS (CDN). No JS framework.

---

## File Map

| File | What changes |
|------|-------------|
| `src/policydb/queries.py` | Add `attach_open_issues()`, `get_dashboard_issues_widget()` |
| `src/policydb/renewal_issues.py` | Add `promote_issue_to_renewal()` |
| `src/policydb/web/routes/issues.py` | Add `DELETE /issues/{id}/unlink-activity/{id}`; add opp fields to detail SELECT |
| `src/policydb/web/routes/dashboard.py` | Add `GET /api/nav/issues-dot`; call `attach_open_issues` on opps; add `issues_widget` context |
| `src/policydb/web/routes/clients.py` | Call `attach_open_issues` on opp tab; add `sidebar_issues` to client detail context |
| `src/policydb/web/routes/policies.py` | Pulse tab: replace renewal_issue query with all-open-issues; convert: call `promote_issue_to_renewal` |
| `src/policydb/web/templates/base.html` | Add HTMX issues dot to Action Center nav link |
| `src/policydb/web/templates/dashboard.html` | Insert `_dashboard_issues_widget.html` partial before Follow-Ups |
| `src/policydb/web/templates/issues/_dashboard_issues_widget.html` | New file — dashboard widget partial |
| `src/policydb/web/templates/issues/detail.html` | Opp context block (lines 257-297); unlink button on each activity row |
| `src/policydb/web/templates/policies/_tab_pulse.html` | Replace renewal banner (lines 58-72) with unified issues section |
| `src/policydb/web/templates/policies/_opp_row.html` | Add issue badge |
| `src/policydb/web/templates/policies/_opp_client_row.html` | Add issue badge |
| `src/policydb/web/templates/clients/_sticky_sidebar.html` | Add Issues subsection after Key Dates (after line 96) |

---

## Task 1: Query Helpers — `attach_open_issues()` + `get_dashboard_issues_widget()`

**Files:**
- Modify: `src/policydb/queries.py`

- [ ] **Step 1: Add `attach_open_issues()` after `attach_renewal_issues()`**

  Find `attach_renewal_issues` in `queries.py` and add this function immediately after it:

  ```python
  def attach_open_issues(conn: sqlite3.Connection, rows: list[dict], policy_id_field: str = "id") -> None:
      """Batch-attach highest-severity open issue to each row dict.

      Sets issue_uid, issue_severity, issue_subject on each row.
      Used for opportunities (which use policy_id, not renewal_term_key).
      """
      if not rows:
          return
      policy_ids = [r.get(policy_id_field) for r in rows if r.get(policy_id_field)]
      if not policy_ids:
          return
      ph = ",".join("?" * len(policy_ids))
      issue_rows = conn.execute(
          f"""SELECT policy_id, issue_uid, issue_severity, subject
              FROM activity_log
              WHERE item_kind = 'issue'
                AND policy_id IN ({ph})
                AND issue_status NOT IN ('Resolved', 'Closed')
              ORDER BY CASE issue_severity
                  WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                  WHEN 'Normal' THEN 3 ELSE 4 END""",
          policy_ids,
      ).fetchall()
      # First row per policy_id wins (already sorted by severity)
      lookup: dict[int, dict] = {}
      for row in issue_rows:
          pid = row["policy_id"]
          if pid not in lookup:
              lookup[pid] = dict(row)
      for r in rows:
          pid = r.get(policy_id_field)
          if pid and pid in lookup:
              issue = lookup[pid]
              r["issue_uid"] = issue["issue_uid"]
              r["issue_severity"] = issue["issue_severity"]
              r["issue_subject"] = issue["subject"]
          else:
              r.setdefault("issue_uid", None)
  ```

- [ ] **Step 2: Add `get_dashboard_issues_widget()` near other dashboard-oriented queries**

  Add after `attach_open_issues()`:

  ```python
  def get_dashboard_issues_widget(conn: sqlite3.Connection, limit: int = 3) -> dict:
      """Return open issue counts and top issues for the dashboard alert block."""
      top = conn.execute(
          """SELECT a.issue_uid, a.subject, a.issue_severity, a.issue_status,
                    a.issue_sla_days,
                    c.name AS client_name,
                    CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open
             FROM activity_log a
             LEFT JOIN clients c ON c.id = a.client_id
             WHERE a.item_kind = 'issue'
               AND a.issue_status NOT IN ('Resolved', 'Closed')
             ORDER BY CASE a.issue_severity
                 WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                 WHEN 'Normal' THEN 3 ELSE 4 END,
                 days_open DESC
             LIMIT ?""",
          (limit,),
      ).fetchall()
      total = conn.execute(
          "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue'"
          " AND issue_status NOT IN ('Resolved','Closed')"
      ).fetchone()[0]
      sla_count = conn.execute(
          """SELECT COUNT(*) FROM activity_log
             WHERE item_kind='issue'
               AND issue_status NOT IN ('Resolved','Closed')
               AND issue_sla_days IS NOT NULL
               AND CAST(julianday('now') - julianday(activity_date) AS INTEGER) > issue_sla_days"""
      ).fetchone()[0]
      return {
          "total": total,
          "sla_count": sla_count,
          "top_issues": [dict(r) for r in top],
      }
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/policydb/queries.py
  git commit -m "feat: add attach_open_issues() and get_dashboard_issues_widget() query helpers"
  ```

---

## Task 2: Add `promote_issue_to_renewal()` to `renewal_issues.py`

**Files:**
- Modify: `src/policydb/renewal_issues.py`

- [ ] **Step 1: Add the function at the bottom of `renewal_issues.py`**

  ```python
  def promote_issue_to_renewal(conn, policy_id: int, policy_uid: str) -> None:
      """After opp→policy conversion: promote open manual issues to renewal issues.

      Finds any open non-renewal issues linked to the policy and sets
      is_renewal_issue=1 + renewal_term_key=policy_uid, then syncs severity
      from the policy's new timeline health.
      """
      rows = conn.execute(
          """SELECT id FROM activity_log
             WHERE policy_id = ?
               AND item_kind = 'issue'
               AND issue_status NOT IN ('Resolved', 'Closed')
               AND (is_renewal_issue IS NULL OR is_renewal_issue = 0)""",
          (policy_id,),
      ).fetchall()
      if not rows:
          return
      for row in rows:
          conn.execute(
              """UPDATE activity_log
                 SET is_renewal_issue = 1,
                     renewal_term_key = ?
                 WHERE id = ?""",
              (policy_uid, row["id"]),
          )
      conn.commit()
      sync_renewal_issue_severity(conn, policy_uid)
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/policydb/renewal_issues.py
  git commit -m "feat: add promote_issue_to_renewal() for opp→policy conversion"
  ```

---

## Task 3: Issues Route — Unlink Endpoint + Opp Context in Detail

**Files:**
- Modify: `src/policydb/web/routes/issues.py`

- [ ] **Step 1: Add opp fields to the issue detail SELECT query**

  In `issue_detail()` (the `GET /issues/{issue_uid}` handler), find the `conn.execute("""SELECT a.*, c.name AS client_name, ...`)` query. Replace the entire SELECT to add opportunity fields on the joined policy:

  ```python
  issue = conn.execute("""
      SELECT a.*, c.name AS client_name,
             p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
             p.is_opportunity, p.opportunity_status, p.target_effective_date,
             p.premium AS policy_premium,
             pr.name AS location_name,
             CASE WHEN a.resolved_date IS NOT NULL
                  THEN julianday(a.resolved_date) - julianday(a.activity_date)
                  ELSE julianday(date('now')) - julianday(a.activity_date)
             END AS days_open,
             CASE WHEN a.resolved_date IS NOT NULL
                  THEN julianday(a.resolved_date) - julianday(a.activity_date)
                  ELSE NULL
             END AS time_to_resolve
      FROM activity_log a
      LEFT JOIN clients c ON c.id = a.client_id
      LEFT JOIN policies p ON p.id = a.policy_id
      LEFT JOIN projects pr ON pr.id = p.project_id
      WHERE a.issue_uid = ? AND a.item_kind = 'issue'
  """, (issue_uid,)).fetchone()
  ```

- [ ] **Step 2: Add the unlink-activity endpoint**

  Find the `link_activities` POST endpoint in `issues.py`. Add this new endpoint immediately after it (before the `GET /issues/{issue_uid}` handler):

  ```python
  @router.delete("/issues/{issue_id}/unlink-activity/{activity_id}", response_class=HTMLResponse)
  def unlink_activity(
      issue_id: int,
      activity_id: int,
      conn=Depends(get_db),
  ):
      """Remove a single activity from an issue's timeline."""
      conn.execute(
          "UPDATE activity_log SET issue_id = NULL WHERE id = ? AND issue_id = ?",
          (activity_id, issue_id),
      )
      conn.commit()
      return HTMLResponse("")
  ```

  > **Route ordering note:** This endpoint uses two integer path params and comes before `GET /issues/{issue_uid}` (string param). FastAPI handles this correctly — no ordering conflict.

- [ ] **Step 3: Commit**

  ```bash
  git add src/policydb/web/routes/issues.py
  git commit -m "feat: add unlink-activity endpoint; add opp fields to issue detail query"
  ```

---

## Task 4: Dashboard Route — Nav Dot Endpoint + Widget Context + Opp Badges

**Files:**
- Modify: `src/policydb/web/routes/dashboard.py`

- [ ] **Step 1: Add the nav dot endpoint**

  At the top of `dashboard.py`, ensure this import exists (add if missing):
  ```python
  from policydb.queries import (
      get_renewal_pipeline, get_all_followups, get_renewal_metrics,
      get_open_opportunities, attach_renewal_issues, get_suggested_followups,
      get_stale_renewals, get_escalation_alerts, get_dashboard_hours_this_month,
      attach_open_issues, get_dashboard_issues_widget,
  )
  ```

  Then add this endpoint anywhere in the file (e.g., after the dashboard GET handler):

  ```python
  @router.get("/api/nav/issues-dot", response_class=HTMLResponse)
  def nav_issues_dot(conn=Depends(get_db)):
      """HTMX lazy-load: returns a red dot if any open issues exist, else empty span."""
      count = conn.execute(
          "SELECT COUNT(*) FROM activity_log"
          " WHERE item_kind='issue' AND issue_status NOT IN ('Resolved','Closed')"
      ).fetchone()[0]
      if count:
          return HTMLResponse(
              '<span class="w-2 h-2 rounded-full bg-red-500 inline-block ml-1 flex-shrink-0 align-middle"></span>'
          )
      return HTMLResponse('<span></span>')
  ```

  > **Note:** This endpoint must be registered in `app.py` if `dashboard.py` router has a prefix. Check that the router prefix allows `/api/nav/issues-dot` to resolve correctly. If the dashboard router has prefix `/`, it will work as-is.

- [ ] **Step 2: Wire up `attach_open_issues` and `get_dashboard_issues_widget` in the dashboard handler**

  In the `dashboard()` function, find the block that processes `open_opportunities`:

  ```python
  open_opportunities = get_open_opportunities(conn)
  ```

  After that line (after all the `open_opportunities` loop processing for `days_to_target` and `mailto_subject`), add:

  ```python
  attach_open_issues(conn, open_opportunities)
  issues_widget = get_dashboard_issues_widget(conn, limit=3)
  ```

- [ ] **Step 3: Add `issues_widget` to the template context**

  In the `return templates.TemplateResponse(...)` call at the bottom of `dashboard()`, add:

  ```python
  "issues_widget": issues_widget,
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/policydb/web/routes/dashboard.py
  git commit -m "feat: add nav dot endpoint; wire issues widget and opp badges in dashboard"
  ```

---

## Task 5: Clients Route — Sidebar Issues + Opp Badges

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 1: Add imports**

  At the top of `clients.py`, ensure the import for `attach_open_issues` is present:

  ```python
  from policydb.queries import (
      ...,  # existing imports
      attach_open_issues,
  )
  ```

- [ ] **Step 2: Wire `attach_open_issues` onto opportunities in `client_detail()`**

  In `client_detail()`, find the line where `opportunities` is defined:

  ```python
  opportunities = [p for p in all_policies if p.get("is_opportunity")]
  ```

  After the existing `opportunities` team-contacts attachment block (which loops `_opp_contacts`), add:

  ```python
  attach_open_issues(conn, opportunities)
  ```

- [ ] **Step 3: Add `sidebar_issues` to `client_detail()` context**

  In `client_detail()`, after fetching `activities`, add:

  ```python
  sidebar_issues = conn.execute(
      """SELECT issue_uid, subject, issue_severity
         FROM activity_log
         WHERE client_id = ? AND item_kind = 'issue'
           AND issue_status NOT IN ('Resolved', 'Closed')
         ORDER BY CASE issue_severity
             WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
             WHEN 'Normal' THEN 3 ELSE 4 END
         LIMIT 5""",
      (client_id,),
  ).fetchall()
  sidebar_issues = [dict(r) for r in sidebar_issues]
  ```

  Add `"sidebar_issues": sidebar_issues` to the `return templates.TemplateResponse(...)` context dict.

- [ ] **Step 4: Commit**

  ```bash
  git add src/policydb/web/routes/clients.py
  git commit -m "feat: add sidebar_issues context and attach opp issue badges in client detail"
  ```

---

## Task 6: Policies Route — Pulse Tab + Convert Endpoint

**Files:**
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Update the Pulse tab query**

  Find the Pulse tab route handler (`GET /{policy_uid}/tab/pulse`). Find lines 2823-2834 — the block that queries `renewal_issue`:

  ```python
  # Renewal issue for this policy (if any)
  renewal_issue = conn.execute("""
      SELECT id, issue_uid, issue_status, issue_severity, is_renewal_issue,
             julianday(date('now')) - julianday(activity_date) AS days_open,
             (SELECT COUNT(*) FROM activity_log sub WHERE sub.issue_id = a.id) AS activity_count
      FROM activity_log a
      WHERE a.is_renewal_issue = 1
        AND a.renewal_term_key = ?
        AND a.issue_status NOT IN ('Resolved', 'Closed')
      LIMIT 1
  """, (policy_uid,)).fetchone()
  renewal_issue = dict(renewal_issue) if renewal_issue else None
  ```

  Replace it entirely with:

  ```python
  # All open issues for this policy (unified section replaces renewal-only banner)
  _policy_id_row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)).fetchone()
  open_issues = []
  if _policy_id_row:
      open_issues = [dict(r) for r in conn.execute(
          """SELECT id, issue_uid, issue_status, issue_severity, is_renewal_issue, subject,
                    CAST(julianday('now') - julianday(activity_date) AS INTEGER) AS days_open,
                    (SELECT COUNT(*) FROM activity_log sub WHERE sub.issue_id = a.id) AS activity_count
             FROM activity_log a
             WHERE a.policy_id = ?
               AND a.item_kind = 'issue'
               AND a.issue_status NOT IN ('Resolved', 'Closed')
             ORDER BY CASE a.issue_severity
                 WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                 WHEN 'Normal' THEN 3 ELSE 4 END""",
          (_policy_id_row["id"],),
      ).fetchall()]
  ```

- [ ] **Step 2: Update the Pulse tab template context**

  In the same route handler, find the `return templates.TemplateResponse(...)`. Replace `"renewal_issue": renewal_issue` with:

  ```python
  "open_issues": open_issues,
  ```

  Remove `renewal_issue` from the context dict entirely (it's no longer used).

- [ ] **Step 3: Update the convert endpoint**

  Find `policy_convert_opportunity()` (the `POST /{policy_uid}/convert` handler). After the `conn.commit()` call and before the redirect, add:

  ```python
  # Promote any open manual issues linked to this policy to renewal issues
  _pol_id_row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
  if _pol_id_row:
      from policydb.renewal_issues import promote_issue_to_renewal
      promote_issue_to_renewal(conn, policy_id=_pol_id_row["id"], policy_uid=uid)
  ```

  The full end of the function should look like:

  ```python
  conn.commit()

  # Generate timeline for converted policy if profile is set
  _regen = conn.execute(
      "SELECT milestone_profile FROM policies WHERE policy_uid = ?", (uid,)
  ).fetchone()
  if _regen and _regen["milestone_profile"]:
      from policydb.timeline_engine import generate_policy_timelines
      generate_policy_timelines(conn, policy_uid=uid)

  # Promote any open manual issues to renewal issues
  _pol_id_row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
  if _pol_id_row:
      from policydb.renewal_issues import promote_issue_to_renewal
      promote_issue_to_renewal(conn, policy_id=_pol_id_row["id"], policy_uid=uid)

  return RedirectResponse(f"/policies/{uid}/edit", status_code=303)
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/policydb/web/routes/policies.py
  git commit -m "feat: pulse tab shows all open issues; convert endpoint promotes issues to renewal"
  ```

---

## Task 7: Templates — Nav Dot + Opp Row Badges

**Files:**
- Modify: `src/policydb/web/templates/base.html`
- Modify: `src/policydb/web/templates/policies/_opp_row.html`
- Modify: `src/policydb/web/templates/policies/_opp_client_row.html`

- [ ] **Step 1: Add nav dot to `base.html`**

  Find the Action Center nav link (around line 503):

  ```html
  <a href="/action-center" class="nav-link {% if active == 'action-center' %}bg-marsh-light{% endif %}"
    {% if _fu_badge.act_now or _fu_badge.nudge_due %}title="..."{% endif %}>
    Action Center{% if _fu_badge.act_now or _fu_badge.nudge_due %}<span class="bg-white/20 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-1">{{ _fu_badge.act_now + _fu_badge.nudge_due }}</span>{% endif %}
  </a>
  ```

  Change the closing part so the dot appears after the follow-up badge (or directly after "Action Center" if no follow-up badge):

  ```html
  <a href="/action-center" class="nav-link {% if active == 'action-center' %}bg-marsh-light{% endif %}"
    {% if _fu_badge.act_now or _fu_badge.nudge_due %}title="{{ _fu_badge.act_now }} action{{ 's' if _fu_badge.act_now != 1 else '' }} &middot; {{ _fu_badge.nudge_due }} nudge{{ 's' if _fu_badge.nudge_due != 1 else '' }}"{% endif %}>
    Action Center{% if _fu_badge.act_now or _fu_badge.nudge_due %}<span class="bg-white/20 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-1">{{ _fu_badge.act_now + _fu_badge.nudge_due }}</span>{% endif %}<span hx-get="/api/nav/issues-dot" hx-trigger="load" hx-swap="outerHTML"></span>
  </a>
  ```

- [ ] **Step 2: Add issue badge to `_opp_row.html`**

  Open `_opp_row.html`. Find where the opportunity status badge or follow-up date is displayed — typically near the bottom of the row. Add the issue badge in the same `<td>` as `opportunity_status` or as its own column. The safest placement is after the follow-up date cell.

  Find the last `<td>` before the Edit/Log buttons and add:

  ```html
  {% if o.issue_uid %}
  <div class="mt-1">
    {% with linked_issue_uid=o.issue_uid, linked_issue_subject=o.issue_subject|default('Issue'), linked_issue_severity=o.issue_severity %}
      {% include "_issue_badge.html" %}
    {% endwith %}
  </div>
  {% endif %}
  ```

  > Place this in the same `<td>` as the follow-up date (`o.follow_up_date`), directly after the follow-up display span.

- [ ] **Step 3: Add issue badge to `_opp_client_row.html`**

  Same pattern. Find the column showing `opportunity_status` and add the badge below it:

  ```html
  {% if o.issue_uid %}
  <div class="mt-1">
    {% with linked_issue_uid=o.issue_uid, linked_issue_subject=o.issue_subject|default('Issue'), linked_issue_severity=o.issue_severity %}
      {% include "_issue_badge.html" %}
    {% endwith %}
  </div>
  {% endif %}
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add src/policydb/web/templates/base.html \
          src/policydb/web/templates/policies/_opp_row.html \
          src/policydb/web/templates/policies/_opp_client_row.html
  git commit -m "feat: add nav issues dot and issue badges on opportunity rows"
  ```

---

## Task 8: Dashboard Widget Template

**Files:**
- Create: `src/policydb/web/templates/issues/_dashboard_issues_widget.html`
- Modify: `src/policydb/web/templates/dashboard.html`

- [ ] **Step 1: Create the widget partial**

  Create `src/policydb/web/templates/issues/_dashboard_issues_widget.html`:

  ```html
  {# Dashboard issues alert block — shown above Follow-Ups when issues exist #}
  {% if issues_widget and issues_widget.total > 0 %}
  <div class="card border-l-4 {% if issues_widget.sla_count > 0 %}border-red-400 bg-red-50/30{% else %}border-amber-400 bg-amber-50/30{% endif %}">
    <div class="px-4 py-2.5 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-xs font-semibold {% if issues_widget.sla_count > 0 %}text-red-700{% else %}text-amber-700{% endif %}">
          Issues — {{ issues_widget.total }} open
          {% if issues_widget.sla_count > 0 %}
          · <span class="font-bold">{{ issues_widget.sla_count }} past SLA</span>
          {% endif %}
        </span>
        {% for issue in issues_widget.top_issues %}
        <span class="inline-flex items-center gap-1 text-[10px] text-gray-600">
          <span class="w-1.5 h-1.5 rounded-full flex-shrink-0
            {% if issue.issue_severity == 'Critical' %}bg-red-500
            {% elif issue.issue_severity == 'High' %}bg-amber-500
            {% elif issue.issue_severity == 'Normal' %}bg-blue-500
            {% else %}bg-gray-400{% endif %}"></span>
          <a href="/issues/{{ issue.issue_uid }}" class="hover:text-marsh hover:underline">
            {{ issue.subject[:35] }}{% if issue.subject|length > 35 %}…{% endif %}
          </a>
          <span class="text-gray-400">· {{ issue.client_name }} · {{ issue.days_open }}d</span>
        </span>
        {% endfor %}
      </div>
      <div class="flex items-center gap-3 flex-shrink-0">
        <button onclick="openIssueCreateSlideover({})"
                class="text-[10px] font-semibold text-marsh hover:underline no-print">+ New Issue</button>
        <a href="/action-center?tab=issues" class="text-[10px] text-gray-400 hover:text-marsh">View all →</a>
      </div>
    </div>
  </div>
  {% endif %}
  ```

- [ ] **Step 2: Insert the widget into `dashboard.html`**

  Find the comment `<!-- Follow-ups -->` (around line 202 in `dashboard.html`). Insert the widget partial immediately before that comment:

  ```html
  {% include "issues/_dashboard_issues_widget.html" %}

  <!-- Follow-ups -->
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/policydb/web/templates/issues/_dashboard_issues_widget.html \
          src/policydb/web/templates/dashboard.html
  git commit -m "feat: add issues alert widget to dashboard above follow-ups"
  ```

---

## Task 9: Policy Pulse Template — Unified Issues Section

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_pulse.html`

- [ ] **Step 1: Replace the renewal issue banner block**

  Find lines 55–72 in `_tab_pulse.html`:

  ```html
  {# ══════════════════════════════════════════════════════════
     Renewal Issue Banner (if active)
     ══════════════════════════════════════════════════════════ #}
  {% if renewal_issue %}
  {% set ri_sev = renewal_issue.issue_severity or 'Normal' %}
  <a href="/issues/{{ renewal_issue.issue_uid }}" class="card px-3 py-2 flex items-center gap-2 border-l-4
    {% if ri_sev == 'Critical' %}border-red-400 bg-red-50/40
    {% elif ri_sev == 'High' %}border-amber-400 bg-amber-50/40
    {% elif ri_sev == 'Normal' %}border-blue-400 bg-blue-50/40
    {% else %}border-gray-300 bg-gray-50/40{% endif %}
    hover:shadow-sm transition-shadow group">
    <span class="inline-flex items-center text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-1.5 py-0.5">Renewal Issue</span>
    <span class="w-2 h-2 rounded-full flex-shrink-0
      {% if ri_sev == 'Critical' %}bg-red-500
      {% elif ri_sev == 'High' %}bg-amber-500
      {% elif ri_sev == 'Normal' %}bg-blue-500
      {% else %}bg-gray-400{% endif %}"></span>
    <span class="text-xs font-medium text-gray-700">{{ ri_sev }}</span>
    <span class="text-xs text-gray-400">&middot; {{ renewal_issue.issue_status }}</span>
    <span class="text-xs text-gray-400">&middot; {{ (renewal_issue.days_open or 0)|int }}d open</span>
    {% if renewal_issue.activity_count %}
    <span class="text-xs text-gray-400">&middot; {{ renewal_issue.activity_count }} act</span>
    {% endif %}
    <span class="ml-auto text-[10px] text-gray-400 group-hover:text-marsh">View &rarr;</span>
  </a>
  {% endif %}
  ```

  Replace it with:

  ```html
  {# ══════════════════════════════════════════════════════════
     Issues Section (all open issues — renewal + manual)
     ══════════════════════════════════════════════════════════ #}
  {% if open_issues %}
  <div class="card p-3">
    <div class="flex items-center justify-between mb-2">
      <span class="text-xs font-semibold text-gray-700">Issues ({{ open_issues|length }} open)</span>
      <button type="button"
              onclick="openIssueCreateSlideover({client_id: {{ policy.client_id }}, policy_id: {{ policy.id }}})"
              class="text-[10px] text-marsh hover:underline font-semibold no-print">+ New Issue</button>
    </div>
    {% for issue in open_issues %}
    {% set isev = issue.issue_severity or 'Normal' %}
    <a href="/issues/{{ issue.issue_uid }}"
       class="flex items-center gap-2 py-1 hover:bg-gray-50 rounded transition-colors group -mx-1 px-1">
      <span class="w-2 h-2 rounded-full flex-shrink-0
        {% if isev == 'Critical' %}bg-red-500
        {% elif isev == 'High' %}bg-amber-500
        {% elif isev == 'Normal' %}bg-blue-500
        {% else %}bg-gray-400{% endif %}"></span>
      {% if issue.is_renewal_issue %}
      <span class="inline-flex items-center text-[9px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-1.5 py-0.5 flex-shrink-0">Renewal</span>
      {% endif %}
      <span class="text-xs text-gray-700 truncate flex-1">{{ issue.subject or (isev + ' issue') }}</span>
      <span class="text-[10px] text-gray-400 flex-shrink-0">{{ isev }}</span>
      <span class="text-[10px] text-gray-400 flex-shrink-0">&middot; {{ (issue.days_open or 0)|int }}d</span>
      <span class="text-[10px] text-gray-400 ml-auto group-hover:text-marsh flex-shrink-0">&rarr;</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/policydb/web/templates/policies/_tab_pulse.html
  git commit -m "feat: replace renewal banner with unified all-issues section on policy pulse tab"
  ```

---

## Task 10: Issue Detail Page — Opp Context Block + Unlink Button

**Files:**
- Modify: `src/policydb/web/templates/issues/detail.html`

- [ ] **Step 1: Add the opportunity context block**

  Find lines 257–297 in `detail.html` — the renewal milestones section:

  ```html
  {# Renewal issue: timeline milestones summary #}
  {% if issue.is_renewal_issue and timeline_milestones %}
  ...
  {% elif issue.is_renewal_issue %}
  ...
  {% endif %}
  ```

  Replace it with (insert the opp block before the existing renewal block):

  ```html
  {# Opportunity context block (when issue is linked to an opportunity) #}
  {% if issue.is_opportunity %}
  <div class="card p-4 border-l-4 border-violet-400 bg-violet-50/30">
    <div class="flex items-center gap-2 mb-3">
      <span class="text-[10px] text-violet-800 uppercase tracking-wide font-semibold">Opportunity — Pipeline Context</span>
      <span class="inline-flex items-center text-[9px] font-medium bg-violet-50 text-violet-700 border border-violet-200 rounded-full px-1.5 py-0.5">Opportunity</span>
    </div>
    <div class="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
      <div>
        <div class="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Status</div>
        <div class="font-medium text-gray-800">{{ issue.opportunity_status or '—' }}</div>
      </div>
      <div>
        <div class="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Target Eff. Date</div>
        <div class="font-medium text-gray-800">{{ issue.target_effective_date or '—' }}</div>
      </div>
      <div>
        <div class="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Coverage</div>
        <div class="font-medium text-gray-800">{{ issue.policy_type or '—' }}</div>
      </div>
      <div>
        <div class="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Est. Premium</div>
        <div class="font-medium text-gray-800">
          {% if issue.policy_premium %}{{ issue.policy_premium | currency_short }}{% else %}—{% endif %}
        </div>
      </div>
    </div>
    {% if issue.policy_uid %}
    <a href="/policies/{{ issue.policy_uid }}/edit" class="block text-[10px] text-violet-600 hover:underline mt-3">
      View Opportunity {{ issue.policy_uid }} &rarr;
    </a>
    {% endif %}
  </div>
  {% elif issue.is_renewal_issue and timeline_milestones %}
  {# ... existing renewal milestones block unchanged ... #}
  ```

  > **Important:** Only replace the opening `{% if issue.is_renewal_issue and timeline_milestones %}` line with the new `{% if issue.is_opportunity %}` + `{% elif issue.is_renewal_issue and timeline_milestones %}` structure. The existing renewal content from lines 259–297 stays intact as the `elif` branch.

  The full structure after the edit — keep lines 259–290 and 291–297 from the current file exactly as they are, only changing the opening `{% if %}` condition from `{% if issue.is_renewal_issue and timeline_milestones %}` to `{% elif issue.is_renewal_issue and timeline_milestones %}`:

  ```html
  {% if issue.is_opportunity %}
  <div class="card p-4 border-l-4 border-violet-400 bg-violet-50/30">
    ...  {# the full purple block from Step 1 above #}
  </div>
  {% elif issue.is_renewal_issue and timeline_milestones %}
  <div class="card p-4 border-l-4 border-emerald-400">
    {# KEEP everything here exactly as it was — lines 259-290 of detail.html #}
    {# This is the green timeline milestones block with the {% for ms in timeline_milestones %} loop #}
  </div>
  {% elif issue.is_renewal_issue %}
  <div class="card p-4 border-l-4 border-emerald-400">
    {# KEEP everything here exactly as it was — lines 291-297 of detail.html #}
    {# This is the fallback green block: "Severity auto-managed by timeline health..." #}
  </div>
  {% endif %}
  ```

- [ ] **Step 2: Add the purple "Opportunity" badge to the metadata row**

  Find the metadata row in `detail.html` (around lines 21–46) — the row showing `client_name`, `policy_uid`, days open, SLA, etc. This is a `<div class="flex ... gap-...">` containing several `<span>` elements.

  Find where `issue.policy_uid` is rendered as a link (there will be a `<a href="/policies/{{ issue.policy_uid }}...">` or plain `<span>` showing it). Add the opportunity badge immediately after that element:

  ```html
  {% if issue.is_opportunity %}
  <span class="inline-flex items-center text-[9px] font-medium bg-violet-50 text-violet-700 border border-violet-200 rounded-full px-1.5 py-0.5 ml-1">Opportunity</span>
  {% endif %}
  ```

- [ ] **Step 3: Add the unlink button to each activity row**

  Find the activity timeline loop (around lines 118–155). Each activity row has a `<div>` wrapping date, type badge, subject. Find the outer `<div>` for each activity item and add an × button aligned to the far right:

  Find this structure (approximately lines 118-122):
  ```html
  {% for act in activities %}
  <div>
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400 w-12 flex-shrink-0">...</span>
  ```

  Make two targeted changes to the activity loop — add an `id` to the outer `<div>` and wrap existing content with a flex container that holds the × button:

  **Change 1:** Add `id="issue-activity-{{ act.id }}"` to the outer `<div>` of each activity item (the `<div>` that wraps the date/type/subject row and the details paragraph). Currently this div has no id:
  ```html
  {# Before: #}
  <div>
    <div class="flex items-center gap-2">

  {# After: #}
  <div id="issue-activity-{{ act.id }}">
    <div class="flex items-start gap-2">
  ```

  **Change 2:** Wrap the entire inner content in a `<div class="flex-1 min-w-0">` and add the × button as a sibling. The outer `<div class="flex items-start gap-2">` should contain two children: the content div and the button:

  ```html
  <div id="issue-activity-{{ act.id }}">
    <div class="flex items-start gap-2">
      <div class="flex-1 min-w-0">
        {# ← ALL existing inner content goes here (date span, type badge, subject, details p, disposition/followup/hours/contact row) — unchanged #}
      </div>
      <button hx-delete="/issues/{{ issue.id }}/unlink-activity/{{ act.id }}"
              hx-target="#issue-activity-{{ act.id }}"
              hx-swap="outerHTML"
              hx-confirm="Remove this activity from the issue?"
              class="text-gray-300 hover:text-red-400 text-sm leading-none flex-shrink-0 mt-0.5 no-print"
              title="Unlink from issue">×</button>
    </div>
  </div>
  ```

  The only structural change is: `<div>` → `<div id="...">`, `flex items-center` → `flex items-start`, add the wrapping `<div class="flex-1 min-w-0">` around all existing inner HTML, and add the `<button>` after the closing `</div>` of the inner wrapper. All the inner HTML (the `<span>` for date, activity type badge, subject, details paragraph, disposition/followup/hours row) stays completely unchanged.

  > `HTMLResponse("")` from the DELETE endpoint removes the element entirely via `hx-swap="outerHTML"` + empty response.

- [ ] **Step 4: Commit**

  ```bash
  git add src/policydb/web/templates/issues/detail.html
  git commit -m "feat: opp context block and unlink button on issue detail page"
  ```

---

## Task 11: Client Sidebar — Issues Subsection

**Files:**
- Modify: `src/policydb/web/templates/clients/_sticky_sidebar.html`

- [ ] **Step 1: Add Issues subsection after the Key Dates panel**

  Find line 96 in `_sticky_sidebar.html` — the closing `</div>` of the Key Dates panel:

  ```html
    </div>
  </div>   {# closes Key Dates py-3 border-b #}

  {# ── Website ── #}
  ```

  Insert the Issues subsection between the Key Dates closing `</div>` and the Website section:

  ```html
    </div>
  </div>

  {# ── Issues ── #}
  {% if sidebar_issues %}
  <div class="py-3 border-b border-gray-100">
    <div class="flex items-center justify-between mb-1.5">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Issues</p>
      <a href="#" onclick="event.preventDefault();document.querySelector('[data-tab=issues]')?.click();"
         class="text-[10px] text-marsh hover:underline no-print">All →</a>
    </div>
    {% for issue in sidebar_issues %}
    <a href="/issues/{{ issue.issue_uid }}"
       class="flex items-center gap-1.5 py-0.5 text-xs text-gray-600 hover:text-marsh group">
      <span class="w-1.5 h-1.5 rounded-full flex-shrink-0
        {% if issue.issue_severity == 'Critical' %}bg-red-500
        {% elif issue.issue_severity == 'High' %}bg-amber-500
        {% elif issue.issue_severity == 'Normal' %}bg-blue-500
        {% else %}bg-gray-400{% endif %}"></span>
      <span class="truncate group-hover:underline">{{ issue.subject[:30] }}{% if issue.subject|length > 30 %}…{% endif %}</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}

  {# ── Website ── #}
  ```

  > The `data-tab=issues` click target assumes the client detail page's Issues tab has `data-tab="issues"` on the tab button. If the tab uses a different selector, update accordingly or just link directly to `?tab=issues`.

- [ ] **Step 2: Commit**

  ```bash
  git add src/policydb/web/templates/clients/_sticky_sidebar.html
  git commit -m "feat: add issues subsection to client sticky sidebar Key Dates panel"
  ```

---

## Task 12: QA Verification

Start the server and verify each feature:

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; pdb serve
```

- [ ] **#1 Dashboard widget:** Create a test issue via Action Center → reload dashboard → alert block appears above Follow-Ups. Resolve the issue → block disappears.

- [ ] **#2 Nav dot:** With any open issue → red dot visible on Action Center nav link. Resolve all issues → dot gone (reload page to trigger fresh HTMX load).

- [ ] **#3 Opp row badges:** Manually create an issue linked to an opportunity (via the opportunity's Activity tab → + Issue). Reload dashboard → opportunity row shows the issue badge. Same on client detail opportunities section.

- [ ] **#4 Opp-aware issue detail:** Navigate to the issue created in #3 → purple "Opportunity — Pipeline Context" block shows (not the green renewal milestones). Purple "Opportunity" badge appears in the metadata row.

- [ ] **#5 Policy pulse unified:** Open any policy with a renewal issue → Pulse tab shows "Issues (1 open)" section with "Renewal" tag. Create a second manual issue for the same policy → Pulse tab shows "Issues (2 open)" with both rows.

- [ ] **#7 Unlink activity:** On any issue detail page with linked activities → × button visible on each activity row. Click × → confirm prompt → activity row disappears from timeline.

- [ ] **#8 Client sidebar:** Open any client that has open issues → Key Dates panel shows "Issues" subsection with severity dots and truncated subjects. Client with no open issues → subsection hidden.

- [ ] **#10 Conversion promote:** Create an opportunity, link a manual issue to it. Convert the opportunity to a policy. Check the policy's Pulse tab → the issue now appears with a "Renewal" tag. Verify in DB: `SELECT is_renewal_issue, renewal_term_key FROM activity_log WHERE issue_uid = '...'` shows `is_renewal_issue=1`.

- [ ] **Final commit**

  ```bash
  git add -A
  git commit -m "feat: complete issue management UX improvements (8 features)"
  ```
