"""Policy routes."""

from __future__ import annotations

import logging
logger = logging.getLogger("policydb.web.routes.policies")

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from policydb import config as cfg
from policydb.llm_schemas import (
    POLICY_EXTRACTION_SCHEMA,
    generate_extraction_prompt,
    generate_json_template,
    parse_llm_json,
)
from policydb.queries import get_all_policies, get_client_by_id, get_opportunity_by_uid, get_policy_by_uid, get_policy_total_hours, get_saved_notes, save_note, delete_saved_note, renew_policy, get_or_create_contact, assign_contact_to_policy, remove_contact_from_policy, set_placement_colleague, get_policy_contacts
from policydb.utils import round_duration, normalize_carrier, normalize_coverage_type, normalize_policy_number, format_city, format_state, format_zip
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
    "exposure_city", "exposure_state", "access_point", "first_named_insured",
    "tower_group",
}


def _renewal_statuses() -> list[str]:
    return cfg.get("renewal_statuses", ["Not Started", "In Progress", "Pending Bind", "Bound"])


_CONFIG_SEEDS: dict[str, str] = {
    "carrier": "carriers",
    "exposure_basis": "exposure_basis_options",
    "exposure_unit": "exposure_unit_options",
}

# ── Status color system ───────────────────────────────────────────────────────

_STATUS_COLORS: dict[str, tuple[str, str, str]] = {
    "Not Started": ("gray-100", "gray-600", "gray-300"),
    "In Progress": ("blue-100", "blue-700", "blue-300"),
    "Quoted": ("purple-100", "purple-700", "purple-300"),
    "Pending Bind": ("amber-100", "amber-700", "amber-300"),
    "Bound": ("green-100", "green-700", "green-300"),
}

_COLOR_PALETTE: list[tuple[str, str, str]] = [
    ("pink-100", "pink-700", "pink-300"),
    ("sky-100", "sky-700", "sky-300"),
    ("yellow-100", "yellow-700", "yellow-300"),
    ("rose-100", "rose-700", "rose-300"),
    ("teal-100", "teal-700", "teal-300"),
    ("indigo-100", "indigo-700", "indigo-300"),
    ("orange-100", "orange-700", "orange-300"),
    ("lime-100", "lime-700", "lime-300"),
]


def get_status_color(status: str, all_statuses: list | None = None) -> tuple[str, str, str]:
    """Return (bg, text, border) Tailwind color classes for a renewal status.

    Built-in statuses get fixed semantic colors. Custom statuses (not in the
    fixed map) are auto-assigned a color from _COLOR_PALETTE based on their
    position in the list of custom statuses, cycling through the palette if
    there are more than 8.
    """
    if status in _STATUS_COLORS:
        return _STATUS_COLORS[status]
    if all_statuses:
        custom = [s for s in all_statuses if s not in _STATUS_COLORS]
        try:
            idx = custom.index(status)
            return _COLOR_PALETTE[idx % len(_COLOR_PALETTE)]
        except ValueError:
            pass
    return ("gray-100", "gray-600", "gray-300")


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
_CLIENT_SCOPED_AC_FIELDS = {"project_name", "exposure_city", "tower_group"}


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
    """Return JSON map of {name: {email, phone, role}} from all policy contact assignments for autocomplete fill."""
    rows = conn.execute(
        """SELECT co.name, co.email, co.phone, cpa.role
           FROM contacts co
           JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.name ORDER BY co.name"""
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
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity log entry, restore the policy row."""
    from datetime import date as _date

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
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, round_duration(duration_hours),
        ),
    )
    if follow_up_date:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
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
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity from dashboard, restore the dashboard pipeline row."""
    from datetime import date as _date

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
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, round_duration(duration_hours),
        ),
    )
    if follow_up_date:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
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
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity from renewals page, restore the renewals pipeline row."""
    from datetime import date as _date

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
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, subject, details or None,
            follow_up_date or None, account_exec, round_duration(duration_hours),
        ),
    )
    if follow_up_date:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
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
    """JSON: return team CC options for a policy — internal team + policy contacts."""
    policy = get_policy_by_uid(conn, policy_uid.upper())
    if not policy:
        return JSONResponse([])
    emails: list[dict] = []
    seen: set[str] = set()

    # Internal team members for the client
    internal = conn.execute(
        """SELECT co.name, co.email
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE cca.client_id=? AND cca.contact_type='internal'
             AND co.email IS NOT NULL AND TRIM(co.email) != ''
           ORDER BY co.name""",
        (policy["client_id"],),
    ).fetchall()
    for r in internal:
        key = r["email"].strip().lower()
        if key not in seen:
            seen.add(key)
            emails.append({"name": r["name"], "email": r["email"]})

    # Policy contacts (placement colleagues, underwriters, etc.)
    pc_rows = conn.execute(
        """SELECT co.name, co.email
           FROM contacts co
           JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           WHERE cpa.policy_id = ? AND co.email IS NOT NULL AND TRIM(co.email) != ''
           ORDER BY co.name""",
        (policy["id"],),
    ).fetchall()
    for r in pc_rows:
        key = r["email"].strip().lower()
        if key not in seen:
            seen.add(key)
            emails.append({"name": r["name"], "email": r["email"]})

    return JSONResponse(emails)


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
    duration_hours: str = Form(""),
    contact_person: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save activity for an opportunity, restore the opportunity row."""
    from datetime import date as _date
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person, subject, details, follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), client_id, policy_id,
            activity_type, contact_person or None, subject, details or None,
            follow_up_date or None, account_exec, round_duration(duration_hours),
        ),
    )
    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    conn.commit()
    return _opp_row_response(request, policy_uid.upper(), conn)


# ---------------------------------------------------------------------------
# Opportunity inline edit (client detail page)
# ---------------------------------------------------------------------------

def _opp_client_row_response(request: Request, uid: str, conn):
    """Build the opportunity client-detail display row template response."""
    from policydb.email_templates import render_tokens as _render_tokens
    o = get_opportunity_by_uid(conn, uid)
    if not o:
        return HTMLResponse("", status_code=404)
    _subj_tpl = cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    _ctx = {
        "client_name": o.get("client_name") or "",
        "policy_type": o.get("policy_type") or "",
        "carrier": o.get("carrier") or "",
        "policy_uid": o.get("policy_uid") or "",
        "project_name": (o.get("project_name") or "").strip(),
        "project_name_sep": f" \u2014 {o['project_name']}" if o.get("project_name") else "",
    }
    o["mailto_subject"] = _render_tokens(_subj_tpl, _ctx)
    # Attach team contacts
    pc_rows = conn.execute(
        "SELECT cpa.policy_id, co.name, co.email, co.phone, cpa.role, co.organization "
        "FROM contact_policy_assignments cpa "
        "JOIN contacts co ON cpa.contact_id = co.id "
        "WHERE cpa.policy_id = ?",
        (o["id"],),
    ).fetchall()
    o["team"] = [dict(r) for r in pc_rows]
    return templates.TemplateResponse("policies/_opp_client_row.html", {
        "request": request,
        "o": o,
    })


@router.get("/{policy_uid}/opp/client-row", response_class=HTMLResponse)
def opp_client_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: restore opportunity client-detail display row (Cancel from edit form)."""
    return _opp_client_row_response(request, policy_uid.upper(), conn)


@router.get("/{policy_uid}/opp/edit", response_class=HTMLResponse)
def opp_row_edit_form(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: inline edit form for an opportunity row on the client detail page."""
    o = get_opportunity_by_uid(conn, policy_uid.upper())
    if not o:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("policies/_opp_row_edit.html", {
        "request": request,
        "o": dict(o),
        "policy_types": cfg.get("policy_types", []),
        "opportunity_statuses": cfg.get("opportunity_statuses", []),
    })


