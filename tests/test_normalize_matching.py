"""Tests for matching-specific normalization functions in utils.py."""

from policydb.utils import (
    normalize_client_name_for_matching,
    normalize_coverage_type,
    normalize_policy_number_for_matching,
    parse_currency,
    rebuild_coverage_aliases,
)


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


# ─── normalize_policy_number_for_matching ────────────────────────────────────


def test_strips_formatting():
    assert normalize_policy_number_for_matching("POL-GL-2025-441") == "POLGL2025441"
    assert normalize_policy_number_for_matching("WC 99.812") == "WC99812"


def test_strips_leading_zeros():
    assert normalize_policy_number_for_matching("001234") == "1234"
    assert normalize_policy_number_for_matching("00ABC456") == "ABC456"


def test_filters_placeholders():
    assert normalize_policy_number_for_matching("TBD") == ""
    assert normalize_policy_number_for_matching("N/A") == ""
    assert normalize_policy_number_for_matching("999") == ""
    assert normalize_policy_number_for_matching("PENDING") == ""


def test_policy_number_empty():
    assert normalize_policy_number_for_matching("") == ""
    assert normalize_policy_number_for_matching(None) == ""


# ─── parse_currency ─────────────────────────────────────────────────────────


def test_parse_currency_basic():
    assert parse_currency("$1,234.56") == 1234.56
    assert parse_currency("1234") == 1234.0


def test_parse_currency_empty():
    assert parse_currency("") == 0.0
    assert parse_currency(None) == 0.0


def test_parse_currency_invalid():
    assert parse_currency("abc") == 0.0


def test_parse_currency_negative():
    assert parse_currency("-$500") == -500.0


# ─── rebuild_coverage_aliases ──────────────────────────────────────────────


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
