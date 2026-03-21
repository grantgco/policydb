# Unified Compliance Review System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the existing client-level risk profile into a unified system where identified risks spawn contractual coverage requirements, checked against actual policies for compliance across locations/projects — with COPE data capture, requirement templates, and matrix + executive summary reporting.

**Architecture:** The system adds 5 new tables (requirement_sources, coverage_requirements, requirement_templates, requirement_template_items, cope_data) to the existing risk infrastructure. A new `compliance.py` engine handles inheritance resolution (client-level requirements cascade to locations), multi-contract conflict resolution (auto-resolve to most stringent per coverage line), and policy auto-suggest matching. A dedicated review page at `/clients/{id}/compliance` provides an interactive matrix view (locations × coverage lines) with drill-down, COPE capture, and XLSX/PDF export.

**Tech Stack:** FastAPI, Jinja2/HTMX, SQLite, openpyxl (XLSX), fpdf2 (PDF)

**Phases:**
- **Phase 1 (Tasks 1–9):** Data model, compliance engine, dedicated review page with matrix — delivers core workflow for contract review projects
- **Phase 2 (Tasks 10–12):** Requirement templates, COPE data capture
- **Phase 3 (Tasks 13–16):** Risk→Requirement spawning, auto-suggest policy matching, inheritance visualization, guided risk prompts
- **Phase 4 (Tasks 17–18):** XLSX and PDF export with executive summary

**Critical implementation notes (from code review):**
- Config API: Use `from policydb import config as cfg` then `cfg.get("key")` — there is NO `Config()` class
- DB dependency: Use `from policydb.web.app import get_db, templates` then `conn=Depends(get_db)` — do NOT wrap in a helper
- Jinja2 currency filter: `{{ value | currency }}` (NOT `fmt_currency`)
- Async body reading: Use `async def` + `await request.json()` for JSON body endpoints
- Route ordering: Define literal routes (e.g., `/templates`) BEFORE parameterized routes (`/client/{client_id}`) per project convention

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/policydb/migrations/066_compliance_requirements.sql` | All 5 new tables + triggers |
| `src/policydb/compliance.py` | Compliance engine: inheritance resolution, conflict resolution, policy matching |
| `src/policydb/web/routes/compliance.py` | Route handlers for compliance review page |
| `src/policydb/web/templates/compliance/index.html` | Main compliance review page |
| `src/policydb/web/templates/compliance/_matrix.html` | Coverage matrix partial (locations × coverage lines) |
| `src/policydb/web/templates/compliance/_location_detail.html` | Location drill-down with requirements by source |
| `src/policydb/web/templates/compliance/_requirement_row.html` | Single requirement display/edit row |
| `src/policydb/web/templates/compliance/_source_form.html` | Add/edit requirement source form |
| `src/policydb/web/templates/compliance/_summary_banner.html` | Executive summary stats banner |
| `src/policydb/web/templates/compliance/_cope_section.html` | COPE data display/edit (Phase 2) |
| `src/policydb/web/templates/compliance/_template_picker.html` | Template selection/apply (Phase 2) |
| `src/policydb/web/templates/compliance/print.html` | Print-friendly compliance report |
| `tests/test_compliance.py` | Tests for compliance engine |

### Modified Files

| File | Change |
|------|--------|
| `src/policydb/db.py` | Add 66 to `_KNOWN_MIGRATIONS` set |
| `src/policydb/config.py` | Add config defaults for compliance fields |
| `src/policydb/web/app.py` | Import + register compliance router |
| `src/policydb/web/templates/base.html` | Add "Compliance" to nav |
| `src/policydb/web/templates/clients/_risk_detail.html` | Add "Create Requirement" button (Phase 3) |
| `src/policydb/exporter.py` | Add compliance XLSX/PDF export functions (Phase 4) |

---

## Phase 1: Core System

### Task 1: Database Migration

**Files:**
- Create: `src/policydb/migrations/066_compliance_requirements.sql`
- Modify: `src/policydb/db.py:298` (add 66 to `_KNOWN_MIGRATIONS`)

- [ ] **Step 1: Write migration SQL**

Create `src/policydb/migrations/066_compliance_requirements.sql`:

```sql
-- Requirement sources: contracts, agreements, loan covenants
CREATE TABLE IF NOT EXISTS requirement_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    counterparty    TEXT,
    clause_ref      TEXT,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS requirement_sources_updated_at
    AFTER UPDATE ON requirement_sources
    FOR EACH ROW
    BEGIN UPDATE requirement_sources SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Coverage requirements: what coverage is needed
