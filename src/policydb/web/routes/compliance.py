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

    # Simple projects list for dropdowns
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id=? ORDER BY name", (client_id,)
    ).fetchall()]

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
        "projects": projects,
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
    project_id: str = Form(""),
):
    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (ValueError, TypeError):
            return None

    conn.execute(
        """INSERT INTO requirement_sources (client_id, project_id, name, counterparty, clause_ref, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, _int_or_none(project_id), name.strip(), counterparty.strip(), clause_ref.strip(), notes.strip()),
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
    project_id: str = Form(""),
):
    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (ValueError, TypeError):
            return None

    conn.execute(
        """UPDATE requirement_sources
           SET name=?, counterparty=?, clause_ref=?, notes=?, project_id=?
           WHERE id=? AND client_id=?""",
        (name.strip(), counterparty.strip(), clause_ref.strip(), notes.strip(), _int_or_none(project_id), source_id, client_id),
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
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id=? ORDER BY name", (client_id,)
    ).fetchall()]
    return templates.TemplateResponse(
        "compliance/_source_row_edit.html",
        {"request": request, "src": dict(source), "client_id": client_id, "projects": projects},
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
        {"request": request, "src": dict(source), "client_id": client_id, "locations": ctx["locations"], "projects": ctx["projects"]},
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
    coverage_line: str = Form(""),
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
            coverage_line.strip() or "",
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


# ── Review Mode (rapid entry) ─────────────────────────────────────────────────

@router.get("/client/{client_id}/review-mode", response_class=HTMLResponse)
def review_mode_panel(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    source_id: str = "",
):
    """Return review mode panel partial with source-scoped requirements table."""
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Default to first source if none selected
    sid = None
    if source_id.strip():
        try:
            sid = int(source_id)
        except ValueError:
            pass
    if sid is None and sources:
        sid = sources[0]["id"]

    # Get requirements for selected source
    requirements = []
    if sid:
        requirements = [dict(r) for r in conn.execute(
            """SELECT * FROM coverage_requirements
               WHERE client_id=? AND source_id=?
               ORDER BY id""",
            (client_id, sid),
        ).fetchall()]
        # Parse endorsements JSON for each row
        import json as _json
        for req in requirements:
            try:
                req["_endorsements_list"] = _json.loads(req.get("required_endorsements") or "[]")
            except (ValueError, TypeError):
                req["_endorsements_list"] = []

    # Get templates for "Apply Template" dropdown
    tmpl_list = [dict(r) for r in conn.execute(
        "SELECT id, name, description FROM requirement_templates ORDER BY name"
    ).fetchall()]

    return templates.TemplateResponse("compliance/_review_mode.html", {
        "request": request,
        "client_id": client_id,
        "sources": sources,
        "selected_source_id": sid,
        "requirements": requirements,
        "templates": tmpl_list,
        "policy_types": cfg.get("policy_types", []),
        "deductible_types": cfg.get("deductible_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
    })


@router.post("/client/{client_id}/review-mode/add-row", response_class=HTMLResponse)
def review_mode_add_row(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    source_id: str = Form(...),
):
    """Create a blank requirement row for review mode rapid entry."""
    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (ValueError, TypeError):
            return None

    sid = _int_or_none(source_id)
    cur = conn.execute(
        """INSERT INTO coverage_requirements
           (client_id, source_id, coverage_line, compliance_status, required_endorsements)
           VALUES (?, ?, '', 'Needs Review', '[]')""",
        (client_id, sid),
    )
    conn.commit()
    req_id = cur.lastrowid
    req = dict(conn.execute(
        "SELECT * FROM coverage_requirements WHERE id=?", (req_id,)
    ).fetchone())
    req["_endorsements_list"] = []

    return templates.TemplateResponse("compliance/_review_mode_row.html", {
        "request": request,
        "req": req,
        "client_id": client_id,
        "policy_types": cfg.get("policy_types", []),
        "deductible_types": cfg.get("deductible_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
    })


@router.patch("/client/{client_id}/review-mode/{req_id}/cell")
async def review_mode_cell(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """JSON-returning cell save for review mode contenteditable table."""
    from fastapi.responses import JSONResponse
    import json as _json

    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    if field not in _CELL_ALLOWED_FIELDS:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)

    formatted = value
    save_value = value or None

    # Currency parsing for money fields — return shorthand (1M, 500K)
    if field in ("required_limit", "max_deductible") and value:
        parsed = parse_currency_with_magnitude(value)
        if parsed:
            save_value = parsed
            if abs(parsed) >= 1_000_000:
                formatted = f"{parsed/1_000_000:,.1f}M"
            elif abs(parsed) >= 1_000:
                formatted = f"{parsed/1_000:,.0f}K"
            else:
                formatted = f"{parsed:,.0f}"
        else:
            save_value = None
            formatted = ""

    # JSON array for endorsements
    if field == "required_endorsements":
        try:
            arr = _json.loads(value) if isinstance(value, str) else value
            save_value = _json.dumps(arr)
            formatted = save_value
        except (ValueError, TypeError):
            save_value = "[]"
            formatted = "[]"

    conn.execute(
        f"UPDATE coverage_requirements SET {field}=? WHERE id=? AND client_id=?",
        (save_value, req_id, client_id),
    )
    conn.commit()

    return JSONResponse({"ok": True, "formatted": formatted})


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


# ── COPE Data ─────────────────────────────────────────────────────────────────

@router.get("/client/{client_id}/location/{project_id}/cope", response_class=HTMLResponse)
def cope_panel(
    client_id: int,
    project_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return COPE data panel for a location."""
    cope = conn.execute(
        "SELECT * FROM cope_data WHERE project_id=?", (project_id,)
    ).fetchone()
    cope_dict = dict(cope) if cope else {}

    project = conn.execute(
        "SELECT id, name FROM projects WHERE id=? AND client_id=?",
        (project_id, client_id),
    ).fetchone()

    return templates.TemplateResponse("compliance/_cope_panel.html", {
        "request": request,
        "cope": cope_dict,
        "project": dict(project) if project else {"id": project_id, "name": ""},
        "client_id": client_id,
        "construction_types": cfg.get("construction_types", []),
        "sprinkler_options": cfg.get("sprinkler_options", []),
        "roof_types": cfg.get("roof_types", []),
        "protection_classes": cfg.get("protection_classes", []),
    })


@router.patch("/client/{client_id}/location/{project_id}/cope")
async def cope_cell(
    client_id: int,
    project_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """JSON-returning cell save for COPE data with upsert."""
    from fastapi.responses import JSONResponse

    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {
        "construction_type", "year_built", "stories", "sq_footage",
        "sprinklered", "roof_type", "occupancy_description",
        "protection_class", "total_insurable_value", "notes",
    }
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not allowed"}, status_code=400)

    formatted = value
    save_value = value or None

    # Currency parsing for money fields
    if field in ("total_insurable_value", "sq_footage") and value:
        parsed = parse_currency_with_magnitude(value)
        if parsed:
            save_value = parsed
            formatted = f"{parsed:,.0f}" if parsed == int(parsed) else f"{parsed:,.2f}"

    # Integer fields
    if field in ("year_built", "stories") and value:
        try:
            save_value = int(value)
            formatted = str(save_value)
        except ValueError:
            pass

    # Upsert: INSERT OR REPLACE
    existing = conn.execute("SELECT project_id FROM cope_data WHERE project_id=?", (project_id,)).fetchone()
    if existing:
        conn.execute(f"UPDATE cope_data SET {field}=? WHERE project_id=?", (save_value, project_id))
    else:
        conn.execute(f"INSERT INTO cope_data (project_id, {field}) VALUES (?, ?)", (project_id, save_value))
    conn.commit()

    return JSONResponse({"ok": True, "formatted": formatted})


# ── Requirement Templates ─────────────────────────────────────────────────────

@router.post("/client/{client_id}/templates/save", response_class=HTMLResponse)
def template_save(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    template_name: str = Form(...),
    source_id: str = Form(""),
):
    """Save requirements from a source as a reusable template."""
    from fastapi.responses import RedirectResponse

    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (ValueError, TypeError):
            return None

    sid = _int_or_none(source_id)
    if not sid:
        return RedirectResponse(f"/compliance/client/{client_id}", status_code=303)

    # Create template
    cur = conn.execute(
        "INSERT INTO requirement_templates (name, description) VALUES (?, ?)",
        (template_name.strip(), f"Saved from client {client_id}"),
    )
    tmpl_id = cur.lastrowid

    # Copy requirements from source as template items
    reqs = conn.execute(
        "SELECT coverage_line, required_limit, max_deductible, deductible_type, required_endorsements, notes "
        "FROM coverage_requirements WHERE client_id=? AND source_id=?",
        (client_id, sid),
    ).fetchall()

    for r in reqs:
        conn.execute(
            """INSERT INTO requirement_template_items
               (template_id, coverage_line, required_limit, max_deductible,
                deductible_type, required_endorsements, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tmpl_id, r["coverage_line"], r["required_limit"], r["max_deductible"],
             r["deductible_type"], r["required_endorsements"], r["notes"]),
        )
    conn.commit()

    return RedirectResponse(f"/compliance/client/{client_id}", status_code=303)


@router.post("/client/{client_id}/templates/{tmpl_id}/apply", response_class=HTMLResponse)
def template_apply(
    client_id: int,
    tmpl_id: int,
    request: Request,
    conn=Depends(get_db),
    source_id: str = Form(""),
    project_id: str = Form(""),
):
    """Apply a template's items as requirements for a client/source."""
    from fastapi.responses import RedirectResponse

    def _int_or_none(v):
        try:
            return int(v) if str(v).strip() else None
        except (ValueError, TypeError):
            return None

    sid = _int_or_none(source_id)
    pid = _int_or_none(project_id)

    # Get template items
    items = conn.execute(
        "SELECT * FROM requirement_template_items WHERE template_id=?",
        (tmpl_id,),
    ).fetchall()

    # Get existing requirements for dedup
    existing_lines = {
        r["coverage_line"]
        for r in conn.execute(
            "SELECT coverage_line FROM coverage_requirements WHERE client_id=? AND source_id=? AND (project_id=? OR (project_id IS NULL AND ? IS NULL))",
            (client_id, sid, pid, pid),
        ).fetchall()
    }

    for item in items:
        if item["coverage_line"] in existing_lines:
            continue
        conn.execute(
            """INSERT INTO coverage_requirements
               (client_id, source_id, project_id, coverage_line, required_limit,
                max_deductible, deductible_type, required_endorsements, notes,
                compliance_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Needs Review')""",
            (client_id, sid, pid, item["coverage_line"], item["required_limit"],
             item["max_deductible"], item["deductible_type"],
             item["required_endorsements"], item["notes"]),
        )
    conn.commit()

    return RedirectResponse(f"/compliance/client/{client_id}", status_code=303)


@router.post("/compliance/templates/{tmpl_id}/delete")
def template_delete(
    tmpl_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Delete a template and its items."""
    from fastapi.responses import RedirectResponse

    conn.execute("DELETE FROM requirement_template_items WHERE template_id=?", (tmpl_id,))
    conn.execute("DELETE FROM requirement_templates WHERE id=?", (tmpl_id,))
    conn.commit()

    referer = request.headers.get("referer", "/settings")
    return RedirectResponse(referer, status_code=303)


# ── Risk → Requirement Spawning ──────────────────────────────────────────────

@router.post("/client/{client_id}/risks/{risk_id}/spawn-requirements")
def spawn_from_risk(
    client_id: int,
    risk_id: int,
    request: Request,
    conn=Depends(get_db),
    source_id: str = Form(""),
):
    """Create compliance requirements from a risk's coverage lines."""
    from policydb.compliance import spawn_requirements_from_risk
    from fastapi.responses import RedirectResponse

    sid = None
    if source_id.strip():
        try:
            sid = int(source_id)
        except ValueError:
            pass

    created = spawn_requirements_from_risk(conn, client_id, risk_id, sid)
    return RedirectResponse(f"/compliance/client/{client_id}", status_code=303)
