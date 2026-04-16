"""Renew Policies routes — slideover panel preview + submit + batch edit grid."""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.renew_policies import (
    RenewChildPayload,
    RenewPayload,
    RenewSubject,
    RenewSubjectPayload,
    execute_create_renewals,
    preview_renew_panel,
)
from policydb.web.app import get_db, templates

logger = logging.getLogger("policydb.renew_policies")
router = APIRouter(tags=["renew_policies"])


def _parse_subjects_param(raw: str) -> list[RenewSubject]:
    if not raw:
        return []
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    parsed: list[RenewSubject] = []
    for t in tokens:
        try:
            parsed.append(RenewSubject.parse(t))
        except ValueError as e:
            logger.warning("Skipping invalid subject token: %s (%s)", t, e)
    return parsed


@router.get("/renew-policies/panel", response_class=HTMLResponse)
def renew_policies_panel(
    request: Request,
    subjects: str = Query(..., description="comma-separated tokens like 'program:PGM-042,policy:POL-017'"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Render the Renew Policies slideover panel for the given subjects."""
    parsed = _parse_subjects_param(subjects)
    if not parsed:
        raise HTTPException(status_code=400, detail="No valid subjects provided")

    panel = preview_renew_panel(conn, parsed)
    return templates.TemplateResponse("renew_policies/_panel.html", {
        "request": request,
        "panel": panel,
    })


@router.post("/renew-policies/submit")
async def renew_policies_submit(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create new term rows for every checked child across all subjects.

    Returns JSON with `edit_grid_url` pointing to the batch edit grid for the
    newly created rows. The frontend redirects there on success.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    from datetime import date
    bind_date = (body.get("bind_date") or date.today().isoformat()).strip()
    bind_note = body.get("bind_note") or ""
    return_to = (body.get("return_to") or "").strip()
    raw_subjects = body.get("subjects") or []
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise HTTPException(status_code=400, detail="Missing 'subjects' list")

    payload_subjects: list[RenewSubjectPayload] = []
    for sub in raw_subjects:
        try:
            children_raw = sub.get("children") or []
            children = [
                RenewChildPayload(
                    policy_uid=str(c.get("policy_uid") or "").strip().upper(),
                    checked=bool(c.get("checked")),
                    disposition=(c.get("disposition") or None),
                    new_premium=(float(c["new_premium"]) if c.get("new_premium") not in (None, "") else None),
                )
                for c in children_raw
                if c.get("policy_uid")
            ]
            payload_subjects.append(RenewSubjectPayload(
                subject_type=sub["subject_type"],
                subject_uid=str(sub["subject_uid"]).strip().upper(),
                new_effective=str(sub.get("new_effective") or "").strip(),
                new_expiration=str(sub.get("new_expiration") or "").strip(),
                children=children,
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid subject payload: {e}")

    payload = RenewPayload(
        bind_date=bind_date,
        bind_note=bind_note,
        subjects=payload_subjects,
    )

    try:
        result = execute_create_renewals(conn, payload)
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        conn.rollback()
        logger.exception("Renewal batch execution failed")
        raise HTTPException(status_code=500, detail=f"Renewal failed: {e}")

    # Build edit-grid URL pointing to the newly created UIDs
    edit_grid_url = None
    if result.new_uids:
        from urllib.parse import urlencode
        params = {"uids": ",".join(result.new_uids)}
        if return_to:
            params["return_to"] = return_to
        edit_grid_url = "/renew-policies/edit-grid?" + urlencode(params)

    return JSONResponse({
        "ok": True,
        "new_uids": result.new_uids,
        "excepted_count": result.excepted_count,
        "skipped_already_renewed": result.skipped_already_renewed,
        "batch_ids": result.batch_ids,
        "edit_grid_url": edit_grid_url,
        "toast_message": result.toast_message,
    })


@router.get("/renew-policies/edit-grid", response_class=HTMLResponse)
def renew_policies_edit_grid(
    request: Request,
    uids: str = Query(..., description="Comma-separated list of newly-created POL-NNN uids"),
    return_to: str = Query("", description="Optional URL to return to when done"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Batch edit grid — Tabulator showing only the new term rows for inline editing."""
    from policydb.queries import get_all_policies_for_grid, get_projects_by_client

    uid_list = [u.strip().upper() for u in uids.split(",") if u.strip()]
    if not uid_list:
        raise HTTPException(status_code=400, detail="No uids provided")

    # Query only the requested UIDs rather than fetching every row and
    # filtering in Python — the edit grid will only ever display a handful
    # of newly-created rows, so we shouldn't scale with total book size.
    rows = get_all_policies_for_grid(conn, uids=uid_list)

    if not rows:
        raise HTTPException(status_code=404, detail="None of the requested uids exist (or they're archived)")

    projects_by_client = get_projects_by_client(conn)
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    client_list = [{"id": c["id"], "name": c["name"]} for c in clients]
    client_names = [c["name"] for c in client_list]

    policy_types = cfg.get("policy_types", [])
    carriers = cfg.get("carriers", [])
    renewal_statuses = cfg.get("renewal_statuses", [])
    coverage_forms = cfg.get("coverage_forms", [])
    layer_positions = cfg.get("layer_positions", [])

    # Column set tuned for new-term data entry: carrier + policy # up front,
    # then limits / retro / endorsement-shaped fields. Reuses the same field
    # names as /policies/spreadsheet so the shared /policies/{uid}/cell PATCH
    # endpoint Just Works.
    columns = [
        {"field": "client_name", "title": "Client", "width": 180,
         "editor": "list", "editorParams": {"values": client_names, "autocomplete": True, "freetext": False, "listOnEmpty": True},
         "headerFilter": "input", "_format": "link"},
        {"field": "policy_uid", "title": "UID", "width": 90,
         "headerFilter": "input", "_format": "link"},
        {"field": "policy_type", "title": "Line of Business", "width": 160,
         "editor": "list", "editorParams": {"values": policy_types, "autocomplete": True, "freetext": True, "listOnEmpty": True},
         "headerFilter": "input"},
        {"field": "carrier", "title": "Carrier", "width": 160,
         "editor": "list", "editorParams": {"values": carriers, "autocomplete": True, "freetext": True, "listOnEmpty": True},
         "headerFilter": "input"},
        {"field": "policy_number", "title": "Policy #", "width": 140,
         "editor": "input", "headerFilter": "input"},
        {"field": "effective_date", "title": "Effective", "width": 120,
         "editor": "date", "_format": "date"},
        {"field": "expiration_date", "title": "Expiration", "width": 120,
         "editor": "date", "_format": "date"},
        {"field": "premium", "title": "Premium", "width": 120,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "currency"},
        {"field": "limit_amount", "title": "Limit", "width": 120,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "currency"},
        {"field": "deductible", "title": "Deductible", "width": 110,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "currency"},
        {"field": "attachment_point", "title": "Attachment Pt", "width": 130,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "currency"},
        {"field": "participation_of", "title": "Participation", "width": 120,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "currency"},
        {"field": "coverage_form", "title": "Form", "width": 110,
         "editor": "list", "editorParams": {"values": coverage_forms, "autocomplete": True, "freetext": True, "listOnEmpty": True}},
        {"field": "layer_position", "title": "Layer", "width": 100,
         "editor": "list", "editorParams": {"values": layer_positions, "autocomplete": True, "freetext": True, "listOnEmpty": True}},
        {"field": "renewal_status", "title": "Status", "width": 130,
         "editor": "list", "editorParams": {"values": renewal_statuses, "autocomplete": True, "freetext": False, "listOnEmpty": True},
         "_format": "status_pill"},
        {"field": "commission_rate", "title": "Comm %", "width": 90,
         "editor": "number", "editorParams": {"selectContents": True},
         "_format": "percent"},
        {"field": "placement_colleague", "title": "Placement Colleague", "width": 170,
         "editor": "input"},
        {"field": "underwriter_name", "title": "Underwriter", "width": 150,
         "editor": "input"},
        {"field": "first_named_insured", "title": "First Named Insured", "width": 180,
         "editor": "input"},
        {"field": "description", "title": "Description", "width": 200,
         "editor": "input"},
        {"field": "notes", "title": "Notes", "width": 200,
         "editor": "input"},
    ]

    return templates.TemplateResponse("renew_policies/edit_grid.html", {
        "request": request,
        "active": "spreadsheet",
        "rows": rows,
        "columns": columns,
        "projects_by_client": projects_by_client,
        "client_list": client_list,
        "return_to": return_to,
    })


@router.post("/renew-policies/legacy-redirect")
def legacy_bind_order_redirect(request: Request):
    """Placeholder for future 308 redirects if old /bind-order URLs need to survive."""
    raise HTTPException(status_code=404)
