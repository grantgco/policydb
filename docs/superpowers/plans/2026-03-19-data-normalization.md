# Field-Level Data Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add normalize-on-save for coverage types, policy numbers, client names, and addresses. Add fuzzy duplicate detection for clients and contacts. Run a one-time hygiene migration on existing data.

**Architecture:** New normalization functions in `utils.py` following the existing `format_phone()` pattern. Coverage aliases moved from `reconciler.py` to `utils.py`. Normalizers applied at every save path (routes + importer). Duplicate detection via RapidFuzz with HTMX warning partials. Hygiene migration runs once via SQL marker + Python function.

**Tech Stack:** SQLite, FastAPI, Jinja2, RapidFuzz, HTMX

**Spec:** `docs/superpowers/specs/2026-03-19-data-normalization-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `tests/test_data_normalization.py` | All tests for this feature |
| Modify | `src/policydb/utils.py` | New normalizer functions + `_COVERAGE_ALIASES` + `_LEGAL_SUFFIX_MAP` + `_STATE_NAME_TO_ABBR` |
| Modify | `src/policydb/reconciler.py` | Remove `_COVERAGE_ALIASES`, import from utils |
| Modify | `src/policydb/web/routes/policies.py` | Normalize policy_type, policy_number, address fields on save |
| Modify | `src/policydb/web/routes/clients.py` | Normalize client name, address fields; duplicate detection |
| Modify | `src/policydb/web/routes/contacts.py` | Contact duplicate detection |
| Modify | `src/policydb/web/routes/reconcile.py` | Normalize policy_type, policy_number on create/batch |
| Modify | `src/policydb/importer.py` | Normalize on import |
| Modify | `src/policydb/db.py` | Hygiene migration registration |
| Create | `src/policydb/migrations/062_normalize_existing_data.sql` | Migration marker |

---

### Task 1: Normalization Functions + Tests

**Files:**
- Modify: `src/policydb/utils.py`
- Create: `tests/test_data_normalization.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_data_normalization.py
"""Tests for field-level data normalization."""

import pytest


def test_normalize_coverage_type_alias():
    from policydb.utils import normalize_coverage_type
    assert normalize_coverage_type("cgl") == "General Liability"
    assert normalize_coverage_type("CGL") == "General Liability"
    assert normalize_coverage_type("wc") == "Workers Compensation"
    assert normalize_coverage_type("D&O") == "Directors & Officers"


def test_normalize_coverage_type_unknown():
    from policydb.utils import normalize_coverage_type
    assert normalize_coverage_type("cyber liability") == "Cyber Liability"
    assert normalize_coverage_type("") == ""


def test_normalize_policy_number():
    from policydb.utils import normalize_policy_number
    assert normalize_policy_number("pol-123") == "POL-123"
    assert normalize_policy_number("  abc.456  ") == "ABC.456"
    assert normalize_policy_number("") == ""


def test_normalize_client_name():
    from policydb.utils import normalize_client_name
    assert normalize_client_name("acme corp") == "Acme Corp."
    assert normalize_client_name("ACME HOLDINGS") == "Acme Holdings"
    assert normalize_client_name("US  Steel  inc") == "US Steel Inc."
    assert normalize_client_name("  delta   services   llc  ") == "Delta Services LLC"
    assert normalize_client_name("") == ""


def test_normalize_client_name_preserves_short_acronyms():
    from policydb.utils import normalize_client_name
    result = normalize_client_name("ABC Corp")
    assert result == "ABC Corp."
    result2 = normalize_client_name("US Steel")
    assert result2 == "US Steel"


def test_format_zip():
    from policydb.utils import format_zip
    assert format_zip("78701") == "78701"
    assert format_zip("787014567") == "78701-4567"
    assert format_zip("787") == "787"
    assert format_zip("78701-AB") == "78701"
    assert format_zip("") == ""


