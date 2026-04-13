"""Focus Queue: unified scoring and ranking for Action Center items."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta

import policydb.config as cfg
from policydb.queries import (
    auto_close_stale_followups,
    get_all_followups,
    get_insurance_deadline_suggestions,
    get_suggested_followups,
)

logger = logging.getLogger("policydb.focus_queue")


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
            days_since_activity = max(0, (today - d).days)
        except ValueError:
            pass

    # Map disposition to accountability.  Use the raw DB value (may be None/empty),
    # then fall back to _resolve_accountability if not already set.
    accountability = item.get("accountability")
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
        "cn_number": item.get("cn_number", ""),
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
        "email_to": item.get("email_to"),
        "email_snippet": item.get("email_snippet"),
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
        "email_to": item.get("email_to"),
        "email_snippet": item.get("email_snippet"),
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
        "cn_number": item.get("cn_number", ""),
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
        "email_to": None,
        "email_snippet": None,
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
        "email_to": None,
        "email_snippet": None,
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
        "cn_number": item.get("cn_number", ""),
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
        "email_to": None,
        "email_snippet": None,
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
    """Normalize an open issue for the Focus Queue.

    Issues WITH a due_date use that directly.  Issues WITHOUT a due_date
    get a synthetic deadline derived from SLA: created_at + sla_days.
    This ensures dateless issues naturally escalate as they age past SLA
    rather than sitting invisible forever.
    """
    due = item.get("due_date", "") or item.get("follow_up_date", "")
    days_until = None
    is_synthetic = False
    if due:
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
            days_until = (d - today).days
        except ValueError:
            pass
    else:
        # Synthetic deadline from SLA: created_at + sla_days
        is_synthetic = True
        created = item.get("created_at", "")
        severity = item.get("issue_severity", "Normal")
        sla_map = {s["label"]: s.get("sla_days", 7)
                   for s in cfg.get("issue_severities", [])}
        sla_days = sla_map.get(severity, 7)
        if created:
            try:
                created_date = datetime.strptime(created[:10], "%Y-%m-%d").date()
                synthetic_due = created_date + timedelta(days=sla_days)
                days_until = (synthetic_due - today).days
                due = synthetic_due.isoformat()
            except ValueError:
                pass

    return {
        "id": item.get("id"),
        "kind": "issue",
        "source": "issue",
        "source_label": "Issue",
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "cn_number": item.get("cn_number", ""),
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
        "is_synthetic_deadline": is_synthetic,
        "prev_disposition": None,
        "prev_days_ago": None,
        "contact_person": None,
        "contact_email": None,
        "reason_line": "",
        "details": "",
        "inbox_id": None,
        "is_matched": None,
        "email_from": None,
        "email_to": None,
        "email_snippet": None,
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
    """Get ALL open issues for Focus Queue — with or without due dates.

    Issues without explicit dates get a synthetic deadline computed from
    SLA days so they naturally escalate as they age past SLA.
    """
    rows = conn.execute("""
        SELECT a.id, a.subject, a.issue_uid, a.issue_severity,
               a.due_date, a.follow_up_date, a.client_id,
               a.created_at,
               c.name AS client_name,
               p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.item_kind = 'issue'
          AND a.issue_status NOT IN ('Resolved', 'Closed')
        ORDER BY COALESCE(a.due_date, a.follow_up_date, a.created_at) ASC
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
          AND julianday(p.target_completion) - julianday('now') > -60
          AND p.project_type != 'Location'
          AND c.archived = 0
          AND p.status NOT IN ('Complete', 'Closed', 'Cancelled')
        ORDER BY p.target_completion ASC
    """, (window_days,)).fetchall()
    return [dict(r) for r in rows]


def get_approaching_opportunities(conn: sqlite3.Connection, window_days: int = 90) -> list[dict]:
    """Get ALL active opportunities for Focus Queue.

    Opportunities WITH target dates are filtered by window_days.
    Opportunities WITHOUT dates are always included so they can't
    silently vanish — they get a staleness-based deadline in the
    normalizer.
    """
    rows = conn.execute("""
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier,
               p.target_effective_date, p.expiration_date,
               p.client_id, p.renewal_status, p.created_at,
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
          AND (
              -- Dated opportunities within window (extended to 30d past-due)
              (COALESCE(p.target_effective_date, p.expiration_date) IS NOT NULL
               AND julianday(COALESCE(p.target_effective_date, p.expiration_date))
                   - julianday('now') <= ?
               AND julianday(COALESCE(p.target_effective_date, p.expiration_date))
                   - julianday('now') > -30)
              OR
              -- Dateless opportunities — always include
              COALESCE(p.target_effective_date, p.expiration_date) IS NULL
          )
        ORDER BY COALESCE(deadline, p.created_at) ASC
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
        "email_to": None,
        "email_snippet": None,
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
    """Normalize an opportunity for the Focus Queue.

    Opportunities WITH a target date use that directly.
    Opportunities WITHOUT a target date get a synthetic deadline based
    on staleness: 14 days after last activity (or creation if no activity).
    This ensures dateless opportunities escalate into view rather than
    hiding forever.
    """
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

    # Synthetic deadline for dateless opportunities
    is_synthetic = False
    if not deadline:
        is_synthetic = True
        stale_days = cfg.get("opportunity_staleness_days", 14)
        anchor = last_act or (item.get("created_at") or "")
        if anchor:
            try:
                anchor_date = datetime.strptime(anchor[:10], "%Y-%m-%d").date()
                synthetic_due = anchor_date + timedelta(days=stale_days)
                deadline = synthetic_due.isoformat()
                days_remaining = (synthetic_due - today).days
            except ValueError:
                pass

    return {
        "id": item.get("id"),
        "kind": "opportunity",
        "source": "opportunity",
        "source_label": "Opportunity",
        "is_synthetic_deadline": is_synthetic,
        "client_id": item.get("client_id"),
        "client_name": item.get("client_name", ""),
        "cn_number": item.get("cn_number", ""),
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
        "email_to": None,
        "email_snippet": None,
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


def _dedup_activity_siblings(all_items: list[dict]) -> tuple[list[dict], int]:
    """Collapse multiple pending activity follow-ups on the same policy.

    When two or more normalized Focus Queue items share the same ``policy_uid``
    and both have ``kind == 'followup'`` with ``source in ('activity','project')``,
    keep the one with the *earliest* ``follow_up_date`` (the most urgent /
    oldest-unresolved), tie-breaking on highest id (most recently written).
    Drop the rest.

    Why earliest and not latest: the write-side invariant (Step 1 of this
    cleanup) guarantees new follow-ups supersede old ones, so any surviving
    duplicates are legacy data where the user manually created overlapping
    rows. In that case the oldest one represents the real bottleneck, and
    it is also more likely to survive the downstream time-horizon filter
    (``horizon_days <= 0`` drops future-dated items).

    Returns ``(kept_items, dropped_count)``.
    """
    # Group activity/project followups by policy_uid
    by_policy: dict[str, list[dict]] = {}
    other: list[dict] = []
    for item in all_items:
        if (item.get("kind") == "followup"
                and item.get("source") in ("activity", "project")
                and item.get("policy_uid")):
            by_policy.setdefault(item["policy_uid"], []).append(item)
        else:
            other.append(item)

    kept: list[dict] = list(other)
    dropped = 0
    for puid, group in by_policy.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        # Winner: earliest follow_up_date (most urgent). Put items with no
        # date at the end. Tie-break on largest id (most recently written).
        def _sort_key(x: dict) -> tuple:
            fu = x.get("follow_up_date") or "9999-99-99"
            return (fu, -(x.get("id") or 0))
        group.sort(key=_sort_key)
        kept.append(group[0])
        dropped += len(group) - 1

    return kept, dropped


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

    # Run the stale sweeper before gathering sources so users who never visit
    # the legacy Follow-ups tab still get stale auto-close. Idempotent —
    # already safe to call from multiple request paths.
    try:
        auto_close_stale_followups(conn)
    except Exception:
        logger.debug("auto_close_stale_followups failed", exc_info=True)

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

    # --- Enrich activity follow-ups with linked issue data ---
    # _normalize_followup hard-codes linked_issue_uid=None because
    # get_all_followups doesn't JOIN the issue row. Without this enrichment,
    # Dedup Pass 3 (suppress issue rows whose follow-up already shows) is dead
    # code and duplicate rows appear: one for the activity, one for the issue.
    activity_ids = [
        item["id"] for item in all_items
        if item.get("kind") == "followup"
        and item.get("source") in ("activity", "project")
        and item.get("id")
    ]
    if activity_ids:
        ph = ",".join("?" * len(activity_ids))
        issue_link_rows = conn.execute(
            f"""SELECT a.id AS activity_id, a.issue_id,
                       iss.issue_uid AS linked_issue_uid,
                       iss.subject AS linked_issue_subject,
                       iss.issue_severity AS linked_issue_severity
                FROM activity_log a
                LEFT JOIN activity_log iss ON a.issue_id = iss.id AND iss.item_kind = 'issue'
                WHERE a.id IN ({ph})""",
            activity_ids,
        ).fetchall()
        _issue_link_map = {r["activity_id"]: dict(r) for r in issue_link_rows}
        for item in all_items:
            if (item.get("kind") == "followup"
                    and item.get("source") in ("activity", "project")
                    and item.get("id")):
                link = _issue_link_map.get(item["id"])
                if link and link.get("linked_issue_uid"):
                    item["linked_issue_uid"] = link["linked_issue_uid"]
                    item["linked_issue_subject"] = link["linked_issue_subject"]
                    item["linked_issue_severity"] = link["linked_issue_severity"]

    # --- Dedup sibling activity follow-ups on the same policy ---
    # Defensive read-side fix: when two or more pending activity follow-ups
    # exist for the same policy, only the most recent survives into Focus.
    all_items, _sibling_dropped = _dedup_activity_siblings(all_items)
    if _sibling_dropped:
        logger.info(
            "Focus Queue: dropped %d duplicate sibling activity follow-up(s) at render time",
            _sibling_dropped,
        )

    # --- Auto-suggest contacts from policy team for items missing a contact ---
    _contact_cache: dict[str, tuple[str, str]] = {}  # policy_uid -> (name, email)
    for item in all_items:
        if item.get("contact_person") or item.get("contact_email"):
            continue
        puid = item.get("policy_uid")
        if not puid:
            continue
        if puid not in _contact_cache:
            row = conn.execute("""
                SELECT co.name, co.email FROM contact_policy_assignments cpa
                JOIN contacts co ON cpa.contact_id = co.id
                WHERE cpa.policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
                ORDER BY cpa.is_placement_colleague DESC, cpa.id ASC
                LIMIT 1
            """, (puid,)).fetchone()
            _contact_cache[puid] = (row["name"], row["email"]) if row else ("", "")
        name, email = _contact_cache[puid]
        if name:
            item["contact_person"] = name
        if email:
            item["contact_email"] = email

    # --- Dedup: suppress lower-priority items when higher-priority exists ---
    # Priority: activity follow-up > issue > milestone > suggested > policy follow-up
    _covered_policies: dict[str, str] = {}  # policy_uid -> covering kind

    # Pass 1: activity follow-ups are highest priority
    for item in all_items:
        puid = item.get("policy_uid")
        if not puid:
            continue
        if item.get("kind") == "followup" and item.get("source") == "activity":
            _covered_policies[puid] = "activity"

    # Pass 2: issues cover policies not already covered by activities
    for item in all_items:
        puid = item.get("policy_uid")
        if not puid or puid in _covered_policies:
            continue
        if item.get("kind") == "issue":
            _covered_policies[puid] = "issue"

    # Pass 3: suppress issue items that have a linked follow-up
    _suppressed_issue_ids: set[int] = set()
    for item in all_items:
        if item.get("kind") == "followup" and item.get("linked_issue_uid"):
            for other in all_items:
                if (other.get("kind") == "issue"
                        and other.get("linked_issue_uid") == item["linked_issue_uid"]):
                    _suppressed_issue_ids.add(id(other))

    # Pass 4: filter
    deduped: list[dict] = []
    for item in all_items:
        puid = item.get("policy_uid")
        kind = item.get("kind")
        source = item.get("source")

        if id(item) in _suppressed_issue_ids:
            continue
        if kind == "followup" and source == "policy" and puid in _covered_policies:
            continue
        if kind == "suggested" and puid in _covered_policies:
            continue
        if kind == "milestone" and puid in _covered_policies:
            continue
        if kind == "opportunity" and puid in _covered_policies:
            continue

        deduped.append(item)

    all_items = deduped

    # --- Score all items ---
    for item in all_items:
        item["score"] = _score_item(item)
        item["context_line"] = _build_context_line(item)
        action, detail = _build_suggestion(item)
        item["suggested_action"] = action
        item["suggested_action_detail"] = detail

    # --- Assign display categories (3 user-facing buckets) ---
    # Action: things YOU need to do by a date
    # Advance: things to move forward proactively
    # Incoming: things to triage/process
    _CATEGORY_MAP = {
        "followup": "action",
        "issue": "action",
        "milestone": "action",
        "insurance_deadline": "action",
        "project_deadline": "action",
        "opportunity": "advance",
        "suggested": "advance",
        "inbox": "incoming",
    }
    for item in all_items:
        item["display_category"] = _CATEGORY_MAP.get(item.get("kind"), "action")

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
            # Negative values (overdue, all) use 7d default
            promote_window = max(horizon_days, 7) if horizon_days > 0 else 7

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
    # horizon_days semantics:
    #   -1   = "overdue" (only past-due items)
    #   0    = "today" (due today + overdue — never hide overdue)
    #   >0   = "next N days" (within N days + overdue — strictly additive)
    #   -999 = "all" (no filter)
    if horizon_days == -1:
        # Overdue only
        focus_items = [
            i for i in focus_items
            if i.get("days_until_deadline") is not None
            and i["days_until_deadline"] < 0
        ]
    elif horizon_days == 0:
        # Today: items due today + ALL overdue (never hide overdue)
        focus_items = [
            i for i in focus_items
            if i.get("days_until_deadline") is None  # no-deadline items (inbox) always show
            or i["days_until_deadline"] <= 0
        ]
    elif horizon_days > 0:
        # Next N days — INCLUDES overdue items (wider views are strictly additive)
        focus_items = [
            i for i in focus_items
            if i.get("days_until_deadline") is None
            or i["days_until_deadline"] <= horizon_days
        ]
    # else: horizon_days == -999 → "all", no filter

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
