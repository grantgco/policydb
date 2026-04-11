"""Unified email compose slideover."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates as jinja_templates
from policydb.email_templates import (
    render_tokens,
    policy_context,
    client_context,
    location_context,
    followup_context,
    timeline_context,
    issue_context,
    CONTEXT_TOKEN_GROUPS,
    CONTEXT_TOKENS,
)

router = APIRouter(prefix="/compose", tags=["compose"])


# ── Recipient loading ────────────────────────────────────────────────────────


def _load_recipients(
    conn,
    policy_uid: str = "",
    client_id: int = 0,
    project_name: str = "",
    mode: str = "",
    issue_uid: str = "",
) -> list[dict]:
    """Load contacts for recipient selection with role badges and pre_checked flags.

    Returns a deduped list of dicts:
        {name, email, role, badge, pre_checked, source}

    Badge types:
        CLIENT   — external contacts assigned to the client
        INTERNAL — internal team members on the client
        PLACEMENT — placement colleagues on the policy
        UNDERWRITER — underwriters/other policy contacts
    """
    recipients: list[dict] = []
    seen: set[str] = set()  # dedup by lowercase email
    resolved_client_id = client_id

    # ── Issue mode: resolve client/policy from issue ────────────────────
    if issue_uid:
        issue_row = conn.execute(
            "SELECT client_id, policy_id, program_id FROM activity_log WHERE issue_uid=? AND item_kind='issue'",
            (issue_uid,),
        ).fetchone()
        if issue_row:
            if issue_row["client_id"]:
                resolved_client_id = issue_row["client_id"]
            if issue_row["policy_id"]:
                # Load contacts from the linked policy
                policy = conn.execute(
                    "SELECT policy_uid FROM policies WHERE id=?",
                    (issue_row["policy_id"],),
                ).fetchone()
                if policy:
                    policy_uid = policy["policy_uid"]
            if issue_row.get("program_id"):
                # Program-level issue: load contacts from ALL policies in the program
                prog_policies = conn.execute(
                    """SELECT cpa.contact_id, co.name, co.email, cpa.role, cpa.is_placement_colleague
                       FROM contact_policy_assignments cpa
                       JOIN contacts co ON cpa.contact_id = co.id
                       JOIN policies p ON cpa.policy_id = p.id
                       WHERE p.program_id = ? AND p.archived = 0
                         AND co.email IS NOT NULL AND TRIM(co.email) != ''
                       ORDER BY co.name""",
                    (issue_row["program_id"],),
                ).fetchall()
                for r in prog_policies:
                    key = r["email"].strip().lower()
                    if key not in seen:
                        seen.add(key)
                        is_pc = r["is_placement_colleague"]
                        recipients.append({
                            "name": r["name"] or "",
                            "email": r["email"],
                            "role": r["role"] or ("Placement Colleague" if is_pc else "Underwriter"),
                            "badge": "PLACEMENT" if is_pc else "UNDERWRITER",
                            "pre_checked": False,
                            "source": "policy",
                        })

    # ── Policy contacts ──────────────────────────────────────────────────
    if policy_uid:
        policy = conn.execute(
            "SELECT id, client_id FROM policies WHERE policy_uid=?",
            (policy_uid.upper(),),
        ).fetchone()
        if policy:
            resolved_client_id = policy["client_id"]
            pc_rows = conn.execute(
                """SELECT co.name, co.email, cpa.role, cpa.is_placement_colleague
                   FROM contact_policy_assignments cpa
                   JOIN contacts co ON cpa.contact_id = co.id
                   WHERE cpa.policy_id=? AND co.email IS NOT NULL AND TRIM(co.email) != ''
                   ORDER BY co.name""",
                (policy["id"],),
            ).fetchall()
            for r in pc_rows:
                key = r["email"].strip().lower()
                if key not in seen:
                    seen.add(key)
                    is_pc = r["is_placement_colleague"]
                    recipients.append({
                        "name": r["name"] or "",
                        "email": r["email"],
                        "role": r["role"] or ("Placement Colleague" if is_pc else "Underwriter"),
                        "badge": "PLACEMENT" if is_pc else "UNDERWRITER",
                        "pre_checked": False,  # external stakeholders opt-in only
                        "source": "policy",
                    })

    elif project_name and client_id:
        # Location/project: load contacts from all policies in this project
        pc_rows = conn.execute(
            """SELECT DISTINCT co.name, co.email, cpa.role, cpa.is_placement_colleague
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
                is_pc = r["is_placement_colleague"]
                recipients.append({
                    "name": r["name"] or "",
                    "email": r["email"],
                    "role": r["role"] or ("Placement Colleague" if is_pc else "Underwriter"),
                    "badge": "PLACEMENT" if is_pc else "UNDERWRITER",
                    "pre_checked": False,  # external stakeholders opt-in only
                    "source": "policy",
                })

    # ── Client contacts (internal + external) ────────────────────────────
    if resolved_client_id:
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
                if r["contact_type"] == "internal":
                    recipients.append({
                        "name": r["name"] or "",
                        "email": r["email"],
                        "role": r["role"] or "Internal Team",
                        "badge": "INTERNAL",
                        "pre_checked": True,  # internal team always pre-checked
                        "source": "internal",
                    })
                elif r["contact_type"] == "external":
                    # External stakeholders — show with distinct badge
                    if mode == "rfi_notify":
                        continue
                    recipients.append({
                        "name": r["name"] or "",
                        "email": r["email"],
                        "role": r["role"] or "External",
                        "badge": "EXTERNAL",
                        "pre_checked": False,
                        "source": "external",
                    })
                else:
                    # Client contacts
                    if mode == "rfi_notify":
                        continue
                    recipients.append({
                        "name": r["name"] or "",
                        "email": r["email"],
                        "role": r["role"] or "Client Contact",
                        "badge": "CLIENT",
                        "pre_checked": False,
                        "source": "client",
                    })

    return recipients


