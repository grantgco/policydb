"""Program Schematic Entry routes."""

from __future__ import annotations

import logging
import sqlite3
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

import policydb.config as cfg
from policydb.charts import _layer_notation, get_tower_data
from policydb.db import next_policy_uid
from policydb.queries import get_sub_coverages_by_policy_id
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

    # Attach sub-coverage metadata to package policies
    all_ids = [p["id"] for p in policies]
    sub_cov_map = get_sub_coverages_by_policy_id(conn, all_ids)
    for p in policies:
        subs = sub_cov_map.get(p["id"], [])
        if subs:
            p["is_package_ghost"] = True
            p["package_parent_type"] = p.get("policy_type") or ""
            p["sub_coverages"] = subs
        else:
            p["is_package_ghost"] = False
            p["sub_coverages"] = []

    # Split into underlying (Primary) and excess (Umbrella + Excess)
    underlying = []
    excess = []
    for p in policies:
        lp = (p.get("layer_position") or "").strip().lower()
        if lp in ("primary", "") or (not lp and not _is_umbrella_or_excess(p)):
            underlying.append(p)
        else:
            excess.append(p)

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

    # Client name
    client_row = conn.execute(
        "SELECT name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    client_name = client_row["name"] if client_row else "Unknown"

    # Config lists
    policy_types = cfg.get("policy_types", [])
    carriers = cfg.get("carriers", [])
    coverage_forms = cfg.get("coverage_forms", [])

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
            "carriers": carriers,
            "coverage_forms": coverage_forms,
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

    # Compute notation from current row state
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
