"""Activity and renewal routes."""

from __future__ import annotations

import logging
logger = logging.getLogger("policydb.web.routes.activities")

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from policydb import config as cfg
from policydb.email_templates import followup_context, render_tokens
from policydb.utils import round_duration
from policydb.queries import (
    get_activities,
    get_activity_by_id,
    get_all_followups,
    get_followup_count_for_date,
    get_open_opportunities,
    get_renewal_pipeline,
    get_suggested_followups,
    get_time_summary,
)
from policydb.web.app import get_db, templates

router = APIRouter()


def _auto_send_rfi_bundle(conn, activity_id: int, *, abandoned: bool = False) -> None:
    """If the completed activity is a 'Send RFI' task, auto-mark the bundle as sent.

    When a user completes (not abandons) a follow-up whose subject starts with
    "Send RFI:", we extract the rfi_uid and set the matching bundle to
    status='sent' with sent_at=CURRENT_TIMESTAMP — unless it's already sent or complete.
    """
    if abandoned:
        return
    row = conn.execute(
        "SELECT subject, client_id FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if not row or not row["subject"]:
        return
    subj = row["subject"]
    if not subj.startswith("Send RFI:"):
        return
    # Extract rfi_uid — subject format is "Send RFI: {rfi_uid} {title}"
    # rfi_uid looks like "CN123-RFI01"
    rest = subj[len("Send RFI:"):].strip()
    parts = rest.split(" ", 1)
    rfi_uid = parts[0] if parts else ""
    if not rfi_uid:
        return
    # Only advance bundles that are still 'open' (don't re-send already sent/complete)
    conn.execute(
        "UPDATE client_request_bundles SET status='sent', sent_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE rfi_uid=? AND client_id=? AND status='open'",
        (rfi_uid, row["client_id"]),
    )


@router.post("/activities/log", response_class=HTMLResponse)
def activity_log(
    request: Request,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    contact_person: str = Form(""),
    contact_id: int = Form(0),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    # Resolve contact_id from contact_person if not provided
    _contact_id = contact_id or None
    if not _contact_id and contact_person:
        _row = conn.execute(
            "SELECT id FROM contacts WHERE LOWER(TRIM(name))=LOWER(TRIM(?))", (contact_person.strip(),)
        ).fetchone()
        if _row:
            _contact_id = _row["id"]

    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person, contact_id, subject, details, follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         contact_person or None, _contact_id, subject, details or None,
         follow_up_date or None, account_exec, round_duration(duration_hours)),
    )

    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    conn.commit()
    logger.info("Activity created for client %d: %s", client_id, activity_type)
    # Return the new activity row as HTMX partial
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (cursor.lastrowid,),
    ).fetchone()
    a = dict(row)
    resp = templates.TemplateResponse("activities/_activity_row.html", {
        "request": request,
        "a": a,
        "dispositions": cfg.get("follow_up_dispositions", []),
    })
    resp.headers["HX-Trigger"] = '{"reorderActivities": "", "activityLogged": "Activity logged"}'
    return resp


@router.post("/activities/{activity_id}/complete", response_class=HTMLResponse)
def activity_complete(
    request: Request,
    activity_id: int,
    duration_hours: float = Form(0),
    note: str = Form(""),
    abandon: str = Form(""),
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    note = note.strip()
    if abandon and note:
        note = f"[Abandoned] {note}"

    # Mark done
    conn.execute(
        "UPDATE activity_log SET follow_up_done=1 WHERE id=?", (activity_id,)
    )

    # Save disposition
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition=? WHERE id=?",
            (disposition.strip(), activity_id),
        )

    # Add time if provided (additive — may already have partial hours)
    if duration_hours and duration_hours > 0:
        conn.execute(
            "UPDATE activity_log SET duration_hours=COALESCE(duration_hours,0)+? WHERE id=?",
            (round_duration(duration_hours), activity_id),
        )

    # Append note if provided
    if note:
        conn.execute(
            "UPDATE activity_log SET details=CASE WHEN details IS NOT NULL AND details!='' THEN details||char(10)||? ELSE ? END WHERE id=?",
            (note, note, activity_id),
        )

    # If this is a mandated activity, update the linked milestone
    mandated = conn.execute(
        "SELECT milestone_id FROM mandated_activity_log WHERE activity_id=?", (activity_id,)
    ).fetchone()
    if mandated and mandated["milestone_id"]:
        if not abandon:
            conn.execute(
                "UPDATE policy_milestones SET completed=1, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (mandated["milestone_id"],),
            )
        # If abandoned, milestone stays incomplete — the note explains why

    # If this is a "Send RFI" task, auto-mark the bundle as sent
    _auto_send_rfi_bundle(conn, activity_id, abandoned=bool(abandon))

    conn.commit()
    logger.info("Follow-up %d completed, disposition=%s", activity_id, disposition or "none")

    # If called from the follow-ups table or briefing, return empty to remove the row
    hx_target = request.headers.get("hx-target", "")
    if hx_target.startswith("followup-") or hx_target.startswith("bq-"):
        return HTMLResponse("", headers={"HX-Trigger": '{"refreshFollowups": "", "activityLogged": "Follow-up completed"}'})
    a = _activity_row_dict(conn, activity_id)
    if not a:
        return HTMLResponse("")
    resp = templates.TemplateResponse("activities/_activity_row.html", {
        "request": request, "a": a,
        "dispositions": cfg.get("follow_up_dispositions", []),
    })
    resp.headers["HX-Trigger"] = '{"reorderActivities": "", "activityLogged": "Activity updated"}'
    return resp


