# LLM Bulk Contact Import (Client Level)

**Date:** 2026-03-26
**Status:** Approved

## Problem

Contacts are added one at a time on the client contacts tab. When onboarding a new client or processing a batch of email introductions, the user needs to manually create each contact. The existing LLM contact extraction lives on the policy page and scopes contacts to a single policy — there's no way to bulk-import contacts at the client level.

## Solution

Add an LLM-powered bulk contact import to the client contacts tab, following the same proven pattern as the bulk policy import: copy prompt → paste into LLM → paste JSON back → review matrix with dupe detection → apply.

The primary source material is email signature blocks, but the system accepts any text: rosters, meeting notes, spreadsheet dumps, or free-form text containing contact info.

## Schema: `CONTACT_BULK_IMPORT_SCHEMA`

Defined in `src/policydb/llm_schemas.py`. Extends the existing `CONTACT_EXTRACTION_SCHEMA` with a `contact_type` field.

| Field | Key | Type | Required | Normalizer | Config Values | Description |
|-------|-----|------|----------|------------|---------------|-------------|
| Full Name | `name` | string | yes | — | — | First and last name |
| Email Address | `email` | string | no | — | — | Email from signature or headers |
| Phone Number | `phone` | string | no | — | — | Office/direct phone |
| Mobile Number | `mobile` | string | no | — | — | Cell/mobile phone |
| Company / Organization | `organization` | string | no | — | — | Company name from sig or domain |
| Job Title | `title` | string | no | — | — | Job title from signature |
| Role | `role` | string | no | — | `contact_roles` (prefer) | Role relative to the client account |
| Contact Type | `contact_type` | string | no | — | — | `client`, `internal`, or `external` — inferred by LLM |

## Prompt Generation: `generate_contact_bulk_import_prompt(conn, client_id)`

Context-aware prompt that includes:

1. **Client context** — client name, industry segment
2. **Known carriers** — list of carriers on this account (so LLM tags carrier employees as `external`)
3. **Brokerage name** — from `cfg.get("brokerage_name")` or similar config key (so LLM tags colleagues as `internal`)
4. **Existing contacts** — names already assigned to this client (so LLM can flag duplicates)
5. **Contact roles** — from `cfg.get("contact_roles")`
6. **Source flexibility instruction** — "The user may paste email signatures, a contact roster, meeting notes, distribution lists, or any text containing people and their contact details."
7. **Type inference rules:**
   - Organization matches a known carrier → `external`
   - Organization matches brokerage name → `internal`
   - All others → `client`
   - If uncertain, omit `contact_type` (defaults to `client` in review)

## JSON Parser: `parse_contact_bulk_import_json(raw_text)`

Follows the same pattern as `parse_contact_extraction_json()`:

1. Size check (500KB limit)
2. Extract JSON from code fences or raw text
3. `json.loads()` → validate array
4. Normalize each item via `_parse_flat_fields()` with schema field definitions
5. Skip entries without a `name`
6. Return `{"ok": True, "contacts": [...], "warnings": [...], "count": N}`

## Routes (3 endpoints on clients router)

All in `src/policydb/web/routes/clients.py`.

### `GET /clients/{client_id}/ai-contact-import/prompt`

1. Fetch client context (name, industry, carriers, existing contacts)
2. Call `generate_contact_bulk_import_prompt(conn, client_id)`
3. Build `json_template` from schema example values
4. Build `context_display` badges (Client name, industry)
5. Render `_ai_import_panel.html` with:
   - `import_type = "client_contacts"`
   - `parse_url = f"/clients/{client_id}/ai-contact-import/parse"`
   - `import_target = "#ai-contact-import-result"`

### `POST /clients/{client_id}/ai-contact-import/parse`

1. Call `parse_contact_bulk_import_json(json_text)`
2. On error → return red error div (422)
3. Fetch existing client contacts via `get_client_contacts(conn, client_id)`
4. Annotate each parsed contact:
   - `already_assigned` — name matches existing client contact (case-insensitive)
   - `existing_contact` — name exists in global `contacts` table (may not be assigned to this client)
