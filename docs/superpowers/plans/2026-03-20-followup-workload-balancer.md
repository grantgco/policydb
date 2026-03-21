# Follow-Up Workload Balancer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Plan Week view that visualizes Mon-Fri follow-up load with color-coded columns, auto-spread for overloaded days, and drag-to-rebalance.

**Architecture:** New route + template under the activities router. Spread algorithm as a query function. Drag uses HTML5 drag-and-drop with fetch to the existing reschedule endpoint. All follow-up sources (activity, policy, client) included via `get_all_followups()` pattern.

**Tech Stack:** FastAPI, Jinja2, HTMX, vanilla JS (HTML5 Drag and Drop), Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-03-20-followup-workload-balancer-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/web/templates/followups/plan.html` | Plan Week page |
| Modify | `src/policydb/web/routes/activities.py` | Plan Week route, spread endpoint, apply-spread endpoint |
| Modify | `src/policydb/queries.py` | `get_week_followups()` and `spread_followups()` functions |
| Modify | `src/policydb/config.py` | Add `daily_followup_target` and `pin_renewal_days` defaults |
| Modify | `src/policydb/web/templates/followups.html` | Add "Plan Week" link |

---

### Task 1: Config + Query Functions

**Files:**
- Modify: `src/policydb/config.py`
- Modify: `src/policydb/queries.py`

- [ ] **Step 1: Add config defaults**

In `src/policydb/config.py`, add to `_DEFAULTS`:

```python
"daily_followup_target": 5,
"pin_renewal_days": 14,
```

- [ ] **Step 2: Add `get_week_followups()` to queries.py**

Add at the end of `src/policydb/queries.py`:

```python
def get_week_followups(
    conn: sqlite3.Connection, week_start: str, pin_days: int = 14
) -> list[dict]:
    """Return all follow-ups for a Mon-Fri week (plus Sat/Sun bucketed into Monday).

    Each item includes a `pinned` flag based on renewal urgency.
    Items from Saturday/Sunday before the week are bucketed into Monday.
    """
    from datetime import date, timedelta
    mon = date.fromisoformat(week_start)
    # Include prior Sat/Sun so they show on Monday
    sat_before = (mon - timedelta(days=2)).isoformat()
    fri = (mon + timedelta(days=4)).isoformat()

    rows = conn.execute("""
        SELECT 'activity' AS source, a.id, a.subject, a.follow_up_date,
               a.activity_type, a.client_id, a.policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
          AND a.follow_up_date BETWEEN ? AND ?

        UNION ALL

        SELECT 'policy' AS source, p.id, ('Renewal: ' || p.policy_type) AS subject,
               p.follow_up_date, 'Policy Reminder' AS activity_type,
               p.client_id, p.id AS policy_id,
               c.name AS client_name,
               p.policy_type, p.carrier, p.expiration_date, p.renewal_status,
               CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal
        FROM policies p
        JOIN clients c ON p.client_id = c.id
        WHERE p.follow_up_date IS NOT NULL
          AND p.follow_up_date BETWEEN ? AND ?
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND NOT EXISTS (
              SELECT 1 FROM activity_log a2
              WHERE a2.policy_id = p.id AND a2.follow_up_done = 0
              AND a2.follow_up_date IS NOT NULL
          )

        ORDER BY follow_up_date
    """, (sat_before, fri, sat_before, fri)).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        fu_date = d["follow_up_date"]
        # Bucket Sat/Sun into Monday
        try:
            fu = date.fromisoformat(fu_date)
            if fu.weekday() >= 5:  # Saturday=5, Sunday=6
                d["follow_up_date"] = mon.isoformat()
                d["bucketed_from"] = fu_date
        except (ValueError, TypeError):
            pass
        # Pin logic
        dtr = d.get("days_to_renewal")
        status = d.get("renewal_status") or ""
        d["pinned"] = bool(
            (dtr is not None and dtr <= pin_days)
            or status.upper() in ("EXPIRED",)
        )
        # Composite ID for reschedule (matches bulk-reschedule pattern)
        d["composite_id"] = f"{d['source']}-{d['id']}"
        items.append(d)
    return items


