# Program Contacts, Workflow & Files

**Date:** 2026-04-02
**Status:** Draft

## Summary

Add Contacts, Workflow, and Files tabs to the Program detail page. Program-level contacts are the source of truth — they inherit down to all child policies. Underwriters remain policy-specific and roll up to the program as a read-only aggregate. The Workflow tab adds Checklist and Information Requests (Timeline and Activity remain in their own existing tabs). Files are program-scoped with no inheritance.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Contact storage | New `contact_program_assignments` junction table | Mirrors existing `contact_policy_assignments` pattern; no risk to existing contact logic |
| Inheritance model | Program contacts are source of truth | Program team (placement colleague, lead broker, etc.) push down to all child policies |
| Underwriter flow | Bottom-up rollup, read-only at program | Underwriters are always per-policy (per carrier/layer); program shows aggregate |
| Policy contact coexistence | Program wins for non-underwriter roles | All roles except "Underwriter" are team roles managed at program level. Underwriters are always policy-specific. |
| Workflow tab scope | Checklist + Information Requests only | Timeline and Activity already have dedicated program tabs |
| File inheritance | None — program files are program-scoped | Keeps it simple; no cross-level file visibility |
| Architecture approach | New junction table (not polymorphic, not extended existing table) | Clean separation, follows existing patterns, zero migration risk |

## Data Model

### New Table: `contact_program_assignments`

```sql
CREATE TABLE IF NOT EXISTS contact_program_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    role TEXT,
    title TEXT,
    notes TEXT,
    is_placement_colleague INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, program_id)
);
```

No changes to `contact_policy_assignments`, `contacts`, or `programs` tables.

### Additional Schema Changes

```sql
-- Allow milestones to be scoped to programs
ALTER TABLE policy_milestones ADD COLUMN program_id INTEGER REFERENCES programs(id);

-- Allow RFI bundles to be scoped to programs
ALTER TABLE client_request_bundles ADD COLUMN program_uid TEXT;
```

Both columns are nullable. Existing rows are unaffected. Program-level milestones have `program_id` set and `policy_id` NULL. Program-level RFI bundles have `program_uid` set.

### Migration

Single migration file adds the new `contact_program_assignments` table plus the two `ALTER TABLE` columns. No data migration needed — programs currently store `lead_broker` and `placement_colleague` as plain text fields. These text fields remain for backward compatibility but the new structured contacts take precedence for token resolution and display.

## Inheritance Flow

### Top-Down: Program → Child Policies

When rendering a policy's contacts (on the policy Contacts tab):

1. Check if `policy.program_id` is set
2. If yes, fetch `contact_program_assignments` for that program
3. Display program contacts with a **"PGM" badge**, read-only at policy level
4. Display the policy's own contacts (underwriters) as editable, below the inherited section

### Bottom-Up: Policy Underwriters → Program

When rendering the program's Contacts tab:

1. Display program team contacts (editable) — the team matrix
2. Below that, display an **"Underwriters" section** that aggregates all underwriter contacts from child policies
3. Each underwriter row shows: contact name, email, carrier (from policy), policy UID — all read-only
4. Clicking an underwriter row links to the source policy

### Behavior Matrix

| Scenario | Behavior |
|----------|----------|
| Add contact to program | Appears on all child policies with PGM badge, read-only |
| Remove contact from program | Disappears from all child policies |
| Edit program contact role/notes | Changes reflect on all child policies immediately |
| View policy contacts (in program) | Program team (read-only, badged) + policy underwriters (editable) |
| View policy contacts (standalone) | No change — works exactly as today |
| View program contacts | Program team (editable) + underwriter rollup (read-only) |
| Compose email from program | Uses `program_context()` tokens |
| Policy moved into a program | Gains program contacts automatically on next render |
| Policy removed from a program | Loses inherited contacts on next render |

## New Program Tabs

### Updated Tab Bar

```
Overview | Schematic | Timeline | Contacts | Workflow | Files | Activity
                                 ^^^^^^^^   ^^^^^^^^   ^^^^^
                                    NEW        NEW       NEW
```

### Contacts Tab (`/programs/{program_uid}/tab/contacts`)

**Sections:**

1. **Team Matrix** — Editable contenteditable table following the policy `_policy_team.html` pattern
   - Columns: Name (combobox), Role (combobox), Title, Email, Phone, Notes
   - Add row button, save on blur via PATCH
   - Compose icon next to contacts with email addresses

2. **Underwriter Rollup** — Read-only table aggregated from child policies
   - Columns: Contact Name, Email, Phone, Carrier, Policy UID
   - Each row links to the source policy
   - Styled with muted background to distinguish from editable section

3. **Correspondence** — Activity log filtered to this program's contacts (same as policy contacts tab)

### Workflow Tab (`/programs/{program_uid}/tab/workflow`)

**Sections:**

1. **Renewal Checklist** — Program-level milestones stored in `policy_milestones` with a new nullable `program_id` column (migration adds `ALTER TABLE policy_milestones ADD COLUMN program_id INTEGER REFERENCES programs(id)`). Rows with `program_id` set and `policy_id` NULL are program-level milestones. Same toggle/completion UI as policy workflow. Uses program's `milestone_profile` for template.

2. **Information Requests** — RFI bundles scoped to program. The `client_request_bundles` table gets a new nullable `program_uid` column (migration adds `ALTER TABLE client_request_bundles ADD COLUMN program_uid TEXT`). Bundles with `program_uid` set are program-level. Loads via `/clients/{client_id}/requests/program-view?program_uid={program_uid}`. Same UI pattern as policy workflow RFIs.

### Files Tab (`/programs/{program_uid}/tab/files`)

