"""Client routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from babel.dates import format_datetime as babel_format_datetime
from datetime import datetime

from policydb import config as cfg
from policydb.utils import clean_email, format_fein, format_phone
from policydb.queries import (
    get_activities,
    get_all_clients,
    get_client_by_id,
    get_client_contacts,
    get_client_summary,
    get_client_total_hours,
    get_or_create_contact,
    assign_contact_to_client,
    remove_contact_from_client,
    set_primary_contact,
    get_linked_group_for_client,
    get_linked_group_overview,
    get_policies_for_client,
    get_saved_notes,
    get_saved_notes_for_client_timeline,
    save_note,
    delete_saved_note,
    full_text_search,
    create_linked_group,
    add_client_to_group,
    remove_client_from_group,
    update_linked_group,
    delete_linked_group,
    count_changed_fields,
    check_auto_review_client,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/clients")


def _get_all_client_contact_orgs(conn):
    """Get all distinct organization values from contacts."""
    rows = conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != '' ORDER BY organization"
    ).fetchall()
    return [r["organization"] for r in rows]


_CLIENT_SORT_FIELDS = {
    "name", "industry_segment", "total_policies", "total_premium",
    "total_revenue", "next_renewal_days", "activity_last_90d",
}


def _apply_client_filters(clients, segment="", urgent="", inactive="", prospect=""):
    if segment:
        clients = [c for c in clients if c["industry_segment"] == segment]
    if urgent:
        clients = [c for c in clients if (c.get("next_renewal_days") or 999) <= 90]
    if inactive:
        clients = [c for c in clients if (c.get("activity_last_90d") or 0) == 0]
    if prospect:
        clients = [c for c in clients if c.get("is_prospect")]
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
    prospect: str = "",
    sort: str = "name",
    dir: str = "asc",
    conn=Depends(get_db),
):
    clients = [dict(r) for r in get_all_clients(conn)]
    clients = _apply_client_filters(clients, segment, urgent, inactive, prospect)
    clients = _sort_clients(clients, sort, dir)
    archived_clients = [dict(r) for r in conn.execute(
        """SELECT c.id, c.name, c.industry_segment,
                  COUNT(p.id) AS policy_count
           FROM clients c
           LEFT JOIN policies p ON p.client_id = c.id
           WHERE c.archived = 1
           GROUP BY c.id
           ORDER BY c.name""",
    ).fetchall()]
    linked_client_ids = {r["client_id"] for r in conn.execute(
        "SELECT client_id FROM client_group_members"
    ).fetchall()}
    return templates.TemplateResponse("clients/list.html", {
        "request": request,
        "active": "clients",
        "clients": clients,
        "q": q,
        "segment": segment,
        "urgent": urgent,
        "inactive": inactive,
        "prospect": prospect,
        "sort": sort if sort in _CLIENT_SORT_FIELDS else "name",
        "dir": dir,
        "industry_segments": cfg.get("industry_segments", []),
        "archived_clients": archived_clients,
        "linked_client_ids": linked_client_ids,
    })


@router.get("/search", response_class=HTMLResponse)
def client_search(
    request: Request,
    q: str = "",
    segment: str = "",
    urgent: str = "",
    inactive: str = "",
    prospect: str = "",
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
    clients = _apply_client_filters(clients, segment, urgent, inactive, prospect)
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
    is_prospect: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    contact_mobile: str = Form(""),
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
        """INSERT INTO clients (name, industry_segment, cn_number, is_prospect, primary_contact, contact_email,
           contact_phone, contact_mobile, address, notes, account_exec, broker_fee, business_description,
           website, renewal_month, client_since, preferred_contact_method, referral_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, industry_segment, cn_number.strip() or None, 1 if is_prospect else 0,
         primary_contact or None, clean_email(contact_email) or None,
         format_phone(contact_phone) or None, format_phone(contact_mobile) or None,
         address or None, notes or None, account_exec,
         _float(broker_fee), business_description or None,
         website or None, _int(renewal_month), client_since or None,
         preferred_contact_method or None, referral_source or None),
    )
    conn.commit()
    return RedirectResponse(f"/clients/{cursor.lastrowid}", status_code=303)


@router.get("/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: int, add_contact: str = "", conn=Depends(get_db)):
    from collections import defaultdict
    client = get_client_by_id(conn, client_id, include_archived=True)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    summary = get_client_summary(conn, client_id)
    all_policies = [dict(p) for p in get_policies_for_client(conn, client_id)]
    opportunities = [p for p in all_policies if p.get("is_opportunity")]
    policies = [p for p in all_policies if not p.get("is_opportunity")]

    # Attach milestone progress and readiness scores for the policy table
    from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
    policies = _attach_readiness_score(conn, _attach_milestone_progress(conn, policies))

    # Attach full policy contacts list to each opportunity for per-contact email links
    if opportunities:
        opp_ids = [o["id"] for o in opportunities]
        _pc_placeholders = ",".join("?" * len(opp_ids))
        _opp_contacts = conn.execute(
            f"SELECT cpa.policy_id, co.name, co.email, co.phone, cpa.role, co.organization "  # noqa: S608
            f"FROM contact_policy_assignments cpa "
            f"JOIN contacts co ON cpa.contact_id = co.id "
            f"WHERE cpa.policy_id IN ({_pc_placeholders}) ORDER BY cpa.id",
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

    def _tower_sort_key(lp):
        att = lp.get("attachment_point")
        if att is not None:
            return (float(att), 0)
        pos = lp.get("layer_position") or "Primary"
        try:
            return (-1, int(pos))
        except (ValueError, TypeError):
            return (-1, 0)

    def _attach_ground_up(layers):
        """Sort layers and compute ground-up running limit for each."""
        sorted_layers = sorted(layers, key=_tower_sort_key)
        running = 0.0
        for lp in sorted_layers:
            lim = float(lp.get("limit_amount") or 0)
            att = lp.get("attachment_point")
            part = lp.get("participation_of")
            if att is not None and float(att) >= 0:
                # Use participation_of as the full layer size when present
                layer_size = float(part) if part else lim
                lp["ground_up"] = float(att) + layer_size
            else:
                running += lim
                lp["ground_up"] = running
        return sorted_layers

    # Sort: named projects A-Z, "Corporate / Standalone" last; within each project sort tower groups A-Z
    tower_groups = {
        proj: {
            tg: _attach_ground_up(layers)
            for tg, layers in sorted(tgs.items())
        }
        for proj, tgs in sorted(
            tower_by_project.items(),
            key=lambda x: ("\xff" if x[0] == "Corporate / Standalone" else x[0].lower()),
        )
    }

    # Build proportional tower visuals (groups co-carriers, computes flex/fill)
    def _build_tower_visuals(tg_dict: dict) -> dict:
        from policydb.analysis import layer_notation as _ln
        visuals = {}
        for proj, tgs in tg_dict.items():
            visuals[proj] = {}
            for tg_name, layers in tgs.items():
                if not layers:
                    continue
                total_gu = max(float(l.get("ground_up") or 0) for l in layers)
                if total_gu == 0:
                    continue
                # Group co-carriers at same attachment point
                grouped: dict[str, list] = {}
                for l in layers:
                    att = l.get("attachment_point")
                    key = str(float(att)) if att is not None else f"pos-{l.get('layer_position', 'Primary')}"
                    grouped.setdefault(key, []).append(l)
                visual_layers = []
                for gkey, carriers in grouped.items():
                    parts = [float(c["participation_of"]) for c in carriers if c.get("participation_of")]
                    carrier_limits = [float(c.get("limit_amount") or 0) for c in carriers]
                    if parts:
                        full_limit = max(parts)
                    elif len(carriers) > 1:
                        full_limit = sum(carrier_limits)
                    else:
                        full_limit = carrier_limits[0] if carrier_limits else 0
                    flex = max(full_limit / 1_000_000, 0.5)
                    att_val = carriers[0].get("attachment_point")
                    gu_val = max(float(c.get("ground_up") or 0) for c in carriers)
                    carrier_data = []
                    for c in carriers:
                        climit = float(c.get("limit_amount") or 0)
                        fill_pct = round(climit / full_limit * 100, 1) if full_limit > 0 else 100
                        carrier_data.append({
                            "carrier": c.get("carrier", ""),
                            "limit": climit,
                            "fill_pct": fill_pct,
                            "policy_uid": c.get("policy_uid", ""),
                            "policy_type": c.get("policy_type", ""),
                            "premium": c.get("premium") or 0,
                            "notation": _ln(c.get("limit_amount"), c.get("attachment_point"), c.get("participation_of")) or "",
                        })
                    is_shared = len(carriers) > 1 or any(c.get("participation_of") for c in carriers)
                    total_fill = sum(cd["fill_pct"] for cd in carrier_data)
                    open_pct = round(100 - total_fill, 1) if is_shared and total_fill < 100 else 0
                    open_amount = full_limit - sum(cd["limit"] for cd in carrier_data) if open_pct > 0 else 0
                    visual_layers.append({
                        "attachment": float(att_val) if att_val is not None else None,
                        "full_limit": full_limit,
                        "flex": flex,
                        "ground_up": gu_val,
                        "carriers": carrier_data,
                        "total_premium": sum(cd["premium"] for cd in carrier_data),
                        "is_shared": is_shared,
                        "open_pct": open_pct,
                        "open_amount": open_amount,
                    })
                visual_layers.sort(key=lambda vl: vl["attachment"] if vl["attachment"] is not None else -1)
                visuals[proj][tg_name] = {
                    "layers": visual_layers,
                    "total_ground_up": total_gu,
                    "total_premium": sum(vl["total_premium"] for vl in visual_layers),
                    "carrier_count": sum(len(vl["carriers"]) for vl in visual_layers),
                }
        return visuals

    tower_visuals = _build_tower_visuals(tower_groups) if tower_groups else {}

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
    client_saved_notes = get_saved_notes(conn, "client", str(client_id))

    contacts = get_client_contacts(conn, client_id, contact_type='client')

    team_contacts = get_client_contacts(conn, client_id, contact_type='internal')

    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)

    # Aggregate placement touchpoints from contact_policy_assignments
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

    # Source: contact_policy_assignments + contacts tables
    _pc_rows = conn.execute(
        """SELECT co.name, co.email, co.organization, cpa.policy_id
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? AND p.archived = 0
           ORDER BY co.name, p.policy_type""",
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
        """SELECT co.name,
                  MAX(co.email)  AS email,
                  MAX(co.phone)  AS phone,
                  MAX(co.mobile) AS mobile,
                  MAX(cca.title) AS title,
                  MAX(cca.role)  AS role
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE cca.contact_type='client' AND co.name IS NOT NULL AND co.name != ''
           GROUP BY co.name ORDER BY co.name"""
    ).fetchall()
    all_contacts_json = _json.dumps({
        r["name"]: {"email": r["email"] or "", "phone": r["phone"] or "",
                    "mobile": r["mobile"] or "",
                    "title": r["title"] or "", "role": r["role"] or ""}
        for r in _ac_rows
    })

    # Risk / Exposure tracking
    risks = [dict(r) for r in conn.execute(
        """SELECT r.*, p.policy_type AS linked_policy_type, p.carrier AS linked_carrier
           FROM client_risks r
           LEFT JOIN policies p ON r.policy_uid = p.policy_uid
           WHERE r.client_id=?
           ORDER BY
             CASE r.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
             r.category""",
        (client_id,),
    ).fetchall()]
    for risk in risks:
        risk["coverage_lines"] = [dict(cl) for cl in conn.execute(
            "SELECT * FROM risk_coverage_lines WHERE risk_id=? ORDER BY coverage_line",
            (risk["id"],),
        ).fetchall()]
        risk["controls"] = [dict(c) for c in conn.execute(
            "SELECT * FROM risk_controls WHERE risk_id=? ORDER BY created_at",
            (risk["id"],),
        ).fetchall()]
    risk_categories = cfg.get("risk_categories", [])
    risk_severities = cfg.get("risk_severities", [])
    # Build list of policy UIDs for the "link to policy" dropdown
    policy_uid_options = [{"uid": p["policy_uid"], "label": f"{p['policy_uid']} — {p['policy_type']}"} for p in all_policies if not p.get("archived")]

    client_total_hours = get_client_total_hours(conn, client_id)
    linked_group = get_linked_group_for_client(conn, client_id)

    # Account Pulse data
    from datetime import date as _date
    _today = _date.today().isoformat()
    pulse_overdue = conn.execute(
        """SELECT COUNT(*) AS n FROM activity_log
           WHERE client_id = ? AND follow_up_date < ? AND follow_up_done = 0""",
        (client_id, _today),
    ).fetchone()["n"]
    pulse_overdue += conn.execute(
        """SELECT COUNT(*) AS n FROM policies
           WHERE client_id = ? AND follow_up_date < ? AND follow_up_date IS NOT NULL AND archived = 0""",
        (client_id, _today),
    ).fetchone()["n"]
    # Milestone progress across all active policies
    _ms_rows = conn.execute(
        """SELECT pm.completed FROM policy_milestones pm
           JOIN policies p ON pm.policy_uid = p.policy_uid
           WHERE p.client_id = ? AND p.archived = 0""",
        (client_id,),
    ).fetchall()
    pulse_milestone_done = sum(1 for r in _ms_rows if r["completed"])
    pulse_milestone_total = len(_ms_rows)
    # High/critical risks
    pulse_high_risks = conn.execute(
        "SELECT COUNT(*) AS n FROM client_risks WHERE client_id = ? AND severity IN ('High', 'Critical')",
        (client_id,),
    ).fetchone()["n"]
    # Recent timeline (activities + saved notes interleaved)
    _recent_acts = [dict(r) | {"_type": "activity", "_sort_date": r["activity_date"]} for r in conn.execute(
        """SELECT activity_type, subject, activity_date, duration_hours
           FROM activity_log WHERE client_id = ?
           ORDER BY activity_date DESC, id DESC LIMIT 5""",
        (client_id,),
    ).fetchall()]
    _recent_notes = [n | {"_type": "note", "_sort_date": n["created_at"][:10]} for n in
                     get_saved_notes_for_client_timeline(conn, client_id, limit=5)]
    pulse_recent = sorted(_recent_acts + _recent_notes, key=lambda x: x["_sort_date"], reverse=True)[:5]

    from policydb.queries import REVIEW_CYCLE_LABELS as _REVIEW_CYCLE_LABELS
    return templates.TemplateResponse("clients/detail.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "summary": dict(summary) if summary else {},
        "client_total_hours": client_total_hours,
        "policy_groups": policy_groups,
        "tower_groups": tower_groups,
        "tower_visuals": tower_visuals,
        "bundles": _get_request_bundles(conn, client_id),
        "activities": activities,
        "activity_types": activity_types,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "project_notes": project_notes,
        "project_ids": project_ids,
        "project_addresses": project_addresses,
        "archived_policies": archived_policies,
        "client_scratchpad": client_scratchpad,
        "client_scratchpad_updated": client_scratchpad_updated,
        "client_saved_notes": client_saved_notes,
        "contacts": contacts,
        "team_contacts": team_contacts,
        "billing_accounts": [dict(r) for r in conn.execute(
            "SELECT * FROM billing_accounts WHERE client_id=? ORDER BY is_master DESC, billing_id",
            (client_id,),
        ).fetchall()],
        "opportunities": opportunities,
        "placement_colleagues": placement_colleagues,
        "mailto_subject": mailto_subject,
        "all_contacts_json": all_contacts_json,
        "add_contact": add_contact,
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": _get_all_client_contact_orgs(conn),
        "all_internal_contacts_json": _json.dumps({
            r["name"]: {"title": r["title"] or "", "email": r["email"] or "",
                        "phone": r["phone"] or "", "mobile": r["mobile"] or "", "role": r["role"] or ""}
            for r in conn.execute(
                """SELECT co.name, MAX(cca.title) AS title, MAX(co.email) AS email,
                          MAX(co.phone) AS phone, MAX(co.mobile) AS mobile, MAX(cca.role) AS role
                   FROM contacts co
                   JOIN contact_client_assignments cca ON co.id = cca.contact_id
                   WHERE cca.contact_type='internal' AND co.name IS NOT NULL AND co.name != ''
                   GROUP BY LOWER(TRIM(co.name)) ORDER BY co.name"""
            ).fetchall()
        }),
        "cycle_labels": _REVIEW_CYCLE_LABELS,
        "risks": risks,
        "risk_summary": _compute_risk_summary(risks),
        "risk_categories": risk_categories,
        "risk_severities": risk_severities,
        "risk_sources": cfg.get("risk_sources", []),
        "risk_control_types": cfg.get("risk_control_types", []),
        "risk_control_statuses": cfg.get("risk_control_statuses", []),
        "risk_adequacy_levels": cfg.get("risk_adequacy_levels", []),
        "policy_types": cfg.get("policy_types", []),
        "policy_uid_options": policy_uid_options,
        "linked_group": linked_group,
        "linked_relationships": cfg.get("linked_account_relationships", []),
        "pulse_overdue": pulse_overdue,
        "pulse_milestone_done": pulse_milestone_done,
        "pulse_milestone_total": pulse_milestone_total,
        "pulse_high_risks": pulse_high_risks,
        "pulse_recent": pulse_recent,
        "today": _today,
        "today_iso": _today,
    })


