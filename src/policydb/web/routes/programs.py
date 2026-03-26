"""Program Schematic Entry routes."""

from __future__ import annotations

import logging
import sqlite3
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

import policydb.config as cfg
from policydb.charts import _layer_notation, get_tower_data
from policydb.db import next_policy_uid
from policydb.queries import get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id
from policydb.utils import parse_currency_with_magnitude
from policydb.web.app import get_db, templates

logger = logging.getLogger("policydb.programs")
router = APIRouter(tags=["programs"])

_CURRENCY_FIELDS = {"limit_amount", "deductible", "premium", "attachment_point", "participation_of"}

# Sub-coverage types that count as umbrella/excess for tower participation
_TOWER_ELIGIBLE_SUB_COVERAGES = {"umbrella", "excess", "umbrella / excess", "umbrella liability", "excess liability"}

_UNDERLYING_ALLOWED = {
    "policy_type", "carrier", "limit_amount", "deductible",
    "premium", "policy_number", "coverage_form",
}

_EXCESS_ALLOWED = {
    "carrier", "limit_amount", "attachment_point", "participation_of",
    "premium", "policy_number", "layer_position", "coverage_form",
}


def _fmt_currency(val: float | None) -> str:
    """Format a currency value for display. Returns '' if zero/None."""
    if not val:
        return ""
    return "${:,.0f}".format(val)


def _parse_and_format_currency(raw: str) -> tuple[float | None, str | None, str | None]:
    """Parse a currency string and return (numeric, formatted, error).

    Returns (value, formatted_string, None) on success,
    or (None, None, error_message) on failure.
    """
    if not raw or not str(raw).strip():
        return 0.0, "", None
    val = parse_currency_with_magnitude(raw)
    if val is None:
        return None, None, f"Could not parse currency value: {raw}"
    return val, _fmt_currency(val), None


# ---------------------------------------------------------------------------
# Create new program (literal route before parameterized)
# ---------------------------------------------------------------------------

