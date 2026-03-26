# Exposure-Policy Linkage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect `client_exposures` to policies via a junction table for rate calculation, replacing the disconnected legacy exposure fields.

**Architecture:** New `policy_exposure_links` junction table links policies to specific `client_exposures` rows. A new `exposures.py` module handles link CRUD and rate calculation (`premium / (amount / denominator)`). The existing exposure matrix UI gains denominator, primary star, and rate columns. LLM import routes extracted exposure data through `client_exposures` instead of legacy policy columns.

**Tech Stack:** FastAPI, SQLite, Jinja2, HTMX, existing `initMatrix()` JS framework

**Spec:** `docs/superpowers/specs/2026-03-26-exposure-policy-linkage-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/policydb/migrations/089_policy_exposure_links.sql` | New junction table + denominator column on client_exposures |
| `src/policydb/db.py` | Wire migration 089 into `init_db()` |
| `src/policydb/exposures.py` (new) | Link CRUD, rate recalc, find-or-create logic |
| `src/policydb/config.py` | Add `exposure_denominators` to `_DEFAULTS` |
| `src/policydb/web/routes/settings.py` | Add `exposure_denominators` to `EDITABLE_LISTS` |
| `src/policydb/views.py` | Update `v_schedule` to JOIN through links with legacy fallback |
| `src/policydb/web/routes/clients.py` | New link/unlink/toggle-primary/denominator endpoints, modify exposure_cell |
| `src/policydb/web/routes/policies.py` | Premium PATCH triggers recalc, exposure card context |
| `src/policydb/web/templates/clients/_exposure_matrix.html` | Add Per, ★, Rate column headers |
| `src/policydb/web/templates/clients/_exposure_matrix_row.html` | Add Per, ★, Rate cells per row |
| `src/policydb/web/templates/policies/_exposure_card.html` (new) | Read-only exposure card for policy detail |
| `src/policydb/email_templates.py` | Add rate/exposure tokens |
| `src/policydb/llm_schemas.py` | Add `exposure_denominator` field, update parse flow |
| `src/policydb/importer.py` | Add exposure column aliases |
| `tests/test_exposures.py` (new) | Unit tests for exposures.py |

---

### Task 1: Migration + Config

**Files:**
- Create: `src/policydb/migrations/089_policy_exposure_links.sql`
- Modify: `src/policydb/db.py` (around line 362, `_KNOWN_MIGRATIONS` set and migration wiring)
- Modify: `src/policydb/config.py:174` (after `exposure_unit_options`)
- Modify: `src/policydb/web/routes/settings.py:48` (after `exposure_unit_options` in `EDITABLE_LISTS`)

- [ ] **Step 1: Create migration SQL**

Create `src/policydb/migrations/089_policy_exposure_links.sql`:

```sql
-- Add denominator column to client_exposures
ALTER TABLE client_exposures ADD COLUMN denominator INTEGER NOT NULL DEFAULT 1;

-- Junction table linking policies to exposures for rate calculation
CREATE TABLE IF NOT EXISTS policy_exposure_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    exposure_id     INTEGER NOT NULL REFERENCES client_exposures(id) ON DELETE CASCADE,
    is_primary      INTEGER NOT NULL DEFAULT 0,
    rate            REAL,
    rate_updated_at DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_uid, exposure_id)
);

CREATE INDEX IF NOT EXISTS idx_pel_policy ON policy_exposure_links(policy_uid);
CREATE INDEX IF NOT EXISTS idx_pel_exposure ON policy_exposure_links(exposure_id);
CREATE INDEX IF NOT EXISTS idx_pel_primary ON policy_exposure_links(policy_uid, is_primary) WHERE is_primary = 1;
```

- [ ] **Step 2: Wire migration into db.py**

In `src/policydb/db.py`:
- Add `89` to the `_KNOWN_MIGRATIONS` set
- Add migration block:

```python
if 89 not in applied:
    _run_migration(conn, 89, "089_policy_exposure_links.sql")
```

- [ ] **Step 3: Add config defaults**

In `src/policydb/config.py`, after `exposure_unit_options` (line 174), add:

```python
    "exposure_denominators": [1, 100, 1000],
```

- [ ] **Step 4: Add to EDITABLE_LISTS**

In `src/policydb/web/routes/settings.py`, after the `exposure_unit_options` entry in `EDITABLE_LISTS`, add:

```python
    "exposure_denominators": "Exposure Denominators",
```

- [ ] **Step 5: Verify migration runs**

Start the server (`pdb serve`), check that `policy_exposure_links` table exists and `client_exposures` has `denominator` column:

```bash
sqlite3 ~/.policydb/policydb.sqlite ".schema policy_exposure_links"
sqlite3 ~/.policydb/policydb.sqlite "PRAGMA table_info(client_exposures)" | grep denominator
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/migrations/089_policy_exposure_links.sql src/policydb/db.py src/policydb/config.py src/policydb/web/routes/settings.py
git commit -m "feat: add policy_exposure_links table and denominator column (migration 089)"
```

---

### Task 2: Core exposures.py Module

**Files:**
- Create: `src/policydb/exposures.py`
- Create: `tests/test_exposures.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_exposures.py`:

```python
"""Tests for exposure-policy linkage and rate calculation."""
import sqlite3
import pytest
from policydb.exposures import (
    create_exposure_link,
    delete_exposure_link,
    set_primary_exposure,
    recalc_exposure_rate,
    get_policy_exposures,
    find_or_create_exposure,
)


@pytest.fixture
def conn():
    """In-memory SQLite with required schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("""CREATE TABLE policies (
        id INTEGER PRIMARY KEY, policy_uid TEXT UNIQUE, client_id INTEGER,
        premium REAL, effective_date TEXT, project_id INTEGER)""")
    db.execute("""CREATE TABLE projects (
        id INTEGER PRIMARY KEY, client_id INTEGER, name TEXT)""")
    db.execute("""CREATE TABLE client_exposures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER, project_id INTEGER, policy_id INTEGER,
        exposure_type TEXT, is_custom INTEGER DEFAULT 0,
        unit TEXT DEFAULT 'number', year INTEGER,
        amount REAL, denominator INTEGER DEFAULT 1,
        source_document TEXT, notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE policy_exposure_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_uid TEXT NOT NULL, exposure_id INTEGER NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0,
        rate REAL, rate_updated_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(policy_uid, exposure_id))""")
    # Seed data
    db.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    db.execute("INSERT INTO policies (id, policy_uid, client_id, premium, effective_date) VALUES (1, 'POL-001', 1, 50000, '2026-01-01')")
    db.execute("INSERT INTO policies (id, policy_uid, client_id, premium, effective_date) VALUES (2, 'POL-002', 1, 25000, '2026-01-01')")
    db.execute("""INSERT INTO client_exposures (id, client_id, exposure_type, year, amount, denominator)
        VALUES (1, 1, 'Payroll', 2026, 10000000, 100)""")
    db.execute("""INSERT INTO client_exposures (id, client_id, exposure_type, year, amount, denominator)
        VALUES (2, 1, 'Revenue', 2026, 28000000, 1000)""")
    db.commit()
    return db


def test_create_link_and_rate(conn):
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["is_primary"] == 1
    # rate = 50000 / (10000000 / 100) = 0.50
    assert abs(link["rate"] - 0.50) < 0.001


def test_duplicate_link_rejected(conn):
    create_exposure_link(conn, "POL-001", 1)
    with pytest.raises(Exception):
        create_exposure_link(conn, "POL-001", 1)


def test_only_one_primary_per_policy(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    create_exposure_link(conn, "POL-001", 2, is_primary=True)
    links = get_policy_exposures(conn, "POL-001")
    primaries = [l for l in links if l["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["exposure_id"] == 2  # latest wins


def test_delete_link(conn):
    create_exposure_link(conn, "POL-001", 1)
    delete_exposure_link(conn, "POL-001", 1)
    assert len(get_policy_exposures(conn, "POL-001")) == 0


def test_recalc_by_policy(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    # Change premium
    conn.execute("UPDATE policies SET premium=100000 WHERE policy_uid='POL-001'")
    conn.commit()
    recalc_exposure_rate(conn, policy_uid="POL-001")
    links = get_policy_exposures(conn, "POL-001")
    # rate = 100000 / (10000000 / 100) = 1.00
    assert abs(links[0]["rate"] - 1.00) < 0.001


def test_recalc_by_exposure(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    create_exposure_link(conn, "POL-002", 1, is_primary=True)
    # Change exposure amount
    conn.execute("UPDATE client_exposures SET amount=5000000 WHERE id=1")
    conn.commit()
    recalc_exposure_rate(conn, exposure_id=1)
    links_1 = get_policy_exposures(conn, "POL-001")
    links_2 = get_policy_exposures(conn, "POL-002")
    # POL-001: 50000 / (5000000 / 100) = 1.00
    assert abs(links_1[0]["rate"] - 1.00) < 0.001
    # POL-002: 25000 / (5000000 / 100) = 0.50
    assert abs(links_2[0]["rate"] - 0.50) < 0.001


def test_null_rate_on_zero_amount(conn):
    conn.execute("UPDATE client_exposures SET amount=0 WHERE id=1")
    conn.commit()
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["rate"] is None


def test_null_rate_on_null_premium(conn):
    conn.execute("UPDATE policies SET premium=NULL WHERE policy_uid='POL-001'")
    conn.commit()
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["rate"] is None


def test_set_primary_exposure(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=False)
    create_exposure_link(conn, "POL-001", 2, is_primary=False)
    set_primary_exposure(conn, "POL-001", 1)
    links = get_policy_exposures(conn, "POL-001")
    primary = [l for l in links if l["is_primary"]]
    assert len(primary) == 1
    assert primary[0]["exposure_id"] == 1


def test_find_or_create_existing(conn):
    exp_id = find_or_create_exposure(conn, client_id=1, project_id=None,
                                     exposure_type="Payroll", year=2026,
                                     amount=10000000, denominator=100)
    assert exp_id == 1  # should find existing row


def test_find_or_create_new(conn):
    exp_id = find_or_create_exposure(conn, client_id=1, project_id=None,
                                     exposure_type="Headcount", year=2026,
                                     amount=500, denominator=1)
    assert exp_id > 2  # new row
    row = conn.execute("SELECT * FROM client_exposures WHERE id=?", (exp_id,)).fetchone()
    assert row["exposure_type"] == "Headcount"
    assert row["amount"] == 500
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_exposures.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'policydb.exposures'`

- [ ] **Step 3: Implement exposures.py**