def _activity_row_dict(conn, activity_id: int) -> dict | None:
    """Fetch a single activity row with client_name and policy_uid for template rendering."""
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, c.id AS client_id, c.cn_number, p.policy_uid, p.project_id
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (activity_id,),
    ).fetchone()
    if not row:
        return None
    a = dict(row)
    _attach_pc_emails(conn, [a])
    return a


def _attach_pc_emails(conn, activities: list[dict]) -> None:
    """Batch-attach pc_name/pc_email to activity dicts that have a policy_uid."""
    policy_uids = {a.get("policy_uid") for a in activities if a.get("policy_uid")}
    if not policy_uids:
        return
    placeholders = ",".join("?" * len(policy_uids))
    pc_rows = conn.execute(
        f"""SELECT p.policy_uid, co.name AS pc_name, co.email AS pc_email
            FROM contact_policy_assignments cpa
            JOIN contacts co ON cpa.contact_id = co.id
            JOIN policies p ON cpa.policy_id = p.id
            WHERE p.policy_uid IN ({placeholders})
              AND cpa.is_placement_colleague = 1
              AND co.email IS NOT NULL AND TRIM(co.email) != ''""",
        list(policy_uids),
    ).fetchall()
    pc_map = {r["policy_uid"]: {"pc_name": r["pc_name"], "pc_email": r["pc_email"]} for r in pc_rows}
    for a in activities:
        pc = pc_map.get(a.get("policy_uid"))
        if pc:
            a["pc_name"] = pc["pc_name"]
            a["pc_email"] = pc["pc_email"]


@router.get("/activities/{activity_id}/row", response_class=HTMLResponse)
def activity_row(request: Request, activity_id: int, conn=Depends(get_db)):
    """HTMX partial: return display row (used by Cancel button)."""
    a = _activity_row_dict(conn, activity_id)
    if not a:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("activities/_activity_row.html", {
        "request": request, "a": a,
        "dispositions": cfg.get("follow_up_dispositions", []),
    })


@router.get("/activities/{activity_id}/row/edit", response_class=HTMLResponse)
def activity_row_edit_form(request: Request, activity_id: int, inline: int = 0, conn=Depends(get_db)):
    """HTMX partial: inline edit form for an activity."""
    a = _activity_row_dict(conn, activity_id)
    if not a:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("activities/_activity_row_edit.html", {
        "request": request,
        "a": a,
        "activity_types": cfg.get("activity_types", []),
        "inline": bool(inline),
    })


@router.post("/activities/{activity_id}/row/edit", response_class=HTMLResponse)
def activity_row_edit_save(
    request: Request,
    activity_id: int,
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    duration_hours: str = Form(""),
    follow_up_date: str = Form(""),
    contact_person: str = Form(""),
    contact_id: int = Form(0),
    inline: int = 0,
    conn=Depends(get_db),
):
    """HTMX: save edits to an activity, return display row."""
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    # Resolve contact_id from contact_person if not provided
    _contact_id = contact_id or None
    if not _contact_id and contact_person:
        _row = conn.execute(
            "SELECT id FROM contacts WHERE LOWER(TRIM(name))=LOWER(TRIM(?))", (contact_person.strip(),)
        ).fetchone()
        if _row:
            _contact_id = _row["id"]

    conn.execute(
        """UPDATE activity_log SET
           activity_type=?, subject=?, details=?,
           duration_hours=?, follow_up_date=?, contact_person=?, contact_id=?
           WHERE id=?""",
        (activity_type, subject, details or None,
         round_duration(duration_hours), follow_up_date or None,
         contact_person or None, _contact_id, activity_id),
    )
    conn.commit()
    if inline:
        return HTMLResponse(
            '<p class="text-xs text-green-600 font-medium py-1">Saved. Changes will appear on next page load.</p>'
        )
    a = _activity_row_dict(conn, activity_id)
    if not a:
        return HTMLResponse("", status_code=404)
    resp = templates.TemplateResponse("activities/_activity_row.html", {
        "request": request, "a": a,
        "dispositions": cfg.get("follow_up_dispositions", []),
    })
    resp.headers["HX-Trigger"] = "reorderActivities"
    return resp


