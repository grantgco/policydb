"""Meeting notes routes."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates

router = APIRouter()


def _meeting_dict(conn, meeting_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not row:
        return None
    m = dict(row)
    m["attendees"] = [dict(a) for a in conn.execute(
        """SELECT ma.*, co.email, co.phone, co.mobile, co.organization
           FROM meeting_attendees ma
           LEFT JOIN contacts co ON ma.contact_id = co.id
           WHERE ma.meeting_id = ?
           ORDER BY ma.is_internal, ma.name""",
        (meeting_id,),
    ).fetchall()]
    m["action_items"] = [dict(ai) for ai in conn.execute(
        "SELECT * FROM meeting_action_items WHERE meeting_id = ? ORDER BY completed, due_date, id",
        (meeting_id,),
    ).fetchall()]
    m["client_name"] = ""
    client = conn.execute("SELECT name FROM clients WHERE id = ?", (m["client_id"],)).fetchone()
    if client:
        m["client_name"] = client["name"]
    return m


@router.get("/meetings", response_class=HTMLResponse)
def meetings_list(
    request: Request,
    client_id: int = 0,
    conn=Depends(get_db),
):
    today = date.today()
    cutoff_past = (today - timedelta(days=30)).isoformat()
    cutoff_future = (today + timedelta(days=7)).isoformat()

    params: list = []
    where = "1=1"
    if client_id:
        where = "m.client_id = ?"
        params.append(client_id)

    rows = conn.execute(
        f"""SELECT m.*, c.name AS client_name,
                   (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = m.id) AS attendee_count,
                   (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = m.id) AS action_total,
                   (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = m.id AND completed = 1) AS action_done
            FROM client_meetings m
            JOIN clients c ON m.client_id = c.id
            WHERE {where}
              AND m.meeting_date >= ?
            ORDER BY m.meeting_date DESC, m.created_at DESC""",
        params + [cutoff_past],
    ).fetchall()

    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()

    return templates.TemplateResponse("meetings/list.html", {
        "request": request,
        "active": "meetings",
        "meetings": [dict(r) for r in rows],
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": client_id,
    })


@router.get("/meetings/new", response_class=HTMLResponse)
def meeting_new(
    request: Request,
    client_id: int = 0,
    conn=Depends(get_db),
):
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("meetings/detail.html", {
        "request": request,
        "active": "meetings",
        "meeting": None,
        "is_new": True,
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": client_id,
        "today": date.today().isoformat(),
    })


@router.post("/meetings/new")
def meeting_create(
    request: Request,
    client_id: int = Form(...),
    title: str = Form(...),
    meeting_date: str = Form(""),
    meeting_time: str = Form(""),
    duration_hours: str = Form(""),
    location: str = Form(""),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import round_duration

    dur = round_duration(duration_hours)
    cursor = conn.execute(
        """INSERT INTO client_meetings
           (client_id, title, meeting_date, meeting_time, duration_hours, location, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (client_id, title.strip(), meeting_date or date.today().isoformat(),
         meeting_time or None, dur, location or None, notes),
    )
    meeting_id = cursor.lastrowid

    # Create activity_log entry for unified timeline
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, subject, details, duration_hours, account_exec)
           VALUES (?, ?, 'Meeting', ?, ?, ?, ?)""",
        (meeting_date or date.today().isoformat(), client_id, title.strip(),
         f"Meeting logged. {notes[:200] if notes else ''}", dur, account_exec),
    )
    conn.commit()
    return RedirectResponse(f"/meetings/{meeting_id}", status_code=303)


@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_detail(
    request: Request,
    meeting_id: int,
    conn=Depends(get_db),
):
    m = _meeting_dict(conn, meeting_id)
    if not m:
        return HTMLResponse("Meeting not found", status_code=404)

    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()

    contacts = _get_client_contacts(conn, m["client_id"])

    return templates.TemplateResponse("meetings/detail.html", {
        "request": request,
        "active": "meetings",
        "meeting": m,
        "is_new": False,
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": m["client_id"],
        "contacts": contacts,
        "today": date.today().isoformat(),
    })


@router.post("/meetings/{meeting_id}/notes")
def meeting_notes_save(
    meeting_id: int,
    content: str = Form(""),
    conn=Depends(get_db),
):
    """Auto-save meeting notes."""
    from babel.dates import format_datetime as babel_format_datetime
    from datetime import datetime as _dt
    conn.execute(
        "UPDATE client_meetings SET notes = ? WHERE id = ?",
        (content, meeting_id),
    )
    conn.commit()
    saved_at = babel_format_datetime(
        _dt.now(), "MMM d 'at' h:mma", locale="en_US"
    ).replace("AM", "am").replace("PM", "pm")
    return JSONResponse({"ok": True, "saved_at": saved_at})


@router.post("/meetings/{meeting_id}/attendees/add")
def meeting_add_attendee(
    request: Request,
    meeting_id: int,
    name: str = Form(...),
    role: str = Form(""),
    title: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    contact_id: int = Form(0),
    is_internal: int = Form(0),
    create_contact: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.queries import get_or_create_contact, assign_contact_to_client
    from policydb.utils import format_phone, clean_email

    m_row = conn.execute("SELECT client_id FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    client_id = m_row["client_id"] if m_row else None

    # If creating/linking a contact record
    cid = contact_id or None
    if name.strip() and (create_contact == "1" or not cid):
        # Get or create in unified contacts table
        extras = {}
        if email:
            extras["email"] = clean_email(email)
        if phone:
            extras["phone"] = format_phone(phone)
        cid = get_or_create_contact(conn, name.strip(), **extras)
        # Also assign to the client if not already
        if client_id and cid:
            contact_type = "internal" if is_internal else "client"
            try:
                assign_contact_to_client(conn, cid, client_id,
                                         contact_type=contact_type,
                                         title=title or None,
                                         role=role or None)
            except Exception:
                pass  # Already assigned

    conn.execute(
        """INSERT INTO meeting_attendees (meeting_id, contact_id, name, role, is_internal)
           VALUES (?, ?, ?, ?, ?)""",
        (meeting_id, cid, name.strip(), role or title or None, is_internal),
    )
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    contacts = _get_client_contacts(conn, m["client_id"]) if m else []
    return templates.TemplateResponse("meetings/_attendees.html", {
        "request": request, "meeting": m, "contacts": contacts,
    })


def _get_client_contacts(conn, client_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT co.id, co.name, co.email, co.phone, cca.role, cca.title, cca.contact_type
           FROM contact_client_assignments cca
           JOIN contacts co ON cca.contact_id = co.id
           WHERE cca.client_id = ?
           ORDER BY cca.contact_type, co.name""",
        (client_id,),
    ).fetchall()]


