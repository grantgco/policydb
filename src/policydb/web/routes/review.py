"""Weekly Review route — cycles through policies, opportunities, and clients."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse

from policydb import config as cfg
from policydb.utils import round_duration, parse_currency_with_magnitude
from policydb.timeline_engine import suggest_profile, get_policy_timeline
from policydb.queries import (
    REVIEW_CYCLE_DAYS,
    REVIEW_CYCLE_LABELS,
    attach_issue_counts,
    get_activities,
    get_client_contacts,
    get_or_create_review_session,
    get_policy_contacts,
    get_review_queue,
    get_review_stats,
    get_review_section_items,
    get_this_week_summary,
    get_last_completed_review_date,
    archive_stale_session,
    update_section_status,
    complete_review_session,
    mark_reviewed,
    set_review_cycle,
    supersede_followups,
)
from policydb.review_checks import WALKTHROUGH_SECTIONS
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/review")


# ── Helpers ───────────────────────────────────────────────────────────────────

_REVIEW_ROW_SQL = """
    SELECT p.*, c.name AS client_name, c.id AS client_id,
           CASE WHEN p.is_opportunity = 1 THEN NULL
                ELSE CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER)
           END AS days_to_renewal,
           CASE WHEN p.is_opportunity = 1 THEN 'OPPORTUNITY'
                WHEN julianday(p.expiration_date) - julianday('now') <= 0 THEN 'EXPIRED'
                WHEN julianday(p.expiration_date) - julianday('now') <= 90 THEN 'URGENT'
                WHEN julianday(p.expiration_date) - julianday('now') <= 120 THEN 'WARNING'
                WHEN julianday(p.expiration_date) - julianday('now') <= 180 THEN 'UPCOMING'
                ELSE 'OK'
           END AS urgency,
           CASE WHEN p.last_reviewed_at IS NULL THEN 9999
                ELSE CAST(julianday('now') - julianday(p.last_reviewed_at) AS INTEGER)
           END AS days_since_review
    FROM policies p JOIN clients c ON c.id = p.client_id
    WHERE p.policy_uid = ?
