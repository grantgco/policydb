# Nav Header Reorganization — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Reorganize 13 top-level nav links into 3 semantic dropdown menus, move capture input to a sub-bar, auto-copy inbox UID to clipboard, add "Schedule" quick-path to inbox processing, keyboard shortcuts.

---

## Problem Statement

The nav header has grown to 13 top-level links plus a capture input and search field. On smaller screens the links wrap; on larger screens the header feels cluttered and visually noisy. The inbox capture input competes for space with the search field.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Grouping strategy | By workflow stage (Book / Activity / Tools) | Matches mental model: data, work, utilities |
| Top-level items | Dashboard, Book, Activity, Tools, Inbox | 5 items vs. current 13 |
| Dropdown trigger | Hover to open, mouseleave to close (small delay) | Fastest access, no click needed |
| Active page indicator | Parent menu highlighted with gold underline when child page active | Preserves navigation context |
| Capture bar | Separate slim sub-line below main nav | Gives capture breathing room without crowding nav links |
| Inbox UID auto-copy | `navigator.clipboard.writeText()` on successful capture | User grabs UID to paste into email — remove the manual step |
| Inbox "Schedule" mode | Quick-path: client + date only, type=Task | Deferring an item doesn't need full activity logging |
| Capture shortcut | `.` (period) focuses capture input | Matches `/` for search — quick keyboard access |
| Client tagging on capture | Not included in sub-bar | Kept simple — client is assigned during processing |

---

## 1. Nav Structure

### Top-Level Items (left to right)

```
COVERAGE [logo]  v5.5  |  Dashboard  Book ▾  Activity ▾  Tools ▾  Inbox (3)  |  [Search...]
```

### Dropdown Contents

**Book** — your data
- Clients (`/clients`)
- Renewals (`/renewals`)
- Contacts (`/contacts`)

**Activity** — your work
- Follow-Ups (`/followups`)
- Activity Log (`/activities`)
- Meetings (`/meetings`)
- Review (`/review`)

**Tools** — utilities
- Briefing (`/briefing`)
- Reconcile (`/reconcile`)
- Templates (`/templates`)
- Settings (`/settings`)

### Active Page Highlighting

Each dropdown parent maps to a set of `active` values. When the current page's `active` variable matches any child, the parent gets `bg-marsh-light` plus a gold bottom border (`border-b-2 border-[#c8a96e]`).

| Parent | Highlights when `active` is |
|--------|---------------------------|
| Dashboard | `dashboard` |
| Book | `clients`, `renewals`, `contacts` |
| Activity | `followups`, `activities`, `meetings`, `review` |
| Tools | `briefing`, `reconcile`, `templates`, `settings` |
| Inbox | `inbox` |

---

## 2. Dropdown Behavior

Pure CSS/JS hover dropdowns:

- **Open:** `mouseenter` on parent link shows the dropdown panel. Click also opens (for trackpad users).
- **Close:** `mouseleave` on the parent+dropdown container with a 150ms delay (prevents accidental close when moving to dropdown items)
- **Styling:** `bg-marsh` dropdown panel with `bg-marsh-light` hover on items, matching the nav theme
- **Position:** Absolute, left-aligned below the parent link
- **Z-index:** Above page content (z-50)
- **Keyboard:** Tab through top-level items. Enter/Space opens dropdown. Arrow keys navigate items within an open dropdown. Escape closes dropdown and returns focus to the parent item.

No click required to open. Click on a dropdown item navigates to that page.

---

## 3. Capture Sub-Bar

A slim bar below the main nav, always visible on every page:

```html
<div class="bg-marsh/80 border-b border-marsh-light">
  <div class="max-w-[1600px] mx-auto px-4 flex items-center gap-3 py-1">
    <span class="text-white/35 text-[11px]">Capture:</span>
    <form hx-post="/inbox/capture" hx-swap="none" ...>
      <input id="capture-input" name="content" placeholder="Type a note, press Enter... (press . to focus)" ...>
    </form>
  </div>
</div>
```

- Height: ~28px (compact)
- Same width constraints as main nav (`max-w-[1600px]`)
- Input spans most of the available width
- No client picker in the sub-bar — client is assigned during processing on /inbox
- On successful capture, the INB-{id} UID is auto-copied to clipboard and toast confirms with updated text

### Auto-Copy Implementation

The capture form handler:
1. Resets the form on success
2. Extracts `INB-{id}` from the `HX-Trigger` header via regex (the header contains JSON like `{"activityLogged": "Captured INB-42 ..."}` — the regex `INB-\d+` matches within the raw string)
3. Copies the UID to clipboard via `navigator.clipboard.writeText()`
4. The toast (fired by the existing `activityLogged` event listener) shows the server message

