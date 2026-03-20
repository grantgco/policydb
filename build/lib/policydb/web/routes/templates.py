"""Email template management and compose routes."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb import config as cfg
from policydb.email_templates import (
    CONTEXT_TOKENS,
    CONTEXT_TOKEN_GROUPS,
    client_context,
    followup_context,
    location_context,
    policy_context,
    render_tokens,
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
    "location": "Location",
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


def _load_contacts(conn, policy_uid: str = "", client_id: int = 0, project_name: str = "") -> list[dict]:
    """Load all contacts (client + policy) for recipient selection in compose panel."""
    contacts: list[dict] = []
    seen: set[str] = set()
    resolved_client_id = client_id

    if policy_uid:
        policy = conn.execute(
            "SELECT id, client_id FROM policies WHERE policy_uid=?", (policy_uid.upper(),)
        ).fetchone()
        if policy:
            resolved_client_id = policy["client_id"]
            # Policy contacts (placement colleagues, underwriters)
            pc_rows = conn.execute(
                """SELECT co.name, co.email, cpa.role FROM contact_policy_assignments cpa
                   JOIN contacts co ON cpa.contact_id = co.id
                   WHERE cpa.policy_id=? AND co.email IS NOT NULL AND TRIM(co.email) != ''
                   ORDER BY co.name""",
                (policy["id"],),
            ).fetchall()
            for r in pc_rows:
                key = r["email"].strip().lower()
                if key not in seen:
                    seen.add(key)
                    contacts.append({"name": r["name"], "email": r["email"], "role": r["role"] or "Policy Contact", "source": "policy"})
    elif project_name and client_id:
        # Location/project compose: load contacts from all policies in this project
        pc_rows = conn.execute(
            """SELECT DISTINCT co.name, co.email, cpa.role
               FROM contact_policy_assignments cpa
               JOIN contacts co ON cpa.contact_id = co.id
               JOIN policies p ON cpa.policy_id = p.id
               WHERE p.client_id = ? AND p.archived = 0
                 AND LOWER(TRIM(COALESCE(p.project_name, ''))) = LOWER(TRIM(?))
                 AND co.email IS NOT NULL AND TRIM(co.email) != ''
               ORDER BY co.name""",
            (client_id, project_name),
        ).fetchall()
        for r in pc_rows:
            key = r["email"].strip().lower()
            if key not in seen:
                seen.add(key)
                contacts.append({"name": r["name"], "email": r["email"], "role": r["role"] or "Policy Contact", "source": "policy"})

    if resolved_client_id:
        # Client contacts
        cc_rows = conn.execute(
            """SELECT co.name, co.email, cca.role, cca.contact_type
               FROM contact_client_assignments cca
               JOIN contacts co ON cca.contact_id = co.id
               WHERE cca.client_id=? AND co.email IS NOT NULL AND TRIM(co.email) != ''
               ORDER BY cca.contact_type DESC, co.name""",
            (resolved_client_id,),
        ).fetchall()
        for r in cc_rows:
            key = r["email"].strip().lower()
            if key not in seen:
                seen.add(key)
                source = "internal" if r["contact_type"] == "internal" else "client"
                contacts.append({"name": r["name"], "email": r["email"], "role": r["role"] or r["contact_type"].title(), "source": source})

    return contacts


# ── Compose panel ─────────────────────────────────────────────────────────────

@router.get("/render", response_class=HTMLResponse)
def render_template(
    request: Request,
    template_id: int,
    policy_uid: str = "",
    client_id: int = 0,
    project_name: str = "",
    conn=Depends(get_db),
):
    """HTMX partial: render a single template with real data (just the output area)."""
    tpl = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not tpl:
        return HTMLResponse('<p class="text-sm text-red-400 p-4">Template not found.</p>')
    if project_name and client_id:
        ctx = location_context(conn, client_id, project_name)
    elif policy_uid:
        ctx = policy_context(conn, policy_uid)
    elif client_id:
        ctx = client_context(conn, client_id)
    else:
        ctx = {}
    rendered_subject = render_tokens(tpl["subject_template"], ctx)
    rendered_body = render_tokens(tpl["body_template"], ctx)
    ref_tag = ctx.get("ref_tag", "")
    if ref_tag:
        rendered_body = rendered_body.rstrip() + "\n\n" + ref_tag if rendered_body.strip() else ref_tag

    # Load all contacts for recipient selection
    all_contacts = _load_contacts(conn, policy_uid=policy_uid, client_id=client_id, project_name=project_name)

    return templates.TemplateResponse("templates/_compose_rendered.html", {
        "request": request,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "selected_template": dict(tpl),
        "policy_uid": policy_uid,
        "client_id": client_id,
        "project_name": project_name,
        "ctx": ctx,
        "all_contacts": all_contacts,
    })


@router.get("/compose", response_class=HTMLResponse)
def compose_panel(
    request: Request,
    context: str = "policy",
    policy_uid: str = "",
    client_id: int = 0,
    project_name: str = "",
    template_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX partial: template picker + rendered output."""
    context = context if context in (*_CONTEXT_LABELS, "followup") else "policy"

    # Load available templates for this context (each also shows general)
    if context == "policy":
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('policy','general') ORDER BY context, name"
        ).fetchall()
    elif context == "client":
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('client','general') ORDER BY context, name"
        ).fetchall()
    elif context == "location":
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('location','general') ORDER BY context, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_templates WHERE context='general' ORDER BY name"
        ).fetchall()
    available = [dict(r) for r in rows]

    # Build rendering context
    if project_name and client_id:
        ctx = location_context(conn, client_id, project_name)
    elif policy_uid:
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
            ref_tag = ctx.get("ref_tag", "")
            if ref_tag:
                rendered_body = rendered_body.rstrip() + "\n\n" + ref_tag if rendered_body.strip() else ref_tag

    # Build quick-email fallback: primary contact email + pre-rendered subject
    from policydb import config as _cfg
    quick_email = ctx.get("primary_email", "")
    if context == "policy":
        _subj_tpl = _cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    elif context == "location":
        _subj_tpl = _cfg.get("email_subject_location", "Re: {{client_name}} — {{location_name}}")
    else:
        _subj_tpl = _cfg.get("email_subject_client", "Re: {{client_name}}")
    quick_subject = render_tokens(_subj_tpl, ctx) if ctx else ""

    # Load all contacts for recipient selection
    all_contacts = _load_contacts(conn, policy_uid=policy_uid, client_id=client_id, project_name=project_name)

    return templates.TemplateResponse("templates/_compose_panel.html", {
        "request": request,
        "available": available,
        "selected_template": selected_template,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "context": context,
        "policy_uid": policy_uid,
        "client_id": client_id,
        "project_name": project_name,
        "template_id": template_id,
        "quick_email": quick_email,
        "quick_subject": quick_subject,
        "ref_tag": ctx.get("ref_tag", ""),
        "all_contacts": all_contacts,
    })