"""


def _fetch_review_row(conn, uid: str) -> dict | None:
    """Fetch a single policy with review-relevant computed columns."""
    row = conn.execute(_REVIEW_ROW_SQL, (uid,)).fetchone()
    if not row:
        return None
    r = dict(row)
    from policydb.web.routes.policies import _attach_milestone_progress
    rows = _attach_milestone_progress(conn, [r])
    r = rows[0]
    _attach_timeline_health(conn, [r])
    return r


def _attach_timeline_health(conn, rows: list[dict]) -> None:
    """Enrich rows in-place with worst timeline health status."""
    for row in rows:
        uid = row.get("policy_uid")
        if not uid:
            continue
        health = conn.execute("""
            SELECT health FROM policy_timeline
            WHERE policy_uid = ? AND completed_date IS NULL
            ORDER BY CASE health WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                     WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END
            LIMIT 1
        """, (uid,)).fetchone()
        row["timeline_health"] = health["health"] if health else ""


def _policy_row_context(request, row: dict, reviewed: bool = False,
                        needs_followup: bool = False,
                        suggestions: dict | None = None) -> dict:
    """Build the template context for _policy_row.html."""
    return {
        "request": request,
        "p": row,
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "milestone_profiles": cfg.get("milestone_profiles", []),
        "reviewed": reviewed,
        "needs_followup": needs_followup,
        "suggestions": suggestions or {},
    }


def _enrich_policy_rows(conn, rows: list[dict]) -> list[dict]:
    """Attach client_id, milestone progress, timeline health, and issue counts to review queue rows."""
    from policydb.web.routes.policies import _attach_milestone_progress
    for r in rows:
        if "client_id" not in r or not r.get("client_id"):
            client_row = conn.execute(
                "SELECT id FROM clients WHERE name=?", (r["client_name"],)
            ).fetchone()
            r["client_id"] = client_row["id"] if client_row else 0
    rows = _attach_milestone_progress(conn, rows)
    _attach_timeline_health(conn, rows)
    attach_issue_counts(conn, rows, id_field="id", scope="policy")
    return rows


def _enrich_client_rows(conn, rows: list[dict]) -> list[dict]:
    """Attach issue counts to client review rows."""
    attach_issue_counts(conn, rows, id_field="id", scope="client")
    return rows


def _get_policy_review_context(conn, uid: str) -> dict | None:
    """Assemble all data needed for the policy review slideover."""
    row = _fetch_review_row(conn, uid)
    if not row:
        return None
    policy_id = row.get("id")
    client_id = row.get("client_id")

    # Contacts
    policy_contacts = get_policy_contacts(conn, policy_id) if policy_id else []
    primary_client_contact = None
    if client_id:
        cc = get_client_contacts(conn, client_id, contact_type="client")
        primary_client_contact = cc[0] if cc else None

    # Review gate
    from policydb.anomaly_engine import get_review_gate_status
    gate = get_review_gate_status(conn, "policy", policy_id)

    # Follow-up info
    active_fu = None
    if policy_id:
        fu_row = conn.execute("""
            SELECT follow_up_date, subject, activity_type
            FROM activity_log
            WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL
            ORDER BY follow_up_date ASC LIMIT 1
        """, (policy_id,)).fetchone()
        if fu_row:
            active_fu = dict(fu_row)

    return {
        "p": row,
        "policy_contacts": policy_contacts,
        "primary_client_contact": primary_client_contact,
        "gate": gate,
        "active_followup": active_fu,
    }


def _get_client_review_context(conn, client_id: int) -> dict | None:
    """Assemble all data needed for the client review slideover."""
    row = conn.execute(
        """SELECT c.*, cs.total_policies, cs.total_premium, cs.next_renewal_days,
                  cs.opportunity_count,
                  CASE WHEN c.last_reviewed_at IS NULL THEN 9999
                       ELSE CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM clients c
           LEFT JOIN v_client_summary cs ON cs.id = c.id
           WHERE c.id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return None
    client = dict(row)

    # Contacts
    client_contacts = get_client_contacts(conn, client_id, contact_type="client")
    internal_contacts = get_client_contacts(conn, client_id, contact_type="internal")

    # Active policies with urgency
    policies = [dict(r) for r in conn.execute("""
        SELECT policy_uid, policy_type, carrier, expiration_date, renewal_status,
               is_opportunity, premium,
               CAST(julianday(expiration_date) - julianday('now') AS INTEGER) AS days_to_renewal,
               CASE WHEN is_opportunity = 1 THEN 'OPPORTUNITY'
                    WHEN julianday(expiration_date) - julianday('now') <= 0 THEN 'EXPIRED'
                    WHEN julianday(expiration_date) - julianday('now') <= 90 THEN 'URGENT'
                    WHEN julianday(expiration_date) - julianday('now') <= 120 THEN 'WARNING'
                    WHEN julianday(expiration_date) - julianday('now') <= 180 THEN 'UPCOMING'
                    ELSE 'OK'
               END AS urgency
        FROM policies
        WHERE client_id = ? AND archived = 0 AND program_id IS NULL
        ORDER BY expiration_date ASC
    """, (client_id,)).fetchall()]

    # Overdue follow-ups
    overdue_fus = [dict(r) for r in conn.execute("""
        SELECT a.id, a.subject, a.follow_up_date, p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.client_id = ? AND a.follow_up_done = 0
          AND a.follow_up_date IS NOT NULL AND a.follow_up_date <= date('now')
        ORDER BY a.follow_up_date ASC LIMIT 10
    """, (client_id,)).fetchall()]

    # Scratchpad
    scratchpad = conn.execute(
        "SELECT content FROM client_scratchpad WHERE client_id = ?", (client_id,)
    ).fetchone()

    return {
        "c": client,
        "client_contacts": client_contacts,
        "internal_contacts": internal_contacts,
        "policies": policies,
        "overdue_followups": overdue_fus,
        "scratchpad": dict(scratchpad)["content"] if scratchpad else "",
    }


# ── Session & Walkthrough Routes ─────────────────────────────────────────────


@router.get("/session", response_class=HTMLResponse)
def get_session(request: Request, conn=Depends(get_db)):
    """Get or create the active review session."""
    session = get_or_create_review_session(conn)
    return JSONResponse({"session_id": session["id"], "stale": session.get("stale", False)})


@router.post("/session/complete", response_class=HTMLResponse)
def complete_session(request: Request, conn=Depends(get_db)):
    """Mark the entire review session as complete."""
    session = get_or_create_review_session(conn)
    complete_review_session(conn, session["id"])
    return RedirectResponse("/review", status_code=303)


@router.post("/session/archive", response_class=HTMLResponse)
def archive_session(request: Request, conn=Depends(get_db)):
    """Archive a stale session and start fresh."""
    session = get_or_create_review_session(conn)
    archive_stale_session(conn, session["id"])
    get_or_create_review_session(conn)
    return RedirectResponse("/review", status_code=303)


@router.post("/vacation", response_class=HTMLResponse)
def set_vacation(request: Request, vacation_return_date: str = Form(""), conn=Depends(get_db)):
    """Set or clear vacation return date on active session."""
    session = get_or_create_review_session(conn)
    date_val = vacation_return_date if vacation_return_date else None
    conn.execute(
        "UPDATE review_sessions SET vacation_return_date = ? WHERE id = ?",
        (date_val, session["id"]),
    )
    conn.commit()
    return RedirectResponse("/review", status_code=303)


