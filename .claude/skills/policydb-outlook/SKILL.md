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
| `src/policydb/outlook_contacts.py` | AppleScript bridge for contacts — `ensure_pdb_category()`, `list_pdb_contacts()`, `upsert_contact()`, `delete_contact()`, `split_name()` |
| `src/policydb/contact_sync.py` | Contact push orchestrator — PolicyDB → Outlook, fenced by PDB category, one-way |
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

> **Empty capture category is a hard error.** If `outlook_capture_category` is
> blank, `sync_outlook` refuses to invoke `search_all_folders` (which would
> otherwise ingest *every* message in *every* folder) and adds a warning to
> the sync results banner. Sent + Flagged scans still proceed.

### Resume window

`last_outlook_sync` is captured **at the moment the sweep starts** (minus a 60s
safety overlap), then written to config when the sweep completes. Storing the
end-of-sweep timestamp would silently drop any email that arrived during the
scan window. The 60s overlap is absorbed by the `outlook_message_id` dedup.

### Matching: Ref Tag + Thread Inheritance

**Tier 1 — Ref tag matching:**
1. Extract `[PDB:xxx]` tags via regex from subject + plain text body
2. Try structured parsing (`_parse_ref_tag`) for compound tags like `CN123-L7-POL042`
3. If parsing returns nothing, **direct DB lookup** of raw tag against `issue_uid`, `policy_uid`, `cn_number`
4. If compound tag partially resolves, try each dash-separated segment as direct lookup

**Tier 2 — Domain matching:**
5. If no ref tag, match sender/recipient email domains against client websites and contact emails
6. Skip domains in `freemail_domains` and `internal_email_domains` config (marsh.com, etc.)
7. **Archived clients are excluded** from the domain index — `_build_domain_index` filters `archived = 0`
8. Requires unique client match — ambiguous (multiple clients) returns None

**Tier 3 — Thread inheritance (post-sync pass):**
9. After all emails processed, `_run_thread_inheritance()` propagates Tier 1 matches to unmatched inbox items in the same thread
10. Thread = exact normalized subject match + same client via domain matching
11. Only Tier 1 matches propagate (must have policy_id, issue_id, or program_id)
12. Promoted items get `source='thread_inherit'`, `follow_up_done=1`
13. Unmatched emails with no ref tag or thread match go to Inbox for manual triage
14. **Historical scan is bounded to 90 days** (matches the matched-rows window) so the inbox doesn't slow every sync as items pile up

### Performance: domain index

`_build_domain_index(conn)` is called **once** at the top of `sync_outlook` and
returns `{domain: {client_id, ...}}` covering both client websites and contact
email domains for non-archived clients. The map is threaded into every
`_match_by_domain` and `_run_thread_inheritance` call so a 500-email sweep does
exactly one client/contact scan instead of N+1 queries per email.

### Resolution Priority (Most Specific Wins)

```
issue > policy > CN number
```

Each more-specific record's `client_id`/`policy_id` overwrites less-specific values. A policy's client always wins over a CN number lookup.

### Activity Creation

| Scenario | Action |
|----------|--------|
| Ref tag match + existing same-day Email activity on same policy | Enrich: add `outlook_message_id`, `email_snippet`, `email_direction` |
| Ref tag match + no existing activity | Create: `activity_type='Email'`, `source='outlook_sync'`, `email_direction` set |
| Thread inheritance match (post-sync) | Create: `activity_type='Email'`, `source='thread_inherit'`, delete from inbox |
| Sent, no match | Route to Inbox as `[Outlook Sent]` item, `email_direction='sent'` |
| Received/flagged, no match | Route to Inbox as `[Outlook Flagged]` / `[Outlook Received]` item |

### Direction tracking (`email_direction` column)

