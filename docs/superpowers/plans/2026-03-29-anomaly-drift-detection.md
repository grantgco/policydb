# Anomaly & Drift Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a startup scan engine that detects workflow problems, neglected accounts, workload imbalances, and data mismatches — surfacing findings in the Action Center and inline on affected records.

**Architecture:** A Python module (`anomaly_engine.py`) runs on every server startup after timeline generation. It evaluates 10 configurable rule functions, writes findings to an `anomalies` table, and auto-resolves stale findings. The Action Center sidebar gets an anomaly widget, and client/policy pages get inline badges. The review gate adds quality conditions before stamping `last_reviewed_at`.

**Tech Stack:** Python stdlib (datetime, statistics), SQLite, Jinja2, HTMX

---

### Task 1: Migration — Create anomalies table + review_override_reason

**Files:**
- Create: `src/policydb/migrations/109_anomalies.sql`
- Create: `src/policydb/migrations/110_review_override.sql`
- Modify: `src/policydb/db.py` (after migration 108 block, ~line 1515)

- [ ] **Step 1: Create anomalies migration**

Create `src/policydb/migrations/109_anomalies.sql`:

```sql
-- Anomaly detection findings table
CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    client_id INTEGER,
    policy_id INTEGER,
    title TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    detected_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    acknowledged_at TEXT,
    resolved_at TEXT,
    scan_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anomalies_status ON anomalies(status);
CREATE INDEX IF NOT EXISTS idx_anomalies_client ON anomalies(client_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_policy ON anomalies(policy_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_rule ON anomalies(rule_key, client_id, policy_id);
```

- [ ] **Step 2: Create review override migration**

Create `src/policydb/migrations/110_review_override.sql`:

```sql
-- Add review_override_reason to policies and programs for guided review gate
ALTER TABLE policies ADD COLUMN review_override_reason TEXT;
ALTER TABLE programs ADD COLUMN review_override_reason TEXT;
```

- [ ] **Step 3: Wire migrations into `init_db()` in `db.py`**

Add after the migration 108 block (after line ~1515 `logger.info("Migration 108:...")`):

```python
    if 109 not in applied:
        sql = (_MIGRATIONS_DIR / "109_anomalies.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (109, "Create anomalies table for drift detection"),
        )
        conn.commit()
        logger.info("Migration 109: created anomalies table")

    if 110 not in applied:
        existing_policy_cols = {r[1] for r in conn.execute("PRAGMA table_info(policies)").fetchall()}
        if "review_override_reason" not in existing_policy_cols:
            conn.execute("ALTER TABLE policies ADD COLUMN review_override_reason TEXT")
        existing_program_cols = {r[1] for r in conn.execute("PRAGMA table_info(programs)").fetchall()}
        if "review_override_reason" not in existing_program_cols:
            conn.execute("ALTER TABLE programs ADD COLUMN review_override_reason TEXT")
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (110, "Add review_override_reason to policies and programs"),
        )
        conn.commit()
        logger.info("Migration 110: added review_override_reason columns")
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/109_anomalies.sql src/policydb/migrations/110_review_override.sql src/policydb/db.py
git commit -m "feat: create anomalies table and review_override_reason columns (migrations 109-110)"
```

---

### Task 2: Config — Add anomaly thresholds to defaults + Settings UI

**Files:**
- Modify: `src/policydb/config.py` — add `anomaly_thresholds` to `_DEFAULTS`
- Modify: `src/policydb/web/routes/settings.py` — add settings tab/section

- [ ] **Step 1: Add anomaly_thresholds to `_DEFAULTS` in config.py**

Add after the `issue_root_cause_categories` block in `_DEFAULTS`:

```python
    # ── Anomaly detection ──────────────────────────────────────────────
    "anomaly_thresholds": {
        "renewal_not_started_days": 60,
        "stale_followup_count": 10,
        "status_no_activity_days": 30,
        "no_activity_days": 90,
        "no_followup_scheduled": True,
        "heavy_week_threshold": 5,
        "forecast_window_days": 30,
        "light_week_window_days": 14,
        "bound_missing_effective": True,
        "expired_no_renewal": True,
        "review_min_health_score": 70,
        "review_activity_window_days": 30,
        "overdue_review_days": 90,
    },
```