@router.get("/vacation-checklist", response_class=HTMLResponse)
def vacation_checklist(request: Request, conn=Depends(get_db)):
    """Vacation pre-departure checklist partial."""
    from policydb.queries import get_vacation_checklist
    session = get_or_create_review_session(conn)
    if not session.get("vacation_return_date"):
        return HTMLResponse("<div class='p-4 text-sm text-gray-500'>No vacation date set.</div>")
    checklist = get_vacation_checklist(conn, session["vacation_return_date"])
    return templates.TemplateResponse("review/_vacation_checklist.html", {"request": request, "checklist": checklist})


@router.get("/this-week", response_class=HTMLResponse)
def this_week(request: Request, conn=Depends(get_db)):
    """This Week activity summary partial."""
    since = get_last_completed_review_date(conn)
    summary = get_this_week_summary(conn, since)
    return templates.TemplateResponse("review/_this_week.html", {"request": request, "summary": summary})


@router.get("/section/{section_key}", response_class=HTMLResponse)
def load_section(request: Request, section_key: str, conn=Depends(get_db)):
    """Lazy-load a walkthrough section's content."""
    items = get_review_section_items(conn, section_key)
    session = get_or_create_review_session(conn)
    return templates.TemplateResponse("review/_walkthrough_section.html", {
        "request": request,
        "section_key": section_key,
        "items": items,
        "session": session,
    })


@router.post("/section/{section_key}/complete", response_class=HTMLResponse)
def section_complete(request: Request, section_key: str, conn=Depends(get_db)):
    """Mark a section as complete."""
    import json as _json
    session = get_or_create_review_session(conn)
    items = get_review_section_items(conn, section_key)
    sections = update_section_status(conn, session["id"], section_key, "complete", len(items))

    all_done = all(s.get("status") in ("complete", "skipped") for s in sections.values())
    if all_done:
        complete_review_session(conn, session["id"])

    return templates.TemplateResponse("review/_stats_banner.html", {
        "request": request,
        "sections": sections,
        "session": session,
        "all_done": all_done,
        "section_defs": WALKTHROUGH_SECTIONS,
    })


@router.post("/section/{section_key}/skip", response_class=HTMLResponse)
def section_skip(request: Request, section_key: str, conn=Depends(get_db)):
    """Mark a section as skipped."""
    session = get_or_create_review_session(conn)
    sections = update_section_status(conn, session["id"], section_key, "skipped")

    all_done = all(s.get("status") in ("complete", "skipped") for s in sections.values())
    if all_done:
        complete_review_session(conn, session["id"])

    return templates.TemplateResponse("review/_stats_banner.html", {
        "request": request,
        "sections": sections,
        "session": session,
        "all_done": all_done,
        "section_defs": WALKTHROUGH_SECTIONS,
    })


# ── Page ──────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def review_page(request: Request, conn=Depends(get_db)):
    """Guided weekly review walkthrough."""
    import json as _json
    session = get_or_create_review_session(conn)
    sections = _json.loads(session.get("sections_json", "{}"))

    section_counts = {}
    for s in WALKTHROUGH_SECTIONS:
        if s.get("conditional") and not session.get("vacation_return_date"):
            continue
        items = get_review_section_items(conn, s["key"])
        section_counts[s["key"]] = len(items)

    first_incomplete = None
    for s in WALKTHROUGH_SECTIONS:
        if s.get("conditional") and not session.get("vacation_return_date"):
            continue
        sec_state = sections.get(s["key"], {})
        if sec_state.get("status") not in ("complete", "skipped"):
            first_incomplete = s["key"]
            break

    return templates.TemplateResponse("review/index.html", {
        "request": request,
        "active": "review",
        "session": session,
        "sections": sections,
        "section_defs": WALKTHROUGH_SECTIONS,
        "section_counts": section_counts,
        "first_incomplete": first_incomplete,
        "all_done": session.get("completed_at") is not None,
    })


@router.get("/stats", response_class=HTMLResponse)
def review_stats(request: Request, conn=Depends(get_db)):
    import json as _json
    session = get_or_create_review_session(conn)
    sections = _json.loads(session.get("sections_json", "{}"))
    return templates.TemplateResponse("review/_stats_banner.html", {
        "request": request,
        "sections": sections,
        "section_defs": WALKTHROUGH_SECTIONS,
        "session": session,
    })


# ── Policy / Opportunity review actions ───────────────────────────────────────

@router.get("/policies/{uid}/gate", response_class=HTMLResponse)
def review_gate(request: Request, uid: str, conn=Depends(get_db)):
    """Show review gate conditions before marking reviewed."""
    from policydb.anomaly_engine import get_review_gate_status
    # gate status uses integer policy_id for DB queries
    row = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ?", (uid,)
    ).fetchone()
    policy_id = row["id"] if row else 0
    gate = get_review_gate_status(conn, "policy", policy_id)
    return templates.TemplateResponse("review/_review_gate.html", {
        "request": request,
        "gate": gate,
        "record_type": "policy",
        "record_id": uid,
    })


