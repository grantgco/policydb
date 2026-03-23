"""Logs viewer — app_log and audit_log in a unified tabbed page."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg

router = APIRouter(prefix="/logs", tags=["logs"])


# ── Main page ─────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def logs_page(request: Request, tab: str = "app"):
    retention = cfg.get("log_retention_days", 730)
    return templates.TemplateResponse("logs/index.html", {
        "request": request,
        "active": "logs",
        "default_tab": tab,
        "retention_days": retention,
    })


# ── App Log tab (HTMX partial) ───────────────────────────────────────────────

_APP_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@router.get("/app", response_class=HTMLResponse)
def app_log_partial(
    request: Request,
    conn=Depends(get_db),
    level: str = Query("", alias="level"),
    path_contains: str = Query("", alias="path"),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    search: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
):
    per_page = 100
    offset = (page - 1) * per_page

    clauses: list[str] = []
    params: list = []

    if level and level in _APP_LEVELS:
        clauses.append("level = ?")
        params.append(level)
    if path_contains:
        clauses.append("path LIKE ?")
        params.append(f"%{path_contains}%")
    if date_from:
        clauses.append("logged_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("logged_at <= ? || ' 23:59:59'")
        params.append(date_to)
    if search:
        clauses.append("(message LIKE ? OR path LIKE ? OR logger_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    try:
        rows = conn.execute(
            f"SELECT * FROM app_log {where} ORDER BY logged_at DESC LIMIT ? OFFSET ?",  # noqa: S608
            params + [per_page, offset],
        ).fetchall()
    except Exception:
        rows = []

    # Summary stats
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM app_log {where}", params).fetchone()[0]  # noqa: S608
    except Exception:
        total = 0

    try:
        errors_today = conn.execute(
            "SELECT COUNT(*) FROM app_log WHERE level IN ('ERROR','CRITICAL') AND logged_at >= date('now')"
        ).fetchone()[0]
    except Exception:
        errors_today = 0

    try:
        avg_duration = conn.execute(
            "SELECT AVG(duration_ms) FROM app_log WHERE duration_ms IS NOT NULL AND logged_at >= date('now')"
        ).fetchone()[0]
    except Exception:
        avg_duration = None

    return templates.TemplateResponse("logs/_app_log_table.html", {
        "request": request,
        "rows": rows,
        "total": total,
        "errors_today": errors_today,
        "avg_duration": round(avg_duration, 1) if avg_duration else None,
        "levels": _APP_LEVELS,
        "f_level": level,
        "f_path": path_contains,
        "f_from": date_from,
        "f_to": date_to,
        "f_search": search,
        "page": page,
        "per_page": per_page,
        "has_next": len(rows) == per_page,
    })


# ── Audit Log tab (HTMX partial) ─────────────────────────────────────────────

_AUDIT_TABLES = [
    "clients", "policies", "activity_log", "contacts",
    "inbox", "policy_milestones", "saved_notes",
]
_AUDIT_OPERATIONS = ["INSERT", "UPDATE", "DELETE"]


@router.get("/audit", response_class=HTMLResponse)
def audit_log_partial(
    request: Request,
    conn=Depends(get_db),
    table_name: str = Query("", alias="table"),
    operation: str = Query("", alias="op"),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    page: int = Query(1, ge=1),
):
    per_page = 100
    offset = (page - 1) * per_page

    clauses: list[str] = []
    params: list = []

    if table_name and table_name in _AUDIT_TABLES:
        clauses.append("table_name = ?")
        params.append(table_name)
    if operation and operation in _AUDIT_OPERATIONS:
        clauses.append("operation = ?")
        params.append(operation)
    if date_from:
        clauses.append("changed_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("changed_at <= ? || ' 23:59:59'")
        params.append(date_to)

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    try:
        rows = conn.execute(
            f"SELECT id, table_name, row_id, operation, old_values, new_values, "  # noqa: S608
            f"changed_at, changed_by FROM audit_log {where} "
            f"ORDER BY changed_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
    except Exception:
        rows = []

    try:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]  # noqa: S608
    except Exception:
        total = 0

    return templates.TemplateResponse("logs/_audit_log_table.html", {
        "request": request,
        "rows": rows,
        "total": total,
        "tables": _AUDIT_TABLES,
        "operations": _AUDIT_OPERATIONS,
        "f_table": table_name,
        "f_op": operation,
        "f_from": date_from,
        "f_to": date_to,
        "page": page,
        "per_page": per_page,
        "has_next": len(rows) == per_page,
    })
