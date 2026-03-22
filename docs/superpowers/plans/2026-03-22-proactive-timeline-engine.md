# Proactive Timeline Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the follow-up system from reactive tracking to a proactive workflow engine with accountability-aware urgency, timeline drift management, prep alerts, and graduated risk visibility.

**Architecture:** New `timeline_engine.py` module owns all timeline logic (generation, recalculation, health computation). A `policy_timeline` table stores ideal vs projected dates per milestone. Dispositions map to accountability states that drive how items appear in the restructured Action Center. The review panel becomes the primary workflow for assigning milestone profiles.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Jinja2/HTMX, pytest

**Spec:** `docs/superpowers/specs/2026-03-22-proactive-timeline-engine-design.md`

**Pre-requisite:** Branch `claude/lucid-euclid` must be merged to `main` before starting implementation. Rebase this worktree onto main after that merge.

---

## Phase 1: Foundation

### Task 1: Migration 069 — policy_timeline table + milestone_profile column

**Files:**
- Create: `src/policydb/migrations/069_policy_timeline.sql`
- Modify: `src/policydb/db.py` (wire migration 068+069 into `_KNOWN_MIGRATIONS` and `init_db()`)
- Test: `tests/test_timeline_engine.py`

- [ ] **Step 1: Write failing test for migration**

```python
# tests/test_timeline_engine.py
import pytest
from policydb.db import init_db, get_connection

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path

def test_policy_timeline_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='policy_timeline'"
    )
    assert cur.fetchone() is not None

def test_policy_timeline_columns(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute("PRAGMA table_info(policy_timeline)")
    cols = {r["name"] for r in cur.fetchall()}
    expected = {
        "id", "policy_uid", "milestone_name", "ideal_date", "projected_date",
        "completed_date", "prep_alert_date", "accountability", "waiting_on",
        "health", "acknowledged", "acknowledged_at", "created_at",
    }
    assert expected.issubset(cols)

def test_milestone_profile_column_on_policies(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute("PRAGMA table_info(policies)")
    cols = {r["name"] for r in cur.fetchall()}
    assert "milestone_profile" in cols

def test_policy_timeline_unique_constraint(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO policies (policy_uid, client_id) VALUES ('POL-001', 1)")
    conn.execute("""
        INSERT INTO policy_timeline (policy_uid, milestone_name, ideal_date, projected_date)
        VALUES ('POL-001', 'RSM Meeting', '2026-06-01', '2026-06-01')
    """)
    conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO policy_timeline (policy_uid, milestone_name, ideal_date, projected_date)
            VALUES ('POL-001', 'RSM Meeting', '2026-06-01', '2026-06-01')
        """)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_timeline_engine.py -v`
Expected: FAIL — table does not exist

- [ ] **Step 3: Create migration SQL file**

```sql
-- src/policydb/migrations/069_policy_timeline.sql
CREATE TABLE IF NOT EXISTS policy_timeline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    milestone_name  TEXT NOT NULL,
    ideal_date      DATE NOT NULL,
    projected_date  DATE NOT NULL,
    completed_date  DATE,
    prep_alert_date DATE,
    accountability  TEXT NOT NULL DEFAULT 'my_action',
    waiting_on      TEXT,
    health          TEXT NOT NULL DEFAULT 'on_track',
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    acknowledged_at DATETIME,
    created_at      DATETIME DEFAULT (datetime('now')),
    UNIQUE(policy_uid, milestone_name)
);

ALTER TABLE policies ADD COLUMN milestone_profile TEXT DEFAULT '';
```

- [ ] **Step 4: Wire migration into db.py**

In `src/policydb/db.py`:
1. Add `68` to `_KNOWN_MIGRATIONS` set (pre-requisite fix — currently missing)
2. Add `69` to `_KNOWN_MIGRATIONS` set
3. Add migration 069 execution block in `init_db()` following the existing pattern:
```python
if 69 not in applied:
    _run_sql(conn, "069_policy_timeline.sql")
    conn.execute("INSERT INTO schema_version (version) VALUES (69)")
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/migrations/069_policy_timeline.sql src/policydb/db.py tests/test_timeline_engine.py
git commit -m "feat: add policy_timeline table and milestone_profile column (migration 069)"
```

---

### Task 2: Config additions — mandated_activities expansion, profiles, timeline engine settings

**Files:**
- Modify: `src/policydb/config.py`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing tests for config defaults**

Append to `tests/test_timeline_engine.py`:

```python
import policydb.config as cfg

def test_mandated_activities_have_prep_days(tmp_db):
    """All mandated activities must have prep_days field."""
    cfg.reload_config()
    activities = cfg.get("mandated_activities")
    for act in activities:
        assert "prep_days" in act, f"{act['name']} missing prep_days"
        assert isinstance(act["prep_days"], int)

def test_dispositions_have_accountability(tmp_db):
    """All dispositions must map to an accountability state."""
    cfg.reload_config()
    dispositions = cfg.get("follow_up_dispositions")
    for d in dispositions:
        assert "accountability" in d, f"{d['label']} missing accountability"
        assert d["accountability"] in ("my_action", "waiting_external", "scheduled")

def test_milestone_profiles_exist(tmp_db):
    cfg.reload_config()
    profiles = cfg.get("milestone_profiles")
    assert len(profiles) >= 3
    names = [p["name"] for p in profiles]
    assert "Full Renewal" in names
    assert "Standard Renewal" in names
    assert "Simple Renewal" in names

def test_timeline_engine_config(tmp_db):
    cfg.reload_config()
    te = cfg.get("timeline_engine")
    assert te["minimum_gap_days"] == 3
    assert te["drift_threshold_days"] == 7
    assert te["compression_threshold"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timeline_engine.py::test_mandated_activities_have_prep_days -v`
Expected: FAIL — `prep_days` key missing

- [ ] **Step 3: Update config.py _DEFAULTS**

In `src/policydb/config.py`, update these sections of `_DEFAULTS`:

1. **`follow_up_dispositions`** — add `"accountability"` field to each entry. Map per spec Section 2 (e.g., Left VM → waiting_external, No Answer → my_action, etc.)

2. **`mandated_activities`** — expand from 2 entries to full list of 10 entries per spec Section 3. Each entry gets `prep_days`, `prep_notes` (optional), `checklist_milestone` (optional). Keep existing RSM Meeting and Post-Binding Meeting entries, add: Market Submissions, Quote Received, Coverage Comparison Prepared, Client Presentation, Client Approved, Binder Requested, Policy Received.