def test_format_state():
    from policydb.utils import format_state
    assert format_state("TX") == "TX"
    assert format_state("tx") == "TX"
    assert format_state("Texas") == "TX"
    assert format_state("texas") == "TX"
    assert format_state("XX") == "XX"
    assert format_state("") == ""


def test_format_city():
    from policydb.utils import format_city
    assert format_city("austin") == "Austin"
    assert format_city("  san   antonio  ") == "San Antonio"
    assert format_city("NEW YORK") == "New York"
    assert format_city("") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_normalization.py -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Add normalization functions to utils.py**

Add all functions from the spec to `src/policydb/utils.py`:
- `_LEGAL_SUFFIX_MAP` dict
- `_STATE_NAME_TO_ABBR` dict
- `normalize_coverage_type(raw)` — uses `_COVERAGE_ALIASES` (added in Task 2)
- `normalize_policy_number(raw)` — uppercase + trim
- `normalize_client_name(raw)` — title-case + legal suffix normalization
- `format_zip(raw)` — digit extraction + 5/9 formatting
- `format_state(raw)` — 2-letter code or name lookup
- `format_city(raw)` — title-case + collapse spaces

For `normalize_coverage_type`, temporarily define a small placeholder `_COVERAGE_ALIASES = {}` dict — Task 2 will move the full dict from reconciler.py.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_data_normalization.py -v`
Expected: `test_normalize_coverage_type_alias` FAILS (empty dict), others PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/utils.py tests/test_data_normalization.py
git commit -m "feat: add normalization functions to utils (format_zip, format_state, format_city, normalize_client_name, normalize_policy_number)"
```

---

### Task 2: Move Coverage Aliases to utils.py

**Files:**
- Modify: `src/policydb/utils.py`
- Modify: `src/policydb/reconciler.py`
- Test: `tests/test_data_normalization.py`

- [ ] **Step 1: Move `_COVERAGE_ALIASES` from reconciler.py to utils.py**

In `src/policydb/reconciler.py`, find the `_COVERAGE_ALIASES` dict (250+ entries, starts around line 19). Cut the entire dict. Paste it into `src/policydb/utils.py` as a module-level constant, replacing the placeholder from Task 1.

- [ ] **Step 2: Update reconciler.py to import from utils**

In `src/policydb/reconciler.py`, replace the local `_normalize_coverage` function:

```python
from policydb.utils import _COVERAGE_ALIASES, normalize_coverage_type

def _normalize_coverage(value: str) -> str:
    return normalize_coverage_type(value)
```

- [ ] **Step 3: Run ALL tests**

Run: `pytest tests/ -v`
Expected: All tests PASS including `test_normalize_coverage_type_alias`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/utils.py src/policydb/reconciler.py
git commit -m "feat: move _COVERAGE_ALIASES to utils.py, reconciler imports from utils"
```

---

### Task 3: Normalize on Policy Save

**Files:**
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Add normalizer imports**

At top of `policies.py`, add:

```python
from policydb.utils import normalize_coverage_type, normalize_policy_number, format_city, format_state, format_zip
```

- [ ] **Step 2: Normalize in `policy_edit_post`**

Find where `policy_type` and `policy_number` are used in the UPDATE SQL parameters. Before the UPDATE, normalize:

```python
    policy_type = normalize_coverage_type(policy_type)
    policy_number = normalize_policy_number(policy_number) if policy_number else ""
```

For address fields (if `exposure_address`, `exposure_city`, `exposure_state`, `exposure_zip` are in the function parameters):

```python
    exposure_address = exposure_address.strip() if exposure_address else ""
    exposure_city = format_city(exposure_city) if exposure_city else ""
    exposure_state = format_state(exposure_state) if exposure_state else ""
    exposure_zip = format_zip(exposure_zip) if exposure_zip else ""
