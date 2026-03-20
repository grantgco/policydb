# Nav Header Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 13 flat nav links with 3 semantic dropdown menus (Book/Activity/Tools), move capture to a sub-bar with auto-copy, add `.` keyboard shortcut, and add "Schedule" quick-path to inbox processing.

**Architecture:** All changes are in `base.html` (nav structure, dropdowns, capture bar, keyboard shortcuts) and `inbox.py`/`inbox.html` (schedule endpoint + form). No schema changes. Dropdowns use pure CSS/JS hover with mouseleave delay.

**Tech Stack:** Jinja2, HTMX, Tailwind CSS, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-20-nav-reorganization-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/policydb/web/templates/base.html` | Nav dropdowns, capture sub-bar, keyboard shortcuts, progress bar position |
| Modify | `src/policydb/web/routes/inbox.py` | Schedule endpoint, update capture toast text |
| Modify | `src/policydb/web/templates/inbox.html` | Schedule button + inline form |

---

### Task 1: Nav Dropdown Menus

**Files:**
- Modify: `src/policydb/web/templates/base.html`

- [ ] **Step 1: Replace flat nav links with dropdown structure**

Replace the nav links block (lines 409-428) with the dropdown menu structure. Each dropdown is a `<div>` with `relative` positioning containing the parent link and an absolutely-positioned dropdown panel.

```html
<div class="flex gap-1 items-center">
  <a href="/" class="nav-link {% if active == 'dashboard' %}bg-marsh-light{% endif %}">Dashboard</a>

  {# Book dropdown #}
  <div class="nav-dropdown relative" onmouseenter="openNavDrop(this)" onmouseleave="closeNavDrop(this)">
    <button class="nav-link {% if active in ['clients','renewals','contacts'] %}bg-marsh-light border-b-2 border-[#c8a96e]{% endif %}">Book ▾</button>
    <div class="nav-drop-panel hidden absolute left-0 top-full mt-0 bg-marsh rounded-b-lg shadow-lg z-50 min-w-[160px] py-1">
      <a href="/clients" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'clients' %}bg-marsh-light{% endif %}">Clients</a>
      <a href="/renewals" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'renewals' %}bg-marsh-light{% endif %}">Renewals</a>
      <a href="/contacts" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'contacts' %}bg-marsh-light{% endif %}">Contacts</a>
    </div>
  </div>

  {# Activity dropdown #}
  <div class="nav-dropdown relative" onmouseenter="openNavDrop(this)" onmouseleave="closeNavDrop(this)">
    <button class="nav-link {% if active in ['followups','activities','meetings','review'] %}bg-marsh-light border-b-2 border-[#c8a96e]{% endif %}">Activity ▾</button>
    <div class="nav-drop-panel hidden absolute left-0 top-full mt-0 bg-marsh rounded-b-lg shadow-lg z-50 min-w-[160px] py-1">
      <a href="/followups" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'followups' %}bg-marsh-light{% endif %}">Follow-Ups</a>
      <a href="/activities" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'activities' %}bg-marsh-light{% endif %}">Activity Log</a>
      <a href="/meetings" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'meetings' %}bg-marsh-light{% endif %}">Meetings</a>
      <a href="/review" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'review' %}bg-marsh-light{% endif %}">Review</a>
    </div>
  </div>

  {# Tools dropdown #}
  <div class="nav-dropdown relative" onmouseenter="openNavDrop(this)" onmouseleave="closeNavDrop(this)">
    <button class="nav-link {% if active in ['briefing','reconcile','templates','settings'] %}bg-marsh-light border-b-2 border-[#c8a96e]{% endif %}">Tools ▾</button>
    <div class="nav-drop-panel hidden absolute left-0 top-full mt-0 bg-marsh rounded-b-lg shadow-lg z-50 min-w-[160px] py-1">
      <a href="/briefing" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'briefing' %}bg-marsh-light{% endif %}">Briefing</a>
      <a href="/reconcile" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'reconcile' %}bg-marsh-light{% endif %}">Reconcile</a>
      <a href="/templates" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'templates' %}bg-marsh-light{% endif %}">Templates</a>
      <a href="/settings" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'settings' %}bg-marsh-light{% endif %}">Settings</a>
    </div>
  </div>

  {# Inbox (standalone) #}
  {% set _inbox_count = inbox_pending_count() %}
  <a href="/inbox" class="nav-link {% if active == 'inbox' %}bg-marsh-light{% endif %}">
    Inbox{% if _inbox_count %}<span class="bg-white/20 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-1">{{ _inbox_count }}</span>{% endif %}
  </a>
</div>
```

- [ ] **Step 2: Add dropdown JS (open/close with delay)**

Add this script in `base.html` after the nav element:

```javascript
var _navDropTimer = null;
function openNavDrop(el) {
  clearTimeout(_navDropTimer);
  // Close all other dropdowns first
  document.querySelectorAll('.nav-drop-panel').forEach(function(p) {
    if (!el.contains(p)) p.classList.add('hidden');
  });
  var panel = el.querySelector('.nav-drop-panel');
  if (panel) panel.classList.remove('hidden');
}
function closeNavDrop(el) {
  var panel = el.querySelector('.nav-drop-panel');
  if (panel) {
    _navDropTimer = setTimeout(function() { panel.classList.add('hidden'); }, 150);
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/base.html
git commit -m "feat: reorganize nav into Book/Activity/Tools dropdown menus"
```

---

### Task 2: Capture Sub-Bar + Auto-Copy + Keyboard Shortcuts

**Files:**
- Modify: `src/policydb/web/templates/base.html`
- Modify: `src/policydb/web/routes/inbox.py`

- [ ] **Step 1: Move capture input to a sub-bar below the nav**

Remove the capture `<form>` from inside the nav's right-side `<div>` (currently between the nav links and the search input).

Add a new sub-bar element immediately after the closing `</nav>` tag and before the HTMX progress bar:

```html
{# Capture sub-bar #}
<div class="bg-marsh/80 border-b border-marsh-light no-print">
  <div class="mx-auto max-w-[1600px] px-4 sm:px-6 lg:px-8 flex items-center gap-3 py-1">
    <span class="text-white/35 text-[11px] flex-shrink-0">Capture:</span>
    <form hx-post="/inbox/capture" hx-swap="none"
          hx-on::after-request="if(event.detail.successful){this.reset();var h=event.detail.xhr.getResponseHeader('HX-Trigger');if(h){var m=h.match(/INB-\\d+/);if(m)navigator.clipboard.writeText(m[0])}}"
          class="flex-1 max-w-xl">
      <input id="capture-input" type="text" name="content" placeholder="Type a note, press Enter..." required
        class="w-full bg-white/8 text-white placeholder-white/30 text-xs rounded px-3 py-1
               border border-white/15 focus:border-white/40 focus:outline-none transition-all">
    </form>
  </div>
</div>
```

- [ ] **Step 2: Update HTMX progress bar position**

Change line with `top-14` (the current progress bar) to `top-[84px]` to account for the nav (56px) + sub-bar (~28px):

```html
<div id="htmx-progress" class="h-0.5 bg-marsh fixed top-[84px] left-0 z-50 transition-all duration-300" style="width:0; opacity:0"></div>
```

- [ ] **Step 3: Add `.` keyboard shortcut for capture**

Add alongside the existing `/` search shortcut script block:

```javascript
document.addEventListener('keydown', function(e) {
  if (e.key === '.' && !e.target.closest('input,textarea,select,[contenteditable]')) {
    e.preventDefault();
    document.getElementById('capture-input').focus();
  }
});
```

- [ ] **Step 4: Update capture toast text in inbox.py**

In `src/policydb/web/routes/inbox.py`, update the `inbox_capture` function's response header from:

```python
"HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - paste into email"}'
```

To:

```python
"HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - copied to clipboard"}'
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/base.html src/policydb/web/routes/inbox.py
git commit -m "feat: capture sub-bar with auto-copy UID + period shortcut"
```

---

### Task 3: Inbox "Schedule" Quick-Path + Inline Add

**Files:**
- Modify: `src/policydb/web/routes/inbox.py`
- Modify: `src/policydb/web/templates/inbox.html`

- [ ] **Step 1: Add schedule endpoint to inbox.py**

Add after the existing `inbox_dismiss` function:

```python
@router.post("/inbox/{inbox_id}/schedule", response_class=HTMLResponse)
def inbox_schedule(
    inbox_id: int,
    client_id: int = Form(...),
    follow_up_date: str = Form(...),
    subject: str = Form(""),
    conn=Depends(get_db),
):
    """Schedule inbox item as a Task follow-up."""
    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
           VALUES (?, ?, 'Task', ?, ?, ?)""",
        (date.today().isoformat(), client_id, subject or "Inbox item",
         follow_up_date, account_exec),
    )
    activity_id = cursor.lastrowid
    conn.execute(
        "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (activity_id, inbox_id),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + (uid["inbox_uid"] if uid else '') + ' scheduled"}'
    })
```

- [ ] **Step 2: Add Schedule button and form to inbox.html**

Add a "Schedule" button next to "Process" in the action buttons area (after the Process button, before Dismiss):

```html
<button onclick="toggleScheduleForm({{ item.id }})"
  class="text-xs bg-amber-100 text-amber-700 px-3 py-1 rounded hover:bg-amber-200 transition-colors">
  Schedule
</button>
```

Add a hidden schedule form after the process form `<div>`:

```html
{# Schedule form — hidden by default #}
<div id="schedule-form-{{ item.id }}" class="hidden border-t border-gray-100 bg-amber-50/50 px-4 py-3">
  <form hx-post="/inbox/{{ item.id }}/schedule" hx-swap="delete" hx-target="#inbox-item-{{ item.id }}"
        class="flex items-end gap-3 flex-wrap">
    <div>
      <label class="text-xs font-medium text-gray-500 block mb-1">Client</label>
      <select name="client_id" required
        class="text-sm border border-gray-200 rounded px-2 py-1.5 focus:border-marsh focus:outline-none">
        <option value="">Select client...</option>
        {% for c in all_clients %}
        <option value="{{ c.id }}" {% if item.client_id == c.id %}selected{% endif %}>{{ c.name }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="text-xs font-medium text-gray-500 block mb-1">Follow-up Date</label>
      <input type="date" name="follow_up_date" required
        class="text-sm border border-gray-200 rounded px-2 py-1.5 focus:border-marsh focus:outline-none">
    </div>
    <div class="flex-1">
      <label class="text-xs font-medium text-gray-500 block mb-1">Subject</label>
      <input type="text" name="subject" value="{{ item.content }}"
        class="w-full text-sm border border-gray-200 rounded px-2 py-1.5 focus:border-marsh focus:outline-none">
    </div>
    <div class="flex gap-2">
      <button type="submit" class="text-xs bg-amber-600 text-white px-4 py-1.5 rounded hover:bg-amber-700 transition-colors font-medium">
        Schedule Task
      </button>
      <button type="button" onclick="toggleScheduleForm({{ item.id }})"
        class="text-xs text-gray-500 px-3 py-1.5 rounded hover:bg-gray-100 transition-colors">
        Cancel
      </button>
    </div>
  </form>
</div>
```

Add the JS toggle function alongside the existing `toggleProcessForm`:

```javascript
function toggleScheduleForm(id) {
  var el = document.getElementById('schedule-form-' + id);
  if (el) el.classList.toggle('hidden');
  // Close the process form if open
  var pf = document.getElementById('process-form-' + id);
  if (pf && !pf.classList.contains('hidden')) pf.classList.add('hidden');
}
```

Update the existing `toggleProcessForm` to also close the schedule form:

```javascript
function toggleProcessForm(id) {
  var el = document.getElementById('process-form-' + id);
  if (el) el.classList.toggle('hidden');
  // Close the schedule form if open
  var sf = document.getElementById('schedule-form-' + id);
  if (sf && !sf.classList.contains('hidden')) sf.classList.add('hidden');
}
```

- [ ] **Step 3: Add inline capture input to inbox page**

At the top of `inbox.html`, between the header and the pending items list, add an inline capture form so users can add items directly from the inbox page:

```html
<div class="card px-4 py-2.5 mb-4 flex items-center gap-3">
  <span class="text-xs text-gray-400 flex-shrink-0">Add item:</span>
  <form hx-post="/inbox/capture" hx-swap="none"
        hx-on::after-request="if(event.detail.successful){this.reset();htmx.ajax('GET','/inbox','#inbox-pending')}"
        class="flex-1">
    <input type="text" name="content" placeholder="Type to add an inbox item..." required
      class="w-full text-sm border border-gray-200 rounded px-3 py-1.5 focus:border-marsh focus:outline-none">
  </form>
</div>
```

On successful capture, the form resets and triggers a refresh of the pending items list. The `hx-on::after-request` handler calls `htmx.ajax('GET', '/inbox', '#inbox-pending')` to reload the page — wrap the pending items `<div>` with `id="inbox-pending"` if not already present, or simply reload via `location.reload()` as a simpler approach:

```
hx-on::after-request="if(event.detail.successful){this.reset();location.reload()}"
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/inbox.py src/policydb/web/templates/inbox.html
git commit -m "feat: inbox Schedule quick-path + inline add item"
```

---

### Task 4: Manual Test + Fixes

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

- [ ] **Step 2: Manual test checklist**

1. **Dropdowns:** Hover Book/Activity/Tools — panels appear. Move mouse to items — panel stays. Mouse away — panel closes after 150ms.
2. **Active highlighting:** Navigate to /clients — "Book" gets gold underline. Navigate to /followups — "Activity" gets gold underline. Navigate to /settings — "Tools" gets gold underline.
3. **Capture bar:** Visible below nav on all pages. Type text, press Enter — toast shows "Captured INB-X - copied to clipboard". Paste from clipboard — INB-X is there.
4. **`.` shortcut:** Press `.` on any page (not in an input) — capture input focuses. Press `/` — search input focuses.
5. **Inbox Schedule:** Navigate to /inbox. Click "Schedule" on an item — compact form appears. Fill client + date, submit — item removed, toast confirms. Check /followups — task appears.
6. **Inbox Process:** Still works as before. Click "Process" — full form appears. Schedule form closes if open.
7. **Inbox Dismiss:** Still works.
8. **Progress bar:** HTMX requests show the loading bar below the capture sub-bar, not overlapping nav.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: nav reorganization adjustments from manual testing"
```