@router.patch("/meetings/{meeting_id}/attendees/{attendee_id}")
async def meeting_patch_attendee(
    meeting_id: int,
    attendee_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """PATCH a single field on an attendee (contenteditable cell save)."""
    from policydb.utils import format_phone, clean_email
    import json as _json
    body = _json.loads(await request.body())
    field = body.get("field", "")
    value = body.get("value", "").strip()

    # Fields on meeting_attendees table
    _attendee_fields = {"name", "role"}
    # Fields on the linked contacts table
    _contact_fields = {"email", "phone", "mobile"}

    if field not in _attendee_fields and field not in _contact_fields:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)

    att = conn.execute("SELECT contact_id FROM meeting_attendees WHERE id = ?", (attendee_id,)).fetchone()
    formatted = value

    if field in _attendee_fields:
        conn.execute(
            f"UPDATE meeting_attendees SET {field} = ? WHERE id = ? AND meeting_id = ?",
            (value or None, attendee_id, meeting_id),
        )
        # Propagate name to linked contact
        if field == "name" and value and att and att["contact_id"]:
            conn.execute("UPDATE contacts SET name = ? WHERE id = ?", (value, att["contact_id"]))
    elif field in _contact_fields and att and att["contact_id"]:
        if field == "phone" or field == "mobile":
            formatted = format_phone(value) or value
        elif field == "email":
            formatted = clean_email(value) or value
        conn.execute(
            f"UPDATE contacts SET {field} = ? WHERE id = ?",
            (formatted or None, att["contact_id"]),
        )

    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/meetings/{meeting_id}/attendees/{attendee_id}/remove")
def meeting_remove_attendee(
    request: Request,
    meeting_id: int,
    attendee_id: int,
    conn=Depends(get_db),
):
    conn.execute("DELETE FROM meeting_attendees WHERE id = ? AND meeting_id = ?", (attendee_id, meeting_id))
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    contacts = _get_client_contacts(conn, m["client_id"]) if m else []
    return templates.TemplateResponse("meetings/_attendees.html", {
        "request": request, "meeting": m, "contacts": contacts,
    })


