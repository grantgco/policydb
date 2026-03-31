# DevonThink-First Files Everywhere

**Date:** 2026-03-31
**Status:** Design

## Context

The universal attachment system (DevonThink link + local upload) is already built and deployed on 4 pages (Policy Details tab, Client Overview tab, RFI Bundle, Meeting detail). DevonThink is the default attachment method when available — paste an `x-devonthink-item://` URL and metadata auto-fetches via AppleScript.

The user wants to make this the standard file management experience across the entire app: dedicated Files tabs on tabbed pages, inline panels on single-page layouts, a client-level rollup showing all files across child records, a paperclip icon on activity rows, and a description/notes field on every attachment card.

## Changes

### 1. Description Field on Attachment Cards

The `attachments` table already has a `description` TEXT column. Surface it in the UI.

**Template:** `attachments/_attachments_panel.html`

- Add a click-to-edit contenteditable line below the title/metadata on each attachment card
- PATCH on blur to `/api/attachments/{uid}` with `{description: value}` — endpoint already accepts this
- Shows as subtle `text-xs text-gray-400` when populated, `empty:before` placeholder "Add a note..." when empty
- Restructure card layout: wrap title+metadata+description in a flex-col container so description sits below the metadata line

### 2. Policy "Files" Tab

Move the attachment section from the Details tab to its own dedicated tab.

**Files to change:**
- `policies/edit.html` — add Files tab button (after Workflow tab)
- `policies/_tab_files.html` — new template, minimal: just the HTMX loader for `/api/attachments/panel?record_type=policy&record_id={{ policy.id }}`
- `policies/_tab_details.html` — remove the "Files & Attachments" card (lines ~594-604)
- `web/routes/policies.py` — add `GET /{uid}/tab/files` route handler
- `web/routes/policies.py` — add `attachment_count` to the main policy edit route context (for tab badge)

**Tab badge:** Show count on the Files tab button: `Files (3)` — query `SELECT COUNT(*) FROM record_attachments WHERE record_type='policy' AND record_id=?`

### 3. Client "Files" Tab (Rollup View)

New tab on the client page showing all attachments from the client and all its child records.

**Files to change:**
- `clients/detail.html` — add Files tab button (after Issues tab)
- `clients/_tab_files.html` — new template (see layout below)
- `clients/_tab_overview.html` — remove the "Files & Attachments" card (lines ~237-247)
- `web/routes/clients.py` — add `GET /{client_id}/tab/files` route handler with rollup query
- `web/routes/attachments.py` — (optional) add rollup query helper

**Rollup query** joins `record_attachments` → `attachments` where record belongs to this client:

```sql
SELECT a.*, ra.id as link_id, ra.record_type, ra.record_id, ra.sort_order,
  CASE ra.record_type
    WHEN 'client' THEN 'Client-level'
    WHEN 'policy' THEN (SELECT policy_uid || ' — ' || COALESCE(policy_type,'') FROM policies WHERE id = ra.record_id)
    WHEN 'meeting' THEN (SELECT title || ' ' || meeting_date FROM client_meetings WHERE id = ra.record_id)
    WHEN 'activity' THEN (SELECT activity_type || ': ' || subject FROM activity_log WHERE id = ra.record_id)
    WHEN 'project' THEN (SELECT name FROM projects WHERE id = ra.record_id)
    WHEN 'rfi_bundle' THEN (SELECT title FROM client_request_bundles WHERE id = ra.record_id)
  END AS source_label
FROM attachments a
JOIN record_attachments ra ON ra.attachment_id = a.id
WHERE (ra.record_type = 'client' AND ra.record_id = :cid)
   OR (ra.record_type = 'policy' AND ra.record_id IN (SELECT id FROM policies WHERE client_id = :cid))
   OR (ra.record_type = 'meeting' AND ra.record_id IN (SELECT id FROM client_meetings WHERE client_id = :cid))
   OR (ra.record_type = 'activity' AND ra.record_id IN (SELECT id FROM activity_log WHERE client_id = :cid))
   OR (ra.record_type = 'project' AND ra.record_id IN (SELECT id FROM projects WHERE client_id = :cid))
   OR (ra.record_type = 'rfi_bundle' AND ra.record_id IN (SELECT id FROM client_request_bundles WHERE client_id = :cid))
ORDER BY ra.record_type, source_label, ra.sort_order
```

**Tab badge:** `SELECT COUNT(DISTINCT a.id)` variant of the same query, passed from the main client detail route.

**Template layout:**

Header bar:
- Total count: "23 files"
- View toggle: [Grouped] [Flat] — pill buttons, swap via HTMX `hx-get` with `?view=grouped|flat`
- Search input: filters by attachment title, debounced HTMX
- "+ Attach to Client" button: toggles the standard `_attachments_panel.html` form with `record_type=client`

Grouped view (default):
```
Client-level (2)
  [card] [card]

POL-042 — General Liability (3)
  [card] [card] [card]

Meeting 3/15 — Quarterly Review (1)
  [card]
```
Each group header links to the source record. Each card uses the standard attachment card markup from `_attachments_panel.html` (extracted to a sub-partial or inlined).

