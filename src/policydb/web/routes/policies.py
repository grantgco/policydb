"""Policy routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.queries import get_all_policies, get_client_by_id, get_opportunity_by_uid, get_policy_by_uid, renew_policy
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/policies")

US_STATES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"),
    ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"),
    ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"),
    ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"),
    ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"),
    ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"), ("WV", "West Virginia"),
    ("WI", "Wisconsin"), ("WY", "Wyoming"), ("DC", "District of Columbia"),
]

# Fields that can serve autocomplete suggestions from prior DB entries
_AUTOCOMPLETE_FIELDS = {
    "carrier", "exposure_basis", "exposure_unit", "project_name",
    "exposure_city", "exposure_state",
}


def _renewal_statuses() -> list[str]:
    return cfg.get("renewal_statuses", ["Not Started", "In Progress", "Pending Bind", "Bound"])


_CONFIG_SEEDS: dict[str, str] = {
    "carrier": "carriers",
    "exposure_basis": "exposure_basis_options",
    "exposure_unit": "exposure_unit_options",
}


@router.get("/project-defaults", response_class=JSONResponse)
def policy_project_defaults(project_name: str, client_id: int = 0, conn=Depends(get_db)):
    """Return most recent exposure/location fields for a known project name."""
    if not project_name.strip():
        return JSONResponse({})
    row = conn.execute(
        """SELECT exposure_address, exposure_city, exposure_state, exposure_zip,
                  exposure_basis, exposure_unit
           FROM policies
           WHERE LOWER(TRIM(project_name)) = LOWER(TRIM(?))
             AND (? = 0 OR client_id = ?)
             AND archived = 0
           ORDER BY id DESC LIMIT 1""",
        (project_name, client_id, client_id),
    ).fetchone()
    if not row:
        return JSONResponse({})
    return JSONResponse({k: row[k] for k in row.keys() if row[k] is not None})


# Fields where autocomplete should be scoped to the same client
_CLIENT_SCOPED_AC_FIELDS = {"project_name", "exposure_city"}


def _sync_project_id(conn, policy_id: int, client_id: int, project_name: str | None) -> None:
    """Ensure a projects row exists for project_name and link policy.project_id to it.

    Case-insensitive: if 'Main St' already exists, typing 'main st' links to the
    same project rather than creating a duplicate.
    """
    name = (project_name or "").strip()
    if not name:
        conn.execute("UPDATE policies SET project_id = NULL WHERE id = ?", (policy_id,))
        return
    existing = conn.execute(
        "SELECT id FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, name),
    ).fetchone()
    if existing:
        project_id = existing["id"]
    else:
        cursor = conn.execute(
            "INSERT INTO projects (client_id, name) VALUES (?, ?)", (client_id, name)
        )
        project_id = cursor.lastrowid
    conn.execute("UPDATE policies SET project_id = ? WHERE id = ?", (project_id, policy_id))


@router.get("/colleague-data", response_class=JSONResponse)
def colleague_data(conn=Depends(get_db)):
    """Return JSON map of {name: {email, phone, role}} from all policy_contacts for autocomplete fill."""
    rows = conn.execute(
        """SELECT name, email, phone, role FROM policy_contacts
           WHERE name IS NOT NULL AND name != ''
           GROUP BY name ORDER BY name"""
    ).fetchall()
    return JSONResponse({
        r["name"]: {
            "email": r["email"] or "",
            "phone": r["phone"] or "",
            "role": r["role"] or "",
        }
        for r in rows
    })


@router.get("/autocomplete", response_class=JSONResponse)
def policy_autocomplete(field: str, q: str = "", client_id: int = 0, conn=Depends(get_db)):
    """Return distinct prior values for a policy field (used by <datalist>).

    For client-scoped fields (project_name, exposure_city), results are filtered
    to the specified client when client_id > 0.
    Merges config-seeded defaults with values already in the DB so the
    list is useful even on a fresh install.
    """
    if field not in _AUTOCOMPLETE_FIELDS:
        return JSONResponse([])

    if field == "project_name":
        if client_id > 0:
            rows = conn.execute(
                "SELECT name FROM projects WHERE client_id = ? ORDER BY name",
                (client_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT name FROM projects ORDER BY name").fetchall()
        db_values: list[str] = [r[0] for r in rows]
        if q:
            db_values = [v for v in db_values if q.lower() in v.lower()]
        return JSONResponse(db_values[:40])
    elif client_id > 0 and field in _CLIENT_SCOPED_AC_FIELDS:
        rows = conn.execute(
            f"SELECT DISTINCT {field} FROM policies WHERE client_id=? AND {field} IS NOT NULL AND {field} != '' ORDER BY {field}",  # noqa: S608
            (client_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT DISTINCT {field} FROM policies WHERE {field} IS NOT NULL AND {field} != '' ORDER BY {field}"  # noqa: S608
        ).fetchall()
    db_values: list[str] = [r[0] for r in rows]

    # Merge config-seeded defaults (DB values take precedence / dedup)
    seed_key = _CONFIG_SEEDS.get(field)
    if seed_key:
        seeds: list[str] = cfg.get(seed_key, [])
        db_lower = {v.lower() for v in db_values}
        for s in seeds:
            if s.lower() not in db_lower:
                db_values.append(s)
        db_values.sort(key=str.lower)

    if q:
        values = [v for v in db_values if q.lower() in v.lower()]
    else:
        values = db_values

    return JSONResponse(values[:40])


@router.get("/{policy_uid}/row", response_class=HTMLResponse)
def policy_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: display-mode policy table row (used by Cancel)."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/{policy_uid}/row/edit", response_class=HTMLResponse)
def policy_row_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline edit panel for a policy row."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_row_edit.html", {
        "request": request,
        "p": dict(policy),
        "policy_types": cfg.get("policy_types"),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/{policy_uid}/row/edit", response_class=HTMLResponse)
def policy_row_edit_post(
    request: Request,
    policy_uid: str,
    policy_type: str = Form(...),
    carrier: str = Form(...),
    policy_number: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    limit_amount: str = Form(""),
    commission_rate: str = Form(""),
    project_name: str = Form(""),
    follow_up_date: str = Form(""),
    attachment_point: str = Form(""),
    participation_of: str = Form(""),
    first_named_insured: str = Form(""),
    access_point: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX partial: save inline row edits, return updated display row."""
    def _float(v: str):
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    uid = policy_uid.upper()
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, policy_number=?,
           effective_date=?, expiration_date=?, premium=?,
           limit_amount=?, commission_rate=?, project_name=?,
           follow_up_date=?, attachment_point=?, participation_of=?,
           first_named_insured=?, access_point=?,
           description=?, notes=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _float(limit_amount), _float(commission_rate),
            project_name or None,
            follow_up_date or None,
            _float(attachment_point), _float(participation_of),
            first_named_insured or None, access_point or None,
            description or None, notes or None,
            uid,
        ),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    _sync_project_id(conn, policy["id"], policy["client_id"], project_name or None)
    conn.commit()
    return templates.TemplateResponse("policies/_policy_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/{policy_uid}/row/log", response_class=HTMLResponse)
