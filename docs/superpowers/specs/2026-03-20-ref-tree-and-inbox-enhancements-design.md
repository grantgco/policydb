# Ref Tree Lookup, COR Auto-Default & Inbox Contact Tagging — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Ref tree lookup page + search integration, COR toggle auto-default from config, inbox `@` contact tagging with autocomplete.

---

## Problem Statement

When working with correspondence, activities, and inbox items, multiple UIDs can be associated with a single thread of work (CN number, policy UID, COR tag, RFI UID, INB tag). There is no way to see all related references from a single starting point. Additionally, the COR toggle is always manual with no smart defaults, and inbox items lack the ability to tag a contact person during capture.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Ref tree trigger | Dedicated page + search integration | User starts from a UID with no page context |
| Tree display | Hierarchical tree with copyable UID pills | Shows relationship chain, enables email search |
| UID types supported | CN, policy UID, COR, RFI, INB, A-{id}, full ref tags | Any UID the user might have |
| COR auto-default | Config-driven list of activity types/dispositions | User controls what triggers it, can always uncheck |
| Inbox contact tagging | `@` autocomplete in capture input | Quick inline tagging without leaving the flow |
| Contact search scope | All contacts, filtered as you type | Simple, no need to segment |
| Contact carry-through | Pre-fills contact_person when processing | Tagged context flows into the activity |

---

## 1. Ref Tree Lookup

### Core Function

`resolve_ref_tree(conn, uid_string) -> dict`

A utility function in `src/policydb/queries.py` that:

1. **Parses the UID** — regex detection of type:
   - `CN{digits}` or `C{digits}` → client
   - `POL-{year}-{seq}` or `POL{year}{seq}` → policy
   - `COR-{digits}` → correspondence thread
   - `INB-{digits}` → inbox item
   - `A-{digits}` → activity
   - `CN{digits}-RFI{digits}` → RFI bundle (lookup by full composite `rfi_uid` column: `WHERE rfi_uid = ?`)
   - Full ref tags (e.g., `CN122333627-POL20250441-COR7`) → split and resolve deepest segment

2. **Finds the anchor entity** in the DB

3. **Walks up** to the client (root of tree)

4. **Walks down** to collect all children:
   - Client → policies, RFI bundles, inbox items (processed with client_id)
   - Policy → activities (grouped by COR thread if applicable)
   - Activity → COR thread siblings, linked inbox source
   - COR thread → all activities in the thread
   - RFI → linked activities (via "Send RFI:" subject pattern)
   - INB → linked activity (if processed)

5. **Returns nested dict:**

```python
{
    "client": {"id": 5, "name": "Acme Corp", "cn_number": "122333627", "uid": "CN122333627"},
    "policies": [
        {
            "uid": "POL-2025-0441", "type": "General Liability", "carrier": "Zurich",
            "threads": [
                {
                    "thread_id": 112, "uid": "COR-112", "activity_count": 2,
                    "activities": [
                        {"id": 112, "uid": "A-112", "subject": "Called John re GL", "date": "2026-03-24"},
                        {"id": 115, "uid": "A-115", "subject": "John confirmed terms", "date": "2026-03-26"},
                    ]
                }
            ],
            "standalone_activities": [...]
        }
    ],
    "rfis": [
        {"uid": "CN122333627-RFI01", "title": "Renewal Info", "status": "sent", "item_count": 8, "received_count": 3}
    ],
    "inbox_items": [
        {"uid": "INB-42", "content": "John responded about GL", "status": "processed", "activity_id": 108}
    ],
    "highlight": "COR-112"  # the UID that was searched, for visual emphasis
}
```

### Dedicated Page

**Route:** `GET /ref-lookup` — under Tools dropdown in nav. Uses `active = "ref-lookup"`. Add `"ref-lookup"` to the Tools dropdown active highlight condition in `base.html`.

**Template:** `src/policydb/web/templates/ref_lookup.html`

**UI:**
- Single input field, auto-focused, placeholder "Paste any UID..."
- Submit via Enter or button → `GET /ref-lookup?q={uid}`
- Results render as an indented tree:

```
CN122333627 (Acme Corp)                              [copy]
  ├─ POL-2025-0441 (General Liability - Zurich)      [copy] [open]
  │    └─ COR-112 (2 activities)                     [copy]
  │         ├─ A-112: "Called John re GL" — 3/24      [copy]
  │         └─ A-115: "John confirmed terms" — 3/26   [copy]
  ├─ CN122333627-RFI01 (Renewal Info — 3/8 received) [copy]
  └─ INB-42 → A-108 (processed)                      [copy]
```

- Each UID is a copyable blue pill (click to copy, same pattern as existing ref tag pills)
- Policy and client nodes have `[open]` links to their detail pages
- The searched UID node is highlighted (yellow background)
- "No results" message if UID not found

### Search Integration

In `src/policydb/web/routes/dashboard.py` search handler, when a UID pattern is detected (any of: CN, POL, COR, INB, RFI, A-{id}):

- Show a banner at the top of search results: "This looks like a reference tag — [View full ref tree →](/ref-lookup?q={uid})"
- Normal search results still show below

This is a lightweight link, not a full tree render in search results.

---

## 2. COR Auto-Default

### Config

New config key `cor_auto_triggers` — a list of strings (activity types and/or disposition labels) that cause the COR toggle to default to checked when logging an activity.

