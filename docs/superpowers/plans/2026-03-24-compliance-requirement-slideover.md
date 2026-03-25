# Compliance Requirement Slideover + Auto-Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cramped inline requirement edit form with a right slideover panel featuring side-by-side policy comparison and auto-computed compliance status.

**Architecture:** New slideover template renders via a dedicated detail endpoint. Auto-status logic lives in `compliance.py` and is triggered by link changes and field edits. A `status_manual_override` column preserves human decisions (Waived/N/A/confirmed Compliant) from being overwritten by automation.

**Tech Stack:** FastAPI, Jinja2, HTMX, Tailwind CSS, SQLite, RapidFuzz

**Spec:** `docs/superpowers/specs/2026-03-24-compliance-requirement-slideover-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/policydb/migrations/076_status_manual_override.sql` | Create | Schema migration — adds override flag column |
| `src/policydb/db.py` | Modify | Wire migration 076 |
| `src/policydb/compliance.py` | Modify | `compute_auto_status()` + auto-compute in `get_client_compliance_data()` |
| `src/policydb/web/routes/compliance.py` | Modify | New detail endpoint + update link/cell endpoints for auto-status |
| `src/policydb/web/templates/compliance/_requirement_slideover.html` | Create | Full slideover panel template |
| `src/policydb/web/templates/compliance/_requirement_row.html` | Modify | Retarget Edit button to slideover |
| `src/policydb/web/templates/compliance/_requirement_row_edit.html` | Modify | Add deprecation comment |
| `src/policydb/web/templates/compliance/index.html` | Modify | Add `#requirement-slideover-container` div |

---

### Task 1: Migration — Add `status_manual_override` Column

**Files:**
- Create: `src/policydb/migrations/076_status_manual_override.sql`
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Create migration SQL file**

```sql
-- 076_status_manual_override.sql
-- Tracks whether compliance_status was manually set (Confirm Compliant, Waived, N/A).
-- Auto-compute skips rows with this flag set. Cleared on policy link changes.
ALTER TABLE coverage_requirements ADD COLUMN status_manual_override INTEGER DEFAULT 0;
```

- [ ] **Step 2: Wire migration in `db.py`**

Find the migration block (search for `075` in `db.py`). Add after the 075 block following the same pattern:

```python
if 76 not in applied:
    conn.executescript((_MIGRATIONS_DIR / "076_status_manual_override.sql").read_text())
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (76, "Add status_manual_override to coverage_requirements"),
    )
    conn.commit()
```

- [ ] **Step 3: Test migration runs**

Run: `policydb serve` (starts server, runs migrations on startup), then verify:
```bash
sqlite3 ~/.policydb/policydb.sqlite ".schema coverage_requirements" | grep status_manual_override
```
Expected: `status_manual_override INTEGER DEFAULT 0`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/076_status_manual_override.sql src/policydb/db.py
git commit -m "feat: add status_manual_override column to coverage_requirements (migration 076)"
```

---

### Task 2: Auto-Compliance Engine

**Files:**
- Modify: `src/policydb/compliance.py`
- Test: `tests/test_compliance.py`

- [ ] **Step 1: Write tests for `compute_auto_status()`**

Add to `tests/test_compliance.py`:

```python
from policydb.compliance import compute_auto_status


def test_auto_status_no_policy():
    req = {"required_limit": 1000000, "max_deductible": 25000, "required_endorsements": "[]"}
    assert compute_auto_status(req, None) == "Gap"


def test_auto_status_limit_insufficient():
    req = {"required_limit": 2000000, "max_deductible": None, "required_endorsements": "[]"}
    policy = {"limit_amount": 1000000, "deductible": 0}
    assert compute_auto_status(req, policy) == "Gap"


def test_auto_status_deductible_exceeds():
    req = {"required_limit": 1000000, "max_deductible": 10000, "required_endorsements": "[]"}
    policy = {"limit_amount": 2000000, "deductible": 50000}
    assert compute_auto_status(req, policy) == "Gap"


def test_auto_status_compliant():
    req = {"required_limit": 1000000, "max_deductible": 25000, "required_endorsements": "[]"}
    policy = {"limit_amount": 2000000, "deductible": 10000}
    assert compute_auto_status(req, policy) == "Compliant"


