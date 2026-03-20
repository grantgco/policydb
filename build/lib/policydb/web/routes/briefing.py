"""Weekly Briefing route — unified review surface."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from policydb import config as cfg
from policydb.queries import (
    get_activities,
    get_all_clients,
    get_all_followups,
    get_client_by_id,
    get_escalation_alerts,
    get_linked_group_for_client,
    get_linked_group_overview,
    get_renewal_metrics,
    get_renewal_pipeline,
    get_stale_renewals,
    get_suggested_followups,
    get_time_summary,
)
from policydb.web.app import get_db, templates

router = APIRouter()


def _enrich_action_queue(conn, action_queue: list[dict]):
    """Attach carrier, renewal_status, readiness, last activity, and contact info to action queue items."""
    today = date.today()

    # Collect policy IDs for batch queries
    policy_ids = [item["id"] for item in action_queue if item.get("id")]
    policy_uids = [item["policy_uid"] for item in action_queue if item.get("policy_uid")]

    # Batch-fetch last activity per policy
    last_activity_map: dict = {}
    if policy_ids:
        ph = ",".join("?" * len(policy_ids))
        rows = conn.execute(
            f"SELECT policy_id, MAX(activity_date) AS last_date, "  # noqa: S608
            f"(SELECT activity_type FROM activity_log a2 WHERE a2.policy_id = a1.policy_id ORDER BY activity_date DESC, id DESC LIMIT 1) AS last_type "
            f"FROM activity_log a1 WHERE policy_id IN ({ph}) GROUP BY policy_id",
            policy_ids,
        ).fetchall()
        last_activity_map = {r["policy_id"]: {"date": r["last_date"], "type": r["last_type"]} for r in rows}

    # Batch-fetch contact info (primary contact email) per policy
    contact_map: dict = {}
    if policy_ids:
        ph = ",".join("?" * len(policy_ids))
        rows = conn.execute(
            f"SELECT p.id AS policy_id, co.name AS contact_name, co.email AS contact_email "  # noqa: S608
            f"FROM policies p "
            f"JOIN contact_client_assignments cca ON cca.client_id = p.client_id AND cca.contact_type = 'client' AND cca.is_primary = 1 "
            f"JOIN contacts co ON cca.contact_id = co.id "
            f"WHERE p.id IN ({ph})",
            policy_ids,
        ).fetchall()
        contact_map = {r["policy_id"]: {"name": r["contact_name"], "email": r["contact_email"]} for r in rows}

    # Attach readiness scores to items with policy context
    policy_items = [item for item in action_queue if item.get("policy_uid") and item.get("days_to_renewal") is not None]
    if policy_items:
        from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
        _attach_milestone_progress(conn, policy_items)
        _attach_readiness_score(conn, policy_items)

    for item in action_queue:
        pid = item.get("id")
        # Last activity
        la = last_activity_map.get(pid, {})
        item["last_activity_date"] = la.get("date")
        item["last_activity_type"] = la.get("type")
        if la.get("date"):
            try:
                item["last_activity_days_ago"] = (today - date.fromisoformat(la["date"])).days
            except (ValueError, TypeError):
                item["last_activity_days_ago"] = None

        # Contact info
        ci = contact_map.get(pid, {})
        if not item.get("contact_person"):
            item["contact_person"] = ci.get("name")
        if not item.get("contact_email"):
            item["contact_email"] = ci.get("email")


@router.get("/briefing", response_class=HTMLResponse)
def briefing_page(request: Request, days: int = 7, conn=Depends(get_db)):
    excluded = cfg.get("renewal_statuses_excluded", [])

    # Section 1: Action Queue
    escalation_alerts = get_escalation_alerts(conn, excluded_statuses=excluded)
    overdue, upcoming = get_all_followups(conn, window=7)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded)

    # Merge into prioritized action queue
    action_queue = []
    for a in escalation_alerts:
        d = dict(a)
        _t = d.get("escalation_tier")
        if _t == "CRITICAL":
            d["_priority"] = 0
        elif _t == "WARNING":
            d["_priority"] = 2
        else:
            d["_priority"] = 3
        d["_source"] = "escalation"
        # Build human-readable reason
        _days = d.get("days_to_renewal")
        _status = d.get("renewal_status", "Not Started")
        if _t == "CRITICAL":
            d["reason"] = f"Expires in {_days}d — \"{_status}\" with no recent activity"
        elif _t == "WARNING":
            d["reason"] = f"Expires in {_days}d — still \"{_status}\""
        elif _t == "NUDGE":
            d["reason"] = f"Expires in {_days}d — no follow-up scheduled"
        action_queue.append(d)
    for o in overdue:
        d = dict(o)
        d["_priority"] = 1
        d["_source"] = "overdue"
        action_queue.append(d)
    for u in upcoming:
        d = dict(u)
        d["_priority"] = 4
        d["_source"] = "upcoming"
        action_queue.append(d)
    for s in suggested:
        d = dict(s)
        d["_priority"] = 5
        d["_source"] = "suggested"
        action_queue.append(d)
    action_queue.sort(key=lambda x: (x["_priority"], x.get("days_overdue", 0) * -1 if x.get("days_overdue") else 0))

    # Enrich action queue with carrier, readiness, last activity, contacts
    _enrich_action_queue(conn, action_queue)

    # Section 2: This Week's Work
    activities = [dict(r) for r in get_activities(conn, days=days)]
    time_summary = get_time_summary(conn, days=days)
    clients_touched = len({a["client_id"] for a in activities if a.get("client_id")})
    policies_touched = len({a.get("policy_uid") for a in activities if a.get("policy_uid")})
    total_clients = conn.execute("SELECT COUNT(*) AS n FROM clients WHERE archived = 0").fetchone()["n"]

    # Section 3: Pipeline Snapshot
    metrics = get_renewal_metrics(conn)
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded)]
    stale = [dict(r) for r in get_stale_renewals(conn, excluded_statuses=excluded)]
    # Top 5 most urgent
    top_urgent = sorted(pipeline, key=lambda p: p.get("days_to_renewal") or 9999)[:5]

    # Section: Open RFIs needing attention
    today_iso = date.today().isoformat()
    rfi_rows = conn.execute(
        """SELECT b.id, b.client_id, b.title, b.status, b.send_by_date, b.sent_at, b.created_at, b.rfi_uid,
                  c.name AS client_name,
                  COUNT(i.id) AS item_total,
                  SUM(CASE WHEN i.received=0 THEN 1 ELSE 0 END) AS outstanding
           FROM client_request_bundles b
           JOIN clients c ON b.client_id = c.id AND c.archived = 0
           LEFT JOIN client_request_items i ON i.bundle_id = b.id
           WHERE b.status IN ('open', 'sent', 'partial')
           GROUP BY b.id
           ORDER BY
             CASE WHEN b.status='open' AND (b.send_by_date IS NULL OR b.send_by_date <= ?) THEN 0 ELSE 1 END,
             b.send_by_date ASC,
             b.created_at ASC""",
        (today_iso,),
    ).fetchall()
    rfi_alerts = []
    for r in rfi_rows:
        d = dict(r)
        outstanding = d.get("outstanding") or 0
        if d["status"] == "open" and not d.get("send_by_date"):
            d["_rfi_tier"] = "no_deadline"
        elif d["status"] == "open" and d.get("send_by_date") and d["send_by_date"] <= today_iso:
            d["_rfi_tier"] = "overdue"
        elif d["status"] in ("sent", "partial") and outstanding > 0:
            d["_rfi_tier"] = "awaiting"
        else:
            d["_rfi_tier"] = "ok"
        rfi_alerts.append(d)
    # Sort: overdue first, then no_deadline, then awaiting, then ok
    _rfi_order = {"overdue": 0, "no_deadline": 1, "awaiting": 2, "ok": 3}
    rfi_alerts.sort(key=lambda x: _rfi_order.get(x["_rfi_tier"], 9))

    # Section 4: Notes & Scratchpad
    note_row = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()
    scratchpad_content = note_row["content"] if note_row else ""
    scratchpad_updated = note_row["updated_at"] if note_row else ""

    from policydb.queries import get_recent_saved_notes
    recent_saved_notes = get_recent_saved_notes(conn, limit=10)

    recent_internal_notes = [dict(r) for r in conn.execute(
        """SELECT id, name, notes, updated_at FROM clients
           WHERE archived = 0 AND notes IS NOT NULL AND TRIM(notes) != ''
           ORDER BY updated_at DESC LIMIT 10"""
    ).fetchall()]

    return templates.TemplateResponse("briefing.html", {
        "request": request,
        "active": "briefing",
        "days": days,
        "today": date.today().isoformat(),
        "today_iso": today_iso,
        "action_queue": action_queue,
        "rfi_alerts": rfi_alerts,
        "activities": activities[:20],
        "time_summary": time_summary,
        "clients_touched": clients_touched,
        "policies_touched": policies_touched,
        "total_clients": total_clients,
        "metrics": metrics,
        "stale_count": len(stale),
        "top_urgent": top_urgent,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "scratchpad_content": scratchpad_content,
        "scratchpad_updated": scratchpad_updated,
        "recent_saved_notes": recent_saved_notes,
        "recent_internal_notes": recent_internal_notes,
    })


def _build_briefing_action_queue(conn, client_ids: list[int] | None = None):
    """Build a prioritized action queue, optionally filtered to specific clients."""
    excluded = cfg.get("renewal_statuses_excluded", [])
    escalation_alerts = get_escalation_alerts(conn, excluded_statuses=excluded, client_ids=client_ids)
    overdue, upcoming = get_all_followups(conn, window=7, client_ids=client_ids)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded, client_ids=client_ids)

    action_queue: list[dict] = []
    for a in escalation_alerts:
        d = dict(a)
        _t = d.get("escalation_tier")
        d["_priority"] = 0 if _t == "CRITICAL" else (2 if _t == "WARNING" else 3)
        d["_source"] = "escalation"
        _days = d.get("days_to_renewal")
        _status = d.get("renewal_status", "Not Started")
        if _t == "CRITICAL":
            d["reason"] = f"Expires in {_days}d — \"{_status}\" with no recent activity"
        elif _t == "WARNING":
            d["reason"] = f"Expires in {_days}d — still \"{_status}\""
        elif _t == "NUDGE":
            d["reason"] = f"Expires in {_days}d — no follow-up scheduled"
        action_queue.append(d)
    for o in overdue:
        d = dict(o); d["_priority"] = 1; d["_source"] = "overdue"; action_queue.append(d)
    for u in upcoming:
        d = dict(u); d["_priority"] = 4; d["_source"] = "upcoming"; action_queue.append(d)
    for s in suggested:
        d = dict(s); d["_priority"] = 5; d["_source"] = "suggested"; action_queue.append(d)
    action_queue.sort(key=lambda x: (x["_priority"], x.get("days_overdue", 0) * -1 if x.get("days_overdue") else 0))
    _enrich_action_queue(conn, action_queue)
    return action_queue


def _build_rfi_alerts(conn, client_ids: list[int] | None = None):
    """Build RFI alerts list, optionally filtered to specific clients."""
    today_iso = date.today().isoformat()
    client_filter = ""
    params: list = []
    if client_ids:
        ph = ",".join("?" * len(client_ids))
        client_filter = f" AND b.client_id IN ({ph})"
        params.extend(client_ids)
    # The ORDER BY uses one ? for today_iso comparison
    params.append(today_iso)
    rfi_rows = conn.execute(
        f"""SELECT b.id, b.client_id, b.title, b.status, b.send_by_date, b.sent_at, b.created_at, b.rfi_uid,
                  c.name AS client_name,
                  COUNT(i.id) AS item_total,
                  SUM(CASE WHEN i.received=0 THEN 1 ELSE 0 END) AS outstanding
           FROM client_request_bundles b
           JOIN clients c ON b.client_id = c.id AND c.archived = 0
           LEFT JOIN client_request_items i ON i.bundle_id = b.id
           WHERE b.status IN ('open', 'sent', 'partial'){client_filter}
           GROUP BY b.id
           ORDER BY
             CASE WHEN b.status='open' AND (b.send_by_date IS NULL OR b.send_by_date <= ?) THEN 0 ELSE 1 END,
             b.send_by_date ASC, b.created_at ASC""",
        params,
    ).fetchall()
    rfi_alerts = []
    for r in rfi_rows:
        d = dict(r)
        outstanding = d.get("outstanding") or 0
        if d["status"] == "open" and not d.get("send_by_date"):
            d["_rfi_tier"] = "no_deadline"
        elif d["status"] == "open" and d.get("send_by_date") and d["send_by_date"] <= today_iso:
            d["_rfi_tier"] = "overdue"
        elif d["status"] in ("sent", "partial") and outstanding > 0:
            d["_rfi_tier"] = "awaiting"
        else:
            d["_rfi_tier"] = "ok"
        rfi_alerts.append(d)
    _rfi_order = {"overdue": 0, "no_deadline": 1, "awaiting": 2, "ok": 3}
    rfi_alerts.sort(key=lambda x: _rfi_order.get(x["_rfi_tier"], 9))
    return rfi_alerts


# ── Client search (for combobox on book briefing) ────────────────────────────

@router.get("/briefing/clients/search", response_class=HTMLResponse)
def briefing_client_search(request: Request, q: str = "", conn=Depends(get_db)):
    """HTMX partial: client combobox results for briefing navigation."""
    clients = [dict(r) for r in conn.execute(
        "SELECT id, name, cn_number, industry_segment FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()]

    # Attach linked group info
    group_map: dict = {}
    for row in conn.execute(
        "SELECT m.client_id, g.id AS group_id, g.label FROM client_group_members m JOIN client_groups g ON m.group_id = g.id"
    ).fetchall():
        group_map[row["client_id"]] = {"group_id": row["group_id"], "group_label": row["label"]}

    q_stripped = q.strip()
    if q_stripped:
        from rapidfuzz import fuzz
        q_lower = q_stripped.lower()
        matches = []
        for c in clients:
            score = max(
                fuzz.partial_ratio(q_lower, (c["name"] or "").lower()),
                fuzz.partial_ratio(q_lower, (c.get("cn_number") or "").lower()),
            )
            if score >= 50:
                grp = group_map.get(c["id"], {})
                c["group_id"] = grp.get("group_id")
                c["group_label"] = grp.get("group_label")
                c["_score"] = score
                matches.append(c)
        matches.sort(key=lambda x: x["_score"], reverse=True)
        matches = matches[:10]
    else:
        # No query — return all clients (prepopulated list on focus)
        matches = []
        for c in clients:
            grp = group_map.get(c["id"], {})
            c["group_id"] = grp.get("group_id")
            c["group_label"] = grp.get("group_label")
            matches.append(c)

    return templates.TemplateResponse("briefing/_client_selector.html", {
        "request": request,
        "matches": matches,
    })


# ── Client Briefing ──────────────────────────────────────────────────────────

@router.get("/briefing/client/{client_id}", response_class=HTMLResponse)
def client_briefing(request: Request, client_id: int, days: int = 7, conn=Depends(get_db)):
    """Full-page client briefing."""
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)
    ids = [client_id]
    excluded = cfg.get("renewal_statuses_excluded", [])

    # Check linked group
    linked_group = get_linked_group_for_client(conn, client_id)
    group_id = None
    if linked_group:
        group_id = linked_group["group"].get("id") if linked_group.get("group") else None

    # Action queue
    action_queue = _build_briefing_action_queue(conn, client_ids=ids)

    # RFI alerts
    rfi_alerts = _build_rfi_alerts(conn, client_ids=ids)

    # Work summary
    activities = [dict(r) for r in get_activities(conn, client_ids=ids, days=days)]
    time_summary = get_time_summary(conn, client_ids=ids, days=days)
    policies_touched = len({a.get("policy_uid") for a in activities if a.get("policy_uid")})

    # Pipeline
    metrics = get_renewal_metrics(conn, client_ids=ids)
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded, client_ids=ids)]
    stale = [dict(r) for r in get_stale_renewals(conn, excluded_statuses=excluded, client_ids=ids)]

    # Account summary data (financials, renewals, coverage, risks)
    from policydb.exporter import build_account_summary
    summary = build_account_summary(conn, client_id, days=days)

    # Client scratchpad
    scratch_row = conn.execute(
        "SELECT content, updated_at FROM client_scratchpad WHERE client_id = ?", (client_id,)
    ).fetchone()
    scratchpad_content = scratch_row["content"] if scratch_row else ""
    scratchpad_updated = scratch_row["updated_at"] if scratch_row else ""

    # Client internal notes
    from policydb.queries import get_saved_notes, build_effort_projection
    client_notes = client.get("notes") or ""
    saved_notes = get_saved_notes(conn, scope="client", scope_id=str(client_id))

    # Effort projection
    effort_projection = build_effort_projection(conn, client_id)

    return templates.TemplateResponse("briefing_client.html", {
        "request": request,
        "active": "briefing",
        "days": days,
        "today_iso": date.today().isoformat(),
        "client": client,
        "client_id": client_id,
        "linked_group": linked_group,
        "group_id": group_id,
        "action_queue": action_queue,
        "rfi_alerts": rfi_alerts,
        "activities": activities[:20],
        "time_summary": time_summary,
        "policies_touched": policies_touched,
        "metrics": metrics,
        "stale_count": len(stale),
        "pipeline": pipeline,
        "s": summary,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "scratchpad_content": scratchpad_content,
        "scratchpad_updated": scratchpad_updated,
        "client_notes": client_notes,
        "saved_notes": saved_notes,
        "effort_projection": effort_projection,
    })


@router.get("/briefing/client/{client_id}/text")
def client_briefing_text(client_id: int, days: int = 90, conn=Depends(get_db)):
    """Plain text export of client briefing for clipboard."""
    from policydb.exporter import build_account_summary, render_account_summary_text
    summary = build_account_summary(conn, client_id, days=days)
    text = render_account_summary_text(summary)
    return PlainTextResponse(text)


@router.get("/briefing/client/{client_id}/print", response_class=HTMLResponse)
def client_briefing_print(request: Request, client_id: int, days: int = 7, conn=Depends(get_db)):
    """Print-optimized client briefing for PDF export."""
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)
    ids = [client_id]
    excluded = cfg.get("renewal_statuses_excluded", [])

    action_queue = _build_briefing_action_queue(conn, client_ids=ids)
    rfi_alerts = _build_rfi_alerts(conn, client_ids=ids)
    activities = [dict(r) for r in get_activities(conn, client_ids=ids, days=days)]
    time_summary = get_time_summary(conn, client_ids=ids, days=days)
    policies_touched = len({a.get("policy_uid") for a in activities if a.get("policy_uid")})
    metrics = get_renewal_metrics(conn, client_ids=ids)
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded, client_ids=ids)]

    from policydb.exporter import build_account_summary
    summary = build_account_summary(conn, client_id, days=days)

    client_notes = client.get("notes") or ""
    scratch_row = conn.execute(
        "SELECT content FROM client_scratchpad WHERE client_id = ?", (client_id,)
    ).fetchone()
    scratchpad_content = scratch_row["content"] if scratch_row else ""

    return templates.TemplateResponse("briefing_client_print.html", {
        "request": request,
        "days": days,
        "today_iso": date.today().isoformat(),
        "client": client,
        "client_id": client_id,
        "action_queue": action_queue[:30],
        "rfi_alerts": rfi_alerts,
        "activities": activities[:15],
        "time_summary": time_summary,
        "policies_touched": policies_touched,
        "metrics": metrics,
        "pipeline": pipeline,
        "s": summary,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "scratchpad_content": scratchpad_content,
        "client_notes": client_notes,
        "account_exec": cfg.get("default_account_exec", ""),
    })


# ── Group Briefing ───────────────────────────────────────────────────────────

@router.get("/briefing/group/{client_id}", response_class=HTMLResponse)
def group_briefing(request: Request, client_id: int, days: int = 7, conn=Depends(get_db)):
    """Full-page linked group briefing, accessed via any member's client_id."""
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    linked_group = get_linked_group_for_client(conn, client_id)
    if not linked_group:
        return HTMLResponse("Client is not in a linked group", status_code=404)

    group = linked_group["group"]
    members = linked_group["members"]
    member_ids = [m["client_id"] for m in members]
    group_id = group["id"]

    # Group overview (coverage matrix, combined metrics)
    overview = get_linked_group_overview(conn, group_id)
    excluded = cfg.get("renewal_statuses_excluded", [])

    # Action queue
    action_queue = _build_briefing_action_queue(conn, client_ids=member_ids)

    # RFI alerts
    rfi_alerts = _build_rfi_alerts(conn, client_ids=member_ids)

    # Work summary
    activities = [dict(r) for r in get_activities(conn, client_ids=member_ids, days=days)]
    time_summary = get_time_summary(conn, client_ids=member_ids, days=days)
    policies_touched = len({a.get("policy_uid") for a in activities if a.get("policy_uid")})

    # Pipeline
    metrics = get_renewal_metrics(conn, client_ids=member_ids)
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded, client_ids=member_ids)]
    stale = [dict(r) for r in get_stale_renewals(conn, excluded_statuses=excluded, client_ids=member_ids)]

    # Combined account summary (with linked)
    from policydb.exporter import build_account_summary
    summary = build_account_summary(conn, client_id, days=days, include_linked=True)

    return templates.TemplateResponse("briefing_group.html", {
        "request": request,
        "active": "briefing",
        "days": days,
        "today_iso": date.today().isoformat(),
        "client": client,
        "client_id": client_id,
        "group": group,
        "members": members,
        "overview": overview,
        "action_queue": action_queue,
        "rfi_alerts": rfi_alerts,
        "activities": activities[:20],
        "time_summary": time_summary,
        "policies_touched": policies_touched,
        "metrics": metrics,
        "stale_count": len(stale),
        "pipeline": pipeline,
        "s": summary,
        "renewal_statuses": cfg.get("renewal_statuses"),
    })


