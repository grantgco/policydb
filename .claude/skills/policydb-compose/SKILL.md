---
name: policydb-compose
description: >
  Email compose slideover and template token system reference for PolicyDB. Use
  when working on the compose panel, email templates, token context functions,
  recipient resolution, template CRUD, formal email formatting, policy table
  inclusion, or any code that touches compose.py, email_templates.py, templates/,
  or _compose_slideover.html. Also trigger when adding new database fields that
  should become email tokens, or debugging token rendering issues.
---

# Compose Mail & Email Template System

The compose system provides a right-side slideover panel for drafting emails from any
context in the app. It resolves recipients, pre-fills subject/body from config templates
or user-defined templates, renders `{{token}}` placeholders, and dispatches via Outlook
AppleScript or mailto: fallback.

## Architecture

| File | Purpose |
|------|---------|
| `src/policydb/email_templates.py` | Token engine: `render_tokens()`, all `*_context()` builders, `CONTEXT_TOKEN_GROUPS`, policy table HTML, `wrap_email_html()` |
| `src/policydb/web/routes/compose.py` | Compose endpoints: `GET /compose`, `GET /compose/render`, `GET /compose/recipients`, `_load_recipients()` |
| `src/policydb/web/routes/templates.py` | Template CRUD: `GET/POST /templates`, `/templates/{id}/edit`, `/templates/{id}/duplicate`, `/templates/{id}/delete` |
| `src/policydb/web/routes/outlook_routes.py` | `POST /outlook/compose` — draft creation (see `policydb-outlook` skill for sync/sweep) |
| `src/policydb/web/templates/_compose_slideover.html` | Slideover panel UI + JavaScript (~350 lines) |
| `src/policydb/web/templates/_recipient_picker.html` | To/CC picker with role badges (~83 lines) |
| `src/policydb/web/templates/templates/index.html` | Template management page |
| `src/policydb/web/templates/templates/_template_form.html` | Template editor with token pill toolbar |
| `src/policydb/web/templates/templates/_template_card.html` | Template display card |
| `src/policydb/web/templates/base.html` (lines 695-738) | `buildMailto()`, `openComposeSlideover()`, `closeComposeSlideover()` |

## Compose Flow

```
User clicks Compose button (any page)
  -> openComposeSlideover(params)           // base.html JS
  -> HTMX GET /compose?context=...&...      // loads slideover partial
  -> compose_panel()                         // compose.py
     |-- Resolves token context (priority below)
     |-- Loads recipients via _load_recipients()
     |-- Pre-fills subject from config template
     |-- Auto-generates body (RFI notify mode only)
     |-- Appends [PDB:{ref_tag}]
     |-- Returns _compose_slideover.html
  -> User optionally picks a template
     -> loadComposeTemplate(id)              // JS fetch
     -> GET /compose/render?template_id=...  // renders tokens
     -> Updates subject + body fields
  -> User sends:
     A) "Create Draft in Outlook" -> POST /outlook/compose
     B) "Copy All" -> clipboard (subject + body text only)
     C) Fallback -> mailto: link (if Outlook unavailable)
```

## Context Resolution Priority

The compose panel and template render endpoints both use the same priority chain:

| Priority | Condition | Context Function | Additional Overlays |
|----------|-----------|------------------|---------------------|
| 1 | `issue_uid` | `issue_context(conn, issue_uid)` | None |
| 2 | `mode='rfi_notify'` + `bundle_id` | `rfi_notify_context(conn, bundle_id)` | Auto-generated body |
| 3 | `policy_uid` | `policy_context(conn, policy_uid)` | `+ timeline_context()` + `location_context()` if project linked |
| 4 | `project_name` + `client_id` | `location_context(conn, client_id, project_name)` | None |
| 5 | `client_id` only | `client_context(conn, client_id)` | None |

## Token Contexts & Functions

### render_tokens(template_text, context) -> str
- Replaces `{{key}}` with `str(value)` or empty string for None/falsy
- Strips unreplaced `{{...}}` via regex cleanup
- **Warning:** Lists render as `['a', 'b']` — context values MUST be strings

### Context Builders

| Function | Input | Key Tokens |
|----------|-------|------------|
| `policy_context(conn, policy_uid)` | policy_uid | policy_type, carrier, premium, limit, deductible, effective/expiration dates, placement_colleague_*, program_*, sub_coverages, exposure_*, COPE data (if project linked), ref_tag |
| `client_context(conn, client_id)` | client_id | client_name, cn_number, fein, industry, address, primary_contact/email, policy_list, coverage_summary, rfi_*, compliance_*, ref_tag |
| `location_context(conn, client_id, project_name)` | client_id + project_name | All location_* fields, team_names/emails, placement_colleagues, policy_count, total_premium, policy_list, coverage_summary |
| `followup_context(row)` | dict | subject, contact_person, duration_hours, disposition, thread_ref |
| `timeline_context(conn, policy_uid)` | policy_uid | days_to_expiry, drift_days, blocking_reason, current_status, milestones_complete/remaining |
| `meeting_context(conn, meeting_id)` | meeting_id | meeting_title/date/time/type/location/duration, attendees, decisions, action_items |
| `issue_context(conn, issue_uid)` | issue_uid | issue_uid/subject/status/severity, linked_activities, client_name, policy_type |
| `rfi_notify_context(conn, bundle_id)` | bundle_id | rfi_uid, request_title, client_name, received_items, outstanding_items |

