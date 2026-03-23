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
    "meeting_types": [
        "Stewardship",
        "Renewal Strategy",
        "Claims Review",
        "New Business",
        "General Check-in",
        "Prospecting",
        "Annual Review",
    ],
    "activity_cluster_days": 7,
    "follow_up_dispositions": [
        {"label": "Left VM", "default_days": 3, "accountability": "waiting_external"},
        {"label": "No Answer", "default_days": 1, "accountability": "my_action"},
        {"label": "Sent Email", "default_days": 7, "accountability": "waiting_external"},
        {"label": "Sent RFI", "default_days": 7, "accountability": "waiting_external"},
        {"label": "Waiting on Colleague", "default_days": 5, "accountability": "waiting_external"},
        {"label": "Waiting on Client", "default_days": 7, "accountability": "waiting_external"},
        {"label": "Waiting on Carrier", "default_days": 7, "accountability": "waiting_external"},
        {"label": "Connected", "default_days": 0, "accountability": "my_action"},
        {"label": "Received Response", "default_days": 0, "accountability": "my_action"},
        {"label": "Meeting Scheduled", "default_days": 0, "accountability": "scheduled"},
        {"label": "Escalated", "default_days": 3, "accountability": "my_action"},
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
    "compliance_statuses": [
        "Compliant", "Gap", "Partial", "Waived", "N/A", "Needs Review",
    ],
    "deductible_types": [
        "Per Occurrence", "Per Claim", "Aggregate", "Named Storm %",
    ],
    "construction_types": [
        "Frame (ISO 1)", "Joisted Masonry (ISO 2)", "Non-Combustible (ISO 3)",
        "Masonry Non-Combustible (ISO 4)", "Modified Fire Resistive (ISO 5)",
        "Fire Resistive (ISO 6)",
    ],
    "sprinkler_options": [
        "Yes", "No", "Partial", "Unknown",
    ],
    "roof_types": [
        "Built-Up", "Modified Bitumen", "TPO/PVC Membrane", "EPDM",
        "Metal", "Tile", "Shingle", "Slate", "Other",
    ],
    "protection_classes": [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "8B", "9E",
    ],
    "endorsement_types": [
        "Additional Insured",
        "Waiver of Subrogation",
        "Primary & Non-Contributory",
        "Per-Project Aggregate",
        "Notice of Cancellation",
        "Completed Operations",
        "Professional Liability",
        "Pollution",
        "Cyber",
        "Builders Risk",
    ],
    "risk_review_prompt_categories": [
        "Operational", "People", "Liability", "Financial", "Contractual",
    ],
    "risk_review_prompts": [
        {
            "category": "Operational",
            "question": "What are the critical assets at each location — property, equipment, inventory, IP, data? What perils threaten them (CAT zones, flood, wind, seismic)?",
            "coverage_lines": ["Property", "Inland Marine / Equipment", "Builders Risk"],
            "industry_keywords_high": [],
        },
        {
            "category": "Operational",
            "question": "What is the revenue model and what interrupts it? Are there single-source suppliers or long lead-time dependencies?",
            "coverage_lines": ["Property"],
            "industry_keywords_high": [],
        },
        {
            "category": "People",
            "question": "Are subcontractors or contingent labor used? Are certificates of insurance collected and tracked for all subs?",
            "coverage_lines": ["General Liability", "Umbrella / Excess", "Workers Compensation"],
            "industry_keywords_high": ["construction", "contractor", "builder"],
        },
        {
            "category": "People",
            "question": "Is there a board of directors, HOA board, or management committee? What management liability exposure exists?",
            "coverage_lines": ["Directors & Officers", "Employment Practices"],
            "industry_keywords_high": ["condo", "hoa", "association", "nonprofit"],
        },
        {
            "category": "Liability",
            "question": "What contractual indemnification obligations exist? Do upstream contracts require specific AI, WOS, or primary/noncontributory endorsements?",
            "coverage_lines": ["General Liability", "Umbrella / Excess", "Professional Liability / E&O"],
            "industry_keywords_high": [],
        },
        {
            "category": "Liability",
            "question": "Does the organization give advice, design, certify, or provide professional services? Is there completed operations exposure?",
            "coverage_lines": ["Professional Liability / E&O", "General Liability"],
            "industry_keywords_high": ["architect", "engineer", "consultant"],
        },
        {
            "category": "Liability",
            "question": "Is there pollution or environmental liability exposure at any location? Underground storage tanks, hazardous materials, or remediation obligations?",
            "coverage_lines": ["Pollution / Environmental"],
            "industry_keywords_high": ["manufacturing", "chemical", "energy", "oil"],
        },
        {
            "category": "Liability",
            "question": "What data does the organization collect, store, or process? What systems are mission-critical? Is there regulatory exposure (PII, PHI, PCI)?",
            "coverage_lines": ["Cyber / Privacy"],
            "industry_keywords_high": ["technology", "healthcare", "financial"],
        },
        {
            "category": "Financial",
            "question": "What is the organization's balance sheet capacity to retain risk? What deductible/SIR level represents the pain threshold?",
            "coverage_lines": [],
            "industry_keywords_high": [],
        },
        {
            "category": "Financial",
            "question": "Is there crime, social engineering fraud, or employee theft exposure? Are fiduciary obligations (ERISA, benefit plans) in scope?",
            "coverage_lines": ["Crime / Fidelity"],
            "industry_keywords_high": [],
        },
        {
            "category": "Contractual",
            "question": "Are there OCIP/CCIP (wrap-up) programs at any location? Which parties are enrolled vs. excluded?",
            "coverage_lines": ["General Liability", "Workers Compensation", "Umbrella / Excess"],
            "industry_keywords_high": ["construction", "development"],
        },
        {
            "category": "Contractual",
            "question": "Do different locations have different lenders, management agreements, or counterparties with distinct insurance requirements?",
            "coverage_lines": [],
            "industry_keywords_high": ["condo", "hoa", "real estate", "portfolio"],
        },
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
    "email_subject_meeting": "Meeting Recap: {{meeting_title}} \u2014 {{meeting_date}}",
    "mandated_activity_horizon_days": 180,
    "mandated_activities": [
        {
            "name": "RSM Meeting",
            "trigger": "days_before_expiry",
            "days": 120,
            "prep_days": 30,
            "prep_notes": "Review loss runs, financials, and exposure changes before meeting",
            "activity_type": "Meeting",
            "subject": "RSM Meeting — {{policy_type}}",
        },
        {
            "name": "Post-Binding Meeting",
            "trigger": "days_after_effective",
            "days": 45,
            "prep_days": 0,
            "activity_type": "Meeting",
            "subject": "Post-Binding Meeting — {{policy_type}}",
        },
        {
            "name": "Loss Runs Received",
            "trigger": "days_before_expiry",
            "days": 130,
            "prep_days": 7,
            "checklist_milestone": "Loss Runs Received",
            "activity_type": "Internal Strategy",
            "subject": "Request loss runs — {{policy_type}}",
        },
        {
            "name": "Market Submissions",
            "trigger": "days_before_expiry",
            "days": 90,
            "prep_days": 14,
            "checklist_milestone": "Submission Sent",
            "activity_type": "Internal Strategy",
            "subject": "Prepare submissions — {{policy_type}}",
        },
        {
            "name": "Quote Received",
            "trigger": "days_before_expiry",
            "days": 75,
            "prep_days": 7,
            "checklist_milestone": "Quote Received",
            "activity_type": "Renewal Check-In",
            "subject": "Follow up on quotes — {{policy_type}}",
        },
        {
            "name": "Coverage Comparison Prepared",
            "trigger": "days_before_expiry",
            "days": 60,
            "prep_days": 10,
            "checklist_milestone": "Coverage Comparison Prepared",
            "activity_type": "Internal Strategy",
            "subject": "Build comparison — {{policy_type}}",
        },
        {
            "name": "Client Presentation",
            "trigger": "days_before_expiry",
            "days": 50,
            "prep_days": 7,
            "activity_type": "Meeting",
            "subject": "Renewal presentation — {{policy_type}}",
        },
        {
            "name": "Client Approved",
            "trigger": "days_before_expiry",
            "days": 40,
            "prep_days": 3,
            "checklist_milestone": "Client Approved",
            "activity_type": "Renewal Check-In",
            "subject": "Get client decision — {{policy_type}}",
        },
        {
            "name": "Binder Requested",
            "trigger": "days_before_expiry",
            "days": 30,
            "prep_days": 3,
            "checklist_milestone": "Binder Requested",
            "activity_type": "Email",
            "subject": "Request binder — {{policy_type}}",
        },
        {
            "name": "Policy Received",
            "trigger": "days_before_expiry",
            "days": 14,
            "prep_days": 0,
            "checklist_milestone": "Policy Received",
            "activity_type": "Renewal Check-In",
            "subject": "Confirm policy issued — {{policy_type}}",
        },
    ],
    "email_subject_request": "{{client_name}} \u2014 {{rfi_uid}} {{request_title}}",
    "email_subject_request_all": "{{client_name}} \u2014 Outstanding Information Requests",
    "email_subject_rfi_notify": "FYI: {{client_name}} \u2014 {{rfi_uid}} Items Received",
    "project_stages": ["Upcoming", "Quoting", "Bound", "Active", "Complete"],
    "project_types": ["Location", "Construction", "Development", "Renovation"],
    "expertise_lines": [
        "Casualty", "Property", "Workers Compensation", "Professional Liability",
        "D&O", "Cyber", "Construction", "Environmental", "Marine",
        "Aviation", "Surety", "Executive Risk", "Employee Benefits",
    ],
    "expertise_industries": [
        "Sports & Entertainment", "Construction", "Healthcare", "Real Estate",
        "Technology", "Manufacturing", "Hospitality", "Energy",
        "Financial Services", "Public Entity", "Transportation",
    ],
    "daily_followup_target": 5,
    "pin_renewal_days": 14,
    "backup_retention_count": 30,
    "migration_backup_retention_count": 10,
    "milestone_profiles": [
        {
            "name": "Full Renewal",
            "description": "Complete renewal workflow for complex or large accounts",
            "milestones": [
                "Loss Runs Received",
                "Submission Sent",
                "Quote Received",
                "Coverage Comparison Prepared",
                "Client Approved",
                "Binder Requested",
                "Policy Received",
            ],
        },
        {
            "name": "Standard Renewal",
            "description": "Standard renewal workflow for mid-market accounts",
            "milestones": [
                "Submission Sent",
                "Quote Received",
                "Client Approved",
                "Binder Requested",
                "Policy Received",
            ],
        },
        {
            "name": "Simple Renewal",
            "description": "Streamlined workflow for small or straightforward accounts",
            "milestones": [
                "Submission Sent",
                "Quote Received",
                "Policy Received",
            ],
        },
    ],
    "milestone_profile_rules": [
        {"if_premium_gte": 100000, "suggest_profile": "Full Renewal"},
        {"if_premium_gte": 25000, "suggest_profile": "Standard Renewal"},
        {"if_premium_lt": 25000, "suggest_profile": "Simple Renewal"},
    ],
    "timeline_engine": {
        "minimum_gap_days": 3,
        "drift_threshold_days": 7,
        "compression_threshold": 0.5,
    },
    "risk_alert_thresholds": {
        "at_risk_notify": True,
        "critical_notify": True,
        "critical_auto_draft": True,
    },
    "carrier_aliases": {
        "Travelers": [
            "Travelers Insurance", "The Travelers Companies", "Travelers Indemnity",
            "Travelers Indemnity Co", "Travelers Casualty", "St Paul Fire",
            "St. Paul Fire & Marine", "Travelers Casualty & Surety",
        ],
        "Chubb": [
            "Chubb Limited", "ACE American", "ACE American Insurance",
            "Federal Insurance", "Federal Insurance Company", "Chubb Insurance",
        ],
        "AIG": [
            "American International Group", "AIG Insurance", "National Union Fire",
            "Lexington Insurance", "AIG Property Casualty",
        ],
        "Hartford": [
            "The Hartford", "Hartford Fire", "Hartford Financial",
            "Hartford Fire Insurance", "Hartford Casualty",
        ],
        "Liberty Mutual": [
            "Liberty Mutual Insurance", "Liberty Mutual Fire", "Liberty Mutual Group",
        ],
        "Zurich": [
            "Zurich Insurance", "Zurich American", "Zurich North America",
            "Zurich American Insurance",
        ],
        "CNA": ["CNA Insurance", "CNA Financial", "Continental Casualty"],
        "Markel": ["Markel Corporation", "Markel Insurance", "Markel Specialty"],
        "Berkshire Hathaway": [
            "Berkshire Hathaway Insurance", "BHSI",
            "Berkshire Hathaway Specialty Insurance",
        ],
        "Nationwide": [
            "Nationwide Insurance", "Nationwide Mutual",
            "Allied Insurance", "Nationwide Mutual Insurance",
        ],
        "Progressive": [
            "Progressive Insurance", "Progressive Casualty", "Progressive Commercial",
        ],
        "Employers": [
            "Employers Insurance", "Employers Holdings",
            "Employers Compensation Insurance",
        ],
        "FM Global": ["Factory Mutual", "FM Insurance", "Factory Mutual Insurance"],
        "Everest": ["Everest Re", "Everest Insurance", "Everest National Insurance"],
        "RLI": ["RLI Insurance", "RLI Corp"],
        "Coalition": ["Coalition Insurance", "Coalition Inc"],
        "Berkley": [
            "W.R. Berkley", "WR Berkley", "Berkley Insurance",
            "Berkley One", "W. R. Berkley Corporation",
        ],
        "Tokio Marine": ["Tokio Marine HCC", "HCC Insurance", "Tokio Marine America"],
        "Hanover": ["The Hanover", "Hanover Insurance", "Hanover Insurance Group"],
        "Arch": ["Arch Insurance", "Arch Capital", "Arch Insurance Group"],
        "Great American": [
            "Great American Insurance", "Great American Insurance Company",
        ],
        "Sompo": ["Sompo International", "Endurance Specialty"],
        "Argo": ["Argo Group", "Argo Insurance"],
        "Aspen": ["Aspen Insurance", "Aspen Specialty"],
        "Axis": ["AXIS Insurance", "AXIS Capital"],
        "Cincinnati Financial": [
            "Cincinnati Insurance", "The Cincinnati Insurance Company",
        ],
        "Erie": ["Erie Insurance", "Erie Indemnity"],
        "Intact": ["Intact Insurance", "OneBeacon"],
        "QBE": ["QBE Insurance", "QBE North America"],
        "Starr": ["Starr Insurance", "Starr Companies", "Starr Indemnity"],
    },
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