@router.post("/policies/{uid}/reviewed", response_class=HTMLResponse)
def policy_mark_reviewed(
    request: Request,
    uid: str,
    review_cycle: str = Form(""),
    override_reason: str = Form(""),
    conn=Depends(get_db),
):
    # Save override reason if provided
    if override_reason:
        conn.execute(
            "UPDATE policies SET review_override_reason = ? WHERE policy_uid = ?",
            (override_reason, uid),
        )

    mark_reviewed(conn, "policy", uid, review_cycle or None)

    # Cascade review to child policies if this is a program
    program = conn.execute(
        "SELECT id FROM programs WHERE program_uid = ?", (uid,)
    ).fetchone()
    if program:
        conn.execute(
            "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
            (program["id"],),
        )
        conn.commit()

    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")

    # Check if policy has an active follow-up
    policy_id = r.get("id")
    needs_followup = False
    if policy_id:
        active_fu = conn.execute("""
            SELECT 1 FROM activity_log
            WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL
            LIMIT 1
        """, (policy_id,)).fetchone()
        needs_followup = active_fu is None

    suggestions = suggest_profile(conn)
    resp = templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, reviewed=True, needs_followup=needs_followup,
                            suggestions=suggestions),
    )
    resp.headers["HX-Trigger"] = "refreshReviewStats"
    return resp


@router.post("/policies/{uid}/cycle", response_class=HTMLResponse)
def policy_set_cycle(
    request: Request,
    uid: str,
    review_cycle: str = Form(...),
    conn=Depends(get_db),
):
    set_review_cycle(conn, "policy", uid, review_cycle)
    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


@router.get("/policies/{uid}/row", response_class=HTMLResponse)
def policy_row(request: Request, uid: str, conn=Depends(get_db)):
    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


@router.get("/policies/{uid}/row/edit", response_class=HTMLResponse)
def policy_row_edit(request: Request, uid: str, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT p.*, c.name AS client_name, c.id AS client_id, c.cn_number FROM policies p JOIN clients c ON c.id = p.client_id WHERE p.policy_uid = ?",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    return templates.TemplateResponse("review/_policy_row_edit.html", {
        "request": request,
        "p": dict(row),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "opportunity_statuses": cfg.get("opportunity_statuses", []),
        "policy_types": cfg.get("policy_types", []),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "activity_types": cfg.get("activity_types", []),
    })


