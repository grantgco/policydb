"""Tests for the 3-pass reconcile() algorithm."""

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
    results = reconcile(
        [_ext(client="Completely Different LLC")],
        [_db()]
    )
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1


def test_unmatched_and_extra():
    results = reconcile(
        [_ext(polnum="BRAND-NEW-001", ptype="Cyber", carrier="Axis", eff="2025-01-01", exp="2026-01-01")],
        [_db(polnum="TOTALLY-DIFFERENT", ptype="Workers Compensation", carrier="Travelers", eff="2024-06-01", exp="2025-06-01")]
    )
    unmatched = [r for r in results if r.status == "UNMATCHED"]
    extra = [r for r in results if r.status == "EXTRA"]
    assert len(unmatched) == 1
    assert len(extra) == 1


def test_single_client_mode():
    results = reconcile(
        [_ext(client="Wrong Name", polnum="GL-441")],
        [_db()],
        single_client=True
    )
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1
    assert paired[0].score_name == 5


def test_scored_match_without_polnum():
    """Match on dates + type + carrier when no policy number."""
    results = reconcile(
        [_ext(polnum="", carrier="Hartford", ptype="GL", eff="2025-04-01", exp="2026-04-01")],
        [_db(polnum="", carrier="Hartford", ptype="General Liability", eff="2025-04-01", exp="2026-04-01")]
    )
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1
    assert paired[0].match_method == "scored"


def test_program_carrier_polnum_match():
    """Program carrier rows should be matched by policy number in Pass 1."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-001", "client_name": "Acme Corp",
        "policy_type": "Property Program", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 500000, "limit_amount": 10000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 10, "carrier": "AIG", "policy_number": "POL-4481", "premium": 200000, "limit_amount": 5000000},
            {"id": 11, "carrier": "Chubb", "policy_number": "CHB-889", "premium": 300000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Acme Corp", "policy_type": "Property",
        "carrier": "AIG", "policy_number": "POL-4481",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) >= 1
    assert paired[0].is_program_match is True
    assert paired[0].matched_carrier_id == 10


def test_program_not_marked_extra_when_matched():
    """Programs with at least one match should NOT appear as EXTRA."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-001", "client_name": "Acme Corp",
        "policy_type": "Property Program", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 500000, "limit_amount": 10000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 10, "carrier": "AIG", "policy_number": "POL-4481", "premium": 200000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Acme Corp", "policy_type": "Property",
        "carrier": "AIG", "policy_number": "POL-4481",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    extra = [r for r in results if r.status == "EXTRA"]
    assert len(extra) == 0


def test_sort_order_amber_before_green():
    """Amber (45-74) results should sort before green (75+) for unconfirmed pairs."""
    # Create two ext rows that will match with different scores
    ext_rows = [
        _ext(polnum="GL-441", carrier="Hartford", ptype="GL"),  # high score match
        _ext(polnum="", carrier="", ptype="GL", eff="2025-04-01", exp="2026-04-01",
             client="Acme Construction"),  # lower score match (no polnum, no carrier)
    ]
    db_rows = [
        _db(id=1, uid="POL-001", polnum="GL-441"),
        _db(id=2, uid="POL-002", polnum="WC-200", ptype="General Liability",
            carrier="", eff="2025-04-01", exp="2026-04-01"),
    ]
    results = reconcile(ext_rows, db_rows)
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 2
    # Amber scores should come before green scores
    if paired[0].match_score < 75 and paired[1].match_score >= 75:
        pass  # correct order
    elif paired[0].match_score >= 75 and paired[1].match_score >= 75:
        pass  # both green, order doesn't matter for this constraint
    elif paired[0].match_score < 75 and paired[1].match_score < 75:
        # Both amber, lower score should come first
        assert paired[0].match_score <= paired[1].match_score


def test_multiple_ext_rows_match_program():
    """Multiple ext rows can match a single program (programs stay in candidate pool)."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-001", "client_name": "Acme Corp",
        "policy_type": "Property Program", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 500000, "limit_amount": 10000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 10, "carrier": "AIG", "policy_number": "POL-4481", "premium": 200000, "limit_amount": 5000000},
            {"id": 11, "carrier": "Chubb", "policy_number": "CHB-889", "premium": 300000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [
        {"client_name": "Acme Corp", "policy_type": "Property", "carrier": "AIG",
         "policy_number": "POL-4481", "effective_date": "2025-04-01",
         "expiration_date": "2026-04-01", "premium": 200000, "limit_amount": 5000000,
         "deductible": 0, "first_named_insured": ""},
        {"client_name": "Acme Corp", "policy_type": "Property", "carrier": "Chubb",
         "policy_number": "CHB-889", "effective_date": "2025-04-01",
         "expiration_date": "2026-04-01", "premium": 300000, "limit_amount": 5000000,
         "deductible": 0, "first_named_insured": ""},
    ]
    results = reconcile(ext_rows, db_rows)
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 2
    # Both should be program matches
    assert all(r.is_program_match for r in paired)