@router.post("/clients/{client_id}/programs/new")
async def create_program(
    request: Request,
    client_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    name = (body.get("name") or "").strip()
    lob = (body.get("lob") or "").strip()

    if not name:
        return JSONResponse({"ok": False, "error": "Program name is required"}, status_code=400)

    # Check for duplicate program (only is_program=1 records — child policies with same tower_group don't count)
    existing = conn.execute(
        "SELECT id FROM policies WHERE client_id = ? AND tower_group = ? AND is_program = 1 AND archived = 0 LIMIT 1",
        (client_id, name),
    ).fetchone()
    if existing:
        return JSONResponse({"ok": False, "error": f"Program '{name}' already exists"}, status_code=409)

    uid = next_policy_uid(conn)
    policy_type = lob if lob else name

    conn.execute(
        """
        INSERT INTO policies (
            policy_uid, client_id, is_program, tower_group, policy_type,
            layer_position, first_named_insured, description, notes
        ) VALUES (?, ?, 1, ?, ?, 'Primary', '', '', '')
        """,
        (uid, client_id, name, policy_type),
    )
    conn.commit()
    logger.info("Created program '%s' (%s) for client %d", name, uid, client_id)

    redirect_url = f"/clients/{client_id}/programs/{quote(name, safe='')}"
    return JSONResponse({"ok": True, "redirect": redirect_url})


# ---------------------------------------------------------------------------
# Rename program
# ---------------------------------------------------------------------------

@router.patch("/clients/{client_id}/programs/{tower_group}/rename")
async def rename_program(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)
    body = await request.json()
    new_name = (body.get("name") or "").strip()

    if not new_name:
        return JSONResponse({"ok": False, "error": "Name is required"}, status_code=400)
    if new_name == tg:
        return JSONResponse({"ok": True, "redirect": None})

    # Check for duplicate program (only is_program=1 records)
    existing = conn.execute(
        "SELECT id FROM policies WHERE client_id = ? AND tower_group = ? AND is_program = 1 AND archived = 0 LIMIT 1",
        (client_id, new_name),
    ).fetchone()
    if existing:
        return JSONResponse({"ok": False, "error": f"Program '{new_name}' already exists"}, status_code=409)

    conn.execute(
        "UPDATE policies SET tower_group = ? WHERE client_id = ? AND tower_group = ? AND archived = 0",
        (new_name, client_id, tg),
    )
    conn.commit()
    logger.info("Renamed program '%s' → '%s' for client %d", tg, new_name, client_id)

    redirect_url = f"/clients/{client_id}/programs/{quote(new_name, safe='')}"
    return JSONResponse({"ok": True, "redirect": redirect_url})


# ---------------------------------------------------------------------------
# Program header PATCH (term dates, status)
# ---------------------------------------------------------------------------

@router.patch("/clients/{client_id}/programs/{tower_group}/header")
async def patch_program_header(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"effective_date", "expiration_date", "renewal_status"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)

    # Update the program policy (is_program=1)
    conn.execute(
        f"UPDATE policies SET {field} = ? WHERE client_id = ? AND tower_group = ? AND is_program = 1 AND archived = 0",
        (value, client_id, tg),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


# ---------------------------------------------------------------------------
# Assign / Unassign existing policy to program
# ---------------------------------------------------------------------------

@router.post("/clients/{client_id}/programs/{tower_group}/assign/{policy_uid}")
async def assign_to_program(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    # Get the program policy id for FK link
    program_row = conn.execute(
        "SELECT id FROM policies WHERE client_id = ? AND tower_group = ? AND is_program = 1 AND archived = 0 LIMIT 1",
        (client_id, tg),
    ).fetchone()
    program_id = program_row["id"] if program_row else None

    # Get next schematic column
    col_row = conn.execute(
        """SELECT COALESCE(MAX(schematic_column), 0) + 1 AS next_col
        FROM policies WHERE client_id = ? AND tower_group = ? AND layer_position = 'Primary' AND archived = 0""",
        (client_id, tg),
    ).fetchone()
    next_col = col_row["next_col"] if col_row else 1

    # Get the policy being assigned
    pol = conn.execute(
        "SELECT id, policy_type FROM policies WHERE client_id = ? AND policy_uid = ? AND archived = 0",
        (client_id, policy_uid),
    ).fetchone()
    if not pol:
        return JSONResponse({"ok": False, "error": "Policy not found"}, status_code=404)

    conn.execute(
        """UPDATE policies SET tower_group = ?, program_id = ?, layer_position = 'Primary',
           schematic_column = ? WHERE id = ?""",
        (tg, program_id, next_col, pol["id"]),
    )

    # Auto-create program_tower_lines rows
    if program_id:
        # Check for sub-coverages with limits (package policy)
        from policydb.queries import get_sub_coverages
        subs = get_sub_coverages(conn, pol["id"])
        subs_with_limits = [s for s in subs if s.get("limit_amount")]

        if subs_with_limits:
            # Package: create one tower line per sub-coverage with limit
            _LIABILITY_KEYWORDS = ("liability", "auto", "gl", "professional", "employer")
            for i, sc in enumerate(subs_with_limits):
                label = sc["coverage_type"]
                # Default: include liability-type, exclude property-type
                is_liability = any(kw in label.lower() for kw in _LIABILITY_KEYWORDS)
                conn.execute(
                    """INSERT OR IGNORE INTO program_tower_lines
                       (program_policy_id, source_policy_id, sub_coverage_id, label, include_in_tower, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (program_id, pol["id"], sc["id"], label, 1 if is_liability else 0, i),
                )
        else:
            # Standalone: one tower line for the whole policy
            conn.execute(
                """INSERT OR IGNORE INTO program_tower_lines
                   (program_policy_id, source_policy_id, sub_coverage_id, label, include_in_tower, sort_order)
                   VALUES (?, ?, NULL, ?, 1, 0)""",
                (program_id, pol["id"], pol["policy_type"] or "Unknown"),
            )

    conn.commit()
    logger.info("Assigned %s to program '%s'", policy_uid, tg)

    return JSONResponse({"ok": True})


@router.post("/clients/{client_id}/programs/{tower_group}/unassign/{policy_uid}")
async def unassign_from_program(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_uid: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    conn.execute(
        """UPDATE policies SET tower_group = NULL, program_id = NULL,
           layer_position = NULL, schematic_column = NULL
           WHERE client_id = ? AND policy_uid = ? AND archived = 0""",
        (client_id, policy_uid),
    )
    # Clean up tower coverage and tower line references
    pol = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)).fetchone()
    if pol:
        conn.execute("DELETE FROM program_tower_coverage WHERE underlying_policy_id = ?", (pol["id"],))
        conn.execute("DELETE FROM program_tower_coverage WHERE excess_policy_id = ?", (pol["id"],))
        conn.execute("DELETE FROM program_tower_lines WHERE source_policy_id = ?", (pol["id"],))
    conn.commit()
    logger.info("Unassigned %s from program '%s'", policy_uid, unquote(tower_group))

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Coverage Map (which umbrella/excess covers which underlying lines)
# ---------------------------------------------------------------------------

@router.post("/clients/{client_id}/programs/{tower_group}/coverage-map")
async def add_coverage_mapping(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    excess_policy_id = body.get("excess_policy_id")
    underlying_policy_id = body.get("underlying_policy_id")
    underlying_sub_coverage_id = body.get("underlying_sub_coverage_id")

    if not excess_policy_id or (not underlying_policy_id and not underlying_sub_coverage_id):
        return JSONResponse({"ok": False, "error": "Missing required fields"}, status_code=400)

    conn.execute(
        """INSERT OR IGNORE INTO program_tower_coverage
           (excess_policy_id, underlying_policy_id, underlying_sub_coverage_id)
           VALUES (?, ?, ?)""",
        (excess_policy_id, underlying_policy_id or None, underlying_sub_coverage_id or None),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@router.delete("/clients/{client_id}/programs/{tower_group}/coverage-map/{ptc_id}")
async def remove_coverage_mapping(
    client_id: int,
    tower_group: str,
    ptc_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    conn.execute("DELETE FROM program_tower_coverage WHERE id = ?", (ptc_id,))
    conn.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@router.get(
    "/clients/{client_id}/programs/{tower_group}",
    response_class=HTMLResponse,
)
async def schematic_page(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    # Fetch policies for this tower group (NOT v_tower — need p.id, p.policy_uid)
    rows = conn.execute(
        """
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
               p.limit_amount, p.deductible, p.premium, p.coverage_form,
               p.layer_position, p.attachment_point, p.participation_of,
               p.schematic_column, p.is_program, p.tower_group
        FROM policies p
        WHERE p.client_id = ? AND p.tower_group = ?
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
        ORDER BY COALESCE(p.schematic_column, 999) ASC,
                 COALESCE(p.attachment_point, 0) ASC
        """,
        (client_id, tg),
    ).fetchall()
    policies = [dict(r) for r in rows]

    # Attach sub-coverage metadata (full dicts with limits) to package policies
    all_ids = [p["id"] for p in policies]
    sub_cov_map = get_sub_coverages_by_policy_id(conn, all_ids)
    sub_cov_full_map = get_sub_coverages_full_by_policy_id(conn, all_ids)
    for p in policies:
        subs = sub_cov_map.get(p["id"], [])
        if subs:
            p["is_package_ghost"] = True
            p["package_parent_type"] = p.get("policy_type") or ""
            p["sub_coverages"] = subs
            p["sub_coverages_full"] = sub_cov_full_map.get(p["id"], [])
        else:
            p["is_package_ghost"] = False
            p["sub_coverages"] = []
            p["sub_coverages_full"] = []

    # Split into underlying (Primary) and excess (Umbrella + Excess)
    underlying = []
    excess = []
    for p in policies:
        lp = (p.get("layer_position") or "").strip().lower()
        if lp in ("primary", "") or (not lp and not _is_umbrella_or_excess(p)):
            underlying.append(p)
        else:
            excess.append(p)

    # Promote umbrella/excess sub-coverages from underlying packages to excess list
    _EXCESS_SC_KEYWORDS = ("umbrella", "excess")
    for p in underlying:
        for sc in p.get("sub_coverages_full", []):
            sc_type = (sc.get("coverage_type") or "").lower()
            if any(kw in sc_type for kw in _EXCESS_SC_KEYWORDS) and sc.get("limit_amount"):
                # Create a synthetic excess row from the sub-coverage
                excess.append({
                    "id": p["id"],  # same policy id for PATCH routing
                    "policy_uid": p["policy_uid"],
                    "policy_type": sc["coverage_type"],
                    "carrier": p.get("carrier") or "",
                    "policy_number": p.get("policy_number") or "",
                    "limit_amount": sc["limit_amount"],
                    "deductible": sc.get("deductible") or 0,
                    "premium": 0,
                    "coverage_form": sc.get("coverage_form") or "",
                    "layer_position": "Umbrella" if "umbrella" in sc_type else "Excess",
                    "attachment_point": sc.get("attachment_point") or 0,
                    "participation_of": None,
                    "schematic_column": None,
                    "is_program": 0,
                    "tower_group": p.get("tower_group") or "",
                    "is_package_ghost": True,
                    "package_parent_type": p.get("policy_type") or "",
                    "sub_coverages": [],
                    "sub_coverages_full": [],
                    "_from_sub_coverage": True,
                    "_sub_coverage_id": sc["id"],
                })

    # Sort underlying by schematic_column, excess by attachment_point
    underlying.sort(key=lambda p: p.get("schematic_column") or 999)
    excess.sort(key=lambda p: p.get("attachment_point") or 0)

    # Fetch program_carriers for excess rows with is_program=1
    program_ids = [p["id"] for p in excess if p.get("is_program")]
    carriers_by_program: dict[int, list[dict]] = {}
    if program_ids:
        placeholders = ",".join("?" * len(program_ids))
        carrier_rows = conn.execute(
            f"""
            SELECT id, program_id, carrier, policy_number, premium,
                   limit_amount, sort_order
            FROM program_carriers
            WHERE program_id IN ({placeholders})
            ORDER BY sort_order
            """,
            program_ids,
        ).fetchall()
        for cr in carrier_rows:
            cr_dict = dict(cr)
            pid = cr_dict["program_id"]
            carriers_by_program.setdefault(pid, []).append(cr_dict)

    # Compute notation for each excess row
    for p in excess:
        p["notation"] = _layer_notation(
            p.get("limit_amount"),
            p.get("attachment_point"),
            p.get("participation_of"),
        )
        p["program_carriers"] = carriers_by_program.get(p["id"], [])

    # Client info
    client_row = conn.execute(
        "SELECT id, name, cn_number FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    client_name = client_row["name"] if client_row else "Unknown"

    # Program policy (is_program=1) for header metadata
    program_policy = None
    for p in policies:
        if p.get("is_program"):
            program_policy = p
            break

    # Program header data
    if program_policy:
        pgm_full = conn.execute(
            """SELECT effective_date, expiration_date, renewal_status, premium
            FROM policies WHERE id = ?""",
            (program_policy["id"],),
        ).fetchone()
        if pgm_full:
            program_policy.update(dict(pgm_full))

    # Total premium / limit across all rows
    total_premium = sum(p.get("premium") or 0 for p in policies)
    total_limit = sum(p.get("limit_amount") or 0 for p in policies)

    # Unassigned policies for this client (no tower_group, no program_id)
    unassigned_rows = conn.execute(
        """SELECT policy_uid, policy_type, carrier, premium, limit_amount, id
        FROM policies
        WHERE client_id = ? AND archived = 0
          AND (is_opportunity = 0 OR is_opportunity IS NULL)
          AND is_program = 0
          AND (tower_group IS NULL OR tower_group = '')
          AND program_id IS NULL
        ORDER BY policy_type""",
        (client_id,),
    ).fetchall()
    unassigned = [dict(r) for r in unassigned_rows]

    # Sub-coverages with limits for tower line selection
    all_policy_ids = [p["id"] for p in underlying]
    sub_cov_full_map = get_sub_coverages_full_by_policy_id(conn, all_policy_ids) if all_policy_ids else {}
    for p in underlying:
        p["sub_coverages_full"] = sub_cov_full_map.get(p["id"], [])

    # Tower coverage map: which excess covers which underlying lines (with labels)
    coverage_map_by_excess: dict[int, list[dict]] = {}
    try:
        excess_ids = [p["id"] for p in excess]
        if excess_ids:
            ptc_rows = conn.execute(
                """
                SELECT ptc.id AS ptc_id, ptc.excess_policy_id,
                       ptc.underlying_policy_id, ptc.underlying_sub_coverage_id,
                       COALESCE(up.policy_type, sc.coverage_type) AS covered_label
                FROM program_tower_coverage ptc
                LEFT JOIN policies up ON ptc.underlying_policy_id = up.id
                LEFT JOIN policy_sub_coverages sc ON ptc.underlying_sub_coverage_id = sc.id
                WHERE ptc.excess_policy_id IN ({})
                """.format(",".join("?" * len(excess_ids))),
                excess_ids,
            ).fetchall()
            for r in ptc_rows:
                eid = r["excess_policy_id"]
                coverage_map_by_excess.setdefault(eid, []).append({
                    "ptc_id": r["ptc_id"],
                    "covered_label": r["covered_label"],
                    "underlying_policy_id": r["underlying_policy_id"],
                    "underlying_sub_coverage_id": r["underlying_sub_coverage_id"],
                })
    except Exception:
        pass  # Table may not exist on older DBs

    for p in excess:
        p["covered_lines"] = coverage_map_by_excess.get(p["id"], [])

    # Build list of available underlying labels for "Covers" selector
    # Includes standalone policies AND exploded sub-coverages from packages
    available_lines = []
    _EXCESS_SC_KW = ("umbrella", "excess")
    for p in underlying:
        subs_full = p.get("sub_coverages_full", [])
        subs_with_limits = [s for s in subs_full if s.get("limit_amount")]
        # Filter out umbrella/excess sub-coverages (those are in the excess panel)
        underlying_subs = [s for s in subs_with_limits
                           if not any(kw in (s.get("coverage_type") or "").lower() for kw in _EXCESS_SC_KW)]

        if underlying_subs:
            # Package: show each sub-coverage as a selectable line
            # Also include the parent policy itself (e.g., WC statutory)
            available_lines.append({
                "policy_id": p["id"],
                "sub_coverage_id": None,
                "label": p.get("policy_type") or "Unknown",
            })
            for sc in underlying_subs:
                available_lines.append({
                    "policy_id": p["id"],
                    "sub_coverage_id": sc["id"],
                    "label": sc["coverage_type"],
                })
        else:
            # Standalone policy
            available_lines.append({
                "policy_id": p["id"],
                "sub_coverage_id": None,
                "label": p.get("policy_type") or "Unknown",
            })

    # Config lists
    policy_types = cfg.get("policy_types", [])
    carriers_list = cfg.get("carriers", [])
    coverage_forms = cfg.get("coverage_forms", [])
    renewal_statuses = cfg.get("renewal_statuses", [])

    # Check if umbrella exists
    has_umbrella = any(
        p.get("layer_position") and "umbrella" in p["layer_position"].lower()
        for p in excess
    )

    return templates.TemplateResponse(
        "programs/schematic.html",
        {
            "request": request,
            "client_id": client_id,
            "client_name": client_name,
            "tower_group": tg,
            "underlying": underlying,
            "excess": excess,
            "has_umbrella": has_umbrella,
            "policy_types": policy_types,
            "carriers": carriers_list,
            "coverage_forms": coverage_forms,
            "program_policy": program_policy,
            "total_premium": total_premium,
            "total_limit": total_limit,
            "unassigned": unassigned,
            "renewal_statuses": renewal_statuses,
            "available_lines": available_lines,
        },
    )


def _is_umbrella_or_excess(p: dict) -> bool:
    """Check if a policy looks like umbrella or excess based on type/position."""
    pt = (p.get("policy_type") or "").lower()
    return "umbrella" in pt or "excess" in pt


# ---------------------------------------------------------------------------
# PATCH underlying cell
# ---------------------------------------------------------------------------

@router.patch("/clients/{client_id}/programs/{tower_group}/underlying/{policy_id}/cell")
async def patch_underlying_cell(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    if field not in _UNDERLYING_ALLOWED:
        return JSONResponse(
            {"ok": False, "error": f"Field '{field}' not allowed"},
            status_code=400,
        )

    # Parse currency fields
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value = val
        display_value = formatted
    else:
        db_value = str(value).strip() if value else ""
        display_value = db_value

    conn.execute(
        f"UPDATE policies SET {field} = ?, updated_at = CURRENT_TIMESTAMP "
        f"WHERE id = ? AND client_id = ?",
        (db_value, policy_id, client_id),
    )
    conn.commit()

    return JSONResponse({"ok": True, "formatted": display_value})


# ---------------------------------------------------------------------------
# PATCH excess cell
# ---------------------------------------------------------------------------

@router.patch("/clients/{client_id}/programs/{tower_group}/excess/{policy_id}/cell")
async def patch_excess_cell(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    if field not in _EXCESS_ALLOWED:
        return JSONResponse(
            {"ok": False, "error": f"Field '{field}' not allowed"},
            status_code=400,
        )

    # Parse currency fields
    if field in _CURRENCY_FIELDS:
        val, formatted, err = _parse_and_format_currency(value)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        db_value = val
        display_value = formatted
    else:
        db_value = str(value).strip() if value else ""
        display_value = db_value

    conn.execute(
        f"UPDATE policies SET {field} = ?, updated_at = CURRENT_TIMESTAMP "
        f"WHERE id = ? AND client_id = ?",
        (db_value, policy_id, client_id),
    )
    conn.commit()

    # Compute notation from current row state and cache it
    row = conn.execute(
        "SELECT limit_amount, attachment_point, participation_of "
        "FROM policies WHERE id = ?",
        (policy_id,),
    ).fetchone()
    notation = ""
    if row:
        notation = _layer_notation(
            row["limit_amount"], row["attachment_point"], row["participation_of"]
        )
        conn.execute(
            "UPDATE policies SET layer_notation = ? WHERE id = ?",
            (notation, policy_id),
        )
        conn.commit()

    return JSONResponse({
        "ok": True,
        "formatted": display_value,
        "notation": notation,
    })


# ---------------------------------------------------------------------------
# Add underlying
# ---------------------------------------------------------------------------

@router.post(
    "/clients/{client_id}/programs/{tower_group}/underlying/add",
    response_class=HTMLResponse,
)
async def add_underlying(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    # Next schematic column
    col_row = conn.execute(
        """
        SELECT COALESCE(MAX(schematic_column), 0) + 1 AS next_col
        FROM policies
        WHERE client_id = ? AND tower_group = ?
          AND layer_position = 'Primary'
          AND archived = 0
        """,
        (client_id, tg),
    ).fetchone()
    next_col = col_row["next_col"] if col_row else 1

    uid = next_policy_uid(conn)

    conn.execute(
        """
        INSERT INTO policies (
            policy_uid, client_id, tower_group, layer_position, schematic_column,
            policy_type, carrier, policy_number, limit_amount, deductible,
            premium, coverage_form, first_named_insured, description, notes
        ) VALUES (?, ?, ?, 'Primary', ?, '', '', '', 0, 0, 0, '', '', '', '')
        """,
        (uid, client_id, tg, next_col),
    )
    conn.commit()

    # Fetch the new row
    new_row = conn.execute(
        """
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
               p.limit_amount, p.deductible, p.premium, p.coverage_form,
               p.layer_position, p.attachment_point, p.participation_of,
               p.schematic_column, p.is_program, p.tower_group
        FROM policies p
        WHERE p.policy_uid = ?
        """,
        (uid,),
    ).fetchone()

    policy_types = cfg.get("policy_types", [])
    carriers = cfg.get("carriers", [])
    coverage_forms = cfg.get("coverage_forms", [])

    return templates.TemplateResponse(
        "programs/_underlying_row.html",
        {
            "request": request,
            "p": dict(new_row),
            "client_id": client_id,
            "tower_group": tg,
            "policy_types": policy_types,
            "carriers": carriers,
            "coverage_forms": coverage_forms,
        },
    )


# ---------------------------------------------------------------------------
# Add excess
# ---------------------------------------------------------------------------

@router.post(
    "/clients/{client_id}/programs/{tower_group}/excess/add",
    response_class=HTMLResponse,
)
async def add_excess(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    # Auto-calculate attachment point = top of current excess stack
    att_row = conn.execute(
        """
        SELECT COALESCE(
            MAX(COALESCE(attachment_point, 0) + COALESCE(participation_of, limit_amount, 0)),
            0
        ) AS next_att
        FROM policies
        WHERE client_id = ? AND tower_group = ?
          AND layer_position != 'Primary'
          AND (layer_position IS NULL OR layer_position NOT LIKE '%mbrella%')
          AND archived = 0
        """,
        (client_id, tg),
    ).fetchone()
    attachment = att_row["next_att"] if att_row else 0

    uid = next_policy_uid(conn)

    conn.execute(
        """
        INSERT INTO policies (
            policy_uid, client_id, tower_group, layer_position, policy_type,
            attachment_point, limit_amount, carrier, policy_number, premium,
            coverage_form, first_named_insured, description, notes
        ) VALUES (?, ?, ?, 'Excess', 'Excess Liability', ?, 0, '', '', 0, '', '', '', '')
        """,
        (uid, client_id, tg, attachment),
    )
    conn.commit()

    # Fetch the new row
    new_row = conn.execute(
        """
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
               p.limit_amount, p.deductible, p.premium, p.coverage_form,
               p.layer_position, p.attachment_point, p.participation_of,
               p.schematic_column, p.is_program, p.tower_group
        FROM policies p
        WHERE p.policy_uid = ?
        """,
        (uid,),
    ).fetchone()

    row_dict = dict(new_row)
    row_dict["notation"] = _layer_notation(
        row_dict.get("limit_amount"),
        row_dict.get("attachment_point"),
        row_dict.get("participation_of"),
    )
    row_dict["program_carriers"] = []

    carriers = cfg.get("carriers", [])
    coverage_forms = cfg.get("coverage_forms", [])

    return templates.TemplateResponse(
        "programs/_excess_row.html",
        {
            "request": request,
            "p": row_dict,
            "layer_num": "—",
            "client_id": client_id,
            "tower_group": tg,
            "carriers": carriers,
            "coverage_forms": coverage_forms,
        },
    )


# ---------------------------------------------------------------------------
# Add umbrella
# ---------------------------------------------------------------------------

@router.post(
    "/clients/{client_id}/programs/{tower_group}/umbrella/add",
    response_class=HTMLResponse,
)
async def add_umbrella(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    # Check if umbrella already exists
    existing = conn.execute(
        """
        SELECT id FROM policies
        WHERE client_id = ? AND tower_group = ?
          AND (layer_position LIKE '%mbrella%')
          AND archived = 0
        LIMIT 1
        """,
        (client_id, tg),
    ).fetchone()

    if existing:
        return JSONResponse(
            {"ok": False, "error": "Umbrella already exists"},
            status_code=409,
        )

    uid = next_policy_uid(conn)

    conn.execute(
        """
        INSERT INTO policies (
            policy_uid, client_id, tower_group, layer_position, policy_type,
            attachment_point, limit_amount, carrier, policy_number, premium,
            coverage_form, first_named_insured, description, notes
        ) VALUES (?, ?, ?, 'Umbrella', 'Umbrella Liability', 0, 0, '', '', 0, '', '', '', '')
        """,
        (uid, client_id, tg),
    )
    conn.commit()

    # Fetch the new row
    new_row = conn.execute(
        """
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
               p.limit_amount, p.deductible, p.premium, p.coverage_form,
               p.layer_position, p.attachment_point, p.participation_of,
               p.schematic_column, p.is_program, p.tower_group
        FROM policies p
        WHERE p.policy_uid = ?
        """,
        (uid,),
    ).fetchone()

    row_dict = dict(new_row)
    row_dict["notation"] = _layer_notation(
        row_dict.get("limit_amount"),
        row_dict.get("attachment_point"),
        row_dict.get("participation_of"),
    )
    row_dict["program_carriers"] = []

    carriers = cfg.get("carriers", [])
    coverage_forms = cfg.get("coverage_forms", [])

    return templates.TemplateResponse(
        "programs/_excess_row.html",
        {
            "request": request,
            "p": row_dict,
            "layer_num": "—",
            "client_id": client_id,
            "tower_group": tg,
            "carriers": carriers,
            "coverage_forms": coverage_forms,
        },
    )


# ---------------------------------------------------------------------------
# Reorder underlying
# ---------------------------------------------------------------------------

@router.post("/clients/{client_id}/programs/{tower_group}/underlying/reorder")
async def reorder_underlying(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    order = body.get("order", [])

    for i, pid in enumerate(order):
        conn.execute(
            "UPDATE policies SET schematic_column = ? WHERE id = ? AND client_id = ?",
            (i + 1, int(pid), client_id),
        )
    conn.commit()

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Delete underlying
# ---------------------------------------------------------------------------

@router.delete(
    "/clients/{client_id}/programs/{tower_group}/underlying/{policy_id}",
    response_class=HTMLResponse,
)
async def delete_underlying(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    conn.execute(
        "DELETE FROM policies WHERE id = ? AND client_id = ?",
        (policy_id, client_id),
    )

    # Renumber remaining underlying columns
    remaining = conn.execute(
        """
        SELECT id FROM policies
        WHERE client_id = ? AND tower_group = ?
          AND layer_position = 'Primary'
          AND archived = 0
        ORDER BY schematic_column
        """,
        (client_id, tg),
    ).fetchall()

    for i, row in enumerate(remaining):
        conn.execute(
            "UPDATE policies SET schematic_column = ? WHERE id = ?",
            (i + 1, row["id"]),
        )
    conn.commit()

    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Delete excess
# ---------------------------------------------------------------------------

@router.delete(
    "/clients/{client_id}/programs/{tower_group}/excess/{policy_id}",
    response_class=HTMLResponse,
)
async def delete_excess(
    request: Request,
    client_id: int,
    tower_group: str,
    policy_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    conn.execute(
        "DELETE FROM program_carriers WHERE program_id = ?",
        (policy_id,),
    )
    conn.execute(
        "DELETE FROM policies WHERE id = ? AND client_id = ?",
        (policy_id, client_id),
    )
    conn.commit()

    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Preview (tower chart)
# ---------------------------------------------------------------------------

@router.get(
    "/clients/{client_id}/programs/{tower_group}/preview",
    response_class=HTMLResponse,
)
async def schematic_preview(
    request: Request,
    client_id: int,
    tower_group: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    tg = unquote(tower_group)

    all_data = get_tower_data(conn, client_id)
    tower_data = [t for t in all_data if t.get("tower_group") == tg]

    return templates.TemplateResponse(
        "programs/_schematic_preview.html",
        {
            "request": request,
            "tower_data": tower_data,
            "client_id": client_id,
            "tower_group": tg,
        },
    )