def _contacts_response(request, conn, client_id: int):
    """Shared helper: return the client contacts card partial with fresh data."""
    import json as _json
    contacts = get_client_contacts(conn, client_id, contact_type='client')
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)
    # All known contacts across all clients for autocomplete fill
    all_ac_rows = conn.execute(
        """SELECT co.name,
                  MAX(co.email)  AS email,
                  MAX(co.phone)  AS phone,
                  MAX(co.mobile) AS mobile,
                  MAX(cca.title) AS title,
                  MAX(cca.role)  AS role
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE cca.contact_type='client' AND co.name IS NOT NULL AND co.name != ''
           GROUP BY co.name ORDER BY co.name"""
    ).fetchall()
    all_contacts_json = _json.dumps({
        r["name"]: {"email": r["email"] or "", "phone": r["phone"] or "",
                    "mobile": r["mobile"] or "",
                    "title": r["title"] or "", "role": r["role"] or ""}
        for r in all_ac_rows
    })
    return templates.TemplateResponse("clients/_contacts.html", {
        "request": request,
        "client": dict(client) if client else {},
        "contacts": contacts,
        "mailto_subject": mailto_subject,
        "all_contacts_json": all_contacts_json,
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": _get_all_client_contact_orgs(conn),
    })


def _internal_contacts_response(request, conn, client_id: int):
    """Shared helper: return the internal team contacts card partial with fresh data."""
    import json as _json
    team_contacts = get_client_contacts(conn, client_id, contact_type='internal')
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens
    _mail_ctx = _client_ctx(conn, client_id)
    mailto_subject = _render_tokens(cfg.get("email_subject_client", "Re: {{client_name}}"), _mail_ctx)
    # Autocomplete: all internal contacts across all clients, deduped by name
    _ac_rows = conn.execute(
        """SELECT co.name,
                  MAX(cca.title) AS title,
                  MAX(co.email)  AS email,
                  MAX(co.phone)  AS phone,
                  MAX(co.mobile) AS mobile,
                  MAX(cca.role)  AS role
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           WHERE cca.contact_type='internal' AND co.name IS NOT NULL AND co.name != ''
           GROUP BY LOWER(TRIM(co.name)) ORDER BY co.name"""
    ).fetchall()
    all_internal_contacts_json = _json.dumps({
        r["name"]: {"title": r["title"] or "", "email": r["email"] or "",
                    "phone": r["phone"] or "", "mobile": r["mobile"] or "", "role": r["role"] or ""}
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
        "contact_roles": cfg.get("contact_roles", []),
        "add_contact": "",
    })


@router.patch("/{client_id}/contacts/{contact_id}/cell")
async def contact_cell(request: Request, client_id: int, contact_id: int, conn=Depends(get_db)):
    """Save a single cell value for a client contact (matrix edit).
    contact_id in URL = assignment_id in new schema."""
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"name", "title", "role", "email", "phone", "mobile", "notes", "organization"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""
    # Shared fields -> update contacts table; per-assignment fields -> update junction table
    if field in ("name", "email", "phone", "mobile", "organization"):
        row = conn.execute("SELECT contact_id FROM contact_client_assignments WHERE id=?", (contact_id,)).fetchone()
        if row:
            conn.execute(
                f"UPDATE contacts SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (formatted or None, row["contact_id"]),
            )
    else:
        # Per-assignment fields: role, title, notes
        conn.execute(
            f"UPDATE contact_client_assignments SET {field}=? WHERE id=? AND client_id=?",
            (formatted or None, contact_id, client_id),
        )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/{client_id}/contacts/add-row", response_class=HTMLResponse)
