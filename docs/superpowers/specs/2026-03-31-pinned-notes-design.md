# Pinned Notes — Design Spec

## Problem

There's no way to attach persistent, always-visible alerts to a client, policy, or project. Important account-level facts like "commission capped at 10%" or "CFO requires 30-day notice" get buried in activity logs or forgotten. Users need these facts in their face every time they touch a record.

## Solution

A **pinned notes banner** — a stacked list of short, color-coded alerts that sits below the page header on client, policy, and project detail pages. Notes cascade downward: a client note appears on all of that client's policy and project pages.

---

## Data Model

### New table: `pinned_notes` (migration 118)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `scope` | TEXT NOT NULL | `'client'`, `'policy'`, or `'project'` |
| `scope_id` | TEXT NOT NULL | `client_id`, `policy_uid`, or `project_id` |
| `headline` | TEXT NOT NULL | Short alert text (1-2 lines) |
| `detail` | TEXT | Optional expanded context |
| `color` | TEXT NOT NULL DEFAULT `'amber'` | One of: `red`, `amber`, `blue`, `green` |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | For manual ordering |
| `created_at` | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| `updated_at` | DATETIME DEFAULT CURRENT_TIMESTAMP | |

**Index:** `(scope, scope_id)` for fast lookups.

**Trigger:** `updated_at` auto-set on UPDATE.

---

## UI — Stacked Banner

### Appearance

- Stacked rows inside a rounded border container
- Each row: colored left border (3px), headline text, expand chevron (▸)
- Bottom row: `+ Add note` link
- Empty state: banner is hidden entirely (no empty container)

### Color palette

| Color | Left border | Background | Text |
|-------|-------------|------------|------|
| Red | `#ef4444` | `#fef2f2` | `#991b1b` |
| Amber | `#f59e0b` | `#fffbeb` | `#92400e` |
| Blue | `#3b82f6` | `#eff6ff` | `#1e40af` |
| Green | `#22c55e` | `#f0fdf4` | `#166534` |

### Interactions

**View:** Click chevron (▸/▾) to expand a note and show:
- Detail text (if any)
- Color picker dots (4 circles — click to change)
- Created date
- Delete link

**Edit headline:** Click headline text → contenteditable. Saves on blur via PATCH.

**Edit detail:** Click detail area → contenteditable. Saves on blur via PATCH.

**Change color:** Click a color dot → PATCH color field → re-render row with new color.

**Add note:** Click `+ Add note` → inline form expands:
- Headline input (required)
- Detail textarea (optional)
- Color picker dots (default: amber)
- Save / Cancel buttons
- POST creates note, re-renders full banner

**Delete:** Click `Delete` in expanded view → confirm inline → DELETE → re-render banner.

---

## Cascade Logic

Notes cascade **downward** through the hierarchy. Cascaded notes are read-only on child pages and show a scope badge.

| Page | Notes shown |
|------|-------------|
| Client detail | `scope='client' AND scope_id=client_id` |
| Policy detail | Own (`scope='policy' AND scope_id=policy_uid`) + client's (`scope='client' AND scope_id=client_id`) |
| Project detail | Own (`scope='project' AND scope_id=project_id`) + client's (`scope='client' AND scope_id=client_id`) |

**Cascaded notes display:**
- Subtle scope badge: small `CLIENT` pill next to headline
- Slightly reduced opacity (0.85)
- Read-only — no edit/delete controls. Click shows detail but not edit UI.
- Sorted after own notes (own notes first, then cascaded, each group by sort_order)

---

## API Endpoints

All endpoints return the full updated banner partial for HTMX swap.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/pinned-notes?scope={s}&scope_id={id}` | Render banner HTML (with cascade) |
| `POST` | `/api/pinned-notes` | Create note (form: headline, detail, color, scope, scope_id) |
| `PATCH` | `/api/pinned-notes/{id}` | Update field (headline, detail, or color) |
| `DELETE` | `/api/pinned-notes/{id}` | Delete note |

### Route location

New route module: `src/policydb/web/routes/pinned_notes.py` — keeps it self-contained. Register in `app.py`.

---

## Template

### Shared partial: `src/policydb/web/templates/_pinned_notes_banner.html`

Expects context:
- `pinned_notes` — list of note dicts (own + cascaded)
- `pinned_scope` — current page scope (`'client'`, `'policy'`, `'project'`)
- `pinned_scope_id` — current page scope_id

The partial is `{% include %}`-ed on:
- `clients/detail.html` — below header, above tabs
- `policies/detail.html` — below header, above tabs
- `clients/project.html` — below header, above tabs

### Banner container ID: `#pinned-notes-banner`

All HTMX responses target this ID with `hx-swap="outerHTML"`.

---

## Query Functions

In `src/policydb/queries.py` (or inline in route module):

- `get_pinned_notes(conn, scope, scope_id)` — returns notes for a single scope/id, ordered by sort_order
- `get_pinned_notes_with_cascade(conn, scope, scope_id, client_id=None)` — returns own notes + cascaded client notes, with a `cascaded` boolean flag on each row
- `create_pinned_note(conn, scope, scope_id, headline, detail, color)` — INSERT, returns new id
- `update_pinned_note(conn, note_id, **fields)` — UPDATE specific fields
- `delete_pinned_note(conn, note_id)` — DELETE

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/policydb/migrations/118_pinned_notes.sql` | Create table, index, trigger |
| `src/policydb/db.py` | Wire migration 118 into `init_db()` |
| `src/policydb/web/routes/pinned_notes.py` | New route module — CRUD endpoints |
| `src/policydb/web/app.py` | Register pinned_notes router |
| `src/policydb/web/templates/_pinned_notes_banner.html` | Shared banner partial |
| `src/policydb/web/templates/clients/detail.html` | Include banner partial |
| `src/policydb/web/templates/policies/detail.html` | Include banner partial |
| `src/policydb/web/templates/clients/project.html` | Include banner partial |

---

## Verification

1. Start server (`pdb serve`), navigate to a client detail page
2. Verify banner is hidden when no pinned notes exist
3. Click `+ Add note`, enter headline "Commission capped at 10%", pick red, save
4. Verify note appears with red left border
5. Add a second note with blue color
6. Click chevron to expand — verify detail area, color picker, delete link
7. Edit headline inline — verify saves on blur
8. Navigate to a policy under that client — verify client notes cascade with "CLIENT" badge
9. Add a policy-level note — verify it appears above cascaded client notes
10. Verify cascaded notes are read-only (no edit/delete controls)
11. Delete a note — verify banner re-renders cleanly
12. Delete all notes — verify banner disappears entirely
