# Timesheet Review — UX Sweep Design

**Status:** Approved
**Date:** 2026-04-17
**Follows:** `2026-04-16-timesheet-review-design.md` (Phase 4 — built in PR #271)

## Problem

The Timesheet Review page shipped in PR #271 gets the hours right but makes reviewing them harder than it should be:

1. **Context is invisible.** Each activity row shows `subject · type · hours` and nothing else. The reviewer can't tell which **client**, **policy**, **project/location**, or **issue** the work is logged against without clicking through.
2. **Contenteditable hours feel off.** No visual affordance that the cell is editable; the display format (`2.00`) doesn't match what the user types (`2`); saves land silently with no flash and no day-total refresh.
3. **Two holdovers that duck the platform UI standards.** The range picker uses `window.prompt()`; the add-activity form uses raw `<input>` / `<select>` instead of the combobox pattern used elsewhere.

## Goal

A single sweep that makes the review page scan cleanly and behave like the rest of PolicyDB. No new features — all changes are UX-level on work the user already logs.

## Changes

### 1 · Activity row — context pills (inline, color-coded)

Each `.activity-row` renders up to four clickable pills to the left of the subject, using the existing `.pill` + `_ref_tag_pill` style vocabulary:

| Pill    | When shown              | Label format             | Click destination                                     |
| ------- | ----------------------- | ------------------------ | ----------------------------------------------------- |
| Client  | `client_id IS NOT NULL` | client name              | `/clients/{client_id}`                                |
| Policy  | `policy_id IS NOT NULL` | `POL-###`                | `/policies/{policy_uid}/edit`                         |
| Project | `project_id IS NOT NULL`| `LOC · {project_name}`   | `/clients/{client_id}/projects/{project_id}`          |
| Issue   | `issue_id IS NOT NULL`  | `{issue_uid}`            | `/issues/{issue_uid}`                                 |

Colors (from the Option A mockup):
- Client: blue `#E8EDFF` / border `#BFCCFF` / text `#0B4BFF`
- Policy: green `#E6F4EA` / border `#BBDFC6` / text `#15803d`
- Project: amber `#FFF7E0` / border `#F1E2A6` / text `#92400e`
- Issue: rose `#FDECEC` / border `#F5C2C2` / text `#991b1b`

Pills are **read-only jumps**. Clicking navigates — no inline re-linking in this sweep. Missing context does not render a placeholder pill.

Order: Client → Project → Policy → Issue (broad-to-specific).

### 2 · Activity row — contenteditable affordance, format, feedback

**Affordance.** Subject and hours cells get:
- Idle: no border, no cursor change (matches the app's other contenteditable tables).
- Hover: `border-bottom: 1px dashed #d6d3d1` + `cursor: text` on the cell.
- Focus: `border-bottom: 1px solid #0B4BFF` + subtle `background: #fffefb`.
- Empty placeholder via `data-placeholder` + `::before` CSS — subject shows "What did you work on?", hours shows "—".

**Format.** The hours cell displays via the existing `_fmt_hours` helper (registered as a Jinja global in `app.py`) rather than a hand-rolled `%.2f`. This gives `1.0 → "1"`, `1.5 → "1.5"`, `0.75 → "0.75"`, `None → "—"` — trailing zeros stripped. Storage is unchanged (still rounded to 0.1 on save per `_round_to_tenth`).

**Save feedback.** The existing `PATCH /timesheet/activity/{id}` already returns JSON `{ok, formatted, total_hours}`. Wire it up client-side — the response contract is unchanged:

- On HTMX `htmx:afterRequest`, read the JSON response; if `ok`, call the shared `flashCell()` helper on the edited element (consistent with the client and policy contenteditable tables).
- Replace the hours cell text with `response.formatted` so display matches DB exactly.
- Update the day card's total-hours span using `response.total_hours` by writing into `closest('.day-card') .day-tot`.

No OOB swap, no HTML returned — keeps the endpoint JSON-only and all DOM mutation in one small `timesheet.js` handler.

### 3 · Range picker — popover with presets

Replace the `onclick="const s=prompt(...)"` button with a click-to-open popover anchored under the `Range` segment. Contents:

- Preset chips: **This week**, **Last week**, **MTD**, **Last 30 days**, **Custom**.
- Two native `<input type="date">` fields (start → end) that activate when Custom is chosen.
- Cancel / Apply buttons. Apply fires the same `GET /timesheet/panel?kind=range&start=&end=` HTMX swap as today.

Popover component lives in `timesheet/_range_popover.html` and is rendered inline (dropped into the panel header). No new JS framework — a `data-open` attribute toggled by a small inline click handler, closed on outside click and on ESC.

### 4 · Add-activity form — cascading combobox

Replace `_add_activity_form.html` with a combobox-based layout. Fields:

| Field          | Required | Behavior                                                              |
| -------------- | -------- | --------------------------------------------------------------------- |
| Client         | Yes      | Typeahead combobox over `SELECT id, name FROM clients`.               |
| Policy         | No       | Cascading — filtered to `policies WHERE client_id = :client_id`.      |
| Project        | No       | Cascading — filtered to `projects WHERE client_id = :client_id`.      |
| Issue          | No       | Cascading — open issues tied to client (`item_kind='issue' AND follow_up_done=0`); issues tied to the chosen policy float first. |
| Activity type  | Yes      | Stays native `<select>` — not in scope per user decision.             |
| Subject        | Yes      | Plain text input.                                                     |
| Hours          | No       | Numeric input, `inputmode="decimal"`.                                 |

Combobox implementation uses the same `data-combobox` wiring already used across the app (see `_combobox.html` partial). No new JS library.

**Backend.** Extend `POST /timesheet/activity` to accept optional `project_id` and `issue_id` form fields, validated against the chosen `client_id`:

- `project_id` must belong to `client_id` (join `projects`).
- `issue_id` must be an `activity_log` row with `item_kind='issue'` and the same `client_id`.

On mismatch → `HTTPException(400)`.

### 5 · Payload additions

`timesheet.py::_load_activities` extends its SELECT + joins:

```sql
SELECT a.id, a.activity_date, a.activity_type, a.subject,
       a.duration_hours, a.reviewed_at, a.source, a.follow_up_done,
       a.item_kind, a.client_id, a.policy_id, a.project_id, a.issue_id,
       a.details,
       c.name  AS client_name,
       p.policy_uid, p.policy_type,
       pr.name AS project_name,
       iss.issue_uid, iss.subject AS issue_subject
  FROM activity_log a
  LEFT JOIN clients      c  ON c.id  = a.client_id
  LEFT JOIN policies     p  ON p.id  = a.policy_id
  LEFT JOIN projects     pr ON pr.id = a.project_id
  LEFT JOIN activity_log iss ON iss.id = a.issue_id AND iss.item_kind = 'issue'
 WHERE a.activity_date BETWEEN ? AND ?
 ORDER BY a.activity_date, a.id
```

The per-activity dict in `build_timesheet_payload` gains:

- `client_href`  — `/clients/{client_id}`
- `policy_uid`, `policy_type`, `policy_href` (`/policies/{policy_uid}/edit`)
- `project_id`, `project_name`, `project_href` (`/clients/{client_id}/projects/{project_id}`)
- `issue_uid`, `issue_subject`, `issue_href` (`/issues/{issue_uid}`)

## Out of scope

- Activity type combobox on either the row or the add form. User didn't flag it; keeping scope tight.
- Row-level re-linking of client / policy / project / issue. Pills are read-only jumps.
- Inline delete confirmation (continues to use HTMX `hx-confirm`).
- Phase 5 working-hours gap detection and hour-estimate features deferred in the original design.

## File changes

| File                                                         | Change                                                               |
| ------------------------------------------------------------ | -------------------------------------------------------------------- |
| `src/policydb/timesheet.py`                                  | Extended SELECT + per-activity dict keys.                            |
| `src/policydb/web/routes/timesheet.py`                       | `POST /activity` accepts `project_id` / `issue_id` with validation. `PATCH /activity/{id}` response contract unchanged. |
| `src/policydb/web/templates/timesheet/_activity_row.html`    | Adds pills; contenteditable affordance classes + `data-placeholder`. |
| `src/policydb/web/templates/timesheet/_panel.html`           | Range segment becomes a popover trigger; renders `_range_popover`.   |
| `src/policydb/web/templates/timesheet/_range_popover.html`   | **New** — preset chips + custom dates + Apply.                       |
| `src/policydb/web/templates/timesheet/_add_activity_form.html` | Cascading combobox layout (client / policy / project / issue).      |
| `src/policydb/web/static/js/timesheet.js` (or inline in `_panel`) | Small handlers — flashCell + hours/day-total write-back on PATCH JSON, range-popover toggle, combobox cascade. Reuse existing `_combobox.html` wiring where possible. |

No migrations. No config keys. No new dependencies.

## Testing

- **Unit.** `_load_activities` returns the new fields with joined labels; missing context resolves to None.
- **Route.** `POST /timesheet/activity` rejects a `project_id` not owned by the chosen `client_id`; same for `issue_id`. Existing `client_id` + `policy_id` paths still pass.
- **Route.** `PATCH /timesheet/activity/{id}` response still returns `{ok, formatted, total_hours}` — contract unchanged for existing callers.
- **Template.** `_activity_row.html` renders only the pills whose underlying ids are set; renders `None` gracefully for fully-unlinked activities (client pill always present — `client_id` is NOT NULL in the schema).
- **Manual QA.** Required (per CLAUDE.md "QA Testing Requirement"):
  - Pills render with the right colors / links in at least one row that has all four context types, and one that has client-only.
  - Subject blur after edit flashes green; hours blur formats `2` → `2`, `1.50` → `1.5`, `.75` → `0.75`; day total updates inline.
  - Range popover opens, presets work, custom dates apply, ESC + outside-click close it.
  - Add-activity form: choosing a client filters the policy / project / issue lists; changing client clears them; submit writes the correct ids.

## Risks & mitigations

- **Row width on narrow screens.** Four pills + subject + type + hours can overflow. Subject already uses `truncate`; we keep it. If a row has every context, pills wrap to a second line via `flex-wrap`. Accepted — not introducing a collapse.
- **Combobox data volume for the add form.** Issue dropdowns on a large client could return dozens of rows. Mitigation: filter to `follow_up_done = 0` (open only) and cap at 50, same as the existing `client_list` cap of 500.
- **Cascading-clear behavior.** Changing client mid-entry must clear the policy / project / issue fields; otherwise the backend will 400 on validation. The form handler listens for `change` on the client combobox and resets the three dependent inputs.

## Success criteria

When I open the timesheet review I can tell — without clicking — which client, policy, project, and issue each activity is tied to. Editing hours and subjects feels identical to editing a client contact table: clear focus affordance, green flash on save, day totals refresh inline. Opening a non-week range and adding a new activity both feel like the rest of the app, not a one-off.