Create `src/policydb/exposures.py`:

```python
"""Exposure-policy linkage: CRUD, rate calculation, find-or-create."""
from datetime import datetime


def _calc_rate(premium, amount, denominator):
    """Calculate rate = premium / (amount / denominator). Returns None if inputs invalid."""
    if not premium or not amount or amount == 0 or denominator == 0:
        return None
    return premium / (amount / denominator)


def create_exposure_link(conn, policy_uid, exposure_id, *, is_primary=False):
    """Create a link between a policy and an exposure row. Returns the link dict."""
    if is_primary:
        # Clear any existing primary for this policy
        conn.execute(
            "UPDATE policy_exposure_links SET is_primary=0 WHERE policy_uid=? AND is_primary=1",
            (policy_uid,),
        )
    # Get premium and exposure data for rate calc
    pol = conn.execute("SELECT premium FROM policies WHERE policy_uid=?", (policy_uid,)).fetchone()
    exp = conn.execute("SELECT amount, denominator FROM client_exposures WHERE id=?", (exposure_id,)).fetchone()
    rate = _calc_rate(
        pol["premium"] if pol else None,
        exp["amount"] if exp else None,
        exp["denominator"] if exp else 1,
    )
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO policy_exposure_links (policy_uid, exposure_id, is_primary, rate, rate_updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (policy_uid, exposure_id, 1 if is_primary else 0, rate, now if rate is not None else None),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    ).fetchone()
    return dict(row)


def delete_exposure_link(conn, policy_uid, exposure_id):
    """Remove a policy-exposure link."""
    conn.execute(
        "DELETE FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    )
    conn.commit()


def set_primary_exposure(conn, policy_uid, exposure_id):
    """Set one exposure as primary for a policy, clearing others."""
    conn.execute(
        "UPDATE policy_exposure_links SET is_primary=0 WHERE policy_uid=?",
        (policy_uid,),
    )
    conn.execute(
        "UPDATE policy_exposure_links SET is_primary=1 WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    )
    conn.commit()


def recalc_exposure_rate(conn, *, link_id=None, policy_uid=None, exposure_id=None):
    """Recalculate cached rate on policy_exposure_links rows.

    Pass one of:
    - link_id: recalc a single link
    - policy_uid: recalc all links for a policy (e.g., premium changed)
    - exposure_id: recalc all links to an exposure (e.g., amount changed)
    """
    if link_id:
        where, params = "pel.id=?", (link_id,)
    elif policy_uid:
        where, params = "pel.policy_uid=?", (policy_uid,)
    elif exposure_id:
        where, params = "pel.exposure_id=?", (exposure_id,)
    else:
        return

    rows = conn.execute(
        f"""SELECT pel.id, p.premium, ce.amount, ce.denominator
            FROM policy_exposure_links pel
            JOIN policies p ON p.policy_uid = pel.policy_uid
            JOIN client_exposures ce ON ce.id = pel.exposure_id
            WHERE {where}""",
        params,
    ).fetchall()

    now = datetime.utcnow().isoformat()
    for r in rows:
        rate = _calc_rate(r["premium"], r["amount"], r["denominator"])
        conn.execute(
            "UPDATE policy_exposure_links SET rate=?, rate_updated_at=? WHERE id=?",
            (rate, now if rate is not None else None, r["id"]),
        )
    conn.commit()


def get_policy_exposures(conn, policy_uid):
    """Get all exposure links for a policy, with exposure details."""
    rows = conn.execute(
        """SELECT pel.*, ce.exposure_type, ce.amount, ce.denominator, ce.year,
                  ce.unit, ce.project_id, ce.client_id
           FROM policy_exposure_links pel
           JOIN client_exposures ce ON ce.id = pel.exposure_id
           WHERE pel.policy_uid=?
           ORDER BY pel.is_primary DESC, ce.exposure_type""",
        (policy_uid,),
    ).fetchall()
    return [dict(r) for r in rows]


def find_or_create_exposure(conn, *, client_id, project_id, exposure_type, year, amount, denominator=1):
    """Find an existing client_exposures row or create one. Returns the exposure id."""
    row = conn.execute(
        """SELECT id FROM client_exposures
           WHERE client_id=? AND COALESCE(project_id,0)=COALESCE(?,0)
           AND exposure_type=? AND year=?""",
        (client_id, project_id, exposure_type, year),
    ).fetchone()
    if row:
        return row["id"]
    conn.execute(
        """INSERT INTO client_exposures (client_id, project_id, exposure_type, year, amount, denominator)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, project_id, exposure_type, year, amount, denominator),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_exposures.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/exposures.py tests/test_exposures.py
git commit -m "feat: add exposures.py module with link CRUD and rate calculation"
```

---

### Task 3: Exposure Matrix UI — Denominator + Rate Columns

**Files:**
- Modify: `src/policydb/web/templates/clients/_exposure_matrix.html`
- Modify: `src/policydb/web/templates/clients/_exposure_matrix_row.html`
- Modify: `src/policydb/web/routes/clients.py:5346` (exposure_cell endpoint)
- Modify: `src/policydb/web/routes/clients.py:5207` (_exposure_tab_context)

- [ ] **Step 1: Add denominator and rate to exposure_cell PATCH**

In `src/policydb/web/routes/clients.py`, modify the `exposure_cell` function (line 5346):

