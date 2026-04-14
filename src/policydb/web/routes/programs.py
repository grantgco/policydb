"""Program Schematic Entry routes."""

from __future__ import annotations

import logging
import sqlite3

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import policydb.config as cfg
from policydb.charts import _layer_notation, get_tower_data
from policydb.db import next_program_uid, next_policy_uid
from policydb.utils import parse_currency_with_magnitude, clean_email, format_phone
from policydb.queries import (
    get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id,
    get_program_by_uid, get_program_child_policies, get_program_aggregates,
    get_unassigned_policies, get_programs_for_project,
    get_program_timeline_milestones, get_program_activities,
    renew_policy,
    get_or_create_contact, get_program_contacts, assign_contact_to_program,
    remove_contact_from_program, set_program_placement_colleague,
    get_program_underwriter_rollup,
    get_program_rollup,
    get_linked_policies_for_program,
    get_scoped_rfi_bundles,
)
from policydb.web.app import get_db, templates

logger = logging.getLogger("policydb.programs")
router = APIRouter(tags=["programs"])

# ===========================================================================
# PROGRAMS v2 — Standalone entity routes (programs table)
# ===========================================================================


def _sync_policy_to_program_location(
    conn: sqlite3.Connection, policy_uid: str, program: dict
) -> None:
    """Sync a policy's location to match its program's project/location.

    Sets project_id, project_name, and exposure address fields on the policy
    from the program's linked project. Uses COALESCE for address fields so
    blank project addresses don't overwrite existing policy data.
    """
    if not program.get("project_id"):
        return
    project = conn.execute(
        "SELECT id, name, address, city, state, zip FROM projects WHERE id = ?",
        (program["project_id"],),
    ).fetchone()
    if not project:
        return
    conn.execute(
        """UPDATE policies SET
           project_id = ?,
           project_name = ?,
           exposure_address = COALESCE(NULLIF(?, ''), exposure_address),
           exposure_city = COALESCE(NULLIF(?, ''), exposure_city),
           exposure_state = COALESCE(NULLIF(?, ''), exposure_state),
           exposure_zip = COALESCE(NULLIF(?, ''), exposure_zip)
           WHERE policy_uid = ?""",
        (
            project["id"],
            project["name"],
            project["address"] or "",
            project["city"] or "",
            project["state"] or "",
            project["zip"] or "",
            policy_uid,
        ),
    )