def contact_add_row(request: Request, client_id: int, conn=Depends(get_db)):
    """Create blank client contact row and return matrix row HTML."""
    cid = get_or_create_contact(conn, 'New Contact')
    cur = conn.execute(
        "INSERT INTO contact_client_assignments (contact_id, client_id, contact_type) VALUES (?, ?, 'client')",
        (cid, client_id),
    )
    conn.commit()
    c = {"id": cur.lastrowid, "contact_id": cid, "name": "New Contact", "title": None, "role": None,
         "email": None, "phone": None, "mobile": None, "notes": None,
         "organization": None, "is_primary": 0}
    return templates.TemplateResponse("clients/_contact_matrix_row.html", {
        "request": request, "c": c, "client": {"id": client_id},
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": _get_all_client_contact_orgs(conn),
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
    mobile: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    cid = get_or_create_contact(conn, name,
                                email=clean_email(email) or None,
                                phone=format_phone(phone) or None,
                                mobile=format_phone(mobile) or None)
    assign_contact_to_client(conn, cid, client_id, contact_type='client',
                             title=title or None, role=role or None, notes=notes or None)
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
    mobile: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    # contact_id = assignment_id; look up the real contact_id
    assignment = conn.execute(
        "SELECT contact_id FROM contact_client_assignments WHERE id=?", (contact_id,)
    ).fetchone()
    if assignment:
        # Update shared fields on the contacts table
        conn.execute(
            "UPDATE contacts SET name=?, email=?, phone=?, mobile=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, clean_email(email) or None, format_phone(phone) or None,
             format_phone(mobile) or None, assignment["contact_id"]),
        )
    # Update per-assignment fields on the junction table
    conn.execute(
        "UPDATE contact_client_assignments SET title=?, role=?, notes=? WHERE id=? AND client_id=?",
        (title or None, role or None, notes or None, contact_id, client_id),
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
    remove_contact_from_client(conn, contact_id)
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.post("/{client_id}/contacts/{contact_id}/set-primary", response_class=HTMLResponse)
def contact_set_primary_route(
    request: Request,
    client_id: int,
    contact_id: int,
    conn=Depends(get_db),
):
    set_primary_contact(conn, client_id, contact_id)
    conn.commit()
    return _contacts_response(request, conn, client_id)


@router.patch("/{client_id}/team/{contact_id}/cell")
async def team_contact_cell(request: Request, client_id: int, contact_id: int, conn=Depends(get_db)):
    """Save a single cell value for an internal team contact (matrix edit).
    contact_id in URL = assignment_id in new schema."""
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"name", "title", "role", "assignment", "email", "phone", "mobile"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""
    # Shared fields -> update contacts table; per-assignment fields -> update junction table
    if field in ("name", "email", "phone", "mobile"):
        row = conn.execute("SELECT contact_id FROM contact_client_assignments WHERE id=?", (contact_id,)).fetchone()
        if row:
            conn.execute(
                f"UPDATE contacts SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (formatted or None, row["contact_id"]),
            )
    else:
        # Per-assignment fields: role, title, assignment
        conn.execute(
            f"UPDATE contact_client_assignments SET {field}=? WHERE id=? AND client_id=?",
            (formatted or None, contact_id, client_id),
        )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/{client_id}/team/add-row", response_class=HTMLResponse)
def team_contact_add_row(request: Request, client_id: int, conn=Depends(get_db)):
    """Create blank internal team contact row and return matrix row HTML."""
    cid = get_or_create_contact(conn, 'New Contact')
    cur = conn.execute(
        "INSERT INTO contact_client_assignments (contact_id, client_id, contact_type) VALUES (?, ?, 'internal')",
        (cid, client_id),
    )
    conn.commit()
    c = {"id": cur.lastrowid, "contact_id": cid, "name": "New Contact", "title": None, "role": None,
         "assignment": None, "email": None, "phone": None, "mobile": None}
    return templates.TemplateResponse("clients/_team_matrix_row.html", {
        "request": request, "c": c, "client": {"id": client_id},
        "contact_roles": cfg.get("contact_roles", []),
    })


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
    mobile: str = Form(""),
    conn=Depends(get_db),
):
    cid = get_or_create_contact(conn, name,
                                email=clean_email(email) or None,
                                phone=format_phone(phone) or None,
                                mobile=format_phone(mobile) or None)
    assign_contact_to_client(conn, cid, client_id, contact_type='internal',
                             title=title or None, role=role or None,
                             assignment=assignment or None)
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
    mobile: str = Form(""),
    conn=Depends(get_db),
):
    # contact_id = assignment_id; look up the real contact_id
    asgn = conn.execute(
        "SELECT contact_id FROM contact_client_assignments WHERE id=?", (contact_id,)
    ).fetchone()
    if asgn:
        # Update shared fields on the contacts table
        conn.execute(
            "UPDATE contacts SET name=?, email=?, phone=?, mobile=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, clean_email(email) or None, format_phone(phone) or None,
             format_phone(mobile) or None, asgn["contact_id"]),
        )
    # Update per-assignment fields on the junction table
    conn.execute(
        "UPDATE contact_client_assignments SET title=?, role=?, assignment=? WHERE id=? AND client_id=?",
        (title or None, role or None, assignment or None, contact_id, client_id),
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
    remove_contact_from_client(conn, contact_id)
    conn.commit()
    return _internal_contacts_response(request, conn, client_id)


# ── Risk / Exposure Tracking ──────────────────────────────────────────────────

def _update_has_coverage(conn, risk_id: int):
    """Auto-derive has_coverage: 1 if any coverage line is Adequate, else 0."""
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM risk_coverage_lines WHERE risk_id=? AND adequacy='Adequate'",
        (risk_id,),
    ).fetchone()
    conn.execute(
        "UPDATE client_risks SET has_coverage=? WHERE id=?",
        (1 if row["cnt"] > 0 else 0, risk_id),
    )


def _policy_uid_options(conn, client_id: int):
    all_policies = [dict(r) for r in conn.execute(
        "SELECT policy_uid, policy_type FROM policies WHERE client_id=? AND archived=0 ORDER BY policy_type",
        (client_id,),
    ).fetchall()]
    return [{"uid": p["policy_uid"], "label": f"{p['policy_uid']} — {p['policy_type']}"} for p in all_policies]


def _compute_risk_summary(risks: list[dict]) -> dict:
    """Compute aggregate risk posture stats for visual widgets."""
    total = len(risks)
    covered = sum(1 for r in risks if r.get("has_coverage"))
    gap = total - covered
    needs_review = sum(1 for r in risks if any(
        cl.get("adequacy") == "Needs Review" for cl in r.get("coverage_lines", [])
    ))
    total_controls = sum(len(r.get("controls", [])) for r in risks)
    implemented_controls = sum(
        sum(1 for c in r.get("controls", []) if c.get("status") == "Implemented")
        for r in risks
    )
    by_severity = {}
    for r in risks:
        sev = r.get("severity", "Unknown")
        by_severity.setdefault(sev, 0)
        by_severity[sev] += 1
    # Score: weighted coverage ratio (0-100)
    score = int(covered / total * 100) if total else 0
    return {
        "score": score,
        "total": total,
        "covered": covered,
        "gap": gap,
        "needs_review": needs_review,
        "total_controls": total_controls,
        "implemented_controls": implemented_controls,
        "by_severity": by_severity,
    }


def _risks_response(request, conn, client_id: int):
    """Shared helper: return the risks card partial with fresh data."""
    risks = [dict(r) for r in conn.execute(
        """SELECT r.*, p.policy_type AS linked_policy_type, p.carrier AS linked_carrier
           FROM client_risks r
           LEFT JOIN policies p ON r.policy_uid = p.policy_uid
           WHERE r.client_id=?
           ORDER BY
             CASE r.severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
             r.category""",
        (client_id,),
    ).fetchall()]
    # Attach coverage lines and controls per risk
    for risk in risks:
        risk["coverage_lines"] = [dict(cl) for cl in conn.execute(
            "SELECT * FROM risk_coverage_lines WHERE risk_id=? ORDER BY coverage_line",
            (risk["id"],),
        ).fetchall()]
        risk["controls"] = [dict(c) for c in conn.execute(
            "SELECT * FROM risk_controls WHERE risk_id=? ORDER BY created_at",
            (risk["id"],),
        ).fetchall()]
    return templates.TemplateResponse("clients/_risks.html", {
        "request": request,
        "client": {"id": client_id},
        "risks": risks,
        "risk_summary": _compute_risk_summary(risks),
        "risk_categories": cfg.get("risk_categories", []),
        "risk_severities": cfg.get("risk_severities", []),
        "risk_sources": cfg.get("risk_sources", []),
        "risk_control_types": cfg.get("risk_control_types", []),
        "risk_control_statuses": cfg.get("risk_control_statuses", []),
        "risk_adequacy_levels": cfg.get("risk_adequacy_levels", []),
        "policy_types": cfg.get("policy_types", []),
        "policy_uid_options": _policy_uid_options(conn, client_id),
    })