@router.post("/{policy_uid}/opp/edit", response_class=HTMLResponse)
def opp_row_edit_post(
    request: Request,
    policy_uid: str,
    policy_type: str = Form(""),
    carrier: str = Form(""),
    opportunity_status: str = Form(""),
    target_effective_date: str = Form(""),
    premium: str = Form(""),
    commission_rate: str = Form(""),
    description: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save inline opportunity edits, return updated client-detail display row."""
    uid = policy_uid.upper()

    def _float(v: str):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    policy_type_clean = normalize_coverage_type(policy_type) if policy_type.strip() else None
    conn.execute(
        """UPDATE policies SET
               policy_type=?, carrier=?, opportunity_status=?,
               target_effective_date=?, premium=?, commission_rate=?,
               description=?
           WHERE policy_uid=?""",
        (
            policy_type_clean,
            carrier.strip() or None,
            opportunity_status.strip() or None,
            target_effective_date.strip() or None,
            _float(premium) or 0,
            _float(commission_rate),
            description.strip() or None,
            uid,
        ),
    )
    conn.commit()
    return _opp_client_row_response(request, uid, conn)


def _build_checklist(conn, policy_uid: str) -> list[dict]:
    """Return checklist items for a policy, ordered by config milestone list."""
    milestones_cfg = cfg.get("renewal_milestones", [])
    critical_set = set(cfg.get("critical_milestones", []))
    rows = {r["milestone"]: dict(r) for r in conn.execute(
        "SELECT * FROM policy_milestones WHERE policy_uid=?", (policy_uid,)
    ).fetchall()}
    return [
        {
            "name": m,
            "completed": rows.get(m, {}).get("completed", 0),
            "completed_at": rows.get(m, {}).get("completed_at", ""),
            "is_critical": m in critical_set,
        }
        for m in milestones_cfg
    ]


def _attach_milestone_progress(conn, rows: list[dict]) -> list[dict]:
    """Enrich pipeline row dicts with milestone_done / milestone_total counts.

    Also computes weighted_done / weighted_total for readiness scoring
    using per-milestone weights from config.
    """
    all_milestones = cfg.get("renewal_milestones", [])
    milestone_weights = cfg.get("readiness_milestone_weights", {})
    total = len(all_milestones)
    # Compute weighted totals from config
    weighted_total = sum(milestone_weights.get(m, 1) for m in all_milestones)
    if not total or not rows:
        for r in rows:
            r["milestone_done"] = 0
            r["milestone_total"] = total
            r["weighted_done"] = 0
            r["weighted_total"] = weighted_total
        return rows
    uids = [r["policy_uid"] for r in rows]
    placeholders = ",".join("?" * len(uids))
    done_rows = conn.execute(
        f"SELECT policy_uid, SUM(completed) AS done FROM policy_milestones "  # noqa: S608
        f"WHERE policy_uid IN ({placeholders}) GROUP BY policy_uid",
        uids,
    ).fetchall()
    done_map = {r["policy_uid"]: (r["done"] or 0) for r in done_rows}

    # Batch-fetch per-milestone completion for weighted scoring
    completed_milestones_map: dict[str, set] = {}
    if all_milestones:
        ms_ph = ",".join("?" * len(all_milestones))
        ms_rows = conn.execute(
            f"SELECT policy_uid, milestone FROM policy_milestones "  # noqa: S608
            f"WHERE policy_uid IN ({placeholders}) AND milestone IN ({ms_ph}) AND completed = 1",
            uids + all_milestones,
        ).fetchall()
        for mr in ms_rows:
            completed_milestones_map.setdefault(mr["policy_uid"], set()).add(mr["milestone"])

    for r in rows:
        r["milestone_done"] = done_map.get(r["policy_uid"], 0)
        r["milestone_total"] = total
        completed = completed_milestones_map.get(r["policy_uid"], set())
        r["weighted_done"] = sum(milestone_weights.get(m, 1) for m in completed)
        r["weighted_total"] = weighted_total
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

    # Batch-fetch placement colleague assignments from contact_policy_assignments
    has_pc: set = set()
    if ids:
        pc_rows = conn.execute(
            f"SELECT DISTINCT cpa.policy_id FROM contact_policy_assignments cpa "  # noqa: S608
            f"WHERE cpa.is_placement_colleague = 1 AND cpa.policy_id IN ({placeholders})",
            ids,
        ).fetchall()
        has_pc = {r["policy_id"] for r in pc_rows}

    today = _date.today()
    renewal_window = cfg.get("renewal_window_days", 180)
    for p in rows:
        # Only compute readiness for policies within the renewal window
        days_to = p.get("days_to_renewal")
        if days_to is not None and days_to > renewal_window:
            p["readiness_score"] = None
            p["readiness_label"] = None
            p["readiness_tooltip"] = None
            continue

        weights = cfg.get("readiness_weights", {})
        w_status = weights.get("status", 40)
        w_checklist = weights.get("checklist", 25)
        w_activity = weights.get("activity", 15)
        w_followup = weights.get("followup", 10)
        w_placement = weights.get("placement", 10)

        parts = []
        score = 0
        status = p.get("renewal_status") or "Not Started"

        # Status — config-driven per-status percentages
        status_pcts = cfg.get("readiness_status_scores", {})
        s_pct = status_pcts.get(status, 25)
        s_pts = int(w_status * s_pct / 100)
        score += s_pts
        parts.append(f"Status: {s_pts}/{w_status} ({status})")

        # Checklist — per-milestone weights from config
        done = p.get("milestone_done", 0)
        total = p.get("milestone_total", 0) or 0
        w_done = p.get("weighted_done", 0)
        w_total = p.get("weighted_total", 0) or 0
        if w_total > 0:
            c_pts = int(w_checklist * w_done / w_total)
        else:
            c_pts = 0
        score += c_pts
        parts.append(f"Checklist: {c_pts}/{w_checklist} ({done}/{total})")

        # Recent activity — config-driven tiers
        last_act = last_activity_map.get(p.get("id"))
        a_pts = 0
        a_desc = "none"
        if last_act:
            try:
                days_since = (today - _date.fromisoformat(last_act)).days
                a_desc = f"{days_since}d ago"
                for tier in cfg.get("readiness_activity_tiers", []):
                    if days_since <= tier.get("days", 0):
                        a_pts = int(w_activity * tier.get("pct", 0) / 100)
                        break
            except (ValueError, TypeError):
                pass
        score += a_pts
        parts.append(f"Activity: {a_pts}/{w_activity} ({a_desc})")

        # Follow-up scheduled
        f_pts = w_followup if p.get("follow_up_date") else 0
        score += f_pts
        parts.append(f"Follow-up: {f_pts}/{w_followup}")

        # Placement colleague assigned
        pc_pts = w_placement if p.get("id") in has_pc or p.get("placement_colleague") else 0
        score += pc_pts
        parts.append(f"Placement: {pc_pts}/{w_placement}")

        total_score = min(score, 100)
        p["readiness_score"] = total_score
        rt = cfg.get("readiness_thresholds", {})
        p["readiness_label"] = (
            "READY" if total_score >= rt.get("ready", 75) else
            "ON TRACK" if total_score >= rt.get("on_track", 50) else
            "AT RISK" if total_score >= rt.get("at_risk", 25) else
            "CRITICAL"
        )
        p["readiness_tooltip"] = f"Score: {total_score}/100\n" + "\n".join(parts)
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

    # When a checklist milestone is completed, sync to timeline if mapped
    completed_now = (existing is None) or (not existing["completed"])
    if completed_now:
        from policydb.timeline_engine import complete_timeline_milestone
        activities = cfg.get("mandated_activities", [])
        for act in activities:
            if act.get("checklist_milestone") == milestone:
                complete_timeline_milestone(conn, uid, act["name"])
                break

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


@router.get("/{policy_uid}/export")
def export_policy(policy_uid: str, conn=Depends(get_db)):
    from policydb.exporter import export_single_policy_xlsx
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    content = export_single_policy_xlsx(conn, uid)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{uid}_detail.xlsx"'},
    )


def _build_cluster(activities: list[dict]) -> dict:
    """Build a display cluster from a list of activities (ordered DESC)."""
    dates = [a["activity_date"] for a in activities if a.get("activity_date")]
    total_hours = sum(a.get("duration_hours") or 0 for a in activities)
    has_pending = any(not a.get("follow_up_done", 1) for a in activities)
    return {
        "date_start": dates[-1] if dates else "",
        "date_end": dates[0] if dates else "",
        "activity_count": len(activities),
        "total_hours": total_hours,
        "has_pending": has_pending,
        "activities": activities,
    }


def _policy_base(conn, uid: str):
    """Load base policy + client info used by all tab routes."""
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return None, None
    p = dict(policy)
    client_row = conn.execute("SELECT id, name, cn_number FROM clients WHERE id = ?", (p["client_id"],)).fetchone()
    client_info = dict(client_row) if client_row else {"id": p["client_id"], "name": "", "cn_number": ""}
    # Inject client_name/cn_number into policy dict for convenience
    p["client_name"] = client_info["name"]
    p["cn_number"] = client_info.get("cn_number", "")
    return p, client_info


# ── AI Import endpoints ──────────────────────────────────────────────────────


@router.get("/{policy_uid}/ai-import/prompt", response_class=HTMLResponse)
def policy_ai_import_prompt(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Return the AI import slideover panel with the generated extraction prompt."""
    uid = policy_uid.upper()
    policy_dict, client_info = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    # Build context for prompt generation
    context: dict = {
        "client_name": client_info["name"],
        "industry": client_info.get("industry_segment", ""),
        "config_lists": {},
    }

    # Collect config values referenced by schema fields
    seen_config_keys: set[str] = set()
    for field in POLICY_EXTRACTION_SCHEMA["fields"]:
        ck = field.get("config_values")
        if ck and ck not in seen_config_keys:
            seen_config_keys.add(ck)
            context["config_lists"][ck] = cfg.get(ck, [])

    prompt_text = generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)
    json_template = generate_json_template(POLICY_EXTRACTION_SCHEMA)

    context_display: dict[str, str] = {"Client": client_info["name"]}
    industry = client_info.get("industry_segment", "")
    if industry:
        context_display["Industry"] = industry

    return templates.TemplateResponse("_ai_import_panel.html", {
        "request": request,
        "import_type": "policy",
        "prompt_text": prompt_text,
        "json_template": json_template,
        "context_display": context_display,
        "parse_url": f"/policies/{uid}/ai-import/parse",
        "import_target": "#ai-import-target",
    })


