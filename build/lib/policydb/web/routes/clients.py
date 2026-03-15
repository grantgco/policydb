"""Client routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from policydb import config as cfg
from policydb.utils import format_phone
from policydb.queries import (
    get_activities,
    get_all_clients,
    get_client_by_id,
    get_client_summary,
    get_policies_for_client,
    full_text_search,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/clients")


_CLIENT_SORT_FIELDS = {
    "name", "industry_segment", "total_policies", "total_premium",
    "total_revenue", "next_renewal_days", "activity_last_90d",
}


def _apply_client_filters(clients, segment="", urgent="", inactive=""):
    if segment:
        clients = [c for c in clients if c["industry_segment"] == segment]
    if urgent:
        clients = [c for c in clients if (c.get("next_renewal_days") or 999) <= 90]
    if inactive:
        clients = [c for c in clients if (c.get("activity_last_90d") or 0) == 0]
    return clients


def _sort_clients(clients, sort="name", dir="asc"):
    field = sort if sort in _CLIENT_SORT_FIELDS else "name"
    reverse = dir == "desc"
    clients.sort(
        key=lambda c: (c.get(field) is None, c.get(field) if c.get(field) is not None else ""),
        reverse=reverse,
    )
    return clients


@router.get("", response_class=HTMLResponse)
def client_list(
    request: Request,
    q: str = "",
    segment: str = "",
    urgent: str = "",
    inactive: str = "",
    sort: str = "name",
    dir: str = "asc",
    conn=Depends(get_db),
):
    clients = [dict(r) for r in get_all_clients(conn)]
    clients = _apply_client_filters(clients, segment, urgent, inactive)
    clients = _sort_clients(clients, sort, dir)
    return templates.TemplateResponse("clients/list.html", {
        "request": request,
        "active": "clients",
        "clients": clients,
        "q": q,
        "segment": segment,
        "urgent": urgent,
        "inactive": inactive,
        "sort": sort if sort in _CLIENT_SORT_FIELDS else "name",
        "dir": dir,
        "industry_segments": cfg.get("industry_segments", []),
    })


@router.get("/search", response_class=HTMLResponse)
def client_search(
    request: Request,
    q: str = "",
    segment: str = "",
    urgent: str = "",
    inactive: str = "",
    sort: str = "name",
    dir: str = "asc",
    conn=Depends(get_db),
):
    """HTMX partial: filtered client table rows."""
    if q.strip():
        raw = full_text_search(conn, q.strip())
        client_ids = {r["id"] for r in raw["clients"]}
        all_clients = [dict(r) for r in get_all_clients(conn)]
        clients = [c for c in all_clients if c["id"] in client_ids or q.lower() in c["name"].lower()]
    else:
        clients = [dict(r) for r in get_all_clients(conn)]
    clients = _apply_client_filters(clients, segment, urgent, inactive)
    clients = _sort_clients(clients, sort, dir)
    return templates.TemplateResponse("clients/_table_rows.html", {
        "request": request,
        "clients": clients,
    })


@router.get("/new", response_class=HTMLResponse)
def client_new_form(request: Request):
    return templates.TemplateResponse("clients/edit.html", {
        "request": request,
        "active": "clients",
        "client": None,
        "industry_segments": cfg.get("industry_segments"),
    })


@router.post("/new")
def client_new_post(
    request: Request,
    name: str = Form(...),
    industry_segment: str = Form(...),
    cn_number: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    broker_fee: str = Form(""),
    business_description: str = Form(""),
    website: str = Form(""),
    renewal_month: str = Form(""),
    client_since: str = Form(""),
    preferred_contact_method: str = Form(""),
    referral_source: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO clients (name, industry_segment, cn_number, primary_contact, contact_email,
           contact_phone, address, notes, account_exec, broker_fee, business_description,
           website, renewal_month, client_since, preferred_contact_method, referral_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, industry_segment, cn_number or None, primary_contact or None, contact_email or None,
         format_phone(contact_phone) or None, address or None, notes or None, account_exec,
         _float(broker_fee), business_description or None,
         website or None, _int(renewal_month), client_since or None,
         preferred_contact_method or None, referral_source or None),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{cursor.lastrowid}", status_code=303)


@router.get("/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: int, add_contact: str = "", conn=Depends(get_db)):
    from collections import defaultdict
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    summary = get_client_summary(conn, client_id)
    all_policies = [dict(p) for p in get_policies_for_client(conn, client_id)]
    opportunities = [p for p in all_policies if p.get("is_opportunity")]
    policies = [p for p in all_policies if not p.get("is_opportunity")]

    # Attach full policy_contacts list to each opportunity for per-contact email links
    if opportunities:
        opp_ids = [o["id"] for o in opportunities]
        _pc_placeholders = ",".join("?" * len(opp_ids))
        _opp_contacts = conn.execute(
            f"SELECT policy_id, name, email, phone, role, organization FROM policy_contacts "  # noqa: S608
            f"WHERE policy_id IN ({_pc_placeholders}) ORDER BY id",
            opp_ids,
        ).fetchall()
        _opp_contacts_map: dict[int, list] = {}
        for _c in _opp_contacts:
            _opp_contacts_map.setdefault(_c["policy_id"], []).append(dict(_c))
        for o in opportunities:
            o["team"] = _opp_contacts_map.get(o["id"], [])
    activities = [dict(a) for a in get_activities(conn, client_id=client_id, days=90)]
    activity_types = cfg.get("activity_types")

    # Group policies by project_name; blank → "Corporate / Standalone" (sorted last).
    # Normalize keys (strip + collapse whitespace + lowercase) so minor format
    # differences ("Main St " vs "main st") still land in the same group.
    def _proj_key(name: str | None) -> str:
        if not name:
            return ""
        return " ".join(name.strip().split()).lower()

    groups: dict[str, list] = defaultdict(list)
    group_display: dict[str, str] = {}  # canonical display name per key
    for p in policies:
        raw = (p.get("project_name") or "").strip()
        key = _proj_key(raw)
        groups[key].append(p)
        if key and key not in group_display:
            group_display[key] = raw

    policy_groups = sorted(
        [(group_display.get(k, ""), v) for k, v in groups.items()],
        key=lambda x: ("\xff" if not x[0] else x[0].lower()),
    )

    # Build tower groups: {project_name: {tower_group: [layers sorted by attachment_point]}}
    # Policies without a tower_group are excluded; blank project_name → "Corporate / Standalone"
    tower_by_project: dict = defaultdict(lambda: defaultdict(list))
    for p in policies:
        tg = p.get("tower_group")
        if tg:
            proj = (p.get("project_name") or "").strip() or "Corporate / Standalone"
            tower_by_project[proj][tg].append(p)
    # Sort: named projects A-Z, "Corporate / Standalone" last; within each project sort tower groups A-Z
    tower_groups = {
        proj: {
            tg: sorted(layers, key=lambda lp: lp.get("attachment_point") or 0)
            for tg, layers in sorted(tgs.items())
        }
        for proj, tgs in sorted(
            tower_by_project.items(),
            key=lambda x: ("\xff" if x[0] == "Corporate / Standalone" else x[0].lower()),
        )
    }

    # Archived policies for this client (for the collapsed audit section)
    archived_policies = [dict(r) for r in conn.execute(
        """SELECT policy_uid, policy_type, carrier, effective_date, expiration_date,
                  premium, policy_number, project_name
           FROM policies WHERE client_id = ? AND archived = 1
           ORDER BY expiration_date DESC""",
        (client_id,),
    ).fetchall()]

    # Load project notes keyed by normalized project name (from projects table)
    notes_rows = conn.execute(
        "SELECT id, LOWER(TRIM(name)) AS key, name, notes FROM projects WHERE client_id = ?",
        (client_id,),
    ).fetchall()
    project_notes = {r["key"]: r["notes"] for r in notes_rows}
    project_ids = {r["key"]: r["id"] for r in notes_rows}

    # Build project address dict from most recent policy per project
    project_addresses: dict = {}
    for p in sorted(policies, key=lambda x: x.get("id", 0), reverse=True):
        key = _proj_key(p.get("project_name"))
        if key not in project_addresses:
            project_addresses[key] = {
                "exposure_address": p.get("exposure_address") or "",
                "exposure_city":    p.get("exposure_city") or "",
                "exposure_state":   p.get("exposure_state") or "",
                "exposure_zip":     p.get("exposure_zip") or "",
            }

    scratch_row = conn.execute(
        "SELECT content, updated_at FROM client_scratchpad WHERE client_id=?",
        (client_id,),
    ).fetchone()
    client_scratchpad = scratch_row["content"] if scratch_row else ""
    client_scratchpad_updated = scratch_row["updated_at"] if scratch_row else ""

    contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM client_contacts WHERE client_id=? AND contact_type='client' ORDER BY is_primary DESC, name",
        (client_id,),
    ).fetchall()]

    team_contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM client_contacts WHERE client_id=? AND contact_type='internal' ORDER BY name",
        (client_id,),
    ).fetchall()]

    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)

    # Aggregate placement touchpoints from policy_contacts (new) and legacy placement_colleague field
    _pol_map = {p["id"]: p for p in all_policies}
    _pol_subj_tpl = cfg.get("email_subject_policy", "Re: {{client_name}} \u2014 {{policy_type}}")
    _colleagues: dict[str, dict] = {}

    def _add_colleague(name: str, email: str, policy_dict: dict, organization: str = "") -> None:
        name = name.strip()
        if not name:
            return
        if name not in _colleagues:
            _colleagues[name] = {"name": name, "email": email.strip(), "organization": organization, "policies": []}
        elif not _colleagues[name]["email"] and email:
            _colleagues[name]["email"] = email.strip()
        p = policy_dict
        _pol_ctx = {
            "client_name": client["name"],
            "policy_type": p.get("policy_type") or "",
            "carrier": p.get("carrier") or "",
            "policy_uid": p.get("policy_uid") or "",
            "effective_date": p.get("effective_date") or "",
            "expiration_date": p.get("expiration_date") or "",
            "project_name": (p.get("project_name") or "").strip(),
            "project_name_sep": f" \u2014 {p.get('project_name')}" if p.get("project_name") else "",
        }
        _colleagues[name]["policies"].append({
            "policy_uid": p.get("policy_uid"),
            "policy_type": p.get("policy_type"),
            "carrier": p.get("carrier"),
            "project_name": (p.get("project_name") or "").strip(),
            "expiration_date": p.get("expiration_date") or "",
            "mailto_subject": _render_tokens(_pol_subj_tpl, _pol_ctx),
        })

    # Source: policy_contacts table only
    _pc_rows = conn.execute(
        """SELECT pc.name, pc.email, pc.organization, p.id AS policy_id
           FROM policy_contacts pc
           JOIN policies p ON pc.policy_id = p.id
           WHERE p.client_id = ? AND p.archived = 0
           ORDER BY pc.name, p.policy_type""",
        (client_id,),
    ).fetchall()
    for row in _pc_rows:
        p = _pol_map.get(row["policy_id"])
        if p:
            _add_colleague(row["name"] or "", row["email"] or "", p, row["organization"] or "")

    placement_colleagues = sorted(_colleagues.values(), key=lambda x: x["name"].lower())

    # Render per-opportunity mailto subjects now that _pol_subj_tpl is available
    for o in opportunities:
        _opp_ctx = {
            "client_name": client["name"],
            "policy_type": o.get("policy_type") or "",
            "carrier": o.get("carrier") or "",
            "policy_uid": o.get("policy_uid") or "",
            "effective_date": o.get("target_effective_date") or "",
            "expiration_date": "",
            "project_name": (o.get("project_name") or "").strip(),
            "project_name_sep": f" \u2014 {o.get('project_name')}" if o.get("project_name") else "",
        }
        o["mailto_subject"] = _render_tokens(_pol_subj_tpl, _opp_ctx)

    # All contacts JSON for the contacts card autocomplete
    import json as _json
    _ac_rows = conn.execute(
        """SELECT name,
                  MAX(email)  AS email,
                  MAX(phone)  AS phone,
                  MAX(title)  AS title,
                  MAX(role)   AS role
           FROM client_contacts
           WHERE contact_type='client' AND name IS NOT NULL AND name != ''
           GROUP BY name ORDER BY name"""
    ).fetchall()
    all_contacts_json = _json.dumps({
        r["name"]: {"email": r["email"] or "", "phone": r["phone"] or "",
                    "title": r["title"] or "", "role": r["role"] or ""}
        for r in _ac_rows
    })

    return templates.TemplateResponse("clients/detail.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "summary": dict(summary) if summary else {},
        "policy_groups": policy_groups,
        "tower_groups": tower_groups,
        "activities": activities,
        "activity_types": activity_types,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "project_notes": project_notes,
        "project_ids": project_ids,
        "project_addresses": project_addresses,
        "archived_policies": archived_policies,
        "client_scratchpad": client_scratchpad,
        "client_scratchpad_updated": client_scratchpad_updated,
        "contacts": contacts,
        "team_contacts": team_contacts,
        "opportunities": opportunities,
        "placement_colleagues": placement_colleagues,
        "mailto_subject": mailto_subject,
        "all_contacts_json": all_contacts_json,
        "add_contact": add_contact,
    })


def _contacts_response(request, conn, client_id: int):
    """Shared helper: return the client contacts card partial with fresh data."""
    import json as _json
    contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM client_contacts WHERE client_id=? AND contact_type='client' ORDER BY is_primary DESC, name",
        (client_id,),
    ).fetchall()]
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)
    # All known contacts across all clients for autocomplete fill
    all_ac_rows = conn.execute(
        """SELECT name,
                  MAX(email)  AS email,
                  MAX(phone)  AS phone,
                  MAX(title)  AS title,
                  MAX(role)   AS role
           FROM client_contacts
           WHERE contact_type='client' AND name IS NOT NULL AND name != ''
           GROUP BY name ORDER BY name"""
    ).fetchall()
    all_contacts_json = _json.dumps({
        r["name"]: {"email": r["email"] or "", "phone": r["phone"] or "",
                    "title": r["title"] or "", "role": r["role"] or ""}
        for r in all_ac_rows
    })
    return templates.TemplateResponse("clients/_contacts.html", {
        "request": request,
        "client": dict(client) if client else {},
        "contacts": contacts,
        "mailto_subject": mailto_subject,
        "all_contacts_json": all_contacts_json,
    })


def _internal_contacts_response(request, conn, client_id: int):
    """Shared helper: return the internal team contacts card partial with fresh data."""
    import json as _json
    team_contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM client_contacts WHERE client_id=? AND contact_type='internal' ORDER BY name",
        (client_id,),
    ).fetchall()]
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)
    # Autocomplete: all internal contacts across all clients, deduped by name
    _ac_rows = conn.execute(
        """SELECT name,
                  MAX(title) AS title,
                  MAX(email) AS email,
                  MAX(phone) AS phone,
                  MAX(role)  AS role
           FROM client_contacts
           WHERE contact_type='internal' AND name IS NOT NULL AND name != ''
           GROUP BY LOWER(TRIM(name)) ORDER BY name"""
    ).fetchall()
    all_internal_contacts_json = _json.dumps({
        r["name"]: {"title": r["title"] or "", "email": r["email"] or "",
                    "phone": r["phone"] or "", "role": r["role"] or ""}
        for r in _ac_rows
    })
    team_cc_json = _json.dumps([{"name": c["name"], "email": c["email"]} for c in team_contacts if c.get("email")])
    return templates.TemplateResponse("clients/_team_contacts.html", {
        "request": request,
        "client": dict(client) if client else {},
        "team_contacts": team_contacts,
        "mailto_subject": mailto_subject,
        "all_internal_contacts_json": all_internal_contacts_json,
        "team_cc_json": team_cc_json,
    })


@router.post("/{client_id}/contacts/add", response_class=HTMLResponse)
def contact_add(
    request: Request,
    client_id: int,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "INSERT INTO client_contacts (client_id, name, title, role, email, phone, notes) VALUES (?,?,?,?,?,?,?)",
        (client_id, name, title or None, role or None, email or None, format_phone(phone) or None, notes or None),
    )
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.post("/{client_id}/contacts/{contact_id}/edit", response_class=HTMLResponse)
def contact_edit(
    request: Request,
    client_id: int,
    contact_id: int,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "UPDATE client_contacts SET name=?, title=?, role=?, email=?, phone=?, notes=? WHERE id=? AND client_id=? AND contact_type='client'",
        (name, title or None, role or None, email or None, format_phone(phone) or None, notes or None, contact_id, client_id),
    )
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.post("/{client_id}/contacts/{contact_id}/delete", response_class=HTMLResponse)
def contact_delete(
    request: Request,
    client_id: int,
    contact_id: int,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM client_contacts WHERE id=? AND client_id=? AND contact_type='client'",
        (contact_id, client_id),
    )
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.post("/{client_id}/contacts/{contact_id}/set-primary", response_class=HTMLResponse)
def contact_set_primary(
    request: Request,
    client_id: int,
    contact_id: int,
    conn=Depends(get_db),
):
    existing = conn.execute(
        "SELECT is_primary FROM client_contacts WHERE id=? AND client_id=?",
        (contact_id, client_id),
    ).fetchone()
    # Clear all primaries for this client first
    conn.execute(
        "UPDATE client_contacts SET is_primary=0 WHERE client_id=? AND contact_type='client'",
        (client_id,),
    )
    # If it wasn't already primary, set it; otherwise leave cleared (toggle off)
    if existing and not existing["is_primary"]:
        conn.execute(
            "UPDATE client_contacts SET is_primary=1 WHERE id=? AND client_id=?",
            (contact_id, client_id),
        )
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.post("/{client_id}/team/add", response_class=HTMLResponse)
def team_contact_add(
    request: Request,
    client_id: int,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    assignment: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "INSERT INTO client_contacts (client_id, name, title, role, assignment, email, phone, contact_type) VALUES (?,?,?,?,?,?,?,?)",
        (client_id, name, title or None, role or None, assignment or None,
         email or None, format_phone(phone) or None, "internal"),
    )
    conn.commit()
    return _internal_contacts_response(request, conn, client_id)


@router.post("/{client_id}/team/{contact_id}/edit", response_class=HTMLResponse)
def team_contact_edit(
    request: Request,
    client_id: int,
    contact_id: int,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    assignment: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "UPDATE client_contacts SET name=?, title=?, role=?, assignment=?, email=?, phone=? WHERE id=? AND client_id=? AND contact_type='internal'",
        (name, title or None, role or None, assignment or None,
         email or None, format_phone(phone) or None, contact_id, client_id),
    )
    conn.commit()
    return _internal_contacts_response(request, conn, client_id)


@router.post("/{client_id}/team/{contact_id}/delete", response_class=HTMLResponse)
def team_contact_delete(
    request: Request,
    client_id: int,
    contact_id: int,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM client_contacts WHERE id=? AND client_id=? AND contact_type='internal'",
        (contact_id, client_id),
    )
    conn.commit()
    return _internal_contacts_response(request, conn, client_id)


@router.get("/{client_id}/edit", response_class=HTMLResponse)
def client_edit_form(request: Request, client_id: int, conn=Depends(get_db)):
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    return templates.TemplateResponse("clients/edit.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "industry_segments": cfg.get("industry_segments"),
    })


@router.post("/{client_id}/edit")
def client_edit_post(
    request: Request,
    client_id: int,
    name: str = Form(...),
    industry_segment: str = Form(...),
    cn_number: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    broker_fee: str = Form(""),
    business_description: str = Form(""),
    website: str = Form(""),
    renewal_month: str = Form(""),
    client_since: str = Form(""),
    preferred_contact_method: str = Form(""),
    referral_source: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    conn.execute(
        """UPDATE clients SET name=?, industry_segment=?, cn_number=?, primary_contact=?,
           contact_email=?, contact_phone=?, address=?, notes=?,
           broker_fee=?, business_description=?,
           website=?, renewal_month=?, client_since=?, preferred_contact_method=?, referral_source=?
           WHERE id=?""",
        (name, industry_segment, cn_number or None, primary_contact or None, contact_email or None,
         format_phone(contact_phone) or None, address or None, notes or None,
         _float(broker_fee), business_description or None,
         website or None, _int(renewal_month), client_since or None,
         preferred_contact_method or None, referral_source or None,
         client_id),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/scratchpad", response_class=HTMLResponse)
def client_scratchpad_save(
    request: Request,
    client_id: int,
    content: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: auto-save per-client working notes."""
    conn.execute(
        "INSERT INTO client_scratchpad (client_id, content) VALUES (?, ?) "
        "ON CONFLICT(client_id) DO UPDATE SET content=excluded.content",
        (client_id, content),
    )
    conn.commit()
    row = conn.execute(
        "SELECT updated_at FROM client_scratchpad WHERE client_id=?", (client_id,)
    ).fetchone()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return templates.TemplateResponse("clients/_scratchpad.html", {
        "request": request,
        "client": dict(client) if client else {},
        "client_scratchpad": content,
        "client_scratchpad_updated": row["updated_at"] if row else "",
    })


