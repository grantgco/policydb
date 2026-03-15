"""Configuration loading with defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from policydb.db import CONFIG_PATH

_DEFAULTS: dict[str, Any] = {
    "default_account_exec": "Grant",
    "renewal_windows": {
        "urgent": 90,
        "warning": 120,
        "upcoming": 180,
    },
    "export_dir": str(Path.home() / ".policydb" / "exports"),
    "stale_threshold_days": 14,
    "coverage_gap_rules": [
        {
            "if_present": "General Liability",
            "should_have": "Umbrella / Excess",
            "message": "GL without Umbrella — intentional?",
        },
        {
            "if_industry": "Digital Infrastructure",
            "should_have": "Cyber / Tech E&O",
            "message": "Digital infrastructure client without Cyber — verify.",
        },
        {
            "if_industry": "Real Estate Development",
            "should_have": "Professional Liability / E&O",
            "message": "RE developer without E&O — verify design/consulting exposure.",
        },
        {
            "if_present": "Property / Builders Risk",
            "should_have": "Inland Marine",
            "message": "Property/BR without Inland Marine — equipment floater needed?",
        },
    ],
    "policy_types": [
        "General Liability",
        "Property / Builders Risk",
        "Professional Liability / E&O",
        "Cyber / Tech E&O",
        "Umbrella / Excess",
        "Workers Compensation",
        "Commercial Auto",
        "Inland Marine",
        "Directors & Officers",
        "Employment Practices Liability",
        "Environmental",
        "Builders Risk (Standalone)",
        "Equipment Breakdown",
        "Crime / Fidelity",
    ],
    "industry_segments": [
        "Real Estate Development",
        "Digital Infrastructure",
        "Construction",
        "Technology",
        "Healthcare",
        "Manufacturing",
    ],
    "coverage_forms": [
        "Occurrence",
        "Claims-Made",
        "Reporting",
    ],
    "renewal_statuses": [
        "Not Started",
        "In Progress",
        "Pending Bind",
        "Bound",
    ],
    "renewal_statuses_excluded": [],
    "opportunity_statuses": [
        "Prospecting",
        "Quoting",
        "Submitted",
        "Pending Bind",
    ],
    "activity_types": [
        "Call",
        "Email",
        "Meeting",
        "Stewardship",
        "Site Visit",
        "Claim Discussion",
        "Internal Strategy",
        "Renewal Check-In",
        "Other",
    ],
    "carriers": [
        "AIG",
        "Berkley One",
        "Chubb",
        "CNA",
        "Employers",
        "Everest",
        "Hanover",
        "Hartford",
        "Hiscox",
        "Intact",
        "Ironshore",
        "Liberty Mutual",
        "Markel",
        "Munich Re",
        "Philadelphia",
        "RLI",
        "Sompo",
        "Starr",
        "State Auto",
        "Tokio Marine",
        "Travelers",
        "Zurich",
    ],
    "exposure_basis_options": [
        "Payroll",
        "Revenue",
        "Square Feet",
        "Headcount",
        "Contract Value",
        "Total Insured Value",
        "Units",
        "Acres",
    ],
    "exposure_unit_options": [
        "Per $100 Payroll",
        "Per $1,000 Revenue",
        "Per $1,000 TIV",
        "Per Unit",
        "Per Employee",
        "Per $1M Contract",
        "Flat",
    ],
    "renewal_milestones": [
        "Submission Sent",
        "Loss Runs Received",
        "Quote Received",
        "Coverage Comparison Prepared",
        "Client Approved",
        "Binder Requested",
        "Policy Received",
    ],
    "review_cycle_default": "1w",
    "email_subject_policy": "Re: {{client_name}}{{project_name_sep}} \u2014 {{policy_type}} \u2014 Eff. {{effective_date}}",
    "email_subject_client": "Re: {{client_name}}",
    "email_subject_followup": "Re: {{client_name}}{{project_name_sep}} \u2014 {{policy_type}} \u2014 {{subject}}",
}

_config: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config
    result = dict(_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user = yaml.safe_load(f) or {}
            # Deep merge renewal_windows
            if "renewal_windows" in user:
                result["renewal_windows"].update(user.pop("renewal_windows"))
            result.update(user)
        except Exception:
            pass
    _config = result
    return _config


def get(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def reload_config() -> dict[str, Any]:
    global _config
    _config = None
    return load_config()


def save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def add_list_item(key: str, item: str) -> None:
    cfg = dict(load_config())
    lst = list(cfg.get(key, []))
    if item not in lst:
        lst.append(item)
        cfg[key] = lst
        save_config(cfg)
        reload_config()


def remove_list_item(key: str, item: str) -> None:
    cfg = dict(load_config())
    cfg[key] = [v for v in cfg.get(key, []) if v != item]
    save_config(cfg)
    reload_config()


def reorder_list_item(key: str, item: str, direction: str) -> None:
    """Move item one position up or down in the list."""
    cfg = dict(load_config())
    lst = list(cfg.get(key, []))
    if item not in lst:
        return
    idx = lst.index(item)
    if direction == "up" and idx > 0:
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
    elif direction == "down" and idx < len(lst) - 1:
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
    cfg[key] = lst
    save_config(cfg)
    reload_config()


def write_default_config() -> None:
    """Write default config.yaml if it doesn't exist."""
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(_DEFAULTS, f, default_flow_style=False, sort_keys=False)
