"""Focus Queue: unified scoring and ranking for Action Center items."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import policydb.config as cfg
from policydb.queries import (
    get_all_followups,
    get_insurance_deadline_suggestions,
    get_suggested_followups,
)


# ---------------------------------------------------------------------------
# Item normalization — convert each source into a common dict shape
# ---------------------------------------------------------------------------

def _normalize_followup(item: dict, today: date) -> dict:
    """Normalize an activity/policy/project/client followup item."""
    fu_date = item.get("follow_up_date") or ""
    exp_date = item.get("expiration_date") or ""

    # Determine the deadline date (the date that matters for scoring)
    deadline = fu_date or exp_date or ""
    days_until = None
    if deadline:
        try:
            d = datetime.strptime(deadline, "%Y-%m-%d").date()
            days_until = (d - today).days
        except ValueError:
            pass

    # Days since last activity
    last_act = item.get("last_activity_date") or item.get("activity_date") or ""
    days_since_activity = None
    if last_act:
        try:
            d = datetime.strptime(last_act[:10], "%Y-%m-%d").date()
            days_since_activity = (today - d).days
        except ValueError:
            pass

    # Map disposition to accountability
    accountability = item.get("accountability") or "my_action"
    disposition = item.get("disposition") or ""
    if not accountability or accountability == "":
        accountability = _resolve_accountability(disposition)

    # Source label for display
    source = item.get("source", "activity")
    source_label_map = {
        "activity": "Follow-up",
        "project": "Project",
        "policy": "Renewal",
        "client": "Client",
    }
    source_label = source_label_map.get(source, "Follow-up")
    if item.get("is_opportunity"):
        source_label = "Opportunity"

    return {
        "id": item.get("id"),
        "kind": "followup",
        "source": source,
        "source_label": source_label,
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": item.get("policy_uid"),
        "policy_type": item.get("policy_type"),
        "carrier": item.get("carrier"),
        "subject": item.get("subject") or item.get("reason_line") or "",
        "follow_up_date": fu_date,
        "expiration_date": exp_date,
        "deadline_date": deadline,
        "days_until_deadline": days_until,
        "days_since_activity": days_since_activity,
        "accountability": accountability,
        "disposition": disposition,
        "severity": None,
        "escalation_tier": item.get("escalation_tier"),
        "nudge_count": item.get("nudge_count", 0),
        "cadence": item.get("cadence"),
        "is_milestone": item.get("is_milestone", False),
        "milestone_name": item.get("milestone_name"),
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "program_uid": item.get("program_uid"),
        "program_name": item.get("program_name"),
        "linked_issue_uid": item.get("linked_issue_uid"),
        "linked_issue_subject": item.get("linked_issue_subject"),
        "linked_issue_severity": item.get("linked_issue_severity"),
        "prev_disposition": item.get("prev_disposition"),
        "prev_days_ago": item.get("prev_days_ago"),
        "contact_person": item.get("contact_person"),
        "contact_email": item.get("contact_email"),
        "reason_line": item.get("reason_line", ""),
        "details": item.get("note_details") or item.get("details") or "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": item.get("email_from"),
        "email_subject": item.get("email_subject"),
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        # Pass through for completion flow
        "activity_type": item.get("activity_type"),
        "duration_hours": item.get("duration_hours"),
        "thread_id": item.get("thread_id"),
        "health": item.get("health"),
    }


def _normalize_inbox(item: dict, today: date) -> dict:
    """Normalize an inbox item for the Focus Queue."""
    is_matched = bool(item.get("client_id"))
    return {
        "id": item["id"],
        "kind": "inbox",
        "source": "inbox",
        "source_label": "Inbox",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": None,
        "policy_type": None,
        "carrier": None,
        "subject": item.get("email_subject") or item.get("content", "")[:120],
        "follow_up_date": None,
        "expiration_date": None,
        "deadline_date": item.get("created_at", "")[:10],
        "days_until_deadline": 0,  # Inbox items are always "now"
        "days_since_activity": None,
        "accountability": "my_action",
        "disposition": None,
        "severity": None,
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": None,
        "project_name": None,
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": item.get("contact_name"),
        "contact_email": None,
        "reason_line": "",
        "details": item.get("content", "")[:200] if item.get("content") else "",
        "inbox_id": item["id"],
        "is_matched": is_matched,
        "email_from": item.get("email_from"),
        "email_subject": item.get("email_subject"),
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


def _normalize_suggested(item: dict, today: date) -> dict:
    """Normalize a suggested followup (policy needing attention)."""
    exp_date = item.get("expiration_date", "")
    days_to = item.get("days_to_renewal")
    last_act = item.get("last_activity_date") or ""
    days_since = None
    if last_act:
        try:
            d = datetime.strptime(last_act[:10], "%Y-%m-%d").date()
            days_since = (today - d).days
        except ValueError:
            pass

    return {
        "id": item.get("policy_uid"),  # Use policy_uid as ID
        "kind": "suggested",
        "source": "suggested",
        "source_label": "Renewal",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": item.get("policy_uid"),
        "policy_type": item.get("policy_type"),
        "carrier": item.get("carrier"),
        "subject": f"{item.get('policy_type', '')} — needs follow-up scheduled",
        "follow_up_date": None,
        "expiration_date": exp_date,
        "deadline_date": exp_date,
        "days_until_deadline": days_to,
        "days_since_activity": days_since,
        "accountability": "my_action",
        "disposition": None,
        "severity": None,
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": None,
        "project_name": item.get("project_name"),
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


def _normalize_insurance_deadline(item: dict, today: date) -> dict:
    """Normalize an insurance deadline suggestion."""
    needed_by = item.get("insurance_needed_by", "")
    days_remaining = item.get("days_remaining")

    return {
        "id": item.get("project_id"),
        "kind": "insurance_deadline",
        "source": "insurance_deadline",
        "source_label": "Insurance Deadline",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": None,
        "policy_type": None,
        "carrier": None,
        "subject": item.get("subject") or f"Insurance needed — {item.get('project_name', '')}",
        "follow_up_date": None,
        "expiration_date": None,
        "deadline_date": needed_by,
        "days_until_deadline": days_remaining,
        "days_since_activity": None,
        "accountability": "my_action",
        "disposition": None,
        "severity": item.get("tier", "Normal"),
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


def _normalize_milestone(item: dict, today: date) -> dict:
    """Normalize a timeline milestone item."""
    proj_date = item.get("projected_date", "")
    days_until = None
    if proj_date:
        try:
            d = datetime.strptime(proj_date, "%Y-%m-%d").date()
            days_until = (d - today).days
        except ValueError:
            pass

    return {
        "id": item.get("id"),
        "kind": "milestone",
        "source": "milestone",
        "source_label": "Milestone",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": item.get("policy_uid"),
        "policy_type": item.get("policy_type"),
        "carrier": None,
        "subject": item.get("milestone_name", ""),
        "follow_up_date": None,
        "expiration_date": None,
        "deadline_date": proj_date,
        "days_until_deadline": days_until,
        "days_since_activity": None,
        "accountability": item.get("accountability", "my_action"),
        "disposition": None,
        "severity": None,
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": True,
        "milestone_name": item.get("milestone_name"),
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": item.get("health"),
    }


def _normalize_issue(item: dict, today: date) -> dict:
    """Normalize an open issue with a due date."""
    due = item.get("due_date", "")
    days_until = None
    if due:
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
            days_until = (d - today).days
        except ValueError:
            pass

    return {
        "id": item.get("id"),
        "kind": "issue",
        "source": "issue",
        "source_label": "Issue",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": item.get("policy_uid"),
        "policy_type": item.get("policy_type"),
        "carrier": None,
        "subject": item.get("subject", ""),
        "follow_up_date": item.get("follow_up_date"),
        "expiration_date": None,
        "deadline_date": due or item.get("follow_up_date", ""),
        "days_until_deadline": days_until,
        "days_since_activity": None,
        "accountability": "my_action",
        "disposition": None,
        "severity": item.get("issue_severity"),
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": None,
        "project_name": None,
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": item.get("issue_uid"),
        "linked_issue_subject": item.get("subject"),
        "linked_issue_severity": item.get("issue_severity"),
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


# ---------------------------------------------------------------------------
# Accountability resolution
# ---------------------------------------------------------------------------

def _resolve_accountability(disposition: str) -> str:
    """Map a disposition label to its accountability level."""
    if not disposition:
        return "my_action"
    dispositions = cfg.get("follow_up_dispositions", [])
    for d in dispositions:
        if d.get("label") == disposition:
            return d.get("accountability", "my_action")
    return "my_action"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_item(item: dict) -> float:
    """Compute focus score for a normalized item. Higher = more urgent."""
    weights = cfg.get("focus_score_weights", {
        "deadline_proximity": 40,
        "staleness": 25,
        "severity": 20,
        "overdue_multiplier": 15,
    })

    score = 0.0
    days = item.get("days_until_deadline")

    # 1. Deadline proximity: closer deadline = higher score
    if days is not None:
        if days <= 0:
            # Past due: base 100 + escalating overdue bonus
            score += weights["deadline_proximity"]
            score += weights["overdue_multiplier"] * min(abs(days), 60) / 10
        elif days <= 3:
            score += weights["deadline_proximity"] * 0.9
        elif days <= 7:
            score += weights["deadline_proximity"] * 0.7
        elif days <= 14:
            score += weights["deadline_proximity"] * 0.5
        elif days <= 30:
            score += weights["deadline_proximity"] * 0.3
        else:
            score += weights["deadline_proximity"] * 0.1

    # 2. Staleness: no recent activity = higher score
    days_since = item.get("days_since_activity")
    if days_since is not None:
        if days_since >= 30:
            score += weights["staleness"]
        elif days_since >= 14:
            score += weights["staleness"] * 0.7
        elif days_since >= 7:
            score += weights["staleness"] * 0.3

    # 3. Severity / source importance
    severity = item.get("severity")
    kind = item.get("kind")
    if severity == "Critical":
        score += weights["severity"]
    elif severity == "High" or severity == "Urgent":
        score += weights["severity"] * 0.7
    elif kind == "issue":
        score += weights["severity"] * 0.5
    elif kind == "inbox":
        score += weights["severity"] * 0.4  # Inbox items get moderate priority
    elif kind in ("suggested", "insurance_deadline"):
        score += weights["severity"] * 0.3

    return round(score, 1)


# ---------------------------------------------------------------------------
# Context lines and action suggestions
# ---------------------------------------------------------------------------

def _build_context_line(item: dict) -> str:
    """Build the 'why it's hot' context line for display."""
    parts = []
    days = item.get("days_until_deadline")
    kind = item.get("kind")

    if kind == "inbox":
        email_from = item.get("email_from", "")
        if email_from:
            parts.append(f"From: {email_from}")
        if item.get("is_matched"):
            parts.append(f"Auto-matched to {item.get('client_name', 'client')}")
        else:
            parts.append("Needs client assignment")
        return " · ".join(parts)

    # Urgency signal
    if days is not None:
        if days < 0:
            parts.append(f"{abs(days)} days overdue")
        elif days == 0:
            parts.append("Due today")
        elif days <= 7:
            parts.append(f"Due in {days} day{'s' if days != 1 else ''}")
        elif days <= 14:
            parts.append(f"Due in {days} days")

    # Expiration proximity
    exp = item.get("expiration_date")
    if exp and kind not in ("insurance_deadline", "project_deadline", "opportunity"):
        try:
            exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
            exp_days = (exp_d - date.today()).days
            if 0 < exp_days <= 14:
                parts.append(f"Expires in {exp_days}d")
            elif exp_days <= 0:
                parts.append(f"Expired {abs(exp_days)}d ago")
        except ValueError:
            pass

    # Staleness
    days_since = item.get("days_since_activity")
    if days_since and days_since >= 7:
        parts.append(f"Last activity: {days_since}d ago")

    # Linked issue
    if item.get("linked_issue_severity") in ("Critical", "High"):
        parts.append(f"{item['linked_issue_severity']} issue linked")

    if not parts:
        if item.get("reason_line"):
            return item["reason_line"]
        return ""

    return " · ".join(parts)


