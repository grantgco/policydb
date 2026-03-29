"""Anomaly detection routes — acknowledge, refresh, list."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from policydb.anomaly_engine import (
    acknowledge_anomaly,
    get_all_active_anomalies,
    get_anomaly_counts,
    scan_anomalies,
)
from policydb.web.app import get_db, templates

router = APIRouter()


@router.post("/anomalies/{anomaly_id}/acknowledge")
def ack_anomaly(anomaly_id: int, conn=Depends(get_db)):
    """Acknowledge a single anomaly finding."""
    acknowledge_anomaly(conn, anomaly_id)
    # Return refreshed widget
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
    return {"ok": True, "counts": counts, "total": sum(counts.values())}


@router.post("/anomalies/refresh", response_class=HTMLResponse)
def refresh_anomalies(request: Request, conn=Depends(get_db)):
    """Re-run anomaly scan and return refreshed widget."""
    scan_anomalies(conn)
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
    return templates.TemplateResponse("action_center/_anomalies_widget.html", {
        "request": request,
        "anomaly_counts": counts,
        "anomalies": anomalies,
    })


@router.get("/anomalies/widget", response_class=HTMLResponse)
def anomalies_widget(request: Request, conn=Depends(get_db)):
    """Return anomalies widget partial for sidebar."""
    counts = get_anomaly_counts(conn)
    anomalies = get_all_active_anomalies(conn)
    return templates.TemplateResponse("action_center/_anomalies_widget.html", {
        "request": request,
        "anomaly_counts": counts,
        "anomalies": anomalies,
    })
