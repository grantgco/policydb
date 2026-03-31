# Universal Attachments with DevonThink Integration

**Date:** 2026-03-31
**Status:** Design
**Issue:** #11 (closed — file attachments for RFIs)

## Context

PolicyDB needs file attachment support across all record types (policies, clients, activities, RFI bundles, meetings). Storing file blobs in SQLite would cause database bloat. The user has DevonThink as their primary document management system on macOS, which provides stable `x-devonthink-item://` URL scheme for persistent file linking.

This design introduces a universal attachment system where DevonThink is the primary file store (PolicyDB stores only link metadata) with local file upload as a fallback. It also merges the existing KB documents system (`kb_documents`, `kb_attachments`, `kb_record_links`) into the new unified model.

## Goals

- Attach files to any record type without DB bloat
- DevonThink as primary store — paste a link, auto-fetch metadata
- Local file upload fallback for quick/small items
- Merge existing KB documents into the unified system
- Reusable attachment panel UI component

## Data Model

### `attachments` table (replaces `kb_documents`)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | |
| uid | TEXT | UNIQUE NOT NULL | Sequential: `ATT-001`, `ATT-002` |
| title | TEXT | NOT NULL | Display name (auto-fetched from DT or original filename) |
| source | TEXT | NOT NULL DEFAULT 'local' | `devonthink` or `local` |
| dt_uuid | TEXT | | DevonThink item UUID (null for local) |
| dt_url | TEXT | | Full `x-devonthink-item://` URL (null for local) |
| file_path | TEXT | | Local disk path (null for DT-only) |
| filename | TEXT | | Original filename |
| file_size | INTEGER | | Bytes |
| mime_type | TEXT | | Content type |
| category | TEXT | DEFAULT 'General' | Configurable category |
| description | TEXT | DEFAULT '' | User notes |
| tags | TEXT | DEFAULT '[]' | JSON array |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | |

### `record_attachments` table (replaces `kb_attachments` + `kb_record_links`)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | |
| attachment_id | INTEGER | FK → attachments NOT NULL | |
| record_type | TEXT | NOT NULL | `policy`, `client`, `activity`, `rfi_bundle`, `kb_article`, `meeting` |
| record_id | INTEGER | NOT NULL | ID in the target table |
| sort_order | INTEGER | DEFAULT 0 | Display ordering |
| created_at | DATETIME | DEFAULT CURRENT_TIMESTAMP | |

**Indexes:**
- `idx_record_attachments_record` on `(record_type, record_id)` — fast lookup per record
- `idx_record_attachments_attachment` on `(attachment_id)` — fast reverse lookup
- `UNIQUE(attachment_id, record_type, record_id)` — prevent duplicate links

### Relationships

- One attachment can link to many records (e.g., a dec page linked to both the policy and the client)
- One record can have many attachments
- Deleting a link (detach) does not delete the attachment itself
- Deleting an attachment cascades to remove all its links

## DevonThink Integration

### Module: `src/policydb/devonthink.py`

```python
def is_devonthink_available() -> bool:
    """Check if DevonThink 3 is installed on this Mac."""

def parse_dt_link(input_str: str) -> str | None:
    """Extract UUID from x-devonthink-item://UUID or raw UUID string."""

def fetch_item_metadata(uuid: str) -> dict | None:
    """Call AppleScript to get item metadata from DevonThink.
    Returns: {name, type, size, path, filename, uuid, url} or None if unavailable.
    """
```

### AppleScript Call

```applescript
tell application "DEVONthink 3"
    set theRecord to get record with uuid "UUID_HERE"
    set theName to name of theRecord
    set theType to type of theRecord as string
    set theSize to size of theRecord
    set thePath to path of theRecord
    set theFilename to filename of theRecord
    return theName & "||" & theType & "||" & theSize & "||" & thePath & "||" & theFilename
end tell
```

Executed via `subprocess.run(['osascript', '-e', script], capture_output=True, timeout=5)`.

### Graceful Degradation

- If DT is not installed or not running, `fetch_item_metadata()` returns `None`
- The attachment is still created with whatever info was provided (at minimum the UUID/URL)
- A "Refresh from DevonThink" button allows retrying metadata fetch later
- The `x-devonthink-item://` link still works for opening the item regardless of metadata fetch success

## Workflows

### Attach via DevonThink Link

1. User clicks "Attach" on any record
2. Panel shows two options: "Paste DevonThink Link" | "Upload File"
3. User pastes `x-devonthink-item://UUID` (or just the UUID)
4. POST `/api/attachments` with `{source: "devonthink", dt_input: "...", record_type, record_id}`
5. Backend parses UUID, calls `fetch_item_metadata()` to get title/type/size
6. Creates `attachments` row + `record_attachments` link
7. Returns attachment card HTML (HTMX swap into the panel)

### Attach via Local Upload

1. User clicks "Upload File" in the attach panel
2. File input accepts: PDF, DOCX, XLSX, PPTX, images (same as KB today)
3. POST `/api/attachments/upload` with multipart file + `{record_type, record_id}`
4. File saved to `~/.policydb/files/attachments/{uid}_{sanitized_filename}`
5. Creates `attachments` row with `source='local'` + `record_attachments` link
6. Returns attachment card HTML