def _build_suggestion(item: dict) -> tuple[str, str]:
    """Build (short_action, detailed_suggestion) for Guide Me mode.

    Returns:
        (button_label, full_suggestion_text)
    """
    kind = item.get("kind")
    days = item.get("days_until_deadline")

    if kind == "inbox":
        if item.get("is_matched"):
            return ("Log & Reply", f"Log this email as activity for {item.get('client_name', 'client')} and reply.")
        return ("Link to Client", "Assign this to a client so it enters your workflow.")

    if kind == "suggested":
        return ("Schedule Follow-up", f"Create a follow-up for {item.get('policy_type', 'this policy')} — renewal approaching.")

    if kind == "insurance_deadline":
        return ("Review Insurance", f"Check insurance requirements for {item.get('project_name', 'project')}.")

    if kind == "project_deadline":
        return ("Review Project", f"Check progress on {item.get('project_name', 'project')} — target completion approaching.")

    if kind == "opportunity":
        return ("Advance Opportunity", f"Move {item.get('policy_type', 'opportunity')} forward for {item.get('client_name', 'client')}.")

    if kind == "milestone":
        name = item.get("milestone_name", "milestone")
        return (f"Complete: {name}", f"Mark '{name}' as done for {item.get('client_name', '')} {item.get('policy_type', '')}.")

    if kind == "issue":
        if days is not None and days <= 2:
            return ("Escalate", f"SLA breach approaching on {item.get('subject', 'issue')}. Consider escalating.")
        return ("Update Issue", f"Review and update status of {item.get('subject', 'issue')}.")

    # Regular followup
    accountability = item.get("accountability", "my_action")
    disposition = item.get("disposition", "")

    if accountability == "waiting_external" or "Waiting" in disposition:
        who = "carrier" if "Carrier" in disposition else "client" if "Client" in disposition else "contact"
        return ("Send Nudge", f"Follow up with {who} on {item.get('subject', 'this item')}. Last contact: {item.get('days_since_activity', '?')} days ago.")

    if days is not None and days <= 0:
        contact = item.get("contact_person") or item.get("contact_email") or ""
        contact_str = f" Contact: {contact}." if contact else ""
        return ("Follow Up", f"Follow up on {item.get('subject', 'this item')}.{contact_str}")

    return ("Review", f"Review {item.get('subject', 'this item')} for {item.get('client_name', '')}.")


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def get_pending_inbox(conn: sqlite3.Connection) -> list[dict]:
    """Get pending inbox items with client/contact names."""
    rows = conn.execute("""
        SELECT i.*, c.name AS client_name, ct.name AS contact_name
        FROM inbox i
        LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN contacts ct ON i.contact_id = ct.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_open_issues_with_due(conn: sqlite3.Connection) -> list[dict]:
    """Get open issues that have due dates, for Focus Queue."""
    rows = conn.execute("""
        SELECT a.id, a.subject, a.issue_uid, a.issue_severity,
               a.due_date, a.follow_up_date, a.client_id,
               c.name AS client_name,
               p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.item_kind = 'issue'
          AND a.issue_status NOT IN ('Resolved', 'Closed')
          AND (a.due_date IS NOT NULL OR a.follow_up_date IS NOT NULL)
        ORDER BY COALESCE(a.due_date, a.follow_up_date) ASC
    """).fetchall()
    return [dict(r) for r in rows]


def get_overdue_milestones(conn: sqlite3.Connection) -> list[dict]:
    """Get milestones that are due or have prep alerts, for Focus Queue."""
    today_str = date.today().isoformat()
    rows = conn.execute("""
        SELECT pt.id, pt.milestone_name, pt.projected_date, pt.prep_alert_date,
               pt.accountability, pt.health,
               p.policy_uid, p.policy_type, p.client_id,
               c.name AS client_name,
               p.project_id, pr.name AS project_name
        FROM policy_timeline pt
        JOIN policies p ON pt.policy_uid = p.policy_uid
        JOIN clients c ON p.client_id = c.id
        LEFT JOIN projects pr ON p.project_id = pr.id
        WHERE pt.completed_date IS NULL
          AND (pt.projected_date <= ? OR pt.prep_alert_date <= ?)
        ORDER BY pt.projected_date ASC
    """, (today_str, today_str)).fetchall()
    return [dict(r) for r in rows]


def get_approaching_projects(conn: sqlite3.Connection, window_days: int = 30) -> list[dict]:
    """Get projects with approaching target_completion dates."""
    today_str = date.today().isoformat()
    rows = conn.execute("""
        SELECT p.id AS project_id, p.name AS project_name, p.target_completion,
               p.status AS project_stage, p.client_id, p.start_date,
               c.name AS client_name,
               CAST(julianday(p.target_completion) - julianday('now') AS INTEGER) AS days_remaining
        FROM projects p
        JOIN clients c ON p.client_id = c.id
        WHERE p.target_completion IS NOT NULL
          AND julianday(p.target_completion) - julianday('now') <= ?
          AND julianday(p.target_completion) - julianday('now') > -30
          AND p.project_type != 'Location'
          AND c.archived = 0
          AND p.status NOT IN ('Complete', 'Closed', 'Cancelled')
        ORDER BY p.target_completion ASC
    """, (window_days,)).fetchall()
    return [dict(r) for r in rows]


def get_approaching_opportunities(conn: sqlite3.Connection, window_days: int = 90) -> list[dict]:
    """Get opportunities with approaching target dates."""
    rows = conn.execute("""
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier,
               p.target_effective_date, p.expiration_date,
               p.client_id, p.renewal_status,
               c.name AS client_name,
               COALESCE(p.target_effective_date, p.expiration_date) AS deadline,
               CAST(julianday(COALESCE(p.target_effective_date, p.expiration_date))
                    - julianday('now') AS INTEGER) AS days_remaining,
               (SELECT MAX(a.activity_date) FROM activity_log a
                WHERE a.policy_id = p.id) AS last_activity_date
        FROM policies p
        JOIN clients c ON p.client_id = c.id
        WHERE p.is_opportunity = 1
          AND p.archived = 0
          AND COALESCE(p.target_effective_date, p.expiration_date) IS NOT NULL
          AND julianday(COALESCE(p.target_effective_date, p.expiration_date))
              - julianday('now') <= ?
          AND julianday(COALESCE(p.target_effective_date, p.expiration_date))
              - julianday('now') > -14
        ORDER BY deadline ASC
    """, (window_days,)).fetchall()
    return [dict(r) for r in rows]


def _normalize_project_deadline(item: dict, today: date) -> dict:
    """Normalize a project with approaching target_completion."""
    target = item.get("target_completion", "")
    days_remaining = item.get("days_remaining")

    return {
        "id": item.get("project_id"),
        "kind": "project_deadline",
        "source": "project_deadline",
        "source_label": "Project",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": None,
        "policy_type": None,
        "carrier": None,
        "subject": f"{item.get('project_name', '')} — target completion approaching",
        "follow_up_date": None,
        "expiration_date": None,
        "deadline_date": target,
        "days_until_deadline": days_remaining,
        "days_since_activity": None,
        "accountability": "my_action",
        "disposition": None,
        "severity": "High" if days_remaining is not None and days_remaining <= 7 else None,
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": item.get("project_id"),
        "project_name": item.get("project_name"),
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


def _normalize_opportunity(item: dict, today: date) -> dict:
    """Normalize an opportunity with approaching target date."""
    deadline = item.get("deadline", "") or ""
    days_remaining = item.get("days_remaining")
    last_act = item.get("last_activity_date") or ""
    days_since = None
    if last_act:
        try:
            d = datetime.strptime(last_act[:10], "%Y-%m-%d").date()
            days_since = (today - d).days
        except ValueError:
            pass

    return {
        "id": item.get("id"),
        "kind": "opportunity",
        "source": "opportunity",
        "source_label": "Opportunity",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "policy_uid": item.get("policy_uid"),
        "policy_type": item.get("policy_type"),
        "carrier": item.get("carrier"),
        "subject": f"{item.get('policy_type', '')} opportunity — {item.get('renewal_status', 'in progress')}",
        "follow_up_date": None,
        "expiration_date": None,
        "deadline_date": deadline,
        "days_until_deadline": days_remaining,
        "days_since_activity": days_since,
        "accountability": "my_action",
        "disposition": None,
        "severity": None,
        "escalation_tier": None,
        "nudge_count": 0,
        "cadence": None,
        "is_milestone": False,
        "milestone_name": None,
        "project_id": None,
        "project_name": None,
        "program_uid": None,
        "program_name": None,
        "linked_issue_uid": None,
        "linked_issue_subject": None,
        "linked_issue_severity": None,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_subject": None,
        "score": 0.0,
        "context_line": "",
        "suggested_action": "",
        "suggested_action_detail": "",
        "activity_type": None,
        "duration_hours": None,
        "thread_id": None,
        "health": None,
    }


def build_focus_queue(
    conn: sqlite3.Connection,
    horizon_days: int = 0,
    client_id: int = 0,
) -> tuple[list[dict], list[dict], dict]:
    """Build the Focus Queue and Waiting list.

    Args:
        conn: SQLite connection
        horizon_days: Time horizon filter. 0 = today, 7 = this week, 14 = next 2 weeks.
                      Items due within this window are included.
        client_id: Optional client filter (0 = all clients)

    Returns:
        (focus_items, waiting_items, stats)
        - focus_items: ranked list of items needing user action
        - waiting_items: items waiting on others, sorted by days waiting
        - stats: {focus_count, waiting_count, nudge_alert_count}
    """
    today = date.today()
    excluded = cfg.get("renewal_statuses_excluded", [])
    auto_promote_days = cfg.get("focus_auto_promote_days", 14)
    nudge_alert_days = cfg.get("focus_nudge_alert_days", 10)

    client_ids = [client_id] if client_id else None

    # --- Gather from all sources ---
    overdue_raw, upcoming_raw = get_all_followups(conn, window=max(horizon_days, 30), client_ids=client_ids)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded, client_ids=client_ids)
    insurance = get_insurance_deadline_suggestions(conn, client_ids=client_ids)
    inbox_items = get_pending_inbox(conn)
    issues = get_open_issues_with_due(conn)
    milestones = get_overdue_milestones(conn)
    projects = get_approaching_projects(conn, window_days=max(horizon_days, 30))
    opportunities = get_approaching_opportunities(conn, window_days=max(horizon_days, 90))

    # Filter by client if needed
    if client_id:
        inbox_items = [i for i in inbox_items if i.get("client_id") == client_id or not i.get("client_id")]
        issues = [i for i in issues if i.get("client_id") == client_id]
        milestones = [m for m in milestones if m.get("client_id") == client_id]
        projects = [p for p in projects if p.get("client_id") == client_id]
        opportunities = [o for o in opportunities if o.get("client_id") == client_id]

    # --- Normalize all items ---
    all_items: list[dict] = []

    for item in overdue_raw + upcoming_raw:
        all_items.append(_normalize_followup(item, today))

    for item in suggested:
        all_items.append(_normalize_suggested(item, today))

    for item in insurance:
        all_items.append(_normalize_insurance_deadline(item, today))

    for item in inbox_items:
        all_items.append(_normalize_inbox(item, today))

    for item in issues:
        all_items.append(_normalize_issue(item, today))

    for item in milestones:
        all_items.append(_normalize_milestone(item, today))

    for item in projects:
        all_items.append(_normalize_project_deadline(item, today))

    for item in opportunities:
        all_items.append(_normalize_opportunity(item, today))

    # --- Score all items ---
    for item in all_items:
        item["score"] = _score_item(item)
        item["context_line"] = _build_context_line(item)
        action, detail = _build_suggestion(item)
        item["suggested_action"] = action
        item["suggested_action_detail"] = detail

    # --- Split into Focus vs Waiting ---
    focus_items: list[dict] = []
    waiting_items: list[dict] = []

    for item in all_items:
        acc = item.get("accountability", "my_action")
        days = item.get("days_until_deadline")

        if acc == "waiting_external":
            # Check expiration date — deadline approaching overrides waiting status
            exp = item.get("expiration_date") or ""
            exp_days = None
            if exp:
                try:
                    exp_days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
                except ValueError:
                    pass

            promote = False
            promote_reason = ""

            # Promotion window matches the time horizon you're looking at
            # "Today" = 7d default, "This Week" = 7d, "Next 2 Weeks" = 14d, etc.
            promote_window = max(horizon_days, 7)

            # Promote if been waiting too long
            if days is not None and days <= -auto_promote_days:
                promote = True
                promote_reason = f"Waiting {abs(days)} days — consider nudging"
            # Promote if deadline/expiration within the horizon window
            elif exp_days is not None and exp_days <= promote_window:
                promote = True
                if exp_days <= 0:
                    promote_reason = f"⚠ Expired {abs(exp_days)}d ago — still waiting"
                else:
                    promote_reason = f"⚠ Expires in {exp_days}d — still waiting"
            # Promote if follow-up date is overdue
            elif days is not None and days <= 0:
                promote = True
                promote_reason = f"Overdue — still waiting"
            # Promote if follow-up date falls within horizon
            elif days is not None and days <= promote_window and horizon_days > 0:
                promote = True
                promote_reason = f"Due in {days}d — still waiting"

            if promote:
                item["context_line"] = promote_reason + (" · " + item["context_line"] if item["context_line"] else "")
                if "Expires" in promote_reason or "Overdue" in promote_reason:
                    item["suggested_action"] = "Escalate"
                else:
                    item["suggested_action"] = "Send Nudge"
                focus_items.append(item)
            else:
                waiting_items.append(item)
        elif acc == "scheduled":
            # Scheduled items go to waiting sidebar unless overdue
            if days is not None and days <= 0:
                focus_items.append(item)
            else:
                waiting_items.append(item)
        else:
            # my_action → focus queue
            focus_items.append(item)

    # --- Apply time horizon filter ---
    if horizon_days > 0:
        focus_items = [
            i for i in focus_items
            if i.get("days_until_deadline") is None
            or i["days_until_deadline"] <= horizon_days
        ]

    # --- Sort ---
    focus_items.sort(key=lambda x: -x["score"])
    waiting_items.sort(key=lambda x: x.get("days_until_deadline") or 0)  # Most overdue first

    # --- Stats ---
    nudge_alert_count = sum(
        1 for w in waiting_items
        if w.get("days_until_deadline") is not None
        and w["days_until_deadline"] <= -nudge_alert_days
    )

    stats = {
        "focus_count": len(focus_items),
        "waiting_count": len(waiting_items),
        "nudge_alert_count": nudge_alert_count,
    }

    return focus_items, waiting_items, stats
