"""Inbox capture queue routes."""

from __future__ import annotations

import logging
logger = logging.getLogger("policydb.web.routes.inbox")

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates

router = APIRouter()


@router.get("/inbox/contacts/search")
def inbox_contact_search(q: str = "", client_id: int = 0, conn=Depends(get_db)):
    """Search contacts for @ autocomplete, optionally filtered by client."""
    if len(q) < 2:
        return JSONResponse([])
    if client_id:
        # Return contacts assigned to this client first, then others
        rows = conn.execute("""
            SELECT co.id, co.name, co.organization,
                   CASE WHEN cca.client_id IS NOT NULL THEN 1 ELSE 0 END AS is_client_contact
            FROM contacts co
            LEFT JOIN contact_client_assignments cca ON cca.contact_id = co.id AND cca.client_id = ?
            WHERE co.name LIKE ?
            ORDER BY is_client_contact DESC, co.name
            LIMIT 15
        """, (client_id, f"%{q}%")).fetchall()
    else:
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
    logger.info("Inbox item created: %s", uid)
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - copied to clipboard"}'
    })


@router.get("/inbox")
def inbox_page():
    """Redirect to Action Center inbox tab."""
    return RedirectResponse("/action-center?tab=inbox", status_code=302)


# ── Scratchpad routes (must be before {inbox_id} routes to avoid path conflict) ──

@router.post("/inbox/scratchpad/clear")
def scratchpad_clear(source: str = Form(...), source_id: str = Form(""), scope_id: str = Form(""), conn=Depends(get_db)):
    """Clear a scratchpad's content."""
    sid = scope_id or source_id
    if source == "dashboard":
        conn.execute("UPDATE user_notes SET content='', updated_at=CURRENT_TIMESTAMP WHERE id=1")
    elif source == "client":
        cid = int(sid.split("/")[-1]) if "/" in sid else int(sid)
        conn.execute("UPDATE client_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE client_id=?", (cid,))
    elif source == "policy":
        uid = sid.split("/")[2] if "/" in sid else sid
        conn.execute("UPDATE policy_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE policy_uid=?", (uid,))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Scratchpad cleared"}'
    })


@router.post("/inbox/scratchpad/process", response_class=HTMLResponse)
def scratchpad_process(
    request: Request,
    source: str = Form(...),
    source_id: str = Form(""), scope_id: str = Form(""),
    client_id: int = Form(0), subject: str = Form(""),
    details: str = Form(""),
    policy_id: int = Form(0),
    activity_type: str = Form("Note"),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Process a scratchpad: create activity + clear."""
    from policydb.utils import round_duration

    # Resolve scope_id (new backported JS sends scope_id directly)
    sid = scope_id or source_id
    resolved_client_id = client_id
    resolved_policy_uid = None
    content_for_note = details or ""

    if source == "client" and not resolved_client_id:
        # Extract client_id from scope_id or source_id
        try:
            resolved_client_id = int(sid.split("/")[-1]) if "/" in sid else int(sid)
        except (ValueError, IndexError):
            resolved_client_id = 0
        # Get scratchpad content if details not provided
        if not content_for_note:
            row = conn.execute("SELECT content FROM client_scratchpad WHERE client_id=?", (resolved_client_id,)).fetchone()
            if row:
                content_for_note = row["content"] or ""
    elif source == "policy" and not resolved_client_id:
        resolved_policy_uid = sid.split("/")[2] if "/" in sid else sid
        row = conn.execute("SELECT client_id FROM policies WHERE policy_uid=?", (resolved_policy_uid,)).fetchone()
        if row:
            resolved_client_id = row["client_id"]
        if not content_for_note:
            row2 = conn.execute("SELECT content FROM policy_scratchpad WHERE policy_uid=?", (resolved_policy_uid,)).fetchone()
            if row2:
                content_for_note = row2["content"] or ""
    elif source == "dashboard" and not content_for_note:
        row = conn.execute("SELECT content FROM user_notes WHERE id=1").fetchone()
        if row:
            content_for_note = row["content"] or ""

    # Resolve policy_id (integer FK) from policy_uid if needed
    resolved_policy_id = policy_id
    if not resolved_policy_id and resolved_policy_uid:
        pid_row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (resolved_policy_uid,)).fetchone()
        if pid_row:
            resolved_policy_id = pid_row["id"]

    # Guard: activity_log.client_id is NOT NULL — dashboard scratchpads have no client
    if not resolved_client_id:
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"ok": False, "error": "No client associated — cannot log as activity"}, status_code=400)
        return HTMLResponse("No client associated with this scratchpad", status_code=400)

    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)

    # 1. Create activity
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), resolved_client_id or None, resolved_policy_id or None, activity_type,
         subject or "Scratchpad note", content_for_note or None,
         follow_up_date or None, account_exec, dur),
    )
    activity_id = cursor.lastrowid
    if follow_up_date and resolved_policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, resolved_policy_id, follow_up_date)

    # 2. Clear the scratchpad
    if source == "dashboard":
        conn.execute("UPDATE user_notes SET content='', updated_at=CURRENT_TIMESTAMP WHERE id=1")
    elif source == "client":
        cid = resolved_client_id
        conn.execute("UPDATE client_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE client_id=?", (cid,))
    elif source == "policy":
        puid = resolved_policy_uid or (sid.split("/")[2] if "/" in sid else sid)
        conn.execute("UPDATE policy_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE policy_uid=?", (puid,))

    conn.commit()

    # Return JSON if requested (Action Center scratchpads use fetch + json)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "activity_id": activity_id})

    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Scratchpad processed - activity created"}'
    })


# ── Inbox item routes (parameterized {inbox_id}) ──

@router.post("/inbox/{inbox_id}/process", response_class=HTMLResponse)
def inbox_process(
    request: Request, inbox_id: int,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    contact_id: int = Form(0),
    issue_id: int = Form(0),
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    activity_date: str = Form(""),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Process inbox item - create activity with contact carryover."""
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    act_date = activity_date or date.today().isoformat()
    # Carry over contact_id from inbox item if not explicitly provided
    if not contact_id:
        inbox_row = conn.execute("SELECT contact_id FROM inbox WHERE id=?", (inbox_id,)).fetchone()
        if inbox_row and inbox_row["contact_id"]:
            contact_id = inbox_row["contact_id"]
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours, contact_id, issue_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (act_date, client_id, policy_id or None, activity_type,
         subject or "Inbox item", details or None,
         follow_up_date or None, account_exec, dur, contact_id or None,
         issue_id or None),
    )
    activity_id = cursor.lastrowid
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
    logger.info("Inbox item %d processed -> activity", inbox_id)
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


@router.get("/inbox/{inbox_id}/issues")
def inbox_client_issues(inbox_id: int, client_id: int = 0, conn=Depends(get_db)):
    """Return open issues for a client (for the process form issue picker)."""
    if not client_id:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT id, issue_uid, subject
        FROM activity_log
        WHERE client_id = ? AND item_kind = 'issue'
          AND (issue_status IS NULL OR issue_status != 'Resolved')
        ORDER BY activity_date DESC
    """, (client_id,)).fetchall()
    return JSONResponse([{"id": r["id"], "uid": r["issue_uid"] or "", "subject": r["subject"] or ""} for r in rows])


def get_inbox_pending_count(conn) -> int:
    """Return count of pending inbox items."""
    try:
        return conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
    except Exception:
        return 0
