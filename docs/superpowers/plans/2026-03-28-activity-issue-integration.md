# Activity–Issue Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the activity system to make issues first-class citizens — client kanban board, issue linking on forms, escalation workflow, and weekly plan escalation review.

**Architecture:** Activities tab gets a client-grouped kanban board view alongside the existing table view. Issue creation uses a unified slideover triggered from escalate buttons, suggestion cards, and the Issues tab. The timeline engine and follow-up staleness feed escalation suggestions into a Weekly Plan review section. A new `escalation_dismissals` table tracks dismissed suggestions with automatic reset logic.

**Tech Stack:** FastAPI routes, Jinja2 templates, HTMX partials, SQLite, existing slideover/combobox patterns.

**Spec:** `docs/superpowers/specs/2026-03-28-activity-issue-integration-design.md`

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `src/policydb/migrations/105_escalation_dismissals.sql` | New dismissals table |
| `src/policydb/web/templates/action_center/_activities_board.html` | Client kanban board view |
| `src/policydb/web/templates/_issue_create_slideover.html` | Unified issue creation slideover |
| `src/policydb/web/templates/_issue_badge.html` | Reusable issue badge pill |
| `src/policydb/web/templates/issues/_link_activities_slideover.html` | Link activities slideover |
| `src/policydb/web/templates/issues/_issue_widget.html` | Issue widget partial for Quick Log form |
| `src/policydb/web/templates/followups/_escalation_review.html` | Weekly Plan escalation review banner |

### Modified Files

| File | Changes |
|------|---------|
| `src/policydb/db.py` | Wire migration 105; `generate_issue_uid()` already done |
| `src/policydb/queries.py` | Add `get_client_activity_board()`, `get_escalation_suggestions()` |
| `src/policydb/web/routes/action_center.py` | Restructure `_activities_ctx()` for kanban; add board data |
| `src/policydb/web/routes/activities.py` | Accept `issue_id` on log; remove auto-threading; add escalation review to plan_week |
| `src/policydb/web/routes/issues.py` | New endpoints: `for-client`, `link-activities`, `linkable-activities`; enhance `create` for source linking |
| `src/policydb/web/routes/policies.py` | Accept `issue_id` on `row/log` |
| `src/policydb/web/templates/action_center/_activities.html` | Add Board/Table toggle, issue badges, escalate buttons |
| `src/policydb/web/templates/action_center/_issues.html` | Replace inline form with slideover trigger |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Add escalate button + issue badge to rows |
| `src/policydb/web/templates/policies/_policy_row_log.html` | Add issue combobox field |
| `src/policydb/web/templates/issues/detail.html` | Add "+ Link Activity" button |
| `src/policydb/web/templates/followups/plan.html` | Add escalation review section |

---

## Task 1: Migration + Dismissals Table

**Files:**
- Create: `src/policydb/migrations/105_escalation_dismissals.sql`
- Modify: `src/policydb/db.py` (migration wiring, around line 1458)

- [ ] **Step 1: Create migration file**

```sql
-- 105_escalation_dismissals.sql
CREATE TABLE IF NOT EXISTS escalation_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(policy_id, trigger_type)
);
```

- [ ] **Step 2: Wire migration into init_db()**

In `src/policydb/db.py`, add migration 105 to `_KNOWN_MIGRATIONS` set (line ~365), then add the version check block after migration 104's block (after line ~1466):

```python
if 105 not in done:
    _run_migration(conn, 105, "105_escalation_dismissals.sql")
```

- [ ] **Step 3: Verify migration runs**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb serve &
sleep 2
curl -s http://127.0.0.1:8000/ | head -5
lsof -ti:8000 | xargs kill -9 2>/dev/null
```

Check server starts without migration errors.

- [ ] **Step 4: Verify table exists**

```bash
sqlite3 ~/.policydb/policydb.sqlite ".schema escalation_dismissals"
```

Expected output shows the CREATE TABLE statement.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/migrations/105_escalation_dismissals.sql src/policydb/db.py
git commit -m "feat: add escalation_dismissals table (migration 105)"
```

---

## Task 2: Query Functions

**Files:**
- Modify: `src/policydb/queries.py` (add functions at end of file)

- [ ] **Step 1: Add `get_client_activity_board()` function**

Append to `src/policydb/queries.py`:

```python
def get_client_activity_board(
    conn, days: int = 7, activity_type: str = "", q: str = "", client_id: int = 0
) -> list[dict]:
    """Return activities grouped by client with issue nesting for kanban board.

    Returns list of client dicts:
    {client_id, client_name, cn_number, activity_count, total_hours,
     issues: [{issue row + activities: [activity rows]}],
     untracked: [activity rows],
     has_issues: bool}

    Sorted: clients with open issues first, then by most recent activity date desc.
    """
    import policydb.config as cfg

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    params: list = [cutoff]
    where_parts = ["a.activity_date >= ?"]

    if activity_type:
        where_parts.append("a.activity_type = ?")
        params.append(activity_type)
    if q:
        where_parts.append("a.subject LIKE ?")
        params.append(f"%{q}%")
    if client_id:
        where_parts.append("a.client_id = ?")
        params.append(client_id)

    where_clause = " AND ".join(where_parts)

    # Fetch all activities in window (exclude issue header rows)
    rows = conn.execute(f"""
        SELECT a.id, a.activity_date, a.client_id, a.policy_id, a.activity_type,
               a.subject, a.details, a.duration_hours, a.disposition,
               a.follow_up_date, a.issue_id, a.item_kind,
               c.name AS client_name, c.cn_number,
               p.policy_uid, p.policy_type, p.project_id,
               -- Issue info for linked activities
               iss.issue_uid AS linked_issue_uid,
               iss.subject AS linked_issue_subject,
               iss.issue_severity AS linked_issue_severity
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        LEFT JOIN activity_log iss ON a.issue_id = iss.id AND iss.item_kind = 'issue'
        WHERE {where_clause}
          AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
        ORDER BY a.activity_date DESC, a.id DESC
    """, params).fetchall()
    activities = [dict(r) for r in rows]

    # Fetch open issues for clients that have activities in the window
    client_ids_in_window = list({a["client_id"] for a in activities})
    if not client_ids_in_window:
        return []

    placeholders = ",".join("?" * len(client_ids_in_window))
    issues_rows = conn.execute(f"""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_status,
               a.issue_sla_days, a.client_id, a.policy_id, a.activity_date,
               p.policy_uid, p.policy_type,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.item_kind = 'issue'
          AND a.issue_id IS NULL
          AND (a.issue_status IS NULL OR a.issue_status NOT IN ('Resolved', 'Closed'))
          AND a.client_id IN ({placeholders})
        ORDER BY
          CASE a.issue_severity
            WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
            WHEN 'Normal' THEN 2 ELSE 3
          END,
          a.activity_date ASC
    """, client_ids_in_window).fetchall()
    issues = [dict(r) for r in issues_rows]

    # Build client columns
    from collections import defaultdict
    client_map: dict[int, dict] = {}

    # Initialize clients from activities
    for a in activities:
        cid = a["client_id"]
        if cid not in client_map:
            client_map[cid] = {
                "client_id": cid,
                "client_name": a["client_name"],
                "cn_number": a["cn_number"],
                "issues": {},       # issue_id -> {issue_row, activities: []}
                "untracked": [],
                "activity_count": 0,
                "total_hours": 0.0,
                "has_issues": False,
                "latest_date": a["activity_date"],
            }
        col = client_map[cid]
        col["activity_count"] += 1
        col["total_hours"] += a["duration_hours"] or 0

    # Attach issues to their client columns
    for iss in issues:
        cid = iss["client_id"]
        if cid not in client_map:
            # Client has issues but no activities in window — still show column
            client_map[cid] = {
                "client_id": cid,
                "client_name": "",  # Will need to look up
                "cn_number": "",
                "issues": {},
                "untracked": [],
                "activity_count": 0,
                "total_hours": 0.0,
                "has_issues": True,
                "latest_date": "",
            }
        client_map[cid]["has_issues"] = True
        client_map[cid]["issues"][iss["id"]] = {**iss, "activities": []}

    # Distribute activities into issue buckets or untracked
    for a in activities:
        cid = a["client_id"]
        col = client_map[cid]
        if a["issue_id"] and a["issue_id"] in col["issues"]:
            col["issues"][a["issue_id"]]["activities"].append(a)
        else:
            col["untracked"].append(a)

    # Convert issues dict to sorted list
    for col in client_map.values():
        col["issues"] = sorted(
            col["issues"].values(),
            key=lambda i: (
                {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}.get(i.get("issue_severity", "Normal"), 2),
                i.get("activity_date", ""),
            ),
        )

    # Sort columns: clients with issues first, then by latest activity date desc
    result = sorted(
        client_map.values(),
        key=lambda c: (0 if c["has_issues"] else 1, c.get("latest_date", "") or ""),
        reverse=False,  # issues first (0 < 1), but we want latest date first within group
    )
    # Re-sort: issues-first group by latest desc, then no-issues group by latest desc
    with_issues = sorted(
        [c for c in result if c["has_issues"]],
        key=lambda c: c.get("latest_date", "") or "",
        reverse=True,
    )
    without_issues = sorted(
        [c for c in result if not c["has_issues"]],
        key=lambda c: c.get("latest_date", "") or "",
        reverse=True,
    )
    return with_issues + without_issues
```

