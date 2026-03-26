"""FastAPI application for PolicyDB web UI."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Generator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles

from policydb.db import get_connection

_req_logger = logging.getLogger("policydb.web.requests")

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="PolicyDB", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _attach_sqlite_log_handler():
    """Attach the SQLite logging handler after the app starts.

    This runs in the uvicorn worker process (important for --reload mode).
    """
    try:
        from policydb.logging_config import setup_logging, setup_sqlite_handler
        setup_logging()  # No-op if already configured (idempotent)
        setup_sqlite_handler()
    except Exception:
        pass  # Never block app startup
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Request logging middleware (pure ASGI — avoids BaseHTTPMiddleware) ────────
# BaseHTTPMiddleware re-chunks response bodies which causes h11
# "Too much data for declared Content-Length" on large TemplateResponse bodies.
# A pure ASGI middleware passes the response through without buffering.


class _RequestLoggingMiddleware:
    """Lightweight ASGI middleware for request logging.

    Unlike @app.middleware("http") (which uses BaseHTTPMiddleware and
    re-buffers response bodies), this passes responses through unmodified,
    avoiding Content-Length mismatches with h11.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if path.startswith("/static"):
            return await self.app(scope, receive, send)

        method = scope.get("method", "")
        start = time.perf_counter()
        status_code = 0

        async def _send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            if status_code >= 500:
                _req_logger.error(
                    "%s %s → %d (%.1fms)", method, path, status_code, duration_ms,
                    extra={"method": method, "path": path, "status_code": status_code, "duration_ms": duration_ms},
                )
            elif status_code >= 400:
                _req_logger.warning(
                    "%s %s → %d (%.1fms)", method, path, status_code, duration_ms,
                    extra={"method": method, "path": path, "status_code": status_code, "duration_ms": duration_ms},
                )
            else:
                _req_logger.info(
                    "%s %s → %d (%.1fms)", method, path, status_code, duration_ms,
                    extra={"method": method, "path": path, "status_code": status_code, "duration_ms": duration_ms},
                )


app.add_middleware(_RequestLoggingMiddleware)


# ── Template filters ──────────────────────────────────────────────────────────

def _fmt_currency(value) -> str:
    if value is None or value == 0:
        return "—"
    try:
        v = float(value)
        return f"${v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_currency_short(value) -> str:
    if value is None or value == 0:
        return "—"
    try:
        v = float(value)
        if abs(v) >= 1_000_000:
            return f"${v/1_000_000:.1f}M"
        if abs(v) >= 1_000:
            return f"${v/1_000:.0f}K"
        return f"${v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _urgency_class(urgency: str) -> str:
    return {
        "EXPIRED": "bg-red-100 text-red-700",
        "URGENT": "bg-orange-100 text-orange-700",
        "WARNING": "bg-amber-100 text-amber-700",
        "UPCOMING": "bg-blue-100 text-blue-700",
        "OK": "bg-green-100 text-green-700",
    }.get(urgency, "bg-gray-100 text-gray-600")


def _readiness_class(label: str) -> str:
    return {
        "CRITICAL": "bg-red-100 text-red-700",
        "AT RISK": "bg-amber-100 text-amber-700",
        "ON TRACK": "bg-blue-100 text-blue-700",
        "READY": "bg-green-100 text-green-700",
    }.get(label, "bg-gray-100 text-gray-600")


def _fmt_layer_notation(p) -> str:
    from policydb.analysis import layer_notation
    notation = layer_notation(
        p.get("limit_amount"),
        p.get("attachment_point"),
        p.get("participation_of"),
    )
    return notation or p.get("layer_position") or ""


def _dict_merge(d, extra: dict) -> dict:
    result = dict(d)
    result.update(extra)
    return result


def _path_quote(s: str) -> str:
    """URL-encode a string for use in a URL path segment (spaces → %20, not +)."""
    from urllib.parse import quote
    return quote(str(s), safe="")


def _safe_id(s: str) -> str:
    """Convert a string to a safe CSS/HTML id — only lowercase alphanumeric + hyphens."""
    import re
    return re.sub(r'[^a-z0-9]+', '-', str(s).lower()).strip('-')