CREATE TABLE IF NOT EXISTS coverage_requirements (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id               INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id              INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    risk_id                 INTEGER REFERENCES client_risks(id) ON DELETE SET NULL,
    source_id               INTEGER REFERENCES requirement_sources(id) ON DELETE SET NULL,
    coverage_line           TEXT NOT NULL,
    required_limit          REAL,
    max_deductible          REAL,
    deductible_type         TEXT,
    ai_required             INTEGER DEFAULT 0,
    wos_required            INTEGER DEFAULT 0,
    primary_noncontrib      INTEGER DEFAULT 0,
    per_project_aggregate   INTEGER DEFAULT 0,
    noc_required            INTEGER DEFAULT 0,
    completed_ops_required  INTEGER DEFAULT 0,
    professional_liability_required INTEGER DEFAULT 0,
    pollution_required      INTEGER DEFAULT 0,
    cyber_required          INTEGER DEFAULT 0,
    builders_risk_required  INTEGER DEFAULT 0,
    compliance_status       TEXT DEFAULT 'Needs Review',
    linked_policy_uid       TEXT,
    notes                   TEXT,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS coverage_requirements_updated_at
    AFTER UPDATE ON coverage_requirements
    FOR EACH ROW
    BEGIN UPDATE coverage_requirements SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Reusable requirement templates (global, not per-client)
CREATE TABLE IF NOT EXISTS requirement_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Items within a requirement template
CREATE TABLE IF NOT EXISTS requirement_template_items (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id             INTEGER NOT NULL REFERENCES requirement_templates(id) ON DELETE CASCADE,
    coverage_line           TEXT NOT NULL,
    required_limit          REAL,
    max_deductible          REAL,
    deductible_type         TEXT,
    ai_required             INTEGER DEFAULT 0,
    wos_required            INTEGER DEFAULT 0,
    primary_noncontrib      INTEGER DEFAULT 0,
    per_project_aggregate   INTEGER DEFAULT 0,
    noc_required            INTEGER DEFAULT 0,
    completed_ops_required  INTEGER DEFAULT 0,
    professional_liability_required INTEGER DEFAULT 0,
    pollution_required      INTEGER DEFAULT 0,
    cyber_required          INTEGER DEFAULT 0,
    builders_risk_required  INTEGER DEFAULT 0,
    notes                   TEXT
);

-- COPE data per location (optional, not all locations need it)
CREATE TABLE IF NOT EXISTS cope_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    construction_type       TEXT,
    year_built              INTEGER,
    stories                 INTEGER,
    sq_footage              REAL,
    sprinklered             TEXT DEFAULT 'Unknown',
    roof_type               TEXT,
    occupancy_description   TEXT,
    protection_class        TEXT,
    total_insurable_value   REAL,
    notes                   TEXT,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS cope_data_updated_at
    AFTER UPDATE ON cope_data
    FOR EACH ROW
    BEGIN UPDATE cope_data SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_coverage_requirements_client_project
    ON coverage_requirements(client_id, project_id);
CREATE INDEX IF NOT EXISTS idx_coverage_requirements_source
    ON coverage_requirements(source_id);
CREATE INDEX IF NOT EXISTS idx_requirement_sources_client
    ON requirement_sources(client_id);
CREATE INDEX IF NOT EXISTS idx_cope_data_project
    ON cope_data(project_id);
```

- [ ] **Step 2: Add 66 to _KNOWN_MIGRATIONS in db.py**

In `src/policydb/db.py:298`, change:
```python
_KNOWN_MIGRATIONS = {1,2,...,65}
```
to:
```python
_KNOWN_MIGRATIONS = {1,2,...,65,66}
```

- [ ] **Step 3: Verify migration runs**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/hopeful-moore && python -c "from policydb.db import init_db; init_db()"`

Expected: No errors. Tables created.

- [ ] **Step 4: Verify tables exist**

Run: `python -c "import sqlite3; conn = sqlite3.connect('$HOME/.policydb/policydb.sqlite'); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('requirement_sources','coverage_requirements','requirement_templates','requirement_template_items','cope_data')\").fetchall()])"`

Expected: All 5 table names printed.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/migrations/066_compliance_requirements.sql src/policydb/db.py
git commit -m "feat: add compliance requirements schema (migration 066)

Tables: requirement_sources, coverage_requirements, requirement_templates,
requirement_template_items, cope_data"
```

---

### Task 2: Config Defaults

**Files:**
- Modify: `src/policydb/config.py` (add new config keys to `_DEFAULTS`)

- [ ] **Step 1: Add compliance config defaults**

In `src/policydb/config.py`, add these keys to the `_DEFAULTS` dict after the existing risk config (after line ~265):

```python
"compliance_statuses": [
    "Compliant", "Gap", "Partial", "Waived", "N/A", "Needs Review",
],
"deductible_types": [
    "Per Occurrence", "Per Claim", "Aggregate", "Named Storm %",
],
"construction_types": [
    "Frame (ISO 1)", "Joisted Masonry (ISO 2)", "Non-Combustible (ISO 3)",
    "Masonry Non-Combustible (ISO 4)", "Modified Fire Resistive (ISO 5)",
    "Fire Resistive (ISO 6)",
],
"sprinkler_options": [
    "Yes", "No", "Partial", "Unknown",
],
"endorsement_flags": [
    "ai_required", "wos_required", "primary_noncontrib",
    "per_project_aggregate", "noc_required", "completed_ops_required",
    "professional_liability_required", "pollution_required",
    "cyber_required", "builders_risk_required",
],
"endorsement_flag_labels": {
    "ai_required": "Additional Insured",
    "wos_required": "Waiver of Subrogation",
    "primary_noncontrib": "Primary & Non-Contributory",
    "per_project_aggregate": "Per-Project Aggregate",
    "noc_required": "Notice of Cancellation",
    "completed_ops_required": "Completed Operations",
    "professional_liability_required": "Professional Liability",
    "pollution_required": "Pollution",
    "cyber_required": "Cyber",
    "builders_risk_required": "Builders Risk",
},
```

- [ ] **Step 2: Verify config loads**

Run: `python -c "from policydb import config as cfg; print(cfg.get('compliance_statuses')); print(cfg.get('endorsement_flag_labels'))"`

Expected: Lists and dict printed correctly.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/config.py
git commit -m "feat: add compliance config defaults (statuses, deductible types, COPE, endorsement flags)"
```

---

### Task 3: Compliance Engine — Core Logic

**Files:**
- Create: `src/policydb/compliance.py`

This module contains pure functions (no route logic) for:
1. Resolving inherited + local requirements per location
2. Auto-resolving multi-source conflicts to most stringent
3. Suggesting policy matches for requirements

- [ ] **Step 1: Write the test file first**

Create `tests/test_compliance.py`:

```python
"""Tests for the compliance engine."""

from policydb.compliance import (
    resolve_governing_requirements,
    suggest_policy_for_requirement,
    compute_compliance_summary,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _req(coverage_line="General Liability", required_limit=1_000_000,
         max_deductible=None, deductible_type=None,
         ai_required=0, wos_required=0, primary_noncontrib=0,
         per_project_aggregate=0, noc_required=0, completed_ops_required=0,
         professional_liability_required=0, pollution_required=0,
         cyber_required=0, builders_risk_required=0,
         source_id=None, source_name="Contract A",
         project_id=None, client_id=1, id=1, risk_id=None,
         compliance_status="Needs Review", linked_policy_uid=None,
         notes=None):
    return {
        "id": id, "client_id": client_id, "project_id": project_id,
        "risk_id": risk_id, "source_id": source_id, "source_name": source_name,
        "coverage_line": coverage_line, "required_limit": required_limit,
        "max_deductible": max_deductible, "deductible_type": deductible_type,
        "ai_required": ai_required, "wos_required": wos_required,
        "primary_noncontrib": primary_noncontrib,
        "per_project_aggregate": per_project_aggregate,
        "noc_required": noc_required,
        "completed_ops_required": completed_ops_required,
        "professional_liability_required": professional_liability_required,
        "pollution_required": pollution_required,
        "cyber_required": cyber_required,
        "builders_risk_required": builders_risk_required,
        "compliance_status": compliance_status,
        "linked_policy_uid": linked_policy_uid, "notes": notes,
    }


def _policy(uid="POL-001", policy_type="General Liability", carrier="Hartford",
            limit_amount=1_000_000, deductible=5_000, project_id=None):
    return {
        "policy_uid": uid, "policy_type": policy_type, "carrier": carrier,
        "limit_amount": limit_amount, "deductible": deductible,
        "project_id": project_id,
    }


# ── Governing resolution ────────────────────────────────────────────────────

def test_single_source_becomes_governing():
    """One requirement for a coverage line = that requirement governs."""
    reqs = [_req(coverage_line="GL", required_limit=1_000_000)]
    gov = resolve_governing_requirements(reqs)
    assert len(gov) == 1
    assert gov["GL"]["required_limit"] == 1_000_000


def test_most_stringent_limit_wins():
    """When two sources require different limits, higher limit governs."""
    reqs = [
        _req(id=1, coverage_line="GL", required_limit=1_000_000,
             source_name="Contract A"),
        _req(id=2, coverage_line="GL", required_limit=2_000_000,
             source_name="Contract B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["GL"]["required_limit"] == 2_000_000
    assert gov["GL"]["governing_source"] == "Contract B"


def test_most_stringent_deductible_wins():
    """Lower max_deductible is more stringent."""
    reqs = [
        _req(id=1, coverage_line="Property", max_deductible=10_000,
             source_name="Lender A"),
        _req(id=2, coverage_line="Property", max_deductible=2_000,
             source_name="Lender B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["Property"]["max_deductible"] == 2_000
    assert gov["Property"]["governing_source"] == "Lender B"


def test_endorsement_flags_or_across_sources():
    """If ANY source requires an endorsement, it's required."""
    reqs = [
        _req(id=1, coverage_line="GL", ai_required=1, wos_required=0,
             source_name="A"),
        _req(id=2, coverage_line="GL", ai_required=0, wos_required=1,
             source_name="B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["GL"]["ai_required"] == 1
    assert gov["GL"]["wos_required"] == 1


def test_multiple_coverage_lines_resolved_independently():
    """Each coverage line resolves independently."""
    reqs = [
        _req(id=1, coverage_line="GL", required_limit=1_000_000),
        _req(id=2, coverage_line="Umbrella", required_limit=5_000_000),
    ]
    gov = resolve_governing_requirements(reqs)
    assert "GL" in gov
    assert "Umbrella" in gov
    assert gov["GL"]["required_limit"] == 1_000_000
    assert gov["Umbrella"]["required_limit"] == 5_000_000


def test_empty_requirements_returns_empty():
    gov = resolve_governing_requirements([])
    assert gov == {}


# ── Policy matching ─────────────────────────────────────────────────────────

def test_suggest_exact_coverage_match():
    """Policy with matching coverage type is suggested."""
    policies = [_policy(uid="POL-001", policy_type="General Liability")]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies)
    assert suggestion is not None
    assert suggestion["policy_uid"] == "POL-001"


def test_suggest_prefers_location_policy_over_corporate():
    """Location-specific policy preferred over corporate (no project_id)."""
    policies = [
        _policy(uid="CORP-GL", policy_type="General Liability", project_id=None),
        _policy(uid="LOC-GL", policy_type="General Liability", project_id=5),
    ]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000,
               "project_id": 5}
    suggestion = suggest_policy_for_requirement(gov_req, policies, location_project_id=5)
    assert suggestion["policy_uid"] == "LOC-GL"


def test_suggest_corporate_when_no_location_match():
    """Corporate policy (no project_id) covers any location."""
    policies = [
        _policy(uid="CORP-GL", policy_type="General Liability", project_id=None),
    ]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000,
               "project_id": 5}
    suggestion = suggest_policy_for_requirement(gov_req, policies, location_project_id=5)
    assert suggestion["policy_uid"] == "CORP-GL"


def test_suggest_no_match_returns_none():
    """No matching policy returns None."""
    policies = [_policy(uid="POL-WC", policy_type="Workers Compensation")]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies)
    assert suggestion is None


# ── Summary computation ─────────────────────────────────────────────────────

def test_compliance_summary():
    """Summary computes correct totals and percentages."""
    governing = {
        "GL": {"compliance_status": "Compliant", "coverage_line": "GL"},
        "Umbrella": {"compliance_status": "Compliant", "coverage_line": "Umbrella"},
        "Property": {"compliance_status": "Gap", "coverage_line": "Property"},
        "D&O": {"compliance_status": "Needs Review", "coverage_line": "D&O"},
    }
    summary = compute_compliance_summary(governing)
    assert summary["total"] == 4
    assert summary["compliant"] == 2
    assert summary["gap"] == 1
    assert summary["needs_review"] == 1
    assert summary["compliance_pct"] == 50  # 2/4 * 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/hopeful-moore && python -m pytest tests/test_compliance.py -v`

Expected: ImportError — `policydb.compliance` does not exist yet.

- [ ] **Step 3: Implement compliance engine**

Create `src/policydb/compliance.py`:

```python
"""Compliance engine: inheritance resolution, conflict resolution, policy matching."""

from __future__ import annotations

from policydb.utils import normalize_coverage_type

# Boolean endorsement flag columns
_ENDORSEMENT_FLAGS = [
    "ai_required", "wos_required", "primary_noncontrib",
    "per_project_aggregate", "noc_required", "completed_ops_required",
    "professional_liability_required", "pollution_required",
    "cyber_required", "builders_risk_required",
]


def resolve_governing_requirements(
    requirements: list[dict],
) -> dict[str, dict]:
    """Resolve a list of requirements to one governing requirement per coverage line.

    When multiple sources require the same coverage line:
    - Highest required_limit wins
    - Lowest max_deductible wins (more stringent)
    - Endorsement flags OR across all sources (if ANY requires it, it's required)
    - governing_source tracks which source drove the most stringent limit

    Args:
        requirements: List of requirement dicts (from coverage_requirements table
                      joined with requirement_sources for source_name)

    Returns:
        Dict keyed by coverage_line, each value is the governing requirement dict
        with an added 'governing_source' field and 'source_requirements' list.
    """
    if not requirements:
        return {}

    # Group by coverage_line
    by_line: dict[str, list[dict]] = {}
    for req in requirements:
        line = req["coverage_line"]
        by_line.setdefault(line, []).append(req)

    governing: dict[str, dict] = {}
    for line, reqs in by_line.items():
        if len(reqs) == 1:
            gov = dict(reqs[0])
            gov["governing_source"] = gov.get("source_name", "")
            gov["source_requirements"] = reqs
            governing[line] = gov
            continue

        # Resolve to most stringent
        gov = dict(reqs[0])
        gov_limit_source = gov.get("source_name", "")

        for req in reqs[1:]:
            # Higher limit is more stringent (client needs MORE coverage)
            req_limit = req.get("required_limit") or 0
            gov_limit = gov.get("required_limit") or 0
            if req_limit > gov_limit:
                gov["required_limit"] = req_limit
                gov_limit_source = req.get("source_name", "")

            # Lower max_deductible is more stringent
            req_ded = req.get("max_deductible")
            gov_ded = gov.get("max_deductible")
            if req_ded is not None:
                if gov_ded is None or req_ded < gov_ded:
                    gov["max_deductible"] = req_ded
                    gov["deductible_type"] = req.get("deductible_type")

            # OR across endorsement flags
            for flag in _ENDORSEMENT_FLAGS:
                if req.get(flag):
                    gov[flag] = 1

        gov["governing_source"] = gov_limit_source
        gov["source_requirements"] = reqs
        governing[line] = gov

    return governing


def get_location_requirements(
    conn,
    client_id: int,
    project_id: int | None,
) -> list[dict]:
    """Fetch all requirements that apply to a specific location.

    Includes:
    - Client-level requirements (project_id IS NULL) — inherited
    - Location-specific requirements (project_id = given project_id)

    Each row is joined with requirement_sources for source_name.
    """
    sql = """
        SELECT cr.*, rs.name AS source_name, rs.counterparty, rs.clause_ref
        FROM coverage_requirements cr
        LEFT JOIN requirement_sources rs ON cr.source_id = rs.id
        WHERE cr.client_id = ?
          AND (cr.project_id IS NULL OR cr.project_id = ?)
        ORDER BY cr.coverage_line, cr.source_id
    """
    rows = conn.execute(sql, (client_id, project_id)).fetchall()
    return [dict(r) for r in rows]


def suggest_policy_for_requirement(
    gov_req: dict,
    policies: list[dict],
    location_project_id: int | None = None,
) -> dict | None:
    """Suggest the best policy match for a governing requirement.

    Priority:
    1. Location-specific policy (project_id matches) with matching coverage type
    2. Corporate policy (project_id IS NULL) with matching coverage type
    3. None if no match

    Uses normalize_coverage_type for fuzzy matching.
    """
    target_line = gov_req.get("coverage_line", "")
    target_normalized = normalize_coverage_type(target_line)

    location_matches = []
    corporate_matches = []

    for pol in policies:
        pol_type = normalize_coverage_type(pol.get("policy_type", ""))
        if pol_type != target_normalized:
            continue

        if location_project_id and pol.get("project_id") == location_project_id:
            location_matches.append(pol)
        elif not pol.get("project_id"):
            corporate_matches.append(pol)

    # Prefer location-specific, then corporate
    if location_matches:
        return location_matches[0]
    if corporate_matches:
        return corporate_matches[0]
    return None


def compute_compliance_summary(governing: dict[str, dict]) -> dict:
    """Compute aggregate compliance stats from governing requirements.

    Returns dict with: total, compliant, gap, partial, waived, na,
    needs_review, compliance_pct.
    """
    total = len(governing)
    counts = {"compliant": 0, "gap": 0, "partial": 0, "waived": 0,
              "na": 0, "needs_review": 0}

    for gov in governing.values():
        status = (gov.get("compliance_status") or "Needs Review").lower().replace(" ", "_").replace("/", "")
        if status == "compliant":
            counts["compliant"] += 1
        elif status == "gap":
            counts["gap"] += 1
        elif status == "partial":
            counts["partial"] += 1
        elif status == "waived":
            counts["waived"] += 1
        elif status in ("na", "n/a", "n_a"):
            counts["na"] += 1
        else:
            counts["needs_review"] += 1

    pct = round(counts["compliant"] / total * 100) if total else 0
    return {"total": total, **counts, "compliance_pct": pct}


def get_client_compliance_data(conn, client_id: int) -> dict:
    """Build the full compliance dataset for a client.

    Returns:
        {
            "locations": [
                {
                    "project": {id, name, address, ...},
                    "requirements": [...],
                    "governing": {coverage_line: {...}, ...},
                    "summary": {total, compliant, gap, ...},
                    "policies": [...],
                },
                ...
            ],
            "client_requirements": [...],  # project_id IS NULL
            "sources": [...],
            "overall_summary": {total, compliant, gap, ...},
        }
    """
    # Get all locations for this client
    locations = [dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Get all policies for this client (non-archived)
    all_policies = [dict(r) for r in conn.execute(
        "SELECT policy_uid, policy_type, carrier, limit_amount, deductible, "
        "project_id, policy_number FROM policies "
        "WHERE client_id=? AND archived=0 ORDER BY policy_type",
        (client_id,),
    ).fetchall()]

    # Get all sources for this client
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Client-level requirements (no project_id)
    client_reqs = [dict(r) for r in conn.execute(
        """SELECT cr.*, rs.name AS source_name, rs.counterparty, rs.clause_ref
           FROM coverage_requirements cr
           LEFT JOIN requirement_sources rs ON cr.source_id = rs.id
           WHERE cr.client_id = ? AND cr.project_id IS NULL
           ORDER BY cr.coverage_line""",
        (client_id,),
    ).fetchall()]

    # Build per-location data
    location_data = []
    all_governing = {}

    for loc in locations:
        loc_reqs = get_location_requirements(conn, client_id, loc["id"])
        gov = resolve_governing_requirements(loc_reqs)

        # Auto-suggest policies for each governing requirement
        for line, gov_req in gov.items():
            if not gov_req.get("linked_policy_uid"):
                suggestion = suggest_policy_for_requirement(
                    gov_req, all_policies, location_project_id=loc["id"]
                )
                if suggestion:
                    gov_req["suggested_policy"] = suggestion

        summary = compute_compliance_summary(gov)

        location_data.append({
            "project": loc,
            "requirements": loc_reqs,
            "governing": gov,
            "summary": summary,
            "policies": [p for p in all_policies
                         if p.get("project_id") == loc["id"]
                         or not p.get("project_id")],
        })

        # Merge into overall
        for line, g in gov.items():
            key = f"{loc['id']}:{line}"
            all_governing[key] = g

    overall_summary = compute_compliance_summary(all_governing)

    return {
        "locations": location_data,
        "client_requirements": client_reqs,
        "sources": sources,
        "all_policies": all_policies,
        "overall_summary": overall_summary,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/hopeful-moore && python -m pytest tests/test_compliance.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/compliance.py tests/test_compliance.py
git commit -m "feat: compliance engine with governing resolution, policy matching, and summary

resolve_governing_requirements() auto-resolves multi-source conflicts
to most stringent per coverage line. suggest_policy_for_requirement()
prefers location-specific policies over corporate."
```

---

### Task 4: Compliance Routes — Source & Requirement CRUD

**Files:**
- Create: `src/policydb/web/routes/compliance.py`

- [ ] **Step 1: Create route module with all endpoints**

Create `src/policydb/web/routes/compliance.py`:

```python
"""Compliance review routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.compliance import (
    get_client_compliance_data,
    get_location_requirements,
    resolve_governing_requirements,
    suggest_policy_for_requirement,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _compliance_context(conn, client_id: int, request: Request) -> dict:
    """Build shared template context for compliance page."""
    client = conn.execute(
        "SELECT * FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    data = get_client_compliance_data(conn, client_id)
    return {
        "request": request,
        "client": dict(client) if client else {},
        "client_id": client_id,
        "active": "compliance",
        **data,
        "compliance_statuses": cfg.get("compliance_statuses"),
        "deductible_types": cfg.get("deductible_types"),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_flags": cfg.get("endorsement_flags"),
        "endorsement_flag_labels": cfg.get("endorsement_flag_labels"),
    }


# ── Main page ────────────────────────────────────────────────────────────────

@router.get("/client/{client_id}", response_class=HTMLResponse)
def compliance_index(request: Request, client_id: int, conn=Depends(get_db)):
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Sources CRUD ─────────────────────────────────────────────────────────────

@router.post("/client/{client_id}/sources/add", response_class=HTMLResponse)
def source_add(
    request: Request, client_id: int,
    name: str = Form(...),
    project_id: int | None = Form(None),
    counterparty: str = Form(""),
    clause_ref: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """INSERT INTO requirement_sources
           (client_id, project_id, name, counterparty, clause_ref, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, project_id or None, name, counterparty, clause_ref, notes),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/sources/{source_id}/edit", response_class=HTMLResponse)
def source_edit(
    request: Request, client_id: int, source_id: int,
    name: str = Form(...),
    counterparty: str = Form(""),
    clause_ref: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """UPDATE requirement_sources
           SET name=?, counterparty=?, clause_ref=?, notes=?
           WHERE id=? AND client_id=?""",
        (name, counterparty, clause_ref, notes, source_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/sources/{source_id}/delete", response_class=HTMLResponse)
def source_delete(
    request: Request, client_id: int, source_id: int,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM requirement_sources WHERE id=? AND client_id=?",
        (source_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Requirements CRUD ────────────────────────────────────────────────────────

@router.post("/client/{client_id}/requirements/add", response_class=HTMLResponse)
def requirement_add(
    request: Request, client_id: int,
    coverage_line: str = Form(...),
    project_id: int | None = Form(None),
    source_id: int | None = Form(None),
    risk_id: int | None = Form(None),
    required_limit: float | None = Form(None),
    max_deductible: float | None = Form(None),
    deductible_type: str = Form(""),
    ai_required: int = Form(0),
    wos_required: int = Form(0),
    primary_noncontrib: int = Form(0),
    per_project_aggregate: int = Form(0),
    noc_required: int = Form(0),
    completed_ops_required: int = Form(0),
    professional_liability_required: int = Form(0),
    pollution_required: int = Form(0),
    cyber_required: int = Form(0),
    builders_risk_required: int = Form(0),
    compliance_status: str = Form("Needs Review"),
    linked_policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """INSERT INTO coverage_requirements
           (client_id, project_id, risk_id, source_id, coverage_line,
            required_limit, max_deductible, deductible_type,
            ai_required, wos_required, primary_noncontrib,
            per_project_aggregate, noc_required, completed_ops_required,
            professional_liability_required, pollution_required,
            cyber_required, builders_risk_required,
            compliance_status, linked_policy_uid, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (client_id, project_id or None, risk_id or None, source_id or None,
         coverage_line, required_limit, max_deductible,
         deductible_type or None,
         ai_required, wos_required, primary_noncontrib,
         per_project_aggregate, noc_required, completed_ops_required,
         professional_liability_required, pollution_required,
         cyber_required, builders_risk_required,
         compliance_status, linked_policy_uid or None, notes),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.patch("/client/{client_id}/requirements/{req_id}/cell")
async def requirement_cell_save(
    request: Request, client_id: int, req_id: int,
    conn=Depends(get_db),
):
    """Inline cell save for a single requirement field."""
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")
    allowed = {"coverage_line", "required_limit", "max_deductible", "deductible_type",
               "compliance_status", "linked_policy_uid", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Invalid field: {field}"})
    conn.execute(
        f"UPDATE coverage_requirements SET {field}=? WHERE id=? AND client_id=?",
        (value or None, req_id, client_id),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@router.post("/client/{client_id}/requirements/{req_id}/edit", response_class=HTMLResponse)
def requirement_edit(
    request: Request, client_id: int, req_id: int,
    coverage_line: str = Form(...),
    required_limit: float | None = Form(None),
    max_deductible: float | None = Form(None),
    deductible_type: str = Form(""),
    ai_required: int = Form(0),
    wos_required: int = Form(0),
    primary_noncontrib: int = Form(0),
    per_project_aggregate: int = Form(0),
    noc_required: int = Form(0),
    completed_ops_required: int = Form(0),
    professional_liability_required: int = Form(0),
    pollution_required: int = Form(0),
    cyber_required: int = Form(0),
    builders_risk_required: int = Form(0),
    compliance_status: str = Form("Needs Review"),
    linked_policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """UPDATE coverage_requirements SET
           coverage_line=?, required_limit=?, max_deductible=?, deductible_type=?,
           ai_required=?, wos_required=?, primary_noncontrib=?,
           per_project_aggregate=?, noc_required=?, completed_ops_required=?,
           professional_liability_required=?, pollution_required=?,
           cyber_required=?, builders_risk_required=?,
           compliance_status=?, linked_policy_uid=?, notes=?
           WHERE id=? AND client_id=?""",
        (coverage_line, required_limit, max_deductible,
         deductible_type or None,
         ai_required, wos_required, primary_noncontrib,
         per_project_aggregate, noc_required, completed_ops_required,
         professional_liability_required, pollution_required,
         cyber_required, builders_risk_required,
         compliance_status, linked_policy_uid or None, notes,
         req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/status", response_class=HTMLResponse)
def requirement_status(
    request: Request, client_id: int, req_id: int,
    compliance_status: str = Form(...),
    conn=Depends(get_db),
):
    """Quick status update from matrix cell dropdown."""
    conn.execute(
        "UPDATE coverage_requirements SET compliance_status=? WHERE id=? AND client_id=?",
        (compliance_status, req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/_matrix.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/link-policy", response_class=HTMLResponse)
def requirement_link_policy(
    request: Request, client_id: int, req_id: int,
    linked_policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Link a policy to a requirement (from auto-suggest or manual pick)."""
    conn.execute(
        "UPDATE coverage_requirements SET linked_policy_uid=? WHERE id=? AND client_id=?",
        (linked_policy_uid or None, req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/_matrix.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/delete", response_class=HTMLResponse)
def requirement_delete(
    request: Request, client_id: int, req_id: int,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Location detail partial ──────────────────────────────────────────────────

@router.get("/client/{client_id}/location/{project_id}", response_class=HTMLResponse)
def location_detail(
    request: Request, client_id: int, project_id: int,
    conn=Depends(get_db),
):
    """Drill-down view for a single location's requirements."""
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    reqs = get_location_requirements(conn, client_id, project_id)
    governing = resolve_governing_requirements(reqs)

    # Get all policies that could cover this location
    policies = [dict(r) for r in conn.execute(
        """SELECT policy_uid, policy_type, carrier, limit_amount, deductible,
                  project_id, policy_number
           FROM policies WHERE client_id=? AND archived=0
           AND (project_id IS NULL OR project_id=?)
           ORDER BY policy_type""",
        (client_id, project_id),
    ).fetchall()]

    # Auto-suggest for unlinked requirements
    for line, gov_req in governing.items():
        if not gov_req.get("linked_policy_uid"):
            suggestion = suggest_policy_for_requirement(
                gov_req, policies, location_project_id=project_id
            )
            if suggestion:
                gov_req["suggested_policy"] = suggestion

    # Sources for this location
    sources = [dict(r) for r in conn.execute(
        """SELECT * FROM requirement_sources
           WHERE client_id=? AND (project_id IS NULL OR project_id=?)
           ORDER BY name""",
        (client_id, project_id),
    ).fetchall()]

    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request,
        "client": dict(client) if client else {},
        "client_id": client_id,
        "project": dict(project) if project else {},
        "project_id": project_id,
        "requirements": reqs,
        "governing": governing,
        "sources": sources,
        "policies": policies,
        "compliance_statuses": cfg.get("compliance_statuses"),
        "deductible_types": cfg.get("deductible_types"),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_flags": cfg.get("endorsement_flags"),
        "endorsement_flag_labels": cfg.get("endorsement_flag_labels"),
    })
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/compliance.py
git commit -m "feat: compliance routes — source CRUD, requirement CRUD, matrix partials"
```

---

### Task 5: Register Router + Nav Link

**Files:**
- Modify: `src/policydb/web/app.py:153-165` (add import + include_router)
- Modify: `src/policydb/web/templates/base.html:440-452` (add nav link)

- [ ] **Step 1: Register compliance router in app.py**

In `src/policydb/web/app.py`, add import with the other route imports and add `app.include_router(compliance.router)` after the existing router registrations (after line 165).

Add to imports:
```python
from policydb.web.routes import compliance
```

Add to router registrations:
```python
app.include_router(compliance.router)
```

- [ ] **Step 2: Wire `_get_db` properly in compliance routes**

Replace the `_get_db` function in `compliance.py` with the proper import pattern used by other route files. Check how other routes import `get_db` — likely from `policydb.web.app` or via `Depends`. Match the existing pattern exactly (look at how `clients.py` or `review.py` does it).

- [ ] **Step 3: Add Compliance to nav**

In `src/policydb/web/templates/base.html`, in the "Tools" dropdown (around line 445-451), add a "Compliance" link. Note: the compliance page is per-client, so the top-nav link should go to a client picker or the most recent client. For now, add it to the Tools dropdown pointing to a generic `/compliance` route, OR better yet, add a "Compliance" link on the **client detail page** since it's per-client.

**Recommended approach:** Add a link on the client detail page rather than the top nav. Find the client detail page template and add a tab/button for "Compliance Review" that links to `/compliance/client/{client_id}`.

Search for the client detail template (likely `clients/detail.html` or `clients/show.html`) and add a link in the tabs/actions section.

- [ ] **Step 4: Verify server starts**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/hopeful-moore && timeout 5 python -c "from policydb.web.app import app; print('App loaded OK')" || true`

Expected: "App loaded OK" with no import errors.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/app.py src/policydb/web/templates/
git commit -m "feat: register compliance router and add nav link on client detail"
```

---

### Task 6: Main Compliance Page Template

**Files:**
- Create: `src/policydb/web/templates/compliance/index.html`
- Create: `src/policydb/web/templates/compliance/_summary_banner.html`
- Create: `src/policydb/web/templates/compliance/_matrix.html`

- [ ] **Step 1: Create template directory**

```bash
mkdir -p src/policydb/web/templates/compliance
```

- [ ] **Step 2: Create summary banner partial**

Create `src/policydb/web/templates/compliance/_summary_banner.html`:

This should show:
- Client name + "Compliance Review" heading
- Stats: total locations, total requirements, compliance percentage (donut or bar)
- Counts by status: Compliant (green), Gap (red), Partial (amber), Needs Review (gray)
- "Add Source" and "Add Requirement" action buttons

Follow the visual patterns from `_risks.html` summary widgets (SVG donut for score, colored pills for counts).

- [ ] **Step 3: Create matrix partial**

Create `src/policydb/web/templates/compliance/_matrix.html`:

This is the core view — a table where:
- **Rows** = coverage lines (from governing requirements across all locations)
- **Columns** = locations (from projects)
- **Cells** = compliance status badge + limit info

Each cell should show:
- Color dot: green (Compliant), red (Gap), amber (Partial/Needs Review), gray (N/A/Waived)
- Linked policy number (if linked) or "—"
- Required limit (abbreviated: $1M, $5M, etc.)
- Click to expand location detail

Use HTMX: clicking a location header or a cell row loads the `_location_detail.html` partial below the matrix.

**Important Jinja2 rules:**
- Use single-quote attribute delimiters with `| tojson`: `data-options='{{ items | tojson }}'`
- Never use `{{ }}` inside `<script>` blocks without escaping (use `'{' + '{'` pattern)

- [ ] **Step 4: Create main index.html**

Create `src/policydb/web/templates/compliance/index.html`:

```html
{% extends "base.html" %}
{% block title %}Compliance Review — {{ client.name }}{% endblock %}
{% block content %}
<div class="mx-auto max-w-[1600px] px-4 sm:px-6 lg:px-8 py-6">

  {# Breadcrumb #}
  <nav class="text-sm text-gray-500 mb-4">
    <a href="/clients" class="hover:text-marsh">Clients</a>
    <span class="mx-1">/</span>
    <a href="/clients/{{ client_id }}" class="hover:text-marsh">{{ client.name }}</a>
    <span class="mx-1">/</span>
    <span class="text-gray-700 font-medium">Compliance Review</span>
  </nav>

  {# Summary banner #}
  {% include "compliance/_summary_banner.html" %}

  {# Source management #}
  <details class="mt-6 bg-white rounded-lg shadow-sm border border-gray-200">
    <summary class="px-5 py-3 cursor-pointer font-semibold text-sm text-gray-700 hover:bg-gray-50">
      Requirement Sources ({{ sources | length }})
    </summary>
    <div class="px-5 pb-4">
      {# List existing sources #}
      {% for src in sources %}
      <div class="flex items-center gap-3 py-2 border-b border-gray-100">
        <span class="font-medium text-sm">{{ src.name }}</span>
        {% if src.counterparty %}
        <span class="text-xs text-gray-500">{{ src.counterparty }}</span>
        {% endif %}
        {% if src.project_id %}
        <span class="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">Location-specific</span>
        {% else %}
        <span class="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">All locations</span>
        {% endif %}
        <form hx-post="/compliance/client/{{ client_id }}/sources/{{ src.id }}/delete"
              hx-target="body" hx-confirm="Delete source '{{ src.name }}'?"
              class="ml-auto">
          <button class="text-red-400 hover:text-red-600 text-xs">Delete</button>
        </form>
      </div>
      {% endfor %}

      {# Add source form #}
      <form hx-post="/compliance/client/{{ client_id }}/sources/add"
            hx-target="body" class="mt-3 flex flex-wrap gap-2 items-end">
        <div>
          <label class="block text-xs text-gray-500">Contract/Source Name</label>
          <input name="name" required class="border rounded px-2 py-1 text-sm w-48">
        </div>
        <div>
          <label class="block text-xs text-gray-500">Counterparty</label>
          <input name="counterparty" class="border rounded px-2 py-1 text-sm w-36">
        </div>
        <div>
          <label class="block text-xs text-gray-500">Location (optional)</label>
          <select name="project_id" class="border rounded px-2 py-1 text-sm">
            <option value="">All Locations</option>
            {% for loc in locations %}
            <option value="{{ loc.project.id }}">{{ loc.project.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Clause Ref</label>
          <input name="clause_ref" class="border rounded px-2 py-1 text-sm w-24">
        </div>
        <div>
          <label class="block text-xs text-gray-500">Notes</label>
          <input name="notes" class="border rounded px-2 py-1 text-sm w-48">
        </div>
        <button type="submit" class="bg-marsh text-white px-3 py-1 rounded text-sm hover:bg-marsh-dark">
          Add Source
        </button>
      </form>
    </div>
  </details>

  {# Coverage Matrix #}
  <div id="compliance-matrix" class="mt-6">
    {% include "compliance/_matrix.html" %}
  </div>

  {# Location detail (loaded via HTMX) #}
  <div id="location-detail" class="mt-6"></div>

  {# Add Requirement form #}
  <details class="mt-6 bg-white rounded-lg shadow-sm border border-gray-200">
    <summary class="px-5 py-3 cursor-pointer font-semibold text-sm text-gray-700 hover:bg-gray-50">
      + Add Requirement
    </summary>
    <div class="px-5 pb-4">
      <form hx-post="/compliance/client/{{ client_id }}/requirements/add"
            hx-target="body" class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
        <div>
          <label class="block text-xs text-gray-500">Coverage Line</label>
          <select name="coverage_line" required class="border rounded px-2 py-1 text-sm w-full">
            {% for pt in policy_types %}
            <option>{{ pt }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Location</label>
          <select name="project_id" class="border rounded px-2 py-1 text-sm w-full">
            <option value="">All Locations (Client-wide)</option>
            {% for loc in locations %}
            <option value="{{ loc.project.id }}">{{ loc.project.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Source</label>
          <select name="source_id" class="border rounded px-2 py-1 text-sm w-full">
            <option value="">None</option>
            {% for src in sources %}
            <option value="{{ src.id }}">{{ src.name }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Required Limit</label>
          <input name="required_limit" type="number" step="1000"
                 class="border rounded px-2 py-1 text-sm w-full" placeholder="1000000">
        </div>
        <div>
          <label class="block text-xs text-gray-500">Max Deductible</label>
          <input name="max_deductible" type="number" step="100"
                 class="border rounded px-2 py-1 text-sm w-full" placeholder="2000">
        </div>
        <div>
          <label class="block text-xs text-gray-500">Deductible Type</label>
          <select name="deductible_type" class="border rounded px-2 py-1 text-sm w-full">
            <option value="">—</option>
            {% for dt in deductible_types %}
            <option>{{ dt }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Status</label>
          <select name="compliance_status" class="border rounded px-2 py-1 text-sm w-full">
            {% for s in compliance_statuses %}
            <option>{{ s }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Notes</label>
          <input name="notes" class="border rounded px-2 py-1 text-sm w-full">
        </div>

        {# Endorsement flags #}
        <div class="col-span-full">
          <label class="block text-xs text-gray-500 mb-1">Required Endorsements</label>
          <div class="flex flex-wrap gap-3">
            {% for flag in endorsement_flags %}
            <label class="flex items-center gap-1 text-xs">
              <input type="checkbox" name="{{ flag }}" value="1" class="rounded">
              {{ endorsement_flag_labels[flag] }}
            </label>
            {% endfor %}
          </div>
        </div>

        <div class="col-span-full">
          <button type="submit" class="bg-marsh text-white px-4 py-2 rounded text-sm hover:bg-marsh-dark">
            Add Requirement
          </button>
        </div>
      </form>
    </div>
  </details>

</div>
{% endblock %}
```

**Note:** The implementing agent should refine the template to match the existing UI patterns (card styles, spacing, font sizes) found in `clients/_risks.html` and `base.html`. The above is structural — the visual polish should follow the codebase's existing Tailwind conventions.

- [ ] **Step 5: Verify page loads**

Start the server and navigate to `/compliance/client/{some_client_id}`.

Expected: Page renders with breadcrumb, empty summary, empty matrix, and add forms.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/compliance/
git commit -m "feat: compliance review page templates — index, matrix, summary banner"
```

---

### Task 7: Location Detail Partial

**Files:**
- Create: `src/policydb/web/templates/compliance/_location_detail.html`
- Create: `src/policydb/web/templates/compliance/_requirement_row.html`

- [ ] **Step 1: Create requirement row partial**

Create `src/policydb/web/templates/compliance/_requirement_row.html`:

This template renders a single requirement with:
- Coverage line name
- Required limit (formatted as currency)
- Max deductible (if set)
- Endorsement flags as small pills (green if required, gray if not)
- Compliance status badge (colored: green/red/amber/gray)
- Linked policy badge (clickable to policy detail)
- Auto-suggested policy (if no linked policy, show suggestion with "Link" button)
- Source name (if linked to a source)
- Notes field
- Edit/Delete action buttons
- Status dropdown (auto-saves via HTMX POST to `/compliance/client/{client_id}/requirements/{req_id}/status`)

Follow the pattern from `_risk_matrix_row.html` for inline editing and action buttons.

- [ ] **Step 2: Create location detail partial**

Create `src/policydb/web/templates/compliance/_location_detail.html`:

This template is loaded via HTMX when a location is clicked in the matrix. It shows:
- Location header: name, address, policy count
- **Per-source sections**: For each requirement source, show its requirements grouped together with the source name as a header and clause_ref/counterparty as metadata
- **Governing requirements summary**: The auto-resolved most-stringent requirement per coverage line, with "Governed by: [source name]" annotation
- **Policies available**: List of policies assigned to this location + corporate policies
- "Link Policy" dropdowns for each requirement

Uses HTMX for status changes and policy linking (target the matrix partial for OOB updates).

- [ ] **Step 3: Verify drill-down works**

Create a test location and some requirements, then click through the matrix to the location detail.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/compliance/
git commit -m "feat: location detail partial with requirement rows, source grouping, policy linking"
```

---

### Task 8: Source Form Partial

**Files:**
- Create: `src/policydb/web/templates/compliance/_source_form.html`

- [ ] **Step 1: Create source form partial**

A reusable form partial for adding/editing a requirement source. Used both in the index page sources section and potentially from the location detail view.

Fields: name (required), counterparty, clause_ref, project_id (dropdown), notes.

HTMX: posts to `/compliance/client/{client_id}/sources/add` (or `.../edit`), targets body for full refresh.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/compliance/_source_form.html
git commit -m "feat: source form partial for compliance page"
```

---

### Task 9: Integration Test

**Files:**
- Modify: `tests/test_compliance.py` (add integration tests)

- [ ] **Step 1: Add integration tests with real database**

Add tests that use a real SQLite database to verify the full flow:

```python
import sqlite3
import tempfile
from pathlib import Path


def _setup_db():
    """Create a temp database with schema for testing."""
    db_path = Path(tempfile.mktemp(suffix=".sqlite"))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Run migration 066 SQL directly
    migration_sql = (Path(__file__).parent.parent /
                     "src/policydb/migrations/066_compliance_requirements.sql").read_text()
    conn.executescript(migration_sql)
    # Create minimal supporting tables
    conn.executescript("""
        CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, client_id INTEGER, name TEXT);
        CREATE TABLE policies (policy_uid TEXT PRIMARY KEY, client_id INTEGER,
                              policy_type TEXT, carrier TEXT, limit_amount REAL,
                              deductible REAL, project_id INTEGER, archived INTEGER DEFAULT 0,
                              policy_number TEXT);
        CREATE TABLE client_risks (id INTEGER PRIMARY KEY, client_id INTEGER,
                                   category TEXT, severity TEXT DEFAULT 'Medium');
    """)
    return conn


def test_full_compliance_flow():
    """End-to-end: create source, add requirements, resolve governing."""
    conn = _setup_db()

    # Setup: client with 2 locations
    conn.execute("INSERT INTO clients VALUES (1, 'ABC Condos')")
    conn.execute("INSERT INTO projects VALUES (1, 1, 'Location A')")
    conn.execute("INSERT INTO projects VALUES (2, 1, 'Location B')")

    # Corporate GL policy
    conn.execute("""INSERT INTO policies VALUES
        ('POL-GL', 1, 'General Liability', 'Hartford', 2000000, 5000, NULL, 0, 'GL-001')""")

    # Source: management agreement
    conn.execute("""INSERT INTO requirement_sources
        (client_id, project_id, name, counterparty) VALUES (1, NULL, 'Mgmt Agreement', 'ABC Mgmt')""")
    # Source: lender for Location A only
    conn.execute("""INSERT INTO requirement_sources
        (client_id, project_id, name, counterparty) VALUES (1, 1, 'Lender Covenant', 'First Bank')""")
    conn.commit()

    # Requirement from mgmt agreement (client-wide): GL $1M
    conn.execute("""INSERT INTO coverage_requirements
        (client_id, project_id, source_id, coverage_line, required_limit)
        VALUES (1, NULL, 1, 'General Liability', 1000000)""")
    # Requirement from lender (Location A only): GL $2M, AI required
    conn.execute("""INSERT INTO coverage_requirements
        (client_id, project_id, source_id, coverage_line, required_limit, ai_required)
        VALUES (1, 1, 2, 'General Liability', 2000000, 1)""")
    conn.commit()

    from policydb.compliance import get_location_requirements, resolve_governing_requirements

    # Location A should see both requirements, governing = $2M with AI
    reqs_a = get_location_requirements(conn, 1, 1)
    assert len(reqs_a) == 2  # mgmt agreement + lender

    gov_a = resolve_governing_requirements(reqs_a)
    assert gov_a["General Liability"]["required_limit"] == 2_000_000
    assert gov_a["General Liability"]["ai_required"] == 1

    # Location B should see only mgmt agreement, governing = $1M
    reqs_b = get_location_requirements(conn, 1, 2)
    assert len(reqs_b) == 1

    gov_b = resolve_governing_requirements(reqs_b)
    assert gov_b["General Liability"]["required_limit"] == 1_000_000

    conn.close()
```

- [ ] **Step 2: Run all compliance tests**

Run: `python -m pytest tests/test_compliance.py -v`

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_compliance.py
git commit -m "test: integration tests for compliance engine with real database"
```

---

## Phase 2: Templates & COPE

### Task 10: Requirement Templates CRUD

**Files:**
- Add routes to: `src/policydb/web/routes/compliance.py`
- Create: `src/policydb/web/templates/compliance/_template_picker.html`

- [ ] **Step 1: Add template management routes**

Add to `compliance.py`:
- `GET /compliance/templates` — list all templates
- `POST /compliance/templates/add` — create template with name + description
- `POST /compliance/templates/{template_id}/items/add` — add item to template
- `POST /compliance/templates/{template_id}/items/{item_id}/delete` — remove item
- `POST /compliance/templates/{template_id}/delete` — delete template
- `POST /compliance/client/{client_id}/apply-template` — apply template to a location:
  - Reads all `requirement_template_items` for the template
  - For each item, inserts a `coverage_requirements` row with the template item's values
  - Sets `project_id` and optional `source_id` from form params

- [ ] **Step 2: Add "copy from location" route**

Add to `compliance.py`:
- `POST /compliance/client/{client_id}/copy-requirements` — copy all requirements from one project_id to another:
  - Reads all `coverage_requirements` for source project_id
  - Inserts copies with new project_id (target location)
  - Optionally re-maps source_id if the source also exists for target location

- [ ] **Step 3: Create template picker partial**

`_template_picker.html` shows:
- Dropdown of available templates
- Preview of template items when selected
- "Apply to Location" button with location dropdown
- "Copy from Location" section with source/target location dropdowns

- [ ] **Step 4: Test template apply and copy flows**

Add tests to `test_compliance.py`:
```python
def test_apply_template_creates_requirements():
    # Create template + items, apply to location, verify requirements created
    ...

def test_copy_requirements_between_locations():
    # Create requirements on Location A, copy to Location B, verify
    ...
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/compliance.py src/policydb/web/templates/compliance/ tests/test_compliance.py
git commit -m "feat: requirement templates — create, manage, apply to locations, copy between locations"
```

---

### Task 11: COPE Data CRUD

**Files:**
- Add routes to: `src/policydb/web/routes/compliance.py`
- Create: `src/policydb/web/templates/compliance/_cope_section.html`

- [ ] **Step 1: Add COPE data routes**

Add to `compliance.py`:
- `GET /compliance/client/{client_id}/location/{project_id}/cope` — get COPE data partial
- `POST /compliance/client/{client_id}/location/{project_id}/cope/save` — upsert COPE data (INSERT OR REPLACE)
- `PATCH /compliance/client/{client_id}/location/{project_id}/cope/cell` — inline cell save

- [ ] **Step 2: Create COPE section template**

`_cope_section.html` displays COPE data in a compact grid (follows contenteditable pattern from `_risks.html`):
- Construction Type (combobox from `construction_types` config)
- Year Built, Stories, Sq Footage (contenteditable numeric cells)
- Sprinklered (combobox from `sprinkler_options` config)
- Roof Type (contenteditable text)
- Occupancy Description (contenteditable text)
- Protection Class (contenteditable text)
- TIV (contenteditable currency)
- Notes (contenteditable text area)

Save on blur via PATCH. Flash green on server-formatted response (follow `flashCell` pattern).

- [ ] **Step 3: Include COPE section in location detail**

Add `{% include "compliance/_cope_section.html" %}` to the location detail partial, in a collapsible `<details>` section labeled "COPE Data".

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/compliance.py src/policydb/web/templates/compliance/
git commit -m "feat: COPE data capture per location — inline editable grid"
```

---

## Phase 3: Integration & Polish

### Task 12: Risk → Requirement Spawning

**Files:**
- Modify: `src/policydb/web/routes/clients.py` (add spawn endpoint)
- Modify: `src/policydb/web/templates/clients/_risk_detail.html` (add button)

- [ ] **Step 1: Add "Create Requirement" button to risk detail**

In `_risk_detail.html`, after the coverage lines section, add a button:
```html
<a href="/compliance/client/{{ client_id }}?from_risk={{ risk.id }}"
   class="text-xs text-marsh hover:underline">
  Create Compliance Requirement from this Risk →
</a>
```

Or better: an HTMX POST that creates a requirement pre-filled from the risk's coverage lines.

- [ ] **Step 2: Add spawn route**

In `compliance.py`, add:
- `POST /compliance/client/{client_id}/spawn-from-risk/{risk_id}` — reads risk + coverage lines, creates `coverage_requirements` entries for each coverage line with `risk_id` set

- [ ] **Step 3: Test spawn flow**

```python
def test_spawn_requirements_from_risk():
    # Create risk with 2 coverage lines, spawn, verify 2 requirements created with risk_id set
    ...
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/compliance.py src/policydb/web/routes/clients.py \
        src/policydb/web/templates/clients/_risk_detail.html tests/test_compliance.py
git commit -m "feat: spawn compliance requirements from risk profile coverage lines"
```

---

### Task 13: Auto-Suggest Policy Matching Enhancement

**Files:**
- Modify: `src/policydb/compliance.py` (enhance matching with fuzzy)

- [ ] **Step 1: Enhance suggest_policy_for_requirement with RapidFuzz**

Add fuzzy matching when exact `normalize_coverage_type` match fails:

```python
from rapidfuzz import fuzz

# If no exact match, try fuzzy match with threshold >= 80
for pol in policies:
    score = fuzz.ratio(target_normalized, normalize_coverage_type(pol.get("policy_type", "")))
    if score >= 80:
        fuzzy_matches.append((score, pol))
```

- [ ] **Step 2: Add limit comparison to suggestions**

When suggesting a policy, include limit comparison info:
```python
suggestion["limit_adequate"] = (pol["limit_amount"] or 0) >= (gov_req.get("required_limit") or 0)
suggestion["deductible_adequate"] = True  # if no max_deductible requirement
if gov_req.get("max_deductible") and pol.get("deductible"):
    suggestion["deductible_adequate"] = pol["deductible"] <= gov_req["max_deductible"]
```

- [ ] **Step 3: Test fuzzy matching**

```python
def test_suggest_fuzzy_coverage_match():
    """'GL' matches 'General Liability' via normalization."""
    policies = [_policy(policy_type="General Liability")]
    gov_req = {"coverage_line": "GL", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies)
    assert suggestion is not None
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/compliance.py tests/test_compliance.py
git commit -m "feat: enhanced policy matching with fuzzy coverage type matching and limit comparison"
```

---

### Task 14: Inheritance Cascade Visualization

**Files:**
- Modify: `src/policydb/web/templates/compliance/_location_detail.html`

- [ ] **Step 1: Add visual indicators for inherited vs. local requirements**

In the location detail template, when rendering requirements:
- Requirements with `project_id IS NULL` get a "↓ Inherited" badge (blue pill)
- Requirements with `project_id = this location` get a "Local" badge (purple pill)
- The governing requirement row shows "Governed by: [source name]" in small text

- [ ] **Step 2: Show override situations**

When a local requirement overrides an inherited one (same coverage line, local is more stringent), show a visual indicator:
- Inherited requirement struck through or dimmed
- Local requirement highlighted
- "Overrides inherited" annotation

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/compliance/
git commit -m "feat: visual inheritance indicators — inherited vs local requirements with override display"
```

---

### Task 15: Guided Risk Review Prompts

**Files:**
- Create: `src/policydb/web/templates/compliance/_risk_prompts.html`
- Modify: `src/policydb/web/templates/compliance/index.html` (include prompts section)
- Modify: `src/policydb/compliance.py` (add prompt generation logic)

**Context:** The risk-analysis skill (`.claude/skills/risk-analysis-skill/`) defines a structured analytical workflow: exposure identification → risk quantification → coverage strategy → gap analysis. This task extracts key prompts from that workflow and surfaces them as guided review questions on the compliance page, helping the user systematically walk through a client's risk profile during review meetings.

- [ ] **Step 1: Add risk review prompt config defaults**

Add to `_DEFAULTS` in `src/policydb/config.py`:

```python
"risk_review_prompt_categories": [
    "Operational", "People", "Liability", "Financial", "Contractual",
],
"risk_review_prompts": [
    {
        "category": "Operational",
        "question": "What are the critical assets at each location — property, equipment, inventory, IP, data? What perils threaten them (CAT zones, flood, wind, seismic)?",
        "coverage_lines": ["Property", "Inland Marine / Equipment", "Builders Risk"],
        "industry_keywords_high": [],
    },
    {
        "category": "Operational",
        "question": "What is the revenue model and what interrupts it? Are there single-source suppliers or long lead-time dependencies?",
        "coverage_lines": ["Property"],
        "industry_keywords_high": [],
    },
    {
        "category": "People",
        "question": "Are subcontractors or contingent labor used? Are certificates of insurance collected and tracked for all subs?",
        "coverage_lines": ["General Liability", "Umbrella / Excess", "Workers Compensation"],
        "industry_keywords_high": ["construction", "contractor", "builder"],
    },
    {
        "category": "People",
        "question": "Is there a board of directors, HOA board, or management committee? What management liability exposure exists?",
        "coverage_lines": ["Directors & Officers", "Employment Practices"],
        "industry_keywords_high": ["condo", "hoa", "association", "nonprofit"],
    },
    {
        "category": "Liability",
        "question": "What contractual indemnification obligations exist? Do upstream contracts require specific AI, WOS, or primary/noncontributory endorsements?",
        "coverage_lines": ["General Liability", "Umbrella / Excess", "Professional Liability / E&O"],
        "industry_keywords_high": [],
    },
    {
        "category": "Liability",
        "question": "Does the organization give advice, design, certify, or provide professional services? Is there completed operations exposure?",
        "coverage_lines": ["Professional Liability / E&O", "General Liability"],
        "industry_keywords_high": ["architect", "engineer", "consultant"],
    },
    {
        "category": "Liability",
        "question": "Is there pollution or environmental liability exposure at any location? Underground storage tanks, hazardous materials, or remediation obligations?",
        "coverage_lines": ["Pollution / Environmental"],
        "industry_keywords_high": ["manufacturing", "chemical", "energy", "oil"],
    },
    {
        "category": "Liability",
        "question": "What data does the organization collect, store, or process? What systems are mission-critical? Is there regulatory exposure (PII, PHI, PCI)?",
        "coverage_lines": ["Cyber / Privacy"],
        "industry_keywords_high": ["technology", "healthcare", "financial"],
    },
    {
        "category": "Financial",
        "question": "What is the organization's balance sheet capacity to retain risk? What deductible/SIR level represents the pain threshold?",
        "coverage_lines": [],
        "industry_keywords_high": [],
    },
    {
        "category": "Financial",
        "question": "Is there crime, social engineering fraud, or employee theft exposure? Are fiduciary obligations (ERISA, benefit plans) in scope?",
        "coverage_lines": ["Crime / Fidelity"],
        "industry_keywords_high": [],
    },
    {
        "category": "Contractual",
        "question": "Are there OCIP/CCIP (wrap-up) programs at any location? Which parties are enrolled vs. excluded?",
        "coverage_lines": ["General Liability", "Workers Compensation", "Umbrella / Excess"],
        "industry_keywords_high": ["construction", "development"],
    },
    {
        "category": "Contractual",
        "question": "Do different locations have different lenders, management agreements, or counterparties with distinct insurance requirements?",
        "coverage_lines": [],
        "industry_keywords_high": ["condo", "hoa", "real estate", "portfolio"],
    },
],
```

These are editable in the Settings UI — users can add, remove, or modify prompts to match their book of business. The `industry_keywords_high` field drives priority auto-escalation: if any keyword matches the client's industry, the prompt becomes High priority.

- [ ] **Step 2: Create risk prompt generator in compliance.py**

Add to `src/policydb/compliance.py`:

```python
def get_risk_review_prompts(client: dict, locations: list[dict],
                            policies: list[dict], cfg_prompts: list[dict]) -> list[dict]:
    """Generate guided risk review prompts based on client profile and coverage gaps.

    Reads prompt definitions from config (editable in Settings).
    Dynamically sets priority based on industry match and coverage gaps.

    Args:
        client: Client dict with industry_segment
        locations: List of location/project dicts
        policies: List of policy dicts with policy_type
        cfg_prompts: The risk_review_prompts list from config

    Returns list of prompt dicts with added fields:
    - relevance: str (auto-generated context note)
    - priority: str (High/Medium/Low)
    """
    industry = (client.get("industry_segment") or "").lower()
    policy_types = {p.get("policy_type", "") for p in policies}
    has_locations = len(locations) > 1

    results = []
    for prompt_def in cfg_prompts:
        prompt = dict(prompt_def)
        coverage_lines = prompt.get("coverage_lines", [])
        keywords = prompt.get("industry_keywords_high", [])

        # Determine priority
        if any(kw in industry for kw in keywords):
            prompt["priority"] = "High"
        elif coverage_lines and not any(cl in policy_types for cl in coverage_lines):
            prompt["priority"] = "High"  # coverage gap = high priority
        else:
            prompt["priority"] = "Medium"

        # Generate relevance note
        if has_locations and "location" in prompt.get("question", "").lower():
            prompt["relevance"] = f"Client has {len(locations)} location(s) — review per-location exposure."
        elif coverage_lines:
            missing = [cl for cl in coverage_lines if cl not in policy_types]
            if missing:
                prompt["relevance"] = f"No current policy for: {', '.join(missing)}"
            else:
                prompt["relevance"] = f"Current coverage includes: {', '.join(coverage_lines[:2])}"
        else:
            prompt["relevance"] = ""

        results.append(prompt)

    return results
```

Note: `cfg_prompts` is passed in from the route (read from config), NOT hardcoded in this module.

- [ ] **Step 3: Wire prompts into compliance route context**

In `_compliance_context()`:
```python
prompts = get_risk_review_prompts(
    dict(client) if client else {},
    [loc["project"] for loc in data["locations"]],
    data.get("all_policies", []),
    cfg.get("risk_review_prompts", []),
)
# Add to context:
"risk_prompts": prompts,
"risk_prompt_categories": cfg.get("risk_review_prompt_categories"),
```

- [ ] **Step 2: Create risk prompts template partial**

Create `src/policydb/web/templates/compliance/_risk_prompts.html`:

This should render the prompts as a collapsible "Guided Review" section on the compliance page:
- Grouped by category (Operational, People, Liability, Financial, Contractual)
- Each prompt shows: question text, relevance note (smaller/gray), related coverage lines as pills
- High priority prompts have a red left border, Medium amber, Low gray
- Each prompt has a checkbox to mark "Reviewed" (client-side only, not persisted — this is a review aid)
- A progress counter at the top: "5 of 12 reviewed"
- Collapsible by default — expanded during active review sessions

Style: follow the risk matrix row pattern with left border coloring by priority.

- [ ] **Step 3: Include prompts in compliance index page**

Add to `compliance/index.html` after the summary banner and before the matrix:

```html
{# Guided Risk Review Prompts #}
{% include "compliance/_risk_prompts.html" %}
```

Add to `_compliance_context()` in the routes:
```python
from policydb.compliance import get_risk_review_prompts
# ... in _compliance_context():
prompts = get_risk_review_prompts(
    dict(client) if client else {},
    [loc["project"] for loc in data["locations"]],
    data.get("all_policies", []),
)
# Add to context:
"risk_prompts": prompts,
```

- [ ] **Step 4: Test prompt generation**

Add to `tests/test_compliance.py`:

```python
# Sample config prompts for testing (mirrors _DEFAULTS structure)
_TEST_PROMPTS = [
    {"category": "Operational", "question": "What are the critical assets?",
     "coverage_lines": ["Property"], "industry_keywords_high": []},
    {"category": "People", "question": "Are subcontractors used?",
     "coverage_lines": ["General Liability", "Workers Compensation"],
     "industry_keywords_high": ["construction", "contractor"]},
    {"category": "Liability", "question": "What data is collected?",
     "coverage_lines": ["Cyber / Privacy"],
     "industry_keywords_high": ["technology", "healthcare"]},
]


def test_risk_prompts_basic():
    """Prompts are generated from config for any client."""
    from policydb.compliance import get_risk_review_prompts
    client = {"name": "ABC Condos", "industry_segment": "Condo/HOA"}
    locations = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    policies = [{"policy_type": "General Liability"}, {"policy_type": "Property"}]
    prompts = get_risk_review_prompts(client, locations, policies, _TEST_PROMPTS)
    assert len(prompts) == 3
    categories = {p["category"] for p in prompts}
    assert "Operational" in categories


def test_risk_prompts_construction_prioritizes_subs():
    """Construction industry keyword match escalates to High priority."""
    from policydb.compliance import get_risk_review_prompts
    client = {"name": "Big Builder", "industry_segment": "General Contractor"}
    prompts = get_risk_review_prompts(
        client, [{"id": 1, "name": "Site A"}], [], _TEST_PROMPTS
    )
    sub_prompts = [p for p in prompts if "subcontractor" in p["question"].lower()]
    assert len(sub_prompts) >= 1
    assert sub_prompts[0]["priority"] == "High"


def test_risk_prompts_coverage_gap_escalates_priority():
    """Missing coverage type escalates prompt to High priority."""
    from policydb.compliance import get_risk_review_prompts
    prompts = get_risk_review_prompts(
        {"name": "Tech Co", "industry_segment": "Technology"},
        [{"id": 1, "name": "HQ"}],
        [{"policy_type": "General Liability"}],  # no Cyber
        _TEST_PROMPTS,
    )
    cyber = [p for p in prompts if "Cyber" in str(p.get("coverage_lines", []))]
    assert any(p["priority"] == "High" for p in cyber)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_compliance.py -v -k "prompt"`

Expected: All prompt tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/compliance.py src/policydb/web/routes/compliance.py \
        src/policydb/web/templates/compliance/ tests/test_compliance.py
git commit -m "feat: guided risk review prompts — config-driven review questions

Prompts stored in config.yaml, editable via Settings UI.
Auto-prioritizes by industry keyword match and coverage gaps.
Organized by exposure category from risk analysis framework."
```

---

## Phase 4: Reporting

### Task 16: XLSX Export

**Files:**
- Modify: `src/policydb/exporter.py` (add compliance export function)
- Add route to: `src/policydb/web/routes/compliance.py`

- [ ] **Step 1: Add export function to exporter.py**

Add `export_compliance_xlsx(conn, client_id) -> bytes`:

**Sheet 1: Executive Summary**
- Client name, date generated
- Total locations, total requirements, compliance %
- Status breakdown table: coverage line | compliant count | gap count | needs review count
- Top gaps list (sorted by severity)

**Sheet 2: Compliance Matrix**
- Row per coverage line
- Column per location
- Cell value: "✓ $1M (POL-001)" or "✗ GAP" or "~ Partial"
- Conditional formatting: green fill for compliant, red for gap, amber for partial

**Sheet 3+: Per-Location Details (one sheet per location)**
- Location name, address, COPE summary
- All requirements with source, limit, deductible, endorsement flags, status, linked policy

Follow existing patterns: use `_write_sheet()`, `_HEADER_FILL`, `_HEADER_FONT`, `_wb_to_bytes()`.

- [ ] **Step 2: Add export route**

```python
@router.get("/client/{client_id}/export/xlsx")
def compliance_export_xlsx(client_id: int, conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_compliance_xlsx
    content = export_compliance_xlsx(conn, client_id)
    client = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    safe_name = (client["name"] if client else "client").lower().replace(" ", "_")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_compliance.xlsx"'},
    )
```

- [ ] **Step 3: Add export button to compliance page**

Add download button to `_summary_banner.html`:
```html
<a href="/compliance/client/{{ client_id }}/export/xlsx"
   class="bg-white text-marsh border border-marsh px-3 py-1 rounded text-sm hover:bg-marsh hover:text-white">
  Export XLSX
</a>
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/exporter.py src/policydb/web/routes/compliance.py \
        src/policydb/web/templates/compliance/
git commit -m "feat: compliance XLSX export — executive summary, matrix, per-location detail sheets"
```

---

### Task 17: PDF Export

**Files:**
- Modify: `src/policydb/exporter.py` (add PDF export function)
- Add route to: `src/policydb/web/routes/compliance.py`
- Create: `src/policydb/web/templates/compliance/print.html`

- [ ] **Step 1: Add PDF export function using fpdf2**

Add `export_compliance_pdf(conn, client_id) -> bytes`:

Follow the pattern from `project_pdf()` in `src/policydb/web/routes/clients.py` (line ~2480). Two sections:

**Page 1: Executive Summary**
- Client name, date
- Compliance score (large number)
- Status summary table
- Top 5 gaps with recommended actions

**Page 2+: Compliance Matrix**
- Table with locations as columns, coverage lines as rows
- Color-coded cells

**Remaining pages: Per-location detail**
- Requirements list with all fields

- [ ] **Step 2: Create print-friendly HTML template**

Create `compliance/print.html` following the pattern from `clients/project_print.html`:
- Standalone HTML with embedded CSS (no Tailwind CDN dependency)
- Print button calls `window.print()`
- `@media print` rules for clean output
- Break-page between locations

- [ ] **Step 3: Add routes**

```python
@router.get("/client/{client_id}/export/pdf")
def compliance_export_pdf(client_id: int, conn=Depends(get_db)):
    ...

@router.get("/client/{client_id}/print", response_class=HTMLResponse)
def compliance_print(request: Request, client_id: int, conn=Depends(get_db)):
    ...
```

- [ ] **Step 4: Add PDF/Print buttons to summary banner**

```html
<a href="/compliance/client/{{ client_id }}/export/pdf" class="...">Export PDF</a>
<a href="/compliance/client/{{ client_id }}/print" target="_blank" class="...">Print View</a>
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/exporter.py src/policydb/web/routes/compliance.py \
        src/policydb/web/templates/compliance/
git commit -m "feat: compliance PDF export and print-friendly view"
```

---

## Implementation Notes for Agentic Workers

### Database patterns
- Always use `conn.execute()` with parameterized queries (never f-strings with SQL)
- `conn.row_factory = sqlite3.Row` is set globally — all rows are dict-like
- Call `conn.commit()` after writes, before returning response
- Use `ON DELETE CASCADE` for child tables; `ON DELETE SET NULL` for optional FKs

### HTMX patterns
- Source/requirement CRUD endpoints return full page re-render (`templates.TemplateResponse("compliance/index.html", ctx)`) for simplicity
- Matrix and location detail are partials that can be swapped independently
- Status changes on the matrix should return `_matrix.html` partial (target `#compliance-matrix`)
- Location detail loads into `#location-detail` div

### Config access
- `from policydb import config as cfg` (module-level import, NOT a class)
- `cfg.get("key_name")` returns the value (with defaults from `_DEFAULTS`)

### Currency formatting
- Use the existing `fmt_currency` Jinja2 filter (registered in `app.py`)
- For display: `{{ value | currency }}` renders as `$1,000,000`

### Testing
- Pure logic tests don't need a database — use dicts
- Integration tests create a temp SQLite database with the migration SQL
- Follow the helper function pattern from `test_reconcile_algorithm.py`

### Key Jinja2 Rules (from CLAUDE.md)
- `data-options='{{ items | tojson }}'` — single quotes for attribute, `tojson` alone (no `| e`)
- In `<script>` blocks: use `'{' + '{'` instead of `{{` for JS template strings
- `{% set var = ... %}` for template variables

### Endorsement Flag Labels
When displaying endorsement flags, use `endorsement_flag_labels` dict from config:
```html
{% for flag in endorsement_flags %}
  {% if gov_req[flag] %}
  <span class="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
    {{ endorsement_flag_labels[flag] }}
  </span>
  {% endif %}
{% endfor %}
```
