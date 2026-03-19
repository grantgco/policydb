# Follow-Up Workflow Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add disposition tracking with auto-scheduling, follow-up threading with `COR-{id}` correspondence tags, and thread history visibility to the follow-up system.

**Architecture:** Two new columns on `activity_log` (`disposition`, `thread_id`). Dispositions are config-managed objects with `{label, default_days}`. Threading uses lazy creation — `thread_id` is set on first re-diary, pointing to the chain anchor. Thread stats computed at Python layer, not SQL window functions.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-18-followup-workflow-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/059_followup_threading.sql` | Schema: disposition + thread_id columns + index |
| Create | `tests/test_followup_threading.py` | All tests for this feature |
| Create | `src/policydb/web/templates/settings/_disposition_card.html` | Settings UI for disposition object list |
| Create | `src/policydb/web/templates/policies/_correspondence_threads.html` | Thread history section on policy edit page |
| Modify | `src/policydb/config.py:87` | Add `follow_up_dispositions` to `_DEFAULTS` |
| Modify | `src/policydb/web/routes/settings.py:115-145` | New disposition CRUD endpoints |
| Modify | `src/policydb/web/templates/settings.html` | Include disposition card |
| Modify | `src/policydb/web/routes/activities.py:128-189,311-401` | Disposition + threading in complete/followup endpoints |
| Modify | `src/policydb/queries.py:531-570` | Add disposition, thread_id to get_all_followups query |
| Modify | `src/policydb/web/templates/followups/_row.html:132-235` | Unified form, disposition dropdown, COR tag, thread summary |
| Modify | `src/policydb/web/templates/activities/_activity_row.html` | Disposition badge display |
| Modify | `src/policydb/web/routes/policies.py` | Load thread data for policy edit page |
| Modify | `src/policydb/web/templates/policies/edit.html:119` | Include correspondence threads section |
| Modify | `src/policydb/email_templates.py` | Add disposition + thread_ref tokens |
| Modify | `src/policydb/web/routes/dashboard.py` | Pass dispositions to followup template context |

---

### Task 1: Migration + Schema

**Files:**
- Create: `src/policydb/migrations/059_followup_threading.sql`
- Create: `tests/test_followup_threading.py`
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 059_followup_threading.sql
ALTER TABLE activity_log ADD COLUMN disposition TEXT;
ALTER TABLE activity_log ADD COLUMN thread_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_activity_thread ON activity_log(thread_id);
```

Write to `src/policydb/migrations/059_followup_threading.sql`.

- [ ] **Step 2: Register migration in db.py**

In `src/policydb/db.py`, add migration 59 to `_KNOWN_MIGRATIONS` and add the `if 59 not in applied` execution block following the existing pattern (same as how migration 58 was added).

- [ ] **Step 3: Write tests**

```python
# tests/test_followup_threading.py
"""Tests for follow-up disposition tracking and threading."""

import sqlite3
import pytest
from datetime import date, timedelta
from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_disposition_column_exists(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()]
    assert "disposition" in cols
    assert "thread_id" in cols
    conn.close()


def test_thread_id_index_exists(tmp_db):
    conn = get_connection(tmp_db)
    indices = [r[1] for r in conn.execute("PRAGMA index_list(activity_log)").fetchall()]
    assert "idx_activity_thread" in indices
    conn.close()


def test_thread_grouping(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Thread Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Create 3 activities in a thread
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Initial RFI', 1, 'Sent RFI')",
        (date.today().isoformat(), cid),
    )
    a1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Set thread_id to self (anchor)
    conn.execute("UPDATE activity_log SET thread_id = ? WHERE id = ?", (a1, a1))
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Follow-up: Initial RFI', ?, 'Left VM')",
        (date.today().isoformat(), cid, a1),
    )
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Follow-up: Initial RFI', ?, 'Connected')",
        (date.today().isoformat(), cid, a1),
    )
    conn.commit()
    # Query the thread
    chain = conn.execute(
        "SELECT * FROM activity_log WHERE thread_id = ? ORDER BY id", (a1,)
    ).fetchall()
    assert len(chain) == 3
    assert chain[0]["disposition"] == "Sent RFI"
    assert chain[1]["disposition"] == "Left VM"
    assert chain[2]["disposition"] == "Connected"
    conn.close()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_followup_threading.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/migrations/059_followup_threading.sql src/policydb/db.py tests/test_followup_threading.py
git commit -m "feat: add disposition and thread_id columns to activity_log (migration 059)"
```