@router.post("/policies/{uid}/row/edit", response_class=HTMLResponse)
def policy_row_edit_save(
    request: Request,
    uid: str,
    policy_type: str = Form(""),
    carrier: str = Form(""),
    access_point: str = Form(""),
    policy_number: str = Form(""),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    premium: str = Form(""),
    limit_amount: str = Form(""),
    commission_rate: str = Form(""),
    follow_up_date: str = Form(""),
    renewal_status: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    opportunity_status: str = Form(""),
    target_effective_date: str = Form(""),
    conn=Depends(get_db),
):
    def _f(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    old_row = dict(conn.execute("SELECT * FROM policies WHERE policy_uid=?", (uid,)).fetchone())

    conn.execute(
        """UPDATE policies SET
               policy_type            = COALESCE(NULLIF(?, ''), policy_type),
               carrier                = NULLIF(?, ''),
               access_point           = NULLIF(?, ''),
               policy_number          = NULLIF(?, ''),
               effective_date         = NULLIF(?, ''),
               expiration_date        = COALESCE(NULLIF(?, ''), expiration_date),
               premium                = COALESCE(?, premium),
               limit_amount           = ?,
               commission_rate        = ?,
               follow_up_date         = NULLIF(?, ''),
               renewal_status         = COALESCE(NULLIF(?, ''), renewal_status),
               description            = NULLIF(?, ''),
               notes                  = NULLIF(?, ''),
               opportunity_status     = NULLIF(?, ''),
               target_effective_date  = NULLIF(?, '')
           WHERE policy_uid = ?""",
        (
            policy_type or None, carrier or None, access_point or None,
            policy_number or None,
            effective_date or None, expiration_date or None,
            _f(premium), _f(limit_amount), _f(commission_rate),
            follow_up_date or None, renewal_status or None,
            description or None, notes or None,
            opportunity_status or None, target_effective_date or None,
            uid,
        ),
    )
    conn.commit()

    # Regenerate timeline if dates changed and profile is set
    if effective_date or expiration_date:
        _regen = conn.execute(
            "SELECT milestone_profile FROM policies WHERE policy_uid = ?", (uid,)
        ).fetchone()
        if _regen and _regen["milestone_profile"]:
            from policydb.timeline_engine import generate_policy_timelines
            generate_policy_timelines(conn, policy_uid=uid)

    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


@router.get("/policies/{uid}/row/log", response_class=HTMLResponse)
def policy_row_log(request: Request, uid: str, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT p.*, c.name AS client_name, c.id AS client_id, c.cn_number FROM policies p JOIN clients c ON c.id = p.client_id WHERE p.policy_uid = ?",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    p = dict(row)
    default_subject = f"{p.get('policy_type', '')} — {p.get('renewal_status', '')}"
    return templates.TemplateResponse("review/_policy_row_log.html", {
        "request": request,
        "p": p,
        "activity_types": cfg.get("activity_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "quick_templates": cfg.get("quick_log_templates", []),
        "default_subject": default_subject,
    })


@router.post("/policies/{uid}/row/log", response_class=HTMLResponse)
def policy_row_log_save(
    request: Request,
    uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         subject, details or None, follow_up_date or None, account_exec, round_duration(duration_hours)),
    )
    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    conn.commit()

    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


# ── Policy Review Slideover ──────────────────────────────────────────────────

@router.get("/policies/{uid}/slideover", response_class=HTMLResponse)
def policy_slideover(request: Request, uid: str, conn=Depends(get_db)):
    """Return the full policy review slideover shell."""
    ctx = _get_policy_review_context(conn, uid)
    if not ctx:
        return HTMLResponse("")
    p = ctx["p"]

    # Build ordered list of all review queue UIDs for next/prev navigation
    queue = get_review_queue(conn)
    queue_uids = [r.get("policy_uid") for r in queue["policies"]] + \
                 [r.get("policy_uid") for r in queue["opportunities"]]

    suggestions = suggest_profile(conn)
    return templates.TemplateResponse("review/_policy_review_slideover.html", {
        "request": request,
        **ctx,
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "milestone_profiles": cfg.get("milestone_profiles", []),
        "suggestions": suggestions or {},
        "today": date.today().isoformat(),
        "queue_uids": queue_uids,
    })


@router.get("/policies/{uid}/slideover/issues", response_class=HTMLResponse)
def policy_slideover_issues(request: Request, uid: str, conn=Depends(get_db)):
    """Return the issues section partial for the policy review slideover."""
    row = conn.execute("SELECT id, client_id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        return HTMLResponse("")
    issues = [dict(r) for r in conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_status,
               a.issue_sla_days, a.activity_date, a.is_renewal_issue,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open,
               a.due_date
        FROM activity_log a
        WHERE a.item_kind = 'issue'
          AND a.policy_id = ?
          AND a.issue_status NOT IN ('Resolved', 'Closed')
        ORDER BY CASE a.issue_severity
            WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
            WHEN 'Normal' THEN 3 ELSE 4 END,
            a.activity_date ASC
    """, (row["id"],)).fetchall()]
    return templates.TemplateResponse("review/_policy_review_issues.html", {
        "request": request,
        "issues": issues,
        "policy_uid": uid,
        "client_id": row["client_id"],
        "policy_id": row["id"],
        "today": date.today().isoformat(),
    })


@router.get("/policies/{uid}/slideover/activity", response_class=HTMLResponse)
def policy_slideover_activity(request: Request, uid: str, conn=Depends(get_db)):
    """Return the activity + quick-log section for the policy review slideover."""
    row = conn.execute("SELECT id, client_id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        return HTMLResponse("")
    activities = [dict(r) for r in conn.execute("""
        SELECT a.id, a.activity_date, a.activity_type, a.subject, a.details,
               a.duration_hours, a.follow_up_date, a.follow_up_done,
               a.disposition, co.name AS contact_name
        FROM activity_log a
        LEFT JOIN contacts co ON a.contact_id = co.id
        WHERE a.policy_id = ? AND a.item_kind != 'issue'
        ORDER BY a.activity_date DESC, a.id DESC
        LIMIT 5
    """, (row["id"],)).fetchall()]

    # Total hours last 30d
    hours_row = conn.execute("""
        SELECT COALESCE(SUM(duration_hours), 0) AS total
        FROM activity_log
        WHERE policy_id = ? AND duration_hours > 0
          AND activity_date >= date('now', '-30 days')
    """, (row["id"],)).fetchone()

    return templates.TemplateResponse("review/_policy_review_activity.html", {
        "request": request,
        "activities": activities,
        "total_hours_30d": hours_row["total"] if hours_row else 0,
        "policy_uid": uid,
        "policy_id": row["id"],
        "client_id": row["client_id"],
        "activity_types": cfg.get("activity_types", []),
        "quick_templates": cfg.get("quick_log_templates", []),
    })


@router.get("/policies/{uid}/slideover/notes", response_class=HTMLResponse)
def policy_slideover_notes(request: Request, uid: str, conn=Depends(get_db)):
    """Return the notes section for the policy review slideover."""
    row = conn.execute(
        "SELECT description, notes FROM policies WHERE policy_uid = ?", (uid,)
    ).fetchone()
    if not row:
        return HTMLResponse("")
    return templates.TemplateResponse("review/_policy_review_notes.html", {
        "request": request,
        "policy_uid": uid,
        "description": row["description"] or "",
        "notes": row["notes"] or "",
    })


@router.post("/policies/{uid}/slideover/field", response_class=HTMLResponse)
def policy_slideover_field_save(
    request: Request,
    uid: str,
    field: str = Form(...),
    value: str = Form(""),
    conn=Depends(get_db),
):
    """Per-field save from the review slideover."""
    allowed = {
        "renewal_status", "follow_up_date", "description", "notes",
        "premium", "limit_amount", "opportunity_status",
    }
    if field not in allowed:
        return HTMLResponse("", status_code=400)

    if field in ("premium", "limit_amount"):
        parsed = parse_currency_with_magnitude(value)
        db_val = parsed if parsed is not None else None
    elif field == "follow_up_date":
        db_val = value or None
        # Sync follow_up_date to policy table
        conn.execute(
            "UPDATE policies SET follow_up_date = ? WHERE policy_uid = ?",
            (db_val, uid),
        )
        conn.commit()
        return HTMLResponse(f'<span class="text-xs text-green-600">{value or "—"}</span>')
    else:
        db_val = value or None

    conn.execute(
        f"UPDATE policies SET {field} = ? WHERE policy_uid = ?",
        (db_val, uid),
    )
    conn.commit()
    return HTMLResponse(f'<span class="text-xs text-green-600">Saved</span>')


@router.post("/policies/{uid}/slideover/log", response_class=HTMLResponse)
def policy_slideover_log_save(
    request: Request,
    uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Log activity from within the review slideover."""
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         subject, details or None, follow_up_date or None, account_exec, round_duration(duration_hours)),
    )
    if follow_up_date and policy_id:
        supersede_followups(conn, policy_id, follow_up_date)
    conn.commit()

    # Return refreshed activity section
    return policy_slideover_activity(request, uid, conn)


@router.post("/policies/{uid}/slideover/reviewed", response_class=HTMLResponse)
def policy_slideover_mark_reviewed(
    request: Request,
    uid: str,
    review_cycle: str = Form(""),
    override_reason: str = Form(""),
    conn=Depends(get_db),
):
    """Mark reviewed from within the slideover. Returns updated footer + OOB row swap."""
    if override_reason:
        conn.execute(
            "UPDATE policies SET review_override_reason = ? WHERE policy_uid = ?",
            (override_reason, uid),
        )
    mark_reviewed(conn, "policy", uid, review_cycle or None)

    # Cascade to children if program
    program = conn.execute(
        "SELECT id FROM programs WHERE program_uid = ?", (uid,)
    ).fetchone()
    if program:
        conn.execute(
            "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
            (program["id"],),
        )
        conn.commit()

    # Build OOB row update
    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")

    policy_id = r.get("id")
    needs_followup = False
    if policy_id:
        active_fu = conn.execute("""
            SELECT 1 FROM activity_log
            WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL
            LIMIT 1
        """, (policy_id,)).fetchone()
        needs_followup = active_fu is None

    suggestions = suggest_profile(conn)

    # Render the updated table row (OOB swap)
    row_html = templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, reviewed=True, needs_followup=needs_followup,
                            suggestions=suggestions),
    ).body.decode()

    # Render the slideover footer in reviewed state
    footer_html = f"""
    <div id="review-slideover-footer" hx-swap-oob="innerHTML:#review-slideover-footer">
      <div class="flex items-center justify-center gap-2 text-green-600">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
        </svg>
        <span class="font-semibold text-sm">Reviewed</span>
      </div>
    </div>
    """

    resp = HTMLResponse(row_html + footer_html)
    resp.headers["HX-Trigger"] = "refreshReviewStats"
    return resp


