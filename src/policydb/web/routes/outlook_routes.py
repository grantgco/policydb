"""Outlook integration routes — compose drafts and sync emails."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from policydb import config as cfg
from policydb.web.app import get_db, templates as jinja_templates
from policydb.email_templates import (
    render_tokens,
    policy_context,
    client_context,
    location_context,
    issue_context,
    markdown_to_html,
    wrap_email_html,
    _render_policy_table_html,
)
from policydb.outlook import is_outlook_available, create_draft

router = APIRouter(prefix="/outlook", tags=["outlook"])

# Module-level mutex so two browser tabs / two parallel POSTs to /outlook/sync
# can't both spawn osascript subprocesses and race on last_outlook_sync.
# `acquire(blocking=False)` lets us reject a second sync immediately with a
# 409 instead of queuing it up.
_sync_lock = threading.Lock()


class ComposeRequest(BaseModel):
    to: str = ""
    cc: list[str] = []
    subject: str = ""
    body: str = ""
    policy_uid: str = ""
    client_id: int = 0
    issue_uid: str = ""
    project_name: str = ""
    include_policy_table: bool = False
    formal_format: bool = False


@router.get("/status")
def outlook_status():
    """Check if Outlook is available."""
    return JSONResponse({"available": is_outlook_available()})


@router.post("/compose")
def outlook_compose(req: ComposeRequest, conn=Depends(get_db)):
    """Create an Outlook draft with HTML-formatted body.

    Renders the body as Markdown → HTML, wraps in the Marsh email shell,
    optionally inserts a policy table, and calls AppleScript to create
    the draft in Outlook.
    """
    # Build context for ref tag and policy table
    ctx: dict = {}
    policy_table_html = None

    if req.policy_uid:
        ctx = policy_context(conn, req.policy_uid)
        # Only include policy table when explicitly requested
        if req.include_policy_table:
            # Single policy: only include that policy's row
            rows = conn.execute(
                """SELECT policy_type, carrier, access_point, policy_number,
                          effective_date, expiration_date, premium, limit_amount, description
                   FROM policies WHERE policy_uid=? AND archived=0""",
                (req.policy_uid.upper(),),
            ).fetchall()
            if rows:
                policy_table_html = _render_policy_table_html([dict(r) for r in rows])
    elif req.issue_uid:
        # Issue context — resolve linked policy/client/program
        issue_row = conn.execute(
            "SELECT client_id, policy_id, program_id FROM activity_log WHERE issue_uid=? AND item_kind='issue'",
            (req.issue_uid,),
        ).fetchone()
        if issue_row and issue_row["client_id"]:
            ctx = issue_context(conn, req.issue_uid)
            # Issue table: linked policies (program or single policy)
            if req.include_policy_table:
                if issue_row.get("program_id"):
                    rows = conn.execute(
                        """SELECT policy_type, carrier, access_point, policy_number,
                                  effective_date, expiration_date, premium, limit_amount, description
                           FROM policies WHERE program_id=? AND archived=0
                           ORDER BY policy_type""",
                        (issue_row["program_id"],),
                    ).fetchall()
                elif issue_row.get("policy_id"):
                    rows = conn.execute(
                        """SELECT policy_type, carrier, access_point, policy_number,
                                  effective_date, expiration_date, premium, limit_amount, description
                           FROM policies WHERE id=? AND archived=0""",
                        (issue_row["policy_id"],),
                    ).fetchall()
                else:
                    rows = []
                if rows:
                    policy_table_html = _render_policy_table_html([dict(r) for r in rows])
    elif req.project_name and req.client_id:
        ctx = location_context(conn, req.client_id, req.project_name)
        # Project table: all policies in the project
        if req.include_policy_table:
            rows = conn.execute(
                """SELECT policy_type, carrier, access_point, policy_number,
                          effective_date, expiration_date, premium, limit_amount, description
                   FROM policies WHERE client_id=? AND archived=0
                     AND LOWER(TRIM(COALESCE(project_name, ''))) = LOWER(TRIM(?))
                   ORDER BY policy_type""",
                (req.client_id, req.project_name),
            ).fetchall()
            if rows:
                policy_table_html = _render_policy_table_html([dict(r) for r in rows])
    elif req.client_id:
        ctx = client_context(conn, req.client_id)

    # Render tokens in subject and body
    subject = render_tokens(req.subject, ctx) if ctx else req.subject
    body_text = render_tokens(req.body, ctx) if ctx else req.body

    ref_tag = ctx.get("ref_tag", "")

    # Decide format: branded HTML shell when formal format or policy table,
    # otherwise plain text with ref tag appended (normal quick email)
    if req.formal_format or policy_table_html:
        # Formal email — Marsh-branded HTML shell with table
        if ref_tag:
            pdb_ref = f"[PDB:{ref_tag}]"
            body_text = body_text.replace(pdb_ref, "").rstrip()
        body_html = markdown_to_html(body_text)
        show_header = cfg.get("outlook_email_shell_header", True)
        html_body = wrap_email_html(
            body_html,
            ref_tag=ref_tag,
            policy_table_html=policy_table_html,
            show_header=show_header,
        )
    else:
        # Plain quick email — just the body text with ref tag
        if ref_tag:
            pdb_ref = f"[PDB:{ref_tag}]"
            if pdb_ref not in body_text:
                body_text = body_text.rstrip() + "\n\n" + pdb_ref if body_text.strip() else pdb_ref
        html_body = body_text

    # Create draft via AppleScript
    result = create_draft(
        to=req.to,
        cc=req.cc,
        subject=subject,
        html_body=html_body,
    )

    return JSONResponse(result)


@router.post("/sync")
def outlook_sync(request: Request, conn=Depends(get_db)):
    """Run Outlook email sweep — scan Sent/Received/Flagged and create activities.

    Returns an HTML partial with sync results. If another sync is already in
    progress (e.g. user clicked Sync in two tabs), returns a 409 with an
    error banner instead of running a second sweep in parallel — parallel
    runs would race on `last_outlook_sync`, spawn duplicate osascript
    subprocesses, and contend for SQLite write locks.
    """
    from policydb.email_sync import sync_outlook

    # Non-blocking: reject the second caller immediately rather than queueing
    if not _sync_lock.acquire(blocking=False):
        return jinja_templates.TemplateResponse(
            "outlook/_sync_results.html",
            {
                "request": request,
                "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
                "suggestions": [],
                "skipped": 0,
                "errors": ["Another Outlook sync is already running. Please wait for it to finish."],
                "total_scanned": 0,
                "since": "",
                "new_contacts_found": 0,
                "thread_inherited": 0,
                "contact_sync": None,
            },
            status_code=409,
        )

    try:
        results = sync_outlook(conn)
        # Phase 2 — push PolicyDB contacts to Outlook (fenced by PDB category).
        # Folds results into the same banner; errors don't block email phase.
        try:
            from policydb.contact_sync import sync_contacts_to_outlook
            contact_results = sync_contacts_to_outlook(conn)
        except Exception as e:
            contact_results = {
                "ok": False,
                "created": 0,
                "updated": 0,
                "deleted": 0,
                "errors": [f"Contact sync crashed: {e}"],
                "skipped_unavailable": False,
                "ambiguous_bootstrap": [],
                "push_set_size": 0,
            }
    finally:
        _sync_lock.release()

    return jinja_templates.TemplateResponse("outlook/_sync_results.html", {
        "request": request,
        "contact_sync": contact_results,
        **results,
    })


