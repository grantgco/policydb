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
    "default_hourly_rate": 150,
    "renewal_effort_multiplier": 1.5,
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
    "critical_milestones": [
        "Submission Sent",
        "Quote Received",
        "Client Approved",
    ],
    "escalation_thresholds": {
        "critical_days": 60,
        "critical_stale_days": 14,
        "warning_days": 90,
        "nudge_days": 120,
        "nudge_stale_days": 30,
    },
    "readiness_thresholds": {
        "ready": 75,
        "on_track": 50,
        "at_risk": 25,
    },
    "readiness_weights": {
        "status": 40,
        "checklist": 25,
        "activity": 15,
        "followup": 10,
        "placement": 10,
    },
    "readiness_status_scores": {
        "Not Started": 0,
        "In Progress": 50,
        "Submitted": 75,
        "Quoted": 80,
        "Pending Bind": 88,
        "Bound": 100,
    },
    "readiness_milestone_weights": {
        "Submission Sent": 2,
        "Loss Runs Received": 1,
        "Quote Received": 2,
        "Coverage Comparison Prepared": 1,
        "Client Approved": 2,
        "Binder Requested": 1,
        "Policy Received": 1,
    },
    "readiness_activity_tiers": [
        {"days": 7, "pct": 100},
        {"days": 14, "pct": 67},
        {"days": 30, "pct": 33},
    ],
    "followup_workload_thresholds": {
        "warning": 3,
        "danger": 5,
    },
    "linked_account_relationships": [
        "Related", "Subsidiary", "Sister Company",
        "Common Ownership", "Joint Venture", "Parent / Holding",
    ],
    "auto_review_enabled": True,
    "auto_review_field_threshold": 2,
    "auto_review_activity_threshold": 3,
    "review_cycle_default": "1w",
    "auto_followup_days_before_expiry": 120,
    "quick_log_templates": [
        {"label": "Called re: renewal", "type": "Call", "subject": "Called re: renewal"},
        {"label": "Emailed placement", "type": "Email", "subject": "Emailed placement colleague"},
        {"label": "Renewal check-in", "type": "Call", "subject": "Renewal check-in"},
        {"label": "Sent submission", "type": "Email", "subject": "Submitted to carrier"},
        {"label": "Internal discussion", "type": "Meeting", "subject": "Internal strategy discussion"},
    ],
    "risk_categories": [
        "Property",
        "General Liability",
        "Auto / Fleet",
        "Workers Compensation",
        "Umbrella / Excess",
        "Professional Liability / E&O",
        "Directors & Officers",
        "Employment Practices",
        "Cyber / Privacy",
        "Pollution / Environmental",
        "Inland Marine / Equipment",
        "Builders Risk",
        "Crime / Fidelity",
        "Management Liability",
        "Other",
    ],
    "risk_severities": [
        "Low",
        "Medium",
        "High",
        "Critical",
    ],
    "risk_sources": [
        "New Business", "Renewal", "Loss Event",
        "Stewardship", "Contract Review", "Other",
    ],
    "risk_control_types": [
        "Prevention", "Mitigation", "Transfer", "Retention", "Avoidance",
    ],
    "risk_control_statuses": [
        "Recommended", "In Progress", "Implemented", "Declined",
    ],
    "risk_adequacy_levels": [
        "Adequate", "Inadequate", "Needs Review", "N/A",
    ],
    "contact_roles": [
        "Account Executive", "Account Manager", "Producer", "CSR",
        "Placement Colleague", "Underwriter", "Broker", "Claims Adjuster",
    ],
    "request_categories": [
        "Exposure Data", "Loss Runs", "Applications",
        "Financial Statements", "Certificates", "Fleet Schedule",
        "Payroll Data", "Contracts", "Underwriting Question", "Other",
    ],
    "client_facing_milestones": [
        "Loss Runs Received",
    ],
    "email_subject_policy": "Re: {{client_name}}{{project_name_sep}} \u2014 {{policy_type}} \u2014 Eff. {{effective_date}}",
    "email_subject_client": "Re: {{client_name}}",
    "email_subject_followup": "Re: {{client_name}}{{project_name_sep}} \u2014 {{policy_type}} \u2014 {{subject}}",
    "mandated_activities": [
        {
            "name": "RSM Meeting",
            "trigger": "days_before_expiry",
            "days": 120,
            "activity_type": "Meeting",
            "subject": "RSM Meeting — {{policy_type}}",
        },
        {
            "name": "Post-Binding Meeting",
            "trigger": "days_after_effective",
            "days": 45,
            "activity_type": "Meeting",
            "subject": "Post-Binding Meeting — {{policy_type}}",
        },
    ],
    "email_subject_request": "{{client_name}} \u2014 {{rfi_uid}} {{request_title}}",
    "email_subject_request_all": "{{client_name}} \u2014 Outstanding Information Requests",
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
