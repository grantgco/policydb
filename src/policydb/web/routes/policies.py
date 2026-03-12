"""Policy routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.queries import get_all_policies, get_client_by_id, get_policy_by_uid, renew_policy
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
    "carrier", "placement_colleague", "placement_colleague_email", "underwriter_name",
    "exposure_basis", "exposure_unit", "project_name",
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

    if client_id > 0 and field in _CLIENT_SCOPED_AC_FIELDS:
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
    placement_colleague: str = Form(""),
    placement_colleague_email: str = Form(""),
    first_named_insured: str = Form(""),
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
           placement_colleague=?, placement_colleague_email=?,
           first_named_insured=?,
           description=?, notes=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _float(limit_amount), _float(commission_rate),
            project_name or None,
            follow_up_date or None,
            _float(attachment_point), _float(participation_of),
            placement_colleague or None, placement_colleague_email or None,
            first_named_insured or None,
            description or None, notes or None,
            uid,
        ),
    )
    conn.commit()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)
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
    return templates.TemplateResponse("policies/_policy_row_log.html", {
        "request": request,
        "p": dict(policy),
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
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
    conn=Depends(get_db),
):
    """HTMX: save activity log entry, restore the policy row."""
    from datetime import date as _date

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec,
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
    return templates.TemplateResponse("policies/_policy_dash_row_log.html", {
        "request": request,
        "p": dict(policy),
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
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
    conn=Depends(get_db),
):
    """HTMX: save activity from dashboard, restore the dashboard pipeline row."""
    from datetime import date as _date

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec,
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
    conn=Depends(get_db),
):
    """HTMX: save quick edits from renewals page, restore the renewal row."""
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
    return templates.TemplateResponse("policies/_policy_renew_row_log.html", {
        "request": request,
        "p": dict(policy),
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
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
    conn=Depends(get_db),
):
    """HTMX: save activity from renewals page, restore the renewals pipeline row."""
    from datetime import date as _date

    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec,
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
    return templates.TemplateResponse("policies/_milestones.html", {
        "request": request,
        "policy": dict(policy),
        "checklist": _build_checklist(conn, uid),
    })


@router.get("/{policy_uid}/edit", response_class=HTMLResponse)
def policy_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
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
        """SELECT name, email, role, phone, title FROM (
               SELECT name, email, role, phone, NULL as title FROM policy_contacts WHERE email IS NOT NULL
               UNION
               SELECT name, email, role, phone, title FROM client_contacts WHERE contact_type='internal' AND email IS NOT NULL
           ) GROUP BY name ORDER BY name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "title": r["title"] or ""} for r in _ac_rows})
    activities = [dict(r) for r in conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.policy_id = ? AND a.activity_date >= date('now', '-90 days')
           ORDER BY a.activity_date DESC, a.id DESC""",
        (policy_dict["id"],),
    ).fetchall()]
    # Build CC string from internal team emails for mailto links
    internal_cc = ",".join(c["email"] for c in team_contacts if c.get("email"))
    # Pre-render mailto subject from config template
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, uid)
    mailto_subject = _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)
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
        "internal_cc": internal_cc,
        "mailto_subject": mailto_subject,
        "activities": activities,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "opportunity_statuses": cfg.get("opportunity_statuses"),
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
           first_named_insured=?
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
            first_named_insured or None,
            uid,
        ),
    )
    conn.commit()

    if action == "save_continue":
        return RedirectResponse(f"/policies/{uid}/edit", status_code=303)

    # Redirect back to client detail
    policy = get_policy_by_uid(conn, uid)
    client_id = policy["client_id"] if policy else 0
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


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
        """SELECT name, email, role, phone, title FROM (
               SELECT name, email, role, phone, NULL as title FROM policy_contacts WHERE email IS NOT NULL
               UNION
               SELECT name, email, role, phone, title FROM client_contacts WHERE contact_type='internal' AND email IS NOT NULL
           ) GROUP BY name ORDER BY name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "title": r["title"] or ""} for r in _ac_rows2})
    internal_cc = ",".join(c["email"] for c in team_contacts if c.get("email"))
    return templates.TemplateResponse("policies/_policy_team.html", {
        "request": request,
        "policy": p,
        "policy_contacts": policy_contacts,
        "team_contacts": team_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "internal_cc": internal_cc,
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
    conn=Depends(get_db),
):
    from policydb.utils import format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "INSERT INTO policy_contacts (policy_id, name, title, role, phone, email) VALUES (?,?,?,?,?,?)",
        (policy["id"], name, title or None, role or None,
         format_phone(phone) if phone else None, email or None),
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
    conn=Depends(get_db),
):
    from policydb.utils import format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "UPDATE policy_contacts SET name=?, title=?, role=?, phone=?, email=? WHERE id=? AND policy_id=?",
        (name, title or None, role or None,
         format_phone(phone) if phone else None, email or None,
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
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


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
            attachment_point, participation_of, first_named_insured)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
         first_named_insured or None),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