@router.get("/{client_id}/risks", response_class=HTMLResponse)
def risks_card(request: Request, client_id: int, conn=Depends(get_db)):
    """Return the full risks card partial (used by Cancel buttons)."""
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/add", response_class=HTMLResponse)
def risk_add(
    request: Request,
    client_id: int,
    category: str = Form(...),
    description: str = Form(""),
    severity: str = Form("Medium"),
    source: str = Form(""),
    review_date: str = Form(""),
    has_coverage: int = Form(0),
    policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """INSERT INTO client_risks (client_id, category, description, severity, has_coverage, policy_uid, notes, source, review_date)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (client_id, category.strip(), description.strip() or None,
         severity, has_coverage, policy_uid.strip().upper() or None, notes.strip() or None,
         source.strip() or None, review_date.strip() or None),
    )
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.patch("/{client_id}/risks/{risk_id}/cell")
async def risk_cell_save(request: Request, client_id: int, risk_id: int, conn=Depends(get_db)):
    """Save a single cell value from the risk matrix."""
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")
    allowed = {"category", "description", "severity", "source", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    # Category and severity require a non-empty value
    if field in ("category", "severity") and not value.strip():
        return JSONResponse({"ok": False, "error": f"{field} cannot be empty"}, status_code=400)
    conn.execute(
        f"UPDATE client_risks SET {field}=? WHERE id=? AND client_id=?",
        (value.strip() or None, risk_id, client_id),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@router.post("/{client_id}/risks/add-row", response_class=HTMLResponse)
def risk_add_row(request: Request, client_id: int, conn=Depends(get_db)):
    """Create a new risk with defaults and return a single matrix row."""
    categories = cfg.get("risk_categories", [])
    default_cat = categories[0] if categories else "General"
    cur = conn.execute(
        """INSERT INTO client_risks (client_id, category, severity, has_coverage)
           VALUES (?,?,?,0)""",
        (client_id, default_cat, "Medium"),
    )
    conn.commit()
    risk_id = cur.lastrowid
    risk = dict(conn.execute("SELECT * FROM client_risks WHERE id=?", (risk_id,)).fetchone())
    risk["coverage_lines"] = []
    risk["controls"] = []
    return templates.TemplateResponse("clients/_risk_matrix_row.html", {
        "request": request,
        "client": {"id": client_id},
        "r": risk,
        "risk_categories": categories,
        "risk_severities": cfg.get("risk_severities", []),
        "risk_sources": cfg.get("risk_sources", []),
        "risk_control_types": cfg.get("risk_control_types", []),
        "risk_control_statuses": cfg.get("risk_control_statuses", []),
        "risk_adequacy_levels": cfg.get("risk_adequacy_levels", []),
        "policy_types": cfg.get("policy_types", []),
        "policy_uid_options": _policy_uid_options(conn, client_id),
    })


@router.get("/{client_id}/risks/{risk_id}/edit", response_class=HTMLResponse)
def risk_edit_form(request: Request, client_id: int, risk_id: int, conn=Depends(get_db)):
    risk = conn.execute("SELECT * FROM client_risks WHERE id=? AND client_id=?", (risk_id, client_id)).fetchone()
    if not risk:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("clients/_risk_row_edit.html", {
        "request": request,
        "client": {"id": client_id},
        "r": dict(risk),
        "risk_categories": cfg.get("risk_categories", []),
        "risk_severities": cfg.get("risk_severities", []),
        "risk_sources": cfg.get("risk_sources", []),
        "policy_uid_options": _policy_uid_options(conn, client_id),
    })


@router.post("/{client_id}/risks/{risk_id}/edit", response_class=HTMLResponse)
def risk_edit_save(
    request: Request,
    client_id: int,
    risk_id: int,
    category: str = Form(...),
    description: str = Form(""),
    severity: str = Form("Medium"),
    source: str = Form(""),
    review_date: str = Form(""),
    has_coverage: int = Form(0),
    policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """UPDATE client_risks SET category=?, description=?, severity=?, has_coverage=?, policy_uid=?, notes=?,
           source=?, review_date=?
           WHERE id=? AND client_id=?""",
        (category.strip(), description.strip() or None, severity,
         has_coverage, policy_uid.strip().upper() or None, notes.strip() or None,
         source.strip() or None, review_date.strip() or None,
         risk_id, client_id),
    )
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/toggle-coverage", response_class=HTMLResponse)
def risk_toggle_coverage(request: Request, client_id: int, risk_id: int, conn=Depends(get_db)):
    current = conn.execute(
        "SELECT has_coverage FROM client_risks WHERE id=? AND client_id=?", (risk_id, client_id)
    ).fetchone()
    if current:
        conn.execute(
            "UPDATE client_risks SET has_coverage=? WHERE id=? AND client_id=?",
            (0 if current["has_coverage"] else 1, risk_id, client_id),
        )
        conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/delete", response_class=HTMLResponse)
def risk_delete(request: Request, client_id: int, risk_id: int, conn=Depends(get_db)):
    conn.execute("DELETE FROM client_risks WHERE id=? AND client_id=?", (risk_id, client_id))
    conn.commit()
    return _risks_response(request, conn, client_id)


# ── Coverage Lines ────────────────────────────────────────────────────────────

@router.post("/{client_id}/risks/{risk_id}/lines/add", response_class=HTMLResponse)
def risk_line_add(
    request: Request,
    client_id: int,
    risk_id: int,
    coverage_line: str = Form(...),
    adequacy: str = Form("Needs Review"),
    policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """INSERT OR IGNORE INTO risk_coverage_lines (risk_id, coverage_line, adequacy, policy_uid, notes)
           VALUES (?,?,?,?,?)""",
        (risk_id, coverage_line.strip(), adequacy, policy_uid.strip().upper() or None, notes.strip() or None),
    )
    _update_has_coverage(conn, risk_id)
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/lines/{line_id}/edit", response_class=HTMLResponse)
def risk_line_edit(
    request: Request,
    client_id: int,
    risk_id: int,
    line_id: int,
    adequacy: str = Form("Needs Review"),
    policy_uid: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "UPDATE risk_coverage_lines SET adequacy=?, policy_uid=?, notes=? WHERE id=? AND risk_id=?",
        (adequacy, policy_uid.strip().upper() or None, notes.strip() or None, line_id, risk_id),
    )
    _update_has_coverage(conn, risk_id)
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/lines/{line_id}/delete", response_class=HTMLResponse)
def risk_line_delete(request: Request, client_id: int, risk_id: int, line_id: int, conn=Depends(get_db)):
    conn.execute("DELETE FROM risk_coverage_lines WHERE id=? AND risk_id=?", (line_id, risk_id))
    _update_has_coverage(conn, risk_id)
    conn.commit()
    return _risks_response(request, conn, client_id)


# ── Risk Controls ─────────────────────────────────────────────────────────────

@router.post("/{client_id}/risks/{risk_id}/controls/add", response_class=HTMLResponse)
def risk_control_add(
    request: Request,
    client_id: int,
    risk_id: int,
    control_type: str = Form(...),
    description: str = Form(...),
    status: str = Form("Recommended"),
    responsible: str = Form(""),
    target_date: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """INSERT INTO risk_controls (risk_id, control_type, description, status, responsible, target_date)
           VALUES (?,?,?,?,?,?)""",
        (risk_id, control_type.strip(), description.strip(),
         status, responsible.strip() or None, target_date.strip() or None),
    )
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/controls/{ctrl_id}/edit", response_class=HTMLResponse)
def risk_control_edit(
    request: Request,
    client_id: int,
    risk_id: int,
    ctrl_id: int,
    control_type: str = Form(...),
    description: str = Form(...),
    status: str = Form("Recommended"),
    responsible: str = Form(""),
    target_date: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        """UPDATE risk_controls SET control_type=?, description=?, status=?, responsible=?, target_date=?
           WHERE id=? AND risk_id=?""",
        (control_type.strip(), description.strip(), status,
         responsible.strip() or None, target_date.strip() or None,
         ctrl_id, risk_id),
    )
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.post("/{client_id}/risks/{risk_id}/controls/{ctrl_id}/delete", response_class=HTMLResponse)
def risk_control_delete(request: Request, client_id: int, risk_id: int, ctrl_id: int, conn=Depends(get_db)):
    conn.execute("DELETE FROM risk_controls WHERE id=? AND risk_id=?", (ctrl_id, risk_id))
    conn.commit()
    return _risks_response(request, conn, client_id)


@router.get("/{client_id}/team-cc")
def client_team_cc(client_id: int, project: str = "", conn=Depends(get_db)):
    """JSON list of team emails for CC — aggregated from policy contacts + internal team."""
    emails: list[dict] = []
    seen: set[str] = set()

    # Internal team members for this client
    internal = conn.execute(
        """SELECT co.name, co.email
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id=? AND cca.contact_type='internal'
             AND co.email IS NOT NULL AND TRIM(co.email) != ''""",
        (client_id,),
    ).fetchall()
    for r in internal:
        key = r["email"].strip().lower()
        if key not in seen:
            seen.add(key)
            emails.append({"name": r["name"], "email": r["email"]})

    # Policy contacts — optionally filtered by project/location
    if project:
        pc_rows = conn.execute(
            """SELECT DISTINCT co.name, co.email
               FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               JOIN policies p ON cpa.policy_id = p.id
               WHERE p.client_id = ? AND p.archived = 0
                 AND LOWER(TRIM(COALESCE(p.project_name, ''))) = LOWER(TRIM(?))
                 AND co.email IS NOT NULL AND TRIM(co.email) != ''""",
            (client_id, project),
        ).fetchall()
    else:
        pc_rows = conn.execute(
            """SELECT DISTINCT co.name, co.email
               FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               JOIN policies p ON cpa.policy_id = p.id
               WHERE p.client_id = ? AND p.archived = 0
                 AND co.email IS NOT NULL AND TRIM(co.email) != ''""",
            (client_id,),
        ).fetchall()
    for r in pc_rows:
        key = r["email"].strip().lower()
        if key not in seen:
            seen.add(key)
            emails.append({"name": r["name"], "email": r["email"]})

    return JSONResponse(emails)


@router.get("/{client_id}/edit", response_class=HTMLResponse)
def client_edit_form(request: Request, client_id: int, conn=Depends(get_db)):
    client = get_client_by_id(conn, client_id, include_archived=True)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    from policydb.queries import REVIEW_CYCLE_LABELS as _REVIEW_CYCLE_LABELS
    return templates.TemplateResponse("clients/edit.html", {
        "request": request,
        "active": "clients",
        "client": dict(client),
        "industry_segments": cfg.get("industry_segments"),
        "cycle_labels": _REVIEW_CYCLE_LABELS,
    })


@router.post("/{client_id}/edit")
def client_edit_post(
    request: Request,
    client_id: int,
    action: str = Form("save"),
    name: str = Form(...),
    industry_segment: str = Form(...),
    cn_number: str = Form(""),
    is_prospect: str = Form(""),
    primary_contact: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    contact_mobile: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    broker_fee: str = Form(""),
    business_description: str = Form(""),
    website: str = Form(""),
    renewal_month: str = Form(""),
    client_since: str = Form(""),
    preferred_contact_method: str = Form(""),
    referral_source: str = Form(""),
    fein: str = Form(""),
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

    old_row = dict(conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone())

    conn.execute(
        """UPDATE clients SET name=?, industry_segment=?, cn_number=?, is_prospect=?, primary_contact=?,
           contact_email=?, contact_phone=?, contact_mobile=?, address=?, notes=?,
           broker_fee=?, business_description=?,
           website=?, renewal_month=?, client_since=?, preferred_contact_method=?, referral_source=?,
           fein=?
           WHERE id=?""",
        (name, industry_segment, cn_number.strip() or None, 1 if is_prospect else 0,
         primary_contact or None, clean_email(contact_email) or None,
         format_phone(contact_phone) or None, format_phone(contact_mobile) or None,
         address or None, notes or None,
         _float(broker_fee), business_description or None,
         website or None, _int(renewal_month), client_since or None,
         preferred_contact_method or None, referral_source or None,
         format_fein(fein) or None,
         client_id),
    )
    conn.commit()

    _auto_review_fields = [
        "name", "industry_segment", "cn_number", "is_prospect", "primary_contact",
        "contact_email", "contact_phone", "contact_mobile", "address", "notes",
        "broker_fee", "business_description", "website", "renewal_month",
        "client_since", "preferred_contact_method", "referral_source", "fein",
    ]
    new_values = {
        "name": name, "industry_segment": industry_segment, "cn_number": cn_number,
        "is_prospect": is_prospect, "primary_contact": primary_contact,
        "contact_email": contact_email, "contact_phone": contact_phone,
        "contact_mobile": contact_mobile, "address": address, "notes": notes,
        "broker_fee": broker_fee, "business_description": business_description,
        "website": website, "renewal_month": renewal_month, "client_since": client_since,
        "preferred_contact_method": preferred_contact_method, "referral_source": referral_source,
        "fein": fein,
    }
    changed = count_changed_fields(old_row, new_values, _auto_review_fields)
    check_auto_review_client(conn, client_id, changed)

    if action == "autosave":
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/archive")
def client_archive(client_id: int, conn=Depends(get_db)):
    """Archive a client (soft delete — hidden from lists, data preserved)."""
    conn.execute("UPDATE clients SET archived=1 WHERE id=?", (client_id,))
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


@router.post("/{client_id}/unarchive")
def client_unarchive(client_id: int, conn=Depends(get_db)):
    """Restore an archived client."""
    conn.execute("UPDATE clients SET archived=0 WHERE id=?", (client_id,))
    conn.commit()
    return RedirectResponse(f"/clients/{client_id}", status_code=303)


# ── Billing Accounts ──────────────────────────────────────────────────────────

def _billing_accounts_response(request, conn, client_id: int):
    """Return rendered billing accounts partial."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM billing_accounts WHERE client_id=? ORDER BY is_master DESC, billing_id",
        (client_id,),
    ).fetchall()]
    client = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
    return templates.TemplateResponse("clients/_billing_accounts.html", {
        "request": request,
        "client": dict(client) if client else {"id": client_id},
        "billing_accounts": rows,
    })


