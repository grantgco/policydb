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


templates.env.filters["currency"] = _fmt_currency
templates.env.filters["currency_short"] = _fmt_currency_short
templates.env.filters["urgency_class"] = _urgency_class
templates.env.filters["layer_notation"] = _fmt_layer_notation
templates.env.filters["dict_merge"] = _dict_merge


# ── DB dependency ─────────────────────────────────────────────────────────────

def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ── Register routers ──────────────────────────────────────────────────────────
from policydb.web.routes import dashboard, clients, policies, activities, settings, reconcile, templates as tpl_routes  # noqa: E402

app.include_router(dashboard.router)
app.include_router(clients.router)
app.include_router(policies.router)
app.include_router(activities.router)
app.include_router(settings.router)
app.include_router(reconcile.router)
app.include_router(tpl_routes.router)
