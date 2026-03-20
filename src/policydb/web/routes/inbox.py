"""Inbox capture queue routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates

router = APIRouter()


@router.post("/inbox/capture")
def inbox_capture(content: str = Form(...), client_id: int = Form(0), conn=Depends(get_db)):
    """Quick capture - create inbox item, return INB-{id} in toast."""
    conn.execute(
        "INSERT INTO inbox (content, client_id, inbox_uid) VALUES (?, ?, '')",
        (content.strip(), client_id or None),
    )
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    uid = f"INB-{row_id}"
    conn.execute("UPDATE inbox SET inbox_uid = ? WHERE id = ?", (uid, row_id))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - copied to clipboard"}'
    })


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, show_processed: str = "", conn=Depends(get_db)):
    """Inbox page - pending items for processing."""
    pending = [dict(r) for r in conn.execute("""
        SELECT i.*, c.name AS client_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()]
    processed = []
    if show_processed:
        processed = [dict(r) for r in conn.execute("""
            SELECT i.*, c.name AS client_name, a.subject AS activity_subject
            FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
            LEFT JOIN activity_log a ON i.activity_id = a.id
            WHERE i.status = 'processed'
            ORDER BY i.processed_at DESC LIMIT 50
        """).fetchall()]
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    return templates.TemplateResponse("inbox.html", {
        "request": request, "active": "inbox",
        "pending": pending,
        "processed": processed,
        "show_processed": bool(show_processed),
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    })


@router.post("/inbox/{inbox_id}/process", response_class=HTMLResponse)
def inbox_process(
    request: Request, inbox_id: int,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    start_correspondence: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Process inbox item - create activity."""
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         subject or "Inbox item", details or None,
         follow_up_date or None, account_exec, dur),
    )
    activity_id = cursor.lastrowid
    # Start correspondence if requested
    if start_correspondence == "1":
        conn.execute("UPDATE activity_log SET thread_id = ? WHERE id = ?", (activity_id, activity_id))
    # Supersede follow-ups if needed
    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    # Mark inbox item as processed
    conn.execute(
        "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (activity_id, inbox_id),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + (uid["inbox_uid"] if uid else '') + ' processed - activity created"}'
    })


@router.post("/inbox/{inbox_id}/dismiss")
def inbox_dismiss(inbox_id: int, conn=Depends(get_db)):
    """Dismiss inbox item without creating activity."""
    conn.execute(
        "UPDATE inbox SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (inbox_id,),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + (uid["inbox_uid"] if uid else '') + ' dismissed"}'
    })


@router.post("/inbox/{inbox_id}/schedule", response_class=HTMLResponse)
def inbox_schedule(
    inbox_id: int,
    client_id: int = Form(...),
    follow_up_date: str = Form(...),
    subject: str = Form(""),
    conn=Depends(get_db),
):
    """Schedule inbox item as a Task follow-up."""
    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
           VALUES (?, ?, 'Task', ?, ?, ?)""",
        (date.today().isoformat(), client_id, subject or "Inbox item",
         follow_up_date, account_exec),
    )
    activity_id = cursor.lastrowid
    conn.execute(
        "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (activity_id, inbox_id),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + (uid["inbox_uid"] if uid else '') + ' scheduled"}'
    })


@router.get("/inbox/{inbox_id}/policies")
def inbox_client_policies(inbox_id: int, client_id: int = 0, conn=Depends(get_db)):
    """Return policies for a client (for the process form policy picker)."""
    if not client_id:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT id, policy_uid, policy_type, carrier
        FROM policies WHERE client_id = ? AND archived = 0
        ORDER BY policy_type
    """, (client_id,)).fetchall()
    return JSONResponse([{"id": r["id"], "uid": r["policy_uid"], "type": r["policy_type"], "carrier": r["carrier"] or ""} for r in rows])


def get_inbox_pending_count(conn) -> int:
    """Return count of pending inbox items."""
    try:
        return conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
    except Exception:
        return 0