@router.patch("/{client_id}/billing/{billing_id_row}/cell")
async def billing_account_cell(request: Request, client_id: int, billing_id_row: int, conn=Depends(get_db)):
    """Save a single cell value for a billing account (matrix edit)."""
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"billing_id", "description", "fein", "entity_name"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field == "fein":
        formatted = format_fein(formatted)
    conn.execute(
        f"UPDATE billing_accounts SET {field}=?, modified_at=datetime('now') WHERE id=? AND client_id=?",
        (formatted or None, billing_id_row, client_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/{client_id}/billing/add-row", response_class=HTMLResponse)
def billing_account_add_row(request: Request, client_id: int, conn=Depends(get_db)):
    """Create a blank billing account row and return matrix row HTML."""
    # Check if there's already a master — new rows default to non-master
    cur = conn.execute(
        "INSERT INTO billing_accounts (client_id, billing_id, is_master) VALUES (?, '', 0)",
        (client_id,),
    )
    conn.commit()
    row = {"id": cur.lastrowid, "billing_id": "", "entity_name": None, "fein": None, "description": None, "is_master": 0}
    return templates.TemplateResponse("clients/_billing_row.html", {
        "request": request, "b": row, "client": {"id": client_id},
    })


@router.post("/{client_id}/billing/{billing_id_row}/toggle-master", response_class=HTMLResponse)
def billing_account_toggle_master(request: Request, client_id: int, billing_id_row: int, conn=Depends(get_db)):
    """Toggle is_master flag on a billing account."""
    existing = conn.execute(
        "SELECT is_master FROM billing_accounts WHERE id=? AND client_id=?",
        (billing_id_row, client_id),
    ).fetchone()
    if not existing:
        return HTMLResponse("", status_code=404)
    # Clear all masters for this client, then set this one if it wasn't already
    conn.execute("UPDATE billing_accounts SET is_master=0, modified_at=datetime('now') WHERE client_id=?", (client_id,))
    if not existing["is_master"]:
        conn.execute("UPDATE billing_accounts SET is_master=1, modified_at=datetime('now') WHERE id=? AND client_id=?", (billing_id_row, client_id))
    conn.commit()
    return _billing_accounts_response(request, conn, client_id)


@router.post("/{client_id}/billing/{billing_id_row}/delete", response_class=HTMLResponse)
def billing_account_delete(request: Request, client_id: int, billing_id_row: int, conn=Depends(get_db)):
    """Delete a billing account."""
    conn.execute("DELETE FROM billing_accounts WHERE id=? AND client_id=?", (billing_id_row, client_id))
    conn.commit()
    return _billing_accounts_response(request, conn, client_id)


def _scratchpad_ctx(request, conn, client_id: int, content: str | None = None) -> dict:
    """Build context dict for the _scratchpad.html partial."""
    if content is None:
        row = conn.execute(
            "SELECT content, updated_at FROM client_scratchpad WHERE client_id=?", (client_id,)
        ).fetchone()
        content = row["content"] if row else ""
        updated = row["updated_at"] if row else ""
    else:
        updated_row = conn.execute(
            "SELECT updated_at FROM client_scratchpad WHERE client_id=?", (client_id,)
        ).fetchone()
        updated = updated_row["updated_at"] if updated_row else ""
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return {
        "request": request,
        "client": dict(client) if client else {},
        "client_scratchpad": content,
        "client_scratchpad_updated": updated,
        "client_saved_notes": get_saved_notes(conn, "client", str(client_id)),
    }


@router.post("/{client_id}/scratchpad")
def client_scratchpad_save(
    request: Request,
    client_id: int,
    content: str = Form(""),
    conn=Depends(get_db),
):
    """Auto-save per-client working notes. Returns JSON if Accept header requests it."""
    from datetime import datetime, timezone
    conn.execute(
        "INSERT INTO client_scratchpad (client_id, content) VALUES (?, ?) "
        "ON CONFLICT(client_id) DO UPDATE SET content=excluded.content",
        (client_id, content),
    )
    conn.commit()
    if "application/json" in (request.headers.get("accept") or ""):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return JSONResponse({"ok": True, "saved_at": now})
    return templates.TemplateResponse(
        "clients/_scratchpad.html", _scratchpad_ctx(request, conn, client_id, content)
    )


@router.post("/{client_id}/notes/save", response_class=HTMLResponse)
def client_note_save(request: Request, client_id: int, conn=Depends(get_db)):
    """Pin current scratchpad content as a saved note, then clear the scratchpad."""
    from datetime import date as _date
    row = conn.execute(
        "SELECT content FROM client_scratchpad WHERE client_id=?", (client_id,)
    ).fetchone()
    content = (row["content"] if row else "").strip()
    new_activity_id = None
    if content:
        save_note(conn, "client", str(client_id), content)
        # Also log to activity_log for unified account history
        account_exec = cfg.get("default_account_exec", "Grant")
        subject = content[:120] + ("…" if len(content) > 120 else "")
        cursor = conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, subject, details, account_exec)
               VALUES (?, ?, NULL, 'Note', ?, ?, ?)""",
            (_date.today().isoformat(), client_id, subject, content, account_exec),
        )
        new_activity_id = cursor.lastrowid
        conn.execute(
            "UPDATE client_scratchpad SET content = '' WHERE client_id = ?",
            (client_id,),
        )
        conn.commit()

    # Render scratchpad response
    scratchpad_resp = templates.TemplateResponse(
        "clients/_scratchpad.html", _scratchpad_ctx(request, conn, client_id)
    )

    # If we logged an activity, append an OOB swap to insert it at top of activity list
    if new_activity_id:
        a_row = conn.execute(
            """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id
               FROM activity_log a
               JOIN clients c ON a.client_id = c.id
               LEFT JOIN policies p ON a.policy_id = p.id
               WHERE a.id = ?""",
            (new_activity_id,),
        ).fetchone()
        if a_row:
            activity_html = templates.TemplateResponse(
                "activities/_activity_row.html", {"request": request, "a": dict(a_row)}
            ).body.decode()
            # OOB swap: prepend to activity list
            oob_html = f'<li hx-swap-oob="afterbegin:#activity-list">{activity_html}</li>'
            scratchpad_html = scratchpad_resp.body.decode()
            return HTMLResponse(scratchpad_html + oob_html)

    return scratchpad_resp


@router.delete("/{client_id}/notes/{note_id}", response_class=HTMLResponse)
def client_note_delete(request: Request, client_id: int, note_id: int, conn=Depends(get_db)):
    """Delete a saved note."""
    delete_saved_note(conn, note_id)
    return templates.TemplateResponse(
        "clients/_scratchpad.html", _scratchpad_ctx(request, conn, client_id)
    )


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


@router.get("/{client_id}/export/project")
def export_project(client_id: int, project: str = "", conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_project_group_xlsx
    client = get_client_by_id(conn, client_id)
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    content = export_project_group_xlsx(conn, client_id, project, client["name"])
    safe_client = client["name"].lower().replace(" ", "_")
    safe_project = (project or "corporate").lower().replace(" ", "_").replace("/", "_")[:30]
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_client}_{safe_project}_policies.xlsx"'},
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
    from policydb.web.routes.policies import US_STATES
    ctx = _project_note_ctx(conn, client_id, project)
    ctx["us_states"] = US_STATES
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


@router.post("/{client_id}/projects/{project_id}/notes")
def project_notes_autosave(
    client_id: int,
    project_id: int,
    content: str = Form(""),
    conn=Depends(get_db),
):
    from fastapi import HTTPException
    cur = conn.execute(
        "UPDATE projects SET notes = ? WHERE id = ? AND client_id = ?",
        (content, project_id, client_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    saved_at = babel_format_datetime(
        datetime.now(), "MMM d 'at' h:mma", locale="en_US"
    ).replace("AM", "am").replace("PM", "pm")
    return JSONResponse({"ok": True, "saved_at": saved_at})


@router.get("/{client_id}/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    client_id: int,
    project_id: int,
    request: Request,
    conn=Depends(get_db),
):
    from fastapi import HTTPException
    import dateparser
    project = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND client_id = ?",
        (project_id, client_id),
    ).fetchone()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    policies = conn.execute(
        """SELECT policy_uid, policy_type, carrier, renewal_status,
                  exposure_address, exposure_city, exposure_state, exposure_zip
           FROM policies
           WHERE project_id = ?
           ORDER BY policy_type""",
        (project_id,),
    ).fetchall()
    project = dict(project)
    # Pull address from first policy that has one
    for pol in policies:
        if pol["exposure_address"] or pol["exposure_city"]:
            project["exposure_address"] = pol["exposure_address"] or ""
            project["exposure_city"] = pol["exposure_city"] or ""
            project["exposure_state"] = pol["exposure_state"] or ""
            project["exposure_zip"] = pol["exposure_zip"] or ""
            break
    if project.get("updated_at"):
        try:
            dt = dateparser.parse(project["updated_at"])
            if dt:
                project["updated_at_fmt"] = babel_format_datetime(
                    dt, "MMM d 'at' h:mma", locale="en_US"
                ).replace("AM", "am").replace("PM", "pm")
        except Exception:
            project["updated_at_fmt"] = project["updated_at"][:16]
    return templates.TemplateResponse(
        "clients/project.html",
        {
            "request": request,
            "project": project,
            "client": dict(client),
            "policies": [dict(p) for p in policies],
        },
    )


# ── Project management: rename / merge / delete ──────────────────────────────


def _cleanup_orphan_projects(conn, client_id: int) -> None:
    """Delete project rows that have no policies linked by project_id."""
    conn.execute(
        """DELETE FROM projects
           WHERE client_id = ?
             AND id NOT IN (
               SELECT DISTINCT project_id FROM policies
               WHERE project_id IS NOT NULL AND client_id = ?
             )""",
        (client_id, client_id),
    )


@router.get("/{client_id}/project/rename-form", response_class=HTMLResponse)
def project_rename_form(request: Request, client_id: int, project: str, conn=Depends(get_db)):
    ctx = _project_note_ctx(conn, client_id, project)
    return templates.TemplateResponse("clients/_project_header_rename.html", {"request": request, **ctx})


@router.post("/{client_id}/project/rename")
def project_rename(
    request: Request,
    client_id: int,
    project_name: str = Form(...),
    new_name: str = Form(...),
    conn=Depends(get_db),
):
    new_name = new_name.strip()
    if not new_name:
        ctx = _project_note_ctx(conn, client_id, project_name)
        ctx["error"] = "Name cannot be empty."
        return templates.TemplateResponse("clients/_project_header_rename.html", {"request": request, **ctx})

    # Check for collision with a DIFFERENT project
    old_norm = project_name.strip().lower()
    new_norm = new_name.lower()
    if old_norm != new_norm:
        collision = conn.execute(
            "SELECT id FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = ?",
            (client_id, new_norm),
        ).fetchone()
        if collision:
            ctx = _project_note_ctx(conn, client_id, project_name)
            ctx["error"] = f"A project named '{new_name}' already exists. Use Merge instead."
            return templates.TemplateResponse("clients/_project_header_rename.html", {"request": request, **ctx})

    # Rename the project row
    conn.execute(
        "UPDATE projects SET name = ? WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (new_name, client_id, project_name),
    )
    # Update project_name on all policies (active + archived)
    conn.execute(
        "UPDATE policies SET project_name = ? WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))",
        (new_name, client_id, project_name),
    )
    conn.commit()
    return HTMLResponse("", headers={"HX-Redirect": f"/clients/{client_id}"})


@router.get("/{client_id}/project/merge-form", response_class=HTMLResponse)
def project_merge_form(request: Request, client_id: int, project: str, conn=Depends(get_db)):
    ctx = _project_note_ctx(conn, client_id, project)
    other = conn.execute(
        "SELECT name FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) != LOWER(TRIM(?)) ORDER BY name",
        (client_id, project),
    ).fetchall()
    ctx["other_projects"] = [r["name"] for r in other]
    return templates.TemplateResponse("clients/_project_header_merge.html", {"request": request, **ctx})


@router.post("/{client_id}/project/merge")
def project_merge(
    request: Request,
    client_id: int,
    source_project: str = Form(...),
    target_project: str = Form(...),
    conn=Depends(get_db),
):
    source = conn.execute(
        "SELECT id, notes FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, source_project),
    ).fetchone()
    target = conn.execute(
        "SELECT id, notes FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, target_project),
    ).fetchone()
    if not source or not target:
        return HTMLResponse("Project not found", status_code=404)

    # Concatenate notes
    src_notes = (source["notes"] or "").strip()
    tgt_notes = (target["notes"] or "").strip()
    if src_notes:
        merged = f"{tgt_notes}\n\n[Merged from {source_project}]: {src_notes}".strip()
        conn.execute("UPDATE projects SET notes = ? WHERE id = ?", (merged, target["id"]))

    # Move policies by project_id
    conn.execute(
        "UPDATE policies SET project_name = ?, project_id = ? WHERE client_id = ? AND project_id = ?",
        (target_project, target["id"], client_id, source["id"]),
    )
    # Catch strays (archived or unlinked)
    conn.execute(
        """UPDATE policies SET project_name = ?, project_id = ?
           WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))
             AND (project_id IS NULL OR project_id = ?)""",
        (target_project, target["id"], client_id, source_project, source["id"]),
    )
    _cleanup_orphan_projects(conn, client_id)
    conn.commit()
    return HTMLResponse("", headers={"HX-Redirect": f"/clients/{client_id}"})


@router.post("/{client_id}/project/delete")
def project_delete(
    request: Request,
    client_id: int,
    project_name: str = Form(...),
    conn=Depends(get_db),
):
    # Unassign all policies (moves to Corporate / Standalone)
    conn.execute(
        "UPDATE policies SET project_name = NULL, project_id = NULL WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))",
        (client_id, project_name),
    )
    conn.execute(
        "DELETE FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, project_name),
    )
    conn.commit()
    return HTMLResponse("", headers={"HX-Redirect": f"/clients/{client_id}"})


@router.get("/{client_id}/project/email-team")
def project_email_team(client_id: int, project: str, conn=Depends(get_db)):
    """Build a mailto: URL with all contacts for policies in a project."""
    from urllib.parse import quote
    # Gather emails from contact_policy_assignments for policies in this project
    rows = conn.execute(
        """SELECT DISTINCT co.email
           FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? AND LOWER(TRIM(COALESCE(p.project_name,''))) = LOWER(TRIM(?))
             AND co.email IS NOT NULL AND TRIM(co.email) != ''
             AND p.archived = 0""",
        (client_id, project),
    ).fetchall()
    emails = [r["email"] for r in rows]
    # Also include client contacts
    client_rows = conn.execute(
        """SELECT DISTINCT co.email
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ? AND co.email IS NOT NULL AND TRIM(co.email) != ''""",
        (client_id,),
    ).fetchall()
    emails.extend(r["email"] for r in client_rows)
    # Also include client's primary email
    client = conn.execute("SELECT contact_email FROM clients WHERE id = ?", (client_id,)).fetchone()
    if client and client["contact_email"]:
        emails.append(client["contact_email"])
    # Deduplicate (case-insensitive)
    seen: set[str] = set()
    unique: list[str] = []
    for e in emails:
        low = e.strip().lower()
        if low not in seen:
            seen.add(low)
            unique.append(e.strip())
    # Build subject from config
    client_row = get_client_by_id(conn, client_id)
    client_name = client_row["name"] if client_row else ""
    subject = f"Re: {client_name} — {project}"
    mailto = f"mailto:{','.join(unique)}?subject={quote(subject)}"
    from fastapi.responses import RedirectResponse as _RR
    return _RR(mailto, status_code=303)


@router.get("/{client_id}/project/log-form", response_class=HTMLResponse)
def project_log_form(request: Request, client_id: int, project: str, conn=Depends(get_db)):
    """HTMX partial: inline activity log form for all policies in a project."""
    ctx = _project_note_ctx(conn, client_id, project)
    ctx["activity_types"] = cfg.get("activity_types")
    # Get contacts for datalist
    contacts = conn.execute(
        """SELECT DISTINCT co.name FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? AND LOWER(TRIM(COALESCE(p.project_name,''))) = LOWER(TRIM(?))
             AND co.name IS NOT NULL AND TRIM(co.name) != '' AND p.archived = 0
           ORDER BY co.name""",
        (client_id, project),
    ).fetchall()
    ctx["contacts"] = [dict(r) for r in contacts]
    return templates.TemplateResponse("clients/_project_log_form.html", {"request": request, **ctx})


@router.post("/{client_id}/project/log", response_class=HTMLResponse)
def project_log_save(
    request: Request,
    client_id: int,
    project_name: str = Form(...),
    activity_type: str = Form(...),
    contact_person: str = Form(""),
    subject: str = Form(...),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Create an activity log entry for every active policy in a project."""
    from datetime import date
    policies = conn.execute(
        """SELECT id FROM policies
           WHERE client_id = ? AND LOWER(TRIM(COALESCE(project_name,''))) = LOWER(TRIM(?))
             AND archived = 0""",
        (client_id, project_name),
    ).fetchall()
    account_exec = cfg.get("default_account_exec", "")
    dur = None
    if duration_hours and duration_hours.strip():
        try:
            dur = float(duration_hours)
        except ValueError:
            dur = None
    today = date.today().isoformat()
    count = 0
    for p in policies:
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, contact_person,
                subject, details, follow_up_date, duration_hours, account_exec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, client_id, p["id"], activity_type, contact_person or None,
             subject, details or None, follow_up_date or None, dur, account_exec),
        )
        if follow_up_date:
            from policydb.queries import supersede_followups
            supersede_followups(conn, p["id"], follow_up_date)
        count += 1
    conn.commit()
    # Return the log form again with a success banner so user can continue editing
    ctx = _project_note_ctx(conn, client_id, project_name)
    ctx["activity_types"] = cfg.get("activity_types")
    contacts = conn.execute(
        """SELECT DISTINCT co.name FROM contact_policy_assignments cpa
           JOIN contacts co ON cpa.contact_id = co.id
           JOIN policies p ON cpa.policy_id = p.id
           WHERE p.client_id = ? AND LOWER(TRIM(COALESCE(p.project_name,''))) = LOWER(TRIM(?))
             AND co.name IS NOT NULL AND TRIM(co.name) != '' AND p.archived = 0
           ORDER BY co.name""",
        (client_id, project_name),
    ).fetchall()
    ctx["contacts"] = [dict(r) for r in contacts]
    ctx["log_success"] = f'Logged to {count} polic{"y" if count == 1 else "ies"} in {project_name}'
    return templates.TemplateResponse("clients/_project_log_form.html", {"request": request, **ctx})


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