3. **Add new keys:**
```python
"milestone_profiles": [
    {
        "name": "Full Renewal",
        "description": "Large/complex accounts with full service cycle",
        "milestones": [
            "RSM Meeting", "Market Submissions", "Quote Received",
            "Coverage Comparison Prepared", "Client Presentation",
            "Client Approved", "Binder Requested", "Policy Received",
        ],
    },
    {
        "name": "Standard Renewal",
        "description": "Mid-size accounts, standard workflow",
        "milestones": [
            "Market Submissions", "Quote Received",
            "Client Approved", "Binder Requested", "Policy Received",
        ],
    },
    {
        "name": "Simple Renewal",
        "description": "Small accounts, minimal touchpoints",
        "milestones": [
            "Quote Received", "Client Approved", "Binder Requested",
        ],
    },
],
"milestone_profile_rules": [
    {"profile": "Full Renewal", "conditions": {"min_premium": 100000}},
    {"profile": "Standard Renewal", "conditions": {"min_premium": 25000}},
    {"profile": "Simple Renewal", "conditions": {"default": True}},
],
"timeline_engine": {
    "minimum_gap_days": 3,
    "drift_threshold_days": 7,
    "compression_threshold": 0.5,
},
"risk_alert_thresholds": {
    "at_risk_notify": True,
    "critical_notify": True,
    "critical_auto_draft": True,
},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/config.py tests/test_timeline_engine.py
git commit -m "feat: add timeline engine config — profiles, accountability, prep_days"
```

---

### Task 3: Timeline engine core — generation logic

**Files:**
- Create: `src/policydb/timeline_engine.py`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing tests for timeline generation**

Append to `tests/test_timeline_engine.py`:

```python
from datetime import date, timedelta
from policydb.timeline_engine import generate_policy_timelines, get_policy_timeline

def test_generate_timeline_standalone_policy(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, ?, ?, 0, 0, 'Simple Renewal')
    """, (eff_date, exp_date))
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.commit()

    generate_policy_timelines(conn)

    timeline = get_policy_timeline(conn, 'POL-001')
    milestone_names = [row["milestone_name"] for row in timeline]
    # Simple Renewal profile: Quote Received, Client Approved, Binder Requested
    assert "Quote Received" in milestone_names
    assert "Client Approved" in milestone_names
    assert "Binder Requested" in milestone_names
    # Should NOT have Full Renewal milestones
    assert "RSM Meeting" not in milestone_names

def test_generate_timeline_ideal_equals_projected_initially(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, ?, ?, 0, 0, 'Simple Renewal')
    """, (eff_date, exp_date))
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.commit()

    generate_policy_timelines(conn)

    timeline = get_policy_timeline(conn, 'POL-001')
    for row in timeline:
        assert row["ideal_date"] == row["projected_date"]

def test_skip_child_policies_in_program(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    # Program policy
    conn.execute("""
        INSERT INTO policies (id, policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, is_program, milestone_profile)
        VALUES (1, 'PGM-001', 1, ?, ?, 0, 0, 1, 'Full Renewal')
    """, (eff_date, exp_date))
    # Child policy
    conn.execute("""
        INSERT INTO policies (id, policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, program_id, milestone_profile)
        VALUES (2, 'POL-002', 1, ?, ?, 0, 0, 1, '')
    """, (eff_date, exp_date))
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.commit()

    generate_policy_timelines(conn)

    # Program should have timeline
    pgm_timeline = get_policy_timeline(conn, 'PGM-001')
    assert len(pgm_timeline) > 0
    # Child should NOT have timeline
    child_timeline = get_policy_timeline(conn, 'POL-002')
    assert len(child_timeline) == 0

def test_skip_opportunities(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('OPP-001', 1, ?, ?, 1, 0, 'Simple Renewal')
    """, (eff_date, exp_date))
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.commit()

    generate_policy_timelines(conn)

    timeline = get_policy_timeline(conn, 'OPP-001')
    assert len(timeline) == 0

def test_default_profile_when_empty(tmp_db):
    """Policies with no milestone_profile default to Simple Renewal."""
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, ?, ?, 0, 0, '')
    """, (eff_date, exp_date))
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.commit()

    generate_policy_timelines(conn)

    timeline = get_policy_timeline(conn, 'POL-001')
    names = [r["milestone_name"] for r in timeline]
    # Simple Renewal defaults
    assert "Quote Received" in names
    assert "RSM Meeting" not in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timeline_engine.py::test_generate_timeline_standalone_policy -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement timeline_engine.py — generation functions**

Create `src/policydb/timeline_engine.py` with:

1. `generate_policy_timelines(conn)` — main entry point called on startup
   - Query all active, non-opportunity, non-archived policies
   - Skip if `program_id IS NOT NULL`
   - For each eligible policy/program:
     - Determine profile (use `milestone_profile` column, default to "Simple Renewal" if empty)
     - Look up profile → milestone list from config
     - For each milestone in the profile:
       - Find matching `mandated_activities` entry by name
       - Calculate `ideal_date` from trigger type + days
       - Calculate `prep_alert_date` from `ideal_date - prep_days`
       - Skip if ideal_date is in the past
       - Skip if beyond `mandated_activity_horizon_days`
       - INSERT OR IGNORE into `policy_timeline`

2. `get_policy_timeline(conn, policy_uid)` — returns all timeline rows for a policy, ordered by ideal_date

3. `_resolve_profile(conn, policy_uid, cfg)` — determines which profile applies (reads column, falls back to auto-suggest, falls back to Simple Renewal)

4. `_calculate_milestone_date(policy, activity_config)` — computes ideal date from trigger type and days offset

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timeline_engine.py tests/test_timeline_engine.py
git commit -m "feat: timeline engine — generate policy timelines from profiles"
```

---

### Task 4: Timeline engine core — health computation

**Files:**
- Modify: `src/policydb/timeline_engine.py`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing tests for health computation**

Append to `tests/test_timeline_engine.py`:

