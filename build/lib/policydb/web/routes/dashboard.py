"""Dashboard and search routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from policydb import config as cfg
from policydb.queries import (
    get_all_followups,
    get_renewal_metrics,
    get_renewal_pipeline,
    full_text_search,
)
from policydb.web.app import get_db, templates

router = APIRouter()

URGENCY_ORDER = ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]


def _attach_client_ids(conn, rows: list[dict]) -> list[dict]:
    result = []
    for d in rows:
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name = ?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        result.append(d)
    return result


@router.get("/dashboard/pipeline", response_class=HTMLResponse)
def dashboard_pipeline(request: Request, window: int = 90, status: str = "", conn=Depends(get_db)):
    """HTMX partial: pipeline table for dashboard window/status filter."""
    rows = get_renewal_pipeline(conn, window_days=window, renewal_status=status or None)
    pipeline = _attach_client_ids(conn, [dict(p) for p in rows])
    return templates.TemplateResponse("policies/_pipeline_table.html", {
        "request": request,
        "pipeline": pipeline,
        "window": window,
        "status": status,
        "renewal_statuses": cfg.get("renewal_statuses"),
    })


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(get_db)):
    metrics = get_renewal_metrics(conn)
    pipeline = get_renewal_pipeline(conn, window_days=90)
    overdue, upcoming = get_all_followups(conn, window=30)

    urgent_count = metrics.get("URGENT", {}).get("count", 0) + metrics.get("EXPIRED", {}).get("count", 0)
    urgency_breakdown = [(u, metrics.get(u, {"count": 0, "premium": 0})) for u in URGENCY_ORDER]

    pipeline_dicts = _attach_client_ids(conn, [dict(p) for p in pipeline])

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active": "dashboard",
        "today": date.today().isoformat(),
        "metrics": metrics,
        "pipeline": pipeline_dicts,
        "overdue": overdue,
        "upcoming": upcoming,
        "urgent_count": urgent_count,
        "urgency_breakdown": urgency_breakdown,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "dash_window": 90,
        "dash_status": "",
    })


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", conn=Depends(get_db)):
    results = {"clients": [], "policies": [], "activities": []}
    if q.strip():
        raw = full_text_search(conn, q.strip())
        results = {k: [dict(r) for r in v] for k, v in raw.items()}
    total = sum(len(v) for v in results.values())
    return templates.TemplateResponse("search.html", {
        "request": request,
        "active": "",
        "q": q,
        "results": results,
        "total": total,
    })
