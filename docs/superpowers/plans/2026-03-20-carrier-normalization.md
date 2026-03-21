# Carrier Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize carrier names on save using config-managed aliases, add Settings UI for alias management, run hygiene migration on existing data.

**Architecture:** `_CARRIER_ALIASES` dict built from config `carrier_aliases` key. `normalize_carrier()` function in utils.py. Applied on all carrier save paths. Settings UI with dedicated endpoints for alias CRUD.

**Tech Stack:** SQLite, FastAPI, Jinja2

**Spec:** `docs/superpowers/specs/2026-03-20-carrier-normalization-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/web/templates/settings/_carrier_aliases.html` | Settings UI for alias management |
| Create | `tests/test_carrier_normalization.py` | Tests |
| Modify | `src/policydb/utils.py` | `normalize_carrier()`, `_CARRIER_ALIASES`, `rebuild_carrier_aliases()` |
| Modify | `src/policydb/config.py` | `carrier_aliases` defaults |
| Modify | `src/policydb/web/routes/policies.py` | Apply on save |
| Modify | `src/policydb/web/routes/reconcile.py` | Apply on save |
| Modify | `src/policydb/importer.py` | Apply on import |
| Modify | `src/policydb/web/routes/settings.py` | Alias CRUD endpoints |
| Modify | `src/policydb/web/templates/settings.html` | Include alias card |
| Modify | `src/policydb/db.py` | Hygiene carrier normalization |

---

### Task 1: Normalize Function + Config + Tests

**Files:**
- Modify: `src/policydb/utils.py`
- Modify: `src/policydb/config.py`
- Create: `tests/test_carrier_normalization.py`

- [ ] **Step 1: Add carrier_aliases to config defaults**

In `config.py` `_DEFAULTS`, add the full `carrier_aliases` dict from the spec (30+ carrier groups with variations).

- [ ] **Step 2: Add normalize_carrier and alias builder to utils.py**

```python
_CARRIER_ALIASES: dict[str, str] = {}

def rebuild_carrier_aliases() -> None:
    """Rebuild _CARRIER_ALIASES from config. Call after config changes."""
    global _CARRIER_ALIASES
    from policydb import config as cfg
    aliases = cfg.get("carrier_aliases", {})
    result = {}
    for canonical, variations in aliases.items():
        result[canonical.lower()] = canonical
        for v in variations:
            result[v.strip().lower()] = canonical
    _CARRIER_ALIASES = result

def normalize_carrier(raw: str) -> str:
    """Normalize a carrier name to its canonical parent company name."""
    if not raw or not raw.strip():
        return ""
    cleaned = raw.strip()
    key = cleaned.lower()
    if key in _CARRIER_ALIASES:
        return _CARRIER_ALIASES[key]
    return cleaned

# Build on module load
try:
    rebuild_carrier_aliases()
except Exception:
    pass  # Config may not be available during testing
```

- [ ] **Step 3: Write tests**

```python
# tests/test_carrier_normalization.py
"""Tests for carrier normalization."""

def test_normalize_carrier_known():
    from policydb.utils import normalize_carrier, rebuild_carrier_aliases
    rebuild_carrier_aliases()
    assert normalize_carrier("Travelers Insurance") == "Travelers"
    assert normalize_carrier("the travelers companies") == "Travelers"
    assert normalize_carrier("ACE American") == "Chubb"
    assert normalize_carrier("National Union Fire") == "AIG"
    assert normalize_carrier("The Hartford") == "Hartford"

def test_normalize_carrier_canonical():
    from policydb.utils import normalize_carrier, rebuild_carrier_aliases
    rebuild_carrier_aliases()
    assert normalize_carrier("Travelers") == "Travelers"
    assert normalize_carrier("AIG") == "AIG"

def test_normalize_carrier_unknown():
    from policydb.utils import normalize_carrier
    assert normalize_carrier("Some Obscure Carrier") == "Some Obscure Carrier"
    assert normalize_carrier("") == ""
    assert normalize_carrier(None) == ""

def test_normalize_carrier_case_insensitive():
    from policydb.utils import normalize_carrier, rebuild_carrier_aliases
    rebuild_carrier_aliases()
    assert normalize_carrier("travelers insurance") == "Travelers"
    assert normalize_carrier("TRAVELERS INSURANCE") == "Travelers"
    assert normalize_carrier("aig") == "AIG"
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/test_carrier_normalization.py -v
git add src/policydb/utils.py src/policydb/config.py tests/test_carrier_normalization.py
git commit -m "feat: add normalize_carrier() with config-managed aliases"
```

---