@router.post("/clients/{client_id}/programs/create")
async def create_program_v2(
    request: Request,
    client_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create a program in the new standalone programs table."""
    body = await request.json()
    name = (body.get("name") or "").strip()
    lob = (body.get("lob") or "").strip()

    if not name:
        return JSONResponse({"ok": False, "error": "Program name is required"}, status_code=400)

    # Check for duplicate in new programs table
    existing = conn.execute(
        "SELECT id FROM programs WHERE client_id = ? AND name = ? AND archived = 0 LIMIT 1",
        (client_id, name),
    ).fetchone()
    if existing:
        return JSONResponse({"ok": False, "error": f"Program '{name}' already exists"}, status_code=409)

    project_id = body.get("project_id")
    if project_id is not None:
        project_id = int(project_id) if project_id else None

    uid = next_program_uid(conn)
    conn.execute(
        """INSERT INTO programs (program_uid, client_id, name, line_of_business, project_id)
           VALUES (?, ?, ?, ?, ?)""",
        (uid, client_id, name, lob, project_id),
    )
    conn.commit()
    logger.info("Created program v2 '%s' (%s) for client %d", name, uid, client_id)

    return JSONResponse({"ok": True, "redirect": f"/programs/{uid}"})


@router.get("/programs/{program_uid}/rfi", response_class=HTMLResponse)
def program_rfi_scoped(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Filtered RFI list scoped to all child policies of a program."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (program["client_id"],)
    ).fetchone()
    linked = get_linked_policies_for_program(conn, program["id"])
    scope_uids = [p["policy_uid"] for p in linked if p.get("policy_uid")]
    bundles = get_scoped_rfi_bundles(conn, program["client_id"], scope_uids) if scope_uids else []
    return templates.TemplateResponse("clients/rfi_scoped.html", {
        "request": request,
        "active": "clients",
        "client": dict(client) if client else {"id": program["client_id"], "name": ""},
        "bundles": bundles,
        "scope_label": f"Program {program['program_uid']} — {program['name']}",
        "scope_back_url": f"/programs/{program['program_uid']}",
        "scope_back_label": "Back to program",
        "scope_policies": linked,
        "today_iso": date.today().isoformat(),
        "request_categories": cfg.get("request_categories", []),
    })


@router.get("/programs/{program_uid}", response_class=HTMLResponse)
def program_detail(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Program detail page with 4 tabs."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)

    client = conn.execute(
        "SELECT id, name, cn_number FROM clients WHERE id = ?",
        (program["client_id"],),
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)

    agg = get_program_aggregates(conn, program["id"])
    renewal_statuses = cfg.get("renewal_statuses", [])

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    # Client locations for the location picker
    client_locations = [
        dict(r) for r in conn.execute(
            "SELECT id, name FROM projects WHERE client_id = ? ORDER BY name",
            (program["client_id"],),
        ).fetchall()
    ]

    # Count child policies (for move-children prompt)
    child_count = conn.execute(
        "SELECT COUNT(*) FROM policies WHERE program_id = ? AND archived = 0",
        (program["id"],),
    ).fetchone()[0]

    return templates.TemplateResponse("programs/detail.html", {
        "request": request,
        "active": "clients",
        "program": program,
        "client": dict(client),
        "aggregates": agg,
        "renewal_statuses": renewal_statuses,
        "issue_severities": cfg.get("issue_severities", []),
        "all_clients": all_clients,
        "client_locations": client_locations,
        "child_count": child_count,
    })


@router.get("/programs/{program_uid}/tab/overview", response_class=HTMLResponse)
def program_tab_overview(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Overview tab: child policies, assign/unassign."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    children = get_program_child_policies(conn, program["id"])

    # Attach sub-coverage info for ghost rows
    child_ids = [c["id"] for c in children]
    sub_cov_map = get_sub_coverages_full_by_policy_id(conn, child_ids) if child_ids else {}
    for child in children:
        child["sub_coverages"] = sub_cov_map.get(child["id"], [])

    unassigned = get_unassigned_policies(conn, program["client_id"])
    agg = get_program_aggregates(conn, program["id"])

    # Fetch open issues linked to this program
    program_issues = [dict(r) for r in conn.execute(
        """SELECT id, issue_uid, subject, issue_severity, issue_status, activity_date
           FROM activity_log
           WHERE item_kind = 'issue'
             AND program_id = ?
             AND issue_status NOT IN ('Resolved', 'Closed')
           ORDER BY CASE issue_severity
               WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
               WHEN 'Normal' THEN 3 ELSE 4 END,
             activity_date ASC""",
        (program["id"],),
    ).fetchall()]

    # Client name for slideover context
    client_row = conn.execute(
        "SELECT name FROM clients WHERE id = ?", (program["client_id"],)
    ).fetchone()
    client_name = client_row["name"] if client_row else ""

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    # Renewal issue for this program
    renewal_issue = conn.execute("""
        SELECT id, issue_uid, issue_status, issue_severity, is_renewal_issue,
               julianday(date('now')) - julianday(activity_date) AS days_open,
               (SELECT COUNT(*) FROM activity_log sub WHERE sub.issue_id = a.id) AS activity_count
        FROM activity_log a
        WHERE a.is_renewal_issue = 1
          AND a.renewal_term_key = ?
          AND a.issue_status NOT IN ('Resolved', 'Closed')
        LIMIT 1
    """, (f"program:{program_uid}",)).fetchone()

    program_rollup = get_program_rollup(conn, program["id"])

    return templates.TemplateResponse("programs/_tab_overview.html", {
        "request": request,
        "program": program,
        "children": children,
        "unassigned": unassigned,
        "aggregates": agg,
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "policy_types": cfg.get("policy_types", []),
        "carriers": cfg.get("carriers", []),
        "activity_types": cfg.get("activity_types", []),
        "program_issues": program_issues,
        "client_name": client_name,
        "all_clients": all_clients,
        "issue_severities": cfg.get("issue_severities", []),
        "renewal_issue": dict(renewal_issue) if renewal_issue else None,
        "program_rollup": program_rollup,
        "rollup_client_id": program["client_id"],
    })


@router.get("/programs/{program_uid}/pipeline-children", response_class=HTMLResponse)
def program_pipeline_children(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return compact child policy list for renewal pipeline expand row."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("", status_code=404)
    children = get_program_child_policies(conn, program["id"])
    return templates.TemplateResponse("policies/_program_pipeline_children.html", {
        "request": request,
        "children": children,
        "program_uid": program_uid,
    })


@router.get("/programs/{program_uid}/tab/schematic", response_class=HTMLResponse)
def program_tab_schematic(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Schematic tab: delegates to existing schematic data loading."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    client_id = program["client_id"]
    program_id = program["id"]
    tg = program["name"]  # kept for template context (tower_group display label)

    # Query child policies via program_id FK
    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
                  p.limit_amount, p.deductible, p.premium, p.coverage_form,
                  p.layer_position, p.attachment_point, p.participation_of,
                  p.schematic_column, p.is_program, p.tower_group,
                  p.effective_date, p.expiration_date, p.renewal_status
           FROM policies p
           WHERE p.program_id = ?
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           ORDER BY p.layer_position, p.schematic_column, p.policy_type""",
        (program_id,),
    ).fetchall()
    policies = [dict(r) for r in rows]

    # Normalize layer_position: treat NULL, empty, "0", or any non-Umbrella/Excess as Primary
    _EXCESS_POSITIONS = {"Umbrella", "Excess"}
    for p in policies:
        lp = (p.get("layer_position") or "").strip()
        if lp not in _EXCESS_POSITIONS and lp != "Primary":
            p["layer_position"] = "Primary"
    underlying = [p for p in policies if p["layer_position"] == "Primary"]
    excess = [p for p in policies if p["layer_position"] in _EXCESS_POSITIONS]

    # Attach sub-coverage data
    all_ids = [p["id"] for p in policies]
    sub_cov_map = get_sub_coverages_by_policy_id(conn, all_ids)
    sub_cov_full_map = get_sub_coverages_full_by_policy_id(conn, all_ids)
    for p in policies:
        subs = sub_cov_map.get(p["id"], [])
        p["sub_coverages"] = subs
        p["sub_coverages_full"] = sub_cov_full_map.get(p["id"], [])
        p["is_package_ghost"] = bool(subs)

    # Promote umbrella/excess sub-coverages from underlying packages to excess
    # Note: promote even if limit_amount is not yet set — the ghost row should appear
    # so users can see the sub-coverage in the schematic and fill in details later
    _EXCESS_SC_KEYWORDS = ("umbrella", "excess")
    for p in list(underlying):
        for sc in p.get("sub_coverages_full", []):
            sc_type = (sc.get("coverage_type") or "").lower()
            if any(kw in sc_type for kw in _EXCESS_SC_KEYWORDS):
                excess.append({
                    "id": p["id"], "policy_uid": p["policy_uid"],
                    "policy_type": sc["coverage_type"],
                    "carrier": sc.get("carrier") or p.get("carrier") or "",
                    "policy_number": sc.get("policy_number") or p.get("policy_number") or "",
                    "limit_amount": sc.get("limit_amount") or 0, "deductible": sc.get("deductible") or 0,
                    "premium": sc.get("premium") or 0, "coverage_form": sc.get("coverage_form") or "",
                    "layer_position": "Umbrella" if "umbrella" in sc_type else "Excess",
                    "attachment_point": sc.get("attachment_point") or 0,
                    "participation_of": sc.get("participation_of"), "schematic_column": None,
                    "is_program": 0, "tower_group": tg,
                    "package_parent_type": p.get("policy_type") or "",
                    "is_package_ghost": True, "sub_coverages": [], "sub_coverages_full": [],
                    "_from_sub_coverage": True, "_sub_coverage_id": sc["id"],
                    "effective_date": p.get("effective_date") or "",
                    "expiration_date": p.get("expiration_date") or "",
                    "renewal_status": p.get("renewal_status") or "",
                })

    has_umbrella = any(p.get("layer_position") == "Umbrella" and not p.get("_from_sub_coverage") for p in excess)

    # Compute notations
    for p in excess:
        lim = p.get("limit_amount") or 0
        att = p.get("attachment_point") or 0
        po = p.get("participation_of")
        p["notation"] = _layer_notation(lim, att, po) if lim else ""

    # Get tower coverage map
    coverage_map = {}
    if excess:
        cov_rows = conn.execute(
            """SELECT ptc.* FROM program_tower_coverage ptc
               JOIN policies p ON p.id = ptc.excess_policy_id
               WHERE p.program_id = ?""",
            (program_id,),
        ).fetchall()
        for cr in cov_rows:
            coverage_map.setdefault(cr["excess_policy_id"], []).append(dict(cr))

    # Build available tower lines dynamically from underlying policies + subcoverages
    available_lines = []
    for p in underlying:
        if p.get("is_program"):
            continue
        # Add the policy itself as a line
        available_lines.append({
            "policy_id": p["id"],
            "sub_coverage_id": None,
            "label": p.get("policy_type") or p.get("policy_uid") or "Unknown",
        })
        # Add non-excess/umbrella subcoverages as separate lines
        for sc in p.get("sub_coverages_full", []):
            sc_type = (sc.get("coverage_type") or "").lower()
            if not any(kw in sc_type for kw in _EXCESS_SC_KEYWORDS):
                available_lines.append({
                    "policy_id": p["id"],
                    "sub_coverage_id": sc["id"],
                    "label": sc.get("coverage_type") or "Sub-coverage",
                })

    # Attach covered_lines from coverage_map to each excess row
    for p in excess:
        p["covered_lines"] = coverage_map.get(p["id"], [])

    # Unassigned policies
    unassigned = get_unassigned_policies(conn, client_id)

    # Program policy (is_program=1 row, if it exists)
    program_policy = next((p for p in policies if p.get("is_program")), None)

    total_premium = sum((p.get("premium") or 0) for p in underlying + excess
                        if not p.get("is_package_ghost") and not p.get("is_program"))
    total_limit = sum((p.get("limit_amount") or 0) for p in underlying
                      if not p.get("is_package_ghost") and not p.get("is_program"))

    client_name = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()

    return templates.TemplateResponse("programs/_tab_schematic.html", {
        "request": request,
        "client_id": client_id,
        "client_name": client_name["name"] if client_name else "",
        "tower_group": tg,
        "program_uid": program_uid,
        "underlying": underlying,
        "excess": excess,
        "has_umbrella": has_umbrella,
        "policy_types": cfg.get("policy_types", []),
        "carriers": cfg.get("carriers", []),
        "coverage_forms": cfg.get("coverage_forms", []),
        "program_policy": program_policy,
        "total_premium": total_premium,
        "total_limit": total_limit,
        "unassigned": unassigned,
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "available_lines": available_lines,
        "coverage_map": coverage_map,
    })


