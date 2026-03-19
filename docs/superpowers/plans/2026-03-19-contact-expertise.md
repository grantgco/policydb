# Contact Expertise & Specialty Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two-category expertise tagging (line + industry) for contacts with search/filter on contacts page, suggestions during policy assignment, and inline quick-tag from any context.

**Architecture:** New `contact_expertise` table with `category` column (line/industry). Config-managed tag lists. Pill-based UI for tagging. Expertise filter on contacts list. Suggested contacts highlighted in policy assignment pickers.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-19-contact-expertise-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/063_contact_expertise.sql` | Schema: expertise table + notes column |
| Create | `tests/test_contact_expertise.py` | All tests |
| Create | `src/policydb/web/templates/contacts/_expertise_pills.html` | Reusable pill tag editor partial |
| Modify | `src/policydb/db.py` | Register migration 063 |
| Modify | `src/policydb/config.py` | Add expertise_lines + expertise_industries defaults |
| Modify | `src/policydb/web/routes/contacts.py` | Expertise CRUD endpoints, filter query |
| Modify | `src/policydb/web/routes/settings.py` | Pass expertise config to settings context |
| Modify | `src/policydb/web/templates/settings.html` | Include two new list cards |
| Modify | `src/policydb/web/templates/contacts/list.html` | Filter pills + display tags |
| Modify | `src/policydb/web/templates/contacts/_row.html` | Display tags + quick-tag button |
| Modify | `src/policydb/web/templates/contacts/_internal_row.html` | Same |
| Modify | `src/policydb/web/templates/contacts/_client_contact_row.html` | Same |
| Modify | `src/policydb/web/routes/policies.py` | Load suggested contacts for assignment |
| Modify | `src/policydb/web/templates/policies/edit.html` | Show suggestions in contact pickers |

---

### Task 1: Migration + Config

**Files:**
- Create: `src/policydb/migrations/063_contact_expertise.sql`
- Modify: `src/policydb/db.py`
- Modify: `src/policydb/config.py`
- Create: `tests/test_contact_expertise.py`

- [ ] **Step 1: Write migration**

```sql
-- 063_contact_expertise.sql
CREATE TABLE IF NOT EXISTS contact_expertise (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category   TEXT NOT NULL DEFAULT 'line',
    tag        TEXT NOT NULL,
    UNIQUE(contact_id, category, tag)
);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_tag ON contact_expertise(tag);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_contact ON contact_expertise(contact_id);
ALTER TABLE contacts ADD COLUMN expertise_notes TEXT;
```

- [ ] **Step 2: Register in db.py**

Add 63 to `_KNOWN_MIGRATIONS` and add `if 63 not in applied` block.

- [ ] **Step 3: Add config defaults**

```python
"expertise_lines": [
    "Casualty", "Property", "Workers Compensation", "Professional Liability",
    "D&O", "Cyber", "Construction", "Environmental", "Marine",
    "Aviation", "Surety", "Executive Risk", "Employee Benefits",
],
"expertise_industries": [
    "Sports & Entertainment", "Construction", "Healthcare", "Real Estate",
    "Technology", "Manufacturing", "Hospitality", "Energy",
    "Financial Services", "Public Entity", "Transportation",
],
```

- [ ] **Step 4: Write tests**

```python
# tests/test_contact_expertise.py
"""Tests for contact expertise tracking."""
import pytest
from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_contact_expertise_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "contact_expertise" in tables
    conn.close()


