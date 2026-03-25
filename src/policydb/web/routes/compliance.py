"""Compliance routes — contract review, requirement tracking, coverage gap analysis."""

from __future__ import annotations

import json
import logging
import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from rapidfuzz import fuzz

from policydb import config as cfg
from policydb.compliance import (
    compute_auto_status,
    get_client_compliance_data,
    get_linkable_policies,
    get_requirement_links,
    link_policy_to_requirement,
    set_primary_link,
    unlink_policy_from_requirement,
    get_risk_review_prompts,
)
from policydb.llm_schemas import (
    COMPLIANCE_EXTRACTION_SCHEMA,
    COPE_FIELDS,
    generate_extraction_prompt,
    generate_json_template,
    parse_llm_json,
)

logger = logging.getLogger("policydb.web.routes.compliance")
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
    "source_id",
    "project_id",
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

    linkable = get_linkable_policies(conn, client_id)

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
        "linkable_policies": linkable,
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
    linkable = get_linkable_policies(conn, client_id)
    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request, "client_id": client_id,
        "location_data": loc,
        "project": loc["project"],
        "loc": loc,
        "locations": locs,
        "sources": data["sources"],
        "all_policies": data["all_policies"],
        "linkable_policies": linkable,
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


def _parse_location_ids(locations: str) -> list[int] | None:
    """Parse comma-separated project IDs from query param, or None for all."""
    ids = [int(x) for x in locations.split(",") if x.strip().isdigit()]
    return ids or None


@router.get("/client/{client_id}/export/xlsx")
def export_xlsx(client_id: int, locations: str = Query(""), conn=Depends(get_db)):
    """Download the 5-sheet compliance workbook."""
    from policydb.exporter import export_compliance_xlsx

    project_ids = _parse_location_ids(locations)
    xlsx_bytes, filename = export_compliance_xlsx(conn, client_id, project_ids=project_ids)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Markdown Export ───────────────────────────────────────────────────────────


