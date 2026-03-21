# Reconcile System Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the reconciler's gate-based fuzzy matching with additive scoring, replace the results table with a side-by-side pairing board, add data prep validation, and build a location assignment tool.

**Architecture:** Five phases — normalization foundation, algorithm rewrite, pairing board UI, data prep layer, location assignment tool. Each phase builds on the previous. The reconciler module (`src/policydb/reconciler.py`) is rewritten with a single `_score_pair()` function replacing `_fuzzy_match()` and `find_candidates()`'s separate scoring. The UI shifts from a results-table dump to a side-by-side pairing board using the same HTMX row-swap pattern used throughout PolicyDB.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, HTMX, SQLite, Tailwind CSS (CDN), RapidFuzz, openpyxl

**Spec:** `docs/superpowers/specs/2026-03-20-reconcile-redesign.md`

---

## Phase 1: Normalization Foundation

### Task 1: Add matching-specific normalize functions to utils.py

**Files:**
- Modify: `src/policydb/utils.py`
- Create: `tests/test_normalize_matching.py`

**Context:** utils.py already has `normalize_client_name()` (line 443) which preserves legal suffixes, and `normalize_policy_number()` (line 427) which preserves formatting. The reconciler needs matching-specific variants that strip suffixes and formatting for comparison only. The reconciler currently has its own `_normalize_client_name()` (reconciler.py:49) and `_normalize_policy_number()` (reconciler.py:35) — these move to utils.py under new names.

- [ ] **Step 1: Write tests for `normalize_client_name_for_matching()`**

```python
# tests/test_normalize_matching.py
from policydb.utils import normalize_client_name_for_matching

def test_strips_legal_suffixes():
    assert normalize_client_name_for_matching("Acme Corp.") == "Acme"
    assert normalize_client_name_for_matching("Acme Holdings LLC") == "Acme Holdings"
    assert normalize_client_name_for_matching("Delta Services, Inc.") == "Delta Services"

def test_title_cases():
    assert normalize_client_name_for_matching("AVALONBAY COMMUNITIES") == "Avalonbay Communities"

def test_preserves_short_acronyms():
    assert normalize_client_name_for_matching("US Steel Inc.") == "US Steel"
    assert normalize_client_name_for_matching("ABC Corp") == "ABC"

def test_collapses_whitespace():
    assert normalize_client_name_for_matching("  Delta   Services   LLC  ") == "Delta Services"

def test_empty_and_none():
    assert normalize_client_name_for_matching("") == ""
    assert normalize_client_name_for_matching(None) == ""
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `pytest tests/test_normalize_matching.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_client_name_for_matching'`

- [ ] **Step 3: Implement `normalize_client_name_for_matching()` in utils.py**

Add after the existing `normalize_client_name()` function (after line ~490). Port logic from `reconciler.py:49-55` (the `_normalize_client_name` function that strips suffixes entirely):

```python
_MATCHING_SUFFIX_RE = re.compile(
    r'\s*(,?\s*)?(LLC|LLP|LP|PLLC|Inc\.?|Corp\.?|Corporation|Co\.?|'
    r'Ltd\.?|Limited|Company|Enterprises?)\b[.,]?$',
    re.IGNORECASE,
)

def normalize_client_name_for_matching(raw: str | None) -> str:
    """Strip legal suffixes entirely, collapse whitespace, title case.

    For fuzzy matching comparison only — never write result to DB.
    "Acme Corp." → "Acme", "AVALONBAY COMMUNITIES INC" → "Avalonbay Communities"
    """
    if not raw or not str(raw).strip():
        return ""
    name = _MATCHING_SUFFIX_RE.sub("", str(raw)).strip()
    name = " ".join(name.split())  # collapse whitespace
    words = name.split()
    return " ".join(w if (len(w) <= 3 and w.isupper()) else w.capitalize() for w in words)
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `pytest tests/test_normalize_matching.py -v`
Expected: PASS

- [ ] **Step 5: Write tests for `normalize_policy_number_for_matching()`**

Add to `tests/test_normalize_matching.py`:

```python
from policydb.utils import normalize_policy_number_for_matching

def test_strips_formatting():
    assert normalize_policy_number_for_matching("POL-GL-2025-441") == "POLGL2025441"
    assert normalize_policy_number_for_matching("WC 99.812") == "WC99812"

def test_strips_leading_zeros():
    assert normalize_policy_number_for_matching("00123") == "123"

def test_filters_placeholders():
    assert normalize_policy_number_for_matching("TBD") == ""
    assert normalize_policy_number_for_matching("N/A") == ""
    assert normalize_policy_number_for_matching("999") == ""
    assert normalize_policy_number_for_matching("PENDING") == ""

def test_empty():
    assert normalize_policy_number_for_matching("") == ""
    assert normalize_policy_number_for_matching(None) == ""
```

- [ ] **Step 6: Implement `normalize_policy_number_for_matching()` in utils.py**

Port logic from `reconciler.py:35-46`:

```python
_PLACEHOLDER_POLICY_NUMBERS = {
    "999", "TBD", "TBA", "PENDING", "NA", "N/A", "NONE", "XXX", "000", "123",
    "NEW", "RENEWAL", "RENEW", "QUOTE", "QUOTED", "APPLIED",
}

