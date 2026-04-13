"""Action Center — unified tabbed page for Follow-ups, Inbox, Activities, Scratchpads."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger("policydb.action_center")

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg
from policydb.activity_review import (
    expire_dismissed_suggestions,
    get_pending_review_count,
    get_pending_suggestions,
    scan_for_unlogged_sessions,
)
from policydb.queries import (
    get_activities,
    get_all_followups,
    get_dashboard_hours_this_month,
    get_insurance_deadline_suggestions,
    get_suggested_followups,
    get_time_summary,
)
from policydb.data_health import get_book_health_summary
from policydb.focus_queue import build_focus_queue

router = APIRouter()


# ── Nudge escalation ─────────────────────────────────────────────────────────


def _compute_nudge_tier(conn, policy_uid: str, dispositions: list[dict]) -> tuple[int, str]:
    """Count waiting_external activities for a policy in last 90 days.

    Returns (count, tier) where tier is 'normal', 'elevated', or 'urgent'.
    """
    waiting_labels = [
        d["label"] for d in dispositions
        if d.get("accountability") == "waiting_external"
    ]
    if not waiting_labels or not policy_uid:
        return 1, "normal"

    placeholders = ",".join("?" * len(waiting_labels))
    count = conn.execute(
        f"""SELECT COUNT(*) FROM activity_log a
            WHERE (a.policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
                   OR (a.issue_id IN (SELECT ipc.issue_id FROM v_issue_policy_coverage ipc
                                      WHERE ipc.policy_id = (SELECT id FROM policies WHERE policy_uid = ?))
                       AND a.item_kind != 'issue'))
              AND a.disposition IN ({placeholders})
              AND a.activity_date >= date('now', '-90 days')""",
        [policy_uid, policy_uid] + waiting_labels,
    ).fetchone()[0]

    count = max(count, 1)
    tier = "urgent" if count >= 3 else "elevated" if count >= 2 else "normal"
    return count, tier


# ── Classification ───────────────────────────────────────────────────────────


def _classify_item(item: dict, today: date, stale_threshold: int, dispositions: list[dict]) -> str:
    """Classify a follow-up item into a bucket.

    Returns one of: triage, today, overdue, stale, nudge_due, watching, scheduled
    """
    source = item.get("source", "activity")
    disposition = item.get("disposition") or ""
    fu_date_str = item.get("follow_up_date", "")

    # Step 1: Triage — activity items with no disposition
    # But only if the follow-up date is today or past. Future items without
    # a disposition go to "watching" — they were created early and aren't
    # actionable yet.
    if source in ("activity", "project") and not disposition.strip():
        try:
            _fu = date.fromisoformat(fu_date_str)
            if (today - _fu).days < 0:
                return "watching"
        except (ValueError, TypeError):
            pass
        return "triage"

    # Step 2: Map disposition → accountability
    accountability = "my_action"  # default
    for d in dispositions:
        if d.get("label", "").lower() == disposition.lower():
            accountability = d.get("accountability", "my_action")
            break

    # Step 3: Scheduled
    if accountability == "scheduled":
        return "scheduled"

    # Parse date
    try:
        fu_date = date.fromisoformat(fu_date_str)
    except (ValueError, TypeError):
        return "triage"  # bad date → triage

    days_overdue = (today - fu_date).days

    # Step 4: waiting_external
    if accountability == "waiting_external":
        return "nudge_due" if days_overdue >= 0 else "watching"

    # Step 5: my_action date tiers
    if days_overdue == 0:
        return "today"
    elif days_overdue > stale_threshold:
        return "stale"
    elif days_overdue > 0:
        return "overdue"
    else:
        return "watching"  # future my_action → watching with "my turn" badge


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sidebar_ctx(conn) -> dict:
    """Compute sidebar stats, quick actions context, and recent activity feed."""
    overdue, upcoming = get_all_followups(conn, window=7)
    inbox_pending = conn.execute(
        "SELECT COUNT(*) FROM inbox WHERE status='pending'"
    ).fetchone()[0]
    try:
        suggested_contacts_count = conn.execute(
            "SELECT COUNT(*) FROM suggested_contacts WHERE status='pending'"
        ).fetchone()[0]
    except Exception:
        suggested_contacts_count = 0
    hours_month = get_dashboard_hours_this_month(conn)
    # Due this week: items from upcoming whose follow_up_date <= 7 days out
    due_this_week = len(upcoming)
    # Recent activity feed (last 5)
    recent = [dict(r) for r in conn.execute("""
        SELECT a.id, a.activity_type, a.subject, a.activity_date, a.created_at,
               c.name AS client_name
        FROM activity_log a JOIN clients c ON a.client_id = c.id
        ORDER BY a.id DESC LIMIT 5
    """).fetchall()]
    try:
        from policydb.anomaly_engine import get_anomaly_counts, get_all_active_anomalies
        anomaly_counts = get_anomaly_counts(conn)
        anomalies_list = get_all_active_anomalies(conn)
    except Exception:
        anomaly_counts = {}
        anomalies_list = []

    return {
        "overdue_count": len(overdue),
        "due_this_week": due_this_week,
        "inbox_pending": inbox_pending,
        "suggested_contacts_count": suggested_contacts_count,
        "hours_month": hours_month,
        "recent_activities": recent,
        "anomaly_counts": anomaly_counts,
        "anomalies": anomalies_list,
    }


def _followups_ctx(conn, window: int, activity_type: str, q: str,
                   client_id: int = 0) -> dict:
    """Build follow-ups tab context — reuses logic from activities.py.

    Produces 8 urgency/accountability buckets alongside the existing
    overdue/upcoming breakdown for backward compatibility:
      triage        – activity items with no disposition (need initial triage)
      today_bucket  – my_action items due today
      overdue_bucket– my_action items overdue (1..stale_threshold days)
      stale         – my_action items overdue beyond stale_threshold
      nudge_due     – waiting_external items with follow_up_date <= today
      prep_coming   – timeline milestones whose prep_alert_date has arrived
      watching      – future items (both my_action and waiting_external)
      scheduled     – items with 'scheduled' accountability
    """
    from policydb.web.routes.activities import _add_mailto_subjects

    filter_client_ids = [client_id] if client_id else None
    excluded = cfg.get("renewal_statuses_excluded", [])
    # Periodic stale cleanup — runs on each followups tab load instead of only at startup
    try:
        from policydb.queries import auto_close_stale_followups
        auto_close_stale_followups(conn)
    except Exception:
        logger.debug("auto_close_stale_followups failed", exc_info=True)
    overdue_raw, upcoming_raw = get_all_followups(conn, window=window, client_ids=filter_client_ids)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded, client_ids=filter_client_ids)
    insurance_suggestions = get_insurance_deadline_suggestions(conn, client_ids=filter_client_ids)

    if activity_type:
        overdue_raw = [r for r in overdue_raw if r.get("activity_type") == activity_type]
        upcoming_raw = [r for r in upcoming_raw if r.get("activity_type") == activity_type]
    if q:
        q_lower = q.lower()
        overdue_raw = [r for r in overdue_raw if q_lower in r.get("client_name", "").lower()]
        upcoming_raw = [r for r in upcoming_raw if q_lower in r.get("client_name", "").lower()]
        suggested = [r for r in suggested if q_lower in r.get("client_name", "").lower()]
        insurance_suggestions = [r for r in insurance_suggestions if q_lower in r.get("client_name", "").lower()]

    subject_tpl = cfg.get("email_subject_followup", "Re: {{client_name}} — {{policy_type}} — {{subject}}")
    overdue = _add_mailto_subjects(overdue_raw, subject_tpl)
    upcoming = _add_mailto_subjects(upcoming_raw, subject_tpl)

    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    today_items = [r for r in upcoming if r.get("follow_up_date") == today_str]
    tomorrow_items = [r for r in upcoming if r.get("follow_up_date") == tomorrow_str]
    later_items = [r for r in upcoming if r.get("follow_up_date", "") > tomorrow_str]

    # ── 7 accountability/urgency buckets ──────────────────────────────
    all_items = overdue + upcoming
    stale_threshold = cfg.get("stale_threshold_days", 14)
    dispositions = cfg.get("follow_up_dispositions", [])
    today = date.today()

    # ── Enrich activity-sourced items with linked issue data ──────────
    # For activity items, look up their issue_id (if any) from activity_log,
    # then attach the linked issue's uid/subject/severity for badge display.
    activity_ids = [item["id"] for item in all_items if item.get("source") in ("activity", "project") and item.get("id")]
    if activity_ids:
        ph = ",".join("?" * len(activity_ids))
        issue_links = conn.execute(
            f"""SELECT a.id AS activity_id, a.issue_id,
                       iss.issue_uid AS linked_issue_uid,
                       iss.subject AS linked_issue_subject,
                       iss.issue_severity AS linked_issue_severity
                FROM activity_log a
                LEFT JOIN activity_log iss ON a.issue_id = iss.id AND iss.item_kind = 'issue'
                WHERE a.id IN ({ph})""",
            activity_ids,
        ).fetchall()
        _issue_link_map = {r["activity_id"]: dict(r) for r in issue_links}
        for item in all_items:
            if item.get("source") in ("activity", "project") and item.get("id"):
                link = _issue_link_map.get(item["id"])
                if link:
                    item["issue_id"] = link.get("issue_id")
                    item["linked_issue_uid"] = link.get("linked_issue_uid")
                    item["linked_issue_subject"] = link.get("linked_issue_subject")
                    item["linked_issue_severity"] = link.get("linked_issue_severity")

    buckets: dict[str, list[dict]] = {
        "triage": [], "today": [], "overdue": [], "stale": [],
        "nudge_due": [], "watching": [], "scheduled": [],
    }

    for item in all_items:
        bucket = _classify_item(item, today, stale_threshold, dispositions)
        # Ensure days_overdue is computed
        fu_date_val = item.get("follow_up_date", "")
        try:
            d = date.fromisoformat(fu_date_val)
            item["days_overdue"] = (today - d).days
        except (ValueError, TypeError):
            item["days_overdue"] = 0
        # Compute days from follow-up date to expiration for proximity warnings
        exp_val = item.get("expiration_date") or ""
        if exp_val and fu_date_val:
            try:
                exp_d = date.fromisoformat(exp_val[:10])
                fu_d = date.fromisoformat(fu_date_val[:10])
                item["days_fu_to_expiry"] = (exp_d - fu_d).days
            except (ValueError, TypeError):
                item["days_fu_to_expiry"] = None
        else:
            item["days_fu_to_expiry"] = None
        # Mark future my_action items in watching with "my turn" badge
        if bucket == "watching":
            disp = (item.get("disposition") or "").lower()
            acct = "my_action"
            for dd in dispositions:
                if dd.get("label", "").lower() == disp:
                    acct = dd.get("accountability", "my_action")
                    break
            item["is_my_turn"] = (acct == "my_action")
        buckets[bucket].append(item)

    # Compute nudge escalation tiers for nudge_due items
    for item in buckets["nudge_due"]:
        count, tier = _compute_nudge_tier(conn, item.get("policy_uid"), dispositions)
        item["nudge_count"] = count
        item["escalation_tier"] = tier

    # ── Cadence computation ──────────────────────────────────────────
    # For activity/project source items with a disposition that has default_days,
    # compute how far over cadence they are:
    #   on_cadence: days_overdue <= default_days
    #   mild: 1-2x over default_days
    #   severe: 2x+ over default_days
    _disp_days_map = {}
    for d in dispositions:
        label = d.get("label", "").lower()
        dd = d.get("default_days", 0)
        if dd and dd > 0:
            _disp_days_map[label] = dd

    for bucket_items in buckets.values():
        for item in bucket_items:
            src = item.get("source", "")
            if src not in ("activity", "project"):
                continue
            disp_label = (item.get("disposition") or "").lower()
            default_days = _disp_days_map.get(disp_label, 0)
            if default_days <= 0:
                continue
            days_over = item.get("days_overdue", 0)
            if days_over <= default_days:
                item["cadence"] = "on_cadence"
            elif days_over <= default_days * 2:
                item["cadence"] = "mild"
            else:
                item["cadence"] = "severe"

    # Inject overdue milestones into urgency tiers.
    # Skip milestones that already have an activity_log follow-up to avoid
    # double-counting from the dual mandated-activity / timeline systems.
    _activity_policy_uids = {
        item.get("policy_uid")
        for item in all_items
        if item.get("source") == "activity" and item.get("policy_uid")
    }
    # Also suppress milestones for policies covered by a program issue
    # that has an active follow-up (avoids double-listing).
    try:
        _covered_uids = conn.execute("""
            SELECT DISTINCT p.policy_uid
            FROM v_issue_policy_coverage ipc
            JOIN policies p ON p.id = ipc.policy_id
            JOIN activity_log a ON a.issue_id = ipc.issue_id
            WHERE a.item_kind != 'issue'
              AND a.follow_up_done = 0
              AND a.follow_up_date IS NOT NULL
        """).fetchall()
        _activity_policy_uids.update(r["policy_uid"] for r in _covered_uids)
    except Exception:
        logger.debug("Focus queue dedup query failed", exc_info=True)
    try:
        _ms_params = [today_str]
        _ms_excl_sql = ""
        if excluded:
            _ms_excl_sql = f" AND (p.renewal_status NOT IN ({','.join('?' * len(excluded))}) OR p.renewal_status IS NULL)"
            _ms_params.extend(excluded)
        milestone_rows = conn.execute(f"""
            SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
                   pt.ideal_date, pt.health, pt.accountability, pt.completed_date,
                   p.policy_type, p.carrier, p.project_name, p.project_id,
                   c.name AS client_name, c.id AS client_id, c.cn_number
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.projected_date <= ?
              AND pt.completed_date IS NULL
              AND p.archived = 0
              AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
              {_ms_excl_sql}
            ORDER BY pt.projected_date
        """, _ms_params).fetchall()

        for row in milestone_rows:
            item = dict(row)
            # Skip if an activity follow-up already covers this policy
            if item["policy_uid"] in _activity_policy_uids:
                continue
            item["source"] = "milestone"
            item["source_label"] = "Milestone"
            item["is_milestone"] = True
            item["follow_up_date"] = item["projected_date"]
            item["id"] = f"ms-{item['policy_uid']}-{item['milestone_name']}"
            try:
                days_past = (today - date.fromisoformat(item["projected_date"])).days
            except (ValueError, TypeError):
                days_past = 0
            item["days_overdue"] = days_past
            # Route through accountability classification
            ms_accountability = item.get("accountability") or "my_action"
            if ms_accountability == "waiting_external":
                buckets["nudge_due" if days_past >= 0 else "watching"].append(item)
            elif ms_accountability == "scheduled":
                buckets["scheduled"].append(item)
            elif days_past == 0:
                buckets["today"].append(item)
            elif days_past > stale_threshold:
                buckets["stale"].append(item)
            elif days_past > 0:
                buckets["overdue"].append(item)
    except Exception:
        logger.debug("policy_timeline query failed", exc_info=True)

    # Prep coming — timeline milestones whose prep_alert_date has arrived
    # Only include milestones whose projected_date is still in the future
    prep_coming: list[dict] = []
    try:
        _prep_params = [today_str, today_str]
        _prep_excl_sql = ""
        if excluded:
            _prep_excl_sql = f" AND (p.renewal_status NOT IN ({','.join('?' * len(excluded))}) OR p.renewal_status IS NULL)"
            _prep_params.extend(excluded)
        prep_rows = conn.execute(f"""
            SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
                   pt.prep_alert_date, pt.accountability, pt.health,
                   p.policy_type, p.carrier, p.project_name, p.project_id,
                   c.name AS client_name, c.id AS client_id, c.cn_number
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.prep_alert_date <= ? AND pt.projected_date > ?
              AND pt.completed_date IS NULL
              AND pt.prep_alert_date IS NOT NULL
              AND p.archived = 0
              AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
              {_prep_excl_sql}
            ORDER BY pt.projected_date
        """, _prep_params).fetchall()
        prep_coming = [dict(r) for r in prep_rows]
    except Exception:
        # policy_timeline table may not exist yet — degrade gracefully
        pass

    # Apply search filter to prep items
    if q:
        q_lower = q.lower()
        prep_coming = [r for r in prep_coming if q_lower in r.get("client_name", "").lower()]

    # ── Inject open issues with due_date into urgency buckets ────────
    try:
        issue_q = """
            SELECT a.id, a.subject, a.due_date, a.client_id, a.policy_id,
                   a.issue_uid, a.issue_status, a.issue_severity, a.item_kind,
                   a.program_id, a.activity_date, a.created_at,
                   c.name AS client_name, c.cn_number,
                   p.policy_uid, p.policy_type, p.carrier,
                   COALESCE(pr.name, p.project_name) AS project_name
            FROM activity_log a
            JOIN clients c ON a.client_id = c.id
            LEFT JOIN policies p ON a.policy_id = p.id
            LEFT JOIN projects prj ON a.program_id = prj.id
            LEFT JOIN projects pr ON COALESCE(a.project_id, p.project_id) = pr.id
            WHERE a.item_kind = 'issue'
              AND a.issue_status NOT IN ('Resolved', 'Closed')
              AND a.due_date IS NOT NULL
              AND a.auto_close_reason IS NULL
        """
        issue_params = []
        if filter_client_ids:
            issue_q += " AND a.client_id IN (" + ",".join("?" * len(filter_client_ids)) + ")"
            issue_params.extend(filter_client_ids)
        issue_rows = conn.execute(issue_q, issue_params).fetchall()

        for row in issue_rows:
            item = dict(row)
            if q and q.lower() not in (item.get("client_name") or "").lower():
                continue
            item["source"] = "issue"
            item["source_label"] = "Issue"
            item["follow_up_date"] = item["due_date"]  # for bucket classification
            try:
                due_d = date.fromisoformat(item["due_date"])
                days_to_due = (today - due_d).days
                item["days_overdue"] = days_to_due
            except (ValueError, TypeError):
                continue

            if days_to_due > stale_threshold:
                buckets["stale"].append(item)
            elif days_to_due > 0:
                buckets["overdue"].append(item)
            elif days_to_due == 0:
                buckets["today"].append(item)
            else:
                buckets["watching"].append(item)
    except Exception:
        logger.debug("Issues query failed", exc_info=True)

    # ── Recently auto-closed items ──────────────────────────────────
    auto_closed_days = cfg.get("auto_closed_section_days", 7)
    recently_auto_closed: list[dict] = []
    try:
        ac_rows = conn.execute("""
            SELECT a.id, a.subject, a.client_id, a.policy_id, a.item_kind,
                   a.follow_up_date, a.due_date, a.auto_close_reason,
                   a.auto_closed_at, a.auto_closed_by,
                   a.issue_uid, a.issue_status,
                   c.name AS client_name, c.cn_number,
                   p.policy_uid, p.policy_type, p.carrier
            FROM activity_log a
            JOIN clients c ON a.client_id = c.id
            LEFT JOIN policies p ON a.policy_id = p.id
            WHERE a.auto_close_reason IS NOT NULL
              AND a.auto_closed_at >= date('now', ?)
            ORDER BY a.auto_closed_at DESC
        """, (f'-{auto_closed_days} days',)).fetchall()
        recently_auto_closed = [dict(r) for r in ac_rows]
        if q:
            recently_auto_closed = [r for r in recently_auto_closed
                                    if q.lower() in (r.get("client_name") or "").lower()]
    except Exception:
        logger.debug("Auto-closed items query failed", exc_info=True)

    # Count items auto-closed today for the alert banner
    auto_closed_today_count = sum(
        1 for r in recently_auto_closed
        if (r.get("auto_closed_at") or "")[:10] == today_str
    )

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    return {
        # Legacy buckets (backward compat for existing row templates)
        "overdue": overdue,
        "upcoming": upcoming,
        "today_items": today_items,
        "tomorrow_items": tomorrow_items,
        "later_items": later_items,
        "suggested": suggested,
        "insurance_suggestions": insurance_suggestions,
        # Urgency-tier buckets (replaces act_now)
        "triage": buckets["triage"],
        "today_bucket": buckets["today"],
        "overdue_bucket": buckets["overdue"],
        "stale": buckets["stale"],
        # Backward compat — union of triage+today+overdue+stale for old template
        "act_now": buckets["triage"] + buckets["today"] + buckets["overdue"] + buckets["stale"],
        # Accountability buckets
        "nudge_due": buckets["nudge_due"],
        "prep_coming": prep_coming,
        "watching": buckets["watching"],
        "scheduled": buckets["scheduled"],
        # Filter state
        "window": window,
        "activity_type": activity_type,
        "q": q,
        "client_id": client_id,
        "all_clients": all_clients,
        "today": today_str,
        "activity_types": cfg.get("activity_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
        "followup_expiration_buffer_days": cfg.get("followup_expiration_buffer_days", 3),
        # Auto-close section
        "recently_auto_closed": recently_auto_closed,
        "auto_closed_today_count": auto_closed_today_count,
    }


def _inbox_ctx(conn, show_processed: bool = False) -> dict:
    """Build inbox tab context."""
    pending = [dict(r) for r in conn.execute("""
        SELECT i.*, c.name AS client_name, c.cn_number, ct.name AS contact_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN contacts ct ON i.contact_id = ct.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()]
    processed = []
    if show_processed:
        processed = [dict(r) for r in conn.execute("""
            SELECT i.*, c.name AS client_name, c.cn_number,
                   a.subject AS activity_subject, a.policy_uid AS activity_policy_uid,
                   p.project_id AS activity_project_id
            FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
            LEFT JOIN activity_log a ON i.activity_id = a.id
            LEFT JOIN policies p ON a.policy_uid = p.policy_uid
            WHERE i.status = 'processed'
            ORDER BY i.processed_at DESC LIMIT 50
        """).fetchall()]
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    return {
        "pending": pending,
        "processed": processed,
        "show_processed": show_processed,
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    }