@router.get("/{client_id}/export/full")
def export_full(client_id: int, conn=Depends(get_db)):
    """Full internal data export (XLSX) — all fields including internal notes."""
    from fastapi.responses import Response
    from policydb.exporter import export_full_xlsx
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    content = export_full_xlsx(conn, client_id, client["name"])
    safe = client["name"].lower().replace(" ", "_")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe}_full.xlsx"'},
    )


@router.get("/{client_id}/export/schedule")
def export_schedule(client_id: int, fmt: str = "md", conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import (
        export_schedule_csv, export_schedule_json, export_schedule_md, export_schedule_xlsx,
    )
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    safe = client["name"].lower().replace(" ", "_")
    if fmt == "xlsx":
        content = export_schedule_xlsx(conn, client_id, client["name"])
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe}_schedule.xlsx"'},
        )
    if fmt == "csv":
        content = export_schedule_csv(conn, client_id)
        media_type = "text/csv"
        ext = "csv"
    elif fmt == "json":
        content = export_schedule_json(conn, client_id, client["name"])
        media_type = "application/json"
        ext = "json"
    else:
        content = export_schedule_md(conn, client_id, client["name"])
        media_type = "text/markdown"
        ext = "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}_schedule.{ext}"'},
    )


def _project_note_ctx(conn, client_id: int, project_name: str) -> dict:
    """Shared context builder for project note partials."""
    row = conn.execute(
        "SELECT id, notes FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, project_name),
    ).fetchone()
    policy_count = conn.execute(
        "SELECT COUNT(*) FROM policies WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?)) AND archived = 0",
        (client_id, project_name),
    ).fetchone()[0]
    client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    # Pull address from the most recent policy in this project
    addr_row = conn.execute(
        """SELECT exposure_address, exposure_city, exposure_state, exposure_zip
           FROM policies
           WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))
             AND archived = 0
           ORDER BY id DESC LIMIT 1""",
        (client_id, project_name),
    ).fetchone()
    return {
        "project_name": project_name,
        "project_id": row["id"] if row else None,
        "note": row["notes"] if row else "",
        "policy_count": policy_count,
        "client": dict(client) if client else {},
        "exposure_address": addr_row["exposure_address"] if addr_row else "",
        "exposure_city": addr_row["exposure_city"] if addr_row else "",
        "exposure_state": addr_row["exposure_state"] if addr_row else "",
        "exposure_zip": addr_row["exposure_zip"] if addr_row else "",
    }