def policy_row_log_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline activity log form for a specific policy row."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    default_subject = f"{p.get('policy_type', '')} — {p.get('renewal_status', '')}"
    return templates.TemplateResponse("policies/_policy_row_log.html", {
        "request": request,
        "p": p,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "quick_templates": cfg.get("quick_log_templates", []),
        "default_subject": default_subject,
    })


@router.post("/{policy_uid}/row/log", response_class=HTMLResponse)
def policy_row_log_post(
    request: Request,
    policy_uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_minutes: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity log entry, restore the policy row."""
    from datetime import date as _date

    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec, duration_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, _int(duration_minutes),
        ),
    )
    conn.commit()

    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)

    return templates.TemplateResponse("policies/_policy_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/{policy_uid}/dash/row", response_class=HTMLResponse)
def policy_dash_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: restore a dashboard pipeline row (used by Cancel in dash log form)."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_dash_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/{policy_uid}/dash/edit", response_class=HTMLResponse)
def policy_dash_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline quick-edit form for a dashboard pipeline row."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_dash_row_edit.html", {
        "request": request,
        "p": dict(policy),
        "policy_types": cfg.get("policy_types"),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/{policy_uid}/dash/edit", response_class=HTMLResponse)
def policy_dash_edit_post(
    request: Request,
    policy_uid: str,
    policy_type: str = Form(...),
    carrier: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    renewal_status: str = Form("Not Started"),
    follow_up_date: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save quick edits from dashboard, restore the dashboard row."""
    uid = policy_uid.upper()
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, expiration_date=?,
           premium=?, renewal_status=?, follow_up_date=?
           WHERE policy_uid=?""",
        (policy_type, carrier, expiration_date, premium,
         renewal_status, follow_up_date or None, uid),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_dash_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/{policy_uid}/dash/log", response_class=HTMLResponse)
def policy_dash_log_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline activity log form for a policy row on the dashboard."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    default_subject = f"{p.get('policy_type', '')} — {p.get('renewal_status', '')}"
    return templates.TemplateResponse("policies/_policy_dash_row_log.html", {
        "request": request,
        "p": p,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "quick_templates": cfg.get("quick_log_templates", []),
        "default_subject": default_subject,
    })


@router.post("/{policy_uid}/dash/log", response_class=HTMLResponse)
def policy_dash_log_post(
    request: Request,
    policy_uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_minutes: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity from dashboard, restore the dashboard pipeline row."""
    from datetime import date as _date

    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec, duration_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, _int(duration_minutes),
        ),
    )
    conn.commit()

    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)

    return templates.TemplateResponse("policies/_policy_dash_row.html", {
        "request": request,
        "p": dict(policy),
        "renewal_statuses": _renewal_statuses(),
    })


def _renew_mailto_subject(conn, policy_uid: str) -> str:
    """Compute pre-rendered email subject for a renewal pipeline row."""
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, policy_uid)
    return _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)


@router.get("/{policy_uid}/renew/row", response_class=HTMLResponse)
def policy_renew_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: restore a renewals pipeline row (Cancel in renew log form)."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    rows_progress = _attach_milestone_progress(conn, [p])
    return templates.TemplateResponse("policies/_policy_renew_row.html", {
        "request": request,
        "p": rows_progress[0],
        "mailto_subject": _renew_mailto_subject(conn, uid),
    })