```python
from policydb.timeline_engine import compute_health

def test_health_on_track():
    """>=7 days away, drift <=7 days → on_track"""
    result = compute_health(
        projected_date=date.today() + timedelta(days=14),
        ideal_date=date.today() + timedelta(days=16),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "on_track"

def test_health_completed_is_on_track():
    result = compute_health(
        projected_date=date.today() - timedelta(days=5),
        ideal_date=date.today() - timedelta(days=10),
        completed_date=date.today() - timedelta(days=3),
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "on_track"

def test_health_drifting():
    """Drift >7 days but still >=7 days away → drifting"""
    result = compute_health(
        projected_date=date.today() + timedelta(days=10),
        ideal_date=date.today() + timedelta(days=25),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "drifting"

def test_health_compressed():
    """Downstream spacing <50% of original → compressed"""
    result = compute_health(
        projected_date=date.today() + timedelta(days=14),
        ideal_date=date.today() + timedelta(days=16),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=12,  # <50% of 30
    )
    assert result == "compressed"

def test_health_at_risk_overdue():
    """Projected date in the past, not completed → at_risk"""
    result = compute_health(
        projected_date=date.today() - timedelta(days=3),
        ideal_date=date.today() - timedelta(days=3),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=60),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "at_risk"

def test_health_at_risk_imminent():
    """<7 days away, not completed → at_risk"""
    result = compute_health(
        projected_date=date.today() + timedelta(days=3),
        ideal_date=date.today() + timedelta(days=3),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=60),
        is_critical_milestone=False,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "at_risk"

def test_health_critical():
    """Expiration <=30 days + incomplete critical milestone → critical"""
    result = compute_health(
        projected_date=date.today() + timedelta(days=10),
        ideal_date=date.today() + timedelta(days=10),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=25),
        is_critical_milestone=True,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "critical"

def test_health_evaluation_order_critical_wins():
    """Critical evaluated before at_risk — critical takes precedence"""
    result = compute_health(
        projected_date=date.today() - timedelta(days=5),
        ideal_date=date.today() - timedelta(days=5),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=20),
        is_critical_milestone=True,
        original_spacing=30,
        current_spacing=28,
    )
    assert result == "critical"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timeline_engine.py::test_health_on_track -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement compute_health()**

Add to `src/policydb/timeline_engine.py`:

```python
def compute_health(
    projected_date: date,
    ideal_date: date,
    completed_date: date | None,
    expiration_date: date,
    is_critical_milestone: bool,
    original_spacing: int,
    current_spacing: int,
    drift_threshold: int = 7,
    compression_threshold: float = 0.5,
) -> str:
    """Compute health status. Evaluation order: critical → at_risk → compressed → drifting → on_track."""
    today = date.today()

    # Completed milestones are always on_track
    if completed_date is not None:
        return "on_track"

    days_to_expiry = (expiration_date - today).days
    days_away = (projected_date - today).days
    drift = (ideal_date - projected_date).days  # negative = slipped

    # Critical: expiration <=30d + incomplete critical milestone
    if is_critical_milestone and days_to_expiry <= 30:
        return "critical"

    # At risk: overdue or <7 days away
    if days_away < drift_threshold:
        return "at_risk"

    # Compressed: downstream spacing <50% of original
    if original_spacing > 0 and current_spacing < original_spacing * compression_threshold:
        return "compressed"

    # Drifting: slipped >7 days from ideal but still >=7 days away
    if abs(drift) > drift_threshold:
        return "drifting"

    return "on_track"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py -v -k "health"`
Expected: All 8 health tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timeline_engine.py tests/test_timeline_engine.py
git commit -m "feat: timeline engine — health computation with evaluation order"
```

---

### Task 5: Timeline engine core — recalculation logic

**Files:**
- Modify: `src/policydb/timeline_engine.py`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing tests for recalculation**

Append to `tests/test_timeline_engine.py`:

```python
from policydb.timeline_engine import recalculate_downstream

def _insert_test_policy_with_timeline(conn, policy_uid, exp_date, milestones):
    """Helper: insert a policy and manually populate its timeline rows."""
    eff_date = (date.today() - timedelta(days=200)).isoformat()
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES (?, 1, ?, ?, 0, 0, 'Full Renewal')
    """, (policy_uid, eff_date, exp_date.isoformat()))
    conn.execute("INSERT OR IGNORE INTO clients (id, name) VALUES (1, 'Acme Corp')")
    for m in milestones:
        conn.execute("""
            INSERT INTO policy_timeline
                (policy_uid, milestone_name, ideal_date, projected_date, prep_alert_date)
            VALUES (?, ?, ?, ?, ?)
        """, (policy_uid, m["name"], m["ideal"], m["projected"], m.get("prep_alert", m["projected"])))
    conn.commit()

def test_recalculate_shifts_downstream(tmp_db):
    conn = get_connection(tmp_db)
    exp = date.today() + timedelta(days=150)
    milestones = [
        {"name": "Quote Received", "ideal": "2026-05-01", "projected": "2026-05-01"},
        {"name": "Client Approved", "ideal": "2026-05-15", "projected": "2026-05-15"},
        {"name": "Binder Requested", "ideal": "2026-05-30", "projected": "2026-05-30"},
    ]
    _insert_test_policy_with_timeline(conn, "POL-001", exp, milestones)

    # Slip Quote Received by 7 days
    recalculate_downstream(conn, "POL-001", "Quote Received", "2026-05-08", exp.isoformat())

    timeline = get_policy_timeline(conn, "POL-001")
    by_name = {r["milestone_name"]: r for r in timeline}
    assert by_name["Quote Received"]["projected_date"] == "2026-05-08"
    # Downstream should shift but preserve minimum_gap
    assert by_name["Client Approved"]["projected_date"] > "2026-05-08"
    assert by_name["Binder Requested"]["projected_date"] > by_name["Client Approved"]["projected_date"]

def test_recalculate_respects_expiration_boundary(tmp_db):
    conn = get_connection(tmp_db)
    exp = date.today() + timedelta(days=30)  # Very close expiration
    milestones = [
        {"name": "Quote Received", "ideal": "2026-04-01", "projected": "2026-04-01"},
        {"name": "Binder Requested", "ideal": "2026-04-20", "projected": "2026-04-20"},
    ]
    _insert_test_policy_with_timeline(conn, "POL-001", exp, milestones)

    # Massive slip
    recalculate_downstream(conn, "POL-001", "Quote Received", exp.isoformat(), exp.isoformat())

    timeline = get_policy_timeline(conn, "POL-001")
    by_name = {r["milestone_name"]: r for r in timeline}
    # Binder should not exceed expiration - 1
    binder_projected = date.fromisoformat(by_name["Binder Requested"]["projected_date"])
    assert binder_projected <= exp

def test_recalculate_minimum_gap(tmp_db):
    conn = get_connection(tmp_db)
    exp = date.today() + timedelta(days=150)
    milestones = [
        {"name": "Quote Received", "ideal": "2026-05-01", "projected": "2026-05-01"},
        {"name": "Client Approved", "ideal": "2026-05-03", "projected": "2026-05-03"},  # Only 2 day gap
    ]
    _insert_test_policy_with_timeline(conn, "POL-001", exp, milestones)

    recalculate_downstream(conn, "POL-001", "Quote Received", "2026-05-05", exp.isoformat())

    timeline = get_policy_timeline(conn, "POL-001")
    by_name = {r["milestone_name"]: r for r in timeline}
    approved = date.fromisoformat(by_name["Client Approved"]["projected_date"])
    quote = date.fromisoformat(by_name["Quote Received"]["projected_date"])
    assert (approved - quote).days >= 3  # minimum_gap_days default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_timeline_engine.py::test_recalculate_shifts_downstream -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement recalculate_downstream()**

Add to `src/policydb/timeline_engine.py`:

```python
def recalculate_downstream(
    conn, policy_uid: str, changed_milestone: str,
    new_projected: str, expiration_date: str
) -> list[dict]:
    """Recalculate projected dates for all milestones after changed_milestone.

    Returns list of {milestone_name, old_projected, new_projected} for changed rows.
    """
    cfg = Config()
    minimum_gap = cfg.get("timeline_engine", {}).get("minimum_gap_days", 3)
    exp = date.fromisoformat(expiration_date)

    # Get all milestones in order
    rows = conn.execute("""
        SELECT id, milestone_name, ideal_date, projected_date
        FROM policy_timeline
        WHERE policy_uid = ?
        ORDER BY ideal_date
    """, (policy_uid,)).fetchall()

    # Find the changed milestone index
    changed_idx = None
    for i, r in enumerate(rows):
        if r["milestone_name"] == changed_milestone:
            changed_idx = i
            break
    if changed_idx is None:
        return []

    # Update the changed milestone itself
    conn.execute("""
        UPDATE policy_timeline SET projected_date = ? WHERE id = ?
    """, (new_projected, rows[changed_idx]["id"]))

    changes = []
    prev_projected = date.fromisoformat(new_projected)

    # Recalculate each downstream milestone
    for i in range(changed_idx + 1, len(rows)):
        row = rows[i]
        ideal_current = date.fromisoformat(row["ideal_date"])
        ideal_prev = date.fromisoformat(rows[i-1]["ideal_date"])
        original_gap = (ideal_current - ideal_prev).days

        new_gap = max(original_gap, minimum_gap)
        new_proj = prev_projected + timedelta(days=new_gap)

        # Clamp to expiration - 1
        if new_proj >= exp:
            new_proj = exp - timedelta(days=1)

        old_proj = row["projected_date"]
        new_proj_str = new_proj.isoformat()

        if new_proj_str != old_proj:
            changes.append({
                "milestone_name": row["milestone_name"],
                "old_projected": old_proj,
                "new_projected": new_proj_str,
            })

        conn.execute("""
            UPDATE policy_timeline SET projected_date = ? WHERE id = ?
        """, (new_proj_str, row["id"]))

        prev_projected = new_proj

    # Recompute prep_alert_date and health for all milestones
    _recompute_prep_and_health(conn, policy_uid, exp)
    conn.commit()
    return changes