Flat view:
- Single list sorted by `created_at DESC`
- Filter pills: record type (Policy, Meeting, Activity, etc.), category, file type
- Same card markup per row

### 4. Project Detail Page (Inline Panel)

`clients/project.html` is a single-page layout (no tabs). Add the attachment panel inline.

**Template:** `clients/project.html` — add a card section:
```html
<div class="card p-4">
  <div hx-get="/api/attachments/panel?record_type=project&record_id={{ project.id }}"
       hx-trigger="load" hx-swap="innerHTML">
    <p class="text-xs text-gray-400 italic">Loading...</p>
  </div>
</div>
```

**Backend:** `attachments.py` — add `"project"` to `_VALID_RECORD_TYPES`.

No other route changes needed — the existing `/api/attachments/panel` endpoint handles this.

### 5. Activity Row Paperclip

Activity rows are compact inline `<li>` elements — no full page. Add a paperclip icon with expandable attachment panel.

**Attachment count:** Batch-fetch counts in `get_activities()` (Python-side merge, not correlated subquery) to avoid N+1:
```python
ids = [a["id"] for a in activities]
counts = conn.execute(
    "SELECT record_id, COUNT(*) as cnt FROM record_attachments "
    "WHERE record_type='activity' AND record_id IN (...) GROUP BY record_id"
).fetchall()
```

**UI — paperclip icon** on each activity row (after the ref tag pill, within the flex-wrap row of badges):
- When `attachment_count > 0`: always-visible paperclip with count badge
- When `attachment_count == 0`: faded paperclip that appears on row hover (`opacity-0 group-hover:opacity-100`)
- Click toggles a hidden `<li>` sibling below the activity row

**UI — expandable panel** as a sibling `<li>` below the activity row:
```html
<li id="attach-panel-{{ a.id }}" class="hidden px-5 py-3 bg-blue-50/30 border-l-4 border-blue-200">
  <div hx-get="/api/attachments/panel?record_type=activity&record_id={{ a.id }}"
       hx-trigger="intersect once" hx-swap="innerHTML">
    Loading...
  </div>
</li>
```

Uses `hx-trigger="intersect once"` so it only fetches when first revealed.

**Files to change:**
- `activities/_activity_row.html` — add paperclip icon + hidden panel `<li>`
- `web/routes/activities.py` or `queries.py` — add batch attachment count to activity list queries

### 6. KB Article Migration

Replace the legacy `kb/_attachments.html` with the universal `_attachments_panel.html`.

**Template:** `kb/article.html` — replace the Attachments card (lines ~87-93) with:
```html
<div class="card p-3">
  <div hx-get="/api/attachments/panel?record_type=kb_article&record_id={{ article.id }}"
       hx-trigger="load" hx-swap="innerHTML">
    Loading...
  </div>
</div>
```

Migration 117 already copied `kb_attachments` → `record_attachments` with `record_type='kb_article'`, so existing linked files will appear. Keep legacy `kb/_attachments.html` file in place temporarily for safety.

## Implementation Order

| # | Phase | Risk | Files |
|---|-------|------|-------|
| 1 | Backend: add `project` to valid types | Low | `attachments.py` |
| 2 | Description field on cards | Low | `_attachments_panel.html` |
| 3 | Policy Files tab | Low | `edit.html`, `_tab_files.html` (new), `_tab_details.html`, `policies.py` |
| 4 | Project inline panel | Low | `project.html` |
| 5 | KB article migration | Medium | `kb/article.html` |
| 6 | Activity paperclip | Medium | `_activity_row.html`, `activities.py` or `queries.py` |
| 7 | Client Files tab (rollup) | High | `detail.html`, `_tab_files.html` (new), `_tab_overview.html`, `clients.py` |

## Gotchas

1. **No migration needed** — `record_attachments.record_type` is free-text, `_VALID_RECORD_TYPES` in Python is the only gate
2. **Table name:** RFI bundles table is `client_request_bundles` (not `rfi_bundles`)
3. **Contenteditable empty state:** Use `{% if att.description %}{{ att.description }}{% endif %}` with no whitespace between tags, so `empty:before` CSS works
4. **Client rollup query performance:** Uses IN-subqueries across 5 tables; `LIMIT 500` safety valve on outer query
5. **Activity count batch-fetch:** Don't use correlated subquery in list views — batch in Python after main query
6. **Card layout restructure:** Current card is single-row flex; description field needs flex-col wrapper for title+meta+description
7. **Tab badge counts:** Must be computed in the main page route (not the tab route) for initial render

## Verification

1. Start `pdb serve`, navigate to each affected page
2. Policy page: verify Files tab appears, attachment panel loads, description editable
3. Client page: verify Files tab shows rollup grouped/flat, toggle works, search filters
4. Project page: verify inline panel loads
5. Activity list: verify paperclip icon, click expands panel, attach/detach works
6. KB article: verify universal panel replaced legacy UI, existing attachments visible
7. DevonThink: paste a DT link on each page, verify metadata fetch + card display
8. Cross-page: attach a file on policy, verify it appears in client Files tab rollup