Migration **144** added `email_direction` (`'sent' | 'received' | 'flagged'`)
to both `activity_log` and `inbox`. The pre-migration "Received: " subject
prefix munging is gone — direction is read from the column. The migration
backfills existing rows from the legacy prefix and the inbox `[Outlook Sent|
Received|Flagged]` brackets, then strips the legacy prefix from
`activity_log.subject`. `_normalize_subject` still tolerates a stray
"Received: " for safety.

### Dedup

Every imported email stores `outlook_message_id` on both `activity_log` and `inbox` tables. Checked before any creation — duplicates are counted as "skipped".

When the user dismisses an Outlook-sourced activity OR an Outlook-sourced
inbox row (single or bulk), the message_id is recorded in
`dismissed_outlook_messages` so the next sync sweep won't re-import it.

### Bulk dismiss (Inbox tab)

The Action Center inbox tab exposes a "Bulk dismiss ▾" menu when there are
pending Outlook items. Three scopes:

| Scope | Behavior |
|-------|----------|
| `outlook_unmatched` | Dismiss every pending Outlook row with no `client_id` (i.e. true triage queue) |
| `outlook_all` | Dismiss every pending Outlook row, matched or not |
| `all_pending` | Dismiss every pending row including manual notes (red, confirm twice) |

Endpoint: `POST /inbox/bulk-dismiss` with JSON body `{"scope": "outlook_unmatched"}`
or `{"inbox_ids": [1,2,3]}`. All paths populate `dismissed_outlook_messages`
for any rows that have an `outlook_message_id`.

## Subject Normalization (`_normalize_subject`)

Used by thread inheritance to determine if two emails are in the same thread. Strips in order:

1. Legacy `"Received: "` prefix from rows imported before migration 144 (modern rows store direction in `email_direction` column, no prefix)
2. External sender warnings: `[EXTERNAL]`, `[EXT]`, `*External*`, `EXTERNAL:`, `[External Sender]` and variants
3. Reply/forward prefixes: `Re:`, `RE:`, `Fwd:`, `FW:`, `Fw:` (repeated/nested)
4. Collapse whitespace, strip, lowercase

Result: `"[EXTERNAL] RE: FW: Re: GL Renewal Discussion"` → `"gl renewal discussion"`

**Adding new prefix patterns:** If you encounter new corporate email prefixes (e.g., `[CAUTION]`, `[SPAM?]`), add them to `_EXTERNAL_RE` in `email_sync.py`.

## Suggested contact capture

`_capture_unknown_contacts` upserts unknown sender/recipient addresses into
`suggested_contacts`. The filter cascade:

1. Skip freemail domains (`gmail.com`, `outlook.com`, ...)
2. Skip internal email domains (`marsh.com`, ...)
3. Skip automated/noreply prefixes via `_is_automated_sender` — local part
   matched against `automated_email_prefixes` config (default: `noreply`,
   `no-reply`, `donotreply`, `mailer-daemon`, `postmaster`, `bounce`,
   `bounces`, `notification`, `notifications`, `alert`, `alerts`,
   `automated`, `system`). Also strips `+suffix` so
   `bounces+abc123@list.com` → `bounces`.
4. Skip if the resolved client is archived (whole capture is bypassed).
5. Skip if already a known contact.

`automated_email_prefixes` is editable in **Settings → Email & Contacts**.

## Config Keys

| Key | Default | Purpose |
|-----|---------|---------|
| `last_outlook_sync` | null | ISO timestamp **of the moment the sweep started** (minus 60s overlap), cleared by Reset button in Settings |
| `outlook_sync_lookback_days` | 7 | First-sync lookback window |
| `outlook_capture_category` | "PDB" | Outlook category for opt-in on received emails. **Empty value triggers a sync error — refusing to scan all folders** |
| `outlook_skip_category` | "Personal" | Outlook category to exclude sent emails |
| `outlook_email_shell_header` | true | Navy header bar in formal HTML emails |
| `freemail_domains` | gmail.com, etc. | Domains skipped during client matching (freemail providers) |
| `internal_email_domains` | marsh.com, marshpm.com, mmc.com | Company domains skipped during client matching |
| `automated_email_prefixes` | noreply, no-reply, donotreply, mailer-daemon, postmaster, bounce, bounces, notification, notifications, alert, alerts, automated, system | Local-part prefixes treated as automated senders and excluded from `_capture_unknown_contacts` |