Add to `_DEFAULTS` in `src/policydb/config.py`:
```python
"cor_auto_triggers": ["Email", "Left VM", "Sent Email", "Awaiting Response"],
```

Add to `EDITABLE_LISTS` in `src/policydb/web/routes/settings.py` so it appears as a manageable list card on `/settings`.

### Settings UI

Standard list card in `/settings` (same pattern as existing list cards for `activity_types`, etc.):

- Title: "COR Auto-Triggers"
- Description: "Activity types and dispositions that auto-check the COR toggle"
- Free-text add/remove (user types matching activity type or disposition label strings)

### Behavior

Forms that have a COR toggle:
- Follow-ups page: re-diary form (`followups/_row.html`)
- Policy edit: quick log form (`policies/edit.html`)
- Inbox: process form (`inbox.html`)

When the user selects an activity type or disposition on any of these forms:

- JS checks the selected value against the `cor_auto_triggers` list
- If matched, COR toggle is auto-checked
- User can always uncheck manually
- The trigger list is passed to templates via config: `cfg.get("cor_auto_triggers", [])`

**Important:** `follow_up_dispositions` config entries are objects (`{"label": "Left VM", "default_days": 3}`), not plain strings. The JS comparison must use the **disposition label string** (the text the user sees/selects), not the config object.

### Implementation

Pass `cor_auto_triggers` as a Jinja2 global or template variable. In the form JS:

```javascript
var corTriggers = {{ cor_auto_triggers | tojson }};
// On activity type pill click or disposition select:
// selectedType = the activity type string (e.g., "Email")
// selectedDisposition = the disposition label string (e.g., "Left VM")
if (corTriggers.includes(selectedType) || corTriggers.includes(selectedDisposition)) {
  corToggle.checked = true;
}
```

The auto-check only fires on user interaction (changing type/disposition), not on page load, to avoid overriding manual unchecks.

---

## 3. Inbox Contact Tagging

### Schema

**Migration:** `src/policydb/migrations/065_inbox_contact_id.sql`

```sql
ALTER TABLE inbox ADD COLUMN contact_id INTEGER REFERENCES contacts(id);
```

Register migration 065 in `src/policydb/db.py` (`_KNOWN_MIGRATIONS` set + if-block).

### `@` Autocomplete in Capture Input

When the user types `@` in the capture input (both nav sub-bar and inbox page inline input):

1. A dropdown appears below the input
2. Searches the `contacts` table as the user types after `@` — queries `name LIKE ?` across all contacts
3. Shows matching names with their organization in the dropdown
4. Selecting a contact:
   - Sets a hidden `contact_id` field on the form
   - Replaces the `@partial` text with the full name (keeps it readable)
5. If no match selected (user just types `@John` without picking), captured as plain text with no contact_id

### Endpoint Updates

`POST /inbox/capture` — add optional `contact_id` form parameter (default 0), save to inbox row. Both capture forms (nav sub-bar and inbox page) need a hidden `<input type="hidden" name="contact_id" value="0">` field that the `@` autocomplete JS sets when a contact is selected.

### Contact Autocomplete Endpoint

`GET /inbox/contacts/search?q={query}` — returns JSON list of matching contacts:

```python
@router.get("/inbox/contacts/search")
def inbox_contact_search(q: str = "", conn=Depends(get_db)):
    if len(q) < 2:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT id, name, organization FROM contacts
        WHERE name LIKE ? ORDER BY name LIMIT 15
    """, (f"%{q}%",)).fetchall()
    return JSONResponse([{"id": r["id"], "name": r["name"], "org": r["organization"] or ""} for r in rows])
```

### Processing Carry-Through

When processing or scheduling an inbox item that has a `contact_id`:

- The process form pre-selects the contact in the contact_person field
- The schedule endpoint includes the contact info in the created activity
- The inbox page shows the tagged contact name next to the item content

### JS Implementation

The `@` autocomplete attaches to any input with `data-at-complete="true"`:

```javascript
// On keyup in the input, detect @ and show dropdown
// Fetch /inbox/contacts/search?q={text after @}
// On select, set hidden field and replace @text with name
// On blur or Enter without selection, ignore the @
```

This uses the same dropdown styling as the existing combobox pattern (`.matrix-combo-dropdown`).

---

## 4. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Ref lookup: UID not found | "No matching reference found" message |
| Ref lookup: CN number matches multiple (shouldn't happen) | Show first match |
| Ref lookup: Full ref tag pasted | Parse deepest segment, resolve from there |
| Ref lookup: Activity has no client (orphaned) | Show activity standalone, no tree |
| COR auto-trigger: user unchecks, then changes type to another trigger | Re-checks COR (user can uncheck again) |
| COR auto-trigger: type is NOT a trigger, but disposition IS | COR still auto-checks (either match triggers) |
| `@` autocomplete: no matches | Dropdown shows "No matches" and closes |
| `@` autocomplete: user types `@` then backspaces | Dropdown closes, no contact tagged |
| `@` autocomplete: multiple `@` mentions | Only the first `@` triggers autocomplete (keep it simple) |
| Inbox item with contact, then dismissed | Contact info preserved on the inbox row but no activity created |
| Capture from nav bar vs inbox page | Both support `@` autocomplete with same behavior |
