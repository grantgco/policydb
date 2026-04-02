# Action Center Edit Slideovers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add source-specific edit slideovers (policy, client, issue) to the Action Center, add pencil edit buttons to Activities and Issues tabs, and remove the unused Activities board view.

**Architecture:** Reuse the existing `fu-edit-panel` container in `base.html`. Each source type gets a small partial template loaded via HTMX into `#fu-edit-content`. New PATCH endpoints return `{"ok": true, "formatted": "..."}` JSON for per-field save-on-blur. The Activities tab board view and its backing query are removed.

**Tech Stack:** FastAPI routes, Jinja2 partials, HTMX `hx-get`/`hx-swap`, vanilla JS `fetch` for PATCH.

**Spec:** `docs/superpowers/specs/2026-04-02-action-center-slideovers-design.md`

---

## File Map

### New files
- `src/policydb/web/templates/action_center/_edit_policy_slideover.html` — policy follow-up date + renewal status editor
- `src/policydb/web/templates/action_center/_edit_client_slideover.html` — client follow-up date + notes editor
- `src/policydb/web/templates/action_center/_edit_issue_slideover.html` — issue due date, severity, status, subject, details editor

### Modified files
- `src/policydb/web/routes/policies.py` — add GET + PATCH slideover endpoints
- `src/policydb/web/routes/clients.py` — add GET + PATCH slideover endpoints
- `src/policydb/web/routes/issues.py` — add GET slideover + PATCH field endpoint
- `src/policydb/web/routes/action_center.py` — remove `view_mode` from activities ctx/route, remove board import
- `src/policydb/web/templates/action_center/_followup_sections.html` — expand pencil routing to all sources
- `src/policydb/web/templates/action_center/_activities.html` — remove board toggle, add pencil column
- `src/policydb/web/templates/action_center/_issue_row.html` — add pencil button
- `src/policydb/web/templates/action_center/_issue_board_card.html` — add pencil button

### Deleted files
- `src/policydb/web/templates/action_center/_activities_board.html`

---

### Task 1: Policy Edit Slideover — Backend

**Files:**
- Modify: `src/policydb/web/routes/policies.py` (add two new endpoints near line 4193, after `policy_snooze_followup`)

- [ ] **Step 1: Add GET endpoint for policy slideover partial**

Add this after the `policy_snooze_followup` function in `src/policydb/web/routes/policies.py`:

```python
@router.get("/{policy_uid}/edit-followup-slideover", response_class=HTMLResponse)
def policy_edit_followup_slideover(policy_uid: str, request: Request, conn=Depends(get_db)):
    """Return the edit slideover partial for a policy follow-up."""
    uid = policy_uid.upper()
    row = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.follow_up_date,
                  p.renewal_status, p.expiration_date, p.project_name,
                  c.name AS client_name
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    return templates.TemplateResponse("action_center/_edit_policy_slideover.html", {
        "request": request,
        "p": dict(row),
        "renewal_statuses": cfg.get("renewal_statuses", []),
    })
```

- [ ] **Step 2: Add PATCH endpoint for policy field updates**

Add immediately after the GET endpoint:

```python
@router.patch("/{policy_uid}/followup-field")
def patch_policy_followup_field(policy_uid: str, body: dict = None, conn=Depends(get_db)):
    """Update follow_up_date or renewal_status on a policy (slideover inline edit)."""
    if not body:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)
    uid = policy_uid.upper()
    field = body.get("field", "")
    value = body.get("value", "")
    allowed = {"follow_up_date", "renewal_status"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)
    conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (value or None, uid))
    conn.commit()
    return {"ok": True, "formatted": value}
```

- [ ] **Step 3: Verify JSONResponse import exists**

