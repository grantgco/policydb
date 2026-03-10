"""Activity and renewal routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from policydb import config as cfg
from policydb.queries import (
    get_activities,
    get_activity_by_id,
    get_overdue_followups,
    get_renewal_pipeline,
)
from policydb.web.app import get_db, templates

router = APIRouter()


@router.post("/activities/log", response_class=HTMLResponse)
def activity_log(
    request: Request,
    client_id: int = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    contact_person: str = Form(""),
    follow_up_date: str = Form(""),
    conn=Depends(get_db),
):
    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, contact_person, subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, activity_type,
         contact_person or None, subject, details or None,
         follow_up_date or None, account_exec),
    )
    conn.commit()
    # Return the new activity row as HTMX partial
    row = conn.execute(
        """SELECT a.*, c.name AS client_name FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE a.id = ?""",
        (cursor.lastrowid,),
    ).fetchone()
    a = dict(row)
    return templates.TemplateResponse("activities/_activity_row.html", {
        "request": request,
        "a": a,
    })


@router.post("/activities/{activity_id}/complete", response_class=HTMLResponse)
def activity_complete(request: Request, activity_id: int, conn=Depends(get_db)):
    conn.execute(
        "UPDATE activity_log SET follow_up_done=1 WHERE id=?", (activity_id,)
    )
    conn.commit()
    # Return empty element to remove from overdue list on dashboard,
    # or updated row on client detail
    return HTMLResponse("")


@router.get("/activities", response_class=HTMLResponse)
def activity_list(request: Request, days: int = 90, conn=Depends(get_db)):
    rows = [dict(r) for r in get_activities(conn, days=days)]
    overdue = [dict(r) for r in get_overdue_followups(conn)]
    return templates.TemplateResponse("activities/list.html", {
        "request": request,
        "active": "activities",
        "activities": rows,
        "overdue": overdue,
        "days": days,
    })


@router.get("/renewals", response_class=HTMLResponse)
def renewals(request: Request, window: int = 180, conn=Depends(get_db)):
    rows = get_renewal_pipeline(conn, window_days=window)

    # Attach client_id for linking
    pipeline = []
    for p in rows:
        d = dict(p)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name=?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        pipeline.append(d)

    return templates.TemplateResponse("renewals.html", {
        "request": request,
        "active": "renewals",
        "rows": pipeline,
        "window": window,
        "renewal_statuses": cfg.get("renewal_statuses"),
    })
