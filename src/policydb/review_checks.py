"""Review check definitions — descriptions, action types, and field mappings.

Used by anomaly_engine.get_review_gate_status() and review slideover templates.
"""

GATE_CHECK_META: dict[str, dict] = {
    "Data Health": {
        "description": "Policy data is {score}% complete — missing: {fields}",
        "why": "Incomplete data leads to coverage gaps and E&O exposure",
        "action_type": "quick_fix",
        "action_label": "Edit Fields",
        "quick_fix_type": "link_to_slideover",
    },
    "Recent Activity": {
        "description": "No user activity logged in {days} days",
        "why": "Indicates the renewal may not be actively managed",
        "action_type": "dual",
        "quick_fix_label": "Quick Log",
        "quick_fix_type": "quick_log",
        "followup_label": "Schedule Follow-Up",
        "followup_note": "Review status of {uid}",
    },
    "No Open Anomalies": {
        "description": "{count} unresolved anomalies: {types}",
        "why": "Open anomalies indicate unaddressed risks or data problems",
        "action_type": "dual",
        "quick_fix_label": "Dismiss",
        "quick_fix_type": "dismiss_anomaly",
        "followup_label": "Schedule Follow-Up",
        "followup_note": "Investigate {anomaly_type} for {uid}",
    },
    "No Overdue Follow-ups": {
        "description": "{count} follow-ups past due: {summaries}",
        "why": "Overdue items represent commitments to clients or carriers",
        "action_type": "quick_fix",
        "action_label": "Reschedule / Complete",
        "quick_fix_type": "reschedule_or_complete",
    },
}

REVIEW_MODE_FIELDS: dict[str, dict] = {
    "Data Health": {
        "fields_shown": "Each missing/incomplete field from the health scorer",
        "editable": True,
        "edit_type": "contenteditable",
        "context": ["health_score", "field_completeness_pct"],
    },
    "Recent Activity": {
        "fields_shown": "Last activity date, type, days since",
        "editable": False,
        "context": ["activity_count_90d", "renewal_proximity"],
    },
    "No Open Anomalies": {
        "fields_shown": "Anomaly type, description, created date, linked entity",
        "editable": False,
        "context": ["anomaly_rule", "severity"],
    },
    "No Overdue Follow-ups": {
        "fields_shown": "Follow-up description, original due date, days overdue",
        "editable": True,
        "edit_type": "reschedule_or_complete",
        "context": ["linked_policy_client", "created_by"],
    },
    "Missing Primary Contact": {
        "fields_shown": "Contact list, 'No primary contact' alert",
        "editable": True,
        "edit_type": "set_primary_button",
        "context": ["client_name", "total_contact_count"],
    },
    "Stale Issue": {
        "fields_shown": "Issue title, type, last activity date, days stale",
        "editable": False,
        "context": ["linked_policy", "severity", "created_date"],
    },
    "Milestone Drifted": {
        "fields_shown": "Milestone name, profile, health status",
        "editable": True,
        "edit_type": "date_picker",
        "edit_field": "projected_date",
        "context": ["policy_uid", "days_to_expiration", "ideal_date"],
    },
    "Renewal Issue Missing": {
        "fields_shown": "Policy UID, expiration date, current renewal status",
        "editable": False,
        "action_type": "create_renewal_issue",
        "context": ["milestone_completion_pct", "carrier"],
    },
}

WALKTHROUGH_SECTIONS = [
    {
        "key": "vacation_prep",
        "label": "Vacation Prep",
        "conditional": True,
        "condition_field": "vacation_return_date",
        "estimated_minutes": "5-10",
        "route": "/review/vacation-checklist",
    },
    {
        "key": "this_week",
        "label": "This Week",
        "auto_complete": True,
        "estimated_minutes": "1-2",
        "route": "/review/this-week",
    },
    {
        "key": "inbox",
        "label": "Inbox",
        "estimated_minutes": "2-5",
        "route": "/review/section/inbox",
    },
    {
        "key": "overdue_followups",
        "label": "Overdue Follow-Ups",
        "estimated_minutes": "3-5",
        "route": "/review/section/overdue_followups",
    },
    {
        "key": "upcoming_renewals",
        "label": "Upcoming Renewals",
        "estimated_minutes": "5-10",
        "route": "/review/section/upcoming_renewals",
    },
    {
        "key": "open_issues",
        "label": "Open Issues",
        "estimated_minutes": "3-5",
        "route": "/review/section/open_issues",
    },
    {
        "key": "client_health",
        "label": "Client Health",
        "estimated_minutes": "3-5",
        "route": "/review/section/client_health",
    },
    {
        "key": "policy_audit",
        "label": "Policy Audit",
        "estimated_minutes": "3-5",
        "route": "/review/section/policy_audit",
    },
]