Add `"denominator"` to the `allowed` set (line 5351):

```python
allowed = {"amount", "source_document", "notes", "policy_id", "denominator"}
```

Add denominator handling after the `policy_id` elif block (after line 5408):

```python
    elif field == "denominator":
        denom = int(formatted) if formatted and formatted not in ("", "—") else 1
        if denom <= 0:
            denom = 1
        conn.execute(
            "UPDATE client_exposures SET denominator=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?",
            (denom, exposure_id, client_id),
        )
        conn.commit()
        # Recalc rates for all policies linked to this exposure
        from policydb.exposures import recalc_exposure_rate
        recalc_exposure_rate(conn, exposure_id=exposure_id)
        # Return the rate for the linked policy (if any)
        link = conn.execute(
            "SELECT rate, is_primary FROM policy_exposure_links WHERE exposure_id=?",
            (exposure_id,),
        ).fetchone()
        return JSONResponse({
            "ok": True, "formatted": str(denom),
            "rate": link["rate"] if link else None,
            "is_primary": link["is_primary"] if link else None,
        })
```

Also in the `amount` handler (around line 5375, after commit), add recalc:

```python
        # Recalc rates for linked policies
        from policydb.exposures import recalc_exposure_rate
        recalc_exposure_rate(conn, exposure_id=exposure_id)
```

- [ ] **Step 2: Add rate data to exposure tab context**

In `_exposure_tab_context()` (line 5207), after fetching exposures, attach link data to each exposure row:

```python
    # Attach link data (rate, is_primary, linked policy_uid) to each exposure
    for e in exposures:
        link = conn.execute(
            """SELECT pel.rate, pel.is_primary, pel.policy_uid, p.policy_type, p.carrier
               FROM policy_exposure_links pel
               JOIN policies p ON p.policy_uid = pel.policy_uid
               WHERE pel.exposure_id=?
               ORDER BY pel.is_primary DESC LIMIT 1""",
            (e["id"],),
        ).fetchone()
        e["link_rate"] = link["rate"] if link else None
        e["link_is_primary"] = link["is_primary"] if link else None
        e["link_policy_uid"] = link["policy_uid"] if link else None
```

Also pass `exposure_denominators` from config into the template context:

```python
    import policydb.config as cfg
    denom_options = cfg.get("exposure_denominators", [1, 100, 1000])
```

Add `denom_options=denom_options` to the template context dict.

- [ ] **Step 3: Update matrix table headers**

In `_exposure_matrix.html`, update the colgroup (lines 44-48) and thead (lines 50-59):

Replace the current colgroup:
```html
      <colgroup>
        <col style="width:12%"><col style="width:6%"><col style="width:12%">
        <col style="width:10%"><col style="width:10%"><col style="width:6%">
        <col style="width:14%"><col style="width:14%"><col style="width:4%">
        <col style="width:8%"><col style="width:28px">
      </colgroup>
```

Replace the current thead row:
```html
        <tr class="text-left text-xs text-gray-400 uppercase tracking-wide border-b border-gray-100 bg-gray-50">
          <th class="px-3 py-2">Type</th>
          <th class="px-3 py-2">Per</th>
          <th class="px-3 py-2">Policy</th>
          <th class="px-3 py-2 text-right">{{ selected_year - 1 }}</th>
          <th class="px-3 py-2 text-right text-blue-600">{{ selected_year }}</th>
          <th class="px-3 py-2 text-center">YoY</th>
          <th class="px-3 py-2">Source</th>
          <th class="px-3 py-2">Notes</th>
          <th class="px-3 py-2 text-center">★</th>
          <th class="px-3 py-2 text-right">Rate</th>
          <th class="px-3 py-2"></th>
        </tr>
```

Update the empty row colspan to match (11 columns):
```html
        <tr id="exposures-empty-{{ client_id }}"><td colspan="11" class="px-4 py-6 text-center text-gray-400 text-xs">No exposures tracked yet.</td></tr>
```

- [ ] **Step 4: Update matrix row template**

In `_exposure_matrix_row.html`, add three new cells. After the Type cell (line 6) and before the Policy cell (line 7), insert the Per cell:

```html
  {# Denominator (Per) #}
  <td class="px-3 py-2.5">
    <div contenteditable="true"
         class="matrix-cell-editable outline-none text-gray-600 text-xs rounded px-1 -mx-1 border border-dashed border-gray-200 text-center w-12"
         data-field="denominator"
         data-placeholder="1">{{ e.denominator or 1 }}</div>
  </td>
```

After the Notes cell (line 53) and before the Delete cell (line 55), insert the Primary star and Rate cells:

```html
  {# Primary Toggle #}
  <td class="px-3 py-2.5 text-center">
    {% if e.link_policy_uid %}
    <button class="text-lg leading-none cursor-pointer no-print"
            hx-patch="{{ exposure_url_prefix }}/exposures/{{ e.id }}/toggle-primary"
            hx-vals='{"policy_uid": "{{ e.link_policy_uid }}"}'
            hx-target="closest tr" hx-swap="outerHTML"
            title="{{ 'Primary rating basis' if e.link_is_primary else 'Set as primary' }}">
      {% if e.link_is_primary %}<span class="text-amber-400">★</span>{% else %}<span class="text-gray-300 hover:text-amber-300">☆</span>{% endif %}
    </button>
    {% else %}<span class="text-gray-200 text-sm">—</span>{% endif %}
  </td>
  {# Rate (auto-calculated) #}
  <td class="px-3 py-2.5 text-right" data-field="rate">
    {% if e.link_rate is not none %}
    <span class="text-xs font-semibold px-2 py-0.5 rounded
      {% if e.link_is_primary %}bg-green-50 text-green-700{% else %}text-gray-400{% endif %}">
      ${{ "{:,.2f}".format(e.link_rate) }}
    </span>
    {% elif e.link_policy_uid %}<span class="text-gray-300 text-xs">—</span>
    {% else %}<span class="text-gray-200 text-xs"></span>{% endif %}
  </td>
```

