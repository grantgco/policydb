# Inbox Capture Queue — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Quick capture input in nav header, inbox table for pending items, process-to-activity flow, INB-{id} UIDs, search integration, dashboard badge.

---

## Problem Statement

When clearing email, the user encounters items that need to be tracked — responses to RFIs, follow-up reminders, underwriter communications — but navigating to the specific client/policy page to log each one breaks the flow. Items get lost in the inbox or require mental juggling to remember.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Capture location | Nav header input (always visible) | Zero-click discovery, never leaves current page |
| Capture fields | Text + optional client | Fast capture with optional context. Details filled during processing |
| Processing outcome | Convert to activity OR dismiss | Not everything needs tracking. Dismiss clears the queue. |
| Inbox UID | `INB-{id}` assigned immediately | User pastes into email before archiving. Searchable later. |
| Inbox page | Dedicated /inbox with inline process forms | Batch processing of queued items in one place |
| Integration | Search + dashboard badge | INB- searchable like COR-. Badge shows pending count. |

---

## 1. Schema

### New table: `inbox`

**Migration file:** `src/policydb/migrations/064_inbox.sql`

```sql
CREATE TABLE IF NOT EXISTS inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_uid    TEXT NOT NULL UNIQUE,
    content      TEXT NOT NULL,
    client_id    INTEGER REFERENCES clients(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    activity_id  INTEGER REFERENCES activity_log(id),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);
```

**UID format:** `INB-{id}` — assigned after INSERT using `last_insert_rowid()`.

**Status values:** `pending` (new, unprocessed) → `processed` (converted to activity or dismissed).

---

## 2. Quick Capture — Nav Header

### Location

In `base.html` nav bar, between the navigation links and the search icon. Always visible on every page.

### UI

```html
<form hx-post="/inbox/capture" hx-swap="none" class="flex items-center gap-1">
  <input type="text" name="content" placeholder="📥 Quick capture..." required
    class="bg-white/10 text-white placeholder-white/40 text-sm rounded px-3 py-1 w-64
           border border-white/20 focus:border-white/50 focus:outline-none">
  <input type="hidden" name="client_id" id="capture-client-id" value="">
</form>
```

### Behavior

1. User types a quick note (email subject, reminder, whatever)
2. Optionally types `@ClientName` to tag a client — JS detects `@` and shows a client picker dropdown
3. Press Enter → `POST /inbox/capture`
4. Server creates inbox row, generates `INB-{id}`
5. Response fires toast: "Captured INB-42" with the UID shown prominently for copy
6. Input clears, stays on current page

### Client picker (`@` trigger)

When user types `@` in the capture input:
- Show a small dropdown below the input with client name suggestions
- Filter as they type after `@`
- Selecting a client sets `capture-client-id` hidden input and replaces the `@name` text with a visual tag
- Press Enter without selecting just captures the raw text (no client tagged)

### Endpoint

```python
@router.post("/inbox/capture")
def inbox_capture(request: Request, content: str = Form(...), client_id: int = Form(0), conn=Depends(get_db)):
    conn.execute(
        "INSERT INTO inbox (content, client_id, inbox_uid) VALUES (?, ?, '')",
        (content.strip(), client_id or None),
    )
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    uid = f"INB-{row_id}"
    conn.execute("UPDATE inbox SET inbox_uid = ? WHERE id = ?", (uid, row_id))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - paste into email"}'
    })
```

---

## 3. Inbox Page (`/inbox`)

### Route

`GET /inbox` — shows all pending items, optionally toggle to show processed.

### Layout

Each pending item shows:
- `INB-{id}` as a copyable blue pill (same pattern as COR tags)
- Content text
- Client name (if tagged) with link, or "(no client)" in gray
- Relative time ("2 hours ago", "yesterday")
- "Process →" button — expands inline form
- "Dismiss" button — marks as processed with no activity

### Process form (expands inline)

When "Process →" is clicked, an inline form expands below the item:

