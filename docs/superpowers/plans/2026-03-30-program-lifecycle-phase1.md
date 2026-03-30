# Program Lifecycle Entity — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make programs a first-class entity in the renewal pipeline, activity log, and issue systems — collapsing 13 individual policy rows into one program row, enabling program-level activities, and linking issues to programs.

**Architecture:** Programs gain pipeline presence via a new `get_program_pipeline()` query. The client page pipeline and the main renewal pipeline both show program rows with expandable child detail. Program activities use the existing `program_id` column on `activity_log`. Issues already accept `program_id` on the backend — just need the form field.

**Tech Stack:** FastAPI, SQLite, Jinja2/HTMX, existing `initMatrix()` pattern

**Spec:** `docs/superpowers/specs/2026-03-30-program-lifecycle-entity-design.md`

---

### Task 1: Program Pipeline Query

**Files:**
- Modify: `src/policydb/queries.py` (after `get_stale_renewals`, ~line 188)

- [ ] **Step 1: Add `get_program_pipeline()` query**

Add after `get_stale_renewals()`:

```python
def get_program_pipeline(
    conn: sqlite3.Connection,
    client_id: int | None = None,
    window_days: int = 180,
) -> list[dict]:
    """Return one row per active program with renewal-relevant aggregated data."""
    sql = """
    SELECT pg.id AS program_id, pg.program_uid, pg.name AS program_name,
           pg.client_id, pg.renewal_status,
           c.name AS client_name, c.cn_number,
           COUNT(p.id) AS policy_count,
           COUNT(DISTINCT p.carrier) AS carrier_count,
           COALESCE(SUM(p.premium), 0) AS total_premium,
           MIN(p.expiration_date) AS earliest_expiration,
           CAST(julianday(MIN(p.expiration_date)) - julianday('now') AS INTEGER) AS days_to_renewal,
           GROUP_CONCAT(DISTINCT p.carrier) AS carriers_list
    FROM programs pg
    JOIN clients c ON pg.client_id = c.id
    LEFT JOIN policies p ON p.program_id = pg.id
        AND p.archived = 0
        AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
    WHERE pg.archived = 0
      AND c.archived = 0
    """
    params: list = []
    if client_id:
        sql += " AND pg.client_id = ?"
        params.append(client_id)
    sql += " GROUP BY pg.id HAVING MIN(p.expiration_date) IS NOT NULL"
    sql += f" AND CAST(julianday(MIN(p.expiration_date)) - julianday('now') AS INTEGER) <= {window_days}"
    sql += " ORDER BY MIN(p.expiration_date) ASC"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        dtr = d.get("days_to_renewal") or 999
        if dtr <= 30:
            d["urgency"] = "CRITICAL"
        elif dtr <= 60:
            d["urgency"] = "HIGH"
        elif dtr <= 90:
            d["urgency"] = "MEDIUM"
        else:
            d["urgency"] = "LOW"
        d["_is_program"] = True
        result.append(d)
    return result
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/policydb/queries.py').read()); print('OK')"`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: add get_program_pipeline() query for program renewal pipeline"
```

---

### Task 2: Client Page Pipeline Collapse

**Files:**
- Modify: `src/policydb/web/routes/clients.py` (~line 1007, client tab policies handler)
- Modify: `src/policydb/web/templates/clients/_renewal_pipeline_mini.html`

- [ ] **Step 1: Update client policies tab query to exclude program children**

In `src/policydb/web/routes/clients.py`, find the inline pipeline query (~line 1007). Add the program exclusion filter and fetch program pipeline data:

```python
# After the existing renewal_pipeline_policies query, add program filter:
# Change the WHERE clause to add:
#   AND (program_id IS NULL OR NOT EXISTS (SELECT 1 FROM programs pg WHERE pg.id = program_id AND pg.archived = 0))

