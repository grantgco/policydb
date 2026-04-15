"""Regression tests for RFI bundle ZIP folder nesting."""
import policydb.web.app  # noqa: F401 — boot FastAPI app (breaks circular import)

from policydb.web.routes.attachments import _friendly_folder_name, _rfi_item_folder


def test_rfi_folder_full_metadata():
    """Full metadata nests Project/Coverage/Item cleanly with human-readable names."""
    folder = _rfi_item_folder({
        "description": "2024 loss runs",
        "sort_order": 3,
        "project_name": "Downtown Tower",
        "policy_coverage_line": "General Liability",
        "category": "Loss Runs",
    })
    assert folder == "Downtown Tower/General Liability/003 - 2024 loss runs/"


def test_rfi_folder_falls_back_to_policy_project():
    """Item has no project_name but the linked policy does."""
    folder = _rfi_item_folder({
        "description": "COI",
        "sort_order": 1,
        "project_name": None,
        "policy_project_name": "Main Office",
        "policy_coverage_line": "Property",
    })
    assert folder == "Main Office/Property/001 - COI/"


def test_rfi_folder_falls_back_to_category_when_no_policy():
    """No linked policy → category becomes the coverage-line folder."""
    folder = _rfi_item_folder({
        "description": "Financial statements",
        "sort_order": 2,
        "project_name": "HQ",
        "category": "Financials",
    })
    assert folder == "HQ/Financials/002 - Financial statements/"


def test_rfi_folder_full_fallback_to_shared_general():
    """Item with no project and no coverage info falls back cleanly."""
    folder = _rfi_item_folder({
        "description": "W-9",
        "sort_order": 1,
    })
    assert folder == "Shared/General/001 - W-9/"


def test_rfi_folder_sanitizes_unsafe_chars():
    """Forbidden filesystem chars are stripped but spaces and punctuation remain."""
    folder = _rfi_item_folder({
        "description": "Worker's Comp: 2023 payroll!",
        "sort_order": 5,
        "project_name": "Main Office / Warehouse",
        "policy_coverage_line": "WC (CA)",
    })
    # Slashes and colons are forbidden — apostrophes, parens, exclamations stay.
    # Whitespace around the removed slash collapses to a single space.
    assert folder == "Main Office Warehouse/WC (CA)/005 - Worker's Comp 2023 payroll!/"


def test_rfi_folder_handles_missing_description():
    """Item with no description uses 'Item' as the fallback."""
    folder = _rfi_item_folder({
        "sort_order": 0,
        "project_name": "Proj",
        "policy_coverage_line": "WC",
    })
    assert folder == "Proj/WC/000 - Item/"


def test_friendly_folder_name_preserves_readability():
    """Client-facing names keep spaces, capitals, and safe punctuation."""
    assert _friendly_folder_name("Downtown Tower") == "Downtown Tower"
    assert _friendly_folder_name("General Liability & Auto") == "General Liability & Auto"
    assert _friendly_folder_name("  leading/trailing  ") == "leadingtrailing"
    # Forbidden chars stripped
    assert _friendly_folder_name('Bad:Name*Here?') == "BadNameHere"
    # Empty → sensible default
    assert _friendly_folder_name("") == "Untitled"
    # Length cap
    long_name = "x" * 200
    assert len(_friendly_folder_name(long_name, max_len=40)) == 40