### Detach

- DELETE `/api/record-attachments/{id}` — removes the link only
- The attachment itself remains (may be linked to other records)
- Orphan cleanup: a periodic check or manual action can find/delete unlinked attachments

### Link Existing Attachment

- A search/combobox in the attach panel lets users find existing attachments by title
- POST `/api/record-attachments` with `{attachment_id, record_type, record_id}`
- This enables reuse — attach the same dec page to the policy AND the client

## Local File Storage

- Directory: `~/.policydb/files/attachments/`
- Filename format: `{uid}_{sanitized_filename}` (e.g., `ATT-042_application_2026.pdf`)
- Max file size: 50 MB (same as KB today)
- Sanitization: reuse `_sanitize_filename()` from `kb.py`
- Download endpoint: `GET /attachments/{uid}/download`

## KB Migration

### What Changes

- `kb_documents` rows → `attachments` table (with `source='local'`, preserve file paths)
- `kb_attachments` rows → `record_attachments` (with `record_type='kb_article'`)
- `kb_record_links` rows → `record_attachments` (mapping `link_type` → `record_type`)
- KB UIDs (`KBD-001`) remap to attachment UIDs (`ATT-001`)
- `kb_articles` table stays unchanged — articles are content, not files

### What Stays

- KB articles remain their own entity with title, body, category, tags
- The article page still shows its attachments via `record_attachments`
- The KB routes in `kb.py` update to query `attachments` + `record_attachments` instead of `kb_documents`

### Migration SQL

Single migration file that:
1. Creates `attachments` and `record_attachments` tables
2. Copies data from `kb_documents` → `attachments`
3. Copies data from `kb_attachments` and `kb_record_links` → `record_attachments`
4. Old tables are left in place (no DROP in SQLite migrations) but no longer queried

## UI: Reusable Attachment Panel

### Partial: `_attachments_panel.html`

Included on any page that supports attachments. Receives `record_type` and `record_id` as template context.

**Display:**
- List of attached files as compact cards
- Each card shows: file icon (by mime type), title, source badge ("DT" navy pill / "Local" gray pill), category tag, file size
- DT items: "Open in DevonThink" link (`x-devonthink-item://` href)
- Local items: "Download" link
- "Detach" button (removes link, not file)

**Attach action:**
- "Attach" button opens an inline panel (HTMX swap)
- Two tabs: "DevonThink" (paste input + fetch button) | "Upload" (file input)
- "Link Existing" combobox to search and attach an already-uploaded file
- Category dropdown on each attachment (editable after attach)

### Where It Appears

| Page | Location | Record Type |
|------|----------|-------------|
| Policy detail | New "Files" section in overview tab | `policy` |
| Client detail | New "Files" section in overview tab | `client` |
| Activity detail / follow-up | Expandable attachment area | `activity` |
| RFI bundle | Within the bundle detail panel | `rfi_bundle` |
| Meeting detail | Within meeting notes section | `meeting` |
| KB article | Replaces current attachment section | `kb_article` |

## Configuration

New config list in `_DEFAULTS` + `EDITABLE_LISTS`:

```python
"attachment_categories": [
    "General", "Dec Page", "Binder", "Certificate",
    "Application", "Endorsement", "Loss Run",
    "Meeting Notes", "Contract", "Correspondence",
    "Proposal", "Report"
]
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/attachments` | Create attachment (DT link) |
| POST | `/api/attachments/upload` | Create attachment (local file) |
| GET | `/api/attachments/{uid}` | Get attachment metadata |
| PATCH | `/api/attachments/{uid}` | Update title, category, description |
| DELETE | `/api/attachments/{uid}` | Delete attachment + all links + local file |
| GET | `/attachments/{uid}/download` | Download local file |
| POST | `/api/record-attachments` | Link existing attachment to record |
| DELETE | `/api/record-attachments/{id}` | Detach (remove link only) |
| GET | `/api/attachments/search?q=...` | Search attachments by title (for "Link Existing") |
| POST | `/api/attachments/{uid}/refresh-dt` | Re-fetch DevonThink metadata |
| GET | `/api/record-attachments?record_type=X&record_id=Y` | List attachments for a record |

## UID Generation

New function `next_attachment_uid(conn)` in `db.py`:
- Pattern: `ATT-001`, `ATT-002`, etc.
- Same sequential pattern as `next_policy_uid()`, `next_kb_doc_uid()`

## Verification Plan

1. **DevonThink linking:** Paste a DT item link → verify metadata auto-fetched → click "Open in DevonThink" → verify item opens in DT
2. **Local upload:** Upload a PDF → verify file saved to disk → download → verify contents match
3. **Multi-record linking:** Attach same file to a policy AND its client → verify shows on both
4. **Detach:** Remove link from one record → verify file still shows on the other
5. **KB migration:** Verify existing KB documents appear in the new system with correct links
6. **Category editing:** Change category on an attachment → verify saves
7. **Search/link existing:** Search for an existing attachment → attach to a new record
8. **Graceful DT failure:** Paste a DT link with DT not running → verify attachment created with partial metadata, "Refresh" button available
9. **All six record types:** Verify attachment panel renders and functions on policy, client, activity, RFI, meeting, and KB article pages