def test_auto_status_compliant_no_deductible_requirement():
    req = {"required_limit": 1000000, "max_deductible": None, "required_endorsements": "[]"}
    policy = {"limit_amount": 1000000, "deductible": 50000}
    assert compute_auto_status(req, policy) == "Compliant"


def test_auto_status_partial_endorsements():
    req = {"required_limit": 1000000, "max_deductible": None,
           "required_endorsements": '["Additional Insured", "Waiver of Subrogation"]'}
    policy = {"limit_amount": 2000000, "deductible": 0}
    assert compute_auto_status(req, policy) == "Partial"


def test_auto_status_compliant_empty_endorsements():
    req = {"required_limit": 1000000, "max_deductible": None, "required_endorsements": "[]"}
    policy = {"limit_amount": 1000000, "deductible": 0}
    assert compute_auto_status(req, policy) == "Compliant"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compliance.py -k "test_auto_status" -v`
Expected: All 7 tests FAIL with `ImportError: cannot import name 'compute_auto_status'`

- [ ] **Step 3: Implement `compute_auto_status()`**

Add to `src/policydb/compliance.py` after `compute_compliance_summary()` (after line ~206):

```python
def compute_auto_status(requirement: dict, policy: dict | None) -> str:
    """Auto-compute compliance status from requirement vs. linked policy.

    Returns "Compliant", "Partial", or "Gap".
    - No policy → Gap
    - Policy limit < required limit → Gap
    - Policy deductible > max deductible → Gap
    - Limits pass but endorsements required → Partial
    - Limits pass and no endorsements → Compliant
    """
    if policy is None:
        return "Gap"

    req_limit = float(requirement.get("required_limit") or 0)
    pol_limit = float(policy.get("limit_amount") or 0)
    if req_limit > 0 and pol_limit < req_limit:
        return "Gap"

    max_ded = requirement.get("max_deductible")
    if max_ded is not None:
        pol_ded = float(policy.get("deductible") or 0)
        if pol_ded > float(max_ded):
            return "Gap"

    # Check endorsements
    endorsements_raw = requirement.get("required_endorsements") or "[]"
    try:
        endorsements = json.loads(endorsements_raw) if isinstance(endorsements_raw, str) else endorsements_raw
    except (ValueError, TypeError):
        endorsements = []
    if endorsements:
        return "Partial"

    return "Compliant"
```

Ensure `import json` is at the top of the file (it already is).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compliance.py -k "test_auto_status" -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/compliance.py tests/test_compliance.py
git commit -m "feat: add compute_auto_status() for auto-compliance detection"
```

---

### Task 3: Auto-Status on Page Load for "Needs Review" Rows

**Files:**
- Modify: `src/policydb/compliance.py` — inside `get_client_compliance_data()`

- [ ] **Step 1: Add auto-compute call in `get_client_compliance_data()`**

In `get_client_compliance_data()`, find the loop where governing requirements are built (after `resolve_governing_requirements()` is called, around line ~460). After the governing dict is built for each location, add auto-compute for "Needs Review" rows:

```python
        # Auto-compute status for "Needs Review" governing requirements
        for line, gov_req in gov.items():
            status = (gov_req.get("compliance_status") or "Needs Review")
            override = gov_req.get("status_manual_override", 0)
            if status == "Needs Review" and not override and gov_req.get("linked_policy_uid"):
                # Fetch primary linked policy data
                pol = conn.execute(
                    "SELECT limit_amount, deductible FROM policies WHERE policy_uid = ? AND archived = 0",
                    (gov_req["linked_policy_uid"],),
                ).fetchone()
                if pol:
                    new_status = compute_auto_status(gov_req, dict(pol))
                    if new_status != status:
                        conn.execute(
                            "UPDATE coverage_requirements SET compliance_status = ? WHERE id = ?",
                            (new_status, gov_req["id"]),
                        )
                        gov_req["compliance_status"] = new_status
        conn.commit()
```

Insert this block right before the `summary = compute_compliance_summary(gov)` call.

- [ ] **Step 2: Test manually**

