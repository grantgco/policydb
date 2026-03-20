# Project Notes Dedicated Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated per-project markdown notes page at `/clients/{client_id}/projects/{project_id}` with EasyMDE auto-save, and upgrade the client detail page project header to show a truncated plain-text preview linking to it.

**Architecture:** Two new route functions in `clients.py` (GET page + POST auto-save), one new template `clients/project.html`, and targeted edits to `_project_header.html`. No migration needed — `projects.notes` and `projects.updated_at` already exist in migration 026.

**Tech Stack:** FastAPI, Jinja2, HTMX, EasyMDE (`window.initMarkdownEditor` in base.html), SQLite, Babel (for timestamp formatting)

**Spec:** `docs/superpowers/specs/2026-03-18-project-notes-page-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/policydb/web/routes/clients.py` | Two new routes: GET project page + POST auto-save |
| Create | `src/policydb/web/templates/clients/project.html` | Dedicated project notes page (two-column layout) |
| Modify | `src/policydb/web/templates/clients/_project_header.html` | Project name → link, add truncated preview |

---

## Task 1: Auto-save route

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

Find the existing `project_note_save` POST route (search for `project-note`) and add the new auto-save route immediately after it.

- [ ] **Step 1.1: Add the import for Babel** (if not already present at top of `clients.py`)

```python
from babel.dates import format_datetime as babel_format_datetime
from datetime import datetime
```

- [ ] **Step 1.2: Add the auto-save route**

Add after the existing `project-note` route block. Use the same synchronous `def` + `Depends(get_db)` pattern as every other route in `clients.py`. `HTTPException` and `JSONResponse` are already imported at the top of that file — do not add inline imports.

```python
@router.post("/{client_id}/projects/{project_id}/notes")
def project_notes_autosave(
    client_id: int,
    project_id: int,
    content: str = Form(""),
    conn=Depends(get_db),
):
    cur = conn.execute(
        "UPDATE projects SET notes = ? WHERE id = ? AND client_id = ?",
        (content, project_id, client_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    saved_at = babel_format_datetime(
        datetime.now(), "MMM d 'at' h:mma", locale="en_US"
    ).replace("AM", "am").replace("PM", "pm")
    return JSONResponse({"ok": True, "saved_at": saved_at})
```

- [ ] **Step 1.3: Smoke-test the endpoint**

Start the server (`policydb serve`), then:

```bash
# Find a real project_id
sqlite3 ~/.policydb/policydb.sqlite "SELECT id, client_id, name FROM projects LIMIT 3;"

# POST to the endpoint (replace 1 and 2 with real client_id and project_id)
curl -s -X POST http://127.0.0.1:8000/clients/1/projects/2/notes \
  -F "content=Test note content" | python3 -m json.tool
```

Expected: `{"ok": true, "saved_at": "Mar 18 at 2:14pm"}` (timestamp varies)

- [ ] **Step 1.4: Verify 404 on bad project**

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://127.0.0.1:8000/clients/999/projects/999/notes \
  -F "content=test"
```

Expected: `404`

- [ ] **Step 1.5: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: add project notes auto-save endpoint"
```

---

## Task 2: Project page route (GET)

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 2.1: Add the GET route**

Add immediately after the auto-save route from Task 1. Same sync `def` + `Depends(get_db)` pattern. Format `updated_at` with Babel so the initial page load shows the same format as the auto-save timestamp ("Mar 18 at 2:14pm"). `HTTPException` is already imported at top of file.

```python
@router.get("/{client_id}/projects/{project_id}")
def project_detail(
    client_id: int,
    project_id: int,
    request: Request,
    conn=Depends(get_db),
):
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND client_id = ?",
        (project_id, client_id),
    ).fetchone()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    policies = conn.execute(
        """SELECT policy_uid, policy_type, carrier, renewal_status
           FROM policies
           WHERE project_id = ?
           ORDER BY policy_type""",
        (project_id,),
    ).fetchall()
    project = dict(project)
    if project.get("updated_at"):
        try:
            from dateparser import parse as dp_parse
            dt = dp_parse(project["updated_at"])
            if dt:
                project["updated_at_fmt"] = babel_format_datetime(
                    dt, "MMM d 'at' h:mma", locale="en_US"
                ).replace("AM", "am").replace("PM", "pm")
        except Exception:
            project["updated_at_fmt"] = project["updated_at"][:16]
    return templates.TemplateResponse(
        "clients/project.html",
        {
            "request": request,
            "project": project,
            "client": dict(client),
            "policies": [dict(p) for p in policies],
        },
    )
```

- [ ] **Step 2.2: Smoke-test the route (404 case)**

```bash
curl -s -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8000/clients/999/projects/999
```

Expected: `404`

The 200 case will be tested after the template is created in Task 3.

