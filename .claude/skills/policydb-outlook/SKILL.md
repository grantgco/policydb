---
name: policydb-outlook
description: >
  Outlook AppleScript integration for PolicyDB — compose drafts, email sweep/sync,
  ref tag matching, category filtering, and inbox triage. Use when working on email
  compose flow, Outlook sync, ref tag resolution, email_sync.py, outlook.py, or
  the Sync Outlook button. Also trigger when user mentions AppleScript, Outlook
  categories, email import, or sent/received email scanning.
---

# Outlook AppleScript Integration

Two-way integration with Legacy Outlook for Mac via `osascript` subprocess calls.

## Architecture

| File | Purpose |
|------|---------|
| `src/policydb/outlook.py` | AppleScript bridge — `create_draft()`, `search_emails()`, `search_all_folders()`, `get_flagged_emails()`, `is_outlook_available()` |
| `src/policydb/email_sync.py` | Sweep orchestrator — ref tag extraction, resolution, activity creation/enrichment, inbox routing |
| `src/policydb/web/routes/outlook_routes.py` | Routes: `POST /outlook/compose`, `POST /outlook/sync`, `GET /outlook/status` |
| `src/policydb/web/templates/outlook/_sync_results.html` | Sync results banner (fixed bottom of viewport) |
| `src/policydb/email_templates.py` | `markdown_to_html()`, `wrap_email_html()`, `issue_context()` |

## Compose Flow

**Plain quick email (default):** Body text + `[PDB:ref_tag]` appended. No HTML wrapping.

**Formal email format (checkbox):** Marsh-branded HTML shell via `wrap_email_html()` — navy header, Noto fonts, structured layout. Coupled with policy table — checking either triggers HTML shell (see `policydb-compose` skill for details).

**Include policy table (checkbox):** Inserts formatted policy schedule. Scoping:
- From a **single policy**: only that policy's row
- From a **project or issue**: all relevant policies

**Fallback:** If Outlook unavailable, falls back to `mailto:` link with toast warning.

### Compose Request Fields

```python
class ComposeRequest(BaseModel):
    to: str, cc: list[str], subject: str, body: str,
    policy_uid: str, client_id: int, issue_uid: str, project_name: str,
    include_policy_table: bool = False,
    formal_format: bool = False
```

## Sweep / Sync Engine

**Trigger:** Manual "Sync Outlook" button in Action Center sidebar. Shows fixed bottom banner with spinner while running, results when complete.

### Scan Rules

| Source | Rule | Category Control |
|--------|------|-----------------|
| **Sent Items** | All captured by default | Skip if has config `outlook_skip_category` (default: "Personal") |
| **All other folders** | Only if has `outlook_capture_category` (default: "PDB") OR `[PDB:]` ref tag in content | Opt-in via category |
| **Flagged (all folders)** | Always captured | N/A |

### Matching: Ref Tag Only (No Fuzzy)

1. Extract `[PDB:xxx]` tags via regex from subject + plain text body
2. Try structured parsing (`_parse_ref_tag`) for compound tags like `CN123-L7-POL042`
3. If parsing returns nothing, **direct DB lookup** of raw tag against `issue_uid`, `policy_uid`, `cn_number`
4. If compound tag partially resolves, try each dash-separated segment as direct lookup
5. **No fuzzy matching** — unmatched emails go straight to Inbox for manual triage

### Resolution Priority (Most Specific Wins)

```
issue > policy > CN number
```

Each more-specific record's `client_id`/`policy_id` overwrites less-specific values. A policy's client always wins over a CN number lookup.

### Activity Creation

| Scenario | Action |
|----------|--------|
| Ref tag match + existing same-day Email activity on same policy | Enrich: add `outlook_message_id`, `email_snippet` |
| Ref tag match + no existing activity | Create: `activity_type='Email'`, `source='outlook_sync'` |
| Sent, no match | Route to Inbox as `[Outlook Sent]` item |
| Received/flagged, no match | Route to Inbox as `[Outlook Flagged]` / `[Outlook Received]` item |

### Dedup

Every imported email stores `outlook_message_id` on both `activity_log` and `inbox` tables. Checked before any creation — duplicates are counted as "skipped".

## Config Keys

| Key | Default | Purpose |
|-----|---------|---------|
| `last_outlook_sync` | null | ISO timestamp, cleared by Reset button in Settings |
| `outlook_sync_lookback_days` | 7 | First-sync lookback window |
| `outlook_capture_category` | "PDB" | Outlook category for opt-in on received emails |
| `outlook_skip_category` | "Personal" | Outlook category to exclude sent emails |
| `outlook_email_shell_header` | true | Navy header bar in formal HTML emails |

All editable in Settings > Database & Admin > Outlook Integration. "Reset" button clears `last_outlook_sync` so next sync uses lookback window (safe — dedup prevents duplicates).

## AppleScript Gotchas

1. **`sender` property fails on sent messages** — wrap in `try/end try`, fall back to empty string
2. **`content` returns HTML** — use `plain text content of msg` for ref tag matching, fall back to `content` if plain text fails
3. **Body snippet truncation** — fetch 2000 chars from Outlook for matching, store only 500 chars (stripped of HTML) in `email_snippet`
4. **Flagged emails** — `todo flag of it is not not flagged` is the correct AppleScript syntax; `is flagged` does NOT work
5. **Categories** — `categories of msg` returns a list of category objects; iterate and get `name of c`
6. **Folder scanning** — `messages of inbox` only gets Inbox; use `every mail folder of default account` and iterate for cross-folder scans. Skip "Deleted Items", "Junk Email", "Drafts", "Trash", "Clutter", "Sent Items" (sent scanned separately)
7. **`mail folder of msg`** — NOT a valid property; track folder name during iteration instead
8. **JSON output** — AppleScript has no native JSON; build JSON strings manually with escaping helpers (`escJSON`, `padNum`, `replaceText`)
9. **Timeout** — 30s per `osascript` call; cap at 500 messages per folder scan

## Email Snippet Display

Imported email content is shown via expandable `<details>` elements in:
- Action Center activities table (expand arrow per row)
- Policy correspondence timeline
- Issue detail activity timeline
- Inbox items (structured header + "show full content" toggle)

Activities from sync show a "synced" badge (`source='outlook_sync'`).

## Reassignment

If an email auto-links to the wrong client/policy, the expandable detail row in the Activities table has Client and Policy dropdowns for reassignment. Changing client reloads the policy dropdown. Uses `PATCH /activities/{id}/field` with `client_id` or `policy_id`.

## Migration 122

```sql
ALTER TABLE activity_log ADD COLUMN outlook_message_id TEXT;
ALTER TABLE activity_log ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE activity_log ADD COLUMN email_snippet TEXT;
CREATE INDEX idx_activity_outlook_msgid ON activity_log(outlook_message_id) WHERE outlook_message_id IS NOT NULL;
```

Inbox table also has `outlook_message_id`, `email_subject`, `email_date` columns (added in same session).
