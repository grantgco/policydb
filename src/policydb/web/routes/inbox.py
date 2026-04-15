"""Inbox capture queue routes."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("policydb.web.routes.inbox")

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.email_sync import _normalize_subject
from policydb.queries import (
    assign_contact_to_client,
    assign_contact_to_policy,
    get_or_create_contact,
)
from policydb.utils import clean_email
from policydb.web.app import get_db, templates

router = APIRouter()


def _upsert_contact_from_email_from(
    conn, email_from: str | None, client_id: int | None = None,
    policy_id: int | None = None,
) -> int | None:
    """Parse an email 'From' header into a unified contact record.

    Accepts 'Name <addr@example.com>' or bare addresses and upserts the contact.
    If client_id or policy_id is provided, attaches the contact to that record
    via the existing junction-table helpers.

    Returns the contact id, or None if the header couldn't be parsed into a name.
    """
    if not email_from:
        return None
    from email.utils import parseaddr

    parsed_name, parsed_email = parseaddr(email_from)
    parsed_email = clean_email(parsed_email) or None
    # Prefer the display name; fall back to the local part of the address.
    name = (parsed_name or "").strip()
    if not name and parsed_email:
        name = parsed_email.split("@")[0].replace(".", " ").replace("_", " ").strip().title()
    if not name:
        return None
    try:
        contact_id = get_or_create_contact(conn, name, email=parsed_email)
    except ValueError:
        return None
    if client_id:
        assign_contact_to_client(
            conn, contact_id, client_id, contact_type="client", is_primary=0,
        )
    if policy_id:
        assign_contact_to_policy(conn, contact_id, policy_id)
    return contact_id


def _find_thread_siblings(conn, inbox_id: int) -> list[dict]:
    """Find pending inbox items in the same email thread as the given item.

    Thread match = same normalized subject. Only returns other pending items,
    excluding the item itself.
    """
    item = conn.execute(
        "SELECT id, email_subject, content FROM inbox WHERE id = ?", (inbox_id,),
    ).fetchone()
    if not item:
        return []

    # Get the subject to normalize
    subject = item["email_subject"] or ""
    if not subject:
        # Parse from content first line for Outlook items
        content = item["content"] or ""
        first_line = content.split("\n")[0] if content else ""
        subject = re.sub(r'^\[Outlook [^\]]*\]\s*', '', first_line)

    norm = _normalize_subject(subject)
    if not norm:
        return []

    # Find all other pending inbox items and check normalized subject match
    candidates = conn.execute(
        """SELECT id, content, email_subject, email_from, email_date, created_at
           FROM inbox
           WHERE status = 'pending' AND id != ?
           ORDER BY created_at DESC""",
        (inbox_id,),
    ).fetchall()

    siblings = []
    for c in candidates:
        c_subject = c["email_subject"] or ""
        if not c_subject:
            c_content = c["content"] or ""
            c_first = c_content.split("\n")[0] if c_content else ""
            c_subject = re.sub(r'^\[Outlook [^\]]*\]\s*', '', c_first)
        if _normalize_subject(c_subject) == norm:
            siblings.append(dict(c))

    return siblings


@router.get("/inbox/clients/search")
def inbox_client_search(q: str = "", conn=Depends(get_db)):
    """Search clients by name for inbox autocomplete."""
    if q:
        rows = conn.execute(
            "SELECT id, name FROM clients WHERE archived=0 AND name LIKE ? ORDER BY name LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name FROM clients WHERE archived=0 ORDER BY name LIMIT 30"
        ).fetchall()
    return JSONResponse([{"id": r["id"], "name": r["name"]} for r in rows])


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
        try:
            cid = int(sid.split("/")[-1]) if "/" in sid else int(sid)
        except (ValueError, IndexError):
            return HTMLResponse("Invalid client ID", status_code=400)
        conn.execute("UPDATE client_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE client_id=?", (cid,))
    elif source == "policy":
        uid = sid.split("/")[2] if sid.count("/") >= 2 else sid
        conn.execute("UPDATE policy_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE policy_uid=?", (uid,))
    elif source == "project":
        try:
            pid = int(sid.split("/")[-1]) if "/" in sid else int(sid)
        except (ValueError, IndexError):
            return HTMLResponse("Invalid project ID", status_code=400)
        conn.execute("UPDATE project_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
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
    resolved_project_id = 0
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
    elif source == "project":
        try:
            resolved_project_id = int(sid.split("/")[-1]) if "/" in sid else int(sid)
        except (ValueError, IndexError):
            resolved_project_id = 0
        if resolved_project_id and not resolved_client_id:
            row = conn.execute("SELECT client_id FROM projects WHERE id=?", (resolved_project_id,)).fetchone()
            if row:
                resolved_client_id = row["client_id"]
        if resolved_project_id and not content_for_note:
            row2 = conn.execute("SELECT content FROM project_scratchpad WHERE project_id=?", (resolved_project_id,)).fetchone()
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
           (activity_date, client_id, policy_id, project_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), resolved_client_id or None, resolved_policy_id or None,
         resolved_project_id or None, activity_type,
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
    elif source == "project" and resolved_project_id:
        conn.execute("UPDATE project_scratchpad SET content='', updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (resolved_project_id,))

    conn.commit()

    # Return JSON if requested (Action Center scratchpads use fetch + json)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "activity_id": activity_id})

    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Scratchpad processed - activity created"}'
    })


# ── Inbox item routes (parameterized {inbox_id}) ──

@router.get("/inbox/{inbox_id}/process-slideover", response_class=HTMLResponse)
def inbox_process_slideover(request: Request, inbox_id: int, conn=Depends(get_db)):
    """Return the process slideover partial for an inbox item."""
    item = conn.execute(
        """SELECT i.*, c.name AS client_name, ct.name AS contact_name
           FROM inbox i
           LEFT JOIN clients c ON i.client_id = c.id
           LEFT JOIN contacts ct ON i.contact_id = ct.id
           WHERE i.id = ?""",
        (inbox_id,),
    ).fetchone()
    if not item:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    is_email = bool(item["outlook_message_id"]) or (item["content"] or "").startswith("[Outlook")
    return templates.TemplateResponse("action_center/_process_inbox_slideover.html", {
        "request": request,
        "item": dict(item),
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "is_email": is_email,
    })


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
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    """Process inbox item - create activity with contact carryover."""
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    act_date = activity_date or date.today().isoformat()
    # Carry over contact_id and email metadata from inbox item
    inbox_row = conn.execute(
        """SELECT contact_id, outlook_message_id, content, email_from, email_to,
                  email_direction
           FROM inbox WHERE id=?""", (inbox_id,),
    ).fetchone()
    if not contact_id and inbox_row and inbox_row["contact_id"]:
        contact_id = inbox_row["contact_id"]
    # If inbox item came from Outlook, carry email body as email_snippet
    email_snippet = None
    outlook_msg_id = None
    email_from = None
    email_to = None
    email_direction = None
    if inbox_row:
        outlook_msg_id = inbox_row["outlook_message_id"]
        email_from = inbox_row["email_from"]
        email_to = inbox_row["email_to"]
        email_direction = inbox_row["email_direction"]
        if outlook_msg_id or (inbox_row["content"] or "").startswith("[Outlook"):
            email_snippet = inbox_row["content"]
    # Touch-once: if the email_from header has a name we don't yet have as a
    # contact, upsert it into the unified contacts table and link it to the
    # client/policy. Preserves any contact_id the user explicitly selected.
    if not contact_id and email_from:
        _new_cid = _upsert_contact_from_email_from(
            conn, email_from, client_id=client_id or None,
            policy_id=policy_id or None,
        )
        if _new_cid:
            contact_id = _new_cid
    # Mark follow-up as done if no follow-up date (log-only)
    follow_up_done = 1 if not follow_up_date else 0
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, follow_up_done, disposition, account_exec,
            duration_hours, contact_id, issue_id,
            email_snippet, outlook_message_id, source, email_from, email_to,
            email_direction)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (act_date, client_id, policy_id or None, activity_type,
         subject or "Inbox item", details or None,
         follow_up_date or None, follow_up_done, disposition or None,
         account_exec, dur, contact_id or None,
         issue_id or None, email_snippet, outlook_msg_id,
         "outlook_sync" if outlook_msg_id else "manual",
         email_from, email_to, email_direction),
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

    # Check for thread siblings to offer batch apply
    siblings = _find_thread_siblings(conn, inbox_id)
    sibling_ids = [s["id"] for s in siblings]
    import json as _json
    trigger_data = {
        "activityLogged": "processed",
        "reviewRowCleared": f"#ac-inbox-item-{inbox_id}",
    }
    if sibling_ids:
        trigger_data["threadSiblings"] = {
            "count": len(sibling_ids),
            "ids": sibling_ids,
            "client_id": client_id,
            "policy_id": policy_id or 0,
            "issue_id": issue_id or 0,
            "contact_id": contact_id or 0,
            "activity_type": activity_type,
            "subject": subject or "Inbox item",
        }
    return HTMLResponse("", headers={
        "HX-Trigger": _json.dumps(trigger_data),
    })


@router.get("/inbox/{inbox_id}/thread-siblings")
def inbox_thread_siblings(inbox_id: int, conn=Depends(get_db)):
    """Return pending inbox items in the same thread as this item."""
    siblings = _find_thread_siblings(conn, inbox_id)
    return JSONResponse([{
        "id": s["id"],
        "subject": s.get("email_subject") or (s.get("content") or "")[:80],
        "from": s.get("email_from") or "",
        "date": s.get("email_date") or s.get("created_at", "")[:10],
    } for s in siblings])


@router.post("/inbox/batch-process")
async def inbox_batch_process(request: Request, conn=Depends(get_db)):
    """Batch process multiple inbox items with the same assignment."""
    from policydb.utils import round_duration
    body = await request.json()
    inbox_ids = body.get("inbox_ids", [])
    client_id = body.get("client_id", 0)
    policy_id = body.get("policy_id", 0) or None
    issue_id = body.get("issue_id", 0) or None
    contact_id = body.get("contact_id", 0) or None
    activity_type = body.get("activity_type", "Email")
    subject_override = body.get("subject", "")

    if not inbox_ids or not client_id:
        return JSONResponse({"ok": False, "error": "Missing inbox_ids or client_id"}, status_code=400)

    account_exec = cfg.get("default_account_exec", "Grant")
    processed = 0

    for iid in inbox_ids:
        inbox_row = conn.execute(
            """SELECT id, content, email_subject, email_date, contact_id,
                      outlook_message_id, email_from, email_to, email_direction
               FROM inbox WHERE id = ? AND status = 'pending'""",
            (iid,),
        ).fetchone()
        if not inbox_row:
            continue

        item_subject = subject_override or inbox_row["email_subject"] or (inbox_row["content"] or "")[:120]
        act_date = (inbox_row["email_date"] or "")[:10] or date.today().isoformat()
        item_contact = contact_id or inbox_row["contact_id"]
        # Touch-once: upsert contact from email_from header when nothing was assigned.
        if not item_contact and inbox_row["email_from"]:
            _new_cid = _upsert_contact_from_email_from(
                conn, inbox_row["email_from"], client_id=client_id or None,
                policy_id=policy_id,
            )
            if _new_cid:
                item_contact = _new_cid
        email_snippet = None
        outlook_msg_id = inbox_row["outlook_message_id"]
        if outlook_msg_id or (inbox_row["content"] or "").startswith("[Outlook"):
            email_snippet = inbox_row["content"]

        cursor = conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, subject, details,
                account_exec, contact_id, issue_id, email_snippet, outlook_message_id,
                source, email_from, email_to, email_direction, follow_up_done, duration_hours)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0.1)""",
            (act_date, client_id, policy_id, activity_type, item_subject,
             f"Batch processed from inbox thread",
             account_exec, item_contact, issue_id, email_snippet,
             outlook_msg_id, "outlook_sync" if outlook_msg_id else "manual",
             inbox_row["email_from"], inbox_row["email_to"], inbox_row["email_direction"]),
        )
        conn.execute(
            "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
            (cursor.lastrowid, iid),
        )
        processed += 1

    conn.commit()
    logger.info("Batch processed %d inbox items", processed)
    return JSONResponse({"ok": True, "processed": processed})


