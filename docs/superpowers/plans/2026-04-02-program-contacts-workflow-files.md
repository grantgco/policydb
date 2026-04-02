# Program Contacts, Workflow & Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Contacts, Workflow, and Files tabs to Program detail pages with contact inheritance (program→policy) and underwriter rollup (policy→program).

**Architecture:** New `contact_program_assignments` junction table mirrors the policy contact pattern. Program contacts inherit down to child policies (read-only with PGM badge). Underwriters remain policy-specific and roll up as read-only aggregates. Workflow tab adds checklist + RFIs using a new `program_milestones` table. Files use existing polymorphic attachment system.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, contenteditable matrix pattern

**Spec:** `docs/superpowers/specs/2026-04-02-program-contacts-workflow-files-design.md`

**Spec deviation:** Spec calls for `ALTER TABLE policy_milestones ADD COLUMN program_id`, but `policy_milestones.policy_uid` has a `NOT NULL` constraint — program milestones can't use that table without a separate migration to relax the constraint. Plan uses a separate `program_milestones` table instead (same schema, keyed on `program_uid`).

---

### Task 1: Migration — Schema Changes

**Files:**
- Create: `src/policydb/migrations/123_program_contacts.sql`
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Create migration SQL file**

```sql
-- Program contact assignments (mirrors contact_policy_assignments)
CREATE TABLE IF NOT EXISTS contact_program_assignments (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id             INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    program_id             INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    role                   TEXT,
    title                  TEXT,
    notes                  TEXT,
    is_placement_colleague INTEGER DEFAULT 0,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, program_id)
);

CREATE INDEX IF NOT EXISTS idx_cpa_program_id ON contact_program_assignments(program_id);
CREATE INDEX IF NOT EXISTS idx_cpa_contact_id_prog ON contact_program_assignments(contact_id);

-- Program milestones (mirrors policy_milestones but keyed on program_uid)
CREATE TABLE IF NOT EXISTS program_milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uid  TEXT NOT NULL,
    milestone    TEXT NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at DATETIME,
    UNIQUE(program_uid, milestone)
);

CREATE INDEX IF NOT EXISTS idx_program_milestones_uid ON program_milestones(program_uid);

-- Add program_uid to client_request_bundles for program-scoped RFIs
ALTER TABLE client_request_bundles ADD COLUMN program_uid TEXT;
```

Write this to `src/policydb/migrations/123_program_contacts.sql`.

- [ ] **Step 2: Wire migration into db.py**

In `src/policydb/db.py`, add `123` to the `_KNOWN_MIGRATIONS` set, then add the migration block inside `init_db()` after the last migration block (122):

```python
if 123 not in applied:
    sql = (_MIGRATIONS_DIR / "123_program_contacts.sql").read_text()
    conn.executescript(sql)
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (123, "Program contacts, milestones, and RFI program_uid"),
    )
    conn.commit()
```

- [ ] **Step 3: Verify migration runs**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.db import init_db; import sqlite3; conn = sqlite3.connect(':memory:'); init_db(conn)"`

Expected: No errors. The in-memory DB applies all migrations including 123.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/123_program_contacts.sql src/policydb/db.py
git commit -m "feat: add migration 123 — program contacts, milestones, RFI scoping"
```

---

### Task 2: Query Functions for Program Contacts

**Files:**
- Modify: `src/policydb/queries.py`

- [ ] **Step 1: Add program contact query functions**

Add these functions to `src/policydb/queries.py` near the existing `get_policy_contacts` function (around line 1250):

```python
def get_program_contacts(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Return contacts assigned to a program via contact_program_assignments."""
    rows = conn.execute(
        """SELECT cpa.id AS assignment_id, co.id AS contact_id,
                  co.name, co.email, co.phone, co.mobile, co.organization,
                  cpa.role, cpa.title, cpa.notes, cpa.is_placement_colleague
           FROM contact_program_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.program_id = ?
           ORDER BY cpa.role, co.name""",
        (program_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["id"] = d["assignment_id"]
        result.append(d)
    return result


def assign_contact_to_program(conn: sqlite3.Connection, contact_id: int, program_id: int, **fields) -> int:
    """Create or update a contact-program assignment. Returns assignment id."""
    existing = conn.execute(
        "SELECT id FROM contact_program_assignments WHERE contact_id=? AND program_id=?",
        (contact_id, program_id),
    ).fetchone()
    if existing:
        updates = []
        params = []
        for field in ("role", "title", "notes", "is_placement_colleague"):
            if field in fields:
                updates.append(f"{field}=?")
                params.append(fields[field])
        if updates:
            params.append(existing["id"])
            conn.execute(f"UPDATE contact_program_assignments SET {', '.join(updates)} WHERE id=?", params)
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO contact_program_assignments
               (contact_id, program_id, role, title, notes, is_placement_colleague)
               VALUES (?,?,?,?,?,?)""",
            (contact_id, program_id,
             fields.get("role"), fields.get("title"), fields.get("notes"),
             fields.get("is_placement_colleague", 0)),
        )
        return cur.lastrowid


def remove_contact_from_program(conn: sqlite3.Connection, assignment_id: int) -> None:
    """Delete a contact-program assignment."""
    conn.execute("DELETE FROM contact_program_assignments WHERE id=?", (assignment_id,))


