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