Start server, navigate to a compliance page with "Needs Review" requirements that have linked policies. Verify the status auto-updates on page load.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/compliance.py
git commit -m "feat: auto-compute compliance status on page load for Needs Review rows"
```

---

### Task 4: Slideover Container + Index Page Update

**Files:**
- Modify: `src/policydb/web/templates/compliance/index.html`
- Modify: `src/policydb/web/templates/compliance/_requirement_row_edit.html`

- [ ] **Step 1: Add slideover container to `index.html`**

Add the slideover container near the bottom of `index.html`, next to `#ai-import-container`, before `{% endblock %}`:

```html
  {# ── Requirement Slideover Container ───────────────────────────────── #}
  <div id="requirement-slideover-container"></div>
```

Add this near the bottom of the page content, before `{% endblock %}`.

- [ ] **Step 2: Add deprecation comment to `_requirement_row_edit.html`**

Add at the very top of the file (before line 1):

```html
{# DEPRECATED: This inline edit form is replaced by _requirement_slideover.html.
   The Edit button in _requirement_row.html now opens the slideover panel instead.
   Kept for backward compatibility with any direct endpoint references. #}
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/compliance/index.html src/policydb/web/templates/compliance/_requirement_row_edit.html
git commit -m "feat: add slideover container to compliance index + deprecate inline edit"
```

---

### Task 5: Detail Endpoint

**Files:**
- Modify: `src/policydb/web/routes/compliance.py`

- [ ] **Step 1: Add the detail endpoint**

Add after the existing `requirements_row_edit` endpoint. The endpoint loads the requirement, its links, the primary linked policy (for comparison), source/location data, and config lists:

```python
@router.get("/client/{client_id}/requirements/{req_id}/detail", response_class=HTMLResponse)
def requirement_detail(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return the slideover detail panel for a requirement."""
    req = conn.execute(
        "SELECT * FROM coverage_requirements WHERE id = ? AND client_id = ?",
        (req_id, client_id),
    ).fetchone()
    if not req:
        return HTMLResponse("Not found", status_code=404)

    req_dict = dict(req)
    try:
        req_dict["_endorsements_list"] = json.loads(req_dict.get("required_endorsements") or "[]")
    except (ValueError, TypeError):
        req_dict["_endorsements_list"] = []

    # Sources and locations for dropdowns
    sources = [dict(r) for r in conn.execute(
        "SELECT id, name, counterparty FROM requirement_sources WHERE client_id = ? ORDER BY name",
        (client_id,),
    ).fetchall()]
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id = ? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Policy links
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)

    # Primary linked policy for comparison
    primary_policy = None
    for link in links:
        if link.get("is_primary"):
            primary_policy = conn.execute(
                "SELECT policy_uid, policy_type, carrier, limit_amount, deductible, "
                "expiration_date FROM policies WHERE policy_uid = ? AND archived = 0",
                (link["policy_uid"],),
            ).fetchone()
            if primary_policy:
                primary_policy = dict(primary_policy)
            break

    # Compute auto-status for display
    auto_status = compute_auto_status(req_dict, primary_policy) if primary_policy else "Gap"

    return templates.TemplateResponse("compliance/_requirement_slideover.html", {
        "request": request,
        "req": req_dict,
        "client_id": client_id,
        "sources": sources,
        "projects": projects,
        "links": links,
        "linkable_policies": linkable,
        "primary_policy": primary_policy,
        "auto_status": auto_status,
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "deductible_types": cfg.get("deductible_types", []),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
    })
```

Add `from policydb.compliance import compute_auto_status` to the imports at the top of the file (alongside existing compliance imports).

Also add `"source_id"` and `"project_id"` to the `_CELL_ALLOWED_FIELDS` set (around line 35-44) so the slideover can edit source and location via the review-mode cell-patch endpoint:

```python
_CELL_ALLOWED_FIELDS = {
    "coverage_line",
    "required_limit",
    "max_deductible",
    "deductible_type",
    "required_endorsements",
    "compliance_status",
    "linked_policy_uid",
    "notes",
    "source_id",      # ← add
    "project_id",     # ← add
}
```

- [ ] **Step 2: Test the endpoint returns 200**