5. Default `contact_type` to `"client"` if not provided by LLM
6. Cache parsed contacts with UUID token in `_CLIENT_CONTACT_IMPORT_CACHE`
7. Render `clients/_ai_contacts_review.html`

### `POST /clients/{client_id}/ai-contact-import/apply`

1. Retrieve cached contacts by token
2. For each selected contact (checkbox `select_{i}`):
   a. Read overridden `role_{i}`, `type_{i}` from form
   b. `get_or_create_contact(conn, name, email=..., phone=..., mobile=..., organization=...)`
   c. `assign_contact_to_client(conn, cid, client_id, contact_type=type, role=role, title=title)`
3. Commit, clear cache
4. Return success HTML with created/updated counts
5. Include HX-Trigger to refresh the contacts tab

## Review Template: `clients/_ai_contacts_review.html`

New template following the pattern of `policies/_ai_contacts_review.html`.

### Layout

1. **Summary badges** — Total extracted | New | Already Assigned | Exists Globally
2. **Warnings section** — parse warnings if any
3. **Contact matrix table:**

| Column | Content | Editable |
|--------|---------|----------|
| Checkbox | `select_{i}`, checked by default unless `already_assigned` | yes |
| Name | Display text | no |
| Email | Display text | no |
| Phone | Office + mobile (if present) | no |
| Organization | Display text | no |
| Title | Display text | no |
| Role | `<select name="role_{i}">` from `contact_roles` config, LLM value pre-selected | yes |
| Type | `<select name="type_{i}">` with client/internal/external, LLM value pre-selected | yes |
| Status | Badge: "Assigned" (green) / "Exists" (blue) / "New" (gray) | no |

4. **Select-all toggle** — toggles non-disabled checkboxes
5. **Apply button** — `hx-post` to apply endpoint, includes form via `hx-include`

### Status Logic

- `already_assigned=True` → green "Assigned" badge, checkbox disabled (unchecked)
- `existing_contact` present but not assigned to this client → blue "Exists" badge, checkbox checked
- Neither → gray "New" badge, checkbox checked

## UI Trigger

Button in the client contacts tab header, next to existing add-row controls:

```html
<button hx-get="/clients/{{ client.id }}/ai-contact-import/prompt"
        hx-target="#ai-contact-import-result"
        hx-swap="innerHTML"
        class="...">
    AI Import
</button>
<div id="ai-contact-import-result"></div>
```

Styled consistently with the bulk policy import button on the client page.

## Files Changed

| File | Change |
|------|--------|
| `src/policydb/llm_schemas.py` | Add `CONTACT_BULK_IMPORT_SCHEMA`, `generate_contact_bulk_import_prompt()`, `parse_contact_bulk_import_json()` |
| `src/policydb/web/routes/clients.py` | Add 3 routes: prompt, parse, apply. Add `_CLIENT_CONTACT_IMPORT_CACHE` dict. |
| `src/policydb/web/templates/clients/_ai_contacts_review.html` | New review matrix template |
| `src/policydb/web/templates/clients/_tab_contacts.html` | Add "AI Import" button + result div |

## No Migration Needed

No schema changes — uses existing `contacts`, `contact_client_assignments` tables and existing CRUD functions.

## Config Dependencies

- `contact_roles` — existing, used for role dropdown
- `brokerage_name` — may need to add to `_DEFAULTS` if not already present (used for internal contact type inference)
- `carriers` — existing, used for external contact type inference

## Edge Cases

- **Empty paste** — parse returns error, red div shown
- **No name on a contact** — skipped during parse with warning
- **All contacts already assigned** — all checkboxes disabled, apply button still works (no-op with 0 applied)
- **Cache expiry** — token-based, same pattern as policy import. Stale token returns "Session expired" message.
- **Phone/email normalization** — `format_phone()` and `clean_email()` applied in the apply step (same as manual contact add)