All editable in Settings > Email & Contacts tab. "Reset" button clears `last_outlook_sync` so next sync uses lookback window (safe — dedup prevents duplicates).

### Internal Domain Matching Lesson

Emails almost always include multiple Marsh colleague addresses in CC/TO. Without `internal_email_domains`, `_match_by_domain` would try to match `marsh.com` against client contacts, either causing ambiguous results (multiple clients → None) or false matches. Both `freemail_domains` and `internal_email_domains` are merged into a single skip set in `_match_by_domain` and `_capture_unknown_contacts`.

## AppleScript Gotchas

1. **`sender` property fails on sent messages** — wrap in `try/end try`, fall back to empty string
2. **`content` returns HTML** — use `plain text content of msg` for ref tag matching, fall back to `content` if plain text fails
3. **Body snippet truncation** — AppleScript truncates at 100 000 chars per email (cap on subprocess stdout volume); `_create_or_enrich_activity` then runs `_clean_email_text(...)[:5000]` so only the first 5 000 cleaned chars land in `activity_log.email_snippet` / `inbox.content`
4. **Flagged emails** — `todo flag of it is not not flagged` is the correct AppleScript syntax; `is flagged` does NOT work
5. **Categories** — `categories of msg` returns a list of category objects; iterate and get `name of c`
6. **Folder scanning** — `messages of inbox` only gets Inbox; use `every mail folder of default account` and iterate for cross-folder scans. Skip "Deleted Items", "Junk Email", "Drafts", "Trash", "Clutter", "Sent Items" (sent scanned separately)
7. **`mail folder of msg`** — NOT a valid property; track folder name during iteration instead
8. **JSON output** — AppleScript has no native JSON; build JSON strings manually with escaping helpers (`escJSON`, `padNum`, `replaceText`)
9. **Timeout** — 30s per `osascript` call; cap at 500 messages per folder scan
10. **`try` does NOT catch compile errors.** Unknown property names (e.g. `business phone` instead of `business phone number`) fail the whole script at parse time with `-2741 "Expected end of line but found property"`, before any `try` block runs. Probe the real dictionary with `properties of (first X)` before writing any new bridge function. See `reference_outlook_contact_properties.md` memory for the verified Outlook `contact` property list.
11. **Notes line endings** — use `linefeed` (LF), not `return` (CR) when building multi-line strings for Outlook note fields; mac apps render bare CRs as one long line.
12. **Local var names collide with app vocabulary inside `tell` blocks.** `set company to ""` resolves as the app's `company` property and crashes with `-10006`. Prefix locals with `v` (`vCompany`, `vNote`) or pick non-reserved names.
13. **Nested reference iteration returns empty property reads.** `repeat with cat in (categories of c)` when `c` came from `every contact` gives category refs whose `name of cat` silently returns `""`. Fix: `set catList to get categories of (contents of c)` then iterate `catList`.
14. **`existingList & scalar` fails when `existingList` is empty.** AppleScript tries to coerce the scalar to type `vector` and errors with `-1700`. Always wrap: `existingList & {scalar}`.
15. **`email addresses` records need BOTH `address` and `type class`.** Setting `{{address:"x"}}` alone fails `-1700`. Use `{{address:"x", type class:work}}`.
16. **`plain text note` strips newlines on read**; `note` preserves them. Prefer `note` for round-tripping plain-text.
17. **Outlook category colors are RGB triples, not `category color N` enum.** Omit the color arg (let Outlook pick) or supply `{r,g,b}` 16-bit ints.

## Contact Sync (PolicyDB → Outlook)

