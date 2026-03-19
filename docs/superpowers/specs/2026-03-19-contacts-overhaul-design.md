# Contacts Management Overhaul — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** Cleanup deprecated edit patterns, show expertise in matrix rows, unify JS controllers, add contact detail page, improve merge UI, add bulk actions, add expandable indicators, add contact email tokens.

---

## Problem Statement

The contacts management system works well functionally but has accumulated inconsistencies:
- Old-style inline edit forms coexist with newer contenteditable matrix patterns
- Two separate JS controllers handle similar functionality across tabs
- Expertise pills don't show in matrix editing rows
- No dedicated contact detail page — all editing is inline with no full-picture view
- Merge UI is basic, bulk actions are missing, expandable columns lack visual indicators

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deprecated edit forms | Remove entirely | Contenteditable matrix IS the edit mode |
| JS controllers | Unify into one | Both do the same thing, eliminate code duplication |
| Expertise in matrix rows | Add pills + quick-tag | Currently only in display rows — gap |
| Contact detail page | Full dossier + edit hub | Both research and management use case |
| Merge UI | Side-by-side comparison | Better than current text input approach |
| Bulk actions | Checkbox + action bar | Same pattern as follow-ups bulk bar |
| Follow-ups on all tabs | Badge indicator on name | Lightweight, doesn't need full column |

---

## 1. Cleanup & Consistency

### Remove deprecated edit templates

Delete these files entirely:
- `src/policydb/web/templates/contacts/_edit_row.html`
- `src/policydb/web/templates/contacts/_internal_edit_row.html`
- `src/policydb/web/templates/contacts/_client_contact_edit_row.html`

Remove the corresponding GET/POST endpoints from `src/policydb/web/routes/contacts.py`:
- `GET /{name}/edit` and `POST /{name}/edit`
- `GET /internal/{name}/edit` and `POST /internal/{name}/edit`
- `GET /client/{name}/edit` and `POST /client/{name}/edit`

These are dead code — all four tabs use contenteditable matrix rows directly.

### Show expertise pills in matrix rows

Add expertise tag display and ⭐ quick-tag button to:
- `_placement_matrix_row.html` — after name, before cross-store badges
- `_internal_matrix_row.html` — same position
- `_client_matrix_row.html` — same position

Pattern: small colored pills (blue=line, green=industry) + ⭐ toggle. Same as `_expertise_pills.html` partial already created.

Requires: `_attach_expertise(conn, contacts)` called in the contacts list handler before passing to templates (already done in Task 3-6 of expertise plan).

### Unify JS matrix controllers

Replace the two separate controllers in `contacts/list.html`:
- "All People" controller (lines 488-606)
- Tab 2/3/4 `initContactMatrix()` (lines 620+)

With a single `initContactMatrix(config)` function that accepts:
```javascript
initContactMatrix({
  tableId: 'unified-table',
  patchUrl: '/contacts/unified/{name}/cell',
  addRowUrl: null,  // or '/contacts/add-row'
  fields: { editable: [...], combobox: [...], email: [...] },
});
```

Each tab calls `initContactMatrix()` with its own config. One code path, four configs.

### Follow-up indicators on all tabs

Currently only "All People" tab shows follow-ups. Add to placement/internal/client tabs:

On the name cell, if the contact has pending follow-ups, show a small badge:
```html
{% if c.open_followup_count %}
<span class="text-[9px] bg-red-100 text-red-600 px-1 py-0.5 rounded-full ml-1" title="{{ c.open_followup_count }} pending follow-up(s)">{{ c.open_followup_count }}</span>
{% endif %}
```

Requires: attach `open_followup_count` to contact dicts in the route handler (batch query on `activity_log`).

---

## 2. Contact Detail Page

### Route

`GET /contacts/{contact_id}` — full contact detail/management page.

### Layout

**Header section:**
- Name (large, contenteditable)
- Organization (contenteditable)
- Email, Phone, Mobile (contenteditable with format-on-blur)
- Expertise pills (line + industry) with ⭐ toggle for editing
- Expertise notes (contenteditable text area)
- All edits save via PATCH to existing endpoints