### Task 2: Apply on All Save Paths

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/routes/reconcile.py`
- Modify: `src/policydb/importer.py`

- [ ] **Step 1: Add to policies.py**

Import `normalize_carrier` from utils. Apply in:
- `policy_edit_post` — `carrier = normalize_carrier(carrier) if carrier else ""`
- `policy_new_post` — same
- `policy_cell_save` (PATCH) — add `carrier` to allowed fields, apply normalization

- [ ] **Step 2: Add to reconcile.py**

Apply in `reconcile_create`, `batch_create`, `batch_create_program`:
```python
carrier = normalize_carrier(carrier) if carrier else ""
```

- [ ] **Step 3: Add to importer.py**

In `PolicyImporter`, after column mapping:
```python
from policydb.utils import normalize_carrier
row["carrier"] = normalize_carrier(row.get("carrier", ""))
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/ -v
git add src/policydb/web/routes/policies.py src/policydb/web/routes/reconcile.py src/policydb/importer.py
git commit -m "feat: normalize carrier on all save paths"
```

---

### Task 3: Settings UI — Carrier Aliases

**Files:**
- Create: `src/policydb/web/templates/settings/_carrier_aliases.html`
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Add alias CRUD endpoints**

In `settings.py`:

```python
@router.post("/carrier-aliases/add-group")
def carrier_alias_add_group(request: Request, canonical: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical not in aliases:
        aliases[canonical] = []
        # Save config
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings", status_code=303)

@router.post("/carrier-aliases/add-alias")
def carrier_alias_add(request: Request, canonical: str = Form(...), alias: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical in aliases and alias not in aliases[canonical]:
        aliases[canonical].append(alias)
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings", status_code=303)

@router.post("/carrier-aliases/remove-alias")
def carrier_alias_remove(request: Request, canonical: str = Form(...), alias: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical in aliases and alias in aliases[canonical]:
        aliases[canonical].remove(alias)
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings", status_code=303)

@router.post("/carrier-aliases/remove-group")
def carrier_alias_remove_group(request: Request, canonical: str = Form(...)):
    aliases = cfg.get("carrier_aliases", {})
    if canonical in aliases:
        del aliases[canonical]
        full = dict(cfg.load_config())
        full["carrier_aliases"] = aliases
        cfg.save_config(full)
        cfg.reload_config()
        from policydb.utils import rebuild_carrier_aliases
        rebuild_carrier_aliases()
    return RedirectResponse("/settings", status_code=303)
```

- [ ] **Step 2: Create _carrier_aliases.html**

Collapsible card showing each carrier group with its aliases. Add alias input per group. Add group button. Remove buttons.

- [ ] **Step 3: Pass to template context and include**

Add `carrier_aliases` to settings GET context. Include the partial in settings.html.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: carrier aliases management in Settings UI"
```

---

### Task 4: Hygiene Migration + Program Carriers

**Files:**
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Add carrier normalization to startup hygiene**

In `init_db()`, in the startup hygiene section (after the address backfill), add:

```python
# Normalize carrier names (idempotent)
try:
    from policydb.utils import normalize_carrier, rebuild_carrier_aliases
    rebuild_carrier_aliases()
    _carrier_changed = 0
    for r in conn.execute("SELECT id, carrier FROM policies WHERE carrier IS NOT NULL AND carrier != ''").fetchall():
        n = normalize_carrier(r["carrier"])
        if n != r["carrier"]:
            conn.execute("UPDATE policies SET carrier = ? WHERE id = ?", (n, r["id"]))
            _carrier_changed += 1
    # Also normalize program_carriers table
    for r in conn.execute("SELECT id, carrier FROM program_carriers WHERE carrier IS NOT NULL AND carrier != ''").fetchall():
        n = normalize_carrier(r["carrier"])
        if n != r["carrier"]:
            conn.execute("UPDATE program_carriers SET carrier = ? WHERE id = ?", (n, r["id"]))
            _carrier_changed += 1
    if _carrier_changed:
        conn.commit()
        print(f"[hygiene] Normalized {_carrier_changed} carrier names")
except Exception as e:
    print(f"[WARNING] Carrier normalization failed: {e}")
```

- [ ] **Step 2: Run tests, commit**

```bash
pytest tests/ -v
git add src/policydb/db.py
git commit -m "feat: carrier normalization in startup hygiene"
```

---

### Task 5: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

- [ ] **Step 2: Manual test**

1. **Normalization:** Create a policy with carrier "Travelers Insurance" → saves as "Travelers" with flash
2. **Unknown carrier:** Enter "Some New Carrier" → stored as-is, no flash
3. **Settings:** Verify Carrier Aliases card shows all groups. Add a new alias. Remove one. Add a new group.
4. **Import:** Import CSV with "ACE American" carrier → normalized to "Chubb"
5. **Hygiene:** Restart server → check any existing dirty carriers get normalized

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for carrier normalization"
```