```bash
# Start server, then:
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000/compliance/client/1/requirements/17/detail"
```
Expected: `200`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/compliance.py
git commit -m "feat: add requirement detail endpoint for slideover panel"
```

---

### Task 6: Slideover Template

**Files:**
- Create: `src/policydb/web/templates/compliance/_requirement_slideover.html`

- [ ] **Step 1: Create the slideover template**

This is the largest single file. It has 5 sections (A–E per the spec) plus JS helpers. Template context variables: `req`, `client_id`, `sources`, `projects`, `links`, `linkable_policies`, `primary_policy`, `auto_status`, plus config lists.

Key patterns to follow:
- Backdrop: `<div class="fixed inset-0 bg-black/30 z-40" onclick="closeRequirementDetail()"></div>` (same as `_ai_import_panel.html` line 12–14)
- Panel: `<div class="fixed top-0 right-0 bottom-0 w-[520px] max-sm:w-full bg-white shadow-xl z-50 overflow-y-auto">` (same as `_ai_import_panel.html` line 17–18)
- Escape handler: `document.addEventListener('keydown', ...)` (same as `_ai_import_panel.html` line 307–311)
- Cell saves use `fetch()` to `PATCH /compliance/client/{client_id}/review-mode/{req_id}/cell` returning JSON
- Endorsement toggle updates hidden JSON input and immediately PATCHes
- Status banner color: green (`bg-green-50`) for Compliant, amber (`bg-amber-50`) for Partial, red (`bg-red-50`) for Gap, gray (`bg-gray-50`) for others
- Policy comparison cards: green border/bg for pass, red for fail

**Critical: `closeRequirementDetail()` must refresh the parent page on close.** The function does two things:
1. Clear `#requirement-slideover-container` innerHTML (removes panel + backdrop)
2. Refresh the location detail tab so requirement rows and summary stats reflect any edits:
```javascript
function closeRequirementDetail() {
    var container = document.getElementById('requirement-slideover-container');
    if (container) container.innerHTML = '';
    // Refresh the active location tab to update rows + summary
    var activeTab = document.querySelector('#location-tab-bar button.border-marsh');
    if (activeTab) htmx.ajax('GET', activeTab.getAttribute('hx-get'), {target: '#location-tab-content', swap: 'innerHTML'});
}
```

**Link actions in the slideover:** When adding/removing/changing primary policy links, the slideover uses `fetch()` to POST to the existing link endpoint, discards the HTML partial response, then re-fetches the entire slideover to get updated auto-status:
```javascript
fetch('/compliance/client/' + clientId + '/requirements/' + reqId + '/links/add', {
    method: 'POST', ...
}).then(function() {
    // Discard link endpoint's HTML response; re-fetch whole slideover for auto-status
    htmx.ajax('GET', '/compliance/client/' + clientId + '/requirements/' + reqId + '/detail',
              {target: '#requirement-slideover-container', swap: 'innerHTML'});
});
```

Sections in order:
1. **Status Banner** — auto_status display + Confirm Compliant / Override buttons
2. **Requirement Fields** — click-to-edit: coverage_line, required_limit, max_deductible, deductible_type, source, location
3. **Endorsements** — toggleable pills
4. **Policy Comparison** — primary_policy limit/deductible comparison cards + linked policies list + add link combobox
5. **Notes** — contenteditable div
6. **Footer** — timestamps + delete

- [ ] **Step 2: Test the slideover renders**

Start server, navigate to compliance page, run in browser console:
```javascript
fetch('/compliance/client/1/requirements/17/detail')
  .then(r => r.text())
  .then(html => {
    document.getElementById('requirement-slideover-container').innerHTML = html;
  });
```
Verify the panel appears on the right side with all sections.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/compliance/_requirement_slideover.html
git commit -m "feat: create requirement slideover template with status banner + policy comparison"
```

---

### Task 7: Retarget Edit Button to Slideover

**Files:**
- Modify: `src/policydb/web/templates/compliance/_requirement_row.html`

- [ ] **Step 1: Update the Edit button**

In `_requirement_row.html`, find the Edit button (line 67–70). Change from:

```html
<button class="text-marsh hover:text-green-800 text-xs mr-1"
        hx-get="/compliance/client/{{ client_id }}/requirements/{{ req.id }}/row/edit"
        hx-target="#req-row-{{ req.id }}"
        hx-swap="outerHTML">Edit</button>
```

To:

```html
<button class="text-marsh hover:text-green-800 text-xs mr-1"
        hx-get="/compliance/client/{{ client_id }}/requirements/{{ req.id }}/detail"
        hx-target="#requirement-slideover-container"
        hx-swap="innerHTML">Edit</button>
