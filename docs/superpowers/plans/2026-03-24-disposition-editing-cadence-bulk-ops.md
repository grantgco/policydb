# Disposition Editing, Cadence Enforcement & Bulk Operations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable one-click disposition editing on all follow-up rows, enforce follow-up cadence with visual indicators, and add bulk operations for managing follow-up queues at scale.

**Architecture:** New `POST /activities/update-disposition` and `POST /activities/bulk-action` endpoints handle source-aware updates using composite IDs. Cadence computation happens server-side in `_followups_ctx()`. Shared JS in `base.html` manages pill bar toggling and bulk mode state. All changes work across Action Center, client detail, and policy detail pages.

**Tech Stack:** FastAPI endpoints, Jinja2 templates, HTMX for partial swaps, vanilla JS for client-side interactions.

**Spec:** `docs/superpowers/specs/2026-03-24-disposition-editing-cadence-bulk-ops-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/policydb/web/routes/activities.py` | New `update-disposition` + `bulk-action` endpoints; add `disposition` param to `/activities/log` |
| `src/policydb/web/routes/action_center.py` | Cadence computation in `_followups_ctx()` |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Clickable disposition badge in `fu_row`/`triage_row`; cadence badge/heat; bulk checkboxes |
| `src/policydb/web/templates/activities/_activity_row.html` | Clickable disposition badge; cadence badge; bulk checkbox |
| `src/policydb/web/templates/activities/_activity_sections.html` | Bulk Edit toggle; floating action bar |
| `src/policydb/web/templates/clients/_tab_overview.html` | Disposition pills in client quick log form |
| `src/policydb/web/templates/policies/_tab_activity.html` | Disposition pills in policy quick log form (check if log form exists here) |
| `src/policydb/web/templates/base.html` | Shared JS: `toggleDispPills()`, `selectDispPill()`, `toggleBulkMode()`, `bulkAction()` |

---

### Task 1: Backend — `POST /activities/update-disposition` endpoint

**Files:**
- Modify: `src/policydb/web/routes/activities.py` (add new endpoint near line 632)

This is the core endpoint that handles inline disposition changes for all source types.

- [ ] **Step 1: Add the `update-disposition` endpoint**

Add after the existing `/activities/{activity_id}/field` PATCH endpoint (~line 632). Uses composite IDs and source-aware branching:

```python
@router.post("/activities/update-disposition", response_class=HTMLResponse)
def update_disposition(
    request: Request,
    composite_id: str = Form(...),
    disposition: str = Form(...),
    follow_up_date: str = Form(""),
    note: str = Form(""),
    context: str = Form(""),  # "action_center" | "client" | "policy"
    conn=Depends(get_db),
):
    """Update disposition on any follow-up item (activity, policy, or client source).

    For activity/project sources: updates existing activity_log row in place.
    For policy/client sources: auto-creates activity_log row and supersedes the reminder.
    """
    import policydb.config as cfg
    from policydb.queries import supersede_followups

    # Parse composite ID
    parts = composite_id.split("-", 1)
    if len(parts) != 2:
        return HTMLResponse("Invalid ID", status_code=400)
    source, item_id = parts[0], parts[1]

    # Look up disposition config for default_days
    dispositions = cfg.get("follow_up_dispositions", [])
    default_days = 0
    for d in dispositions:
        if d["label"].lower() == disposition.strip().lower():
            default_days = d.get("default_days", 0)
            break

    # Auto-compute follow_up_date if not provided
    if not follow_up_date and default_days > 0:
        from datetime import date, timedelta
        follow_up_date = (date.today() + timedelta(days=default_days)).isoformat()

    if source == "activity":
        activity_id = int(item_id)
        # Update disposition in place
        conn.execute(
            "UPDATE activity_log SET disposition = ? WHERE id = ?",
            (disposition.strip(), activity_id),
        )
        # Update follow_up_date if provided
        if follow_up_date:
            conn.execute(
                "UPDATE activity_log SET follow_up_date = ? WHERE id = ?",
                (follow_up_date, activity_id),
            )
        # Append note if provided
        if note.strip():
            conn.execute(
                """UPDATE activity_log SET details = CASE
                   WHEN details IS NOT NULL AND details != '' THEN details || char(10) || ?
                   ELSE ? END WHERE id = ?""",
                (note.strip(), note.strip(), activity_id),
            )
        # Timeline re-sync if policy-linked
        # NOTE: Do NOT call supersede_followups here — we are updating an existing
        # activity row in place, not creating a new one. supersede_followups would
        # mark THIS row as done, canceling the follow-up we just updated.
        row = conn.execute(
            "SELECT policy_id FROM activity_log WHERE id = ?", (activity_id,)
        ).fetchone()
        if row and row["policy_id"]:
            policy = conn.execute(
                "SELECT policy_uid FROM policies WHERE id = ?", (row["policy_id"],)
            ).fetchone()
            if policy:
                try:
                    from policydb.timeline_engine import update_timeline_from_followup
                    update_timeline_from_followup(
                        conn, policy["policy_uid"], None,
                        disposition.strip(), follow_up_date or None, waiting_on=None,
                    )
                except Exception:
                    pass

    elif source == "policy":
        # Auto-create activity_log row from policy reminder
        policy = conn.execute(
            """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.follow_up_date,
                      p.client_id, c.name AS client_name
               FROM policies p JOIN clients c ON p.client_id = c.id
               WHERE p.policy_uid = ?""",
            (item_id,),
        ).fetchone()
        if not policy:
            return HTMLResponse("Policy not found", status_code=404)
        fu_date = follow_up_date or policy["follow_up_date"]
        subject = f"{policy['policy_type']}"
        if policy["carrier"]:
            subject += f" — {policy['carrier']}"
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, subject,
                details, follow_up_date, disposition, follow_up_done)
               VALUES (date('now'), ?, ?, 'Follow-up', ?, ?, ?, ?, 0)""",
            (policy["client_id"], policy["id"], subject,
             note.strip() or None, fu_date, disposition.strip()),
        )
        # Supersede the policy reminder
        supersede_followups(conn, policy["id"], fu_date)

    elif source == "client":
        client = conn.execute(
            "SELECT id, name, follow_up_date FROM clients WHERE id = ?",
            (int(item_id),),
        ).fetchone()
        if not client:
            return HTMLResponse("Client not found", status_code=404)
        fu_date = follow_up_date or client["follow_up_date"]
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, subject,
                details, follow_up_date, disposition, follow_up_done)
               VALUES (date('now'), ?, 'Follow-up', ?, ?, ?, ?, 0)""",
            (client["id"], f"Client Follow-Up: {client['name']}",
             note.strip() or None, fu_date, disposition.strip()),
        )
        # Clear client reminder (activity now owns the follow-up)
        conn.execute("UPDATE clients SET follow_up_date = NULL WHERE id = ?", (client["id"],))

    conn.commit()

    # Return simple 200 — JS (quickSaveDisp) and HTMX (expand form) handle refresh
    # For HTMX expand form submissions: the hx-target="#ac-tab-content" on the form
    # triggers a follow-up GET automatically. For fetch() calls from quickSaveDisp:
    # the JS handles reload after receiving the response.
    return HTMLResponse("OK")
```

- [ ] **Step 2: Verify endpoint compiles**

Run: `python -c "from policydb.web.routes.activities import router; print('OK')"`

---

### Task 2: Backend — `POST /activities/bulk-action` endpoint

**Files:**
- Modify: `src/policydb/web/routes/activities.py` (add after update-disposition endpoint)

- [ ] **Step 1: Add the bulk-action endpoint**