The server response text in `inbox_capture()` should be updated from `"Captured INB-42 - paste into email"` to `"Captured INB-42 - copied to clipboard"` to reflect the auto-copy behavior.

### Keyboard Shortcut

`.` (period) focuses the capture input, mirroring how `/` focuses search.

```javascript
document.addEventListener('keydown', function(e) {
  if (e.key === '.' && !e.target.closest('input,textarea,[contenteditable]')) {
    e.preventDefault();
    document.getElementById('capture-input').focus();
  }
});
```

The guard (`!e.target.closest(...)`) prevents activation when typing in any input field.

---

## 4. Inbox "Schedule" Quick-Path

When processing an inbox item, two modes:

### Full Process (existing)
Expands inline form with client, policy, activity type, subject, details, follow-up date, COR toggle, duration. Creates a full activity log entry.

### Schedule (new)
A streamlined button next to "Process" that shows a minimal inline form:
- Client picker (required) — uses `all_clients` already in the inbox page template context
- Follow-up date (required)
- Subject (pre-filled from capture content)

Creates an activity with `activity_type='Task'`, the subject, `follow_up_date`, and marks the inbox item as processed. No policy, no details, no COR, no duration.

### UI

```
┌──────────────────────────────────────────────────────┐
│ INB-42 · Got response from John on GL renewal         │
│ Acme Corp · 2 hours ago                               │
│                              [Process] [Schedule] [Dismiss] │
└──────────────────────────────────────────────────────┘
```

When "Schedule" is clicked, a compact inline form appears:

```
┌──────────────────────────────────────────────────────┐
│ Client: [Acme Corp ▾]  Follow-up: [2026-03-24]       │
│ Subject: [Got response from John on GL renewal___]    │
│                              [Schedule Task] [Cancel] │
└──────────────────────────────────────────────────────┘
```

### Endpoint

`POST /inbox/{inbox_id}/schedule` — creates a Task activity with minimal fields.

The form uses `hx-post="/inbox/{id}/schedule"` with `hx-swap="delete"` and `hx-target="#inbox-item-{id}"` to remove the parent inbox item card on success. The response returns `HTMLResponse("")` with an `HX-Trigger` header for the toast, same pattern as dismiss.

```python
@router.post("/inbox/{inbox_id}/schedule", response_class=HTMLResponse)
def inbox_schedule(inbox_id: int, client_id: int = Form(...),
                   follow_up_date: str = Form(...),
                   subject: str = Form(""), conn=Depends(get_db)):
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

The schedule form is pre-rendered inside each inbox row in `inbox.html` (hidden by default). Since `all_clients` is already passed to the template context by the `/inbox` GET handler, no additional endpoint is needed to provide the client list.

---

## 5. Search Field

Stays in the main nav bar, right-aligned. No changes to search behavior. Existing `/` shortcut continues to work.

---

## 6. HTMX Progress Bar

The progress bar (`#htmx-progress`) currently positions at `top: 56px` (nav height). With the capture sub-bar adding ~28px, update to `top-[84px]` in the Tailwind class. Since both the nav and capture bar have fixed heights, a static value is reliable.

---

## 7. Print Safety

All nav elements (dropdowns, capture bar) already inherit `@media print { nav { display: none; } }` from the existing print styles. The capture sub-bar should be placed inside the `<nav>` element or given its own `no-print` class to ensure it's hidden in print output.

---

## 8. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Hover dropdown, mouse moves to dropdown items | 150ms delay prevents premature close |
| Click dropdown parent (not just hover) | Also opens dropdown (for trackpad users) |
| Active page is a child of a dropdown | Parent gets highlight, dropdown does NOT auto-open |
| Keyboard: Tab | Cycles through top-level items |
| Keyboard: Enter/Space on dropdown parent | Opens dropdown |
| Keyboard: Arrow keys in open dropdown | Navigate items within dropdown |
| Keyboard: Escape in open dropdown | Closes dropdown, returns focus to parent |
| Keyboard: `.` (period) | Focuses capture input (unless already in an input/textarea/contenteditable) |
| Keyboard: `/` (slash) | Focuses search input (existing behavior, unchanged) |
| Mobile/narrow screen | Not a concern — this is a desktop-only local app |
| Schedule without client tagged | Client picker is required in the schedule form |
| Schedule without follow-up date | Date is required — this is the whole point of scheduling |
| Capture with no text | Rejected — content is required (HTML `required` attribute) |
