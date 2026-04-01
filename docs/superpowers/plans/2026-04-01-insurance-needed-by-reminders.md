# Insurance Needed By — Reminders & Urgency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `insurance_needed_by` field on the project pipeline actionable — escalating suggested follow-ups in the Action Center and visual countdown badges on the pipeline row.

**Architecture:** New query function in `queries.py` returns insurance deadline suggestions. Action Center merges them into the existing suggested section with a distinct visual treatment. Pipeline row template adds a colored countdown pill next to the date input. Config defaults + Settings UI for thresholds.

**Tech Stack:** Python/SQLite queries, Jinja2 templates, Tailwind CSS, existing config system.

---

### Task 1: Add Config Defaults

**Files:**
- Modify: `src/policydb/config.py` (add to `_DEFAULTS` dict)
- Modify: `src/policydb/web/routes/settings.py` (add to `EDITABLE_LISTS`)

- [ ] **Step 1: Add config defaults**

In `src/policydb/config.py`, find the `_DEFAULTS` dict and add these two keys near the existing `"project_stages"` entry:

```python
"insurance_reminder_tiers": [30, 14, 7],
"insurance_completed_stages": ["Bound", "Active", "Complete"],
```

- [ ] **Step 2: Add to EDITABLE_LISTS in settings.py**

In `src/policydb/web/routes/settings.py`, add to the `EDITABLE_LISTS` dict after the `"project_types"` entry:

```python
"insurance_completed_stages": "Insurance — Completed Stages",
```

Note: `insurance_reminder_tiers` is a list of integers, not strings, so it should NOT go in `EDITABLE_LISTS` (which manages string lists). The tiers are rarely changed and the default `[30, 14, 7]` is sufficient. If needed later, a dedicated UI can be added.

- [ ] **Step 3: Verify config loads**

Run:
```bash
$HOME/.policydb/venv/bin/python -c "import policydb.config as cfg; print(cfg.get('insurance_reminder_tiers')); print(cfg.get('insurance_completed_stages'))"
```

Expected: `[30, 14, 7]` and `['Bound', 'Active', 'Complete']`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py
git commit -m "feat: add insurance reminder config defaults and settings entry"
```

---

### Task 2: Query Function for Insurance Deadline Suggestions

**Files:**
- Modify: `src/policydb/queries.py` (add new function after `get_suggested_followups`)

- [ ] **Step 1: Add `get_insurance_deadline_suggestions()` function**

Add this function in `src/policydb/queries.py` after the `get_suggested_followups()` function (around line 1486):

```python
def get_insurance_deadline_suggestions(
    conn: sqlite3.Connection,
    client_ids: list[int] | None = None,
) -> list[dict]:
    """Return project pipeline items approaching their insurance_needed_by deadline.

    Returns suggestions for projects where:
    - insurance_needed_by is set and in the future
    - project stage is NOT in insurance_completed_stages
    - deadline is within the largest tier window

    Each result includes a tier label (Normal/High/Urgent) based on days remaining.
    """
    import policydb.config as cfg

    tiers = cfg.get("insurance_reminder_tiers", [30, 14, 7])
    completed = cfg.get("insurance_completed_stages", ["Bound", "Active", "Complete"])
    if not tiers:
        return []

    max_window = max(tiers)
    tiers_sorted = sorted(tiers, reverse=True)  # e.g. [30, 14, 7]

    client_clause = ""
    client_params: list = []
    if client_ids:
        placeholders = ",".join("?" * len(client_ids))
        client_clause = f"AND p.client_id IN ({placeholders})"
        client_params = list(client_ids)

    stage_clause = ""
    stage_params: list = []
    if completed:
        placeholders = ",".join("?" * len(completed))
        stage_clause = f"AND (p.project_stage IS NULL OR p.project_stage NOT IN ({placeholders}))"
        stage_params = list(completed)

    sql = f"""
    SELECT p.id AS project_id, p.name AS project_name, p.insurance_needed_by,
           p.project_stage, p.client_id,
           c.name AS client_name,
           CAST(julianday(p.insurance_needed_by) - julianday('now') AS INTEGER) AS days_remaining
    FROM projects p
    JOIN clients c ON p.client_id = c.id
    WHERE p.insurance_needed_by IS NOT NULL
      AND julianday(p.insurance_needed_by) - julianday('now') > 0
      AND julianday(p.insurance_needed_by) - julianday('now') <= ?
      AND p.project_type != 'Location'
      AND c.archived = 0
      {stage_clause}
      {client_clause}
    ORDER BY p.insurance_needed_by ASC
    """
    params = [max_window] + stage_params + client_params
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Assign tier label based on days remaining
    for row in rows:
        days = row["days_remaining"]
        if days <= tiers_sorted[-1]:      # e.g. <= 7
            row["tier"] = "Urgent"
        elif days <= tiers_sorted[-2] if len(tiers_sorted) > 1 else False:  # e.g. <= 14
            row["tier"] = "High"
        else:
            row["tier"] = "Normal"
        row["subject"] = f"Insurance needed in {days}d — {row['project_name']}"

    return rows