@router.post("/{policy_uid}/ai-import/parse", response_class=HTMLResponse)
def policy_ai_import_parse(
    request: Request,
    policy_uid: str,
    json_text: str = Form(...),
    conn=Depends(get_db),
):
    """Parse LLM JSON response and return pre-filled Details tab partial."""
    uid = policy_uid.upper()
    result = parse_llm_json(json_text, POLICY_EXTRACTION_SCHEMA)

    if not result["ok"]:
        return HTMLResponse(
            f'<div class="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">'
            f'{result["error"]}</div>',
            status_code=422,
        )

    policy_dict, client_info = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    # Merge parsed values onto existing policy
    merged = dict(policy_dict)
    for k, v in result["parsed"].items():
        if v is not None:
            merged[k] = v

    # FEIN cross-reference warning
    ai_warnings: list[str] = list(result.get("warnings", []))
    parsed_fein = result["parsed"].get("fein")
    if parsed_fein:
        client_fein = conn.execute(
            "SELECT fein FROM clients WHERE id = ?", (client_info["id"],)
        ).fetchone()
        if client_fein and client_fein["fein"] and client_fein["fein"] != parsed_fein:
            ai_warnings.append(
                f"FEIN mismatch: document shows {parsed_fein}, "
                f"client record has {client_fein['fein']}"
            )

    # Build the same context as policy_tab_details
    from policydb.queries import REVIEW_CYCLE_LABELS as _RCL

    # Tower structure
    _tower_layers: list[dict] = []
    if merged.get("tower_group"):
        _tg_rows = conn.execute(
            """SELECT policy_uid, policy_type, carrier, limit_amount, layer_position,
                      attachment_point, participation_of
               FROM policies
               WHERE client_id = ? AND LOWER(TRIM(tower_group)) = LOWER(TRIM(?)) AND archived = 0""",
            (merged["client_id"], merged["tower_group"]),
        ).fetchall()

        def _layer_sort_key(r):
            att = r["attachment_point"]
            if att is not None:
                return (float(att), 0)
            lp = r["layer_position"] or "Primary"
            try:
                return (-1, int(lp))
            except (ValueError, TypeError):
                return (-1, 0)

        _tg_rows = sorted(_tg_rows, key=_layer_sort_key)
        running = 0.0
        for tr in _tg_rows:
            lim = float(tr["limit_amount"] or 0)
            att = tr["attachment_point"]
            part = tr["participation_of"]
            if att is not None and float(att) >= 0:
                layer_size = float(part) if part else lim
                ground_up = float(att) + layer_size
            else:
                running += lim
                ground_up = running
            _tower_layers.append(dict(tr) | {"ground_up": ground_up, "is_current": tr["policy_uid"] == uid})

    html = templates.TemplateResponse("policies/_tab_details.html", {
        "request": request,
        "policy": merged,
        "client": client_info,
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
        "opportunity_statuses": cfg.get("opportunity_statuses"),
        "tower_layers": _tower_layers,
        "cycle_labels": _RCL,
        "program_linked_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium, effective_date, expiration_date
               FROM policies WHERE program_id = ? AND archived = 0 ORDER BY policy_type""",
            (merged["id"],),
        ).fetchall()] if merged.get("is_program") else [],
        "linkable_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium
               FROM policies WHERE client_id = ? AND archived = 0
                 AND (is_program = 0 OR is_program IS NULL)
                 AND (is_opportunity = 0 OR is_opportunity IS NULL)
                 AND (program_id IS NULL OR program_id = ?)
               ORDER BY policy_type""",
            (merged["client_id"], merged["id"]),
        ).fetchall()] if merged.get("is_program") else [],
        "program_carrier_rows": [dict(r) for r in conn.execute(
            "SELECT * FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (merged["id"],),
        ).fetchall()] if merged.get("is_program") else [],
        "ai_warnings": ai_warnings,
    })

    # Append OOB warnings div if there are warnings
    if ai_warnings:
        warning_pills = "".join(
            f'<span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs '
            f'font-medium bg-amber-100 text-amber-800">{w}</span>'
            for w in ai_warnings
        )
        oob_html = (
            f'<div id="ai-import-warnings" hx-swap-oob="innerHTML">'
            f'<div class="flex flex-wrap gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg">'
            f'{warning_pills}</div></div>'
        )
        html.body += oob_html.encode()

    return html


@router.get("/{policy_uid}/tab/details", response_class=HTMLResponse)
def policy_tab_details(request: Request, policy_uid: str, conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy_dict, client_info = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    from policydb.queries import REVIEW_CYCLE_LABELS as _RCL

    # Tower structure
    _tower_layers = []
    if policy_dict.get("tower_group"):
        _tg_rows = conn.execute(
            """SELECT policy_uid, policy_type, carrier, limit_amount, layer_position,
                      attachment_point, participation_of
               FROM policies
               WHERE client_id = ? AND LOWER(TRIM(tower_group)) = LOWER(TRIM(?)) AND archived = 0""",
            (policy_dict["client_id"], policy_dict["tower_group"]),
        ).fetchall()

        def _layer_sort_key(r):
            att = r["attachment_point"]
            if att is not None:
                return (float(att), 0)
            lp = r["layer_position"] or "Primary"
            try:
                return (-1, int(lp))
            except (ValueError, TypeError):
                return (-1, 0)

        _tg_rows = sorted(_tg_rows, key=_layer_sort_key)
        running = 0.0
        for tr in _tg_rows:
            lim = float(tr["limit_amount"] or 0)
            att = tr["attachment_point"]
            part = tr["participation_of"]
            if att is not None and float(att) >= 0:
                layer_size = float(part) if part else lim
                ground_up = float(att) + layer_size
            else:
                running += lim
                ground_up = running
            _tower_layers.append(dict(tr) | {"ground_up": ground_up, "is_current": tr["policy_uid"] == uid})

    return templates.TemplateResponse("policies/_tab_details.html", {
        "request": request,
        "policy": policy_dict,
        "client": client_info,
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
        "opportunity_statuses": cfg.get("opportunity_statuses"),
        "tower_layers": _tower_layers,
        "cycle_labels": _RCL,
        "program_linked_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium, effective_date, expiration_date
               FROM policies WHERE program_id = ? AND archived = 0 ORDER BY policy_type""",
            (policy_dict["id"],),
        ).fetchall()] if policy_dict.get("is_program") else [],
        "linkable_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium
               FROM policies WHERE client_id = ? AND archived = 0
                 AND (is_program = 0 OR is_program IS NULL)
                 AND (is_opportunity = 0 OR is_opportunity IS NULL)
                 AND (program_id IS NULL OR program_id = ?)
               ORDER BY policy_type""",
            (policy_dict["client_id"], policy_dict["id"]),
        ).fetchall()] if policy_dict.get("is_program") else [],
        "program_carrier_rows": [dict(r) for r in conn.execute(
            "SELECT * FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (policy_dict["id"],),
        ).fetchall()] if policy_dict.get("is_program") else [],
    })


@router.get("/{policy_uid}/tab/activity", response_class=HTMLResponse)
def policy_tab_activity(request: Request, policy_uid: str, conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy_dict, _ = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    activities = [dict(r) for r in conn.execute(
        """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.policy_id = ? AND a.activity_date >= date('now', '-90 days')
           ORDER BY
             CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN 0 ELSE 1 END,
             CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN a.follow_up_date END ASC,
             a.activity_date DESC, a.id DESC""",
        (policy_dict["id"],),
    ).fetchall()]

    all_contact_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM contacts WHERE name IS NOT NULL AND name != '' ORDER BY name"
    ).fetchall()]

    linked_meetings = [dict(r) for r in conn.execute(
        """SELECT cm.id, cm.title, cm.meeting_date, cm.meeting_uid
           FROM meeting_policies mp
           JOIN client_meetings cm ON cm.id = mp.meeting_id
           WHERE mp.policy_uid = ?
           ORDER BY cm.meeting_date DESC""",
        (uid,),
    ).fetchall()]

    linked_decisions = [dict(r) for r in conn.execute(
        """SELECT md.description, md.confirmed, cm.title as meeting_title, cm.id as meeting_id
           FROM meeting_decisions md
           JOIN client_meetings cm ON cm.id = md.meeting_id
           WHERE md.policy_uid = ?
           ORDER BY md.created_at DESC""",
        (uid,),
    ).fetchall()]

    return templates.TemplateResponse("policies/_tab_activity.html", {
        "request": request,
        "policy": policy_dict,
        "activities": activities,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "all_contact_names": all_contact_names,
        "policy_total_hours": get_policy_total_hours(conn, policy_dict["id"]),
        "dispositions": cfg.get("follow_up_dispositions", []),
        "linked_meetings": linked_meetings,
        "linked_decisions": linked_decisions,
    })


@router.get("/{policy_uid}/tab/contacts", response_class=HTMLResponse)
def policy_tab_contacts(request: Request, policy_uid: str, conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy_dict, client_info = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    from policydb.queries import get_client_contacts as _gcc
    contacts = _gcc(conn, policy_dict["client_id"], contact_type="client")
    team_contacts = _gcc(conn, policy_dict["client_id"], contact_type="internal")
    policy_contacts = get_policy_contacts(conn, policy_dict["id"])

    # Expertise tags
    _pc_ids = [c["contact_id"] for c in policy_contacts if c.get("contact_id")]
    if _pc_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in policy_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])

    all_contact_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM contacts WHERE name IS NOT NULL AND name != '' ORDER BY name"
    ).fetchall()]

    import json as _json_ct
    _ac_rows = conn.execute(
        """SELECT co.name, co.email, co.phone, co.mobile, co.organization,
                  MAX(COALESCE(cpa.role, cca.role)) AS role,
                  MAX(COALESCE(cpa.title, cca.title)) AS title
           FROM contacts co
           LEFT JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           LEFT JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.id ORDER BY co.name"""
    ).fetchall()
    all_contacts_for_ac_json = _json_ct.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "mobile": r["mobile"] or "", "title": r["title"] or "", "organization": r["organization"] or ""} for r in _ac_rows})

    team_cc_json = _json_ct.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])

    from policydb.email_templates import policy_context as _pctx, render_tokens as _rtk
    mailto_subject = _rtk(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _pctx(conn, uid))

    # Auto-clustered activity timeline (replaces correspondence threads)
    _cluster_days = cfg.get("activity_cluster_days", 7)
    _all_acts = [dict(r) for r in conn.execute(
        """SELECT activity_date, activity_type, subject, disposition, details,
                  duration_hours, follow_up_done
           FROM activity_log WHERE policy_id = ?
           ORDER BY activity_date DESC, id DESC""",
        (policy_dict["id"],),
    ).fetchall()] if policy_dict.get("id") else []

    _activity_clusters: list[dict] = []
    _cur_cluster: list[dict] = []
    _prev_date_str: str = ""
    for _act in _all_acts:
        _act_date = _act.get("activity_date") or ""
        if _prev_date_str and _act_date:
            try:
                gap = (date.fromisoformat(_prev_date_str) - date.fromisoformat(_act_date)).days
            except (ValueError, TypeError):
                gap = 0
            if gap > _cluster_days and _cur_cluster:
                _activity_clusters.append(_build_cluster(_cur_cluster))
                _cur_cluster = []
        _cur_cluster.append(_act)
        if _act_date:
            _prev_date_str = _act_date
    if _cur_cluster:
        _activity_clusters.append(_build_cluster(_cur_cluster))

    suggested_contact_ids: set[int] = set()
    if policy_dict.get("policy_type"):
        suggested_contact_ids = {r["contact_id"] for r in conn.execute(
            "SELECT DISTINCT contact_id FROM contact_expertise WHERE category = 'line' AND tag = ?",
            (policy_dict["policy_type"],),
        ).fetchall()}

    return templates.TemplateResponse("policies/_tab_contacts.html", {
        "request": request,
        "policy": policy_dict,
        "client": client_info,
        "contacts": contacts,
        "team_contacts": team_contacts,
        "policy_contacts": policy_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "suggested_contact_ids": suggested_contact_ids,
        "team_cc_json": team_cc_json,
        "mailto_subject": mailto_subject,
        "activity_clusters": _activity_clusters,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute("SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''").fetchall()}),
    })