### Helper Functions

| Helper | Purpose |
|--------|---------|
| `_client_tokens(conn, client_id, row)` | Resolves primary contact, coverage_gaps, risk_summary from client record |
| `_project_tokens(conn, project_id)` | Location fields, full_address, COPE data from projects table |
| `_build_policy_list_tokens(conn, client_id, project_name)` | policy_list (bulleted), coverage_summary (grouped), policy_table (tab-separated) |
| `_build_rfi_tokens(conn, client_id)` | rfi_due_dates, rfi_outstanding_count |
| `_build_compliance_tokens(conn, client_id)` | compliance_pct, compliance_gaps, compliance_gap_lines |

## CONTEXT_TOKEN_GROUPS

Defines which tokens appear in the template editor pill toolbar, grouped by context:

- **"policy"** context: Policy, Program, Dates, Financials, Exposure, Client, Contact, Location, COPE, Follow-up, Timeline, Tracking
- **"client"** context: Client, Contact, Book of Business, Meeting, Compliance, Other, Tracking
- **"issue"** context: Issue, Client, Policy, Other, Tracking

### Critical Rule: New Fields -> Add to Tokens

Every time a new field is added to policies, clients, projects, or related tables:
1. Add to the relevant `*_context()` function or `_*_tokens()` helper
2. Add to the `CONTEXT_TOKEN_GROUPS` dict under the correct context and group
3. For projects table changes: update `_project_tokens()` which feeds both `location_context()` and `policy_context()`

## Recipient Resolution

`_load_recipients(conn, policy_uid, client_id, project_name, mode, issue_uid)` returns a list of `{name, email, role, badge, pre_checked, source}` dicts.

### Badge Types & Behavior

| Badge | Color | Source | Pre-checked |
|-------|-------|--------|-------------|
| CLIENT | green | `contact_client_assignments` (contact_type='client') | No |
| INTERNAL | blue | `contact_client_assignments` (contact_type='internal') | Yes |
| PLACEMENT | amber | `contact_policy_assignments` (is_placement_colleague=1) | No |
| UNDERWRITER | amber | `contact_policy_assignments` (not placement) | No |
| EXTERNAL | purple | `contact_client_assignments` (contact_type='external') | No |

### Loading Order
1. **Issue mode:** Resolves to program (all policies) or single policy contacts
2. **Policy contacts:** From `contact_policy_assignments`
3. **Location contacts:** Deduped across all policies in project
4. **Client contacts:** Internal first, then external. Deduped by email (case-insensitive)
5. **RFI notify mode:** Excludes CLIENT/EXTERNAL contacts (internal team only)

### Primary "To" Selection
- If `to_email` param: finds matching recipient
- Else (not rfi_notify): defaults to first CLIENT contact
- RFI notify: no default To (CC-only mode)

## Email Subject Templates (Config)

| Config Key | Default Template |
|------------|-----------------|
| `email_subject_policy` | `Re: {{client_name}}{{project_name_sep}} - {{policy_type}} - Eff. {{effective_date}}` |
| `email_subject_client` | `Re: {{client_name}}` |
| `email_subject_followup` | `Re: {{client_name}}{{project_name_sep}} - {{policy_type}} - {{subject}}` |
| `email_subject_meeting` | `Meeting Recap: {{meeting_title}} - {{meeting_date}}` |
| `email_subject_request` | `{{client_name}} - {{rfi_uid}} {{request_title}}` |
| `email_subject_rfi_notify` | `FYI: {{client_name}} - {{rfi_uid}} Items Received` |
| `email_subject_issue` | `Re: {{client_name}} - Issue: {{issue_subject}}` |

All editable in Settings UI.

## Formal Format vs Policy Table (Known Coupling)

The two compose checkboxes interact in a non-obvious way:

**Backend logic** (`outlook_routes.py:123`):
```python
if req.formal_format or policy_table_html:
    # Both trigger the Marsh-branded HTML shell
```

| Formal? | Table? | Result |
|---------|--------|--------|
| No | No | Plain text body + `[PDB:ref_tag]` appended |
| Yes | No | HTML shell (Marsh branded), body converted markdown -> HTML |
| No | Yes | **HTML shell activated anyway** — body converted to HTML + table inserted |
| Yes | Yes | HTML shell + table |

**Why coupled:** An HTML table cannot be embedded in a plain-text email. Checking "Include policy table" forces the entire email into HTML mode.

**Discrepancy 1 — UI doesn't communicate coupling:** The checkboxes appear independent but aren't. Checking table silently upgrades body formatting.

**Discrepancy 2 — Fallback paths ignore both checkboxes:**
- `openComposeInMail()` (mailto: fallback): Plain text only. Neither formal format nor policy table are included. No warning shown.
- `copyComposeAll()`: Copies subject + body text only. Both checkboxes silently ignored.

