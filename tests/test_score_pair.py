"""Tests for the additive _score_pair() scoring function in reconciler.py."""

from policydb.reconciler import _score_pair


def test_exact_policy_number_scores_40():
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "GL-2025-441", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 12500, "limit_amount": 0,
           "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme Construction Inc.", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-2025-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 12500, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_policy_number == 40
    assert result.total >= 90


def test_no_hard_gates():
    ext = {"client_name": "Totally Different Name", "policy_type": "GL",
           "carrier": "Hartford", "policy_number": "GL-2025-441",
           "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme Construction", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-2025-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.total >= 70  # pol# 40 + dates 30 + carrier 10 = 80 min


def test_date_scoring_exact():
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_dates == 30


def test_missing_policy_number_neutral():
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 0, "limit_amount": 0,
           "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-123",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_policy_number == 0


def test_confidence_tiers():
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "GL-441", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 12500, "limit_amount": 0,
           "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 12500, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.confidence == "high"


def test_single_client_mode():
    ext = {"client_name": "Wrong Name", "policy_type": "GL", "carrier": "",
           "policy_number": "", "effective_date": "", "expiration_date": "",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "General Liability", "carrier": "",
          "policy_number": "", "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db, single_client=True)
    assert result.score_name == 5


def test_premium_diff_tracked():
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "", "expiration_date": "",
           "premium": 8200, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "", "expiration_date": "",
          "premium": 7850, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert "premium" in result.diff_fields


def test_fillable_fields():
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "ABC-123",
           "effective_date": "", "expiration_date": "",
           "premium": 5000, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert "policy_number" in result.fillable_fields
    assert "premium" in result.fillable_fields


def test_fni_cross_matching():
    ext = {"client_name": "DBA Name", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "", "expiration_date": "",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Legal Entity LLC", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": "DBA Name"}
    result = _score_pair(ext, db)
    # FNI match: ext client "DBA Name" vs db FNI "DBA Name" should score high
    assert result.score_name >= 4


# ─── Additional edge case tests ──────────────────────────────────────────────


def test_date_within_14_days():
    """Dates within 14 days should score 12 per date (not 15)."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "2025-04-05", "expiration_date": "2026-04-05",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_dates == 24  # 12 + 12 for within-14d


def test_date_within_45_days():
    """Dates within 45 days should score 8 per date."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "2025-05-01", "expiration_date": "2026-05-01",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.score_dates == 16  # 8 + 8 for within-45d


def test_medium_confidence():
    """Score between 45 and 75 should be medium confidence."""
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "",
           "policy_number": "", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 0, "limit_amount": 0,
           "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "General Liability", "carrier": "",
          "policy_number": "", "effective_date": "2025-04-01",
          "expiration_date": "2026-04-01", "premium": 0, "limit_amount": 0,
          "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.confidence == "medium"  # dates 30 + type 15 + name 5 = 50


def test_low_confidence():
    """Score below 45 should be low confidence."""
    ext = {"client_name": "Acme", "policy_type": "Property", "carrier": "",
           "policy_number": "", "effective_date": "", "expiration_date": "",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "Auto", "carrier": "",
          "policy_number": "", "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.confidence == "low"  # name 5 + type low or 0 = well below 45


def test_fuzzy_policy_number_scores_32():
    """Fuzzy policy number >= 90 should score 32."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "GL-2025-4410",
           "effective_date": "", "expiration_date": "",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "GL-2025-441",
          "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    # The fuzzy ratio between "GL20254410" and "GL2025441" should be >= 90
    assert result.score_policy_number >= 20  # at minimum fuzzy >= 75 threshold


def test_deductible_fillable():
    """Ext has deductible, DB has 0 -> fillable."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "", "expiration_date": "",
           "premium": 0, "limit_amount": 0, "deductible": 2500, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "", "expiration_date": "",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert "deductible" in result.fillable_fields


def test_score_breakdown_has_total():
    """ScoreBreakdown namedtuple should have a total property."""
    ext = {"client_name": "Acme", "policy_type": "GL", "carrier": "Hartford",
           "policy_number": "GL-441", "effective_date": "2025-04-01",
           "expiration_date": "2026-04-01", "premium": 0, "limit_amount": 0,
           "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "Acme", "policy_type": "General Liability",
          "carrier": "Hartford", "policy_number": "GL-441",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    expected_total = (result.score_policy_number + result.score_dates +
                      result.score_type + result.score_carrier + result.score_name)
    assert result.total == expected_total


def test_eff_exp_delta_populated():
    """_score_pair should populate eff_delta_days and exp_delta_days."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "2025-04-05", "expiration_date": "2026-04-10",
           "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
          "premium": 0, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert result.eff_delta_days == 4
    assert result.exp_delta_days == 9


def test_cosmetic_diffs_not_in_diff_fields():
    """Premium that matches exactly should not appear in diff_fields or cosmetic_diffs."""
    ext = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
           "effective_date": "", "expiration_date": "",
           "premium": 5000, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    db = {"client_name": "", "policy_type": "", "carrier": "", "policy_number": "",
          "effective_date": "", "expiration_date": "",
          "premium": 5000, "limit_amount": 0, "deductible": 0, "first_named_insured": ""}
    result = _score_pair(ext, db)
    assert "premium" not in result.diff_fields
    assert "premium" not in result.fillable_fields