def spread_followups(
    items: list[dict], daily_target: int, week_days: list[str]
) -> list[dict]:
    """Compute proposed redistribution of follow-ups across the week.

    Returns list of {composite_id, old_date, new_date} for items that should move.
    Only moves non-pinned items from days exceeding daily_target.
    Fills lightest days first.
    """
    from collections import defaultdict

    # Group by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_date[item["follow_up_date"]].append(item)

    # Ensure all week days are in the map
    for d in week_days:
        by_date.setdefault(d, [])

    # Identify overloaded days and collect movable items
    movable_pool: list[dict] = []
    for d in week_days:
        day_items = by_date[d]
        total = len(day_items)
        if total > daily_target:
            # Collect non-pinned items from this day (keep pinned in place)
            pinned_count = sum(1 for i in day_items if i.get("pinned"))
            movable = [i for i in day_items if not i.get("pinned")]
            # Only move excess items
            excess = total - max(daily_target, pinned_count)
            if excess > 0:
                movable_pool.extend(movable[:excess])

    # Remove movable items from their current days
    for item in movable_pool:
        by_date[item["follow_up_date"]].remove(item)

    # Assign each movable item to the lightest day
    proposals: list[dict] = []
    for item in movable_pool:
        lightest_day = min(week_days, key=lambda d: len(by_date[d]))
        by_date[lightest_day].append(item)
        proposals.append({
            "composite_id": item["composite_id"],
            "old_date": item["follow_up_date"],
            "new_date": lightest_day,
            "subject": item.get("subject", ""),
            "client_name": item.get("client_name", ""),
        })

    return proposals
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/config.py src/policydb/queries.py
git commit -m "feat: week followup query and spread algorithm"
```

---

### Task 2: Plan Week Route + Endpoints

**Files:**
- Modify: `src/policydb/web/routes/activities.py`

- [ ] **Step 1: Add Plan Week GET route**

Add to `src/policydb/web/routes/activities.py`:

```python
@router.get("/followups/plan", response_class=HTMLResponse)
def followups_plan(request: Request, week_start: str = "", conn=Depends(get_db)):
    """Plan Week view — visualize and rebalance follow-up workload."""
    from datetime import date, timedelta
    from policydb.queries import get_week_followups
    from collections import defaultdict

    # Default to current week's Monday
    today = date.today()
    if week_start:
        try:
            mon = date.fromisoformat(week_start)
        except ValueError:
            mon = today - timedelta(days=today.weekday())
    else:
        mon = today - timedelta(days=today.weekday())

    week_days = [(mon + timedelta(days=i)).isoformat() for i in range(5)]
    pin_days = cfg.get("pin_renewal_days", 14)
    target = cfg.get("daily_followup_target", 5)

    items = get_week_followups(conn, mon.isoformat(), pin_days)

    # Group by date
    by_date = defaultdict(list)
    for item in items:
        by_date[item["follow_up_date"]].append(item)

    columns = []
    for d in week_days:
        day_items = by_date.get(d, [])
        day_date = date.fromisoformat(d)
        columns.append({
            "date": d,
            "label": day_date.strftime("%a %b %d"),
            "items": day_items,
            "count": len(day_items),
            "pinned_count": sum(1 for i in day_items if i.get("pinned")),
        })

    prev_week = (mon - timedelta(days=7)).isoformat()
    next_week = (mon + timedelta(days=7)).isoformat()
    this_monday = (today - timedelta(days=today.weekday())).isoformat()

    return templates.TemplateResponse("followups/plan.html", {
        "request": request,
        "active": "followups",
        "columns": columns,
        "week_start": mon.isoformat(),
        "week_label": f"{mon.strftime('%b %d')} – {(mon + timedelta(days=4)).strftime('%b %d, %Y')}",
        "prev_week": prev_week,
        "next_week": next_week,
        "this_monday": this_monday,
        "daily_target": target,
        "total_items": len(items),
    })