def set_program_placement_colleague(conn: sqlite3.Connection, assignment_id: int) -> None:
    """Toggle is_placement_colleague on a program contact assignment."""
    current = conn.execute(
        "SELECT is_placement_colleague FROM contact_program_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if current:
        new_val = 0 if current["is_placement_colleague"] else 1
        conn.execute(
            "UPDATE contact_program_assignments SET is_placement_colleague=? WHERE id=?",
            (new_val, assignment_id),
        )


def get_program_underwriter_rollup(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """Aggregate underwriter contacts from all child policies of a program."""
    rows = conn.execute(
        """SELECT DISTINCT co.id AS contact_id, co.name, co.email, co.phone, co.mobile,
                  p.carrier, p.policy_uid,
                  cpa.role, cpa.title
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.program_id = ?
             AND p.archived = 0
             AND LOWER(COALESCE(cpa.role, '')) IN ('underwriter', 'uw')
           ORDER BY p.carrier, co.name""",
        (program_id,),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 2: Add imports to the top of queries.py if needed**

No new imports needed — `sqlite3` is already imported.

- [ ] **Step 3: Verify functions load without errors**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.queries import get_program_contacts, assign_contact_to_program, remove_contact_from_program, set_program_placement_colleague, get_program_underwriter_rollup; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: add program contact query functions (CRUD + underwriter rollup)"
```

---

### Task 3: Program Detail Template — Add Tab Buttons

**Files:**
- Modify: `src/policydb/web/templates/programs/detail.html`

- [ ] **Step 1: Add Contacts, Workflow, and Files tab buttons to the tab bar**

In `src/policydb/web/templates/programs/detail.html`, find the tab bar section that contains the 4 existing tab buttons (Overview, Schematic, Timeline, Activity). Add 3 new buttons between Timeline and Activity:

Find the existing tab bar buttons that look like:
```html
    <button class="tab-btn" data-tab="timeline"
      data-tab-url="/programs/{{ program.program_uid }}/tab/timeline">Timeline</button>
    <button class="tab-btn" data-tab="activity"
      data-tab-url="/programs/{{ program.program_uid }}/tab/activity">Activity</button>
```

Replace with:
```html
    <button class="tab-btn" data-tab="timeline"
      data-tab-url="/programs/{{ program.program_uid }}/tab/timeline">Timeline</button>
    <button class="tab-btn" data-tab="contacts"
      data-tab-url="/programs/{{ program.program_uid }}/tab/contacts">Contacts</button>
    <button class="tab-btn" data-tab="workflow"
      data-tab-url="/programs/{{ program.program_uid }}/tab/workflow">Workflow</button>
    <button class="tab-btn" data-tab="files"
      data-tab-url="/programs/{{ program.program_uid }}/tab/files">Files</button>
    <button class="tab-btn" data-tab="activity"
      data-tab-url="/programs/{{ program.program_uid }}/tab/activity">Activity</button>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/programs/detail.html
git commit -m "feat: add Contacts, Workflow, Files tab buttons to program detail"
```

---

### Task 4: Program Contacts Tab — Route + Team Template

**Files:**
- Modify: `src/policydb/web/routes/programs.py`
- Create: `src/policydb/web/templates/programs/_tab_contacts.html`
- Create: `src/policydb/web/templates/programs/_program_team.html`
- Create: `src/policydb/web/templates/programs/_team_matrix_row.html`
- Create: `src/policydb/web/templates/programs/_underwriter_rollup.html`

- [ ] **Step 1: Add imports to programs.py**

At the top of `src/policydb/web/routes/programs.py`, update the imports from `policydb.queries`:

```python
from policydb.queries import (
    get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id,
    get_program_by_uid, get_program_child_policies, get_program_aggregates,
    get_unassigned_policies, get_programs_for_project,
    get_program_timeline_milestones, get_program_activities,
    renew_policy,
    get_or_create_contact, get_program_contacts, assign_contact_to_program,
    remove_contact_from_program, set_program_placement_colleague,
    get_program_underwriter_rollup,
)
```

Also add `Form` to the FastAPI imports if not already present (it is — already in the import line).

- [ ] **Step 2: Add the contacts tab route handler**

Add this route to `src/policydb/web/routes/programs.py` after the existing tab routes (after `program_tab_activity`):

```python
# ── Contacts tab ───────────────────────────────────────────────────────────

@router.get("/programs/{program_uid}/tab/contacts", response_class=HTMLResponse)
def program_tab_contacts(request: Request, program_uid: str, conn=Depends(get_db)):
    """Contacts tab: program team matrix + underwriter rollup + correspondence."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    program_contacts = get_program_contacts(conn, program["id"])

    # Attach expertise tags
    _pc_ids = [c["contact_id"] for c in program_contacts if c.get("contact_id")]
    if _pc_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in program_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])

    # Underwriter rollup from child policies
    underwriters = get_program_underwriter_rollup(conn, program["id"])

    # Autocomplete data for contact name combobox
    _ac_rows = conn.execute(
        """SELECT co.name, co.email, co.phone, co.mobile, co.organization,
                  MAX(COALESCE(cpa.role, cca.role)) AS role,
                  MAX(COALESCE(cpa.title, cca.title)) AS title
           FROM contacts co
           LEFT JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           LEFT JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.id ORDER BY co.name"""
    ).fetchall()
    import json as _json_mod
    all_contacts_for_ac_json = _json_mod.dumps({
        r["name"]: {
            "email": r["email"] or "", "role": r["role"] or "",
            "phone": r["phone"] or "", "mobile": r["mobile"] or "",
            "title": r["title"] or "", "organization": r["organization"] or "",
        } for r in _ac_rows
    })

    # Mailto subject
    from policydb.email_templates import render_tokens as _rtk
    _ctx = {"client_name": "", "program_name": program["name"] or ""}
    client_row = conn.execute("SELECT name FROM clients WHERE id=?", (program["client_id"],)).fetchone()
    if client_row:
        _ctx["client_name"] = client_row["name"]
    mailto_subject = _rtk(
        cfg.get("email_subject_program", "Re: {{client_name}} — {{program_name}}"),
        _ctx,
    )

    # Activity clusters for correspondence section
    _cluster_days = cfg.get("activity_cluster_days", 7)
    _all_acts = [dict(r) for r in conn.execute(
        """SELECT activity_date, activity_type, subject, disposition, details,
                  duration_hours, follow_up_done
           FROM activity_log WHERE program_id = ?
           ORDER BY activity_date DESC, id DESC""",
        (program["id"],),
    ).fetchall()]
    # Build clusters
    activity_clusters: list[list[dict]] = []
    if _all_acts:
        import dateutil.parser as _dp
        from datetime import timedelta
        current_cluster: list[dict] = [_all_acts[0]]
        for act in _all_acts[1:]:
            prev_date = current_cluster[-1].get("activity_date") or ""
            curr_date = act.get("activity_date") or ""
            try:
                gap = abs((_dp.parse(prev_date) - _dp.parse(curr_date)).days) if prev_date and curr_date else 999
            except Exception:
                gap = 999
            if gap <= _cluster_days:
                current_cluster.append(act)
            else:
                activity_clusters.append(current_cluster)
                current_cluster = [act]
        activity_clusters.append(current_cluster)

    return templates.TemplateResponse("programs/_tab_contacts.html", {
        "request": request,
        "program": program,
        "program_contacts": program_contacts,
        "underwriters": underwriters,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "mailto_subject": mailto_subject,
        "activity_clusters": activity_clusters,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute(
            "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
        ).fetchall()}),
    })
```

- [ ] **Step 3: Create `_tab_contacts.html` template**

Create `src/policydb/web/templates/programs/_tab_contacts.html`:

```html
{# Program Contacts Tab — team matrix + underwriter rollup + correspondence #}
<div class="py-4 space-y-6">

  {# ── Program Team Matrix ─────────────────────────────── #}
  {% include "programs/_program_team.html" %}

  {# ── Underwriter Rollup (read-only) ─────────────────── #}
  {% include "programs/_underwriter_rollup.html" %}

  {# ── Correspondence ──────────────────────────────────── #}
  <div class="mt-6">
    <h3 class="text-sm font-semibold text-[#3D3C37] mb-3">Correspondence</h3>
    {% if activity_clusters %}
      {% for cluster in activity_clusters %}
        <div class="mb-4 border-l-2 border-[#E8E4DE] pl-3">
          {% for act in cluster %}
            <div class="py-1 text-xs text-[#6B6962]">
              <span class="font-medium text-[#3D3C37]">{{ act.activity_date or '' }}</span>
              — {{ act.activity_type or '' }}{% if act.subject %}: {{ act.subject }}{% endif %}
              {% if act.disposition %}<span class="ml-1 text-[#8C8880]">({{ act.disposition }})</span>{% endif %}
            </div>
          {% endfor %}
        </div>
      {% endfor %}
    {% else %}
      <p class="text-xs text-[#8C8880] italic">No activity logged yet.</p>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 4: Create `_program_team.html` template**

Create `src/policydb/web/templates/programs/_program_team.html`:

```html
{# Program Team Matrix — editable contact table #}
<div id="program-team-wrap">
  <div class="flex items-center justify-between mb-2">
    <h3 class="text-sm font-semibold text-[#3D3C37]">Program Team</h3>
    <div class="flex items-center gap-2">
      {# Compose email button #}
      <button onclick="document.getElementById('compose-slideover')?.remove();
        htmx.ajax('GET', '/compose?context=program&program_uid={{ program.program_uid }}', {target: 'body', swap: 'beforeend'})"
        class="text-xs text-[#0B4BFF] hover:underline cursor-pointer no-print">
        ✉ Compose
      </button>
      <button onclick="window._programTeamMatrix?.addRow()"
        class="text-xs bg-[#0B4BFF] text-white px-2 py-0.5 rounded hover:bg-[#0940d4] no-print">
        + Add
      </button>
    </div>
  </div>

  <div class="overflow-x-auto">
    <table class="w-full text-xs border-collapse">
      <thead>
        <tr class="text-left text-[#8C8880] border-b border-[#E8E4DE]">
          <th class="py-1 px-2 font-medium" style="width:160px">Name</th>
          <th class="py-1 px-2 font-medium" style="width:120px">Org</th>
          <th class="py-1 px-2 font-medium" style="width:100px">Title</th>
          <th class="py-1 px-2 font-medium" style="width:130px">Role</th>
          <th class="py-1 px-2 font-medium" style="width:180px">Email</th>
          <th class="py-1 px-2 font-medium" style="width:110px">Phone</th>
          <th class="py-1 px-2 font-medium" style="width:110px">Mobile</th>
          <th class="py-1 px-2 font-medium" style="width:140px">Notes</th>
          <th class="py-1 px-2 font-medium no-print" style="width:28px">★</th>
          <th class="py-1 px-2 font-medium no-print" style="width:28px"></th>
        </tr>
      </thead>
      <tbody id="program-team-tbody">
        {% if program_contacts %}
          {% for c in program_contacts %}
            {% include "programs/_team_matrix_row.html" %}
          {% endfor %}
        {% endif %}
        <tr id="program-team-empty" {% if program_contacts %}class="hidden"{% endif %}>
          <td colspan="10" class="py-4 text-center text-xs text-[#8C8880] italic">No program contacts yet.</td>
        </tr>
      </tbody>
    </table>
  </div>

  {# Contact picker for autocomplete #}
  {% include "contacts/_picker.html" %}

  <script>
    window._programTeamMatrix = window.initMatrix({
      tbodyId: 'program-team-tbody',
      idAttr: 'data-row-id',
      rowClass: 'matrix-row',
      patchUrl: function(id) { return '/programs/{{ program.program_uid }}/team/' + id + '/cell'; },
      addRowUrl: '/programs/{{ program.program_uid }}/team/add-row',
      emptyRowId: 'program-team-empty',
    });
  </script>
</div>
```

- [ ] **Step 5: Create `_team_matrix_row.html` template**

Create `src/policydb/web/templates/programs/_team_matrix_row.html`:

```html
{# Program Team Matrix Row — editable contenteditable cells #}
<tr data-row-id="{{ c.id }}" class="matrix-row group border-b border-[#F0ECE6] hover:bg-[#FDFBF8]">
  {# Name — combobox with autocomplete #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="name"
         data-placeholder="Name"
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.name or '' }}</div>
    {% if c.get('expertise_lines') or c.get('expertise_industries') %}
      <div class="flex flex-wrap gap-0.5 mt-0.5">
        {% for tag in c.get('expertise_lines', []) %}
          <span class="text-[9px] bg-blue-100 text-blue-700 px-1 rounded">{{ tag }}</span>
        {% endfor %}
        {% for tag in c.get('expertise_industries', []) %}
          <span class="text-[9px] bg-green-100 text-green-700 px-1 rounded">{{ tag }}</span>
        {% endfor %}
      </div>
    {% endif %}
  </td>

  {# Organization — combobox #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="organization"
         data-placeholder="Org"
         data-combobox='{{ all_orgs | tojson }}'
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.organization or '' }}</div>
  </td>

  {# Title #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="title"
         data-placeholder="Title"
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.title or '' }}</div>
  </td>

  {# Role — combobox #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="role"
         data-placeholder="Role"
         data-combobox='{{ contact_roles | tojson }}'
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.role or '' }}</div>
  </td>

  {# Email — click-to-input with mailto link #}
  <td class="py-1 px-2">
    <div class="flex items-center gap-1">
      <div contenteditable="true" data-field="email"
           data-placeholder="Email"
           class="outline-none min-h-[1.4em] flex-1 focus:border-b focus:border-[#0B4BFF]"
      >{{ c.email or '' }}</div>
      {% if c.email %}
        <button onclick="document.getElementById('compose-slideover')?.remove();
          htmx.ajax('GET', '/compose?context=program&program_uid={{ program.program_uid }}&to_email={{ c.email | urlencode }}', {target: 'body', swap: 'beforeend'})"
          class="text-[#0B4BFF] hover:text-[#0940d4] opacity-0 group-hover:opacity-100 no-print"
          title="Compose email">✉</button>
      {% endif %}
    </div>
  </td>

  {# Phone #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="phone"
         data-placeholder="Phone"
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.phone or '' }}</div>
  </td>

  {# Mobile #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="mobile"
         data-placeholder="Mobile"
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.mobile or '' }}</div>
  </td>

  {# Notes #}
  <td class="py-1 px-2">
    <div contenteditable="true" data-field="notes"
         data-placeholder="Notes"
         class="outline-none min-h-[1.4em] focus:border-b focus:border-[#0B4BFF]"
    >{{ c.notes or '' }}</div>
  </td>

  {# Placement Colleague toggle #}
  <td class="py-1 px-2 text-center no-print">
    <button hx-post="/programs/{{ program.program_uid }}/team/{{ c.id }}/toggle-pc"
            hx-target="#program-team-wrap" hx-swap="outerHTML"
            class="text-sm {{ 'text-amber-500' if c.is_placement_colleague else 'text-gray-300 hover:text-amber-400' }}"
            title="Toggle placement colleague">★</button>
  </td>

  {# Delete #}
  <td class="py-1 px-2 text-center no-print">
    <button hx-post="/programs/{{ program.program_uid }}/team/{{ c.id }}/delete"
            hx-target="#program-team-wrap" hx-swap="outerHTML"
            hx-confirm="Remove this contact from the program?"
            class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100"
            title="Remove">✕</button>
  </td>
</tr>
```

- [ ] **Step 6: Create `_underwriter_rollup.html` template**

Create `src/policydb/web/templates/programs/_underwriter_rollup.html`:

```html
{# Underwriter Rollup — read-only aggregate from child policies #}
<div class="mt-6">
  <h3 class="text-sm font-semibold text-[#3D3C37] mb-2">Underwriters
    <span class="font-normal text-[#8C8880]">(from child policies)</span>
  </h3>

  {% if underwriters %}
    <div class="overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="text-left text-[#8C8880] border-b border-[#E8E4DE]">
            <th class="py-1 px-2 font-medium">Name</th>
            <th class="py-1 px-2 font-medium">Email</th>
            <th class="py-1 px-2 font-medium">Phone</th>
            <th class="py-1 px-2 font-medium">Carrier</th>
            <th class="py-1 px-2 font-medium">Policy</th>
          </tr>
        </thead>
        <tbody>
          {% for uw in underwriters %}
            <tr class="border-b border-[#F0ECE6] bg-[#FDFBF8]">
              <td class="py-1 px-2 text-[#3D3C37]">{{ uw.name or '' }}</td>
              <td class="py-1 px-2 text-[#6B6962]">
                {% if uw.email %}
                  <button onclick="document.getElementById('compose-slideover')?.remove();
                    htmx.ajax('GET', '/compose?context=program&program_uid={{ program.program_uid }}&to_email={{ uw.email | urlencode }}', {target: 'body', swap: 'beforeend'})"
                    class="text-[#0B4BFF] hover:underline no-print">{{ uw.email }}</button>
                {% endif %}
              </td>
              <td class="py-1 px-2 text-[#6B6962]">{{ uw.phone or '' }}</td>
              <td class="py-1 px-2 text-[#6B6962]">{{ uw.carrier or '' }}</td>
              <td class="py-1 px-2">
                <a href="/policies/{{ uw.policy_uid }}" class="text-[#0B4BFF] hover:underline">{{ uw.policy_uid }}</a>
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="text-xs text-[#8C8880] italic">No underwriters on child policies yet.</p>
  {% endif %}
</div>
```

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/programs.py \
        src/policydb/web/templates/programs/_tab_contacts.html \
        src/policydb/web/templates/programs/_program_team.html \
        src/policydb/web/templates/programs/_team_matrix_row.html \
        src/policydb/web/templates/programs/_underwriter_rollup.html
git commit -m "feat: program contacts tab with team matrix + underwriter rollup"
```

---

### Task 5: Program Contact CRUD Routes

**Files:**
- Modify: `src/policydb/web/routes/programs.py`

- [ ] **Step 1: Add helper function `_program_team_response`**

Add this helper function to `src/policydb/web/routes/programs.py`:

```python
def _program_team_response(request, conn, program_uid: str):
    """Return rendered _program_team.html partial (+ underwriter rollup)."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)

    program_contacts = get_program_contacts(conn, program["id"])

    # Attach expertise tags
    _pc_ids = [c["contact_id"] for c in program_contacts if c.get("contact_id")]
    if _pc_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in program_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])

    import json as _json_mod
    _ac_rows = conn.execute(
        """SELECT co.name, co.email, co.phone, co.mobile, co.organization,
                  MAX(COALESCE(cpa.role, cca.role)) AS role,
                  MAX(COALESCE(cpa.title, cca.title)) AS title
           FROM contacts co
           LEFT JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           LEFT JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.id ORDER BY co.name"""
    ).fetchall()
    all_contacts_for_ac_json = _json_mod.dumps({
        r["name"]: {
            "email": r["email"] or "", "role": r["role"] or "",
            "phone": r["phone"] or "", "mobile": r["mobile"] or "",
            "title": r["title"] or "", "organization": r["organization"] or "",
        } for r in _ac_rows
    })

    from policydb.email_templates import render_tokens as _rtk
    _ctx = {"client_name": "", "program_name": program["name"] or ""}
    client_row = conn.execute("SELECT name FROM clients WHERE id=?", (program["client_id"],)).fetchone()
    if client_row:
        _ctx["client_name"] = client_row["name"]
    mailto_subject = _rtk(
        cfg.get("email_subject_program", "Re: {{client_name}} — {{program_name}}"),
        _ctx,
    )

    return templates.TemplateResponse("programs/_program_team.html", {
        "request": request,
        "program": dict(program),
        "program_contacts": program_contacts,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "mailto_subject": mailto_subject,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute(
            "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
        ).fetchall()}),
    })