def _activities_ctx(conn, days: int = 90, activity_type: str = "",
                    client_id: int = 0, q: str = "") -> dict:
    """Build activities tab context."""
    from policydb.web.routes.activities import _attach_pc_emails

    rows = [dict(r) for r in get_activities(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )]
    _attach_pc_emails(conn, rows)

    # Apply text search filter
    if q:
        q_lower = q.lower()
        rows = [r for r in rows if (
            q_lower in r.get("subject", "").lower()
            or q_lower in r.get("client_name", "").lower()
            or q_lower in (r.get("details") or "").lower()
        )]

    # Enrich activities with linked issue info (issue_uid, subject, severity)
    issue_ids = list({r["issue_id"] for r in rows if r.get("issue_id")})
    issue_info: dict = {}
    if issue_ids:
        ph = ",".join("?" * len(issue_ids))
        for ir in conn.execute(
            f"SELECT id, issue_uid, subject, issue_severity FROM activity_log "
            f"WHERE id IN ({ph}) AND item_kind = 'issue'",
            issue_ids,
        ).fetchall():
            issue_info[ir["id"]] = dict(ir)
    for r in rows:
        iss = issue_info.get(r.get("issue_id"))
        if iss:
            r["linked_issue_uid"] = iss["issue_uid"]
            r["linked_issue_subject"] = iss["subject"]
            r["linked_issue_severity"] = iss["issue_severity"]
        else:
            r["linked_issue_uid"] = None
            r["linked_issue_subject"] = None
            r["linked_issue_severity"] = None

    time_summary = get_time_summary(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )
    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    dispositions = cfg.get("follow_up_dispositions", [])
    disposition_labels = [d["label"] if isinstance(d, dict) else d for d in dispositions]

    return {
        "activities": rows,
        "time_summary": time_summary,
        "days": days,
        "activity_type": activity_type,
        "client_id": client_id,
        "q": q,
        "activity_types": cfg.get("activity_types", []),
        "disposition_labels": disposition_labels,
        "all_clients": all_clients,
        "issue_severities": cfg.get("issue_severities", []),
    }