def test_expertise_notes_column(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(contacts)").fetchall()]
    assert "expertise_notes" in cols
    conn.close()


def test_expertise_tagging(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('John Smith')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Casualty')", (cid,))
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'industry', 'Sports & Entertainment')", (cid,))
    conn.commit()
    tags = conn.execute("SELECT category, tag FROM contact_expertise WHERE contact_id = ? ORDER BY category", (cid,)).fetchall()
    assert len(tags) == 2
    assert tags[0]["category"] == "industry"
    assert tags[0]["tag"] == "Sports & Entertainment"
    assert tags[1]["category"] == "line"
    assert tags[1]["tag"] == "Casualty"
    conn.close()


def test_expertise_unique_constraint(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('Jane Doe')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Property')", (cid,))
    conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Property')", (cid,))
    conn.close()


def test_expertise_cascade_delete(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('Bob Wilson')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'D&O')", (cid,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM contact_expertise WHERE contact_id = ?", (cid,)).fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 5: Run tests, commit**

```bash
pytest tests/test_contact_expertise.py -v
git add src/policydb/migrations/063_contact_expertise.sql src/policydb/db.py src/policydb/config.py tests/test_contact_expertise.py
git commit -m "feat: add contact_expertise table and config (migration 063)"
```

---

### Task 2: Expertise CRUD Endpoints

**Files:**
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Add expertise toggle endpoint**

```python
@router.post("/contacts/{contact_id}/expertise")
async def contact_expertise_toggle(
    request: Request,
    contact_id: int,
    conn=Depends(get_db),
):
    """Add or remove an expertise tag for a contact."""
    body = await request.json()
    category = body.get("category", "")
    tag = body.get("tag", "")
    action = body.get("action", "add")  # "add" or "remove"

    if category not in ("line", "industry") or not tag:
        return JSONResponse({"ok": False, "error": "Invalid"}, status_code=400)

    contact = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    if action == "remove":
        conn.execute("DELETE FROM contact_expertise WHERE contact_id = ? AND category = ? AND tag = ?",
                     (contact_id, category, tag))
    else:
        conn.execute("INSERT OR IGNORE INTO contact_expertise (contact_id, category, tag) VALUES (?, ?, ?)",
                     (contact_id, category, tag))
    conn.commit()

    # Return current tags
    tags = conn.execute("SELECT category, tag FROM contact_expertise WHERE contact_id = ?", (contact_id,)).fetchall()
    return JSONResponse({"ok": True, "tags": [dict(t) for t in tags]})
```

- [ ] **Step 2: Add expertise notes endpoint**

```python
@router.patch("/contacts/{contact_id}/expertise-notes")
async def contact_expertise_notes(
    request: Request,
    contact_id: int,
    conn=Depends(get_db),
):
    """Update expertise notes for a contact."""
    body = await request.json()
    value = body.get("value", "").strip()
    conn.execute("UPDATE contacts SET expertise_notes = ? WHERE id = ?", (value or None, contact_id))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})
```

- [ ] **Step 3: Add helper to load expertise tags for contacts**

```python
def _attach_expertise(conn, contacts: list[dict]) -> None:
    """Attach expertise tags to a list of contact dicts (mutates in place)."""
    if not contacts:
        return
    ids = [c["id"] for c in contacts if c.get("id")]
    if not ids:
        return
    rows = conn.execute(
        f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    tag_map = {}
    for r in rows:
        tag_map.setdefault(r["contact_id"], {"line": [], "industry": []})
        tag_map[r["contact_id"]][r["category"]].append(r["tag"])
    for c in contacts:
        cid = c.get("id")
        c["expertise_lines"] = tag_map.get(cid, {}).get("line", [])
        c["expertise_industries"] = tag_map.get(cid, {}).get("industry", [])
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/contacts.py
git commit -m "feat: expertise CRUD endpoints and tag loader"
```

---

### Task 3: Expertise Pills Partial + Contact Row Display

**Files:**
- Create: `src/policydb/web/templates/contacts/_expertise_pills.html`
- Modify: `src/policydb/web/templates/contacts/_row.html` (and similar contact row templates)

- [ ] **Step 1: Create reusable expertise pills partial**

Write `src/policydb/web/templates/contacts/_expertise_pills.html`:

A compact inline pill editor that can be included anywhere a contact row appears. Expects variables: `contact_id`, `line_tags` (list), `industry_tags` (list), `expertise_lines` (config), `expertise_industries` (config).

Shows existing tags as colored pills. A `⭐` button toggles the full pill editor for adding/removing tags.

- [ ] **Step 2: Add expertise display to contact rows**

In contact row templates (`_row.html`, `_internal_row.html`, `_client_contact_row.html`), after the contact name, include:

```html
{% if c.expertise_lines or c.expertise_industries %}
<span class="ml-1">
  {% for t in c.expertise_lines %}<span class="text-[9px] px-1 py-0.5 rounded bg-blue-50 text-blue-600">{{ t }}</span> {% endfor %}
  {% for t in c.expertise_industries %}<span class="text-[9px] px-1 py-0.5 rounded bg-green-50 text-green-600">{{ t }}</span> {% endfor %}
</span>
{% endif %}
```

Add the quick-tag `⭐` button that expands the inline pills editor.

- [ ] **Step 3: Pass expertise config to template contexts**

In routes that render contact rows, add:
```python
"expertise_lines": cfg.get("expertise_lines", []),
"expertise_industries": cfg.get("expertise_industries", []),
```

And call `_attach_expertise(conn, contacts)` before passing contacts to templates.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/contacts/_expertise_pills.html src/policydb/web/templates/contacts/_row.html src/policydb/web/templates/contacts/_internal_row.html src/policydb/web/templates/contacts/_client_contact_row.html src/policydb/web/routes/contacts.py src/policydb/web/routes/clients.py
git commit -m "feat: expertise pills display and quick-tag on contact rows"
```

---

### Task 4: Contacts List — Filter by Expertise

**Files:**
- Modify: `src/policydb/web/routes/contacts.py`
- Modify: `src/policydb/web/templates/contacts/list.html`

- [ ] **Step 1: Add filter query logic**

In the contacts list handler, accept `line` and `industry` query params. Filter contacts using EXISTS subqueries on `contact_expertise`.

```python
@router.get("/contacts", response_class=HTMLResponse)
def contacts_list(request: Request, line: str = "", industry: str = "", ...):
    # Build filtered query with EXISTS subqueries when filters set
    ...
```

- [ ] **Step 2: Add filter pill rows to contacts list template**

At the top of `contacts/list.html`, add two rows of filter pills:

```html
<div class="flex flex-wrap gap-1 mb-2">
  <span class="text-xs text-gray-500 mr-1">Line:</span>
  <a href="?line=" class="text-xs px-2 py-0.5 rounded-full border {{ 'bg-marsh text-white border-marsh' if not line_filter else 'border-gray-200 text-gray-500' }}">All</a>
  {% for t in expertise_lines %}
  <a href="?line={{ t }}&industry={{ industry_filter }}" class="text-xs px-2 py-0.5 rounded-full border {{ 'bg-blue-500 text-white border-blue-500' if line_filter == t else 'border-gray-200 text-gray-500 hover:border-blue-400' }}">{{ t }}</a>
  {% endfor %}
</div>
```

Same pattern for industry row with green colors.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/contacts.py src/policydb/web/templates/contacts/list.html
git commit -m "feat: filter contacts list by expertise line and industry"
```

---

### Task 5: Policy Assignment — Expertise Suggestions

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/templates/policies/edit.html`

- [ ] **Step 1: Load suggested contacts in policy edit handler**

In the `policy_edit_form` handler, query contacts whose expertise line tag matches the policy's `policy_type`:

```python
suggested_contact_ids = set()
if policy_dict.get("policy_type"):
    suggested = conn.execute("""
        SELECT DISTINCT ce.contact_id FROM contact_expertise ce
        WHERE ce.category = 'line' AND ce.tag = ?
    """, (policy_dict["policy_type"],)).fetchall()
    suggested_contact_ids = {r["contact_id"] for r in suggested}
```

Pass `suggested_contact_ids` to the template context.

- [ ] **Step 2: Highlight suggested contacts in pickers**

In `policies/edit.html`, where the contact autocomplete/datalist is rendered, mark suggested contacts with a `⭐` prefix or sort them to the top.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/templates/policies/edit.html
git commit -m "feat: suggest contacts by expertise during policy assignment"
```

---

### Task 6: Settings Integration

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Pass expertise config lists to settings**

Add to the settings GET handler:
```python
"expertise_lines": cfg.get("expertise_lines", []),
"expertise_industries": cfg.get("expertise_industries", []),
```

These are flat string lists — use existing `_list_card.html` pattern (same as `EDITABLE_LISTS` dict or explicit includes).

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat: expertise lines and industries in settings UI"
```

---

### Task 7: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual test**

1. **Settings:** Verify Expertise Lines and Expertise Industries cards appear. Add/remove items.
2. **Tag a contact:** Go to contacts list → click ⭐ on a contact → select "Casualty" line + "Sports & Entertainment" industry → tags save and display as pills.
3. **Filter contacts:** Click "Casualty" filter pill → list shows only tagged contacts. Add "Sports & Entertainment" → AND filter narrows further.
4. **Quick-tag from policy:** Open a policy edit page → next to placement colleague name, click ⭐ → tag inline.
5. **Suggestions:** On a General Liability policy, contacts tagged "General Liability" show ⭐ Suggested in the picker.
6. **Expertise notes:** Add notes to a contact → verify they display.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for contact expertise"
```