---

### Task 2: Configuration — Disposition Defaults

**Files:**
- Modify: `src/policydb/config.py`

- [ ] **Step 1: Add `follow_up_dispositions` to `_DEFAULTS`**

In `src/policydb/config.py`, find the `_DEFAULTS` dict. After the `"activity_types"` list (around line 97), add:

```python
    "follow_up_dispositions": [
        {"label": "Left VM", "default_days": 3},
        {"label": "No Answer", "default_days": 1},
        {"label": "Sent Email", "default_days": 7},
        {"label": "Sent RFI", "default_days": 7},
        {"label": "Waiting on Colleague", "default_days": 5},
        {"label": "Waiting on Client", "default_days": 7},
        {"label": "Waiting on Carrier", "default_days": 7},
        {"label": "Connected", "default_days": 0},
        {"label": "Received Response", "default_days": 0},
        {"label": "Meeting Scheduled", "default_days": 0},
        {"label": "Escalated", "default_days": 3},
    ],
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/config.py
git commit -m "feat: add follow_up_dispositions config defaults"
```

---

### Task 3: Settings UI — Disposition Management

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Create: `src/policydb/web/templates/settings/_disposition_card.html`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Add disposition CRUD endpoints to settings.py**

Add after the existing `list_reorder` function (around line 145 of `src/policydb/web/routes/settings.py`):