def _scratchpads_ctx(conn) -> dict:
    """Aggregate non-empty scratchpads from all sources."""
    scratchpads = []
    # Dashboard
    dash = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()
    if dash and (dash["content"] or "").strip():
        scratchpads.append({
            "source": "dashboard", "label": "Dashboard", "link": "/",
            "content": dash["content"], "updated_at": dash["updated_at"],
        })
    # Client scratchpads
    for cs in conn.execute("""
        SELECT cs.client_id, cs.content, cs.updated_at, c.name AS client_name, c.cn_number
        FROM client_scratchpad cs JOIN clients c ON cs.client_id = c.id
        WHERE cs.content IS NOT NULL AND cs.content != ''
    """).fetchall():
        scratchpads.append({
            "source": "client", "label": cs["client_name"],
            "link": f"/clients/{cs['client_id']}",
            "content": cs["content"], "updated_at": cs["updated_at"],
            "client_id": cs["client_id"], "cn_number": cs["cn_number"],
        })
    # Policy scratchpads
    for ps in conn.execute("""
        SELECT ps.policy_uid, ps.content, ps.updated_at, p.policy_type,
               p.client_id, p.project_id, c.name AS client_name, c.cn_number
        FROM policy_scratchpad ps JOIN policies p ON ps.policy_uid = p.policy_uid
        JOIN clients c ON p.client_id = c.id
        WHERE ps.content IS NOT NULL AND ps.content != ''
    """).fetchall():
        scratchpads.append({
            "source": "policy",
            "label": f"{ps['client_name']} — {ps['policy_type']}",
            "link": f"/policies/{ps['policy_uid']}/edit",
            "content": ps["content"], "updated_at": ps["updated_at"],
            "client_id": ps["client_id"], "policy_uid": ps["policy_uid"],
            "cn_number": ps["cn_number"], "project_id": ps["project_id"],
        })
    return {"scratchpads": scratchpads}