**Assignments section** (collapsible, open by default):
- Grouped by client
- Each client shows: client name (link), assignment type (Placement/Internal/Client badge), policies list
- For placement contacts: shows each linked policy with type, carrier, status
- For internal team: shows role and assignment
- For client contacts: shows role and notes
- Actions per assignment: "Remove from [client/policy]" link
- "+ Assign to Client" and "+ Assign to Policy" buttons at bottom (use existing picker partial)

**Activity History section** (collapsible):
- All activities where `contact_id` matches or `contact_person` matches this contact's name
- Reverse chronological
- Shows: date, type, disposition, subject (truncated), policy_uid link, duration
- Total hours at bottom
- COR tags displayed where threaded
- Paginated or limited to last 50 with "Show all" link

**Active Follow-Ups section** (collapsible, open if pending):
- Pending follow-ups involving this contact
- Same row pattern as follow-ups page (disposition pills, snooze, etc.)
- Shows COR tag if threaded

### Query

```python
@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(request: Request, contact_id: int, conn=Depends(get_db)):
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return HTMLResponse("Not found", status_code=404)
    contact = dict(contact)

    # Expertise
    _attach_expertise(conn, [contact])

    # Assignments grouped by client
    policy_assignments = conn.execute("""
        SELECT cpa.*, p.policy_uid, p.policy_type, p.carrier, p.renewal_status,
               c.name AS client_name, c.id AS client_id
        FROM contact_policy_assignments cpa
        JOIN policies p ON cpa.policy_id = p.id
        JOIN clients c ON p.client_id = c.id
        WHERE cpa.contact_id = ? AND p.archived = 0
        ORDER BY c.name, p.policy_type
    """, (contact_id,)).fetchall()

    client_assignments = conn.execute("""
        SELECT cca.*, c.name AS client_name, c.id AS client_id
        FROM contact_client_assignments cca
        JOIN clients c ON cca.client_id = c.id
        WHERE cca.contact_id = ? AND c.archived = 0
        ORDER BY c.name
    """, (contact_id,)).fetchall()

    # Group by client
    assignments = {}  # {client_id: {name, policies: [], team_role, contact_role, ...}}
    for pa in policy_assignments:
        cid = pa["client_id"]
        if cid not in assignments:
            assignments[cid] = {"name": pa["client_name"], "id": cid, "policies": [], "team": None, "contact": None}
        assignments[cid]["policies"].append(dict(pa))
    for ca in client_assignments:
        cid = ca["client_id"]
        if cid not in assignments:
            assignments[cid] = {"name": ca["client_name"], "id": cid, "policies": [], "team": None, "contact": None}
        if ca["contact_type"] == "internal":
            assignments[cid]["team"] = dict(ca)
        else:
            assignments[cid]["contact"] = dict(ca)

    # Activities
    activities = conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.contact_id = ? OR LOWER(TRIM(a.contact_person)) = LOWER(TRIM(?))
        ORDER BY a.activity_date DESC, a.id DESC
        LIMIT 50
    """, (contact_id, contact["name"])).fetchall()

    total_hours = sum(float(a["duration_hours"] or 0) for a in activities)

    # Pending follow-ups
    followups = conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type, p.carrier,
               CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE (a.contact_id = ? OR LOWER(TRIM(a.contact_person)) = LOWER(TRIM(?)))
          AND a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
        ORDER BY a.follow_up_date
    """, (contact_id, contact["name"])).fetchall()

    return templates.TemplateResponse("contacts/detail.html", {
        "request": request,
        "contact": contact,
        "assignments": sorted(assignments.values(), key=lambda a: a["name"]),
        "activities": [dict(a) for a in activities],
        "total_hours": total_hours,
        "followups": [dict(f) for f in followups],
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    })
```

### Template

Create `src/policydb/web/templates/contacts/detail.html` — extends base, renders the four sections described above.

### Navigation

- Contact names throughout the app should link to `/contacts/{contact_id}` where `contact_id` is available
- On the contacts list page, contact names in all four tabs link to the detail page
- "View" link/icon on matrix rows

---

## 3. UI Polish & Features

### Better merge UI