```

- [ ] **Step 3: Normalize in `policy_new_post`**

Same normalizations before the INSERT.

- [ ] **Step 4: Normalize in inline row edit PATCH endpoints**

Find any PATCH endpoints that save `policy_type` or `policy_number` (e.g., the dashboard row edit, renewal row edit). Apply the same normalization. Return `{"ok": true, "formatted": normalized_value}` so `flashCell()` fires.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: normalize coverage type, policy number, address on policy save"
```

---

### Task 4: Normalize on Client Save

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 1: Add normalizer imports**

```python
from policydb.utils import normalize_client_name, format_city, format_state, format_zip
```

- [ ] **Step 2: Normalize client name on create and edit**

Find client creation endpoints (search for `INSERT INTO clients`). Before insert, normalize:

```python
    name = normalize_client_name(name)
```

Find client edit endpoints. Same normalization before UPDATE.

- [ ] **Step 3: Normalize address fields in project header save**

Find `project_note_save` or similar function that saves exposure address fields. Apply `format_city()`, `format_state()`, `format_zip()` before the UPDATE.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: normalize client name and address fields on save"
```

---

### Task 5: Normalize on Reconcile + Import

**Files:**
- Modify: `src/policydb/web/routes/reconcile.py`
- Modify: `src/policydb/importer.py`

- [ ] **Step 1: Normalize in reconcile create endpoints**

In `reconcile.py`, find `reconcile_create`, `batch_create`, and `batch_create_program`. Before each INSERT, normalize:

```python
from policydb.utils import normalize_coverage_type, normalize_policy_number

policy_type = normalize_coverage_type(policy_type)
policy_number = normalize_policy_number(policy_number) if policy_number else ""
```

- [ ] **Step 2: Normalize in importer**

In `src/policydb/importer.py`, find `PolicyImporter.import_file()` or `_import_row()`. After column mapping, normalize:

```python
from policydb.utils import normalize_coverage_type, normalize_policy_number, normalize_client_name

# After parsing the row:
row["policy_type"] = normalize_coverage_type(row.get("policy_type", ""))
row["policy_number"] = normalize_policy_number(row.get("policy_number", ""))
row["client_name"] = normalize_client_name(row.get("client_name", "")) if row.get("client_name") else ""
```

- [ ] **Step 3: Add import-time duplicate detection**

In `src/policydb/importer.py`, in the import flow where new clients are created (before the INSERT), add duplicate detection:

```python
from policydb.utils import normalize_client_name

# After normalizing client_name, check for duplicates
dupes = _find_similar_clients(conn, client_name)
if dupes:
    # Flag in import results — don't block, just warn
    row["_duplicate_warning"] = f"Possible duplicate: {dupes[0]['name']} ({dupes[0]['score']}%)"
```

Import `_find_similar_clients` from wherever it's defined (clients.py or move to utils.py/queries.py for shared access). The warning is attached to the row dict and displayed in import results.

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/reconcile.py src/policydb/importer.py
git commit -m "feat: normalize fields on reconcile/import, add import duplicate detection"
```

---

### Task 6: Client Duplicate Detection

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 1: Add `find_similar_clients` function**

Add to `clients.py`:

```python
def _find_similar_clients(conn, name: str, threshold: int = 85) -> list[dict]:
    """Find existing clients with names similar to the given name."""
    from rapidfuzz import fuzz
    normalized = normalize_client_name(name)
    existing = conn.execute(
        "SELECT id, name, industry_segment FROM clients WHERE archived = 0"
    ).fetchall()
    matches = []
    for r in existing:
        score = fuzz.WRatio(normalized, r["name"])
        if score >= threshold:
            matches.append({"id": r["id"], "name": r["name"],
                           "industry": r["industry_segment"], "score": round(score)})
    return sorted(matches, key=lambda x: -x["score"])
```

- [ ] **Step 2: Add duplicate check to client create endpoint**

Find the client creation endpoint. Before the INSERT, check for duplicates:

```python
    force = request.query_params.get("force", "")
    if not force:
        dupes = _find_similar_clients(conn, name)
        if dupes:
            return templates.TemplateResponse("clients/_duplicate_client_warning.html", {
                "request": request,
                "name": name,
                "matches": dupes,
                # Pass all original form fields so "Create anyway" can resubmit
            })
```

- [ ] **Step 3: Create `_duplicate_client_warning.html`**

Create `src/policydb/web/templates/clients/_duplicate_client_warning.html`:

```html
<div class="bg-amber-50 border border-amber-200 rounded-lg p-4">
  <p class="text-sm font-medium text-amber-800">Similar clients found:</p>
  <ul class="mt-2 space-y-1">
    {% for m in matches %}
    <li class="text-sm text-amber-700">
      <a href="/clients/{{ m.id }}" class="text-marsh hover:underline font-medium">{{ m.name }}</a>
      <span class="text-xs text-amber-500">({{ m.score }}% match{% if m.industry %} · {{ m.industry }}{% endif %})</span>
    </li>
    {% endfor %}
  </ul>
  <div class="flex gap-2 mt-3">
    <a href="/clients/{{ matches[0].id }}" class="text-xs bg-marsh text-white px-3 py-1.5 rounded hover:bg-marsh-light">Use existing</a>
    <button type="button" onclick="/* resubmit with force=1 */"
      class="text-xs text-gray-600 border border-gray-300 px-3 py-1.5 rounded hover:bg-gray-100">Create anyway</button>
  </div>
</div>
```

The "Create anyway" button should resubmit the original form with `?force=1` appended. Implementation depends on how the create form works (standard form POST vs HTMX).

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_duplicate_client_warning.html
git commit -m "feat: fuzzy duplicate detection for client creation"
```

---

### Task 7: Contact Duplicate Detection

**Files:**
- Modify: `src/policydb/web/routes/contacts.py` (or clients.py where contacts are created)

- [ ] **Step 1: Add `find_similar_contacts` function**

```python
def _find_similar_contacts(conn, name: str, threshold: int = 85, source: str = "client") -> list[dict]:
    """Find existing contacts with names similar to the given name."""
    from rapidfuzz import fuzz
    existing = conn.execute(
        """SELECT co.id, co.name, co.email, co.phone,
                  GROUP_CONCAT(DISTINCT c.name) AS client_names
           FROM contacts co
           LEFT JOIN contact_client_assignments cca ON cca.contact_id = co.id
           LEFT JOIN clients c ON cca.client_id = c.id
           GROUP BY co.id"""
    ).fetchall()
    matches = []
    for r in existing:
        score = fuzz.WRatio(name.strip(), r["name"])
        if score >= threshold:
            matches.append({
                "id": r["id"], "name": r["name"],
                "email": r["email"], "phone": r["phone"],
                "client_names": r["client_names"] or "",
                "score": round(score),
                "match_type": "name",
                "source": source,
            })
    return sorted(matches, key=lambda x: -x["score"])
```

- [ ] **Step 2: Extend `_duplicate_warning.html` template**

In `src/policydb/web/templates/contacts/_duplicate_warning.html`, add display for:
- Fuzzy match score (e.g., "92% match")
- Which clients the contact is assigned to (`client_names`)
- "Use existing" and "Create anyway" action buttons

The function from Step 1 provides `score`, `client_names`, `match_type`, and `source` fields.

- [ ] **Step 3: Add duplicate check to contact creation paths**

Find where contacts are created (contact matrix add-row, contact create endpoints). Before INSERT, check for duplicates and return the extended `_duplicate_warning.html` partial if matches found.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/contacts.py src/policydb/web/routes/clients.py
git commit -m "feat: fuzzy duplicate detection for contact creation"
```

---

### Task 8: Data Hygiene Migration

**Files:**
- Create: `src/policydb/migrations/062_normalize_existing_data.sql`
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Create SQL marker file**