- [ ] **Step 2: Add anomaly thresholds to Settings UI**

In `src/policydb/web/routes/settings.py`, add a route for updating anomaly thresholds. Find the settings page route and add to the template context. Add a GET endpoint for the anomaly settings tab partial and a POST endpoint for saving threshold changes.

The settings page already has tabs — add an "Anomaly Detection" tab. Create `src/policydb/web/templates/settings/_tab_anomalies.html`:

```html
{# Anomaly Detection settings tab #}
<div class="card p-5">
  <h2 class="text-sm font-semibold text-gray-900 mb-4">Anomaly Detection Thresholds</h2>
  <p class="text-xs text-gray-500 mb-4">Adjust when the system flags workflow issues. Changes take effect on next server restart or manual refresh.</p>

  <form hx-post="/settings/anomaly-thresholds" hx-swap="none"
        hx-on::after-request="if(event.detail.successful){document.getElementById('anomaly-save-msg').classList.remove('hidden');setTimeout(()=>document.getElementById('anomaly-save-msg').classList.add('hidden'),2000)}">

    <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">

      <div>
        <h3 class="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 border-b border-gray-100 pb-1">Falling Behind</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-gray-600 mb-1">Renewal not started (days before expiry)</label>
            <input type="number" name="renewal_not_started_days" value="{{ thresholds.renewal_not_started_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Stale follow-up backlog (max open)</label>
            <input type="number" name="stale_followup_count" value="{{ thresholds.stale_followup_count }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Status "In Progress" with no activity (days)</label>
            <input type="number" name="status_no_activity_days" value="{{ thresholds.status_no_activity_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
        </div>
      </div>

      <div>
        <h3 class="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 border-b border-gray-100 pb-1">Neglected Accounts</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-gray-600 mb-1">No activity threshold (days)</label>
            <input type="number" name="no_activity_days" value="{{ thresholds.no_activity_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
          <div class="flex items-center gap-2">
            <input type="checkbox" name="no_followup_scheduled" id="nfs" class="rounded border-gray-300 focus:ring-marsh"
                   {{ 'checked' if thresholds.no_followup_scheduled }}>
            <label for="nfs" class="text-xs text-gray-600">Flag clients with no scheduled follow-ups</label>
          </div>
        </div>
      </div>

      <div>
        <h3 class="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 border-b border-gray-100 pb-1">Workload Forecasting</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-gray-600 mb-1">Heavy week threshold (expirations per week)</label>
            <input type="number" name="heavy_week_threshold" value="{{ thresholds.heavy_week_threshold }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Forecast window (days ahead)</label>
            <input type="number" name="forecast_window_days" value="{{ thresholds.forecast_window_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="7">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Light week window (days ahead)</label>
            <input type="number" name="light_week_window_days" value="{{ thresholds.light_week_window_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="7">
          </div>
        </div>
      </div>

      <div>
        <h3 class="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 border-b border-gray-100 pb-1">Mismatches</h3>
        <div class="space-y-3">
          <div class="flex items-center gap-2">
            <input type="checkbox" name="bound_missing_effective" id="bme" class="rounded border-gray-300 focus:ring-marsh"
                   {{ 'checked' if thresholds.bound_missing_effective }}>
            <label for="bme" class="text-xs text-gray-600">Flag Bound/Issued with no effective date</label>
          </div>
          <div class="flex items-center gap-2">
            <input type="checkbox" name="expired_no_renewal" id="enr" class="rounded border-gray-300 focus:ring-marsh"
                   {{ 'checked' if thresholds.expired_no_renewal }}>
            <label for="enr" class="text-xs text-gray-600">Flag expired policies with no renewal</label>
          </div>
        </div>

        <h3 class="text-xs font-semibold text-gray-700 uppercase tracking-wide mt-4 mb-3 border-b border-gray-100 pb-1">Review Gate</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-gray-600 mb-1">Min data health score for clean review (%)</label>
            <input type="number" name="review_min_health_score" value="{{ thresholds.review_min_health_score }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="0" max="100">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Recent activity window for review (days)</label>
            <input type="number" name="review_activity_window_days" value="{{ thresholds.review_activity_window_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
          <div>
            <label class="block text-xs text-gray-600 mb-1">Overdue review threshold (days)</label>
            <input type="number" name="overdue_review_days" value="{{ thresholds.overdue_review_days }}"
                   class="w-24 rounded border-gray-300 text-sm px-2 py-1.5 focus:ring-marsh" min="1">
          </div>
        </div>
      </div>
    </div>

    <div class="mt-4 flex items-center gap-3">
      <button type="submit" class="text-sm bg-marsh text-white rounded px-4 py-1.5 hover:bg-marsh-light transition-colors">Save Thresholds</button>
      <span id="anomaly-save-msg" class="hidden text-xs text-green-600">Saved</span>
    </div>
  </form>
</div>
```

