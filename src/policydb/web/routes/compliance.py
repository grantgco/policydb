"""Compliance routes — contract review, requirement tracking, coverage gap analysis."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb import config as cfg
from policydb.compliance import (
    get_client_compliance_data,
    get_risk_review_prompts,
)
from policydb.queries import get_client_by_id
from policydb.utils import parse_currency_with_magnitude
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/compliance", tags=["compliance"])

# Allowed fields for cell-patch updates
_CELL_ALLOWED_FIELDS = {
    "coverage_line",
    "required_limit",
    "max_deductible",
    "deductible_type",
    "required_endorsements",
    "compliance_status",
    "linked_policy_uid",
    "notes",
}


def _compliance_context(conn: sqlite3.Connection, client_id: int, request: Request) -> dict:
    """Build full template context for the compliance index page."""
    client = get_client_by_id(conn, client_id)
    data = get_client_compliance_data(conn, client_id)

    cfg_prompts = cfg.get("risk_review_prompts", [])
    risk_prompts = get_risk_review_prompts(
        client=dict(client),
        locations=[loc["project"] for loc in data["locations"]],
        policies=data["all_policies"],
        cfg_prompts=cfg_prompts,
    )

    return {
        "request": request,
        "client": client,
        "client_id": client_id,
        "locations": data["locations"],
        "client_requirements": data["client_requirements"],
        "sources": data["sources"],
        "all_policies": data["all_policies"],
        "overall_summary": data["overall_summary"],
        "risk_prompts": risk_prompts,
        # Config values
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "deductible_types": cfg.get("deductible_types", []),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
        "risk_review_prompt_categories": cfg.get("risk_review_prompt_categories", []),
    }


# ── Literal routes first ───────────────────────────────────────────────────────

# (none currently — all routes are parameterized)

# ── Main page ─────────────────────────────────────────────────────────────────

@router.get("/client/{client_id}", response_class=HTMLResponse)
def compliance_index(client_id: int, request: Request, conn=Depends(get_db)):
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Sources CRUD ──────────────────────────────────────────────────────────────

@router.post("/client/{client_id}/sources/add", response_class=HTMLResponse)
def sources_add(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    name: str = Form(...),
    counterparty: str = Form(""),
    clause_ref: str = Form(""),
    notes: str = Form(""),
):
    conn.execute(
        """INSERT INTO requirement_sources (client_id, name, counterparty, clause_ref, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (client_id, name.strip(), counterparty.strip(), clause_ref.strip(), notes.strip()),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/sources/{source_id}/edit", response_class=HTMLResponse)
def sources_edit(
    client_id: int,
    source_id: int,
    request: Request,
    conn=Depends(get_db),
    name: str = Form(...),
    counterparty: str = Form(""),
    clause_ref: str = Form(""),
    notes: str = Form(""),
):
    conn.execute(
        """UPDATE requirement_sources
           SET name=?, counterparty=?, clause_ref=?, notes=?
           WHERE id=? AND client_id=?""",
        (name.strip(), counterparty.strip(), clause_ref.strip(), notes.strip(), source_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.get("/client/{client_id}/sources/{source_id}/row/edit", response_class=HTMLResponse)
def sources_row_edit(
    client_id: int,
    source_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return inline edit form for a source row."""
    source = conn.execute(
        "SELECT * FROM requirement_sources WHERE id=? AND client_id=?",
        (source_id, client_id),
    ).fetchone()
    return templates.TemplateResponse(
        "compliance/_source_row_edit.html",
        {"request": request, "src": dict(source), "client_id": client_id},
    )


@router.get("/client/{client_id}/sources/{source_id}/row", response_class=HTMLResponse)
def sources_row_display(
    client_id: int,
    source_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return display row for a source (cancel edit)."""
    ctx = _compliance_context(conn, client_id, request)
    source = conn.execute(
        "SELECT * FROM requirement_sources WHERE id=? AND client_id=?",
        (source_id, client_id),
    ).fetchone()
    return templates.TemplateResponse(
        "compliance/_source_row.html",
        {"request": request, "src": dict(source), "client_id": client_id, "locations": ctx["locations"]},
    )


@router.get("/client/{client_id}/requirements/{req_id}/row/edit", response_class=HTMLResponse)
def requirements_row_edit(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return inline edit form for a requirement row."""
    req = conn.execute(
        "SELECT * FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    ).fetchone()
    import json as _json
    req_dict = dict(req)
    # Parse endorsements JSON for template
    try:
        req_dict["_endorsements_list"] = _json.loads(req_dict.get("required_endorsements") or "[]")
    except (ValueError, TypeError):
        req_dict["_endorsements_list"] = []

    sources = [dict(r) for r in conn.execute(
        "SELECT id, name FROM requirement_sources WHERE client_id=?", (client_id,)
    ).fetchall()]
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id=?", (client_id,)
    ).fetchall()]

    return templates.TemplateResponse(
        "compliance/_requirement_row_edit.html",
        {
            "request": request,
            "req": req_dict,
            "client_id": client_id,
            "sources": sources,
            "projects": projects,
            "compliance_statuses": cfg.get("compliance_statuses", []),
            "deductible_types": cfg.get("deductible_types", []),
            "policy_types": cfg.get("policy_types", []),
            "endorsement_types": cfg.get("endorsement_types", []),
        },
    )


@router.post("/client/{client_id}/sources/{source_id}/delete", response_class=HTMLResponse)
def sources_delete(
    client_id: int,
    source_id: int,
    request: Request,
    conn=Depends(get_db),
):
    # Cascade: delete requirements linked to this source
    conn.execute(
        "DELETE FROM coverage_requirements WHERE source_id=? AND client_id=?",
        (source_id, client_id),
    )
    conn.execute(
        "DELETE FROM requirement_sources WHERE id=? AND client_id=?",
        (source_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Requirements CRUD ─────────────────────────────────────────────────────────

@router.post("/client/{client_id}/requirements/add", response_class=HTMLResponse)
def requirements_add(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    coverage_line: str = Form(...),
    project_id: str = Form(""),
    source_id: str = Form(""),
    risk_id: str = Form(""),
    required_limit: str = Form(""),
    max_deductible: str = Form(""),
    deductible_type: str = Form(""),
    compliance_status: str = Form("Needs Review"),
    linked_policy_uid: str = Form(""),
    notes: str = Form(""),
    required_endorsements: str = Form("[]"),
):
    def _int_or_none(v: str):
        try:
            return int(v) if v.strip() else None
        except (ValueError, AttributeError):
            return None

    def _money_or_none(v: str):
        """Parse currency shorthand (e.g., '1m' → 1000000, '500k' → 500000)."""
        if not v or not v.strip():
            return None
        parsed = parse_currency_with_magnitude(v)
        return parsed if parsed else None

    # Parse endorsements: accepts JSON array string or comma-separated
    import json as _json
    try:
        endorsements = _json.dumps(_json.loads(required_endorsements))
    except (ValueError, TypeError):
        endorsements = _json.dumps([e.strip() for e in required_endorsements.split(",") if e.strip()])

    conn.execute(
        """INSERT INTO coverage_requirements (
               client_id, project_id, source_id, risk_id, coverage_line,
               required_limit, max_deductible, deductible_type,
               compliance_status, linked_policy_uid, notes,
               required_endorsements
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            client_id,
            _int_or_none(project_id),
            _int_or_none(source_id),
            _int_or_none(risk_id),
            coverage_line.strip(),
            _money_or_none(required_limit),
            _money_or_none(max_deductible),
            deductible_type.strip() or None,
            compliance_status.strip() or "Needs Review",
            linked_policy_uid.strip() or None,
            notes.strip() or None,
            endorsements,
        ),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.patch("/client/{client_id}/requirements/{req_id}/cell", response_class=HTMLResponse)
async def requirements_cell(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    if field not in _CELL_ALLOWED_FIELDS:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)

    conn.execute(
        f"UPDATE coverage_requirements SET {field}=? WHERE id=? AND client_id=?",
        (value or None, req_id, client_id),
    )
    conn.commit()

    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/_matrix.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/edit", response_class=HTMLResponse)
def requirements_edit(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    coverage_line: str = Form(...),
    project_id: str = Form(""),
    source_id: str = Form(""),
    risk_id: str = Form(""),
    required_limit: str = Form(""),
    max_deductible: str = Form(""),
    deductible_type: str = Form(""),
    compliance_status: str = Form("Needs Review"),
    linked_policy_uid: str = Form(""),
    notes: str = Form(""),
    required_endorsements: str = Form("[]"),
):
    def _int_or_none(v: str):
        try:
            return int(v) if v.strip() else None
        except (ValueError, AttributeError):
            return None

    def _money_or_none(v: str):
        """Parse currency shorthand (e.g., '1m' → 1000000, '500k' → 500000)."""
        if not v or not v.strip():
            return None
        parsed = parse_currency_with_magnitude(v)
        return parsed if parsed else None

    import json as _json
    try:
        endorsements = _json.dumps(_json.loads(required_endorsements))
    except (ValueError, TypeError):
        endorsements = _json.dumps([e.strip() for e in required_endorsements.split(",") if e.strip()])

    conn.execute(
        """UPDATE coverage_requirements
           SET coverage_line=?, project_id=?, source_id=?, risk_id=?,
               required_limit=?, max_deductible=?, deductible_type=?,
               compliance_status=?, linked_policy_uid=?, notes=?,
               required_endorsements=?
           WHERE id=? AND client_id=?""",
        (
            coverage_line.strip(),
            _int_or_none(project_id),
            _int_or_none(source_id),
            _int_or_none(risk_id),
            _money_or_none(required_limit),
            _money_or_none(max_deductible),
            deductible_type.strip() or None,
            compliance_status.strip() or "Needs Review",
            linked_policy_uid.strip() or None,
            notes.strip() or None,
            endorsements,
            req_id,
            client_id,
        ),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/status", response_class=HTMLResponse)
def requirements_status(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    compliance_status: str = Form(...),
):
    conn.execute(
        "UPDATE coverage_requirements SET compliance_status=? WHERE id=? AND client_id=?",
        (compliance_status.strip(), req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/_matrix.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/link-policy", response_class=HTMLResponse)
def requirements_link_policy(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    linked_policy_uid: str = Form(""),
):
    conn.execute(
        "UPDATE coverage_requirements SET linked_policy_uid=? WHERE id=? AND client_id=?",
        (linked_policy_uid.strip() or None, req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


@router.post("/client/{client_id}/requirements/{req_id}/delete", response_class=HTMLResponse)
def requirements_delete(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    )
    conn.commit()
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/index.html", ctx)


# ── Location detail ───────────────────────────────────────────────────────────

@router.get("/client/{client_id}/location/{project_id}", response_class=HTMLResponse)
def location_detail(
    client_id: int,
    project_id: int,
    request: Request,
    conn=Depends(get_db),
):
    ctx = _compliance_context(conn, client_id, request)

    # Find the specific location data
    location_data = next(
        (loc for loc in ctx["locations"] if loc["project"]["id"] == project_id),
        None,
    )
    ctx["location_data"] = location_data
    ctx["project"] = location_data["project"] if location_data else {}

    return templates.TemplateResponse("compliance/_location_detail.html", ctx)