```sql
-- 062_normalize_existing_data.sql
-- Marker for one-time data normalization hygiene pass.
-- Actual normalization runs as Python function in init_db().
SELECT 1;
```

- [ ] **Step 2: Register migration and add hygiene function in db.py**

Add 62 to `_KNOWN_MIGRATIONS`. Add the `if 62 not in applied` block that runs the SQL marker and then calls `_run_hygiene_062(conn)`.

The `_run_hygiene_062(conn)` function (defined above the migration block or as a helper):

```python
def _run_hygiene_062(conn):
    """One-time normalization of existing data."""
    from policydb.utils import (normalize_coverage_type, normalize_policy_number,
                                 normalize_client_name, format_zip, format_state, format_city)
    changed = {"policy_type": 0, "policy_number": 0, "client_name": 0,
               "zip": 0, "state": 0, "city": 0}

    for r in conn.execute("SELECT id, policy_type FROM policies WHERE policy_type IS NOT NULL").fetchall():
        n = normalize_coverage_type(r["policy_type"])
        if n != r["policy_type"]:
            conn.execute("UPDATE policies SET policy_type = ? WHERE id = ?", (n, r["id"]))
            changed["policy_type"] += 1

    for r in conn.execute("SELECT id, policy_number FROM policies WHERE policy_number IS NOT NULL AND policy_number != ''").fetchall():
        n = normalize_policy_number(r["policy_number"])
        if n != r["policy_number"]:
            conn.execute("UPDATE policies SET policy_number = ? WHERE id = ?", (n, r["id"]))
            changed["policy_number"] += 1

    for r in conn.execute("SELECT id, name FROM clients WHERE name IS NOT NULL").fetchall():
        n = normalize_client_name(r["name"])
        if n != r["name"]:
            conn.execute("UPDATE clients SET name = ? WHERE id = ?", (n, r["id"]))
            changed["client_name"] += 1

    for r in conn.execute("SELECT id, exposure_zip, exposure_state, exposure_city FROM policies WHERE exposure_zip IS NOT NULL OR exposure_state IS NOT NULL OR exposure_city IS NOT NULL").fetchall():
        updates = {}
        if r["exposure_zip"]:
            fmt = format_zip(r["exposure_zip"])
            if fmt != r["exposure_zip"]: updates["exposure_zip"] = fmt; changed["zip"] += 1
        if r["exposure_state"]:
            fmt = format_state(r["exposure_state"])
            if fmt != r["exposure_state"]: updates["exposure_state"] = fmt; changed["state"] += 1
        if r["exposure_city"]:
            fmt = format_city(r["exposure_city"])
            if fmt != r["exposure_city"]: updates["exposure_city"] = fmt; changed["city"] += 1
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE policies SET {set_clause} WHERE id = ?", (*updates.values(), r["id"]))

    conn.commit()
    total = sum(changed.values())
    if total > 0:
        print(f"[hygiene-062] Normalized {total} fields: {changed}")
```

Note: Migration number is 062 (not 060 as in the spec — 060 was used for `is_bor` and 061 for project pipeline).

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/062_normalize_existing_data.sql src/policydb/db.py
git commit -m "feat: one-time data hygiene migration normalizing existing records"
```

---

### Task 9: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual test**

Run: `policydb serve`

1. **Coverage normalization:** Create a new policy with type "cgl" → saves as "General Liability" with green flash
2. **Policy number:** Enter "pol-123" → saves as "POL-123" with flash
3. **Client name:** Create client "acme corp" → saves as "Acme Corp."
4. **Address:** Enter zip "787014567" → formats as "78701-4567". Enter state "texas" → formats as "TX"
5. **Duplicate client:** Try creating "Acme Corporation" when "Acme Corp." exists → warning appears
6. **Import:** Import a CSV with mixed coverage types → all normalized in DB
7. **Hygiene:** Restart server → check that existing dirty data was cleaned (one-time)

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for data normalization"
```