def _portfolio_health_ctx(conn) -> dict:
    """Compute portfolio health counts by worst-health-per-policy from timeline."""
    counts = {"on_track": 0, "drifting": 0, "compressed": 0, "at_risk": 0, "critical": 0}
    try:
        rows = conn.execute("""
            SELECT policy_uid,
                   MIN(CASE health
                       WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                       WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4
                       ELSE 5 END) AS worst_rank
            FROM policy_timeline
            WHERE completed_date IS NULL
            GROUP BY policy_uid
        """).fetchall()
        rank_map = {1: "critical", 2: "at_risk", 3: "compressed", 4: "drifting", 5: "on_track"}
        for r in rows:
            h = rank_map.get(r["worst_rank"], "on_track")
            counts[h] = counts.get(h, 0) + 1
    except Exception:
        logger.debug("policy_timeline query failed", exc_info=True)
    return {"health_counts": [(k, v) for k, v in counts.items()]}


def _risk_alerts_ctx(conn) -> dict:
    """Return at_risk / critical timeline items for the risk alerts banner."""
    alerts: list[dict] = []
    try:
        rows = conn.execute("""
            SELECT DISTINCT pt.policy_uid, pt.health, pt.waiting_on,
                   pt.acknowledged, pt.acknowledged_at,
                   p.policy_type, p.expiration_date,
                   c.name AS client_name, c.id AS client_id,
                   CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_expiry,
                   CAST(julianday(pt.projected_date) - julianday(pt.ideal_date) AS INTEGER) AS drift_days
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.health IN ('at_risk', 'critical')
              AND pt.completed_date IS NULL
            ORDER BY CASE pt.health WHEN 'critical' THEN 0 ELSE 1 END, p.expiration_date
        """).fetchall()
        alerts = [dict(r) for r in rows]
    except Exception:
        logger.debug("policy_timeline query failed", exc_info=True)
    return {"risk_alerts": alerts}