```python
@router.post("/dispositions/add")
def disposition_add(request: Request, label: str = Form(...), default_days: int = Form(0)):
    """Add a new disposition to follow_up_dispositions."""
    lst = cfg.get("follow_up_dispositions", [])
    if any(d["label"] == label for d in lst):
        return RedirectResponse("/settings", status_code=303)
    lst.append({"label": label, "default_days": max(0, default_days)})
    cfg.set("follow_up_dispositions", lst)
    cfg.save_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/dispositions/remove")
def disposition_remove(request: Request, label: str = Form(...)):
    """Remove a disposition by label."""
    lst = cfg.get("follow_up_dispositions", [])
    lst = [d for d in lst if d["label"] != label]
    cfg.set("follow_up_dispositions", lst)
    cfg.save_config()
    return RedirectResponse("/settings", status_code=303)


@router.post("/dispositions/reorder")
def disposition_reorder(request: Request, label: str = Form(...), direction: str = Form(...)):
    """Move a disposition up or down."""
    lst = cfg.get("follow_up_dispositions", [])
    idx = next((i for i, d in enumerate(lst) if d["label"] == label), None)
    if idx is None:
        return RedirectResponse("/settings", status_code=303)
    if direction == "up" and idx > 0:
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
    elif direction == "down" and idx < len(lst) - 1:
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
    cfg.set("follow_up_dispositions", lst)
    cfg.save_config()
    return RedirectResponse("/settings", status_code=303)


@router.patch("/dispositions/update")
async def disposition_update(request: Request):
    """Update default_days for a disposition."""
    body = await request.json()
    label = body.get("label", "")
    default_days = int(body.get("default_days", 0))
    lst = cfg.get("follow_up_dispositions", [])
    for d in lst:
        if d["label"] == label:
            d["default_days"] = max(0, default_days)
            break
    cfg.set("follow_up_dispositions", lst)
    cfg.save_config()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Create `_disposition_card.html` template partial**

Write to `src/policydb/web/templates/settings/_disposition_card.html`:

```html
{# Disposition config card — object list with {label, default_days} #}
<div class="card mb-4 overflow-hidden">
  <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-100">
    <h3 class="text-xs font-bold text-gray-600 uppercase tracking-wide">Follow-Up Dispositions</h3>
    <p class="text-[10px] text-gray-400 mt-0.5">Outcome labels for completed follow-ups. Default days auto-fills the next follow-up date on re-diary.</p>
  </div>
  <ul class="divide-y divide-gray-100">
    {% for d in dispositions %}
    <li class="px-4 py-2 flex items-center gap-2 text-sm hover:bg-gray-50 transition-colors">
      <div class="flex gap-1 shrink-0">
        <form method="post" action="/settings/dispositions/reorder" class="inline">
          <input type="hidden" name="label" value="{{ d.label }}">
          <input type="hidden" name="direction" value="up">
          <button type="submit" class="text-gray-300 hover:text-gray-500 text-xs"{% if loop.first %} disabled{% endif %}>&#9650;</button>
        </form>
        <form method="post" action="/settings/dispositions/reorder" class="inline">
          <input type="hidden" name="label" value="{{ d.label }}">
          <input type="hidden" name="direction" value="down">
          <button type="submit" class="text-gray-300 hover:text-gray-500 text-xs"{% if loop.last %} disabled{% endif %}>&#9660;</button>
        </form>
      </div>
      <span class="flex-1 text-gray-700">{{ d.label }}</span>
      <div class="flex items-center gap-1 shrink-0">
        <label class="text-[10px] text-gray-400">days:</label>
        <input type="number" min="0" max="90" value="{{ d.default_days }}"
          class="w-12 text-xs text-center border border-gray-200 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-marsh"
          onchange="fetch('/settings/dispositions/update',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:'{{ d.label }}',default_days:parseInt(this.value)||0})})">
      </div>
      <form method="post" action="/settings/dispositions/remove" class="inline shrink-0">
        <input type="hidden" name="label" value="{{ d.label }}">
        <button type="submit" class="text-red-300 hover:text-red-500 text-xs ml-2">&#10005;</button>
      </form>
    </li>
    {% endfor %}
  </ul>
  <div class="px-4 py-2.5 bg-gray-50 border-t border-gray-100">
    <form method="post" action="/settings/dispositions/add" class="flex items-center gap-2">
      <input type="text" name="label" placeholder="New disposition..." required
        class="flex-1 border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-marsh">
      <input type="number" name="default_days" value="0" min="0" max="90"
        class="w-14 border border-gray-200 rounded px-2 py-1 text-xs text-center focus:outline-none focus:ring-1 focus:ring-marsh"
        placeholder="days">
      <button type="submit" class="text-xs bg-marsh text-white px-3 py-1 rounded hover:bg-marsh-light transition-colors">Add</button>
    </form>
  </div>
</div>
```

- [ ] **Step 3: Include disposition card in settings.html**

In `src/policydb/web/templates/settings.html`, find where other `_list_card.html` includes are rendered (likely in a grid or column layout). Add:

```html
{% include 'settings/_disposition_card.html' %}
```

Also ensure `dispositions` is passed in the template context.

- [ ] **Step 4: Pass dispositions to settings template context**

In `src/policydb/web/routes/settings.py`, find the settings GET handler. Add to the template context:

```python
"dispositions": cfg.get("follow_up_dispositions", []),
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings/_disposition_card.html src/policydb/web/templates/settings.html
git commit -m "feat: settings UI for follow-up dispositions"
```

---

### Task 4: Backend — Disposition + Threading in Activity Endpoints

**Files:**
- Modify: `src/policydb/web/routes/activities.py:128-189,311-401`

- [ ] **Step 1: Add `disposition` parameter to `activity_complete`**

In `src/policydb/web/routes/activities.py`, function `activity_complete` (line 128):

Add parameter: `disposition: str = Form("")`

After the `follow_up_done=1` UPDATE (line 143), add:

```python
    # Save disposition
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition=? WHERE id=?",
            (disposition.strip(), activity_id),
        )
```

- [ ] **Step 2: Add `disposition` parameter and threading to `activity_followup`**

In function `activity_followup` (line 311):

Add parameter: `disposition: str = Form("")`

After marking original done (line 332), add:

```python
    # Save disposition on the original activity
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition=? WHERE id=?",
            (disposition.strip(), activity_id),
        )

    # Threading: determine thread_id for the new activity
    _thread_id = original.get("thread_id")
    if _thread_id is None:
        # Lazy thread creation: set parent's thread_id to itself
        _thread_id = original["id"]
        conn.execute(
            "UPDATE activity_log SET thread_id=? WHERE id=?",
            (_thread_id, activity_id),
        )