@router.get("/{policy_uid}/tab/workflow", response_class=HTMLResponse)
def policy_tab_workflow(request: Request, policy_uid: str, conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy_dict, _ = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    # Program context for child policies
    program_policy = None
    program_health = ""
    if policy_dict.get("program_id"):
        _pp = conn.execute(
            "SELECT policy_uid, policy_type FROM policies WHERE id = ?",
            (policy_dict["program_id"],),
        ).fetchone()
        if _pp:
            program_policy = dict(_pp)
            _ph = conn.execute(
                """SELECT health FROM policy_timeline
                   WHERE policy_uid = ? AND completed_date IS NULL
                   ORDER BY CASE health
                     WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                     WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END
                   LIMIT 1""",
                (program_policy["policy_uid"],),
            ).fetchone()
            program_health = _ph["health"] if _ph else ""

    return templates.TemplateResponse("policies/_tab_workflow.html", {
        "request": request,
        "policy": policy_dict,
        "checklist": _build_checklist(conn, uid),
        "request_categories": cfg.get("request_categories", []),
        "program_policy": program_policy,
        "program_health": program_health,
    })


@router.get("/{policy_uid}/timeline", response_class=HTMLResponse)
def policy_timeline_view(request: Request, policy_uid: str, conn=Depends(get_db)):
    """HTMX partial: vertical timeline visualization for a policy."""
    uid = policy_uid.upper()
    policy_dict, _ = _policy_base(conn, uid)
    if not policy_dict:
        return HTMLResponse("Not found", status_code=404)

    from policydb.timeline_engine import get_policy_timeline
    timeline = get_policy_timeline(conn, uid)

    return templates.TemplateResponse("policies/_timeline.html", {
        "request": request,
        "policy": policy_dict,
        "timeline": timeline,
    })


@router.get("/{policy_uid}/edit", response_class=HTMLResponse)
def policy_edit_form(request: Request, policy_uid: str, add_contact: str = "", conn=Depends(get_db)):
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    policy_dict = dict(policy)
    _client_row = conn.execute("SELECT id, name, cn_number FROM clients WHERE id = ?", (policy_dict["client_id"],)).fetchone()
    _client_info = dict(_client_row) if _client_row else {"id": policy_dict["client_id"], "name": "", "cn_number": ""}
    from policydb.queries import get_client_contacts as _get_client_contacts
    contacts = _get_client_contacts(conn, policy_dict["client_id"], contact_type="client")
    team_contacts = _get_client_contacts(conn, policy_dict["client_id"], contact_type="internal")
    policy_contacts = get_policy_contacts(conn, policy_dict["id"])
    # Attach expertise tags to policy team contacts
    _pc_contact_ids = [c["contact_id"] for c in policy_contacts if c.get("contact_id")]
    if _pc_contact_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_contact_ids))})",
            _pc_contact_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in policy_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])
    # All known contacts for autocomplete (name + email + role for auto-fill)
    all_contact_names = [r[0] for r in conn.execute(
        """SELECT DISTINCT co.name FROM contacts co
           WHERE co.name IS NOT NULL AND co.name != ''
           ORDER BY co.name"""
    ).fetchall()]
    import json as _json
    _ac_rows = conn.execute(
        """SELECT co.name,
                  co.email,
                  co.phone,
                  co.mobile,
                  co.organization,
                  MAX(COALESCE(cpa.role, cca.role)) AS role,
                  MAX(COALESCE(cpa.title, cca.title)) AS title
           FROM contacts co
           LEFT JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           LEFT JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.id
           ORDER BY co.name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "mobile": r["mobile"] or "", "title": r["title"] or "", "organization": r["organization"] or ""} for r in _ac_rows})
    activities = [dict(r) for r in conn.execute(
        """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.policy_id = ? AND a.activity_date >= date('now', '-90 days')
           ORDER BY
             CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN 0 ELSE 1 END,
             CASE WHEN a.follow_up_date IS NOT NULL AND (a.follow_up_done IS NULL OR a.follow_up_done = 0) THEN a.follow_up_date END ASC,
             a.activity_date DESC, a.id DESC""",
        (policy_dict["id"],),
    ).fetchall()]
    # Build CC options for email popover (opt-in, shown as checkboxes)
    import json as _json_edit
    team_cc_json = _json_edit.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])
    # Pre-render mailto subject from config template
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, uid)
    mailto_subject = _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)
    policy_total_hours = get_policy_total_hours(conn, policy_dict["id"])
    scratch_row = conn.execute(
        "SELECT content, updated_at FROM policy_scratchpad WHERE policy_uid=?", (uid,)
    ).fetchone()

    # ── Policy alerts / readiness context ──
    from policydb.queries import get_escalation_alerts as _get_esc
    _excluded = cfg.get("renewal_statuses_excluded", [])
    _all_alerts = _get_esc(conn, excluded_statuses=_excluded)
    _policy_alert = next((a for a in _all_alerts if a.get("policy_uid") == uid), None)

    _escalation_tier = None
    _escalation_reason = None
    if _policy_alert:
        _escalation_tier = _policy_alert.get("escalation_tier")
        # Build human-readable reason from tier + data
        _t = _escalation_tier
        _days = _policy_alert.get("days_to_renewal")
        _status = _policy_alert.get("renewal_status", "Not Started")
        if _t == "CRITICAL":
            _escalation_reason = f"Expires in {_days}d — status \"{_status}\" with no recent activity"
        elif _t == "WARNING":
            _escalation_reason = f"Expires in {_days}d — still \"{_status}\""
        elif _t == "NUDGE":
            _escalation_reason = f"Expires in {_days}d — no follow-up scheduled"

    # Readiness score
    _readiness_score = None
    _readiness_label = None
    _readiness_tooltip = None
    if policy_dict.get("days_to_renewal") is not None:
        _tmp = [policy_dict]
        _attach_milestone_progress(conn, _tmp)
        _attach_readiness_score(conn, _tmp)
        _readiness_score = policy_dict.get("readiness_score")
        _readiness_label = policy_dict.get("readiness_label")
        _readiness_tooltip = policy_dict.get("readiness_tooltip")

    # Overdue follow-ups for this policy
    _overdue_followups = [dict(r) for r in conn.execute(
        """SELECT subject, follow_up_date,
               CAST(julianday('now') - julianday(follow_up_date) AS INTEGER) AS days_overdue
           FROM activity_log
           WHERE policy_id=? AND follow_up_done=0 AND follow_up_date < date('now')
           ORDER BY follow_up_date""",
        (policy_dict["id"],),
    ).fetchall()]

    # Missing items warnings
    _policy_warnings = []
    if not any(c.get("is_placement_colleague") for c in (policy_contacts or [])):
        _policy_warnings.append("No placement colleague assigned")
    checklist = _build_checklist(conn, uid)
    if checklist and not any(c["completed"] for c in checklist):
        _policy_warnings.append("Checklist 0% — no items started")
    if not policy_dict.get("follow_up_date"):
        _policy_warnings.append("No follow-up date scheduled")

    # Tower structure: sibling policies in same tower group for ground-up calc
    _tower_layers = []
    if policy_dict.get("tower_group"):
        _tg_rows = conn.execute(
            """SELECT policy_uid, policy_type, carrier, limit_amount, layer_position,
                      attachment_point, participation_of
               FROM policies
               WHERE client_id = ? AND LOWER(TRIM(tower_group)) = LOWER(TRIM(?)) AND archived = 0""",
            (policy_dict["client_id"], policy_dict["tower_group"]),
        ).fetchall()

        def _layer_sort_key(r):
            # Primary sort: attachment_point (NULL/0 = primary first)
            att = r["attachment_point"]
            if att is not None:
                return (float(att), 0)
            # Fallback: normalise layer_position ("Primary" → 0, numeric strings as-is)
            lp = r["layer_position"] or "Primary"
            try:
                return (-1, int(lp))
            except (ValueError, TypeError):
                return (-1, 0)  # "Primary" or unknown → bottom of tower

        _tg_rows = sorted(_tg_rows, key=_layer_sort_key)
        running = 0.0
        for tr in _tg_rows:
            lim = float(tr["limit_amount"] or 0)
            att = tr["attachment_point"]
            part = tr["participation_of"]
            if att is not None and float(att) >= 0:
                layer_size = float(part) if part else lim
                ground_up = float(att) + layer_size
            else:
                running += lim
                ground_up = running
            _tower_layers.append(dict(tr) | {"ground_up": ground_up, "is_current": tr["policy_uid"] == uid})

    # Correspondence threads for this policy
    _correspondence_threads = [dict(r) for r in conn.execute("""
        SELECT thread_id,
               MIN(subject) AS thread_subject,
               COUNT(*) AS attempt_count,
               COALESCE(SUM(duration_hours), 0) AS total_hours,
               MAX(CASE WHEN follow_up_done = 0 THEN 1 ELSE 0 END) AS has_pending
        FROM activity_log
        WHERE policy_id = ? AND thread_id IS NOT NULL
        GROUP BY thread_id
        ORDER BY MAX(activity_date) DESC
    """, (policy_dict["id"],)).fetchall()] if policy_dict.get("id") else []
    for _ct in _correspondence_threads:
        _ct["activities"] = [dict(r) for r in conn.execute("""
            SELECT activity_date, disposition, details, duration_hours, follow_up_done
            FROM activity_log WHERE thread_id = ?
            ORDER BY activity_date DESC, id DESC
        """, (_ct["thread_id"],)).fetchall()]

    # Expertise-based contact suggestions for policy team assignment
    suggested_contact_ids: set[int] = set()
    if policy_dict.get("policy_type"):
        _suggested = conn.execute(
            "SELECT DISTINCT contact_id FROM contact_expertise WHERE category = 'line' AND tag = ?",
            (policy_dict["policy_type"],),
        ).fetchall()
        suggested_contact_ids = {r["contact_id"] for r in _suggested}

    # Program context for child policies (used by workflow tab timeline banner)
    _program_policy = None
    _program_health = ""
    if policy_dict.get("program_id"):
        _pp = conn.execute(
            "SELECT policy_uid, policy_type FROM policies WHERE id = ?",
            (policy_dict["program_id"],),
        ).fetchone()
        if _pp:
            _program_policy = dict(_pp)
            _ph = conn.execute(
                """SELECT health FROM policy_timeline
                   WHERE policy_uid = ? AND completed_date IS NULL
                   ORDER BY CASE health
                     WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                     WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4 ELSE 5 END
                   LIMIT 1""",
                (_program_policy["policy_uid"],),
            ).fetchone()
            _program_health = _ph["health"] if _ph else ""

    from policydb.queries import REVIEW_CYCLE_LABELS as _REVIEW_CYCLE_LABELS
    return templates.TemplateResponse("policies/edit.html", {
        "request": request,
        "active": "",
        "policy": policy_dict,
        "client": _client_info,
        "policy_total_hours": policy_total_hours,
        "escalation_tier": _escalation_tier,
        "escalation_reason": _escalation_reason,
        "readiness_score": _readiness_score,
        "readiness_label": _readiness_label,
        "readiness_tooltip": _readiness_tooltip,
        "overdue_followups": _overdue_followups,
        "policy_warnings": _policy_warnings,
        "policy_scratchpad": scratch_row["content"] if scratch_row else "",
        "policy_scratchpad_updated": scratch_row["updated_at"] if scratch_row else "",
        "policy_saved_notes": get_saved_notes(conn, "policy", uid),
        "policy_types": cfg.get("policy_types"),
        "coverage_forms": cfg.get("coverage_forms"),
        "renewal_statuses": _renewal_statuses(),
        "us_states": US_STATES,
        "checklist": checklist,
        "contacts": contacts,
        "team_contacts": team_contacts,
        "policy_contacts": policy_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "suggested_contact_ids": suggested_contact_ids,
        "team_cc_json": team_cc_json,
        "mailto_subject": mailto_subject,
        "activities": activities,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "dispositions": cfg.get("follow_up_dispositions", []),
        "opportunity_statuses": cfg.get("opportunity_statuses"),
        "add_contact": add_contact,
        "cycle_labels": _REVIEW_CYCLE_LABELS,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute("SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''").fetchall()}),
        "correspondence_threads": _correspondence_threads,
        "tower_layers": _tower_layers,
        "request_categories": cfg.get("request_categories", []),
        "program_linked_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium, effective_date, expiration_date
               FROM policies WHERE program_id = ? AND archived = 0 ORDER BY policy_type""",
            (policy_dict["id"],),
        ).fetchall()] if policy_dict.get("is_program") else [],
        "linkable_policies": [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium
               FROM policies WHERE client_id = ? AND archived = 0
                 AND (is_program = 0 OR is_program IS NULL)
                 AND (is_opportunity = 0 OR is_opportunity IS NULL)
                 AND (program_id IS NULL OR program_id = ?)
               ORDER BY policy_type""",
            (policy_dict["client_id"], policy_dict["id"]),
        ).fetchall()] if policy_dict.get("is_program") else [],
        "program_carrier_rows": [dict(r) for r in conn.execute(
            "SELECT * FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (policy_dict["id"],),
        ).fetchall()] if policy_dict.get("is_program") else [],
        "program_policy": _program_policy,
        "program_health": _program_health,
    })


@router.post("/{policy_uid}/program-link")
def program_link_policy(
    request: Request,
    policy_uid: str,
    link_uid: str = Form(""),
    unlink_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Link or unlink a policy to/from a program."""
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)
    if link_uid:
        conn.execute(
            "UPDATE policies SET program_id = ? WHERE policy_uid = ? AND (program_id IS NULL OR program_id = ?)",
            (program["id"], link_uid.upper(), program["id"]),
        )
    if unlink_uid:
        conn.execute(
            "UPDATE policies SET program_id = NULL WHERE policy_uid = ? AND program_id = ?",
            (unlink_uid.upper(), program["id"]),
        )
    conn.commit()
    # Return updated linked policies list as HTML partial
    linked = conn.execute(
        """SELECT policy_uid, policy_type, carrier, premium, effective_date, expiration_date
           FROM policies WHERE program_id = ? AND archived = 0 ORDER BY policy_type""",
        (program["id"],),
    ).fetchall()
    rows_html = ""
    for p in linked:
        p = dict(p)
        prem = f"${p['premium']:,.0f}" if p.get('premium') else "—"
        rows_html += f'''<tr class="border-b border-gray-50">
          <td class="px-3 py-1.5 text-xs"><a href="/policies/{p['policy_uid']}/edit" class="text-marsh hover:underline" target="_blank">{p['policy_uid']}</a></td>
          <td class="px-3 py-1.5 text-xs text-gray-700">{p['policy_type']}</td>
          <td class="px-3 py-1.5 text-xs text-gray-500">{p.get('carrier') or '—'}</td>
          <td class="px-3 py-1.5 text-xs text-right">{prem}</td>
          <td class="px-3 py-1.5 text-xs">
            <button type="button" hx-post="/policies/{policy_uid.upper()}/program-link"
              hx-vals='{{"unlink_uid": "{p['policy_uid']}"}}'
              hx-target="#program-linked-list"
              hx-swap="innerHTML"
              class="text-red-400 hover:text-red-600 text-xs">Unlink</button>
          </td>
        </tr>'''
    if not rows_html:
        rows_html = '<tr><td colspan="5" class="px-3 py-3 text-xs text-gray-400 text-center italic">No policies linked yet</td></tr>'
    return HTMLResponse(rows_html)