@router.post("/activities/{activity_id}/delete", response_class=HTMLResponse)
def activity_delete(
    request: Request,
    activity_id: int,
    context: str = Form(""),
    conn=Depends(get_db),
):
    """Delete an activity log entry. Also clears any linked meeting action items."""
    # Unlink from meeting action items and mandated activity log
    conn.execute(
        "UPDATE meeting_action_items SET activity_id = NULL WHERE activity_id = ?",
        (activity_id,),
    )
    conn.execute(
        "UPDATE mandated_activity_log SET activity_id = NULL WHERE activity_id = ?",
        (activity_id,),
    )
    conn.execute("DELETE FROM activity_log WHERE id = ?", (activity_id,))
    conn.commit()
    if context == "followup_table":
        # In the follow-ups table, replace the <tr> and its related form rows
        resp = HTMLResponse(
            f'<tr id="followup-activity-{activity_id}"><td colspan="8" class="px-4 py-2 text-xs text-gray-400 italic">Deleted.</td></tr>'
        )
    else:
        resp = HTMLResponse(
            f'<li id="activity-{activity_id}" class="py-2 text-xs text-gray-400 italic">Deleted.</li>'
        )
    resp.headers["HX-Trigger"] = '{"activityLogged": "Activity deleted"}'
    return resp


@router.post("/activities/{activity_id}/followup", response_class=HTMLResponse)
def activity_followup(
    request: Request,
    activity_id: int,
    notes: str = Form(""),
    duration_hours: str = Form(""),
    new_follow_up_date: str = Form(""),
    context: str = Form(""),
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    """Follow-up + re-diary: mark current done, create new activity with hours and next follow-up.

    Workflow: user checked in on a follow-up, spent time, needs to re-diary.
    This marks the original done, creates a new activity with hours logged and the new date.
    The activity chain shows the full work log.
    """
    original = _activity_row_dict(conn, activity_id)
    if not original:
        return HTMLResponse("", status_code=404)

    # Mark original follow-up as done
    conn.execute("UPDATE activity_log SET follow_up_done=1 WHERE id=?", (activity_id,))

    # Save disposition on the original activity
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition=? WHERE id=?",
            (disposition.strip(), activity_id),
        )

    # Create new activity (re-diary)
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    subject = original.get("subject", "")
    if not subject.startswith("Follow-up:"):
        subject = f"Follow-up: {subject}"

    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person,
            subject, details, follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), original["client_id"],
         original.get("policy_id") or None,
         original.get("activity_type", "Call"),
         original.get("contact_person") or None,
         subject, notes or None,
         new_follow_up_date or None, account_exec, dur),
    )
    if new_follow_up_date and original.get("policy_id"):
        from policydb.queries import supersede_followups
        supersede_followups(conn, original["policy_id"], new_follow_up_date)
    conn.commit()

    # If this follow-up is for a policy with a disposition, update the timeline
    if original.get("policy_id") and disposition:
        pol_row = conn.execute(
            "SELECT policy_uid FROM policies WHERE id=?", (original["policy_id"],)
        ).fetchone()
        if pol_row:
            _policy_uid = pol_row["policy_uid"]
            from policydb.timeline_engine import update_timeline_from_followup
            # Find the earliest incomplete milestone to re-diary against
            active = conn.execute("""
                SELECT milestone_name FROM policy_timeline
                WHERE policy_uid = ? AND completed_date IS NULL
                ORDER BY projected_date LIMIT 1
            """, (_policy_uid,)).fetchone()
            if active:
                update_timeline_from_followup(
                    conn, _policy_uid, active["milestone_name"],
                    disposition, new_follow_up_date or None,
                    waiting_on=None,
                )

    if context == "followup_table":
        # Build a followup-table-style dict for the new activity
        new_id = cursor.lastrowid
        frow = conn.execute(
            """SELECT a.*, c.name AS client_name, c.cn_number, c.id AS client_id,
                      p.policy_uid, p.project_id, p.policy_type, p.carrier, p.project_name,
                      CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
                      NULL AS contact_email, NULL AS internal_cc
               FROM activity_log a
               JOIN clients c ON a.client_id = c.id
               LEFT JOIN policies p ON a.policy_id = p.id
               WHERE a.id = ?""",
            (new_id,),
        ).fetchone()
        if not frow:
            return HTMLResponse("")
        r = dict(frow)
        r["source"] = "activity"
        today_str = date.today().isoformat()
        r["_is_overdue"] = (r.get("follow_up_date") or "") < today_str
        r["note_details"] = r.get("details")
        r["note_subject"] = r.get("subject")
        r["note_date"] = r.get("activity_date")
        resp = templates.TemplateResponse("followups/_row.html", {
            "request": request, "r": r, "today": today_str,
            "dispositions": cfg.get("follow_up_dispositions", []),
        })
        resp.headers["HX-Trigger"] = '{"refreshFollowups": "", "activityLogged": "Follow-up re-diaried - new activity created"}'
        return resp

    new_activity = _activity_row_dict(conn, cursor.lastrowid)
    if not new_activity:
        return HTMLResponse("")
    resp = templates.TemplateResponse("activities/_activity_row.html", {
        "request": request, "a": new_activity,
        "dispositions": cfg.get("follow_up_dispositions", []),
    })
    resp.headers["HX-Trigger"] = '{"reorderActivities": "", "activityLogged": "Follow-up re-diaried - new activity created"}'
    return resp