# ── Issues context ───────────────────────────────────────────────────────────


def _issues_ctx(conn, q: str = "", client_id: int = 0, issue_type: str = "") -> dict:
    """Build issues tab context — open and recently resolved issues."""
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.details, a.client_id, a.policy_id,
               a.program_id, a.issue_status, a.issue_severity, a.issue_sla_days,
               a.resolution_type, a.resolution_notes, a.root_cause_category,
               a.resolved_date, a.activity_date, a.created_at, a.is_renewal_issue,
               c.name AS client_name,
               p.policy_uid, p.policy_type, p.carrier,
               pr.name AS location_name,
               (SELECT COUNT(*) FROM activity_log sub
                WHERE sub.issue_id = a.id) AS activity_count,
               (SELECT COALESCE(SUM(sub.duration_hours), 0) FROM activity_log sub
                WHERE sub.issue_id = a.id AND sub.duration_hours IS NOT NULL) AS total_hours,
               (SELECT MAX(sub.activity_date) FROM activity_log sub
                WHERE sub.issue_id = a.id) AS last_activity_date,
               julianday(?) - julianday(a.activity_date) AS days_open
        FROM activity_log a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN policies p ON p.id = a.policy_id
        LEFT JOIN projects pr ON pr.id = p.project_id
        WHERE a.item_kind = 'issue'
          AND a.issue_id IS NULL
          AND a.merged_into_id IS NULL
        ORDER BY
          CASE a.issue_severity
            WHEN 'Critical' THEN 0
            WHEN 'High' THEN 1
            WHEN 'Normal' THEN 2
            WHEN 'Low' THEN 3
            ELSE 4
          END,
          a.activity_date ASC
    """, (today,)).fetchall()
    issues = [dict(r) for r in rows]

    # Apply filters
    if client_id:
        issues = [i for i in issues if i.get("client_id") == client_id]
    if q:
        q_lower = q.lower()
        issues = [i for i in issues if
                  q_lower in (i.get("subject") or "").lower() or
                  q_lower in (i.get("client_name") or "").lower() or
                  q_lower in (i.get("details") or "").lower()]
    if issue_type == "renewal":
        issues = [i for i in issues if i.get("is_renewal_issue")]
    elif issue_type == "manual":
        issues = [i for i in issues if not i.get("is_renewal_issue")]

    # Bucket into sections
    severities = cfg.get("issue_severities", [])
    sla_map = {s["label"]: s.get("sla_days", 7) for s in severities}

    critical_overdue = []
    active = []
    waiting = []
    recently_resolved = []

    for issue in issues:
        status = issue.get("issue_status") or "Open"
        severity = issue.get("issue_severity") or "Normal"
        days_open = issue.get("days_open") or 0
        sla = issue.get("issue_sla_days") or sla_map.get(severity, 7)
        issue["sla_days"] = sla
        issue["over_sla"] = days_open > sla

        # List-view bucketing (existing logic)
        if status in ("Resolved", "Closed"):
            # Only show recently resolved (last 7 days)
            if issue.get("resolved_date"):
                from dateutil.parser import parse as dparse
                try:
                    days_since = (date.today() - dparse(issue["resolved_date"]).date()).days
                    if days_since <= 7:
                        recently_resolved.append(issue)
                except Exception:
                    pass
        elif severity == "Critical" or issue["over_sla"]:
            critical_overdue.append(issue)
        elif status == "Waiting":
            waiting.append(issue)
        else:
            active.append(issue)

    open_count = len(critical_overdue) + len(active) + len(waiting)

    # SLA breach stats (open issues only)
    open_issues = critical_overdue + active + waiting
    sla_breached = [i for i in open_issues if i.get("over_sla")]
    sla_breach_count = len(sla_breached)
    oldest_breach_days = 0
    if sla_breached:
        oldest_breach_days = max(
            int((i.get("days_open") or 0) - (i.get("sla_days") or 7))
            for i in sla_breached
        )

    # All clients for filter dropdown
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    # Resolve client name for autocomplete pre-fill
    client_name = ""
    if client_id:
        _cl = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
        if _cl:
            client_name = _cl["name"]

    # ── Consolidation suggestions: open standalone renewal issues sharing (client, project) ──
    cluster_rows = conn.execute("""
        SELECT a.client_id, c.name AS client_name,
               pr.id AS project_id, pr.name AS location_name,
               a.id AS issue_id, a.issue_uid, a.subject,
               p.policy_uid, p.policy_type, p.expiration_date
        FROM activity_log a
        JOIN clients c ON c.id = a.client_id
        JOIN policies p ON p.id = a.policy_id
        JOIN projects pr ON pr.id = p.project_id
        WHERE a.item_kind = 'issue'
          AND a.is_renewal_issue = 1
          AND a.issue_status NOT IN ('Resolved', 'Closed')
          AND a.merged_into_id IS NULL
          AND a.program_id IS NULL
        ORDER BY c.name, pr.name, p.expiration_date ASC, a.id ASC
    """).fetchall()

    clusters: dict[tuple[int, int], dict] = {}
    for r in cluster_rows:
        key = (r["client_id"], r["project_id"])
        if key not in clusters:
            clusters[key] = {
                "client_id": r["client_id"],
                "client_name": r["client_name"],
                "project_id": r["project_id"],
                "location_name": r["location_name"],
                "issues": [],
            }
        clusters[key]["issues"].append({
            "id": r["issue_id"],
            "issue_uid": r["issue_uid"],
            "subject": r["subject"],
            "policy_uid": r["policy_uid"],
            "policy_type": r["policy_type"],
            "expiration_date": r["expiration_date"],
        })
    consolidation_suggestions = [c for c in clusters.values() if len(c["issues"]) >= 2]

    return {
        "critical_overdue": critical_overdue,
        "active_issues": active,
        "waiting_issues": waiting,
        "recently_resolved": recently_resolved,
        "open_issues_count": open_count,
        "all_clients": all_clients,
        "q": q,
        "client_id": client_id,
        "client_name": client_name,
        "issue_type": issue_type,
        "issue_severities": cfg.get("issue_severities", []),
        "issue_lifecycle_states": cfg.get("issue_lifecycle_states", []),
        "sla_breach_count": sla_breach_count,
        "oldest_breach_days": oldest_breach_days,
        "consolidation_suggestions": consolidation_suggestions,
    }


# ── Anomalies ───────────────────────────────────────────────────────────────


def _anomalies_ctx(conn, category: str = "", q: str = "") -> dict:
    """Build anomalies tab context."""
    from policydb.anomaly_engine import get_anomaly_counts, get_all_active_anomalies
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)

    # Apply filters
    if category:
        anomalies = [a for a in anomalies if a.get("category") == category]
    if q:
        q_lower = q.lower()
        anomalies = [a for a in anomalies if
                     q_lower in (a.get("title") or "").lower() or
                     q_lower in (a.get("details") or "").lower() or
                     q_lower in (a.get("client_name") or "").lower()]

    total = sum(counts.values())
    return {
        "anomaly_counts": counts,
        "anomaly_total": total,
        "anomaly_list": anomalies,
        "anomaly_category": category,
        "anomaly_q": q,
    }


# ── Data Health ──────────────────────────────────────────────────────────────


def _data_health_ctx(conn) -> dict:
    """Build data-health tab context."""
    from policydb.data_health import get_missing_fields_report
    summary = get_book_health_summary(conn)
    missing_items = get_missing_fields_report(conn)
    return {"summary": summary, "missing_items": missing_items}


# ── Main page ────────────────────────────────────────────────────────────────


@router.get("/action-center", response_class=HTMLResponse)
def action_center_page(request: Request, tab: str = "", conn=Depends(get_db)):
    """Main Action Center page — renders shell with tabs and sidebar."""
    sidebar = _sidebar_ctx(conn)
    # Default tab content: follow-ups (loaded server-side for first render)
    initial_tab = tab or "focus"
    tab_ctx = {}
    if initial_tab == "focus":
        focus_items, waiting_items, fq_stats = build_focus_queue(conn, horizon_days=-999)
        time_summary = get_time_summary(conn)
        fq_stats["hours_today"] = time_summary.get("hours_today", 0)
        all_clients_fq = conn.execute(
            "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
        ).fetchall()
        all_contact_names_fq = [r[0] for r in conn.execute(
            "SELECT DISTINCT name FROM contacts WHERE name IS NOT NULL AND name != '' ORDER BY name"
        ).fetchall()]
        tab_ctx = {
            "focus_items": focus_items,
            "waiting_items": waiting_items,
            "stats": fq_stats,
            "guide_me": False,
            "horizon": "all",
            "client_id": 0,
            "all_clients": [dict(c) for c in all_clients_fq],
            "selected_client_name": "",
            "dispositions": cfg.get("follow_up_dispositions", []),
            "activity_types": cfg.get("activity_types", []),
            "all_contact_names": all_contact_names_fq,
        }
    elif initial_tab == "followups":
        tab_ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    elif initial_tab == "inbox":
        tab_ctx = _inbox_ctx(conn)
    elif initial_tab == "activities":
        tab_ctx = _activities_ctx(conn)
    elif initial_tab == "scratchpads":
        tab_ctx = _scratchpads_ctx(conn)
    elif initial_tab == "issues":
        tab_ctx = _issues_ctx(conn)
    elif initial_tab == "anomalies":
        tab_ctx = _anomalies_ctx(conn)
    elif initial_tab == "activity-review":
        tab_ctx = _activity_review_ctx(conn)
    elif initial_tab == "data-health":
        tab_ctx = _data_health_ctx(conn)
    # Always compute scratchpad count for tab badge
    scratchpad_count = 0
    if "scratchpads" not in tab_ctx:
        dash = conn.execute("SELECT content FROM user_notes WHERE id=1").fetchone()
        if dash and (dash["content"] or "").strip():
            scratchpad_count += 1
        scratchpad_count += conn.execute(
            "SELECT COUNT(*) FROM client_scratchpad WHERE content IS NOT NULL AND content != ''"
        ).fetchone()[0]
        scratchpad_count += conn.execute(
            "SELECT COUNT(*) FROM policy_scratchpad WHERE content IS NOT NULL AND content != ''"
        ).fetchone()[0]
    # Portfolio health + risk alerts (always shown)
    health_ctx = _portfolio_health_ctx(conn)
    risk_ctx = _risk_alerts_ctx(conn)
    # Accountability counts for sidebar badges
    if "stats" in tab_ctx and "focus_count" in tab_ctx.get("stats", {}):
        # Focus Queue mode — use focus queue stats
        act_now_count = tab_ctx["stats"]["focus_count"]
        nudge_due_count = tab_ctx["stats"].get("nudge_alert_count", 0)
    else:
        act_now_count = (
            len(tab_ctx.get("triage", []))
            + len(tab_ctx.get("today_bucket", []))
            + len(tab_ctx.get("overdue_bucket", []))
            + len(tab_ctx.get("stale", []))
        )
        nudge_due_count = len(tab_ctx.get("nudge_due", []))
    review_pending_count = get_pending_review_count(conn)
    # Issues count for tab badge (skip if already computed)
    if "open_issues_count" in tab_ctx:
        issues_count = tab_ctx["open_issues_count"]
    else:
        issues_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue' AND issue_id IS NULL "
            "AND merged_into_id IS NULL "
            "AND (issue_status IS NULL OR issue_status NOT IN ('Resolved','Closed'))"
        ).fetchone()[0]
    # Data health incomplete count for tab badge (always computed)
    if "summary" in tab_ctx:
        health_incomplete = tab_ctx["summary"]["incomplete_count"]
    else:
        health_incomplete = get_book_health_summary(conn)["incomplete_count"]
    # Anomaly count for tab badge
    if "anomaly_total" in tab_ctx:
        anomaly_total = tab_ctx["anomaly_total"]
    else:
        try:
            from policydb.anomaly_engine import get_anomaly_counts
            anomaly_total = sum(get_anomaly_counts(conn).values())
        except Exception:
            anomaly_total = 0
    ctx = {
        "request": request,
        "active": "action-center",
        "initial_tab": initial_tab,
        "scratchpad_count": scratchpad_count,
        "act_now_count": act_now_count,
        "nudge_due_count": nudge_due_count,
        "review_pending_count": review_pending_count,
        "issues_count": issues_count,
        "issue_severities": cfg.get("issue_severities", []),
        "health_incomplete": health_incomplete,
        "anomaly_total": anomaly_total,
        **sidebar,
        **tab_ctx,
        **health_ctx,
        **risk_ctx,
    }
    return templates.TemplateResponse("action_center/page.html", ctx)


# ── Tab partials (HTMX) ─────────────────────────────────────────────────────


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
    # Special values: "overdue" = -1, "today" = 0, "all" = -999, numeric = N days
    if custom_date:
        try:
            target = datetime.strptime(custom_date, "%Y-%m-%d").date()
            horizon_days = (target - date.today()).days
        except ValueError:
            horizon_days = -999
    elif horizon == "overdue":
        horizon_days = -1
    elif horizon == "today":
        horizon_days = 0
    elif horizon == "all":
        horizon_days = -999
    else:
        horizon_days = int(horizon) if horizon.isdigit() else -999

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
        promoted = None
        new_waiting = []
        for w in waiting_items:
            if w.get("id") == promote:
                promoted = w
            else:
                new_waiting.append(w)
        if promoted:
            promoted["accountability"] = "my_action"
            focus_items.insert(0, promoted)
            waiting_items = new_waiting
            stats["focus_count"] += 1
            stats["waiting_count"] -= 1

    # Add hours today to stats
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

    # Contact names for autocomplete in completion form
    all_contact_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM contacts WHERE name IS NOT NULL AND name != '' ORDER BY name"
    ).fetchall()]

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
            "all_contact_names": all_contact_names,
        },
    )


@router.get("/action-center/followups", response_class=HTMLResponse)
def ac_followups(
    request: Request,
    window: int = 30,
    activity_type: str = "",
    q: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    ctx = _followups_ctx(conn, window, activity_type, q, client_id=client_id)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


@router.get("/action-center/inbox", response_class=HTMLResponse)
def ac_inbox(
    request: Request,
    show_processed: str = "",
    conn=Depends(get_db),
):
    ctx = _inbox_ctx(conn, show_processed=bool(show_processed))
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_inbox.html", ctx)


@router.get("/action-center/activities", response_class=HTMLResponse)
def ac_activities(
    request: Request,
    days: int = 90,
    activity_type: str = "",
    client_id: str = "",
    q: str = "",
    conn=Depends(get_db),
):
    _cid = int(client_id) if str(client_id).strip().isdigit() else 0
    ctx = _activities_ctx(conn, days=days, activity_type=activity_type,
                          client_id=_cid, q=q)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activities.html", ctx)


@router.get("/action-center/scratchpads", response_class=HTMLResponse)
def ac_scratchpads(request: Request, conn=Depends(get_db)):
    ctx = _scratchpads_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_scratchpads.html", ctx)


@router.get("/action-center/issues", response_class=HTMLResponse)
def ac_issues(
    request: Request,
    q: str = "",
    client_id: int = 0,
    issue_type: str = "",
    conn=Depends(get_db),
):
    ctx = _issues_ctx(conn, q=q, client_id=client_id, issue_type=issue_type)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


@router.get("/action-center/anomalies", response_class=HTMLResponse)
def ac_anomalies(
    request: Request,
    category: str = "",
    q: str = "",
    conn=Depends(get_db),
):
    """Anomalies tab partial — lazy loaded."""
    ctx = _anomalies_ctx(conn, category=category, q=q)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_anomalies_tab.html", ctx)


@router.get("/action-center/data-health", response_class=HTMLResponse)
def ac_data_health(request: Request, conn=Depends(get_db)):
    """Data Health tab partial — lazy loaded."""
    ctx = _data_health_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_data_health.html", ctx)


@router.get("/action-center/sidebar", response_class=HTMLResponse)
def ac_sidebar(request: Request, conn=Depends(get_db)):
    ctx = _sidebar_ctx(conn)
    health_ctx = _portfolio_health_ctx(conn)
    ctx.update(health_ctx)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_sidebar.html", ctx)


# ── Risk alert acknowledge ────────────────────────────────────────────────────


@router.post("/action-center/acknowledge/{policy_uid}", response_class=HTMLResponse)
def acknowledge_alert(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Mark at_risk/critical timeline items as acknowledged for a policy."""
    now = datetime.now().isoformat()
    try:
        conn.execute("""
            UPDATE policy_timeline SET acknowledged = 1, acknowledged_at = ?
            WHERE policy_uid = ? AND health IN ('at_risk', 'critical')
        """, (now, policy_uid))
        conn.commit()
    except Exception:
        logger.exception("Failed to acknowledge alert for %s", policy_uid)
        return HTMLResponse("Acknowledge failed", status_code=500)
    # Return the updated alert row
    try:
        row = conn.execute("""
            SELECT DISTINCT pt.policy_uid, pt.health, pt.waiting_on,
                   pt.acknowledged, pt.acknowledged_at,
                   p.policy_type, p.expiration_date,
                   c.name AS client_name, c.id AS client_id,
                   CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_expiry,
                   CAST(julianday(pt.projected_date) - julianday(pt.ideal_date) AS INTEGER) AS drift_days
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.policy_uid = ? AND pt.health IN ('at_risk', 'critical')
              AND pt.completed_date IS NULL
            LIMIT 1
        """, (policy_uid,)).fetchone()
        if row:
            alert = dict(row)
            return templates.TemplateResponse(
                "action_center/_risk_alert_row.html",
                {"request": request, "alert": alert},
            )
    except Exception:
        logger.debug("Could not fetch alert row for %s after acknowledge", policy_uid)
    # Fallback: return empty (row disappears — acknowledged successfully)
    return HTMLResponse("")


