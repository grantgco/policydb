"""Import history routes — view import sessions and source profiles."""

from __future__ import annotations

import json
import logging
logger = logging.getLogger("policydb.web.routes.import_history")

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates

router = APIRouter(prefix="/import-history")


@router.get("", response_class=HTMLResponse)
def import_history_index(request: Request, conn=Depends(get_db)):
    """Import history page — lists recent sessions and source profiles."""
    from policydb.import_ledger import get_recent_sessions, get_all_source_profiles

    sessions = get_recent_sessions(conn, limit=50)
    profiles = get_all_source_profiles(conn)

    # Enrich sessions with client names
    client_ids = {s["client_id"] for s in sessions if s.get("client_id")}
    client_names = {}
    if client_ids:
        rows = conn.execute(
            f"SELECT id, name FROM clients WHERE id IN ({','.join('?' * len(client_ids))})",
            list(client_ids),
        ).fetchall()
        client_names = {r["id"]: r["name"] for r in rows}

    for s in sessions:
        s["client_name"] = client_names.get(s.get("client_id"), "")

    # Enrich profiles with column map count
    for p in profiles:
        try:
            col_map = json.loads(p.get("column_map") or "{}")
            p["_map_count"] = len(col_map)
        except Exception:
            p["_map_count"] = 0

    return templates.TemplateResponse("import_history.html", {
        "request": request,
        "active": "import-history",
        "sessions": sessions,
        "profiles": profiles,
    })
