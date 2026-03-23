"""Weekly Review route — cycles through policies, opportunities, and clients."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from policydb import config as cfg
from policydb.utils import round_duration
from policydb.timeline_engine import suggest_profile
from policydb.queries import (
    REVIEW_CYCLE_DAYS,
    REVIEW_CYCLE_LABELS,
    get_review_queue,
    get_review_stats,
    mark_reviewed,
    set_review_cycle,
)
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
    """Attach client_id, milestone progress, and timeline health to review queue rows."""
    from policydb.web.routes.policies import _attach_milestone_progress
    for r in rows:
        if "client_id" not in r or not r.get("client_id"):
            client_row = conn.execute(
                "SELECT id FROM clients WHERE name=?", (r["client_name"],)
            ).fetchone()
            r["client_id"] = client_row["id"] if client_row else 0
    rows = _attach_milestone_progress(conn, rows)
    _attach_timeline_health(conn, rows)
    return rows


# ── Page ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def review_page(request: Request, conn=Depends(get_db)):
    queue = get_review_queue(conn)
    stats = get_review_stats(conn)

    policies = _enrich_policy_rows(conn, queue["policies"])
    opportunities = _enrich_policy_rows(conn, queue["opportunities"])
    clients = queue["clients"]

    suggestions = suggest_profile(conn)
    unassigned_count = len(suggestions)

    return templates.TemplateResponse("review/index.html", {
        "request": request,
        "active": "review",
        "policies": policies,
        "opportunities": opportunities,
        "clients": clients,
        "stats": stats,
        "suggestions": suggestions,
        "unassigned_count": unassigned_count,
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "cycle_labels": REVIEW_CYCLE_LABELS,
        "milestone_profiles": cfg.get("milestone_profiles", []),
        "today": date.today().isoformat(),
    })


@router.get("/stats", response_class=HTMLResponse)
def review_stats(request: Request, conn=Depends(get_db)):
    stats = get_review_stats(conn)
    return templates.TemplateResponse("review/_stats_banner.html", {
        "request": request,
        "stats": stats,
    })


# ── Policy / Opportunity review actions ───────────────────────────────────────

@router.post("/policies/{uid}/reviewed", response_class=HTMLResponse)
def policy_mark_reviewed(
    request: Request,
    uid: str,
    review_cycle: str = Form(""),
    conn=Depends(get_db),
):
    mark_reviewed(conn, "policy", uid, review_cycle or None)

    # Cascade review to child policies if this is a program
    prog_row = conn.execute(
        "SELECT id, is_program FROM policies WHERE policy_uid = ?", (uid,)
    ).fetchone()
    if prog_row and prog_row["is_program"]:
        conn.execute(
            "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
            (prog_row["id"],),
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