# ── Triage disposition ───────────────────────────────────────────────────────


@router.post("/policies/{policy_uid}/milestone/{milestone_name}/complete", response_class=HTMLResponse)
def complete_timeline_milestone_endpoint(
    request: Request,
    policy_uid: str,
    milestone_name: str,
    conn=Depends(get_db),
):
    """Complete a timeline milestone and re-render the followups tab."""
    import urllib.parse
    from policydb.timeline_engine import complete_timeline_milestone
    decoded_name = urllib.parse.unquote(milestone_name)
    complete_timeline_milestone(conn, policy_uid, decoded_name)
    conn.commit()
    # Re-render the full followups tab
    ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


@router.post("/policies/{policy_uid}/milestone/{milestone_name}/defer", response_class=Response)
def defer_timeline_milestone_endpoint(
    policy_uid: str,
    milestone_name: str,
    days: int = Form(7),
    conn=Depends(get_db),
):
    """Defer a milestone by N days from the Focus Queue."""
    import urllib.parse
    from policydb.timeline_engine import defer_timeline_milestone
    decoded_name = urllib.parse.unquote(milestone_name)
    defer_timeline_milestone(conn, policy_uid, decoded_name, days=days)
    return Response(status_code=200)


@router.post("/action-center/set-disposition/{activity_id}", response_class=HTMLResponse)
def set_disposition(
    request: Request,
    activity_id: int,
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    """Set disposition on a triage activity item and re-render followups tab."""
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition = ? WHERE id = ?",
            (disposition, activity_id),
        )
        conn.commit()
    # Re-render the full followups tab so the item moves to its new section
    ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