Add to `settings.py` — a POST route for saving thresholds:

```python
@router.post("/settings/anomaly-thresholds")
def save_anomaly_thresholds(request: Request):
    """Save anomaly detection thresholds."""
    import json
    form = await request.form()
    thresholds = cfg.get("anomaly_thresholds", {})
    int_keys = ["renewal_not_started_days", "stale_followup_count", "status_no_activity_days",
                "no_activity_days", "heavy_week_threshold", "forecast_window_days",
                "light_week_window_days", "review_min_health_score",
                "review_activity_window_days", "overdue_review_days"]
    bool_keys = ["no_followup_scheduled", "bound_missing_effective", "expired_no_renewal"]
    for k in int_keys:
        if k in form:
            thresholds[k] = int(form[k])
    for k in bool_keys:
        thresholds[k] = k in form
    cfg.set("anomaly_thresholds", thresholds)
    cfg.save_config()
    return {"ok": True}
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py src/policydb/web/templates/settings/_tab_anomalies.html
git commit -m "feat: add anomaly_thresholds config defaults and Settings UI"
```

---

### Task 3: Core Engine — anomaly_engine.py with all 10 rules

**Files:**
- Create: `src/policydb/anomaly_engine.py`

- [ ] **Step 1: Create the anomaly engine module**

Create `src/policydb/anomaly_engine.py` with:

1. `scan_anomalies(conn)` — main entry point called on startup
2. `_load_existing(conn)` — loads current non-resolved findings keyed by `(rule_key, client_id, policy_id)`
3. `_reconcile(conn, scan_id, existing, new_findings)` — inserts new, keeps active, auto-resolves stale
4. Individual rule functions that each return a list of finding tuples:
   - `_rule_renewal_not_started(conn, thresholds)`
   - `_rule_stale_followup_backlog(conn, thresholds)`
   - `_rule_milestone_drift(conn, thresholds)`
   - `_rule_overdue_review(conn, thresholds)`
   - `_rule_no_activity(conn, thresholds)`
   - `_rule_no_followup_scheduled(conn, thresholds)`
   - `_rule_heavy_week(conn, thresholds)`
   - `_rule_light_week(conn, thresholds)`
   - `_rule_status_contradiction(conn, thresholds)`
   - `_rule_expired_no_renewal(conn, thresholds)`
5. `get_anomaly_counts(conn)` — returns dict of counts by category for sidebar widget
6. `get_anomalies_for_client(conn, client_id)` — returns active findings for a client
7. `get_anomalies_for_policy(conn, policy_id)` — returns active findings for a policy
8. `get_all_active_anomalies(conn)` — returns all non-resolved findings grouped by category
9. `acknowledge_anomaly(conn, anomaly_id)` — sets status to acknowledged
10. `get_review_gate_status(conn, record_type, record_id)` — evaluates 4 review conditions, returns pass/fail per condition

Each finding tuple: `(rule_key, category, severity, client_id, policy_id, title, details)`

The scan function:
```python
def scan_anomalies(conn):
    """Run all anomaly rules and reconcile findings. Called on server startup."""
    import logging
    logger = logging.getLogger("policydb.anomaly_engine")
    thresholds = cfg.get("anomaly_thresholds", {})
    scan_id = datetime.now().isoformat()

    existing = _load_existing(conn)

    rules = [
        _rule_renewal_not_started,
        _rule_stale_followup_backlog,
        _rule_milestone_drift,
        _rule_overdue_review,
        _rule_no_activity,
        _rule_no_followup_scheduled,
        _rule_heavy_week,
        _rule_light_week,
        _rule_status_contradiction,
        _rule_expired_no_renewal,
    ]

    new_findings = []
    for rule_fn in rules:
        try:
            new_findings.extend(rule_fn(conn, thresholds))
        except Exception as e:
            logger.warning("Anomaly rule %s failed: %s", rule_fn.__name__, e)

    count = _reconcile(conn, scan_id, existing, new_findings)
    logger.info("Anomaly scan complete: %d active findings", count)
```