# Then fetch program pipeline:
from policydb.queries import get_program_pipeline
program_pipeline = get_program_pipeline(conn, client_id=client_id, window_days=120)
```

Add `program_pipeline` to the template context passed to `_tab_policies.html`.

- [ ] **Step 2: Update `_renewal_pipeline_mini.html` to show program rows**

Before the existing status-grouped loop, add a program section:

```html
{# Program renewal rows #}
{% if program_pipeline %}
  {% for pgm in program_pipeline %}
  <div class="flex-shrink-0 min-w-[160px] max-w-[200px]">
    <div class="text-xs font-medium text-gray-500 mb-1.5 flex items-center gap-1">
      {{ pgm.program_name }}
      <span class="bg-indigo-100 text-indigo-700 rounded-full px-1.5 py-0.5 text-[10px] leading-none">{{ pgm.policy_count }} policies</span>
    </div>
    <a href="/programs/{{ pgm.program_uid }}"
       class="block border border-indigo-200 rounded-lg px-2.5 py-2 hover:border-marsh hover:shadow-sm transition-all bg-indigo-50/30 cursor-pointer">
      <div class="text-xs font-medium text-gray-900">{{ pgm.carriers_list[:30] }}{% if pgm.carriers_list|length > 30 %}…{% endif %}</div>
      <div class="text-xs text-gray-500">{{ pgm.total_premium | currency_short }}</div>
      <div class="flex items-center justify-between mt-1">
        <span class="text-xs px-1.5 py-0.5 rounded
          {% if pgm.renewal_status == 'Bound' %}bg-green-50 text-green-700
          {% elif pgm.renewal_status == 'In Progress' %}bg-blue-50 text-blue-700
          {% else %}bg-gray-100 text-gray-600{% endif %}">{{ pgm.renewal_status or 'Not Started' }}</span>
        <span class="text-xs font-medium
          {% if pgm.days_to_renewal <= 30 %}text-red-600
          {% elif pgm.days_to_renewal <= 60 %}text-amber-600
          {% else %}text-gray-500{% endif %}">{{ pgm.days_to_renewal }}d</span>
      </div>
    </a>
  </div>
  {% endfor %}
{% endif %}
```

- [ ] **Step 3: Verify templates parse**

Run: `python -c "from jinja2 import Environment; env = Environment(); env.parse(open('src/policydb/web/templates/clients/_renewal_pipeline_mini.html').read()); print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_renewal_pipeline_mini.html
git commit -m "feat: collapse program children in client renewal pipeline mini-view"
```

---

### Task 3: Main Renewal Pipeline Program Rows

**Files:**
- Modify: `src/policydb/web/routes/activities.py` (renewal pipeline handler)
- Modify: relevant renewal pipeline templates

- [ ] **Step 1: Find the renewal pipeline page handler**

The renewals page is at `/renewals` in `activities.py`. Find where `get_renewal_pipeline()` is called and add `get_program_pipeline()` alongside it. Pass `program_rows` to the template.

- [ ] **Step 2: Add program rows to the renewal pipeline template**

The renewal pipeline template renders rows in a table. Add program rows with a distinct style (indigo left border, program name, policy count badge, expandable child list). Use an `hx-get` on click to lazy-load child policies for the expanded view.

Program rows should be interleaved with standalone policy rows, sorted by `days_to_renewal`.

- [ ] **Step 3: Create expandable child partial**

Create `src/policydb/web/templates/policies/_program_pipeline_children.html` — a partial that renders child policies in a compact nested table, loaded via `GET /programs/{program_uid}/pipeline-children`.

Add the route handler in `programs.py`:

```python
@router.get("/programs/{program_uid}/pipeline-children", response_class=HTMLResponse)
def program_pipeline_children(request: Request, program_uid: str, conn=Depends(get_db)):
    pgm = get_program_by_uid(conn, program_uid)
    if not pgm:
        return HTMLResponse("", status_code=404)
    children = get_program_child_policies(conn, pgm["id"])
    return templates.TemplateResponse("policies/_program_pipeline_children.html", {
        "request": request,
        "children": children,
        "program_uid": program_uid,
    })
```

- [ ] **Step 4: Verify and commit**

```bash
python -c "import ast; ast.parse(open('src/policydb/web/routes/programs.py').read()); print('OK')"
git add -A
git commit -m "feat: program rows in main renewal pipeline with expandable children"
```

---

### Task 4: Program Activity Logging

**Files:**
- Modify: `src/policydb/web/routes/programs.py`
- Modify: `src/policydb/web/templates/programs/_tab_overview.html`
- Modify: `src/policydb/web/templates/programs/_tab_activity.html`

- [ ] **Step 1: Add POST endpoint for program activity logging**

In `programs.py`, add:

```python
@router.post("/programs/{program_uid}/log", response_class=HTMLResponse)
def program_log_activity(
    request: Request,
    program_uid: str,
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    duration_hours: str = Form(""),
    follow_up_date: str = Form(""),
    disposition: str = Form(""),
    contact_person: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import round_duration
    pgm = _get_program_or_404(conn, program_uid)
    account_exec = cfg.get("default_account_exec", "")
    dur = round_duration(duration_hours)

    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, program_id, activity_type, subject, details,
            follow_up_date, duration_hours, disposition, contact_person, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), pgm["client_id"], pgm["id"],
         activity_type, subject.strip(), details.strip() or None,
         follow_up_date or None, dur, disposition.strip() or None,
         contact_person.strip() or None, account_exec),
    )
    conn.commit()
    # Reload activity tab
    return HTMLResponse(
        '<script>htmx.ajax("GET", "/programs/' + program_uid + '/tab/activity", {target: ".tab-content", swap: "innerHTML"});</script>'
    )
