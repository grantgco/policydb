"""Open Tasks Panel — shared command-center panel on issue/client/program/policy
pages. See docs/superpowers/specs/2026-04-14-open-tasks-panel-design.md."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from policydb.queries import (
    create_followup_activity,
    get_open_tasks,
    sync_client_follow_up_date,
    sync_policy_follow_up_date,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/open-tasks", tags=["open-tasks"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _render_panel(
    request: Request,
    conn,
    scope_type: str,
    scope_id: int,
    toast_message: str | None = None,
    toast_kind: str = "success",
) -> HTMLResponse:
    data = get_open_tasks(conn, scope_type, scope_id)
    return templates.TemplateResponse(
        "_open_tasks_panel.html",
        {
            "request": request,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "data": data,
            "toast_message": toast_message,
            "toast_kind": toast_kind,
        },
    )


def _parse_activity_id(activity_id: str) -> tuple[str, int]:
    """Returns (kind, id). kind: 'activity' | 'policy' | 'client'."""
    if activity_id.startswith("P"):
        return ("policy", int(activity_id[1:]))
    if activity_id.startswith("C"):
        return ("client", int(activity_id[1:]))
    return ("activity", int(activity_id))


def _fetch_activity(conn, activity_id: int):
    row = conn.execute(
        "SELECT id, client_id, policy_id, follow_up_date, issue_id, subject "
        "FROM activity_log WHERE id = ?",
        (activity_id,),
    ).fetchone()
    return row


# ── Render ───────────────────────────────────────────────────────────────────

@router.get("/panel", response_class=HTMLResponse)
def panel(
    request: Request,
    scope_type: str,
    scope_id: int,
    conn=Depends(get_db),
):
    """Render the full Open Tasks panel for the given scope. Used for initial
    lazy-load from each page and as the target of every action's HTMX swap."""
    if scope_type not in ("issue", "client", "program", "policy"):
        raise HTTPException(status_code=400, detail="Invalid scope_type")
    return _render_panel(request, conn, scope_type, scope_id)
