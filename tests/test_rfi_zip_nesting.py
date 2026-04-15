"""Regression tests for RFI bundle ZIP folder nesting."""
import policydb.web.app  # noqa: F401 — boot FastAPI app (breaks circular import)

from policydb.web.routes.attachments import _rfi_item_folder


def test_rfi_folder_full_metadata():
    """Full metadata nests Project/Coverage/Item cleanly."""
    folder = _rfi_item_folder({
        "description": "2024 loss runs",
        "sort_order": 3,
        "project_name": "Downtown Tower",
        "policy_coverage_line": "General Liability",
        "category": "Loss Runs",
    })
    assert folder == "Downtown_Tower/General_Liability/Item_003_2024_loss_runs/"


def test_rfi_folder_falls_back_to_policy_project():
    """Item has no project_name but the linked policy does."""
    folder = _rfi_item_folder({
        "description": "COI",
        "sort_order": 1,
        "project_name": None,
        "policy_project_name": "Main Office",
        "policy_coverage_line": "Property",
    })
    assert folder == "Main_Office/Property/Item_001_COI/"


def test_rfi_folder_falls_back_to_category_when_no_policy():
    """No linked policy → category becomes the coverage-line folder."""
    folder = _rfi_item_folder({
        "description": "Financial statements",
        "sort_order": 2,
        "project_name": "HQ",
        "category": "Financials",
    })
    assert folder == "HQ/Financials/Item_002_Financial_statements/"


def test_rfi_folder_full_fallback_to_shared_general():
    """Item with no project and no coverage info falls back cleanly."""
    folder = _rfi_item_folder({
        "description": "W-9",
        "sort_order": 1,
    })
    assert folder == "Shared/General/Item_001_W-9/"


def test_rfi_folder_sanitizes_unsafe_chars():
    """Slashes and other unsafe chars are stripped from folder names."""
    folder = _rfi_item_folder({
        "description": "Worker's Comp: 2023 payroll!",
        "sort_order": 5,
        "project_name": "Main Office / Warehouse",
        "policy_coverage_line": "WC (CA)",
    })
    # Slashes, parens, colons, apostrophes, exclamation marks all removed
    assert folder == "Main_Office_Warehouse/WC_CA/Item_005_Workers_Comp_2023_payroll/"


def test_rfi_folder_handles_missing_description():
    """Item with no description uses 'Item' as the slug fallback."""
    folder = _rfi_item_folder({
        "sort_order": 0,
        "project_name": "Proj",
        "policy_coverage_line": "WC",
    })
    assert folder == "Proj/WC/Item_000_Item/"