```

- [ ] **Step 2: Add quick-log form to overview tab**

In `_tab_overview.html`, add a compact log form after the child policies grid (before the unassigned panel). Use the same field pattern as existing activity log forms:

```html
{# -- Quick Log -- #}
<div class="card mb-4 no-print">
  <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-100">
    <span class="text-xs font-bold text-marsh uppercase tracking-wide">Log Activity</span>
  </div>
  <form hx-post="/programs/{{ program.program_uid }}/log"
        hx-target=".tab-content" hx-swap="innerHTML"
        class="px-4 py-3 flex flex-wrap gap-2 items-end">
    <div>
      <label class="text-[10px] text-gray-400 block mb-0.5">Type</label>
      <select name="activity_type" class="text-xs border border-gray-300 rounded px-2 py-1.5">
        {% for t in activity_types %}<option>{{ t }}</option>{% endfor %}
      </select>
    </div>
    <div class="flex-1 min-w-[200px]">
      <label class="text-[10px] text-gray-400 block mb-0.5">Subject</label>
      <input type="text" name="subject" required placeholder="What happened..."
             class="w-full text-xs border border-gray-300 rounded px-2 py-1.5 focus:ring-marsh focus:border-marsh">
    </div>
    <div class="w-20">
      <label class="text-[10px] text-gray-400 block mb-0.5">Hours</label>
      <input type="number" name="duration_hours" step="any" min="0" placeholder="hrs"
             class="w-full text-xs border border-gray-300 rounded px-2 py-1.5 focus:ring-marsh focus:border-marsh">
    </div>
    <div>
      <label class="text-[10px] text-gray-400 block mb-0.5">Follow-up</label>
      <input type="date" name="follow_up_date"
             class="text-xs border border-gray-300 rounded px-2 py-1.5 focus:ring-marsh focus:border-marsh">
    </div>
    <button type="submit"
            class="text-xs bg-marsh text-white px-3 py-1.5 rounded hover:bg-marsh/90 transition-colors">Log</button>
  </form>
</div>
```

Pass `activity_types` to the overview tab context: `"activity_types": cfg.get("activity_types", [])`.

- [ ] **Step 3: Update program Activity tab to include program-scoped activities**

In the Activity tab route handler (`program_tab_activity` in `programs.py`), the current query only fetches activities where `policy_id` matches a child policy. Add a UNION for program-level activities:

```sql
-- Add to existing query:
UNION ALL
SELECT a.*, c.name AS client_name, c.cn_number,
       NULL AS policy_uid, NULL AS policy_type, NULL AS carrier
FROM activity_log a
JOIN clients c ON a.client_id = c.id
WHERE a.program_id = ? AND a.policy_id IS NULL
```

In `_tab_activity.html`, handle rows where `policy_uid` is NULL by showing "Program" instead of the policy link.

- [ ] **Step 4: Verify and commit**

```bash
python -c "import ast; ast.parse(open('src/policydb/web/routes/programs.py').read()); print('OK')"
git add -A
git commit -m "feat: program-level activity logging with quick-log form"
```

---

### Task 5: Issue Create Slideover — Program Field

**Files:**
- Modify: `src/policydb/web/templates/_issue_create_slideover.html`

- [ ] **Step 1: Add hidden `program_id` field to the issue create form**

The backend already accepts `program_id` (confirmed in `issues.py` line 38). Add a hidden input to the form:

```html
<input type="hidden" name="program_id" id="issue-program-id" value="0">
```

- [ ] **Step 2: Update `openIssueCreateSlideover()` to accept `program_id`**

In the slideover's JS initialization function, add `program_id` to the accepted params:

```javascript
if (params.program_id) {
  document.getElementById('issue-program-id').value = params.program_id;
}
```

- [ ] **Step 3: Add escalate button to program overview activity rows**

When program activities are rendered (from Task 4), include the escalate button that passes `program_id`:

```javascript
openIssueCreateSlideover({
  subject: '...',
  client_id: '{{ program.client_id }}',
  program_id: '{{ program.id }}',
  context_label: 'Creating from program activity'
})
```

- [ ] **Step 4: Show program issues on program detail page**

Add an issues section to `_tab_overview.html` that queries and displays program-linked issues. Use the existing `_issue_badge.html` pattern.

- [ ] **Step 5: Verify and commit**

```bash
git add -A
git commit -m "feat: program-linked issues via slideover + display on program page"
```

---

### Task 6: Follow-up Query Program Context

**Files:**
- Modify: `src/policydb/queries.py` (`get_all_followups` ~line 543)
- Modify: `src/policydb/web/templates/action_center/_followup_sections.html`

- [ ] **Step 1: Add `program_id` and program name to `get_all_followups()` activity branch**

In the first UNION branch (activity-sourced follow-ups, ~line 558), add:

```sql
a.program_id,
pg.name AS program_name,
pg.program_uid,
```

And add the JOIN: `LEFT JOIN programs pg ON a.program_id = pg.id`

Add the same columns (as NULLs) to the other 3 UNION branches to keep column counts aligned.

- [ ] **Step 2: Display program name in follow-up rows**

In `_followup_sections.html`, after the client name link, add a program pill when `item.program_name` is set:

```html
{% if item.program_name %}
<a href="/programs/{{ item.program_uid }}"
   class="text-[10px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded hover:bg-indigo-100">
  {{ item.program_name }}
</a>
{% endif %}
```

- [ ] **Step 3: Verify and commit**

```bash
python -c "import ast; ast.parse(open('src/policydb/queries.py').read()); print('OK')"
git add -A
git commit -m "feat: program context in Action Center follow-ups"
```

---

### Task 7: Final Integration Verification

- [ ] **Step 1: Restart server**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
pip install -e . -q
uvicorn policydb.web.app:app --host 127.0.0.1 --port 8000 &
sleep 3
```

- [ ] **Step 2: Verify client page pipeline collapse**

Navigate to a client with programs. Verify:
- Program appears as a single card in the pipeline mini-view
- Child policies of that program do NOT appear as individual cards
- Standalone policies still appear normally

- [ ] **Step 3: Verify program activity logging**

Navigate to a program's overview tab. Log an activity via the quick-log form. Verify it appears in the Activity tab.

- [ ] **Step 4: Verify program issues**

From a program activity, click escalate. Verify the issue create slideover has `program_id` populated. Create the issue and verify it appears on the program page.

- [ ] **Step 5: Verify Action Center follow-ups**

Log a program activity with a follow-up date. Navigate to Action Center → Follow-ups. Verify the program name pill appears on the follow-up row.

- [ ] **Step 6: Final commit and PR**

```bash
git add -A
git commit -m "feat: program lifecycle entity — Phase 1 complete"
```