def _fmt_hours(value) -> str:
    """:g strips trailing zeros: 1.0 → '1h', 1.5 → '1.5h', 0 → '—'."""
    if value is None or value == 0:
        return "—"
    try:
        return f"{float(value):g}h"
    except (TypeError, ValueError):
        return "—"


templates.env.filters["currency"] = _fmt_currency
templates.env.filters["currency_short"] = _fmt_currency_short
templates.env.filters["urgency_class"] = _urgency_class
templates.env.filters["layer_notation"] = _fmt_layer_notation
templates.env.filters["readiness_class"] = _readiness_class
templates.env.filters["dict_merge"] = _dict_merge
templates.env.filters["path_quote"] = _path_quote
templates.env.filters["safe_id"] = _safe_id
templates.env.filters["format_hours"] = _fmt_hours

# ── Template globals ─────────────────────────────────────────────────────────
from policydb import __version__ as _app_version
from policydb.utils import build_ref_tag as _build_ref_tag
templates.env.globals["app_version"] = _app_version
templates.env.globals["build_ref_tag"] = _build_ref_tag

import policydb.config as _cfg
def _followup_workload_thresholds():
    return _cfg.get("followup_workload_thresholds", {"warning": 3, "danger": 5})
templates.env.globals["followup_workload_thresholds"] = _followup_workload_thresholds

from policydb.utils import get_status_color as _get_status_color
templates.env.globals["get_status_color"] = _get_status_color

def _inbox_pending_count():
    """Jinja2 global — returns pending inbox count (opens own connection)."""
    try:
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0
templates.env.globals["inbox_pending_count"] = _inbox_pending_count


def _followup_badge_counts():
    """Jinja2 global — returns {act_now, nudge_due} for the nav badge.

    act_now  = follow-ups due/overdue where disposition accountability is my_action (or unset)
    nudge_due = follow-ups due/overdue where disposition accountability is waiting_external
    """
    from datetime import date
    try:
        conn = get_connection()
        today = date.today().isoformat()
        # Build accountability map from config
        dispositions = _cfg.get("follow_up_dispositions", [])
        waiting_labels = [
            d["label"] for d in dispositions
            if isinstance(d, dict) and d.get("accountability") == "waiting_external"
        ]
        # Total overdue/due follow-ups (undone, with a date <= today)
        rows = conn.execute(
            "SELECT disposition FROM activity_log "
            "WHERE follow_up_done = 0 AND follow_up_date IS NOT NULL AND follow_up_date <= ?",
            (today,),
        ).fetchall()
        conn.close()

        act_now = 0
        nudge_due = 0
        for row in rows:
            disp = row[0] if row[0] else ""
            if disp in waiting_labels:
                nudge_due += 1
            else:
                act_now += 1
        return {"act_now": act_now, "nudge_due": nudge_due}
    except Exception:
        return {"act_now": 0, "nudge_due": 0}
templates.env.globals["followup_badge_counts"] = _followup_badge_counts


# ── DB dependency ─────────────────────────────────────────────────────────────

def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ── Register routers ──────────────────────────────────────────────────────────
from policydb.web.routes import dashboard, clients, policies, activities, settings, reconcile, templates as tpl_routes, contacts, review, briefing, meetings, inbox, ref_lookup, compliance, action_center, logs, compose, import_history, geocoder as geocoder_routes, charts as charts_routes  # noqa: E402

app.include_router(dashboard.router)
app.include_router(clients.router)
app.include_router(policies.router)
app.include_router(activities.router)
app.include_router(settings.router)
app.include_router(reconcile.router)
app.include_router(tpl_routes.router)
app.include_router(contacts.router)
app.include_router(review.router)
app.include_router(briefing.router)
app.include_router(meetings.router)
app.include_router(inbox.router)
app.include_router(ref_lookup.router)
app.include_router(compliance.router)
app.include_router(action_center.router)
app.include_router(logs.router)
app.include_router(import_history.router)
app.include_router(compose.router)
app.include_router(geocoder_routes.router)
app.include_router(charts_routes.router)

# Static files (charts JS/CSS assets)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
