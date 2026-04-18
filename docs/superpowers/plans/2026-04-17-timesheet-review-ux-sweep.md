# Timesheet Review — UX Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the UX sweep in spec `2026-04-17-timesheet-review-ux-sweep-design.md` — inline context pills on activity rows, contenteditable polish with save feedback, range-picker popover replacing `prompt()`, and a cascading-combobox add-activity form.

**Architecture:** No migrations. Extend `build_timesheet_payload` with joined context fields. Rewrite three templates (`_activity_row.html`, `_panel.html`, `_add_activity_form.html`), add two new partials (`_activity_pills.html`, `_range_popover.html`), add a small static JS file (`timesheet.js`), and extend `POST /timesheet/activity` to accept two optional ids. All existing tests continue to pass.

**Tech Stack:** FastAPI + Jinja2 + HTMX + Tailwind (existing). Native `<datalist>` for combobox typeahead — no new JS dependency.

---

## File Structure

**New files:**
- `src/policydb/web/templates/timesheet/_activity_pills.html` — reusable context-pill strip (client / project / policy / issue)
- `src/policydb/web/templates/timesheet/_range_popover.html` — preset chips + native date inputs
- `src/policydb/web/static/js/timesheet.js` — small handlers for flashCell, day-total writeback, popover toggle, combobox cascade

**Modified files:**
- `src/policydb/timesheet.py` — extend `_load_activities` + per-activity dict
- `src/policydb/web/app.py` — add `format_hours_bare` Jinja filter
- `src/policydb/web/routes/timesheet.py` — `POST /activity` accepts + validates `project_id` / `issue_id`; new `GET /timesheet/options/{kind}` JSON endpoint for cascade data
- `src/policydb/web/templates/timesheet/_activity_row.html` — include pills partial; replace `%.2f` display; contenteditable affordance + placeholder classes
- `src/policydb/web/templates/timesheet/_panel.html` — swap `prompt()` button for popover trigger; include `timesheet.js` and the popover partial
- `src/policydb/web/templates/timesheet/_add_activity_form.html` — cascading combobox fields (client / policy / project / issue)
- `src/policydb/web/templates/timesheet/full_page.html` — load `timesheet.js` once at page scope

**Tests:**
- `tests/test_timesheet.py` — new cases for extended payload fields
- `tests/test_timesheet_routes.py` — new cases for POST validation + cascade endpoint

---

## Task 1 — Extend `_load_activities` with joined context

**Files:**
- Modify: `src/policydb/timesheet.py` (the `_load_activities` function, ~lines 30–44)
- Test: `tests/test_timesheet.py`

- [ ] **Step 1: Add a failing test for joined context fields**

Append to `tests/test_timesheet.py`:

```python
def test_load_activities_includes_context_fields(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import _load_activities
    cid = _seed_client(conn, "Acme")
    pid = _seed_policy(conn, client_id=cid, expiration_date="2026-12-31")
    conn.execute(
        "INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')",
        (cid,),
    )
    prj_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    iss_id = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES ('2026-04-13', ?, 'WC audit dispute', 'Issue',
                   'issue', 'ISS-001', 0)""",
        (cid,),
    ).lastrowid
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, project_id, issue_id,
            subject, activity_type, duration_hours, item_kind)
           VALUES ('2026-04-13', ?, ?, ?, ?, 'Follow up', 'Call', 0.5,
                   'activity')""",
        (cid, pid, prj_id, iss_id),
    )
    conn.commit()

    rows = _load_activities(conn, date(2026, 4, 13), date(2026, 4, 13))
    assert len(rows) == 2
    work_row = next(r for r in rows if r["item_kind"] == "activity")
    assert work_row["client_name"] == "Acme"
    assert work_row["policy_uid"] is not None
    assert work_row["project_name"] == "Plant 3"
    assert work_row["issue_uid"] == "ISS-001"
    assert work_row["issue_subject"] == "WC audit dispute"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source ~/.policydb/venv/bin/activate
pytest tests/test_timesheet.py::test_load_activities_includes_context_fields -v
```