@router.post("/activities/{activity_id}/snooze", response_class=HTMLResponse)
def activity_snooze(request: Request, activity_id: int, days: int = 7, conn=Depends(get_db)):
    conn.execute(
        "UPDATE activity_log SET follow_up_date = date(follow_up_date, ?) WHERE id=?",
        (f"+{days} days", activity_id),
    )
    conn.commit()
    # If called from activity list context, return activity row
    hx_target = request.headers.get("hx-target", "")
    if hx_target.startswith("activity-"):
        a = _activity_row_dict(conn, activity_id)
        if not a:
            return HTMLResponse("")
        resp = templates.TemplateResponse("activities/_activity_row.html", {
            "request": request, "a": a,
            "dispositions": cfg.get("follow_up_dispositions", []),
        })
        resp.headers["HX-Trigger"] = "reorderActivities"
        return resp
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, c.cn_number, p.policy_uid, p.project_id, p.policy_type, p.carrier, p.project_name,
                  CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
                  NULL AS contact_email, NULL AS internal_cc
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (activity_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    new_date = dict(row)["follow_up_date"]
    # Row deleted by hx-swap="delete" on button. Trigger refresh + toast.
    return HTMLResponse("", headers={
        "HX-Trigger": '{"refreshFollowups": "", "activityLogged": "Snoozed +' + str(days) + 'd to ' + new_date + '"}',
    })


@router.post("/activities/{activity_id}/reschedule", response_class=HTMLResponse)
def activity_reschedule(request: Request, activity_id: int, new_date: str = Form(...), conn=Depends(get_db)):
    """Reschedule an activity follow-up to a specific date."""
    conn.execute(
        "UPDATE activity_log SET follow_up_date = ? WHERE id=?",
        (new_date, activity_id),
    )
    conn.commit()
    # If called from activity list, return activity row
    hx_target = request.headers.get("hx-target", "")
    if hx_target.startswith("activity-"):
        a = _activity_row_dict(conn, activity_id)
        if not a:
            return HTMLResponse("")
        resp = templates.TemplateResponse("activities/_activity_row.html", {
            "request": request, "a": a,
            "dispositions": cfg.get("follow_up_dispositions", []),
        })
        resp.headers["HX-Trigger"] = '{"reorderActivities": "", "activityLogged": "Rescheduled to ' + new_date + '"}'
        return resp
    # Row deleted by hx-swap="delete" on button. Trigger refresh + toast.
    return HTMLResponse("", headers={
        "HX-Trigger": '{"refreshFollowups": "", "activityLogged": "Rescheduled to ' + new_date + '"}',
    })


@router.get("/followups/date-count")
def followup_date_count(date: str = "", conn=Depends(get_db)):
    """Return the number of pending follow-ups on a given date."""
    if not date:
        return JSONResponse({"count": 0})
    count = get_followup_count_for_date(conn, date)
    return JSONResponse({"count": count})


# ── Activity field PATCH (Action Center inline editing) ──────────────────────


_ACTIVITY_EDITABLE_FIELDS = {"subject", "activity_type", "duration_hours", "disposition", "details"}


@router.patch("/activities/{activity_id}/field")
def patch_activity_field(activity_id: int, request_body: dict = None, conn=Depends(get_db)):
    """Update a single field on an activity (for inline editing)."""
    import json
    from starlette.requests import Request as _Req

    if request_body is None:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)

    field = request_body.get("field", "")
    value = request_body.get("value", "")

    if field not in _ACTIVITY_EDITABLE_FIELDS:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)

    # Validate & format
    formatted = value
    if field == "duration_hours":
        formatted = str(round_duration(value))
        value = round_duration(value)

    conn.execute(f"UPDATE activity_log SET {field} = ? WHERE id = ?", (value or None, activity_id))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


