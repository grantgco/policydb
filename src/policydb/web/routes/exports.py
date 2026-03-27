"""Data Export page — browse tables/views and download as CSV, XLSX, or JSON."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from openpyxl import Workbook

from policydb.exporter import _write_sheet, _wb_to_bytes
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/exports", tags=["exports"])

# Tables excluded from the export list (internal bookkeeping)
_EXCLUDE_TABLES = {
    "schema_version", "sqlite_sequence", "app_log", "audit_log",
}

_VALID_FORMATS = {"csv", "xlsx", "json"}


def _get_sources(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Introspect SQLite for exportable tables and views."""
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    views = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()
    return {
        "views": [r[0] for r in views],
        "tables": [r[0] for r in tables if r[0] not in _EXCLUDE_TABLES],
    }


def _validate_source(conn: sqlite3.Connection, name: str) -> bool:
    """Check source name exists in sqlite_master (prevents injection)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (name,),
    ).fetchone()
    return row is not None


def _get_columns(conn: sqlite3.Connection, source: str) -> list[str]:
    """Get column names for a table or view."""
    info = conn.execute(f"PRAGMA table_info([{source}])").fetchall()  # noqa: S608
    return [r[1] for r in info]


# ── Main page ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def exports_page(request: Request, conn=Depends(get_db)):
    sources = _get_sources(conn)
    return templates.TemplateResponse("exports/index.html", {
        "request": request,
        "active": "exports",
        "sources": sources,
    })


# ── Preview partial (HTMX) ──────────────────────────────────────────────────

@router.get("/preview", response_class=HTMLResponse)
def exports_preview(
    request: Request,
    source: str = Query(""),
    limit: int = Query(25, ge=1, le=500),
    conn=Depends(get_db),
):
    if not source or not _validate_source(conn, source):
        return HTMLResponse('<p class="text-gray-400 text-sm">Invalid source.</p>')

    columns = _get_columns(conn, source)
    total = conn.execute(f"SELECT COUNT(*) FROM [{source}]").fetchone()[0]  # noqa: S608
    rows = conn.execute(f"SELECT * FROM [{source}] LIMIT ?", (limit,)).fetchall()  # noqa: S608
    rows_dicts = [dict(r) for r in rows]

    return templates.TemplateResponse("exports/_preview.html", {
        "request": request,
        "source": source,
        "columns": columns,
        "rows": rows_dicts,
        "total": total,
        "limit": limit,
    })


# ── Download endpoint ────────────────────────────────────────────────────────

@router.get("/download")
def exports_download(
    source: str = Query(""),
    format: str = Query("csv"),
    columns: str = Query(""),
    conn=Depends(get_db),
):
    if not source or not _validate_source(conn, source):
        return Response("Invalid source", status_code=400)
    if format not in _VALID_FORMATS:
        return Response("Invalid format. Use csv, xlsx, or json.", status_code=400)

    # Validate and filter columns
    all_cols = _get_columns(conn, source)
    if columns:
        requested = [c.strip() for c in columns.split(",") if c.strip()]
        selected = [c for c in requested if c in all_cols]
        if not selected:
            selected = all_cols
    else:
        selected = all_cols

    col_list = ", ".join(f"[{c}]" for c in selected)
    rows = conn.execute(f"SELECT {col_list} FROM [{source}]").fetchall()  # noqa: S608
    rows_dicts = [dict(r) for r in rows]

    today = date.today().isoformat()
    safe_name = source.lower().replace(" ", "_")

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(selected)
        for row in rows_dicts:
            writer.writerow([row.get(c, "") for c in selected])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_{today}.csv"'},
        )

    if format == "xlsx":
        wb = Workbook()
        wb.remove(wb.active)  # remove default empty sheet
        _write_sheet(wb, source[:31], rows_dicts)  # sheet title max 31 chars
        return Response(
            content=_wb_to_bytes(wb),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_{today}.xlsx"'},
        )

    # JSON
    return Response(
        content=json.dumps(rows_dicts, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_{today}.json"'},
    )