- [ ] **Step 2: Add `get_escalation_suggestions()` function**

Append to `src/policydb/queries.py`:

```python
def get_escalation_suggestions(conn) -> list[dict]:
    """Return escalation suggestions for the Weekly Plan review.

    Aggregates four trigger types, filters out dismissed (with reset logic)
    and already-tracked (open issue exists). Sorted by severity then age.

    Trigger types:
    - stale_followups: follow-ups overdue > stale_threshold_days, grouped by client
    - timeline_drift: policy_timeline milestones at_risk or critical
    - nudge_escalation: 3+ waiting_external nudges on same policy in 90d
    - critical_renewal: renewal <=60d + Not Started + stale
    """
    import policydb.config as cfg

    stale_days = cfg.get("stale_threshold_days", 14)
    today = date.today().isoformat()
    suggestions: list[dict] = []

    # Helper: check if policy already has an open issue
    open_issue_policy_ids = {
        r["policy_id"]
        for r in conn.execute("""
            SELECT DISTINCT policy_id FROM activity_log
            WHERE item_kind = 'issue' AND issue_id IS NULL
              AND (issue_status IS NULL OR issue_status NOT IN ('Resolved', 'Closed'))
              AND policy_id IS NOT NULL
        """).fetchall()
    }

    # Helper: check dismissals (with reset logic)
    dismissals = {}
    for r in conn.execute("SELECT policy_id, trigger_type, dismissed_at FROM escalation_dismissals").fetchall():
        dismissals[(r["policy_id"], r["trigger_type"])] = r["dismissed_at"]

    def is_dismissed(policy_id: int, trigger_type: str) -> bool:
        key = (policy_id, trigger_type)
        if key not in dismissals:
            return False
        dismissed_at = dismissals[key]
        # Check if data changed since dismissal
        latest = conn.execute("""
            SELECT MAX(COALESCE(a.activity_date, a.follow_up_date)) AS latest
            FROM activity_log a WHERE a.policy_id = ?
        """, (policy_id,)).fetchone()
        if latest and latest["latest"] and latest["latest"] > dismissed_at:
            return False  # Data changed — reset dismissal
        return True

    # --- Trigger 1: Stale follow-ups (grouped by client) ---
    stale_cutoff = (date.today() - timedelta(days=stale_days)).isoformat()
    stale_rows = conn.execute("""
        SELECT a.client_id, c.name AS client_name, a.policy_id,
               p.policy_uid, p.policy_type, a.subject, a.follow_up_date,
               CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.follow_up_date IS NOT NULL
          AND a.follow_up_date < ?
          AND a.follow_up_date <= ?
          AND (a.follow_up_done IS NULL OR a.follow_up_done = 0)
          AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
          AND (a.disposition IS NULL OR a.disposition NOT IN ('Done', 'Closed'))
        ORDER BY a.client_id, a.follow_up_date ASC
    """, (stale_cutoff, today)).fetchall()

    # Group by client
    from collections import defaultdict
    stale_by_client: dict[int, list] = defaultdict(list)
    for r in stale_rows:
        if r["policy_id"] and r["policy_id"] in open_issue_policy_ids:
            continue  # Already has open issue
        stale_by_client[r["client_id"]].append(dict(r))

    for client_id, items in stale_by_client.items():
        if len(items) < 2:
            continue  # Only suggest when multiple stale items
        # Check dismissal for first policy (use client-level check)
        any_dismissed = all(
            is_dismissed(it["policy_id"], "stale_followups") for it in items if it["policy_id"]
        )
        if any_dismissed:
            continue
        policy_summaries = ", ".join(
            f"{it['policy_type'] or 'policy'}" for it in items[:3]
        )
        suggestions.append({
            "trigger_type": "stale_followups",
            "severity_preset": "High",
            "icon": "stale",
            "client_id": client_id,
            "client_name": items[0]["client_name"],
            "policy_id": items[0]["policy_id"],
            "title": f"{items[0]['client_name']} — {len(items)} stale follow-ups ({stale_days}+ days overdue)",
            "detail": f"{policy_summaries} · No activity in {items[0].get('days_overdue', stale_days)}+ days",
            "source_activity_ids": [it.get("id") for it in items if it.get("id")],
        })

    # --- Trigger 2: Timeline drift ---
    drift_rows = conn.execute("""
        SELECT pt.policy_uid, pt.milestone_name, pt.health, pt.ideal_date, pt.projected_date,
               p.id AS policy_id, p.policy_type, p.client_id,
               c.name AS client_name,
               CAST(julianday(pt.projected_date) - julianday(pt.ideal_date) AS INTEGER) AS drift_days
        FROM policy_timeline pt
        JOIN policies p ON pt.policy_uid = p.policy_uid
        JOIN clients c ON p.client_id = c.id
        WHERE pt.health IN ('at_risk', 'critical')
          AND pt.completed_date IS NULL
          AND (pt.acknowledged IS NULL OR pt.acknowledged = 0)
        ORDER BY
          CASE pt.health WHEN 'critical' THEN 0 ELSE 1 END,
          pt.ideal_date ASC
    """).fetchall()

    for r in drift_rows:
        r = dict(r)
        if r["policy_id"] in open_issue_policy_ids:
            continue
        if is_dismissed(r["policy_id"], "timeline_drift"):
            continue
        sev = "Critical" if r["health"] == "critical" else "High"
        suggestions.append({
            "trigger_type": "timeline_drift",
            "severity_preset": sev,
            "icon": "drift",
            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "policy_id": r["policy_id"],
            "title": f"{r['client_name']} — {r['policy_type'] or r['policy_uid']} milestone {r['health']} ({r['milestone_name']})",
            "detail": f"{r['policy_uid']} · {r.get('drift_days', 0)} days drift · Due {r['ideal_date']}",
            "source_activity_ids": [],
        })

    # --- Trigger 3: Nudge escalation (3+ waiting_external) ---
    nudge_rows = conn.execute("""
        SELECT a.policy_id, p.policy_uid, p.policy_type, a.client_id,
               c.name AS client_name,
               COUNT(*) AS nudge_count,
               MIN(a.activity_date) AS first_nudge
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        JOIN policies p ON a.policy_id = p.id
        WHERE a.disposition IN (
            SELECT json_extract(value, '$.label')
            FROM json_each((SELECT json_group_array(json(value))
                FROM json_each(?)
                WHERE json_extract(value, '$.accountability') = 'waiting_external'))
        )
          AND a.activity_date >= date('now', '-90 days')
          AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
          AND a.policy_id IS NOT NULL
        GROUP BY a.policy_id
        HAVING COUNT(*) >= 3
    """, (cfg.get_json("follow_up_dispositions"),)).fetchall()

    for r in nudge_rows:
        r = dict(r)
        if r["policy_id"] in open_issue_policy_ids:
            continue
        if is_dismissed(r["policy_id"], "nudge_escalation"):
            continue
        suggestions.append({
            "trigger_type": "nudge_escalation",
            "severity_preset": "High",
            "icon": "nudge",
            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "policy_id": r["policy_id"],
            "title": f"{r['client_name']} — {r['nudge_count']} nudges on {r['policy_type'] or r['policy_uid']} (urgent tier)",
            "detail": f"{r['policy_uid']} · Waiting since {r['first_nudge']} · {r['nudge_count']} unanswered follow-ups",
            "source_activity_ids": [],
        })

    # --- Trigger 4: Critical renewal (from existing get_escalation_alerts) ---
    alerts = get_escalation_alerts(conn)
    for alert in alerts:
        if alert.get("tier") != "CRITICAL":
            continue
        pid = alert.get("policy_id")
        if pid and pid in open_issue_policy_ids:
            continue
        if pid and is_dismissed(pid, "critical_renewal"):
            continue
        suggestions.append({
            "trigger_type": "critical_renewal",
            "severity_preset": "Critical",
            "icon": "critical",
            "client_id": alert.get("client_id"),
            "client_name": alert.get("client_name", ""),
            "policy_id": pid,
            "title": f"{alert.get('client_name', '')} — Renewal in {alert.get('days_to_renewal', '?')}d, status \"{alert.get('renewal_status', 'Not Started')}\"",
            "detail": f"{alert.get('policy_uid', '')} · {alert.get('policy_type', '')} · No recent activity · CRITICAL escalation tier",
            "source_activity_ids": [],
        })

    # Sort: Critical first, then High, then by title
    severity_order = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}
    suggestions.sort(key=lambda s: (severity_order.get(s["severity_preset"], 2), s["title"]))
    return suggestions
```

**Note on `get_json()`:** The nudge query uses `cfg.get_json("follow_up_dispositions")` to pass dispositions as JSON to SQLite's `json_each()`. If `config.py` doesn't have a `get_json()` method, simplify the nudge query by fetching waiting_external disposition labels in Python and building the SQL IN clause with parameters instead:

```python
# Alternative if get_json doesn't exist:
dispositions = cfg.get("follow_up_dispositions", [])
waiting_labels = [d["label"] for d in dispositions if isinstance(d, dict) and d.get("accountability") == "waiting_external"]
if waiting_labels:
    placeholders = ",".join("?" * len(waiting_labels))
    # Use: WHERE a.disposition IN ({placeholders}) with waiting_labels as params
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: add kanban board and escalation suggestion queries"
```

