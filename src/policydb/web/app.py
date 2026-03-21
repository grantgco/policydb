"""FastAPI application for PolicyDB web UI."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from policydb.db import get_connection

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="PolicyDB", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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


# ── DB dependency ─────────────────────────────────────────────────────────────

def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ── Register routers ──────────────────────────────────────────────────────────
from policydb.web.routes import dashboard, clients, policies, activities, settings, reconcile, templates as tpl_routes, contacts, review, briefing, meetings, inbox, ref_lookup, compliance  # noqa: E402

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