```

Then in the INSERT for the new activity (line 341-352), add `thread_id` to the column list and `_thread_id` to the values:

```python
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person,
            subject, details, follow_up_date, account_exec, duration_hours, thread_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), original["client_id"],
         original.get("policy_id") or None,
         original.get("activity_type", "Call"),
         original.get("contact_person") or None,
         subject, notes or None,
         new_follow_up_date or None, account_exec, dur, _thread_id),
    )
```

- [ ] **Step 3: Write threading test**

Add to `tests/test_followup_threading.py`:

```python
def test_lazy_thread_creation(tmp_db):
    """Re-diarying a standalone activity should create a thread lazily."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Lazy Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Create standalone activity (no thread_id)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, follow_up_date) VALUES (?, ?, 'Call', 'Check in', '2025-01-15')",
        (date.today().isoformat(), cid),
    )
    a1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    # Verify no thread yet
    row = conn.execute("SELECT thread_id FROM activity_log WHERE id=?", (a1,)).fetchone()
    assert row["thread_id"] is None
    # Simulate re-diary: set parent thread_id, create child
    conn.execute("UPDATE activity_log SET thread_id=?, follow_up_done=1, disposition='Left VM' WHERE id=?", (a1, a1))
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, follow_up_date) VALUES (?, ?, 'Call', 'Follow-up: Check in', ?, '2025-01-18')",
        (date.today().isoformat(), cid, a1),
    )
    conn.commit()
    # Both should be in the thread
    chain = conn.execute("SELECT * FROM activity_log WHERE thread_id=? ORDER BY id", (a1,)).fetchall()
    assert len(chain) == 2
    assert chain[0]["disposition"] == "Left VM"
    conn.close()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_followup_threading.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/activities.py tests/test_followup_threading.py
git commit -m "feat: disposition tracking and threading in activity endpoints"
```

---

### Task 5: Queries — Add Disposition + Thread to Follow-Up Queries

**Files:**
- Modify: `src/policydb/queries.py:531-570`

- [ ] **Step 1: Add columns to `get_all_followups` activity source query**

In `src/policydb/queries.py`, find the `get_all_followups` function (line 531). In the activity source SELECT (lines 536-551), add `a.disposition, a.thread_id` to the column list:

```sql
SELECT 'activity' AS source,
       a.id, a.subject, a.follow_up_date, a.activity_type,
       a.contact_person, a.disposition, a.thread_id,
       ...
```

For the policy source and client source UNION queries, add `NULL AS disposition, NULL AS thread_id` to keep column counts aligned.

- [ ] **Step 2: Add thread stats computation after query**

At the end of `get_all_followups()`, after the overdue/upcoming split (around line 633), add thread stats computation:

```python
    # Compute thread stats for rows with thread_id
    all_rows = overdue + upcoming
    thread_ids = {r["thread_id"] for r in all_rows if r.get("thread_id")}
    if thread_ids:
        placeholders = ",".join("?" * len(thread_ids))
        stats = conn.execute(f"""
            SELECT thread_id, COUNT(*) AS thread_total,
                   MAX(activity_date) AS latest_date
            FROM activity_log WHERE thread_id IN ({placeholders})
            GROUP BY thread_id
        """, list(thread_ids)).fetchall()
        stats_map = {s["thread_id"]: dict(s) for s in stats}

        # Get previous disposition per thread (the second-to-last activity)
        prev_map = {}
        for tid in thread_ids:
            prev = conn.execute("""
                SELECT disposition, activity_date FROM activity_log
                WHERE thread_id = ? ORDER BY activity_date DESC, id DESC LIMIT 1 OFFSET 1
            """, (tid,)).fetchone()
            if prev:
                prev_map[tid] = dict(prev)

        for r in all_rows:
            tid = r.get("thread_id")
            if tid and tid in stats_map:
                r["thread_total"] = stats_map[tid]["thread_total"]
                # Attempt number: count activities in thread up to this one
                r["thread_attempt_num"] = conn.execute(
                    "SELECT COUNT(*) FROM activity_log WHERE thread_id = ? AND id <= ?",
                    (tid, r["id"]),
                ).fetchone()[0]
                if tid in prev_map:
                    r["prev_disposition"] = prev_map[tid].get("disposition")
                    prev_date = prev_map[tid].get("activity_date")
                    if prev_date:
                        try:
                            r["prev_days_ago"] = (date.today() - date.fromisoformat(prev_date)).days
                        except (ValueError, TypeError):
                            r["prev_days_ago"] = None
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: add disposition and thread stats to follow-up queries"
```

---

### Task 6: Follow-Up Row UI — Unified Form + Thread Summary

**Files:**
- Modify: `src/policydb/web/templates/followups/_row.html`
- Modify: `src/policydb/web/routes/activities.py` (pass dispositions to template context)
- Modify: `src/policydb/web/routes/dashboard.py` (pass dispositions to followup context)

- [ ] **Step 1: Pass dispositions list to template contexts**

In every route that renders follow-up rows (search for `followups/_row.html` usage and `_results.html` includes), add `"dispositions": cfg.get("follow_up_dispositions", [])` to the template context.

Key files:
- `src/policydb/web/routes/activities.py` — the `followups_page` handler and `activity_followup` response
- `src/policydb/web/routes/dashboard.py` — dashboard renders follow-up rows

- [ ] **Step 2: Add COR tag and disposition badge to follow-up row display**

In `src/policydb/web/templates/followups/_row.html`, find the area where `A-{id}` is displayed (or the subject line area). Add:

```html
{% if r.disposition %}
<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ r.disposition }}</span>
{% endif %}
{% if r.thread_id %}
<span class="text-[10px] text-blue-400 font-mono">COR-{{ r.thread_id }}</span>
{% endif %}
```

Add inline thread summary below the subject when thread exists:

```html
{% if r.thread_id and r.get('thread_total') %}
<p class="text-[10px] text-gray-400 mt-0.5">
  COR-{{ r.thread_id }} &middot; {{ r.thread_attempt_num | default('?') }}{{ 'st' if r.thread_attempt_num == 1 else ('nd' if r.thread_attempt_num == 2 else ('rd' if r.thread_attempt_num == 3 else 'th')) }} attempt
  {% if r.prev_disposition %}&middot; last: {{ r.prev_disposition }}{% if r.prev_days_ago is not none %} {{ r.prev_days_ago }}d ago{% endif %}{% endif %}
</p>
{% endif %}
```

- [ ] **Step 3: Update copy-ref-tag button to include COR tag**

Find the `copyRefTag` button (line 132). Update the `onclick` to include the COR tag when a thread exists:

```html
<button type="button" onclick="copyRefTag(this, '{% if r.thread_id %}COR-{{ r.thread_id }} | {% endif %}{{ build_ref_tag(...) }}')"
```

- [ ] **Step 4: Replace two form rows with unified disposition form**

Replace the two hidden `<tr>` form rows (`followup-form-row-*` at lines 137-197 and `complete-form-row-*` at lines 198-235) with a single unified form:

```html
<tr id="disposition-form-row-{{ row_id }}" class="hidden">
  <td colspan="8" class="px-4 py-3 bg-amber-50/50 border-l-4 border-amber-400">
    <form hx-post="/activities/{{ r.id }}/followup"
          hx-target="#{{ row_id }}"
          hx-swap="outerHTML"
          hx-on::before-request="var f=document.getElementById('disposition-form-row-{{ row_id }}');if(f)f.remove();"
          class="space-y-2">
      <input type="hidden" name="context" value="followup_table">

      <div class="flex items-center gap-3 flex-wrap">
        <div>
          <label class="text-[10px] text-gray-500 block">Disposition</label>
          <select name="disposition" class="text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-marsh"
                  onchange="dispositionChanged(this, '{{ row_id }}')">
            <option value="">— Select —</option>
            {% for d in dispositions %}
            <option value="{{ d.label }}" data-days="{{ d.default_days }}">{{ d.label }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="text-[10px] text-gray-500 block">Hours</label>
          <input type="number" name="duration_hours" step="0.1" min="0" placeholder="0"
            class="w-16 text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-marsh">
        </div>
      </div>

      <div>
        <label class="text-[10px] text-gray-500 block">Note</label>
        <input type="text" name="notes" placeholder="Details..."
          class="w-full text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-marsh">
      </div>

      <div id="rediary-section-{{ row_id }}" class="hidden">
        <label class="text-[10px] text-gray-500 block">Next Follow-Up</label>
        <div class="flex items-center gap-2">
          <input type="date" name="new_follow_up_date" id="rediary-date-{{ row_id }}"
            class="text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-marsh">
          <button type="button" onclick="setRediaryDays('{{ row_id }}', 1)" class="text-[10px] text-gray-400 hover:text-marsh border border-gray-200 rounded px-1.5 py-0.5">+1d</button>
          <button type="button" onclick="setRediaryDays('{{ row_id }}', 3)" class="text-[10px] text-gray-400 hover:text-marsh border border-gray-200 rounded px-1.5 py-0.5">+3d</button>
          <button type="button" onclick="setRediaryDays('{{ row_id }}', 7)" class="text-[10px] text-gray-400 hover:text-marsh border border-gray-200 rounded px-1.5 py-0.5">+7d</button>
          <button type="button" onclick="setRediaryDays('{{ row_id }}', 14)" class="text-[10px] text-gray-400 hover:text-marsh border border-gray-200 rounded px-1.5 py-0.5">+14d</button>
        </div>
      </div>

      <div class="flex gap-2 mt-2">
        <button type="submit" class="text-xs bg-marsh text-white px-3 py-1.5 rounded hover:bg-marsh-light transition-colors">Log + Re-Diary</button>
        <button type="button" onclick="markDoneOnly(this, {{ r.id }})" class="text-xs text-gray-600 border border-gray-300 px-3 py-1.5 rounded hover:bg-gray-100 transition-colors">Mark Done</button>
        <button type="button" onclick="this.closest('tr').classList.add('hidden')" class="text-xs text-gray-400 hover:text-gray-600 ml-auto">Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 5: Update button handlers**

Replace `toggleFollowupForm` and `toggleCompleteForm` calls on the "Follow Up" and "Done" buttons with a single `toggleDispositionForm('{{ row_id }}')`.

Add JavaScript at the bottom of `_row.html` or in the page:

```javascript
function toggleDispositionForm(rowId) {
  var el = document.getElementById('disposition-form-row-' + rowId);
  if (el) el.classList.toggle('hidden');
}

function dispositionChanged(sel, rowId) {
  var days = parseInt(sel.selectedOptions[0].dataset.days || '0');
  var section = document.getElementById('rediary-section-' + rowId);
  var dateInput = document.getElementById('rediary-date-' + rowId);
  if (days > 0) {
    section.classList.remove('hidden');
    var d = new Date();
    d.setDate(d.getDate() + days);
    dateInput.value = d.toISOString().split('T')[0];
  } else {
    section.classList.remove('hidden'); // still show, just no pre-fill
    dateInput.value = '';
  }
}

function setRediaryDays(rowId, days) {
  var dateInput = document.getElementById('rediary-date-' + rowId);
  var d = new Date();
  d.setDate(d.getDate() + days);
  dateInput.value = d.toISOString().split('T')[0];
  document.getElementById('rediary-section-' + rowId).classList.remove('hidden');
}

function markDoneOnly(btn, activityId) {
  var form = btn.closest('form');
  var disposition = form.querySelector('select[name="disposition"]').value;
  var hours = form.querySelector('input[name="duration_hours"]').value;
  var note = form.querySelector('input[name="notes"]').value;
  htmx.ajax('POST', '/activities/' + activityId + '/complete', {
    target: btn.closest('tr').previousElementSibling,
    swap: 'outerHTML',
    values: {disposition: disposition, duration_hours: hours || '0', note: note}
  });
  btn.closest('tr').remove();
}
```

Note: The `source == 'policy'` form path (lines 139-165 of the original) should be kept separately since policy follow-ups don't have activity IDs or dispositions. Only merge the activity-source forms.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/followups/_row.html src/policydb/web/routes/activities.py src/policydb/web/routes/dashboard.py
git commit -m "feat: unified disposition form with auto-scheduling in follow-up rows"
```

---

### Task 7: Activity Row — Disposition Badge

**Files:**
- Modify: `src/policydb/web/templates/activities/_activity_row.html`

- [ ] **Step 1: Add disposition badge to activity row display**

Find the area in `_activity_row.html` where the activity type badge is displayed. After it, add:

```html
{% if a.disposition %}
<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ a.disposition }}</span>
{% endif %}
{% if a.thread_id %}
<span class="text-[10px] text-blue-400 font-mono">COR-{{ a.thread_id }}</span>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/activities/_activity_row.html
git commit -m "feat: disposition badge and COR tag on activity rows"
```

---

### Task 8: Policy Edit — Correspondence Threads Section

**Files:**
- Create: `src/policydb/web/templates/policies/_correspondence_threads.html`
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/templates/policies/edit.html`

- [ ] **Step 1: Load thread data in policy edit route**

In `src/policydb/web/routes/policies.py`, find the `policy_edit` GET handler. After loading the policy data, add thread loading:

```python
        # Correspondence threads for this policy
        "correspondence_threads": [dict(r) for r in conn.execute("""
            SELECT thread_id,
                   MIN(subject) AS thread_subject,
                   COUNT(*) AS attempt_count,
                   COALESCE(SUM(duration_hours), 0) AS total_hours,
                   MAX(CASE WHEN follow_up_done = 0 THEN 1 ELSE 0 END) AS has_pending
            FROM activity_log
            WHERE policy_id = ? AND thread_id IS NOT NULL
            GROUP BY thread_id
            ORDER BY MAX(activity_date) DESC
        """, (policy_dict["id"],)).fetchall()] if policy_dict.get("id") else [],
```

Then for each thread, load its activities:

```python
        # After the template context dict is built:
        for t in ctx.get("correspondence_threads", []):
            t["activities"] = [dict(r) for r in conn.execute("""
                SELECT activity_date, disposition, details, duration_hours, follow_up_done
                FROM activity_log WHERE thread_id = ?
                ORDER BY activity_date DESC, id DESC
            """, (t["thread_id"],)).fetchall()]
```

- [ ] **Step 2: Create thread history template partial**

Write to `src/policydb/web/templates/policies/_correspondence_threads.html`:

```html
{% if correspondence_threads %}
<details {% if correspondence_threads | selectattr('has_pending') | list %}open{% endif %} class="card-section">
  <summary class="card-header cursor-pointer select-none list-none">
    <span class="section-title flex items-center gap-1">
      <span class="details-arrow">&#9654;</span>
      Correspondence Threads
      <span class="text-gray-400 font-normal text-xs">&middot; {{ correspondence_threads | length }} thread{{ 's' if correspondence_threads | length != 1 }}</span>
    </span>
  </summary>
  <div class="p-4 space-y-3">
    {% for t in correspondence_threads %}
    <details {% if t.has_pending %}open{% endif %} class="border border-gray-100 rounded overflow-hidden">
      <summary class="px-3 py-2 bg-gray-50 cursor-pointer select-none list-none flex items-center gap-2 text-xs hover:bg-gray-100 transition-colors">
        <span class="details-arrow text-gray-400">&#9654;</span>
        <span class="text-blue-500 font-mono font-medium">COR-{{ t.thread_id }}</span>
        <span class="text-gray-700">{{ t.thread_subject }}</span>
        <span class="text-gray-400">&middot; {{ t.attempt_count }} attempt{{ 's' if t.attempt_count != 1 }} &middot; {{ t.total_hours | format_hours }}</span>
        {% if t.has_pending %}
        <span class="bg-amber-100 text-amber-700 text-[10px] px-1.5 py-0.5 rounded ml-auto">Pending</span>
        {% endif %}
      </summary>
      <table class="w-full text-xs">
        <tbody>
          {% for a in t.activities %}
          <tr class="border-t border-gray-50 hover:bg-gray-50 transition-colors">
            <td class="px-3 py-1.5 text-gray-400 w-20 whitespace-nowrap">{{ a.activity_date }}</td>
            <td class="px-3 py-1.5 w-32">
              {% if a.disposition %}
              <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ a.disposition }}</span>
              {% else %}
              <span class="text-gray-300">&mdash;</span>
              {% endif %}
            </td>
            <td class="px-3 py-1.5 text-gray-600 truncate max-w-xs">{{ a.details or '' }}</td>
            <td class="px-3 py-1.5 text-right text-gray-400 tabular-nums w-16">
              {% if a.duration_hours %}{{ a.duration_hours | format_hours }}{% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
        <tfoot>
          <tr class="border-t border-gray-200">
            <td colspan="3" class="px-3 py-1.5 text-gray-400 font-medium">Total</td>
            <td class="px-3 py-1.5 text-right text-gray-500 font-medium tabular-nums">{{ t.total_hours | format_hours }}</td>
          </tr>
        </tfoot>
      </table>
    </details>
    {% endfor %}
  </div>
</details>
{% endif %}
```

- [ ] **Step 3: Include thread section in edit.html**

In `src/policydb/web/templates/policies/edit.html`, find the Activity Log section (around line 119). **Before** it, add:

```html
{% include 'policies/_correspondence_threads.html' %}
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/policies/_correspondence_threads.html src/policydb/web/routes/policies.py src/policydb/web/templates/policies/edit.html
git commit -m "feat: correspondence threads section on policy edit page"
```

---

### Task 9: Email Template Tokens

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add tokens to `followup_context()` and `CONTEXT_TOKENS`**

In `src/policydb/email_templates.py`, find the `followup_context()` function. Add:

```python
    ctx["disposition"] = row.get("disposition") or ""
    ctx["thread_ref"] = f"COR-{row['thread_id']}" if row.get("thread_id") else ""
```

Find the `CONTEXT_TOKENS` dict (or `CONTEXT_TOKEN_GROUPS`). Under the `"Followup"` group, add:

```python
    ("disposition", "Disposition"),
    ("thread_ref", "Thread Reference"),
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: add disposition and thread_ref tokens to email templates"
```

---

### Task 10: Search — COR Tag Support

**Files:**
- Modify: `src/policydb/web/routes/dashboard.py` (search handler)

- [ ] **Step 1: Add COR-{id} pattern matching to search**

Find the search handler in `src/policydb/web/routes/dashboard.py`. When the search query matches the pattern `COR-\d+`, extract the thread_id and query:

```python
import re

# In the search handler, before or alongside existing search logic:
cor_match = re.match(r'^COR-(\d+)$', q.strip(), re.IGNORECASE)
if cor_match:
    thread_id = int(cor_match.group(1))
    activities = conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.thread_id = ?
        ORDER BY a.activity_date DESC
    """, (thread_id,)).fetchall()
    # Add to search results
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/dashboard.py
git commit -m "feat: search by COR-{id} correspondence thread tag"
```

---

### Task 11: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual test workflow**

Run: `policydb serve`

1. **Settings:** Navigate to `/settings` → verify Dispositions card shows all 11 defaults. Change "Left VM" to 5 days. Add a new disposition "Sent Proposal" with 10 days. Remove "Escalated". Verify changes persist on reload.

2. **Complete with disposition:** Go to `/followups`. Find a pending follow-up. Click to open form. Select "Left VM" disposition. Verify date auto-fills to today+5. Click "Mark Done". Verify disposition badge appears on the completed activity.

3. **Re-diary with threading:** Find another follow-up. Select "Sent Email". Verify date auto-fills to today+7. Click "Log + Re-Diary". Verify:
   - Original marked done with "Sent Email" disposition
   - New follow-up created with thread_id set
   - COR-{id} tag visible on the new row
   - Thread summary shows "2nd attempt · last: Sent Email 0d ago"

4. **Thread history:** Navigate to the policy's edit page. Verify "Correspondence Threads" section appears above Activity Log. Verify it shows the thread with both activities, dispositions, and total hours.

5. **Copy COR tag:** Click ⧉ on a threaded follow-up. Verify clipboard contains `COR-{id}` prefix.

6. **Search:** Go to search, type `COR-{id}`. Verify results show all activities in the thread.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for follow-up workflow"
```