Check that `from fastapi.responses import JSONResponse` is present in the imports at the top of `policies.py`. If not, add it.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: add policy follow-up slideover GET + PATCH endpoints"
```

---

### Task 2: Policy Edit Slideover — Template

**Files:**
- Create: `src/policydb/web/templates/action_center/_edit_policy_slideover.html`

- [ ] **Step 1: Create the policy slideover partial**

Create `src/policydb/web/templates/action_center/_edit_policy_slideover.html`:

```html
{# Policy Follow-Up Edit Slideover — per-field save on blur via PATCH #}

<div class="flex items-start justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
  <div>
    <h2 class="text-sm font-semibold text-gray-900">Edit Policy Follow-Up</h2>
    <p class="text-xs text-gray-500 mt-0.5">
      {{ p.client_name }}
      {% if p.policy_uid %} &middot; {{ p.policy_uid }}{% endif %}
      {% if p.carrier %} &middot; {{ p.carrier }}{% endif %}
    </p>
  </div>
  <button type="button" onclick="closeFollowupEdit()"
          class="text-gray-400 hover:text-gray-600 transition-colors ml-4 mt-0.5">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
    </svg>
  </button>
</div>

<div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
  {# Follow-up date #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Follow-up Date</label>
    <div class="flex items-center gap-2 mt-1">
      <input type="date" id="edit-pol-fu-date" value="{{ p.follow_up_date or '' }}"
        class="text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh"
        onchange="patchPolField('{{ p.policy_uid }}', 'follow_up_date', this.value, this)">
      <button type="button" onclick="setPolDate(1)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+1d</button>
      <button type="button" onclick="setPolDate(3)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+3d</button>
      <button type="button" onclick="setPolDate(7)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+7d</button>
      <button type="button" onclick="setPolDate(14)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+2w</button>
    </div>
  </div>

  {# Renewal status — pill buttons #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Renewal Status</label>
    <div class="flex flex-wrap gap-1.5 mt-1">
      {% for s in renewal_statuses %}
      <button type="button"
        onclick="selectPolStatus(this, '{{ p.policy_uid }}', '{{ s }}')"
        class="pol-status-pill text-[11px] px-3 py-1 rounded border transition-colors cursor-pointer
          {% if p.renewal_status == s %}bg-[#003865] text-white border-[#003865]{% else %}border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]{% endif %}">{{ s }}</button>
      {% endfor %}
    </div>
  </div>
</div>

<script>
function setPolDate(days) {
  var d = new Date();
  d.setDate(d.getDate() + days);
  var val = d.toISOString().split('T')[0];
  var el = document.getElementById('edit-pol-fu-date');
  el.value = val;
  patchPolField('{{ p.policy_uid }}', 'follow_up_date', val, el);
}

function selectPolStatus(btn, uid, status) {
  document.querySelectorAll('.pol-status-pill').forEach(function(p) {
    p.className = p.className.replace(/bg-\[#003865\] text-white border-\[#003865\]/g, 'border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]');
  });
  btn.className = btn.className.replace(/border-gray-200 text-gray-500 hover:border-\[#003865\] hover:text-\[#003865\]/g, 'bg-[#003865] text-white border-[#003865]');
  patchPolField(uid, 'renewal_status', status, btn);
}

function patchPolField(uid, field, value, el) {
  fetch('/policies/' + uid + '/followup-field', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({field: field, value: value})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok && el && el.tagName) {
      el.style.transition = 'background-color 0.3s';
      el.style.backgroundColor = '#ecfdf5';
      setTimeout(function() { el.style.backgroundColor = ''; }, 800);
    } else if (!data.ok && typeof showToast === 'function') {
      showToast('Save failed: ' + (data.error || 'unknown'), false);
    }
  });
}
</script>
```

- [ ] **Step 2: Verify the template renders**

Start the server and test by navigating to `/action-center`. Find a policy-sourced follow-up (if any exist). We'll wire up the pencil button in a later task — for now, test the GET endpoint directly:

```bash
curl -s http://127.0.0.1:8000/policies/POL-001/edit-followup-slideover | head -5
```

Expected: HTML fragment starting with the slideover header div.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_edit_policy_slideover.html
git commit -m "feat: add policy follow-up edit slideover template"
```

---

### Task 3: Client Edit Slideover — Backend + Template

**Files:**
- Modify: `src/policydb/web/routes/clients.py` (add two new endpoints)
- Create: `src/policydb/web/templates/action_center/_edit_client_slideover.html`

- [ ] **Step 1: Add GET + PATCH endpoints to clients.py**

Add these endpoints to `src/policydb/web/routes/clients.py`. Place them near other client detail endpoints. Ensure `JSONResponse` is imported (`from fastapi.responses import JSONResponse`).

```python
@router.get("/clients/{client_id}/edit-followup-slideover", response_class=HTMLResponse)
def client_edit_followup_slideover(client_id: int, request: Request, conn=Depends(get_db)):
    """Return the edit slideover partial for a client follow-up."""
    row = conn.execute(
        "SELECT id, name, follow_up_date, notes FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    return templates.TemplateResponse("action_center/_edit_client_slideover.html", {
        "request": request,
        "c": dict(row),
    })


@router.patch("/clients/{client_id}/followup-field")
def patch_client_followup_field(client_id: int, body: dict = None, conn=Depends(get_db)):
    """Update follow_up_date or notes on a client (slideover inline edit)."""
    if not body:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)
    field = body.get("field", "")
    value = body.get("value", "")
    allowed = {"follow_up_date", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)
    conn.execute(f"UPDATE clients SET {field} = ? WHERE id = ?", (value or None, client_id))
    conn.commit()
    return {"ok": True, "formatted": value}
```

- [ ] **Step 2: Create the client slideover template**

Create `src/policydb/web/templates/action_center/_edit_client_slideover.html`:

```html
{# Client Follow-Up Edit Slideover — per-field save on blur via PATCH #}

<div class="flex items-start justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
  <div>
    <h2 class="text-sm font-semibold text-gray-900">Edit Client Follow-Up</h2>
    <p class="text-xs text-gray-500 mt-0.5">{{ c.name }}</p>
  </div>
  <button type="button" onclick="closeFollowupEdit()"
          class="text-gray-400 hover:text-gray-600 transition-colors ml-4 mt-0.5">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
    </svg>
  </button>
</div>

<div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
  {# Follow-up date #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Follow-up Date</label>
    <div class="flex items-center gap-2 mt-1">
      <input type="date" id="edit-cli-fu-date" value="{{ c.follow_up_date or '' }}"
        class="text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh"
        onchange="patchCliField({{ c.id }}, 'follow_up_date', this.value, this)">
      <button type="button" onclick="setCliDate(1)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+1d</button>
      <button type="button" onclick="setCliDate(3)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+3d</button>
      <button type="button" onclick="setCliDate(7)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+7d</button>
      <button type="button" onclick="setCliDate(14)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+2w</button>
    </div>
  </div>

  {# Notes #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Notes</label>
    <textarea id="edit-cli-notes" rows="4"
      class="mt-1 w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh resize-y"
      onblur="patchCliField({{ c.id }}, 'notes', this.value, this)">{{ c.notes or '' }}</textarea>
  </div>
</div>

<script>
function setCliDate(days) {
  var d = new Date();
  d.setDate(d.getDate() + days);
  var val = d.toISOString().split('T')[0];
  var el = document.getElementById('edit-cli-fu-date');
  el.value = val;
  patchCliField({{ c.id }}, 'follow_up_date', val, el);
}

function patchCliField(clientId, field, value, el) {
  fetch('/clients/' + clientId + '/followup-field', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({field: field, value: value})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok && el && el.tagName) {
      el.style.transition = 'background-color 0.3s';
      el.style.backgroundColor = '#ecfdf5';
      setTimeout(function() { el.style.backgroundColor = ''; }, 800);
    } else if (!data.ok && typeof showToast === 'function') {
      showToast('Save failed: ' + (data.error || 'unknown'), false);
    }
  });
}
</script>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/action_center/_edit_client_slideover.html
git commit -m "feat: add client follow-up edit slideover (GET + PATCH + template)"
```

---

### Task 4: Issue Edit Slideover — Backend + Template

**Files:**
- Modify: `src/policydb/web/routes/issues.py` (add GET slideover + PATCH field endpoint)
- Create: `src/policydb/web/templates/action_center/_edit_issue_slideover.html`

- [ ] **Step 1: Add GET slideover endpoint to issues.py**

Add after the `update_issue_details` function (around line 258) in `src/policydb/web/routes/issues.py`:

```python
@router.get("/issues/{issue_id}/edit-slideover", response_class=HTMLResponse)
def issue_edit_slideover(issue_id: int, request: Request, conn=Depends(get_db)):
    """Return the edit slideover partial for an issue."""
    row = conn.execute(
        """SELECT a.id, a.issue_uid, a.subject, a.details, a.due_date,
                  a.issue_severity, a.issue_status,
                  c.name AS client_name
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE a.id = ? AND a.item_kind = 'issue'""",
        (issue_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    return templates.TemplateResponse("action_center/_edit_issue_slideover.html", {
        "request": request,
        "iss": dict(row),
        "lifecycle_states": cfg.get("issue_lifecycle_states", []),
        "severities": cfg.get("issue_severities", []),
    })
```

- [ ] **Step 2: Add PATCH field endpoint to issues.py**

Add immediately after the GET endpoint:

```python
@router.patch("/issues/{issue_id}/field")
def patch_issue_field(issue_id: int, body: dict = None, conn=Depends(get_db)):
    """Update a single field on an issue (slideover inline edit)."""
    if not body:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"due_date", "issue_severity", "issue_status", "subject", "details"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)

    if field == "subject" and not (value or "").strip():
        return JSONResponse({"ok": False, "error": "Subject cannot be empty"}, status_code=400)

    # Update SLA when severity changes
    if field == "issue_severity":
        severities = cfg.get("issue_severities", [])
        sla_days = 7
        for sev in severities:
            if sev["label"] == value:
                sla_days = sev.get("sla_days", 7)
                break
        conn.execute(
            "UPDATE activity_log SET issue_severity = ?, issue_sla_days = ? WHERE id = ? AND item_kind = 'issue'",
            (value, sla_days, issue_id),
        )
    elif field == "issue_status":
        conn.execute(
            "UPDATE activity_log SET issue_status = ? WHERE id = ? AND item_kind = 'issue'",
            (value, issue_id),
        )
        if value in ("Resolved", "Closed"):
            auto_close_followups(conn, issue_id=issue_id, reason="issue_resolved", closed_by="issue_status_change")
    else:
        conn.execute(
            f"UPDATE activity_log SET {field} = ? WHERE id = ? AND item_kind = 'issue'",
            (value or None, issue_id),
        )

    conn.commit()
    return {"ok": True, "formatted": value}
```

Ensure `JSONResponse` is imported at the top of `issues.py`: `from fastapi.responses import JSONResponse`. It may not be there yet — check and add if needed.

- [ ] **Step 3: Create the issue slideover template**

Create `src/policydb/web/templates/action_center/_edit_issue_slideover.html`:

```html
{# Issue Edit Slideover — per-field save on blur via PATCH #}

<div class="flex items-start justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
  <div>
    <h2 class="text-sm font-semibold text-gray-900">Edit Issue</h2>
    <p class="text-xs text-gray-500 mt-0.5">
      {% if iss.issue_uid %}{{ iss.issue_uid }} &middot; {% endif %}{{ iss.client_name }}
    </p>
  </div>
  <button type="button" onclick="closeFollowupEdit()"
          class="text-gray-400 hover:text-gray-600 transition-colors ml-4 mt-0.5">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
    </svg>
  </button>
</div>

<div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
  {# Due date #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Due Date</label>
    <div class="flex items-center gap-2 mt-1">
      <input type="date" id="edit-iss-due" value="{{ iss.due_date or '' }}"
        class="text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh"
        onchange="patchIssField({{ iss.id }}, 'due_date', this.value, this)">
      <button type="button" onclick="setIssDue(1)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+1d</button>
      <button type="button" onclick="setIssDue(3)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+3d</button>
      <button type="button" onclick="setIssDue(7)" class="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-[#003865] hover:border-[#003865]">+7d</button>
    </div>
  </div>

  {# Severity — pill buttons #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Severity</label>
    <div class="flex flex-wrap gap-1.5 mt-1">
      {% for sev in severities %}
      <button type="button"
        onclick="selectIssSev(this, {{ iss.id }}, '{{ sev.label }}')"
        class="iss-sev-pill text-[11px] px-3 py-1 rounded border transition-colors cursor-pointer
          {% if iss.issue_severity == sev.label %}bg-[#003865] text-white border-[#003865]{% else %}border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]{% endif %}">{{ sev.label }}</button>
      {% endfor %}
    </div>
  </div>

  {# Status — pill buttons #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Status</label>
    <div class="flex flex-wrap gap-1.5 mt-1">
      {% for st in lifecycle_states %}
      <button type="button"
        onclick="selectIssStat(this, {{ iss.id }}, '{{ st }}')"
        class="iss-stat-pill text-[11px] px-3 py-1 rounded border transition-colors cursor-pointer
          {% if (iss.issue_status or 'Open') == st %}bg-[#003865] text-white border-[#003865]{% else %}border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]{% endif %}">{{ st }}</button>
      {% endfor %}
    </div>
  </div>

  {# Subject #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Subject</label>
    <input type="text" id="edit-iss-subject" value="{{ iss.subject or '' }}"
      class="mt-1 w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh"
      onblur="patchIssField({{ iss.id }}, 'subject', this.value, this)">
  </div>

  {# Details #}
  <div>
    <label class="text-[10px] font-medium text-gray-500 uppercase tracking-wide">Details</label>
    <textarea id="edit-iss-details" rows="4"
      class="mt-1 w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh resize-y"
      onblur="patchIssField({{ iss.id }}, 'details', this.value, this)">{{ iss.details or '' }}</textarea>
  </div>
</div>

<script>
function setIssDue(days) {
  var d = new Date();
  d.setDate(d.getDate() + days);
  var val = d.toISOString().split('T')[0];
  var el = document.getElementById('edit-iss-due');
  el.value = val;
  patchIssField({{ iss.id }}, 'due_date', val, el);
}

function selectIssSev(btn, issId, label) {
  document.querySelectorAll('.iss-sev-pill').forEach(function(p) {
    p.className = p.className.replace(/bg-\[#003865\] text-white border-\[#003865\]/g, 'border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]');
  });
  btn.className = btn.className.replace(/border-gray-200 text-gray-500 hover:border-\[#003865\] hover:text-\[#003865\]/g, 'bg-[#003865] text-white border-[#003865]');
  patchIssField(issId, 'issue_severity', label, btn);
}

function selectIssStat(btn, issId, label) {
  document.querySelectorAll('.iss-stat-pill').forEach(function(p) {
    p.className = p.className.replace(/bg-\[#003865\] text-white border-\[#003865\]/g, 'border-gray-200 text-gray-500 hover:border-[#003865] hover:text-[#003865]');
  });
  btn.className = btn.className.replace(/border-gray-200 text-gray-500 hover:border-\[#003865\] hover:text-\[#003865\]/g, 'bg-[#003865] text-white border-[#003865]');
  patchIssField(issId, 'issue_status', label, btn);
}

function patchIssField(issId, field, value, el) {
  fetch('/issues/' + issId + '/field', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({field: field, value: value})
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (data.ok && el && el.tagName) {
      el.style.transition = 'background-color 0.3s';
      el.style.backgroundColor = '#ecfdf5';
      setTimeout(function() { el.style.backgroundColor = ''; }, 800);
    } else if (!data.ok && typeof showToast === 'function') {
      showToast('Save failed: ' + (data.error || 'unknown'), false);
    }
  });
}
</script>
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/issues.py src/policydb/web/templates/action_center/_edit_issue_slideover.html
git commit -m "feat: add issue edit slideover (GET + PATCH + template)"
```

---

### Task 5: Wire Pencil Buttons in Follow-ups Tab

**Files:**
- Modify: `src/policydb/web/templates/action_center/_followup_sections.html` (around lines 158-165)

- [ ] **Step 1: Replace the pencil button block**

In `src/policydb/web/templates/action_center/_followup_sections.html`, find the current pencil button block (around line 158):

```html
      {# Edit slideover (activity_log-sourced items) #}
      {% if item.source in ('activity', 'project') and item.id %}
      <button type="button"
        hx-get="/activities/{{ item.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
        onclick="openFollowupEdit()"
        class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-[#003865] transition-colors"
        title="Edit follow-up">&#9998;</button>
      {% endif %}
```

Replace with:

```html
      {# Edit slideover — dispatches to source-specific endpoint #}
      {% if item.source in ('activity', 'project') and item.id %}
      <button type="button"
        hx-get="/activities/{{ item.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
        onclick="openFollowupEdit()"
        class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-[#003865] transition-colors"
        title="Edit follow-up">&#9998;</button>
      {% elif item.source == 'policy' and item.policy_uid %}
      <button type="button"
        hx-get="/policies/{{ item.policy_uid }}/edit-followup-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
        onclick="openFollowupEdit()"
        class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-[#003865] transition-colors"
        title="Edit policy follow-up">&#9998;</button>
      {% elif item.source == 'client' and item.id %}
      <button type="button"
        hx-get="/clients/{{ item.id }}/edit-followup-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
        onclick="openFollowupEdit()"
        class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-[#003865] transition-colors"
        title="Edit client follow-up">&#9998;</button>
      {% elif item.source == 'issue' and item.id %}
      <button type="button"
        hx-get="/issues/{{ item.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
        onclick="openFollowupEdit()"
        class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-[#003865] transition-colors"
        title="Edit issue">&#9998;</button>
      {% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/action_center/_followup_sections.html
git commit -m "feat: wire pencil buttons for all source types in follow-ups tab"
```

---

### Task 6: Activities Tab — Remove Board View + Add Pencil

**Files:**
- Modify: `src/policydb/web/templates/action_center/_activities.html` (remove board toggle, add pencil column)
- Modify: `src/policydb/web/routes/action_center.py` (remove `view_mode` from activities, remove board import)
- Delete: `src/policydb/web/templates/action_center/_activities_board.html`

- [ ] **Step 1: Remove board view from activities route**

In `src/policydb/web/routes/action_center.py`:

1. **Remove the `get_client_activity_board` import** (line 21). Find the import line and remove `get_client_activity_board` from it. If it's the only import on that line, remove the whole line.

2. **Remove `view_mode` param and board query from `_activities_ctx`** (lines 581-657). In the function signature, remove `view_mode: str = "board"`. In the function body, remove the `client_columns = get_client_activity_board(...)` call (lines 637-642) and remove `"view_mode"` and `"client_columns"` from the return dict (lines 651-652).

3. **Remove `view_mode` param from `ac_activities` route** (lines 1054-1068). Remove `view_mode: str = "board"` from the function signature and remove `view_mode=view_mode` from the `_activities_ctx()` call.

- [ ] **Step 2: Remove board toggle and add pencil column in template**

In `src/policydb/web/templates/action_center/_activities.html`:

1. **Remove the hidden `view_mode` input** (line 114):
   ```html
   <input type="hidden" id="ac-act-view-mode" name="view_mode" value="{{ view_mode or 'table' }}">
   ```

2. **Remove all `hx-include` references to `#ac-act-view-mode`** from the filter controls (lines 71, 83, 95, 111). Change each `hx-include` from:
   ```
   hx-include="#ac-act-days, #ac-act-type, #ac-act-client, #ac-act-search, #ac-act-view-mode"
   ```
   to:
   ```
   hx-include="#ac-act-days, #ac-act-type, #ac-act-client, #ac-act-search"
   ```

3. **Remove the entire view toggle div** (lines 116-132 — the `ml-auto flex` div with Board/Table buttons).

4. **Remove the board conditional and its include** (lines 140-142):
   ```html
   {% if view_mode == 'board' %}
     {% include "action_center/_activities_board.html" %}
   {% else %}
   ```
   And remove the matching `{% endif %}` at the end of the table section (find the `{% endif %}` that closes the board/table conditional — it's separate from the `{% if activities %}` endif).

5. **Add a pencil column header** in the `<thead>` row. Find the empty `<th>` for the delete button column (line 159):
   ```html
   <th class="px-3 py-2 whitespace-nowrap no-print"></th>
   ```
   Add another `<th>` before it:
   ```html
   <th class="px-3 py-2 whitespace-nowrap no-print"></th>
   ```

6. **Add pencil button cell** in each `<tr>` row. Find the delete button `<td>` (around line 268). Add a new `<td>` before it:
   ```html
   <td class="px-3 py-2 whitespace-nowrap no-print">
     <button type="button"
       hx-get="/activities/{{ a.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
       onclick="openFollowupEdit()"
       class="text-xs text-gray-400 hover:text-[#003865] transition-colors"
       title="Edit activity">&#9998;</button>
   </td>
   ```

7. **Update the expandable detail row colspan** from `10` to `11` (line 281):
   ```html
   <td colspan="11" class="px-6 py-3">
   ```

- [ ] **Step 3: Delete the board template**

Delete `src/policydb/web/templates/action_center/_activities_board.html`.

- [ ] **Step 4: Verify app imports cleanly**

```bash
~/.policydb/venv/bin/python -c "from policydb.web.app import app; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py src/policydb/web/templates/action_center/_activities.html
git rm src/policydb/web/templates/action_center/_activities_board.html
git commit -m "feat: remove activities board view, add pencil edit button to activities table"
```

---

### Task 7: Issues Tab — Add Pencil Buttons

**Files:**
- Modify: `src/policydb/web/templates/action_center/_issue_row.html` (add pencil in quick actions)
- Modify: `src/policydb/web/templates/action_center/_issue_board_card.html` (add pencil button)

- [ ] **Step 1: Add pencil to issue list row**

In `src/policydb/web/templates/action_center/_issue_row.html`, find the quick actions div (line 83):

```html
  <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
    {# Status change #}
    <select class="text-xs border-gray-200 rounded py-0.5 px-1"
```

Add a pencil button as the first child inside that div, before the status select:

```html
    <button type="button"
      hx-get="/issues/{{ issue.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
      onclick="openFollowupEdit()"
      class="text-xs text-gray-400 hover:text-[#003865] transition-colors p-0.5"
      title="Edit issue">&#9998;</button>
```

- [ ] **Step 2: Add pencil to issue board card**

In `src/policydb/web/templates/action_center/_issue_board_card.html`, the entire card is an `<a>` link wrapping. Add a pencil button in the SLA row area (around line 47). Find:

```html
  {# Row 3: SLA + activity count #}
  <div class="flex items-center gap-1.5 mt-1.5 text-[10px]">
```

Add a pencil button at the end of that div, before the closing `</div>`:

```html
    <button type="button"
      hx-get="/issues/{{ issue.id }}/edit-slideover" hx-target="#fu-edit-content" hx-swap="innerHTML"
      onclick="event.preventDefault();event.stopPropagation();openFollowupEdit()"
      class="ml-auto text-gray-400 hover:text-[#003865] transition-colors"
      title="Edit issue">&#9998;</button>
```

Note: `event.preventDefault()` and `event.stopPropagation()` prevent the parent `<a>` link from navigating.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_issue_row.html src/policydb/web/templates/action_center/_issue_board_card.html
git commit -m "feat: add pencil edit buttons to issues tab (list + board views)"
```

---

### Task 8: QA — Visual Verification

**Files:** None (testing only)

- [ ] **Step 1: Start the server**

```bash
PORT=$((RANDOM % 1000 + 8100))
~/.policydb/venv/bin/uvicorn policydb.web.app:app --port $PORT
```

- [ ] **Step 2: Verify Follow-ups tab**

Navigate to `http://127.0.0.1:$PORT/action-center`. Check that:
- Activity/project items show the pencil (existing behavior)
- Policy reminder items show the pencil — click it, verify the policy slideover opens with follow-up date + renewal status pills
- Client items show the pencil — click it, verify the client slideover opens with follow-up date + notes
- Issue items show the pencil — click it, verify the issue slideover opens with due date, severity, status, subject, details
- Milestone items do NOT show a pencil
- All slideovers close on X click and backdrop click
- Changing a field triggers green flash feedback

- [ ] **Step 3: Verify Activities tab**

Navigate to the Activities tab. Check that:
- No board/table toggle exists — table is the only view
- Each activity row has a pencil button in the actions area
- Clicking the pencil opens the existing activity edit slideover
- The slideover saves correctly on blur

- [ ] **Step 4: Verify Issues tab**

Navigate to the Issues tab. Check that:
- List view: each issue row has a pencil in the hover actions
- Board view: each card has a pencil that opens the slideover without navigating to the issue page
- The issue slideover saves severity/status/due date/subject/details correctly

- [ ] **Step 5: Kill test server**

```bash
kill %1
```