def _format_money(value) -> str:
    """Format a numeric value as $N,NNN (no decimals)."""
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return ""


def _parse_money(raw: str):
    """Strip $ and , then convert to float. Returns None on failure."""
    try:
        return float(raw.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _update_program_totals(conn, program_id: int) -> dict:
    """Recompute and save the parent policy's premium + limit_amount from carrier rows."""
    rows = conn.execute(
        "SELECT premium, limit_amount FROM program_carriers WHERE program_id = ?",
        (program_id,),
    ).fetchall()
    total_premium = sum(r["premium"] for r in rows if r["premium"] is not None)
    total_limit = sum(r["limit_amount"] for r in rows if r["limit_amount"] is not None)
    conn.execute(
        "UPDATE policies SET premium = ?, limit_amount = ? WHERE id = ?",
        (total_premium or None, total_limit or None, program_id),
    )
    return {
        "premium": _format_money(total_premium) if total_premium else "",
        "limit_amount": _format_money(total_limit) if total_limit else "",
    }


@router.patch("/{policy_uid}/program-carrier/{carrier_id}")
async def program_carrier_patch(
    request: Request,
    policy_uid: str,
    carrier_id: int,
    conn=Depends(get_db),
):
    """Update a single cell of a program_carriers row."""
    import json as _json
    body = await request.body()
    try:
        data = _json.loads(body)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    field = data.get("field", "")
    value = data.get("value", "")

    allowed_fields = {"carrier", "policy_number", "premium", "limit_amount"}
    if field not in allowed_fields:
        return JSONResponse({"ok": False, "error": "Unknown field"}, status_code=400)

    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    row = conn.execute(
        "SELECT id FROM program_carriers WHERE id = ? AND program_id = ?",
        (carrier_id, program["id"]),
    ).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "Carrier row not found"}, status_code=404)

    formatted = value
    if field in ("premium", "limit_amount"):
        parsed = _parse_money(str(value))
        conn.execute(
            f"UPDATE program_carriers SET {field} = ? WHERE id = ?",
            (parsed, carrier_id),
        )
        formatted = _format_money(parsed) if parsed is not None else ""
    else:
        conn.execute(
            f"UPDATE program_carriers SET {field} = ? WHERE id = ?",
            (value or None, carrier_id),
        )

    totals = _update_program_totals(conn, program["id"])
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted, "totals": totals})


@router.post("/{policy_uid}/program-carrier", response_class=HTMLResponse)
def program_carrier_add(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """Add a blank carrier row to a program and return its <tr> HTML."""
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, policy_number, premium, limit_amount, sort_order) VALUES (?, NULL, NULL, NULL, NULL, ?)",
        (program["id"], max_order + 1),
    )
    conn.commit()
    new_id = cur.lastrowid
    uid = policy_uid.upper()
    endpoint_base = f"/policies/{uid}/program-carrier/{new_id}"

    html = f'''<tr class="border-b border-gray-100 carrier-row" data-id="{new_id}" draggable="true">
  <td class="px-2 py-1 text-gray-300 cursor-grab carrier-drag-handle" title="Drag to reorder">&#x2807;</td>
  <td class="px-2 py-1 text-xs text-gray-700 carrier-cell"
      contenteditable="true"
      data-field="carrier"
      data-id="{new_id}"
      data-placeholder="Carrier"
      data-endpoint="{endpoint_base}"></td>
  <td class="px-2 py-1 text-xs text-gray-500 carrier-cell"
      contenteditable="true"
      data-field="policy_number"
      data-id="{new_id}"
      data-placeholder="Policy #"
      data-endpoint="{endpoint_base}"></td>
  <td class="px-2 py-1 text-xs text-right tabular-nums carrier-cell"
      contenteditable="true"
      data-field="premium"
      data-id="{new_id}"
      data-placeholder="$0"
      data-endpoint="{endpoint_base}"></td>
  <td class="px-2 py-1 text-xs text-right tabular-nums carrier-cell"
      contenteditable="true"
      data-field="limit_amount"
      data-id="{new_id}"
      data-placeholder="$0"
      data-endpoint="{endpoint_base}"></td>
  <td class="px-2 py-1 text-center">
    <button type="button"
            class="text-red-300 hover:text-red-600 text-xs carrier-delete no-print"
            data-id="{new_id}"
            data-endpoint="/policies/{uid}/program-carrier/{new_id}"
            title="Remove row">&#x2715;</button>
  </td>
</tr>'''
    return HTMLResponse(html)