@router.post("/inbox/{inbox_id}/dismiss")
def inbox_dismiss(inbox_id: int, conn=Depends(get_db)):
    """Dismiss inbox item without creating activity.

    For Outlook-sourced items, also record the message_id in
    `dismissed_outlook_messages` so the next sync sweep doesn't re-import it.
    """
    row = conn.execute(
        "SELECT outlook_message_id FROM inbox WHERE id=?", (inbox_id,),
    ).fetchone()
    if row and row["outlook_message_id"]:
        conn.execute(
            "INSERT OR IGNORE INTO dismissed_outlook_messages (message_id) VALUES (?)",
            (row["outlook_message_id"],),
        )
    conn.execute(
        "UPDATE inbox SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (inbox_id,),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    uid_str = (uid["inbox_uid"] or "") if uid else ""
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + uid_str + ' dismissed"}'
    })


# SQLite default SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds, 32766 on
# newer. Stay well below the lower bound to be safe with parameterized IN clauses.
_BULK_CHUNK = 500


@router.post("/inbox/bulk-dismiss")
async def inbox_bulk_dismiss(request: Request, conn=Depends(get_db)):
    """Bulk dismiss inbox items.

    Body JSON:
        {"inbox_ids": [1,2,3]}                  — dismiss exact ids
        {"scope": "outlook_unmatched"}          — dismiss every pending Outlook
                                                  item that has no client_id yet
        {"scope": "outlook_all"}                — dismiss every pending Outlook
                                                  item (matched or not)
        {"scope": "all_pending"}                — dismiss every pending item

    For any Outlook-sourced rows, message_ids are also written to
    `dismissed_outlook_messages` so the next sync doesn't re-import them.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body must be a JSON object"}, status_code=400)

    raw_ids = body.get("inbox_ids") or []
    scope = (body.get("scope") or "").strip()

    # Validate inbox_ids shape — must be a list of integers (or coercible)
    inbox_ids: list[int] = []
    if raw_ids:
        if not isinstance(raw_ids, list):
            return JSONResponse(
                {"ok": False, "error": "inbox_ids must be a list of integers"},
                status_code=400,
            )
        for x in raw_ids:
            try:
                inbox_ids.append(int(x))
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "inbox_ids must contain only integers"},
                    status_code=400,
                )

    if not inbox_ids and not scope:
        return JSONResponse({"ok": False, "error": "Provide inbox_ids or scope"}, status_code=400)

    if scope == "outlook_unmatched":
        rows = conn.execute(
            """SELECT id, outlook_message_id FROM inbox
               WHERE status = 'pending'
                 AND outlook_message_id IS NOT NULL
                 AND (client_id IS NULL OR client_id = 0)"""
        ).fetchall()
    elif scope == "outlook_all":
        rows = conn.execute(
            """SELECT id, outlook_message_id FROM inbox
               WHERE status = 'pending'
                 AND outlook_message_id IS NOT NULL"""
        ).fetchall()
    elif scope == "all_pending":
        rows = conn.execute(
            """SELECT id, outlook_message_id FROM inbox
               WHERE status = 'pending'"""
        ).fetchall()
    elif inbox_ids:
        # Chunk the IN-clause to avoid hitting SQLITE_MAX_VARIABLE_NUMBER on
        # large lists. SELECT-then-aggregate so the bulk-dismiss numbers below
        # operate on a single combined row set.
        rows = []
        for i in range(0, len(inbox_ids), _BULK_CHUNK):
            chunk = inbox_ids[i:i + _BULK_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows.extend(conn.execute(
                f"""SELECT id, outlook_message_id FROM inbox
                    WHERE status = 'pending' AND id IN ({placeholders})""",
                chunk,
            ).fetchall())
    else:
        return JSONResponse({"ok": False, "error": "Unknown scope"}, status_code=400)

    if not rows:
        return JSONResponse({"ok": True, "dismissed": 0})

    target_ids = [r["id"] for r in rows]
    msg_ids = [r["outlook_message_id"] for r in rows if r["outlook_message_id"]]

    # Block re-import on next sync (executemany batches the inserts)
    if msg_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO dismissed_outlook_messages (message_id) VALUES (?)",
            [(m,) for m in msg_ids],
        )

    # Chunk the UPDATE the same way as the SELECT to stay under the parameter
    # limit on huge dismiss operations.
    for i in range(0, len(target_ids), _BULK_CHUNK):
        chunk = target_ids[i:i + _BULK_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(
            f"""UPDATE inbox
                SET status='processed', processed_at=CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})""",
            chunk,
        )
    conn.commit()
    logger.info("Bulk-dismissed %d inbox items (scope=%s)", len(target_ids), scope or "explicit_ids")
    return JSONResponse({"ok": True, "dismissed": len(target_ids)})


@router.post("/inbox/{inbox_id}/schedule", response_class=HTMLResponse)
def inbox_schedule(
    inbox_id: int,
    client_id: int = Form(...),
    follow_up_date: str = Form(...),
    subject: str = Form(""),
    conn=Depends(get_db),
):
    """Schedule inbox item as a Task follow-up."""
    from policydb.queries import create_followup_activity
    activity_id = create_followup_activity(
        conn,
        client_id=client_id,
        policy_id=None,
        issue_id=None,
        subject=subject or "Inbox item",
        activity_type="Task",
        follow_up_date=follow_up_date,
    )
    conn.execute(
        "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (activity_id, inbox_id),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    uid_str = (uid["inbox_uid"] or "") if uid else ""
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + uid_str + ' scheduled"}'
    })


@router.get("/inbox/{inbox_id}/policies")
def inbox_client_policies(inbox_id: int, client_id: int = 0, q: str = "", conn=Depends(get_db)):
    """Return policies for a client, optionally filtered by search query."""
    if not client_id:
        return JSONResponse([])
    if q:
        rows = conn.execute("""
            SELECT id, policy_uid, policy_type, carrier
            FROM policies WHERE client_id = ? AND archived = 0
              AND (policy_type LIKE ? OR carrier LIKE ? OR policy_uid LIKE ?)
            ORDER BY policy_type LIMIT 20
        """, (client_id, f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, policy_uid, policy_type, carrier
            FROM policies WHERE client_id = ? AND archived = 0
            ORDER BY policy_type
        """, (client_id,)).fetchall()
    return JSONResponse([{"id": r["id"], "uid": r["policy_uid"], "type": r["policy_type"], "carrier": r["carrier"] or ""} for r in rows])


