# Email Compose System Redesign

## Context

The current email compose system has grown organically with different implementations across pages (policy, client, follow-up, RFI, dashboard, briefing, opportunities), resulting in inconsistent recipient logic, confusing template contexts, and no way to notify internal colleagues on RFI receipt. Users end up manually adjusting recipients in Outlook because the defaults are unpredictable and opaque. This redesign unifies everything into a single compose slideover with transparent recipient management.

## Design Summary

### 1. Unified Compose Slideover

**Replace all inline compose panels and ep-trigger popovers** with a single right-side slideover panel that works identically from every trigger point.

**Layout (top to bottom):**
- Header: "Compose Email" + close button (Escape key also closes)
- Context label: auto-detected (e.g., "ACME Corp — GL Policy (POL-042)")
- Recipient picker (see section 2)
- Subject field (editable, pre-filled from config subject template — NOT blank)
- Template selector (optional dropdown below subject, defaults to "No template — quick email")
- Body field (editable textarea — ref tag auto-appended; blank for quick email, filled when template selected)
- Preview line: "To: x@y.com · CC: a@b.com, c@d.com"
- Actions: "Copy All" (subject + newline + body to clipboard) + "Open in Mail →"

**Slideover behavior:**
- Right-side panel, `position: fixed`, z-index above all content
- Body scroll lock when open
- Click-outside closes (with confirmation if body was edited)
- Escape key closes
- On viewports < 640px: full-width overlay instead of right-side panel

**Trigger:** Contextual buttons on each page call `openComposeSlideover({context, policy_uid, client_id, project_name, bundle_id, mode, ...})`. One JS function, one slideover, different pre-filled data.

**New route module:** `src/policydb/web/routes/compose.py` with prefix `/compose`

**Endpoints:**
- `GET /compose` — returns slideover HTML partial with recipient picker, subject, body, template dropdown
- `GET /compose/recipients` — returns JSON recipient list with role, source, pre_checked flag
- `GET /compose/render` — renders a selected template, returns subject + body HTML

**Files to create:**
- `src/policydb/web/routes/compose.py` — new route module
- `src/policydb/web/templates/_compose_slideover.html` — slideover template
- `src/policydb/web/templates/_recipient_picker.html` — reusable recipient partial

**Files to modify (trigger conversion — ALL templates with ep-trigger or compose panels):**
- `src/policydb/web/templates/base.html` — add slideover container div, `openComposeSlideover()` JS, extract `buildMailto()` to standalone function, remove ep-trigger IIFE
- `src/policydb/web/templates/policies/_tab_contacts.html` — replace `<details>` compose with button
- `src/policydb/web/templates/clients/_tab_contacts.html` — replace `<details>` compose with button
- `src/policydb/web/templates/clients/_project_header.html` — replace inline compose with button
- `src/policydb/web/templates/clients/_request_bundle.html` — replace compose button + add "Notify Team"
- `src/policydb/web/templates/followups/_row.html` — replace compose row + ep-trigger buttons
- `src/policydb/web/templates/dashboard.html` — convert ep-trigger buttons
- `src/policydb/web/templates/briefing.html` — convert ep-trigger buttons
- `src/policydb/web/templates/briefing_client.html` — convert ep-trigger buttons
- `src/policydb/web/templates/activities/_activity_row.html` — convert ep-trigger buttons
- `src/policydb/web/templates/policies/_opportunities_section.html` — convert ep-trigger buttons
- `src/policydb/web/templates/policies/_opp_row.html` — convert ep-trigger buttons
- `src/policydb/web/templates/policies/_policy_renew_row.html` — convert ep-trigger buttons
- `src/policydb/web/app.py` — register compose router

### 2. Recipient Picker

**Role-based groups with full visibility.** Every contact shows a colored badge explaining why they're suggested.

**Badge types:**
- `CLIENT` (green) — external contacts assigned to the client
- `INTERNAL` (blue) — internal team members on the client
- `PLACEMENT` (amber) — placement colleagues on the policy
- `UNDERWRITER` (amber) — underwriters on the policy

**Sections:**
- **To:** Primary contact (removable, changeable via dropdown or search)
- **Suggested CC:** All relevant contacts with role badges, checkboxes (pre-checked based on context)
- **Add recipient:** Search across all contacts or type freeform email address
- **Preview line:** Always-visible "To: ... · CC: ..." summary

