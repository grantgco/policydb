"""Phase 4 — Timesheet Review routes."""
from __future__ import annotations

import sqlite3 as _sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from policydb import config as cfg
from policydb.db import get_connection
from policydb.timesheet import build_timesheet_payload
from policydb.web.app import templates

router = APIRouter(prefix="/timesheet", tags=["timesheet"])


def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _resolve_range(
    kind: str,
    start: str | None,
    end: str | None,
) -> tuple[date, date, str]:
    today = date.today()
    if kind == "day":
        d = date.fromisoformat(start) if start else today
        return d, d, "day"
    if kind == "range":
        if not start or not end:
            raise HTTPException(400, "range requires start and end")
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        if e < s:
            raise HTTPException(400, "end < start")
        cap = int((cfg.get("timesheet_thresholds", {}) or {}).get("range_cap_days", 92))
        if (e - s).days + 1 > cap:
            raise HTTPException(400, f"range exceeds {cap} days")
        return s, e, "range"
    anchor = date.fromisoformat(start) if start else today
    week_start = anchor - timedelta(days=anchor.weekday())
    return week_start, week_start + timedelta(days=6), "week"


@router.get("/panel", response_class=HTMLResponse)
def get_panel(
    request: Request,
    kind: str = Query("week"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    conn=Depends(get_db),
):
    s, e, resolved_kind = _resolve_range(kind, start, end)
    payload = build_timesheet_payload(conn, start=s, end=e)
    payload["range"]["kind"] = resolved_kind
    return templates.TemplateResponse(
        "timesheet/_panel.html",
        {"request": request, "payload": payload},
    )


@router.post("/closeout")
def post_closeout(
    week_start: str = Form(...),
    conn=Depends(get_db),
):
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(400, "Invalid week_start")
    if ws.weekday() != 0:
        raise HTTPException(400, "week_start must be a Monday")
    we = ws + timedelta(days=6)

    payload = build_timesheet_payload(conn, start=ws, end=we)

    try:
        cur = conn.execute(
            """INSERT INTO timesheet_closeouts
               (week_start, week_end, total_hours, activity_count, flag_count)
               VALUES (?, ?, ?, ?, ?)""",
            (ws.isoformat(), we.isoformat(),
             payload["totals"]["total_hours"],
             payload["totals"]["activity_count"],
             payload["totals"]["flag_count"]),
        )
    except _sqlite3.IntegrityError:
        raise HTTPException(409, "Week already closed")

    conn.execute(
        """UPDATE activity_log
           SET reviewed_at = datetime('now')
           WHERE reviewed_at IS NULL
             AND activity_date BETWEEN ? AND ?""",
        (ws.isoformat(), we.isoformat()),
    )
    conn.commit()

    return JSONResponse({
        "ok": True,
        "id": cur.lastrowid,
        "week_start": ws.isoformat(),
    })


@router.post("/closeout/{closeout_id}/reopen")
def post_reopen(closeout_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM timesheet_closeouts WHERE id=?", (closeout_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Closeout not found")
    conn.execute("DELETE FROM timesheet_closeouts WHERE id=?", (closeout_id,))
    conn.commit()
    return JSONResponse({"ok": True})


@router.get("/activity/new", response_class=HTMLResponse)
def get_new_activity_form(
    request: Request,
    date: str = Query(...),
    conn=Depends(get_db),
):
    from datetime import date as _date
    try:
        _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date")
    clients = conn.execute(
        "SELECT id, name FROM clients ORDER BY name LIMIT 500"
    ).fetchall()
    return templates.TemplateResponse(
        "timesheet/_add_activity_form.html",
        {
            "request": request,
            "day": {"date": date},
            "client_list": [dict(r) for r in clients],
        },
    )


@router.post("/activity/{activity_id}/review")
def post_review(activity_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")
    conn.execute(
        """UPDATE activity_log
           SET reviewed_at = datetime('now')
           WHERE id = ? AND reviewed_at IS NULL""",
        (activity_id,),
    )
    conn.commit()
    return Response(status_code=204)


def _round_to_tenth(raw: str) -> float | None:
    """Accept any numeric input; round to nearest 0.1 (half-up). Per feedback_hours_any_numeric."""
    import decimal
    try:
        return float(
            decimal.Decimal(str(raw)).quantize(
                decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP
            )
        )
    except (TypeError, ValueError, decimal.InvalidOperation):
        return None


@router.patch("/activity/{activity_id}")
def patch_activity(
    activity_id: int,
    duration_hours: str | None = Form(None),
    subject: str | None = Form(None),
    activity_type: str | None = Form(None),
    details: str | None = Form(None),
    conn=Depends(get_db),
):
    row = conn.execute(
        "SELECT id, activity_date FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")

    updates: list[str] = []
    params: list = []

    if duration_hours is not None:
        rounded = _round_to_tenth(duration_hours)
        if rounded is None:
            raise HTTPException(400, "duration_hours must be numeric")
        updates.append("duration_hours=?")
        params.append(rounded)

    if subject is not None:
        updates.append("subject=?")
        params.append(subject.strip())

    if activity_type is not None:
        updates.append("activity_type=?")
        params.append(activity_type.strip())

    if details is not None:
        updates.append("details=?")
        params.append(details)

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates.append("reviewed_at=COALESCE(reviewed_at, datetime('now'))")
    params.append(activity_id)

    conn.execute(f"UPDATE activity_log SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()

    day_total = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS h
           FROM activity_log WHERE activity_date=?""",
        (row["activity_date"],),
    ).fetchone()["h"]

    formatted = (
        f"{round(float(duration_hours), 2):g}" if duration_hours is not None else None
    )

    return JSONResponse({
        "ok": True,
        "formatted": formatted,
        "total_hours": round(float(day_total), 2),
    })


@router.post("/activity")
def post_activity(
    client_id: int = Form(...),
    activity_date: str = Form(...),
    subject: str = Form(""),
    activity_type: str = Form("Note"),
    duration_hours: str | None = Form(None),
    details: str | None = Form(None),
    policy_id: int | None = Form(None),
    conn=Depends(get_db),
):
    try:
        date.fromisoformat(activity_date)
    except ValueError:
        raise HTTPException(400, "Invalid activity_date")

    ok = conn.execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone()
    if not ok:
        raise HTTPException(400, "client_id does not exist")

    rounded = _round_to_tenth(duration_hours) if duration_hours else None
    account_exec = cfg.get("default_account_exec", "Grant")

    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, subject, activity_type,
            duration_hours, details, account_exec, item_kind, reviewed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'activity', datetime('now'))""",
        (activity_date, client_id, policy_id, subject.strip(),
         activity_type.strip(), rounded, details, account_exec),
    )
    conn.commit()
    return JSONResponse({"ok": True, "id": cur.lastrowid}, status_code=201)


@router.delete("/activity/{activity_id}")
def delete_activity(activity_id: int, conn=Depends(get_db)):
    row = conn.execute("SELECT id FROM activity_log WHERE id=?", (activity_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")
    conn.execute("DELETE FROM activity_log WHERE id=?", (activity_id,))
    conn.commit()
    return Response(status_code=204)


@router.get("", response_class=HTMLResponse)
def get_full_page(request: Request):
    return templates.TemplateResponse(
        "timesheet/full_page.html", {"request": request}
    )