# ── Client Review Slideover ─────────────────────────────────────────────────

@router.get("/clients/{client_id}/slideover", response_class=HTMLResponse)
def client_slideover(request: Request, client_id: int, conn=Depends(get_db)):
    """Return the full client review slideover."""
    ctx = _get_client_review_context(conn, client_id)
    if not ctx:
        return HTMLResponse("")

    # Queue UIDs for navigation
    queue = get_review_queue(conn)
    queue_client_ids = [r.get("id") for r in queue["clients"]]

    return templates.TemplateResponse("review/_client_review_slideover.html", {
        "request": request,
        **ctx,
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "queue_client_ids": queue_client_ids,
    })


@router.get("/clients/{client_id}/slideover/issues", response_class=HTMLResponse)
def client_slideover_issues(request: Request, client_id: int, conn=Depends(get_db)):
    """Return the issues section for the client review slideover."""
    issues = [dict(r) for r in conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_status,
               a.issue_sla_days, a.activity_date, a.is_renewal_issue,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open,
               a.due_date, p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.item_kind = 'issue'
          AND a.client_id = ?
          AND a.issue_status NOT IN ('Resolved', 'Closed')
        ORDER BY CASE a.issue_severity
            WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
            WHEN 'Normal' THEN 3 ELSE 4 END,
            a.activity_date ASC
    """, (client_id,)).fetchall()]
    return templates.TemplateResponse("review/_client_review_issues.html", {
        "request": request,
        "issues": issues,
        "client_id": client_id,
    })


@router.get("/clients/{client_id}/slideover/activity", response_class=HTMLResponse)
def client_slideover_activity(request: Request, client_id: int, conn=Depends(get_db)):
    """Return the activity section for the client review slideover."""
    activities = [dict(r) for r in conn.execute("""
        SELECT a.id, a.activity_date, a.activity_type, a.subject,
               a.duration_hours, p.policy_uid, p.policy_type,
               co.name AS contact_name
        FROM activity_log a
        LEFT JOIN policies p ON a.policy_id = p.id
        LEFT JOIN contacts co ON a.contact_id = co.id
        WHERE a.client_id = ? AND a.item_kind != 'issue'
        ORDER BY a.activity_date DESC, a.id DESC
        LIMIT 8
    """, (client_id,)).fetchall()]

    hours_row = conn.execute("""
        SELECT COALESCE(SUM(duration_hours), 0) AS total
        FROM activity_log
        WHERE client_id = ? AND duration_hours > 0
          AND activity_date >= date('now', '-30 days')
    """, (client_id,)).fetchone()

    return templates.TemplateResponse("review/_client_review_activity.html", {
        "request": request,
        "activities": activities,
        "total_hours_30d": hours_row["total"] if hours_row else 0,
        "client_id": client_id,
    })


@router.post("/clients/{client_id}/slideover/reviewed", response_class=HTMLResponse)
def client_slideover_mark_reviewed(
    request: Request,
    client_id: int,
    review_cycle: str = Form(""),
    conn=Depends(get_db),
):
    """Mark client reviewed from within the slideover."""
    mark_reviewed(conn, "client", client_id, review_cycle or None)
    row = conn.execute(
        """SELECT c.*, cs.total_policies, cs.total_premium, cs.next_renewal_days, cs.opportunity_count,
                  CASE WHEN c.last_reviewed_at IS NULL THEN 9999
                       ELSE CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM clients c
           LEFT JOIN v_client_summary cs ON cs.id = c.id
           WHERE c.id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")

    # OOB row update
    row_html = templates.TemplateResponse("review/_client_row.html", {
        "request": request,
        "c": dict(row),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "reviewed": True,
    }).body.decode()

    footer_html = """
    <div id="client-review-slideover-footer" hx-swap-oob="innerHTML:#client-review-slideover-footer">
      <div class="flex items-center justify-center gap-2 text-green-600">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
        </svg>
        <span class="font-semibold text-sm">Reviewed</span>
      </div>
    </div>
    """

    resp = HTMLResponse(row_html + footer_html)
    resp.headers["HX-Trigger"] = "refreshReviewStats"
    return resp


# ── Milestone profile change ─────────────────────────────────────────────────

@router.post("/policies/{uid}/profile", response_class=HTMLResponse)
def change_profile(
    request: Request,
    uid: str,
    milestone_profile: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "UPDATE policies SET milestone_profile = ? WHERE policy_uid = ?",
        (milestone_profile, uid),
    )
    conn.commit()

    # Regenerate timeline for this policy with new profile
    from policydb.timeline_engine import generate_policy_timelines
    generate_policy_timelines(conn, policy_uid=uid)

    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


# ── Profile suggestion accept ─────────────────────────────────────────────────

@router.post("/policies/{uid}/accept-profile", response_class=HTMLResponse)
def accept_profile(
    request: Request,
    uid: str,
    profile: str = Form(""),
    conn=Depends(get_db),
):
    """Accept a suggested milestone profile for a policy."""
    if profile:
        conn.execute(
            "UPDATE policies SET milestone_profile = ? WHERE policy_uid = ?",
            (profile, uid),
        )
        conn.commit()
        from policydb.timeline_engine import generate_policy_timelines
        generate_policy_timelines(conn, policy_uid=uid)

    r = _fetch_review_row(conn, uid)
    if not r:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse(
        "review/_policy_row.html",
        _policy_row_context(request, r, suggestions=suggestions),
    )


# ── Bulk accept all profile suggestions ──────────────────────────────────────

@router.post("/accept-all-profiles")
def accept_all_profiles(request: Request, conn=Depends(get_db)):
    """Accept all suggested milestone profiles at once."""
    suggestions = suggest_profile(conn)
    for pol_uid, profile_name in suggestions.items():
        conn.execute(
            "UPDATE policies SET milestone_profile = ? WHERE policy_uid = ?",
            (profile_name, pol_uid),
        )
    conn.commit()
    from policydb.timeline_engine import generate_policy_timelines
    generate_policy_timelines(conn)
    return RedirectResponse("/review", status_code=303)


# ── Client review actions ──────────────────────────────────────────────────────

@router.post("/clients/{client_id}/reviewed", response_class=HTMLResponse)
def client_mark_reviewed(
    request: Request,
    client_id: int,
    review_cycle: str = Form(""),
    conn=Depends(get_db),
):
    mark_reviewed(conn, "client", client_id, review_cycle or None)
    row = conn.execute(
        """SELECT c.*, cs.total_policies, cs.total_premium, cs.next_renewal_days, cs.opportunity_count,
                  CASE WHEN c.last_reviewed_at IS NULL THEN 9999
                       ELSE CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM clients c
           LEFT JOIN v_client_summary cs ON cs.id = c.id
           WHERE c.id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    resp = templates.TemplateResponse("review/_client_row.html", {
        "request": request,
        "c": dict(row),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "reviewed": True,
    })
    resp.headers["HX-Trigger"] = "refreshReviewStats"
    return resp


@router.post("/policies/{uid}/mark", response_class=HTMLResponse)
def policy_mark_badge(
    request: Request,
    uid: str,
    review_cycle: str = Form(""),
    mark: str = Form("0"),
    conn=Depends(get_db),
):
    """Mark policy reviewed (or just update cycle) and return the badge partial for edit pages."""
    if mark == "1":
        mark_reviewed(conn, "policy", uid, review_cycle or None)
    elif review_cycle:
        set_review_cycle(conn, "policy", uid, review_cycle)
    row = conn.execute(
        """SELECT last_reviewed_at, review_cycle,
                  CASE WHEN last_reviewed_at IS NULL THEN NULL
                       ELSE CAST(julianday('now') - julianday(last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM policies WHERE policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    return templates.TemplateResponse("review/_review_badge.html", {
        "request": request,
        "record_type": "policy",
        "record_id": uid,
        "last_reviewed_at": r["last_reviewed_at"],
        "review_cycle": r["review_cycle"],
        "days_since_review": r["days_since_review"],
        "cycle_labels": REVIEW_CYCLE_LABELS,
    })


@router.post("/clients/{client_id}/mark", response_class=HTMLResponse)
def client_mark_badge(
    request: Request,
    client_id: int,
    review_cycle: str = Form(""),
    mark: str = Form("0"),
    conn=Depends(get_db),
):
    """Mark client reviewed (or just update cycle) and return the badge partial for edit/detail pages."""
    if mark == "1":
        mark_reviewed(conn, "client", client_id, review_cycle or None)
    elif review_cycle:
        set_review_cycle(conn, "client", client_id, review_cycle)
    row = conn.execute(
        """SELECT last_reviewed_at, review_cycle,
                  CASE WHEN last_reviewed_at IS NULL THEN NULL
                       ELSE CAST(julianday('now') - julianday(last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM clients WHERE id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    return templates.TemplateResponse("review/_review_badge.html", {
        "request": request,
        "record_type": "client",
        "record_id": client_id,
        "last_reviewed_at": r["last_reviewed_at"],
        "review_cycle": r["review_cycle"],
        "days_since_review": r["days_since_review"],
        "cycle_labels": REVIEW_CYCLE_LABELS,
    })


@router.post("/clients/{client_id}/cycle", response_class=HTMLResponse)
def client_set_cycle(
    request: Request,
    client_id: int,
    review_cycle: str = Form(...),
    conn=Depends(get_db),
):
    set_review_cycle(conn, "client", client_id, review_cycle)
    row = conn.execute(
        """SELECT c.*, cs.total_policies, cs.total_premium, cs.next_renewal_days, cs.opportunity_count,
                  CASE WHEN c.last_reviewed_at IS NULL THEN 9999
                       ELSE CAST(julianday('now') - julianday(c.last_reviewed_at) AS INTEGER)
                  END AS days_since_review
           FROM clients c
           LEFT JOIN v_client_summary cs ON cs.id = c.id
           WHERE c.id = ?""",
        (client_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    return templates.TemplateResponse("review/_client_row.html", {
        "request": request,
        "c": dict(row),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "reviewed": False,
    })
