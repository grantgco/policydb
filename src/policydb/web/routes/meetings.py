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
           ORDER BY ma.attendee_type, ma.name""",
        (meeting_id,),
    ).fetchall()]
    m["action_items"] = [dict(ai) for ai in conn.execute(
        "SELECT * FROM meeting_action_items WHERE meeting_id = ? ORDER BY completed, due_date, id",
        (meeting_id,),
    ).fetchall()]
    m["linked_policies"] = [dict(p) for p in conn.execute(
        """SELECT mp.policy_uid, p.policy_type, p.carrier
           FROM meeting_policies mp
           LEFT JOIN policies p ON mp.policy_uid = p.policy_uid
           WHERE mp.meeting_id = ?
           ORDER BY p.policy_type""",
        (meeting_id,),
    ).fetchall()]
    m["decisions"] = [dict(d) for d in conn.execute(
        "SELECT * FROM meeting_decisions WHERE meeting_id = ? ORDER BY created_at",
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
    today = date.today().isoformat()

    params_up: list = []
    params_past: list = []
    where_up = "1=1"
    where_past = "1=1"
    if client_id:
        where_up = "m.client_id = ?"
        where_past = "m.client_id = ?"
        params_up.append(client_id)
        params_past.append(client_id)

    _agg_cols = """m.*, c.name AS client_name,
                   (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = m.id) AS attendee_count,
                   (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = m.id) AS action_total,
                   (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = m.id AND completed = 1) AS action_done"""

    upcoming_rows = conn.execute(
        f"""SELECT {_agg_cols}
            FROM client_meetings m
            JOIN clients c ON m.client_id = c.id
            WHERE {where_up}
              AND m.meeting_date >= date('now')
            ORDER BY m.meeting_date ASC, m.meeting_time ASC
            LIMIT 6""",
        params_up,
    ).fetchall()

    past_rows = conn.execute(
        f"""SELECT {_agg_cols}
            FROM client_meetings m
            JOIN clients c ON m.client_id = c.id
            WHERE {where_past}
              AND m.meeting_date < date('now')
            ORDER BY m.meeting_date DESC, m.created_at DESC
            LIMIT 50""",
        params_past,
    ).fetchall()

    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()

    return templates.TemplateResponse("meetings/list_enhanced.html", {
        "request": request,
        "active": "meetings",
        "upcoming": [dict(r) for r in upcoming_rows],
        "past": [dict(r) for r in past_rows],
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": client_id,
        "meeting_types": cfg.get("meeting_types", []),
    })


@router.get("/meetings/export.csv")
def meetings_export_csv(
    client_id: int = 0,
    conn=Depends(get_db),
):
    from policydb.utils import csv_response
    params: list = []
    where = "1=1"
    if client_id:
        where = "m.client_id = ?"
        params.append(client_id)
    rows = [dict(r) for r in conn.execute(
        f"""SELECT m.meeting_uid, m.meeting_date, m.meeting_time, c.name AS client_name, m.title,
                   m.duration_hours, m.location,
                   (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = m.id) AS attendees,
                   (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = m.id) AS action_items
            FROM client_meetings m JOIN clients c ON m.client_id = c.id
            WHERE {where} ORDER BY m.meeting_date DESC""",
        params,
    ).fetchall()]
    cols = ["meeting_uid", "meeting_date", "meeting_time", "client_name", "title", "duration_hours",
            "location", "attendees", "action_items"]
    from datetime import date as _d
    fname = f"meetings_{_d.today().isoformat()}.csv"
    return csv_response(rows, fname, cols)


@router.get("/meetings/{meeting_id}/export.csv")
def meeting_detail_export_csv(
    meeting_id: int,
    conn=Depends(get_db),
):
    """Export a single meeting's attendees + action items as CSV."""
    from policydb.utils import csv_response
    m = _meeting_dict(conn, meeting_id)
    if not m:
        from fastapi.responses import Response
        return Response("Not found", status_code=404)
    # Combine attendees and action items into one export
    rows = []
    for a in m.get("attendees", []):
        rows.append({"section": "Attendee", "name": a["name"], "role": a.get("role", ""),
                      "detail": a.get("email", ""), "status": "Internal" if a.get("is_internal") else "Client"})
    for ai in m.get("action_items", []):
        rows.append({"section": "Action Item", "name": ai["description"], "role": ai.get("assignee", ""),
                      "detail": ai.get("due_date", ""), "status": "Done" if ai.get("completed") else "Open"})
    cols = ["section", "name", "role", "detail", "status"]
    safe = m["title"].replace(" ", "_")[:30]
    return csv_response(rows, f"meeting_{safe}.csv", cols)


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
    meeting_type: str = Form(""),
    conn=Depends(get_db),
):
    from policydb.utils import round_duration
    from policydb.db import next_meeting_uid

    dur = round_duration(duration_hours)
    meeting_uid = next_meeting_uid(conn, client_id)
    cursor = conn.execute(
        """INSERT INTO client_meetings
           (client_id, title, meeting_date, meeting_time, duration_hours, location, notes, meeting_uid,
            meeting_type, phase)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'before')""",
        (client_id, title.strip(), meeting_date or date.today().isoformat(),
         meeting_time or None, dur, location or None, notes, meeting_uid,
         meeting_type or None),
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
    return RedirectResponse(f"/meetings/{meeting_id}?created=1", status_code=303)


@router.post("/meetings/{meeting_id}/start")
def start_meeting(meeting_id: int, conn=Depends(get_db)):
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    conn.execute("UPDATE client_meetings SET phase = 'during', start_time = ? WHERE id = ?", (now, meeting_id))
    conn.commit()
    return RedirectResponse(f"/meetings/{meeting_id}", status_code=303)


@router.post("/meetings/{meeting_id}/end")
def end_meeting(meeting_id: int, conn=Depends(get_db)):
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    conn.execute("UPDATE client_meetings SET phase = 'after', end_time = ? WHERE id = ?", (now, meeting_id))
    conn.commit()
    return RedirectResponse(f"/meetings/{meeting_id}", status_code=303)


@router.post("/meetings/{meeting_id}/complete")
def complete_meeting(meeting_id: int, conn=Depends(get_db)):
    conn.execute("UPDATE client_meetings SET phase = 'complete' WHERE id = ?", (meeting_id,))
    m = dict(conn.execute("SELECT * FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone())
    if m.get("start_time") and m.get("end_time"):
        from policydb.utils import round_duration
        start_parts = m["start_time"].split(":")
        end_parts = m["end_time"].split(":")
        start_mins = int(start_parts[0]) * 60 + int(start_parts[1])
        end_mins = int(end_parts[0]) * 60 + int(end_parts[1])
        if end_mins > start_mins:
            dur = round_duration(str((end_mins - start_mins) / 60))
            conn.execute("UPDATE client_meetings SET duration_hours = ? WHERE id = ?", (dur, meeting_id))
            conn.execute(
                "UPDATE activity_log SET duration_hours = ? WHERE client_id = ? AND activity_type = 'Meeting' AND subject = ?",
                (dur, m["client_id"], m["title"]),
            )
    conn.commit()
    return RedirectResponse(f"/meetings/{meeting_id}", status_code=303)


@router.get("/meetings/{meeting_id}/prep-briefing", response_class=HTMLResponse)
def prep_briefing(
    request: Request,
    meeting_id: int,
    compact: int = 0,
    conn=Depends(get_db),
):
    """Auto-generated prep briefing with all client data."""
    m = _meeting_dict(conn, meeting_id)
    if not m:
        return HTMLResponse("Meeting not found", status_code=404)
    client_id = m["client_id"]
    today = date.today().isoformat()

    # Renewal status summary
    renewals = [dict(r) for r in conn.execute(
        "SELECT * FROM v_renewal_pipeline WHERE client_id = ?", (client_id,)
    ).fetchall()]

    # Outstanding: overdue follow-ups
    overdue_followups = [dict(r) for r in conn.execute(
        "SELECT * FROM v_overdue_followups WHERE client_id = ?", (client_id,)
    ).fetchall()]

    # Incomplete milestones
    incomplete_milestones = [dict(r) for r in conn.execute(
        """SELECT pm.id, pm.policy_uid, pm.milestone AS description, pm.completed,
                  p.policy_type
           FROM policy_milestones pm
           JOIN policies p ON p.policy_uid = pm.policy_uid
           WHERE p.client_id = ? AND pm.completed = 0""",
        (client_id,),
    ).fetchall()]

    # Open action items from PREVIOUS meetings (not this one)
    prev_actions = [dict(r) for r in conn.execute(
        """SELECT mai.*, cm.title as meeting_title
           FROM meeting_action_items mai
           JOIN client_meetings cm ON cm.id = mai.meeting_id
           WHERE cm.client_id = ? AND mai.completed = 0 AND cm.id != ?
           ORDER BY mai.due_date""",
        (client_id, meeting_id),
    ).fetchall()]

    # Schedule of insurance — query policies directly since v_schedule has no client_id
    schedule = [dict(r) for r in conn.execute(
        """SELECT policy_type, carrier, policy_number, effective_date, expiration_date,
                  premium, limit_amount, deductible, project_name
           FROM policies
           WHERE client_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
           ORDER BY policy_type, layer_position""",
        (client_id,),
    ).fetchall()]

    # Recent activity (30 days)
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    recent_activity = [dict(r) for r in conn.execute(
        """SELECT al.activity_date, al.activity_type, al.subject, al.duration_hours
           FROM activity_log al
           WHERE al.client_id = ? AND al.activity_date >= ?
           ORDER BY al.activity_date DESC LIMIT 10""",
        (client_id, thirty_days_ago),
    ).fetchall()]

    # Client summary / account pulse
    client_summary = conn.execute(
        "SELECT * FROM v_client_summary WHERE id = ?", (client_id,)
    ).fetchone()
    client_summary = dict(client_summary) if client_summary else {}

    from policydb.queries import get_client_total_hours
    total_hours = get_client_total_hours(conn, client_id)

    template = "meetings/_prep_briefing.html"
    return templates.TemplateResponse(template, {
        "request": request,
        "meeting": m,
        "compact": bool(compact),
        "attendees": m.get("attendees", []),
        "renewals": renewals,
        "overdue_followups": overdue_followups,
        "incomplete_milestones": incomplete_milestones,
        "prev_actions": prev_actions,
        "schedule": schedule,
        "recent_activity": recent_activity,
        "client_summary": client_summary,
        "total_hours": total_hours,
    })


@router.post("/meetings/{meeting_id}/agenda")
async def save_agenda(
    meeting_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Save meeting agenda / talking points."""
    form = await request.form()
    agenda = form.get("agenda", "")
    conn.execute("UPDATE client_meetings SET agenda = ? WHERE id = ?", (agenda, meeting_id))
    conn.commit()
    return JSONResponse({"ok": True})


@router.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_detail(
    request: Request,
    meeting_id: int,
    created: int = 0,
    conn=Depends(get_db),
):
    m = _meeting_dict(conn, meeting_id)
    if not m:
        return HTMLResponse("Meeting not found", status_code=404)

    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()

    contacts = _get_client_contacts(conn, m["client_id"])
    client_policies = [dict(r) for r in conn.execute(
        """SELECT policy_uid, policy_type, carrier, project_name,
                  CASE WHEN is_program = 1 THEN 'Program' ELSE '' END AS program_label
           FROM policies
           WHERE client_id = ? AND archived = 0 ORDER BY project_name, policy_type""",
        (m["client_id"],),
    ).fetchall()]

    # For the Before phase Account Pulse sidebar — load eagerly so no spinner for right column
    client_summary = {}
    total_hours = 0.0
    if (m.get("phase") or "before") == "before":
        cs = conn.execute(
            "SELECT * FROM v_client_summary WHERE id = ?", (m["client_id"],)
        ).fetchone()
        client_summary = dict(cs) if cs else {}
        from policydb.queries import get_client_total_hours
        total_hours = get_client_total_hours(conn, m["client_id"])

    return templates.TemplateResponse("meetings/detail_phased.html", {
        "request": request,
        "active": "meetings",
        "meeting": m,
        "is_new": False,
        "all_clients": [dict(c) for c in all_clients],
        "selected_client_id": m["client_id"],
        "contacts": contacts,
        "client_policies": client_policies,
        "today": date.today().isoformat(),
        "just_created": bool(created),
        "meeting_types": cfg.get("meeting_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "client_summary": client_summary,
        "total_hours": total_hours,
    })


@router.patch("/meetings/{meeting_id}")
async def meeting_patch(
    meeting_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """PATCH a single field on the meeting record."""
    import json as _json
    body = _json.loads(await request.body())
    field = body.get("field", "")
    value = body.get("value", "").strip()
    allowed = {"title", "meeting_date", "meeting_time", "duration_hours", "location", "meeting_type"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    if field == "duration_hours":
        from policydb.utils import round_duration
        value = str(round_duration(value) or "")
    conn.execute(
        f"UPDATE client_meetings SET {field} = ? WHERE id = ?",
        (value or None, meeting_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


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
    attendee_type: str = Form(""),
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
            contact_type = attendee_type.lower() if attendee_type else "client"
            try:
                assign_contact_to_client(conn, cid, client_id,
                                         contact_type=contact_type,
                                         title=title or None,
                                         role=role or None)
            except Exception:
                pass  # Already assigned

    conn.execute(
        """INSERT INTO meeting_attendees (meeting_id, contact_id, name, role, attendee_type)
           VALUES (?, ?, ?, ?, ?)""",
        (meeting_id, cid, name.strip(), role or title or None, attendee_type or ""),
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


@router.post("/meetings/{meeting_id}/attendees/add-row", response_class=HTMLResponse)
def meeting_attendee_add_row(
    request: Request,
    meeting_id: int,
    conn=Depends(get_db),
):
    """Create a blank attendee row + contact record, return matrix row HTML."""
    from policydb.queries import get_or_create_contact
    m = conn.execute("SELECT client_id FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not m:
        return HTMLResponse("")
    cid = get_or_create_contact(conn, "New Contact")
    cur = conn.execute(
        "INSERT INTO meeting_attendees (meeting_id, contact_id, name, attendee_type) VALUES (?, ?, 'New Contact', '')",
        (meeting_id, cid),
    )
    conn.commit()
    a = {"id": cur.lastrowid, "contact_id": cid, "name": "New Contact",
         "role": None, "attendee_type": "", "email": None, "phone": None, "mobile": None}
    return templates.TemplateResponse("meetings/_attendee_row.html", {
        "request": request, "a": a, "meeting": {"id": meeting_id},
    })


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
    _attendee_fields = {"name", "role", "attendee_type"}
    # Fields on the linked contacts table
    _contact_fields = {"email", "phone", "mobile"}

    if field not in _attendee_fields and field not in _contact_fields:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)

    att = conn.execute("SELECT contact_id FROM meeting_attendees WHERE id = ?", (attendee_id,)).fetchone()
    formatted = value

    if field in _attendee_fields:
        save_val = value or None
        if field == "attendee_type":
            save_val = value.strip() or ""
            formatted = save_val
        conn.execute(
            f"UPDATE meeting_attendees SET {field} = ? WHERE id = ? AND meeting_id = ?",
            (save_val, attendee_id, meeting_id),
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


@router.post("/meetings/{meeting_id}/actions/add-row", response_class=HTMLResponse)
def meeting_action_add_row(
    request: Request,
    meeting_id: int,
    conn=Depends(get_db),
):
    """Create a blank action item row."""
    cur = conn.execute(
        "INSERT INTO meeting_action_items (meeting_id, description) VALUES (?, '')",
        (meeting_id,),
    )
    conn.commit()
    ai = {"id": cur.lastrowid, "description": "", "assignee": None, "due_date": None, "completed": 0, "activity_id": None}
    return templates.TemplateResponse("meetings/_action_row.html", {
        "request": request, "ai": ai, "meeting": {"id": meeting_id},
        "today": date.today().isoformat(),
    })


@router.patch("/meetings/{meeting_id}/actions/{action_id}")
async def meeting_patch_action(
    meeting_id: int,
    action_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """PATCH a single field on an action item."""
    import json as _json
    body = _json.loads(await request.body())
    field = body.get("field", "")
    value = body.get("value", "").strip()
    allowed = {"description", "assignee", "due_date"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    conn.execute(
        f"UPDATE meeting_action_items SET {field} = ? WHERE id = ? AND meeting_id = ?",
        (value or None, action_id, meeting_id),
    )
    # If due_date changed and there's a linked follow-up, update it too
    if field == "due_date":
        ai = conn.execute("SELECT activity_id FROM meeting_action_items WHERE id = ?", (action_id,)).fetchone()
        if ai and ai["activity_id"]:
            conn.execute("UPDATE activity_log SET follow_up_date = ? WHERE id = ?", (value or None, ai["activity_id"]))
        # Auto-create follow-up if due_date set and no linked activity yet
        elif value and ai and not ai["activity_id"]:
            m = conn.execute("SELECT client_id, title FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
            desc_row = conn.execute("SELECT description FROM meeting_action_items WHERE id = ?", (action_id,)).fetchone()
            if m and desc_row:
                account_exec = cfg.get("default_account_exec", "Grant")
                cursor = conn.execute(
                    """INSERT INTO activity_log
                       (activity_date, client_id, activity_type, subject, follow_up_date, account_exec)
                       VALUES (?, ?, 'Meeting Action', ?, ?, ?)""",
                    (date.today().isoformat(), m["client_id"],
                     f"{m['title']}: {desc_row['description'] or 'Action item'}", value, account_exec),
                )
                conn.execute("UPDATE meeting_action_items SET activity_id = ? WHERE id = ?", (cursor.lastrowid, action_id))
                conn.commit()
                return JSONResponse({"ok": True, "formatted": value, "activity_logged": "Follow-up created for action item"})
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


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
    _created_followup = bool(due_date and m_row)
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    resp = templates.TemplateResponse("meetings/_actions.html", {
        "request": request, "meeting": m, "today": date.today().isoformat(),
    })
    if _created_followup:
        resp.headers["HX-Trigger"] = '{"activityLogged": "Follow-up created for action item"}'
    return resp


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
    resp = templates.TemplateResponse("meetings/_actions.html", {
        "request": request, "meeting": m, "today": date.today().isoformat(),
    })
    if ai and ai["activity_id"]:
        label = "Action completed — follow-up marked done" if new_status else "Action reopened — follow-up restored"
        resp.headers["HX-Trigger"] = '{"activityLogged": "' + label + '"}'
    return resp


@router.post("/meetings/{meeting_id}/actions/{action_id}/track")
def action_toggle_track(
    meeting_id: int,
    action_id: int,
    conn=Depends(get_db),
):
    """Toggle follow-up tracking for an action item."""
    action = conn.execute(
        "SELECT * FROM meeting_action_items WHERE id = ? AND meeting_id = ?",
        (action_id, meeting_id),
    ).fetchone()
    if not action:
        return JSONResponse({"ok": False}, status_code=404)

    meeting = conn.execute("SELECT * FROM client_meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not meeting:
        return JSONResponse({"ok": False}, status_code=404)

    if action["activity_id"]:
        # Untrack: mark the linked activity as done and unlink
        conn.execute("UPDATE activity_log SET follow_up_done = 1 WHERE id = ?", (action["activity_id"],))
        conn.execute("UPDATE meeting_action_items SET activity_id = NULL WHERE id = ?", (action_id,))
        conn.commit()
        return JSONResponse({"ok": True, "tracked": False})
    else:
        # Track: create a follow-up activity
        account_exec = cfg.get("default_account_exec", "Grant")
        due = action["due_date"] or (date.today() + timedelta(days=7)).isoformat()
        cursor = conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, activity_type, subject, contact_person,
                follow_up_date, account_exec)
               VALUES (?, ?, 'Meeting Action', ?, ?, ?, ?)""",
            (
                date.today().isoformat(),
                meeting["client_id"],
                action["description"] or "Meeting action item",
                action["assignee"] or None,
                due,
                account_exec,
            ),
        )
        new_aid = cursor.lastrowid
        conn.execute("UPDATE meeting_action_items SET activity_id = ? WHERE id = ?", (new_aid, action_id))
        conn.commit()
        return JSONResponse({"ok": True, "tracked": True, "activity_id": new_aid})


@router.post("/meetings/{meeting_id}/actions/{action_id}/delete")
def meeting_delete_action(
    request: Request,
    meeting_id: int,
    action_id: int,
    conn=Depends(get_db),
):
    # Clear linked follow-up
    ai = conn.execute("SELECT activity_id FROM meeting_action_items WHERE id = ?", (action_id,)).fetchone()
    if ai and ai["activity_id"]:
        conn.execute("UPDATE activity_log SET follow_up_done = 1 WHERE id = ?", (ai["activity_id"],))
    conn.execute("DELETE FROM meeting_action_items WHERE id = ? AND meeting_id = ?", (action_id, meeting_id))
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    return templates.TemplateResponse("meetings/_actions.html", {
        "request": request, "meeting": m, "today": date.today().isoformat(),
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
    return JSONResponse({"ok": True, "logged": f"{dur}h {label.lower()}", "activity_logged": True})


@router.post("/meetings/{meeting_id}/policies/link")
def meeting_link_policy(
    meeting_id: int,
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    if not policy_uid.strip():
        return JSONResponse({"ok": False})
    try:
        conn.execute(
            "INSERT OR IGNORE INTO meeting_policies (meeting_id, policy_uid) VALUES (?, ?)",
            (meeting_id, policy_uid.strip().upper()),
        )
        conn.commit()
    except Exception:
        pass
    m = _meeting_dict(conn, meeting_id)
    policies = m.get("linked_policies", [])
    html = ""
    for p in policies:
        html += (f'<span class="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded mr-1 mb-1">'
                 f'<a href="/policies/{p["policy_uid"]}/edit" class="hover:underline" target="_blank">{p.get("policy_type") or p["policy_uid"]}</a>'
                 f' <span class="text-blue-400">{p.get("carrier") or ""}</span>'
                 f'<button type="button" hx-post="/meetings/{meeting_id}/policies/unlink" '
                 f'hx-vals=\'{{\"policy_uid\": \"{p["policy_uid"]}\"}}\' '
                 f'hx-target="#meeting-policies" hx-swap="innerHTML" '
                 f'class="text-blue-300 hover:text-red-500 ml-0.5">&times;</button></span>')
    return HTMLResponse(html or '<span class="text-xs text-gray-400 italic">No policies linked</span>')


@router.post("/meetings/{meeting_id}/policies/unlink")
def meeting_unlink_policy(
    meeting_id: int,
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    conn.execute(
        "DELETE FROM meeting_policies WHERE meeting_id = ? AND policy_uid = ?",
        (meeting_id, policy_uid.strip().upper()),
    )
    conn.commit()
    m = _meeting_dict(conn, meeting_id)
    policies = m.get("linked_policies", [])
    html = ""
    for p in policies:
        html += (f'<span class="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded mr-1 mb-1">'
                 f'<a href="/policies/{p["policy_uid"]}/edit" class="hover:underline" target="_blank">{p.get("policy_type") or p["policy_uid"]}</a>'
                 f' <span class="text-blue-400">{p.get("carrier") or ""}</span>'
                 f'<button type="button" hx-post="/meetings/{meeting_id}/policies/unlink" '
                 f'hx-vals=\'{{\"policy_uid\": \"{p["policy_uid"]}\"}}\' '
                 f'hx-target="#meeting-policies" hx-swap="innerHTML" '
                 f'class="text-blue-300 hover:text-red-500 ml-0.5">&times;</button></span>')
    return HTMLResponse(html or '<span class="text-xs text-gray-400 italic">No policies linked</span>')


@router.get("/meetings/{meeting_id}/recap", response_class=HTMLResponse)
def meeting_recap(
    meeting_id: int,
    conn=Depends(get_db),
):
    """Generate a meeting recap text for copy/email."""
    import html as _html

    m = _meeting_dict(conn, meeting_id)
    if not m:
        return HTMLResponse("")

    uid_line = f"**Ref:** {m.get('meeting_uid', '')}" if m.get("meeting_uid") else ""
    lines = [
        f"# Meeting Recap: {m['title']}",
        "",
        f"**Client:** {m.get('client_name', '')}",
        f"**Date:** {m.get('meeting_date', '')} {m.get('meeting_time', '') or ''}".rstrip(),
    ]
    if uid_line:
        lines.append(uid_line)
    lines.append("")

    if m.get("attendees"):
        lines.append("## Attendees")
        for a in m["attendees"]:
            parts = [a["name"]]
            if a.get("role"):
                parts.append(f"({a['role']})")
            if a.get("attendee_type"):
                parts.append(f"— {a['attendee_type']}")
            lines.append(f"- {' '.join(parts)}")
        lines.append("")
    if m.get("linked_policies"):
        lines.append("## Policies Discussed")
        for p in m["linked_policies"]:
            lines.append(f"- {p.get('policy_type', p['policy_uid'])} · {p.get('carrier', '')}")
        lines.append("")
    if m.get("notes"):
        lines.append("## Notes")
        lines.append(m["notes"])
        lines.append("")
    if m.get("action_items"):
        lines.append("## Action Items")
        for ai in m["action_items"]:
            check = "[x]" if ai.get("completed") else "[ ]"
            assignee = f" ({ai['assignee']})" if ai.get("assignee") else ""
            due = f" — due {ai['due_date']}" if ai.get("due_date") else ""
            lines.append(f"- {check} {ai['description']}{assignee}{due}")
        lines.append("")

    recap = "\n".join(lines)
    safe_recap = _html.escape(recap)
    return HTMLResponse(
        f'<div class="card p-4">'
        f'<div id="recap-viewer" class="mb-3"></div>'
        f'<textarea id="recap-raw" class="hidden">{safe_recap}</textarea>'
        f'<div class="flex gap-2">'
        f'<button type="button" id="btn-copy-md"'
        f' class="text-xs bg-marsh text-white px-3 py-1.5 rounded hover:bg-marsh-light transition-colors">Copy markdown</button>'
        f'<button type="button" id="btn-copy-rich"'
        f' class="text-xs bg-gray-100 text-gray-700 px-3 py-1.5 rounded hover:bg-gray-200 transition-colors">Copy rich text</button>'
        f'</div>'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'var el=document.getElementById("recap-viewer");'
        f'var raw=document.getElementById("recap-raw");'
        f'if(el && raw && typeof toastui!=="undefined"){{'
        f'toastui.Editor.factory({{el:el,viewer:true,initialValue:raw.value}});'
        f'}}'
        # Copy markdown button
        f'var btnMd=document.getElementById("btn-copy-md");'
        f'if(btnMd)btnMd.onclick=function(){{'
        f'navigator.clipboard.writeText(raw.value).then(function(){{'
        f'btnMd.textContent="Copied!";setTimeout(function(){{btnMd.textContent="Copy markdown"}},1500);'
        f'}});'
        f'}};'
        # Copy rich text (HTML) button
        f'var btnRich=document.getElementById("btn-copy-rich");'
        f'if(btnRich)btnRich.onclick=function(){{'
        f'var html=el.innerHTML;'
        f'var blob=new Blob([html],{{type:"text/html"}});'
        f'var textBlob=new Blob([raw.value],{{type:"text/plain"}});'
        f'var item=new ClipboardItem({{"text/html":blob,"text/plain":textBlob}});'
        f'navigator.clipboard.write([item]).then(function(){{'
        f'btnRich.textContent="Copied!";setTimeout(function(){{btnRich.textContent="Copy rich text"}},1500);'
        f'}});'
        f'}};'
        f'}})();'
        f'</script>'
    )


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
        "client_id": client_id,
        "pipeline": pipeline,
        "overdue": [dict(f) for f in overdue],
        "upcoming": [dict(f) for f in upcoming],
        "activities": activities,
        "risks": risks,
        "bundles": bundles,
    })
