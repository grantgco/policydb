"""Recurring event template routes — CRUD + skip wrapper.

Templates live in the recurring_events table. Each active template materializes
pending occurrences as activity_log issue rows via the generator module. The
UI lives under the Recurring tab on the client detail page.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

import policydb.config as cfg
from policydb.recurring_events import (
    advance_template_for_completion,
    compute_initial_next_occurrence,
    generate_due_recurring_instances,
    next_recurring_uid,
)
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/recurring-events")


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_int(value, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_template(conn, recurring_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM recurring_events WHERE id = ?", (recurring_id,)
    ).fetchone()
    return dict(row) if row else None


def _render_client_tab(request: Request, conn, client_id: int) -> HTMLResponse:
    """Render the client Recurring tab partial — used as HTMX swap target."""
    from policydb.web.routes.clients import client_tab_recurring  # local import to avoid cycles
    return client_tab_recurring(request=request, client_id=client_id, conn=conn)


# ─────────────────────────────────────────────────────────────────────────
# Slideover forms (new + edit)
# ─────────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def new_template_slideover(request: Request, client_id: int, conn=Depends(get_db)):
    """Return the create slideover bound to a specific client."""
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Client not found.</p>", status_code=404)

    policies = [dict(r) for r in conn.execute(
        "SELECT id, policy_uid, policy_type, carrier FROM policies "
        "WHERE client_id = ? AND archived = 0 ORDER BY policy_type, policy_uid",
        (client_id,),
    ).fetchall()]

    return templates.TemplateResponse("clients/_recurring_slideover.html", {
        "request": request,
        "template": None,
        "client": dict(client),
        "policies": policies,
        "cadences": cfg.get("recurring_event_cadences", []),
        "event_types": cfg.get("recurring_event_types", []),
        "severities": cfg.get("issue_severities", []),
        "today_iso": date.today().isoformat(),
    })


@router.get("/{recurring_id}/edit", response_class=HTMLResponse)
def edit_template_slideover(recurring_id: int, request: Request, conn=Depends(get_db)):
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)

    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (template["client_id"],)
    ).fetchone()
    policies = [dict(r) for r in conn.execute(
        "SELECT id, policy_uid, policy_type, carrier FROM policies "
        "WHERE client_id = ? AND archived = 0 ORDER BY policy_type, policy_uid",
        (template["client_id"],),
    ).fetchall()]

    return templates.TemplateResponse("clients/_recurring_slideover.html", {
        "request": request,
        "template": template,
        "client": dict(client) if client else {"id": template["client_id"], "name": ""},
        "policies": policies,
        "cadences": cfg.get("recurring_event_cadences", []),
        "event_types": cfg.get("recurring_event_types", []),
        "severities": cfg.get("issue_severities", []),
        "today_iso": date.today().isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────
# Create / update
# ─────────────────────────────────────────────────────────────────────────

@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
def create_template(
    request: Request,
    client_id: int = Form(...),
    name: str = Form(...),
    cadence: str = Form(...),
    start_date: str = Form(...),
    policy_id: int = Form(0),
    event_type: str = Form(""),
    interval_n: int = Form(1),
    day_of_week: str = Form(""),
    day_of_month: str = Form(""),
    lead_days: int = Form(0),
    end_date: str = Form(""),
    default_severity: str = Form("Normal"),
    subject_template: str = Form(""),
    details_template: str = Form(""),
    account_exec: str = Form(""),
    catch_up_mode: str = Form("collapse"),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    """Create a new recurring event template."""
    start = _parse_date(start_date) or date.today()
    end = _parse_date(end_date)
    dow = _parse_int(day_of_week)
    dom = _parse_int(day_of_month)
    next_occ = compute_initial_next_occurrence(start, cadence, dow, dom)

    uid = next_recurring_uid(conn)
    conn.execute(
        """
        INSERT INTO recurring_events (
            recurring_uid, client_id, policy_id, event_type, name,
            subject_template, details_template, default_severity,
            cadence, interval_n, day_of_week, day_of_month, lead_days,
            start_date, end_date, next_occurrence,
            account_exec, active, catch_up_mode, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            uid,
            client_id,
            policy_id or None,
            event_type or None,
            name,
            subject_template or None,
            details_template or None,
            default_severity or "Normal",
            cadence,
            max(1, int(interval_n or 1)),
            dow,
            dom,
            max(0, int(lead_days or 0)),
            start.isoformat(),
            end.isoformat() if end else None,
            next_occ.isoformat(),
            account_exec or None,
            catch_up_mode or "collapse",
            notes or None,
        ),
    )
    conn.commit()

    # Run the generator so any immediately-due instance appears without a refresh
    try:
        generate_due_recurring_instances(conn)
    except Exception:
        pass

    return _render_client_tab(request, conn, client_id)


