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
from policydb.utils import parse_currency_with_magnitude
from policydb.queries import (
    get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id,
    get_program_by_uid, get_program_child_policies, get_program_aggregates,
    get_unassigned_policies, get_programs_for_project,
    get_program_timeline_milestones, get_program_activities,
    renew_policy,
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
    })


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
def assign_to_program_v2(
    program_uid: str,
    policy_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Assign a policy to a program (sets tower_group for schematic compat)."""
    program = get_program_by_uid(conn, program_uid)
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    policy = conn.execute(
        "SELECT id, policy_uid FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not policy:
        return JSONResponse({"ok": False, "error": "Policy not found"}, status_code=404)

    # Set program_id FK + tower_group; force layer_position to Primary unless already Umbrella/Excess
    conn.execute(
        """UPDATE policies SET program_id = ?, tower_group = ?,
           layer_position = CASE WHEN layer_position IN ('Umbrella', 'Excess') THEN layer_position ELSE 'Primary' END
           WHERE policy_uid = ?""",
        (program["id"], program["name"], policy_uid),
    )
    # Auto-sync location if program is scoped to a project/location
    _sync_policy_to_program_location(conn, policy_uid, program)
    conn.commit()
    logger.info("Assigned %s to program %s", policy_uid, program_uid)

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