# ── Plan Week routes ─────────────────────────────────────────────────────────


@router.get("/followups/plan", response_class=HTMLResponse)
def followups_plan(request: Request, week_start: str = "", conn=Depends(get_db)):
    """Plan Week view — visualize and rebalance follow-up workload."""
    from datetime import date, timedelta
    from policydb.queries import get_week_followups
    from collections import defaultdict

    # Default to current week's Monday
    today = date.today()
    if week_start:
        try:
            mon = date.fromisoformat(week_start)
        except ValueError:
            mon = today - timedelta(days=today.weekday())
    else:
        mon = today - timedelta(days=today.weekday())

    week_days = [(mon + timedelta(days=i)).isoformat() for i in range(5)]
    pin_days = cfg.get("pin_renewal_days", 14)
    target = cfg.get("daily_followup_target", 5)

    items = get_week_followups(conn, mon.isoformat(), pin_days)

    # Group by date
    by_date = defaultdict(list)
    for item in items:
        by_date[item["follow_up_date"]].append(item)

    from policydb.queries import _weighted_load
    columns = []
    for d in week_days:
        day_items = by_date.get(d, [])
        day_date = date.fromisoformat(d)
        load = _weighted_load(day_items)
        columns.append({
            "date": d,
            "label": day_date.strftime("%a %b %d"),
            "day_items": day_items,
            "count": len(day_items),
            "weighted_load": load,
            "pinned_count": sum(1 for i in day_items if i.get("pinned")),
        })

    prev_week = (mon - timedelta(days=7)).isoformat()
    next_week = (mon + timedelta(days=7)).isoformat()
    this_monday = (today - timedelta(days=today.weekday())).isoformat()

    return templates.TemplateResponse("followups/plan.html", {
        "request": request,
        "active": "followups",
        "columns": columns,
        "week_start": mon.isoformat(),
        "week_label": f"{mon.strftime('%b %d')} – {(mon + timedelta(days=4)).strftime('%b %d, %Y')}",
        "prev_week": prev_week,
        "next_week": next_week,
        "this_monday": this_monday,
        "daily_target": target,
        "total_items": len(items),
    })


@router.post("/followups/plan/spread", response_class=HTMLResponse)
def followups_spread(request: Request, week_start: str = Form(...), conn=Depends(get_db)):
    """Compute and return proposed spread for the week."""
    from datetime import date, timedelta
    from policydb.queries import get_week_followups, spread_followups

    mon = date.fromisoformat(week_start)
    week_days = [(mon + timedelta(days=i)).isoformat() for i in range(5)]
    pin_days = cfg.get("pin_renewal_days", 14)
    target = cfg.get("daily_followup_target", 5)

    items = get_week_followups(conn, week_start, pin_days)
    proposals = spread_followups(items, target, week_days)

    if not proposals:
        return HTMLResponse("", headers={
            "HX-Trigger": '{"activityLogged": "Week is already balanced"}'
        })

    # Return proposals as JSON for the JS to preview
    return JSONResponse({
        "proposals": proposals,
        "count": len(proposals),
    })


@router.post("/followups/plan/apply-spread")
async def followups_apply_spread(request: Request, conn=Depends(get_db)):
    """Apply proposed spread — batch reschedule follow-ups."""
    body = await request.json()
    moves = body.get("moves", [])
    count = 0
    for move in moves:
        cid = move.get("composite_id", "")
        new_date = move.get("new_date", "")
        if not cid or not new_date:
            continue
        source, item_id = cid.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        count += 1
    conn.commit()
    return JSONResponse({"ok": True, "count": count})


