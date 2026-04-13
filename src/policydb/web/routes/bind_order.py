"""Bind Order routes — slideover panel preview + submit."""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.bind_order import (
    BindChildPayload,
    BindOrderPayload,
    BindSubject,
    BindSubjectPayload,
    execute_bind_order,
    preview_bind_panel,
)
from policydb.web.app import get_db, templates

logger = logging.getLogger("policydb.bind_order")
router = APIRouter(tags=["bind_order"])


def _parse_subjects_param(raw: str) -> list[BindSubject]:
    if not raw:
        return []
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    parsed: list[BindSubject] = []
    for t in tokens:
        try:
            parsed.append(BindSubject.parse(t))
        except ValueError as e:
            logger.warning("Skipping invalid subject token: %s (%s)", t, e)
    return parsed


@router.get("/bind-order/panel", response_class=HTMLResponse)
def bind_order_panel(
    request: Request,
    subjects: str = Query(..., description="comma-separated tokens like 'program:PGM-042,policy:POL-017'"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Render the Bind Order slideover panel for the given subjects."""
    parsed = _parse_subjects_param(subjects)
    if not parsed:
        raise HTTPException(status_code=400, detail="No valid subjects provided")

    panel = preview_bind_panel(conn, parsed)
    return templates.TemplateResponse("bind_order/_panel.html", {
        "request": request,
        "panel": panel,
    })


@router.post("/bind-order/submit")
async def bind_order_submit(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Apply a bind order. Accepts JSON payload describing the panel state."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    bind_date = (body.get("bind_date") or "").strip()
    bind_note = body.get("bind_note") or ""
    raw_subjects = body.get("subjects") or []
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise HTTPException(status_code=400, detail="Missing 'subjects' list")

    payload_subjects: list[BindSubjectPayload] = []
    for sub in raw_subjects:
        try:
            children_raw = sub.get("children") or []
            children = [
                BindChildPayload(
                    policy_uid=str(c.get("policy_uid") or "").strip().upper(),
                    checked=bool(c.get("checked")),
                    disposition=(c.get("disposition") or None),
                    new_premium=(float(c["new_premium"]) if c.get("new_premium") not in (None, "") else None),
                )
                for c in children_raw
                if c.get("policy_uid")
            ]
            payload_subjects.append(BindSubjectPayload(
                subject_type=sub["subject_type"],
                subject_uid=str(sub["subject_uid"]).strip().upper(),
                new_effective=str(sub.get("new_effective") or "").strip(),
                new_expiration=str(sub.get("new_expiration") or "").strip(),
                children=children,
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid subject payload: {e}")

    payload = BindOrderPayload(
        bind_date=bind_date,
        bind_note=bind_note,
        subjects=payload_subjects,
    )

    try:
        result = execute_bind_order(conn, payload)
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        conn.rollback()
        logger.exception("Bind order execution failed")
        raise HTTPException(status_code=500, detail=f"Bind order failed: {e}")

    response = JSONResponse({
        "ok": True,
        "bound_count": result.bound_count,
        "excepted_count": result.excepted_count,
        "renewed_inline_count": result.renewed_inline_count,
        "bind_event_ids": result.bind_event_ids,
        "toast_message": result.toast_message,
    })
    response.headers["HX-Trigger"] = json.dumps({"bindOrderComplete": result.toast_message})
    return response