Each rule follows this pattern (example for `_rule_renewal_not_started`):
```python
def _rule_renewal_not_started(conn, thresholds):
    days = thresholds.get("renewal_not_started_days", 60)
    excluded = cfg.get("renewal_statuses_excluded", [])
    # Find policies expiring within X days with no renewal activity
    rows = conn.execute("""
        SELECT p.id, p.policy_uid, p.policy_type, p.expiration_date,
               c.name AS client_name, c.id AS client_id,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_exp
        FROM policies p
        JOIN clients c ON c.id = p.client_id
        WHERE p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND p.expiration_date >= date('now')
          AND julianday(p.expiration_date) - julianday('now') <= ?
          AND p.id NOT IN (
              SELECT DISTINCT a.policy_id FROM activity_log a
              WHERE a.policy_id = p.id
                AND a.activity_date >= date('now', '-' || ? || ' days')
          )
    """, (days, days)).fetchall()

    findings = []
    for r in rows:
        status = ... # check renewal_status not in excluded
        findings.append((
            "renewal_not_started", "falling_behind", "alert",
            r["client_id"], r["id"],
            f"{r['policy_type'] or 'Policy'} renewal {r['days_to_exp']}d out — no activity",
            f"{r['client_name']} · {r['policy_uid']} · expires {r['expiration_date']}"
        ))
    return findings
```

The reconciliation function:
```python
def _reconcile(conn, scan_id, existing, new_findings):
    seen_keys = set()
    for (rule_key, category, severity, client_id, policy_id, title, details) in new_findings:
        key = (rule_key, client_id, policy_id)
        seen_keys.add(key)
        if key in existing:
            # Still active — update scan_id
            conn.execute("UPDATE anomalies SET scan_id=?, title=?, details=?, severity=? WHERE id=?",
                        (scan_id, title, details, severity, existing[key]["id"]))
        else:
            # New finding
            conn.execute("""INSERT INTO anomalies (rule_key, category, severity, client_id, policy_id,
                           title, details, status, detected_at, scan_id)
                           VALUES (?,?,?,?,?,?,?,'new',DATETIME('now'),?)""",
                        (rule_key, category, severity, client_id, policy_id, title, details, scan_id))

    # Auto-resolve stale findings
    for key, row in existing.items():
        if key not in seen_keys:
            conn.execute("UPDATE anomalies SET status='resolved', resolved_at=DATETIME('now') WHERE id=?",
                        (row["id"],))

    conn.commit()
    return len(seen_keys)
```

The full module should implement ALL 10 rules with complete SQL queries. No placeholders.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/anomaly_engine.py
git commit -m "feat: anomaly detection engine with 10 rules and reconciliation"
```

---

### Task 4: Wire startup scan into db.py

**Files:**
- Modify: `src/policydb/db.py` — add `scan_anomalies()` call and anomaly purge

- [ ] **Step 1: Add anomaly scan call**

In `src/policydb/db.py`, add before `_purge_old_logs(conn)` (~line 1719):

```python
    # Anomaly scan (runs every startup, idempotent)
    try:
        from policydb.anomaly_engine import scan_anomalies
        scan_anomalies(conn)
    except Exception as e:
        logger.warning("Anomaly scan failed (non-fatal): %s", e)
