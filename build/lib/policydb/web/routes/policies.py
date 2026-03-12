"""Policy routes."""

from __future__ import annotations

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


@router.get("/autocomplete", response_class=JSONResponse)
def policy_autocomplete(field: str, q: str = "", conn=Depends(get_db)):
    """Return distinct prior values for a policy field (used by <datalist>).

    Merges config-seeded defaults with values already in the DB so the
    list is useful even on a fresh install.
    """
    if field not in _AUTOCOMPLETE_FIELDS:
        return JSONResponse([])

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
    })


@router.get("/{policy_uid}/edit", response_class=HTMLResponse)
def policy_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    return templates.TemplateResponse("policies/edit.html", {
        "request": request,
        "active": "",
        "policy": dict(policy),
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
    })


@router.post("/{policy_uid}/edit")
def policy_edit_post(
    request: Request,
    policy_uid: str,
    action: str = Form("save"),
    policy_type: str = Form(...),
    carrier: str = Form(...),
    policy_number: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
    limit_amount: str = Form(""),
    deductible: str = Form(""),
    description: str = Form(""),
    coverage_form: str = Form(""),
    layer_position: str = Form("Primary"),
    tower_group: str = Form(""),
    is_standalone: str = Form("0"),
    placement_colleague: str = Form(""),
    underwriter_name: str = Form(""),
    underwriter_contact: str = Form(""),
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
    conn=Depends(get_db),
):
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
           limit_amount=?, deductible=?, description=?,
           coverage_form=?, layer_position=?, tower_group=?,
           is_standalone=?, placement_colleague=?,
           underwriter_name=?, underwriter_contact=?,
           renewal_status=?, commission_rate=?, prior_premium=?, notes=?,
           project_name=?, exposure_basis=?, exposure_amount=?, exposure_unit=?,
           exposure_address=?, exposure_city=?, exposure_state=?, exposure_zip=?,
           follow_up_date=?, attachment_point=?, participation_of=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier, policy_number or None,
            effective_date, expiration_date, premium,
            _float(limit_amount), _float(deductible), description or None,
            coverage_form or None, layer_position or "Primary", tower_group or None,
            1 if is_standalone == "1" else 0,
            placement_colleague or None, underwriter_name or None,
            underwriter_contact or None, renewal_status,
            _float(commission_rate), _float(prior_premium), notes or None,
            project_name or None,
            exposure_basis or None, _float(exposure_amount), exposure_unit or None,
            exposure_address or None, exposure_city or None,
            exposure_state or None, exposure_zip or None,
            follow_up_date or None,
            _float(attachment_point), _float(participation_of),
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
def policy_new_form(request: Request, client: int = 0, conn=Depends(get_db)):
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
    })


@router.post("/new")
def policy_new_post(
    request: Request,
    client_id: int = Form(...),
    policy_type: str = Form(...),
    carrier: str = Form(...),
    policy_number: str = Form(""),
    effective_date: str = Form(...),
    expiration_date: str = Form(...),
    premium: float = Form(...),
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
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, coverage_form, layer_position, tower_group, is_standalone,
            renewal_status, placement_colleague, underwriter_name, underwriter_contact,
            account_exec, project_name,
            exposure_basis, exposure_amount, exposure_unit,
            exposure_address, exposure_city, exposure_state, exposure_zip,
            commission_rate, prior_premium, notes, follow_up_date,
            attachment_point, participation_of)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, client_id, policy_type, carrier, policy_number or None,
         effective_date, expiration_date, premium,
         _float(limit_amount), _float(deductible),
         description or None, coverage_form or None,
         layer_position or "Primary", tower_group or None,
         1 if is_standalone == "1" else 0,
         renewal_status, placement_colleague or None,
         underwriter_name or None, underwriter_contact or None,
         account_exec, project_name or None,
         exposure_basis or None, _float(exposure_amount), exposure_unit or None,
         exposure_address or None, exposure_city or None,
         exposure_state or None, exposure_zip or None,
         _float(commission_rate), _float(prior_premium), notes or None,
         follow_up_date or None,
         _float(attachment_point), _float(participation_of)),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)
