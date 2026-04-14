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


# ── Actions ──────────────────────────────────────────────────────────────────

@router.post("/{activity_id}/done", response_class=HTMLResponse)
def action_done(
    request: Request,
    activity_id: str,
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind == "activity":
        act = _fetch_activity(conn, rid)
        if not act:
            raise HTTPException(404, "Activity not found")
        conn.execute(
            """UPDATE activity_log
               SET follow_up_done = 1,
                   auto_close_reason = 'manual',
                   auto_closed_at = datetime('now'),
                   auto_closed_by = 'open_tasks_panel'
               WHERE id = ?""",
            (rid,),
        )
        if act["policy_id"]:
            sync_policy_follow_up_date(conn, act["policy_id"])
        elif act["client_id"]:
            sync_client_follow_up_date(conn, act["client_id"])
    elif kind == "policy":
        conn.execute(
            "UPDATE policies SET follow_up_date = NULL WHERE id = ?", (rid,)
        )
    elif kind == "client":
        conn.execute(
            "UPDATE clients SET follow_up_date = NULL WHERE id = ?", (rid,)
        )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Task marked done",
    )


@router.post("/{activity_id}/snooze", response_class=HTMLResponse)
def action_snooze(
    request: Request,
    activity_id: str,
    days: int = Form(0),
    new_date: Optional[str] = Form(None),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    def _compute_new_date(current: Optional[str]) -> Optional[str]:
        if new_date:
            return new_date
        if not days:
            return current
        try:
            base = date.fromisoformat(current) if current else date.today()
        except (ValueError, TypeError):
            base = date.today()
        return (base + timedelta(days=days)).isoformat()

    kind, rid = _parse_activity_id(activity_id)
    if kind == "activity":
        act = _fetch_activity(conn, rid)
        if not act:
            raise HTTPException(404, "Activity not found")
        updated = _compute_new_date(act["follow_up_date"])
        conn.execute(
            "UPDATE activity_log SET follow_up_date = ? WHERE id = ?",
            (updated, rid),
        )
        if act["policy_id"]:
            sync_policy_follow_up_date(conn, act["policy_id"])
        elif act["client_id"]:
            sync_client_follow_up_date(conn, act["client_id"])
    elif kind == "policy":
        row = conn.execute(
            "SELECT follow_up_date FROM policies WHERE id = ?", (rid,)
        ).fetchone()
        updated = _compute_new_date(row["follow_up_date"] if row else None)
        conn.execute(
            "UPDATE policies SET follow_up_date = ? WHERE id = ?", (updated, rid)
        )
    elif kind == "client":
        row = conn.execute(
            "SELECT follow_up_date FROM clients WHERE id = ?", (rid,)
        ).fetchone()
        updated = _compute_new_date(row["follow_up_date"] if row else None)
        conn.execute(
            "UPDATE clients SET follow_up_date = ? WHERE id = ?", (updated, rid)
        )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message=f"Snoozed +{days}d" if days else "Snoozed",
    )


@router.post("/{activity_id}/disposition", response_class=HTMLResponse)
def action_disposition(
    request: Request,
    activity_id: str,
    move: str = Form(...),  # "my" or "waiting"
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Disposition only supported on activity-source rows")

    from policydb.config import get as cfg_get
    label = ""
    if move == "waiting":
        for d in cfg_get("follow_up_dispositions", []):
            if d.get("accountability") == "waiting_external":
                label = d.get("label", "Waiting on Response")
                break
    conn.execute(
        "UPDATE activity_log SET disposition = ? WHERE id = ?",
        (label or None, rid),
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Marked waiting" if move == "waiting" else "Marked my move",
    )


@router.post("/{activity_id}/log-close", response_class=HTMLResponse)
def action_log_close(
    request: Request,
    activity_id: str,
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Log & close only supported on activity-source rows")
    act = _fetch_activity(conn, rid)
    if not act:
        raise HTTPException(404, "Activity not found")
    conn.execute(
        """UPDATE activity_log
           SET follow_up_done = 1,
               follow_up_date = NULL,
               auto_close_reason = 'manual',
               auto_closed_at = datetime('now'),
               auto_closed_by = 'open_tasks_panel'
           WHERE id = ?""",
        (rid,),
    )
    if act["policy_id"]:
        sync_policy_follow_up_date(conn, act["policy_id"])
    elif act["client_id"]:
        sync_client_follow_up_date(conn, act["client_id"])
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Logged & closed",
    )


@router.post("/{activity_id}/attach", response_class=HTMLResponse)
def action_attach(
    request: Request,
    activity_id: str,
    target_issue_id: int = Form(...),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Attach only supported on activity-source rows")
    # Verify target is a valid issue row
    iss = conn.execute(
        "SELECT id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (target_issue_id,),
    ).fetchone()
    if not iss:
        raise HTTPException(404, "Target issue not found")
    conn.execute(
        "UPDATE activity_log SET issue_id = ? WHERE id = ?",
        (target_issue_id, rid),
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Attached to issue",
    )


@router.post("/{activity_id}/note", response_class=HTMLResponse)
def action_note(
    request: Request,
    activity_id: str,
    text: str = Form(...),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Note only supported on activity-source rows")
    if not text.strip():
        raise HTTPException(400, "Note text required")
    act = _fetch_activity(conn, rid)
    if not act:
        raise HTTPException(404, "Parent activity not found")

    create_followup_activity(
        conn,
        client_id=act["client_id"],
        policy_id=act["policy_id"],
        issue_id=act["issue_id"],
        subject=text.strip(),
        activity_type="Note",
        follow_up_date=None,
        follow_up_done=True,
        disposition="",
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Note saved",
    )