```

**Important:** This route MUST be registered BEFORE the `/followups/{path}` catch-all patterns. Place it early in the file or ensure no wildcard route shadows it.

- [ ] **Step 2: Add spread preview endpoint**

```python
@router.post("/followups/plan/spread", response_class=HTMLResponse)
def followups_spread(request: Request, week_start: str = Form(...), conn=Depends(get_db)):
    """Compute and return proposed spread for the week."""
    from datetime import date, timedelta
    from policydb.queries import get_week_followups, spread_followups
    import json

    mon = date.fromisoformat(week_start)
    week_days = [(mon + timedelta(days=i)).isoformat() for i in range(5)]
    pin_days = cfg.get("pin_renewal_days", 14)
    target = cfg.get("daily_followup_target", 5)

    items = get_week_followups(conn, week_start, pin_days)
    proposals = spread_followups(items, target, week_days)

    if not proposals:
        return HTMLResponse("", headers={
            "HX-Trigger": '{"activityLogged": "Week is already balanced"}'
        })

    # Return proposals as JSON for the JS to preview
    return JSONResponse({
        "proposals": proposals,
        "count": len(proposals),
    })
```

- [ ] **Step 3: Add apply-spread endpoint**

```python
@router.post("/followups/plan/apply-spread")
async def followups_apply_spread(request: Request, conn=Depends(get_db)):
    """Apply proposed spread — batch reschedule follow-ups."""
    body = await request.json()
    moves = body.get("moves", [])
    count = 0
    for move in moves:
        cid = move.get("composite_id", "")
        new_date = move.get("new_date", "")
        if not cid or not new_date:
            continue
        source, item_id = cid.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        count += 1
    conn.commit()
    return JSONResponse({"ok": True, "count": count})