@router.get("/programs/{program_uid}/tab/timeline", response_class=HTMLResponse)
def program_tab_timeline(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Timeline tab: milestones from child policies."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    milestones = get_program_timeline_milestones(conn, program["id"])

    return templates.TemplateResponse("programs/_tab_timeline.html", {
        "request": request,
        "program": program,
        "milestones": milestones,
    })


@router.get("/programs/{program_uid}/tab/activity", response_class=HTMLResponse)
def program_tab_activity(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Activity tab: recent activities from child policies."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    activities = get_program_activities(conn, program["id"])

    # Client name for escalate → issue slideover
    client_row = conn.execute(
        "SELECT name FROM clients WHERE id = ?", (program["client_id"],)
    ).fetchone()
    client_name = client_row["name"] if client_row else ""

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    return templates.TemplateResponse("programs/_tab_activity.html", {
        "request": request,
        "program": program,
        "activities": activities,
        "client_name": client_name,
        "all_clients": all_clients,
        "issue_severities": cfg.get("issue_severities", []),
        "activity_types": cfg.get("activity_types", []),
    })


# ── Contacts tab ───────────────────────────────────────────────────────────

@router.get("/programs/{program_uid}/tab/contacts", response_class=HTMLResponse)
def program_tab_contacts(request: Request, program_uid: str, conn=Depends(get_db)):
    """Contacts tab: program team matrix + underwriter rollup + correspondence."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    program_contacts = get_program_contacts(conn, program["id"])

    # Attach expertise tags
    _pc_ids = [c["contact_id"] for c in program_contacts if c.get("contact_id")]
    if _pc_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in program_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])

    # Underwriter rollup from child policies
    underwriters = get_program_underwriter_rollup(conn, program["id"])

    # Autocomplete data for contact name combobox
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
    import json as _json_mod
    all_contacts_for_ac_json = _json_mod.dumps({
        r["name"]: {
            "email": r["email"] or "", "role": r["role"] or "",
            "phone": r["phone"] or "", "mobile": r["mobile"] or "",
            "title": r["title"] or "", "organization": r["organization"] or "",
        } for r in _ac_rows
    })

    # Mailto subject
    from policydb.email_templates import render_tokens as _rtk
    _ctx = {"client_name": "", "program_name": program["name"] or ""}
    client_row = conn.execute("SELECT name FROM clients WHERE id=?", (program["client_id"],)).fetchone()
    if client_row:
        _ctx["client_name"] = client_row["name"]
    mailto_subject = _rtk(
        cfg.get("email_subject_program", "Re: {{client_name}} — {{program_name}}"),
        _ctx,
    )

    # Activity clusters for correspondence section
    _cluster_days = cfg.get("activity_cluster_days", 7)
    _all_acts = [dict(r) for r in conn.execute(
        """SELECT activity_date, activity_type, subject, disposition, details,
                  duration_hours, follow_up_done
           FROM activity_log WHERE program_id = ?
           ORDER BY activity_date DESC, id DESC""",
        (program["id"],),
    ).fetchall()]
    # Build clusters
    activity_clusters: list[list[dict]] = []
    if _all_acts:
        import dateutil.parser as _dp
        current_cluster: list[dict] = [_all_acts[0]]
        for act in _all_acts[1:]:
            prev_date = current_cluster[-1].get("activity_date") or ""
            curr_date = act.get("activity_date") or ""
            try:
                gap = abs((_dp.parse(prev_date) - _dp.parse(curr_date)).days) if prev_date and curr_date else 999
            except Exception:
                gap = 999
            if gap <= _cluster_days:
                current_cluster.append(act)
            else:
                activity_clusters.append(current_cluster)
                current_cluster = [act]
        activity_clusters.append(current_cluster)

    return templates.TemplateResponse("programs/_tab_contacts.html", {
        "request": request,
        "program": program,
        "program_contacts": program_contacts,
        "underwriters": underwriters,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "mailto_subject": mailto_subject,
        "activity_clusters": activity_clusters,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute(
            "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
        ).fetchall()}),
    })


@router.get("/programs/{program_uid}/tab/files", response_class=HTMLResponse)
def program_tab_files(request: Request, program_uid: str, conn=Depends(get_db)):
    """Files tab: universal attachment panel for program."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    return templates.TemplateResponse("programs/_tab_files.html", {
        "request": request,
        "program": program,
    })


# ── Program Contact CRUD ───────────────────────────────────────────────────