- [ ] **Step 2.3: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: add project detail page route"
```

---

## Task 3: Project page template

**Files:**
- Create: `src/policydb/web/templates/clients/project.html`

- [ ] **Step 3.1: Create the template**

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} — PolicyDB{% endblock %}
{% block content %}

{# ── Breadcrumb ── #}
<div class="flex items-center gap-2 text-sm text-gray-400 mb-1">
  <a href="/clients" class="hover:text-marsh">Clients</a>
  <span>/</span>
  <a href="/clients/{{ client.id }}" class="hover:text-marsh">{{ client.name }}</a>
  <span>/</span>
  <span class="text-gray-600 font-medium">{{ project.name }}</span>
</div>

<div class="flex flex-col sm:flex-row gap-6 mt-4">

  {# ── Left column: editor ── #}
  <div class="flex-1 min-w-0">

    <div class="flex items-baseline justify-between gap-4 mb-3">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">{{ project.name }}</h1>
        <p class="text-sm text-gray-400 mt-0.5">{{ client.name }}</p>
      </div>
      <div class="text-right shrink-0">
        <span id="proj-word-count" class="text-xs text-gray-400"></span>
        <span class="text-gray-300 mx-1">·</span>
        <span id="proj-save-ts" class="text-xs text-gray-400">
          {% if project.updated_at_fmt %}saved {{ project.updated_at_fmt }}{% endif %}
        </span>
      </div>
    </div>

    <div class="card p-4">
      <textarea id="proj-notes-ta" rows="20"
        class="w-full text-sm text-gray-800 bg-transparent resize-none focus:outline-none">{{ project.notes or '' }}</textarea>
    </div>

  </div>

  {# ── Right sidebar ── #}
  <div class="sm:w-72 shrink-0 space-y-4">

    {# Policies #}
    <div class="card p-4">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Policies in this project
      </p>
      {% if policies %}
      <ul class="space-y-1.5">
        {% for pol in policies %}
        <li>
          <a href="/policies/{{ pol.policy_uid }}/edit"
             class="flex items-center justify-between text-sm text-marsh hover:underline group">
            <span>{{ pol.policy_type }}</span>
            <span class="text-xs text-gray-400 group-hover:text-gray-600">{{ pol.carrier or '—' }} ↗</span>
          </a>
        </li>
        {% endfor %}
      </ul>
      {% else %}
      <p class="text-xs text-gray-400 italic">No policies assigned to this project yet.</p>
      {% endif %}
    </div>

    {# Address #}
    {% if project.exposure_address or project.exposure_city %}
    <div class="card p-4">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Address</p>
      <p class="text-sm text-gray-700">
        {% if project.exposure_address %}{{ project.exposure_address }}<br>{% endif %}
        {% if project.exposure_city %}{{ project.exposure_city }}{% if project.exposure_state %}, {{ project.exposure_state }}{% endif %}
        {% if project.exposure_zip %} {{ project.exposure_zip }}{% endif %}{% endif %}
      </p>
      <a href="/clients/{{ client.id }}" class="text-xs text-gray-400 hover:text-marsh mt-1 block">Edit on client page ↗</a>
    </div>
    {% endif %}

    {# Metadata #}
    <div class="card p-4">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Metadata</p>
      <dl class="text-xs text-gray-500 space-y-1">
        <div class="flex justify-between">
          <dt>Created</dt>
          <dd>{{ project.created_at[:10] if project.created_at else '—' }}</dd>
        </div>
        <div class="flex justify-between">
          <dt>Updated</dt>
          <dd>{{ project.updated_at[:10] if project.updated_at else '—' }}</dd>
        </div>
        <div class="flex justify-between">
          <dt>Policies</dt>
          <dd>{{ policies | length }}</dd>
        </div>
      </dl>
    </div>

  </div>
</div>

<script>
(function () {
  var UID = {{ project.id | tojson }};
  var CID = {{ client.id | tojson }};
  var endpoint = '/clients/' + CID + '/projects/' + UID + '/notes';

  var mde = window.initMarkdownEditor('proj-notes-ta', { minHeight: '500px' });
  var tsEl = document.getElementById('proj-save-ts');
  var wcEl = document.getElementById('proj-word-count');
  var saveTimer = null;

  function wordCount(text) {
    return text.trim().split(/\s+/).filter(Boolean).length;
  }

  function updateWordCount() {
    var wc = wordCount(mde ? mde.value() : document.getElementById('proj-notes-ta').value);
    wcEl.textContent = wc > 0 ? wc.toLocaleString() + ' words' : '';
  }

  function autoSave() {
    var content = mde ? mde.value() : document.getElementById('proj-notes-ta').value;
    var fd = new FormData();
    fd.set('content', content);
    fetch(endpoint, { method: 'POST', body: fd, headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok && tsEl) tsEl.textContent = 'saved ' + data.saved_at;
      });
  }

  if (mde) {
    mde.codemirror.on('change', function () {
      updateWordCount();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(autoSave, 800);
    });
  }

  updateWordCount();
})();
</script>

{% endblock %}
```

