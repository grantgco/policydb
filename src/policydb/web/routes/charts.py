"""Chart Deck Builder routes."""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.db import get_connection
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/charts", tags=["charts"])

CHART_REGISTRY = [
    {"id": "premium_comparison", "title": "Premium Comparison", "category": "core", "type": "d3"},
    {"id": "schedule", "title": "Schedule of Insurance", "category": "core", "type": "html"},
    {"id": "tower", "title": "Tower / Layer Diagram", "category": "core", "type": "d3"},
    {"id": "carrier_breakdown", "title": "Carrier Breakdown", "category": "core", "type": "d3"},
    {"id": "rate_change", "title": "Rate Change Summary", "category": "core", "type": "d3"},
    {"id": "activity_timeline", "title": "Activity Timeline", "category": "core", "type": "d3"},
    {"id": "market_conditions", "title": "Market Conditions", "category": "core", "type": "d3"},
    {"id": "premium_history", "title": "Premium History Trend", "category": "core", "type": "d3"},
    {"id": "coverage_comparison", "title": "Coverage Comparison", "category": "core", "type": "html"},
    {"id": "exposure_trend", "title": "Exposure Trend", "category": "exposure", "type": "d3"},
    {"id": "normalized_premium", "title": "Normalized Premium", "category": "exposure", "type": "d3"},
    {"id": "observations", "title": "Key Observations", "category": "exposure", "type": "html"},
    {"id": "exposure_vs_premium", "title": "Exposure vs Premium", "category": "exposure", "type": "d3"},
]

_CHART_TITLE_MAP = {c["id"]: c["title"] for c in CHART_REGISTRY}
_CHART_TYPE_MAP = {c["id"]: c["type"] for c in CHART_REGISTRY}


# ── Route 1: Client Selector ─────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def chart_index(request: Request, conn=Depends(get_db)):
    """Client selector — pick a client to build a chart deck for."""
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    clients_list = [dict(r) for r in clients]
    return templates.TemplateResponse(
        "charts/index.html", {"request": request, "clients": clients_list}
    )


# ── Route 2: Deck Configurator ───────────────────────────────────────────────

@router.get("/{client_id}/deck", response_class=HTMLResponse)
async def deck_configurator(
    request: Request,
    client_id: int,
    type: str = "renewal-recap",
    conn=Depends(get_db),
):
    """Deck configurator — select charts and configure options."""
    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    # Fetch distinct policy types for this client (used by market conditions config)
    rows = conn.execute(
        "SELECT DISTINCT policy_type FROM policies "
        "WHERE client_id = ? AND policy_type IS NOT NULL AND policy_type != '' "
        "ORDER BY policy_type",
        (client_id,),
    ).fetchall()
    policy_types = [r["policy_type"] for r in rows]

    return templates.TemplateResponse(
        "charts/deck.html",
        {
            "request": request,
            "client": client,
            "charts": CHART_REGISTRY,
            "deck_type": type,
            "policy_types": policy_types,
        },
    )


# ── Route 3: Chart Viewer ────────────────────────────────────────────────────

@router.post("/{client_id}/deck/view", response_class=HTMLResponse)
async def deck_view(
    request: Request,
    client_id: int,
    conn=Depends(get_db),
):
    """Render the selected charts with data."""
    form = await request.form()

    client = conn.execute(
        "SELECT id, name FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)
    client = dict(client)

    selected_charts = form.getlist("selected_charts[]")
    if not selected_charts:
        selected_charts = form.getlist("selected_charts")

    # Import chart data functions
    from policydb.charts import (
        get_premium_comparison_data,
        get_schedule_data,
        get_tower_data,
        get_carrier_breakdown_data,
        get_rate_change_data,
        get_activity_timeline_data,
        get_premium_history_data,
        get_coverage_comparison_data,
        get_exposure_trend_data,
        get_normalized_premium_data,
        get_exposure_observations_data,
        get_exposure_vs_premium_data,
    )

    DATA_FUNCTIONS = {
        "premium_comparison": get_premium_comparison_data,
        "schedule": get_schedule_data,
        "tower": get_tower_data,
        "carrier_breakdown": get_carrier_breakdown_data,
        "rate_change": get_rate_change_data,
        "activity_timeline": get_activity_timeline_data,
        "premium_history": get_premium_history_data,
        "coverage_comparison": get_coverage_comparison_data,
        "exposure_trend": get_exposure_trend_data,
        "normalized_premium": get_normalized_premium_data,
        "observations": get_exposure_observations_data,
        "exposure_vs_premium": get_exposure_vs_premium_data,
    }

    chart_data = {}
    for chart_id in selected_charts:
        if chart_id == "market_conditions":
            # Parse market conditions form arrays
            lines = form.getlist("market__line[]")
            avg_pcts = form.getlist("market__avg_pct[]")
            notes = form.getlist("market__notes[]")
            market_rows = []
            for i, line in enumerate(lines):
                if not line:
                    continue
                try:
                    pct = float(avg_pcts[i]) if i < len(avg_pcts) and avg_pcts[i] else 0
                except (ValueError, IndexError):
                    pct = 0
                note = notes[i] if i < len(notes) else ""
                market_rows.append({"line": line, "avg_pct": pct, "notes": note})

            # Get actual rate change data for this client and merge
            actuals = get_rate_change_data(conn, client_id)
            actual_map = {a["policy_type"]: a["pct_change"] for a in actuals}
            combined_lines = []
            for mr in market_rows:
                combined_lines.append({
                    "line": mr["line"],
                    "market_avg_pct": mr["avg_pct"],
                    "actual_pct": actual_map.get(mr["line"]),
                    "notes": mr.get("notes", ""),
                })
            chart_data["market_conditions"] = {"lines": combined_lines}
        elif chart_id in DATA_FUNCTIONS:
            chart_data[chart_id] = DATA_FUNCTIONS[chart_id](conn, client_id)

    chart_titles = {cid: _CHART_TITLE_MAP.get(cid, cid) for cid in selected_charts}
    chart_types = {cid: _CHART_TYPE_MAP.get(cid, "html") for cid in selected_charts}

    return templates.TemplateResponse(
        "charts/view.html",
        {
            "request": request,
            "client": client,
            "selected_charts": selected_charts,
            "chart_data": chart_data,
            "chart_titles": chart_titles,
            "chart_types": chart_types,
        },
    )