One-way push; PolicyDB is source of truth. Fenced by a user-configurable category (`outlook_contact_category`, default "PDB"). Orchestrated in `contact_sync.sync_contacts_to_outlook`, runs at the end of `/outlook/sync`.

### Push set
Every contact with at least one `contact_client_assignments` row on a non-archived client. Preferred client assignment (primary → oldest) drives `job_title` and `business_street_address`. Field mapping in `_row_to_payload`:

| PolicyDB | Outlook | Notes |
|---|---|---|
| `contacts.name` | `first name` / `last name` / `display name` | Split via `split_name()` (honorifics + suffixes stripped) |
| `contacts.email` | `email addresses[0].address` | Sanitized via `clean_email()` |
| `contacts.phone` | `business phone number` | Normalized via `format_phone()` |
| `contacts.mobile` | `mobile number` | Normalized via `format_phone()` |
| `contacts.organization` | `company` | — |
| `cca.title` | `job title` | From preferred assignment |
| `contacts.expertise_notes` | `note` (truncated 5000 chars, LF line endings) | — |
| `clients.address` | `business street address` (plain string) | From preferred assignment |

### Safety fences
- **PDB category is the escape hatch** — `list_pdb_contacts` only returns contacts carrying the category, and `delete_contact` refuses to delete a contact missing it. Untagging in Outlook removes a contact from sync scope.
- **Empty push set + tracked ids → abort** — if DB has `outlook_contact_id` pointers but Outlook returns zero tagged contacts, the sync stops instead of recreating everything (category likely renamed/deleted).
- **Archived clients excluded** — the push set query filters `archived = 0`.
- **Delete phase is opt-in** — gated by `outlook_contact_allow_deletes` config; only deletes contacts that (a) carry PDB tag and (b) are tracked in `contacts.outlook_contact_id`.

### Config keys
| Key | Default | Purpose |
|---|---|---|
| `outlook_contact_sync_enabled` | `true` | Master toggle |
| `outlook_contact_category` | `"PDB"` | Safety-fence category name |
| `outlook_contact_allow_deletes` | `true` | Allow push-set orphans to be deleted |

All editable in Settings → Email & Contacts. Reset button clears every `outlook_contact_id`.

### Migration 148
Adds `outlook_contact_id TEXT` to `contacts`.

## Email Snippet Display

Imported email content is shown via expandable `<details>` elements in:
- Action Center activities table (expand arrow per row)
- Policy correspondence timeline
- Issue detail activity timeline
- Inbox items (structured header + "show full content" toggle)

Activities from sync show a "synced" badge (`source='outlook_sync'`). Thread-inherited activities show `source='thread_inherit'`.

## Reassignment

If an email auto-links to the wrong client/policy, the expandable detail row in the Activities table has Client and Policy dropdowns for reassignment. Changing client reloads the policy dropdown. Uses `PATCH /activities/{id}/field` with `client_id` or `policy_id`.

## Migrations relevant to email sync

- **122** `outlook_message_id`, `source`, `email_snippet` on `activity_log` + index
- **123** `email_subject`, `email_date`, `outlook_message_id` on `inbox`
- **125** `dismissed_outlook_messages` table — message_ids whose activities were intentionally deleted, so sync won't re-import
- **129** `suggested_contacts` table — populated by `_capture_unknown_contacts`
- **132** `email_from` / `email_to` on `activity_log` and `inbox`
- **144** `email_direction` on `activity_log` and `inbox` (`'sent' | 'received' | 'flagged'`); backfills from legacy `Received: ` prefix and `[Outlook Sent|Received|Flagged]` brackets, then strips the legacy prefix from existing subjects

## Activity filter rule (anomaly engine + activity review)

`anomaly_engine.py` and `activity_review.py` count **`source IN ('manual',
'outlook_sync', 'thread_inherit') OR source IS NULL`** as user activity.
Including `thread_inherit` is required so renewals where most correspondence
was promoted via thread inheritance still register as "active" — leaving it
out causes silent false-positive `no_activity` anomaly findings.