```

- [ ] **Step 2: Add add-row route**

```python
@router.post("/programs/{program_uid}/team/add-row", response_class=HTMLResponse)
def program_team_add_row(request: Request, program_uid: str, conn=Depends(get_db)):
    """Create blank program contact row and return matrix row HTML."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    cid = get_or_create_contact(conn, "New Contact")
    asg_id = assign_contact_to_program(conn, cid, program["id"])
    conn.commit()
    c = {"id": asg_id, "contact_id": cid, "name": "New Contact", "title": None, "role": None,
         "organization": None, "email": None, "phone": None, "mobile": None,
         "notes": None, "is_placement_colleague": 0}
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    return templates.TemplateResponse("programs/_team_matrix_row.html", {
        "request": request, "c": c, "program": dict(program),
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })
```

- [ ] **Step 3: Add cell PATCH route**

```python
@router.patch("/programs/{program_uid}/team/{contact_id}/cell")
async def program_team_cell(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Save a single cell value for a program contact (matrix edit)."""
    from policydb.utils import clean_email, format_phone
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"name", "organization", "title", "role", "email", "phone", "mobile", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    assignment_id = contact_id
    shared_fields = {"name", "email", "phone", "mobile", "organization"}
    assignment_fields = {"role", "title", "notes"}
    if field in shared_fields:
        asg = conn.execute(
            "SELECT contact_id FROM contact_program_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if asg:
            conn.execute(
                f"UPDATE contacts SET {field}=? WHERE id=?",
                (formatted or None, asg["contact_id"]),
            )
    elif field in assignment_fields:
        conn.execute(
            f"UPDATE contact_program_assignments SET {field}=? WHERE id=?",
            (formatted or None, assignment_id),
        )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})
