# Client Overview "About" Section + Email Token Fixes

**Date:** 2026-04-01
**Status:** Approved

## Problem

Business Description and Internal Notes are buried behind the client Edit button — users rarely see or maintain them. Several email template tokens are broken or missing (contact_organization always empty, no policy notes/description tokens, no issue details token).

## Design

### 1. New "About" Card on Client Overview

A new card inserted between Account Pulse and Account Strategy on the client overview tab.

**Business Description**
- Contenteditable div, saves on blur via PATCH to `/clients/{id}/field`
- Placeholder: "Describe the client's business, operations, and key exposures..."
- Plain text (no markdown), multi-line supported

**Internal Notes**
- Same contenteditable pattern, saves on blur
- Placeholder: "Internal notes, reminders, context for the team..."
- Replaces the notes snippet currently shown in Account Pulse (remove the snippet from Pulse)

**Save behavior (both fields):**
- On blur → PATCH → returns `{"ok": true, "formatted": "..."}`
- Success: brief green flash on field border (existing `flashCell()` pattern)
- Failure: red border flash + small inline error text that fades after 3s
- No save button — consistent with other contenteditable fields on the overview

### 2. Email Token Fixes

1. **Fix `contact_organization`** — populate from contacts table (primary client contact's organization field) instead of hardcoded empty string
2. **Add `policy_notes` and `policy_description`** tokens to policy context, registered in CONTEXT_TOKEN_GROUPS under the Policy group
3. **Add `issue_details`** token to issue context (from `activity_log.details`), registered in CONTEXT_TOKEN_GROUPS under the Issue group

### 3. Remove Notes Snippet from Account Pulse

The Account Pulse section currently shows a truncated Internal Notes snippet with an "Edit" button. Since the full notes will now be visible in the About card directly below, remove this snippet from Account Pulse to avoid duplication.

## Implementation Scope

### Template Changes
- Modify: `src/policydb/web/templates/clients/_tab_overview.html`
  - Insert new "About" card between Account Pulse and Account Strategy
  - Two contenteditable divs with labels, placeholders, and save-on-blur JS
  - Remove the Internal Notes snippet from the Account Pulse section

### Email Templates
- Modify: `src/policydb/email_templates.py`
  - Fix `contact_organization`: query contacts table for primary client contact's organization
  - Add `policy_notes` and `policy_description` to `_policy_tokens()` or `policy_context()`
  - Add `issue_details` to `issue_context()`
  - Register all new tokens in `CONTEXT_TOKEN_GROUPS`

### No New Routes Needed
- The PATCH `/clients/{id}/field` endpoint already handles `business_description` and `notes` fields
- No migration needed — all columns already exist

## Out of Scope
- Markdown rendering for Business Description
- Moving other Edit-only fields to the overview (contact mobile, hourly rate, etc.)
- Changes to the Edit page itself