- [ ] **Step 3.2: Smoke-test the full page**

```bash
# Get a real project_id and client_id
sqlite3 ~/.policydb/policydb.sqlite \
  "SELECT id, client_id, name FROM projects LIMIT 3;"

# Fetch the page (replace 1 and 2 with real values)
curl -s -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8000/clients/1/projects/2
```

Expected: `200`

Also open in browser: navigate to the page, type in the editor — "saved X:XXpm" should appear within 1 second, word count updates live.

- [ ] **Step 3.3: Verify persistence**

Type several sentences, wait 1 second for auto-save, reload the page. Content should be present.

- [ ] **Step 3.4: Commit**

```bash
git add src/policydb/web/templates/clients/project.html
git commit -m "feat: add project notes page template with EasyMDE and auto-save"
```

---

## Task 4: Update project header on client detail page

**Files:**
- Modify: `src/policydb/web/templates/clients/_project_header.html`

The goal is two changes:
1. Project name → link to the project page (using `_proj_id` which is already set in `detail.html` scope)
2. Notes display → plain-text truncated preview with "View full note →" or "Add project notes →"

- [ ] **Step 4.1: Read the current `_project_header.html`**

Open `src/policydb/web/templates/clients/_project_header.html` and locate:
- Where the project name is rendered (likely a `<span>` or `<p>`)
- Where the `note` variable is displayed (the existing plain-text note)

- [ ] **Step 4.2: Replace the project name with a link**

Find the element rendering the project name (something like `{{ project_name }}` or similar) and wrap it in an anchor:

```html
<a href="/clients/{{ client_id }}/projects/{{ _proj_id }}"
   class="font-semibold text-marsh hover:underline">{{ project_name }}</a>
```

If `_proj_id` is 0 or None (project exists by name but has no `projects` table row yet), fall back to plain text:

```html
{% if _proj_id %}
<a href="/clients/{{ client_id }}/projects/{{ _proj_id }}"
   class="font-semibold text-marsh hover:underline">{{ project_name }}</a>
{% else %}
<span class="font-semibold text-gray-700">{{ project_name }}</span>
{% endif %}
```

- [ ] **Step 4.3: Replace the note display with truncated preview + link**

Find the existing note display block (showing `{{ note }}` or similar) and replace with:

```html
{% if note %}
<span class="text-xs text-gray-500 italic">{{ note | truncate(150) }}</span>
{% if _proj_id %}
<a href="/clients/{{ client_id }}/projects/{{ _proj_id }}"
   class="text-xs text-marsh hover:underline ml-1 whitespace-nowrap">View full note →</a>
{% endif %}
{% else %}
{% if _proj_id %}
<a href="/clients/{{ client_id }}/projects/{{ _proj_id }}"
   class="text-xs text-gray-400 hover:text-marsh italic">Add project notes →</a>
{% else %}
<span class="text-xs text-gray-300 italic">No notes</span>
{% endif %}
{% endif %}
```

- [ ] **Step 4.4: Verify `_proj_id` is set in detail.html**

In `src/policydb/web/templates/clients/detail.html`, search for `_proj_id` (the loop-local variable). Confirm it is set via `{% set _proj_id = ... %}` just before `{% include "clients/_project_header.html" %}`. If it is not set, add:

```html
{% set _proj_id = project_ids.get(project_name | lower, 0) %}
```

(The exact expression depends on how `project_ids` is built in `clients.py`. Check the route.)

- [ ] **Step 4.5: Smoke-test the client detail page**

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/clients/1
```

Expected: `200`

Open in browser: project name should be a clickable link, notes preview shows first 150 chars.

- [ ] **Step 4.6: Commit**

```bash
git add src/policydb/web/templates/clients/_project_header.html \
        src/policydb/web/templates/clients/detail.html
git commit -m "feat: link project header to project notes page, add preview"
```

---

## Task 5: End-to-end verification

- [ ] **Step 5.1: Full flow test**

1. Open a client detail page with at least one project group
2. Project name is a link → click it → lands on `/clients/{id}/projects/{project_id}` (not 404)
3. Type in the editor → word count updates immediately
4. After ~1 second → timestamp appears ("saved Mar 18 at X:XXpm")
5. Paste 300+ word markdown → word count shows correctly
6. Reload page → content persists
7. Navigate back to client detail page → project header shows first 150 chars of notes
8. "View full note →" link is present and returns to the project page
9. Client with empty project notes → shows "Add project notes →"

- [ ] **Step 5.2: Sidebar verification**

On the project page, confirm:
- Policies list shows correct policies with working links to their edit pages
- Address block only appears if address fields are populated
- Word count and saved timestamp display correctly in header

- [ ] **Step 5.3: Final commit and push**

```bash
git status  # confirm clean
git log --oneline -5  # confirm commit history
```
