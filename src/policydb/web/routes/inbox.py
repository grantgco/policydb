"""Inbox capture queue routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates

router = APIRouter()


@router.get("/inbox/contacts/search")
def inbox_contact_search(q: str = "", conn=Depends(get_db)):
    """Search contacts for @ autocomplete."""
    if len(q) < 2:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT id, name, organization FROM contacts
        WHERE name LIKE ? ORDER BY name LIMIT 15
    """, (f"%{q}%",)).fetchall()
    return JSONResponse([{"id": r["id"], "name": r["name"], "org": r["organization"] or ""} for r in rows])


@router.post("/inbox/capture")
def inbox_capture(content: str = Form(...), client_id: int = Form(0), contact_id: int = Form(0), conn=Depends(get_db)):
    """Quick capture - create inbox item, return INB-{id} in toast."""
    conn.execute(
        "INSERT INTO inbox (content, client_id, contact_id, inbox_uid) VALUES (?, ?, ?, '')",
        (content.strip(), client_id or None, contact_id or None),
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
        SELECT i.*, c.name AS client_name, ct.name AS contact_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN contacts ct ON i.contact_id = ct.id
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
    # Aggregate non-empty scratchpads from all sources
    scratchpads = []
    # Dashboard scratchpad
    dash_note = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()
    if dash_note and (dash_note["content"] or "").strip():
        scratchpads.append({"source": "dashboard", "label": "Dashboard", "link": "/",
                            "content": dash_note["content"], "updated_at": dash_note["updated_at"]})
    # Client scratchpads
    for cs in conn.execute("""
        SELECT cs.client_id, cs.content, cs.updated_at, c.name AS client_name
        FROM client_scratchpad cs JOIN clients c ON cs.client_id = c.id
        WHERE cs.content IS NOT NULL AND cs.content != ''
    """).fetchall():
        scratchpads.append({"source": "client", "label": cs["client_name"], "link": f"/clients/{cs['client_id']}",
                            "content": cs["content"], "updated_at": cs["updated_at"],
                            "client_id": cs["client_id"]})
    # Policy scratchpads
    for ps in conn.execute("""
        SELECT ps.policy_uid, ps.content, ps.updated_at, p.policy_type, p.id AS policy_id,
               p.client_id, c.name AS client_name
        FROM policy_scratchpad ps JOIN policies p ON ps.policy_uid = p.policy_uid
        JOIN clients c ON p.client_id = c.id
        WHERE ps.content IS NOT NULL AND ps.content != ''
    """).fetchall():
        scratchpads.append({"source": "policy", "label": f"{ps['client_name']} — {ps['policy_type']}",
                            "link": f"/policies/{ps['policy_uid']}/edit",
                            "content": ps["content"], "updated_at": ps["updated_at"],
                            "policy_id": ps["policy_id"], "client_id": ps["client_id"]})
    return templates.TemplateResponse("inbox.html", {
        "request": request, "active": "inbox",
        "pending": pending,
        "processed": processed,
        "show_processed": bool(show_processed),
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
        "cor_auto_triggers": cfg.get("cor_auto_triggers", []),
        "scratchpads": scratchpads,
    })


# ── Scratchpad routes (must be before {inbox_id} routes to avoid path conflict) ──

@router.post("/inbox/scratchpad/clear")
def scratchpad_clear(source: str = Form(...), source_id: str = Form(...), conn=Depends(get_db)):
    """Clear a scratchpad's content."""
    if source == "dashboard":
        conn.execute("UPDATE user_notes SET content='', updated_at=CURRENT_TIMESTAMP WHERE id=1")
    elif source == "client":
        cid = source_id.split("/")[-1]
        conn.execute("UPDATE client_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE client_id=?", (int(cid),))
    elif source == "policy":
        uid = source_id.split("/")[2]
        conn.execute("UPDATE policy_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE policy_uid=?", (uid,))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Scratchpad cleared"}'
    })


@router.post("/inbox/scratchpad/process", response_class=HTMLResponse)
def scratchpad_process(
    source: str = Form(...), source_id: str = Form(...),
    client_id: int = Form(...), subject: str = Form(""),
    details: str = Form(""),
    policy_id: int = Form(0),
    activity_type: str = Form("Note"),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    start_correspondence: str = Form(""),
    conn=Depends(get_db),
):
    """Process a scratchpad into an activity and clear it."""
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         subject or "Scratchpad note", details or None,
         follow_up_date or None, account_exec, dur),
    )
    activity_id = cursor.lastrowid
    if start_correspondence == "1":
        conn.execute("UPDATE activity_log SET thread_id = ? WHERE id = ?", (activity_id, activity_id))
    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    conn.commit()
    # Clear the scratchpad
    if source == "dashboard":
        conn.execute("UPDATE user_notes SET content='', updated_at=CURRENT_TIMESTAMP WHERE id=1")
    elif source == "client":
        cid = source_id.split("/")[-1]
        conn.execute("UPDATE client_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE client_id=?", (int(cid),))
    elif source == "policy":
        uid = source_id.split("/")[2]
        conn.execute("UPDATE policy_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE policy_uid=?", (uid,))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Scratchpad processed - activity created"}'
    })


# ── Inbox item routes (parameterized {inbox_id}) ──

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