# ─── LINKED ACCOUNTS ─────────────────────────────────────────────────────────


def _linked_accounts_ctx(request, conn, client_id: int) -> dict:
    """Build context dict for the _linked_accounts.html partial."""
    linked_group = get_linked_group_for_client(conn, client_id)
    client = get_client_by_id(conn, client_id, include_archived=True)
    return {
        "request": request,
        "client": dict(client) if client else {},
        "linked_group": linked_group,
        "linked_relationships": cfg.get("linked_account_relationships", []),
    }


@router.get("/{client_id}/linked", response_class=HTMLResponse)
def linked_accounts_partial(request: Request, client_id: int, conn=Depends(get_db)):
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


@router.get("/{client_id}/linked/overview", response_class=HTMLResponse)
def linked_overview_partial(request: Request, client_id: int, conn=Depends(get_db)):
    linked_group = get_linked_group_for_client(conn, client_id)
    if not linked_group:
        return HTMLResponse("")
    overview = get_linked_group_overview(conn, linked_group["group"]["id"])
    client = get_client_by_id(conn, client_id, include_archived=True)
    return templates.TemplateResponse("clients/_linked_overview.html", {
        "request": request,
        "client": dict(client) if client else {},
        "linked_group": linked_group,
        "overview": overview,
    })


@router.get("/{client_id}/linked/search", response_class=HTMLResponse)
def linked_search(request: Request, client_id: int, q: str = "", conn=Depends(get_db)):
    """Search for active clients to link. Allows merging across existing groups."""
    if not q or len(q) < 2:
        return HTMLResponse("")
    # Find which clients are already in THIS client's group (exclude them)
    my_group = get_linked_group_for_client(conn, client_id)
    my_group_ids = set()
    if my_group:
        my_group_ids = {m["client_id"] for m in my_group["members"]}
    all_clients = conn.execute(
        "SELECT id, name, cn_number, industry_segment FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    from rapidfuzz import fuzz
    results = []
    for c in all_clients:
        if c["id"] == client_id or c["id"] in my_group_ids:
            continue
        score = fuzz.WRatio(q.lower(), c["name"].lower())
        if score >= 50 or q.lower() in c["name"].lower():
            results.append(dict(c) | {"score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return templates.TemplateResponse("clients/_linked_search_results.html", {
        "request": request,
        "results": results[:10],
        "client_id": client_id,
        "linked_group": my_group,
    })


@router.post("/{client_id}/linked/create", response_class=HTMLResponse)
def linked_create(
    request: Request,
    client_id: int,
    target_client_id: int = Form(...),
    label: str = Form(""),
    relationship: str = Form("Related"),
    conn=Depends(get_db),
):
    my_group = get_linked_group_for_client(conn, client_id)
    target_group = get_linked_group_for_client(conn, target_client_id)
    if my_group and target_group and my_group["group"]["id"] != target_group["group"]["id"]:
        # Both in different groups — merge target's group into ours
        for m in target_group["members"]:
            add_client_to_group(conn, my_group["group"]["id"], m["client_id"])
        delete_linked_group(conn, target_group["group"]["id"])
    elif my_group:
        add_client_to_group(conn, my_group["group"]["id"], target_client_id)
    elif target_group:
        add_client_to_group(conn, target_group["group"]["id"], client_id)
    else:
        create_linked_group(conn, label, relationship, [client_id, target_client_id])
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


@router.post("/{client_id}/linked/add", response_class=HTMLResponse)
def linked_add(
    request: Request,
    client_id: int,
    target_client_id: int = Form(...),
    conn=Depends(get_db),
):
    linked_group = get_linked_group_for_client(conn, client_id)
    target_group = get_linked_group_for_client(conn, target_client_id)
    if linked_group and target_group and linked_group["group"]["id"] != target_group["group"]["id"]:
        # Merge: move all members from target's group into this client's group
        for m in target_group["members"]:
            add_client_to_group(conn, linked_group["group"]["id"], m["client_id"])
        delete_linked_group(conn, target_group["group"]["id"])
    elif linked_group:
        add_client_to_group(conn, linked_group["group"]["id"], target_client_id)
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


@router.post("/{client_id}/linked/{member_id}/remove", response_class=HTMLResponse)
def linked_remove(
    request: Request, client_id: int, member_id: int, conn=Depends(get_db)
):
    remove_client_from_group(conn, member_id)
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


@router.post("/{client_id}/linked/edit", response_class=HTMLResponse)
def linked_edit(
    request: Request,
    client_id: int,
    label: str = Form(""),
    relationship: str = Form("Related"),
    conn=Depends(get_db),
):
    linked_group = get_linked_group_for_client(conn, client_id)
    if linked_group:
        update_linked_group(conn, linked_group["group"]["id"], label, relationship)
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


@router.post("/{client_id}/linked/dissolve", response_class=HTMLResponse)
def linked_dissolve(
    request: Request, client_id: int, conn=Depends(get_db)
):
    linked_group = get_linked_group_for_client(conn, client_id)
    if linked_group:
        delete_linked_group(conn, linked_group["group"]["id"])
    return templates.TemplateResponse(
        "clients/_linked_accounts.html", _linked_accounts_ctx(request, conn, client_id)
    )


# ── Account Summary ──────────────────────────────────────────────────────────


@router.get("/{client_id}/summary", response_class=HTMLResponse)
def client_summary_panel(
    request: Request,
    client_id: int,
    include_linked: int = 0,
    days: int = 90,
    conn=Depends(get_db),
):
    """HTMX partial: account summary card."""
    from policydb.exporter import build_account_summary
    summary = build_account_summary(conn, client_id, days=days, include_linked=bool(include_linked))
    linked_group = None
    from policydb.queries import get_linked_group_for_client
    linked_group = get_linked_group_for_client(conn, client_id)
    return templates.TemplateResponse("clients/_account_summary.html", {
        "request": request,
        "s": summary,
        "client_id": client_id,
        "include_linked": bool(include_linked),
        "has_linked_group": linked_group is not None,
        "days": days,
    })


@router.get("/{client_id}/summary/text")
def client_summary_text(
    client_id: int,
    include_linked: int = 0,
    days: int = 90,
    conn=Depends(get_db),
):
    """Return plain text account summary for clipboard."""
    from policydb.exporter import build_account_summary, render_account_summary_text
    summary = build_account_summary(conn, client_id, days=days, include_linked=bool(include_linked))
    text = render_account_summary_text(summary)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(text)


# ── Request Tracker ─────


def _get_request_bundles(conn, client_id: int) -> list[dict]:
    """Fetch all request bundles for a client with item counts."""
    bundles = [dict(r) for r in conn.execute(
        "SELECT * FROM client_request_bundles WHERE client_id=? ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'sent' THEN 1 WHEN 'partial' THEN 2 ELSE 3 END, updated_at DESC",
        (client_id,),
    ).fetchall()]
    for b in bundles:
        items = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN received=1 THEN 1 ELSE 0 END) AS done FROM client_request_items WHERE bundle_id=?",
            (b["id"],),
        ).fetchone()
        b["item_total"] = items["total"] or 0
        b["item_done"] = items["done"] or 0
    return bundles


