"""Prompt Builder routes — build, preview, and manage prompt templates."""

from __future__ import annotations

import json
import logging
import traceback

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

from policydb.prompt_assembler import (
    PRIMARY_RECORD_TYPES,
    assemble_prompt,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/prompt-builder", tags=["prompt-builder"])


# ── Helpers ──────────────────────────────────────────────────────────────────

_DELIVERABLE_TYPES = [
    "briefing", "email", "report", "agenda", "narrative", "memo",
    "schedule", "submission", "other",
]


def _get_templates(conn, active_only: bool = True) -> list[dict]:
    """Fetch prompt templates from DB."""
    where = "WHERE active = 1" if active_only else ""
    rows = conn.execute(
        f"SELECT * FROM prompt_templates {where} ORDER BY is_builtin DESC, name"
    ).fetchall()
    return [dict(r) for r in rows]


def _get_template_by_id(conn, template_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM prompt_templates WHERE id = ?", (template_id,)
    ).fetchone()
    return dict(row) if row else None


def _filter_templates_for_type(all_templates: list[dict], record_type: str) -> list[dict]:
    """Filter templates compatible with the selected primary record type."""
    result = []
    for t in all_templates:
        required = json.loads(t.get("required_record_types") or "[]")
        # Template matches if its first required type matches, or if it has no requirements
        if not required or (required and required[0] == record_type):
            result.append(t)
    return result


# ── Main page ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def prompt_builder_index(request: Request, conn=Depends(get_db)):
    all_templates = _get_templates(conn, active_only=True)
    return templates.TemplateResponse("prompt_builder/index.html", {
        "request": request,
        "active": "prompt-builder",
        "all_templates": all_templates,
        "record_types": PRIMARY_RECORD_TYPES,
        "deliverable_types": _DELIVERABLE_TYPES,
    })


# ── Record search ────────────────────────────────────────────────────────────

@router.get("/records", response_class=HTMLResponse)
def record_search(
    request: Request,
    conn=Depends(get_db),
    type: str = Query("client"),
    q: str = Query(""),
):
    """HTMX partial: search records by type and query string."""
    q_like = f"%{q}%" if q else "%"
    records = []

    if type == "client":
        rows = conn.execute(
            """SELECT id, name, cn_number, industry_segment
               FROM clients WHERE archived = 0
               AND (name LIKE ? OR cn_number LIKE ? OR industry_segment LIKE ?)
               ORDER BY name LIMIT 20""",
            (q_like, q_like, q_like),
        ).fetchall()
        records = [
            {"id": r["id"], "label": r["name"],
             "detail": " | ".join(filter(None, [r["cn_number"], r["industry_segment"]]))}
            for r in rows
        ]

    elif type == "policy":
        rows = conn.execute(
            """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name AS client_name
               FROM policies p JOIN clients c ON c.id = p.client_id
               WHERE p.archived = 0 AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
               AND (p.policy_uid LIKE ? OR p.policy_type LIKE ? OR p.carrier LIKE ? OR c.name LIKE ?)
               ORDER BY c.name, p.policy_type LIMIT 20""",
            (q_like, q_like, q_like, q_like),
        ).fetchall()
        records = [
            {"id": r["id"], "label": f"{r['policy_uid']} — {r['policy_type']}",
             "detail": " | ".join(filter(None, [r["carrier"], r["client_name"]]))}
            for r in rows
        ]

    elif type == "renewal":
        rows = conn.execute(
            """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name AS client_name,
                      p.expiration_date, p.renewal_status,
                      CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days
               FROM policies p JOIN clients c ON c.id = p.client_id
               WHERE p.archived = 0 AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
                 AND p.renewal_status IS NOT NULL AND p.renewal_status != ''
                 AND julianday(p.expiration_date) - julianday('now') <= 365
               AND (p.policy_uid LIKE ? OR p.policy_type LIKE ? OR c.name LIKE ?)
               ORDER BY p.expiration_date LIMIT 20""",
            (q_like, q_like, q_like),
        ).fetchall()
        records = [
            {"id": r["id"],
             "label": f"{r['policy_uid']} — {r['policy_type']}",
             "detail": f"{r['client_name']} | {r['days']}d | {r['renewal_status']}"}
            for r in rows
        ]

    elif type == "issue":
        rows = conn.execute(
            """SELECT a.id, a.issue_uid, a.subject, a.issue_status, a.issue_severity,
                      c.name AS client_name
               FROM activity_log a
               JOIN clients c ON c.id = a.client_id
               WHERE a.item_kind = 'issue'
               AND (a.subject LIKE ? OR a.issue_uid LIKE ? OR c.name LIKE ?)
               ORDER BY CASE WHEN a.issue_status IN ('Closed','Resolved') THEN 1 ELSE 0 END,
                        a.activity_date DESC
               LIMIT 20""",
            (q_like, q_like, q_like),
        ).fetchall()
        records = [
            {"id": r["id"],
             "label": f"{r['issue_uid']} — {r['subject']}" if r["issue_uid"] else r["subject"],
             "detail": f"{r['client_name']} | {r['issue_status']} | {r['issue_severity'] or ''}"}
            for r in rows
        ]

    return templates.TemplateResponse("prompt_builder/_record_results.html", {
        "request": request,
        "records": records,
        "record_type": type,
    })


# ── Template filtering (HTMX) ───────────────────────────────────────────────

@router.get("/templates-for-type", response_class=HTMLResponse)
def templates_for_type(
    request: Request,
    conn=Depends(get_db),
    type: str = Query("client"),
):
    """HTMX partial: template cards filtered for a record type."""
    all_templates = _get_templates(conn, active_only=True)
    filtered = _filter_templates_for_type(all_templates, type)
    return templates.TemplateResponse("prompt_builder/_template_cards.html", {
        "request": request,
        "templates_list": filtered,
    })


# ── Preview ──────────────────────────────────────────────────────────────────

@router.post("/preview", response_class=HTMLResponse)
def preview_prompt(
    request: Request,
    conn=Depends(get_db),
    template_id: int = Form(...),
    record_type: str = Form(...),
    record_id: int = Form(...),
):
    """Assemble and return the prompt preview."""
    template = _get_template_by_id(conn, template_id)
    if not template:
        return HTMLResponse("<p class='text-red-500'>Template not found.</p>")

    try:
        result = assemble_prompt(conn, template, record_type, record_id)
    except Exception as exc:
        log.exception("Prompt assembly failed: template=%s record=%s/%s", template_id, record_type, record_id)
        tb = traceback.format_exc()
        return HTMLResponse(
            "<div class='rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800'>"
            f"<p class='font-semibold'>Failed to assemble prompt: {type(exc).__name__}: {exc}</p>"
            f"<pre class='mt-2 overflow-auto text-xs text-red-700'>{tb}</pre>"
            "</div>",
            status_code=200,
        )

    return templates.TemplateResponse("prompt_builder/_preview.html", {
        "request": request,
        "full_prompt": result["full"],
        "data_only": result["data_only"],
        "template_name": template["name"],
        "template_id": template_id,
        "record_type": record_type,
        "record_id": record_id,
    })


# ── Export log ───────────────────────────────────────────────────────────────

@router.post("/log-export")
def log_export(
    conn=Depends(get_db),
    template_id: int = Form(...),
    record_type: str = Form(...),
    record_id: int = Form(...),
):
    """Log a clipboard copy event."""
    conn.execute(
        "INSERT INTO prompt_export_log (template_id, record_type, record_id) VALUES (?, ?, ?)",
        (template_id, record_type, record_id),
    )
    conn.commit()
    return JSONResponse({"ok": True})


# ── Template management ──────────────────────────────────────────────────────

@router.get("/templates", response_class=HTMLResponse)
def templates_list(request: Request, conn=Depends(get_db)):
    """Template manager tab content."""
    all_templates = _get_templates(conn, active_only=False)
    return templates.TemplateResponse("prompt_builder/_templates_tab.html", {
        "request": request,
        "all_templates": all_templates,
        "deliverable_types": _DELIVERABLE_TYPES,
        "record_types": PRIMARY_RECORD_TYPES,
    })


@router.post("/templates/new", response_class=HTMLResponse)
def template_create(
    request: Request,
    conn=Depends(get_db),
    name: str = Form(...),
    deliverable_type: str = Form("other"),
    description: str = Form(""),
    system_prompt: str = Form(""),
    closing_instruction: str = Form(""),
    required_record_types: str = Form("[]"),
    depth_overrides: str = Form(""),
):
    """Create a new custom template."""
    # Validate JSON fields
    try:
        json.loads(required_record_types)
    except json.JSONDecodeError:
        required_record_types = "[]"
    if depth_overrides.strip():
        try:
            json.loads(depth_overrides)
        except json.JSONDecodeError:
            depth_overrides = None
    else:
        depth_overrides = None

    conn.execute(
        """INSERT INTO prompt_templates
           (name, deliverable_type, description, system_prompt, closing_instruction,
            required_record_types, depth_overrides, is_builtin)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (name, deliverable_type, description, system_prompt, closing_instruction,
         required_record_types, depth_overrides),
    )
    conn.commit()

    # Return updated list
    all_templates = _get_templates(conn, active_only=False)
    return templates.TemplateResponse("prompt_builder/_templates_tab.html", {
        "request": request,
        "all_templates": all_templates,
        "deliverable_types": _DELIVERABLE_TYPES,
        "record_types": PRIMARY_RECORD_TYPES,
    })


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def template_edit_form(request: Request, template_id: int, conn=Depends(get_db)):
    """Edit form partial for a template."""
    t = _get_template_by_id(conn, template_id)
    if not t:
        return HTMLResponse("<p class='text-red-500'>Template not found.</p>")
    return templates.TemplateResponse("prompt_builder/_template_form.html", {
        "request": request,
        "t": t,
        "editing": True,
        "deliverable_types": _DELIVERABLE_TYPES,
        "record_types": PRIMARY_RECORD_TYPES,
    })


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
def template_edit_save(
    request: Request,
    template_id: int,
    conn=Depends(get_db),
    name: str = Form(...),
    deliverable_type: str = Form("other"),
    description: str = Form(""),
    system_prompt: str = Form(""),
    closing_instruction: str = Form(""),
    required_record_types: str = Form("[]"),
    depth_overrides: str = Form(""),
):
    """Save an edited template."""
    t = _get_template_by_id(conn, template_id)
    if not t or t.get("is_builtin"):
        return HTMLResponse("<p class='text-red-500'>Cannot edit built-in templates.</p>")

    try:
        json.loads(required_record_types)
    except json.JSONDecodeError:
        required_record_types = "[]"
    if depth_overrides.strip():
        try:
            json.loads(depth_overrides)
        except json.JSONDecodeError:
            depth_overrides = None
    else:
        depth_overrides = None

    conn.execute(
        """UPDATE prompt_templates SET
           name=?, deliverable_type=?, description=?, system_prompt=?, closing_instruction=?,
           required_record_types=?, depth_overrides=?
           WHERE id=? AND is_builtin=0""",
        (name, deliverable_type, description, system_prompt, closing_instruction,
         required_record_types, depth_overrides, template_id),
    )
    conn.commit()

    t = _get_template_by_id(conn, template_id)
    return templates.TemplateResponse("prompt_builder/_template_card.html", {
        "request": request,
        "t": t,
        "manage_mode": True,
    })


@router.post("/templates/{template_id}/duplicate", response_class=HTMLResponse)
def template_duplicate(request: Request, template_id: int, conn=Depends(get_db)):
    """Duplicate a template as a custom copy."""
    t = _get_template_by_id(conn, template_id)
    if not t:
        return HTMLResponse("<p class='text-red-500'>Template not found.</p>")

    conn.execute(
        """INSERT INTO prompt_templates
           (name, deliverable_type, description, system_prompt, closing_instruction,
            required_record_types, depth_overrides, is_builtin)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (f"{t['name']} (Copy)", t["deliverable_type"], t["description"],
         t["system_prompt"], t["closing_instruction"],
         t["required_record_types"], t["depth_overrides"]),
    )
    conn.commit()

    # Return updated list
    all_templates = _get_templates(conn, active_only=False)
    return templates.TemplateResponse("prompt_builder/_templates_tab.html", {
        "request": request,
        "all_templates": all_templates,
        "deliverable_types": _DELIVERABLE_TYPES,
        "record_types": PRIMARY_RECORD_TYPES,
    })


@router.post("/templates/{template_id}/toggle", response_class=HTMLResponse)
def template_toggle(request: Request, template_id: int, conn=Depends(get_db)):
    """Toggle active status of a template."""
    conn.execute(
        "UPDATE prompt_templates SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (template_id,),
    )
    conn.commit()
    t = _get_template_by_id(conn, template_id)
    return templates.TemplateResponse("prompt_builder/_template_card.html", {
        "request": request,
        "t": t,
        "manage_mode": True,
    })


@router.post("/templates/{template_id}/delete", response_class=HTMLResponse)
def template_delete(request: Request, template_id: int, conn=Depends(get_db)):
    """Delete a custom template. Built-in templates cannot be deleted."""
    t = _get_template_by_id(conn, template_id)
    if not t or t.get("is_builtin"):
        return HTMLResponse("<p class='text-red-500'>Cannot delete built-in templates.</p>")

    conn.execute("DELETE FROM prompt_templates WHERE id = ? AND is_builtin = 0", (template_id,))
    conn.commit()
    return HTMLResponse("")  # Remove the card from the DOM
