"""Compliance routes — contract review, requirement tracking, coverage gap analysis."""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response

from policydb import config as cfg
from policydb.compliance import (
    get_client_compliance_data,
    get_risk_review_prompts,
)
from policydb.llm_schemas import (
    COMPLIANCE_EXTRACTION_SCHEMA,
    generate_extraction_prompt,
    generate_json_template,
    parse_llm_json,
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
    active_location_id = int(request.query_params.get("location", 0))
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
        "active_location_id": active_location_id,
        # Config values
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "deductible_types": cfg.get("deductible_types", []),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
        "risk_review_prompt_categories": cfg.get("risk_review_prompt_categories", []),
    }


# ── Helpers for targeted partial + OOB responses ──────────────────────────────


def _oob_summary_and_matrix(request: Request, conn: sqlite3.Connection, client_id: int) -> str:
    """Return OOB HTML string for summary banner + matrix refresh."""
    ctx = _compliance_context(conn, client_id, request)
    summary_resp = templates.TemplateResponse("compliance/_summary_banner.html", {
        "request": request, **ctx
    })
    matrix_resp = templates.TemplateResponse("compliance/_matrix.html", {
        "request": request, **ctx
    })
    # outerHTML replaces the entire #compliance-summary / #compliance-matrix divs
    summary_oob = summary_resp.body.decode().replace(
        'id="compliance-summary"', 'id="compliance-summary" hx-swap-oob="outerHTML"', 1
    )
    matrix_oob = matrix_resp.body.decode().replace(
        'id="compliance-matrix"', 'id="compliance-matrix" hx-swap-oob="outerHTML"', 1
    )
    return summary_oob + matrix_oob


def _location_response(request: Request, conn: sqlite3.Connection, client_id: int, project_id: int) -> str:
    """Build _location_detail.html context and render for a given location."""
    data = get_client_compliance_data(conn, client_id)
    locs = data["locations"]
    loc = next((l for l in locs if l["project"]["id"] == project_id), None)
    if not loc:
        return ""
    idx = next((i for i, l in enumerate(locs) if l["project"]["id"] == project_id), 0)
    next_loc = locs[idx + 1]["project"] if idx + 1 < len(locs) else None
    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request, "client_id": client_id,
        "location_data": loc,
        "project": loc["project"],
        "loc": loc,
        "locations": locs,
        "sources": data["sources"],
        "all_policies": data["all_policies"],
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "policy_types": cfg.get("policy_types", []),
        "deductible_types": cfg.get("deductible_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
        "location_index": idx, "location_count": len(locs),
        "next_location": next_loc,
    }).body.decode()


def _sources_container_html(request: Request, conn: sqlite3.Connection, client_id: int) -> str:
    """Render the sources container partial (_sources_section.html)."""
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id=? ORDER BY name", (client_id,)
    ).fetchall()]
    return templates.TemplateResponse("compliance/_sources_section.html", {
        "request": request,
        "client_id": client_id,
        "sources": sources,
        "projects": projects,
    }).body.decode()


def _corporate_location_html(request: Request, conn: sqlite3.Connection, client_id: int) -> str:
    """Render the corporate (project_id IS NULL) location detail."""
    reqs = [dict(r) for r in conn.execute(
        "SELECT * FROM coverage_requirements WHERE client_id=? AND project_id IS NULL ORDER BY coverage_line",
        (client_id,),
    ).fetchall()]
    for req in reqs:
        try:
            req["_endorsements_list"] = json.loads(req.get("required_endorsements") or "[]")
        except (ValueError, TypeError):
            req["_endorsements_list"] = []
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? AND (project_id IS NULL) ORDER BY name",
        (client_id,),
    ).fetchall()]
    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request, "client_id": client_id,
        "location_data": {"project": {"id": 0, "name": "Corporate"}, "requirements": reqs, "sources": sources, "policies": [], "governing": {}, "summary": {"gap": 0, "met": 0, "total": len(reqs)}},
        "project": {"id": 0, "name": "Corporate"},
        "loc": {"project": {"id": 0, "name": "Corporate"}, "requirements": reqs, "sources": sources, "policies": [], "governing": {}, "summary": {"gap": 0, "met": 0, "total": len(reqs)}},
        "locations": [],
        "sources": sources,
        "all_policies": [],
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "policy_types": cfg.get("policy_types", []),
        "deductible_types": cfg.get("deductible_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
    }).body.decode()