```

- [ ] **Step 2: Test the Edit button opens the slideover**

Navigate to a compliance page, click Edit on a requirement row. Verify the slideover opens instead of the inline edit form.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/compliance/_requirement_row.html
git commit -m "feat: retarget Edit button to open slideover instead of inline form"
```

---

### Task 8: Auto-Status Triggers in Link & Cell Endpoints

**Files:**
- Modify: `src/policydb/web/routes/compliance.py`

- [ ] **Step 1: Update link endpoints to clear override and trigger auto-status**

In the `links/add` endpoint (search for `ai_import_apply_source` or `links/add` in compliance.py), after the link is inserted, add:

```python
# Clear manual override and recompute auto-status
conn.execute(
    "UPDATE coverage_requirements SET status_manual_override = 0 WHERE id = ?",
    (req_id,),
)
_recompute_auto_status(conn, req_id)
```

Do the same in `links/{id}/remove` and `links/{id}/set-primary` endpoints.

Create the helper function:

```python
def _recompute_auto_status(conn, req_id: int):
    """Recompute auto-status for a requirement based on its primary linked policy."""
    req = conn.execute(
        "SELECT * FROM coverage_requirements WHERE id = ?", (req_id,)
    ).fetchone()
    if not req:
        return
    req_dict = dict(req)
    status = req_dict.get("compliance_status") or "Needs Review"
    override = req_dict.get("status_manual_override", 0)
    if status in ("Waived", "N/A") and override:
        return  # Preserve manual Waived/N/A

    # Find primary linked policy
    primary = conn.execute(
        """SELECT p.limit_amount, p.deductible
           FROM requirement_policy_links rpl
           JOIN policies p ON p.policy_uid = rpl.policy_uid AND p.archived = 0
           WHERE rpl.requirement_id = ? AND rpl.is_primary = 1""",
        (req_id,),
    ).fetchone()

    new_status = compute_auto_status(req_dict, dict(primary) if primary else None)
    if new_status != status:
        conn.execute(
            "UPDATE coverage_requirements SET compliance_status = ? WHERE id = ?",
            (new_status, req_id),
        )
    conn.commit()
```

- [ ] **Step 2: Update review-mode cell-patch to trigger auto-status on limit/deductible changes**

In the `review_mode_cell` endpoint (search for `review-mode/{req_id}/cell`), after the field update is saved, add a check:

```python
# Trigger auto-status recompute on limit/deductible field changes
if field in ("required_limit", "max_deductible"):
    _recompute_auto_status(conn, req_id)
```

- [ ] **Step 3: Handle "Confirm Compliant" and override in cell-patch**

In the same `review_mode_cell` endpoint, add special handling for `compliance_status` saves:

```python
if field == "compliance_status" and value in ("Compliant", "Waived", "N/A"):
    conn.execute(
        "UPDATE coverage_requirements SET status_manual_override = 1 WHERE id = ?",
        (req_id,),
    )
    conn.commit()
```

- [ ] **Step 4: Test auto-status triggers**

1. Link a policy that meets a requirement's limits → verify status changes from "Needs Review" to "Compliant"
2. Link a policy with endorsements required → verify "Partial"
3. Remove all links → verify "Gap"
4. Set "Waived" via override → add a link → verify status recomputes (override cleared)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/compliance.py
git commit -m "feat: trigger auto-status recompute on link changes and field edits"
```

---

### Task 9: End-to-End QA

- [ ] **Step 1: Start server and run full verification checklist**

Per the spec's verification section:

1. Navigate to compliance page → click Edit on requirement → slideover opens
2. Click-to-edit: change limit → green flash on save
3. Toggle endorsement pills → verify they save
4. Link a policy → auto-status banner updates
5. Link a policy with endorsements required → shows "Partial"
6. Remove all links → shows "Gap"
7. "Confirm Compliant" on Partial → status sticks
8. Set "Waived" via Override → auto-compute skips it
9. Close panel (✕, Escape, backdrop) → parent page refreshes
10. Check compliance percentage → Waived/N/A excluded from denominator
11. Narrow viewport → panel is full-width on mobile

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Final commit with any fixes**

```bash
git add -A
git commit -m "fix: QA fixes for compliance requirement slideover"
```
