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
    assert normalize_coverage_type("cyber liability") == "Cyber / Tech E&O"
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