```
┌──────────────────────────────────────────────────────────┐
│ INB-42 · Got response from John on Acme GL renewal       │
│                                                          │
│ Client: [Acme Corp ▾]  Policy: [GL-2025-0441 ▾]        │
│ Type: [Call] [Email] [Meeting] [Note] ...  (pills)       │
│ Subject: [Got response from John on Acme GL renewal____] │
│ Details: [________________________________________]       │
│ Follow-Up: [____]  ☐ COR  Duration: [__]                │
│ Disposition: [Left VM] [Sent Email] [Connected] ...      │
│                                                          │
│ [Log Activity]  [Cancel]                                 │
└──────────────────────────────────────────────────────────┘
```

- Client pre-filled if tagged at capture
- Policy picker filtered by selected client
- Subject pre-filled from capture content
- All other fields from the standard activity log form
- "Log Activity" → creates activity via existing `POST /activities/log`, marks inbox item as processed, links `activity_id`
- COR thread: if checked, the activity gets `thread_id = own id` (same as the COR toggle on other log forms)

### Dismiss

"Dismiss" → `POST /inbox/{id}/dismiss` → sets `status = 'processed'`, `processed_at = now()`, no `activity_id`. Toast confirms.

### Processed items

Toggle "Show processed" at top → shows processed items with what they became:
- "→ A-87 on Acme Corp" (linked to activity)
- "Dismissed" (no activity)

---

## 4. Search Integration

In the search handler (`dashboard.py`), add `INB-{id}` pattern matching:

```python
inb_match = re.match(r'^INB-(\d+)$', q.strip(), re.IGNORECASE)
if inb_match:
    inbox_id = int(inb_match.group(1))
    item = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    # Return inbox item details + linked activity if processed
```

Also extend the general search to include inbox content:
```sql
SELECT 'inbox' AS source, id, content AS subject, created_at, status
FROM inbox WHERE content LIKE ? LIMIT 10
```

---

## 5. Dashboard Integration

On the dashboard, next to the Follow-Ups section header or as a separate small card:

```html
{% if inbox_pending_count %}
<a href="/inbox" class="text-xs bg-indigo-100 text-indigo-700 font-bold px-2 py-0.5 rounded-full">
  📥 {{ inbox_pending_count }} pending
</a>
{% endif %}
```

Shows only when there are pending items. Links to `/inbox`.

---

## 6. Nav Link

Add "Inbox" to the nav bar links (between Follow-Ups and the capture input):

```html
<a href="/inbox" class="nav-link {% if active == 'inbox' %}bg-marsh-light{% endif %}">
  Inbox
  {% if inbox_pending_count %}<span class="bg-white/20 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-1">{{ inbox_pending_count }}</span>{% endif %}
</a>
```

Badge shows pending count inline.

---

## 7. `build_ref_tag` Integration

Extend `build_ref_tag()` in `utils.py` to accept an optional `inbox_id` parameter:

```python
def build_ref_tag(..., inbox_id: int = 0) -> str:
    ...
    if inbox_id:
        tag += f"-INB{inbox_id}"
    ...
```

When an inbox item is processed into an activity, the activity's ref tag can include the INB origin for traceability.

---

## 8. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Capture with empty text | Rejected — content is required |
| Capture with `@` but no client match | Captured as-is, no client tagged |
| Process item — client not tagged | Client picker required during processing (can't log activity without client) |
| Process item — same client multiple policies | Policy picker shows all non-archived policies for selected client |
| Dismiss then want to undo | Show processed items, no undo — but item is still there for reference |
| Search INB-999 (doesn't exist) | "No inbox item found" message |
| Server restart | Pending items persist in DB — nothing lost |
| Multiple rapid captures | Each gets unique INB-{id}, all show in toast, all land in queue |
| Process converts to activity with COR | Activity gets `thread_id`, future re-diary threads normally |
| 100+ pending items | Inbox page paginates or shows newest first with "Show all" |