# ── Compose panel endpoint ───────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def compose_panel(
    request: Request,
    conn=Depends(get_db),
    context: str = Query("policy"),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    bundle_id: int = Query(0),
    mode: str = Query(""),
    to_email: str = Query(""),
    template_id: int = Query(0),
    issue_uid: str = Query(""),
    program_uid: str = Query(""),
):
    """Return the compose slideover HTML partial."""

    # ── Build rendering context based on params ──────────────────────────
    ctx: dict = {}

    if issue_uid:
        ctx = issue_context(conn, issue_uid)
        # Resolve client_id from issue if not provided
        if not client_id and ctx.get("client_name"):
            issue_row = conn.execute(
                "SELECT client_id FROM activity_log WHERE issue_uid=? AND item_kind='issue'",
                (issue_uid,),
            ).fetchone()
            if issue_row:
                client_id = issue_row["client_id"] or 0
    elif mode == "rfi_notify" and bundle_id:
        # RFI notify mode — lazy import since rfi_notify_context is added in Task 4
        try:
            from policydb.email_templates import rfi_notify_context
            ctx = rfi_notify_context(conn, bundle_id)
        except ImportError:
            # rfi_notify_context not yet implemented — fall back to empty context
            ctx = {}
        # Resolve client_id from bundle if not provided
        if not client_id and bundle_id:
            bundle_row = conn.execute(
                "SELECT client_id FROM client_request_bundles WHERE id=?", (bundle_id,)
            ).fetchone()
            if bundle_row:
                client_id = bundle_row["client_id"]
    elif program_uid:
        from policydb.email_templates import program_context as _program_ctx
        ctx = _program_ctx(conn, program_uid)
        if not client_id:
            _prog_row = conn.execute(
                "SELECT client_id FROM programs WHERE program_uid=?", (program_uid,)
            ).fetchone()
            if _prog_row:
                client_id = _prog_row["client_id"]
    elif policy_uid:
        ctx = policy_context(conn, policy_uid)
        # Overlay timeline context if available
        try:
            tl_ctx = timeline_context(conn, policy_uid)
            if tl_ctx:
                ctx.update(tl_ctx)
        except Exception:
            pass
        # Overlay location aggregate tokens if policy linked to a project
        _pol_row = conn.execute(
            "SELECT client_id, project_name FROM policies WHERE policy_uid=?",
            (policy_uid.upper(),),
        ).fetchone()
        if _pol_row:
            if not client_id:
                client_id = _pol_row["client_id"]
            if _pol_row["project_name"]:
                try:
                    loc_ctx = location_context(conn, _pol_row["client_id"], _pol_row["project_name"])
                    for k, v in loc_ctx.items():
                        if k not in ctx or not ctx[k]:
                            ctx[k] = v
                except Exception:
                    pass
    elif project_name and client_id:
        ctx = location_context(conn, client_id, project_name)
    elif client_id:
        ctx = client_context(conn, client_id)

    # ── Load recipients ──────────────────────────────────────────────────
    recipients = _load_recipients(
        conn,
        policy_uid=policy_uid,
        client_id=client_id,
        project_name=project_name,
        mode=mode,
        issue_uid=issue_uid,
    )

    # ── Determine primary To contact ─────────────────────────────────────
    primary_to = {}
    if to_email:
        # Explicit To address passed from per-contact email button
        for r in recipients:
            if r["email"].strip().lower() == to_email.strip().lower():
                primary_to = r
                break
        if not primary_to:
            primary_to = {"name": "", "email": to_email, "role": "", "badge": "", "source": "manual"}
    elif mode != "rfi_notify":
        # Default: first CLIENT badge contact only — never default to
        # placement colleagues or underwriters in the To field
        client_contacts = [r for r in recipients if r["badge"] == "CLIENT"]
        if client_contacts:
            primary_to = client_contacts[0]

    # ── Pre-fill subject from config template ────────────────────────────
    if issue_uid:
        subj_tpl = cfg.get(
            "email_subject_issue",
            "Re: {{client_name}} — Issue: {{issue_subject}}",
        )
    elif mode == "rfi_notify":
        subj_tpl = cfg.get(
            "email_subject_rfi_notify",
            "FYI: {{client_name}} — {{rfi_uid}} Items Received",
        )
    elif program_uid:
        subj_tpl = cfg.get(
            "email_subject_program",
            "Re: {{client_name}} — {{program_name}}",
        )
    elif policy_uid:
        subj_tpl = cfg.get(
            "email_subject_policy",
            "Re: {{client_name}} — {{policy_type}}",
        )
    elif project_name:
        subj_tpl = cfg.get(
            "email_subject_location",
            "Re: {{client_name}} — {{location_name}}",
        )
    else:
        subj_tpl = cfg.get(
            "email_subject_client",
            "Re: {{client_name}}",
        )
    subject = render_tokens(subj_tpl, ctx) if ctx else ""

    # ── Build body ───────────────────────────────────────────────────────
    body = ""
    if mode == "rfi_notify" and bundle_id:
        # Auto-generate received/outstanding items list
        try:
            items = conn.execute(
                "SELECT item_label, received FROM client_request_items WHERE bundle_id=? ORDER BY sort_order, id",
                (bundle_id,),
            ).fetchall()
            received = [i["item_label"] for i in items if i["received"]]
            outstanding = [i["item_label"] for i in items if not i["received"]]
            lines: list[str] = []
            if received:
                lines.append("Items Received:")
                for item in received:
                    lines.append(f"  - {item}")
            if outstanding:
                if received:
                    lines.append("")
                lines.append("Still Outstanding:")
                for item in outstanding:
                    lines.append(f"  - {item}")
            body = "\n".join(lines)
        except Exception:
            pass

    # Append ref tag to body
    ref_tag = ctx.get("ref_tag", "")
    if ref_tag:
        pdb_ref = f"[PDB:{ref_tag}]"
        body = body.rstrip() + "\n\n" + pdb_ref if body.strip() else pdb_ref

    # ── Template rendering (if template_id provided) ─────────────────────
    selected_template = None
    if template_id:
        tpl = conn.execute(
            "SELECT * FROM email_templates WHERE id=?", (template_id,)
        ).fetchone()
        if tpl:
            selected_template = dict(tpl)
            subject = render_tokens(tpl["subject_template"], ctx)
            rendered_body = render_tokens(tpl["body_template"], ctx)
            # Re-append ref tag
            if ref_tag:
                pdb_ref = f"[PDB:{ref_tag}]"
                rendered_body = rendered_body.rstrip() + "\n\n" + pdb_ref if rendered_body.strip() else pdb_ref
            body = rendered_body

    # ── Load available templates for dropdown ────────────────────────────
    if program_uid:
        tpl_rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('policy','general') ORDER BY context, name"
        ).fetchall()
    elif policy_uid or project_name:
        tpl_rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('policy','general') ORDER BY context, name"
        ).fetchall()
    elif client_id:
        tpl_rows = conn.execute(
            "SELECT * FROM email_templates WHERE context IN ('client','general') ORDER BY context, name"
        ).fetchall()
    else:
        tpl_rows = conn.execute(
            "SELECT * FROM email_templates WHERE context='general' ORDER BY name"
        ).fetchall()
    available_templates = [dict(r) for r in tpl_rows]

    return jinja_templates.TemplateResponse("_compose_slideover.html", {
        "request": request,
        "context": context,
        "policy_uid": policy_uid,
        "client_id": client_id,
        "project_name": project_name,
        "bundle_id": bundle_id,
        "mode": mode,
        "issue_uid": issue_uid,
        "program_uid": program_uid,
        "recipients": recipients,
        "primary_to": primary_to,
        "subject": subject,
        "body": body,
        "ref_tag": ref_tag,
        "selected_template": selected_template,
        "available_templates": available_templates,
        "template_id": template_id,
        "ctx": ctx,
    })