def _requests_response(request, conn, client_id: int):
    from datetime import date as _date
    bundles = _get_request_bundles(conn, client_id)
    client = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
    return templates.TemplateResponse("clients/_requests.html", {
        "request": request,
        "client": dict(client) if client else {"id": client_id, "name": ""},
        "bundles": bundles,
        "today_iso": _date.today().isoformat(),
    })


@router.get("/{client_id}/requests", response_class=HTMLResponse)
def get_requests(request: Request, client_id: int, conn=Depends(get_db)):
    return _requests_response(request, conn, client_id)


@router.post("/{client_id}/requests", response_class=HTMLResponse)
def create_request_bundle(
    request: Request,
    client_id: int,
    title: str = Form("Information Request"),
    send_by_date: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.db import next_rfi_uid
    rfi_uid = next_rfi_uid(conn, client_id)
    conn.execute(
        "INSERT INTO client_request_bundles (client_id, title, send_by_date, rfi_uid) VALUES (?, ?, ?, ?)",
        (client_id, title, send_by_date.strip() or None, rfi_uid),
    )
    bundle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # If a send-by date is set, create an activity_log follow-up so it surfaces in follow-ups
    if send_by_date.strip():
        from datetime import date as _date
        account_exec = cfg.get("default_account_exec", "")
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
               VALUES (?, ?, 'Task', ?, ?, ?)""",
            (
                _date.today().isoformat(),
                client_id,
                f"Send RFI: {rfi_uid} {title}",
                send_by_date.strip(),
                account_exec,
            ),
        )
    conn.commit()
    return _requests_response(request, conn, client_id)


def _enrich_request_items(conn, items: list[dict]) -> list[dict]:
    """Add policy_type and carrier to request items from the policies table."""
    for item in items:
        if item.get("policy_uid"):
            pol = conn.execute(
                "SELECT policy_type, carrier, project_name FROM policies WHERE policy_uid=?",
                (item["policy_uid"],),
            ).fetchone()
            if pol:
                item["policy_type"] = pol["policy_type"]
                item["carrier"] = pol["carrier"]
                if not item.get("project_name"):
                    item["project_name"] = pol["project_name"]
    return items


def _bundle_response(request, conn, client_id: int, bundle_id: int):
    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE id=? AND client_id=?",
        (bundle_id, client_id),
    ).fetchone()
    if not bundle:
        return HTMLResponse("Bundle not found", status_code=404)
    items = _enrich_request_items(conn, [dict(r) for r in conn.execute(
        "SELECT * FROM client_request_items WHERE bundle_id=? ORDER BY received ASC, sort_order ASC, id ASC",
        (bundle_id,),
    ).fetchall()])
    client = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
    # Get policies for this client (for linking items to policies)
    policies = [dict(r) for r in conn.execute(
        "SELECT policy_uid, policy_type, carrier, project_name, effective_date FROM policies WHERE client_id=? AND archived=0 ORDER BY policy_type, effective_date DESC",
        (client_id,),
    ).fetchall()]
    return templates.TemplateResponse("clients/_request_bundle.html", {
        "request": request,
        "client": dict(client) if client else {"id": client_id, "name": ""},
        "bundle": dict(bundle),
        "items": items,
        "policies": policies,
        "request_categories": cfg.get("request_categories", []),
    })


@router.get("/{client_id}/requests/policy-items")
def request_policy_items(client_id: int, policy_uid: str = "", conn=Depends(get_db)):
    """JSON: return all request items linked to a specific policy_uid for inline display."""
    if not policy_uid:
        return JSONResponse({"items": []})
    items = [dict(r) for r in conn.execute(
        """SELECT i.id, i.bundle_id, i.description, i.category, i.received, i.received_at, i.notes
           FROM client_request_items i
           JOIN client_request_bundles b ON i.bundle_id = b.id
           WHERE b.client_id = ? AND i.policy_uid = ?
           ORDER BY i.received ASC, i.id ASC""",
        (client_id, policy_uid),
    ).fetchall()]
    bundle_url = f"/clients/{client_id}"
    return JSONResponse({"items": items, "bundle_url": bundle_url})


@router.get("/{client_id}/requests/policy-view", response_class=HTMLResponse)
def request_policy_view(request: Request, client_id: int, policy_uid: str = "", conn=Depends(get_db)):
    """HTMX partial: server-rendered request items for a policy, with full card layout."""
    # Find the active bundle (or latest)
    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE client_id=? AND status IN ('open','sent','partial') ORDER BY updated_at DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    bundle_id = bundle["id"] if bundle else None

    items = []
    if bundle_id:
        items = _enrich_request_items(conn, [dict(r) for r in conn.execute(
            "SELECT * FROM client_request_items WHERE bundle_id=? AND policy_uid=? ORDER BY received ASC, sort_order ASC, id ASC",
            (bundle_id, policy_uid),
        ).fetchall()])

    client = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
    return templates.TemplateResponse("clients/_request_policy_view.html", {
        "request": request,
        "client": dict(client) if client else {"id": client_id, "name": ""},
        "bundle": dict(bundle) if bundle else None,
        "bundle_id": bundle_id,
        "items": items,
        "policy_uid": policy_uid,
        "request_categories": cfg.get("request_categories", []),
    })


@router.get("/{client_id}/requests/export-all")
def request_export_all(client_id: int, conn=Depends(get_db)):
    """Export all open request bundles as a multi-sheet XLSX."""
    from fastapi.responses import Response
    from policydb.exporter import export_client_requests_xlsx

    content = export_client_requests_xlsx(conn, client_id)
    client = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    client_name = client["name"] if client else "Client"
    from datetime import date
    filename = f"{client_name} - Outstanding Requests - {date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{client_id}/requests/compose-all", response_class=HTMLResponse)
def request_compose_all(request: Request, client_id: int, conn=Depends(get_db)):
    """Generate compose panel with all outstanding items across all open bundles."""
    from policydb.exporter import render_client_requests_compose_text
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens

    body_text = render_client_requests_compose_text(conn, client_id)
    mail_ctx = _client_ctx(conn, client_id)
    subject_tpl = cfg.get("email_subject_request_all", "{{client_name}} — Outstanding Information Requests")
    subject = _render_tokens(subject_tpl, mail_ctx)

    contacts = []
    _cc_rows = conn.execute(
        """SELECT co.name, co.email
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id=? AND cca.contact_type='client'
             AND co.email IS NOT NULL AND TRIM(co.email) != ''
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id,),
    ).fetchall()
    for c in _cc_rows:
        contacts.append({"name": c["name"], "email": c["email"]})

    return templates.TemplateResponse("clients/_request_compose.html", {
        "request": request,
        "client_id": client_id,
        "bundle": None,
        "subject": subject,
        "body_text": body_text,
        "contacts": contacts,
    })


@router.get("/{client_id}/requests/{bundle_id}", response_class=HTMLResponse)
def get_request_bundle(
    request: Request, client_id: int, bundle_id: int, conn=Depends(get_db)
):
    return _bundle_response(request, conn, client_id, bundle_id)


@router.post("/{client_id}/requests/{bundle_id}/items", response_class=HTMLResponse)
def add_request_item(
    request: Request,
    client_id: int,
    bundle_id: int,
    description: str = Form(...),
    policy_uid: str = Form(""),
    project_name: str = Form(""),
    category: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "INSERT INTO client_request_items (bundle_id, description, policy_uid, project_name, category) VALUES (?, ?, ?, ?, ?)",
        (bundle_id, description, policy_uid, project_name, category),
    )
    conn.commit()
    return _bundle_response(request, conn, client_id, bundle_id)