---

## Task 3: Issue Badge Partial + Issue Creation Slideover

**Files:**
- Create: `src/policydb/web/templates/_issue_badge.html`
- Create: `src/policydb/web/templates/_issue_create_slideover.html`

- [ ] **Step 1: Create issue badge partial**

Create `src/policydb/web/templates/_issue_badge.html`:

```html
{# Reusable issue badge pill — expects: linked_issue_uid, linked_issue_subject, linked_issue_severity #}
{% if linked_issue_uid %}
<a href="/issues/{{ linked_issue_uid }}"
   class="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border no-print
     {% if linked_issue_severity == 'Critical' %}bg-red-50 text-red-700 border-red-200
     {% elif linked_issue_severity == 'High' %}bg-amber-50 text-amber-700 border-amber-200
     {% elif linked_issue_severity == 'Normal' %}bg-blue-50 text-blue-700 border-blue-200
     {% else %}bg-gray-100 text-gray-600 border-gray-200{% endif %}"
   title="{{ linked_issue_subject }}">
  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0
    {% if linked_issue_severity == 'Critical' %}bg-red-500
    {% elif linked_issue_severity == 'High' %}bg-amber-500
    {% elif linked_issue_severity == 'Normal' %}bg-blue-500
    {% else %}bg-gray-400{% endif %}"></span>
  {{ linked_issue_subject[:20] }}{% if linked_issue_subject|length > 20 %}…{% endif %}
</a>
{% endif %}
```

- [ ] **Step 2: Create issue creation slideover**

Create `src/policydb/web/templates/_issue_create_slideover.html`:

```html
{# ── Issue Creation Slideover ── #}
{# Triggered by: escalate buttons, suggestion cards, + New Issue button #}
{# JS function openIssueCreateSlideover(opts) populates fields from data attrs #}

{# Backdrop #}
<div id="issue-create-backdrop"
     class="fixed inset-0 bg-black/30 z-40 hidden"
     onclick="closeIssueCreateSlideover()"></div>

{# Panel #}
<div id="issue-create-panel"
     class="fixed top-0 right-0 bottom-0 w-[480px] max-sm:w-full bg-white shadow-xl z-50 flex flex-col hidden">

  {# Header #}
  <div class="flex items-center justify-between p-4 border-b shrink-0">
    <h2 class="text-base font-semibold text-gray-900">Create Issue</h2>
    <button onclick="closeIssueCreateSlideover()"
            class="text-gray-400 hover:text-gray-600">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
      </svg>
    </button>
  </div>

  {# Scrollable content #}
  <form id="issue-create-form" hx-post="/issues/create" hx-target="#ac-tab-content" hx-swap="innerHTML"
        class="flex-1 overflow-y-auto p-4 space-y-4">

    {# Context banner (hidden by default, shown when creating from activity/suggestion) #}
    <div id="issue-create-context" class="hidden bg-blue-50 border border-blue-200 rounded-lg p-3">
      <div class="text-[10px] text-gray-500 uppercase tracking-wide mb-1">
        <span id="issue-create-context-label">Creating from activity</span>
      </div>
      <div id="issue-create-context-subject" class="text-xs font-medium text-blue-800"></div>
      <div id="issue-create-context-detail" class="text-[11px] text-gray-500 mt-1"></div>
    </div>

    {# Title #}
    <div>
      <label class="text-xs font-semibold text-gray-700 block mb-1">Issue Title *</label>
      <input type="text" name="subject" required id="issue-create-subject"
             class="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-1 focus:ring-marsh"
             placeholder="Brief description of the issue">
      <p id="issue-create-subject-hint" class="text-[10px] text-gray-400 mt-1 hidden">Pre-filled from activity — edit as needed</p>
    </div>

    {# Client #}
    <div>
      <label class="text-xs font-semibold text-gray-700 block mb-1">Client *</label>
      {# Read-only display (when inherited) #}
      <div id="issue-create-client-display" class="hidden text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-marsh font-medium"></div>
      {# Editable select (when creating from scratch) #}
      <select name="client_id" id="issue-create-client-select" required
              class="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus:ring-1 focus:ring-marsh">
        <option value="">Select client…</option>
        {% for cl in all_clients %}
        <option value="{{ cl.id }}">{{ cl.name }}</option>
        {% endfor %}
      </select>
    </div>

    {# Policy (optional) #}
    <div id="issue-create-policy-section">
      <label class="text-xs font-semibold text-gray-700 block mb-1">Policy</label>
      <div id="issue-create-policy-display" class="hidden text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-gray-700"></div>
      <input type="hidden" name="policy_id" id="issue-create-policy-id" value="">
    </div>

    {# Severity pills #}
    <div>
      <label class="text-xs font-semibold text-gray-700 block mb-2">Severity *</label>
      <div class="grid grid-cols-4 gap-2" id="issue-severity-pills">
        {% for sev in issue_severities %}
        <label class="cursor-pointer text-center py-2 px-1 rounded-lg border transition-all
          {% if sev.color == 'red' %}border-red-200 hover:bg-red-50
          {% elif sev.color == 'amber' %}border-amber-200 hover:bg-amber-50
          {% elif sev.color == 'blue' %}border-blue-200 hover:bg-blue-50
          {% else %}border-gray-200 hover:bg-gray-50{% endif %}">
          <input type="radio" name="severity" value="{{ sev.label }}"
                 {% if sev.label == 'Normal' %}checked{% endif %}
                 class="sr-only peer">
          <div class="peer-checked:font-bold peer-checked:ring-2 peer-checked:ring-offset-1 rounded-lg px-1 py-1
            {% if sev.color == 'red' %}peer-checked:ring-red-400 peer-checked:bg-red-50
            {% elif sev.color == 'amber' %}peer-checked:ring-amber-400 peer-checked:bg-amber-50
            {% elif sev.color == 'blue' %}peer-checked:ring-blue-400 peer-checked:bg-blue-50
            {% else %}peer-checked:ring-gray-400{% endif %}">
            <div class="w-2 h-2 rounded-full mx-auto mb-1
              {% if sev.color == 'red' %}bg-red-500
              {% elif sev.color == 'amber' %}bg-amber-500
              {% elif sev.color == 'blue' %}bg-blue-500
              {% else %}bg-gray-400{% endif %}"></div>
            <div class="text-xs">{{ sev.label }}</div>
            <div class="text-[9px] text-gray-400">SLA {{ sev.sla_days }}d</div>
          </div>
        </label>
        {% endfor %}
      </div>
    </div>

    {# Details #}
    <div>
      <label class="text-xs font-semibold text-gray-700 block mb-1">Details</label>
      <textarea name="details" rows="3" id="issue-create-details"
                class="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-1 focus:ring-marsh resize-y"
                placeholder="What's the problem? What needs to happen?"></textarea>
    </div>

    {# Source activity linking (hidden fields) #}
    <input type="hidden" name="source_activity_id" id="issue-create-source-id" value="">
    <input type="hidden" name="source_activity_ids" id="issue-create-source-ids" value="">

    {# Auto-link confirmation (shown when source exists) #}
    <div id="issue-create-link-confirm" class="hidden flex items-center gap-2 p-3 bg-green-50 border border-green-200 rounded-lg">
      <span class="text-green-600 text-sm">&#10003;</span>
      <div class="flex-1">
        <div class="text-xs font-medium text-green-800" id="issue-create-link-label">Original activity will be linked</div>
        <div class="text-[10px] text-green-600" id="issue-create-link-detail"></div>
      </div>
    </div>
  </form>

  {# Sticky footer #}
  <div class="shrink-0 border-t p-4 flex items-center gap-3 bg-white">
    <button type="submit" form="issue-create-form"
            class="flex-1 text-sm bg-marsh text-white rounded-lg px-4 py-2.5 font-medium hover:bg-marsh-light transition-colors">
      Create Issue
    </button>
    <button type="button" onclick="closeIssueCreateSlideover()"
            class="text-sm text-gray-500 border border-gray-300 rounded-lg px-4 py-2.5 hover:bg-gray-50">
      Cancel
    </button>
  </div>
</div>

<script>
function openIssueCreateSlideover(opts) {
  opts = opts || {};
  var panel = document.getElementById('issue-create-panel');
  var backdrop = document.getElementById('issue-create-backdrop');

  // Reset form
  document.getElementById('issue-create-form').reset();
  document.getElementById('issue-create-context').classList.add('hidden');
  document.getElementById('issue-create-subject-hint').classList.add('hidden');
  document.getElementById('issue-create-link-confirm').classList.add('hidden');
  document.getElementById('issue-create-source-id').value = '';
  document.getElementById('issue-create-source-ids').value = '';
  document.getElementById('issue-create-policy-id').value = '';

  // Client field: read-only vs editable
  var clientDisplay = document.getElementById('issue-create-client-display');
  var clientSelect = document.getElementById('issue-create-client-select');
  var policyDisplay = document.getElementById('issue-create-policy-display');

  if (opts.client_id) {
    clientSelect.value = opts.client_id;
    if (opts.client_name) {
      clientDisplay.textContent = opts.client_name;
      clientDisplay.classList.remove('hidden');
      clientSelect.classList.add('hidden');
    }
  } else {
    clientDisplay.classList.add('hidden');
    clientSelect.classList.remove('hidden');
  }

  // Policy
  if (opts.policy_id) {
    document.getElementById('issue-create-policy-id').value = opts.policy_id;
    if (opts.policy_label) {
      policyDisplay.textContent = opts.policy_label;
      policyDisplay.classList.remove('hidden');
    }
  } else {
    policyDisplay.classList.add('hidden');
  }

  // Subject
  if (opts.subject) {
    document.getElementById('issue-create-subject').value = opts.subject;
    document.getElementById('issue-create-subject-hint').classList.remove('hidden');
  }

  // Severity preset
  if (opts.severity) {
    var radio = document.querySelector('input[name="severity"][value="' + opts.severity + '"]');
    if (radio) radio.checked = true;
  }

  // Context banner
  if (opts.context_label) {
    document.getElementById('issue-create-context-label').textContent = opts.context_label;
    document.getElementById('issue-create-context-subject').textContent = opts.context_subject || '';
    document.getElementById('issue-create-context-detail').textContent = opts.context_detail || '';
    document.getElementById('issue-create-context').classList.remove('hidden');
  }

  // Source activity linking
  if (opts.source_activity_id) {
    document.getElementById('issue-create-source-id').value = opts.source_activity_id;
    document.getElementById('issue-create-link-label').textContent = 'Original activity will be linked';
    document.getElementById('issue-create-link-detail').textContent = opts.link_detail || '';
    document.getElementById('issue-create-link-confirm').classList.remove('hidden');
  }
  if (opts.source_activity_ids) {
    document.getElementById('issue-create-source-ids').value = opts.source_activity_ids;
    var count = opts.source_activity_ids.split(',').length;
    document.getElementById('issue-create-link-label').textContent = count + ' activities will be linked';
    document.getElementById('issue-create-link-detail').textContent = opts.link_detail || '';
    document.getElementById('issue-create-link-confirm').classList.remove('hidden');
  }

  // Show panel
  panel.classList.remove('hidden');
  backdrop.classList.remove('hidden');
  document.getElementById('issue-create-subject').focus();
}

function closeIssueCreateSlideover() {
  document.getElementById('issue-create-panel').classList.add('hidden');
  document.getElementById('issue-create-backdrop').classList.add('hidden');
}
</script>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/_issue_badge.html src/policydb/web/templates/_issue_create_slideover.html
git commit -m "feat: add issue badge partial and creation slideover"
```