```

Also add helper `_recompute_prep_and_health(conn, policy_uid, exp)` that:
- Reads all milestones for the policy
- For each, looks up `prep_days` from config
- Sets `prep_alert_date = projected_date - prep_days`
- Calls `compute_health()` and updates `health` column

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py -v -k "recalculate"`
Expected: All 3 recalculation tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timeline_engine.py tests/test_timeline_engine.py
git commit -m "feat: timeline engine — downstream recalculation with gap + expiry constraints"
```

---

### Task 6: Wire timeline generation into server startup

**Files:**
- Modify: `src/policydb/db.py`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing test for startup wiring**

```python
def test_init_db_calls_generate_timelines(tmp_db):
    """After init_db, policies with profiles should have timeline rows."""
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, ?, ?, 0, 0, 'Simple Renewal')
    """, (eff_date, exp_date))
    conn.commit()

    # Re-run init_db which should call generate_policy_timelines
    init_db(path=tmp_db)

    timeline = get_policy_timeline(conn, 'POL-001')
    assert len(timeline) > 0
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add generate_policy_timelines() call to init_db()**

In `src/policydb/db.py`, after the existing `generate_mandated_activities()` call:
```python
from policydb.timeline_engine import generate_policy_timelines
generate_policy_timelines(conn)
```

- [ ] **Step 4: Run all tests to verify nothing breaks**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/db.py tests/test_timeline_engine.py
git commit -m "feat: wire timeline generation into server startup"
```

---

### Task 6b: Update v_renewal_pipeline and v_overdue_followups views

**Files:**
- Modify: `src/policydb/views.py` — `V_RENEWAL_PIPELINE`, `V_OVERDUE_FOLLOWUPS`

- [ ] **Step 1: Update V_RENEWAL_PIPELINE to include timeline health**

LEFT JOIN `policy_timeline` to get the worst (most severe) health status per policy:
```sql
LEFT JOIN (
    SELECT policy_uid,
           MIN(CASE health
               WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
               WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4
               ELSE 5 END) as health_rank,
           -- ... derive worst health label from rank
    FROM policy_timeline WHERE completed_date IS NULL
    GROUP BY policy_uid
) th ON th.policy_uid = p.policy_uid
```

Add `timeline_health` column to the view output.

- [ ] **Step 2: Update V_OVERDUE_FOLLOWUPS to include accountability**

Add a computed `accountability` field derived from the `disposition` column, mapping disposition labels to accountability states via Python-side enrichment (since the mapping is config-driven, not SQL-storable).

Alternatively, add a simple LEFT JOIN to `policy_timeline` for the associated milestone's accountability.

- [ ] **Step 3: Run existing tests**

Run: `pytest tests/test_db.py -v`
Expected: All view tests PASS (views are rebuilt on startup)

- [ ] **Step 4: Commit**

```bash
git add src/policydb/views.py
git commit -m "feat: add timeline health to pipeline view, accountability to follow-ups view"
```

---

## Phase 2: Accountability

### Task 7: Accountability state on follow-up queries

**Files:**
- Modify: `src/policydb/queries.py` — `get_all_followups()`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_get_all_followups_includes_accountability(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.queries import get_all_followups
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    exp_date = (date.today() + timedelta(days=90)).isoformat()
    conn.execute("""
        INSERT INTO policies (id, policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived)
        VALUES (1, 'POL-001', 1, '2025-10-01', ?, 0, 0)
    """, (exp_date,))
    conn.execute("""
        INSERT INTO activity_log (id, client_id, policy_id, activity_type, subject,
                                   follow_up_date, follow_up_done, disposition)
        VALUES (1, 1, 1, 'Call', 'Follow up on quotes', ?, 0, 'Waiting on Carrier')
    """, ((date.today() + timedelta(days=3)).isoformat(),))
    conn.commit()

    overdue, upcoming = get_all_followups(conn)
    all_items = overdue + upcoming
    assert len(all_items) > 0
    item = all_items[0]
    assert item["accountability"] == "waiting_external"  # "Waiting on Carrier" maps to waiting_external
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Modify get_all_followups() to include accountability**

In `src/policydb/queries.py`, modify the activity source query in `get_all_followups()` to:
1. Look up the disposition's `accountability` from config
2. Add it as a computed column in the result
3. Default to `"my_action"` if no disposition set

This is a Python-side enrichment after the SQL query returns, since accountability is config-derived not stored in activity_log.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_timeline_engine.py::test_get_all_followups_includes_accountability -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/queries.py tests/test_timeline_engine.py
git commit -m "feat: add accountability state to follow-up query results"
```

---

### Task 8: Re-diary triggers timeline recalculation

**Files:**
- Modify: `src/policydb/web/routes/activities.py` — `activity_followup()`
- Modify: `src/policydb/timeline_engine.py` — add `update_timeline_from_followup()`

- [ ] **Step 1: Write failing test**

```python
def test_update_timeline_from_followup_waiting(tmp_db):
    from policydb.timeline_engine import update_timeline_from_followup
    conn = get_connection(tmp_db)
    exp = date.today() + timedelta(days=120)
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, '2025-10-01', ?, 0, 0, 'Simple Renewal')
    """, (exp.isoformat(),))
    conn.commit()
    generate_policy_timelines(conn)

    # Simulate re-diary with "Waiting on Carrier"
    new_followup_date = (date.today() + timedelta(days=10)).isoformat()
    update_timeline_from_followup(
        conn, policy_uid="POL-001",
        milestone_name="Quote Received",
        disposition="Waiting on Carrier",
        new_followup_date=new_followup_date,
        waiting_on="AmTrust",
    )

    timeline = get_policy_timeline(conn, "POL-001")
    by_name = {r["milestone_name"]: r for r in timeline}
    assert by_name["Quote Received"]["accountability"] == "waiting_external"
    assert by_name["Quote Received"]["waiting_on"] == "AmTrust"
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement update_timeline_from_followup()**