```

- [ ] **Step 4: Add single-item drag reschedule endpoint**

```python
@router.post("/followups/plan/move")
async def followups_plan_move(request: Request, conn=Depends(get_db)):
    """Drag-and-drop reschedule a single follow-up."""
    body = await request.json()
    cid = body.get("composite_id", "")
    new_date = body.get("new_date", "")
    if not cid or not new_date:
        return JSONResponse({"ok": False})
    source, item_id = cid.split("-", 1)
    if source == "activity":
        conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
    elif source == "policy":
        conn.execute("UPDATE policies SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
    conn.commit()
    return JSONResponse({"ok": True})
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/activities.py
git commit -m "feat: plan week route with spread and drag-reschedule endpoints"
```

---

### Task 3: Plan Week Template

**Files:**
- Create: `src/policydb/web/templates/followups/plan.html`
- Modify: `src/policydb/web/templates/followups.html`

- [ ] **Step 1: Create plan.html template**

Create `src/policydb/web/templates/followups/plan.html`:

```html
{% extends "base.html" %}
{% block title %}Plan Week — Coverage{% endblock %}

{% block content %}
<div class="flex items-center justify-between mb-4">
  <div class="flex items-center gap-3">
    <h1 class="text-lg font-bold text-gray-900">Plan Week</h1>
    <span class="text-sm text-gray-500">{{ total_items }} follow-ups</span>
    <span class="text-xs text-gray-400">target: {{ daily_target }}/day</span>
  </div>
  <div class="flex items-center gap-3">
    <a href="/followups/plan?week_start={{ prev_week }}" class="text-xs text-gray-500 hover:text-marsh">&larr; Prev</a>
    <span class="text-sm font-medium text-gray-700">{{ week_label }}</span>
    <a href="/followups/plan?week_start={{ next_week }}" class="text-xs text-gray-500 hover:text-marsh">Next &rarr;</a>
    <a href="/followups/plan?week_start={{ this_monday }}" class="text-xs text-gray-400 hover:text-marsh ml-2">This Week</a>
    <button id="spread-btn" onclick="runSpread()"
      class="text-xs bg-amber-100 text-amber-700 px-3 py-1.5 rounded hover:bg-amber-200 transition-colors font-medium ml-3">
      Spread
    </button>
    <a href="/followups" class="text-xs text-gray-400 hover:text-marsh">&larr; Follow-Ups</a>
  </div>
</div>

<!-- Spread preview bar (hidden by default) -->
<div id="spread-preview" class="hidden bg-amber-50 border border-amber-200 rounded-lg px-4 py-2 mb-4 flex items-center justify-between">
  <span class="text-sm text-amber-700" id="spread-msg"></span>
  <div class="flex gap-2">
    <button onclick="applySpread()" class="text-xs bg-amber-600 text-white px-3 py-1.5 rounded hover:bg-amber-700 font-medium">Apply Changes</button>
    <button onclick="cancelSpread()" class="text-xs text-gray-500 px-3 py-1.5 rounded hover:bg-gray-100">Cancel</button>
  </div>
</div>

<div class="grid grid-cols-5 gap-3" id="plan-grid">
  {% for col in columns %}
  <div class="card overflow-hidden" data-date="{{ col.date }}"
    ondragover="event.preventDefault();this.classList.add('ring-2','ring-marsh')"
    ondragleave="this.classList.remove('ring-2','ring-marsh')"
    ondrop="handleDrop(event, '{{ col.date }}');this.classList.remove('ring-2','ring-marsh')">
    {# Column header #}
    <div class="px-3 py-2 border-b border-gray-100 flex items-center justify-between
      {% if col.count > daily_target * 2 %}bg-red-50{% elif col.count > daily_target %}bg-amber-50{% else %}bg-green-50/50{% endif %}">
      <span class="text-xs font-bold {% if col.count > daily_target * 2 %}text-red-700{% elif col.count > daily_target %}text-amber-700{% else %}text-green-700{% endif %}">
        {{ col.label }}
      </span>
      <span class="text-xs font-mono {% if col.count > daily_target %}font-bold{% endif %}
        {% if col.count > daily_target * 2 %}text-red-600{% elif col.count > daily_target %}text-amber-600{% else %}text-gray-400{% endif %}"
        id="count-{{ col.date }}">{{ col.count }}</span>
    </div>
    {# Items #}
    <div class="p-2 space-y-1 min-h-[200px]" id="items-{{ col.date }}">
      {% for item in col.items %}
      <div class="rounded border px-2 py-1.5 text-xs
        {% if item.pinned %}border-red-200 bg-red-50/30{% else %}border-gray-200 bg-white hover:border-gray-300{% endif %}"
        id="card-{{ item.composite_id }}"
        data-composite-id="{{ item.composite_id }}"
        {% if not item.pinned %}draggable="true" ondragstart="handleDragStart(event, '{{ item.composite_id }}')"{% endif %}>
        <div class="flex items-start gap-1.5">
          {% if item.pinned %}
          <span class="text-red-400 text-[10px] mt-0.5" title="Pinned — urgent/expiring">&#128274;</span>
          {% else %}
          <span class="text-gray-300 cursor-grab text-[10px] mt-0.5" title="Drag to move">&#9776;</span>
          {% endif %}
          <div class="flex-1 min-w-0">
            <p class="font-medium text-gray-800 truncate">{{ item.subject }}</p>
            <p class="text-gray-400 truncate">{{ item.client_name }}{% if item.policy_type %} · {{ item.policy_type }}{% endif %}</p>
            {% if item.pinned and item.days_to_renewal is not none %}
            <p class="text-red-500 text-[10px]">{{ item.days_to_renewal }}d to renewal</p>
            {% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>

<script>
var _dragId = null;
var _spreadProposals = null;

function handleDragStart(e, compositeId) {
  _dragId = compositeId;
  e.dataTransfer.effectAllowed = 'move';
  e.target.style.opacity = '0.5';
  e.target.addEventListener('dragend', function() { this.style.opacity = '1'; }, {once: true});
}

function handleDrop(e, newDate) {
  e.preventDefault();
  if (!_dragId) return;
  var card = document.getElementById('card-' + _dragId);
  if (!card) return;
  // Move the card visually
  var targetItems = document.getElementById('items-' + newDate);
  targetItems.appendChild(card);
  // Update counts
  updateCounts();
  // Save to server
  fetch('/followups/plan/move', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({composite_id: _dragId, new_date: newDate})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok && typeof showToast === 'function') showToast('Moved', true);
  });
  _dragId = null;
}

function updateCounts() {
  document.querySelectorAll('[id^="items-"]').forEach(function(container) {
    var date = container.id.replace('items-', '');
    var count = container.children.length;
    var countEl = document.getElementById('count-' + date);
    if (countEl) countEl.textContent = count;
    // Update header color
    var header = container.previousElementSibling;
    var target = {{ daily_target }};
    header.className = header.className
      .replace(/bg-\w+-50\/?(?:\d+)?/g, '')
      .trim();
    if (count > target * 2) header.classList.add('bg-red-50');
    else if (count > target) header.classList.add('bg-amber-50');
    else header.classList.add('bg-green-50/50');
  });
}

function runSpread() {
  fetch('/followups/plan/spread', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'week_start={{ week_start }}'
  }).then(function(r) {
    if (r.headers.get('HX-Trigger')) {
      // Already balanced
      if (typeof showToast === 'function') showToast('Week is already balanced', true);
      return null;
    }
    return r.json();
  }).then(function(data) {
    if (!data) return;
    _spreadProposals = data.proposals;
    // Highlight proposed moves
    data.proposals.forEach(function(p) {
      var card = document.getElementById('card-' + p.composite_id);
      if (card) {
        card.style.borderColor = '#f59e0b';
        card.style.borderWidth = '2px';
        // Move visually
        var targetItems = document.getElementById('items-' + p.new_date);
        if (targetItems) targetItems.appendChild(card);
      }
    });
    updateCounts();
    document.getElementById('spread-msg').textContent = data.count + ' item(s) would be moved';
    document.getElementById('spread-preview').classList.remove('hidden');
  });
}

function applySpread() {
  if (!_spreadProposals) return;
  fetch('/followups/plan/apply-spread', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({moves: _spreadProposals})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok) {
      document.getElementById('spread-preview').classList.add('hidden');
      _spreadProposals = null;
      // Remove highlights
      document.querySelectorAll('[style*="border-color"]').forEach(function(el) {
        el.style.borderColor = ''; el.style.borderWidth = '';
      });
      if (typeof showToast === 'function') showToast(data.count + ' follow-ups rescheduled', true);
    }
  });
}