@router.post("/meetings/{meeting_id}/actions/add")
def meeting_add_action(
    request: Request,
    meeting_id: int,
    description: str = Form(...),
    assignee: str = Form(""),
    due_date: str = Form(""),
    conn=Depends(get_db),
):
    m_row = conn.execute("SELECT client_id, title FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    conn.execute(
        """INSERT INTO meeting_action_items (meeting_id, description, assignee, due_date)
           VALUES (?, ?, ?, ?)""",
        (meeting_id, description.strip(), assignee or None, due_date or None),
    )
    action_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Auto-create follow-up if due date set
    if due_date and m_row:
        account_exec = cfg.get("default_account_exec", "Grant")
        cursor = conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
               VALUES (?, ?, 'Meeting Action', ?, ?, ?)""",
            (date.today().isoformat(), m_row["client_id"],
             f"{m_row['title']}: {description.strip()}", due_date, account_exec),
        )
        conn.execute(
            "UPDATE meeting_action_items SET activity_id = ? WHERE id = ?",
            (cursor.lastrowid, action_id),
        )
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    return templates.TemplateResponse("meetings/_actions.html", {
        "request": request, "meeting": m,
    })


@router.post("/meetings/{meeting_id}/actions/{action_id}/toggle")
def meeting_toggle_action(
    request: Request,
    meeting_id: int,
    action_id: int,
    conn=Depends(get_db),
):
    ai = conn.execute("SELECT * FROM meeting_action_items WHERE id = ?", (action_id,)).fetchone()
    if ai:
        new_status = 0 if ai["completed"] else 1
        conn.execute("UPDATE meeting_action_items SET completed = ? WHERE id = ?", (new_status, action_id))
        # Sync linked follow-up
        if ai["activity_id"]:
            conn.execute(
                "UPDATE activity_log SET follow_up_done = ? WHERE id = ?",
                (new_status, ai["activity_id"]),
            )
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    return templates.TemplateResponse("meetings/_actions.html", {
        "request": request, "meeting": m,
    })


@router.post("/meetings/{meeting_id}/log-time")
def meeting_log_time(
    meeting_id: int,
    time_type: str = Form(...),
    hours: str = Form(""),
    conn=Depends(get_db),
):
    """Log prep or debrief time as a separate activity."""
    from policydb.utils import round_duration
    dur = round_duration(hours)
    if not dur:
        return JSONResponse({"ok": False, "error": "No hours"})
    m = conn.execute("SELECT client_id, title FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not m:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    label = "Prep" if time_type == "prep" else "Debrief"
    account_exec = cfg.get("default_account_exec", "Grant")
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, subject, duration_hours, account_exec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), m["client_id"], f"Meeting {label}",
         f"{label}: {m['title']}", dur, account_exec),
    )
    conn.commit()
    return JSONResponse({"ok": True, "logged": f"{dur}h {label.lower()}"})


@router.get("/meetings/{meeting_id}/prep", response_class=HTMLResponse)
def meeting_prep(
    request: Request,
    meeting_id: int,
    conn=Depends(get_db),
):
    """HTMX: load meeting prep panel with client context."""
    m = conn.execute("SELECT client_id FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not m:
        return HTMLResponse("")
    client_id = m["client_id"]
    from policydb.queries import get_renewal_pipeline, get_all_followups, get_activities
    excluded = cfg.get("renewal_statuses_excluded", [])
    pipeline = [dict(r) for r in get_renewal_pipeline(conn, excluded_statuses=excluded, client_ids=[client_id])]
    overdue, upcoming = get_all_followups(conn, window=30, client_ids=[client_id])
    activities = [dict(a) for a in get_activities(conn, client_id=client_id, days=30)][:5]
    risks = [dict(r) for r in conn.execute(
        "SELECT category, description, severity FROM client_risks WHERE client_id = ? AND severity IN ('High', 'Critical') ORDER BY severity",
        (client_id,),
    ).fetchall()]
    bundles = [dict(r) for r in conn.execute(
        "SELECT title, status, rfi_uid FROM client_request_bundles WHERE client_id = ? AND status != 'complete' ORDER BY created_at DESC",
        (client_id,),
    ).fetchall()]
    return templates.TemplateResponse("meetings/_prep_panel.html", {
        "request": request,
        "pipeline": pipeline,
        "overdue": overdue,
        "upcoming": upcoming,
        "activities": activities,
        "risks": risks,
        "bundles": bundles,
    })