---

## Task 4: Issue Routes — New Endpoints

**Files:**
- Modify: `src/policydb/web/routes/issues.py` (add new endpoints, enhance create)

- [ ] **Step 1: Add `GET /issues/for-client/{client_id}` endpoint**

Add after the existing create route (~line 68) in `src/policydb/web/routes/issues.py`:

```python
@router.get("/issues/for-client/{client_id}", response_class=HTMLResponse)
def issues_for_client(
    request: Request,
    client_id: int,
    conn=Depends(get_db),
):
    """Return HTML partial of open issues for a client (for Quick Log widget)."""
    rows = conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_sla_days,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open
        FROM activity_log a
        WHERE a.item_kind = 'issue'
          AND a.issue_id IS NULL
          AND a.client_id = ?
          AND (a.issue_status IS NULL OR a.issue_status NOT IN ('Resolved', 'Closed'))
        ORDER BY
          CASE a.issue_severity
            WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
            WHEN 'Normal' THEN 2 ELSE 3
          END,
          a.activity_date ASC
    """, (client_id,)).fetchall()
    issues = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "issues/_issue_widget.html",
        {"request": request, "issues": issues},
    )
```

- [ ] **Step 2: Create issue widget partial**

Create `src/policydb/web/templates/issues/_issue_widget.html`:

```html
{# Issue widget for Quick Log form — loaded via HTMX when client selected #}
{% if issues %}
<div class="bg-white border border-gray-200 rounded-lg p-3 mb-3">
  <div class="flex items-center gap-2 mb-2">
    <span class="text-[10px] font-semibold text-gray-700 uppercase tracking-wide">Open Issues</span>
    <span class="text-[10px] text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">{{ issues|length }}</span>
  </div>
  {% for iss in issues %}
  <div class="flex items-center gap-2 px-2 py-1.5 rounded-md mb-1
    {% if iss.issue_severity == 'Critical' %}bg-red-50 border border-red-200
    {% elif iss.issue_severity == 'High' %}bg-amber-50 border border-amber-200
    {% elif iss.issue_severity == 'Normal' %}bg-blue-50 border border-blue-200
    {% else %}bg-gray-50 border border-gray-200{% endif %}">
    <span class="w-1.5 h-1.5 rounded-full flex-shrink-0
      {% if iss.issue_severity == 'Critical' %}bg-red-500
      {% elif iss.issue_severity == 'High' %}bg-amber-500
      {% elif iss.issue_severity == 'Normal' %}bg-blue-500
      {% else %}bg-gray-400{% endif %}"></span>
    <a href="/issues/{{ iss.issue_uid }}" class="text-xs font-medium text-gray-800 hover:text-marsh truncate flex-1">
      {{ iss.subject }}
    </a>
    <span class="text-[10px] {% if iss.days_open > (iss.issue_sla_days or 7) %}text-red-600 font-semibold{% else %}text-gray-400{% endif %}">
      {{ iss.days_open }}d
    </span>
    <button type="button"
      onclick="document.getElementById('issue-link-id').value='{{ iss.id }}'; this.closest('.bg-white').querySelectorAll('.issue-link-btn').forEach(b=>b.classList.remove('bg-marsh','text-white')); this.classList.add('bg-marsh','text-white'); document.getElementById('issue-linked-bar').classList.remove('hidden'); document.getElementById('issue-linked-name').textContent='{{ iss.subject|e }}';"
      class="issue-link-btn text-[10px] bg-marsh text-white px-2.5 py-1 rounded font-medium hover:bg-marsh-light transition-colors flex-shrink-0">
      Link
    </button>
  </div>
  {% endfor %}
  <div class="text-[10px] text-gray-400 mt-2 text-center">Click "Link" to tag this activity to an issue</div>
</div>
{# Hidden field for issue_id #}
<input type="hidden" name="issue_id" id="issue-link-id" value="">
{# Linked confirmation bar #}
<div id="issue-linked-bar" class="hidden flex items-center gap-2 p-2 bg-green-50 border border-green-200 rounded-md mb-3">
  <span class="text-green-600 text-xs">&#10003;</span>
  <span class="text-xs text-green-800 font-medium">Linked to: <span id="issue-linked-name"></span></span>
  <span class="text-[10px] text-green-600 ml-auto cursor-pointer"
    onclick="document.getElementById('issue-link-id').value=''; this.closest('#issue-linked-bar').classList.add('hidden');">unlink</span>
</div>
{% endif %}
```

- [ ] **Step 3: Add `GET /issues/{issue_id}/linkable-activities` endpoint**

Add to `src/policydb/web/routes/issues.py`:

```python
@router.get("/issues/{issue_id}/linkable-activities", response_class=HTMLResponse)
def linkable_activities(
    request: Request,
    issue_id: int,
    q: str = "",
    activity_type: str = "",
    days: int = 30,
    conn=Depends(get_db),
):
    """Return HTML partial of unlinked activities for linking to an issue."""
    issue = conn.execute(
        "SELECT client_id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (issue_id,),
    ).fetchone()
    if not issue:
        return HTMLResponse("<p class='text-sm text-gray-400'>Issue not found</p>")

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    params: list = [issue["client_id"], cutoff]
    where_extra = ""
    if q:
        where_extra += " AND a.subject LIKE ?"
        params.append(f"%{q}%")
    if activity_type:
        where_extra += " AND a.activity_type = ?"
        params.append(activity_type)

    rows = conn.execute(f"""
        SELECT a.id, a.activity_date, a.activity_type, a.subject,
               a.duration_hours, a.policy_id,
               p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.client_id = ?
          AND a.activity_date >= ?
          AND a.issue_id IS NULL
          AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
          {where_extra}
        ORDER BY a.activity_date DESC, a.id DESC
    """, params).fetchall()

    import policydb.config as cfg
    return templates.TemplateResponse(
        "issues/_linkable_list.html",
        {"request": request, "activities": [dict(r) for r in rows], "activity_types": cfg.get("activity_types", [])},
    )
```

Create `src/policydb/web/templates/issues/_linkable_list.html`:

```html
{# Linkable activities list for link slideover — HTMX partial #}
{% for a in activities %}
<div class="flex items-start gap-2 p-2 rounded-md cursor-pointer hover:bg-blue-50 transition-colors linkable-row"
     data-id="{{ a.id }}"
     onclick="this.classList.toggle('bg-blue-50'); this.classList.toggle('border-blue-200'); var cb=this.querySelector('input'); cb.checked=!cb.checked; updateLinkCount();">
  <input type="checkbox" name="activity_ids" value="{{ a.id }}"
         class="mt-1 rounded border-gray-300 text-marsh focus:ring-marsh" onclick="event.stopPropagation()">
  <div class="flex-1 min-w-0">
    <div class="flex items-center gap-1.5">
      <span class="text-[10px] px-1.5 py-0.5 rounded
        {% if a.activity_type == 'Call' %}bg-blue-100 text-blue-700
        {% elif a.activity_type == 'Email' %}bg-amber-100 text-amber-700
        {% elif a.activity_type == 'Meeting' %}bg-indigo-100 text-indigo-700
        {% elif a.activity_type == 'Note' %}bg-pink-100 text-pink-700
        {% else %}bg-gray-100 text-gray-600{% endif %}">{{ a.activity_type }}</span>
      <span class="text-[10px] text-gray-400">{{ a.activity_date }}</span>
    </div>
    <div class="text-xs font-medium text-gray-800 mt-1">{{ a.subject }}</div>
    <div class="text-[10px] text-gray-500 mt-0.5">
      {% if a.policy_uid %}{{ a.policy_uid }} · {{ a.policy_type or '' }}{% endif %}
      {% if a.duration_hours %} · {{ a.duration_hours }}h{% endif %}
    </div>
  </div>
</div>
{% else %}
<p class="text-sm text-gray-400 text-center py-6">No unlinked activities found</p>
{% endfor %}
```

- [ ] **Step 4: Add `POST /issues/{issue_id}/link-activities` endpoint**

Add to `src/policydb/web/routes/issues.py`:

```python
@router.post("/issues/{issue_id}/link-activities", response_class=HTMLResponse)
def link_activities_to_issue(
    request: Request,
    issue_id: int,
    activity_ids: list[int] = Form(default=[]),
    conn=Depends(get_db),
):
    """Bulk-link activities to an issue."""
    if activity_ids:
        placeholders = ",".join("?" * len(activity_ids))
        conn.execute(
            f"UPDATE activity_log SET issue_id = ? WHERE id IN ({placeholders})",
            [issue_id] + activity_ids,
        )
        conn.commit()
    # Redirect to issue detail page
    row = conn.execute("SELECT issue_uid FROM activity_log WHERE id = ?", (issue_id,)).fetchone()
    uid = row["issue_uid"] if row else issue_id
    from starlette.responses import RedirectResponse
    return RedirectResponse(f"/issues/{uid}", status_code=303)
```

- [ ] **Step 5: Enhance `POST /issues/create` for source linking**

In the existing `create` route (~line 21-68 of `issues.py`), add `source_activity_id` and `source_activity_ids` form params and link after insert:

Add form params to the function signature:

```python
source_activity_id: int = Form(0),
source_activity_ids: str = Form(""),
```

After the INSERT and `conn.commit()`, add:

```python
    # Link source activities
    new_issue_id = cursor.lastrowid
    if source_activity_id:
        conn.execute(
            "UPDATE activity_log SET issue_id = ? WHERE id = ?",
            (new_issue_id, source_activity_id),
        )
        conn.commit()
    elif source_activity_ids:
        ids = [int(x.strip()) for x in source_activity_ids.split(",") if x.strip().isdigit()]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE activity_log SET issue_id = ? WHERE id IN ({placeholders})",
                [new_issue_id] + ids,
            )
            conn.commit()
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/issues.py src/policydb/web/templates/issues/_issue_widget.html src/policydb/web/templates/issues/_linkable_list.html
git commit -m "feat: add issue widget, linkable activities, and source linking endpoints"
```

---

## Task 5: Activity Route Changes — Accept issue_id, Remove Auto-Threading

**Files:**
- Modify: `src/policydb/web/routes/activities.py` (lines 88-162)
- Modify: `src/policydb/web/routes/policies.py` (lines 260-309)

- [ ] **Step 1: Update `POST /activities/log` to accept `issue_id` and remove auto-threading**

In `src/policydb/web/routes/activities.py`, add `issue_id` form param to `activity_log()` function signature (after `disposition`):

```python
issue_id: int = Form(0),
```

In the INSERT statement (~line 124-132), add `issue_id` to the column list and values:

```python
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person, contact_id, subject, details, follow_up_date, account_exec, duration_hours, disposition, issue_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         contact_person or None, _contact_id, subject, details or None,
         follow_up_date or None, account_exec, round_duration(duration_hours),
         disposition.strip() or None, issue_id or None),
    )
```

**Remove the auto-threading block** (lines ~136-160 approximately — the entire `if policy_id:` block that checks for open issues and auto-links). Replace it with nothing — the `issue_id` from the form handles linking now.

- [ ] **Step 2: Update `POST /policies/{uid}/row/log` to accept `issue_id`**

In `src/policydb/web/routes/policies.py`, find the `row_log` handler (~line 260). Add `issue_id` form param:

```python
issue_id: int = Form(0),
```

Add `issue_id` to the INSERT statement's column list and values (same pattern as Step 1).

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/activities.py src/policydb/web/routes/policies.py
git commit -m "feat: accept issue_id on activity log, remove auto-threading"
```

---

## Task 6: Activities Tab — Kanban Board View

**Files:**
- Create: `src/policydb/web/templates/action_center/_activities_board.html`
- Modify: `src/policydb/web/templates/action_center/_activities.html`
- Modify: `src/policydb/web/routes/action_center.py` (~lines 415-456)

- [ ] **Step 1: Update `_activities_ctx()` to return kanban data**

In `src/policydb/web/routes/action_center.py`, modify `_activities_ctx()` to also call the new kanban query:

```python
from policydb.queries import get_client_activity_board