**Suggestion logic by context:**
| Trigger | To default | CC suggestions (pre-checked) |
|---------|-----------|------|
| Policy page | Primary client contact | Account exec ✓, placement colleague ✓, underwriter ☐ |
| Client page | Primary client contact | Account exec ✓, all internal team ✓ |
| Follow-up row | contact_person (if set) or primary | Account exec ✓, placement colleague ✓ |
| Location/project | Primary client contact | Account exec ✓, project team ✓ |
| RFI notify (internal) | *(no To — internal only)* | All internal ✓, all placement ✓ |

**Edge cases:**
- No contacts: Show empty picker with "No contacts found — add one below or type an email address" message, with the Add recipient search field prominent
- Multiple primaries: Use first `is_primary=1` contact; if none flagged, use first client contact alphabetically
- Per-contact email buttons (e.g., clicking an email on a contact row): Opens slideover with that specific contact as To

**Backend:** Move `_load_contacts()` from `templates.py` into `compose.py` (or shared utility). Add `pre_checked` flag and role badge info to the response.

### 3. Template Simplification (7 → 2 contexts)

**Collapse to 2 contexts:** Policy and Client.

| Old context | New context |
|---|---|
| `policy` | `policy` |
| `client` | `client` |
| `location` | `policy` |
| `general` | `client` |
| `followup` | `policy` |
| `meeting` | `client` |
| `timeline` | `policy` |

**Token merging:** All tokens from collapsed contexts must be added to the surviving groups in `CONTEXT_TOKEN_GROUPS`:
- **Policy group gains:** location tokens (`location_name`, `location_description`, `policy_count`, `total_premium`, `team_names`, `team_emails`, `placement_colleagues`, `placement_emails`), followup tokens (`subject`, `contact_person`, `duration_hours`, `disposition`, `thread_ref`), timeline tokens (`days_to_expiry`, `drift_days`, `blocking_reason`, `current_status`, `milestones_complete`, `milestones_remaining`)
- **Client group gains:** meeting tokens (`meeting_title`, `meeting_date`, `meeting_time`, `meeting_type`, `meeting_location`, `meeting_duration`, `attendees`, `decisions`, `action_items`, `meeting_notes`)

**Context builders preserved:** All existing builder functions (`policy_context`, `client_context`, `location_context`, `followup_context`, `meeting_context`, `timeline_context`) remain. At render time, the compose endpoint builds a merged context dict by calling the appropriate builders based on available params:
- Has `policy_uid` → call `policy_context()`, overlay `timeline_context()` if policy has timeline data
- Has `client_id` only → call `client_context()`
- Has `project_name` → call `location_context()` overlay
- Has `meeting_id` → call `meeting_context()` overlay
- Has follow-up row data → overlay `followup_context()`

**Template builder UI:** Replace context dropdown with two pill buttons: "Policy emails" / "Client emails". Token pill toolbar shows all token groups for the selected context.

**Cross-context rendering note:** If a user selects a policy template from a client context, policy-specific tokens render as blank. Add a subtle note in the template dropdown: "(some tokens may not fill in this context)" next to mismatched templates.

**Migration:** `071_simplify_template_contexts.sql` — UPDATE existing template context values. **Must be wired into `init_db()` in `db.py`** with version check + INSERT into `schema_version`.

**Files:**
- `src/policydb/migrations/071_simplify_template_contexts.sql`
- `src/policydb/db.py` — wire migration 071
- `src/policydb/email_templates.py` — merge token groups, keep all builders
- `src/policydb/web/routes/templates.py` — update CRUD for 2 contexts, update `_CONTEXT_LABELS`
- `src/policydb/web/templates/templates/_template_form.html` — pill buttons instead of dropdown

### 4. RFI Notify Flow

**New "Notify Team" button at the RFI bundle level** when any items have been received.

**Trigger:** Button visible on bundle card when `received_count > 0`. Reactively updated via HTMX partial swap when items are toggled received/un-received. Calls `openComposeSlideover({mode: 'rfi_notify', bundle_id: ...})`.

**Slideover behavior in RFI notify mode:**
- Pre-selects internal + placement contacts only (no client contacts in To)
- Subject from new config key: `email_subject_rfi_notify`
- Body auto-generated listing received items + outstanding items
- Ref tag: `[PDB:{rfi_uid}]`

**New context builder:** `rfi_notify_context(conn, bundle_id)` in `email_templates.py`:
- Returns: `rfi_uid`, `request_title`, `client_name`, `received_items` (list), `outstanding_items` (list), `bundle_status`, `sent_at`
- Body template built server-side (not a user-editable template — hardcoded format)

**Config:**
- Add `email_subject_rfi_notify` to `_DEFAULTS`: `"FYI: {{client_name}} — {{rfi_uid}} Items Received"`
- Add to `_allowed` set in `save_email_subject()` in `settings.py` (NOT `EDITABLE_LISTS`)
- Render in Settings UI Email Subject Lines section alongside existing subject templates