@router.get("/{client_id}/project-note", response_class=HTMLResponse)
def project_note_row(request: Request, client_id: int, project: str, conn=Depends(get_db)):
    """HTMX partial: display project header with note (used by Cancel)."""
    ctx = _project_note_ctx(conn, client_id, project)
    return templates.TemplateResponse("clients/_project_header.html", {"request": request, **ctx})


@router.get("/{client_id}/project-note/edit", response_class=HTMLResponse)
def project_note_edit(request: Request, client_id: int, project: str, conn=Depends(get_db)):
    """HTMX partial: edit form for a project note."""
    ctx = _project_note_ctx(conn, client_id, project)
    return templates.TemplateResponse("clients/_project_header_edit.html", {"request": request, **ctx})


@router.post("/{client_id}/project-note", response_class=HTMLResponse)
def project_note_save(
    request: Request,
    client_id: int,
    project_name: str = Form(...),
    notes: str = Form(""),
    exposure_address: str = Form(""),
    exposure_city: str = Form(""),
    exposure_state: str = Form(""),
    exposure_zip: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: upsert project note and bulk-update location address on all policies in the project."""
    conn.execute(
        "INSERT INTO projects (client_id, name, notes) VALUES (?, ?, ?) "
        "ON CONFLICT(client_id, name) DO UPDATE SET notes=excluded.notes",
        (client_id, project_name, notes.strip()),
    )
    conn.execute(
        """UPDATE policies SET
               exposure_address = ?,
               exposure_city    = ?,
               exposure_state   = ?,
               exposure_zip     = ?
           WHERE client_id = ?
             AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))
             AND archived = 0""",
        (
            exposure_address.strip() or None,
            exposure_city.strip() or None,
            exposure_state.strip() or None,
            exposure_zip.strip() or None,
            client_id, project_name,
        ),
    )
    conn.commit()
    ctx = _project_note_ctx(conn, client_id, project_name)
    return templates.TemplateResponse("clients/_project_header.html", {"request": request, **ctx})


@router.get("/{client_id}/export/llm")
def export_llm(client_id: int, conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_llm_client_md
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    content = export_llm_client_md(conn, client_id)
    safe = client["name"].lower().replace(" ", "_")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe}_llm.md"'},
    )
