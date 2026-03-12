"""Email template management and compose routes."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb import config as cfg
from policydb.email_templates import (
    CONTEXT_TOKENS,
    client_context,
    followup_context,
    policy_context,
    render_tokens,
)
from policydb.web.app import get_db, templates

# Pre-serialize tokens for embedding in JS — lists of [key, label] pairs per context
_CONTEXT_TOKENS_JSON = _json.dumps({k: list(v) for k, v in CONTEXT_TOKENS.items()})

router = APIRouter(prefix="/templates")

_CONTEXT_LABELS = {
    "policy": "Policy",
    "client": "Client",
    "general": "General",
}


# ── Management CRUD ───────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def templates_index(request: Request, conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT * FROM email_templates ORDER BY context, sort_order, name"
    ).fetchall()
    all_templates = [dict(r) for r in rows]
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        "active": "templates",
        "all_templates": all_templates,
        "context_labels": _CONTEXT_LABELS,
        "context_tokens": CONTEXT_TOKENS,
        "context_tokens_json": _CONTEXT_TOKENS_JSON,
    })


@router.post("/new")
def template_create(
    name: str = Form(...),
    context: str = Form("policy"),
    description: str = Form(""),
    subject_template: str = Form(""),
    body_template: str = Form(""),
    conn=Depends(get_db),
):
    from fastapi.responses import RedirectResponse
    context = context if context in _CONTEXT_LABELS else "policy"
    conn.execute(
        """INSERT INTO email_templates (name, context, description, subject_template, body_template)
           VALUES (?, ?, ?, ?, ?)""",
        (name.strip(), context, description.strip() or None,
         subject_template, body_template),
    )
    conn.commit()
    return RedirectResponse("/templates", status_code=303)


@router.get("/{template_id}/edit", response_class=HTMLResponse)
def template_edit_form(request: Request, template_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("templates/_template_form.html", {
        "request": request,
        "t": dict(row),
        "context_labels": _CONTEXT_LABELS,
        "context_tokens": CONTEXT_TOKENS,
        "context_tokens_json": _CONTEXT_TOKENS_JSON,
        "editing": True,
    })


@router.post("/{template_id}/edit", response_class=HTMLResponse)
def template_edit_save(
    request: Request,
    template_id: int,
    name: str = Form(...),
    context: str = Form("policy"),
    description: str = Form(""),
    subject_template: str = Form(""),
    body_template: str = Form(""),
    conn=Depends(get_db),
):
    context = context if context in _CONTEXT_LABELS else "policy"
    conn.execute(
        """UPDATE email_templates
           SET name=?, context=?, description=?, subject_template=?, body_template=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (name.strip(), context, description.strip() or None,
         subject_template, body_template, template_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not row:
        return HTMLResponse("")
    return templates.TemplateResponse("templates/_template_card.html", {
        "request": request,
        "t": dict(row),
        "context_labels": _CONTEXT_LABELS,
        "context_tokens": CONTEXT_TOKENS,
        "context_tokens_json": _CONTEXT_TOKENS_JSON,
    })


@router.post("/{template_id}/delete", response_class=HTMLResponse)
def template_delete(template_id: int, conn=Depends(get_db)):
    conn.execute("DELETE FROM email_templates WHERE id=?", (template_id,))
    conn.commit()
    return HTMLResponse("")


# ── Compose panel ─────────────────────────────────────────────────────────────

@router.get("/render", response_class=HTMLResponse)
def render_template(
    request: Request,
    template_id: int,
    policy_uid: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX partial: render a single template with real data (just the output area)."""
    tpl = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not tpl:
        return HTMLResponse('<p class="text-sm text-red-400 p-4">Template not found.</p>')
    if policy_uid:
        ctx = policy_context(conn, policy_uid)
    elif client_id:
        ctx = client_context(conn, client_id)
    else:
        ctx = {}
    rendered_subject = render_tokens(tpl["subject_template"], ctx)
    rendered_body = render_tokens(tpl["body_template"], ctx)
    return templates.TemplateResponse("templates/_compose_rendered.html", {
        "request": request,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "selected_template": dict(tpl),
        "policy_uid": policy_uid,
        "client_id": client_id,
        "ctx": ctx,
    })


@router.get("/compose", response_class=HTMLResponse)
def compose_panel(
    request: Request,
    context: str = "policy",
    policy_uid: str = "",
    client_id: int = 0,
    template_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX partial: template picker + rendered output."""
    context = context if context in (*_CONTEXT_LABELS, "followup") else "policy"

    # Load available templates for this context (policy also shows general)
    if context == "policy":
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('policy','general') ORDER BY context, name"
        ).fetchall()
    elif context == "client":
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('client','general') ORDER BY context, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context='general' ORDER BY name"
        ).fetchall()
    available = [dict(r) for r in rows]

    # Build rendering context
    if policy_uid:
        ctx = policy_context(conn, policy_uid)
    elif client_id:
        ctx = client_context(conn, client_id)
    else:
        ctx = {}

    # Render the selected template (if one is chosen)
    rendered_subject = ""
    rendered_body = ""
    selected_template = None
    if template_id:
        tpl = conn.execute(
            "SELECT * FROM email_templates WHERE id=?", (template_id,)
        ).fetchone()
        if tpl:
            selected_template = dict(tpl)
            rendered_subject = render_tokens(tpl["subject_template"], ctx)
            rendered_body = render_tokens(tpl["body_template"], ctx)

    return templates.TemplateResponse("templates/_compose_panel.html", {
        "request": request,
        "available": available,
        "selected_template": selected_template,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "context": context,
        "policy_uid": policy_uid,
        "client_id": client_id,
        "template_id": template_id,
    })