@router.get("/inbox/{inbox_id}/issues")
def inbox_client_issues(inbox_id: int, client_id: int = 0, q: str = "", conn=Depends(get_db)):
    """Return open + recently resolved issues for a client."""
    if not client_id:
        return JSONResponse([])
    # Include open issues + resolved within last 30 days (for catching up on correspondence)
    status_filter = """AND (issue_status IS NULL OR issue_status != 'Resolved'
                        OR (issue_status = 'Resolved' AND activity_date >= date('now', '-30 days')))"""
    if q:
        rows = conn.execute(f"""
            SELECT id, issue_uid, subject, issue_status
            FROM activity_log
            WHERE client_id = ? AND item_kind = 'issue'
              AND merged_into_id IS NULL
              {status_filter}
              AND (subject LIKE ? OR issue_uid LIKE ?)
            ORDER BY activity_date DESC LIMIT 20
        """, (client_id, f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT id, issue_uid, subject, issue_status
            FROM activity_log
            WHERE client_id = ? AND item_kind = 'issue'
              AND merged_into_id IS NULL
              {status_filter}
            ORDER BY activity_date DESC
        """, (client_id,)).fetchall()
    return JSONResponse([{
        "id": r["id"], "uid": r["issue_uid"] or "", "subject": r["subject"] or "",
        "resolved": r["issue_status"] == "Resolved",
    } for r in rows])


def get_inbox_pending_count(conn) -> int:
    """Return count of pending inbox items."""
    try:
        return conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
    except Exception:
        return 0
