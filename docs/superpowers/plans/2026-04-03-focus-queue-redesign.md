# Focus Queue: Action Center Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 8-bucket Follow-ups tab and separate Inbox processing with a single ranked Focus Queue + Waiting Sidebar, with Guide Me mode and time horizon control.

**Architecture:** New `focus_queue.py` module handles scoring and data aggregation from all existing sources (`get_all_followups`, `get_suggested_followups`, `get_insurance_deadline_suggestions`, milestones, inbox). Templates are new files; existing followup/inbox templates remain but are superseded. The page.html layout switches to Focus Queue as default view with secondary tabs in a "More" menu.

**Tech Stack:** FastAPI, Jinja2, HTMX, Tailwind CSS, SQLite

**Spec:** `docs/superpowers/specs/2026-04-03-focus-queue-redesign.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/policydb/focus_queue.py` | **Create** | Scoring model, data aggregation, action suggestions |
| `src/policydb/config.py` | Modify | Add `focus_score_weights` defaults |
| `src/policydb/web/routes/action_center.py` | Modify | New Focus Queue endpoint, updated page handler |
| `src/policydb/web/templates/action_center/page.html` | Modify | New layout: Focus Queue + Waiting Sidebar default, "More" tab menu |
| `src/policydb/web/templates/action_center/_focus_queue.html` | **Create** | Focus Queue item list with inline actions |
| `src/policydb/web/templates/action_center/_waiting_sidebar.html` | **Create** | Waiting sidebar panel |
| `src/policydb/web/templates/action_center/_focus_item.html` | **Create** | Single Focus Queue item row (macro partial) |
| `src/policydb/web/templates/action_center/_sidebar.html` | Modify | Update stats to reflect Focus Queue counts |

---

### Task 1: Config — Add Focus Score Weights

**Files:**
- Modify: `src/policydb/config.py` (add to `_DEFAULTS` dict)

- [ ] **Step 1: Add focus_score_weights to _DEFAULTS**

In `src/policydb/config.py`, add after the `stale_threshold_days` entry in `_DEFAULTS`:

```python
    "focus_score_weights": {
        "deadline_proximity": 40,    # Weight for days-until-deadline (closer = higher)
        "staleness": 25,             # Weight for days-since-last-activity
        "severity": 20,              # Weight for issue severity / source importance
        "overdue_multiplier": 15,    # Extra weight for items past due
    },
    "focus_auto_promote_days": 14,   # Waiting items auto-promote to Focus after this many days
    "focus_nudge_alert_days": 10,    # Yellow alert threshold for waiting items
```

- [ ] **Step 2: Verify config loads**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "import policydb.config as cfg; print(cfg.get('focus_score_weights'))"`

Expected: `{'deadline_proximity': 40, 'staleness': 25, 'severity': 20, 'overdue_multiplier': 15}`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/config.py
git commit -m "feat: add focus score weights config for Focus Queue redesign"
```

---

### Task 2: Focus Queue Scoring Module

**Files:**
- Create: `src/policydb/focus_queue.py`

This is the core backend module. It:
1. Aggregates items from all sources into a uniform shape
2. Scores each item
3. Splits into focus_items (my_action) and waiting_items (waiting_external)
4. Generates action suggestions

- [ ] **Step 1: Create focus_queue.py with item normalization and scoring**

Create `src/policydb/focus_queue.py`:

```python
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

    if days is not None:
        if days < 0:
            parts.append(f"{abs(days)} days overdue")
        elif days == 0:
            parts.append("Due today")
        elif days <= 7:
            deadline = item.get("deadline_date", "")
            parts.append(f"Due in {days} day{'s' if days != 1 else ''}")
        elif days <= 14:
            parts.append(f"Due in {days} days")

    exp = item.get("expiration_date")
    if exp and kind != "insurance_deadline":
        try:
            exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
            exp_days = (exp_d - date.today()).days
            if 0 < exp_days <= 14:
                parts.append(f"Expires in {exp_days}d")
            elif exp_days <= 0:
                parts.append(f"Expired {abs(exp_days)}d ago")
        except ValueError:
            pass

    days_since = item.get("days_since_activity")
    if days_since and days_since >= 7:
        parts.append(f"Last activity: {days_since} days ago")

    if item.get("linked_issue_severity") in ("Critical", "High"):
        parts.append(f"{item['linked_issue_severity']} issue linked")

    if not parts:
        if item.get("reason_line"):
            return item["reason_line"]
        return item.get("subject", "")

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
               pr.id AS project_id, pr.name AS project_name
        FROM policy_timeline pt
        JOIN policies p ON pt.policy_id = p.id
        JOIN clients c ON p.client_id = c.id
        LEFT JOIN projects pr ON pt.project_id = pr.id
        WHERE pt.completed_date IS NULL
          AND (pt.projected_date <= ? OR pt.prep_alert_date <= ?)
        ORDER BY pt.projected_date ASC
    """, (today_str, today_str)).fetchall()
    return [dict(r) for r in rows]


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

    # Filter inbox by client if needed
    if client_id:
        inbox_items = [i for i in inbox_items if i.get("client_id") == client_id or not i.get("client_id")]
        issues = [i for i in issues if i.get("client_id") == client_id]
        milestones = [m for m in milestones if m.get("client_id") == client_id]

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
            # Auto-promote stale waiting items to focus
            if days is not None and days <= -auto_promote_days:
                item["context_line"] = f"Waiting {abs(days)} days — consider nudging · " + item["context_line"]
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
```

