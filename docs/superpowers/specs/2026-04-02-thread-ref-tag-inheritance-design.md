# Thread-Level Ref Tag Inheritance — Design Spec

**Date:** 2026-04-02
**Status:** Approved

## Problem

When emails are synced from Outlook, only emails containing a `[PDB:...]` ref tag get auto-linked to client/policy records. Older emails in the same thread — sent before the ref tag was added — land in the Inbox for manual triage, even though they clearly belong to the same conversation. This creates unnecessary manual work.

## Solution

After the normal sync pass, run a **thread inheritance pass** that propagates ref tag matches to unmatched emails in the same thread. Operates on both new unmatched emails from the current sync batch and existing unmatched inbox items from prior syncs.

## Subject Normalization

Thread membership is determined by normalized email subject comparison:

1. Strip leading reply/forward prefixes: `Re:`, `RE:`, `Fwd:`, `FW:`, `Fw:` — repeated and nested (e.g., `RE: FW: Re:`)
2. Collapse whitespace to single spaces, strip leading/trailing
3. Lowercase for comparison

Example: `"RE: FW: Re: GL Renewal Discussion"` → `"gl renewal discussion"`

Two emails are in the same thread if their normalized subjects match exactly. No fuzzy matching.

## Scoping Rules

An unmatched email inherits a ref tag match when ALL of the following hold:

1. **Exact normalized subject match** with a tagged email
2. **Client domain match** — the unmatched email's sender/recipient domains resolve to the same client as the tagged email (via existing `_match_by_domain` logic)
3. **Source match is Tier 1 only** — inheritance only propagates from ref-tag-resolved matches (`tier=1`), not from domain-only guesses (`tier=2`). This prevents domain guesses from cascading across a thread.
4. **No existing match** — the email doesn't already have a resolved match

## Inherited Fields

The full match dict from the tagged email is inherited: `client_id`, `policy_id`, `program_id`, `issue_id`.

## Processing Flow

### Within `sync_outlook()`

After the three normal scan phases (Sent, Received/PDB-category, Flagged) and before updating the last sync timestamp:

```
1. Build thread map:
   - Key: (normalized_subject, client_id)
   - Value: match dict (client_id, policy_id, program_id, issue_id)
   - Source: all emails that matched via Tier 1 (ref tag) during this sync
            + historical activities with outlook_message_id and source='outlook_sync'

2. Collect unmatched candidates:
   a) New emails from current sync that went to inbox (tracked in results["suggestions"])
   b) Existing inbox items with outlook_message_id from prior syncs

3. For each unmatched candidate:
   a) Normalize subject
   b) Extract sender/recipient email addresses
   c) Run _match_by_domain() to get candidate client_id
   d) Look up (normalized_subject, client_id) in thread map
   e) If found → promote to activity with inherited match
```

## Activity Creation for Inherited Matches

Promoted emails become activities with:
- `source = 'thread_inherit'` (distinguishable from `outlook_sync` and `manual`)
- `activity_type = 'Email'`
- Same fields as normal sync-created activities (subject, snippet, contact resolution, etc.)
- `outlook_message_id` preserved from the inbox item for dedup
- `follow_up_done = 1` (informational record, not an action item)

## Inbox Item Cleanup

When an inbox item is promoted via thread inheritance:
- Delete the row from the `inbox` table
- The activity in `activity_log` replaces it

## Sync Results Reporting

Add a new counter to the results dict:
- `results["thread_inherited"]` — integer count of emails linked via thread inheritance
- Display in the sync results banner as a separate line: "X linked via thread"

## File Changes

| File | Change |
|------|--------|
| `src/policydb/email_sync.py` | Add `_normalize_subject()`, `_run_thread_inheritance()`, call from `sync_outlook()` |
| `src/policydb/web/templates/outlook/_sync_results.html` | Add thread-inherited count to results banner |

No migration needed — uses existing `source` column on `activity_log` (free-text field, already stores `outlook_sync` and `manual`).

## No Config Toggle

Ships enabled by default. The exact-subject + domain-scoping combination makes false positives unlikely. A `thread_inherit_enabled` config key can be added later if needed.

## Edge Cases

| Case | Behavior |
|------|----------|
| Multiple tagged emails in same thread with different policies | Uses the most recent tagged email's match (latest `activity_date`) |
| Unmatched email domain resolves to multiple clients | Skipped — ambiguous domain match returns None |
| Thread with only domain-matched (Tier 2) emails, no ref tags | No inheritance — only Tier 1 matches propagate |
| Email already exists as an activity (dedup) | Skipped via `outlook_message_id` check |
| Previously dismissed email (`dismissed_outlook_messages`) | Skipped — respects user's explicit dismiss |
