"""Program Schematic Entry routes."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import policydb.config as cfg
from policydb.charts import _layer_notation
from policydb.db import next_program_uid
from policydb.queries import (
    get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id,
    get_program_by_uid, get_program_child_policies, get_program_aggregates,
    get_unassigned_policies,
    get_program_timeline_milestones, get_program_activities,
)
from policydb.web.app import get_db, templates

logger = logging.getLogger("policydb.programs")
router = APIRouter(tags=["programs"])

# ===========================================================================
# PROGRAMS v2 — Standalone entity routes (programs table)
# ===========================================================================


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

    uid = next_program_uid(conn)
    conn.execute(
        """INSERT INTO programs (program_uid, client_id, name, line_of_business)
           VALUES (?, ?, ?, ?)""",
        (uid, client_id, name, lob),
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

    return templates.TemplateResponse("programs/detail.html", {
        "request": request,
        "active": "clients",
        "program": program,
        "client": dict(client),
        "aggregates": agg,
        "renewal_statuses": renewal_statuses,
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

    return templates.TemplateResponse("programs/_tab_overview.html", {
        "request": request,
        "program": program,
        "children": children,
        "unassigned": unassigned,
        "aggregates": agg,
        "renewal_statuses": cfg.get("renewal_statuses", []),
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
                  p.schematic_column, p.is_program, p.tower_group
           FROM policies p
           WHERE p.program_id = ?
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           ORDER BY p.layer_position, p.schematic_column, p.policy_type""",
        (program_id,),
    ).fetchall()
    policies = [dict(r) for r in rows]

    # Split underlying vs excess
    underlying = [p for p in policies if (p.get("layer_position") or "Primary") == "Primary"
                  and not p.get("is_program")]
    excess = [p for p in policies if (p.get("layer_position") or "") in ("Umbrella", "Excess")
              and not p.get("is_program")]

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
    _EXCESS_SC_KEYWORDS = ("umbrella", "excess")
    for p in list(underlying):
        for sc in p.get("sub_coverages_full", []):
            sc_type = (sc.get("coverage_type") or "").lower()
            if any(kw in sc_type for kw in _EXCESS_SC_KEYWORDS) and sc.get("limit_amount"):
                excess.append({
                    "id": p["id"], "policy_uid": p["policy_uid"],
                    "policy_type": sc["coverage_type"], "carrier": p.get("carrier") or "",
                    "policy_number": p.get("policy_number") or "",
                    "limit_amount": sc["limit_amount"], "deductible": sc.get("deductible") or 0,
                    "premium": 0, "coverage_form": sc.get("coverage_form") or "",
                    "layer_position": "Umbrella" if "umbrella" in sc_type else "Excess",
                    "attachment_point": sc.get("attachment_point") or 0,
                    "participation_of": None, "schematic_column": None,
                    "is_program": 0, "tower_group": tg,
                    "is_package_ghost": True, "sub_coverages": [], "sub_coverages_full": [],
                    "_from_sub_coverage": True, "_sub_coverage_id": sc["id"],
                })

    has_umbrella = any(p.get("layer_position") == "Umbrella" for p in excess)

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

    # Available tower lines for coverage selector
    tower_lines = conn.execute(
        "SELECT * FROM program_tower_lines WHERE program_id = ?",
        (program_id,),
    ).fetchall()
    available_lines = [dict(tl) for tl in tower_lines]

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

    return templates.TemplateResponse("programs/_tab_activity.html", {
        "request": request,
        "program": program,
        "activities": activities,
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
               "placement_colleague", "account_exec", "milestone_profile"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return JSONResponse({"ok": False, "error": "No valid fields"})

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

    # Set tower_group for backward compat with schematic endpoints
    conn.execute(
        """UPDATE policies SET tower_group = ?, layer_position = COALESCE(layer_position, 'Primary')
           WHERE policy_uid = ?""",
        (program["name"], policy_uid),
    )
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
