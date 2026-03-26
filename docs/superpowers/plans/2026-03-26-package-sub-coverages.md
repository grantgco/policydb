# Package Policy Sub-Coverages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow any policy to carry multiple sub-coverage type tags via a junction table, enabling package policies (BOPs, WC/EL) to appear correctly in schedules, towers, coverage matrices, and email tokens.

**Architecture:** New `policy_sub_coverages` junction table linked to `policies(id)`. Sub-coverages are selected from the existing `policy_types` config list. Auto-generation on policy creation for configured mappings (e.g., WC → EL). Ghost rows in schedule views, layer participation in tower diagrams, and a `{{sub_coverages}}` email token.

**Tech Stack:** SQLite migration, FastAPI routes, Jinja2 templates, HTMX partial updates, existing pill/tag input pattern.

**Spec:** `docs/superpowers/specs/2026-03-26-package-policy-sub-coverages-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/policydb/migrations/090_policy_sub_coverages.sql` | Table DDL |
| Modify | `src/policydb/db.py` ~line 376 | Wire migration 090 |
| Modify | `src/policydb/config.py` ~line 49 | Add policy types + auto_sub_coverages default |
| Modify | `src/policydb/utils.py` ~line 159 | Update BOP coverage aliases |
| Modify | `src/policydb/web/routes/policies.py` ~line 4306 | Sub-coverage CRUD + auto-generation + schedule ghost rows |
| Modify | `src/policydb/web/templates/policies/_tab_details.html` ~line 201 | Sub-coverage pill/tag UI |
| Modify | `src/policydb/web/routes/programs.py` | Tower ghost layers (Python-level) |
| Modify | `src/policydb/queries.py` ~line 1515 | Coverage matrix union with sub-coverages |
| Modify | Schedule template(s) | Ghost row rendering + Package Policies section |
| Modify | Tower template(s) | Package layer visual treatment |
| Modify | `src/policydb/email_templates.py` ~line 307, ~line 732 | Add sub_coverages token |
| Create | `tests/test_sub_coverages.py` | All sub-coverage tests |

---

### Task 1: Migration — Create `policy_sub_coverages` Table

**Files:**
- Create: `src/policydb/migrations/090_policy_sub_coverages.sql`
- Modify: `src/policydb/db.py` ~line 376

- [ ] **Step 1: Write the migration SQL file**

Create `src/policydb/migrations/090_policy_sub_coverages.sql`:

```sql
CREATE TABLE IF NOT EXISTS policy_sub_coverages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    coverage_type   TEXT    NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_id, coverage_type)
);
CREATE INDEX IF NOT EXISTS idx_sub_cov_policy ON policy_sub_coverages(policy_id);
CREATE INDEX IF NOT EXISTS idx_sub_cov_type   ON policy_sub_coverages(coverage_type);
```

- [ ] **Step 2: Wire migration into `init_db()`**

In `src/policydb/db.py`, after the migration 089 block (~line 376), add:

```python
if 90 not in applied:
    sql = (_MIGRATIONS_DIR / "090_policy_sub_coverages.sql").read_text()
    conn.executescript(sql)
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (90, "policy_sub_coverages junction table"),
    )
    conn.commit()
```

- [ ] **Step 3: Write test for migration**

Create `tests/test_sub_coverages.py`:

```python
import pytest
import sqlite3
from policydb.db import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    init_db(path=db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_sub_coverages_table_exists(db):
    """policy_sub_coverages table is created by migration 090."""
    tables = [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "policy_sub_coverages" in tables


def test_sub_coverages_unique_constraint(db):
    """Cannot insert duplicate (policy_id, coverage_type) pair."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-TEST', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-TEST'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
        (pid, "Employers Liability"),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
            (pid, "Employers Liability"),
        )


def test_sub_coverages_cascade_delete(db):
    """Deleting a policy cascades to its sub-coverages."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-DEL', 0, 'Business Owners Policy')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-DEL'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
        (pid, "General Liability"),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM policies WHERE id = ?", (pid,))
    db.commit()
    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 0
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/nervous-jones && python -m pytest tests/test_sub_coverages.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/migrations/090_policy_sub_coverages.sql src/policydb/db.py tests/test_sub_coverages.py
git commit -m "feat: add policy_sub_coverages junction table (migration 090)"
```