- [ ] **Step 2: Verify module imports correctly**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.focus_queue import build_focus_queue; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/focus_queue.py
git commit -m "feat: add focus_queue module with scoring, normalization, and suggestion engine"
```

---

### Task 3: Focus Queue Template — Item Row

**Files:**
- Create: `src/policydb/web/templates/action_center/_focus_item.html`

This is the reusable row template for a single Focus Queue item. Each item shows: source badge, client/subject, context line, and action button. In Guide Me mode, the top item gets an expanded suggestion panel.

- [ ] **Step 1: Create _focus_item.html**

Create `src/policydb/web/templates/action_center/_focus_item.html`:

```html
{# Single Focus Queue item row.
   Variables: item (dict), guide_me (bool), is_highlighted (bool), dispositions (list)
#}
{% set border_colors = {
    'inbox': 'border-blue-500',
    'suggested': 'border-amber-500',
    'milestone': 'border-purple-500',
    'issue': 'border-red-500',
    'insurance_deadline': 'border-orange-500',
} %}
{% set badge_colors = {
    'Follow-up': 'bg-gray-100 text-gray-700',
    'Renewal': 'bg-red-50 text-red-700',
    'Inbox': 'bg-blue-50 text-blue-700',
    'Milestone': 'bg-purple-50 text-purple-700',
    'Issue': 'bg-red-50 text-red-700',
    'Insurance Deadline': 'bg-orange-50 text-orange-700',
    'Opportunity': 'bg-teal-50 text-teal-700',
    'Project': 'bg-indigo-50 text-indigo-700',
    'Client': 'bg-gray-50 text-gray-600',
} %}

{% set border = border_colors.get(item.kind, 'border-gray-400') %}
{% set badge_color = badge_colors.get(item.source_label, 'bg-gray-100 text-gray-700') %}
{% set row_id = item.kind ~ '-' ~ item.id %}

<div id="fq-{{ row_id }}"
     class="bg-white rounded-lg p-3.5 mb-2 border-l-4 {{ border }} transition-all
            {% if is_highlighted %}ring-2 ring-purple-300 shadow-md{% else %}shadow-sm hover:shadow{% endif %}"
     data-score="{{ item.score }}">

  {# Top row: badge + title + action buttons #}
  <div class="flex justify-between items-start gap-3">
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2 mb-1 flex-wrap">
        <span class="text-[11px] font-semibold px-2 py-0.5 rounded {{ badge_color }} whitespace-nowrap">
          {{ item.source_label | upper }}
        </span>
        <span class="text-sm font-semibold text-[#3D3C37] truncate">
          {% if item.client_name %}{{ item.client_name }}{% endif %}
          {% if item.policy_type %} — {{ item.policy_type }}{% endif %}
          {% if item.carrier %} <span class="text-gray-400 font-normal text-xs">({{ item.carrier }})</span>{% endif %}
        </span>
        {% if item.is_milestone and item.health in ('at_risk', 'critical') %}
          <span class="text-[10px] px-1.5 py-0.5 rounded-full {% if item.health == 'critical' %}bg-red-100 text-red-700{% else %}bg-amber-100 text-amber-700{% endif %}">
            {{ item.health | replace('_', ' ') | title }}
          </span>
        {% endif %}
      </div>
      {# Subject line #}
      <div class="text-[13px] text-[#3D3C37] truncate">{{ item.subject }}</div>
      {# Context line (why it's hot) #}
      <div class="text-[12px] text-[#6B6860] mt-0.5">{{ item.context_line }}</div>
    </div>

    {# Action buttons #}
    <div class="flex gap-2 items-center flex-shrink-0 ml-3">
      {% if item.kind == 'inbox' and not item.is_matched %}
        {# Unmatched inbox: show client autocomplete inline #}
        <div class="flex items-center gap-1">
          <input type="text" placeholder="Client..."
                 class="text-xs border border-gray-200 rounded px-2 py-1 w-32 focus:border-blue-400 focus:outline-none"
                 id="fq-link-{{ item.inbox_id }}"
                 list="fq-clients-list"
                 hx-get="/inbox/clients/search"
                 hx-trigger="keyup changed delay:300ms"
                 hx-target="#fq-link-results-{{ item.inbox_id }}"
                 hx-swap="innerHTML">
          <div id="fq-link-results-{{ item.inbox_id }}" class="hidden"></div>
        </div>
      {% endif %}

      <button class="bg-[#0B4BFF] text-white text-xs font-semibold px-3 py-1.5 rounded-md hover:bg-blue-700 whitespace-nowrap"
              {% if item.kind == 'inbox' %}
                hx-get="/inbox/{{ item.inbox_id }}/process-slideover"
                hx-target="#fu-edit-content" hx-swap="innerHTML"
                onclick="openFollowupEdit()"
              {% elif item.kind == 'milestone' %}
                hx-post="/policies/{{ item.policy_uid }}/milestones/{{ item.id }}/complete"
                hx-target="#fq-{{ row_id }}" hx-swap="outerHTML"
              {% elif item.kind == 'suggested' %}
                hx-get="/policies/{{ item.policy_uid }}/row/log?ctx=focus"
                hx-target="#fq-{{ row_id }}" hx-swap="outerHTML"
              {% else %}
                onclick="fqExpandComplete('{{ row_id }}', {{ item | tojson }})"
              {% endif %}>
        {{ item.suggested_action }}
      </button>

      <button class="bg-[#EDEAE4] text-[#6B6860] text-xs px-2 py-1.5 rounded-md hover:bg-gray-200"
              onclick="fqToggleMenu('{{ row_id }}')">
        ⋯
      </button>
    </div>
  </div>

  {# Guide Me expanded suggestion (only on highlighted item) #}
  {% if is_highlighted and guide_me %}
  <div class="mt-2.5 p-2.5 bg-purple-50 rounded-md text-xs text-purple-700">
    <strong>→ Suggested:</strong> {{ item.suggested_action_detail }}
  </div>
  {% endif %}

  {# Inline completion form (hidden, expanded by fqExpandComplete) #}
  <div id="fq-complete-{{ row_id }}" class="hidden mt-2.5 p-3 bg-gray-50 rounded-md border border-gray-200">
    <form hx-post="{% if item.kind == 'followup' and item.source == 'activity' %}/activities/{{ item.id }}/followup{% elif item.policy_uid %}/policies/{{ item.policy_uid }}/followup{% else %}/activities/log{% endif %}"
          hx-target="#fq-{{ row_id }}" hx-swap="outerHTML"
          class="flex flex-wrap gap-2 items-end">
      {% if not item.id or item.kind in ('suggested', 'insurance_deadline') %}
        <input type="hidden" name="client_id" value="{{ item.client_id or '' }}">
        <input type="hidden" name="policy_uid" value="{{ item.policy_uid or '' }}">
      {% endif %}
      <input type="hidden" name="context" value="focus_queue">
      <div class="flex flex-col gap-0.5">
        <label class="text-[10px] uppercase text-gray-400 font-semibold">Note</label>
        <input type="text" name="notes" placeholder="What happened?"
               value="{% if guide_me %}{{ item.suggested_action_detail }}{% endif %}"
               class="text-xs border border-gray-200 rounded px-2 py-1 w-48 focus:border-blue-400 focus:outline-none">
      </div>
      <div class="flex flex-col gap-0.5">
        <label class="text-[10px] uppercase text-gray-400 font-semibold">Next follow-up</label>
        <input type="date" name="new_follow_up_date"
               value="{{ item.follow_up_date or '' }}"
               class="text-xs border border-gray-200 rounded px-2 py-1 focus:border-blue-400 focus:outline-none">
      </div>
      <div class="flex flex-col gap-0.5">
        <label class="text-[10px] uppercase text-gray-400 font-semibold">Ball with</label>
        <select name="disposition" class="text-xs border border-gray-200 rounded px-2 py-1 focus:border-blue-400 focus:outline-none">
          <option value="">— Select —</option>
          {% for d in dispositions %}
            <option value="{{ d.label }}" {% if d.label == item.disposition %}selected{% endif %}>{{ d.label }}</option>
          {% endfor %}
        </select>
      </div>
      <button type="submit" class="bg-green-600 text-white text-xs font-semibold px-3 py-1.5 rounded-md hover:bg-green-700">
        Done
      </button>
      <button type="button" onclick="fqCollapseComplete('{{ row_id }}')"
              class="text-xs text-gray-500 hover:text-gray-700 px-2 py-1.5">
        Cancel
      </button>
    </form>
  </div>

  {# Overflow menu (hidden) #}
  <div id="fq-menu-{{ row_id }}" class="hidden mt-2 p-2 bg-white border border-gray-200 rounded-md shadow-sm">
    <div class="flex gap-2 text-xs">
      {% if item.client_id %}
        <a href="/clients/{{ item.client_id }}" class="text-blue-600 hover:underline">View Client</a>
      {% endif %}
      {% if item.policy_uid %}
        <a href="/policies/{{ item.policy_uid }}" class="text-blue-600 hover:underline">View Policy</a>
      {% endif %}
      <button class="text-gray-500 hover:text-gray-700"
              hx-post="/activities/{{ item.id }}/snooze" hx-vals='{"days": 1}'
              hx-target="#fq-{{ row_id }}" hx-swap="outerHTML">
        Snooze 1d
      </button>
      <button class="text-gray-500 hover:text-gray-700"
              hx-post="/activities/{{ item.id }}/snooze" hx-vals='{"days": 7}'
              hx-target="#fq-{{ row_id }}" hx-swap="outerHTML">
        Snooze 7d
      </button>
      {% if item.kind != 'inbox' %}
        <button class="text-green-600 hover:text-green-800"
                hx-post="/activities/{{ item.id }}/complete"
                hx-target="#fq-{{ row_id }}" hx-swap="delete">
          Mark Done
        </button>
      {% endif %}
    </div>
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/action_center/_focus_item.html
git commit -m "feat: add Focus Queue item row template"
```

---

### Task 4: Focus Queue Main Template

**Files:**
- Create: `src/policydb/web/templates/action_center/_focus_queue.html`

- [ ] **Step 1: Create _focus_queue.html**

Create `src/policydb/web/templates/action_center/_focus_queue.html`:

```html
{# Focus Queue — main content panel.
   Variables: focus_items (list), waiting_items (list), stats (dict),
              guide_me (bool), horizon (str), client_id (int),
              all_clients (list), dispositions (list), activity_types (list)
#}

{# ─── Top Bar: Time Horizon + Guide Me + Client Filter ─── #}
<div class="flex items-center justify-between px-5 py-3 bg-white border-b border-[#E8E2D9]" id="fq-top-bar">
  <div class="flex items-center gap-4">
    {# Time Horizon segmented control #}
    <div class="flex bg-[#EDEAE4] rounded-lg overflow-hidden text-[13px]" id="fq-horizon">
      {% for h in [("0", "Today"), ("7", "This Week"), ("14", "Next 2 Weeks")] %}
        <button class="px-3.5 py-1.5 transition-colors
                       {% if horizon == h[0] %}bg-[#000F47] text-white font-semibold{% else %}text-[#6B6860] hover:bg-[#E0DBD3]{% endif %}"
                hx-get="/action-center/focus?horizon={{ h[0] }}&client_id={{ client_id }}&guide_me={{ guide_me | int }}"
                hx-target="#fq-content" hx-swap="innerHTML">
          {{ h[1] }}
        </button>
      {% endfor %}
      <button class="px-3.5 py-1.5 text-[#6B6860] hover:bg-[#E0DBD3] relative"
              onclick="document.getElementById('fq-custom-date').showPicker()">
        Custom...
        <input type="date" id="fq-custom-date" class="absolute opacity-0 w-0 h-0"
               hx-get="/action-center/focus" hx-target="#fq-content" hx-swap="innerHTML"
               hx-trigger="change" hx-include="this"
               name="custom_date"
               hx-vals='{"client_id": "{{ client_id }}", "guide_me": "{{ guide_me | int }}"}'>
      </button>
    </div>
    <span class="text-[13px] text-[#9B9790]">{{ stats.focus_count }} item{{ 's' if stats.focus_count != 1 }} need{{ '' if stats.focus_count != 1 else 's' }} focus</span>
  </div>

  <div class="flex items-center gap-3">
    {# Guide Me toggle #}
    <label class="flex items-center gap-2 px-3 py-1 rounded-lg cursor-pointer transition-colors
                  {% if guide_me %}bg-purple-50 border border-purple-300{% else %}bg-[#EDEAE4] border border-transparent{% endif %}"
           id="fq-guide-toggle">
      <div class="relative w-8 h-[18px] rounded-full transition-colors
                  {% if guide_me %}bg-purple-600{% else %}bg-gray-300{% endif %}"
           hx-get="/action-center/focus?horizon={{ horizon }}&client_id={{ client_id }}&guide_me={{ 0 if guide_me else 1 }}"
           hx-target="#fq-content" hx-swap="innerHTML">
        <div class="absolute top-0.5 w-3.5 h-3.5 bg-white rounded-full shadow transition-transform
                    {% if guide_me %}translate-x-[17px]{% else %}translate-x-0.5{% endif %}"></div>
      </div>
      <span class="text-[13px] font-semibold {% if guide_me %}text-purple-700{% else %}text-[#6B6860]{% endif %}">
        Guide Me
      </span>
    </label>

    {# Client filter #}
    <div class="relative">
      <input type="text" placeholder="All Clients" value="{{ selected_client_name or '' }}"
             list="fq-client-list" class="text-[13px] text-[#6B6860] px-3 py-1.5 bg-[#EDEAE4] rounded-md border-none w-36 focus:outline-none focus:ring-1 focus:ring-blue-400"
             hx-get="/action-center/focus" hx-target="#fq-content" hx-swap="innerHTML"
             hx-trigger="change" name="client_name"
             hx-vals='{"horizon": "{{ horizon }}", "guide_me": "{{ guide_me | int }}"}'>
      <datalist id="fq-client-list">
        <option value="">All Clients</option>
        {% for c in all_clients %}
          <option value="{{ c.name }}">{{ c.name }}</option>
        {% endfor %}
      </datalist>
    </div>
  </div>
</div>

{# ─── Two-Panel Layout ─── #}
<div class="flex min-h-[calc(100vh-220px)]" id="fq-panels">

  {# ─── Focus Queue (main area) ─── #}
  <div class="flex-[7] px-5 py-4 border-r border-[#E8E2D9] overflow-y-auto">
    <div class="text-[11px] uppercase tracking-wider text-[#9B9790] font-semibold mb-3">Focus Queue</div>

    {# Quick capture #}
    <form hx-post="/inbox/capture" hx-swap="none" class="mb-4 flex gap-2"
          hx-on::after-request="this.reset(); htmx.ajax('GET', '/action-center/focus?horizon={{ horizon }}&client_id={{ client_id }}&guide_me={{ guide_me | int }}', {target: '#fq-content', swap: 'innerHTML'})">
      <input type="text" name="content" placeholder="Quick capture — type a note, task, or reminder..."
             required class="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-2 focus:border-teal-400 focus:outline-none focus:ring-1 focus:ring-teal-200">
      <button type="submit" class="bg-teal-600 text-white text-xs font-semibold px-3 py-2 rounded-lg hover:bg-teal-700">
        Capture
      </button>
    </form>

    {% if focus_items %}
      {% for item in focus_items %}
        {% set is_highlighted = guide_me and loop.first %}
        {% include "action_center/_focus_item.html" %}
      {% endfor %}
    {% else %}
      <div class="text-center py-16 text-[#9B9790]">
        <div class="text-3xl mb-2">✓</div>
        <div class="text-sm font-medium">All clear — nothing needs your focus right now.</div>
      </div>
    {% endif %}
  </div>

  {# ─── Waiting Sidebar ─── #}
  <div class="flex-[3] px-4 py-4 bg-[#FAF8F5] overflow-y-auto">
    {% include "action_center/_waiting_sidebar.html" %}
  </div>

</div>

{# ─── JavaScript ─── #}
<script>
function fqExpandComplete(rowId, item) {
  var form = document.getElementById('fq-complete-' + rowId);
  if (form) { form.classList.remove('hidden'); }
}
function fqCollapseComplete(rowId) {
  var form = document.getElementById('fq-complete-' + rowId);
  if (form) { form.classList.add('hidden'); }
}
function fqToggleMenu(rowId) {
  var menu = document.getElementById('fq-menu-' + rowId);
  if (menu) { menu.classList.toggle('hidden'); }
}
</script>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/action_center/_focus_queue.html
git commit -m "feat: add Focus Queue main template with top bar and two-panel layout"
```

---

### Task 5: Waiting Sidebar Template

**Files:**
- Create: `src/policydb/web/templates/action_center/_waiting_sidebar.html`

- [ ] **Step 1: Create _waiting_sidebar.html**

Create `src/policydb/web/templates/action_center/_waiting_sidebar.html`:

```html
{# Waiting Sidebar — items where ball is with someone else.
   Variables: waiting_items (list), stats (dict), horizon (str),
             client_id (int), guide_me (bool)
#}

<div class="text-[11px] uppercase tracking-wider text-[#9B9790] font-semibold mb-3">
  Waiting On Others
  <span class="bg-[#EDEAE4] px-2 py-0.5 rounded-full text-[11px] ml-1">{{ stats.waiting_count }}</span>
</div>

{# Nudge alert banner #}
{% if stats.nudge_alert_count > 0 %}
  <div class="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5 mb-3 text-[12px] text-amber-800">
    <strong>⚠ {{ stats.nudge_alert_count }} item{{ 's' if stats.nudge_alert_count != 1 }} waiting 10+ days</strong> — may need a nudge
  </div>
{% endif %}

{% for item in waiting_items[:8] %}
  {% set days_waiting = -(item.days_until_deadline or 0) if item.days_until_deadline and item.days_until_deadline < 0 else 0 %}
  {% set is_stale = days_waiting >= 10 %}
  <div class="bg-white rounded-lg px-3 py-2.5 mb-1.5 text-[13px] {% if is_stale %}border border-amber-200{% endif %}">
    <div class="font-semibold text-[#3D3C37] truncate">
      {{ item.client_name }}{% if item.policy_type %} — {{ item.policy_type }}{% endif %}
    </div>
    <div class="text-[12px] text-[#9B9790] mt-0.5 truncate">
      {{ item.disposition or 'Waiting' }}
      {% if days_waiting > 0 %} · {{ days_waiting }} day{{ 's' if days_waiting != 1 }}{% endif %}
    </div>
    {% if is_stale %}
      <div class="flex gap-1.5 mt-1.5">
        <button class="bg-amber-50 text-amber-800 border border-amber-200 text-[11px] font-semibold px-2.5 py-0.5 rounded hover:bg-amber-100"
                hx-post="/activities/{{ item.id }}/nudge"
                hx-target="closest div"
                hx-swap="outerHTML"
                hx-vals='{"context": "waiting_sidebar"}'>
          Nudge →
        </button>
        <button class="bg-gray-50 text-[#6B6860] text-[11px] px-2.5 py-0.5 rounded hover:bg-gray-100"
                hx-get="/action-center/focus?horizon={{ horizon }}&client_id={{ client_id }}&guide_me={{ guide_me | int }}&promote={{ item.id }}"
                hx-target="#fq-content" hx-swap="innerHTML">
          Pull to Focus
        </button>
      </div>
    {% endif %}
  </div>
{% endfor %}

{% if waiting_items | length > 8 %}
  <div class="text-center py-2 text-[12px] text-[#9B9790] cursor-pointer hover:text-[#6B6860]"
       onclick="this.nextElementSibling.classList.toggle('hidden'); this.textContent = this.textContent.includes('+') ? 'Show less' : '+ {{ waiting_items | length - 8 }} more items'">
    + {{ waiting_items | length - 8 }} more items
  </div>
  <div class="hidden">
    {% for item in waiting_items[8:] %}
      {% set days_waiting = -(item.days_until_deadline or 0) if item.days_until_deadline and item.days_until_deadline < 0 else 0 %}
      <div class="bg-white rounded-lg px-3 py-2.5 mb-1.5 text-[13px]">
        <div class="font-semibold text-[#3D3C37] truncate">{{ item.client_name }}{% if item.policy_type %} — {{ item.policy_type }}{% endif %}</div>
        <div class="text-[12px] text-[#9B9790] mt-0.5">{{ item.disposition or 'Waiting' }}{% if days_waiting > 0 %} · {{ days_waiting }}d{% endif %}</div>
      </div>
    {% endfor %}
  </div>
{% endif %}

{# Quick stats #}
<div class="border-t border-[#E8E2D9] mt-3 pt-3">
  <div class="text-[11px] uppercase tracking-wider text-[#9B9790] font-semibold mb-2">Quick Stats</div>
  <div class="text-[13px] text-[#6B6860] leading-relaxed space-y-1">
    <div>📋 {{ stats.focus_count }} in focus</div>
    <div>⏳ {{ stats.waiting_count }} waiting on others</div>
    {% if stats.get('hours_today') %}<div>⏱ {{ stats.hours_today }}h logged today</div>{% endif %}
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/action_center/_waiting_sidebar.html
git commit -m "feat: add Waiting Sidebar template with nudge alerts and quick stats"
```

---

### Task 6: Route — Focus Queue Endpoint

**Files:**
- Modify: `src/policydb/web/routes/action_center.py`

Add the Focus Queue endpoint and update the page handler.

- [ ] **Step 1: Add import for focus_queue module**

At the top of `action_center.py`, after the existing imports, add:

```python
from policydb.focus_queue import build_focus_queue
```

- [ ] **Step 2: Add Focus Queue endpoint**

Add this endpoint in `action_center.py`, after the existing followups endpoint (around line 560). Place it BEFORE any parameterized routes:

```python
@router.get("/action-center/focus", response_class=HTMLResponse)
def action_center_focus(
    request: Request,
    horizon: str = "0",
    client_id: int = 0,
    guide_me: int = 0,
    client_name: str = "",
    custom_date: str = "",
    promote: int = 0,
    conn=Depends(get_db),
):
    """Focus Queue partial — returns the two-panel Focus Queue + Waiting Sidebar."""
    # Resolve horizon days
    if custom_date:
        try:
            target = datetime.strptime(custom_date, "%Y-%m-%d").date()
            horizon_days = (target - date.today()).days
        except ValueError:
            horizon_days = 0
    else:
        horizon_days = int(horizon) if horizon.isdigit() else 0

    # Resolve client_id from name if needed
    if client_name and not client_id:
        row = conn.execute(
            "SELECT id FROM clients WHERE name = ? AND archived = 0", (client_name,)
        ).fetchone()
        if row:
            client_id = row["id"]

    # Build the queue
    focus_items, waiting_items, stats = build_focus_queue(
        conn, horizon_days=horizon_days, client_id=client_id
    )

    # Handle manual promote from waiting sidebar
    if promote:
        waiting_items = [w for w in waiting_items if w.get("id") != promote]
        promoted = next((w for w in waiting_items if w.get("id") == promote), None)
        if promoted:
            promoted["accountability"] = "my_action"
            focus_items.insert(0, promoted)
            stats["focus_count"] += 1
            stats["waiting_count"] -= 1

    # Add hours today to stats
    from policydb.queries import get_time_summary
    time_summary = get_time_summary(conn)
    stats["hours_today"] = time_summary.get("hours_today", 0)

    # All clients for filter dropdown
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()

    selected_client_name = ""
    if client_id:
        row = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row:
            selected_client_name = row["name"]

    return templates.TemplateResponse(
        "action_center/_focus_queue.html",
        {
            "request": request,
            "focus_items": focus_items,
            "waiting_items": waiting_items,
            "stats": stats,
            "guide_me": bool(guide_me),
            "horizon": horizon if not custom_date else str(horizon_days),
            "client_id": client_id,
            "all_clients": [dict(c) for c in all_clients],
            "selected_client_name": selected_client_name,
            "dispositions": cfg.get("follow_up_dispositions", []),
            "activity_types": cfg.get("activity_types", []),
        },
    )
```

- [ ] **Step 3: Update main page handler to default to Focus Queue**

In the existing `action_center_page()` function (around line 939), update the `initial_tab` logic. Change the default from `"followups"` to `"focus"`:

Find the line:
```python
initial_tab = tab or "followups"
```

Replace with:
```python
initial_tab = tab or "focus"
```

Also ensure the Focus Queue context is loaded when `initial_tab == "focus"`. In the section where tab contexts are built, add:

```python
    # Build tab context for the initial tab
    tab_ctx = {}
    if initial_tab == "focus":
        focus_items, waiting_items, fq_stats = build_focus_queue(conn)
        from policydb.queries import get_time_summary
        time_summary = get_time_summary(conn)
        fq_stats["hours_today"] = time_summary.get("hours_today", 0)
        all_clients = conn.execute(
            "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
        ).fetchall()
        tab_ctx = {
            "focus_items": focus_items,
            "waiting_items": waiting_items,
            "stats": fq_stats,
            "guide_me": False,
            "horizon": "0",
            "client_id": 0,
            "all_clients": [dict(c) for c in all_clients],
            "selected_client_name": "",
            "dispositions": cfg.get("follow_up_dispositions", []),
            "activity_types": cfg.get("activity_types", []),
        }
    elif initial_tab == "followups":
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/action_center.py
git commit -m "feat: add Focus Queue route endpoint and update page handler default"
```

---

### Task 7: Page Layout — Restructure Tabs

**Files:**
- Modify: `src/policydb/web/templates/action_center/page.html`

Replace the 8-tab row with Focus Queue as the default view and a "More" menu for secondary tabs.

- [ ] **Step 1: Read current page.html**

Read `src/policydb/web/templates/action_center/page.html` to understand the exact current tab bar structure.

- [ ] **Step 2: Update the tab bar**

Replace the tab bar section (the `<div>` containing all 8 tab buttons) with a new structure that has:
- **Focus** as the primary/default tab (styled as the main tab, always visible)
- **Follow-ups** as a secondary tab (for users who want the old view)
- **More ▾** dropdown containing: Activities, Scratchpads, Issues, Anomalies, Activity Review, Data Health

The exact HTML will depend on the current structure read in Step 1. The pattern:

```html
{# Primary tabs #}
<div class="flex items-center gap-1 px-4 pt-3 border-b border-[#E8E2D9] bg-white">
  {# Focus Queue - primary #}
  <button data-tab="focus" data-tab-url="/action-center/focus"
          class="tab-btn px-4 py-2 text-sm font-semibold rounded-t-lg transition-colors
                 {% if initial_tab == 'focus' %}bg-[#F7F3EE] text-[#000F47] border-b-2 border-[#0B4BFF]{% else %}text-[#6B6860] hover:text-[#3D3C37]{% endif %}">
    Focus
    {% if act_now_count + nudge_due_count > 0 %}
      <span class="ml-1 text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full">{{ act_now_count + nudge_due_count }}</span>
    {% endif %}
  </button>

  {# Legacy Follow-ups #}
  <button data-tab="followups" data-tab-url="/action-center/followups"
          class="tab-btn px-4 py-2 text-sm rounded-t-lg transition-colors
                 {% if initial_tab == 'followups' %}bg-[#F7F3EE] text-[#000F47] border-b-2 border-[#0B4BFF]{% else %}text-[#9B9790] hover:text-[#6B6860]{% endif %}">
    Follow-ups
  </button>

  {# More dropdown #}
  <div class="relative ml-2" id="ac-more-menu">
    <button onclick="document.getElementById('ac-more-dropdown').classList.toggle('hidden')"
            class="px-3 py-2 text-sm text-[#9B9790] hover:text-[#6B6860] rounded-t-lg">
      More ▾
      {% set secondary_badges = (scratchpad_count or 0) + (issues_count or 0) + (anomaly_total or 0) + (review_pending_count or 0) %}
      {% if secondary_badges > 0 %}
        <span class="ml-0.5 w-1.5 h-1.5 bg-amber-400 rounded-full inline-block"></span>
      {% endif %}
    </button>
    <div id="ac-more-dropdown" class="hidden absolute left-0 top-full mt-0.5 bg-white border border-gray-200 rounded-lg shadow-lg z-50 py-1 w-48">
      {% for tab_info in [
        ("inbox", "/action-center/inbox", "Inbox", inbox_pending),
        ("activities", "/action-center/activities", "Activities", 0),
        ("scratchpads", "/action-center/scratchpads", "Scratchpads", scratchpad_count),
        ("issues", "/action-center/issues", "Issues", issues_count),
        ("anomalies", "/action-center/anomalies", "Anomalies", anomaly_total),
        ("activity-review", "/action-center/activity-review", "Activity Review", review_pending_count),
        ("data-health", "/action-center/data-health", "Data Health", health_incomplete),
      ] %}
        <button data-tab="{{ tab_info[0] }}" data-tab-url="{{ tab_info[1] }}"
                class="tab-btn w-full text-left px-3 py-1.5 text-sm text-[#6B6860] hover:bg-gray-50 flex justify-between items-center"
                onclick="document.getElementById('ac-more-dropdown').classList.add('hidden')">
          {{ tab_info[2] }}
          {% if tab_info[3] %}
            <span class="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full">{{ tab_info[3] }}</span>
          {% endif %}
        </button>
      {% endfor %}
    </div>
  </div>
</div>
```

- [ ] **Step 3: Update initial tab content include**

In the section where the initial tab content is included, add the Focus Queue case:

```html
{% if initial_tab == 'focus' %}
  {% include "action_center/_focus_queue.html" %}
{% elif initial_tab == 'followups' %}
  {% include "action_center/_followups.html" %}
{% elif ...existing cases... %}
```

- [ ] **Step 4: Close the More dropdown on outside click**

Add to the page's `<script>` block:

```javascript
document.addEventListener('click', function(e) {
  var menu = document.getElementById('ac-more-dropdown');
  var btn = document.getElementById('ac-more-menu');
  if (menu && btn && !btn.contains(e.target)) {
    menu.classList.add('hidden');
  }
});
```

- [ ] **Step 5: Update sessionStorage tab key**

In the existing tab-switching JS, ensure that clicking "Focus" stores `'focus'` and the HTMX ajax call targets `#ac-tab-content`:

```javascript
// When Focus tab is clicked, load the full Focus Queue (which includes the waiting sidebar)
// For other tabs, load into #ac-tab-content as before
```

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/action_center/page.html
git commit -m "feat: restructure Action Center tabs — Focus Queue as default, secondary tabs in More menu"
```

---

### Task 8: Sidebar Updates

**Files:**
- Modify: `src/policydb/web/templates/action_center/_sidebar.html`

- [ ] **Step 1: Read current _sidebar.html**

Read `src/policydb/web/templates/action_center/_sidebar.html` to understand exact structure.

- [ ] **Step 2: Update stats grid to reflect Focus Queue**

Update the stats boxes to show Focus Queue counts. Change:
- "Actions" → "Focus" (shows focus_count instead of act_now_count)
- Keep "Nudges" → shows nudge_alert_count from stats
- Keep "Inbox" → shows inbox_pending
- Keep "This Month" → hours

The exact changes depend on the current structure read in Step 1. The sidebar template needs to handle both the old `act_now_count` variable (for legacy followups tab) and the new `stats` dict (for Focus Queue).

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_sidebar.html
git commit -m "feat: update sidebar stats for Focus Queue counts"
```

---

### Task 9: Integration — Wire Up Nudge and Snooze Endpoints

**Files:**
- Modify: `src/policydb/web/routes/action_center.py`

The Focus Queue template references a few endpoints that may need to be created or verified:
- `POST /activities/{id}/nudge` — for the Waiting Sidebar nudge button
- `POST /activities/{id}/snooze` — for the overflow menu snooze

- [ ] **Step 1: Check if snooze and nudge endpoints exist**

Search for existing `/snooze` and `/nudge` endpoints in the routes.

- [ ] **Step 2: Add nudge endpoint if missing**

If not found, add to `action_center.py`:

```python
@router.post("/activities/{activity_id}/nudge", response_class=HTMLResponse)
def nudge_activity(
    request: Request,
    activity_id: int,
    context: str = Form("focus_queue"),
    conn=Depends(get_db),
):
    """Log a nudge follow-up for a waiting activity."""
    row = conn.execute(
        "SELECT * FROM activity_log WHERE id = ?", (activity_id,)
    ).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    row = dict(row)
    # Create a new follow-up activity
    conn.execute("""
        INSERT INTO activity_log (client_id, policy_id, activity_type, subject, details,
                                  follow_up_date, activity_date, account_exec, disposition)
        VALUES (?, ?, 'Email', ?, 'Nudge follow-up sent', ?, date('now'), ?, ?)
    """, (
        row["client_id"], row.get("policy_id"),
        f"Follow-up nudge: {row.get('subject', '')}",
        (date.today() + timedelta(days=7)).isoformat(),
        row.get("account_exec", cfg.get("default_account_exec", "Grant")),
        row.get("disposition", ""),
    ))
    # Mark the old one as done
    conn.execute(
        "UPDATE activity_log SET follow_up_done = 1 WHERE id = ?", (activity_id,)
    )
    conn.commit()

    return HTMLResponse(
        '<div class="text-xs text-green-600 p-2">Nudge sent ✓</div>',
        headers={"HX-Trigger": '{"activityLogged": "Nudge sent"}'},
    )
```

- [ ] **Step 3: Add snooze endpoint if missing**

If not found, add:

```python
@router.post("/activities/{activity_id}/snooze", response_class=HTMLResponse)
def snooze_activity(
    request: Request,
    activity_id: int,
    days: int = Form(1),
    conn=Depends(get_db),
):
    """Snooze an activity's follow-up date by N days."""
    new_date = (date.today() + timedelta(days=days)).isoformat()
    conn.execute(
        "UPDATE activity_log SET follow_up_date = ? WHERE id = ?",
        (new_date, activity_id),
    )
    conn.commit()
    return HTMLResponse("", headers={"HX-Trigger": '{"activityLogged": "Snoozed"}'})
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/action_center.py
git commit -m "feat: add nudge and snooze endpoints for Focus Queue actions"
```

---

### Task 10: QA — Browser Verification

**Files:** None (read-only testing)

- [ ] **Step 1: Start the server**

```bash
cd /Users/grantgreeson/Documents/Projects/policydb && ~/.policydb/venv/bin/policydb serve --port 8321
```

- [ ] **Step 2: Navigate to Action Center and screenshot**

Open `http://127.0.0.1:8321/action-center` in browser. Verify:
- Focus Queue is the default view (not Follow-ups)
- Two-panel layout: Focus Queue on left, Waiting Sidebar on right
- Top bar has: time horizon control, Guide Me toggle, client filter
- Items are ranked by score (most urgent at top)
- Source badges (RENEWAL, MILESTONE, INBOX, ISSUE, FOLLOW-UP) render correctly
- Context lines show meaningful text

- [ ] **Step 3: Test time horizon**

Click "This Week" and "Next 2 Weeks" — verify the item count changes.

- [ ] **Step 4: Test Guide Me mode**

Toggle Guide Me ON — verify:
- Top item gets purple highlight
- Suggestion panel appears below the highlighted item
- "Suggested:" text shows a specific action

- [ ] **Step 5: Test completion flow**

Click the suggested action button on an item — verify:
- Inline completion form expands
- Note field pre-filled with suggestion (when Guide Me is ON)
- "Ball with" dropdown shows disposition options
- "Done" submits and removes the item from the queue

- [ ] **Step 6: Test Waiting Sidebar**

Verify:
- Sidebar shows items with "Waiting on..." labels
- Stale items (10+ days) show nudge alert banner
- "Nudge →" button logs activity
- "Pull to Focus" moves item to Focus Queue

- [ ] **Step 7: Test secondary tabs**

Click "More ▾" — verify dropdown shows all secondary tabs.
Click each one to confirm they still load correctly.

- [ ] **Step 8: Test legacy Follow-ups tab**

Click "Follow-ups" tab — verify it still renders the old 8-bucket view (backward compatibility).

- [ ] **Step 9: Fix any issues found**

Address visual bugs, broken endpoints, or missing data.

- [ ] **Step 10: Commit all fixes**

```bash
git add -A
git commit -m "fix: QA fixes for Focus Queue — layout, styling, and data issues"
```

---

## Summary of Changes

| What | Before | After |
|------|--------|-------|
| Default Action Center view | 8-bucket Follow-ups | Single ranked Focus Queue + Waiting Sidebar |
| Inbox processing | Separate tab + slideover workflow | Inline in Focus Queue (matched items get one-click action) |
| Time planning | Not supported | Time horizon control: Today / Week / 2 Weeks / Custom |
| Low-energy mode | Not supported | Guide Me toggle with step-by-step suggestions |
| Completion | Disposition pills + date picker | Smart default: pre-filled note + date, one-click confirm |
| Tab count (visible) | 8 equal tabs | 2 primary (Focus, Follow-ups) + More dropdown |
| Urgency model | 8 separate buckets to scan | Single scored/ranked list |
| "Ball with" concept | Called "disposition" | Renamed to "Ball with" in UI |
