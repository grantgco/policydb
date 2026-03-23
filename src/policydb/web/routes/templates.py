"""Email template management routes (CRUD)."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb.email_templates import (
    CONTEXT_TOKENS,
    CONTEXT_TOKEN_GROUPS,
)
from policydb.web.app import get_db, templates

# Pre-serialize grouped tokens for JS — {context: [[group_name, [[key, label], ...]], ...]}
_CONTEXT_TOKENS_JSON = _json.dumps({
    ctx: [[name, list(tokens)] for name, tokens in groups]
    for ctx, groups in CONTEXT_TOKEN_GROUPS.items()
})

router = APIRouter(prefix="/templates")

_CONTEXT_LABELS = {
    "policy": "Policy",
    "client": "Client",
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
        "context_token_groups": CONTEXT_TOKEN_GROUPS,
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
        "context_token_groups": CONTEXT_TOKEN_GROUPS,
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
        "context_token_groups": CONTEXT_TOKEN_GROUPS,
        "context_tokens_json": _CONTEXT_TOKENS_JSON,
    })


@router.post("/{template_id}/duplicate", response_class=HTMLResponse)
def template_duplicate(request: Request, template_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT * FROM email_templates WHERE id=?", (template_id,)).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    r = dict(row)
    conn.execute(
        """INSERT INTO email_templates (name, context, description, subject_template, body_template)
           VALUES (?, ?, ?, ?, ?)""",
        (f"{r['name']} (copy)", r["context"], r["description"],
         r["subject_template"], r["body_template"]),
    )
    conn.commit()
    new_row = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (conn.execute("SELECT last_insert_rowid()").fetchone()[0],)
    ).fetchone()
    return templates.TemplateResponse("templates/_template_card.html", {
        "request": request,
        "t": dict(new_row),
        "context_labels": _CONTEXT_LABELS,
        "context_tokens": CONTEXT_TOKENS,
        "context_token_groups": CONTEXT_TOKEN_GROUPS,
        "context_tokens_json": _CONTEXT_TOKENS_JSON,
    })


@router.post("/{template_id}/delete", response_class=HTMLResponse)
def template_delete(template_id: int, conn=Depends(get_db)):
    conn.execute("DELETE FROM email_templates WHERE id=?", (template_id,))
    conn.commit()
    return HTMLResponse("")