# ── XLSX Export ───────────────────────────────────────────────────────────────


@router.get("/client/{client_id}/export/xlsx")
def export_xlsx(client_id: int, conn=Depends(get_db)):
    """Download the 5-sheet compliance workbook."""
    from policydb.exporter import export_compliance_xlsx

    xlsx_bytes, filename = export_compliance_xlsx(conn, client_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── AI Import ─────────────────────────────────────────────────────────────────


@router.get("/client/{client_id}/ai-import/prompt", response_class=HTMLResponse)
def ai_import_prompt(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    source_id: int | None = Query(None),
    project_id: int | None = Query(None),
):
    """Return the slideover panel HTML with the generated compliance prompt."""
    client = get_client_by_id(conn, client_id)

    context: dict = {
        "client_name": client["name"],
        "config_lists": {
            "policy_types": cfg.get("policy_types", []),
            "deductible_types": cfg.get("deductible_types", []),
            "endorsement_types": cfg.get("endorsement_types", []),
            "construction_types": cfg.get("construction_types", []),
            "sprinkler_options": cfg.get("sprinkler_options", []),
            "roof_types": cfg.get("roof_types", []),
            "protection_classes": cfg.get("protection_classes", []),
        },
    }

    # Add location context if project_id provided
    if project_id is not None:
        project = conn.execute(
            "SELECT name FROM projects WHERE id=? AND client_id=?",
            (project_id, client_id),
        ).fetchone()
        if project:
            context["location_name"] = project["name"]

    # Add source context if source_id provided
    if source_id is not None:
        source = conn.execute(
            "SELECT name FROM requirement_sources WHERE id=? AND client_id=?",
            (source_id, client_id),
        ).fetchone()
        if source:
            context["source_name"] = source["name"]

    prompt_text = generate_extraction_prompt(COMPLIANCE_EXTRACTION_SCHEMA, context)
    json_template = generate_json_template(COMPLIANCE_EXTRACTION_SCHEMA)

    # Build context display for the panel
    context_display: dict = {"Client": client["name"]}
    if context.get("location_name"):
        context_display["Location"] = context["location_name"]
    if context.get("source_name"):
        context_display["Source"] = context["source_name"]

    # Build parse URL with applicable query params
    parse_params: dict = {}
    if source_id is not None:
        parse_params["source_id"] = source_id
    if project_id is not None:
        parse_params["project_id"] = project_id
    parse_url = f"/compliance/client/{client_id}/ai-import/parse"
    if parse_params:
        parse_url += "?" + urlencode(parse_params)

    # Pass locations for the location selector dropdown
    data = get_client_compliance_data(conn, client_id)

    return templates.TemplateResponse("_ai_import_panel.html", {
        "request": request,
        "client_id": client_id,
        "prompt_text": prompt_text,
        "json_template": json_template,
        "context_display": context_display,
        "parse_url": parse_url,
        "import_target": "#review-mode-container",
        "locations": data["locations"],
        "active_location_id": project_id or 0,
    })


@router.post("/client/{client_id}/ai-import/parse", response_class=HTMLResponse)
def ai_import_parse(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
    json_text: str = Form(...),
    source_id: int | None = Query(None),
    project_id: int | None = Query(None),
    project_id_form: str | None = Form(None, alias="project_id"),
):
    """Parse JSON from LLM, create DB rows, return review mode."""
    # Form body project_id takes precedence over query string (allows location selector override)
    if project_id_form is not None and project_id_form != "":
        project_id = int(project_id_form)
    elif project_id_form == "":
        project_id = None
    result = parse_llm_json(json_text, COMPLIANCE_EXTRACTION_SCHEMA)

    if not result["ok"]:
        error_html = (
            '<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">'
            f'<p class="font-semibold">Parse Error</p>'
            f'<p>{result["error"]}</p>'
            '</div>'
        )
        return HTMLResponse(error_html, status_code=422)

    parsed = result["parsed"]
    warnings = result.get("warnings", [])

    # --- Source ---
    if source_id is not None:
        # Use existing source
        sid = source_id
    else:
        # Create new source from parsed data
        src = parsed.get("source", {})
        cur = conn.execute(
            """INSERT INTO requirement_sources
               (client_id, project_id, name, counterparty, clause_ref, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                client_id,
                project_id,
                src.get("name", "AI Import"),
                src.get("counterparty", ""),
                src.get("clause_ref", ""),
                src.get("notes", ""),
            ),
        )
        sid = cur.lastrowid

    # --- Requirements ---
    for item in parsed.get("requirements", []):
        endorsements = json.dumps(item.get("required_endorsements", []))
        conn.execute(
            """INSERT INTO coverage_requirements
               (client_id, project_id, source_id, coverage_line, required_limit,
                max_deductible, deductible_type, required_endorsements,
                compliance_status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Needs Review', ?)""",
            (
                client_id,
                project_id,
                sid,
                item.get("coverage_line", ""),
                item.get("required_limit"),
                item.get("max_deductible"),
                item.get("deductible_type"),
                endorsements,
                item.get("notes"),
            ),
        )

    req_count = len(parsed.get("requirements", []))

    # --- COPE ---
    cope_skipped = False
    cope = parsed.get("cope")
    if cope and project_id is None:
        cope_skipped = True
        warnings.append("COPE data detected but no location selected — COPE data was not imported. Select a location first, then re-import.")
    if cope and project_id is not None:
        conn.execute(
            """INSERT OR REPLACE INTO cope_data
               (project_id, construction_type, year_built, stories, sq_footage,
                sprinklered, roof_type, occupancy_description, protection_class,
                total_insurable_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                cope.get("construction_type"),
                cope.get("year_built"),
                cope.get("stories"),
                cope.get("sq_footage"),
                cope.get("sprinklered"),
                cope.get("roof_type"),
                cope.get("occupancy_description"),
                cope.get("protection_class"),
                cope.get("total_insurable_value"),
            ),
        )

    conn.commit()

    # --- Build response: review mode with newly created rows ---
    requirements = [dict(r) for r in conn.execute(
        "SELECT * FROM coverage_requirements WHERE source_id=? ORDER BY id",
        (sid,),
    ).fetchall()]
    for req in requirements:
        try:
            req["_endorsements_list"] = json.loads(req.get("required_endorsements") or "[]")
        except (ValueError, TypeError):
            req["_endorsements_list"] = []

    # Get sources list for the review mode dropdown
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Get templates for "Apply Template" dropdown
    tmpl_list = [dict(r) for r in conn.execute(
        "SELECT id, name, description FROM requirement_templates ORDER BY name"
    ).fetchall()]

    # Build OOB success + warnings HTML
    success_html = (
        f'<div class="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800 mb-3">'
        f'<p class="font-semibold">Imported {req_count} requirement{"s" if req_count != 1 else ""}</p>'
        f'</div>'
    ) if req_count else ""

    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warnings_html = (
            f'<div id="ai-import-warnings" hx-swap-oob="innerHTML">'
            f'<div class="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">'
            f'<p class="font-semibold mb-1">Warnings</p><ul class="list-disc ml-4">{items}</ul>'
            f'</div></div>'
        )

    response = templates.TemplateResponse("compliance/_review_mode.html", {
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

    # Prepend success banner + append OOB warnings
    if success_html or warnings_html:
        body = response.body
        if success_html:
            body = success_html.encode() + body
        if warnings_html:
            body += warnings_html.encode()
        response.body = body

    return response


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
    html = _sources_container_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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
    html = _sources_container_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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
    html = _sources_container_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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

    pid = _int_or_none(project_id)
    conn.execute(
        """INSERT INTO coverage_requirements (
               client_id, project_id, source_id, risk_id, coverage_line,
               required_limit, max_deductible, deductible_type,
               compliance_status, linked_policy_uid, notes,
               required_endorsements
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            client_id,
            pid,
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
    # Return location detail for the affected location + OOB summary/matrix
    if pid:
        html = _location_response(request, conn, client_id, pid)
    else:
        html = _corporate_location_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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

    pid = _int_or_none(project_id)
    conn.execute(
        """UPDATE coverage_requirements
           SET coverage_line=?, project_id=?, source_id=?, risk_id=?,
               required_limit=?, max_deductible=?, deductible_type=?,
               compliance_status=?, linked_policy_uid=?, notes=?,
               required_endorsements=?
           WHERE id=? AND client_id=?""",
        (
            coverage_line.strip(),
            pid,
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
    if pid:
        html = _location_response(request, conn, client_id, pid)
    else:
        html = _corporate_location_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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
    # Return updated matrix + OOB summary
    ctx = _compliance_context(conn, client_id, request)
    matrix_html = templates.TemplateResponse("compliance/_matrix.html", {
        "request": request, **ctx
    }).body.decode()
    summary_resp = templates.TemplateResponse("compliance/_summary_banner.html", {
        "request": request, **ctx
    })
    summary_oob = summary_resp.body.decode().replace(
        'id="compliance-summary"', 'id="compliance-summary" hx-swap-oob="outerHTML"', 1
    )
    return HTMLResponse(matrix_html + summary_oob)


@router.post("/client/{client_id}/requirements/{req_id}/link-policy", response_class=HTMLResponse)
def requirements_link_policy(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    linked_policy_uid: str = Form(""),
):
    # Look up the requirement's project_id before updating
    row = conn.execute(
        "SELECT project_id FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    ).fetchone()
    pid = row["project_id"] if row else None

    conn.execute(
        "UPDATE coverage_requirements SET linked_policy_uid=? WHERE id=? AND client_id=?",
        (linked_policy_uid.strip() or None, req_id, client_id),
    )
    conn.commit()
    if pid:
        html = _location_response(request, conn, client_id, pid)
    else:
        html = _corporate_location_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


@router.post("/client/{client_id}/requirements/{req_id}/delete", response_class=HTMLResponse)
def requirements_delete(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    # Look up project_id before deleting so we know which location to refresh
    row = conn.execute(
        "SELECT project_id FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    ).fetchone()
    pid = row["project_id"] if row else None

    conn.execute(
        "DELETE FROM coverage_requirements WHERE id=? AND client_id=?",
        (req_id, client_id),
    )
    conn.commit()
    if pid:
        html = _location_response(request, conn, client_id, pid)
    else:
        html = _corporate_location_html(request, conn, client_id)
    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(html + oob)


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


# ── Requirement row restore ───────────────────────────────────────────────────

@router.get("/client/{client_id}/requirements/{req_id}/row", response_class=HTMLResponse)
def requirement_row_display(client_id: int, req_id: int, request: Request, conn=Depends(get_db)):
    """Return display row for a requirement (cancel edit)."""
    req = dict(conn.execute(
        "SELECT * FROM coverage_requirements WHERE id=?", (req_id,)
    ).fetchone())
    try:
        req["_endorsements_list"] = json.loads(req.get("required_endorsements") or "[]")
    except (ValueError, TypeError):
        req["_endorsements_list"] = []
    return templates.TemplateResponse("compliance/_requirement_row.html", {
        "request": request, "req": req, "client_id": client_id,
    })


# ── Location detail ───────────────────────────────────────────────────────────

# IMPORTANT: literal route "corporate" must come BEFORE parameterized {project_id}
@router.get("/client/{client_id}/location/corporate", response_class=HTMLResponse)
def location_corporate(client_id: int, request: Request, conn=Depends(get_db)):
    """Return location detail for corporate-level (project_id IS NULL) requirements."""
    return HTMLResponse(_corporate_location_html(request, conn, client_id))


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