- [ ] **Step 5: Verify in browser**

Navigate to a client's Exposures tab. Confirm:
- Per column shows denominator values (default 1)
- Star column shows for rows with a linked policy
- Rate column calculates when policy + amount + denominator are present
- Editing denominator recalculates rate

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/clients/_exposure_matrix.html src/policydb/web/templates/clients/_exposure_matrix_row.html src/policydb/web/routes/clients.py
git commit -m "feat: add denominator, primary star, and rate columns to exposure matrix"
```

---

### Task 4: Policy Combobox → Junction Table Switchover

**Files:**
- Modify: `src/policydb/web/routes/clients.py:5396` (exposure_cell policy_id handler)
- Create: `src/policydb/web/routes/clients.py` (new toggle-primary endpoint)

- [ ] **Step 1: Modify policy_id cell handler to use junction table**

Replace the `policy_id` handler block in `exposure_cell()` (lines 5396-5408) with:

```python
    elif field == "policy_id":
        from policydb.exposures import create_exposure_link, delete_exposure_link, recalc_exposure_rate
        # Get current link for this exposure
        old_link = conn.execute(
            "SELECT policy_uid FROM policy_exposure_links WHERE exposure_id=?",
            (exposure_id,),
        ).fetchone()
        pid = int(formatted) if formatted and formatted not in ("", "—", "0") else None
        if old_link:
            delete_exposure_link(conn, old_link["policy_uid"], exposure_id)
        if pid:
            # Look up policy_uid from policy id
            pol = conn.execute("SELECT policy_uid, policy_type, carrier FROM policies WHERE id=?", (pid,)).fetchone()
            if pol:
                link = create_exposure_link(conn, pol["policy_uid"], exposure_id, is_primary=True)
                label = f"{pol['policy_type']} — {pol['carrier'] or '?'}"
                return JSONResponse({
                    "ok": True, "formatted": label,
                    "rate": link.get("rate"),
                    "is_primary": link.get("is_primary"),
                })
        return JSONResponse({"ok": True, "formatted": ""})
```

- [ ] **Step 2: Add toggle-primary endpoint**

Add new endpoint in `clients.py` (near the exposure endpoints):

```python
@router.patch("/{client_id}/exposures/{exposure_id}/toggle-primary")
async def exposure_toggle_primary(request: Request, client_id: int, exposure_id: int, conn=Depends(get_db)):
    """Toggle primary status for a policy-exposure link."""
    from policydb.exposures import set_primary_exposure, get_policy_exposures
    body = await request.form()
    policy_uid = body.get("policy_uid", "")
    if not policy_uid:
        return HTMLResponse("")
    link = conn.execute(
        "SELECT is_primary FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
        (policy_uid, exposure_id),
    ).fetchone()
    if not link:
        return HTMLResponse("")
    if link["is_primary"]:
        # Unset primary
        conn.execute(
            "UPDATE policy_exposure_links SET is_primary=0 WHERE policy_uid=? AND exposure_id=?",
            (policy_uid, exposure_id),
        )
        conn.commit()
    else:
        set_primary_exposure(conn, policy_uid, exposure_id)
    # Return refreshed row
    # (reuse existing exposure row rendering context)
    return _render_exposure_row(request, conn, client_id, exposure_id)
```

Create the helper `_render_exposure_row()` that fetches the exposure row data and renders `_exposure_matrix_row.html`. This follows the pattern used by other matrix row refresh endpoints in the codebase.

- [ ] **Step 3: Update onSaved callback for rate display**

In `_exposure_matrix.html`, update the `onSaved` callback in the `initMatrix` call (line 102) to also handle rate updates:

```javascript
    onSaved: function(rowEl, field, resp) {
      if (resp.yoy !== undefined) {
        var yoyCell = rowEl.querySelector('[data-field="yoy"]');
        if (yoyCell) {
          yoyCell.innerHTML = resp.yoy
            ? '<span class="text-xs font-semibold ' + (resp.yoy_direction === 'up' ? 'text-red-500' : 'text-green-600') + '">' + resp.yoy + '</span>'
            : '<span class="text-gray-300 text-xs">&mdash;</span>';
        }
      }
      if (resp.rate !== undefined && resp.rate !== null) {
        var rateCell = rowEl.querySelector('[data-field="rate"]');
        if (rateCell) {
          var cls = resp.is_primary ? 'bg-green-50 text-green-700' : 'text-gray-400';
          rateCell.innerHTML = '<span class="text-xs font-semibold px-2 py-0.5 rounded ' + cls + '">$' + resp.rate.toFixed(2) + '</span>';
        }
      }
    }
