"""Pinned notes — persistent, color-coded alerts on client/policy/project pages."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.web.app import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pinned-notes", tags=["pinned-notes"])

VALID_SCOPES = {"client", "policy", "project"}
VALID_COLORS = {"red", "amber", "blue", "green"}


# ── Query helpers ────────────────────────────────────────────────────────────


def get_pinned_notes(conn: sqlite3.Connection, scope: str, scope_id: str) -> list[dict]:
    """Get notes for a single scope/id, ordered by sort_order."""
    rows = conn.execute(
        "SELECT * FROM pinned_notes WHERE scope = ? AND scope_id = ? ORDER BY sort_order, created_at",
        (scope, str(scope_id)),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pinned_notes_with_cascade(
    conn: sqlite3.Connection,
    scope: str,
    scope_id: str,
    client_id: int | str | None = None,
) -> list[dict]:
    """Get own notes + cascaded client notes. Adds 'cascaded' flag to each row."""
    own = get_pinned_notes(conn, scope, str(scope_id))
    for n in own:
        n["cascaded"] = False

    cascaded = []
    if scope in ("policy", "project") and client_id:
        cascaded = get_pinned_notes(conn, "client", str(client_id))
        for n in cascaded:
            n["cascaded"] = True

    return own + cascaded


# ── Banner rendering helper ──────────────────────────────────────────────────


def _render_banner(
    request: Request,
    conn: sqlite3.Connection,
    scope: str,
    scope_id: str,
    client_id: int | str | None = None,
) -> HTMLResponse:
    notes = get_pinned_notes_with_cascade(conn, scope, scope_id, client_id)
    html = templates.get_template("_pinned_notes_banner.html").render(
        pinned_notes=notes,
        pinned_scope=scope,
        pinned_scope_id=str(scope_id),
        pinned_client_id=str(client_id) if client_id else "",
    )
    return HTMLResponse(html)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def list_pinned_notes(
    request: Request,
    scope: str = Query(...),
    scope_id: str = Query(...),
    client_id: str = Query(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Render the pinned notes banner partial."""
    if scope not in VALID_SCOPES:
        return HTMLResponse("")
    return _render_banner(request, conn, scope, scope_id, client_id or None)


@router.post("", response_class=HTMLResponse)
def create_pinned_note(
    request: Request,
    scope: str = Form(...),
    scope_id: str = Form(...),
    client_id: str = Form(""),
    headline: str = Form(...),
    detail: str = Form(""),
    color: str = Form("amber"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create a new pinned note."""
    if scope not in VALID_SCOPES:
        return HTMLResponse("Invalid scope", status_code=400)
    if color not in VALID_COLORS:
        color = "amber"
    headline = headline.strip()
    if not headline:
        return HTMLResponse("Headline required", status_code=400)

    # Get next sort_order
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM pinned_notes WHERE scope = ? AND scope_id = ?",
        (scope, scope_id),
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO pinned_notes (scope, scope_id, headline, detail, color, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
        (scope, scope_id, headline, detail.strip(), color, max_order + 1),
    )
    conn.commit()
    logger.info("Pinned note created: scope=%s scope_id=%s headline=%s", scope, scope_id, headline[:60])

    return _render_banner(request, conn, scope, scope_id, client_id or None)


@router.patch("/{note_id}", response_class=HTMLResponse)
def update_pinned_note(
    request: Request,
    note_id: int,
    scope: str = Form(""),
    scope_id: str = Form(""),
    client_id: str = Form(""),
    headline: str | None = Form(None),
    detail: str | None = Form(None),
    color: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Update a pinned note field (headline, detail, or color)."""
    row = conn.execute("SELECT * FROM pinned_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    updates = []
    params = []
    if headline is not None:
        headline = headline.strip()
        if headline:
            updates.append("headline = ?")
            params.append(headline)
    if detail is not None:
        updates.append("detail = ?")
        params.append(detail.strip())
    if color is not None and color in VALID_COLORS:
        updates.append("color = ?")
        params.append(color)

    if updates:
        params.append(note_id)
        conn.execute(f"UPDATE pinned_notes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    # Re-read to get scope info for re-render
    row = conn.execute("SELECT * FROM pinned_notes WHERE id = ?", (note_id,)).fetchone()
    use_scope = scope or row["scope"]
    use_scope_id = scope_id or str(row["scope_id"])

    return _render_banner(request, conn, use_scope, use_scope_id, client_id or None)


@router.delete("/{note_id}", response_class=HTMLResponse)
def delete_pinned_note(
    request: Request,
    note_id: int,
    scope: str = Query(""),
    scope_id: str = Query(""),
    client_id: str = Query(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Delete a pinned note."""
    row = conn.execute("SELECT * FROM pinned_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    use_scope = scope or row["scope"]
    use_scope_id = scope_id or str(row["scope_id"])

    conn.execute("DELETE FROM pinned_notes WHERE id = ?", (note_id,))
    conn.commit()
    logger.info("Pinned note deleted: id=%s scope=%s scope_id=%s", note_id, use_scope, use_scope_id)

    return _render_banner(request, conn, use_scope, use_scope_id, client_id or None)