function cancelSpread() {
  document.getElementById('spread-preview').classList.add('hidden');
  _spreadProposals = null;
  // Reload to reset positions
  location.reload();
}
</script>
{% endblock %}
```

- [ ] **Step 2: Add "Plan Week" link to follow-ups page**

In `src/policydb/web/templates/followups.html`, find the page header area and add a link:

```html
<a href="/followups/plan" class="text-xs bg-amber-100 text-amber-700 px-3 py-1 rounded hover:bg-amber-200 transition-colors font-medium">
  Plan Week
</a>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/followups/plan.html src/policydb/web/templates/followups.html
git commit -m "feat: plan week template with drag-and-drop and spread preview"
```

---

### Task 4: Manual Test + Fixes

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

- [ ] **Step 2: Manual test checklist**

1. **Plan Week loads:** Navigate to /followups/plan → 5 columns show Mon-Fri with follow-ups
2. **Color coding:** Day with >5 items is amber, >10 is red, ≤5 is green
3. **Pinned items:** Items with expiring policies show lock icon, no drag handle
4. **Drag:** Drag a non-pinned item from overloaded day to lighter day → card moves, count updates, toast confirms
5. **Spread:** Click Spread → proposals highlighted in amber, preview bar shows count → Apply → items saved → Cancel → page reloads
6. **Week nav:** Click Prev/Next → shows previous/next week. "This Week" returns to current.
7. **Follow-ups link:** Follow-ups page has "Plan Week" button. Plan Week has "Follow-Ups" link back.
8. **Weekend bucketing:** Items scheduled for Saturday/Sunday show on Monday

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: plan week adjustments from manual testing"
```