```python
@router.post("/activities/bulk-action", response_class=HTMLResponse)
def bulk_action(
    request: Request,
    ids: str = Form(...),        # comma-separated composite IDs
    action: str = Form(...),     # "set_disposition" | "snooze" | "mark_done"
    disposition: str = Form(""),
    days: int = Form(0),
    context: str = Form(""),
    conn=Depends(get_db),
):
    """Bulk update follow-up items across all source types."""
    import policydb.config as cfg
    from policydb.queries import supersede_followups

    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    updated = 0

    # For set_disposition, compute auto-date
    auto_fu_date = ""
    if action == "set_disposition" and disposition:
        for d in cfg.get("follow_up_dispositions", []):
            if d["label"].lower() == disposition.strip().lower():
                dd = d.get("default_days", 0)
                if dd > 0:
                    from datetime import date, timedelta
                    auto_fu_date = (date.today() + timedelta(days=dd)).isoformat()
                break

    for composite_id in id_list:
        parts = composite_id.split("-", 1)
        if len(parts) != 2:
            continue
        source, item_id = parts[0], parts[1]

        if action == "set_disposition":
            if source == "activity":
                conn.execute("UPDATE activity_log SET disposition=? WHERE id=?",
                             (disposition.strip(), int(item_id)))
                if auto_fu_date:
                    conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?",
                                 (auto_fu_date, int(item_id)))
                updated += 1
            elif source == "policy":
                pol = conn.execute(
                    "SELECT id, policy_type, carrier, client_id, follow_up_date FROM policies WHERE policy_uid=?",
                    (item_id,)).fetchone()
                if pol:
                    subj = pol["policy_type"]
                    if pol["carrier"]:
                        subj += f" — {pol['carrier']}"
                    conn.execute(
                        """INSERT INTO activity_log
                           (activity_date, client_id, policy_id, activity_type, subject,
                            follow_up_date, disposition, follow_up_done)
                           VALUES (date('now'), ?, ?, 'Follow-up', ?, ?, ?, 0)""",
                        (pol["client_id"], pol["id"], subj,
                         auto_fu_date or pol["follow_up_date"], disposition.strip()))
                    supersede_followups(conn, pol["id"], auto_fu_date or pol["follow_up_date"])
                    updated += 1
            elif source == "client":
                cl = conn.execute("SELECT id, name, follow_up_date FROM clients WHERE id=?",
                                  (int(item_id),)).fetchone()
                if cl:
                    conn.execute(
                        """INSERT INTO activity_log
                           (activity_date, client_id, activity_type, subject,
                            follow_up_date, disposition, follow_up_done)
                           VALUES (date('now'), ?, 'Follow-up', ?, ?, ?, 0)""",
                        (cl["id"], f"Client Follow-Up: {cl['name']}",
                         auto_fu_date or cl["follow_up_date"], disposition.strip()))
                    conn.execute("UPDATE clients SET follow_up_date=NULL WHERE id=?", (cl["id"],))
                    updated += 1

        elif action == "snooze":
            snooze_expr = f"+{days} days"
            if source == "activity":
                conn.execute(
                    "UPDATE activity_log SET follow_up_date=date(follow_up_date, ?) WHERE id=?",
                    (snooze_expr, int(item_id)))
                updated += 1
            elif source == "policy":
                conn.execute(
                    "UPDATE policies SET follow_up_date=date(follow_up_date, ?) WHERE policy_uid=?",
                    (snooze_expr, item_id))
                updated += 1
            elif source == "client":
                conn.execute(
                    "UPDATE clients SET follow_up_date=date(follow_up_date, ?) WHERE id=?",
                    (snooze_expr, int(item_id)))
                updated += 1

        elif action == "mark_done":
            if source == "activity":
                conn.execute("UPDATE activity_log SET follow_up_done=1 WHERE id=?", (int(item_id),))
                updated += 1
            elif source == "policy":
                conn.execute("UPDATE policies SET follow_up_date=NULL WHERE policy_uid=?", (item_id,))
                updated += 1
            elif source == "client":
                conn.execute("UPDATE clients SET follow_up_date=NULL WHERE id=?", (int(item_id),))
                updated += 1

    conn.commit()

    # Refresh the section
    if context == "action_center":
        return RedirectResponse("/action-center/followups", status_code=303)
    return HTMLResponse(f"Updated {updated} items")
```

- [ ] **Step 2: Verify endpoint compiles**

Run: `python -c "from policydb.web.routes.activities import router; print('OK')"`

---

### Task 3: Backend — Add `disposition` param to `/activities/log` + cadence computation

**Files:**
- Modify: `src/policydb/web/routes/activities.py` (~line 74) — add `disposition` Form param to log endpoint
- Modify: `src/policydb/web/routes/action_center.py` — add cadence computation in `_followups_ctx()`

- [ ] **Step 1: Add disposition to /activities/log**

In `activities.py`, the log endpoint at ~line 64 has Form parameters. Add `disposition: str = Form("")` to the parameter list. Then in the INSERT statement, add `disposition` to the column list and value:

Find the INSERT (around line 97):
```python
"INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, contact_person, contact_id, subject, details, follow_up_date, account_exec, duration_hours) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
```

Add `disposition` column and value. Also ensure `disposition` is passed in the tuple.

- [ ] **Step 2: Add cadence computation to `_followups_ctx()`**

In `action_center.py`, after the classification loop (around line 215 where buckets are filled), add cadence computation:

```python
# ── Cadence enforcement ───────────────────────────────────────────
_disp_default_days = {
    d["label"].lower(): d.get("default_days", 0) for d in dispositions
}
for bucket_items in buckets.values():
    for item in bucket_items:
        disp = (item.get("disposition") or "").lower()
        dd = _disp_default_days.get(disp, 0)
        days_over = item.get("days_overdue", 0)
        if dd > 0 and days_over > 0 and item.get("source") in ("activity", "project"):
            cadence_over = days_over - dd
            if cadence_over <= 0:
                item["cadence_tier"] = "on_cadence"
                item["cadence_over"] = 0
            elif cadence_over < dd:  # 1-2x over
                item["cadence_tier"] = "mild"
                item["cadence_over"] = cadence_over
            else:  # 2x+ over
                item["cadence_tier"] = "severe"
                item["cadence_over"] = cadence_over
```

---

### Task 4: Shared JS — Pill bar toggle, bulk mode, disposition selection

**Files:**
- Modify: `src/policydb/web/templates/base.html` — add shared JS functions in the IIFE block (~line 102)

- [ ] **Step 1: Add shared JS functions**

Add these functions inside the existing script block in `base.html`:

```javascript
/* ── Disposition pill bar toggle (inline editing) ── */
window.toggleDispBar = function(rowId) {
  var bar = document.getElementById('disp-bar-' + rowId);
  if (bar) bar.classList.toggle('hidden');
};

window.selectDispPill = function(btn, label, days, rowId) {
  // Highlight selected pill
  btn.closest('.disp-pill-bar').querySelectorAll('.disp-pill-inline').forEach(function(p) {
    p.classList.remove('bg-[#003865]', 'text-white');
    p.classList.add('border-gray-200', 'text-gray-500');
  });
  btn.classList.add('bg-[#003865]', 'text-white');
  btn.classList.remove('border-gray-200', 'text-gray-500');
  // Set hidden input
  var input = document.getElementById('disp-val-' + rowId);
  if (input) input.value = label;
  // Auto-fill date if days > 0
  if (days > 0) {
    var dateInput = document.getElementById('disp-date-' + rowId);
    if (dateInput) {
      var d = new Date();
      d.setDate(d.getDate() + days);
      dateInput.value = d.toISOString().split('T')[0];
    }
  }
};

window.expandDispBar = function(rowId) {
  var expand = document.getElementById('disp-expand-' + rowId);
  if (expand) expand.classList.toggle('hidden');
};

/* ── Quick-save disposition (no expand, just pill click + auto-submit) ── */
window.quickSaveDisp = function(btn, label, days, rowId, compositeId, context) {
  // Submit immediately via fetch
  var fd = new FormData();
  fd.append('composite_id', compositeId);
  fd.append('disposition', label);
  fd.append('context', context || '');
  fetch('/activities/update-disposition', { method: 'POST', body: fd })
    .then(function() {
      // Refresh the section
      if (typeof htmx !== 'undefined') {
        var target = context === 'action_center'
          ? document.getElementById('ac-tab-content')
          : btn.closest('.fu-section') || btn.closest('[data-section]');
        if (target) htmx.ajax('GET', target.getAttribute('hx-get') || window.location.pathname, {target: target, swap: 'innerHTML'});
        else window.location.reload();
      } else {
        window.location.reload();
      }
    });
};

/* ── Bulk mode ── */
window.toggleBulkMode = function(btn) {
  var active = document.body.classList.toggle('bulk-mode');
  btn.textContent = active ? '✕ Exit Bulk' : '☐ Bulk Edit';
  // Show/hide floating bar
  var bar = document.getElementById('bulk-action-bar');
  if (bar) bar.style.display = active ? 'flex' : 'none';
  // Update count
  if (!active) {
    document.querySelectorAll('.bulk-check:checked').forEach(function(cb) { cb.checked = false; });
    updateBulkCount();
  }
};

window.updateBulkCount = function() {
  var checked = document.querySelectorAll('.bulk-check:checked').length;
  var countEl = document.getElementById('bulk-count');
  if (countEl) countEl.textContent = checked + ' selected';
  var bar = document.getElementById('bulk-action-bar');
  if (bar && document.body.classList.contains('bulk-mode')) {
    bar.style.display = checked > 0 ? 'flex' : 'none';
  }
};

window.bulkAction = function(action, extraField, extraValue) {
  var ids = [];
  document.querySelectorAll('.bulk-check:checked').forEach(function(cb) {
    ids.push(cb.value);
  });
  if (!ids.length) return;
  var fd = new FormData();
  fd.append('ids', ids.join(','));
  fd.append('action', action);
  fd.append('context', 'action_center');
  if (extraField) fd.append(extraField, extraValue);
  fetch('/activities/bulk-action', { method: 'POST', body: fd })
    .then(function() { window.location.reload(); });
};

window.bulkSetDisposition = function(label) {
  bulkAction('set_disposition', 'disposition', label);
};
```