@router.post("/{recurring_id}", response_class=HTMLResponse)
def update_template(
    recurring_id: int,
    request: Request,
    name: str = Form(...),
    cadence: str = Form(...),
    start_date: str = Form(...),
    policy_id: int = Form(0),
    event_type: str = Form(""),
    interval_n: int = Form(1),
    day_of_week: str = Form(""),
    day_of_month: str = Form(""),
    lead_days: int = Form(0),
    end_date: str = Form(""),
    default_severity: str = Form("Normal"),
    subject_template: str = Form(""),
    details_template: str = Form(""),
    account_exec: str = Form(""),
    catch_up_mode: str = Form("collapse"),
    notes: str = Form(""),
    conn=Depends(get_db),
):
    """Update an existing template. next_occurrence is NOT rewritten so the
    current cycle stays on its existing pointer; new cadence/DOW values take
    effect on the next _advance() call."""
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("Not found", status_code=404)

    dow = _parse_int(day_of_week)
    dom = _parse_int(day_of_month)
    end = _parse_date(end_date)
    start = _parse_date(start_date) or _parse_date(template.get("start_date")) or date.today()

    conn.execute(
        """
        UPDATE recurring_events
        SET name = ?, policy_id = ?, event_type = ?, cadence = ?, interval_n = ?,
            day_of_week = ?, day_of_month = ?, lead_days = ?, start_date = ?,
            end_date = ?, default_severity = ?, subject_template = ?,
            details_template = ?, account_exec = ?, catch_up_mode = ?, notes = ?
        WHERE id = ?
        """,
        (
            name,
            policy_id or None,
            event_type or None,
            cadence,
            max(1, int(interval_n or 1)),
            dow,
            dom,
            max(0, int(lead_days or 0)),
            start.isoformat(),
            end.isoformat() if end else None,
            default_severity or "Normal",
            subject_template or None,
            details_template or None,
            account_exec or None,
            catch_up_mode or "collapse",
            notes or None,
            recurring_id,
        ),
    )
    conn.commit()
    return _render_client_tab(request, conn, template["client_id"])


# ─────────────────────────────────────────────────────────────────────────
# Activate / deactivate / delete / manual advance
# ─────────────────────────────────────────────────────────────────────────

@router.post("/{recurring_id}/deactivate", response_class=HTMLResponse)
def deactivate_template(recurring_id: int, request: Request, conn=Depends(get_db)):
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("Not found", status_code=404)
    conn.execute("UPDATE recurring_events SET active = 0 WHERE id = ?", (recurring_id,))
    conn.commit()
    return _render_client_tab(request, conn, template["client_id"])


@router.post("/{recurring_id}/activate", response_class=HTMLResponse)
def activate_template(recurring_id: int, request: Request, conn=Depends(get_db)):
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("Not found", status_code=404)
    conn.execute("UPDATE recurring_events SET active = 1 WHERE id = ?", (recurring_id,))
    conn.commit()
    try:
        generate_due_recurring_instances(conn)
    except Exception:
        pass
    return _render_client_tab(request, conn, template["client_id"])


@router.post("/{recurring_id}/delete", response_class=HTMLResponse)
def delete_template(recurring_id: int, request: Request, conn=Depends(get_db)):
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("Not found", status_code=404)
    client_id = template["client_id"]
    conn.execute("DELETE FROM recurring_events WHERE id = ?", (recurring_id,))
    conn.commit()
    return _render_client_tab(request, conn, client_id)


@router.post("/{recurring_id}/advance", response_class=HTMLResponse)
def advance_template(recurring_id: int, request: Request, conn=Depends(get_db)):
    """Manually bump next_occurrence by one cadence step — lets the user skip
    an upcoming occurrence that hasn't been materialized yet."""
    from policydb.recurring_events import _advance, _parse
    template = _get_template(conn, recurring_id)
    if not template:
        return HTMLResponse("Not found", status_code=404)
    next_occ = _parse(template["next_occurrence"]) or date.today()
    advanced = _advance(
        next_occ,
        template["cadence"],
        template.get("interval_n") or 1,
        template.get("day_of_week"),
        template.get("day_of_month"),
    )
    conn.execute(
        "UPDATE recurring_events SET next_occurrence = ? WHERE id = ?",
        (advanced.isoformat(), recurring_id),
    )
    conn.commit()
    return _render_client_tab(request, conn, template["client_id"])


# ─────────────────────────────────────────────────────────────────────────
# Skip a materialized instance
# ─────────────────────────────────────────────────────────────────────────

@router.post("/instance/{issue_id}/skip", response_class=HTMLResponse)
def skip_instance(issue_id: int, request: Request, conn=Depends(get_db)):
    """Skip a recurring issue instance: close it as Withdrawn and advance the
    template. Thin wrapper around the standard issue resolve path."""
    row = conn.execute(
        "SELECT client_id, recurring_event_id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (issue_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    conn.execute(
        """
        UPDATE activity_log
        SET issue_status = 'Closed',
            resolution_type = 'Withdrawn',
            resolution_notes = COALESCE(NULLIF(resolution_notes, ''), 'Skipped — recurring event'),
            resolved_date = ?,
            auto_close_reason = 'recurring_skipped'
        WHERE id = ? AND item_kind = 'issue'
        """,
        (date.today().isoformat(), issue_id),
    )

    try:
        advance_template_for_completion(conn, issue_id)
    except Exception:
        pass

    conn.commit()
    return _render_client_tab(request, conn, row["client_id"])