# ── Activity Review ──────────────────────────────────────────────────────────

# Track last backfill completion (resets on server restart)
_backfill_done: bool = False


def _activity_review_ctx(conn, scan_date: str = "") -> dict:
    """Build context for the Activity Review tab."""
    global _backfill_done
    today = date.today().isoformat()
    target_date = scan_date or today

    if not scan_date:
        # Always scan today (picks up new work since last visit)
        scan_for_unlogged_sessions(conn, today, today)

        # On first visit after server start, also backfill last 7 days
        if not _backfill_done:
            lookback = (date.today() - timedelta(days=7)).isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            scan_for_unlogged_sessions(conn, lookback, yesterday)
            _backfill_done = True

    suggestions = get_pending_suggestions(conn)
    activity_types = cfg.get("activity_types", [])
    default_activity_type = cfg.get("default_review_activity_type", "Other")

    return {
        "suggestions": suggestions,
        "review_count": len(suggestions),
        "scan_date": target_date,
        "activity_types": activity_types,
        "default_activity_type": default_activity_type,
    }


@router.get("/action-center/activity-review", response_class=HTMLResponse)
def ac_activity_review(request: Request, conn=Depends(get_db)):
    """Activity Review tab partial — lazy loaded."""
    ctx = _activity_review_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activity_review.html", ctx)


