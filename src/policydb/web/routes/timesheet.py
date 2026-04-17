"""Phase 4 — Timesheet Review routes."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

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