```

- [ ] **Step 4: Add delete route**

```python
@router.post("/programs/{program_uid}/team/{contact_id}/delete", response_class=HTMLResponse)
def program_team_delete(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Remove a contact from the program team."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    remove_contact_from_program(conn, contact_id)
    conn.commit()
    return _program_team_response(request, conn, program_uid)
```

- [ ] **Step 5: Add toggle placement colleague route**

```python
@router.post("/programs/{program_uid}/team/{contact_id}/toggle-pc", response_class=HTMLResponse)
def program_team_toggle_pc(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Toggle is_placement_colleague flag on a program contact assignment."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    set_program_placement_colleague(conn, contact_id)
    conn.commit()
    return _program_team_response(request, conn, program_uid)
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/programs.py
git commit -m "feat: program contact CRUD routes (add, edit, delete, toggle PC)"
```

---

### Task 6: Policy Contacts — Show Inherited Program Contacts

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_contacts.html`
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Update policy contacts tab route to pass program contacts**

In `src/policydb/web/routes/policies.py`, in the `policy_tab_contacts` function (around line 2599), after fetching `policy_contacts`, add logic to fetch inherited program contacts:

```python
    # Inherited program contacts (if policy belongs to a program)
    inherited_contacts = []
    if policy_dict.get("program_id"):
        from policydb.queries import get_program_contacts as _gpc
        inherited_contacts = _gpc(conn, policy_dict["program_id"])
```

Then add `"inherited_contacts": inherited_contacts` to the template context dict returned by `templates.TemplateResponse(...)`.

Also add `"program": policy_dict` (it's already passed as `"policy"`) so the template can check `policy.program_id`.

- [ ] **Step 2: Update `_tab_contacts.html` to display inherited contacts**

In `src/policydb/web/templates/policies/_tab_contacts.html`, add an inherited contacts section **above** the existing Policy Team include. Insert this block before the `{% include "policies/_policy_team.html" %}` line:

```html
  {# ── Inherited Program Contacts (read-only) ──────────── #}
  {% if inherited_contacts %}
    <div class="mb-4">
      <h3 class="text-sm font-semibold text-[#3D3C37] mb-2">
        Program Team
        <span class="text-[10px] font-medium bg-[#0B4BFF] text-white px-1.5 py-0.5 rounded ml-1">PGM</span>
      </h3>
      <div class="overflow-x-auto">
        <table class="w-full text-xs border-collapse">
          <thead>
            <tr class="text-left text-[#8C8880] border-b border-[#E8E4DE]">
              <th class="py-1 px-2 font-medium">Name</th>
              <th class="py-1 px-2 font-medium">Role</th>
              <th class="py-1 px-2 font-medium">Email</th>
              <th class="py-1 px-2 font-medium">Phone</th>
            </tr>
          </thead>
          <tbody>
            {% for ic in inherited_contacts %}
              <tr class="border-b border-[#F0ECE6] bg-[#f0f4ff]">
                <td class="py-1 px-2 text-[#3D3C37]">
                  {{ ic.name or '' }}
                  <span class="text-[9px] bg-[#0B4BFF] text-white px-1 rounded ml-1">PGM</span>
                </td>
                <td class="py-1 px-2 text-[#6B6962]">{{ ic.role or '' }}</td>
                <td class="py-1 px-2 text-[#6B6962]">{{ ic.email or '' }}</td>
                <td class="py-1 px-2 text-[#6B6962]">{{ ic.phone or '' }}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      <p class="text-[10px] text-[#8C8880] mt-1 italic">Inherited from program — edit on the
        <a href="/programs/{{ policy.program_uid }}" class="text-[#0B4BFF] hover:underline">program page</a>.</p>
    </div>
  {% endif %}
```

Note: `policy.program_uid` may need to be resolved. Check if the policy dict includes `program_uid`. If not, add a lookup in the route handler:

```python
    program_uid_for_link = ""
    if policy_dict.get("program_id"):
        _prog_row = conn.execute("SELECT program_uid FROM programs WHERE id=?", (policy_dict["program_id"],)).fetchone()
        if _prog_row:
            program_uid_for_link = _prog_row["program_uid"]
```

And pass `"program_uid_for_link": program_uid_for_link` to the template. Then use `{{ program_uid_for_link }}` in the link instead.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/policies.py \
        src/policydb/web/templates/policies/_tab_contacts.html
git commit -m "feat: show inherited program contacts on policy contacts tab"
```

---

### Task 7: Workflow Tab — Route + Template

**Files:**
- Modify: `src/policydb/web/routes/programs.py`
- Create: `src/policydb/web/templates/programs/_tab_workflow.html`

- [ ] **Step 1: Add workflow tab route**

Add to `src/policydb/web/routes/programs.py`:

```python
@router.get("/programs/{program_uid}/tab/workflow", response_class=HTMLResponse)
def program_tab_workflow(request: Request, program_uid: str, conn=Depends(get_db)):
    """Workflow tab: checklist + information requests."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    # Program milestones (checklist)
    milestones_config = cfg.get("renewal_milestones", [])
    checklist = []
    existing = {r["milestone"]: dict(r) for r in conn.execute(
        "SELECT * FROM program_milestones WHERE program_uid=?", (program_uid,)
    ).fetchall()}

    for ms in milestones_config:
        if ms in existing:
            checklist.append(existing[ms])
        else:
            checklist.append({"id": None, "program_uid": program_uid, "milestone": ms, "completed": 0, "completed_at": None})

    return templates.TemplateResponse("programs/_tab_workflow.html", {
        "request": request,
        "program": program,
        "checklist": checklist,
    })
```

- [ ] **Step 2: Add milestone toggle route**

```python
@router.post("/programs/{program_uid}/milestone/toggle", response_class=HTMLResponse)
def program_milestone_toggle(
    request: Request,
    program_uid: str,
    milestone: str = Form(...),
    conn=Depends(get_db),
):
    """Toggle a program milestone completion status."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    existing = conn.execute(
        "SELECT id, completed FROM program_milestones WHERE program_uid=? AND milestone=?",
        (program_uid, milestone),
    ).fetchone()

    if existing:
        new_val = 0 if existing["completed"] else 1
        conn.execute(
            "UPDATE program_milestones SET completed=?, completed_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
            (new_val, new_val, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO program_milestones (program_uid, milestone, completed, completed_at) VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
            (program_uid, milestone),
        )
    conn.commit()

    # Return the full workflow tab
    return program_tab_workflow(request, program_uid, conn)
```

- [ ] **Step 3: Create `_tab_workflow.html` template**

Create `src/policydb/web/templates/programs/_tab_workflow.html`:

```html
{# Program Workflow Tab — Checklist + Information Requests #}
<div class="py-4 space-y-6">

  {# ── Renewal Checklist ──────────────────────────────── #}
  <div>
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-sm font-semibold text-[#3D3C37]">Renewal Checklist</h3>
      {% set done = checklist | selectattr('completed') | list | length %}
      {% set total = checklist | length %}
      {% if total %}
        <span class="text-xs text-[#8C8880]">{{ done }}/{{ total }} complete</span>
      {% endif %}
    </div>

    {% if checklist %}
      <div class="space-y-1">
        {% for ms in checklist %}
          <form hx-post="/programs/{{ program.program_uid }}/milestone/toggle"
                hx-target="closest div.py-4"
                hx-swap="outerHTML"
                class="flex items-center gap-2 py-1 px-2 rounded hover:bg-[#FDFBF8]">
            <input type="hidden" name="milestone" value="{{ ms.milestone }}">
            <button type="submit"
                    class="w-4 h-4 rounded border flex items-center justify-center text-xs
                           {{ 'bg-[#0B4BFF] border-[#0B4BFF] text-white' if ms.completed else 'border-[#C8C4BD] text-transparent hover:border-[#0B4BFF]' }}">
              {% if ms.completed %}✓{% endif %}
            </button>
            <span class="text-xs {{ 'text-[#8C8880] line-through' if ms.completed else 'text-[#3D3C37]' }}">
              {{ ms.milestone }}
            </span>
            {% if ms.completed_at %}
              <span class="text-[10px] text-[#8C8880] ml-auto">{{ ms.completed_at[:10] }}</span>
            {% endif %}
          </form>
        {% endfor %}
      </div>
    {% else %}
      <p class="text-xs text-[#8C8880] italic">No milestones configured. Add items in Settings → Renewal Checklist.</p>
    {% endif %}
  </div>

  {# ── Information Requests ───────────────────────────── #}
  <div>
    <h3 class="text-sm font-semibold text-[#3D3C37] mb-2">Information Requests</h3>
    <div hx-get="/clients/{{ program.client_id }}/requests/program-view?program_uid={{ program.program_uid }}"
         hx-trigger="load" hx-swap="innerHTML">
      <p class="text-xs text-[#8C8880] italic">Loading…</p>
    </div>
  </div>

</div>
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/programs.py \
        src/policydb/web/templates/programs/_tab_workflow.html
git commit -m "feat: program workflow tab with checklist + information requests"
```

---

### Task 8: Files Tab — Route + Template

**Files:**
- Modify: `src/policydb/web/routes/programs.py`
- Create: `src/policydb/web/templates/programs/_tab_files.html`

- [ ] **Step 1: Add files tab route**

Add to `src/policydb/web/routes/programs.py`:

```python
@router.get("/programs/{program_uid}/tab/files", response_class=HTMLResponse)
def program_tab_files(request: Request, program_uid: str, conn=Depends(get_db)):
    """Files tab: universal attachment panel for program."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    return templates.TemplateResponse("programs/_tab_files.html", {
        "request": request,
        "program": program,
    })
```

- [ ] **Step 2: Create `_tab_files.html` template**

Create `src/policydb/web/templates/programs/_tab_files.html`:

```html
{# Program Files Tab — lazy-loads the universal attachment panel #}
<div class="py-4">
  <div hx-get="/api/attachments/panel?record_type=program&record_id={{ program.id }}"
       hx-trigger="load" hx-swap="innerHTML">
    <p class="text-xs text-[#8C8880] italic">Loading…</p>
  </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/programs.py \
        src/policydb/web/templates/programs/_tab_files.html
git commit -m "feat: program files tab using universal attachment panel"
```

---

### Task 9: Email Template Integration — program_context()

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add `program_context()` function**

Add this function to `src/policydb/email_templates.py` near the existing `policy_context()` function:

```python
def program_context(conn: sqlite3.Connection, program_uid: str) -> dict:
    """Build token context for a program — program fields, contacts, client, aggregates."""
    from policydb.queries import get_program_by_uid, get_program_contacts, get_program_child_policies

    program = get_program_by_uid(conn, program_uid)
    if not program:
        return {}

    # Client tokens
    ctx = _client_tokens(conn, program["client_id"])

    # Placement colleague from contact_program_assignments
    _pc_row = conn.execute(
        """SELECT co.name, co.email, co.phone FROM contact_program_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.program_id = ? AND cpa.is_placement_colleague = 1 LIMIT 1""",
        (program["id"],),
    ).fetchone()
    pc_name = _pc_row["name"] if _pc_row else ""
    pc_email = _pc_row["email"] if _pc_row else ""
    pc_phone = _pc_row["phone"] if _pc_row else ""

    # Lead broker from contact_program_assignments
    _lb_row = conn.execute(
        """SELECT co.name, co.email, co.phone FROM contact_program_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.program_id = ? AND LOWER(COALESCE(cpa.role, '')) IN ('lead broker', 'broker') LIMIT 1""",
        (program["id"],),
    ).fetchone()
    lb_name = _lb_row["name"] if _lb_row else ""
    lb_email = _lb_row["email"] if _lb_row else ""
    lb_phone = _lb_row["phone"] if _lb_row else ""

    # Aggregate carriers from child policies
    child_carriers = conn.execute(
        """SELECT DISTINCT carrier FROM policies
           WHERE program_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
             AND carrier IS NOT NULL AND carrier != ''
           ORDER BY carrier""",
        (program["id"],),
    ).fetchall()
    carriers_list = ", ".join(r["carrier"] for r in child_carriers)

    ctx.update({
        "program_name": program["name"] or "",
        "program_uid": program["program_uid"] or "",
        "line_of_business": program["line_of_business"] or "",
        "effective_date": program["effective_date"] or "",
        "expiration_date": program["expiration_date"] or "",
        "renewal_status": program["renewal_status"] or "",
        "placement_colleague": pc_name,
        "placement_colleague_name": pc_name,
        "placement_colleague_email": pc_email,
        "placement_colleague_phone": pc_phone,
        "lead_broker": lb_name,
        "lead_broker_name": lb_name,
        "lead_broker_email": lb_email,
        "lead_broker_phone": lb_phone,
        "account_exec": program.get("account_exec") or "",
        "carriers": carriers_list,
        "today": date.today().strftime("%B %d, %Y"),
        "today_iso": date.today().isoformat(),
        "ref_tag": build_ref_tag(
            cn_number=ctx.get("cn_number") or "",
            client_id=program["client_id"],
        ),
    })
    return ctx
```

- [ ] **Step 2: Add `"program"` to `CONTEXT_TOKEN_GROUPS`**

In the `CONTEXT_TOKEN_GROUPS` dict in `email_templates.py`, add a new `"program"` key after the existing `"policy"` and `"client"` entries:

```python
    "program": OrderedDict([
        ("Program", [
            ("program_name", "Program Name"),
            ("program_uid", "Program ID"),
            ("line_of_business", "Line of Business"),
            ("effective_date", "Effective Date"),
            ("expiration_date", "Expiration Date"),
            ("renewal_status", "Renewal Status"),
        ]),
        ("Program Team", [
            ("placement_colleague", "Placement Colleague"),
            ("placement_colleague_email", "Colleague Email"),
            ("placement_colleague_phone", "Colleague Phone"),
            ("lead_broker", "Lead Broker"),
            ("lead_broker_email", "Lead Broker Email"),
            ("lead_broker_phone", "Lead Broker Phone"),
            ("account_exec", "Account Executive"),
        ]),
        ("Client", _CLIENT_GROUP),
        ("Contact", _CLIENT_CONTACT_GROUP),
        ("Aggregated", [
            ("carriers", "Carriers (comma-separated)"),
        ]),
        ("Tracking", [
            ("ref_tag", "Reference Tag"),
            ("today", "Today's Date"),
        ]),
    ]),
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: program_context() for email tokens + CONTEXT_TOKEN_GROUPS entry"
```

---

### Task 10: Compose Panel — Program Context Support

**Files:**
- Modify: `src/policydb/web/routes/compose.py`

- [ ] **Step 1: Add `program_uid` query parameter to `compose_panel`**

In `src/policydb/web/routes/compose.py`, add `program_uid` parameter to the `compose_panel` function signature:

```python
def compose_panel(
    request: Request,
    conn=Depends(get_db),
    context: str = Query("policy"),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    bundle_id: int = Query(0),
    mode: str = Query(""),
    to_email: str = Query(""),
    template_id: int = Query(0),
    issue_uid: str = Query(""),
    program_uid: str = Query(""),   # <-- ADD THIS
):
```

- [ ] **Step 2: Add program context handling**

In the context-building section of `compose_panel`, add a program branch. Find the `elif policy_uid:` block and add this before it:

```python
    elif program_uid:
        from policydb.email_templates import program_context as _program_ctx
        ctx = _program_ctx(conn, program_uid)
        if not client_id:
            _prog_row = conn.execute(
                "SELECT client_id FROM programs WHERE program_uid=?", (program_uid,)
            ).fetchone()
            if _prog_row:
                client_id = _prog_row["client_id"]
```

The full order should be: `if issue_uid` → `elif mode == "rfi_notify"` → `elif program_uid` → `elif policy_uid` → `elif project_name and client_id` → `elif client_id`.

- [ ] **Step 3: Add program subject template handling**

In the subject template selection section, add a program branch. Find the `elif policy_uid:` block for subject and add before it:

```python
    elif program_uid:
        subj_tpl = cfg.get(
            "email_subject_program",
            "Re: {{client_name}} — {{program_name}}",
        )
```

- [ ] **Step 4: Add program_uid to _load_recipients call and template context**

Add `program_uid=program_uid` to the `_load_recipients(...)` call if the function supports it. If not, the recipients will still load via `client_id` which is resolved from the program.

Add `"program_uid": program_uid` to the template context dict passed to `TemplateResponse`.

- [ ] **Step 5: Handle program templates in the template dropdown**

In the template dropdown section, add a program case. Find where template rows are loaded:

```python
    if policy_uid or project_name:
        tpl_rows = conn.execute(...)
```

Add before this:

```python
    if program_uid:
        tpl_rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('policy','general') ORDER BY context, name"
        ).fetchall()
    elif policy_uid or project_name:
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/compose.py
git commit -m "feat: compose panel supports program context for email composition"
```

---

### Task 11: Policy Token Resolution — Program Contact Override

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Update `policy_context()` to check program contacts first**

In `src/policydb/email_templates.py`, in the `policy_context()` function, find the placement colleague resolution block (around line 568):

```python
    # Placement colleague from contact_policy_assignments (is_placement_colleague flag)
    _pc_row = conn.execute(
        """SELECT co.name, co.email, co.phone FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           WHERE cpa.policy_id = (SELECT id FROM policies WHERE policy_uid = ?) AND cpa.is_placement_colleague = 1 LIMIT 1""",
        (policy_uid.upper(),),
    ).fetchone()
```

Replace with:

```python
    # Placement colleague: check program-level first (program wins), then policy-level
    _pc_row = None
    if row.get("program_id"):
        _pc_row = conn.execute(
            """SELECT co.name, co.email, co.phone FROM contact_program_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               WHERE cpa.program_id = ? AND cpa.is_placement_colleague = 1 LIMIT 1""",
            (row["program_id"],),
        ).fetchone()
    if not _pc_row:
        _pc_row = conn.execute(
            """SELECT co.name, co.email, co.phone FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               WHERE cpa.policy_id = (SELECT id FROM policies WHERE policy_uid = ?) AND cpa.is_placement_colleague = 1 LIMIT 1""",
            (policy_uid.upper(),),
        ).fetchone()
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: policy_context() checks program contacts first for placement colleague"
```

---

### Task 12: Config — Email Subject Default

**Files:**
- Modify: `src/policydb/config.py`
- Modify: `src/policydb/web/routes/settings.py`

- [ ] **Step 1: Add `email_subject_program` to `_DEFAULTS` in config.py**

In `src/policydb/config.py`, find the `_DEFAULTS` dict and locate the existing `email_subject_*` entries. Add after the last one:

```python
    "email_subject_program": "Re: {{client_name}} — {{program_name}}",
```

- [ ] **Step 2: Add to `EDITABLE_LISTS` in settings.py**

In `src/policydb/web/routes/settings.py`, the `EDITABLE_LISTS` dict doesn't contain email subject templates (they're managed differently — via free-text fields, not list items). Verify this by checking how `email_subject_policy` is handled in the settings page. If email subjects are NOT in `EDITABLE_LISTS`, skip this step — the config default is sufficient and the Settings UI already handles email subject fields via the email settings section.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/config.py
git commit -m "feat: add email_subject_program config default"
```

---

### Task 13: QA — Visual Verification

**Files:** None (testing only)

- [ ] **Step 1: Start the server**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb serve --port 8099`

- [ ] **Step 2: Navigate to a program that has child policies**

Open a program detail page in the browser. Verify:
- Tab bar shows all 7 tabs: Overview, Schematic, Timeline, Contacts, Workflow, Files, Activity
- Contacts tab loads and shows empty state or team matrix
- Add a contact via "+ Add" button, verify row appears
- Edit cells (name, role, email), verify save on blur works
- Toggle placement colleague star, verify amber highlight
- Delete a contact, verify removal

- [ ] **Step 3: Verify underwriter rollup**

On a program with child policies that have underwriter contacts:
- Contacts tab should show "Underwriters (from child policies)" section
- Rows should be read-only with carrier and policy UID links
- Clicking policy UID navigates to the policy

- [ ] **Step 4: Verify policy inheritance**

Navigate to a child policy's Contacts tab:
- Should show "Program Team" section with PGM badges (read-only)
- Below it, the policy's own contacts should be editable
- Link "edit on the program page" should navigate correctly

- [ ] **Step 5: Verify Workflow tab**

On the program:
- Workflow tab loads checklist from renewal_milestones config
- Clicking checkboxes toggles completion
- Information Requests section loads (may show empty state)

- [ ] **Step 6: Verify Files tab**

On the program:
- Files tab loads the attachment panel
- Can attach/detach files if attachments are configured

- [ ] **Step 7: Verify compose from program**

Click the ✉ Compose button on the Contacts tab:
- Compose slideover opens
- Subject is pre-filled with program name + client name
- Token pills are available for program context
- Per-contact ✉ button pre-fills the To field

- [ ] **Step 8: Fix any issues found and commit fixes**

```bash
git add -A
git commit -m "fix: QA fixes for program contacts, workflow, and files"
```