**Files:**
- `src/policydb/email_templates.py` — add `rfi_notify_context()`
- `src/policydb/web/routes/compose.py` — handle `mode=rfi_notify`
- `src/policydb/web/templates/clients/_request_bundle.html` — add "Notify Team" button, reactive visibility
- `src/policydb/config.py` — add `email_subject_rfi_notify` to `_DEFAULTS`
- `src/policydb/web/routes/settings.py` — add key to `_allowed` set
- `src/policydb/web/templates/settings.html` — render new subject field

### 5. Cleanup / Removal

**Remove after new system is fully wired:**

Templates:
- `templates/_compose_panel.html`
- `templates/_compose_rendered.html`
- `clients/_request_compose.html`

Endpoints:
- `GET /templates/compose` in `templates.py`
- `GET /templates/render` in `templates.py`
- `GET /clients/{id}/requests/{bundle_id}/compose` in `clients.py`
- `GET /clients/{id}/requests/compose-all` in `clients.py`
- `GET /clients/{id}/projects/{name}/email-team` in `clients.py`
- `GET /policies/{uid}/team-cc` in `policies.py`
- `GET /clients/{id}/team-cc` in `clients.py`

JavaScript:
- ep-trigger IIFE in `base.html` (lines ~627-767) — extract `buildMailto()` first
- `toggleRequestCompose()` in `_request_bundle.html`
- `updateQuickEmail()` in `_compose_panel.html`
- All compose-row toggle functions in `followups/_row.html`

Route variables:
- `team_cc_json` computation in `policies.py` (lines ~1337, 1519, 2198) and `clients.py` (~1432)
- `_CONTEXT_LABELS` in `templates.py` — update from 4+ entries to 2

**Keep:**
- Template CRUD at `/templates` (list, create, edit, delete, duplicate)
- All context builder functions in `email_templates.py`
- Config email subject templates (all existing ones)
- `buildMailto()` JS function (extracted to standalone)

### 6. Implementation Phases

**Phase 1 — Build new infrastructure (additive, nothing removed):**
- Create `compose.py` router with `/compose`, `/compose/recipients`, `/compose/render`
- Create `_compose_slideover.html` and `_recipient_picker.html`
- Add slideover container + `openComposeSlideover()` JS to `base.html`
- Add `rfi_notify_context()` to `email_templates.py`
- Add config key + settings UI for `email_subject_rfi_notify`
- Register compose router in `app.py`

**Phase 2 — Convert trigger points (old system still works as fallback):**
- Convert all 13+ templates with ep-trigger buttons to `openComposeSlideover()` calls
- Replace `<details>` compose panels with simple buttons
- Add "Notify Team" button to RFI bundle template
- Test each converted page individually

**Phase 3 — Remove old infrastructure:**
- Remove old compose templates, endpoints, JS functions
- Remove `team_cc_json` route computation
- Clean up unused imports

**Phase 4 — Template context migration:**
- Run migration 071 to consolidate contexts
- Update `CONTEXT_TOKEN_GROUPS` with merged tokens
- Update template builder UI to 2-context pill buttons
- Update `_CONTEXT_LABELS`

## Verification

**Core flows:**
1. Compose from policy page → slideover opens, correct context/recipients/subject
2. Compose from client page → client context, client contacts
3. Compose from follow-up row → policy context, follow-up contact as To
4. Compose from location header → location subject, project team as CC
5. RFI notify → internal contacts only, received/outstanding items listed
6. Template rendering → select template, tokens render, body fills in
7. Recipient editing → add/remove/toggle, preview line updates
8. Open in Mail → Outlook opens with correct To/CC/Subject/Body
9. Copy All → clipboard has subject + body

**Regression testing (all ep-trigger conversion points):**
10. Dashboard follow-up email buttons
11. Renewal pipeline row email buttons
12. Opportunity row email buttons
13. Activity row email buttons
14. Briefing page email buttons
15. Per-contact inline email links (contact rows)

**Edge cases:**
16. No contacts → empty picker with search field
17. Cross-context template selection → tokens render blank gracefully
18. RFI un-receive → "Notify Team" button hides reactively
19. Narrow viewport (< 640px) → slideover renders full-width
20. Keyboard navigation → open/close/tab through slideover
21. Edited body + click outside → confirmation before closing

**Migration:**
22. After migration 071 → verify all existing templates retained with correct context labels
23. Template builder → only 2 context pills shown, all tokens available per context