Expected: FAIL — `KeyError: 'policy_uid'` (the current SELECT doesn't include it).

- [ ] **Step 3: Rewrite `_load_activities` to join context**

Replace the body of `_load_activities` in `src/policydb/timesheet.py`:

```python
def _load_activities(conn, start: date, end: date) -> list[sqlite3.Row]:
    """Fetch activity rows in [start, end], joined to client/policy/project/issue labels."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject,
                  a.duration_hours, a.reviewed_at, a.source, a.follow_up_done,
                  a.item_kind, a.client_id, a.policy_id, a.project_id, a.issue_id,
                  a.details,
                  c.name       AS client_name,
                  p.policy_uid AS policy_uid,
                  p.policy_type AS policy_type,
                  pr.name      AS project_name,
                  iss.issue_uid AS issue_uid,
                  iss.subject  AS issue_subject
           FROM activity_log a
           LEFT JOIN clients      c  ON c.id  = a.client_id
           LEFT JOIN policies     p  ON p.id  = a.policy_id
           LEFT JOIN projects     pr ON pr.id = a.project_id
           LEFT JOIN activity_log iss ON iss.id = a.issue_id
                                    AND iss.item_kind = 'issue'
           WHERE a.activity_date BETWEEN ? AND ?
           ORDER BY a.activity_date, a.id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_timesheet.py::test_load_activities_includes_context_fields -v
```

Expected: PASS.

- [ ] **Step 5: Run the full timesheet test module to check nothing regressed**

```bash
pytest tests/test_timesheet.py -v
```

Expected: all existing cases still pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "feat(timesheet): join policy/project/issue context in _load_activities"
```

---

## Task 2 — Extend activity payload dict with hrefs/labels

**Files:**
- Modify: `src/policydb/timesheet.py` (inside `build_timesheet_payload`, the activities.append block ~lines 141–151)
- Test: `tests/test_timesheet.py`

- [ ] **Step 1: Add a failing test for the payload shape**

Append to `tests/test_timesheet.py`:

```python
def test_build_payload_exposes_context_hrefs(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn, "Acme")
    pid = _seed_policy(conn, client_id=cid, expiration_date="2026-12-31")
    conn.execute(
        "INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')",
        (cid,),
    )
    prj_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, project_id,
            subject, activity_type, duration_hours, item_kind)
           VALUES ('2026-04-13', ?, ?, ?, 'Follow up', 'Call', 0.5, 'activity')""",
        (cid, pid, prj_id),
    )
    conn.commit()

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 13),
    )
    act = payload["days"][0]["activities"][0]
    assert act["client_name"] == "Acme"
    assert act["client_href"] == f"/clients/{cid}"
    assert act["policy_uid"].startswith("POL-")
    assert act["policy_href"] == f"/policies/{act['policy_uid']}/edit"
    assert act["project_name"] == "Plant 3"
    assert act["project_href"] == f"/clients/{cid}/projects/{prj_id}"
    assert act["issue_uid"] is None
    assert act["issue_href"] is None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_timesheet.py::test_build_payload_exposes_context_hrefs -v
```

Expected: FAIL — `KeyError: 'client_href'`.

- [ ] **Step 3: Update the activities.append block in `build_timesheet_payload`**

In `src/policydb/timesheet.py`, replace the `day["activities"].append({...})` call with:

```python
        day["activities"].append({
            "id": r["id"],
            "subject": r["subject"] or "",
            "activity_type": r["activity_type"] or "",
            "duration_hours": r["duration_hours"],
            "reviewed_at": r["reviewed_at"],
            "source": r["source"] or "manual",
            "item_kind": r["item_kind"],

            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "client_href": (
                f"/clients/{r['client_id']}" if r["client_id"] else None
            ),

            "policy_id": r["policy_id"],
            "policy_uid": r["policy_uid"],
            "policy_type": r["policy_type"],
            "policy_href": (
                f"/policies/{r['policy_uid']}/edit" if r["policy_uid"] else None
            ),

            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "project_href": (
                f"/clients/{r['client_id']}/projects/{r['project_id']}"
                if r["project_id"] and r["client_id"] else None
            ),

            "issue_id": r["issue_id"],
            "issue_uid": r["issue_uid"],
            "issue_subject": r["issue_subject"],
            "issue_href": (
                f"/issues/{r['issue_uid']}" if r["issue_uid"] else None
            ),
        })
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_timesheet.py::test_build_payload_exposes_context_hrefs -v
```

Expected: PASS.

- [ ] **Step 5: Re-run the whole timesheet test module**

```bash
pytest tests/test_timesheet.py tests/test_timesheet_routes.py -v
```

Expected: all cases still pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "feat(timesheet): expose client/policy/project/issue hrefs in payload"
```

---

## Task 3 — Add `format_hours_bare` Jinja filter

**Files:**
- Modify: `src/policydb/web/app.py` (add helper near `_fmt_hours` ~line 204, register filter ~line 223)
- Test: no new test — filter is trivial; verified visually in Task 5 and the manual QA task.

- [ ] **Step 1: Add the helper and register it**

Directly under the existing `_fmt_hours` in `src/policydb/web/app.py`:

```python
def _fmt_hours_bare(value) -> str:
    """Strip trailing zeros, no unit suffix. Used in contenteditable cells where the
    column context already implies hours: 1.0 → '1', 1.5 → '1.5', 0.75 → '0.75', None/0 → ''."""
    if value is None or value == 0:
        return ""
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""
```

Register immediately below the `format_hours` filter registration:

```python
templates.env.filters["format_hours_bare"] = _fmt_hours_bare
```

- [ ] **Step 2: Import sanity-check**

```bash
source ~/.policydb/venv/bin/activate
python -c "from policydb.web.app import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/app.py
git commit -m "feat(web): add format_hours_bare jinja filter for contenteditable cells"
```

---

## Task 4 — Write reusable `_activity_pills.html` partial

**Files:**
- Create: `src/policydb/web/templates/timesheet/_activity_pills.html`

- [ ] **Step 1: Create the partial**

Write to `src/policydb/web/templates/timesheet/_activity_pills.html`:

```jinja
{#
  Context-pill strip for a timesheet activity.
  Expects a dict `activity` with the keys populated by build_timesheet_payload:
    client_name, client_href
    project_name, project_href
    policy_uid,  policy_href
    issue_uid,   issue_href, issue_subject
  Order: Client → Project → Policy → Issue.
  Pills are read-only jump links; missing context renders nothing.
#}
<span class="ts-pills inline-flex items-center flex-wrap gap-1 mr-1">
  {% if activity.client_href %}
    <a href="{{ activity.client_href }}"
       class="ts-pill ts-pill-client"
       title="Client">{{ activity.client_name }}</a>
  {% endif %}

  {% if activity.project_href %}
    <a href="{{ activity.project_href }}"
       class="ts-pill ts-pill-project"
       title="Project / Location">
      <span class="ts-pill-k">LOC</span> {{ activity.project_name }}
    </a>
  {% endif %}

  {% if activity.policy_href %}
    <a href="{{ activity.policy_href }}"
       class="ts-pill ts-pill-policy"
       title="{{ activity.policy_type or 'Policy' }}">
      {{ activity.policy_uid }}
    </a>
  {% endif %}

  {% if activity.issue_href %}
    <a href="{{ activity.issue_href }}"
       class="ts-pill ts-pill-issue"
       title="{{ activity.issue_subject or 'Issue' }}">
      {{ activity.issue_uid }}
    </a>
  {% endif %}
</span>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/timesheet/_activity_pills.html
git commit -m "feat(timesheet): add _activity_pills partial for inline context chips"
```

---

## Task 5 — Rewrite `_activity_row.html` with pills + contenteditable polish

**Files:**
- Modify: `src/policydb/web/templates/timesheet/_activity_row.html` (full rewrite)

- [ ] **Step 1: Replace the file**

Overwrite `src/policydb/web/templates/timesheet/_activity_row.html` with:

```jinja
{#
  Context: activity (dict from payload.days[i].activities[j])
#}
<div class="activity-row ts-row flex items-center gap-2 py-1 text-xs border-t border-stone-100"
     data-activity-id="{{ activity.id }}"
     data-reviewed="{{ 'true' if activity.reviewed_at else 'false' }}">

  {% include "timesheet/_activity_pills.html" %}

  <span class="ts-subject flex-1 min-w-0 truncate"
        contenteditable="plaintext-only"
        data-placeholder="What did you work on?"
        hx-patch="/timesheet/activity/{{ activity.id }}"
        hx-trigger="blur changed"
        hx-vals='js:{subject: event.target.innerText}'
        hx-swap="none">{{ activity.subject }}</span>

  <select class="ts-type border-0 bg-transparent text-xs"
          hx-patch="/timesheet/activity/{{ activity.id }}"
          hx-trigger="change"
          hx-vals='js:{activity_type: event.target.value}'
          hx-swap="none">
    {% for t in (activity_types or ["Email", "Call", "Meeting", "Task", "Note"]) %}
      <option value="{{ t }}" {% if activity.activity_type == t %}selected{% endif %}>{{ t }}</option>
    {% endfor %}
  </select>

  <span class="ts-hours font-mono text-right w-12"
        contenteditable="plaintext-only"
        data-placeholder="—"
        hx-patch="/timesheet/activity/{{ activity.id }}"
        hx-trigger="blur changed, keyup[keyCode==13]"
        hx-vals='js:{duration_hours: event.target.innerText}'
        hx-swap="none">{{ activity.duration_hours | format_hours_bare }}</span>

  <span class="review-mark w-4 text-center
               {% if activity.reviewed_at %}text-emerald-600{% else %}text-amber-600{% endif %}">
    {% if activity.reviewed_at %}✓{% else %}●{% endif %}
  </span>

  <button class="delete-btn text-stone-400 hover:text-red-600 px-1"
          hx-delete="/timesheet/activity/{{ activity.id }}"
          hx-confirm="Delete this activity?"
          hx-target="closest .activity-row"
          hx-swap="outerHTML">×</button>
</div>
```

- [ ] **Step 2: Add the CSS for pills, affordance, and placeholders**

Append inside the existing `{% block extra_head %}` of `src/policydb/web/templates/timesheet/full_page.html`, OR (cleaner) at the top of `_panel.html` inside a `<style>` block. Use the `_panel.html` location since the pills and rows are scoped to that panel.

Add to the TOP of `src/policydb/web/templates/timesheet/_panel.html`, before `<div id="timesheet-panel"…`:

```jinja
<style>
  .ts-pill { display:inline-flex; align-items:center; gap:3px; font-size:10px;
             padding:1px 6px; border-radius:10px; border:1px solid #e7e5e4;
             background:#F7F3EE; color:#3D3C37; text-decoration:none;
             white-space:nowrap; }
  .ts-pill:hover { filter:brightness(.97); }
  .ts-pill-k     { opacity:.55; font-weight:500; letter-spacing:.02em; }
  .ts-pill-client  { background:#E8EDFF; border-color:#BFCCFF; color:#0B4BFF; }
  .ts-pill-policy  { background:#E6F4EA; border-color:#BBDFC6; color:#15803d; }
  .ts-pill-project { background:#FFF7E0; border-color:#F1E2A6; color:#92400e; }
  .ts-pill-issue   { background:#FDECEC; border-color:#F5C2C2; color:#991b1b; }

  .ts-subject, .ts-hours {
    border-bottom: 1px solid transparent;
    padding-bottom: 1px;
    outline: none;
    transition: border-color .15s, background-color .15s;
  }
  .ts-subject:hover, .ts-hours:hover {
    border-bottom: 1px dashed #d6d3d1;
    cursor: text;
  }
  .ts-subject:focus, .ts-hours:focus {
    border-bottom: 1px solid #0B4BFF;
    background: #fffefb;
  }
  .ts-subject:empty::before, .ts-hours:empty::before {
    content: attr(data-placeholder);
    color: #a8a29e;
    font-style: italic;
  }
</style>
```

- [ ] **Step 3: Run the route tests to confirm nothing broke**

```bash
source ~/.policydb/venv/bin/activate
pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/timesheet/_activity_row.html \
        src/policydb/web/templates/timesheet/_panel.html
git commit -m "feat(timesheet): context pills on activity rows + contenteditable polish"
```

---

## Task 6 — Add `timesheet.js` for save feedback + day-total writeback

**Files:**
- Create: `src/policydb/web/static/js/timesheet.js`
- Modify: `src/policydb/web/templates/timesheet/full_page.html` (include the script)

- [ ] **Step 1: Create the `js/` subdirectory under `static/`**

```bash
mkdir -p src/policydb/web/static/js
```

The existing `StaticFiles` mount at `/static/...` in `app.py` serves anything under that directory, so the file will be reachable at `/static/js/timesheet.js`.

- [ ] **Step 2: Write the JS handler**

Create `src/policydb/web/static/js/timesheet.js`:

```javascript
/* Timesheet review — client-side glue.
   Handles flashCell feedback on contenteditable PATCHes,
   inline day-total refresh from the PATCH JSON response,
   and range-popover open/close + cascade for the add-activity form.
*/
(function () {
  "use strict";

  function flash(el) {
    if (typeof window.flashCell === "function") {
      window.flashCell(el);
    } else {
      el.style.transition = "background-color .3s ease";
      el.style.backgroundColor = "#d1fae5";
      setTimeout(function () {
        el.style.backgroundColor = "";
        setTimeout(function () { el.style.transition = ""; }, 300);
      }, 800);
    }
  }

  // Intercept timesheet PATCH responses and wire up UI feedback.
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var xhr = evt.detail.xhr;
    var path = evt.detail.requestConfig && evt.detail.requestConfig.path;
    if (!path || path.indexOf("/timesheet/activity/") !== 0) return;
    if (evt.detail.requestConfig.verb !== "patch") return;
    if (!xhr || xhr.status !== 200) return;

    var data;
    try { data = JSON.parse(xhr.responseText); } catch (e) { return; }
    if (!data || !data.ok) return;

    var target = evt.detail.elt;
    if (!target) return;

    // Update hours cell display to the server-rounded value.
    if (target.classList.contains("ts-hours") && typeof data.formatted === "string") {
      target.innerText = data.formatted;
    }

    // Update the day-card total. Day card holds a .day-tot span in its header.
    var card = target.closest(".day-card");
    if (card && typeof data.total_hours === "number") {
      var tot = card.querySelector(".day-tot");
      if (tot) {
        var h = Number(data.total_hours);
        tot.textContent = (Math.round(h * 10) / 10).toFixed(1) + "h";
      }
    }

    flash(target);
  });

  // Range popover toggling.
  document.body.addEventListener("click", function (evt) {
    var trigger = evt.target.closest("[data-range-trigger]");
    if (trigger) {
      evt.preventDefault();
      var pop = document.querySelector("[data-range-popover]");
      if (pop) {
        pop.dataset.open = pop.dataset.open === "1" ? "0" : "1";
      }
      return;
    }
    // Outside-click close.
    var open = document.querySelector('[data-range-popover][data-open="1"]');
    if (open && !evt.target.closest("[data-range-popover]") &&
               !evt.target.closest("[data-range-trigger]")) {
      open.dataset.open = "0";
    }
  });
  document.body.addEventListener("keydown", function (evt) {
    if (evt.key !== "Escape") return;
    var open = document.querySelector('[data-range-popover][data-open="1"]');
    if (open) open.dataset.open = "0";
  });

  // Add-activity form cascade: when the client changes, reset policy/project/issue inputs
  // and refetch the option lists.
  function setDatalist(dlId, options) {
    var dl = document.getElementById(dlId);
    if (!dl) return;
    dl.innerHTML = "";
    options.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o.label;
      opt.dataset.id = o.id;
      dl.appendChild(opt);
    });
  }

  function refreshCascade(form, clientId) {
    ["policy", "project", "issue"].forEach(function (kind) {
      var input = form.querySelector('[data-cascade="' + kind + '"]');
      var hid   = form.querySelector('[data-cascade-id="' + kind + '"]');
      if (input) input.value = "";
      if (hid)   hid.value = "";
      setDatalist("ts-options-" + kind, []);
    });
    if (!clientId) return;
    fetch("/timesheet/options/all?client_id=" + encodeURIComponent(clientId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setDatalist("ts-options-policy",  data.policies  || []);
        setDatalist("ts-options-project", data.projects || []);
        setDatalist("ts-options-issue",   data.issues   || []);
      });
  }

  function resolveId(form, kind) {
    var input = form.querySelector('[data-cascade="' + kind + '"]');
    var hid   = form.querySelector('[data-cascade-id="' + kind + '"]');
    if (!input || !hid) return;
    var dl = document.getElementById("ts-options-" + kind);
    if (!dl) return;
    hid.value = "";
    var match = Array.prototype.find.call(dl.options, function (opt) {
      return opt.value === input.value;
    });
    if (match) hid.value = match.dataset.id || "";
  }

  document.body.addEventListener("input", function (evt) {
    var form = evt.target.closest(".add-activity-form");
    if (!form) return;

    if (evt.target.matches('[data-cascade="client"]')) {
      // Find the matching client id from the client datalist.
      var dl = document.getElementById("ts-options-client");
      var hid = form.querySelector('[data-cascade-id="client"]');
      if (!dl || !hid) return;
      var match = Array.prototype.find.call(dl.options, function (opt) {
        return opt.value === evt.target.value;
      });
      hid.value = match ? (match.dataset.id || "") : "";
      refreshCascade(form, hid.value);
      return;
    }

    ["policy", "project", "issue"].forEach(function (kind) {
      if (evt.target.matches('[data-cascade="' + kind + '"]')) {
        resolveId(form, kind);
      }
    });
  });
})();
```

- [ ] **Step 3: Include the script in `full_page.html`**

Edit `src/policydb/web/templates/timesheet/full_page.html` — replace its body:

```jinja
{% extends "base.html" %}

{% block title %}Timesheet Review — PolicyDB{% endblock %}

{% block content %}
<div class="max-w-5xl mx-auto p-6">
  <h1 class="text-2xl font-serif text-policydb-midnight mb-4">Timesheet Review</h1>
  <div id="timesheet-panel" hx-get="/timesheet/panel" hx-trigger="load" hx-swap="outerHTML">
    <div class="text-sm text-stone-500">Loading…</div>
  </div>
</div>
<script src="/static/js/timesheet.js"></script>
{% endblock %}
```

- [ ] **Step 4: Smoke-test the JS is served**

```bash
source ~/.policydb/venv/bin/activate
policydb serve &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/static/js/timesheet.js
kill %1
```

Expected: `200`.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/static/js/timesheet.js \
        src/policydb/web/templates/timesheet/full_page.html
git commit -m "feat(timesheet): client-side flashCell, day-total writeback, cascade handlers"
```

---

## Task 7 — Range-picker popover partial

**Files:**
- Create: `src/policydb/web/templates/timesheet/_range_popover.html`

- [ ] **Step 1: Create the partial**

Write `src/policydb/web/templates/timesheet/_range_popover.html`:

```jinja
{#
  Context: payload.range (start, end, kind).
  Anchored inside the panel header. Opens/closes via timesheet.js
  responding to elements with [data-range-trigger] / [data-range-popover].
#}
<div class="relative inline-block">
  <button type="button"
          class="ts-range-btn px-2 py-1 {% if payload.range.kind == 'range' %}bg-policydb-blue text-white{% endif %}"
          data-range-trigger>
    Range ▾
  </button>

  <div class="ts-range-pop absolute z-10 mt-1 right-0 w-72 bg-white border border-stone-300 rounded-md shadow-lg p-3 text-xs"
       data-range-popover data-open="0"
       style="display:none;">
    <div class="flex flex-wrap gap-1 mb-2">
      <button type="button" class="ts-preset" data-preset="this-week">This week</button>
      <button type="button" class="ts-preset" data-preset="last-week">Last week</button>
      <button type="button" class="ts-preset" data-preset="mtd">MTD</button>
      <button type="button" class="ts-preset" data-preset="last-30">Last 30d</button>
    </div>
    <div class="flex items-center gap-2">
      <input type="date" class="ts-range-start flex-1 px-2 py-1 border border-stone-300 rounded"
             value="{{ payload.range.start }}">
      <span class="text-stone-500">→</span>
      <input type="date" class="ts-range-end flex-1 px-2 py-1 border border-stone-300 rounded"
             value="{{ payload.range.end }}">
    </div>
    <div class="flex justify-end gap-2 mt-2">
      <button type="button" class="ts-range-cancel text-stone-500">Cancel</button>
      <button type="button" class="ts-range-apply bg-policydb-blue text-white rounded px-3 py-1">
        Apply
      </button>
    </div>
  </div>
</div>

<style>
  .ts-range-pop[data-open="1"] { display:block; }
  .ts-preset { font-size:11px; padding:3px 8px; border-radius:4px;
               background:#F7F3EE; border:1px solid #e7e5e4; cursor:pointer; }
  .ts-preset:hover { filter:brightness(.97); }
  .ts-preset.active { background:#0B4BFF; color:#fff; border-color:#0B4BFF; }
</style>
```

- [ ] **Step 2: Extend `timesheet.js` with preset + apply wiring**

Append inside the IIFE in `src/policydb/web/static/js/timesheet.js`:

```javascript
  // Range popover — presets + apply.
  function isoMonday(d) {
    var wd = d.getDay(); // Sunday = 0
    var diff = (wd === 0 ? -6 : 1 - wd);
    var out = new Date(d); out.setDate(d.getDate() + diff);
    return out;
  }
  function iso(d) { return d.toISOString().slice(0, 10); }

  document.body.addEventListener("click", function (evt) {
    var preset = evt.target.closest(".ts-preset");
    if (preset) {
      var pop = preset.closest("[data-range-popover]");
      if (!pop) return;
      var startInput = pop.querySelector(".ts-range-start");
      var endInput   = pop.querySelector(".ts-range-end");
      var today = new Date();
      var s, e;
      switch (preset.dataset.preset) {
        case "this-week":
          s = isoMonday(today);
          e = new Date(s); e.setDate(s.getDate() + 6);
          break;
        case "last-week":
          s = isoMonday(today); s.setDate(s.getDate() - 7);
          e = new Date(s); e.setDate(s.getDate() + 6);
          break;
        case "mtd":
          s = new Date(today.getFullYear(), today.getMonth(), 1);
          e = today;
          break;
        case "last-30":
          s = new Date(today); s.setDate(today.getDate() - 30);
          e = today;
          break;
        default: return;
      }
      startInput.value = iso(s);
      endInput.value   = iso(e);
      pop.querySelectorAll(".ts-preset").forEach(function (p) { p.classList.remove("active"); });
      preset.classList.add("active");
      return;
    }

    var apply = evt.target.closest(".ts-range-apply");
    if (apply) {
      var pop2 = apply.closest("[data-range-popover]");
      if (!pop2) return;
      var s = pop2.querySelector(".ts-range-start").value;
      var e = pop2.querySelector(".ts-range-end").value;
      if (!s || !e) return;
      pop2.dataset.open = "0";
      htmx.ajax("GET",
        "/timesheet/panel?kind=range&start=" + encodeURIComponent(s) +
        "&end=" + encodeURIComponent(e),
        "#timesheet-panel");
      return;
    }

    var cancel = evt.target.closest(".ts-range-cancel");
    if (cancel) {
      var pop3 = cancel.closest("[data-range-popover]");
      if (pop3) pop3.dataset.open = "0";
    }
  });
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/timesheet/_range_popover.html \
        src/policydb/web/static/js/timesheet.js
git commit -m "feat(timesheet): range-picker popover partial + preset wiring"
```

---

## Task 8 — Wire the popover into `_panel.html`, remove `prompt()`

**Files:**
- Modify: `src/policydb/web/templates/timesheet/_panel.html` (the `data-range-toggle` inline-flex block)

- [ ] **Step 1: Replace the range toggle segment**

In `_panel.html`, find the `<div data-range-toggle…>` block and replace it with:

```jinja
<div data-range-toggle class="inline-flex items-center border border-stone-300 rounded overflow-hidden text-xs">
  <button class="px-2 py-1 {% if payload.range.kind == 'day' %}bg-policydb-blue text-white{% endif %}"
          hx-get="/timesheet/panel?kind=day"
          hx-target="#timesheet-panel"
          hx-swap="outerHTML"
          hx-push-url="true">Day</button>
  <button class="px-2 py-1 border-l border-stone-300 {% if payload.range.kind == 'week' %}bg-policydb-blue text-white{% endif %}"
          hx-get="/timesheet/panel?kind=week"
          hx-target="#timesheet-panel"
          hx-swap="outerHTML"
          hx-push-url="true">Week</button>
  <span class="border-l border-stone-300">
    {% include "timesheet/_range_popover.html" %}
  </span>
</div>
```

- [ ] **Step 2: Load the JS when the panel renders standalone**

The script is loaded in `full_page.html`. If `/timesheet/panel` is also requested directly (Action Center embed, etc.), belt-and-suspenders: add one line at the bottom of `_panel.html`, just before the closing `</div>` of `#timesheet-panel`:

```jinja
<script>if(!window._tsLoaded){var s=document.createElement('script');s.src='/static/js/timesheet.js';document.head.appendChild(s);window._tsLoaded=true;}</script>
```

- [ ] **Step 3: Manual smoke test**

```bash
source ~/.policydb/venv/bin/activate
policydb serve &
sleep 2
curl -s http://127.0.0.1:8000/timesheet/panel | head -40
kill %1
```

Expected: the HTML response contains `data-range-trigger` and does NOT contain `const s=prompt`.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/timesheet/_panel.html
git commit -m "feat(timesheet): replace prompt() range picker with popover"
```

---

## Task 9 — Cascade options endpoint

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py` (add one new GET handler)
- Test: `tests/test_timesheet_routes.py`

- [ ] **Step 1: Add a failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_options_endpoint_returns_client_scoped_lists(client):
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('OptCust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='OptCust'").fetchone()["id"]
    # Seed a policy + project + issue under the same client.
    from policydb.db import next_policy_uid
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, first_named_insured,
                                 policy_type, expiration_date)
           VALUES (?, ?, 'OptCust', 'GL', '2026-12-31')""",
        (uid, cid),
    )
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')", (cid,))
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES (date('now'), ?, 'Audit dispute', 'Issue',
                   'issue', 'ISS-99', 0)""",
        (cid,),
    )
    conn.commit()
    conn.close()

    resp = client.get(f"/timesheet/options/all?client_id={cid}")
    assert resp.status_code == 200
    data = resp.json()
    assert any(p["label"].startswith("POL-") for p in data["policies"])
    assert any(p["label"] == "Plant 3" for p in data["projects"])
    assert any(i["label"].startswith("ISS-99") for i in data["issues"])
    # Each row must carry an integer id.
    for k in ("policies", "projects", "issues"):
        for row in data[k]:
            assert isinstance(row["id"], int)


def test_options_endpoint_requires_client_id(client):
    resp = client.get("/timesheet/options/all")
    assert resp.status_code == 422  # FastAPI validation — missing query param
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_timesheet_routes.py::test_options_endpoint_returns_client_scoped_lists \
       tests/test_timesheet_routes.py::test_options_endpoint_requires_client_id -v
```

Expected: 404 / not found — the route doesn't exist yet.

- [ ] **Step 3: Add the endpoint**

In `src/policydb/web/routes/timesheet.py`, add after the `get_new_activity_form` handler:

```python
@router.get("/options/all")
def get_options_all(client_id: int = Query(...), conn=Depends(get_db)):
    """Cascade options for the add-activity form. Scoped to one client."""
    ok = conn.execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone()
    if not ok:
        raise HTTPException(404, "Client not found")

    policies = conn.execute(
        """SELECT id, policy_uid, policy_type
           FROM policies
           WHERE client_id = ?
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
           ORDER BY policy_uid
           LIMIT 200""",
        (client_id,),
    ).fetchall()
    projects = conn.execute(
        "SELECT id, name FROM projects WHERE client_id = ? ORDER BY name LIMIT 200",
        (client_id,),
    ).fetchall()
    issues = conn.execute(
        """SELECT id, issue_uid, subject
           FROM activity_log
           WHERE client_id = ?
             AND item_kind = 'issue'
             AND follow_up_done = 0
           ORDER BY id DESC
           LIMIT 50""",
        (client_id,),
    ).fetchall()

    return JSONResponse({
        "policies": [
            {"id": r["id"],
             "label": f"{r['policy_uid']}" + (f" · {r['policy_type']}" if r["policy_type"] else "")}
            for r in policies
        ],
        "projects": [
            {"id": r["id"], "label": r["name"]} for r in projects
        ],
        "issues": [
            {"id": r["id"],
             "label": f"{r['issue_uid']} · {r['subject']}" if r["issue_uid"] else (r["subject"] or "")}
            for r in issues
        ],
    })
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_timesheet_routes.py::test_options_endpoint_returns_client_scoped_lists \
       tests/test_timesheet_routes.py::test_options_endpoint_requires_client_id -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "feat(timesheet): GET /timesheet/options/all for cascade pickers"
```

---

## Task 10 — Rewrite `_add_activity_form.html` with cascading combobox

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py` (pass the full client list with ids into the form context — already does)
- Modify: `src/policydb/web/templates/timesheet/_add_activity_form.html` (full rewrite)

- [ ] **Step 1: Confirm the route already passes `client_list` with ids**

Read `get_new_activity_form` in `routes/timesheet.py`:

```python
clients = conn.execute(
    "SELECT id, name FROM clients ORDER BY name LIMIT 500"
).fetchall()
...
"client_list": [dict(r) for r in clients],
```

Good — already has ids. No route change needed.

- [ ] **Step 2: Overwrite `_add_activity_form.html`**

Write:

```jinja
{#
  Context: day (dict with .date), client_list (list of {id, name}).
  Uses native <datalist> for typeahead combobox — cascade wired by timesheet.js.
#}
<form class="add-activity-form flex flex-wrap items-end gap-2 pt-2 mt-2
             border-t border-dashed border-stone-300"
      hx-post="/timesheet/activity"
      hx-target="#timesheet-panel"
      hx-swap="outerHTML"
      hx-on::after-request="if(event.detail.successful) htmx.ajax('GET', '/timesheet/panel', '#timesheet-panel')">

  <input type="hidden" name="activity_date" value="{{ day.date }}">

  {# Client — typeahead + hidden id #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Client*
    <input list="ts-options-client"
           class="ts-combo w-48 text-xs border border-stone-300 rounded px-2 py-1 mt-0.5"
           placeholder="Type to search…"
           data-cascade="client"
           required>
  </label>
  <datalist id="ts-options-client">
    {% for c in client_list %}
      <option value="{{ c.name }}" data-id="{{ c.id }}"></option>
    {% endfor %}
  </datalist>
  <input type="hidden" name="client_id" data-cascade-id="client" required>

  {# Policy #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Policy
    <input list="ts-options-policy"
           class="ts-combo w-40 text-xs border border-stone-300 rounded px-2 py-1 mt-0.5"
           placeholder="(optional)"
           data-cascade="policy">
  </label>
  <datalist id="ts-options-policy"></datalist>
  <input type="hidden" name="policy_id" data-cascade-id="policy">

  {# Project #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Project
    <input list="ts-options-project"
           class="ts-combo w-40 text-xs border border-stone-300 rounded px-2 py-1 mt-0.5"
           placeholder="(optional)"
           data-cascade="project">
  </label>
  <datalist id="ts-options-project"></datalist>
  <input type="hidden" name="project_id" data-cascade-id="project">

  {# Issue #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Issue
    <input list="ts-options-issue"
           class="ts-combo w-40 text-xs border border-stone-300 rounded px-2 py-1 mt-0.5"
           placeholder="(optional)"
           data-cascade="issue">
  </label>
  <datalist id="ts-options-issue"></datalist>
  <input type="hidden" name="issue_id" data-cascade-id="issue">

  {# Type #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Type
    <select name="activity_type"
            class="text-xs border border-stone-300 rounded px-2 py-1 mt-0.5">
      {% for t in ["Note", "Email", "Call", "Meeting", "Task"] %}
        <option value="{{ t }}">{{ t }}</option>
      {% endfor %}
    </select>
  </label>

  {# Subject #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider flex-1 min-w-[180px]">
    Subject*
    <input name="subject"
           class="text-xs border border-stone-300 rounded px-2 py-1 mt-0.5"
           placeholder="What did you work on?" required>
  </label>

  {# Hours #}
  <label class="flex flex-col text-[10px] uppercase text-stone-500 tracking-wider">
    Hours
    <input name="duration_hours"
           class="text-xs border border-stone-300 rounded px-2 py-1 mt-0.5 w-16 text-right"
           placeholder="h" inputmode="decimal">
  </label>

  <div class="flex gap-2">
    <button class="text-xs bg-policydb-blue text-white rounded px-3 py-1.5">Add</button>
    <button type="button" class="text-xs text-stone-500"
            onclick="this.closest('.add-slot').innerHTML=''">Cancel</button>
  </div>
</form>
```

- [ ] **Step 3: Smoke-test the rendered form HTML**

```bash
source ~/.policydb/venv/bin/activate
policydb serve &
sleep 2
curl -s "http://127.0.0.1:8000/timesheet/activity/new?date=2026-04-15" | grep 'data-cascade'
kill %1
```

Expected: several lines matching `data-cascade="client|policy|project|issue"`.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/timesheet/_add_activity_form.html
git commit -m "feat(timesheet): cascading combobox add-activity form (client→policy/project/issue)"
```

---

## Task 11 — Accept & validate `project_id` / `issue_id` on POST /activity

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py` (the `post_activity` handler, ~lines 239–271)
- Test: `tests/test_timesheet_routes.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_timesheet_routes.py`:

```python
def _seed_client_with_extras(client):
    from policydb.db import get_connection, next_policy_uid
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('PCust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='PCust'").fetchone()["id"]
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, first_named_insured,
                                 policy_type, expiration_date)
           VALUES (?, ?, 'PCust', 'GL', '2026-12-31')""",
        (uid, cid),
    )
    pol_id = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (uid,)).fetchone()["id"]
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')", (cid,))
    prj_id = conn.execute("SELECT id FROM projects WHERE client_id=?", (cid,)).fetchone()["id"]
    iss_id = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES (date('now'), ?, 'Issue Q1', 'Issue',
                   'issue', 'ISS-10', 0)""",
        (cid,),
    ).lastrowid
    conn.commit()
    conn.close()
    return cid, pol_id, prj_id, iss_id


def test_post_activity_accepts_project_and_issue(client):
    cid, pol_id, prj_id, iss_id = _seed_client_with_extras(client)
    resp = client.post("/timesheet/activity", data={
        "client_id": cid,
        "activity_date": "2026-04-15",
        "subject": "Follow up",
        "activity_type": "Call",
        "duration_hours": "0.5",
        "policy_id": pol_id,
        "project_id": prj_id,
        "issue_id": iss_id,
    })
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT client_id, policy_id, project_id, issue_id FROM activity_log WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["client_id"] == cid
    assert row["policy_id"] == pol_id
    assert row["project_id"] == prj_id
    assert row["issue_id"] == iss_id
    conn.close()


def test_post_activity_rejects_cross_client_project(client):
    cid, _, prj_id, _ = _seed_client_with_extras(client)
    # Another client with no projects.
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('Other', 'X', 'Grant')"
    )
    other_cid = conn.execute("SELECT id FROM clients WHERE name='Other'").fetchone()["id"]
    conn.commit()
    conn.close()
    resp = client.post("/timesheet/activity", data={
        "client_id": other_cid,
        "activity_date": "2026-04-15",
        "subject": "X",
        "activity_type": "Note",
        "project_id": prj_id,  # belongs to PCust, not Other
    })
    assert resp.status_code == 400


def test_post_activity_rejects_non_issue_row_as_issue(client):
    cid, _, _, _ = _seed_client_with_extras(client)
    # A plain activity row — not an issue.
    from policydb.db import get_connection
    conn = get_connection()
    aid = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type, item_kind)
           VALUES (date('now'), ?, 'plain', 'Note', 'activity')""",
        (cid,),
    ).lastrowid
    conn.commit()
    conn.close()
    resp = client.post("/timesheet/activity", data={
        "client_id": cid,
        "activity_date": "2026-04-15",
        "subject": "X",
        "activity_type": "Note",
        "issue_id": aid,
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_timesheet_routes.py -v -k post_activity_
```

Expected: three failures (the new tests).

- [ ] **Step 3: Extend the `post_activity` handler**

In `src/policydb/web/routes/timesheet.py`, replace the existing `post_activity` with:

```python
@router.post("/activity")
def post_activity(
    client_id: int = Form(...),
    activity_date: str = Form(...),
    subject: str = Form(""),
    activity_type: str = Form("Note"),
    duration_hours: str | None = Form(None),
    details: str | None = Form(None),
    policy_id: int | None = Form(None),
    project_id: int | None = Form(None),
    issue_id: int | None = Form(None),
    conn=Depends(get_db),
):
    try:
        date.fromisoformat(activity_date)
    except ValueError:
        raise HTTPException(400, "Invalid activity_date")

    ok = conn.execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone()
    if not ok:
        raise HTTPException(400, "client_id does not exist")

    if policy_id is not None:
        row = conn.execute(
            "SELECT client_id FROM policies WHERE id=?", (policy_id,)
        ).fetchone()
        if not row or row["client_id"] != client_id:
            raise HTTPException(400, "policy_id does not belong to client")

    if project_id is not None:
        row = conn.execute(
            "SELECT client_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if not row or row["client_id"] != client_id:
            raise HTTPException(400, "project_id does not belong to client")

    if issue_id is not None:
        row = conn.execute(
            "SELECT client_id, item_kind FROM activity_log WHERE id=?", (issue_id,)
        ).fetchone()
        if (not row
                or row["client_id"] != client_id
                or row["item_kind"] != "issue"):
            raise HTTPException(400, "issue_id is not a valid issue for client")

    rounded = _round_to_tenth(duration_hours) if duration_hours else None
    account_exec = cfg.get("default_account_exec", "Grant")

    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, project_id, issue_id,
            subject, activity_type, duration_hours, details, account_exec,
            item_kind, reviewed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activity', datetime('now'))""",
        (activity_date, client_id, policy_id, project_id, issue_id,
         subject.strip(), activity_type.strip(), rounded, details, account_exec),
    )
    conn.commit()
    return JSONResponse({"ok": True, "id": cur.lastrowid}, status_code=201)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "feat(timesheet): POST /activity validates project_id + issue_id against client"
```

---

## Task 12 — Manual QA pass in the browser

**Files:** none (runtime verification).

- [ ] **Step 1: Seed sample data (if test DB is empty)**

Use an existing client that has a policy, a project, and an open issue, OR quickly seed one via the CLI / SQL console. Example via `policydb`:

```bash
source ~/.policydb/venv/bin/activate
policydb serve &
sleep 2
```

If the DB already has live data, skip seeding — this is a read-only visual check.

- [ ] **Step 2: Open the page and verify pills**

Navigate to `http://127.0.0.1:8000/timesheet`. In the browser:

- At least one activity row shows a **blue** client pill.
- Any row with a policy shows a **green** `POL-###` pill that links to `/policies/{uid}/edit`.
- Any row with a project shows an **amber** `LOC · {name}` pill that links to `/clients/{id}/projects/{id}`.
- Any row with an issue shows a **rose** `ISS-###` pill that links to `/issues/{uid}`.
- Rows with none of those extra context still render cleanly with just the client pill.

- [ ] **Step 3: Verify contenteditable affordance and feedback**

- Hover the subject cell → dashed bottom border appears.
- Click it → solid brand-blue border + cursor.
- Change text → blur → cell flashes green; day total in the card header doesn't change (subject edit doesn't affect hours).
- Edit an hours cell from e.g. `2` to `3.5` → blur → cell shows `3.5` (no trailing zero), flashes green; day total updates to reflect the new sum.
- Clear an hours cell → blur → cell shows the `—` placeholder in italic stone-400.

- [ ] **Step 4: Verify range popover**

- Click `Range ▾` → popover opens under the button.
- Click `This week`, `Last week`, `MTD`, `Last 30d` → date inputs populate correctly.
- Click `Apply` → panel reloads with the new range; popover closes.
- Click outside the popover → it closes.
- Re-open it, press `Esc` → it closes.
- Confirm the old `prompt()` behavior is gone: open devtools and search the DOM for `const s=prompt` — should not exist.

- [ ] **Step 5: Verify cascading add-activity form**

- Click `➕ Add activity` in a day card.
- Type a client name in the Client field — datalist matches.
- Pick the client. Policy / Project / Issue datalists populate with that client's options.
- Start typing in Policy — only that client's policies are suggested.
- Change the Client → the Policy / Project / Issue fields clear.
- Submit the form → new row appears in the day card with the right context pills.

- [ ] **Step 6: Run the full test suite to catch regressions**

```bash
pytest tests/test_timesheet.py tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 7: Stop the dev server**

```bash
kill %1
```

---

## Self-Review Checklist (for the implementer)

Before opening the PR, re-read the spec and confirm each bullet below is covered by the tasks above:

1. ✅ Context pills on every activity row (Client / Project / Policy / Issue).  — Tasks 4, 5
2. ✅ Pills are read-only jumps to the right URLs.  — Task 4
3. ✅ Contenteditable hover + focus affordance; placeholder text when empty.  — Task 5
4. ✅ Hours display strips trailing zeros; storage unchanged.  — Tasks 3, 5
5. ✅ `flashCell()` on save; day-total refreshes inline from PATCH JSON.  — Task 6
6. ✅ Range picker popover replaces `prompt()`; presets work; ESC / outside-click close.  — Tasks 7, 8
7. ✅ Add-activity form: cascading combobox (client → policy / project / issue).  — Tasks 9, 10
8. ✅ `POST /timesheet/activity` accepts + validates `project_id` / `issue_id`.  — Task 11
9. ✅ `_load_activities` + payload carry the new fields.  — Tasks 1, 2
10. ✅ Manual QA covers pill rendering, edit feedback, popover, cascade.  — Task 12

No migrations. No config keys. No new dependencies. Out-of-scope items (activity-type combobox on the row, inline delete confirm, row-level re-linking) remain untouched per the spec.