# Add to the returned dict (alongside existing 'activities' key):
ctx["client_columns"] = get_client_activity_board(conn, days, activity_type, q, client_id)
ctx["view_mode"] = "board"  # default; will be overridden by query param
```

Add `view_mode: str = ""` query param to the activities tab route handler and pass it into context.

- [ ] **Step 2: Add Board/Table toggle to `_activities.html`**

At the top of `src/policydb/web/templates/action_center/_activities.html`, inside the filter bar div (line ~64), add the toggle before the count badge:

```html
    {# View toggle #}
    <div class="flex border border-gray-200 rounded-md overflow-hidden ml-auto">
      <button hx-get="/action-center/activities"
              hx-target="#ac-tab-content"
              hx-include="#ac-act-days, #ac-act-type, #ac-act-client, #ac-act-search"
              hx-vals='{"view_mode": "board"}'
              class="text-[11px] px-3 py-1 {% if view_mode == 'board' %}bg-marsh text-white{% else %}bg-white text-gray-500 hover:bg-gray-50{% endif %} transition-colors">
        Board
      </button>
      <button hx-get="/action-center/activities"
              hx-target="#ac-tab-content"
              hx-include="#ac-act-days, #ac-act-type, #ac-act-client, #ac-act-search"
              hx-vals='{"view_mode": "table"}'
              class="text-[11px] px-3 py-1 border-l border-gray-200 {% if view_mode == 'table' %}bg-marsh text-white{% else %}bg-white text-gray-500 hover:bg-gray-50{% endif %} transition-colors">
        Table
      </button>
    </div>
```

Below the filter bar, add conditional rendering:

```html
  {% if view_mode == 'board' %}
    {% include "action_center/_activities_board.html" %}
  {% else %}
    {# existing table markup stays here #}
  {% endif %}
```

Add issue badge and escalate button to each table row (inside the existing `{% for a in activities %}` loop). Before the delete button `<td>`:

```html
          {# Issue badge or escalate button #}
          <td class="px-3 py-2 whitespace-nowrap no-print">
            {% if a.linked_issue_uid %}
              {% with linked_issue_uid=a.linked_issue_uid, linked_issue_subject=a.linked_issue_subject, linked_issue_severity=a.linked_issue_severity %}
                {% include "_issue_badge.html" %}
              {% endwith %}
            {% else %}
              <button type="button"
                onclick="openIssueCreateSlideover({subject:'{{ a.subject|e }}', client_id:'{{ a.client_id }}', client_name:'{{ a.client_name|e }}', policy_id:'{{ a.policy_id or '' }}', policy_label:'{{ a.policy_uid or '' }} {{ a.policy_type or '' }}', source_activity_id:'{{ a.id }}', context_label:'Creating from activity', context_subject:'{{ a.subject|e }}', context_detail:'{{ a.activity_date }} · {{ a.client_name|e }}', link_detail:'{{ a.subject|e }} ({{ a.activity_date }})'})"
                class="text-[10px] text-blue-600 border border-blue-200 rounded px-2 py-0.5 hover:bg-blue-50 transition-colors whitespace-nowrap">
                &#9873; escalate
              </button>
            {% endif %}
          </td>
```

- [ ] **Step 3: Create kanban board partial**

Create `src/policydb/web/templates/action_center/_activities_board.html`:

```html
{# Activities Tab — Client Kanban Board View #}
<div class="flex gap-3 overflow-x-auto pb-4" style="min-height: 300px; align-items: flex-start;">
  {% for col in client_columns %}
  <div class="min-w-[260px] max-w-[280px] flex-shrink-0 bg-white rounded-lg border border-gray-200 flex flex-col">

    {# Column header #}
    <div class="px-3 py-2.5 border-b border-gray-200 flex items-center gap-2">
      <a href="/clients/{{ col.client_id }}" class="text-sm font-semibold text-marsh hover:underline truncate">{{ col.client_name }}</a>
      <span class="text-[10px] text-gray-400 ml-auto whitespace-nowrap">{{ col.activity_count }} act{% if col.total_hours %} · {{ '%.1f'|format(col.total_hours) }}h{% endif %}</span>
    </div>

    <div class="p-2 flex flex-col gap-2 overflow-y-auto" style="max-height: 400px;">

      {# ── Issue cards ── #}
      {% for iss in col.issues %}
      <div class="rounded-md p-2 border
        {% if iss.issue_severity == 'Critical' %}bg-red-50 border-red-200
        {% elif iss.issue_severity == 'High' %}bg-amber-50 border-amber-200
        {% elif iss.issue_severity == 'Normal' %}bg-blue-50 border-blue-200
        {% else %}bg-gray-50 border-gray-200{% endif %}">

        {# Issue header #}
        <div class="flex items-center gap-1.5 mb-2">
          <span class="w-2 h-2 rounded-full flex-shrink-0
            {% if iss.issue_severity == 'Critical' %}bg-red-500
            {% elif iss.issue_severity == 'High' %}bg-amber-500
            {% elif iss.issue_severity == 'Normal' %}bg-blue-500
            {% else %}bg-gray-400{% endif %}"></span>
          <a href="/issues/{{ iss.issue_uid }}" class="text-[11px] font-semibold truncate flex-1
            {% if iss.issue_severity == 'Critical' %}text-red-800
            {% elif iss.issue_severity == 'High' %}text-amber-800
            {% elif iss.issue_severity == 'Normal' %}text-blue-800
            {% else %}text-gray-700{% endif %} hover:underline">
            {{ iss.subject }}
          </a>
          <span class="text-[9px] font-medium
            {% if iss.days_open > (iss.issue_sla_days or 7) %}text-red-600{% else %}text-gray-400{% endif %}">
            {{ iss.days_open }}d
          </span>
        </div>

        {# Linked activities #}
        {% for act in iss.activities %}
        <div class="px-2 py-1.5 bg-white rounded mb-1 border-l-[3px] text-[11px]
          {% if act.activity_type == 'Email' %}border-l-blue-500
          {% elif act.activity_type == 'Call' %}border-l-amber-500
          {% elif act.activity_type == 'Meeting' %}border-l-indigo-500
          {% elif act.activity_type == 'Note' %}border-l-pink-500
          {% else %}border-l-gray-400{% endif %}">
          <div class="flex justify-between">
            <span class="font-medium
              {% if act.activity_type == 'Email' %}text-blue-800
              {% elif act.activity_type == 'Call' %}text-amber-800
              {% elif act.activity_type == 'Meeting' %}text-indigo-800
              {% elif act.activity_type == 'Note' %}text-pink-800
              {% else %}text-gray-600{% endif %}">{{ act.activity_type }}</span>
            <span class="text-[10px] text-gray-400">{{ act.activity_date[5:].replace('-', '/') if act.activity_date else '' }}</span>
          </div>
          <div class="text-gray-600 mt-0.5 truncate">{{ act.subject }}</div>
        </div>
        {% endfor %}

        {# + log action #}
        <div class="mt-1.5 text-center">
          <button type="button"
            onclick="openIssueCreateSlideover({subject:'', client_id:'{{ col.client_id }}', client_name:'{{ col.client_name|e }}', severity:'{{ iss.issue_severity }}'})"
            class="text-[10px] text-gray-400 hover:text-marsh px-2 py-0.5 border border-dashed border-gray-300 rounded cursor-pointer transition-colors">
            + log
          </button>
        </div>
      </div>
      {% endfor %}

      {# ── Untracked activities ── #}
      {% if col.untracked %}
      {% if col.issues %}
      <div class="border-t border-dashed border-gray-300 pt-1.5 mt-1">
        <div class="text-[9px] text-gray-400 uppercase tracking-wider mb-1.5">Untracked</div>
      </div>
      {% endif %}
      {% for act in col.untracked %}
      <div class="px-2 py-1.5 bg-gray-50 rounded border-l-[3px] border-l-gray-300 text-[11px]">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-1">
            <span class="font-medium text-gray-700">{{ act.activity_type }}</span>
            <span class="text-[10px] text-gray-400 ml-1">{{ act.activity_date[5:].replace('-', '/') if act.activity_date else '' }}</span>
          </div>
          <button type="button"
            onclick="openIssueCreateSlideover({subject:'{{ act.subject|e }}', client_id:'{{ col.client_id }}', client_name:'{{ col.client_name|e }}', policy_id:'{{ act.policy_id or '' }}', policy_label:'{{ act.policy_uid or '' }} {{ act.policy_type or '' }}', source_activity_id:'{{ act.id }}', context_label:'Creating from activity', context_subject:'{{ act.subject|e }}', context_detail:'{{ act.activity_date }} · {{ col.client_name|e }}', link_detail:'{{ act.subject|e }} ({{ act.activity_date }})'})"
            class="text-[9px] text-blue-600 border border-blue-200 rounded px-1.5 py-0.5 hover:bg-blue-50 transition-colors">
            escalate
          </button>
        </div>
        <div class="text-gray-600 mt-0.5 truncate">{{ act.subject }}</div>
      </div>
      {% endfor %}
      {% endif %}

    </div>
  </div>
  {% else %}
  <div class="border border-dashed border-gray-300 rounded-lg p-8 text-center text-gray-400 text-sm w-full">
    No activities found for this period.
  </div>
  {% endfor %}
</div>
```

- [ ] **Step 4: Include the creation slideover in the Action Center page**

The Action Center main template needs to include the slideover. Find the main `action_center.html` template and add before `{% endblock %}`:

```html
{% include "_issue_create_slideover.html" %}
```

Ensure `issue_severities` and `all_clients` are passed to the template context from the route handler.

- [ ] **Step 5: Update activity queries to include issue join data**

In `_activities_ctx()` in `action_center.py`, update the activity query to LEFT JOIN for issue info. The existing query joins should be extended to include:

```sql
LEFT JOIN activity_log iss ON a.issue_id = iss.id AND iss.item_kind = 'issue'
```

And select: `iss.issue_uid AS linked_issue_uid, iss.subject AS linked_issue_subject, iss.issue_severity AS linked_issue_severity`

- [ ] **Step 6: Start server and QA test**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb serve &
sleep 2
```

Navigate to `http://127.0.0.1:8000/action-center?tab=activities` and verify:
- Board/Table toggle appears and switches views
- Board view shows client columns with activities
- Table view shows issue badges and escalate buttons
- Escalate button opens the creation slideover
- Slideover fields populate correctly

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/templates/action_center/_activities_board.html src/policydb/web/templates/action_center/_activities.html src/policydb/web/routes/action_center.py
git commit -m "feat: add client kanban board view for activities tab"
```

---

## Task 7: Follow-up Rows — Issue Badge + Escalate Button

**Files:**
- Modify: `src/policydb/web/templates/action_center/_followup_sections.html`
- Modify: `src/policydb/web/routes/action_center.py` (`_followups_ctx()`)

- [ ] **Step 1: Update follow-up queries to include issue data**

In `_followups_ctx()` in `action_center.py`, extend the follow-up queries to LEFT JOIN for issue info (same pattern as Task 6 Step 5):

```sql
LEFT JOIN activity_log iss ON a.issue_id = iss.id AND iss.item_kind = 'issue'
```

Select: `iss.issue_uid AS linked_issue_uid, iss.subject AS linked_issue_subject, iss.issue_severity AS linked_issue_severity`

- [ ] **Step 2: Add issue badge and escalate button to follow-up rows**

In `_followup_sections.html`, find the row template (the `fu_row()` macro or equivalent). Add the badge/escalate button in each row, after the existing action buttons:

```html
{# Issue badge or escalate #}
{% if item.linked_issue_uid %}
  {% with linked_issue_uid=item.linked_issue_uid, linked_issue_subject=item.linked_issue_subject, linked_issue_severity=item.linked_issue_severity %}
    {% include "_issue_badge.html" %}
  {% endwith %}
{% else %}
  <button type="button"
    onclick="openIssueCreateSlideover({subject:'{{ item.subject|e }}', client_id:'{{ item.client_id }}', client_name:'{{ item.client_name|e }}', policy_id:'{{ item.policy_id or '' }}', policy_label:'{{ item.policy_uid or '' }} {{ item.policy_type or '' }}', source_activity_id:'{{ item.id }}', context_label:'Creating from activity', context_subject:'{{ item.subject|e }}', context_detail:'{{ item.follow_up_date or item.activity_date }} · {{ item.client_name|e }}', link_detail:'{{ item.subject|e }}'})"
    class="text-[10px] text-blue-600 border border-blue-200 rounded px-2 py-0.5 hover:bg-blue-50 transition-colors no-print">
    &#9873; escalate
  </button>
{% endif %}
```

- [ ] **Step 3: QA test follow-ups tab**

Navigate to `http://127.0.0.1:8000/action-center?tab=followups` and verify:
- Issue badges show on rows linked to issues
- Escalate buttons show on unlinked rows
- Clicking escalate opens the slideover with correct pre-fills

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/action_center/_followup_sections.html src/policydb/web/routes/action_center.py
git commit -m "feat: add issue badge and escalate button to follow-up rows"
```

---

## Task 8: Policy Row Log — Issue Combobox

**Files:**
- Modify: `src/policydb/web/templates/policies/_policy_row_log.html`

- [ ] **Step 1: Add issue combobox to the inline log form**

In `src/policydb/web/templates/policies/_policy_row_log.html`, change `grid-cols-6` to `grid-cols-7` on the grid div (~line 27), then add a new column after the Contact field:

```html
        <div>
          <p class="text-xs text-gray-500 mb-0.5">Issue <span class="text-xs text-gray-300">(opt)</span></p>
          <select name="issue_id"
            class="border border-gray-300 rounded px-2 py-1 text-xs w-full focus:outline-none focus:ring-1 focus:ring-amber-400 bg-white">
            <option value="">None</option>
            {# Open issues for this policy's client — populated server-side #}
            {% if open_issues %}
            {% for iss in open_issues %}
            <option value="{{ iss.id }}">{{ iss.subject[:30] }}</option>
            {% endfor %}
            {% endif %}
          </select>
        </div>
```

- [ ] **Step 2: Pass open issues to the row log template**

In `src/policydb/web/routes/policies.py`, in the `row_log` GET handler (the one that renders the form), query open issues for the policy's client and pass as `open_issues` in the template context:

```python
open_issues = conn.execute("""
    SELECT a.id, a.subject, a.issue_severity
    FROM activity_log a
    WHERE a.item_kind = 'issue' AND a.issue_id IS NULL
      AND a.client_id = ?
      AND (a.issue_status IS NULL OR a.issue_status NOT IN ('Resolved', 'Closed'))
    ORDER BY CASE a.issue_severity
      WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END
""", (policy["client_id"],)).fetchall()
```

Pass `open_issues=[dict(r) for r in open_issues]` to the template.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/policies/_policy_row_log.html src/policydb/web/routes/policies.py
git commit -m "feat: add issue combobox to policy row inline log"
```

---

## Task 9: Issues Tab — Replace Inline Form with Slideover Trigger

**Files:**
- Modify: `src/policydb/web/templates/action_center/_issues.html`

- [ ] **Step 1: Replace the inline new-issue form with a slideover trigger**

In `src/policydb/web/templates/action_center/_issues.html`, replace the `+ New Issue` button's onclick (line ~27-28) to open the slideover instead of toggling the inline form:

```html
      <button onclick="openIssueCreateSlideover({})"
              class="text-sm bg-marsh text-white rounded px-3 py-1.5 hover:bg-marsh-light">
        + New Issue
      </button>
```

Remove or hide the entire `#new-issue-form` div (lines 35-96) — it's replaced by the slideover.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/action_center/_issues.html
git commit -m "feat: replace inline issue form with creation slideover trigger"
```

---

## Task 10: Link Activities Slideover on Issue Detail Page

**Files:**
- Create: `src/policydb/web/templates/issues/_link_activities_slideover.html`
- Modify: `src/policydb/web/templates/issues/detail.html`

- [ ] **Step 1: Create link activities slideover partial**

Create `src/policydb/web/templates/issues/_link_activities_slideover.html`:

```html
{# ── Link Activities Slideover ── #}
<div id="link-activities-backdrop"
     class="fixed inset-0 bg-black/30 z-40 hidden"
     onclick="closeLinkActivities()"></div>

<div id="link-activities-panel"
     class="fixed top-0 right-0 bottom-0 w-[480px] max-sm:w-full bg-white shadow-xl z-50 flex flex-col hidden">

  {# Header #}
  <div class="flex items-center justify-between p-4 border-b shrink-0">
    <div>
      <h2 class="text-base font-semibold text-gray-900">Link Activities</h2>
      <p class="text-xs text-gray-500 mt-0.5">Attach existing activities to this issue</p>
    </div>
    <button onclick="closeLinkActivities()"
            class="text-gray-400 hover:text-gray-600">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
      </svg>
    </button>
  </div>

  {# Filters #}
  <div class="p-4 border-b border-gray-100 shrink-0 space-y-2">
    <input type="search" placeholder="Search by subject…"
           id="link-act-search"
           hx-get="/issues/{{ issue.id }}/linkable-activities"
           hx-trigger="input changed delay:300ms"
           hx-target="#linkable-list"
           hx-include="#link-act-type, #link-act-days"
           class="w-full rounded border-gray-300 text-sm px-3 py-1.5"
           name="q">
    <div class="flex gap-2">
      <select name="activity_type" id="link-act-type"
              hx-get="/issues/{{ issue.id }}/linkable-activities"
              hx-trigger="change"
              hx-target="#linkable-list"
              hx-include="#link-act-search, #link-act-days"
              class="rounded border-gray-300 text-xs px-2 py-1.5 flex-1">
        <option value="">All Types</option>
        {% for at in activity_types %}
        <option value="{{ at }}">{{ at }}</option>
        {% endfor %}
      </select>
      <select name="days" id="link-act-days"
              hx-get="/issues/{{ issue.id }}/linkable-activities"
              hx-trigger="change"
              hx-target="#linkable-list"
              hx-include="#link-act-search, #link-act-type"
              class="rounded border-gray-300 text-xs px-2 py-1.5 flex-1">
        <option value="30">Last 30 days</option>
        <option value="7">Last 7 days</option>
        <option value="90">Last 90 days</option>
      </select>
    </div>
    <div class="text-[10px] text-gray-400">Showing unlinked activities for {{ issue.client_name }}</div>
  </div>

  {# Activity list — loaded via HTMX #}
  <div class="flex-1 overflow-y-auto p-4" id="linkable-list"
       hx-get="/issues/{{ issue.id }}/linkable-activities"
       hx-trigger="load"
       hx-swap="innerHTML">
    <p class="text-sm text-gray-400 text-center py-6">Loading...</p>
  </div>

  {# Sticky footer #}
  <div class="shrink-0 border-t p-4 flex items-center gap-3 bg-white">
    <span class="text-xs font-semibold text-marsh" id="link-selected-count">0 selected</span>
    <form method="post" action="/issues/{{ issue.id }}/link-activities" id="link-activities-form" class="ml-auto flex gap-2">
      <input type="hidden" name="activity_ids" id="link-activity-ids" value="">
      <button type="submit" class="text-sm bg-marsh text-white rounded-lg px-4 py-2 font-medium hover:bg-marsh-light">
        Link Selected
      </button>
      <button type="button" onclick="closeLinkActivities()"
              class="text-sm text-gray-500 border border-gray-300 rounded-lg px-4 py-2 hover:bg-gray-50">
        Cancel
      </button>
    </form>
  </div>
</div>

<script>
function openLinkActivities() {
  document.getElementById('link-activities-panel').classList.remove('hidden');
  document.getElementById('link-activities-backdrop').classList.remove('hidden');
}
function closeLinkActivities() {
  document.getElementById('link-activities-panel').classList.add('hidden');
  document.getElementById('link-activities-backdrop').classList.add('hidden');
}
function updateLinkCount() {
  var checked = document.querySelectorAll('#linkable-list input[name="activity_ids"]:checked');
  var count = checked.length;
  document.getElementById('link-selected-count').textContent = count + ' selected';
  var ids = Array.from(checked).map(function(cb) { return cb.value; });
  document.getElementById('link-activity-ids').value = ids.join(',');
}
// Delegate checkbox changes
document.addEventListener('change', function(e) {
  if (e.target.name === 'activity_ids') updateLinkCount();
});
</script>
```

- [ ] **Step 2: Add "+ Link Activity" button to issue detail page**

In `src/policydb/web/templates/issues/detail.html`, find the Activity Timeline header (~line 65-69). Add a button next to the activity count:

```html
          <div class="flex items-center gap-2">
            <span class="text-xs text-gray-400">
              {{ activities|length }} activities{% if total_hours %} &middot; {{ total_hours }}h{% endif %}
            </span>
            <button onclick="openLinkActivities()"
                    class="text-xs text-blue-600 border border-blue-200 rounded px-2 py-1 hover:bg-blue-50 transition-colors no-print">
              + Link Activity
            </button>
          </div>
```

At the bottom of the template (before `{% endblock %}`), include the slideover:

```html
{% include "issues/_link_activities_slideover.html" %}
```

- [ ] **Step 3: QA test the link activities flow**

Navigate to an issue detail page and verify:
- "+ Link Activity" button appears
- Clicking opens the slideover
- Filters work (search, type, date range)
- Checkboxes select/deselect and count updates
- "Link Selected" submits and refreshes the page with new activities in the timeline

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/issues/_link_activities_slideover.html src/policydb/web/templates/issues/detail.html
git commit -m "feat: add link activities slideover to issue detail page"
```

---

## Task 11: Quick Log Form — Issue Widget

**Files:**
- Modify: `src/policydb/web/templates/action_center/_activities.html` (Quick Log form)

- [ ] **Step 1: Add HTMX issue widget target to Quick Log form**

In the Quick Log form in `_activities.html` (lines 5-61), add an `hx-get` trigger on the client `<select>` to load the issue widget when a client is selected:

On the client `<select>` (~line 17-23), add:

```html
hx-get="/issues/for-client/"
hx-trigger="change"
hx-target="#quick-log-issue-widget"
hx-swap="innerHTML"
hx-vals="js:{}"
```

Use JavaScript to build the URL dynamically. Replace the simple `hx-get` with an `onchange` handler:

```html
<select name="client_id" required
  onchange="if(this.value){htmx.ajax('GET','/issues/for-client/'+this.value,{target:'#quick-log-issue-widget'})}else{document.getElementById('quick-log-issue-widget').innerHTML='';}"
  class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 bg-white focus:ring-1 focus:ring-marsh">
```

After the Details textarea (~line 52), add the widget container:

```html
      {# Issue widget — populated via HTMX when client selected #}
      <div id="quick-log-issue-widget"></div>
```

- [ ] **Step 2: QA test the issue widget**

Navigate to `http://127.0.0.1:8000/action-center?tab=activities`, open Quick Log, select a client that has open issues. Verify:
- Issue widget appears with open issues
- Clicking "Link" sets the hidden field and shows green confirmation
- Submitting the form with a linked issue creates an activity with `issue_id` set

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_activities.html
git commit -m "feat: add issue widget to Quick Log form"
```

---

## Task 12: Weekly Plan — Escalation Review

**Files:**
- Create: `src/policydb/web/templates/followups/_escalation_review.html`
- Modify: `src/policydb/web/templates/followups/plan.html`
- Modify: `src/policydb/web/routes/activities.py` (plan_week route)

- [ ] **Step 1: Add escalation suggestions to plan_week route context**

In `src/policydb/web/routes/activities.py`, in the `followups_plan()` handler (~line 1028), add:

```python
from policydb.queries import get_escalation_suggestions
escalation_suggestions = get_escalation_suggestions(conn)
```

Pass `escalation_suggestions=escalation_suggestions` to the template context.

Also add a dismiss route:

```python
@router.post("/followups/plan/dismiss-escalation", response_class=HTMLResponse)
def dismiss_escalation(
    request: Request,
    policy_id: int = Form(...),
    trigger_type: str = Form(...),
    conn=Depends(get_db),
):
    """Dismiss an escalation suggestion."""
    conn.execute(
        "INSERT OR REPLACE INTO escalation_dismissals (policy_id, trigger_type, dismissed_at) VALUES (?, ?, datetime('now'))",
        (policy_id, trigger_type),
    )
    conn.commit()
    return HTMLResponse("")  # Remove the row via hx-swap="delete"


@router.post("/followups/plan/dismiss-all-escalations", response_class=HTMLResponse)
def dismiss_all_escalations(
    request: Request,
    suggestions: str = Form(""),
    conn=Depends(get_db),
):
    """Dismiss all current escalation suggestions."""
    import json
    try:
        items = json.loads(suggestions) if suggestions else []
    except (json.JSONDecodeError, TypeError):
        items = []
    for item in items:
        pid = item.get("policy_id")
        tt = item.get("trigger_type")
        if pid and tt:
            conn.execute(
                "INSERT OR REPLACE INTO escalation_dismissals (policy_id, trigger_type, dismissed_at) VALUES (?, ?, datetime('now'))",
                (pid, tt),
            )
    conn.commit()
    return HTMLResponse("")
```

- [ ] **Step 2: Create escalation review partial**

Create `src/policydb/web/templates/followups/_escalation_review.html`:

```html
{# Escalation Review Banner — shown at top of Plan Week when suggestions exist #}
{% if escalation_suggestions %}
<div class="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-5" id="escalation-review">
  <div class="flex items-center justify-between mb-3">
    <div class="flex items-center gap-2">
      <span class="text-base">&#9888;</span>
      <span class="text-sm font-semibold text-amber-800">Escalation Review</span>
      <span class="text-xs text-amber-600 bg-amber-100 px-2 py-0.5 rounded-full">{{ escalation_suggestions|length }}</span>
    </div>
    <form hx-post="/followups/plan/dismiss-all-escalations" hx-target="#escalation-review" hx-swap="delete">
      <input type="hidden" name="suggestions" value='{{ escalation_suggestions | tojson }}'>
      <button type="submit" class="text-xs text-amber-700 hover:text-amber-900 underline">Dismiss all</button>
    </form>
  </div>

  <div class="space-y-2">
    {% for sug in escalation_suggestions %}
    <div class="flex items-center gap-3 p-3 bg-white rounded-md border border-amber-100" id="esc-{{ loop.index }}">
      {# Icon #}
      <div class="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0
        {% if sug.severity_preset == 'Critical' %}bg-red-100
        {% else %}bg-amber-100{% endif %}">
        {% if sug.icon == 'drift' or sug.icon == 'critical' %}
        <span class="text-xs {% if sug.severity_preset == 'Critical' %}text-red-600{% else %}text-amber-600{% endif %}">&#9660;</span>
        {% elif sug.icon == 'nudge' %}
        <span class="text-xs text-amber-600">&#8635;</span>
        {% else %}
        <span class="text-xs text-amber-600">&#9201;</span>
        {% endif %}
      </div>

      {# Description #}
      <div class="flex-1 min-w-0">
        <div class="text-xs font-medium text-gray-900">{{ sug.title }}</div>
        <div class="text-[10px] text-gray-500 mt-0.5">{{ sug.detail }}</div>
      </div>

      {# Actions #}
      <div class="flex gap-2 flex-shrink-0">
        <button type="button"
          onclick="openIssueCreateSlideover({subject:'{{ sug.title|e }}', client_id:'{{ sug.client_id }}', client_name:'{{ sug.client_name|e }}', policy_id:'{{ sug.policy_id or '' }}', severity:'{{ sug.severity_preset }}', context_label:'Creating from suggestion', context_subject:'{{ sug.title|e }}', context_detail:'{{ sug.detail|e }}', source_activity_ids:'{{ sug.source_activity_ids|join(\",\") }}', link_detail:'{{ sug.source_activity_ids|length }} activities will be linked'})"
          class="text-[10px] bg-marsh text-white px-3 py-1.5 rounded font-medium hover:bg-marsh-light transition-colors">
          Create Issue
        </button>
        <form hx-post="/followups/plan/dismiss-escalation" hx-target="#esc-{{ loop.index }}" hx-swap="delete" class="inline">
          <input type="hidden" name="policy_id" value="{{ sug.policy_id }}">
          <input type="hidden" name="trigger_type" value="{{ sug.trigger_type }}">
          <button type="submit"
            class="text-[10px] text-gray-500 border border-gray-200 px-3 py-1.5 rounded hover:bg-gray-50 transition-colors">
            Dismiss
          </button>
        </form>
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 3: Include escalation review in plan.html**

In `src/policydb/web/templates/followups/plan.html`, add after the header section and before the week grid:

```html
{% include "followups/_escalation_review.html" %}
```

Also include the creation slideover at the bottom of the template:

```html
{% include "_issue_create_slideover.html" %}
```

- [ ] **Step 4: QA test the Weekly Plan escalation review**

Navigate to `http://127.0.0.1:8000/followups/plan` and verify:
- Escalation review banner appears when suggestions exist
- "Create Issue" opens the creation slideover with pre-fills
- "Dismiss" removes the individual suggestion row
- "Dismiss all" removes the entire banner
- Suggestions don't reappear for dismissed items (until data changes)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/followups/_escalation_review.html src/policydb/web/templates/followups/plan.html src/policydb/web/routes/activities.py
git commit -m "feat: add escalation review section to Weekly Plan"
```

---

## Task 13: Final QA + Cleanup

**Files:** All modified files

- [ ] **Step 1: Full QA pass**

Start the server and test the complete flow end-to-end:

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb serve &
sleep 2
```

Test matrix:
1. **Activities tab board view**: Client columns render, issues nested, escalate works
2. **Activities tab table view**: Toggle works, badges show, escalate works
3. **Quick Log**: Select client → issue widget appears → link → log → activity has issue_id
4. **Policy Row Log**: Issue combobox shows, saves correctly
5. **Follow-ups tab**: Badges and escalate buttons on all row types
6. **Issues tab**: "+ New Issue" opens slideover (not inline form)
7. **Issue detail page**: "+ Link Activity" → slideover → select → link → timeline updates
8. **Weekly Plan**: Escalation review shows, create/dismiss work
9. **Creation slideover**: All three entry paths (escalate, suggestion, new) populate correctly

- [ ] **Step 2: Fix any issues found in QA**

Address any layout, data, or interaction issues.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "fix: QA fixes for activity-issue integration"
```

---

## Summary

| Task | Description | Key Files |
|------|------------|-----------|
| 1 | Migration: escalation_dismissals table | migration 105, db.py |
| 2 | Query functions: kanban board + escalation suggestions | queries.py |
| 3 | UI primitives: issue badge + creation slideover | _issue_badge.html, _issue_create_slideover.html |
| 4 | Issue routes: for-client, linkable, link, enhanced create | issues.py, widget/list partials |
| 5 | Activity routes: accept issue_id, remove auto-threading | activities.py, policies.py |
| 6 | Activities tab kanban board view | _activities_board.html, _activities.html, action_center.py |
| 7 | Follow-up rows: badge + escalate | _followup_sections.html, action_center.py |
| 8 | Policy row log: issue combobox | _policy_row_log.html, policies.py |
| 9 | Issues tab: slideover trigger replaces inline form | _issues.html |
| 10 | Issue detail: link activities slideover | _link_activities_slideover.html, detail.html |
| 11 | Quick Log: issue widget | _activities.html |
| 12 | Weekly Plan: escalation review | _escalation_review.html, plan.html, activities.py |
| 13 | Final QA + cleanup | All files |
