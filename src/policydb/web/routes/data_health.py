"""Data Health routes — API endpoints + Action Center tab."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg
from policydb.data_health import (
    compute_health_score,
    detect_stage,
    get_book_health_summary,
    get_field_last_changed,
    get_missing_fields_report,
    score_client,
    score_policies,
)

router = APIRouter()


# ── API endpoints ────────────────────────────────────────────────────────────


@router.get("/api/health/summary")
def api_health_summary(conn=Depends(get_db)):
    """Book-wide health summary stats."""
    return get_book_health_summary(conn)


@router.get("/api/health/client/{client_id}")
def api_health_client(client_id: int, conn=Depends(get_db)):
    """Client health score + breakdown."""
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "Client not found"}, status_code=404)
    client = dict(row)
    score_client(conn, client, include_staleness=True)
    return {
        "score": client["health_score"],
        "client_score": client["health_client_score"],
        "policy_score": client["health_policy_score"],
        "missing": client["health_missing"],
        "stale": client["health_stale"],
        "stage": client["health_stage"],
    }


@router.get("/api/health/policy/{policy_uid}")
def api_health_policy(policy_uid: str, conn=Depends(get_db)):
    """Policy health score + breakdown."""
    row = conn.execute(
        "SELECT * FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if not row:
        return JSONResponse({"error": "Policy not found"}, status_code=404)
    p = dict(row)
    field_config = cfg.get("data_health_fields", {})
    stage = detect_stage(p)
    stale_fields = [f["field"] for f in field_config.get("policy", []) if f.get("decay_days")]
    field_dates = get_field_last_changed(conn, "policies", policy_uid, stale_fields)
    result = compute_health_score(p, "policy", stage, field_config, field_dates)
    return {
        "score": result["score"],
        "missing": result["missing"],
        "stale": result["stale"],
        "filled": result["filled"],
        "total": result["total"],
        "stage": stage,
    }


# ── Fill Blitz ───────────────────────────────────────────────────────────────


@router.get("/api/health/blitz/next", response_class=HTMLResponse)
def blitz_next(request: Request, offset: int = 0, conn=Depends(get_db)):
    """Return the next missing field card for Fill Blitz mode."""
    missing = get_missing_fields_report(conn)
    if offset >= len(missing):
        return HTMLResponse(
            '<div class="text-center py-8 text-gray-500">'
            '<div class="text-2xl mb-2">All caught up!</div>'
            '<div class="text-sm">No more missing fields to fill.</div>'
            '</div>'
        )

    item = missing[offset]
    return templates.TemplateResponse("action_center/_blitz_card.html", {
        "request": request,
        "item": item,
        "offset": offset,
        "total": len(missing),
    })


@router.patch("/api/health/blitz/save", response_class=HTMLResponse)
def blitz_save(
    request: Request,
    table: str = Form(...),
    record_id: str = Form(...),
    field: str = Form(...),
    value: str = Form(""),
    offset: int = Form(0),
    conn=Depends(get_db),
):
    """Save a single field from Fill Blitz, return next card."""
    if table not in ("clients", "policies"):
        return JSONResponse({"error": "Invalid table"}, status_code=400)

    field_config = cfg.get("data_health_fields", {})
    record_type = "client" if table == "clients" else "policy"
    valid_fields = [f["field"] for f in field_config.get(record_type, [])]
    if field not in valid_fields:
        return JSONResponse({"error": "Invalid field"}, status_code=400)

    # Validate field is an actual DB column (config may reference non-existent columns)
    _POLICY_COLUMNS = {
        "carrier", "premium", "effective_date", "expiration_date",
        "policy_type", "policy_number", "renewal_status",
        "first_named_insured", "limit_amount", "deductible",
        "attachment_point", "description", "access_point",
        "opportunity_status", "target_effective_date",
        "prior_premium", "exposure_amount", "coverage_form",
        "layer_position", "notes", "policy_number_unknown",
    }
    _CLIENT_COLUMNS = {
        "industry_segment", "account_exec", "cn_number", "name",
    }
    allowed_cols = _POLICY_COLUMNS if table == "policies" else _CLIENT_COLUMNS
    if field not in allowed_cols:
        return JSONResponse(
            {"error": f"Field '{field}' is not a valid column"},
            status_code=400,
        )

    save_value = value.strip()
    # Parse currency fields through the standard parser
    currency_fields = {"premium", "limit_amount", "deductible", "attachment_point",
                       "prior_premium", "exposure_amount"}
    if field in currency_fields:
        from policydb.utils import parse_currency_with_magnitude
        parsed = parse_currency_with_magnitude(save_value)
        if parsed is not None:
            save_value = parsed

    if table == "policies":
        conn.execute(
            f"UPDATE policies SET {field} = ? WHERE policy_uid = ?",  # noqa: S608
            (save_value, record_id),
        )
    else:
        try:
            client_id = int(record_id)
        except (ValueError, TypeError):
            return JSONResponse({"error": "Invalid client ID"}, status_code=400)
        conn.execute(
            f"UPDATE clients SET {field} = ? WHERE id = ?",  # noqa: S608
            (save_value, client_id),
        )
    conn.commit()

    return blitz_next(request, offset=offset + 1, conn=conn)