@router.delete("/{policy_uid}/program-carrier/{carrier_id}")
def program_carrier_delete(
    request: Request,
    policy_uid: str,
    carrier_id: int,
    conn=Depends(get_db),
):
    """Delete a carrier row and update the parent policy totals."""
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    conn.execute(
        "DELETE FROM program_carriers WHERE id = ? AND program_id = ?",
        (carrier_id, program["id"]),
    )
    totals = _update_program_totals(conn, program["id"])
    conn.commit()
    return JSONResponse({"ok": True, "totals": totals})


@router.post("/{policy_uid}/program-carrier/reorder")
async def program_carrier_reorder(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """Reorder carrier rows by accepting a list of IDs in desired order."""
    import json as _json
    body = await request.body()
    try:
        data = _json.loads(body)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    order = data.get("order", [])
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    for idx, row_id in enumerate(order):
        conn.execute(
            "UPDATE program_carriers SET sort_order = ? WHERE id = ? AND program_id = ?",
            (idx + 1, row_id, program["id"]),
        )
    conn.commit()
    return JSONResponse({"ok": True})


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
    is_bor: str = Form("0"),
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
    is_program: str = Form("0"),
    conn=Depends(get_db),
):
    def _float(v: str):
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    from policydb.utils import parse_currency_with_magnitude as _parse_money

    uid = policy_uid.upper()
    # Parse currency shorthand (e.g., "$5M" → 5000000) for money fields
    premium = str(_parse_money(premium) or 0) if premium else premium
    limit_amount = str(_parse_money(limit_amount) or '') if limit_amount else limit_amount
    deductible = str(_parse_money(deductible) or '') if deductible else deductible

    old_row = dict(conn.execute("SELECT * FROM policies WHERE policy_uid=?", (uid,)).fetchone())
    opp = 1 if is_opportunity == "1" else 0
    pgm = 1 if is_program == "1" else 0
    policy_type = normalize_coverage_type(policy_type)
    carrier = normalize_carrier(carrier) if carrier else ""
    policy_number = normalize_policy_number(policy_number) if policy_number else ""
    exposure_address = exposure_address.strip() if exposure_address else ""
    exposure_city = format_city(exposure_city) if exposure_city else ""
    exposure_state = format_state(exposure_state) if exposure_state else ""
    exposure_zip = format_zip(exposure_zip) if exposure_zip else ""
    conn.execute(
        """UPDATE policies SET
           policy_type=?, carrier=?, policy_number=?,
           effective_date=?, expiration_date=?, premium=?,
           limit_amount=?, deductible=?, description=?,
           coverage_form=?, layer_position=?, tower_group=?,
           is_standalone=?, is_bor=?, is_opportunity=?, opportunity_status=?, target_effective_date=?,
           renewal_status=?, commission_rate=?, prior_premium=?, notes=?,
           project_name=?, exposure_basis=?, exposure_amount=?, exposure_unit=?,
           exposure_address=?, exposure_city=?, exposure_state=?, exposure_zip=?,
           follow_up_date=?, attachment_point=?, participation_of=?,
           first_named_insured=?, access_point=?,
           is_program=?
           WHERE policy_uid=?""",
        (
            policy_type, carrier or None, policy_number or None,
            effective_date or None, expiration_date or None, _float(premium) or 0,
            _float(limit_amount), _float(deductible), description or None,
            coverage_form or None, layer_position or "Primary", tower_group or None,
            1 if is_standalone == "1" else 0,
            1 if is_bor == "1" else 0,
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
            pgm,
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

    policy = get_policy_by_uid(conn, uid)
    _client_id = policy["client_id"] if policy else 0
    _policy_id = policy["id"] if policy else 0
    if _policy_id:
        _sync_project_id(conn, _policy_id, _client_id, project_name or None)
        conn.commit()

    if action == "autosave":
        return JSONResponse({"ok": True})
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

    # Generate timeline for converted policy if profile is set
    _regen = conn.execute(
        "SELECT milestone_profile FROM policies WHERE policy_uid = ?", (uid,)
    ).fetchone()
    if _regen and _regen["milestone_profile"]:
        from policydb.timeline_engine import generate_policy_timelines
        generate_policy_timelines(conn, policy_uid=uid)

    return RedirectResponse(f"/policies/{uid}/edit", status_code=303)


def _policy_team_response(request, conn, policy_uid: str):
    """Return rendered _policy_team.html partial for a given policy."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    p = dict(policy)
    from policydb.queries import get_client_contacts as _get_client_contacts
    policy_contacts_list = get_policy_contacts(conn, p["id"])
    # Attach expertise tags to policy team contacts
    _pc_ids = [c["contact_id"] for c in policy_contacts_list if c.get("contact_id")]
    if _pc_ids:
        _exp_rows2 = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map2: dict = {}
        for _er2 in _exp_rows2:
            _exp_map2.setdefault(_er2["contact_id"], {"line": [], "industry": []})
            _exp_map2[_er2["contact_id"]][_er2["category"]].append(_er2["tag"])
        for _pc2 in policy_contacts_list:
            _cid2 = _pc2.get("contact_id")
            _pc2["expertise_lines"] = _exp_map2.get(_cid2, {}).get("line", [])
            _pc2["expertise_industries"] = _exp_map2.get(_cid2, {}).get("industry", [])
    team_contacts = _get_client_contacts(conn, p["client_id"], contact_type="internal")
    all_contact_names = [r[0] for r in conn.execute(
        """SELECT DISTINCT co.name FROM contacts co
           WHERE co.name IS NOT NULL AND co.name != ''
           ORDER BY co.name"""
    ).fetchall()]
    import json as _json
    _ac_rows2 = conn.execute(
        """SELECT co.name,
                  co.email,
                  co.phone,
                  co.mobile,
                  co.organization,
                  MAX(COALESCE(cpa.role, cca.role)) AS role,
                  MAX(COALESCE(cpa.title, cca.title)) AS title
           FROM contacts co
           LEFT JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           LEFT JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE co.name IS NOT NULL AND co.name != ''
           GROUP BY co.id
           ORDER BY co.name"""
    ).fetchall()
    all_contacts_for_ac_json = _json.dumps({r["name"]: {"email": r["email"] or "", "role": r["role"] or "", "phone": r["phone"] or "", "mobile": r["mobile"] or "", "title": r["title"] or "", "organization": r["organization"] or ""} for r in _ac_rows2})
    import json as _json_team
    team_cc_json = _json_team.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _mail_ctx = _policy_ctx(conn, policy_uid)
    mailto_subject = _render_tokens(cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"), _mail_ctx)
    # Compute which team contacts are already assigned to this policy (by contact_id match)
    assigned_contact_ids = {c["contact_id"] for c in policy_contacts_list if c.get("contact_id")}
    already_assigned_ids = {t["id"] for t in team_contacts if t.get("contact_id") in assigned_contact_ids}
    return templates.TemplateResponse("policies/_policy_team.html", {
        "request": request,
        "policy": p,
        "policy_contacts": policy_contacts_list,
        "team_contacts": team_contacts,
        "all_contact_names": all_contact_names,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "team_cc_json": team_cc_json,
        "mailto_subject": mailto_subject,
        "already_assigned_ids": already_assigned_ids,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute("SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''").fetchall()}),
    })


@router.patch("/{policy_uid}/cell")
async def policy_cell_save(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Save a single field on a policy (contenteditable / combobox cell save).

    Handles all policy fields with type-specific parsing:
    - Currency fields: parse_currency_with_magnitude()
    - Date fields: stripped ISO string
    - Boolean fields: toggle true/false
    - Combobox fields: normalize or validate
    - Text fields: strip + save
    """
    from policydb.utils import (
        clean_email, format_city, format_phone, format_state, format_zip,
        parse_currency_with_magnitude,
    )

    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    # -- Field allowlists by type --
    currency_fields = {
        "premium", "limit_amount", "deductible", "attachment_point",
        "participation_of", "prior_premium", "exposure_amount",
    }
    date_fields = {
        "effective_date", "expiration_date", "follow_up_date", "target_effective_date",
    }
    bool_fields = {"is_bor", "is_standalone"}
    text_fields = {
        "policy_number", "first_named_insured", "access_point", "description",
        "notes", "placement_notation", "exposure_address", "exposure_basis",
        "exposure_unit", "tower_group", "exposure_zip",
    }
    combobox_fields = {
        "policy_type", "carrier", "renewal_status", "opportunity_status",
        "coverage_form", "layer_position", "exposure_state", "exposure_city",
        "review_cycle",
    }
    special_fields = {"project_name", "commission_rate"}

    allowed = currency_fields | date_fields | bool_fields | text_fields | combobox_fields | special_fields
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Invalid field: {field}"}, status_code=400)

    uid = policy_uid.upper()
    policy = conn.execute(
        "SELECT id, client_id FROM policies WHERE policy_uid = ?", (uid,)
    ).fetchone()
    if not policy:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    formatted = value

    # -- Currency fields --
    if field in currency_fields:
        num = parse_currency_with_magnitude(value)
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (num, uid))  # noqa: S608
        formatted = f"${num:,.0f}" if num else ""

    # -- Date fields --
    elif field in date_fields:
        val = value.strip() or None
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (val, uid))  # noqa: S608
        formatted = val or ""

    # -- Boolean toggle fields --
    elif field in bool_fields:
        bval = 1 if str(value).lower() in ("1", "true", "yes", "on") else 0
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (bval, uid))  # noqa: S608
        formatted = str(bval)

    # -- Combobox / validated fields --
    elif field == "policy_type":
        formatted = normalize_coverage_type(value)
        conn.execute("UPDATE policies SET policy_type = ? WHERE policy_uid = ?", (formatted, uid))
    elif field == "carrier":
        formatted = normalize_carrier(value)
        conn.execute("UPDATE policies SET carrier = ? WHERE policy_uid = ?", (formatted or None, uid))
    elif field == "renewal_status":
        val = value.strip()
        conn.execute("UPDATE policies SET renewal_status = ? WHERE policy_uid = ?", (val or None, uid))
        formatted = val
    elif field == "opportunity_status":
        val = value.strip()
        conn.execute("UPDATE policies SET opportunity_status = ? WHERE policy_uid = ?", (val or None, uid))
        formatted = val
    elif field == "coverage_form":
        val = value.strip()
        conn.execute("UPDATE policies SET coverage_form = ? WHERE policy_uid = ?", (val or None, uid))
        formatted = val
    elif field == "layer_position":
        val = value.strip()
        conn.execute("UPDATE policies SET layer_position = ? WHERE policy_uid = ?", (val or None, uid))
        formatted = val
    elif field == "exposure_state":
        formatted = format_state(value)
        conn.execute("UPDATE policies SET exposure_state = ? WHERE policy_uid = ?", (formatted or None, uid))
    elif field == "exposure_city":
        formatted = format_city(value)
        conn.execute("UPDATE policies SET exposure_city = ? WHERE policy_uid = ?", (formatted or None, uid))
    elif field == "review_cycle":
        val = value.strip()
        conn.execute("UPDATE policies SET review_cycle = ? WHERE policy_uid = ?", (val or None, uid))
        formatted = val

    # -- Text fields --
    elif field == "policy_number":
        formatted = normalize_policy_number(value)
        conn.execute("UPDATE policies SET policy_number = ? WHERE policy_uid = ?", (formatted, uid))
    elif field == "exposure_zip":
        formatted = format_zip(value)
        conn.execute("UPDATE policies SET exposure_zip = ? WHERE policy_uid = ?", (formatted or None, uid))
    elif field in text_fields:
        val = value.strip()
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (val or None, uid))  # noqa: S608
        formatted = val

    # -- Special fields --
    elif field == "project_name":
        _sync_project_id(conn, policy["id"], policy["client_id"], value)
        formatted = value.strip()
    elif field == "commission_rate":
        try:
            rate = float(value) if value.strip() else None
        except (ValueError, TypeError):
            rate = None
        conn.execute("UPDATE policies SET commission_rate = ? WHERE policy_uid = ?", (rate, uid))
        formatted = f"{rate:.3f}" if rate is not None else ""

    conn.commit()

    # Regenerate timeline if a date field changed and profile is set
    if field in ("effective_date", "expiration_date"):
        _regen = conn.execute(
            "SELECT milestone_profile FROM policies WHERE policy_uid = ?", (uid,)
        ).fetchone()
        if _regen and _regen["milestone_profile"]:
            from policydb.timeline_engine import generate_policy_timelines
            generate_policy_timelines(conn, policy_uid=uid)

    return JSONResponse({"ok": True, "formatted": formatted})