# ── Recipients JSON endpoint ─────────────────────────────────────────────────


@router.get("/recipients")
def compose_recipients(
    conn=Depends(get_db),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    mode: str = Query(""),
):
    """Return recipient list as JSON for dynamic updates."""
    recipients = _load_recipients(
        conn,
        policy_uid=policy_uid,
        client_id=client_id,
        project_name=project_name,
        mode=mode,
    )
    return JSONResponse(recipients)


# ── Template render endpoint ─────────────────────────────────────────────────


@router.get("/render")
def compose_render(
    conn=Depends(get_db),
    template_id: int = Query(0),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    bundle_id: int = Query(0),
    mode: str = Query(""),
):
    """Render a selected template and return JSON {subject, body}.

    Returns JSON (not HTML) to avoid XSS and duplicate element issues.
    """
    if not template_id:
        return JSONResponse({"subject": "", "body": ""})

    tpl = conn.execute(
        "SELECT * FROM email_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not tpl:
        return JSONResponse({"subject": "", "body": ""}, status_code=404)

    # Build rendering context — priority must match compose_panel()
    ctx: dict = {}
    if mode == "rfi_notify" and bundle_id:
        try:
            from policydb.email_templates import rfi_notify_context
            ctx = rfi_notify_context(conn, bundle_id)
        except ImportError:
            ctx = {}
    elif policy_uid:
        ctx = policy_context(conn, policy_uid)
        try:
            tl_ctx = timeline_context(conn, policy_uid)
            if tl_ctx:
                ctx.update(tl_ctx)
        except Exception:
            pass
        # Overlay location aggregate tokens if policy linked to a project
        _pol_row = conn.execute(
            "SELECT client_id, project_name FROM policies WHERE policy_uid=?",
            (policy_uid.upper(),),
        ).fetchone()
        if _pol_row and _pol_row["project_name"]:
            try:
                loc_ctx = location_context(conn, _pol_row["client_id"], _pol_row["project_name"])
                for k, v in loc_ctx.items():
                    if k not in ctx or not ctx[k]:
                        ctx[k] = v
            except Exception:
                pass
    elif project_name and client_id:
        ctx = location_context(conn, client_id, project_name)
    elif client_id:
        ctx = client_context(conn, client_id)

    rendered_subject = render_tokens(tpl["subject_template"], ctx)
    rendered_body = render_tokens(tpl["body_template"], ctx)

    # Append ref tag
    ref_tag = ctx.get("ref_tag", "")
    if ref_tag:
        pdb_ref = f"[PDB:{ref_tag}]"
        rendered_body = (
            rendered_body.rstrip() + "\n\n" + pdb_ref
            if rendered_body.strip()
            else pdb_ref
        )

    return JSONResponse({"subject": rendered_subject, "body": rendered_body})


# ── Copy policy table for compose panel ──────────────────────────────────────


@router.get("/copy-table", response_class=JSONResponse)
def compose_copy_table(
    conn=Depends(get_db),
    issue_uid: str = Query(""),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
):
    """Return {"html": ..., "text": ...} for clipboard copy of linked policies."""
    from policydb.email_templates import build_policy_table, _render_policy_table_html, _render_policy_table_text

    rows = None

    if issue_uid:
        issue_row = conn.execute(
            "SELECT client_id, policy_id, program_id FROM activity_log WHERE issue_uid=? AND item_kind='issue'",
            (issue_uid,),
        ).fetchone()
        if issue_row:
            if not client_id:
                client_id = issue_row["client_id"] or 0
            if issue_row["program_id"]:
                rows = [dict(r) for r in conn.execute(
                    """SELECT policy_type, carrier, access_point, policy_number,
                              effective_date, expiration_date, premium, limit_amount, description
                       FROM policies WHERE program_id=? AND archived=0 ORDER BY policy_type""",
                    (issue_row["program_id"],),
                ).fetchall()]
            elif issue_row["policy_id"]:
                rows = [dict(r) for r in conn.execute(
                    """SELECT policy_type, carrier, access_point, policy_number,
                              effective_date, expiration_date, premium, limit_amount, description
                       FROM policies WHERE id=? AND archived=0""",
                    (issue_row["policy_id"],),
                ).fetchall()]

    if rows is not None:
        return JSONResponse({"html": _render_policy_table_html(rows), "text": _render_policy_table_text(rows)})

    if client_id:
        result = build_policy_table(conn, client_id, project_name or None)
        return JSONResponse(result)

    return JSONResponse({"html": "", "text": ""}, status_code=400)