Also add CSS for bulk mode visibility:

```css
/* Bulk mode */
.bulk-check { display: none; }
body.bulk-mode .bulk-check { display: inline-block; }
```

---

### Task 5: Templates — Action Center inline disposition + cadence + bulk

**Files:**
- Modify: `src/policydb/web/templates/action_center/_followup_sections.html` — update `fu_row` macro
- Modify: `src/policydb/web/templates/action_center/_followups.html` — add Bulk Edit toggle

- [ ] **Step 1: Update `fu_row` macro with clickable disposition + cadence + bulk checkbox**

In `_followup_sections.html`, in the `fu_row` macro's date/info column (around line 76), add:

1. **Bulk checkbox** before the color dot (line 10):
```html
<input type="checkbox" class="bulk-check accent-[#003865] mr-1" value="{{ item.source }}-{{ item.id }}" onchange="updateBulkCount()">
```

2. **Clickable disposition badge** in the date column area (after the date display, ~line 77). Replace the existing overdue badge section with a combined disposition + cadence display:

After the existing date display (`<div class="text-xs font-medium {{ date_class }}">{{ item.follow_up_date }}</div>`), add:

```html
{# Disposition badge (clickable) #}
{% if not (item.is_milestone is defined and item.is_milestone) %}
  {% if item.disposition %}
  <button type="button" onclick="toggleDispBar('{{ row_id }}')"
    class="text-[10px] px-2 py-0.5 rounded-full border border-dashed border-gray-300 text-gray-500 hover:border-[#003865] hover:text-[#003865] cursor-pointer mt-0.5 transition-colors">{{ item.disposition }} ✎</button>
  {% else %}
  <button type="button" onclick="toggleDispBar('{{ row_id }}')"
    class="text-[10px] px-2 py-0.5 rounded-full border border-dashed border-gray-300 text-gray-400 hover:border-[#003865] hover:text-[#003865] cursor-pointer mt-0.5 transition-colors">+ disposition</button>
  {% endif %}
{% endif %}

{# Cadence badge #}
{% if item.cadence_tier is defined and item.cadence_tier == 'mild' %}
<div class="text-[10px] font-medium text-amber-600 mt-0.5">cadence +{{ item.cadence_over }}d</div>
{% elif item.cadence_tier is defined and item.cadence_tier == 'severe' %}
<div class="text-[10px] font-medium text-red-600 mt-0.5">cadence +{{ item.cadence_over }}d !</div>
{% elif item.cadence_tier is defined and item.cadence_tier == 'on_cadence' %}
<div class="text-[10px] text-green-600 mt-0.5">on cadence</div>
{% endif %}
```