@router.post("/action-center/activity-review/scan", response_class=HTMLResponse)
def ac_activity_review_scan(
    request: Request,
    scan_date: str = Form(""),
    conn=Depends(get_db),
):
    """Run activity review scan for a specific date."""
    target = scan_date or date.today().isoformat()
    scan_for_unlogged_sessions(conn, target, target)

    ctx = _activity_review_ctx(conn, scan_date=target)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activity_review.html", ctx)


@router.post("/action-center/activity-review/{suggestion_id}/dismiss", response_class=HTMLResponse)
def ac_dismiss_suggestion(
    request: Request,
    suggestion_id: int,
    conn=Depends(get_db),
):
    """Soft-dismiss a suggested activity (resurfaces after configured days)."""
    dismiss_days = cfg.get("review_dismiss_days", 7)
    now = datetime.now()
    expires = (now + timedelta(days=dismiss_days)).isoformat()
    conn.execute(
        """UPDATE suggested_activities
           SET status = 'dismissed', dismissed_at = ?, dismiss_expires_at = ?
           WHERE id = ?""",
        (now.isoformat(), expires, suggestion_id),
    )
    conn.commit()

    # Return empty string to remove the card + OOB badge update
    count = get_pending_review_count(conn)
    badge_html = f'<span id="review-badge" hx-swap-oob="true">'
    if count:
        badge_html += f'<span class="tab-badge" style="background:#f59e0b;color:white">{count}</span>'
    badge_html += '</span>'
    return HTMLResponse(badge_html)


@router.post("/action-center/activity-review/{suggestion_id}/log", response_class=HTMLResponse)
def ac_log_suggestion(
    request: Request,
    suggestion_id: int,
    activity_type: str = Form(""),
    subject: str = Form(""),
    details: str = Form(""),
    duration_hours: float = Form(0.1),
    activity_date: str = Form(""),
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Log a suggested activity as a real activity_log entry."""
    # Look up the suggestion to get client_id
    suggestion = conn.execute(
        "SELECT * FROM suggested_activities WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if not suggestion:
        return HTMLResponse("")

    client_id = suggestion["client_id"]
    act_date = activity_date or suggestion["session_date"]

    # Resolve policy_id from policy_uid if provided
    policy_id = None
    if policy_uid:
        prow = conn.execute(
            "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
        ).fetchone()
        if prow:
            policy_id = prow["id"]

    # Insert activity
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject,
            details, duration_hours, account_exec, follow_up_done)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Grant', 0)""",
        (act_date, client_id, policy_id, activity_type or "Other",
         subject or "Account work (from review)", details, duration_hours),
    )
    activity_id = cursor.lastrowid

    # Mark suggestion as logged
    conn.execute(
        """UPDATE suggested_activities
           SET status = 'logged', logged_activity_id = ?
           WHERE id = ?""",
        (activity_id, suggestion_id),
    )
    conn.commit()

    # Return empty + OOB badge update
    count = get_pending_review_count(conn)
    badge_html = f'<span id="review-badge" hx-swap-oob="true">'
    if count:
        badge_html += f'<span class="tab-badge" style="background:#f59e0b;color:white">{count}</span>'
    badge_html += '</span>'
    return HTMLResponse(badge_html)


# ── Suggested Contacts ───────────────────────────────────────────────────────


@router.get("/action-center/suggested-contacts", response_class=HTMLResponse)
def suggested_contacts_list(request: Request, conn=Depends(get_db)):
    """Return suggested contacts panel HTML."""
    rows = [dict(r) for r in conn.execute(
        """SELECT id, email, parsed_name, organization, client_id, client_name,
                  source_subject, first_seen_at, last_seen_at, seen_count
           FROM suggested_contacts
           WHERE status = 'pending' AND blocked = 0
           ORDER BY seen_count DESC, last_seen_at DESC"""
    ).fetchall()]
    clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients ORDER BY name"
    ).fetchall()]
    return templates.TemplateResponse("action_center/_suggested_contacts.html", {
        "request": request, "suggestions": rows, "clients": clients,
    })


@router.post("/action-center/suggested-contacts/{sc_id}/add", response_class=HTMLResponse)
def suggested_contact_add(sc_id: int, request: Request, client_id: int = Form(0), conn=Depends(get_db)):
    """Add a suggested contact to the contacts table and assign to client."""
    row = conn.execute("SELECT * FROM suggested_contacts WHERE id=?", (sc_id,)).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    # Use provided client_id override, or fall back to the suggestion's client_id
    target_client_id = client_id or row["client_id"]
    email = row["email"]
    name = row["parsed_name"] or email.split("@")[0].replace(".", " ").title()
    org = row["organization"] or ""

    # Create contact (skip if email already exists)
    existing = conn.execute(
        "SELECT id FROM contacts WHERE LOWER(TRIM(email))=?", (email.lower().strip(),)
    ).fetchone()
    if existing:
        contact_id = existing["id"]
    else:
        cursor = conn.execute(
            "INSERT INTO contacts (name, email, organization) VALUES (?, ?, ?)",
            (name, email, org),
        )
        contact_id = cursor.lastrowid

    # Assign to client if we have one
    if target_client_id:
        exists = conn.execute(
            "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND client_id=?",
            (contact_id, target_client_id),
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO contact_client_assignments (contact_id, client_id, contact_type) VALUES (?, ?, 'client')",
                (contact_id, target_client_id),
            )

    conn.execute("UPDATE suggested_contacts SET status='added' WHERE id=?", (sc_id,))
    conn.commit()

    # Re-render the list
    return suggested_contacts_list(request, conn)


@router.post("/action-center/suggested-contacts/{sc_id}/dismiss", response_class=HTMLResponse)
def suggested_contact_dismiss(sc_id: int, request: Request, block: int = Form(0), conn=Depends(get_db)):
    """Dismiss a suggested contact. If block=1, permanently suppress this email."""
    conn.execute(
        "UPDATE suggested_contacts SET status='dismissed', blocked=? WHERE id=?",
        (1 if block else 0, sc_id),
    )
    conn.commit()
    return suggested_contacts_list(request, conn)
