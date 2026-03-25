"""Import history routes — view import sessions and source profiles."""

from __future__ import annotations

import logging
logger = logging.getLogger("policydb.web.routes.import_history")

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb import config as cfg
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

    # Build HTML (inline for simplicity — no separate template needed for MVP)
    parts = [
        '<div class="max-w-5xl mx-auto px-4 py-6">',
        '<h1 class="text-xl font-bold text-gray-900 mb-6">Import History</h1>',
    ]

    # Source profiles section
    if profiles:
        parts.append('<div class="card p-4 mb-6">')
        parts.append('<h2 class="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Source Profiles</h2>')
        parts.append('<div class="overflow-x-auto"><table class="w-full text-xs">')
        parts.append('<thead><tr class="border-b border-gray-200 text-gray-500 text-left">')
        for h in ["Source", "Type", "Uses", "Last Used", "Column Mappings"]:
            parts.append(f'<th class="py-2 px-2">{h}</th>')
        parts.append('</tr></thead><tbody>')
        for p in profiles:
            import json
            col_map = {}
            try:
                col_map = json.loads(p.get("column_map") or "{}")
            except Exception:
                pass
            map_count = len(col_map)
            last_used = (p.get("last_used") or "")[:10]
            parts.append('<tr class="border-b border-gray-100 hover:bg-gray-50">')
            parts.append(f'<td class="py-1.5 px-2 font-medium text-gray-800">{p["source_name"]}</td>')
            parts.append(f'<td class="py-1.5 px-2 text-gray-500">{p.get("source_type", "")}</td>')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums">{p.get("use_count", 0)}</td>')
            parts.append(f'<td class="py-1.5 px-2 text-gray-500">{last_used}</td>')
            parts.append(f'<td class="py-1.5 px-2">')
            if map_count:
                parts.append(f'<span class="text-green-600">{map_count} fields mapped</span>')
            else:
                parts.append('<span class="text-gray-400">None</span>')
            parts.append('</td></tr>')
        parts.append('</tbody></table></div></div>')

    # Sessions section
    parts.append('<div class="card p-4">')
    parts.append('<h2 class="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">Recent Sessions</h2>')
    if not sessions:
        parts.append('<p class="text-sm text-gray-400">No import sessions yet. Upload a file via the Reconcile page to get started.</p>')
    else:
        parts.append('<div class="overflow-x-auto"><table class="w-full text-xs">')
        parts.append('<thead><tr class="border-b border-gray-200 text-gray-500 text-left">')
        for h in ["Date", "Source", "Client", "File", "Rows", "Matched", "Created", "Updated", "Status"]:
            parts.append(f'<th class="py-2 px-2">{h}</th>')
        parts.append('</tr></thead><tbody>')
        for s in sessions:
            imported = (s.get("imported_at") or "")[:16]
            status = s.get("status", "")
            status_cls = {
                "completed": "text-green-700 bg-green-50",
                "in_progress": "text-blue-700 bg-blue-50",
                "cancelled": "text-gray-500 bg-gray-50",
            }.get(status, "text-gray-500 bg-gray-50")
            parts.append('<tr class="border-b border-gray-100 hover:bg-gray-50">')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums text-gray-500">{imported}</td>')
            parts.append(f'<td class="py-1.5 px-2 font-medium text-gray-800">{s.get("source_name", "")}</td>')
            parts.append(f'<td class="py-1.5 px-2">{s.get("client_name", "")}</td>')
            parts.append(f'<td class="py-1.5 px-2 text-gray-500 truncate max-w-[150px]" title="{s.get("file_name", "")}">{s.get("file_name", "")}</td>')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums">{s.get("row_count", 0)}</td>')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums">{s.get("matched_count", 0)}</td>')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums">{s.get("created_count", 0)}</td>')
            parts.append(f'<td class="py-1.5 px-2 tabular-nums">{s.get("updated_count", 0)}</td>')
            parts.append(f'<td class="py-1.5 px-2"><span class="px-1.5 py-0.5 rounded text-[10px] font-medium {status_cls}">{status}</span></td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div>')
    parts.append('</div></div>')

    return HTMLResponse("\n".join(parts))