@router.patch("/{policy_uid}/team/{contact_id}/cell")
async def policy_team_cell(request: Request, policy_uid: str, contact_id: int, conn=Depends(get_db)):
    """Save a single cell value for a policy contact (matrix edit)."""
    from policydb.utils import clean_email, format_phone
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"name", "organization", "title", "role", "email", "phone", "mobile", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    # contact_id in URL = assignment_id for backward compat with templates
    assignment_id = contact_id
    # Shared fields live on the contacts table; per-assignment fields on the junction table
    shared_fields = {"name", "email", "phone", "mobile", "organization"}
    assignment_fields = {"role", "title", "notes"}
    if field in shared_fields:
        # Look up the contact_id from the assignment
        asg = conn.execute(
            "SELECT contact_id FROM contact_policy_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if asg:
            conn.execute(
                f"UPDATE contacts SET {field}=? WHERE id=?",
                (formatted or None, asg["contact_id"]),
            )
    elif field in assignment_fields:
        conn.execute(
            f"UPDATE contact_policy_assignments SET {field}=? WHERE id=?",
            (formatted or None, assignment_id),
        )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/{policy_uid}/team/add-row", response_class=HTMLResponse)
def policy_team_add_row(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Create blank policy contact row and return matrix row HTML."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    cid = get_or_create_contact(conn, "New Contact")
    asg_id = assign_contact_to_policy(conn, cid, policy["id"])
    conn.commit()
    c = {"id": asg_id, "contact_id": cid, "name": "New Contact", "title": None, "role": None,
         "organization": None, "email": None, "phone": None, "mobile": None,
         "notes": None, "is_placement_colleague": 0}
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    return templates.TemplateResponse("policies/_team_matrix_row.html", {
        "request": request, "c": c, "policy": dict(policy),
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


@router.post("/{policy_uid}/team/add", response_class=HTMLResponse)
def policy_team_add(
    request: Request,
    policy_uid: str,
    name: str = Form(...),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    title: str = Form(""),
    organization: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import clean_email, format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    cid = get_or_create_contact(
        conn, name,
        email=clean_email(email) or None,
        phone=format_phone(phone) if phone else None,
        mobile=format_phone(mobile) if mobile else None,
        organization=organization or None,
    )
    assign_contact_to_policy(
        conn, cid, policy["id"],
        role=role or None, title=title or None, notes=notes or None,
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
    # contact_id in URL = assignment_id for backward compat with templates
    remove_contact_from_policy(conn, contact_id)
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
    mobile: str = Form(""),
    title: str = Form(""),
    organization: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import clean_email, format_phone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)

    # contact_id in URL = assignment_id for backward compat with templates
    assignment_id = contact_id
    formatted_phone = format_phone(phone) if phone else None
    formatted_mobile = format_phone(mobile) if mobile else None

    # Look up the contact_id from the assignment
    asg = conn.execute(
        "SELECT contact_id FROM contact_policy_assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if asg:
        # Update shared fields on the contacts table
        conn.execute(
            "UPDATE contacts SET name=?, email=?, phone=?, mobile=?, organization=? WHERE id=?",
            (name, clean_email(email) or None, formatted_phone, formatted_mobile,
             organization or None, asg["contact_id"]),
        )
        # Update per-assignment fields on the junction table
        conn.execute(
            "UPDATE contact_policy_assignments SET role=?, title=?, notes=? WHERE id=?",
            (role or None, title or None, notes or None, assignment_id),
        )

    conn.commit()
    return _policy_team_response(request, conn, uid)


@router.post("/{policy_uid}/team/{contact_id}/toggle-pc", response_class=HTMLResponse)
def policy_team_toggle_pc(
    request: Request,
    policy_uid: str,
    contact_id: int,
    conn=Depends(get_db),
):
    """Toggle is_placement_colleague flag on a policy contact assignment."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    # contact_id in URL = assignment_id for backward compat with templates
    set_placement_colleague(conn, contact_id)
    conn.commit()
    return _policy_team_response(request, conn, uid)


@router.post("/{policy_uid}/team/quick-assign", response_class=HTMLResponse)
def policy_team_quick_assign(
    request: Request,
    policy_uid: str,
    contact_ids: list[str] = Form([]),
    conn=Depends(get_db),
):
    """Bulk-assign client team members to a policy from checkboxes.

    contact_ids here are client assignment IDs from the team contacts list.
    """
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    for cid_str in contact_ids:
        try:
            asg_id = int(cid_str)
        except ValueError:
            continue
        # Look up the contact_id from the client assignment
        src = conn.execute(
            """SELECT cca.contact_id, co.name, cca.title, cca.role, co.email, co.phone, co.mobile
               FROM contact_client_assignments cca
               JOIN contacts co ON cca.contact_id = co.id
               WHERE cca.id=?""",
            (asg_id,),
        ).fetchone()
        if not src:
            continue
        # Skip if this contact is already assigned to this policy
        existing = conn.execute(
            "SELECT id FROM contact_policy_assignments WHERE policy_id=? AND contact_id=?",
            (policy["id"], src["contact_id"]),
        ).fetchone()
        if existing:
            continue
        assign_contact_to_policy(
            conn, src["contact_id"], policy["id"],
            role=src["role"], title=src["title"],
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
    logger.info("Policy %s status -> %s", uid, status)
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


@router.post("/{policy_uid}/followup", response_class=HTMLResponse)
def policy_followup(
    request: Request,
    policy_uid: str,
    activity_type: str = Form("Call"),
    notes: str = Form(""),
    duration_hours: str = Form(""),
    new_follow_up_date: str = Form(""),
    conn=Depends(get_db),
):
    """Follow-up + re-diary for a policy reminder: log an activity and reschedule."""
    from datetime import date as _date

    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("", status_code=404)

    p = dict(policy)
    subject = f"Follow-up: {p.get('policy_type', '')} — {p.get('carrier', '')}"
    account_exec = cfg.get("default_account_exec", "Grant")

    # Create activity log entry
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _date.today().isoformat(), p["client_id"], p["id"],
            activity_type, subject, notes or None,
            new_follow_up_date or None, account_exec, round_duration(duration_hours),
        ),
    )

    # Update the policy's follow_up_date (reschedule or clear)
    conn.execute(
        "UPDATE policies SET follow_up_date = ? WHERE policy_uid = ?",
        (new_follow_up_date or None, uid),
    )
    conn.commit()

    # If no new follow-up date, the row should disappear from follow-ups
    if not new_follow_up_date:
        return HTMLResponse("")

    # Return updated followup row
    row = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.follow_up_date,
                  p.project_name, p.project_id, p.client_id, p.is_opportunity,
                  c.name AS client_name, c.cn_number,
                  'policy' AS source,
                  CASE WHEN p.is_opportunity = 1 THEN 'Opportunity' ELSE 'Policy Reminder' END AS activity_type,
                  NULL AS contact_person, NULL AS contact_email, NULL AS internal_cc,
                  p.policy_type || ' — ' || COALESCE(p.carrier, '') AS subject,
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
    # Attach latest note for display
    note = conn.execute(
        """SELECT subject, details, activity_date FROM activity_log
           WHERE policy_id = ? ORDER BY activity_date DESC, id DESC LIMIT 1""",
        (p["id"],),
    ).fetchone()
    r["note_subject"] = note["subject"] if note else None
    r["note_details"] = note["details"] if note else None
    r["note_date"] = note["activity_date"] if note else None
    return templates.TemplateResponse("followups/_row.html", {
        "request": request, "r": r, "today": today,
    })


@router.post("/{policy_uid}/clear-followup", response_class=HTMLResponse)
def policy_clear_followup(
    policy_uid: str,
    duration_hours: float = Form(0),
    note: str = Form(""),
    abandon: str = Form(""),
    conn=Depends(get_db),
):
    """Clear a policy-level follow-up date. Optionally log time and a note."""
    from datetime import date as _date
    uid = policy_uid.upper()
    note = note.strip()
    if abandon and note:
        note = f"[Abandoned] {note}"

    conn.execute("UPDATE policies SET follow_up_date=NULL WHERE policy_uid=?", (uid,))

    # If time or note provided, create an activity log entry to record the work
    if (duration_hours and duration_hours > 0) or note:
        policy = conn.execute(
            "SELECT id, client_id, policy_type, carrier FROM policies WHERE policy_uid=?", (uid,)
        ).fetchone()
        if policy:
            subject = f"Cleared follow-up — {policy['policy_type']}"
            if abandon:
                subject = f"[Abandoned] {subject}"
            account_exec = cfg.get("default_account_exec", "")
            conn.execute(
                """INSERT INTO activity_log
                   (activity_date, client_id, policy_id, activity_type, subject, details,
                    duration_hours, follow_up_done, account_exec)
                   VALUES (?, ?, ?, 'Task', ?, ?, ?, 1, ?)""",
                (
                    _date.today().isoformat(),
                    policy["client_id"],
                    policy["id"],
                    subject,
                    note or None,
                    round_duration(duration_hours) if duration_hours and duration_hours > 0 else None,
                    account_exec,
                ),
            )

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
    resp = templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})
    resp.headers["HX-Trigger"] = '{"refreshFollowups": "", "activityLogged": "Snoozed +' + str(days) + 'd to ' + r["follow_up_date"] + '"}'
    return resp


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
    resp = templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})
    resp.headers["HX-Trigger"] = '{"refreshFollowups": "", "activityLogged": "Rescheduled to ' + new_date + '"}'
    return resp


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
    conn.execute("DELETE FROM policy_timeline WHERE policy_uid=?", (uid,))
    conn.commit()
    logger.info("Policy %s archived", uid)
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.post("/{policy_uid}/delete")
def policy_delete(policy_uid: str, conn=Depends(get_db)):
    """Permanently delete a policy and all related records."""
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    client_id = policy["client_id"]
    pid = policy["id"]
    # Clean up related records (order matters for FK constraints)
    conn.execute("DELETE FROM mandated_activity_log WHERE policy_uid = ?", (uid,))
    conn.execute("DELETE FROM policy_milestones WHERE policy_uid = ?", (uid,))
    conn.execute("DELETE FROM policy_scratchpad WHERE policy_uid = ?", (uid,))
    conn.execute("DELETE FROM contact_policy_assignments WHERE policy_id = ?", (pid,))
    conn.execute("DELETE FROM meeting_policies WHERE policy_uid = ?", (uid,))
    conn.execute("UPDATE activity_log SET policy_id = NULL WHERE policy_id = ?", (pid,))
    conn.execute("UPDATE meeting_action_items SET policy_uid = NULL WHERE policy_uid = ?", (uid,))
    # Unlink any policies linked to this as a program
    conn.execute("UPDATE policies SET program_id = NULL WHERE program_id = ?", (pid,))
    conn.execute("DELETE FROM policies WHERE policy_uid = ?", (uid,))
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


def _policy_scratchpad_ctx(request, conn, uid: str, content: str | None = None) -> dict:
    """Build context dict for the policies/_scratchpad.html partial."""
    policy = get_policy_by_uid(conn, uid)
    if content is None:
        row = conn.execute(
            "SELECT content, updated_at FROM policy_scratchpad WHERE policy_uid=?", (uid,)
        ).fetchone()
        content = row["content"] if row else ""
        updated = row["updated_at"] if row else ""
    else:
        updated_row = conn.execute(
            "SELECT updated_at FROM policy_scratchpad WHERE policy_uid=?", (uid,)
        ).fetchone()
        updated = updated_row["updated_at"] if updated_row else ""
    return {
        "request": request,
        "policy": dict(policy) if policy else {},
        "policy_scratchpad": content,
        "policy_scratchpad_updated": updated,
        "policy_saved_notes": get_saved_notes(conn, "policy", uid),
    }


@router.post("/{policy_uid}/scratchpad")
def policy_scratchpad_save(
    request: Request, policy_uid: str, content: str = Form(""), conn=Depends(get_db)
):
    """Auto-save per-policy working notes. Returns JSON if Accept header requests it."""
    from datetime import datetime, timezone
    uid = policy_uid.upper()
    policy = get_policy_by_uid(conn, uid)
    if not policy:
        return HTMLResponse("Policy not found", status_code=404)
    conn.execute(
        "INSERT INTO policy_scratchpad (policy_uid, content) VALUES (?, ?) "
        "ON CONFLICT(policy_uid) DO UPDATE SET content = excluded.content",
        (uid, content),
    )
    conn.commit()
    if "application/json" in (request.headers.get("accept") or ""):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return JSONResponse({"ok": True, "saved_at": now})
    return templates.TemplateResponse(
        "policies/_scratchpad.html", _policy_scratchpad_ctx(request, conn, uid, content)
    )


@router.post("/{policy_uid}/notes/save", response_class=HTMLResponse)
def policy_note_save(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Pin current policy scratchpad content as a saved note, then clear."""
    uid = policy_uid.upper()
    row = conn.execute(
        "SELECT content FROM policy_scratchpad WHERE policy_uid=?", (uid,)
    ).fetchone()
    content = (row["content"] if row else "").strip()
    if content:
        save_note(conn, "policy", uid, content)
        # Also log to activity_log for unified account history
        policy = get_policy_by_uid(conn, uid)
        if policy:
            account_exec = cfg.get("default_account_exec", "Grant")
            subject = content[:120] + ("…" if len(content) > 120 else "")
            conn.execute(
                """INSERT INTO activity_log
                   (activity_date, client_id, policy_id, activity_type, subject, details, account_exec)
                   VALUES (?, ?, ?, 'Note', ?, ?, ?)""",
                (datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 policy["client_id"], policy["id"], subject, content, account_exec),
            )
        conn.execute(
            "UPDATE policy_scratchpad SET content = '' WHERE policy_uid = ?", (uid,)
        )
        conn.commit()
    return templates.TemplateResponse(
        "policies/_scratchpad.html", _policy_scratchpad_ctx(request, conn, uid)
    )


@router.delete("/{policy_uid}/notes/{note_id}", response_class=HTMLResponse)
def policy_note_delete(request: Request, policy_uid: str, note_id: int, conn=Depends(get_db)):
    """Delete a saved note from a policy."""
    uid = policy_uid.upper()
    delete_saved_note(conn, note_id)
    return templates.TemplateResponse(
        "policies/_scratchpad.html", _policy_scratchpad_ctx(request, conn, uid)
    )


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
    is_bor: str = Form("0"),
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
    is_program: str = Form("0"),
    conn=Depends(get_db),
):
    from policydb.db import next_policy_uid

    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    from policydb.utils import parse_currency_with_magnitude as _parse_money
    uid = next_policy_uid(conn)
    account_exec = cfg.get("default_account_exec", "Grant")
    opp = 1 if is_opportunity == "1" else 0
    pgm = 1 if is_program == "1" else 0
    policy_type = normalize_coverage_type(policy_type)
    carrier = normalize_carrier(carrier) if carrier else ""
    policy_number = normalize_policy_number(policy_number) if policy_number else ""
    # Parse currency shorthand (e.g., "$5M" → 5000000)
    premium = str(_parse_money(premium) or 0) if premium else premium
    limit_amount = str(_parse_money(limit_amount) or '') if limit_amount else limit_amount
    deductible = str(_parse_money(deductible) or '') if deductible else deductible
    exposure_address = exposure_address.strip() if exposure_address else ""
    exposure_city = format_city(exposure_city) if exposure_city else ""
    exposure_state = format_state(exposure_state) if exposure_state else ""
    exposure_zip = format_zip(exposure_zip) if exposure_zip else ""
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, coverage_form, layer_position, tower_group, is_standalone, is_bor,
            is_opportunity, opportunity_status, target_effective_date,
            renewal_status, underwriter_name, underwriter_contact,
            account_exec, project_name,
            exposure_basis, exposure_amount, exposure_unit,
            exposure_address, exposure_city, exposure_state, exposure_zip,
            commission_rate, prior_premium, notes, follow_up_date,
            attachment_point, participation_of, first_named_insured, access_point,
            is_program)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, client_id, policy_type, carrier or None, policy_number or None,
         effective_date or None, expiration_date or None, _float(premium) or 0,
         _float(limit_amount), _float(deductible),
         description or None, coverage_form or None,
         layer_position or "Primary", tower_group or None,
         1 if is_standalone == "1" else 0,
         1 if is_bor == "1" else 0,
         opp, opportunity_status or None, target_effective_date or None,
         renewal_status,
         underwriter_name or None, underwriter_contact or None,
         account_exec, project_name or None,
         exposure_basis or None, _float(exposure_amount), exposure_unit or None,
         exposure_address or None, exposure_city or None,
         exposure_state or None, exposure_zip or None,
         _float(commission_rate), _float(prior_premium), notes or None,
         follow_up_date or None,
         _float(attachment_point), _float(participation_of),
         first_named_insured or None, access_point or None,
         pgm),
    )
    conn.commit()
    logger.info("Policy %s created for client %d", uid, client_id)
    new_policy = get_policy_by_uid(conn, uid)
    if new_policy:
        _pid = new_policy["id"]
        # Create structured contact records for placement colleague and underwriter
        _pc_name = (placement_colleague or "").strip()
        if _pc_name:
            _pc_cid = get_or_create_contact(conn, _pc_name)
            assign_contact_to_policy(conn, _pc_cid, _pid, is_placement_colleague=1)
        _uw_name = (underwriter_name or "").strip()
        if _uw_name:
            _uw_email = (underwriter_contact or "").strip() or None
            _uw_cid = get_or_create_contact(conn, _uw_name, email=_uw_email)
            assign_contact_to_policy(conn, _uw_cid, _pid, role="Underwriter")
        if project_name:
            _sync_project_id(conn, _pid, client_id, project_name)
        conn.commit()
    return RedirectResponse(f"/policies/{uid}/edit", status_code=303)