@router.post(
    "/{client_id}/requests/{bundle_id}/items/{item_id}/toggle",
    response_class=HTMLResponse,
)
def toggle_request_item(
    request: Request,
    client_id: int,
    bundle_id: int,
    item_id: int,
    conn=Depends(get_db),
):
    item = conn.execute(
        "SELECT * FROM client_request_items WHERE id=? AND bundle_id=?",
        (item_id, bundle_id),
    ).fetchone()
    if not item:
        return HTMLResponse("Item not found", status_code=404)
    new_received = 0 if item["received"] else 1
    if new_received:
        conn.execute(
            "UPDATE client_request_items SET received=1, received_at=CURRENT_TIMESTAMP WHERE id=?",
            (item_id,),
        )
    else:
        conn.execute(
            "UPDATE client_request_items SET received=0, received_at=NULL WHERE id=?",
            (item_id,),
        )
    # Auto-update bundle status based on item completion
    counts = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN received=1 THEN 1 ELSE 0 END) AS done FROM client_request_items WHERE bundle_id=?",
        (bundle_id,),
    ).fetchone()
    total = counts["total"] or 0
    done = counts["done"] or 0
    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE id=? AND client_id=?",
        (bundle_id, client_id),
    ).fetchone()
    if total > 0 and done == total:
        conn.execute(
            "UPDATE client_request_bundles SET status='complete', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )
    elif bundle and bundle["status"] == "complete":
        conn.execute(
            "UPDATE client_request_bundles SET status='partial', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )
    conn.commit()
    # Re-fetch updated item and bundle
    updated_item = dict(conn.execute(
        "SELECT * FROM client_request_items WHERE id=?", (item_id,)
    ).fetchone())
    _enrich_request_items(conn, [updated_item])
    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE id=? AND client_id=?",
        (bundle_id, client_id),
    ).fetchone()
    client = conn.execute("SELECT id, name FROM clients WHERE id=?", (client_id,)).fetchone()
    return templates.TemplateResponse("clients/_request_item.html", {
        "request": request,
        "item": updated_item,
        "bundle": dict(bundle),
        "client": dict(client) if client else {"id": client_id, "name": ""},
        "request_categories": cfg.get("request_categories", []),
    })


@router.patch("/{client_id}/requests/{bundle_id}/items/{item_id}")
async def edit_request_item(
    request: Request,
    client_id: int,
    bundle_id: int,
    item_id: int,
    conn=Depends(get_db),
):
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")
    allowed = {"description", "notes", "category", "policy_uid", "project_name"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    conn.execute(
        f"UPDATE client_request_items SET {field}=? WHERE id=? AND bundle_id=?",
        (value, item_id, bundle_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


@router.delete(
    "/{client_id}/requests/{bundle_id}/items/{item_id}",
    response_class=HTMLResponse,
)
def delete_request_item(
    request: Request,
    client_id: int,
    bundle_id: int,
    item_id: int,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM client_request_items WHERE id=? AND bundle_id=?",
        (item_id, bundle_id),
    )
    conn.commit()
    return HTMLResponse("")


@router.post(
    "/{client_id}/requests/{bundle_id}/status", response_class=HTMLResponse
)
def update_request_bundle_status(
    request: Request,
    client_id: int,
    bundle_id: int,
    status: str = Form(...),
    follow_up_date: str = Form(""),
    conn=Depends(get_db),
):
    from datetime import date as _date
    from policydb.utils import round_duration

    allowed_statuses = {"open", "sent", "partial", "complete"}
    if status not in allowed_statuses:
        return HTMLResponse("Invalid status", status_code=400)
    if status == "sent":
        conn.execute(
            "UPDATE client_request_bundles SET status=?, sent_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?",
            (status, bundle_id, client_id),
        )
        # Create an activity log entry so this shows in follow-ups
        bundle = conn.execute(
            "SELECT title, rfi_uid FROM client_request_bundles WHERE id=?", (bundle_id,)
        ).fetchone()
        counts = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN received=0 THEN 1 ELSE 0 END) AS outstanding FROM client_request_items WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()
        outstanding = counts["outstanding"] or 0
        total = counts["total"] or 0
        _rfi_tag = f"{bundle['rfi_uid']} " if bundle and bundle["rfi_uid"] else ""
        subject = f"Sent {_rfi_tag}{bundle['title'] if bundle else 'information request'} — {outstanding} of {total} items outstanding"
        account_exec = cfg.get("default_account_exec", "Grant")
        fu = follow_up_date.strip() or None
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, subject, details, follow_up_date, account_exec)
               VALUES (?, ?, NULL, 'Email', ?, ?, ?, ?)""",
            (_date.today().isoformat(), client_id, subject, None, fu, account_exec),
        )
    else:
        conn.execute(
            "UPDATE client_request_bundles SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?",
            (status, bundle_id, client_id),
        )
    conn.commit()
    return _requests_response(request, conn, client_id)


@router.post("/{client_id}/requests/quick-add", response_class=HTMLResponse)
def quick_add_request_item(
    request: Request,
    client_id: int,
    description: str = Form(...),
    policy_uid: str = Form(""),
    project_name: str = Form(""),
    category: str = Form(""),
    send_by_date: str = Form(""),
    conn=Depends(get_db),
):
    # Find the latest open bundle for this client, or create one
    bundle = conn.execute(
        "SELECT id FROM client_request_bundles WHERE client_id=? AND status='open' ORDER BY created_at DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    if not bundle:
        from policydb.db import next_rfi_uid
        sbd = send_by_date.strip() or None
        rfi_uid = next_rfi_uid(conn, client_id)
        conn.execute(
            "INSERT INTO client_request_bundles (client_id, title, send_by_date, rfi_uid) VALUES (?, 'Information Request', ?, ?)",
            (client_id, sbd, rfi_uid),
        )
        bundle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Create follow-up activity so the send deadline surfaces in follow-ups
        if sbd:
            from datetime import date as _date
            account_exec = cfg.get("default_account_exec", "")
            conn.execute(
                """INSERT INTO activity_log
                   (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
                   VALUES (?, ?, 'Task', ?, ?, ?)""",
                (_date.today().isoformat(), client_id, f"Send RFI: {rfi_uid}", sbd, account_exec),
            )
    else:
        bundle_id = bundle["id"]
    conn.execute(
        "INSERT INTO client_request_items (bundle_id, description, policy_uid, project_name, category) VALUES (?, ?, ?, ?, ?)",
        (bundle_id, description, policy_uid, project_name, category),
    )
    conn.commit()
    # Return the full policy-view partial so the list updates in place
    return request_policy_view(request, client_id, policy_uid, conn)


@router.post(
    "/{client_id}/requests/{bundle_id}/seed-from-checklist",
    response_class=HTMLResponse,
)
def seed_from_checklist(
    request: Request,
    client_id: int,
    bundle_id: int,
    conn=Depends(get_db),
):
    client_facing = cfg.get("client_facing_milestones", [])
    if not client_facing:
        return _bundle_response(request, conn, client_id, bundle_id)
    # Get all active non-opportunity policies for this client
    policies = conn.execute(
        "SELECT policy_uid, policy_type, carrier, project_name FROM policies WHERE client_id=? AND archived=0 AND (is_opportunity=0 OR is_opportunity IS NULL)",
        (client_id,),
    ).fetchall()
    for pol in policies:
        # For each client-facing milestone, check if it's incomplete (no row or completed=0)
        for ms_name in client_facing:
            existing_ms = conn.execute(
                "SELECT completed FROM policy_milestones WHERE policy_uid=? AND milestone=?",
                (pol["policy_uid"], ms_name),
            ).fetchone()
            # Skip if already completed
            if existing_ms and existing_ms["completed"]:
                continue
            # Build a descriptive item name
            desc = f"{ms_name} — {pol['policy_type']}"
            if pol["carrier"]:
                desc += f" ({pol['carrier']})"
            # Check if this item already exists in the bundle (avoid duplicates)
            existing_item = conn.execute(
                "SELECT id FROM client_request_items WHERE bundle_id=? AND description=? AND policy_uid=?",
                (bundle_id, desc, pol["policy_uid"]),
            ).fetchone()
            if existing_item:
                continue
            conn.execute(
                "INSERT INTO client_request_items (bundle_id, description, policy_uid, project_name, category) VALUES (?, ?, ?, ?, ?)",
                (bundle_id, desc, pol["policy_uid"], pol["project_name"] or "", ms_name.split()[0] if ms_name else "Other"),
            )
    conn.commit()
    return _bundle_response(request, conn, client_id, bundle_id)


@router.post("/{client_id}/requests/{bundle_id}/send-by", response_class=HTMLResponse)
def set_bundle_send_by(
    request: Request,
    client_id: int,
    bundle_id: int,
    send_by_date: str = Form(...),
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Set or update send_by_date on a bundle. Creates an activity follow-up."""
    sbd = send_by_date.strip() or None
    conn.execute(
        "UPDATE client_request_bundles SET send_by_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND client_id=?",
        (sbd, bundle_id, client_id),
    )
    if sbd:
        from datetime import date as _date
        bundle_row = conn.execute("SELECT title, rfi_uid FROM client_request_bundles WHERE id=?", (bundle_id,)).fetchone()
        title = bundle_row["title"] if bundle_row else "Information Request"
        rfi_uid = bundle_row["rfi_uid"] if bundle_row else ""
        account_exec = cfg.get("default_account_exec", "")
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
               VALUES (?, ?, 'Task', ?, ?, ?)""",
            (_date.today().isoformat(), client_id, f"Send RFI: {rfi_uid} {title}", sbd, account_exec),
        )
    conn.commit()
    if policy_uid:
        return request_policy_view(request, client_id, policy_uid, conn)
    return _bundle_response(request, conn, client_id, bundle_id)


@router.get("/{client_id}/requests/{bundle_id}/compose", response_class=HTMLResponse)
def request_bundle_compose(
    request: Request, client_id: int, bundle_id: int, conn=Depends(get_db)
):
    """Generate compose panel with formatted outstanding/received item list."""
    from policydb.exporter import render_request_compose_text
    from policydb.email_templates import client_context as _client_ctx, render_tokens as _render_tokens

    bundle = conn.execute(
        "SELECT * FROM client_request_bundles WHERE id=? AND client_id=?",
        (bundle_id, client_id),
    ).fetchone()
    if not bundle:
        return HTMLResponse("Bundle not found", status_code=404)

    body_text = render_request_compose_text(conn, bundle_id)
    mail_ctx = _client_ctx(conn, client_id)
    subject_tpl = cfg.get("email_subject_request", "{{client_name}} — {{request_title}}")
    mail_ctx["request_title"] = bundle["title"]
    mail_ctx["rfi_uid"] = bundle["rfi_uid"] or ""
    subject = _render_tokens(subject_tpl, mail_ctx)

    # Load contacts for recipient picker
    contacts = []
    _cc_rows2 = conn.execute(
        """SELECT co.name, co.email
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id=? AND cca.contact_type='client'
             AND co.email IS NOT NULL AND TRIM(co.email) != ''
           ORDER BY cca.is_primary DESC, co.name""",
        (client_id,),
    ).fetchall()
    for c in _cc_rows2:
        contacts.append({"name": c["name"], "email": c["email"]})

    return templates.TemplateResponse("clients/_request_compose.html", {
        "request": request,
        "client_id": client_id,
        "bundle": dict(bundle),
        "subject": subject,
        "body_text": body_text,
        "contacts": contacts,
    })


@router.get("/{client_id}/requests/{bundle_id}/export")
def request_bundle_export(client_id: int, bundle_id: int, conn=Depends(get_db)):
    """Export a request bundle as XLSX."""
    from fastapi.responses import Response
    from policydb.exporter import export_request_bundle_xlsx

    bundle = conn.execute(
        "SELECT title FROM client_request_bundles WHERE id=? AND client_id=?",
        (bundle_id, client_id),
    ).fetchone()
    if not bundle:
        return HTMLResponse("Bundle not found", status_code=404)

    content = export_request_bundle_xlsx(conn, bundle_id)
    client = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    client_name = client["name"] if client else "Client"
    from datetime import date
    filename = f"{client_name} - {bundle['title']} - {date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