def _program_team_response(request, conn, program_uid: str):
    """Return rendered _program_team.html partial (+ underwriter rollup)."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)

    program_contacts = get_program_contacts(conn, program["id"])

    # Attach expertise tags
    _pc_ids = [c["contact_id"] for c in program_contacts if c.get("contact_id")]
    if _pc_ids:
        _exp_rows = conn.execute(
            f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(_pc_ids))})",
            _pc_ids,
        ).fetchall()
        _exp_map: dict = {}
        for _er in _exp_rows:
            _exp_map.setdefault(_er["contact_id"], {"line": [], "industry": []})
            _exp_map[_er["contact_id"]][_er["category"]].append(_er["tag"])
        for _pc in program_contacts:
            _cid = _pc.get("contact_id")
            _pc["expertise_lines"] = _exp_map.get(_cid, {}).get("line", [])
            _pc["expertise_industries"] = _exp_map.get(_cid, {}).get("industry", [])

    import json as _json_mod
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
    all_contacts_for_ac_json = _json_mod.dumps({
        r["name"]: {
            "email": r["email"] or "", "role": r["role"] or "",
            "phone": r["phone"] or "", "mobile": r["mobile"] or "",
            "title": r["title"] or "", "organization": r["organization"] or "",
        } for r in _ac_rows
    })

    from policydb.email_templates import render_tokens as _rtk
    _ctx = {"client_name": "", "program_name": program["name"] or ""}
    client_row = conn.execute("SELECT name FROM clients WHERE id=?", (program["client_id"],)).fetchone()
    if client_row:
        _ctx["client_name"] = client_row["name"]
    mailto_subject = _rtk(
        cfg.get("email_subject_program", "Re: {{client_name}} — {{program_name}}"),
        _ctx,
    )

    return templates.TemplateResponse("programs/_program_team.html", {
        "request": request,
        "program": dict(program),
        "program_contacts": program_contacts,
        "all_contacts_for_ac_json": all_contacts_for_ac_json,
        "mailto_subject": mailto_subject,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "all_orgs": sorted({r["organization"] for r in conn.execute(
            "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
        ).fetchall()}),
    })


@router.post("/programs/{program_uid}/team/add-row", response_class=HTMLResponse)
def program_team_add_row(request: Request, program_uid: str, conn=Depends(get_db)):
    """Create blank program contact row and return matrix row HTML."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    cid = get_or_create_contact(conn, "New Contact")
    asg_id = assign_contact_to_program(conn, cid, program["id"])
    conn.commit()
    c = {"id": asg_id, "contact_id": cid, "name": "New Contact", "title": None, "role": None,
         "organization": None, "email": None, "phone": None, "mobile": None,
         "notes": None, "is_placement_colleague": 0}
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    return templates.TemplateResponse("programs/_team_matrix_row.html", {
        "request": request, "c": c, "program": dict(program),
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


@router.patch("/programs/{program_uid}/team/{contact_id}/cell")
async def program_team_cell(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Save a single cell value for a program contact (matrix edit)."""
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
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    assignment_id = contact_id
    shared_fields = {"name", "email", "phone", "mobile", "organization"}
    assignment_fields = {"role", "title", "notes"}
    if field in shared_fields:
        asg = conn.execute(
            "SELECT contact_id FROM contact_program_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if asg:
            conn.execute(
                f"UPDATE contacts SET {field}=? WHERE id=?",
                (formatted or None, asg["contact_id"]),
            )
    elif field in assignment_fields:
        conn.execute(
            f"UPDATE contact_program_assignments SET {field}=? WHERE id=?",
            (formatted or None, assignment_id),
        )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/programs/{program_uid}/team/{contact_id}/delete", response_class=HTMLResponse)
def program_team_delete(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Remove a contact from the program team."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    remove_contact_from_program(conn, contact_id)
    conn.commit()
    return _program_team_response(request, conn, program_uid)


@router.post("/programs/{program_uid}/team/{contact_id}/toggle-pc", response_class=HTMLResponse)
def program_team_toggle_pc(request: Request, program_uid: str, contact_id: int, conn=Depends(get_db)):
    """Toggle is_placement_colleague flag on a program contact assignment."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Program not found", status_code=404)
    set_program_placement_colleague(conn, contact_id)
    conn.commit()
    return _program_team_response(request, conn, program_uid)


# ── Workflow tab ───────────────────────────────────────────────────────────

@router.get("/programs/{program_uid}/tab/workflow", response_class=HTMLResponse)
def program_tab_workflow(request: Request, program_uid: str, conn=Depends(get_db)):
    """Workflow tab: checklist + information requests."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    # Program milestones (checklist)
    milestones_config = cfg.get("renewal_milestones", [])
    checklist = []
    existing = {r["milestone"]: dict(r) for r in conn.execute(
        "SELECT * FROM program_milestones WHERE program_uid=?", (program_uid,)
    ).fetchall()}

    for ms in milestones_config:
        if ms in existing:
            checklist.append(existing[ms])
        else:
            checklist.append({"id": None, "program_uid": program_uid, "milestone": ms, "completed": 0, "completed_at": None})

    return templates.TemplateResponse("programs/_tab_workflow.html", {
        "request": request,
        "program": program,
        "checklist": checklist,
    })


@router.post("/programs/{program_uid}/milestone/toggle", response_class=HTMLResponse)
def program_milestone_toggle(
    request: Request,
    program_uid: str,
    milestone: str = Form(...),
    conn=Depends(get_db),
):
    """Toggle a program milestone completion status."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("Not found", status_code=404)

    existing = conn.execute(
        "SELECT id, completed FROM program_milestones WHERE program_uid=? AND milestone=?",
        (program_uid, milestone),
    ).fetchone()

    if existing:
        new_val = 0 if existing["completed"] else 1
        conn.execute(
            "UPDATE program_milestones SET completed=?, completed_at=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
            (new_val, new_val, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO program_milestones (program_uid, milestone, completed, completed_at) VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
            (program_uid, milestone),
        )
    conn.commit()

    # Return the full workflow tab
    return program_tab_workflow(request, program_uid, conn)


@router.patch("/programs/{program_uid}/header")
async def patch_program_header(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Update program header fields."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    body = await request.json()
    allowed = {"name", "effective_date", "expiration_date", "renewal_status",
               "line_of_business", "notes", "working_notes", "lead_broker",
               "placement_colleague", "account_exec", "milestone_profile",
               "project_id"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return JSONResponse({"ok": False, "error": "No valid fields"})

    # Normalize project_id to int or None
    if "project_id" in updates:
        pid = updates["project_id"]
        updates["project_id"] = int(pid) if pid else None

    # If name changes, also update tower_group on child policies
    old_name = program["name"]
    new_name = updates.get("name", old_name)
    if new_name and new_name != old_name:
        conn.execute(
            "UPDATE policies SET tower_group = ? WHERE tower_group = ? AND client_id = ?",
            (new_name, old_name, program["client_id"]),
        )

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [program_uid]
    conn.execute(f"UPDATE programs SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE program_uid = ?", vals)
    conn.commit()

    # Auto-resolve renewal issue on terminal status
    if "renewal_status" in updates:
        import policydb.config as _cfg
        if updates["renewal_status"] in _cfg.get("renewal_issue_resolve_statuses", ["Bound"]):
            from policydb.renewal_issues import auto_resolve_renewal_issue
            auto_resolve_renewal_issue(conn, program_uid=program_uid)
            conn.commit()

    return JSONResponse({"ok": True, "formatted": new_name if "name" in updates else ""})


@router.post("/programs/{program_uid}/assign/{policy_uid}")
async def assign_to_program_v2(
    request: Request,
    program_uid: str,
    policy_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Assign a policy to a program. Optional JSON body {"layer_position": ...}
    lets callers put the policy directly on the Primary/Umbrella/Excess layer."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    policy = conn.execute(
        "SELECT id, policy_uid FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not policy:
        return JSONResponse({"ok": False, "error": "Policy not found"}, status_code=404)

    requested_layer: str | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            raw = body.get("layer_position")
            if isinstance(raw, str) and raw.strip() in {"Primary", "Umbrella", "Excess"}:
                requested_layer = raw.strip()
    except Exception:
        pass

    if requested_layer:
        conn.execute(
            "UPDATE policies SET program_id = ?, tower_group = ?, layer_position = ? WHERE policy_uid = ?",
            (program["id"], program["name"], requested_layer, policy_uid),
        )
    else:
        # Default: keep existing Umbrella/Excess or fall back to Primary
        conn.execute(
            """UPDATE policies SET program_id = ?, tower_group = ?,
               layer_position = CASE WHEN layer_position IN ('Umbrella', 'Excess') THEN layer_position ELSE 'Primary' END
               WHERE policy_uid = ?""",
            (program["id"], program["name"], policy_uid),
        )
    # Auto-sync location if program is scoped to a project/location
    _sync_policy_to_program_location(conn, policy_uid, program)
    conn.commit()
    logger.info("Assigned %s to program %s as %s", policy_uid, program_uid, requested_layer or "Primary")

    return JSONResponse({"ok": True})


@router.post("/programs/{program_uid}/unassign/{policy_uid}")
def unassign_from_program_v2(
    program_uid: str,
    policy_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Remove a policy from a program."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    policy = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not policy:
        return JSONResponse({"ok": False, "error": "Policy not found"}, status_code=404)

    conn.execute(
        "UPDATE policies SET tower_group = NULL, program_id = NULL, layer_position = NULL, schematic_column = NULL WHERE policy_uid = ?",
        (policy_uid,),
    )
    # Clean up tower refs
    conn.execute("DELETE FROM program_tower_coverage WHERE excess_policy_id = ? OR underlying_policy_id = ?",
                 (policy["id"], policy["id"]))
    conn.execute("DELETE FROM program_tower_lines WHERE source_policy_id = ?", (policy["id"],))
    conn.commit()
    logger.info("Unassigned %s from program %s", policy_uid, program_uid)

    return JSONResponse({"ok": True})


@router.post("/programs/{program_uid}/move-children")
async def move_children_to_location(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Bulk-update child policies' location to match program's new project_id."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    children = get_program_child_policies(conn, program["id"])
    moved = 0
    for child in children:
        _sync_policy_to_program_location(conn, child["policy_uid"], program)
        moved += 1
    conn.commit()
    logger.info("Moved %d child policies of %s to project %s", moved, program_uid, program.get("project_id"))
    return JSONResponse({"ok": True, "moved": moved})


# ---------------------------------------------------------------------------
# Schematic CRUD — cell patch, add/delete rows, reorder, preview, coverage map
# ---------------------------------------------------------------------------

_CURRENCY_FIELDS = {"limit_amount", "deductible", "premium", "attachment_point", "participation_of"}
_DATE_FIELDS = {"effective_date", "expiration_date"}
_UNDERLYING_ALLOWED = {"policy_type", "carrier", "policy_number", "limit_amount",
                       "deductible", "premium", "coverage_form",
                       "effective_date", "expiration_date", "renewal_status"}
_EXCESS_ALLOWED = {"policy_type", "carrier", "policy_number", "limit_amount",
                   "attachment_point", "participation_of", "premium", "coverage_form", "layer_position",
                   "effective_date", "expiration_date", "renewal_status"}


def _parse_and_format_currency(raw: str):
    """Parse currency input, return (db_value, formatted, error)."""
    if not raw or not str(raw).strip():
        return 0, "$0", None
    val = parse_currency_with_magnitude(str(raw).strip())
    if val is None:
        return None, None, f"Could not parse '{raw}'"
    formatted = f"${val:,.0f}" if val == int(val) else f"${val:,.2f}"
    return val, formatted, None


@router.post("/programs/{program_uid}/log", response_class=HTMLResponse)
def program_log_activity(
    request: Request,
    program_uid: str,
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    duration_hours: str = Form(""),
    follow_up_date: str = Form(""),
    disposition: str = Form(""),
    contact_person: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Log an activity directly against the program (not a child policy)."""
    pgm = _get_program_or_404(conn, program_uid)
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "")
    dur = round_duration(duration_hours)

    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, program_id, activity_type, subject, details,
            follow_up_date, duration_hours, disposition, contact_person, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), pgm["client_id"], pgm["id"],
         activity_type, subject.strip(), details.strip() or None,
         follow_up_date or None, dur, disposition.strip() or None,
         contact_person.strip() or None, account_exec),
    )
    conn.commit()
    # Reload activity tab via script
    return HTMLResponse(
        '<script>htmx.ajax("GET", "/programs/' + program_uid + '/tab/activity", {target: ".tab-content", swap: "innerHTML"});</script>'
    )


def _get_program_or_404(conn, program_uid: str):
    pgm = get_program_by_uid(conn, program_uid)
    if not pgm:
        raise HTTPException(status_code=404, detail="Program not found")
    return pgm


@router.patch("/programs/{program_uid}/underlying/{policy_id}/cell")
async def patch_underlying_cell_v2(request: Request, program_uid: str, policy_id: int,
                                   conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    if field not in _UNDERLYING_ALLOWED:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value, display_value = val, formatted
    elif field in _DATE_FIELDS:
        db_value = str(value).strip() if value and str(value).strip() else None
        display_value = db_value or ""
    else:
        db_value = display_value = str(value).strip() if value else ""
    conn.execute(f"UPDATE policies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (db_value, policy_id))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": display_value})


@router.patch("/programs/{program_uid}/excess/{policy_id}/cell")
async def patch_excess_cell_v2(request: Request, program_uid: str, policy_id: int,
                               conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    if field not in _EXCESS_ALLOWED:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value, display_value = val, formatted
    elif field in _DATE_FIELDS:
        db_value = str(value).strip() if value and str(value).strip() else None
        display_value = db_value or ""
    else:
        db_value = display_value = str(value).strip() if value else ""
    conn.execute(f"UPDATE policies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (db_value, policy_id))
    conn.commit()
    row = conn.execute("SELECT limit_amount, attachment_point, participation_of FROM policies WHERE id = ?",
                       (policy_id,)).fetchone()
    notation = _layer_notation(row["limit_amount"], row["attachment_point"], row["participation_of"]) if row else ""
    return JSONResponse({"ok": True, "formatted": display_value, "notation": notation})


@router.patch("/programs/{program_uid}/subcoverage/{sc_id}/cell")
async def patch_subcoverage_cell(request: Request, program_uid: str, sc_id: int,
                                 conn: sqlite3.Connection = Depends(get_db)):
    """Patch a sub-coverage field from the schematic excess matrix (ghost rows)."""
    _get_program_or_404(conn, program_uid)
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    _SC_ALLOWED = {"limit_amount", "deductible", "attachment_point", "premium",
                   "participation_of", "carrier", "policy_number", "coverage_form"}
    if field not in _SC_ALLOWED:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value, display_value = val, formatted
    else:
        db_value = display_value = str(value).strip() if value else ""
    conn.execute(f"UPDATE policy_sub_coverages SET {field} = ? WHERE id = ?",
                 (db_value, sc_id))
    conn.commit()
    sc = conn.execute("SELECT limit_amount, attachment_point, participation_of FROM policy_sub_coverages WHERE id = ?",
                      (sc_id,)).fetchone()
    notation = ""
    if sc:
        lim = sc["limit_amount"] or 0
        att = sc["attachment_point"] or 0
        po = sc["participation_of"]
        notation = _layer_notation(lim, att, po) if lim else ""
    return JSONResponse({"ok": True, "formatted": display_value, "notation": notation})


@router.post("/programs/{program_uid}/underlying/add", response_class=HTMLResponse)
async def add_underlying_v2(request: Request, program_uid: str,
                            conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    col_row = conn.execute(
        "SELECT COALESCE(MAX(schematic_column), 0) + 1 AS next_col FROM policies "
        "WHERE program_id = ? AND layer_position = 'Primary' AND archived = 0",
        (pgm["id"],)).fetchone()
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, program_id, tower_group, layer_position,
           schematic_column, policy_type, carrier, policy_number, limit_amount, deductible,
           premium, coverage_form, first_named_insured, description, notes)
           VALUES (?, ?, ?, ?, 'Primary', ?, '', '', '', 0, 0, 0, '', '', '', '')""",
        (uid, pgm["client_id"], pgm["id"], pgm["name"], col_row["next_col"]))
    conn.commit()
    new_row = conn.execute("SELECT * FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    return templates.TemplateResponse("programs/_underlying_row.html", {
        "request": request, "p": dict(new_row), "client_id": pgm["client_id"],
        "tower_group": pgm["name"], "program_uid": program_uid,
        "policy_types": cfg.get("policy_types", []), "carriers": cfg.get("carriers", []),
        "coverage_forms": cfg.get("coverage_forms", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
    })


@router.post("/programs/{program_uid}/excess/add", response_class=HTMLResponse)
async def add_excess_v2(request: Request, program_uid: str,
                        conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    att_row = conn.execute(
        """SELECT COALESCE(MAX(COALESCE(attachment_point, 0) + COALESCE(participation_of, limit_amount, 0)), 0) AS next_att
           FROM policies WHERE program_id = ? AND layer_position != 'Primary'
           AND (layer_position IS NULL OR layer_position NOT LIKE '%%mbrella%%') AND archived = 0""",
        (pgm["id"],)).fetchone()
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, program_id, tower_group, layer_position, policy_type,
           attachment_point, limit_amount, carrier, policy_number, premium, coverage_form,
           first_named_insured, description, notes)
           VALUES (?, ?, ?, ?, 'Excess', 'Excess Liability', ?, 0, '', '', 0, '', '', '', '')""",
        (uid, pgm["client_id"], pgm["id"], pgm["name"], att_row["next_att"]))
    conn.commit()
    new_row = conn.execute("SELECT * FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    row_dict = dict(new_row)
    row_dict["notation"] = _layer_notation(row_dict.get("limit_amount"), row_dict.get("attachment_point"),
                                           row_dict.get("participation_of"))
    return templates.TemplateResponse("programs/_excess_row.html", {
        "request": request, "p": row_dict, "layer_num": "—",
        "client_id": pgm["client_id"], "tower_group": pgm["name"], "program_uid": program_uid,
        "carriers": cfg.get("carriers", []), "coverage_forms": cfg.get("coverage_forms", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
    })


@router.post("/programs/{program_uid}/umbrella/add", response_class=HTMLResponse)
async def add_umbrella_v2(request: Request, program_uid: str,
                          conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    existing = conn.execute(
        "SELECT id FROM policies WHERE program_id = ? AND layer_position LIKE '%%mbrella%%' AND archived = 0 LIMIT 1",
        (pgm["id"],)).fetchone()
    if existing:
        return JSONResponse({"ok": False, "error": "Umbrella already exists"}, status_code=409)
    uid = next_policy_uid(conn)
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, program_id, tower_group, layer_position, policy_type,
           attachment_point, limit_amount, carrier, policy_number, premium, coverage_form,
           first_named_insured, description, notes)
           VALUES (?, ?, ?, ?, 'Umbrella', 'Umbrella Liability', 0, 0, '', '', 0, '', '', '', '')""",
        (uid, pgm["client_id"], pgm["id"], pgm["name"]))
    conn.commit()
    new_row = conn.execute("SELECT * FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    row_dict = dict(new_row)
    row_dict["notation"] = _layer_notation(row_dict.get("limit_amount"), row_dict.get("attachment_point"),
                                           row_dict.get("participation_of"))
    return templates.TemplateResponse("programs/_excess_row.html", {
        "request": request, "p": row_dict, "layer_num": "—",
        "client_id": pgm["client_id"], "tower_group": pgm["name"], "program_uid": program_uid,
        "carriers": cfg.get("carriers", []), "coverage_forms": cfg.get("coverage_forms", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
    })


@router.post("/programs/{program_uid}/underlying/reorder")
async def reorder_underlying_v2(request: Request, program_uid: str,
                                conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    body = await request.json()
    for i, pid in enumerate(body.get("order", [])):
        conn.execute("UPDATE policies SET schematic_column = ? WHERE id = ?", (i + 1, int(pid)))
    conn.commit()
    return JSONResponse({"ok": True})


@router.delete("/programs/{program_uid}/underlying/{policy_id}", response_class=HTMLResponse)
async def delete_underlying_v2(request: Request, program_uid: str, policy_id: int,
                               conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
    remaining = conn.execute(
        "SELECT id FROM policies WHERE program_id = ? AND layer_position = 'Primary' AND archived = 0 ORDER BY schematic_column",
        (pgm["id"],)).fetchall()
    for i, row in enumerate(remaining):
        conn.execute("UPDATE policies SET schematic_column = ? WHERE id = ?", (i + 1, row["id"]))
    conn.commit()
    return HTMLResponse("")


@router.delete("/programs/{program_uid}/excess/{policy_id}", response_class=HTMLResponse)
async def delete_excess_v2(request: Request, program_uid: str, policy_id: int,
                           conn: sqlite3.Connection = Depends(get_db)):
    _get_program_or_404(conn, program_uid)
    conn.execute("DELETE FROM program_tower_coverage WHERE excess_policy_id = ?", (policy_id,))
    conn.execute("DELETE FROM program_tower_lines WHERE source_policy_id = ?", (policy_id,))
    conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
    conn.commit()
    return HTMLResponse("")


@router.get("/programs/{program_uid}/preview", response_class=HTMLResponse)
async def schematic_preview_v2(request: Request, program_uid: str,
                               conn: sqlite3.Connection = Depends(get_db)):
    pgm = _get_program_or_404(conn, program_uid)
    all_data = get_tower_data(conn, pgm["client_id"])
    tower_data = [t for t in all_data if t.get("program_name") == pgm["name"]
                  or t.get("tower_group") == pgm["name"]]
    return templates.TemplateResponse("programs/_schematic_preview.html", {
        "request": request, "tower_data": tower_data,
        "client_id": pgm["client_id"], "tower_group": pgm["name"],
    })


@router.post("/programs/{program_uid}/coverage-map")
async def add_coverage_mapping_v2(request: Request, program_uid: str,
                                  conn: sqlite3.Connection = Depends(get_db)):
    _get_program_or_404(conn, program_uid)
    body = await request.json()
    excess_id = body.get("excess_policy_id")
    underlying_id = body.get("underlying_policy_id")
    sub_cov_id = body.get("underlying_sub_coverage_id")
    if not excess_id or (not underlying_id and not sub_cov_id):
        return JSONResponse({"ok": False, "error": "Missing required fields"}, status_code=400)
    conn.execute(
        "INSERT OR IGNORE INTO program_tower_coverage (excess_policy_id, underlying_policy_id, underlying_sub_coverage_id) VALUES (?, ?, ?)",
        (excess_id, underlying_id or None, sub_cov_id or None))
    conn.commit()
    return JSONResponse({"ok": True})


@router.delete("/programs/{program_uid}/coverage-map/{ptc_id}")
async def remove_coverage_mapping_v2(program_uid: str, ptc_id: int,
                                     conn: sqlite3.Connection = Depends(get_db)):
    _get_program_or_404(conn, program_uid)
    conn.execute("DELETE FROM program_tower_coverage WHERE id = ?", (ptc_id,))
    conn.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Child policy cell PATCH — used by Overview tab editable grid
# ---------------------------------------------------------------------------

_CHILD_ALLOWED = {
    "policy_type", "carrier", "policy_number", "premium", "limit_amount",
    "deductible", "attachment_point", "effective_date", "expiration_date",
    "renewal_status", "layer_position", "participation_of",
}


@router.patch("/programs/{program_uid}/child/{policy_id}/cell")
async def patch_child_cell(request: Request, program_uid: str, policy_id: int,
                           conn: sqlite3.Connection = Depends(get_db)):
    """PATCH a single field on a child policy from the overview grid."""
    _get_program_or_404(conn, program_uid)
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    if field not in _CHILD_ALLOWED:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value, display_value = val, formatted
    elif field in _DATE_FIELDS:
        # Pass through ISO date string or clear
        db_value = str(value).strip() if value and str(value).strip() else None
        display_value = db_value or ""
    else:
        db_value = display_value = str(value).strip() if value else ""
    conn.execute(f"UPDATE policies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (db_value, policy_id))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": display_value})


# ---------------------------------------------------------------------------
# Program inline logging from renewal pipeline
# (Must be before /programs/{uid}/renew to avoid route capture)
# ---------------------------------------------------------------------------

def _build_program_row_context(conn, program_uid: str) -> dict:
    """Build a program row dict matching get_program_pipeline() output for template rendering.

    The _program_renew_row.html template reads `days_since_touch` without
    an `is defined` guard, so every code path returning from this helper
    must populate that key (None when there's no activity).
    """
    from policydb.queries import get_program_pipeline
    all_pgms = get_program_pipeline(conn, window_days=9999)
    found = None
    for pgm in all_pgms:
        if pgm.get("program_uid") == program_uid:
            pgm["expiration_date"] = pgm["earliest_expiration"]
            pgm["premium"] = pgm["total_premium"]
            found = pgm
            break

    if found is None:
        program = get_program_by_uid(conn, program_uid)
        if not program:
            return {}
        d = dict(program)
        client = conn.execute(
            "SELECT name, cn_number FROM clients WHERE id=?", (d["client_id"],)
        ).fetchone()
        d["client_name"] = client["name"] if client else ""
        d["cn_number"] = client["cn_number"] if client else ""
        d["program_name"] = d["name"]
        d["_is_program"] = True
        d["program_id"] = d["id"]
        d["followup_overdue"] = bool(
            d.get("follow_up_date") and d["follow_up_date"] < date.today().isoformat()
        )
        found = d

    # Aggregate last activity across all child policies (matches the inline
    # decoration in the /renewals route so row refreshes match initial render)
    child_rows = conn.execute(
        "SELECT id FROM policies WHERE program_id=? AND archived=0",
        (found.get("program_id") or found.get("id"),),
    ).fetchall()
    child_ids = [c["id"] for c in child_rows]
    last_date = None
    if child_ids:
        ph = ",".join("?" * len(child_ids))
        row = conn.execute(
            f"SELECT MAX(activity_date) AS last_date FROM activity_log WHERE policy_id IN ({ph})",
            child_ids,
        ).fetchone()
        last_date = row["last_date"] if row else None
    found["last_activity_date"] = last_date
    if last_date:
        try:
            found["days_since_touch"] = (
                date.today() - date.fromisoformat(last_date)
            ).days
        except (ValueError, TypeError):
            found["days_since_touch"] = None
    else:
        found["days_since_touch"] = None
    return found


@router.get("/programs/{program_uid}/renew/log", response_class=HTMLResponse)
def program_renew_log_form(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return inline activity log form for program row in renewal pipeline."""
    p = _build_program_row_context(conn, program_uid)
    if not p:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("programs/_program_renew_row_log.html", {
        "request": request,
        "p": p,
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
    })


@router.post("/programs/{program_uid}/renew/log", response_class=HTMLResponse)
def program_renew_log_save(
    request: Request,
    program_uid: str,
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    duration_hours: str = Form(""),
    follow_up_date: str = Form(""),
    contact_person: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Save activity from program renewal pipeline inline form."""
    pgm = _get_program_or_404(conn, program_uid)
    from policydb.utils import round_duration
    dur = round_duration(duration_hours)

    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, program_id, activity_type, subject, details,
            follow_up_date, duration_hours, contact_person)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), pgm["client_id"], pgm["id"],
         activity_type, subject.strip(), details.strip() or None,
         follow_up_date or None, dur, contact_person.strip() or None),
    )

    # Update program follow_up_date if provided
    if follow_up_date:
        conn.execute(
            "UPDATE programs SET follow_up_date=? WHERE program_uid=?",
            (follow_up_date, program_uid),
        )
    conn.commit()
    logger.info("Program %s activity logged from renewal pipeline", program_uid)

    # Return the display row
    p = _build_program_row_context(conn, program_uid)
    from policydb.queries import attach_renewal_issues
    attach_renewal_issues(conn, [p])
    return templates.TemplateResponse("policies/_program_renew_row.html", {
        "request": request,
        "p": p,
        "renewal_statuses": _renewal_statuses(),
    })


@router.get("/programs/{program_uid}/renew/row", response_class=HTMLResponse)
def program_renew_row(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Restore display row for program in renewal pipeline (Cancel target)."""
    p = _build_program_row_context(conn, program_uid)
    if not p:
        return HTMLResponse("", status_code=404)
    from policydb.queries import attach_renewal_issues
    attach_renewal_issues(conn, [p])
    return templates.TemplateResponse("policies/_program_renew_row.html", {
        "request": request,
        "p": p,
        "renewal_statuses": _renewal_statuses(),
    })


# ---------------------------------------------------------------------------
# Bulk Renew Program — renews all child policies in one action
# ---------------------------------------------------------------------------

@router.post("/programs/{program_uid}/renew")
async def renew_program(request: Request, program_uid: str,
                        conn: sqlite3.Connection = Depends(get_db)):
    """Renew all active child policies in the program.

    For each child: call renew_policy() (archives old, creates new term),
    then set program_id and schematic_column on the new term.
    Finally, copy tower coverage and tower line mappings from old to new IDs.
    """
    pgm = _get_program_or_404(conn, program_uid)
    children = get_program_child_policies(conn, pgm["id"])
    if not children:
        return JSONResponse({"ok": False, "error": "No child policies to renew"}, status_code=400)

    # Map old policy id -> new policy id for tower coverage remapping
    old_to_new = {}
    renewed = 0
    errors = []

    for child in children:
        try:
            new_uid = renew_policy(conn, child["policy_uid"])
            new_row = conn.execute(
                "SELECT id FROM policies WHERE policy_uid = ?", (new_uid,)
            ).fetchone()
            if new_row:
                new_id = new_row["id"]
                old_to_new[child["id"]] = new_id
                # renew_policy does NOT copy program_id or schematic_column — set them
                conn.execute(
                    """UPDATE policies SET program_id = ?, tower_group = ?, schematic_column = ?
                       WHERE id = ?""",
                    (pgm["id"], pgm["name"], child.get("schematic_column"), new_id),
                )
                # Auto-sync location if program is scoped to a project/location
                _sync_policy_to_program_location(conn, new_uid, pgm)
                conn.commit()
                renewed += 1
        except Exception as exc:
            logger.warning("Failed to renew %s in program %s: %s",
                           child["policy_uid"], program_uid, exc)
            errors.append(f"{child['policy_uid']}: {exc}")

    # Copy program_tower_coverage rows: remap old excess/underlying IDs to new IDs
    if old_to_new:
        old_coverages = conn.execute(
            """SELECT ptc.* FROM program_tower_coverage ptc
               WHERE ptc.excess_policy_id IN ({})""".format(
                ",".join(str(k) for k in old_to_new.keys())
            )
        ).fetchall()
        for cov in old_coverages:
            new_excess = old_to_new.get(cov["excess_policy_id"])
            new_underlying = old_to_new.get(cov["underlying_policy_id"]) if cov["underlying_policy_id"] else None
            if new_excess:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO program_tower_coverage
                           (excess_policy_id, underlying_policy_id, underlying_sub_coverage_id)
                           VALUES (?, ?, ?)""",
                        (new_excess, new_underlying or cov["underlying_policy_id"],
                         cov["underlying_sub_coverage_id"]),
                    )
                except Exception:
                    pass  # skip if constraint fails

        # Copy program_tower_lines rows: remap old source_policy_id to new IDs
        old_lines = conn.execute(
            """SELECT * FROM program_tower_lines
               WHERE source_policy_id IN ({})""".format(
                ",".join(str(k) for k in old_to_new.keys())
            )
        ).fetchall()
        for line in old_lines:
            new_source = old_to_new.get(line["source_policy_id"])
            if new_source:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO program_tower_lines
                           (program_policy_id, source_policy_id, sub_coverage_id,
                            label, include_in_tower, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (line["program_policy_id"], new_source,
                         line["sub_coverage_id"], line["label"],
                         line["include_in_tower"], line["sort_order"]),
                    )
                except Exception:
                    pass

        conn.commit()

    result = {"ok": True, "renewed_count": renewed}
    if errors:
        result["errors"] = errors
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Program status + bound automation (mirrors policy status flow)
# ---------------------------------------------------------------------------


def _renewal_statuses() -> list[str]:
    return cfg.get("renewal_statuses", ["Not Started", "In Progress", "Pending Bind", "Bound"])


@router.post("/programs/{program_uid}/status", response_class=HTMLResponse)
def program_update_status(
    request: Request,
    program_uid: str,
    status: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """HTMX endpoint: update program renewal status, return updated badge partial."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("", status_code=404)

    if status not in _renewal_statuses():
        status = _renewal_statuses()[0]

    prior_status = program["renewal_status"] or "Not Started"

    conn.execute(
        "UPDATE programs SET renewal_status=?, updated_at=CURRENT_TIMESTAMP WHERE program_uid=?",
        (status, program_uid),
    )
    # Clear bound_date when moving away from a terminal status
    resolve_statuses = cfg.get("renewal_issue_resolve_statuses", ["Bound"])
    if status not in resolve_statuses and prior_status in resolve_statuses:
        conn.execute("UPDATE programs SET bound_date=NULL WHERE program_uid=?", (program_uid,))
    conn.commit()
    logger.info("Program %s status -> %s", program_uid, status)

    # Re-fetch for badge render
    program = get_program_by_uid(conn, program_uid)
    p = dict(program)
    badge_html = templates.TemplateResponse("programs/_program_status_badge.html", {
        "request": request,
        "p": p,
        "renewal_statuses": _renewal_statuses(),
    }).body.decode()

    # Show confirmation banner for terminal status
    if status in resolve_statuses:
        inner_html = templates.TemplateResponse("programs/_program_bound_confirm.html", {
            "request": request,
            "uid": program_uid,
            "prior_status": prior_status,
        }).body.decode()
        oob_html = f'<div id="bound-confirm-prompt" hx-swap-oob="innerHTML">{inner_html}</div>'
        return HTMLResponse(badge_html + oob_html)

    # Dismiss any stale confirmation banner
    dismiss_html = '<div id="bound-confirm-prompt" hx-swap-oob="innerHTML"></div>'
    return HTMLResponse(badge_html + dismiss_html)


@router.get("/programs/{program_uid}/status-badge", response_class=HTMLResponse)
def program_status_badge(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Return just the status badge partial (used by cancel revert)."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("programs/_program_status_badge.html", {
        "request": request,
        "p": dict(program),
        "renewal_statuses": _renewal_statuses(),
    })


@router.post("/programs/{program_uid}/bound-confirm", response_class=HTMLResponse)
def program_bound_confirm(
    request: Request,
    program_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Execute all bound automations for a program (status badge banner path).

    Fast path: marks every active child policy bound using today's date and the
    panel-default new term dates. For richer control (selective children, custom
    dates, bind notes) the user goes through the Bind Order panel instead.
    """
    from datetime import date as _date
    from policydb.bind_order import (
        BindChildPayload,
        BindOrderPayload,
        BindSubject,
        BindSubjectPayload,
        execute_bind_order,
        preview_bind_panel,
    )

    program = get_program_by_uid(conn, program_uid)
    if not program:
        return HTMLResponse("", status_code=404)

    program_id = program["id"]

    # Build a default payload by previewing the panel — this gives us computed dates + child list
    panel = preview_bind_panel(conn, [BindSubject(subject_type="program", subject_uid=program_uid)])
    if not panel.sections:
        # No active children — log a bare program-level "Renewal bound" entry and bail
        conn.execute("UPDATE programs SET bound_date=date('now'), renewal_status='Bound' WHERE program_uid=?", (program_uid,))
        conn.execute(
            """INSERT INTO activity_log (client_id, program_id, activity_type, subject, created_at)
               VALUES (?, ?, 'Milestone', 'Renewal bound', datetime('now'))""",
            (program["client_id"], program_id),
        )
        conn.commit()
        return HTMLResponse("")

    section = panel.sections[0]
    payload = BindOrderPayload(
        bind_date=_date.today().isoformat(),
        bind_note="",
        subjects=[BindSubjectPayload(
            subject_type="program",
            subject_uid=program_uid,
            new_effective=section.new_effective,
            new_expiration=section.new_expiration,
            children=[
                BindChildPayload(
                    policy_uid=child.policy_uid,
                    checked=(child.state != "already_bound"),
                    disposition="Bound",
                    new_premium=None,
                )
                for child in section.children
            ],
        )],
    )

    try:
        execute_bind_order(conn, payload)
    except Exception as exc:
        conn.rollback()
        logger.exception("Program %s fast-path bind failed: %s", program_uid, exc)
        return HTMLResponse(
            f'<div id="bound-confirm-prompt" hx-swap-oob="innerHTML">'
            f'<div class="text-xs text-red-600 p-2">Bind failed: {exc}</div></div>'
        )

    logger.info("Program %s bound (banner fast path)", program_uid)

    # Show renewal prompt offering to also create the next term row (preserves prior UX)
    children = get_program_child_policies(conn, program_id)
    if children:
        return templates.TemplateResponse("programs/_program_bound_renew_prompt.html", {
            "request": request,
            "program_uid": program_uid,
        })
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Legacy URL redirect — catch old /clients/{id}/programs/{tower_group} URLs
# ---------------------------------------------------------------------------

@router.get("/clients/{client_id}/programs/{tower_group:path}")
async def redirect_legacy_program(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Redirect old tower_group URLs to new program detail page."""
    program = conn.execute(
        "SELECT program_uid FROM programs WHERE client_id = ? AND name = ? AND archived = 0 LIMIT 1",
        (client_id, tower_group),
    ).fetchone()
    if program:
        return RedirectResponse(f"/programs/{program['program_uid']}", status_code=302)
    raise HTTPException(status_code=404, detail="Program not found")