3. **Inline pill bar** (hidden, below the row's main content div, inside the row wrapper but after the flex row). Add before the existing inline disposition form:

```html
{# ── Inline disposition pill bar (hidden until badge clicked) ── #}
{% if not (item.is_milestone is defined and item.is_milestone) %}
<div id="disp-bar-{{ row_id }}" class="hidden">
  <div class="disp-pill-bar bg-white border border-gray-200 rounded-lg p-2 mx-4 mb-2 mt-1">
    <input type="hidden" id="disp-val-{{ row_id }}" value="{{ item.disposition or '' }}">
    <div class="flex flex-wrap gap-1">
      {% for d in dispositions %}
      <button type="button"
        onclick="quickSaveDisp(this, '{{ d.label }}', {{ d.default_days }}, '{{ row_id }}', '{{ item.source }}-{{ item.id }}', 'action_center')"
        class="disp-pill-inline text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865] transition-colors cursor-pointer{% if item.disposition == d.label %} bg-[#003865] text-white{% endif %}">{{ d.label }}</button>
      {% endfor %}
      <button type="button" onclick="expandDispBar('{{ row_id }}')"
        class="text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-400 hover:text-[#003865] transition-colors cursor-pointer" title="Date/note override">⋯</button>
    </div>
    <div id="disp-expand-{{ row_id }}" class="hidden mt-2 pt-2 border-t border-dashed border-gray-200">
      <form hx-post="/activities/update-disposition" hx-target="#ac-tab-content" hx-swap="innerHTML" class="flex gap-3 items-end">
        <input type="hidden" name="composite_id" value="{{ item.source }}-{{ item.id }}">
        <input type="hidden" name="disposition" id="disp-val-{{ row_id }}" value="{{ item.disposition or '' }}">
        <input type="hidden" name="context" value="action_center">
        <div>
          <div class="text-[9px] text-gray-400">Follow-up date</div>
          <div class="flex gap-1 items-center">
            <input type="date" name="follow_up_date" id="disp-date-{{ row_id }}" value="{{ item.follow_up_date }}"
              class="text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-[#003865]">
            <button type="button" onclick="var d=new Date();d.setDate(d.getDate()+1);document.getElementById('disp-date-{{ row_id }}').value=d.toISOString().split('T')[0]" class="text-[9px] border border-gray-200 rounded px-1 py-0.5 text-gray-400 hover:text-[#003865]">+1d</button>
            <button type="button" onclick="var d=new Date();d.setDate(d.getDate()+3);document.getElementById('disp-date-{{ row_id }}').value=d.toISOString().split('T')[0]" class="text-[9px] border border-gray-200 rounded px-1 py-0.5 text-gray-400 hover:text-[#003865]">+3d</button>
            <button type="button" onclick="var d=new Date();d.setDate(d.getDate()+7);document.getElementById('disp-date-{{ row_id }}').value=d.toISOString().split('T')[0]" class="text-[9px] border border-gray-200 rounded px-1 py-0.5 text-gray-400 hover:text-[#003865]">+7d</button>
          </div>
        </div>
        <div class="flex-1">
          <div class="text-[9px] text-gray-400">Note</div>
          <input type="text" name="note" placeholder="Optional..." class="w-full text-xs border border-gray-200 rounded px-2 py-1 focus:ring-1 focus:ring-[#003865]">
        </div>
        <button type="submit" class="text-xs bg-[#003865] text-white px-3 py-1.5 rounded hover:bg-[#004a80]">Save</button>
        <button type="button" onclick="toggleDispBar('{{ row_id }}')" class="text-xs text-gray-400">Cancel</button>
      </form>
    </div>
  </div>
</div>
{% endif %}
```

- [ ] **Step 2: Add cadence-based row heat to `fu_row` macro**

Modify the `fu_row` macro signature call sites. Where the section renders `{{ fu_row(item, ...) }}`, the `bg_class` parameter should incorporate cadence heat. This is best done by adjusting the `bg_class` in the template based on `item.cadence_tier`:

In each section's `{% for item in ... %}` loop, before calling `fu_row`, set a heat-adjusted bg:
```jinja2
{% set _heat_bg = 'bg-red-100' if item.cadence_tier is defined and item.cadence_tier == 'severe' else 'bg-amber-100' if item.cadence_tier is defined and item.cadence_tier == 'mild' else bg_class %}
```

Then pass `_heat_bg` instead of the static `bg_class`. Apply in Overdue, Stale, and Nudge Due sections.

- [ ] **Step 3: Add Bulk Edit toggle to `_followups.html` and floating action bar**

In `_followups.html`, add a "Bulk Edit" button to the filter bar (after the Plan Week link, ~line 70):

```html
<button type="button" onclick="toggleBulkMode(this)"
  class="text-xs bg-white text-gray-600 border border-gray-200 px-3 py-1.5 rounded-full hover:border-[#003865] hover:text-[#003865] transition-colors font-medium whitespace-nowrap no-print">☐ Bulk Edit</button>
```

At the bottom of `_followups.html` (before the closing script tag), add the floating action bar:

```html
<div id="bulk-action-bar" class="fixed bottom-4 left-1/2 -translate-x-1/2 bg-gray-900 text-white px-5 py-3 rounded-xl shadow-2xl flex items-center gap-3 z-50 no-print" style="display:none;">
  <span id="bulk-count" class="text-xs font-semibold">0 selected</span>
  <span class="w-px h-5 bg-gray-600"></span>
  <div class="relative" id="bulk-disp-dropdown">
    <button type="button" onclick="document.getElementById('bulk-disp-list').classList.toggle('hidden')"
      class="text-[10px] bg-[#003865] text-white px-3 py-1.5 rounded-lg hover:bg-[#004a80]">Set Disposition ▾</button>
    <div id="bulk-disp-list" class="hidden absolute bottom-full left-0 mb-1 bg-white border border-gray-200 rounded-lg shadow-xl p-2 min-w-48">
      {% for d in dispositions %}
      <button type="button" onclick="bulkSetDisposition('{{ d.label }}'); document.getElementById('bulk-disp-list').classList.add('hidden');"
        class="block w-full text-left text-xs text-gray-700 px-3 py-1.5 rounded hover:bg-gray-100">{{ d.label }}</button>
      {% endfor %}
    </div>
  </div>
  <button type="button" onclick="bulkAction('snooze', 'days', '1')" class="text-[10px] bg-gray-700 text-white px-3 py-1.5 rounded-lg hover:bg-gray-600">+1d</button>
  <button type="button" onclick="bulkAction('snooze', 'days', '3')" class="text-[10px] bg-gray-700 text-white px-3 py-1.5 rounded-lg hover:bg-gray-600">+3d</button>
  <button type="button" onclick="bulkAction('snooze', 'days', '7')" class="text-[10px] bg-gray-700 text-white px-3 py-1.5 rounded-lg hover:bg-gray-600">+7d</button>
  <button type="button" onclick="bulkAction('mark_done')" class="text-[10px] bg-green-600 text-white px-3 py-1.5 rounded-lg hover:bg-green-700">Mark Done</button>
  <button type="button" onclick="toggleBulkMode(document.querySelector('[onclick*=toggleBulkMode]'))" class="text-[10px] text-gray-400 ml-2 hover:text-white">Cancel</button>
</div>
```

- [ ] **Step 4: Update `triage_row` macro with inline disposition pill bar**

The `triage_row` macro (line 185 in `_followup_sections.html`) is specifically for items WITHOUT dispositions — the primary use case for "+ disposition." Currently it has its own "Set disposition →" button that reveals pills posting to `/action-center/set-disposition/{id}`. Replace this with the same inline pill bar pattern used in `fu_row`, using `quickSaveDisp()` to call the new `/activities/update-disposition` endpoint instead of the old triage-specific endpoint. Add a bulk checkbox too.

Key changes:
- Add `<input type="checkbox" class="bulk-check ...">` before the color dot
- Replace the "Set disposition →" button with the same "+ disposition" clickable badge
- Replace the existing `ac-triage-{row_id}` pills section with the `disp-bar-{row_id}` pill bar that calls `quickSaveDisp()`
- The existing `/action-center/set-disposition/{id}` endpoint can remain for backward compatibility but the triage_row will now use the new unified endpoint

---

### Task 6: Templates — Client/Policy activity rows + bulk + cadence

**Files:**
- Modify: `src/policydb/web/templates/activities/_activity_row.html` — add disposition badge + bulk checkbox
- Modify: `src/policydb/web/templates/activities/_activity_sections.html` — add Bulk Edit toggle + floating bar

- [ ] **Step 1: Add clickable disposition badge to activity rows**

In `_activity_row.html`, after the existing disposition display (line 29-31), replace the static badge with a clickable one:

Replace:
```html
{% if a.disposition %}
<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ a.disposition }}</span>
{% endif %}
```

With:
```html
{% if a.follow_up_date and not a.follow_up_done %}
  {% if a.disposition %}
  <button type="button" onclick="toggleDispBar('act-{{ a.id }}')"
    class="text-[10px] px-2 py-0.5 rounded-full border border-dashed border-gray-300 text-gray-500 hover:border-[#003865] hover:text-[#003865] cursor-pointer transition-colors">{{ a.disposition }} ✎</button>
  {% else %}
  <button type="button" onclick="toggleDispBar('act-{{ a.id }}')"
    class="text-[10px] px-2 py-0.5 rounded-full border border-dashed border-gray-300 text-gray-400 hover:border-[#003865] hover:text-[#003865] cursor-pointer transition-colors">+ disposition</button>
  {% endif %}
{% elif a.disposition %}
<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{{ a.disposition }}</span>
{% endif %}
```

Add a **bulk checkbox** at the start of the `<li>` content (after the opening `<li>` tag, before the ref tag line):
```html
<input type="checkbox" class="bulk-check accent-[#003865] mr-1 flex-shrink-0" value="activity-{{ a.id }}" onchange="updateBulkCount()">
```

Add an **inline pill bar** after the `</li>` for the main activity row (before the disposition form `<li>`):
```html
{% if a.follow_up_date and not a.follow_up_done %}
<li id="disp-bar-act-{{ a.id }}" class="hidden px-5 py-2 bg-gray-50 border-l-4 border-gray-200">
  <div class="disp-pill-bar">
    <div class="flex flex-wrap gap-1">
      {% for d in dispositions %}
      <button type="button"
        onclick="quickSaveDisp(this, '{{ d.label }}', {{ d.default_days }}, 'act-{{ a.id }}', 'activity-{{ a.id }}', 'client')"
        class="disp-pill-inline text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865] transition-colors cursor-pointer{% if a.disposition == d.label %} bg-[#003865] text-white{% endif %}">{{ d.label }}</button>
      {% endfor %}
    </div>
  </div>
</li>
{% endif %}
```

- [ ] **Step 2: Add Bulk Edit toggle and floating bar to `_activity_sections.html`**

The `_activity_sections.html` template doesn't have a header area for the toggle. The toggle should be added by the parent template that includes it. However, since both client and policy pages include this template, the simplest approach is to add the toggle and bar directly inside `_activity_sections.html` at the top:

```html
{# ── Bulk Edit toggle ── #}
<div class="flex justify-end mb-2 no-print">
  <button type="button" onclick="toggleBulkMode(this)"
    class="text-[10px] bg-white text-gray-500 border border-gray-200 px-2 py-1 rounded-full hover:border-[#003865] hover:text-[#003865] transition-colors">☐ Bulk Edit</button>
</div>
```

Add the same floating action bar HTML from Task 5 Step 3 at the bottom (before the empty state). Adjust `context` to pass the correct value for the page.

---

### Task 7: Templates — Quick log disposition pills

**Files:**
- Modify: `src/policydb/web/templates/clients/_tab_overview.html` (~line 30-60, the log form)
- Modify: `src/policydb/web/templates/policies/_tab_activity.html` (find the log form)

- [ ] **Step 1: Add disposition pills to client quick log form**

In `clients/_tab_overview.html`, inside the log form (after the details textarea, before the submit button), add:

```html
{# Disposition pills (optional) #}
<div class="mt-2">
  <div class="text-[9px] text-gray-400 mb-1">Disposition (optional)</div>
  <input type="hidden" name="disposition" id="client-log-disp" value="">
  <div class="flex flex-wrap gap-1">
    {% for d in dispositions %}
    <button type="button"
      onclick="this.parentElement.querySelectorAll('button').forEach(function(b){b.classList.remove('bg-[#003865]','text-white');b.classList.add('border-gray-200','text-gray-500');}); this.classList.add('bg-[#003865]','text-white'); this.classList.remove('border-gray-200','text-gray-500'); document.getElementById('client-log-disp').value='{{ d.label }}'; if({{ d.default_days }}>0){var di=document.querySelector('#client-log-form input[name=follow_up_date]'); if(di && !di.value){var dt=new Date(); dt.setDate(dt.getDate()+{{ d.default_days }}); di.value=dt.toISOString().split('T')[0];}}"
      class="text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865] transition-colors cursor-pointer">{{ d.label }}</button>
    {% endfor %}
  </div>
</div>
```

- [ ] **Step 2: Add same disposition pills to policy log form**

Check if `policies/_tab_activity.html` has an inline log form. If so, add the same pills. If the policy page reuses the client log form pattern, apply the same change there.

---

### Task 8: Run tests + verify all screens

- [ ] **Step 1: Run existing tests**

Run: `pytest tests/ -v`

Expect: all existing tests pass (264+), no regressions.

- [ ] **Step 2: Start server and verify Action Center**

Navigate to `/action-center`:
- Verify disposition badges appear on rows (clickable "Left VM ✎" or "+ disposition")
- Click a badge → pill bar appears inline
- Click a disposition pill → row refreshes, item reclassifies
- Verify cadence badges show on overdue items with dispositions
- Click "Bulk Edit" → checkboxes appear, floating bar works
- Select items → use Set Disposition / Snooze / Mark Done

- [ ] **Step 3: Verify client detail page**

Navigate to `/clients/{id}`:
- Activity rows show clickable disposition badges
- Bulk Edit toggle works on activity sections
- Quick log form shows disposition pills
- Logging with disposition → activity appears in correct bucket

- [ ] **Step 4: Verify policy detail page**

Navigate to `/policies/{uid}/edit` → Activity tab:
- Same disposition badge + bulk functionality
- Quick log with disposition works

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: inline disposition editing, cadence enforcement, and bulk operations"
```