Add to `src/policydb/timeline_engine.py`:
- Looks up disposition → accountability from config
- Updates the milestone's `accountability`, `waiting_on`
- If `waiting_external`: extends `projected_date` to new_followup_date, calls `recalculate_downstream()`
- If `my_action`: resets `accountability`, no downstream shift

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Wire into activities.py**

In `activity_followup()` route handler, after creating the new follow-up activity:
```python
from policydb.timeline_engine import update_timeline_from_followup
# Only if the activity has a policy_id
if policy_uid and disposition:
    update_timeline_from_followup(conn, policy_uid, milestone_name, disposition, new_date, waiting_on)
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/timeline_engine.py src/policydb/web/routes/activities.py tests/test_timeline_engine.py
git commit -m "feat: re-diary with disposition triggers timeline recalculation"
```

---

### Task 9: Milestone completion syncs with timeline + checklist

**Files:**
- Modify: `src/policydb/timeline_engine.py` — add `complete_timeline_milestone()`
- Modify: `src/policydb/web/routes/policies.py` — milestone toggle endpoint

- [ ] **Step 1: Write failing test**

```python
def test_complete_milestone_syncs_checklist(tmp_db):
    from policydb.timeline_engine import complete_timeline_milestone
    conn = get_connection(tmp_db)
    exp = date.today() + timedelta(days=120)
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, milestone_profile)
        VALUES ('POL-001', 1, '2025-10-01', ?, 0, 0, 'Simple Renewal')
    """, (exp.isoformat(),))
    conn.commit()
    generate_policy_timelines(conn)

    # Add a checklist milestone
    conn.execute("""
        INSERT INTO policy_milestones (policy_uid, milestone, completed)
        VALUES ('POL-001', 'Quote Received', 0)
    """)
    conn.commit()

    complete_timeline_milestone(conn, "POL-001", "Quote Received")

    # Timeline should be marked completed
    timeline = get_policy_timeline(conn, "POL-001")
    by_name = {r["milestone_name"]: r for r in timeline}
    assert by_name["Quote Received"]["completed_date"] is not None

    # Checklist should also be marked done (via checklist_milestone mapping)
    checklist = conn.execute(
        "SELECT completed FROM policy_milestones WHERE policy_uid='POL-001' AND milestone='Quote Received'"
    ).fetchone()
    assert checklist["completed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement complete_timeline_milestone()**

- [ ] **Step 4: Wire into the milestone toggle endpoint in policies.py**

When the existing `POST /policies/{uid}/milestones/{milestone}` toggles a milestone on, also call `complete_timeline_milestone()` if there's a matching timeline entry (via `checklist_milestone` mapping).

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Commit**

```bash
git add src/policydb/timeline_engine.py src/policydb/web/routes/policies.py tests/test_timeline_engine.py
git commit -m "feat: milestone completion syncs between timeline and checklist"
```

---

## Phase 3: Review Panel Overhaul

### Task 10: Remove auto-review system

**Files:**
- Modify: `src/policydb/queries.py` — delete `check_auto_review_policy()`, `check_auto_review_client()`, `count_changed_fields()`
- Modify: `src/policydb/web/routes/review.py` — remove call sites at lines 289, 381
- Modify: `src/policydb/web/routes/policies.py` — remove call sites at lines 281, 362, 453, 592, 692, 1829, 2391
- Modify: `src/policydb/web/routes/activities.py` — remove call sites at lines 114, 115, 430, 431, 1088
- Modify: `src/policydb/web/routes/clients.py` — remove call site at line 2302
- Modify: `src/policydb/config.py` — remove `auto_review_enabled`, `auto_review_field_threshold`, `auto_review_activity_threshold` from `_DEFAULTS`

- [ ] **Step 1: Search for all auto-review references**

Run: `grep -rn "auto_review\|check_auto_review\|count_changed_fields" src/policydb/`
Document all locations to verify spec's list is complete. **Note:** Line numbers below are from the spec (current codebase state). Earlier tasks may have shifted line numbers — use the grep output as the authoritative source.

- [ ] **Step 2: Remove the three functions from queries.py**

Delete `count_changed_fields()`, `check_auto_review_policy()`, `check_auto_review_client()`.

- [ ] **Step 3: Remove all call sites from route modules**

Remove the import lines and all calls to these functions in review.py, policies.py, activities.py, clients.py. Do NOT remove the surrounding logic (saves, field updates) — only the auto-review trigger calls.

- [ ] **Step 4: Remove config keys from _DEFAULTS**

Remove `auto_review_enabled`, `auto_review_field_threshold`, `auto_review_activity_threshold` from `_DEFAULTS` in config.py.

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS (no test should depend on auto-review)

- [ ] **Step 6: Commit**

```bash
git add src/policydb/queries.py src/policydb/web/routes/review.py \
    src/policydb/web/routes/policies.py src/policydb/web/routes/activities.py \
    src/policydb/web/routes/clients.py src/policydb/config.py
