# Project Notes Page — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

The existing project/location notes field is a plain 2-row textarea embedded in the project header on the client detail page. Users now need to store large markdown documents (LLM-generated project summaries, coverage briefs, renewal analysis) tied to a project/location. The current field is too small, does not support markdown, and has no auto-save.

---

## Solution

A dedicated project page (`/clients/{client_id}/projects/{project_id}`) with a full-width EasyMDE markdown editor, auto-save, and contextual sidebar. The client detail page project header gains a truncated markdown preview and a link to this page.

---

## URL & Routing

- **Dedicated page:** `GET /clients/{client_id}/projects/{project_id}`
- **Auto-save endpoint:** `POST /clients/{client_id}/projects/{project_id}/notes`
  - Accepts: `FormData` with `content` field
  - Executes: `UPDATE projects SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?` (plain UPDATE, not upsert — omit `updated_at` from the SET clause to avoid a redundant trigger fire; the migration 026 trigger handles it)
  - Returns `{"ok": true, "saved_at": "Mar 18 at 2:14pm"}` on success
  - Returns HTTP 404 if `cursor.rowcount == 0` (project not found or wrong client)
- Both routes added to `src/policydb/web/routes/clients.py`

---

## Navigation

- **Breadcrumb** at the top of the project page: `Clients / {client.name} / {project.name}` — consistent with all other detail pages in the app.
- `{client.name}` links to `/clients/{client_id}`.

---

## Page Layout

Two-column layout at `sm:` breakpoint and above; stacks single-column on mobile.

### Left column (~65%) — Editor

- **Header:** Project name (h1), client name subtitle, auto-save timestamp ("Saved Mar 18 at 2:14pm"), word count ("1,240 words")
- **Editor:** Full-width EasyMDE instance initialized with `window.initMarkdownEditor(id, {minHeight: '500px'})`. Pre-populated from `projects.notes`.
- **Auto-save:** `mde.codemirror.on('change', ...)` with 800ms debounce. On success updates timestamp display.
- **Word count:** Computed as `mde.value().trim().split(/\s+/).filter(Boolean).length`; updated on the same `'change'` handler as auto-save.

### Right sidebar (~35%)

- **Policies in this project:** List of policies with line of business + carrier, each linking to `/policies/{uid}/edit`
- **Address block:** Read-only display of street/city/state/zip. Note: "Edit address on the client page."
- **Metadata:** Created date, last updated date

---

## Client Detail Page — Project Header Updates

File: `src/policydb/web/templates/clients/_project_header.html`

Changes:
1. **Project name becomes a link** to `/clients/{client_id}/projects/{project_id}`. In `_project_header.html`, use `_proj_id` — the Jinja2 loop-local variable already set in `detail.html` just before the include (do not rename it; just reference `_proj_id` directly in the template).
2. **Preview replaces the plain text note display:**
   - Display `{{ note | truncate(150) }}` as plain text inside a `<p>` tag — no markdown rendering in the preview
   - Follow with a "View full note →" link to the project page
   - If `notes` is empty: show "Add project notes →" as the link instead
3. Existing Edit / Log / Email / Rename / Merge / Delete buttons remain unchanged

---

## Data Layer

**No migration required.** The `projects` table (migration 026) already provides:

```sql
CREATE TABLE projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, name)
);
```

The auto-save endpoint updates `notes` and `updated_at` on the matching row.

**Context for project page route:**
```python
project = conn.execute(
    "SELECT * FROM projects WHERE id = ? AND client_id = ?", (project_id, client_id)
).fetchone()
policies = conn.execute(
    "SELECT policy_uid, policy_type, carrier, renewal_status FROM policies WHERE project_id = ? ORDER BY policy_type",
    (project_id,)
).fetchall()
client = conn.execute("SELECT id, name FROM clients WHERE id = ?", (client_id,)).fetchone()
```

**Ensure `project_id` is available in `_project_header.html` context.** The client detail route must include `project_id` when building the project header context. Verify this is already passed; add if missing.

---

## Files

| Action | File |
|--------|------|
| Create | `src/policydb/web/templates/clients/project.html` |
| Modify | `src/policydb/web/routes/clients.py` |
| Modify | `src/policydb/web/templates/clients/_project_header.html` |

---

## Verification

1. `policydb serve`, open a client with at least one project/location group
2. Click the project name link → navigates to `/clients/{id}/projects/{project_id}`
3. Type in the editor → "Saved X:XXpm" appears within ~1 second, no page reload
4. Paste 500+ word markdown → word count updates, content persists after page reload
5. Return to client detail page → project header shows truncated rendered preview
6. "View full note →" link present; "Add project notes →" shown when notes empty
7. Sidebar shows correct policies with working links to their edit pages