@router.get("/{policy_uid}/renew/edit", response_class=HTMLResponse)
def policy_renew_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline quick-edit form for a renewals pipeline row."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_policy_renew_row_edit.html", {
        "request": request,
        "p": dict(policy),
        "policy_types": cfg.get("policy_types"),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/{policy_uid}/renew/edit", response_class=HTMLResponse)
def policy_renew_edit_post(
    request: Request,
    policy_uid: str,
    policy_type: str = Form(...),
    carrier: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    renewal_status: str = Form("Not Started"),
    follow_up_date: str = Form(""),
    effective_date: str = Form(""),
    policy_number: str = Form(""),
    limit_amount: str = Form(""),
    commission_rate: str = Form(""),
    access_point: str = Form(""),
    placement_colleague: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save quick edits from renewals page, restore the renewal row."""
    def _f(v):
        try: return float(v) if v else None
        except (ValueError, TypeError): return None

    uid = policy_uid.upper()
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, expiration_date=?,
           premium=?, renewal_status=?, follow_up_date=?,
           effective_date=?, policy_number=?, limit_amount=?,
           commission_rate=?, access_point=?, placement_colleague=?,
           description=?, notes=?
           WHERE policy_uid=?""",
        (policy_type, carrier, expiration_date, premium,
         renewal_status, follow_up_date or None,
         effective_date or None, policy_number or None,
         _f(limit_amount), _f(commission_rate),
         access_point or None, placement_colleague or None,
         description or None, notes or None,
         uid),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    rows_progress = _attach_milestone_progress(conn, [p])
    return templates.TemplateResponse("policies/_policy_renew_row.html", {
        "request": request,
        "p": rows_progress[0],
        "renewal_statuses": _renewal_statuses(),
        "mailto_subject": _renew_mailto_subject(conn, uid),
    })


@router.get("/{policy_uid}/renew/log", response_class=HTMLResponse)
def policy_renew_log_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline activity log form on the renewals page."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    default_subject = f"{p.get('policy_type', '')} — {p.get('renewal_status', '')}"
    return templates.TemplateResponse("policies/_policy_renew_row_log.html", {
        "request": request,
        "p": p,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "quick_templates": cfg.get("quick_log_templates", []),
        "default_subject": default_subject,
    })


@router.post("/{policy_uid}/renew/log", response_class=HTMLResponse)
def policy_renew_log_post(
    request: Request,
    policy_uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_minutes: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity from renewals page, restore the renewals pipeline row."""
    from datetime import date as _date

    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec, duration_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, _int(duration_minutes),
        ),
    )
    conn.commit()

    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)

    p = dict(policy)
    rows_progress = _attach_milestone_progress(conn, [p])
    return templates.TemplateResponse("policies/_policy_renew_row.html", {
        "request": request,
        "p": rows_progress[0],
        "mailto_subject": _renew_mailto_subject(conn, uid),
    })


@router.get("/{policy_uid}/team-cc")
def policy_team_cc(policy_uid: str, conn=Depends(get_db)):
    """JSON: return internal team CC options for a policy (for email popover lazy-load)."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return JSONResponse([])
    rows = conn.execute(
        """SELECT name, email FROM client_contacts
           WHERE client_id=? AND contact_type='internal'
             AND email IS NOT NULL AND email != ''
           ORDER BY name""",
        (policy["client_id"],),
    ).fetchall()
    return JSONResponse([{"name": r["name"], "email": r["email"]} for r in rows])


def _opp_row_response(request: Request, uid: str, conn):
    """Build the opportunity display row template response."""
    from datetime import date as _date
    from policydb import config as _cfg
    from policydb.email_templates import render_tokens as _render_tokens
    o = get_opportunity_by_uid(conn, uid)
    if not o:
        return HTMLResponse("", status_code=404)
    _subj_tpl = _cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    _ctx = {
        "client_name": o.get("client_name") or "",
        "policy_type": o.get("policy_type") or "",
        "carrier": o.get("carrier") or "",
        "policy_uid": o.get("policy_uid") or "",
        "project_name": (o.get("project_name") or "").strip(),
        "project_name_sep": f" \u2014 {o['project_name']}" if o.get("project_name") else "",
    }
    o["mailto_subject"] = _render_tokens(_subj_tpl, _ctx)
    return templates.TemplateResponse("policies/_opp_row.html", {
        "request": request,
        "o": o,
        "today": _date.today().isoformat(),
    })


@router.get("/{policy_uid}/opp/row", response_class=HTMLResponse)
def opp_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: restore opportunity display row (Cancel from log form)."""
    return _opp_row_response(request, policy_uid.upper(), conn)


@router.get("/{policy_uid}/opp/log", response_class=HTMLResponse)
def opp_log_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline activity log form for an opportunity row."""
    o = get_opportunity_by_uid(conn, policy_uid.upper())
    if not o:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_opp_log.html", {
        "request": request,
        "o": o,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
    })


@router.post("/{policy_uid}/opp/log", response_class=HTMLResponse)
def opp_log_post(
    request: Request,
    policy_uid: str,
    client_id: int = Form(...),
    policy_id: int = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_minutes: str = Form(""),
    contact_person: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity for an opportunity, restore the opportunity row."""
    from datetime import date as _date
    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person, subject, details, follow_up_date, account_exec, duration_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, contact_person or None, subject, details or None,
            follow_up_date or None, account_exec, _int(duration_minutes),
        ),
    )
    conn.commit()
    return _opp_row_response(request, policy_uid.upper(), conn)


def _build_checklist(conn, policy_uid: str) -> list[dict]:
    """Return checklist items for a policy, ordered by config milestone list."""
    milestones_cfg = cfg.get("renewal_milestones", [])
    rows = {r["milestone"]: dict(r) for r in conn.execute(
        "SELECT * FROM policy_milestones WHERE policy_uid=?", (policy_uid,)
    ).fetchall()}
    return [
        {
            "name": m,
            "completed": rows.get(m, {}).get("completed", 0),
            "completed_at": rows.get(m, {}).get("completed_at", ""),
        }
        for m in milestones_cfg
    ]


def _attach_milestone_progress(conn, rows: list[dict]) -> list[dict]:
    """Enrich pipeline row dicts with milestone_done / milestone_total counts."""
    total = len(cfg.get("renewal_milestones", []))
    if not total or not rows:
        for r in rows:
            r["milestone_done"] = 0
            r["milestone_total"] = total
        return rows
    uids = [r["policy_uid"] for r in rows]
    placeholders = ",".join("?" * len(uids))
    done_rows = conn.execute(
        f"SELECT policy_uid, SUM(completed) AS done FROM policy_milestones "  # noqa: S608
        f"WHERE policy_uid IN ({placeholders}) GROUP BY policy_uid",
        uids,
    ).fetchall()
    done_map = {r["policy_uid"]: (r["done"] or 0) for r in done_rows}
    for r in rows:
        r["milestone_done"] = done_map.get(r["policy_uid"], 0)
        r["milestone_total"] = total
    return rows


def _attach_readiness_score(conn, rows: list[dict]) -> list[dict]:
    """Attach renewal readiness score (0-100) and label to pipeline rows."""
    if not rows:
        return rows
    from datetime import date as _date
    # Batch-fetch last activity dates
    ids = [r["id"] for r in rows if r.get("id")]
    last_activity_map: dict = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        la_rows = conn.execute(
            f"SELECT policy_id, MAX(activity_date) AS last_date FROM activity_log "  # noqa: S608
            f"WHERE policy_id IN ({placeholders}) GROUP BY policy_id",
            ids,
        ).fetchall()
        last_activity_map = {r["policy_id"]: r["last_date"] for r in la_rows}

    today = _date.today()
    for p in rows:
        score = 0
        status = p.get("renewal_status") or "Not Started"

        # Status (0-40)
        status_scores = {
            "Not Started": 0, "In Progress": 20, "Submitted": 30,
            "Pending Bind": 35, "Bound": 40,
        }
        score += status_scores.get(status, 10)

        # Checklist (0-25)
        done = p.get("milestone_done", 0)
        total = max(p.get("milestone_total", 1) or 1, 1)
        score += int(25 * done / total)

        # Recent activity (0-15)
        last_act = last_activity_map.get(p.get("id"))
        if last_act:
            try:
                days_since = (today - _date.fromisoformat(last_act)).days
                score += 15 if days_since <= 7 else 10 if days_since <= 14 else 5 if days_since <= 30 else 0
            except (ValueError, TypeError):
                pass

        # Follow-up scheduled (0-10)
        if p.get("follow_up_date"):
            score += 10

        # Placement colleague assigned (0-10)
        if p.get("placement_colleague"):
            score += 10

        p["readiness_score"] = min(score, 100)
        rt = cfg.get("readiness_thresholds", {})
        p["readiness_label"] = (
            "READY" if score >= rt.get("ready", 75) else
            "ON TRACK" if score >= rt.get("on_track", 50) else
            "AT RISK" if score >= rt.get("at_risk", 25) else
            "CRITICAL"
        )
    return rows


@router.post("/{policy_uid}/milestones/{milestone}", response_class=HTMLResponse)
def toggle_milestone(
    request: Request,
    policy_uid: str,
    milestone: str,
    conn=Depends(get_db),
):
    """HTMX: toggle a renewal checklist milestone for a policy."""
    uid = policy_uid.upper()
    existing = conn.execute(
        "SELECT completed FROM policy_milestones WHERE policy_uid=? AND milestone=?",
        (uid, milestone),
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        new_val = 0 if existing["completed"] else 1
        conn.execute(
            "UPDATE policy_milestones SET completed=?, completed_at=? WHERE policy_uid=? AND milestone=?",
            (new_val, now if new_val else None, uid, milestone),
        )
    else:
        conn.execute(
            "INSERT INTO policy_milestones (policy_uid, milestone, completed, completed_at) VALUES (?,?,1,?)",
            (uid, milestone, now),
        )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    checklist = _build_checklist(conn, uid)
    done = sum(1 for c in checklist if c["completed"])
    total = len(cfg.get("renewal_milestones", []))
    import json as _json_ms
    response = templates.TemplateResponse("policies/_milestones.html", {
        "request": request,
        "policy": dict(policy),
        "checklist": checklist,
    })
    response.headers["HX-Trigger"] = _json_ms.dumps({
        "milestoneUpdated": {"uid": uid, "done": done, "total": total}
    })
    return response


@router.get("/{policy_uid}/milestones/popover", response_class=HTMLResponse)
def milestones_popover(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: milestone checklist popover content."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    checklist = _build_checklist(conn, uid)
    done = sum(1 for c in checklist if c["completed"])
    return templates.TemplateResponse("policies/_milestones_popover.html", {
        "request": request,
        "policy": dict(policy),
        "checklist": checklist,
        "done": done,
        "total": len(checklist),
    })


@router.get("/{policy_uid}/edit", response_class=HTMLResponse)
def policy_edit_form(request: Request, policy_uid: str, add_contact: str = "", conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    policy_dict = dict(policy)
    contacts = [dict(r) for r in conn.execute(
        "SELECT name, title, role, email, phone FROM client_contacts WHERE client_id=? AND contact_type='client' ORDER BY is_primary DESC, name",
        (policy_dict["client_id"],),
    ).fetchall()]
    team_contacts = [dict(r) for r in conn.execute(
        "SELECT id, name, title, role, email, phone FROM client_contacts WHERE client_id=? AND contact_type='internal' ORDER BY name",
        (policy_dict["client_id"],),
    ).fetchall()]
    policy_contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM policy_contacts WHERE policy_id=? ORDER BY role, name",
        (policy_dict["id"],),
    ).fetchall()]
    # All known contacts for autocomplete (name + email + role for auto-fill)
    all_contact_names = [r[0] for r in conn.execute(
        """SELECT DISTINCT name FROM (
               SELECT name FROM policy_contacts
               UNION
               SELECT name FROM client_contacts WHERE contact_type='internal'
           ) ORDER BY name"""
    ).fetchall()]
    import json as _json
    _ac_rows = conn.execute(
        """SELECT name,
                  MAX(email)        AS email,
                  MAX(role)         AS role,
                  MAX(phone)        AS phone,
                  MAX(title)        AS title,
                  MAX(organization) AS organization
           FROM (
               SELECT name, email, role, phone, NULL AS title, organization FROM policy_contacts
               UNION ALL
               SELECT name, email, role, phone, title, NULL AS organization FROM client_contacts WHERE contact_type='internal'
           )
           WHERE name IS NOT NULL AND name != ''
           GROUP BY name
           ORDER BY name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "title": r["title"] or "", "organization": r["organization"] or ""} for r in _ac_rows})
    activities = [dict(r) for r in conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.policy_id = ? AND a.activity_date >= date('now', '-90 days')
           ORDER BY a.activity_date DESC, a.id DESC""",
        (policy_dict["id"],),
    ).fetchall()]
    # Build CC options for email popover (opt-in, shown as checkboxes)
    import json as _json_edit
    team_cc_json = _json_edit.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])
    # Pre-render mailto subject from config template
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, uid)
    mailto_subject = _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)
    from policydb.queries import REVIEW_CYCLE_LABELS as _REVIEW_CYCLE_LABELS
    return templates.TemplateResponse("policies/edit.html", {
        "request": request,
        "active": "",
        "policy": policy_dict,
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
        "checklist": _build_checklist(conn, uid),
        "contacts": contacts,
        "team_contacts": team_contacts,
        "policy_contacts": policy_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "team_cc_json": team_cc_json,
        "mailto_subject": mailto_subject,
        "activities": activities,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "opportunity_statuses": cfg.get("opportunity_statuses"),
        "add_contact": add_contact,
        "cycle_labels": _REVIEW_CYCLE_LABELS,
    })


@router.post("/{policy_uid}/edit")
def policy_edit_post(
    request: Request,
    policy_uid: str,
    action: str = Form("save"),
    policy_type: str = Form(...),
    carrier: str = Form(""),
    is_opportunity: str = Form("0"),
    opportunity_status: str = Form(""),
    target_effective_date: str = Form(""),
    policy_number: str = Form(""),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    premium: str = Form("0"),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    description: str = Form(""),
    coverage_form: str = Form(""),
    layer_position: str = Form("Primary"),
    tower_group: str = Form(""),
    is_standalone: str = Form("0"),
    renewal_status: str = Form("Not Started"),
    commission_rate: str = Form(""),
    prior_premium: str = Form(""),
    notes: str = Form(""),
    project_name: str = Form(""),
    exposure_basis: str = Form(""),
    exposure_amount: str = Form(""),
    exposure_unit: str = Form(""),
    exposure_address: str = Form(""),
    exposure_city: str = Form(""),
    exposure_state: str = Form(""),
    exposure_zip: str = Form(""),
    follow_up_date: str = Form(""),
    attachment_point: str = Form(""),
    participation_of: str = Form(""),
    first_named_insured: str = Form(""),
    access_point: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v: str):
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    uid = policy_uid.upper()
    opp = 1 if is_opportunity == "1" else 0
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, policy_number=?,
           effective_date=?, expiration_date=?, premium=?,
           limit_amount=?, deductible=?, description=?,
           coverage_form=?, layer_position=?, tower_group=?,
           is_standalone=?, is_opportunity=?, opportunity_status=?, target_effective_date=?,
           renewal_status=?, commission_rate=?, prior_premium=?, notes=?,
           project_name=?, exposure_basis=?, exposure_amount=?, exposure_unit=?,
           exposure_address=?, exposure_city=?, exposure_state=?, exposure_zip=?,
           follow_up_date=?, attachment_point=?, participation_of=?,
           first_named_insured=?, access_point=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier or None, policy_number or None,
            effective_date or None, expiration_date or None, _float(premium) or 0,
            _float(limit_amount), _float(deductible), description or None,
            coverage_form or None, layer_position or "Primary", tower_group or None,
            1 if is_standalone == "1" else 0,
            opp, opportunity_status or None, target_effective_date or None,
            renewal_status,
            _float(commission_rate), _float(prior_premium), notes or None,
            project_name or None,
            exposure_basis or None, _float(exposure_amount), exposure_unit or None,
            exposure_address or None, exposure_city or None,
            exposure_state or None, exposure_zip or None,
            follow_up_date or None,
            _float(attachment_point), _float(participation_of),
            first_named_insured or None, access_point or None,
            uid,
        ),
    )
    conn.commit()

    policy = get_policy_by_uid(conn, uid)
    _client_id = policy["client_id"] if policy else 0
    _policy_id = policy["id"] if policy else 0
    if _policy_id:
        _sync_project_id(conn, _policy_id, _client_id, project_name or None)
        conn.commit()

    if action == "save_continue":
        return RedirectResponse(f"/policies/{uid}/edit", status_code=303)

    return RedirectResponse(f"/clients/{_client_id}", status_code=303)


@router.post("/{policy_uid}/convert", response_class=HTMLResponse)
def policy_convert_opportunity(
    request: Request,
    policy_uid: str,
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    carrier: str = Form(""),
    premium: str = Form("0"),
    conn=Depends(get_db),
):
    """Convert an opportunity to a real policy by setting dates and clearing the opportunity flag."""
    uid = policy_uid.upper()

    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    conn.execute(
        """UPDATE policies SET
           is_opportunity=0, opportunity_status=NULL,
           effective_date=?, expiration_date=?,
           renewal_status='Not Started'
           WHERE policy_uid=?""",
        (effective_date, expiration_date, uid),
    )
    # Only update carrier/premium if provided
    if carrier:
        conn.execute("UPDATE policies SET carrier=? WHERE policy_uid=?", (carrier, uid))
    if _float(premium):
        conn.execute("UPDATE policies SET premium=? WHERE policy_uid=?", (_float(premium), uid))
    conn.commit()
    return RedirectResponse(f"/policies/{uid}/edit", status_code=303)


def _policy_team_response(request, conn, policy_uid: str):
    """Return rendered _policy_team.html partial for a given policy."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    p = dict(policy)
    policy_contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM policy_contacts WHERE policy_id=? ORDER BY role, name",
        (p["id"],),
    ).fetchall()]
    team_contacts = [dict(r) for r in conn.execute(
        "SELECT id, name, title, role, email, phone FROM client_contacts WHERE client_id=? AND contact_type='internal' ORDER BY name",
        (p["client_id"],),
    ).fetchall()]
    all_contact_names = [r[0] for r in conn.execute(
        """SELECT DISTINCT name FROM (
               SELECT name FROM policy_contacts
               UNION
               SELECT name FROM client_contacts WHERE contact_type='internal'
           ) ORDER BY name"""
    ).fetchall()]
    import json as _json
    _ac_rows2 = conn.execute(
        """SELECT name,
                  MAX(email)        AS email,
                  MAX(role)         AS role,
                  MAX(phone)        AS phone,
                  MAX(title)        AS title,
                  MAX(organization) AS organization
           FROM (
               SELECT name, email, role, phone, NULL AS title, organization FROM policy_contacts
               UNION ALL
               SELECT name, email, role, phone, title, NULL AS organization FROM client_contacts WHERE contact_type='internal'
           )
           WHERE name IS NOT NULL AND name != ''
           GROUP BY name
           ORDER BY name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "title": r["title"] or "", "organization": r["organization"] or ""} for r in _ac_rows2})
    import json as _json_team
    team_cc_json = _json_team.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, policy_uid)
    mailto_subject = _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)
    return templates.TemplateResponse("policies/_policy_team.html", {
        "request": request,
        "policy": p,
        "policy_contacts": policy_contacts,
        "team_contacts": team_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "team_cc_json": team_cc_json,
        "mailto_subject": mailto_subject,
    })


@router.post("/{policy_uid}/team/add", response_class=HTMLResponse)
def policy_team_add(
    request: Request,
    policy_uid: str,
    name: str = Form(...),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    title: str = Form(""),
    organization: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "INSERT INTO policy_contacts (policy_id, name, title, role, phone, email, organization) VALUES (?,?,?,?,?,?,?)",
        (policy["id"], name, title or None, role or None,
         format_phone(phone) if phone else None, email or None, organization or None),
    )
    conn.commit()
    return _policy_team_response(request, conn, uid)


@router.post("/{policy_uid}/team/{contact_id}/delete", response_class=HTMLResponse)
def policy_team_delete(
    request: Request,
    policy_uid: str,
    contact_id: int,
    conn=Depends(get_db),
):
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "DELETE FROM policy_contacts WHERE id=? AND policy_id=?",
        (contact_id, policy["id"]),
    )
    conn.commit()
    return _policy_team_response(request, conn, uid)


@router.post("/{policy_uid}/team/{contact_id}/edit", response_class=HTMLResponse)
def policy_team_edit(
    request: Request,
    policy_uid: str,
    contact_id: int,
    name: str = Form(...),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    title: str = Form(""),
    organization: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "UPDATE policy_contacts SET name=?, title=?, role=?, phone=?, email=?, organization=? WHERE id=? AND policy_id=?",
        (name, title or None, role or None,
         format_phone(phone) if phone else None, email or None, organization or None,
         contact_id, policy["id"]),
    )
    conn.commit()
    return _policy_team_response(request, conn, uid)


@router.post("/{policy_uid}/status", response_class=HTMLResponse)
def policy_update_status(
    request: Request,
    policy_uid: str,
    status: str = Form(...),
    conn=Depends(get_db),
):
    """HTMX endpoint: update renewal status, return updated badge partial."""
    uid = policy_uid.upper()
    if status not in _renewal_statuses():
        status = _renewal_statuses()[0]
    conn.execute(
        "UPDATE policies SET renewal_status=? WHERE policy_uid=?",
        (status, uid),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
    p = dict(policy)
    return templates.TemplateResponse("policies/_status_badge.html", {
        "request": request,
        "p": p,
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/{policy_uid}/renew")
def policy_renew(policy_uid: str, conn=Depends(get_db)):
    """Create a new renewal term from an existing policy, archive the prior term."""
    new_uid = renew_policy(conn, policy_uid.upper())
    return RedirectResponse(f"/policies/{new_uid}/edit", status_code=303)


@router.post("/{policy_uid}/clear-followup", response_class=HTMLResponse)
def policy_clear_followup(policy_uid: str, conn=Depends(get_db)):
    """Clear a policy-level follow-up date (removes it from the unified follow-ups view)."""
    uid = policy_uid.upper()
    conn.execute("UPDATE policies SET follow_up_date=NULL WHERE policy_uid=?", (uid,))
    conn.commit()
    return HTMLResponse("")


@router.post("/{policy_uid}/snooze-followup", response_class=HTMLResponse)
def policy_snooze_followup(
    request: Request, policy_uid: str, days: int = 7, conn=Depends(get_db)
):
    """Reschedule a policy follow-up by +N days; returns updated row partial."""
    from datetime import date as _date
    uid = policy_uid.upper()
    conn.execute(
        "UPDATE policies SET follow_up_date = date(follow_up_date, ?) WHERE policy_uid=?",
        (f"+{days} days", uid),
    )
    conn.commit()
    row = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.follow_up_date,
                  p.project_name, p.client_id,
                  c.name AS client_name,
                  'policy' AS source,
                  'Policy Reminder' AS activity_type,
                  NULL AS contact_person, NULL AS contact_email, NULL AS internal_cc,
                  p.policy_type || ' – ' || p.carrier AS subject,
                  CAST(julianday('now') - julianday(p.follow_up_date) AS INTEGER) AS days_overdue
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    today = _date.today().isoformat()
    r["_is_overdue"] = r["follow_up_date"] < today
    return templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})


@router.post("/{policy_uid}/reschedule-followup", response_class=HTMLResponse)
def policy_reschedule_followup(
    request: Request, policy_uid: str, new_date: str = Form(...), conn=Depends(get_db)
):
    """Reschedule a policy follow-up to a specific date."""
    from datetime import date as _date
    uid = policy_uid.upper()
    conn.execute(
        "UPDATE policies SET follow_up_date = ? WHERE policy_uid=?",
        (new_date, uid),
    )
    conn.commit()
    row = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.follow_up_date,
                  p.project_name, p.client_id,
                  c.name AS client_name,
                  'policy' AS source,
                  'Policy Reminder' AS activity_type,
                  NULL AS contact_person, NULL AS contact_email, NULL AS internal_cc,
                  p.policy_type || ' – ' || p.carrier AS subject,
                  CAST(julianday('now') - julianday(p.follow_up_date) AS INTEGER) AS days_overdue
           FROM policies p JOIN clients c ON p.client_id = c.id
           WHERE p.policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    today = _date.today().isoformat()
    r["_is_overdue"] = r["follow_up_date"] < today
    return templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})


@router.get("/{policy_uid}/followup-form", response_class=HTMLResponse)
def policy_followup_form(request: Request, policy_uid: str):
    """HTMX: return inline date form for scheduling a follow-up from the suggested list."""
    uid = policy_uid.upper()
    return HTMLResponse(
        f'<form hx-post="/policies/{uid}/set-followup"'
        f' hx-target="#suggested-actions-{uid}" hx-swap="innerHTML"'
        f' class="flex gap-1.5 items-center">'
        f'<input type="date" name="follow_up_date" required'
        f' class="border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-marsh focus:outline-none">'
        f'<button type="submit" class="text-xs bg-marsh text-white px-2 py-1 rounded hover:bg-marsh-light">Set</button>'
        f'<button type="button"'
        f' hx-get="/policies/{uid}/followup-form-cancel"'
        f' hx-target="#suggested-actions-{uid}" hx-swap="innerHTML"'
        f' class="text-xs text-gray-400 hover:underline px-1">✕</button>'
        f'</form>'
    )


@router.get("/{policy_uid}/followup-form-cancel", response_class=HTMLResponse)
def policy_followup_form_cancel(request: Request, policy_uid: str):
    """HTMX: restore the original Schedule Follow-Up button."""
    uid = policy_uid.upper()
    return HTMLResponse(
        f'<div class="flex gap-1.5 items-center">'
        f'<button hx-get="/policies/{uid}/followup-form"'
        f' hx-target="#suggested-actions-{uid}" hx-swap="innerHTML"'
        f' class="text-xs border border-marsh text-marsh bg-white hover:bg-marsh hover:text-white px-2 py-1 rounded transition-colors">'
        f'Schedule Follow-Up</button>'
        f'<a href="/policies/{uid}/edit" class="text-xs text-gray-400 hover:text-marsh hover:underline">Edit →</a>'
        f'</div>'
    )


@router.post("/{policy_uid}/set-followup", response_class=HTMLResponse)
def policy_set_followup(
    request: Request, policy_uid: str, follow_up_date: str = Form(...), conn=Depends(get_db)
):
    """Set (or update) a policy follow-up date from the suggested section."""
    uid = policy_uid.upper()
    conn.execute(
        "UPDATE policies SET follow_up_date=? WHERE policy_uid=?",
        (follow_up_date, uid),
    )
    conn.commit()
    # Return confirmation + edit link in place of the form
    return HTMLResponse(
        f'<span class="text-xs text-green-600 font-medium">✓ Set {follow_up_date}</span>'
        f' <a href="/policies/{uid}/edit" class="text-xs text-gray-400 hover:text-marsh hover:underline ml-2">Edit →</a>'
    )


@router.post("/{policy_uid}/archive")
def policy_archive(policy_uid: str, conn=Depends(get_db)):
    """Archive a policy (soft delete — hidden from all views, data preserved)."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    client_id = policy["client_id"]
    conn.execute("UPDATE policies SET archived=1 WHERE policy_uid=?", (uid,))
    conn.commit()
    return RedirectResponse(f"/policies/{uid}/edit", status_code=303)


@router.get("/new", response_class=HTMLResponse)
def policy_new_form(request: Request, client: int = 0, opp: int = 0, conn=Depends(get_db)):
    client_row = get_client_by_id(conn, client) if client else None
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("policies/new.html", {
        "request": request,
        "active": "",
        "client": dict(client_row) if client_row else None,
        "all_clients": [dict(c) for c in all_clients],
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
        "opportunity_statuses": cfg.get("opportunity_statuses"),
        "default_opportunity": opp == 1,
    })


@router.post("/new")
def policy_new_post(
    request: Request,
    client_id: int = Form(...),
    policy_type: str = Form(...),
    carrier: str = Form(""),
    is_opportunity: str = Form("0"),
    opportunity_status: str = Form(""),
    target_effective_date: str = Form(""),
    policy_number: str = Form(""),
    effective_date: str = Form(""),
    expiration_date: str = Form(""),
    premium: str = Form("0"),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    description: str = Form(""),
    coverage_form: str = Form(""),
    layer_position: str = Form("Primary"),
    tower_group: str = Form(""),
    is_standalone: str = Form("0"),
    renewal_status: str = Form("Not Started"),
    placement_colleague: str = Form(""),
    underwriter_name: str = Form(""),
    underwriter_contact: str = Form(""),
    project_name: str = Form(""),
    exposure_basis: str = Form(""),
    exposure_amount: str = Form(""),
    exposure_unit: str = Form(""),
    exposure_address: str = Form(""),
    exposure_city: str = Form(""),
    exposure_state: str = Form(""),
    exposure_zip: str = Form(""),
    commission_rate: str = Form(""),
    prior_premium: str = Form(""),
    notes: str = Form(""),
    follow_up_date: str = Form(""),
    attachment_point: str = Form(""),
    participation_of: str = Form(""),
    first_named_insured: str = Form(""),
    access_point: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.db import next_policy_uid

    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    uid = next_policy_uid(conn)
    account_exec = cfg.get("default_account_exec", "Grant")
    opp = 1 if is_opportunity == "1" else 0
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, coverage_form, layer_position, tower_group, is_standalone,
            is_opportunity, opportunity_status, target_effective_date,
            renewal_status, placement_colleague, underwriter_name, underwriter_contact,
            account_exec, project_name,
            exposure_basis, exposure_amount, exposure_unit,
            exposure_address, exposure_city, exposure_state, exposure_zip,
            commission_rate, prior_premium, notes, follow_up_date,
            attachment_point, participation_of, first_named_insured, access_point)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, client_id, policy_type, carrier or None, policy_number or None,
         effective_date or None, expiration_date or None, _float(premium) or 0,
         _float(limit_amount), _float(deductible),
         description or None, coverage_form or None,
         layer_position or "Primary", tower_group or None,
         1 if is_standalone == "1" else 0,
         opp, opportunity_status or None, target_effective_date or None,
         renewal_status, placement_colleague or None,
         underwriter_name or None, underwriter_contact or None,
         account_exec, project_name or None,
         exposure_basis or None, _float(exposure_amount), exposure_unit or None,
         exposure_address or None, exposure_city or None,
         exposure_state or None, exposure_zip or None,
         _float(commission_rate), _float(prior_premium), notes or None,
         follow_up_date or None,
         _float(attachment_point), _float(participation_of),
         first_named_insured or None, access_point or None),
    )
    conn.commit()
    new_policy = get_policy_by_uid(conn, uid)
    if new_policy and project_name:
        _sync_project_id(conn, new_policy["id"], client_id, project_name)
        conn.commit()
    return RedirectResponse(f"/policies/{uid}/edit", status_code=303)