def normalize_policy_number_for_matching(raw: str | None) -> str:
    """Strip formatting and placeholders for fuzzy matching comparison only.

    Never write result to DB — use normalize_policy_number() for that.
    "POL-GL-2025-441" → "POLGL2025441"
    """
    if not raw or not str(raw).strip():
        return ""
    cleaned = re.sub(r'[\s\-/.]', '', str(raw)).upper()
    cleaned = cleaned.lstrip('0') or ''
    if cleaned in _PLACEHOLDER_POLICY_NUMBERS:
        return ""
    return cleaned
```

- [ ] **Step 7: Run all tests — verify they pass**

Run: `pytest tests/test_normalize_matching.py -v`
Expected: all PASS

- [ ] **Step 8: Promote `_parse_currency` to `parse_currency` in utils.py**

Add to utils.py (port from `importer.py:21-29` with identical behavior):

```python
def parse_currency(value) -> float:
    """Strip currency symbols, commas; return float. Returns 0.0 on error.

    Promoted from importer._parse_currency — shared by reconciler and importer.
    """
    if not value or not str(value).strip():
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0
```

- [ ] **Step 9: Commit**

```bash
git add src/policydb/utils.py tests/test_normalize_matching.py
git commit -m "feat: add matching-specific normalize functions and parse_currency to utils"
```

---

### Task 2: Add `rebuild_coverage_aliases()` for auto-learn persistence

**Files:**
- Modify: `src/policydb/utils.py`
- Modify: `tests/test_normalize_matching.py`

**Context:** `_COVERAGE_ALIASES` is hardcoded at utils.py:97-334. `carrier_aliases` already has a config-merge pattern via `rebuild_carrier_aliases()` (utils.py:50-60). Mirror this pattern for coverage.

- [ ] **Step 1: Write test for `rebuild_coverage_aliases()`**

```python
from policydb.utils import rebuild_coverage_aliases, normalize_coverage_type

def test_rebuild_coverage_aliases_merges_config(monkeypatch):
    """Config coverage_aliases should merge with hardcoded aliases."""
    import policydb.config as cfg
    monkeypatch.setattr(cfg, 'get', lambda key, default=None:
        {"Custom Coverage": ["custom cov", "cc"]} if key == "coverage_aliases" else default)
    rebuild_coverage_aliases()
    assert normalize_coverage_type("custom cov") == "Custom Coverage"
    assert normalize_coverage_type("cc") == "Custom Coverage"
    # Hardcoded aliases still work
    assert normalize_coverage_type("gl") == "General Liability"
```

- [ ] **Step 2: Run test — verify it fails**

Run: `pytest tests/test_normalize_matching.py::test_rebuild_coverage_aliases_merges_config -v`
Expected: FAIL — `ImportError: cannot import name 'rebuild_coverage_aliases'`

- [ ] **Step 3: Implement `rebuild_coverage_aliases()`**

Add near `rebuild_carrier_aliases()` in utils.py:

```python
_BASE_COVERAGE_ALIASES: dict[str, str] = {}  # populated on first call

def rebuild_coverage_aliases() -> None:
    """Merge config coverage_aliases with hardcoded _COVERAGE_ALIASES."""
    global _COVERAGE_ALIASES, _BASE_COVERAGE_ALIASES
    if not _BASE_COVERAGE_ALIASES:
        _BASE_COVERAGE_ALIASES = dict(_COVERAGE_ALIASES)
    from policydb import config as cfg
    config_aliases = cfg.get("coverage_aliases", {})
    merged = dict(_BASE_COVERAGE_ALIASES)
    for canonical, variations in config_aliases.items():
        merged[canonical.lower()] = canonical
        for v in variations:
            merged[v.strip().lower()] = canonical
    _COVERAGE_ALIASES = merged
```

- [ ] **Step 4: Call on module load — add after `rebuild_carrier_aliases()` call**

Find the existing `rebuild_carrier_aliases()` call (utils.py:87-90 area) and add `rebuild_coverage_aliases()` after it.

- [ ] **Step 5: Run tests — verify pass**

Run: `pytest tests/test_normalize_matching.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/utils.py tests/test_normalize_matching.py
git commit -m "feat: add rebuild_coverage_aliases for auto-learn alias persistence"
```

---

## Phase 2: Algorithm Rewrite

### Task 3: Implement `_score_pair()` and updated `ReconcileRow`

**Files:**
- Modify: `src/policydb/reconciler.py`
- Create: `tests/test_score_pair.py`

**Context:** This is the core algorithm change. Replace `_fuzzy_match()` (reconciler.py:352-502) with a single `_score_pair()` function that returns per-field additive scores. Update `ReconcileRow` dataclass to carry score breakdowns. See spec Section 1.1 for scoring table.

- [ ] **Step 1: Write tests for `_score_pair()`**

```python
# tests/test_score_pair.py
from policydb.reconciler import _score_pair