@router.post("/followups/plan/move")
async def followups_plan_move(request: Request, conn=Depends(get_db)):
    """Drag-and-drop reschedule a single follow-up."""
    body = await request.json()
    cid = body.get("composite_id", "")
    new_date = body.get("new_date", "")
    if not cid or not new_date:
        return JSONResponse({"ok": False})
    source, item_id = cid.split("-", 1)
    if source == "activity":
        conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
    elif source == "policy":
        conn.execute("UPDATE policies SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
    conn.commit()
    return JSONResponse({"ok": True})


def _add_mailto_subjects(rows: list, subject_tpl: str) -> list:
    """Convert rows to dicts and add rendered mailto_subject to each."""
    result = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        d["mailto_subject"] = render_tokens(subject_tpl, followup_context(d))
        result.append(d)
    return result


def _followups_ctx(conn, window: int, activity_type: str, q: str,
                   client_id: int = 0, group_id: int = 0) -> dict:
    # Resolve client_ids for filtering
    filter_client_ids = None
    group_label = ""
    if group_id:
        members = conn.execute(
            "SELECT client_id FROM client_group_members WHERE group_id=?", (group_id,)
        ).fetchall()
        filter_client_ids = [m["client_id"] for m in members]
        grp = conn.execute("SELECT label, relationship FROM client_groups WHERE id=?", (group_id,)).fetchone()
        if grp:
            group_label = grp["label"] or grp["relationship"] or "Linked Group"
    elif client_id:
        filter_client_ids = [client_id]

    excluded = cfg.get("renewal_statuses_excluded", [])
    overdue_raw, upcoming_raw = get_all_followups(conn, window=window, client_ids=filter_client_ids)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded, client_ids=filter_client_ids)
    if activity_type:
        overdue_raw  = [r for r in overdue_raw  if r["activity_type"] == activity_type]
        upcoming_raw = [r for r in upcoming_raw if r["activity_type"] == activity_type]
    if q:
        q_lower = q.lower()
        overdue_raw  = [r for r in overdue_raw  if q_lower in r["client_name"].lower()]
        upcoming_raw = [r for r in upcoming_raw if q_lower in r["client_name"].lower()]
        suggested    = [r for r in suggested    if q_lower in r["client_name"].lower()]
    subject_tpl = cfg.get("email_subject_followup", "Re: {{client_name}} — {{policy_type}} — {{subject}}")
    overdue  = _add_mailto_subjects(overdue_raw,  subject_tpl)
    upcoming = _add_mailto_subjects(upcoming_raw, subject_tpl)
    # Split upcoming into triage groups
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    today_items = [r for r in upcoming if r.get("follow_up_date") == today_str]
    tomorrow_items = [r for r in upcoming if r.get("follow_up_date") == tomorrow_str]
    later_items = [r for r in upcoming if r.get("follow_up_date", "") > tomorrow_str]
    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    return {
        "overdue": overdue,
        "upcoming": upcoming,
        "today_items": today_items,
        "tomorrow_items": tomorrow_items,
        "later_items": later_items,
        "suggested": suggested,
        "window": window,
        "activity_type": activity_type,
        "q": q,
        "client_id": client_id,
        "group_id": group_id,
        "group_label": group_label,
        "all_clients": all_clients,
        "today": today_str,
        "activity_types": cfg.get("activity_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    }


@router.get("/followups")
def followups_page():
    """Redirect to Action Center follow-ups tab."""
    return RedirectResponse("/action-center?tab=followups", status_code=302)


@router.get("/followups/results", response_class=HTMLResponse)
def followups_results(
    request: Request,
    window: int = 30,
    activity_type: str = "",
    q: str = "",
    client_id: int = 0,
    group_id: int = 0,
    conn=Depends(get_db),
):
    """HTMX partial: return just the results tables for filter updates."""
    ctx = _followups_ctx(conn, window, activity_type, q, client_id=client_id, group_id=group_id)
    ctx["request"] = request
    return templates.TemplateResponse("followups/_results.html", ctx)


@router.get("/activities")
def activity_list():
    """Redirect to Action Center activities tab."""
    return RedirectResponse("/action-center?tab=activities", status_code=302)


@router.get("/activities-old", response_class=HTMLResponse)
def activity_list_old(
    request: Request,
    days: int = 90,
    activity_type: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    rows = [dict(r) for r in get_activities(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )]
    _attach_pc_emails(conn, rows)
    time_summary = get_time_summary(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )
    overdue, _ = get_all_followups(conn, window=0)
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("activities/list.html", {
        "request": request,
        "active": "activities",
        "activities": rows,
        "time_summary": time_summary,
        "overdue": overdue,
        "days": days,
        "activity_type": activity_type,
        "client_id": client_id,
        "activity_types": cfg.get("activity_types", []),
        "all_clients": [dict(c) for c in all_clients],
    })


_URGENCY_ORDER = ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]


@router.get("/renewals/calendar", response_class=HTMLResponse)
def renewals_calendar(request: Request, conn=Depends(get_db)):
    rows = get_renewal_pipeline(conn, window_days=365)

    months: dict = defaultdict(list)
    for p in rows:
        d = dict(p)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name=?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        months[d["expiration_date"][:7]].append(d)

    calendar = []
    for month_key in sorted(months.keys()):
        policies = months[month_key]
        urgencies = [p["urgency"] for p in policies]
        worst = min(urgencies, key=lambda u: _URGENCY_ORDER.index(u) if u in _URGENCY_ORDER else 99)
        calendar.append({
            "month_key": month_key,
            "month_label": datetime.strptime(month_key, "%Y-%m").strftime("%B %Y"),
            "policies": policies,
            "policy_count": len(policies),
            "total_premium": sum(p["premium"] or 0 for p in policies),
            "worst_urgency": worst,
        })

    return templates.TemplateResponse("renewals_calendar.html", {
        "request": request,
        "active": "renewals",
        "calendar": calendar,
    })


@router.get("/renewals/export")
def renewals_export(window: int = 180, fmt: str = "xlsx", conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_renewals_csv, export_renewals_xlsx
    today = date.today().isoformat()
    if fmt == "xlsx":
        content = export_renewals_xlsx(conn, window_days=window)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="renewals_{today}.xlsx"'},
        )
    content = export_renewals_csv(conn, window_days=window)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="renewals_{today}.csv"'},
    )


_RENEWAL_SORT_FIELDS = {
    "client_name", "carrier", "expiration_date", "days_to_renewal",
    "premium", "renewal_status", "follow_up_date",
}

@router.get("/renewals", response_class=HTMLResponse)
def renewals(request: Request, window: int = 180, urgency: str = "", status: str = "",
             sort: str = "expiration_date", dir: str = "asc", conn=Depends(get_db)):
    excluded = cfg.get("renewal_statuses_excluded", [])
    rows = get_renewal_pipeline(
        conn,
        window_days=window,
        urgency=urgency or None,
        renewal_status=status or None,
        excluded_statuses=excluded,
    )

    # Attach client_id for linking, then milestone progress
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _subj_tpl = cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    pipeline = []
    for p in rows:
        d = dict(p)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name=?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        _mail_ctx = _policy_ctx(conn, d["policy_uid"])
        d["mailto_subject"] = _render_tokens(_subj_tpl, _mail_ctx)
        pipeline.append(d)
    from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
    pipeline = _attach_readiness_score(conn, _attach_milestone_progress(conn, pipeline))

    sort_field = sort if sort in _RENEWAL_SORT_FIELDS else "expiration_date"
    reverse = dir == "desc"
    pipeline.sort(
        key=lambda r: (r.get(sort_field) is None, r.get(sort_field) or ""),
        reverse=reverse,
    )

    open_opportunities = get_open_opportunities(conn)
    _today = date.today()
    for o in open_opportunities:
        if o.get("target_effective_date"):
            try:
                o["days_to_target"] = (date.fromisoformat(o["target_effective_date"]) - _today).days
            except ValueError:
                o["days_to_target"] = None
        else:
            o["days_to_target"] = None

    return templates.TemplateResponse("renewals.html", {
        "request": request,
        "active": "renewals",
        "rows": pipeline,
        "window": window,
        "urgency": urgency,
        "status": status,
        "sort": sort_field,
        "dir": dir,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "renewal_milestones": cfg.get("renewal_milestones", []),
        "activity_types": cfg.get("activity_types", ["Call", "Email", "Meeting", "Note", "Other"]),
        "open_opportunities": open_opportunities,
        "today": date.today().isoformat(),
    })


# ── Bulk triage actions ──────────────────────────────────────────────────────

@router.post("/followups/bulk-reschedule", response_class=HTMLResponse)
def bulk_reschedule(
    request: Request,
    ids: str = Form(...),
    new_date: str = Form(...),
    conn=Depends(get_db),
):
    """Bulk reschedule selected follow-ups to a specific date."""
    for item in ids.split(","):
        item = item.strip()
        if not item:
            continue
        source, item_id = item.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=? WHERE policy_uid=?", (new_date, item_id))
    conn.commit()
    count = len([i for i in ids.split(",") if i.strip()])
    window = 30
    ctx = _followups_ctx(conn, window, "", "")
    ctx["request"] = request
    resp = templates.TemplateResponse("followups/_results.html", ctx)
    resp.headers["HX-Trigger"] = '{"activityLogged": "' + f'{count} follow-up(s) rescheduled to {new_date}' + '"}'
    return resp


@router.post("/renewals/bulk-milestones", response_class=HTMLResponse)
def bulk_milestones(
    request: Request,
    policy_uids: str = Form(...),
    milestone: str = Form(...),
    action: str = Form(...),
    conn=Depends(get_db),
):
    """Bulk mark a milestone complete or incomplete for multiple policies."""
    now = datetime.now().isoformat()
    for uid in policy_uids.split(","):
        uid = uid.strip().upper()
        if not uid:
            continue
        existing = conn.execute(
            "SELECT completed FROM policy_milestones WHERE policy_uid=? AND milestone=?",
            (uid, milestone),
        ).fetchone()
        if action == "complete":
            if existing:
                conn.execute(
                    "UPDATE policy_milestones SET completed=1, completed_at=? WHERE policy_uid=? AND milestone=?",
                    (now, uid, milestone),
                )
            else:
                conn.execute(
                    "INSERT INTO policy_milestones (policy_uid, milestone, completed, completed_at) VALUES (?,?,1,?)",
                    (uid, milestone, now),
                )
        else:  # incomplete
            if existing:
                conn.execute(
                    "UPDATE policy_milestones SET completed=0, completed_at=NULL WHERE policy_uid=? AND milestone=?",
                    (uid, milestone),
                )
    conn.commit()
    return HTMLResponse("")


@router.post("/renewals/bulk/log", response_class=HTMLResponse)
def bulk_log(
    request: Request,
    policy_uids: str = Form(...),
    activity_type: str = Form(...),
    subject: str = Form(...),
    contact_person: str = Form(""),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Bulk log an activity to multiple selected renewal policies."""
    def _float(v):
        try:
            return float(v) if str(v).strip() else None
        except ValueError:
            return None

    today = date.today().isoformat()
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    fu = follow_up_date.strip() or None

    for uid in policy_uids.split(","):
        uid = uid.strip().upper()
        if not uid:
            continue
        policy = conn.execute(
            "SELECT id, client_id FROM policies WHERE policy_uid = ?", (uid,)
        ).fetchone()
        if not policy:
            continue
        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, activity_type, contact_person,
                subject, details, follow_up_date, duration_hours, account_exec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, policy["client_id"], policy["id"], activity_type,
             contact_person.strip() or None, subject.strip(), details.strip() or None,
             fu, dur, account_exec),
        )
        if fu:
            from policydb.queries import supersede_followups
            supersede_followups(conn, policy["id"], fu)
    conn.commit()
    count = sum(1 for uid in policy_uids.split(",") if uid.strip())
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = '{"activityLogged": "' + f'Activity logged to {count} policies' + '"}'
    return resp


@router.post("/followups/bulk-complete", response_class=HTMLResponse)
def bulk_complete(
    request: Request,
    ids: str = Form(...),
    duration_hours: float = Form(0),
    note: str = Form(""),
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    """Bulk complete/clear selected follow-ups with optional time and note."""
    note = note.strip()
    dur = round_duration(duration_hours) if duration_hours and duration_hours > 0 else None
    for item in ids.split(","):
        item = item.strip()
        if not item:
            continue
        source, item_id = item.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_done=1 WHERE id=?", (int(item_id),))
            if disposition:
                conn.execute(
                    "UPDATE activity_log SET disposition=? WHERE id=?",
                    (disposition.strip(), int(item_id)),
                )
            if dur:
                conn.execute(
                    "UPDATE activity_log SET duration_hours=COALESCE(duration_hours,0)+? WHERE id=?",
                    (dur, int(item_id)),
                )
            if note:
                conn.execute(
                    "UPDATE activity_log SET details=CASE WHEN details IS NOT NULL AND details!='' THEN details||char(10)||? ELSE ? END WHERE id=?",
                    (note, note, int(item_id)),
                )
            # Auto-mark RFI bundle as sent when its "Send RFI" task is completed
            _auto_send_rfi_bundle(conn, int(item_id))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=NULL WHERE policy_uid=?", (item_id,))
            if dur or note:
                pol = conn.execute(
                    "SELECT id, client_id, policy_type FROM policies WHERE policy_uid=?", (item_id,)
                ).fetchone()
                if pol:
                    from datetime import date as _date
                    account_exec = cfg.get("default_account_exec", "")
                    conn.execute(
                        """INSERT INTO activity_log
                           (activity_date, client_id, policy_id, activity_type, subject, details,
                            duration_hours, follow_up_done, account_exec)
                           VALUES (?, ?, ?, 'Task', ?, ?, ?, 1, ?)""",
                        (_date.today().isoformat(), pol["client_id"], pol["id"],
                         f"Cleared follow-up — {pol['policy_type']}", note or None, dur, account_exec),
                    )
    conn.commit()
    count = len([i for i in ids.split(",") if i.strip()])
    window = 30
    ctx = _followups_ctx(conn, window, "", "")
    ctx["request"] = request
    resp = templates.TemplateResponse("followups/_results.html", ctx)
    resp.headers["HX-Trigger"] = '{"activityLogged": "' + f'{count} follow-up(s) completed' + '"}'
    return resp