**Discrepancy 3 — Policy table scoping varies by context:**
- Single policy → only that policy's row
- Project/location → all policies matching project_name for client
- Issue → all program policies (if program linked) or single linked policy
- Client-only → **no table available** (checkbox not shown, but code has no handler)

## Compose Entry Points

Every compose button calls `openComposeSlideover(params)` from `base.html`:

| Template | Context | Key Params |
|----------|---------|------------|
| `clients/_tab_contacts.html` | client | `context:'client', client_id` |
| `policies/_tab_contacts.html` | policy | `context:'policy', policy_uid, client_id, to_email` |
| `clients/_project_header.html` | location | `context:'policy', client_id, project_name` |
| `clients/_request_bundle.html` | RFI | `context:'client', bundle_id` or `mode:'rfi_notify', bundle_id` |
| `followups/_row.html` | follow-up | `context:'policy', policy_uid, client_id, to_email` |
| `activities/_activity_row.html` | activity | `context:'policy', policy_uid, client_id, to_email` |
| `issues/detail.html` | issue | `context:'issue', issue_uid, client_id` |
| `dashboard.html` | dashboard | `context:'policy', policy_uid, client_id, to_email` |
| `briefing.html` | briefing | `context:'policy', policy_uid, client_id, to_email` |
| `policies/_policy_renew_row.html` | renewal | `context:'policy', policy_uid, client_id, to_email` |
| `policies/_opp_row.html` | opportunity | `context:'policy', policy_uid, client_id, to_email` |

## Template CRUD Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `GET /templates` | GET | List all templates grouped by context |
| `POST /templates/new` | POST | Create (name, context, description, subject_template, body_template) |
| `GET /templates/{id}/edit` | GET | Edit form partial |
| `POST /templates/{id}/edit` | POST | Save edits |
| `POST /templates/{id}/duplicate` | POST | Clone with "(copy)" suffix |
| `POST /templates/{id}/delete` | POST | Delete template |

Template contexts: `"policy"`, `"client"` (maps to template selector filtering in compose panel).

## Token Pill Toolbar (Template Editor)

`_template_form.html` renders grouped token buttons per context. Clicking a pill calls `insertToken(fieldId, tokenKey)` to insert `{{key}}` at cursor position.

Context radio buttons (Policy/Client) trigger `updatePills()` which dynamically rebuilds the toolbar from `CONTEXT_TOKENS_DATA` (pre-serialized from `CONTEXT_TOKEN_GROUPS`).

## Known Bugs & Edge Cases

### Active Bugs

1. **List values stringify as `['a', 'b']`** — `rfi_notify_context()` returns `received_items` and `outstanding_items` as Python lists. `render_tokens()` calls `str(value)` which produces `"['Item 1', 'Item 2']"` in the email. Fix: format as `"\n".join(list)` before returning.

2. **`exposure_denominator` renders as "None"** — `policy_context():661` does `str(primary["denominator"])` when primary exists but denominator is None. Fix: add `and primary.get("denominator")` to the conditional.

### Token Group Documentation Gaps

3. **Follow-up/Timeline/Location tokens listed under "policy" context** but only populated when `followup_context()`, `timeline_context()`, or `location_context()` is overlaid on top of `policy_context()`. Users see these in the template editor but they render empty in many policy-only emails.

4. **Meeting tokens listed under "client" context** but `client_context()` does NOT populate them. Only `meeting_context(meeting_id)` fills these — and it's only invoked from specific meeting-related compose triggers, not from generic client compose.

5. **`contact_organization` hardcoded empty** — `_client_tokens():513` initializes as `""` and never queries `contacts.company_organization`. Either populate from primary contact or remove from tokens.

### Edge Case Behavior

6. **RFI body auto-generation in compose.py vs rfi_notify_context()** — compose.py (lines 340-361) correctly formats items as bulleted text. But if a template references `{{received_items}}`, it gets the raw list (bug #1 above). Two different code paths for the same data.

7. **Template context filtering** — Compose panel loads templates by context match: policy/location -> policy + general templates, client -> client + general. There is no "issue" or "rfi_notify" template context — issue emails use policy templates, RFI uses client templates.

8. **Token re-rendering on template select** — `loadComposeTemplate()` fetches `/compose/render` which rebuilds the full context. If the user manually edited the body before selecting a template, edits are overwritten without warning (only `composeEdited` flag is tracked but not checked before overwrite).

## Policy Table HTML

`_render_policy_table_html(rows)` in `email_templates.py` produces Outlook-safe inline-styled HTML:
- Columns: Policy Type, Carrier, Policy #, Effective, Expiration, Premium, Limit, Description
- Header: `#003865` bg, white text
- Alt rows: `#F7F3EE`
- All inline styles (no CSS classes) for Outlook compatibility

`_render_policy_table_text(rows)` produces tab-separated plain text fallback.

## Ref Tag Handling

- Format: `[PDB:{ref_tag}]` appended to all composed emails
- In formal mode: stripped from body text before markdown conversion, re-added as styled footer in `wrap_email_html()`
- In plain mode: appended to body text as-is
- Purpose: enables Outlook sync to match sent emails back to records