```

- [ ] **Step 2: Verify the function loads**

Run:
```bash
$HOME/.policydb/venv/bin/python -c "from policydb.queries import get_insurance_deadline_suggestions; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: add get_insurance_deadline_suggestions() query function"
```

---

### Task 3: Wire Into Action Center Follow-ups Tab

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` (import + call + pass to template)
- Modify: `src/policydb/web/templates/action_center/_followups.html` (render insurance suggestions)

- [ ] **Step 1: Import and call the new function in action_center.py**

In `src/policydb/web/routes/action_center.py`, add the import alongside the existing one at the top of the file where `get_suggested_followups` is imported:

```python
from policydb.queries import get_insurance_deadline_suggestions
```

Then in the `_followups_tab()` function, after line 186 (`suggested = get_suggested_followups(...)`), add:

```python
insurance_suggestions = get_insurance_deadline_suggestions(conn, client_ids=filter_client_ids)
```

And apply the same `q` (client name search) filter that applies to `suggested`:

After the existing `q` filter block (around line 195), add:

```python
    if q:
        insurance_suggestions = [r for r in insurance_suggestions if q_lower in r.get("client_name", "").lower()]
```

- [ ] **Step 2: Pass to template context**

Find the template context dict (around line 510) and add `insurance_suggestions`:

```python
"insurance_suggestions": insurance_suggestions,
```

- [ ] **Step 3: Add the insurance suggestions section to the follow-ups template**

In `src/policydb/web/templates/action_center/_followups.html`, add a new section just BEFORE the existing `{# suggested #}` section (before line 194). This section renders insurance deadline suggestions with a distinct amber/orange visual treatment:

```html
{# ── Insurance deadline suggestions ── #}
{% if insurance_suggestions %}
<div class="mb-5 fu-section" data-section="suggested">
  <div class="flex items-center gap-2 mb-2">
    <span class="text-[10px] font-bold uppercase tracking-widest text-amber-600">Insurance Deadlines</span>
    <span class="bg-amber-100 text-amber-700 text-[10px] font-bold px-2 py-0.5 rounded-full">{{ insurance_suggestions | length }}</span>
    <span class="text-[10px] text-gray-400">&mdash; project insurance placements approaching deadline</span>
  </div>
  <div class="space-y-1">
    {% for s in insurance_suggestions %}
    <div class="fu-row bg-amber-50 rounded-lg border border-amber-100 hover:border-amber-200 transition-colors" data-status="suggested">
      <div class="flex items-center gap-3 px-4 py-3">
        <div class="w-2 h-2 rounded-full flex-shrink-0
          {% if s.days_remaining <= 7 %}bg-red-500
          {% elif s.days_remaining <= 14 %}bg-orange-400
          {% else %}bg-amber-400{% endif %}"></div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <a href="/clients/{{ s.client_id }}" class="text-sm font-semibold text-gray-900 hover:text-marsh hover:underline truncate">{{ s.client_name }}</a>
          </div>
          <div class="text-xs text-gray-600 mt-0.5">
            {{ s.project_name }}
            {% if s.project_stage %}
            <span class="text-gray-400">&middot; {{ s.project_stage }}</span>
            {% endif %}
          </div>
        </div>
        <div class="text-right flex-shrink-0">
          <div class="text-xs text-gray-500">{{ s.insurance_needed_by }}</div>
          <span class="text-[10px] px-1.5 py-0.5 rounded
            {% if s.days_remaining <= 7 %}bg-red-100 text-red-700
            {% elif s.days_remaining <= 14 %}bg-orange-100 text-orange-700
            {% else %}bg-amber-100 text-amber-700{% endif %}">
            {{ s.days_remaining }}d &middot; {{ s.tier }}
          </span>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 4: Update the empty state check**

In the same template, find the empty state condition (around line 249) and add `insurance_suggestions` to the check:

Change:
```
{% if not triage and not today_bucket and not overdue_bucket and not stale and not nudge_due and not (prep_coming is defined and prep_coming) and not watching and not scheduled and not suggested %}
```

To:
```
{% if not triage and not today_bucket and not overdue_bucket and not stale and not nudge_due and not (prep_coming is defined and prep_coming) and not watching and not scheduled and not suggested and not insurance_suggestions %}
```

- [ ] **Step 5: Update the suggested filter button count**

In the same template, find the suggested count variable (around line 11) and update it to include insurance suggestions:

Change:
```
{% set suggested_count = suggested | length %}
```

To:
```
{% set suggested_count = (suggested | length) + (insurance_suggestions | length if insurance_suggestions is defined else 0) %}
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/action_center.py src/policydb/web/templates/action_center/_followups.html
git commit -m "feat: wire insurance deadline suggestions into Action Center follow-ups"
```

---

### Task 4: Visual Urgency Badge on Pipeline Row

**Files:**
- Modify: `src/policydb/web/templates/clients/_project_pipeline_row.html` (add badge next to date)

- [ ] **Step 1: Add urgency badge to the pipeline row template**

In `src/policydb/web/templates/clients/_project_pipeline_row.html`, find the "Insurance Needed By" cell (around line 25-31). Replace the existing `<td>` block:

```html
  {# Insurance Needed By #}
  <td class="px-2 py-2 whitespace-nowrap">
    <input type="date"
           value="{{ p.insurance_needed_by or '' }}"
           class="text-xs border-0 border-b border-gray-200 focus:border-marsh focus:outline-none bg-transparent w-32 py-0.5"
           onchange="ppSaveDate(this, 'insurance_needed_by', {{ client.id }}, {{ p.id }})">
  </td>
```

With:

```html
  {# Insurance Needed By + urgency badge #}
  <td class="px-2 py-2 whitespace-nowrap">
    <div class="flex items-center gap-1.5">
      <input type="date"
             value="{{ p.insurance_needed_by or '' }}"
             class="text-xs border-0 border-b border-gray-200 focus:border-marsh focus:outline-none bg-transparent w-32 py-0.5"
             onchange="ppSaveDate(this, 'insurance_needed_by', {{ client.id }}, {{ p.id }})">
      {% if p.insurance_needed_by %}
        {% set _ins_days = ((p.insurance_needed_by | string)[:10] | default('9999-12-31')) %}
        {% set _ins_delta = ((_ins_days ~ 'T00:00:00') | default(none)) %}
        {# Compute days via a data attribute and inline JS — Jinja2 lacks date math #}
        <span class="ins-urgency-badge text-[10px] font-medium px-1.5 py-0.5 rounded hidden"
              data-deadline="{{ p.insurance_needed_by }}"></span>
      {% endif %}
    </div>
  </td>
```

- [ ] **Step 2: Add the badge computation script**

In the same template file, or in the parent `_project_pipeline.html` template, find the `<script>` section and add this function that runs on page load to compute and display all urgency badges:

Find the existing `<script>` tag in `src/policydb/web/templates/clients/_project_pipeline.html` and add this at the end of the script block:

```javascript
/* Insurance deadline urgency badges */
(function() {
  var badges = document.querySelectorAll('.ins-urgency-badge');
  var now = new Date();
  now.setHours(0,0,0,0);
  badges.forEach(function(badge) {
    var deadline = badge.dataset.deadline;
    if (!deadline) return;
    var d = new Date(deadline + 'T00:00:00');
    var diff = Math.ceil((d - now) / 86400000);
    var text, cls;
    if (diff < 0) {
      text = Math.abs(diff) + 'd overdue';
      cls = 'bg-red-200 text-red-900';
    } else if (diff <= 7) {
      text = diff + 'd';
      cls = 'bg-red-100 text-red-700';
    } else if (diff <= 14) {
      text = diff + 'd';
      cls = 'bg-orange-100 text-orange-700';
    } else if (diff <= 30) {
      text = diff + 'd';
      cls = 'bg-amber-100 text-amber-700';
    } else {
      text = diff + 'd';
      cls = 'bg-green-100 text-green-700';
    }
    badge.textContent = text;
    badge.className = 'ins-urgency-badge text-[10px] font-medium px-1.5 py-0.5 rounded ' + cls;
  });
})();
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_project_pipeline_row.html src/policydb/web/templates/clients/_project_pipeline.html
git commit -m "feat: add visual urgency countdown badge on pipeline insurance_needed_by"
```

---

### Task 5: QA Testing

**Files:** None (verification only)

- [ ] **Step 1: Start test server**

```bash
PORT=8037
lsof -ti :$PORT | xargs kill -9 2>/dev/null
$HOME/.policydb/venv/bin/uvicorn policydb.web.app:app --host 127.0.0.1 --port $PORT --log-level warning &
sleep 3
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PORT/
```

Expected: `200`

- [ ] **Step 2: Verify Action Center follow-ups tab**

Navigate to `http://127.0.0.1:$PORT/action-center?tab=followups` and take a screenshot. Verify the "Insurance Deadlines" section appears if there are projects with `insurance_needed_by` dates within 30 days.

- [ ] **Step 3: Verify pipeline urgency badges**

Navigate to a client page with project pipeline items that have `insurance_needed_by` dates set. Verify the colored countdown pills render next to the date inputs.

- [ ] **Step 4: Verify Settings page**

Navigate to `http://127.0.0.1:$PORT/settings` and verify "Insurance — Completed Stages" appears in the list management section.

- [ ] **Step 5: Kill test server**

```bash
lsof -ti :8037 | xargs kill -9 2>/dev/null
```

- [ ] **Step 6: Final commit (if any QA fixes needed)**

```bash
git add -A && git commit -m "fix: QA adjustments for insurance deadline reminders"
```

- [ ] **Step 7: Push**

```bash
git push
```