@router.get("/client/{client_id}/export/md")
def export_md(client_id: int, locations: str = Query(""), conn=Depends(get_db)):
    """Download the compliance report as Markdown."""
    from policydb.exporter import export_compliance_md

    project_ids = _parse_location_ids(locations)
    md_text, filename = export_compliance_md(conn, client_id, project_ids=project_ids)
    return Response(
        content=md_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
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
        "import_target": "#ai-review-container",
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
    """Parse JSON from LLM and return diff review panel (no DB writes)."""
    # Form body project_id takes precedence over query string
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

    try:
        return _ai_import_build_diffs(request, conn, client_id, source_id, project_id, result)
    except Exception:
        logger.exception("Compliance AI import parse failed for client %d", client_id)
        return HTMLResponse(
            '<div class="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">'
            'An error occurred processing the import. Check server logs for details.</div>',
            status_code=500,
        )


# ── Field labels for diff display ────────────────────────────────────────────

_SOURCE_FIELD_LABELS: dict[str, str] = {
    "name": "Document / Source Name",
    "counterparty": "Counterparty",
    "clause_ref": "Clause / Section Reference",
    "notes": "Notes",
}

_REQ_FIELD_LABELS: dict[str, str] = {
    "coverage_line": "Coverage Line",
    "required_limit": "Required Limit",
    "max_deductible": "Maximum Deductible",
    "deductible_type": "Deductible Type",
    "required_endorsements": "Required Endorsements",
    "notes": "Notes",
}

_COPE_LABELS: dict[str, str] = {f["key"]: f["label"] for f in COPE_FIELDS}

_REQ_DIFF_FIELDS = [
    "coverage_line", "required_limit", "max_deductible",
    "deductible_type", "required_endorsements", "notes",
]


def _ai_import_build_diffs(
    request: Request, conn, client_id: int,
    source_id: int | None, project_id: int | None, result: dict,
):
    """Build diff data comparing extracted data vs existing, return review panel."""
    parsed = result["parsed"]
    warnings: list[str] = list(result.get("warnings", []))

    # ── Source diffs ──────────────────────────────────────────────────────
    src_parsed = parsed.get("source", {})
    ai_source_diffs: list[dict] = []
    existing_source: dict | None = None

    if source_id is not None:
        row = conn.execute(
            "SELECT * FROM requirement_sources WHERE id=?", (source_id,)
        ).fetchone()
        if row:
            existing_source = dict(row)
            for fkey in ("name", "counterparty", "clause_ref", "notes"):
                ext_val = src_parsed.get(fkey)
                if ext_val is None:
                    continue
                cur_val = existing_source.get(fkey)
                cur_str = str(cur_val) if cur_val else ""
                ext_str = str(ext_val) if ext_val else ""
                if cur_str != ext_str:
                    ai_source_diffs.append({
                        "field": fkey,
                        "label": _SOURCE_FIELD_LABELS.get(fkey, fkey),
                        "current": cur_val,
                        "extracted": ext_val,
                        "is_fill": not cur_str,
                    })
    else:
        # New source — all fields are "fills"
        for fkey in ("name", "counterparty", "clause_ref", "notes"):
            ext_val = src_parsed.get(fkey)
            if ext_val:
                ai_source_diffs.append({
                    "field": fkey,
                    "label": _SOURCE_FIELD_LABELS.get(fkey, fkey),
                    "current": None,
                    "extracted": ext_val,
                    "is_fill": True,
                })

    # ── Requirement matching ─────────────────────────────────────────────
    extracted_reqs = parsed.get("requirements", [])
    ai_requirement_data: list[dict] = []

    # Load existing requirements for the source (if any)
    existing_reqs: list[dict] = []
    if source_id is not None:
        existing_reqs = [dict(r) for r in conn.execute(
            "SELECT * FROM coverage_requirements WHERE source_id=? ORDER BY id",
            (source_id,),
        ).fetchall()]

    # Track which existing reqs have been matched to avoid double-matching
    matched_existing_ids: set[int] = set()

    for req_idx, ext_req in enumerate(extracted_reqs):
        req_entry: dict = {
            "index": req_idx,
            "extracted": ext_req,
            "match_type": "new",
            "match_score": 0,
            "existing": None,
            "existing_id": None,
            "diffs": [],
        }

        # Fuzzy match against existing requirements by coverage_line
        ext_cov = (ext_req.get("coverage_line") or "").lower().strip()
        if ext_cov and existing_reqs:
            best_score = 0
            best_match = None
            for ereq in existing_reqs:
                if ereq["id"] in matched_existing_ids:
                    continue
                cur_cov = (ereq.get("coverage_line") or "").lower().strip()
                if not cur_cov:
                    continue
                score = fuzz.ratio(ext_cov, cur_cov)
                if score > best_score and score >= 60:
                    best_score = score
                    best_match = ereq

            if best_match:
                matched_existing_ids.add(best_match["id"])
                req_entry["match_type"] = "matched"
                req_entry["match_score"] = int(best_score)
                req_entry["existing"] = best_match
                req_entry["existing_id"] = best_match["id"]

                # Build field-level diffs
                for fkey in _REQ_DIFF_FIELDS:
                    ext_val = ext_req.get(fkey)
                    if ext_val is None:
                        continue
                    cur_val = best_match.get(fkey)
                    # Normalize endorsements for comparison
                    if fkey == "required_endorsements":
                        ext_str = json.dumps(ext_val) if isinstance(ext_val, list) else str(ext_val or "")
                        try:
                            cur_list = json.loads(cur_val) if isinstance(cur_val, str) else (cur_val or [])
                        except (ValueError, TypeError):
                            cur_list = []
                        cur_str = json.dumps(cur_list)
                    else:
                        cur_str = str(cur_val) if cur_val is not None else ""
                        ext_str = str(ext_val) if ext_val is not None else ""

                    if cur_str != ext_str:
                        req_entry["diffs"].append({
                            "field": fkey,
                            "label": _REQ_FIELD_LABELS.get(fkey, fkey),
                            "current": cur_val,
                            "extracted": ext_val,
                            "is_fill": not cur_str or cur_str == "[]",
                        })
        else:
            # New requirement — all non-empty fields are fills
            for fkey in _REQ_DIFF_FIELDS:
                ext_val = ext_req.get(fkey)
                if ext_val is not None and ext_val != "" and ext_val != []:
                    req_entry["diffs"].append({
                        "field": fkey,
                        "label": _REQ_FIELD_LABELS.get(fkey, fkey),
                        "current": None,
                        "extracted": ext_val,
                        "is_fill": True,
                    })

        ai_requirement_data.append(req_entry)

    # ── COPE diffs ───────────────────────────────────────────────────────
    cope_parsed = parsed.get("cope", {})
    ai_cope_diffs: list[dict] = []

    if cope_parsed and project_id is None:
        warnings.append(
            "COPE data detected but no location selected — select a location "
            "to review COPE changes."
        )
    elif cope_parsed and project_id is not None:
        cope_row = conn.execute(
            "SELECT * FROM cope_data WHERE project_id=?", (project_id,)
        ).fetchone()
        cope_current = dict(cope_row) if cope_row else {}
        for fkey in [f["key"] for f in COPE_FIELDS]:
            ext_val = cope_parsed.get(fkey)
            if ext_val is None:
                continue
            cur_val = cope_current.get(fkey)
            cur_str = str(cur_val) if cur_val is not None else ""
            ext_str = str(ext_val) if ext_val is not None else ""
            if cur_str != ext_str:
                ai_cope_diffs.append({
                    "field": fkey,
                    "label": _COPE_LABELS.get(fkey, fkey),
                    "current": cur_val,
                    "extracted": ext_val,
                    "is_fill": not cur_str,
                })

    # ── Render review panel ──────────────────────────────────────────────
    response = templates.TemplateResponse("compliance/_ai_review_panel.html", {
        "request": request,
        "client_id": client_id,
        "source_id": source_id,
        "project_id": project_id,
        "ai_source_diffs": ai_source_diffs,
        "ai_requirement_data": ai_requirement_data,
        "ai_cope_diffs": ai_cope_diffs,
        "ai_parsed_json": json.dumps(parsed, default=str),
        "is_new_source": source_id is None,
        "existing_source_name": existing_source["name"] if existing_source else None,
    })

    # Build full body + OOB warnings
    body_parts: list[bytes] = [response.body]

    if warnings:
        from html import escape
        items = "".join(f"<li>{escape(w)}</li>" for w in warnings)
        body_parts.append((
            f'<div id="ai-import-warnings" hx-swap-oob="innerHTML">'
            f'<div class="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">'
            f'<p class="font-semibold mb-1">Warnings</p><ul class="list-disc ml-4">{items}</ul>'
            f'</div></div>'
        ).encode())

    return HTMLResponse(content=b"".join(body_parts))


# ── AI Import Apply Endpoints ─────────────────────────────────────────────────


@router.post("/client/{client_id}/ai-import/apply-source")
async def ai_import_apply_source(
    client_id: int, request: Request, conn=Depends(get_db),
):
    """Create or update a requirement source from AI import."""
    body = await request.json()
    source_fields = body.get("source_fields", {})
    source_id = body.get("source_id")
    project_id = body.get("project_id")

    if not source_fields:
        return JSONResponse({"ok": False, "error": "No fields selected"}, status_code=400)

    if source_id:
        # Update existing source
        sets = []
        vals = []
        for fkey in ("name", "counterparty", "clause_ref", "notes"):
            if fkey in source_fields:
                sets.append(f"{fkey} = ?")
                vals.append(source_fields[fkey])
        if sets:
            vals.append(source_id)
            conn.execute(
                f"UPDATE requirement_sources SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            conn.commit()
        return JSONResponse({"ok": True, "source_id": source_id, "created": False})
    else:
        # Create new source
        cur = conn.execute(
            """INSERT INTO requirement_sources
               (client_id, project_id, name, counterparty, clause_ref, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                client_id,
                project_id,
                source_fields.get("name", "AI Import"),
                source_fields.get("counterparty", ""),
                source_fields.get("clause_ref", ""),
                source_fields.get("notes", ""),
            ),
        )
        conn.commit()
        return JSONResponse({"ok": True, "source_id": cur.lastrowid, "created": True})


@router.post("/client/{client_id}/ai-import/apply-requirement")
async def ai_import_apply_requirement(
    client_id: int, request: Request, conn=Depends(get_db),
):
    """Create or update a coverage requirement from AI import."""
    body = await request.json()
    req_fields = body.get("requirement_fields", {})
    existing_id = body.get("existing_id")
    source_id = body.get("source_id")
    project_id = body.get("project_id")
    create_new = body.get("create_new", False)

    if not req_fields:
        return JSONResponse({"ok": False, "error": "No fields selected"}, status_code=400)

    if existing_id and not create_new:
        # Update existing requirement
        sets = []
        vals = []
        for fkey in ("coverage_line", "required_limit", "max_deductible",
                      "deductible_type", "required_endorsements", "notes"):
            if fkey in req_fields:
                val = req_fields[fkey]
                if fkey == "required_endorsements" and isinstance(val, list):
                    val = json.dumps(val)
                sets.append(f"{fkey} = ?")
                vals.append(val)
        if sets:
            vals.append(existing_id)
            conn.execute(
                f"UPDATE coverage_requirements SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            conn.commit()
        return JSONResponse({"ok": True, "requirement_id": existing_id, "created": False})
    else:
        # Create new requirement
        endorsements = req_fields.get("required_endorsements", [])
        if isinstance(endorsements, list):
            endorsements = json.dumps(endorsements)
        cur = conn.execute(
            """INSERT INTO coverage_requirements
               (client_id, project_id, source_id, coverage_line, required_limit,
                max_deductible, deductible_type, required_endorsements,
                compliance_status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Needs Review', ?)""",
            (
                client_id,
                project_id,
                source_id,
                req_fields.get("coverage_line", ""),
                req_fields.get("required_limit"),
                req_fields.get("max_deductible"),
                req_fields.get("deductible_type"),
                endorsements,
                req_fields.get("notes"),
            ),
        )
        conn.commit()
        return JSONResponse({"ok": True, "requirement_id": cur.lastrowid, "created": True})


@router.post("/client/{client_id}/ai-import/apply-cope")
async def ai_import_apply_cope(
    client_id: int, request: Request, conn=Depends(get_db),
):
    """Apply COPE data from AI import to a location."""
    body = await request.json()
    cope_fields = body.get("cope_fields", {})
    project_id = body.get("project_id")

    if not cope_fields or not project_id:
        return JSONResponse(
            {"ok": False, "error": "No fields or no location selected"},
            status_code=400,
        )

    # Check if COPE row exists
    existing = conn.execute(
        "SELECT project_id FROM cope_data WHERE project_id = ?", (project_id,)
    ).fetchone()

    if existing:
        # Update only the selected fields
        sets = []
        vals = []
        for fkey in [f["key"] for f in COPE_FIELDS]:
            if fkey in cope_fields:
                sets.append(f"{fkey} = ?")
                vals.append(cope_fields[fkey])
        if sets:
            vals.append(project_id)
            conn.execute(
                f"UPDATE cope_data SET {', '.join(sets)} WHERE project_id = ?",
                vals,
            )
    else:
        # Build full INSERT with only selected fields
        cope_vals = {f["key"]: None for f in COPE_FIELDS}
        cope_vals.update(cope_fields)
        conn.execute(
            """INSERT INTO cope_data
               (project_id, construction_type, year_built, stories, sq_footage,
                sprinklered, roof_type, occupancy_description, protection_class,
                total_insurable_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                cope_vals.get("construction_type"),
                cope_vals.get("year_built"),
                cope_vals.get("stories"),
                cope_vals.get("sq_footage"),
                cope_vals.get("sprinklered"),
                cope_vals.get("roof_type"),
                cope_vals.get("occupancy_description"),
                cope_vals.get("protection_class"),
                cope_vals.get("total_insurable_value"),
            ),
        )

    conn.commit()
    return JSONResponse({"ok": True})


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

    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)

    return templates.TemplateResponse(
        "compliance/_requirement_row_edit.html",
        {
            "request": request,
            "req": req_dict,
            "client_id": client_id,
            "sources": sources,
            "projects": projects,
            "links": links,
            "linkable_policies": linkable,
            "compliance_statuses": cfg.get("compliance_statuses", []),
            "deductible_types": cfg.get("deductible_types", []),
            "policy_types": cfg.get("policy_types", []),
            "endorsement_types": cfg.get("endorsement_types", []),
        },
    )


@router.get("/client/{client_id}/requirements/{req_id}/detail", response_class=HTMLResponse)
def requirement_detail(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    location_project_id: int | None = Query(None),
):
    """Return the slideover detail panel for a requirement."""
    req = conn.execute(
        "SELECT * FROM coverage_requirements WHERE id = ? AND client_id = ?",
        (req_id, client_id),
    ).fetchone()
    if not req:
        return HTMLResponse("Not found", status_code=404)

    req_dict = dict(req)
    try:
        req_dict["_endorsements_list"] = json.loads(req_dict.get("required_endorsements") or "[]")
    except (ValueError, TypeError):
        req_dict["_endorsements_list"] = []

    # Sources and locations for dropdowns
    sources = [dict(r) for r in conn.execute(
        "SELECT id, name, counterparty FROM requirement_sources WHERE client_id = ? ORDER BY name",
        (client_id,),
    ).fetchall()]
    projects = [dict(r) for r in conn.execute(
        "SELECT id, name FROM projects WHERE client_id = ? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Use location context from viewing tab, falling back to requirement's own project_id
    effective_pid = location_project_id or req_dict.get("project_id")

    # Policy links
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id, req_project_id=effective_pid)

    # Primary linked policy for comparison
    primary_policy = None
    for link in links:
        if link.get("is_primary"):
            primary_policy = conn.execute(
                "SELECT policy_uid, policy_type, carrier, limit_amount, deductible, "
                "expiration_date FROM policies WHERE policy_uid = ? AND archived = 0",
                (link["policy_uid"],),
            ).fetchone()
            if primary_policy:
                primary_policy = dict(primary_policy)
            break

    # Compute auto-status for display
    auto_status = compute_auto_status(req_dict, primary_policy) if primary_policy else "Gap"

    return templates.TemplateResponse("compliance/_requirement_slideover.html", {
        "request": request,
        "req": req_dict,
        "client_id": client_id,
        "sources": sources,
        "projects": projects,
        "links": links,
        "linkable_policies": linkable,
        "location_project_id": effective_pid,
        "primary_policy": primary_policy,
        "auto_status": auto_status,
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "deductible_types": cfg.get("deductible_types", []),
        "policy_types": cfg.get("policy_types", []),
        "endorsement_types": cfg.get("endorsement_types", []),
    })


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

    # Trigger auto-status recompute on limit/deductible field changes
    if field in ("required_limit", "max_deductible"):
        _recompute_auto_status(conn, req_id)

    if field == "compliance_status" and value in ("Compliant", "Waived", "N/A"):
        conn.execute(
            "UPDATE coverage_requirements SET status_manual_override = 1 WHERE id = ?",
            (req_id,),
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
    req["policy_links"] = get_requirement_links(conn, req_id)
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

    # Add navigation context for "Next location →" footer
    locs = ctx.get("locations", [])
    idx = next((i for i, l in enumerate(locs) if l["project"]["id"] == project_id), 0)
    ctx["location_index"] = idx
    ctx["location_count"] = len(locs)
    ctx["next_location"] = locs[idx + 1]["project"] if idx + 1 < len(locs) else None

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


# ── Policy Link Management ────────────────────────────────────────────────────


def _recompute_auto_status(conn, req_id: int):
    """Recompute auto-status for a requirement based on its primary linked policy."""
    req = conn.execute(
        "SELECT * FROM coverage_requirements WHERE id = ?", (req_id,)
    ).fetchone()
    if not req:
        return
    req_dict = dict(req)
    status = req_dict.get("compliance_status") or "Needs Review"
    override = req_dict.get("status_manual_override", 0)
    if status in ("Waived", "N/A") and override:
        return  # Preserve manual Waived/N/A

    # Find primary linked policy
    primary = conn.execute(
        """SELECT p.limit_amount, p.deductible
           FROM requirement_policy_links rpl
           JOIN policies p ON p.policy_uid = rpl.policy_uid AND p.archived = 0
           WHERE rpl.requirement_id = ? AND rpl.is_primary = 1""",
        (req_id,),
    ).fetchone()

    new_status = compute_auto_status(req_dict, dict(primary) if primary else None)
    if new_status != status:
        conn.execute(
            "UPDATE coverage_requirements SET compliance_status = ? WHERE id = ?",
            (new_status, req_id),
        )
    conn.commit()


@router.get("/client/{client_id}/requirements/{req_id}/links", response_class=HTMLResponse)
def requirement_links(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return the _policy_links.html partial for a requirement."""
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)
    return templates.TemplateResponse("compliance/_policy_links.html", {
        "request": request,
        "client_id": client_id,
        "req_id": req_id,
        "links": links,
        "linkable_policies": linkable,
    })


@router.post("/client/{client_id}/requirements/{req_id}/links/add", response_class=HTMLResponse)
def requirement_link_add(
    client_id: int,
    req_id: int,
    request: Request,
    conn=Depends(get_db),
    policy_uid: str = Form(...),
    link_type: str = Form("direct"),
):
    """Add a policy link to a requirement."""
    link_policy_to_requirement(conn, req_id, policy_uid.strip(), link_type.strip())
    # Clear manual override and recompute auto-status
    conn.execute(
        "UPDATE coverage_requirements SET status_manual_override = 0 WHERE id = ?",
        (req_id,),
    )
    _recompute_auto_status(conn, req_id)
    # Return updated links partial
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)

    links_html = templates.TemplateResponse("compliance/_policy_links.html", {
        "request": request,
        "client_id": client_id,
        "req_id": req_id,
        "links": links,
        "linkable_policies": linkable,
    }).body.decode()

    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(links_html + oob)


@router.post("/client/{client_id}/requirements/{req_id}/links/{link_id}/remove", response_class=HTMLResponse)
def requirement_link_remove(
    client_id: int,
    req_id: int,
    link_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Remove a policy link from a requirement."""
    unlink_policy_from_requirement(conn, req_id, link_id)
    # Clear manual override and recompute auto-status
    conn.execute(
        "UPDATE coverage_requirements SET status_manual_override = 0 WHERE id = ?",
        (req_id,),
    )
    _recompute_auto_status(conn, req_id)
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)

    links_html = templates.TemplateResponse("compliance/_policy_links.html", {
        "request": request,
        "client_id": client_id,
        "req_id": req_id,
        "links": links,
        "linkable_policies": linkable,
    }).body.decode()

    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(links_html + oob)


@router.post("/client/{client_id}/requirements/{req_id}/links/{link_id}/set-primary", response_class=HTMLResponse)
def requirement_link_set_primary(
    client_id: int,
    req_id: int,
    link_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Set a specific link as the primary for a requirement."""
    set_primary_link(conn, req_id, link_id)
    # Clear manual override and recompute auto-status
    conn.execute(
        "UPDATE coverage_requirements SET status_manual_override = 0 WHERE id = ?",
        (req_id,),
    )
    _recompute_auto_status(conn, req_id)
    links = get_requirement_links(conn, req_id)
    linkable = get_linkable_policies(conn, client_id)

    links_html = templates.TemplateResponse("compliance/_policy_links.html", {
        "request": request,
        "client_id": client_id,
        "req_id": req_id,
        "links": links,
        "linkable_policies": linkable,
    }).body.decode()

    oob = _oob_summary_and_matrix(request, conn, client_id)
    return HTMLResponse(links_html + oob)


@router.get("/client/{client_id}/linkable-policies")
def linkable_policies_search(
    client_id: int,
    conn=Depends(get_db),
    q: str = Query(""),
):
    """JSON search endpoint for policy combobox. Returns matching policies grouped by program/standalone."""
    from fastapi.responses import JSONResponse

    all_policies = get_linkable_policies(conn, client_id)
    query = q.strip().lower()

    if not query:
        return JSONResponse(all_policies)

    def _matches(pol: dict) -> bool:
        searchable = " ".join(filter(None, [
            pol.get("policy_uid", ""),
            pol.get("policy_type", ""),
            pol.get("carrier", ""),
            pol.get("policy_number", ""),
        ])).lower()
        return query in searchable

    result = []
    for pol in all_policies:
        children = pol.get("children", [])
        if _matches(pol):
            result.append(pol)
        elif children:
            matching_children = [c for c in children if _matches(c)]
            if matching_children:
                p = dict(pol)
                p["children"] = matching_children
                result.append(p)

    return JSONResponse(result)


# ── Compliance Report ─────────────────────────────────────────────────────────


@router.get("/client/{client_id}/report", response_class=HTMLResponse)
def compliance_report(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Render the full compliance report page."""
    ctx = _compliance_context(conn, client_id, request)
    return templates.TemplateResponse("compliance/report.html", ctx)


@router.get("/client/{client_id}/export/md")
def export_md(client_id: int, conn=Depends(get_db)):
    """Download the compliance report as Markdown."""
    from policydb.exporter import export_compliance_md

    md_text, filename = export_compliance_md(conn, client_id)
    return Response(
        content=md_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