- Uses existing attachment panel component
- `record_type='program'`, `record_id=program.id`
- No inheritance — program files are self-contained
- Same add/remove/download UI as policy files

## Email Template Integration

### New Context Function: `program_context()`

```python
def program_context(conn, program_uid):
    """Build token context for a program."""
    # Program fields
    # {{program_name}}, {{program_uid}}, {{line_of_business}}
    # {{effective_date}}, {{expiration_date}}, {{renewal_status}}

    # Program contacts (from contact_program_assignments)
    # {{placement_colleague}}, {{placement_colleague_email}}, {{placement_colleague_phone}}
    # {{lead_broker}}, {{lead_broker_email}}, {{lead_broker_phone}}
    # {{account_exec}}

    # Client fields (inherited from program's client)
    # {{client_name}}, {{primary_contact}}, {{primary_email}}
    # {{contact_phone}}, {{contact_organization}}

    # Aggregated from child policies
    # {{carriers}} — comma-separated unique carriers
```

### CONTEXT_TOKEN_GROUPS Update

Add a `"program"` context key to `CONTEXT_TOKEN_GROUPS` with groups:

- **Program** — program_name, program_uid, line_of_business, effective_date, expiration_date, renewal_status
- **Program Team** — placement_colleague, placement_colleague_email, placement_colleague_phone, lead_broker, lead_broker_email, lead_broker_phone, account_exec
- **Client** — client_name, primary_contact, primary_email, contact_phone, contact_organization
- **Aggregated** — carriers

### Compose Panel Integration

- Compose button appears next to each contact with an email on the Contacts tab
- `GET /compose?context=program&program_uid={uid}` loads the compose slideover
- Compose route resolves `program_context()` for token rendering
- Email subject template: add `email_subject_program` config key with `{{program_name}}` default

### Policy Token Resolution Update

When `policy_context()` resolves placement colleague and lead broker tokens, it should check:

1. If policy belongs to a program → resolve from `contact_program_assignments` (program wins)
2. If no program → resolve from `contact_policy_assignments` (current behavior)

This ensures email templates sent from a policy in a program use the correct program-level contacts.

## Query Functions

### New Functions in `queries.py`

- `get_program_contacts(conn, program_id)` — fetch all `contact_program_assignments` joined with `contacts`
- `assign_contact_to_program(conn, program_id, contact_id, role, ...)` — insert into junction table
- `update_program_contact_assignment(conn, assignment_id, ...)` — update role, title, notes
- `remove_program_contact_assignment(conn, assignment_id)` — delete from junction table
- `get_program_underwriter_rollup(conn, program_id)` — aggregate underwriter contacts from child policies

### Updated Functions

- `get_policy_contacts(conn, policy_id)` — add logic to also fetch inherited program contacts when `policy.program_id` is set, with `source='program'` flag on each row

## Route Endpoints

### New Routes in `programs.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/programs/{program_uid}/tab/contacts` | Contacts tab content |
| GET | `/programs/{program_uid}/tab/workflow` | Workflow tab content |
| GET | `/programs/{program_uid}/tab/files` | Files tab content |
| POST | `/programs/{program_uid}/team/add` | Add contact to program |
| PATCH | `/programs/{program_uid}/team/{assignment_id}/cell` | Edit contact cell |
| POST | `/programs/{program_uid}/team/{assignment_id}/delete` | Remove contact |

### Updated Routes

- `GET /compose` — accept `context=program&program_uid={uid}` parameter
- `GET /compose/render` — resolve `program_context()` when context is program

## Templates

### New Templates

- `programs/_tab_contacts.html` — Contacts tab (team matrix + underwriter rollup + correspondence)
- `programs/_tab_workflow.html` — Workflow tab (checklist + RFIs)
- `programs/_tab_files.html` — Files tab (attachment panel)
- `programs/_team_matrix_row.html` — Editable row for program team matrix
- `programs/_underwriter_rollup.html` — Read-only underwriter aggregate section

### Updated Templates

- `programs/detail.html` — Add Contacts, Workflow, Files tabs to tab bar
- `policies/_tab_contacts.html` — Show inherited program contacts (read-only, PGM badge) above policy contacts
- `policies/_team_matrix_row.html` — Add read-only variant for inherited contacts

## Config Changes

- Add `email_subject_program` to `_DEFAULTS` in `config.py` — default: `"{{program_name}} — {{client_name}}"`
- Add to `EDITABLE_LISTS` in `settings.py` for Settings UI

## Files Changed

| File | Change |
|------|--------|
| `migrations/NNN_program_contacts.sql` | New `contact_program_assignments` table + `program_id` on `policy_milestones` + `program_uid` on `client_request_bundles` |
| `db.py` | Wire migration |
| `queries.py` | New program contact query functions + update `get_policy_contacts` |
| `web/routes/programs.py` | New tab routes + contact CRUD endpoints |
| `web/routes/compose.py` | Accept program context |
| `email_templates.py` | `program_context()` + update `CONTEXT_TOKEN_GROUPS` + update `policy_context()` |
| `config.py` | Add `email_subject_program` default |
| `web/routes/settings.py` | Add to `EDITABLE_LISTS` |
| `web/templates/programs/detail.html` | Add 3 new tabs to tab bar |
| `web/templates/programs/_tab_contacts.html` | New — contacts tab |
| `web/templates/programs/_tab_workflow.html` | New — workflow tab |
| `web/templates/programs/_tab_files.html` | New — files tab |
| `web/templates/programs/_team_matrix_row.html` | New — editable row |
| `web/templates/programs/_underwriter_rollup.html` | New — read-only rollup |
| `web/templates/policies/_tab_contacts.html` | Show inherited program contacts |
| `web/templates/policies/_team_matrix_row.html` | Read-only variant for inherited |