Replace the current text-input merge panel (lines 58-88 of `list.html`) with:

1. User selects two contacts (checkboxes on any tab)
2. "Merge Selected" button appears in bulk action bar
3. Opens a side-by-side comparison panel:
   ```
   ┌─────────────────┬─────────────────┐
   │ John Smith       │ John A. Smith   │
   │ ABC Insurance    │ ABC Ins Group   │
   │ john@abc.com     │ jsmith@abc.com  │
   │ (512) 555-1234   │ —               │
   │ Casualty, D&O    │ Casualty        │
   │ 3 policies       │ 1 policy        │
   │ 2 clients        │ 1 client        │
   ├─────────────────┴─────────────────┤
   │ Keep: ○ Left  ○ Right             │
   │ [Merge] [Cancel]                   │
   └───────────────────────────────────┘
   ```
4. "Keep" radio selects which contact survives — the other's assignments, expertise, and activity references are transferred to the survivor
5. POST to existing `/contacts/merge` endpoint (already handles the merge logic)

### Bulk actions

Add to all four tabs of the contacts list:

**Checkbox column** — first column of each table, with master toggle in header.

**Bulk action bar** (fixed bottom, same pattern as follow-ups bulk bar):
- Appears when 1+ contacts selected
- Actions:
  - "Delete Selected" — with confirmation
  - "Merge Selected" (when exactly 2 selected) — opens comparison panel
  - "Assign to Client" — dropdown to pick client, bulk-assigns selected contacts

### Expandable details indicators

On Policies/Clients columns in matrix rows, add a visual indicator:

```html
<span class="text-xs text-gray-400 cursor-pointer hover:text-marsh">
  {{ count }} {% if count %}▸{% endif %}
</span>
```

The `▸` chevron tells users the cell is expandable.

### Contact email tokens

Add to `src/policydb/email_templates.py`:

In `policy_context()`, add contact fields from the primary placement colleague:
```python
ctx["placement_colleague_name"] = ""
ctx["placement_colleague_email"] = ""
ctx["placement_colleague_phone"] = ""
```

In `CONTEXT_TOKEN_GROUPS` under "Policy", add:
```python
("placement_colleague_name", "Placement Colleague"),
("placement_colleague_email", "Colleague Email"),
("placement_colleague_phone", "Colleague Phone"),
```

---

## 4. Files Affected

### Delete
- `src/policydb/web/templates/contacts/_edit_row.html`
- `src/policydb/web/templates/contacts/_internal_edit_row.html`
- `src/policydb/web/templates/contacts/_client_contact_edit_row.html`

### Create
- `src/policydb/web/templates/contacts/detail.html` — contact detail page

### Modify
- `src/policydb/web/routes/contacts.py` — remove deprecated endpoints, add detail page route, add bulk actions, attach follow-up counts
- `src/policydb/web/templates/contacts/list.html` — unify JS, add checkboxes, improve merge UI, add expandable indicators
- `src/policydb/web/templates/contacts/_placement_matrix_row.html` — add expertise pills, follow-up badge, checkbox, detail link
- `src/policydb/web/templates/contacts/_internal_matrix_row.html` — same
- `src/policydb/web/templates/contacts/_client_matrix_row.html` — same
- `src/policydb/email_templates.py` — add placement colleague tokens

---

## 5. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Contact with no assignments | Detail page shows empty assignments section with "Not assigned to any clients or policies" |
| Contact with no activities | Activity section shows "No activity history" |
| Delete contact from detail page | Confirmation with warning showing assignment count. Unlinks all, then deletes. Redirects to contacts list. |
| Merge when contacts have overlapping assignments | Surviving contact keeps all. Duplicate assignments (same policy/client) are deduplicated. |
| Bulk delete with mixed stores | Each contact's store-specific assignments removed, then contact deleted if orphaned |
| Contact name links when contact_id is NULL | No link — freeform contact_person strings without resolved IDs stay as plain text |
| Detail page for deleted contact | 404 |
| Follow-up badge count | Count of `activity_log` rows where `contact_id = ?` AND `follow_up_done = 0` AND `follow_up_date IS NOT NULL` |