```

- [ ] **Step 4: Test the switchover**

In browser:
- Select a policy from the combobox → verify link created in DB, rate appears
- Clear the policy → verify link deleted, rate disappears
- Click star to toggle primary → verify star changes, rate color changes
- Edit amount → verify rate recalculates

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_exposure_matrix.html
git commit -m "feat: switch policy combobox to junction table writes with toggle-primary"
```

---

### Task 5: Premium PATCH → Rate Recalc

**Files:**
- Modify: `src/policydb/web/routes/policies.py:3329` (policy_cell_save)

- [ ] **Step 1: Add recalc trigger to premium save**

In `policy_cell_save()` in `routes/policies.py`, after the premium value is saved to the database (inside the premium handling block, after the commit), add:

```python
        # Recalc exposure rates when premium changes
        from policydb.exposures import recalc_exposure_rate
        recalc_exposure_rate(conn, policy_uid=uid)
```

This goes after the existing premium save logic, before the response is returned. The `uid` variable (policy_uid) is already available in the function scope.

- [ ] **Step 2: Test**

In browser: change a policy's premium → verify the rate on linked exposure rows updates.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: trigger exposure rate recalc on premium change"
```

---

### Task 6: Policy Detail — Exposure Card

**Files:**
- Create: `src/policydb/web/templates/policies/_exposure_card.html`
- Modify: `src/policydb/web/routes/policies.py:1912` (policy_tab_details)

- [ ] **Step 1: Create exposure card partial**

Create `src/policydb/web/templates/policies/_exposure_card.html`:

```html
{# Exposure card — read-only summary of linked exposures on policy detail page #}
<div class="card p-4 mt-4">
  <h3 class="font-semibold text-gray-900 text-sm mb-3">Rating Basis</h3>
  {% if exposure_links %}
    {% for link in exposure_links %}
    {% if link.is_primary %}
    <div class="flex items-center gap-3 p-3 bg-green-50 border border-green-200 rounded-lg mb-2">
      <span class="text-amber-400 text-lg">★</span>
      <div class="flex-1 min-w-0">
        <div class="font-semibold text-gray-900 text-sm">{{ link.exposure_type }}</div>
        <div class="text-xs text-gray-500">{{ link.project_name or 'Corporate' }} · {{ link.year }} · per {{ link.denominator }}</div>
      </div>
      <div class="text-right">
        <div class="text-xs text-gray-500">{% if link.unit == 'currency' %}{{ "${:,.0f}".format(link.amount) }}{% else %}{{ "{:,.0f}".format(link.amount) }}{% endif %}</div>
        {% if link.rate is not none %}
        <div class="font-bold text-green-700 text-base">${{ "{:,.2f}".format(link.rate) }}</div>
        <div class="text-[10px] text-gray-400">per ${{ "{:,}".format(link.denominator) }} of {{ link.exposure_type | lower }}</div>
        {% else %}
        <div class="text-gray-400 text-xs">No rate</div>
        {% endif %}
      </div>
    </div>
    {% endif %}
    {% endfor %}
    {# Context exposures #}
    {% set context_links = exposure_links | selectattr('is_primary', 'falsy') | list %}
    {% if context_links %}
    <div class="text-[10px] uppercase tracking-wide text-gray-400 mt-3 mb-1.5">Context Exposures</div>
    {% for link in context_links %}
    <div class="flex items-center gap-3 p-2.5 bg-gray-50 border border-gray-100 rounded-lg mb-1.5">
      <span class="text-gray-300 text-sm">☆</span>
      <div class="flex-1 min-w-0">
        <div class="font-medium text-gray-600 text-sm">{{ link.exposure_type }}</div>
        <div class="text-xs text-gray-400">{{ link.project_name or 'Corporate' }} · {{ link.year }}</div>
      </div>
      <div class="text-right">
        <div class="text-xs text-gray-400">{% if link.unit == 'currency' %}{{ "${:,.0f}".format(link.amount) }}{% else %}{{ "{:,.0f}".format(link.amount) }}{% endif %}</div>
        {% if link.rate is not none %}
        <div class="font-medium text-gray-500">${{ "{:,.2f}".format(link.rate) }}</div>
        {% endif %}
      </div>
    </div>
    {% endfor %}
    {% endif %}
  {% else %}
  <div class="p-4 bg-amber-50 border border-amber-200 rounded-lg text-center">
    <div class="text-amber-700 text-sm font-medium">No rating basis linked</div>
    <div class="text-xs text-amber-500 mt-1">Link exposures in the client's Exposures tab</div>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 2: Add exposure data to policy detail context**

In `policy_tab_details()` (line 1912 in `routes/policies.py`), fetch exposure links and pass to template:

```python
    from policydb.exposures import get_policy_exposures
    exposure_links = get_policy_exposures(conn, policy["policy_uid"])
    # Attach project names
    for link in exposure_links:
        if link.get("project_id"):
            proj = conn.execute("SELECT name FROM projects WHERE id=?", (link["project_id"],)).fetchone()
            link["project_name"] = proj["name"] if proj else None
        else:
            link["project_name"] = None
```

Add `exposure_links=exposure_links` to the template context.

- [ ] **Step 3: Include card in policy detail template**

In the policy detail template (wherever the Details tab content is rendered), add:

```html
{% include "policies/_exposure_card.html" %}
```

Place it after the core policy fields section, before any working notes or activity sections.

- [ ] **Step 4: Verify in browser**

Navigate to a policy that has linked exposures → verify the card shows with primary highlighted green and context in gray. Navigate to a policy with no links → verify amber placeholder appears.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/policies/_exposure_card.html src/policydb/web/routes/policies.py
git commit -m "feat: add exposure card to policy detail page"
```

---

### Task 7: Update v_schedule View

**Files:**
- Modify: `src/policydb/views.py:157` (V_SCHEDULE)

- [ ] **Step 1: Update v_schedule with LEFT JOIN through links**

Replace `V_SCHEDULE` (lines 157-184) in `views.py`:

```python
V_SCHEDULE = """
CREATE VIEW v_schedule AS
SELECT
    c.name AS client_name,
    COALESCE(p.first_named_insured, c.name) AS "First Named Insured",
    CASE WHEN p.is_program = 1 THEN p.policy_type || ' [PROGRAM]' ELSE p.policy_type END AS "Line of Business",
    CASE WHEN p.is_program = 1
         THEN COALESCE((SELECT GROUP_CONCAT(pc.carrier, ', ') FROM program_carriers pc WHERE pc.program_id = p.id ORDER BY pc.sort_order), p.carrier)
         ELSE p.carrier END AS "Carrier",
    p.policy_number AS "Policy Number",
    p.effective_date AS "Effective",
    p.expiration_date AS "Expiration",
    p.premium AS "Premium",
    p.limit_amount AS "Limit",
    p.deductible AS "Deductible",
    p.coverage_form AS "Form",
    p.layer_position AS "Layer",
    p.project_name AS "Project",
    COALESCE(ce.exposure_type || ' /' || ce.denominator, p.exposure_basis) AS "Exposure Basis",
    COALESCE(ce.amount, p.exposure_amount) AS "Exposure Amount",
    COALESCE('per ' || ce.denominator, p.exposure_unit) AS "Exposure Unit",
    pel.rate AS "Rate",
    p.description AS "Comments"
FROM policies p
JOIN clients c ON p.client_id = c.id
LEFT JOIN policy_exposure_links pel ON pel.policy_uid = p.policy_uid AND pel.is_primary = 1
LEFT JOIN client_exposures ce ON ce.id = pel.exposure_id
WHERE p.archived = 0
  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
ORDER BY c.name, p.policy_type, p.layer_position
"""
```

Key changes:
- LEFT JOIN to `policy_exposure_links` (primary only) and `client_exposures`
- COALESCE to fall back to legacy columns when no link exists
- New "Rate" column

- [ ] **Step 2: Restart server and verify**

Views are rebuilt on every startup. Restart and query:

```bash
sqlite3 ~/.policydb/policydb.sqlite "SELECT \"Exposure Basis\", \"Exposure Amount\", \"Rate\" FROM v_schedule LIMIT 5"
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/views.py
git commit -m "feat: update v_schedule to use exposure links with legacy fallback"
```

---

### Task 8: Email Tokens

**Files:**
- Modify: `src/policydb/email_templates.py:262` (policy_context function)
- Modify: `src/policydb/email_templates.py:667` (CONTEXT_TOKEN_GROUPS)

- [ ] **Step 1: Add exposure/rate tokens to policy_context()**

In `policy_context()`, after the existing field extraction, add:

```python
    # Exposure/rate from linked exposures
    from policydb.exposures import get_policy_exposures
    exp_links = get_policy_exposures(conn, row.get("policy_uid", ""))
    primary = next((e for e in exp_links if e["is_primary"]), None)
    ctx["exposure_type"] = primary["exposure_type"] if primary else ""
    ctx["exposure_amount"] = "${:,.0f}".format(primary["amount"]) if primary and primary["amount"] else ""
    ctx["exposure_denominator"] = str(primary["denominator"]) if primary else ""
    ctx["exposure_rate"] = "${:,.2f}".format(primary["rate"]) if primary and primary["rate"] is not None else ""
    ctx["exposure_rate_label"] = (
        f"${primary['rate']:,.2f} per ${primary['denominator']:,} of {primary['exposure_type'].lower()}"
        if primary and primary["rate"] is not None else ""
    )
```

- [ ] **Step 2: Add tokens to CONTEXT_TOKEN_GROUPS**

In `CONTEXT_TOKEN_GROUPS`, the structure is a dict of context types, each containing a list of `(group_name, [(key, label)])` tuples. Add a new "Exposure" sub-group within the policy context (or append to the "Financials" sub-group if one exists). Read the file first to find the exact structure. Add:

```python
        ("exposure_type", "Exposure Type"),
        ("exposure_amount", "Exposure Amount"),
        ("exposure_denominator", "Exposure Denominator"),
        ("exposure_rate", "Exposure Rate"),
        ("exposure_rate_label", "Rate Label (e.g. $0.50 per $100 of payroll)"),
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: add exposure and rate tokens to email template system"
```

---

### Task 9: LLM Import Integration

**Files:**
- Modify: `src/policydb/llm_schemas.py:423` (POLICY_EXTRACTION_SCHEMA)
- Modify: `src/policydb/llm_schemas.py` (_ai_import_parse_inner)
- Modify: `src/policydb/importer.py:126` (ALIASES dict)

- [ ] **Step 1: Add exposure_denominator to LLM schema**

In `llm_schemas.py`, after the `exposure_amount` definition (around line 430), add:

```python
{
    "key": "exposure_denominator",
    "label": "Exposure Denominator",
    "type": "number",
    "required": False,
    "description": "Rating unit denominator — the 'per X' value. For example, if the rate is 'per $100 of payroll', the denominator is 100. Common values: 1, 100, 1000.",
    "example": "100",
},
```

- [ ] **Step 2: Update import parse to route exposure data through client_exposures**

In `_ai_import_parse_inner()`, after the existing field processing, add exposure linkage logic:

```python
    # Route exposure data through client_exposures → policy_exposure_links
    exposure_basis = parsed.get("exposure_basis")
    exposure_amount = parsed.get("exposure_amount")
    exposure_denom = parsed.get("exposure_denominator", 1) or 1
    if exposure_basis and exposure_amount:
        from policydb.exposures import find_or_create_exposure, create_exposure_link
        eff_date = parsed.get("effective_date") or policy.get("effective_date", "")
        year = int(eff_date[:4]) if eff_date and len(eff_date) >= 4 else datetime.now().year
        client_id = policy["client_id"]
        project_id = policy.get("project_id")
        exp_id = find_or_create_exposure(
            conn,
            client_id=client_id,
            project_id=project_id,
            exposure_type=exposure_basis,
            year=year,
            amount=float(exposure_amount),
            denominator=int(exposure_denom),
        )
        # Check for existing link
        existing = conn.execute(
            "SELECT id FROM policy_exposure_links WHERE policy_uid=? AND exposure_id=?",
            (policy["policy_uid"], exp_id),
        ).fetchone()
        if not existing:
            create_exposure_link(conn, policy["policy_uid"], exp_id, is_primary=True)
```

- [ ] **Step 3: Add CSV importer aliases**

In `importer.py`, add to the `ALIASES` dict in `PolicyImporter`:

```python
        "exposure_basis": ["exposure basis", "rating basis", "exposure type"],
        "exposure_amount": ["exposure amount", "exposure value", "exposure"],
        "exposure_denominator": ["denominator", "per", "rating unit"],
```

- [ ] **Step 4: Test LLM import**

Navigate to a policy → Import from AI → paste test JSON with exposure fields → verify:
- `client_exposures` row created (or existing one found)
- `policy_exposure_links` row created with rate
- Exposure card on policy detail shows the linked exposure

- [ ] **Step 5: Commit**

```bash
git add src/policydb/llm_schemas.py src/policydb/importer.py
git commit -m "feat: route LLM/CSV exposure imports through client_exposures and create links"
```

---

### Task 10: Unlinked Policy Indicators

**Files:**
- Modify: `src/policydb/web/templates/policies/_exposure_card.html` (already has placeholder — verify)
- Modify: `src/policydb/web/routes/clients.py` (location assignment board)

- [ ] **Step 1: Verify policy detail placeholder exists**

The `_exposure_card.html` created in Task 6 already includes the amber "No rating basis linked" placeholder. Verify it renders correctly for an unlinked policy.

- [ ] **Step 2: Add indicator to location assignment board**

In the location assignment board template (where policy cards are rendered after assignment), add a small amber dot indicator when the policy has no exposure links but the location has exposures:

```html
{% if not policy_has_exposure_link and location_has_exposures %}
<span class="inline-block w-2 h-2 rounded-full bg-amber-400 ml-1" title="No exposure linked"></span>
{% endif %}
```

The exact template location depends on the assignment board's policy card partial. Add `policy_has_exposure_link` to the context by querying:

```python
has_link = conn.execute(
    "SELECT 1 FROM policy_exposure_links WHERE policy_uid=? LIMIT 1",
    (policy["policy_uid"],),
).fetchone() is not None
```

- [ ] **Step 3: Test indicators**

In browser:
- Policy detail with no links → amber placeholder visible
- Location assignment board with unlinked policies → amber dots visible
- Schedule view → dashes in exposure columns for unlinked policies

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/
git commit -m "feat: add unlinked policy indicators across UI touchpoints"
```

---

### Task 11: Final QA & Cleanup

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```

Fix any failures.

- [ ] **Step 2: QA walkthrough**

Test the complete flow in browser:
1. Navigate to client → Exposures tab → add an exposure (Payroll, amount 10M, denominator 100)
2. Link a WC policy via combobox → verify link created, rate calculated
3. Click star to set as primary → verify star fills amber
4. Navigate to the policy detail → verify exposure card shows
5. Change the policy premium → return to exposures → verify rate updated
6. Check Schedule of Insurance → verify exposure and rate columns populated
7. Test LLM import with exposure fields → verify data routes correctly
8. Test an unlinked policy → verify amber placeholder on detail page

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: QA fixes for exposure-policy linkage"
```

---

## Deferred Items

These items from the spec are deliberately deferred to a follow-up pass:

1. **LLM import conflict handling:** When the extracted exposure amount differs from an existing `client_exposures` row, the spec calls for showing both values with a diff indicator. The current plan's `find_or_create_exposure()` silently reuses the existing row. Add diff detection and UI in a follow-up.

2. **Combobox "unlinked" badge:** The spec lists showing an indicator on policies in the combobox dropdown that have no exposure links. This requires modifying the combobox option rendering and is minor polish — add after core linkage works.

3. **Migration number verification:** The plan uses 089. Verify against `main` branch before implementation — if 089 already exists there, increment accordingly.