@router.get("/briefing/group/{client_id}/text")
def group_briefing_text(client_id: int, days: int = 90, conn=Depends(get_db)):
    """Plain text export of group briefing for clipboard."""
    from policydb.exporter import build_account_summary, render_account_summary_text
    summary = build_account_summary(conn, client_id, days=days, include_linked=True)
    text = render_account_summary_text(summary)
    return PlainTextResponse(text)


@router.get("/briefing/group/{client_id}/print", response_class=HTMLResponse)
def group_briefing_print(request: Request, client_id: int, days: int = 7, conn=Depends(get_db)):
    """Print-optimized group briefing for PDF export."""
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    linked_group = get_linked_group_for_client(conn, client_id)
    if not linked_group:
        return HTMLResponse("Client is not in a linked group", status_code=404)

    group = linked_group["group"]
    members = linked_group["members"]
    member_ids = [m["client_id"] for m in members]

    overview = get_linked_group_overview(conn, group["id"])
    excluded = cfg.get("renewal_statuses_excluded", [])

    action_queue = _build_briefing_action_queue(conn, client_ids=member_ids)
    rfi_alerts = _build_rfi_alerts(conn, client_ids=member_ids)
    activities = [dict(r) for r in get_activities(conn, client_ids=member_ids, days=days)]
    time_summary = get_time_summary(conn, client_ids=member_ids, days=days)
    policies_touched = len({a.get("policy_uid") for a in activities if a.get("policy_uid")})
    metrics = get_renewal_metrics(conn, client_ids=member_ids)
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded, client_ids=member_ids)]

    from policydb.exporter import build_account_summary
    summary = build_account_summary(conn, client_id, days=days, include_linked=True)

    return templates.TemplateResponse("briefing_group_print.html", {
        "request": request,
        "days": days,
        "today_iso": date.today().isoformat(),
        "client": client,
        "client_id": client_id,
        "group": group,
        "members": members,
        "overview": overview,
        "action_queue": action_queue[:30],
        "rfi_alerts": rfi_alerts,
        "activities": activities[:15],
        "time_summary": time_summary,
        "policies_touched": policies_touched,
        "metrics": metrics,
        "pipeline": pipeline,
        "s": summary,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "account_exec": cfg.get("default_account_exec", ""),
    })
