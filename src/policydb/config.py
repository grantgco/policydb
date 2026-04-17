"""Configuration loading with defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from policydb.db import CONFIG_PATH

_DEFAULTS: dict[str, Any] = {
    "default_account_exec": "Grant",
    "brokerage_name": "",
    "renewal_windows": {
        "urgent": 90,
        "warning": 120,
        "upcoming": 180,
    },
    "export_dir": str(Path.home() / ".policydb" / "exports"),
    "report_logo_path": str(Path.home() / ".policydb" / "logo.png"),
    "stale_threshold_days": 14,
    "focus_score_weights": {
        "deadline_proximity": 40,
        "staleness": 25,
        "severity": 20,
        "overdue_multiplier": 15,
    },
    "focus_auto_promote_days": 14,
    "focus_nudge_alert_days": 10,
    "opportunity_staleness_days": 14,
    "google_places_api_key": "",
    "google_places_daily_limit": 1000,
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
        "Business Owners Policy",
        "Employers Liability",
    ],
    "auto_sub_coverages": {
        "Workers Compensation": ["Employers Liability"],
    },
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
        "Lost",
        "Non-Renewed",
        "Declined",
        "Not Tracked",
    ],
    # Statuses silenced from alerts, renewal pipeline, suggested follow-ups,
    # AND renewal-issue auto-create. The generator also hard-skips Lost /
    # Non-Renewed / Declined / Not Tracked via renewal_issues._renewal_skip_statuses,
    # so clearing this list does not re-enable those cases.
    "renewal_statuses_excluded": ["Lost", "Non-Renewed", "Declined", "Not Tracked"],
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
    "activity_cluster_days": 7,
    "followup_expiration_buffer_days": 3,
    "follow_up_dispositions": [
        {"label": "Left VM", "default_days": 3, "accountability": "waiting_external", "category": "waiting"},
        {"label": "No Answer", "default_days": 1, "accountability": "my_action", "category": "action"},
        {"label": "Sent Email", "default_days": 7, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Sent RFI", "default_days": 7, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Waiting on Colleague", "default_days": 5, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Waiting on Client", "default_days": 7, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Waiting on Carrier", "default_days": 7, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Waiting on Response", "default_days": 7, "accountability": "waiting_external", "category": "waiting"},
        {"label": "Connected", "default_days": 0, "accountability": "my_action", "category": "action"},
        {"label": "Received Response", "default_days": 0, "accountability": "my_action", "category": "action"},
        {"label": "Meeting Scheduled", "default_days": 0, "accountability": "scheduled", "category": "completed"},
        {"label": "Escalated", "default_days": 3, "accountability": "my_action", "category": "action"},
    ],
    "disposition_categories": {
        "waiting": "I reached out (waiting)",
        "action": "I got a response (my turn)",
        "completed": "It's handled",
    },
    "disposition_context_hints": {
        "Call": ["Left VM", "Connected", "No Answer"],
        "Email": ["Sent Email", "Received Response"],
        "Meeting": ["Meeting Scheduled", "Connected"],
    },
    # ── Issue tracking ──────────────────────────────────────────────
    "issue_lifecycle_states": [
        "Open",
        "In Hand",
        "Waiting",
        "Resolved",
        "Closed",
    ],
    "issue_severities": [
        {"label": "Critical", "sla_days": 1, "color": "red"},
        {"label": "High", "sla_days": 3, "color": "amber"},
        {"label": "Normal", "sla_days": 7, "color": "blue"},
        {"label": "Low", "sla_days": 14, "color": "gray"},
    ],
    "issue_resolution_types": [
        "Completed",
        "Escalated",
        "Withdrawn",
        "Workaround",
        "Duplicate",
    ],
    "issue_root_cause_categories": [
        "Carrier Error",
        "Client Request",
        "Coverage Gap",
        "Billing",
        "Documentation",
        "Compliance",
        "Other",
    ],
    "renewal_issue_window_days": 120,
    "renewal_issue_health_threshold": "at_risk",
    "renewal_issue_auto_create": True,
    "renewal_issue_auto_link": True,
    "renewal_issue_resolve_statuses": ["Bound"],
    "renewal_terminal_statuses": ["Bound", "Lost", "Non-Renewed", "Declined"],
    "issue_auto_close_days": 14,
    "stale_auto_close_days": 30,
    "auto_closed_section_days": 7,
    # ── Knowledge Base ────────────────────────────────────────────────
    "kb_categories": [
        "Glossary",
        "Procedure",
        "Coverage",
        "Carrier Intel",
        "Underwriting",
        "Claims",
        "General",
    ],
    "kb_article_sources": [
        "Authored",
        "LLM-Assisted",
        "External",
    ],
    "attachment_categories": [
        "General",
        "Dec Page",
        "Binder",
        "Certificate",
        "Application",
        "Endorsement",
        "Loss Run",
        "Meeting Notes",
        "Contract",
        "Correspondence",
        "Proposal",
        "Report",
    ],
    # ── Anomaly detection ──────────────────────────────────────────────
    "anomaly_thresholds": {
        "renewal_not_started_days": 60,
        "stale_followup_count": 10,
        "status_no_activity_days": 30,
        "no_activity_days": 90,
        "no_followup_scheduled": True,
        "heavy_week_threshold": 5,
        "forecast_window_days": 30,
        "light_week_window_days": 14,
        "bound_missing_effective": True,
        "expired_no_renewal": True,
        "review_min_health_score": 70,
        "review_activity_window_days": 30,
        "overdue_review_days": 90,
    },
    # ── Timesheet Review ───────────────────────────────────────────────
    "timesheet_thresholds": {
        "low_day_threshold_hours": 4.0,
        "silence_renewal_window_days": 30,
        "range_cap_days": 92,
    },
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
    "standard_exposure_types": {
        "Payroll": "currency",
        "Revenue": "currency",
        "TIV": "currency",
        "Vehicle Count": "number",
        "Employee Count": "number",
        "Square Footage": "number",
    },
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
    "exposure_denominators": [1, 100, 1000],
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
    "review_reminder_day": "monday",
    "review_stale_issue_days": 14,
    "review_renewal_urgency_days": 60,
    "review_renewal_window_days": 120,
    "review_inactive_client_days": 180,
    "review_vacation_pre_marketing_days": 14,
    "auto_followup_days_before_expiry": 120,
    "quick_log_templates": [
        {"label": "Called Client", "icon": "phone", "type": "Call", "subject": "Called {primary_contact}", "follow_up_days": 3},
        {"label": "Sent Email", "icon": "mail", "type": "Email", "subject": "Email re: {next_renewal}", "follow_up_days": 5},
        {"label": "Left Voicemail", "icon": "voicemail", "type": "Call", "subject": "VM for {primary_contact}", "follow_up_days": 2},
        {"label": "Received Docs", "icon": "doc", "type": "Other", "subject": "Received documents", "follow_up_days": 0},
        {"label": "Internal Meeting", "icon": "meeting", "type": "Meeting", "subject": "Internal strategy discussion", "follow_up_days": 7},
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
        "Needs Review", "Compliant", "Partial", "Gap",
        "External", "Pending Info", "Waived", "N/A",
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
    "team_assignments": [
        "Account Management", "Placement/Broking", "Claims",
        "Analytics", "Risk Engineering", "Administration",
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
    "email_subject_program": "Re: {{client_name}} \u2014 {{program_name}}",
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
    # Post-bind stewardship follow-ups generated when a Bind Order is submitted.
    # Anchored on bind_date. Generated once per subject (program OR standalone policy).
    # Editable in Settings (mirror of mandated_activities).
    "post_bind_activities": [
        {
            "name": "Binder received from carrier",
            "days_after_bind": 5,
            "activity_type": "Follow-up",
            "subject": "Confirm binder received from carrier",
        },
        {
            "name": "Binder sent to client",
            "days_after_bind": 7,
            "activity_type": "Follow-up",
            "subject": "Send binder to client",
        },
        {
            "name": "Invoice issued to client",
            "days_after_bind": 7,
            "activity_type": "Follow-up",
            "subject": "Issue invoice to client",
        },
        {
            "name": "Final policy document received",
            "days_after_bind": 30,
            "activity_type": "Follow-up",
            "subject": "Confirm final policy document received",
        },
    ],
    # ── Recurring Events ─────────────────────────────────────────────
    # Cadence labels used by recurring_events.cadence. _advance() in
    # recurring_events.py hard-codes how each label steps forward; adding a
    # new label here requires a corresponding case there.
    "recurring_event_cadences": [
        "Daily",
        "Weekly",
        "Biweekly",
        "Monthly",
        "Quarterly",
        "Semi-Annual",
        "Annual",
    ],
    # Classification labels for recurring_events.event_type (UI filtering only)
    "recurring_event_types": [
        "Call",
        "Deliverable",
        "Meeting",
        "Review",
        "Report",
    ],
    # How far ahead the generator looks when materializing issue instances
    "recurring_event_generation_horizon_days": 14,
    # Safety cap on instances materialized per template per generation pass
    "recurring_event_max_catchup": 12,
    "email_subject_request": "{{client_name}} \u2014 {{rfi_uid}} {{request_title}}",
    "email_subject_request_all": "{{client_name}} \u2014 Outstanding Information Requests",
    "email_subject_rfi_notify": "FYI: {{client_name}} \u2014 {{rfi_uid}} Items Received",
    "project_stages": ["Upcoming", "Quoting", "Bound", "Active", "Complete"],
    "project_types": ["Location", "Construction", "Development", "Renovation"],
    "insurance_reminder_tiers": [30, 14, 7],
    "insurance_completed_stages": ["Bound", "Active", "Complete"],
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
    "account_priority_options": [
        "Cost Reduction", "Coverage Expansion", "Claims Support",
        "Risk Mitigation", "Market Competition", "Growth", "Compliance",
    ],
    "relationship_risk_levels": ["None", "Low", "Medium", "High"],
    "service_model_options": ["Standard", "High-touch", "White-glove"],
    "daily_followup_target": 5,
    "pin_renewal_days": 14,
    "loss_run_follow_up_days": 7,
    "backup_retention_count": 30,
    "migration_backup_retention_count": 10,
    "log_level": "INFO",
    "log_retention_days": 730,
    "merged_issue_retention_days": 90,
    "default_review_activity_type": "Other",
    "review_session_gap_minutes": 30,
    "review_dismiss_days": 7,
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
    # Import source names for reconcile match memory
    "import_source_names": [
        "AMS Export",
        "Carrier Statement",
        "Manual Spreadsheet",
        "Prior AE Handoff",
        "Binder / Dec Page",
    ],
    # Per-source field trust weights (0-100). Higher = more authoritative for that field.
    # Used in conflict resolution: effective_priority = trust_weight × recency_factor
    "field_trust_defaults": {
        "AMS Export": {"premium": 95, "policy_number": 95, "carrier": 90,
                       "effective_date": 95, "expiration_date": 95, "project_name": 20},
        "Manual Spreadsheet": {"project_name": 90, "exposure_address": 85,
                               "layer_position": 80, "premium": 50},
        "Carrier Statement": {"premium": 100, "policy_number": 100, "carrier": 100,
                              "effective_date": 100, "expiration_date": 100},
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
    # ── Data Health ──────────────────────────────────────────────────────
    "data_health_fields": {
        "policy": [
            {"field": "carrier", "label": "Carrier", "weight": 3, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": 365},
            {"field": "premium", "label": "Premium", "weight": 3, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": 180},
            {"field": "effective_date", "label": "Effective Date", "weight": 3, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "expiration_date", "label": "Expiration Date", "weight": 3, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "policy_type", "label": "Policy Type", "weight": 2, "stages": ["opportunity", "active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "policy_number", "label": "Policy Number", "weight": 2, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "renewal_status", "label": "Renewal Status", "weight": 2, "stages": ["renewal_window", "bound_complete"], "decay_days": None},
            {"field": "premium", "label": "Estimated Premium", "weight": 2, "stages": ["opportunity"], "decay_days": 180},
        ],
        "client": [
            {"field": "industry_segment", "label": "Industry", "weight": 2, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "account_exec", "label": "Account Executive", "weight": 2, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
            {"field": "cn_number", "label": "Account Number", "weight": 1, "stages": ["active", "renewal_window", "bound_complete"], "decay_days": None},
        ],
    },
    "data_health_threshold": 85,
    "data_health_completeness_weight": 0.7,
    "data_health_freshness_weight": 0.3,
    # Outlook integration
    "last_outlook_sync": None,
    "outlook_sync_lookback_days": 7,
    "outlook_email_shell_header": True,
    "outlook_capture_category": "PDB",
    "outlook_skip_category": "Personal",
    "outlook_contact_sync_enabled": True,
    "outlook_search_auto_paste": True,
    "outlook_contact_category": "PDB",
    "outlook_contact_allow_deletes": True,
    # Phase 3 comprehensive crawl — folders matching this list get
    # include_in_crawl=0 during folder discovery. Editable via the
    # Settings > Email & Contacts > Email Sync Folders page. Matching
    # is case-sensitive on the *leaf* name (the last path segment), so
    # excluding "Archive" skips any folder whose leaf is "Archive"
    # regardless of where it lives in the tree.
    #
    # Default exclusions cover three categories:
    #   - System folders that contain no actionable correspondence
    #     (Deleted Items, Trash, Junk Email, Drafts, Outbox, etc.)
    #   - Outlook for Mac native chrome (Conversation History from
    #     Teams/Skype, Scheduled outbound queue, Other Users for
    #     shared mailboxes — opt-in if you want shared inbox crawl)
    #   - SaneBox auto-filing buckets (@SaneBlackHole through
    #     @SaneTomorrow). These contain real emails Sanebox triaged
    #     out of Inbox; users with active Sanebox workflows can
    #     re-enable individual buckets via the per-folder toggle.
    "outlook_excluded_folders": [
        # System folders
        "Deleted Items",
        "Trash",
        "Junk Email",
        "Drafts",
        "Outbox",
        "RSS Feeds",
        "Sync Issues",
        "Clutter",
        # Outlook for Mac chrome
        "Conversation History",
        "Scheduled",
        "Other Users",
        # SaneBox auto-filing (re-enable specific buckets via UI if you use them)
        "@SaneBlackHole",
        "@SaneLater",
        "@SaneNews",
        "@SaneNextWeek",
        "@SaneThings",
        "@SaneTomorrow",
    ],
    # First-run crawl horizon for Phase 3: how many days of history to
    # pull on the very first sync after folder discovery. Subsequent
    # syncs are incremental via outlook_folder_sync.last_synced_at and
    # aren't affected by this number. 14 days is the recommended anchor —
    # smaller = faster first run, larger = more historical catch-up.
    "outlook_first_run_days": 14,
    # Master switch for the Phase 3D comprehensive crawl. False = legacy
    # sync_outlook() runs (Sent Items + PDB-categorized + Flagged).
    # True  = crawl_folders() runs (every folder where include_in_crawl=1
    # in outlook_folder_sync, with per-folder last_synced_at). Flip via
    # the toggle on the Email Sync Folders settings card after running
    # discovery and confirming the folder list looks right.
    "outlook_use_comprehensive_crawl": False,
    # Per-operation osascript subprocess timeouts (seconds). Folder crawls
    # and discover runs build large `whose` predicates against Outlook and
    # can genuinely take minutes on deep archives; a flat 30s ceiling causes
    # silent per-folder sync loss. Override individual keys in config.yaml
    # only when the defaults here are genuinely insufficient.
    "outlook_script_timeout_seconds": {
        "create_draft": 30,
        "search_emails": 30,
        "search_all_folders": 120,
        "search_folder_since": 120,
        "get_flagged_emails": 120,
        "discover_folders": 300,
    },
    "freemail_domains": [
        "gmail.com", "outlook.com", "yahoo.com", "hotmail.com",
        "aol.com", "icloud.com", "live.com", "msn.com", "me.com",
        "comcast.net", "att.net", "verizon.net",
    ],
    "internal_email_domains": [
        "marsh.com", "marshpm.com", "mmc.com",
    ],
    "automated_email_prefixes": [
        "noreply", "no-reply", "no_reply",
        "donotreply", "do-not-reply", "do_not_reply",
        "mailer-daemon", "postmaster",
        "bounce", "bounces",
        "notification", "notifications",
        "alert", "alerts",
        "automated", "system",
    ],
}

_config: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, preserving unspecified sub-keys.

    A shallow merge (``{**base, **override}``) replaces an entire nested dict
    with the partial override, silently discarding all sibling default values
    that the user did not explicitly repeat. This recursive version walks into
    nested dicts so that a partial ``anomaly_thresholds`` override in
    config.yaml only replaces the keys the user specified.
    """
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config() -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config
    result = dict(_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user = yaml.safe_load(f) or {}
            result = _deep_merge(result, user)
        except Exception as e:
            import logging
            logging.getLogger("policydb").warning("Failed to load config.yaml: %s", e)
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