def test_exact_policy_number_scores_40():
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "GL-2025-441", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 12500}
    db = {"client_name": "Acme Construction Inc.", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-2025-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 12500, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_policy_number == 40
    assert result.total >= 90  # pol# 40 + dates 30 + type ~12 + carrier 10 + name ~4

def test_no_hard_gates():
    """Even with client name completely different, pol# + dates should score."""
    ext = {"client_name": "Totally Different Name", "policy_type": "GL",
           "carrier": "Hartford", "policy_number": "GL-2025-441",
           "effective_date": "2025-04-01", "expiration_date": "2026-04-01", "premium": 0}
    db = {"client_name": "Acme Construction", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-2025-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.total >= 70  # pol# 40 + dates 30 + carrier 10 = 80 min

def test_same_type_different_label_scores_partial():
    ext = {"client_name": "Acme", "policy_type": "Workers Compensation",
           "carrier": "", "policy_number": "", "effective_date": "", "expiration_date": "", "premium": 0}
    db = {"client_name": "Acme", "policy_type": "Workers Comp",
          "carrier": "", "policy_number": "", "effective_date": "", "expiration_date": "",
          "premium": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_type >= 8  # fuzzy >= 70

def test_date_scoring_exact():
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "2025-04-01", "expiration_date": "2026-04-01", "premium": 0}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_dates == 30

def test_missing_policy_number_neutral():
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 0}
    db = {"client_name": "Acme", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-123",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_policy_number == 0  # neutral, not penalized

def test_confidence_tiers():
    from policydb.reconciler import _score_pair
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "GL-441", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 12500}
    db = {"client_name": "Acme", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 12500, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.confidence == "high"  # score >= 75

def test_single_client_mode_maxes_name():
    ext = {"client_name": "Wrong Name", "policy_type": "GL", "carrier": "",
           "policy_number": "", "effective_date": "", "expiration_date": "", "premium": 0}
    db = {"client_name": "Acme", "policy_type": "General Liability", "carrier": "",
          "policy_number": "", "effective_date": "", "expiration_date": "",
          "premium": 0, "first_named_insured": ""}
    result = _score_pair(ext, db, single_client=True)
    assert result.score_name == 5
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `pytest tests/test_score_pair.py -v`
Expected: FAIL — `ImportError: cannot import name '_score_pair'`

- [ ] **Step 3: Update `ReconcileRow` dataclass**

Replace the existing `ReconcileRow` at reconciler.py:81-99 with:

```python
@dataclass
class ReconcileRow:
    """One row in reconciliation results."""
    ext: dict | None            # uploaded record; None for EXTRA
    db: dict | None             # PolicyDB record; None for UNMATCHED
    status: str = "PAIRED"      # "PAIRED" | "UNMATCHED" | "EXTRA"
    match_score: float = 0.0    # 0–100 total
    confidence: str = "none"    # "high" | "medium" | "low" | "none"
    match_method: str = ""      # "policy_number" | "scored" | "manual" | ""
    confirmed: bool = False     # user stamped

    # Per-field score breakdown
    score_policy_number: float = 0.0
    score_dates: float = 0.0
    score_type: float = 0.0
    score_carrier: float = 0.0
    score_name: float = 0.0

    # Diff tracking
    diff_fields: list[str] = field(default_factory=list)
    cosmetic_diffs: list[str] = field(default_factory=list)
    fillable_fields: list[str] = field(default_factory=list)

    # Metadata
    eff_delta_days: int | None = None
    exp_delta_days: int | None = None
    ext_type_raw: str = ""
    ext_type_normalized: str = ""
    coverage_alias_applied: bool = False

    # Program support
    is_program_match: bool = False
    matched_carrier_id: int | None = None
```

- [ ] **Step 4: Implement `ScoreBreakdown` namedtuple and `_score_pair()` function**

Add after the `ReconcileRow` dataclass:

```python
from collections import namedtuple

ScoreBreakdown = namedtuple("ScoreBreakdown", [
    "total", "confidence",
    "score_policy_number", "score_dates", "score_type", "score_carrier", "score_name",
    "diff_fields", "cosmetic_diffs", "fillable_fields",
])

def _score_pair(ext_row: dict, db_row: dict, single_client: bool = False) -> ScoreBreakdown:
    """Additive scoring — no hard gates. Returns per-field breakdown.

    Scoring (100 pts max):
      Policy Number: 40  |  Dates: 30  |  Type: 15  |  Carrier: 10  |  Name: 5
    """
    from policydb.utils import (
        normalize_client_name_for_matching,
        normalize_policy_number_for_matching,
        normalize_coverage_type,
        normalize_carrier,
        parse_currency,
    )

    diff_fields = []
    cosmetic_diffs = []
    fillable_fields = []

    # --- Policy Number (40 pts) ---
    ext_pn = normalize_policy_number_for_matching(ext_row.get("policy_number", ""))
    db_pn = normalize_policy_number_for_matching(db_row.get("policy_number", ""))
    if not ext_pn or not db_pn:
        score_pn = 0.0
    elif ext_pn == db_pn:
        score_pn = 40.0
    else:
        pn_ratio = fuzz.ratio(ext_pn, db_pn)
        if pn_ratio >= 90:
            score_pn = 32.0
        elif pn_ratio >= 75:
            score_pn = 20.0
        else:
            score_pn = 0.0
    # Track diffs for policy_number
    if ext_pn and db_pn and ext_pn != db_pn and score_pn < 32:
        diff_fields.append("policy_number")
    elif ext_pn and not db_pn:
        fillable_fields.append("policy_number")

    # --- Dates (30 pts: 15 eff + 15 exp) ---
    score_dates = 0.0
    for date_field, max_pts in [("effective_date", 15.0), ("expiration_date", 15.0)]:
        ext_d = ext_row.get(date_field, "")
        db_d = db_row.get(date_field, "")
        if ext_d and db_d:
            delta = _date_delta_days(ext_d, db_d)
            if delta is not None:
                if delta == 0:
                    score_dates += max_pts
                elif delta <= 14:
                    score_dates += max_pts * 0.8  # 12
                elif delta <= 45:
                    score_dates += max_pts * 0.53  # ~8
                elif _same_year(ext_d, db_d):
                    score_dates += max_pts * 0.27  # ~4
                if delta > 14:
                    diff_fields.append(date_field)
            else:
                diff_fields.append(date_field)
        elif ext_d and not db_d:
            fillable_fields.append(date_field)

    # --- Policy Type (15 pts) ---
    ext_type = normalize_coverage_type(ext_row.get("policy_type", ""))
    db_type = normalize_coverage_type(db_row.get("policy_type", ""))
    if ext_type and db_type:
        if ext_type.lower() == db_type.lower():
            score_type = 15.0
            # Check if raw values differ (cosmetic)
            raw_ext = (ext_row.get("policy_type") or "").strip()
            raw_db = (db_row.get("policy_type") or "").strip()
            if raw_ext.lower() != raw_db.lower():
                cosmetic_diffs.append("policy_type")
        else:
            type_ratio = fuzz.WRatio(ext_type, db_type)
            if type_ratio >= 85:
                score_type = 12.0
                cosmetic_diffs.append("policy_type")
            elif type_ratio >= 70:
                score_type = 8.0
                diff_fields.append("policy_type")
            else:
                score_type = 0.0
                diff_fields.append("policy_type")
    else:
        score_type = 0.0

    # --- Carrier (10 pts) ---
    ext_carrier = normalize_carrier(ext_row.get("carrier", ""))
    db_carrier = normalize_carrier(db_row.get("carrier", ""))
    if ext_carrier and db_carrier:
        if ext_carrier.lower() == db_carrier.lower():
            score_carrier = 10.0
        else:
            carrier_ratio = fuzz.WRatio(ext_carrier, db_carrier)
            if carrier_ratio >= 80:
                score_carrier = 7.0
                cosmetic_diffs.append("carrier")
            elif carrier_ratio >= 60:
                score_carrier = 4.0
                diff_fields.append("carrier")
            else:
                score_carrier = 0.0
                diff_fields.append("carrier")
    else:
        score_carrier = 0.0

    # --- Client Name (5 pts) ---
    if single_client:
        score_name = 5.0
    else:
        ext_name = normalize_client_name_for_matching(ext_row.get("client_name", ""))
        db_name = normalize_client_name_for_matching(db_row.get("client_name", ""))
        # FNI cross-matching — take best score
        db_fni = normalize_client_name_for_matching(db_row.get("first_named_insured", ""))
        ext_fni = normalize_client_name_for_matching(ext_row.get("first_named_insured", ""))

        best_name_ratio = 0.0
        for a in ([ext_name] + ([ext_fni] if ext_fni else [])):
            for b in ([db_name] + ([db_fni] if db_fni else [])):
                if a and b:
                    best_name_ratio = max(best_name_ratio, fuzz.WRatio(a, b))

        if best_name_ratio >= 95:
            score_name = 5.0
        elif best_name_ratio >= 80:
            score_name = 4.0
        elif best_name_ratio >= 60:
            score_name = 2.0
        else:
            score_name = 0.0

    # --- Currency diffs (not scored, but tracked) ---
    for cur_field in ("premium", "limit_amount", "deductible"):
        ext_v = parse_currency(ext_row.get(cur_field, 0))
        db_v = parse_currency(db_row.get(cur_field, 0))
        if ext_v > 0 and db_v > 0:
            pct = abs(ext_v - db_v) / max(ext_v, db_v)
            if pct > 0.01:
                diff_fields.append(cur_field)
        elif ext_v > 0 and db_v == 0:
            fillable_fields.append(cur_field)

    total = score_pn + score_dates + score_type + score_carrier + score_name
    if total >= 75:
        confidence = "high"
    elif total >= 45:
        confidence = "medium"
    else:
        confidence = "low"

    return ScoreBreakdown(
        total=total, confidence=confidence,
        score_policy_number=score_pn, score_dates=score_dates,
        score_type=score_type, score_carrier=score_carrier, score_name=score_name,
        diff_fields=diff_fields, cosmetic_diffs=cosmetic_diffs, fillable_fields=fillable_fields,
    )
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `pytest tests/test_score_pair.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/reconciler.py tests/test_score_pair.py
git commit -m "feat: implement _score_pair additive scoring with per-field breakdowns"
```

---

### Task 4: Rewrite `reconcile()` with 3-pass structure

**Files:**
- Modify: `src/policydb/reconciler.py`
- Modify: `tests/test_program_carriers.py`
- Create: `tests/test_reconcile_algorithm.py`

**Context:** Replace the current 4-pass reconcile() (reconciler.py:601-787) with a simplified 3-pass version using `_score_pair()`. Pass 1: exact policy number. Pass 2: scored matching (>= 45). Pass 3: unmatched/extra.

- [ ] **Step 1: Write tests for the new reconcile()**

```python
# tests/test_reconcile_algorithm.py
from policydb.reconciler import reconcile

def _ext(client="Acme", ptype="GL", carrier="Hartford", polnum="GL-441",
         eff="2025-04-01", exp="2026-04-01", premium=12500):
    return {"client_name": client, "policy_type": ptype, "carrier": carrier,
            "policy_number": polnum, "effective_date": eff, "expiration_date": exp,
            "premium": premium, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}

def _db(id=1, uid="POL-001", client="Acme Construction Inc.", ptype="General Liability",
        carrier="Hartford", polnum="GL-441", eff="2025-04-01", exp="2026-04-01",
        premium=12500, client_id=1, fni=""):
    return {"id": id, "policy_uid": uid, "client_name": client, "policy_type": ptype,
            "carrier": carrier, "policy_number": polnum, "effective_date": eff,
            "expiration_date": exp, "premium": premium, "limit_amount": 0, "deductible": 0,
            "client_id": client_id, "first_named_insured": fni,
            "is_program": 0, "program_carriers": "", "program_carrier_count": 0}

def test_exact_polnum_match():
    results = reconcile([_ext()], [_db()])
    assert len(results) == 1
    assert results[0].status == "PAIRED"
    assert results[0].match_method == "policy_number"
    assert results[0].score_policy_number == 40

def test_no_gate_on_client_name():
    """Different client name should still match on pol# + dates."""
    results = reconcile(
        [_ext(client="Completely Different LLC")],
        [_db()]
    )
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1

def test_cross_type_not_matched():
    """GL should not match WC even if same client."""
    results = reconcile(
        [_ext(ptype="GL", polnum="", eff="2025-04-01", exp="2026-04-01")],
        [_db(ptype="Workers Compensation", polnum="", eff="2025-04-01", exp="2026-04-01")]
    )
    # Score: dates 30 + name ~4 + type 0 + carrier 10 = 44 < 45 threshold
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 0

def test_unmatched_and_extra():
    results = reconcile(
        [_ext(polnum="NEW-001")],
        [_db(polnum="OLD-999")]
    )
    unmatched = [r for r in results if r.status == "UNMATCHED"]
    extra = [r for r in results if r.status == "EXTRA"]
    assert len(unmatched) + len(extra) >= 1

def test_single_client_mode():
    results = reconcile(
        [_ext(client="Wrong Name", polnum="GL-441")],
        [_db()],
        single_client=True
    )
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1
    assert paired[0].score_name == 5
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `pytest tests/test_reconcile_algorithm.py -v`
Expected: FAIL (old ReconcileRow doesn't have new fields)

- [ ] **Step 3: Rewrite `reconcile()` function**

Replace reconciler.py:601-787 with the new 3-pass implementation. Key changes:
- Pass 1: Exact policy number match using `normalize_policy_number_for_matching()`. Call `_score_pair()` for full scoring. Set `match_method = "policy_number"`.
- Pass 2: For remaining rows, score every ext against every remaining db candidate using `_score_pair()`. Hungarian-style best-match assignment. Accept pairs >= 45. Set `match_method = "scored"`.
- Pass 3: Remaining ext rows → UNMATCHED. Remaining db rows → EXTRA.
- Sort: unconfirmed amber (45-74) ascending, then green (75+) ascending, then unmatched, then extra.

- [ ] **Step 4: Implement program matching in the new passes**

Programs (`is_program=1`) need special handling:
- In Pass 1: when a policy number matches a `program_carriers` row, pair the ext row with the parent program. Set `is_program_match=True` and `matched_carrier_id` to the carrier row's id. The `_score_pair()` should also check carrier-level policy numbers from `db_row.get("_program_carrier_rows", [])`.
- In Pass 2: programs can match multiple ext rows (one per carrier). Don't remove a program from the candidate pool after the first match.
- In Pass 3: programs that received at least one match are NOT marked EXTRA. Only programs with zero matches become EXTRA.
- Carry `_program_carrier_rows` on db_rows (attached in `_load_db_policies()` route handler, same as current pattern).

- [ ] **Step 5: Remove old functions** — `_fuzzy_match()`, Pass 1.5 logic, `_find_likely_pairs()`, `_cross_pair_score()`, `_compare_fields()`, `_attach_metadata()`, `COMPARE_FIELDS`, `_TEXT_FIELDS`, `_DATE_FIELDS`, `_CURRENCY_FIELDS` constants (all absorbed into `_score_pair()`)

- [ ] **Step 6: Update `find_candidates()`** to use `_score_pair()` — return top 8 by score, no threshold

- [ ] **Step 7: Run tests — verify pass**

Run: `pytest tests/test_reconcile_algorithm.py tests/test_score_pair.py tests/test_program_carriers.py -v`
Expected: all PASS (update test_program_carriers.py for new ReconcileRow fields if needed)

- [ ] **Step 8: Commit**

```bash
git add src/policydb/reconciler.py tests/
git commit -m "feat: rewrite reconcile() with 3-pass additive scoring, no hard gates"
```

---

### Task 5: Update `summarize()` and `build_reconcile_xlsx()`

**Files:**
- Modify: `src/policydb/reconciler.py`

**Context:** Update for new status values ("PAIRED"/"UNMATCHED"/"EXTRA") and per-field score breakdown in XLSX.

- [ ] **Step 1: Update `summarize()`**

Replace reconciler.py:980-984:

```python
def summarize(results: list[ReconcileRow]) -> dict:
    paired = [r for r in results if r.status == "PAIRED"]
    paired_with_diffs = [r for r in paired if r.diff_fields]
    confirmed = [r for r in paired if r.confirmed]
    review = [r for r in paired if not r.confirmed]
    return {
        "total": len(results),
        "paired": len(paired),
        "paired_clean": len(paired) - len(paired_with_diffs),
        "paired_diffs": len(paired_with_diffs),
        "confirmed": len(confirmed),
        "review": len(review),
        "unmatched": sum(1 for r in results if r.status == "UNMATCHED"),
        "extra": sum(1 for r in results if r.status == "EXTRA"),
    }
```

- [ ] **Step 2: Update `build_reconcile_xlsx()`**

Update the XLSX builder to use new status values and add score breakdown columns. Sheets: "Summary", "Paired" (with score + diff count), "Unmatched", "Extra", "All Results".

- [ ] **Step 3: Update `_STATUS_SORT`**

```python
_STATUS_SORT = {"PAIRED": 0, "UNMATCHED": 1, "EXTRA": 2}
```

- [ ] **Step 4: Remove `MatchStatus` type alias** — no longer needed

- [ ] **Step 5: Run existing tests**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/reconciler.py
git commit -m "feat: update summarize and XLSX export for new status model"
```

---

### Task 6: Update `reconciler.py` imports and remove dead code

**Files:**
- Modify: `src/policydb/reconciler.py`
- Modify: `src/policydb/importer.py`

**Context:** Remove `_normalize_client_name`, `_normalize_policy_number`, `_normalize_coverage` from reconciler.py (now in utils.py). Update importer.py to use shared `parse_currency`. Clean up unused imports.

- [ ] **Step 1: Update reconciler imports**

```python
from policydb.utils import (
    _COVERAGE_ALIASES,
    normalize_coverage_type,
    normalize_carrier,
    normalize_client_name_for_matching,
    normalize_policy_number_for_matching,
    parse_currency,
)
```

- [ ] **Step 2: Remove local normalize functions** from reconciler.py (lines 18-55, `_normalize_coverage`, `_normalize_policy_number`, `_normalize_client_name`, `_LEGAL_SUFFIX_RE`, `_PLACEHOLDER_POLICY_NUMBERS`)

- [ ] **Step 3: Update importer.py** — add `from policydb.utils import parse_currency` and replace calls to `_parse_currency` with `parse_currency`. Keep `_parse_currency` as a deprecated alias: `_parse_currency = parse_currency`

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/reconciler.py src/policydb/importer.py
git commit -m "refactor: consolidate normalize functions, remove dead code from reconciler"
```

---

## Phase 3: Pairing Board UI

### Task 7: Create row-level templates

**Files:**
- Create: `src/policydb/web/templates/reconcile/_pair_row.html`
- Create: `src/policydb/web/templates/reconcile/_unmatched_row.html`
- Create: `src/policydb/web/templates/reconcile/_extra_row.html`
- Create: `src/policydb/web/templates/reconcile/_score_breakdown.html`

**Context:** Each template renders one row of the pairing board. All use `hx-target="#pair-{idx}"` and `hx-swap="outerHTML"` for HTMX partial swaps. See spec Section 2 and brainstorm mockup for exact layout. Follow existing Tailwind patterns from the codebase.

- [ ] **Step 1: Create `_score_breakdown.html`** — score pills with color coding. Receives `row` (ReconcileRow) in context. Shows per-field scores as colored pills (green >= 80% of max, amber 40-79%, red < 40%). Shows diff details below.

- [ ] **Step 2: Create `_pair_row.html`** — paired row (green/amber). Left: ext data. Center: score badge (clickable to toggle breakdown). Right: db data with diff tags. Actions: Confirm / Break. Includes `_score_breakdown.html` in a hidden div toggled by JS.

- [ ] **Step 3: Create `_unmatched_row.html`** — unmatched upload row (red). Left: ext data. Center: "no match" badge. Right: drop zone (`ondragover`, `ondrop`). Actions: Search / Create.

- [ ] **Step 4: Create `_extra_row.html`** — extra DB policy (purple). Draggable (`draggable="true"`, `data-policy-uid`). Shows policy info inline. Actions: Archive.

- [ ] **Step 5: Verify templates render** — manually test with `policydb serve` by navigating to `/reconcile` and uploading test data.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/reconcile/_pair_row.html \
  src/policydb/web/templates/reconcile/_unmatched_row.html \
  src/policydb/web/templates/reconcile/_extra_row.html \
  src/policydb/web/templates/reconcile/_score_breakdown.html
git commit -m "feat: add pairing board row templates"
```

---

### Task 8: Create `_pairing_board.html` and update `index.html`

**Files:**
- Create: `src/policydb/web/templates/reconcile/_pairing_board.html`
- Modify: `src/policydb/web/templates/reconcile/index.html`

**Context:** The board wraps all rows with toolbar (counters, filters, bulk actions) and the extras pool section. `index.html` keeps the upload form but renders `_pairing_board.html` as the results target instead of the old results table.

- [ ] **Step 1: Create `_pairing_board.html`** — toolbar with counters (`id="board-counters"`), filter tabs (All/Needs Review/Confirmed/Diffs Only as client-side toggles), "Confirm All Paired" button, "Export XLSX" link. Column headers. Loop over `results` and include `_pair_row.html`/`_unmatched_row.html` based on status. Extras pool section at bottom with `_extra_row.html` for each. Hidden form field with `token`. ~40 lines of drag-drop JS at bottom.

- [ ] **Step 2: Update `index.html`** — change the `hx-target` of the reconcile form to swap in `_pairing_board.html` instead of old templates. Add reference guide collapsible panel link. Add template CSV download links.

- [ ] **Step 3: Test rendering** — upload test data, verify board renders with correct sections.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/reconcile/_pairing_board.html \
  src/policydb/web/templates/reconcile/index.html
git commit -m "feat: add pairing board layout and update index.html"
```

---

### Task 9: Implement pairing board route handlers

**Files:**
- Modify: `src/policydb/web/routes/reconcile.py`

**Context:** Update `reconcile_run()` to render `_pairing_board.html`. Add new endpoints: confirm, break, pair, confirm-all, search-coverage. Cache reconcile results with per-field scores. Each action modifies cache and returns updated row HTML + OOB counters.

- [ ] **Step 1: Update `reconcile_run()` and cache structure**

Redefine the cache structure. The current `_RESULT_CACHE` stores `dict[str, tuple[bytes, float]]` (XLSX bytes + timestamp). The new board needs mutable row state:

```python
# New cache structure
_BOARD_CACHE: dict[str, tuple[list[ReconcileRow], list[dict], float]] = {}
# token → (results list, extra_db_rows list, timestamp)
# XLSX generated on-demand at export time, not cached separately
```

Rename `_MISSING_CACHE` to `_UNMATCHED_CACHE` (for status rename). Update `_cache_cleanup()`.

Call updated `reconcile()`, store results in `_BOARD_CACHE` keyed by token. Render `_pairing_board.html` with results, token, and summary counts.

- [ ] **Step 2: Add `POST /reconcile/confirm/{idx}`** — mark `_RESULT_CACHE[token][idx].confirmed = True`, return `_pair_row.html` with confirmed state + OOB counters.

- [ ] **Step 3: Add `POST /reconcile/break/{idx}`** — set row status to "UNMATCHED", move db dict to extras list in cache. Return `_unmatched_row.html` + OOB append `_extra_row.html` to extras pool + OOB counters.

- [ ] **Step 4: Add `POST /reconcile/pair/{idx}`** — accept `policy_uid` form field. Find db row, call `_score_pair()`, update cache row with pair data. Return `_pair_row.html` + OOB delete extra + OOB counters.

- [ ] **Step 5: Add `POST /reconcile/confirm-all`** — confirm all PAIRED rows with score >= 75. Return HX-Trigger for full board refresh.

- [ ] **Step 6: Add `GET /reconcile/search-coverage`** — accept `q` param, search DB policies by type/carrier/number. Return HTML dropdown with "Pair" buttons that POST to `/reconcile/pair/{idx}`.

- [ ] **Step 7: Update existing routes** for new ReconcileRow fields — `reconcile_apply_field`, `reconcile_create`, `reconcile_archive`.

- [ ] **Step 8: Run smoketest** — `python -c "from fastapi.testclient import TestClient; from policydb.web.app import app; c = TestClient(app); print(c.get('/reconcile').status_code)"`

- [ ] **Step 9: Commit**

```bash
git add src/policydb/web/routes/reconcile.py
git commit -m "feat: implement pairing board route handlers"
```

---

### Task 10: Remove old templates

**Files:**
- Remove: `src/policydb/web/templates/reconcile/_results_table.html`
- Remove: `src/policydb/web/templates/reconcile/_review_panel.html`
- Remove: `src/policydb/web/templates/reconcile/_pair_section.html`
- Remove: `src/policydb/web/templates/reconcile/_suggest_panel.html`
- Remove: `src/policydb/web/templates/reconcile/_extra_panel.html`
- Remove: `src/policydb/web/templates/reconcile/_edit_form.html`
- Remove: `src/policydb/web/templates/reconcile/_batch_create_review.html`

- [ ] **Step 1: Verify no remaining references** — grep for each template name in routes and other templates.

- [ ] **Step 2: Remove old route handlers** that are no longer needed — `reconcile_suggest`, `reconcile_suggest_extra`, `reconcile_confirm_match`, `reconcile_confirm_pair`, `reconcile_ignore_pair`, `batch_create_review`, `batch_create`, `batch_create_program`, `reconcile_edit_form`, `reconcile_update`, `reconcile_apply_selected`.

- [ ] **Step 3: Delete old template files.**

- [ ] **Step 4: Run smoketest** — verify `/reconcile` loads, upload works.

- [ ] **Step 5: Commit**

```bash
git add -u src/policydb/web/templates/reconcile/ src/policydb/web/routes/reconcile.py
git commit -m "refactor: remove old reconcile templates and routes"
```

---

## Phase 4: Data Prep Layer

### Task 11: Validation panel

**Files:**
- Create: `src/policydb/web/templates/reconcile/_validation_panel.html`
- Modify: `src/policydb/web/routes/reconcile.py`

**Context:** After column mapping, before the match runs. Shows parsed data summary: coverage types (recognized/unrecognized), carriers, dates, client names, programs, policy numbers. "Run Match →" button proceeds to reconcile. See spec Section 4.1.

- [ ] **Step 1: Add `GET /reconcile/validation-panel`** — accept token, load cached parsed rows, run validation checks (coverage normalization, carrier lookup, date parsing, client fuzzy-match, program auto-detection). Return `_validation_panel.html`.

- [ ] **Step 2: Create `_validation_panel.html`** — sections for each data type with green check/amber warning icons. Unrecognized items show mapping dropdowns. "Run Match →" button POSTs to `/reconcile` with token.

- [ ] **Step 3: Add auto-learn endpoints** — `POST /reconcile/learn-carrier-alias` and `POST /reconcile/learn-coverage-alias`. Save to config, call rebuild functions. Return updated pill HTML.

- [ ] **Step 4: Detect location columns in upload** — during validation, check if the parsed rows contain `location`, `address`, `project`, or `site` columns. If so, show a notice: "Location data detected — will be available for assignment after matching." Store the location data in the cached parsed rows for post-reconcile use.

- [ ] **Step 5: Update the upload flow** in `index.html` — after column mapping succeeds, swap in validation panel instead of immediately running reconcile.

- [ ] **Step 5: Test flow** — upload CSV, verify validation panel appears, click "Run Match →", verify pairing board renders.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/reconcile/_validation_panel.html \
  src/policydb/web/routes/reconcile.py \
  src/policydb/web/templates/reconcile/index.html
git commit -m "feat: add pre-match validation panel with auto-learn aliases"
```

---

### Task 12: Reference guide and template CSV downloads

**Files:**
- Create: `src/policydb/web/templates/reconcile/_reference_guide.html`
- Modify: `src/policydb/web/routes/reconcile.py`

**Context:** Printable reference page showing all canonical coverage types + aliases, carrier aliases, column header aliases, program flagging instructions. Dynamically generated from code. Template CSV downloads.

- [ ] **Step 1: Add `GET /reconcile/reference-guide`** — gather `_COVERAGE_ALIASES`, `carrier_aliases` from config, `PolicyImporter.ALIASES`. Render `_reference_guide.html`.

- [ ] **Step 2: Create `_reference_guide.html`** — clean printable layout with tables. Coverage types + aliases. Carrier aliases. Column header aliases. Program flagging instructions. `@media print` styles.

- [ ] **Step 3: Add `GET /reconcile/template-csv/{type}`** — generate and download CSV template. `type = "standard"` or `"full"`. Return CSV response with correct headers and one example row.

- [ ] **Step 4: Add links to `index.html`** — collapsible reference panel, template download buttons.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/reconcile/_reference_guide.html \
  src/policydb/web/routes/reconcile.py \
  src/policydb/web/templates/reconcile/index.html
git commit -m "feat: add data prep reference guide and template CSV downloads"
```

---

## Phase 5: Location Assignment Tool

### Task 13: Location board templates

**Files:**
- Create: `src/policydb/web/templates/clients/_location_board.html`
- Create: `src/policydb/web/templates/clients/_location_policy_row.html`
- Create: `src/policydb/web/templates/clients/_location_group.html`

**Context:** Reuse pairing board pattern. Policies on top (unassigned) / inside location groups (assigned). Drag-to-assign. See spec Section 5 and brainstorm mockup.

- [ ] **Step 1: Create `_location_policy_row.html`** — draggable policy row with grip handle, policy type, carrier, policy number, premium, policy_uid link. "Unassign" button if assigned.

- [ ] **Step 2: Create `_location_group.html`** — location header (name, address, policy count, total premium, color). Collapsible. Drop zone. Contains policy rows.

- [ ] **Step 3: Create `_location_board.html`** — toolbar with counters (assigned/unassigned/locations), "Import CSV Mapping" button, "+ New Location" button. Unassigned pool at top. Location groups below. Smart suggestion banners for address-matched policies. ~30 lines of drag-drop JS.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/clients/_location_board.html \
  src/policydb/web/templates/clients/_location_policy_row.html \
  src/policydb/web/templates/clients/_location_group.html
git commit -m "feat: add location assignment board templates"
```

---

### Task 14: Location board route handlers

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

**Context:** Add endpoints for location assignment board under `/clients/{id}/locations`. Uses HTMX partial swaps same as pairing board.

- [ ] **Step 1: Add `GET /clients/{id}/locations`** — load client's policies (grouped by project_name), load projects for client. Render `_location_board.html` with unassigned policies, location groups, smart suggestions (group unassigned policies by shared exposure_address).

- [ ] **Step 2: Add `PATCH /clients/{id}/locations/assign`** — accept policy_uid(s) and project_id. Set `project_name` + `project_id` on policies. Return updated `_location_policy_row.html` + OOB counters.

- [ ] **Step 3: Add `PATCH /clients/{id}/locations/unassign`** — clear `project_name` + `project_id` on policy. Return policy row in unassigned state + OOB counters.

- [ ] **Step 4: Add `POST /clients/{id}/locations/create`** — create new project row. Return new `_location_group.html`.

- [ ] **Step 5: Add `POST /clients/{id}/locations/import-csv`** — parse CSV mapping policy_uid/policy_number → location name. Create locations if needed. Bulk assign. Return full board refresh.

- [ ] **Step 6: Add "Organize by Location" button** to client detail template (`src/policydb/web/templates/clients/detail.html`).

- [ ] **Step 7: Implement post-reconcile hook** — in `_pairing_board.html`, after all pairs are confirmed, if the reconcile upload had location data (stored in cached parsed rows), show an "Assign Locations Now?" button. Clicking it navigates to `/clients/{id}/locations` with the CSV location values passed as query params or cached for the location board to pre-populate suggestions.

- [ ] **Step 7: Run smoketest** — verify `/clients/1/locations` loads.

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/clients.py \
  src/policydb/web/templates/clients/detail.html
git commit -m "feat: implement location assignment board routes"
```

---

## Phase 6: Integration & Cleanup

### Task 15: End-to-end smoketest and final cleanup

**Files:**
- Various

- [ ] **Step 1: Full smoketest** — test each reconcile endpoint via TestClient:
  - GET /reconcile → 200
  - POST /reconcile with test CSV → pairing board renders
  - POST /reconcile/confirm/{idx} → row confirms
  - POST /reconcile/break/{idx} → row breaks
  - POST /reconcile/pair/{idx} → row pairs
  - GET /reconcile/search-coverage?q=GL → results
  - GET /reconcile/reference-guide → printable page
  - GET /reconcile/template-csv/standard → CSV download
  - GET /clients/1/locations → location board

- [ ] **Step 2: Verify importer still works** — run import smoketest.

- [ ] **Step 3: Update `build/lib/` copies** if the build process requires it.

- [ ] **Step 4: Final commit**

```bash
git add src/policydb/ tests/
git commit -m "feat: reconcile redesign complete — pairing board, additive scoring, data prep, location tool"
```