git commit -m "refactor: remove auto-review system — deliberate weekly review replaces it"
```

---

### Task 11: Program-level review scoping

**Files:**
- Modify: `src/policydb/views.py` — update `v_review_queue`
- Modify: `src/policydb/web/routes/review.py` — cascade reviewed to child policies
- Modify: `src/policydb/web/templates/review/index.html`
- Test: `tests/test_timeline_engine.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_review_queue_excludes_child_policies(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.views import rebuild_views
    exp = (date.today() + timedelta(days=90)).isoformat()
    eff = (date.today() - timedelta(days=275)).isoformat()
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme')")
    # Program
    conn.execute("""
        INSERT INTO policies (id, policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, is_program, review_cycle)
        VALUES (1, 'PGM-001', 1, ?, ?, 0, 0, 1, '1w')
    """, (eff, exp))
    # Child
    conn.execute("""
        INSERT INTO policies (id, policy_uid, client_id, effective_date, expiration_date,
                              is_opportunity, archived, program_id, review_cycle)
        VALUES (2, 'POL-002', 1, ?, ?, 0, 0, 1, '1w')
    """, (eff, exp))
    conn.commit()
    rebuild_views(conn)

    queue = conn.execute("SELECT policy_uid FROM v_review_queue").fetchall()
    uids = [r["policy_uid"] for r in queue]
    assert "PGM-001" in uids
    assert "POL-002" not in uids  # Child excluded
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Update v_review_queue to filter on program_id**

Add `AND (p.program_id IS NULL)` to the WHERE clause. This excludes child policies. Program policies (`is_program = 1`) pass through since they have `program_id IS NULL`.

- [ ] **Step 4: Update mark_reviewed in review.py to cascade to children**

When marking a program reviewed, also update all child policies:
```python
if is_program:
    conn.execute(
        "UPDATE policies SET last_reviewed_at = ? WHERE program_id = ?",
        (now, program_policy_id)
    )
```

- [ ] **Step 5: Run tests to verify they pass**

- [ ] **Step 6: Commit**

```bash
git add src/policydb/views.py src/policydb/web/routes/review.py tests/test_timeline_engine.py
git commit -m "feat: program-level review scoping — child policies excluded from queue"
```

---

### Task 12: Milestone profile dropdown + health badge in review row

**Files:**
- Modify: `src/policydb/web/routes/review.py` — add profile change endpoint, pass timeline context
- Modify: `src/policydb/web/templates/review/_policy_row.html` — add profile dropdown + health badge
- Create: `src/policydb/web/templates/review/_profile_select.html` — combobox partial

- [ ] **Step 1: Add profile change endpoint**

Add `POST /review/policies/{uid}/profile` endpoint in review.py:
- Accepts `milestone_profile` form field
- Updates `policies.milestone_profile`
- Regenerates timeline for the policy via `generate_policy_timelines(conn, policy_uid=uid)`
- Returns updated review row

- [ ] **Step 2: Pass timeline health to review row template context**

In the review page context builder, join `policy_timeline` to get the worst health status per policy and the next active milestone name.

- [ ] **Step 3: Update _policy_row.html template**

Add:
- Profile dropdown (combobox) with HTMX POST to `/review/policies/{uid}/profile`
- Health badge (color-coded dot + label) from timeline data
- Next milestone label

- [ ] **Step 4: Add follow-up prompt after marking reviewed**

In review.py's mark-reviewed endpoint, after setting `last_reviewed_at`:
- Check if the policy has an active follow-up (`follow_up_date IS NOT NULL AND follow_up_date >= today` in activity_log or policies)
- If no active follow-up, return an inline follow-up prompt with a date picker in the HTMX response (appended via `hx-swap-oob`)
- When user sets a date, POST to existing `/activities/log` with the policy context

- [ ] **Step 5: Add "Plan this week" button in review page**

When unscheduled follow-ups exist for reviewed policies, show a link to `/followups/plan?week_start={monday}` at the top of the review page. Use HTMX to check after each review mark.

- [ ] **Step 6: QA — navigate to /review, verify dropdown, health badge, follow-up prompt, and plan link**

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/review.py \
    src/policydb/web/templates/review/_policy_row.html \
    src/policydb/web/templates/review/_profile_select.html
git commit -m "feat: milestone profile dropdown, health badge, follow-up prompt in review rows"
```

---

## Phase 4: Action Center Overhaul

### Task 13: Restructure follow-ups tab into 5 sections

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — rewrite `_followups_ctx()`
- Create: `src/policydb/web/templates/action_center/_followup_sections.html`
- Modify: `src/policydb/web/templates/action_center/_followups.html`

- [ ] **Step 1: Create _followups_ctx() that produces 5 buckets**

Rewrite `_followups_ctx()` in action_center.py to:
1. Call `get_all_followups()` (which now includes accountability)
2. Query `policy_timeline` for prep alerts where `prep_alert_date <= today AND completed_date IS NULL`
3. Bucket results into: `act_now`, `nudge_due`, `prep_coming`, `watching`, `scheduled`
4. Logic:
   - `act_now`: accountability == "my_action" AND (overdue OR due today)
   - `nudge_due`: accountability == "waiting_external" AND follow_up_date <= today
   - `prep_coming`: prep alerts from timeline (no existing follow-up, prep_alert_date <= today)
   - `watching`: accountability == "waiting_external" AND follow_up_date > today
   - `scheduled`: accountability == "scheduled"

- [ ] **Step 2: Create _followup_sections.html template**

Template with 5 collapsible sections (Act Now, Nudge Due, Prep Coming Up expanded; Watching, Scheduled collapsed). Each section header shows count badge. Items use accountability-aware styling per spec Section 2.

- [ ] **Step 3: Add nudge escalation counting**

In the `nudge_due` bucket logic, compute nudge count from `thread_id` re-diary history:
```python
# For each waiting_external item in nudge_due:
# Count activities in same thread_id to get nudge_count
# nudge_count 1 = normal, 2 = amber, 3+ = red + "consider escalating" label
```
Add `nudge_count` and `escalation_tier` (normal/elevated/urgent) to each nudge_due item.

- [ ] **Step 4: Update _followups.html to use new sections template**

Replace existing overdue/upcoming layout with the new sections template. Apply escalation tier styling to nudge items (normal = default, elevated = amber border, urgent = red border + escalation label).

- [ ] **Step 5: QA — navigate to Action Center, verify 5 sections render correctly**

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py \
    src/policydb/web/templates/action_center/_followup_sections.html \
    src/policydb/web/templates/action_center/_followups.html
git commit -m "feat: restructure Action Center follow-ups into 5 accountability sections"
```

---

### Task 14: Portfolio health sidebar widget

**Files:**
- Create: `src/policydb/web/templates/action_center/_portfolio_health.html`
- Modify: `src/policydb/web/routes/action_center.py` — add `_portfolio_health_ctx()`
- Modify: `src/policydb/web/templates/action_center/_sidebar.html`

- [ ] **Step 1: Add _portfolio_health_ctx() function**

Query `policy_timeline` grouped by health status:
```sql
SELECT health, COUNT(DISTINCT policy_uid) as count
FROM policy_timeline
WHERE completed_date IS NULL
GROUP BY health
```

- [ ] **Step 2: Create _portfolio_health.html partial**

Color-coded health breakdown with clickable rows that link to filtered pipeline view.

- [ ] **Step 3: Update sidebar to include portfolio health widget**

Add `{% include "action_center/_portfolio_health.html" %}` to sidebar template.

- [ ] **Step 4: Update sidebar badge counts**

Change from "X overdue" to "X actions · Y nudges" format. Count `act_now` + `nudge_due` items.

- [ ] **Step 5: QA — verify sidebar renders with health widget and new badge format**

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/action_center/_portfolio_health.html \
    src/policydb/web/routes/action_center.py \
    src/policydb/web/templates/action_center/_sidebar.html
git commit -m "feat: portfolio health dashboard in Action Center sidebar"
```

---

### Task 15: Risk alerts banner

**Files:**
- Create: `src/policydb/web/templates/action_center/_risk_alerts.html`
- Modify: `src/policydb/web/routes/action_center.py` — add `_risk_alerts_ctx()`, acknowledge endpoint
- Modify: `src/policydb/web/templates/action_center/page.html`

- [ ] **Step 1: Add _risk_alerts_ctx() function**

Query policies with `at_risk` or `critical` health:
```sql
SELECT DISTINCT pt.policy_uid, pt.health, pt.waiting_on,
       p.policy_type, p.expiration_date, c.name as client_name
FROM policy_timeline pt
JOIN policies p ON p.policy_uid = pt.policy_uid
JOIN clients c ON c.id = p.client_id
WHERE pt.health IN ('at_risk', 'critical')
  AND pt.completed_date IS NULL
ORDER BY pt.health DESC, p.expiration_date
```

- [ ] **Step 2: Add acknowledge endpoint**

`POST /action-center/acknowledge/{policy_uid}` — sets `acknowledged=1, acknowledged_at=now` on all timeline rows for the policy.

- [ ] **Step 3: Create _risk_alerts.html template**

Alert cards with policy info, drift amount, blocking reason, three buttons: Draft Notification, Acknowledge, View Timeline.

- [ ] **Step 4: Include risk alerts banner at top of Action Center page**

Add to page.html above the tab content area.

- [ ] **Step 5: Wire Draft Notification button to compose panel**

Add `POST /action-center/draft-notification/{policy_uid}` endpoint:
- Calls `timeline_context(conn, policy_uid)` from email_templates.py (created in Task 17)
- For now, stub `timeline_context()` to return basic policy data — Task 17 will complete it
- Returns the compose panel partial pre-filled with timeline tokens and a suggested risk notification template
- "Draft Notification" button in the risk alert card uses `hx-post` to this endpoint

- [ ] **Step 6: QA — verify risk alerts banner appears for at_risk/critical policies**

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/templates/action_center/_risk_alerts.html \
    src/policydb/web/routes/action_center.py \
    src/policydb/web/templates/action_center/page.html
git commit -m "feat: risk alerts banner with draft notification support in Action Center"
```

---

## Phase 5: Plan Week Enrichment (depends on Phases 1-4 including Phase 3)

### Task 16: Timeline context in Plan Week items

**Files:**
- Modify: `src/policydb/queries.py` — `get_week_followups()` to include timeline data
- Modify: `src/policydb/web/templates/followups/plan.html`

- [ ] **Step 1: Modify get_week_followups() to LEFT JOIN policy_timeline**

Add health badge, accountability, milestone_name to each follow-up item. Use LEFT JOIN so items without timeline data still appear.

- [ ] **Step 2: Update plan.html grid item template**

Add to each follow-up card:
- Health badge (colored dot)
- Accountability icon (action/nudge/scheduled)
- Milestone label (small text below subject)
- Prep flag styling for prep-generated items

- [ ] **Step 3: Enhance pinning logic**

In `get_week_followups()`, also pin items where the policy's worst health is `critical` or `at_risk`.

- [ ] **Step 4: Add "Due for review" badge**

LEFT JOIN `v_review_queue` to check if the policy is due for review. Show small badge on card if so.

- [ ] **Step 5: QA — navigate to Plan Week, verify enriched items render**

- [ ] **Step 6: Commit**

```bash
git add src/policydb/queries.py src/policydb/web/templates/followups/plan.html
git commit -m "feat: timeline context + review badges in Plan Week grid items"
```

---

## Phase 6: Templates & Polish

### Task 17: Timeline tokens in email template system

**Files:**
- Modify: `src/policydb/email_templates.py` — add `timeline_context()` + tokens to `CONTEXT_TOKEN_GROUPS`

- [ ] **Step 1: Add timeline_context() function**

Build token dict from `policy_timeline` data for a given policy_uid: `drift_days`, `blocking_reason`, `milestones_complete`, `milestones_remaining`, `current_status`, `days_to_expiry`, `contact_first_name`, `nudge_count`.

- [ ] **Step 2: Add timeline tokens to CONTEXT_TOKEN_GROUPS**

Add a `"timeline"` context group with all new tokens.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: timeline tokens for email template system"
```

---

### Task 18: Seed nudge templates

**Files:**
- Modify: `src/policydb/db.py` or `src/policydb/seed.py` — seed default nudge templates

- [ ] **Step 1: Create nudge template seeding function**

4 templates per spec Section 5:
1. Waiting on Client — Document/Signature
2. Waiting on Client — Decision/Approval
3. Waiting on Carrier — Status Check
4. Scheduled Meeting — Confirmation

Each with appropriate subject line using `{{tokens}}` and professional body text.

- [ ] **Step 2: Wire seeding into init_db() or first-run check**

Only seed if no nudge templates exist yet (check `email_templates` table for `category='nudge'`).

- [ ] **Step 3: Commit**

```bash
git add src/policydb/db.py src/policydb/email_templates.py
git commit -m "feat: seed nudge email templates for follow-up workflows"
```

---

### Task 19: Timeline visualization on policy/program pages

**Files:**
- Create: `src/policydb/web/templates/policies/_timeline.html` — full timeline view
- Create: `src/policydb/web/templates/policies/_timeline_banner.html` — compact banner for child policies
- Modify: `src/policydb/web/routes/policies.py` — timeline endpoint
- Modify: `src/policydb/web/templates/policies/_tab_details.html`

- [ ] **Step 1: Add timeline endpoint**

`GET /policies/{uid}/timeline` — returns timeline partial. Queries `policy_timeline` for the policy, joins config for milestone metadata.

- [ ] **Step 2: Create _timeline.html template**

Vertical timeline visualization per spec Section 4:
- Each milestone row: status icon (✓/●/○), name, projected date, ideal date, drift, health badge
- Completed milestones show actual date vs ideal with drift
- Current milestone highlighted with accountability state and waiting_on context

- [ ] **Step 3: Create _timeline_banner.html**

Compact banner for child policies in a program:
- "Timeline managed by: {Program Name}"
- Current health badge + next active milestone
- Link to program timeline view

- [ ] **Step 4: Add timeline view to policy details tab**

Include timeline partial on the policy edit page details tab. Show banner for child policies, full timeline for standalone/program policies.

- [ ] **Step 5: QA — navigate to policy pages, verify timeline renders**

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/policies/_timeline.html \
    src/policydb/web/templates/policies/_timeline_banner.html \
    src/policydb/web/routes/policies.py \
    src/policydb/web/templates/policies/_tab_details.html
git commit -m "feat: timeline visualization on policy and program pages"
```

---

### Task 20a: Settings UI — mandated activities editor

**Files:**
- Create: `src/policydb/web/templates/settings/_mandated_activities_editor.html`
- Modify: `src/policydb/web/routes/settings.py`

- [ ] **Step 1: Create mandated activities editor template**

Table editor with columns: name, trigger, days, prep_days, prep_notes, checklist_milestone, activity_type. Each row editable on blur. Uses contenteditable + combobox pattern per CLAUDE.md standards.

- [ ] **Step 2: Add PATCH endpoint for mandated activities**

`PATCH /settings/mandated-activities/{index}` — updates a single activity entry in config and saves.

- [ ] **Step 3: Include editor in settings page**

- [ ] **Step 4: QA — verify editing and saving works**

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/settings/_mandated_activities_editor.html src/policydb/web/routes/settings.py
git commit -m "feat: Settings UI — mandated activities editor"
```

---

### Task 20b: Settings UI — milestone profiles editor

**Files:**
- Create: `src/policydb/web/templates/settings/_milestone_profiles_editor.html`
- Modify: `src/policydb/web/routes/settings.py`

- [ ] **Step 1: Create milestone profiles editor template**

Card per profile: name, description, drag-to-reorder milestone list. Milestones selectable from available `mandated_activities` names. Add/remove milestones with + and × buttons.

- [ ] **Step 2: Add PATCH endpoint for milestone profiles**

`PATCH /settings/milestone-profiles/{index}` — updates a single profile and saves.

- [ ] **Step 3: Include editor in settings page**

- [ ] **Step 4: QA — verify editing, reordering, and saving works**

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/settings/_milestone_profiles_editor.html src/policydb/web/routes/settings.py
git commit -m "feat: Settings UI — milestone profiles editor"
```

---

### Task 20c: Settings UI — timeline engine thresholds

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html` or settings index

- [ ] **Step 1: Add threshold editor section**

Simple key-value card for `minimum_gap_days`, `drift_threshold_days`, `compression_threshold` and `risk_alert_thresholds` (at_risk_notify, critical_notify, critical_auto_draft). Number inputs + toggle switches.

- [ ] **Step 2: Add PATCH endpoint for threshold config**

`PATCH /settings/timeline-engine` — updates `timeline_engine` and `risk_alert_thresholds` in config.

- [ ] **Step 3: QA — verify threshold editing and saving**

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat: Settings UI — timeline engine thresholds and risk alert config"
```

---

### Task 20d: Settings UI — disposition accountability editor

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify existing disposition editor template

- [ ] **Step 1: Extend disposition editor with accountability dropdown**

Add a combobox per disposition row with options: `my_action`, `waiting_external`, `scheduled`. PATCH saves on change.

- [ ] **Step 2: Wire PATCH to include accountability field**

Update existing disposition save endpoint to also persist the `accountability` field.

- [ ] **Step 3: QA — verify accountability dropdown renders and saves**

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/settings.py
git commit -m "feat: Settings UI — disposition accountability mapping editor"
```

---

### Task 21: Update nav badge format

**Files:**
- Modify: `src/policydb/web/templates/base.html`
- Modify: `src/policydb/web/routes/dashboard.py` or `app.py` (wherever badge count is computed)

- [ ] **Step 1: Change badge computation**

Replace overdue count with: `act_now_count` (my_action items overdue) + `nudge_count` (waiting_external items due for nudge). Format as "X actions · Y nudges".

- [ ] **Step 2: Update base.html badge rendering**

Use the new format in the nav bar badge.

- [ ] **Step 3: QA — verify badge displays correctly across pages**

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/base.html src/policydb/web/routes/dashboard.py
git commit -m "feat: nav badge shows 'X actions · Y nudges' instead of overdue count"
```

---

### Task 22: Final integration test + QA pass

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Start server and QA all affected pages**

```bash
policydb serve
```

Test checklist:
- [ ] Action Center: 5-section follow-ups render
- [ ] Action Center: portfolio health sidebar
- [ ] Action Center: risk alerts banner (create test data with at_risk policy)
- [ ] Review page: profile dropdown, health badge, no auto-review
- [ ] Review page: program-level scoping (child policies excluded)
- [ ] Plan Week: health badges, accountability icons, enhanced pinning
- [ ] Policy page: timeline visualization
- [ ] Policy page: child policy banner
- [ ] Settings: mandated activities editor
- [ ] Settings: milestone profiles editor
- [ ] Settings: disposition accountability editor
- [ ] Nav badge: "X actions · Y nudges" format
- [ ] Email templates: nudge templates available

- [ ] **Step 3: Fix any issues found in QA**

- [ ] **Step 4: Final commit**

```bash
git commit -m "fix: QA fixes for proactive timeline engine"
```