---

### Task 2: Config Defaults — New Policy Types + Auto-Sub-Coverages

**Files:**
- Modify: `src/policydb/config.py` ~line 49

- [ ] **Step 1: Add new policy types to `_DEFAULTS`**

In `src/policydb/config.py`, add to the `policy_types` list (after "Crime / Fidelity"):

```python
"Business Owners Policy",
"Employers Liability",
```

- [ ] **Step 2: Add `auto_sub_coverages` config key**

In `src/policydb/config.py`, add to `_DEFAULTS` dict (anywhere after `policy_types`):

```python
"auto_sub_coverages": {
    "Workers Compensation": ["Employers Liability"],
},
```

- [ ] **Step 3: Write test**

Append to `tests/test_sub_coverages.py`:

```python
import policydb.config as cfg


def test_config_has_bop_policy_type():
    """Business Owners Policy is in the default policy_types list."""
    types = cfg.get("policy_types")
    assert "Business Owners Policy" in types
    assert "Employers Liability" in types


def test_config_has_auto_sub_coverages():
    """auto_sub_coverages default maps WC to EL."""
    auto = cfg.get("auto_sub_coverages")
    assert auto.get("Workers Compensation") == ["Employers Liability"]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sub_coverages.py -v`
Expected: 5 PASS (3 from Task 1 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/config.py tests/test_sub_coverages.py
git commit -m "feat: add Business Owners Policy, Employers Liability to config defaults"
```

---

### Task 3: Coverage Alias Update — BOP → Business Owners Policy

**Files:**
- Modify: `src/policydb/utils.py` ~line 159

- [ ] **Step 1: Update BOP aliases**

In `src/policydb/utils.py`, find and update these entries in `_COVERAGE_ALIASES`:

```python
# Change FROM:
"bop": "Property / Builders Risk",
"bop policy": "Property / Builders Risk",
"businessowners": "Property / Builders Risk",
"businessowners policy": "Property / Builders Risk",
"business owners policy": "Property / Builders Risk",

# Change TO:
"bop": "Business Owners Policy",
"bop policy": "Business Owners Policy",
"businessowners": "Business Owners Policy",
"businessowners policy": "Business Owners Policy",
"business owners policy": "Business Owners Policy",
```

Also check for `"package policy"` — it currently maps to "Property / Builders Risk" (~line 314). Leave that one as-is since "package policy" is ambiguous and could mean any package, not specifically a BOP.

- [ ] **Step 2: Write test**

Append to `tests/test_sub_coverages.py`:

```python
from policydb.utils import normalize_coverage_type


def test_bop_normalizes_to_business_owners():
    """BOP variants normalize to Business Owners Policy, not Property."""
    assert normalize_coverage_type("BOP") == "Business Owners Policy"
    assert normalize_coverage_type("bop policy") == "Business Owners Policy"
    assert normalize_coverage_type("businessowners") == "Business Owners Policy"
    assert normalize_coverage_type("Business Owners Policy") == "Business Owners Policy"


def test_property_aliases_unchanged():
    """Property aliases still normalize correctly (regression check)."""
    assert normalize_coverage_type("commercial property") == "Property / Builders Risk"
    assert normalize_coverage_type("building") == "Property / Builders Risk"
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_sub_coverages.py -v`
Expected: 7 PASS

- [ ] **Step 4: Commit**

```bash
git add src/policydb/utils.py tests/test_sub_coverages.py
git commit -m "feat: update BOP coverage aliases to normalize to Business Owners Policy"
```

---

### Task 4: Sub-Coverage CRUD Endpoints + Auto-Generation

**Files:**
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Add helper function to get sub-coverages for a policy**

Add near the top of `policies.py` (after imports, before routes):

```python
def _get_sub_coverages(conn, policy_id: int) -> list[dict]:
    """Return sub-coverages for a policy, ordered by sort_order."""
    rows = conn.execute(
        "SELECT id, coverage_type, sort_order "
        "FROM policy_sub_coverages WHERE policy_id = ? ORDER BY sort_order, id",
        (policy_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _auto_generate_sub_coverages(conn, policy_id: int, policy_type: str):
    """Insert auto-sub-coverages based on config mapping. Skips duplicates."""
    import policydb.config as cfg
    auto_map = cfg.get("auto_sub_coverages", {})
    sub_types = auto_map.get(policy_type, [])
    for i, ctype in enumerate(sub_types):
        conn.execute(
            "INSERT OR IGNORE INTO policy_sub_coverages (policy_id, coverage_type, sort_order) "
            "VALUES (?, ?, ?)",
            (policy_id, ctype, i),
        )
    if sub_types:
        conn.commit()
```

- [ ] **Step 2: Call auto-generation in policy creation endpoint**

In the `policy_new_post` function (~line 4306), after the policy INSERT and before the redirect, add:

```python
# Auto-generate sub-coverages if configured
_auto_generate_sub_coverages(conn, new_policy_id, policy_type)
```

Find where `new_policy_id` (or equivalent) is obtained after the INSERT — it may be `cursor.lastrowid` or fetched by policy_uid. Insert the call there.

- [ ] **Step 3: Add GET endpoint for sub-coverages**

Add new route (before any parameterized `/{uid}` catch-all routes):

```python
@router.get("/{uid}/sub-coverages")
async def get_sub_coverages(uid: str, conn=Depends(get_db)):
    row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return _get_sub_coverages(conn, row["id"])
```

- [ ] **Step 4: Add POST endpoint to add a sub-coverage**

```python
@router.post("/{uid}/sub-coverages")
async def add_sub_coverage(uid: str, request: Request, conn=Depends(get_db)):
    row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404)
    body = await request.json()
    coverage_type = body.get("coverage_type", "").strip()
    if not coverage_type:
        return JSONResponse({"ok": False, "error": "coverage_type required"}, 400)
    # Get next sort_order
    max_sort = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM policy_sub_coverages WHERE policy_id = ?",
        (row["id"],),
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO policy_sub_coverages (policy_id, coverage_type, sort_order) "
        "VALUES (?, ?, ?)",
        (row["id"], coverage_type, max_sort + 1),
    )
    conn.commit()
    subs = _get_sub_coverages(conn, row["id"])
    return {"ok": True, "sub_coverages": subs}
```

- [ ] **Step 5: Add DELETE endpoint to remove a sub-coverage**

```python
@router.delete("/{uid}/sub-coverages/{sub_id}")
async def remove_sub_coverage(uid: str, sub_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute(
        "DELETE FROM policy_sub_coverages WHERE id = ? AND policy_id = ?",
        (sub_id, row["id"]),
    )
    conn.commit()
    subs = _get_sub_coverages(conn, row["id"])
    return {"ok": True, "sub_coverages": subs}
```

- [ ] **Step 6: Write tests for auto-generation and CRUD**

Append to `tests/test_sub_coverages.py`:

```python
def test_auto_generate_wc_creates_el(db):
    """Creating a WC policy auto-inserts Employers Liability sub-coverage."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-WC', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC'").fetchone()[0]

    # Import and call the helper directly
    from policydb.web.routes.policies import _auto_generate_sub_coverages
    _auto_generate_sub_coverages(db, pid, "Workers Compensation")

    rows = db.execute(
        "SELECT coverage_type FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert [r[0] for r in rows] == ["Employers Liability"]


def test_auto_generate_no_op_for_gl(db):
    """GL has no auto-sub-coverages configured."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-GL', 0, 'General Liability')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-GL'").fetchone()[0]

    from policydb.web.routes.policies import _auto_generate_sub_coverages
    _auto_generate_sub_coverages(db, pid, "General Liability")

    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 0


def test_auto_generate_idempotent(db):
    """Calling auto-generate twice doesn't create duplicates."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-WC2', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC2'").fetchone()[0]

    from policydb.web.routes.policies import _auto_generate_sub_coverages
    _auto_generate_sub_coverages(db, pid, "Workers Compensation")
    _auto_generate_sub_coverages(db, pid, "Workers Compensation")

    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 1
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_sub_coverages.py -v`
Expected: 10 PASS

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/policies.py tests/test_sub_coverages.py
git commit -m "feat: sub-coverage CRUD endpoints + auto-generation on policy create"
```

---

### Task 5: Policy Edit UI — Sub-Coverage Pill/Tag Input

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_details.html` ~line 201
- Modify: `src/policydb/web/routes/policies.py` (pass sub_coverages to template context)

- [ ] **Step 1: Pass sub-coverages to the policy detail template context**

In `policies.py`, find where the policy detail page is rendered (the function that returns `_tab_details.html` or the parent `detail.html`). Add `sub_coverages` to the template context:

```python
sub_coverages = _get_sub_coverages(conn, policy["id"])
# ... in the template response:
"sub_coverages": sub_coverages,
```

- [ ] **Step 2: Add sub-coverage pill/tag section to `_tab_details.html`**

After the policy_type select field (~line 201), add:

```html
{# ── Sub-Coverages ── #}
<div class="sm:col-span-2 lg:col-span-3 mt-2" id="sub-coverages-section">
  <label class="field-label">Sub-Coverages</label>
  <div class="flex flex-wrap items-center gap-2 mt-1" id="sub-coverage-pills">
    {% for sc in sub_coverages %}
    <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm bg-blue-50 text-blue-700 border border-blue-200"
          data-sub-id="{{ sc.id }}">
      {{ sc.coverage_type }}
      <button type="button" class="ml-1 text-blue-400 hover:text-red-500 no-print"
              onclick="removeSubCoverage('{{ policy.policy_uid }}', {{ sc.id }}, this.parentElement)"
              title="Remove">&times;</button>
    </span>
    {% endfor %}
    <div class="relative no-print" id="add-sub-coverage-wrapper">
      <input type="text" id="add-sub-coverage-input"
             class="border border-dashed border-gray-300 rounded-full px-3 py-1 text-sm w-48
                    focus:border-marsh focus:ring-1 focus:ring-marsh focus:outline-none"
             placeholder="+ Add sub-coverage..."
             autocomplete="off"
             data-policy-uid="{{ policy.policy_uid }}"
             data-options='{{ policy_types | tojson }}' />
      <div id="sub-coverage-dropdown"
           class="hidden absolute z-50 mt-1 w-64 max-h-48 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg">
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add JavaScript for sub-coverage add/remove**

At the bottom of `_tab_details.html` (or in a `<script>` block within the sub-coverages section):

```html
<script>
(function() {
  const input = document.getElementById('add-sub-coverage-input');
  const dropdown = document.getElementById('sub-coverage-dropdown');
  if (!input || !dropdown) return;

  const allTypes = JSON.parse(input.dataset.options || '[]');
  const policyUid = input.dataset.policyUid;

  function getExisting() {
    return Array.from(document.querySelectorAll('#sub-coverage-pills [data-sub-id]'))
      .map(el => el.textContent.trim().replace(/\u00d7$/, '').trim());
  }

  function renderDropdown(filter) {
    const existing = getExisting();
    const filtered = allTypes.filter(t =>
      !existing.includes(t) && t.toLowerCase().includes(filter.toLowerCase())
    );
    dropdown.innerHTML = filtered.map(t =>
      '<div class="px-3 py-2 hover:bg-blue-50 cursor-pointer text-sm" onclick="selectSubCoverage(this)"'
      + ' data-value="' + t.replace(/"/g, '&quot;') + '">' + t + '</div>'
    ).join('');
    dropdown.classList.toggle('hidden', filtered.length === 0);
  }

  input.addEventListener('focus', () => renderDropdown(input.value));
  input.addEventListener('input', () => renderDropdown(input.value));
  input.addEventListener('blur', () => setTimeout(() => dropdown.classList.add('hidden'), 200));

  window.selectSubCoverage = async function(el) {
    const coverageType = el.dataset.value;
    dropdown.classList.add('hidden');
    input.value = '';
    const resp = await fetch('/policies/' + policyUid + '/sub-coverages', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({coverage_type: coverageType}),
    });
    const data = await resp.json();
    if (data.ok) {
      rebuildPills(data.sub_coverages);
    }
  };

  window.removeSubCoverage = async function(uid, subId, pillEl) {
    const resp = await fetch('/policies/' + uid + '/sub-coverages/' + subId, {
      method: 'DELETE',
    });
    const data = await resp.json();
    if (data.ok) {
      rebuildPills(data.sub_coverages);
    }
  };

  function rebuildPills(subs) {
    const container = document.getElementById('sub-coverage-pills');
    const wrapper = document.getElementById('add-sub-coverage-wrapper');
    // Remove existing pills (not the input wrapper)
    container.querySelectorAll('[data-sub-id]').forEach(el => el.remove());
    // Insert new pills before the input wrapper
    subs.forEach(sc => {
      const pill = document.createElement('span');
      pill.className = 'inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm bg-blue-50 text-blue-700 border border-blue-200';
      pill.dataset.subId = sc.id;
      pill.innerHTML = sc.coverage_type
        + ' <button type="button" class="ml-1 text-blue-400 hover:text-red-500 no-print"'
        + ' onclick="removeSubCoverage(\'' + policyUid + '\', ' + sc.id + ', this.parentElement)"'
        + ' title="Remove">&times;</button>';
      container.insertBefore(pill, wrapper);
    });
  }
})();
</script>
```

- [ ] **Step 4: Verify in browser**

Kill existing server: `lsof -ti:8000 | xargs kill -9 2>/dev/null`
Start: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/nervous-jones && pdb serve`
Navigate to a policy detail page. Confirm:
- Sub-coverages section appears below Line of Business
- Adding a sub-coverage creates a pill
- Removing a sub-coverage removes the pill
- Creating a WC policy auto-populates with EL pill

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/policies/_tab_details.html src/policydb/web/routes/policies.py
git commit -m "feat: sub-coverage pill/tag input on policy edit page"
```

---

### Task 6: Schedule of Insurance — Ghost Rows (Python-Level Injection)

**Files:**
- Modify: the route that builds the schedule of insurance (find the route serving the schedule page — likely in `routes/policies.py` or `routes/clients.py`)
- Modify: the schedule template (find the Jinja2 template that renders the schedule table)

**Important:** Do NOT modify `v_schedule` in `views.py`. Ghost rows are injected at the Python level per the spec. This avoids breaking chart exports and other downstream consumers of `v_schedule`.

- [ ] **Step 1: Add helper to fetch sub-coverages for multiple policies**

In `routes/policies.py` (or wherever schedule logic lives), add a helper:

```python
def _get_sub_coverages_by_policy_id(conn, policy_ids: list[int]) -> dict[int, list[str]]:
    """Return {policy_id: [coverage_type, ...]} for policies with sub-coverages."""
    if not policy_ids:
        return {}
    placeholders = ",".join("?" * len(policy_ids))
    rows = conn.execute(
        f"SELECT policy_id, coverage_type FROM policy_sub_coverages "
        f"WHERE policy_id IN ({placeholders}) ORDER BY sort_order, id",
        policy_ids,
    ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["policy_id"], []).append(r["coverage_type"])
    return result
```

- [ ] **Step 2: Inject ghost rows in the schedule route**

In the route function that builds the schedule data, after fetching rows from `v_schedule`:

```python
# Fetch sub-coverages for all policies in the schedule
policy_ids = [r["id"] for r in schedule_rows if r.get("id")]
sub_cov_map = _get_sub_coverages_by_policy_id(conn, policy_ids)

# Build ghost rows and a package policies section
package_policies = []
ghost_rows = []  # (coverage_type, row_dict)

for row in schedule_rows:
    pid = row.get("id")
    subs = sub_cov_map.get(pid, [])
    if subs:
        # Mark this policy as having sub-coverages (for the Package section)
        row_dict = dict(row)
        row_dict["sub_coverages"] = subs
        package_policies.append(row_dict)
        # Create a ghost row for each sub-coverage
        for sc_type in subs:
            ghost = dict(row)
            ghost["is_package_ghost"] = True
            ghost["package_parent_type"] = row["policy_type"]
            ghost["display_policy_type"] = sc_type  # used for section grouping
            ghost_rows.append(ghost)

# Group schedule_rows by policy_type, then insert ghost_rows into the right groups
# Pass package_policies and ghost_rows to the template
```

Adapt variable names to match the actual route code. Read the route function first to understand how rows are grouped by coverage type before rendering.

- [ ] **Step 3: Update schedule template to render ghost rows**

In the template, when iterating rows within a coverage-type section, check for ghost rows:

```html
{% if row.is_package_ghost %}
<tr class="bg-gray-50/50">
  <td class="text-gray-400 italic">
    {{ row.policy_uid }}
    <span class="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full ml-1">Package</span>
  </td>
  <td class="text-gray-400 italic">{{ row.carrier }}</td>
  <td class="text-gray-400 italic">{{ row.effective_date }}</td>
  <td class="text-gray-400 italic">{{ row.expiration_date }}</td>
  <td class="text-gray-400 italic text-right">&mdash;</td>
</tr>
{% else %}
  {# ... existing row rendering ... #}
{% endif %}
```

- [ ] **Step 4: Add Package Policies section at the top of the schedule**

Before the per-type sections, render a "Package Policies" section using `package_policies`:

```html
{% if package_policies %}
<div class="mb-6">
  <div class="bg-blue-50 px-4 py-2 font-semibold text-gray-800 border-b-2 border-blue-400 flex items-center gap-2">
    Package Policies
  </div>
  <table class="w-full text-sm">
    {# ... table header ... #}
    {% for row in package_policies %}
    <tr class="border-b border-gray-100">
      <td class="px-4 py-2 font-medium">{{ row.policy_uid }}</td>
      <td class="px-4 py-2">{{ row.carrier }}</td>
      <td class="px-4 py-2">
        {% for sc in row.sub_coverages %}
        <span class="inline-block bg-blue-50 text-blue-700 text-xs px-2 py-0.5 rounded-full border border-blue-200 mr-1">{{ sc }}</span>
        {% endfor %}
      </td>
      <td class="px-4 py-2">{{ row.effective_date }}</td>
      <td class="px-4 py-2">{{ row.expiration_date }}</td>
      <td class="px-4 py-2 text-right font-medium">{{ row.premium | currency }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}
```

- [ ] **Step 5: Test visually in browser**

Kill existing server: `lsof -ti:8000 | xargs kill -9 2>/dev/null`
Start: `pdb serve`

Navigate to a client's schedule of insurance. Add a BOP with sub-coverages. Confirm:
- Package Policies section shows at top with sub-coverage pills
- Ghost rows appear in each relevant coverage section
- Ghost rows have lighter styling and "Package" badge
- Premium shows "—" on ghost rows
- No double-counting in totals
- Chart exports and XLSX exports are NOT affected (they use `v_schedule` directly, which has no ghost rows)

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/ src/policydb/web/templates/
git commit -m "feat: schedule of insurance ghost rows for package sub-coverages"
```

---

### Task 7: Tower / Schematic — Sub-Coverage Layer Participation (Python-Level)

**Files:**
- Modify: `src/policydb/web/routes/programs.py` (tower data building + policy picker)
- Modify: tower template(s)

**Important:** Do NOT modify `v_tower` in `views.py`. Sub-coverage layer injection happens at the Python level, same as the schedule approach.

- [ ] **Step 1: Inject package layers in the tower route**

In `programs.py`, find where the tower/schematic data is built (the route that reads from `v_tower` and splits policies into "underlying" vs "excess"). After fetching tower rows:

```python
# Check for package policies with umbrella/excess sub-coverages
tower_policy_ids = [r["id"] for r in tower_rows if r.get("id")]
sub_cov_map = _get_sub_coverages_by_policy_id(conn, tower_policy_ids)

# Also find policies NOT already in the tower but with umbrella sub-coverages
# that have this tower_group assigned
umbrella_sub_types = {"Umbrella / Excess"}
for row in tower_rows:
    pid = row.get("id")
    subs = sub_cov_map.get(pid, [])
    has_umbrella_sub = any(s in umbrella_sub_types for s in subs)
    if has_umbrella_sub:
        row_dict = dict(row)
        row_dict["is_package_ghost"] = True
        row_dict["package_parent_type"] = row["policy_type"]
        row_dict["sub_coverages"] = subs
        # The policy already has layer_position/tower_group set,
        # so it participates as-is in the tower stacking
```

Read the actual route function first to understand the exact data structures used for underlying/excess splitting. Adapt variable names accordingly.

Import or reuse the `_get_sub_coverages_by_policy_id` helper from Task 6 (or move it to a shared location like `queries.py`).

- [ ] **Step 2: Update tower template for package layer visual treatment**

In the tower template, for layer blocks where `is_package_ghost` is truthy:

```html
{% if layer.is_package_ghost %}
<div class="border-2 border-indigo-400 rounded-lg p-4 bg-indigo-50 text-center relative">
  <span class="absolute top-2 right-3 text-xs bg-indigo-100 text-indigo-600 px-2 py-0.5 rounded-full font-semibold">Package</span>
  <div class="text-xs text-indigo-500 uppercase tracking-wide">{{ layer.layer_position or 'Umbrella' }}</div>
  <div class="font-bold text-gray-800">{{ layer.policy_uid }} — {{ layer.carrier }}</div>
  <div class="text-xs text-indigo-500 mt-0.5">via {{ layer.package_parent_type }}</div>
  <div class="text-sm text-gray-500 mt-1">
    <span>{{ layer.limit_amount | currency_short if layer.limit_amount else '—' }}</span>
    <span class="italic text-gray-400 ml-4">pkg premium</span>
  </div>
</div>
{% else %}
  {# ... existing layer rendering ... #}
{% endif %}
```

- [ ] **Step 3: Update schematic entry page policy picker**

In `programs.py`, the policy picker that shows assignable policies for a tower should include package policies with tower-eligible sub-coverages. Query `policy_sub_coverages` to find policies with "Umbrella / Excess" sub-coverage and add a "Package" indicator next to these in the picker dropdown.

- [ ] **Step 4: Test visually in browser**

Create a BOP with an "Umbrella / Excess" sub-coverage. Assign it to a tower group. Verify:
- Layer appears with purple border and "Package" badge
- "via Business Owners Policy" subtitle
- "pkg premium" instead of dollar amount
- Click navigates to policy detail

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/programs.py src/policydb/web/templates/
git commit -m "feat: tower/schematic participation for package sub-coverages"
```

---

### Task 8: Coverage Matrix — Sub-Coverage Columns

**Files:**
- Modify: `src/policydb/queries.py` ~line 1540 (`get_linked_group_overview`)

- [ ] **Step 1: Add sub-coverage entries to the coverage matrix**

Read `get_linked_group_overview()` in `queries.py` (~line 1515) to understand its exact data structure. The function builds a `matrix` dict keyed by `policy_type`, mapping `client_id` to a list of carrier names.

After the main query that populates the matrix, add a second query for sub-coverages:

```python
# Also include sub-coverage entries in the matrix
# Build client_ids list from the members already fetched
client_id_list = [m["id"] for m in members]  # adapt to actual variable name
if client_id_list:
    placeholders = ",".join("?" * len(client_id_list))
    sub_rows = conn.execute(
        f"SELECT sc.coverage_type AS policy_type, p.client_id, p.carrier "
        f"FROM policy_sub_coverages sc "
        f"JOIN policies p ON p.id = sc.policy_id "
        f"WHERE p.client_id IN ({placeholders}) "
        f"  AND p.archived = 0 "
        f"  AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)",
        client_id_list,
    ).fetchall()
    for r in sub_rows:
        carrier = (r["carrier"] or "") + " [Pkg]"
        matrix[r["policy_type"]][r["client_id"]].append(carrier)
```

The `[Pkg]` suffix lets the template distinguish package-sourced entries. Read the actual variable names from the function before implementing.

- [ ] **Step 2: Test visually**

Navigate to a linked group briefing page with a client that has a BOP. Confirm the BOP's sub-coverages appear in the correct coverage matrix columns with a package indicator.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/queries.py
git commit -m "feat: coverage matrix includes package sub-coverage columns"
```

---

### Task 9: Email Template Token — `{{sub_coverages}}`

**Files:**
- Modify: `src/policydb/email_templates.py` ~line 307, ~line 732

- [ ] **Step 1: Add sub_coverages to `policy_context()`**

In `policy_context()` (~line 307), after fetching the policy row, add:

```python
# Sub-coverages
sub_rows = conn.execute(
    "SELECT coverage_type FROM policy_sub_coverages "
    "WHERE policy_id = ? ORDER BY sort_order, id",
    (row["id"],),
).fetchall()
tokens["sub_coverages"] = ", ".join(r["coverage_type"] for r in sub_rows) if sub_rows else ""
```

- [ ] **Step 2: Add to `CONTEXT_TOKEN_GROUPS`**

In the "policy" context, "Policy" group (~line 732), add after the `("policy_type", "Policy Type")` entry:

```python
("sub_coverages", "Sub-Coverages"),
```

- [ ] **Step 3: Write test**

Append to `tests/test_sub_coverages.py`:

```python
def test_sub_coverages_email_token(db):
    """policy_context returns comma-separated sub-coverages token."""
    db.execute(
        "INSERT INTO clients (id, name) VALUES (1, 'Test Client')"
    )
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-TOK', 1, 'Business Owners Policy')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-TOK'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, sort_order) VALUES (?, ?, ?)",
        (pid, "General Liability", 0),
    )
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, sort_order) VALUES (?, ?, ?)",
        (pid, "Property / Builders Risk", 1),
    )
    db.commit()

    from policydb.email_templates import policy_context
    tokens = policy_context(db, "POL-TOK")
    assert tokens["sub_coverages"] == "General Liability, Property / Builders Risk"


def test_sub_coverages_token_empty_when_none(db):
    """policy_context returns empty string when no sub-coverages."""
    db.execute(
        "INSERT INTO clients (id, name) VALUES (2, 'Another Client')"
    )
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-NONE', 2, 'General Liability')"
    )
    db.commit()

    from policydb.email_templates import policy_context
    tokens = policy_context(db, "POL-NONE")
    assert tokens["sub_coverages"] == ""
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sub_coverages.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/email_templates.py tests/test_sub_coverages.py
git commit -m "feat: add sub_coverages email template token"
```

---

### Task 10: Integration Test + Full QA

**Files:**
- Modify: `tests/test_sub_coverages.py` (add integration test)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All existing tests still pass, no regressions.

- [ ] **Step 2: Visual QA in browser**

Kill existing server: `lsof -ti:8000 | xargs kill -9 2>/dev/null`
Start: `pdb serve`

Test checklist:
1. Create a new policy with type "Business Owners Policy" → add sub-coverages GL, Property, Inland Marine
2. Create a new WC policy → verify EL auto-populates as sub-coverage
3. Remove EL sub-coverage from WC → verify it's removable
4. Navigate to client's schedule → verify Package section + ghost rows
5. Navigate to tower/schematic → verify package layer with badge (if applicable)
6. Check email compose → verify `{{sub_coverages}}` token appears in pill toolbar
7. Import a CSV with "BOP" as coverage type → verify it imports as "Business Owners Policy"

- [ ] **Step 3: Final commit**

If any QA fixes were needed, commit them:

```bash
git add -A
git commit -m "fix: QA fixes for package sub-coverages feature"
```