```

- [ ] **Step 2: Add anomaly purge to `_purge_old_logs()`**

In the `_purge_old_logs()` function, add after the audit_log purge:

```python
    # Purge old resolved anomalies
    try:
        n = conn.execute(
            "DELETE FROM anomalies WHERE status = 'resolved' AND resolved_at < ?",
            (cutoff,)
        ).rowcount
        if n:
            logger.info("Purged %d old resolved anomalies", n)
    except Exception:
        pass
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/db.py
git commit -m "feat: wire anomaly scan into server startup and log purge"
```

---

### Task 5: Routes — Anomaly endpoints (acknowledge, refresh, counts)

**Files:**
- Create: `src/policydb/web/routes/anomalies.py`
- Modify: `src/policydb/web/app.py` — register router

- [ ] **Step 1: Create anomaly routes**

Create `src/policydb/web/routes/anomalies.py`:

```python
"""Anomaly detection routes — acknowledge, refresh, list."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from policydb.anomaly_engine import (
    acknowledge_anomaly,
    get_all_active_anomalies,
    get_anomaly_counts,
    scan_anomalies,
)
from policydb.web.app import get_db, templates

router = APIRouter()


@router.post("/anomalies/{anomaly_id}/acknowledge")
def ack_anomaly(anomaly_id: int, conn=Depends(get_db)):
    acknowledge_anomaly(conn, anomaly_id)
    return {"ok": True}


@router.post("/anomalies/refresh", response_class=HTMLResponse)
def refresh_anomalies(request: Request, conn=Depends(get_db)):
    scan_anomalies(conn)
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
    return templates.TemplateResponse("action_center/_anomalies_widget.html", {
        "request": request,
        "anomaly_counts": counts,
        "anomalies": anomalies,
    })


@router.get("/anomalies/widget", response_class=HTMLResponse)
def anomalies_widget(request: Request, conn=Depends(get_db)):
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
    return templates.TemplateResponse("action_center/_anomalies_widget.html", {
        "request": request,
        "anomaly_counts": counts,
        "anomalies": anomalies,
    })
```

- [ ] **Step 2: Register router in app.py**

Add import and `app.include_router()` for anomalies routes.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/anomalies.py src/policydb/web/app.py
git commit -m "feat: add anomaly routes (acknowledge, refresh, widget)"
```

---

### Task 6: Templates — Action Center anomaly widget + sidebar integration

**Files:**
- Create: `src/policydb/web/templates/action_center/_anomalies_widget.html`
- Modify: `src/policydb/web/templates/action_center/_sidebar.html`
- Modify: `src/policydb/web/routes/action_center.py` — add anomaly counts to sidebar context

- [ ] **Step 1: Create anomalies widget template**

Create `src/policydb/web/templates/action_center/_anomalies_widget.html`:

```html
{# Anomalies widget — used in sidebar and as refresh target #}
<div id="anomalies-widget">
  {% set total = (anomaly_counts.falling_behind or 0) + (anomaly_counts.neglected or 0) + (anomaly_counts.workload or 0) + (anomaly_counts.mismatch or 0) %}
  {% if total > 0 %}
  <div class="mb-5">
    <div class="flex items-center justify-between mb-2">
      <p class="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">Anomalies</p>
      <button hx-post="/anomalies/refresh" hx-target="#anomalies-widget" hx-swap="outerHTML"
              class="text-[9px] text-gray-400 hover:text-marsh transition-colors no-print" title="Re-scan">&#8635;</button>
    </div>

    <div class="space-y-1">
      {% if anomaly_counts.falling_behind %}
      <div class="flex items-center gap-2 text-[11px]">
        <span class="w-2 h-2 rounded-full bg-red-500 flex-shrink-0"></span>
        <span class="text-red-700 font-medium">{{ anomaly_counts.falling_behind }} falling behind</span>
      </div>
      {% endif %}
      {% if anomaly_counts.neglected %}
      <div class="flex items-center gap-2 text-[11px]">
        <span class="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0"></span>
        <span class="text-amber-700 font-medium">{{ anomaly_counts.neglected }} neglected</span>
      </div>
      {% endif %}
      {% if anomaly_counts.workload %}
      <div class="flex items-center gap-2 text-[11px]">
        <span class="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0"></span>
        <span class="text-blue-700 font-medium">{{ anomaly_counts.workload }} workload</span>
      </div>
      {% endif %}
      {% if anomaly_counts.mismatch %}
      <div class="flex items-center gap-2 text-[11px]">
        <span class="w-2 h-2 rounded-full bg-purple-500 flex-shrink-0"></span>
        <span class="text-purple-700 font-medium">{{ anomaly_counts.mismatch }} mismatch{{ 'es' if anomaly_counts.mismatch != 1 }}</span>
      </div>
      {% endif %}
    </div>

    {# Expandable detail list #}
    <details class="mt-2">
      <summary class="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">Show details</summary>
      <div class="mt-2 space-y-1.5 max-h-[300px] overflow-y-auto">
        {% for a in anomalies %}
        <div class="p-2 bg-white rounded border {% if a.severity == 'alert' %}border-red-200{% else %}border-amber-200{% endif %} text-[11px]">
          <div class="flex items-start gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full mt-1 flex-shrink-0 {% if a.severity == 'alert' %}bg-red-500{% else %}bg-amber-500{% endif %}"></span>
            <div class="flex-1 min-w-0">
              <div class="font-medium text-gray-800 truncate">{{ a.title }}</div>
              {% if a.details %}
              <div class="text-gray-500 truncate">{{ a.details }}</div>
              {% endif %}
            </div>
            <button hx-post="/anomalies/{{ a.id }}/acknowledge"
                    hx-target="#anomalies-widget"
                    hx-swap="outerHTML"
                    hx-get="/anomalies/widget"
                    hx-trigger="click"
                    class="text-[9px] text-gray-400 hover:text-gray-600 flex-shrink-0" title="Acknowledge">&#10003;</button>
          </div>
        </div>
        {% endfor %}
      </div>
    </details>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 2: Add widget to sidebar**

In `_sidebar.html`, add after the Portfolio Health include (line ~27):

```html
{# ── Anomalies ── #}
{% include "action_center/_anomalies_widget.html" %}
```

- [ ] **Step 3: Add anomaly data to sidebar context**

In `action_center.py`, update `_sidebar_ctx()` to include anomaly counts:

```python
    from policydb.anomaly_engine import get_anomaly_counts, get_all_active_anomalies
    anomaly_counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
```

Add to the return dict: `"anomaly_counts": anomaly_counts, "anomalies": anomalies,`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/action_center/_anomalies_widget.html src/policydb/web/templates/action_center/_sidebar.html src/policydb/web/routes/action_center.py
git commit -m "feat: add anomalies widget to Action Center sidebar"
```

---

### Task 7: Templates — Client overview anomaly card + inline badges

**Files:**
- Modify: `src/policydb/web/templates/clients/_tab_overview.html` — add anomaly card
- Modify: `src/policydb/web/routes/clients.py` — add anomaly data to overview context

- [ ] **Step 1: Add anomaly data to client overview context**

In `clients.py`, in the `client_tab_overview()` function, add after the `open_issues` query:

```python
        from policydb.anomaly_engine import get_anomalies_for_client
        "client_anomalies": get_anomalies_for_client(conn, client_id),
```

- [ ] **Step 2: Add anomaly card to overview template**

In `_tab_overview.html`, add after the Issues section (before `<!-- Activity -->`):

```html
<!-- Anomalies -->
{% if client_anomalies is defined and client_anomalies|length > 0 %}
<div class="card mt-4">
  <div class="px-5 py-3 border-b border-gray-100">
    <h2 class="font-semibold text-gray-900">Anomalies <span class="text-sm font-normal text-gray-400">({{ client_anomalies|length }})</span></h2>
  </div>
  <div class="divide-y divide-gray-50">
    {% for a in client_anomalies %}
    <div class="flex items-center gap-3 px-5 py-3">
      <span class="w-2.5 h-2.5 rounded-full flex-shrink-0 {% if a.severity == 'alert' %}bg-red-500{% else %}bg-amber-500{% endif %}"></span>
      <div class="flex-1 min-w-0">
        <div class="text-sm text-gray-900">{{ a.title }}</div>
        {% if a.details %}
        <div class="text-xs text-gray-500 mt-0.5">{{ a.details }}</div>
        {% endif %}
      </div>
      <span class="text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap
        {% if a.category == 'falling_behind' %}bg-red-50 text-red-700
        {% elif a.category == 'neglected' %}bg-amber-50 text-amber-700
        {% elif a.category == 'workload' %}bg-blue-50 text-blue-700
        {% else %}bg-purple-50 text-purple-700{% endif %}">
        {{ a.category | replace('_', ' ') | title }}
      </span>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_tab_overview.html src/policydb/web/routes/clients.py
git commit -m "feat: add anomaly card to client overview tab"
```

---

### Task 8: Review Gate — Guided review with condition checks

**Files:**
- Modify: `src/policydb/web/routes/review.py` — add review gate endpoint
- Create: `src/policydb/web/templates/review/_review_gate.html` — condition checklist
- Modify: `src/policydb/queries.py` — update `mark_reviewed` to accept override_reason

- [ ] **Step 1: Add `get_review_gate_status()` to anomaly_engine.py**

Add to `anomaly_engine.py`:

```python
def get_review_gate_status(conn, record_type: str, record_id, thresholds: dict | None = None) -> dict:
    """Evaluate review gate conditions. Returns dict with pass/fail per condition."""
    if thresholds is None:
        thresholds = cfg.get("anomaly_thresholds", {})

    result = {"all_pass": True, "conditions": []}

    # 1. Data health score
    min_score = thresholds.get("review_min_health_score", 70)
    if record_type == "policy":
        from policydb.data_health import score_policy_health
        policy = conn.execute("SELECT * FROM policies WHERE policy_uid = ?", (record_id,)).fetchone()
        if policy:
            health = score_policy_health(dict(policy))
            score = health.get("completeness", 0)
        else:
            score = 0
    else:
        from policydb.data_health import score_client_health
        client = conn.execute("SELECT * FROM clients WHERE id = ?", (record_id,)).fetchone()
        if client:
            health = score_client_health(conn, dict(client))
            score = health.get("completeness", 0)
        else:
            score = 0
    passed = score >= min_score
    result["conditions"].append({"name": "Data Health", "passed": passed, "detail": f"{score:.0f}% (min {min_score}%)"})
    if not passed:
        result["all_pass"] = False

    # 2. Recent activity
    activity_days = thresholds.get("review_activity_window_days", 30)
    if record_type == "policy":
        pid = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (record_id,)).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE policy_id = ? AND activity_date >= date('now', ?)",
            (pid["id"] if pid else 0, f"-{activity_days} days")
        ).fetchone()[0] if pid else 0
    else:
        count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE client_id = ? AND activity_date >= date('now', ?)",
            (record_id, f"-{activity_days} days")
        ).fetchone()[0]
    passed = count > 0
    result["conditions"].append({"name": "Recent Activity", "passed": passed, "detail": f"{count} in last {activity_days}d"})
    if not passed:
        result["all_pass"] = False

    # 3. Open anomalies
    if record_type == "policy":
        pid = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (record_id,)).fetchone()
        anom_count = conn.execute(
            "SELECT COUNT(*) FROM anomalies WHERE policy_id = ? AND status = 'new'",
            (pid["id"] if pid else 0,)
        ).fetchone()[0] if pid else 0
    else:
        anom_count = conn.execute(
            "SELECT COUNT(*) FROM anomalies WHERE client_id = ? AND status = 'new'",
            (record_id,)
        ).fetchone()[0]
    passed = anom_count == 0
    result["conditions"].append({"name": "Open Anomalies", "passed": passed, "detail": f"{anom_count} unacknowledged"})
    if not passed:
        result["all_pass"] = False

    # 4. Overdue follow-ups
    if record_type == "policy":
        pid = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (record_id,)).fetchone()
        overdue = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date < date('now')",
            (pid["id"] if pid else 0,)
        ).fetchone()[0] if pid else 0
    else:
        overdue = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE client_id = ? AND follow_up_done = 0 AND follow_up_date < date('now')",
            (record_id,)
        ).fetchone()[0]
    passed = overdue == 0
    result["conditions"].append({"name": "Overdue Follow-ups", "passed": passed, "detail": f"{overdue} overdue"})
    if not passed:
        result["all_pass"] = False

    return result
```

- [ ] **Step 2: Add review gate endpoint to review.py**

Add to `review.py`:

```python
@router.get("/review/gate/{record_type}/{record_id}", response_class=HTMLResponse)
def review_gate(request: Request, record_type: str, record_id: str, conn=Depends(get_db)):
    """Show review gate conditions before marking reviewed."""
    from policydb.anomaly_engine import get_review_gate_status
    gate = get_review_gate_status(conn, record_type, record_id)
    return templates.TemplateResponse("review/_review_gate.html", {
        "request": request,
        "gate": gate,
        "record_type": record_type,
        "record_id": record_id,
    })


@router.post("/review/mark/{record_type}/{record_id}")
def mark_reviewed_gated(
    request: Request, record_type: str, record_id: str,
    override_reason: str = Form(""),
    review_cycle: str = Form(""),
    conn=Depends(get_db),
):
    """Mark reviewed with optional override reason."""
    if override_reason:
        table = "policies" if record_type == "policy" else "programs" if record_type == "program" else "clients"
        id_col = "policy_uid" if record_type == "policy" else "id"
        conn.execute(f"UPDATE {table} SET review_override_reason = ? WHERE {id_col} = ?",
                    (override_reason, record_id))
    mark_reviewed(conn, record_type, record_id, review_cycle or None)
    conn.commit()
    return RedirectResponse(request.headers.get("referer", "/review"), status_code=303)
```

- [ ] **Step 3: Create review gate template**

Create `src/policydb/web/templates/review/_review_gate.html`:

```html
{# Review gate condition checklist — returned as HTMX partial #}
<div class="p-4 bg-white rounded-lg border border-gray-200 shadow-sm max-w-sm">
  <h3 class="text-sm font-semibold text-gray-900 mb-3">Review Checklist</h3>
  <div class="space-y-2 mb-4">
    {% for cond in gate.conditions %}
    <div class="flex items-center gap-2 text-sm">
      {% if cond.passed %}
        <span class="text-green-600">&#10003;</span>
      {% else %}
        <span class="text-red-500">&#10007;</span>
      {% endif %}
      <span class="{% if cond.passed %}text-gray-700{% else %}text-red-700 font-medium{% endif %}">{{ cond.name }}</span>
      <span class="text-xs text-gray-400 ml-auto">{{ cond.detail }}</span>
    </div>
    {% endfor %}
  </div>

  {% if gate.all_pass %}
    <form method="post" action="/review/mark/{{ record_type }}/{{ record_id }}">
      <button type="submit" class="w-full text-sm bg-green-600 text-white rounded px-4 py-2 hover:bg-green-700 transition-colors">
        Mark Reviewed
      </button>
    </form>
  {% else %}
    <form method="post" action="/review/mark/{{ record_type }}/{{ record_id }}" class="space-y-2">
      <div>
        <label class="text-xs text-gray-500 block mb-1">Override reason (required)</label>
        <input type="text" name="override_reason" required placeholder="Why is this OK to review now?"
               class="w-full rounded border-gray-300 text-sm px-3 py-1.5 focus:ring-marsh">
      </div>
      <div class="flex gap-2">
        <button type="submit" class="flex-1 text-sm bg-amber-500 text-white rounded px-4 py-2 hover:bg-amber-600 transition-colors">
          Override & Review
        </button>
      </div>
    </form>
  {% endif %}
</div>
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/anomaly_engine.py src/policydb/web/routes/review.py src/policydb/web/templates/review/_review_gate.html
git commit -m "feat: add review gate with condition checks and override support"
```

---

### Task 9: QA — Server startup, scan, UI verification

- [ ] **Step 1: Kill existing server, restart fresh**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; sleep 1; pdb serve &
```

Check logs for: `Migration 109: created anomalies table`, `Migration 110: added review_override_reason`, `Anomaly scan complete: N active findings`

- [ ] **Step 2: Verify Action Center sidebar**

Navigate to `http://127.0.0.1:8000/action-center`. Verify anomalies widget appears in sidebar if any findings exist. Click "Show details" to expand. Test acknowledge button.

- [ ] **Step 3: Verify client overview**

Navigate to a client page. Check for anomaly card in overview tab (if that client has findings).

- [ ] **Step 4: Verify Settings**

Navigate to `/settings` and find the Anomaly Detection thresholds section. Change a value, save, verify it persists.

- [ ] **Step 5: Test refresh**

Click the refresh button on the anomalies widget. Verify it re-runs the scan and updates counts.

- [ ] **Step 6: Verify review gate**

Navigate to `/review`. Try to mark a policy as reviewed. Verify the gate checklist appears.

- [ ] **Step 7: Fix any issues found during QA**

- [ ] **Step 8: Final commit**

```bash
git add -A && git commit -m "fix: QA fixes for anomaly detection system"
```

- [ ] **Step 9: Push, PR, merge, pull**

```bash
git push -u origin HEAD
gh pr create --title "feat: Anomaly & drift detection system" --body "..."
gh pr merge --merge
git checkout main && git pull
```
