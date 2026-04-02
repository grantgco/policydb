"""Universal Attachments routes — DevonThink links + local file uploads.

Supports attaching files to any record type: policy, client, activity,
rfi_bundle, kb_article, meeting, project.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import policydb.config as cfg
from policydb.db import next_attachment_uid
from policydb.devonthink import build_dt_url, fetch_item_metadata, is_devonthink_available, parse_dt_link
from policydb.web.app import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_ATTACHMENTS_DIR = Path.home() / ".policydb" / "files" / "attachments"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif", ".txt", ".md", ".csv",
}

_MIME_MAP = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}

_VALID_RECORD_TYPES = {"policy", "client", "activity", "rfi_bundle", "kb_article", "meeting", "project", "issue"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sanitize_filename(name: str, max_len: int = 100) -> str:
    name = re.sub(r'[/\\:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("_")
    if len(name) > max_len:
        base, ext = os.path.splitext(name)
        name = base[: max_len - len(ext)] + ext
    return name


def _file_type_info(mime_type: str, filename: str) -> dict:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return {"icon": "pdf", "color": "text-red-500", "label": "PDF"}
    elif ext in (".doc", ".docx"):
        return {"icon": "word", "color": "text-blue-500", "label": "Word"}
    elif ext in (".xls", ".xlsx"):
        return {"icon": "excel", "color": "text-green-600", "label": "Excel"}
    elif ext in (".ppt", ".pptx"):
        return {"icon": "ppt", "color": "text-orange-500", "label": "PowerPoint"}
    elif ext in (".jpg", ".jpeg", ".png", ".gif"):
        return {"icon": "image", "color": "text-purple-500", "label": "Image"}
    return {"icon": "file", "color": "text-gray-500", "label": "File"}


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def _get_attachments_for_record(conn, record_type: str, record_id: int) -> list[dict]:
    """Get all attachments linked to a specific record."""
    rows = conn.execute(
        """
        SELECT a.*, ra.id as link_id, ra.sort_order
        FROM attachments a
        JOIN record_attachments ra ON ra.attachment_id = a.id
        WHERE ra.record_type = ? AND ra.record_id = ?
        ORDER BY ra.sort_order, a.created_at DESC
        """,
        (record_type, record_id),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["file_info"] = _file_type_info(d.get("mime_type", ""), d.get("filename", ""))
        d["size_display"] = _format_file_size(d.get("file_size", 0))
        result.append(d)
    return result


# ── Create attachment from DevonThink link ───────────────────────────────────


@router.post("/api/attachments")
async def create_attachment_dt(request: Request, conn=Depends(get_db)):
    """Create an attachment from a DevonThink link."""
    data = await request.json()
    dt_input = data.get("dt_input", "").strip()
    record_type = data.get("record_type", "")
    record_id = data.get("record_id")
    category = data.get("category", "General")

    if not dt_input:
        return JSONResponse({"ok": False, "error": "No DevonThink link provided"}, status_code=400)

    uuid = parse_dt_link(dt_input)
    if not uuid:
        return JSONResponse({"ok": False, "error": "Could not parse DevonThink UUID from input"}, status_code=400)

    # Try to fetch metadata from DevonThink
    meta = fetch_item_metadata(uuid)

    uid = next_attachment_uid(conn)
    title = (meta or {}).get("name", f"DT Item {uuid[:8]}")
    dt_url = build_dt_url(uuid)
    filename = (meta or {}).get("filename", "")
    file_size = (meta or {}).get("size", 0)
    mime_type = (meta or {}).get("mime_type", "")

    conn.execute(
        """INSERT INTO attachments
        (uid, title, source, dt_uuid, dt_url, filename, file_size, mime_type, category)
        VALUES (?, ?, 'devonthink', ?, ?, ?, ?, ?, ?)""",
        (uid, title, uuid, dt_url, filename, file_size, mime_type, category),
    )

    attachment_id = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()["id"]

    # Link to record if provided
    if record_type in _VALID_RECORD_TYPES and record_id:
        conn.execute(
            "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, ?, ?)",
            (attachment_id, record_type, int(record_id)),
        )

    conn.commit()
    logger.info("Attachment created: %s (DevonThink) — %s", uid, title)

    return JSONResponse({
        "ok": True,
        "uid": uid,
        "attachment_id": attachment_id,
        "title": title,
        "dt_metadata_fetched": meta is not None,
    })


# ── Create attachment from local file upload ─────────────────────────────────


@router.post("/api/attachments/upload")
async def create_attachment_upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("General"),
    record_type: str = Form(""),
    record_id: int = Form(0),
    conn=Depends(get_db),
):
    """Create an attachment from a local file upload."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return JSONResponse({"ok": False, "error": f"File type {ext} not allowed"}, status_code=400)

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        return JSONResponse({"ok": False, "error": "File too large (50 MB max)"}, status_code=400)

    uid = next_attachment_uid(conn)
    safe_name = _sanitize_filename(file.filename or "document")
    stored_name = f"{uid}_{safe_name}"

    _ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = _ATTACHMENTS_DIR / stored_name
    with open(file_path, "wb") as f:
        f.write(content)

    mime_type = _MIME_MAP.get(ext, "application/octet-stream")
    display_title = title or os.path.splitext(file.filename or "Document")[0]

    conn.execute(
        """INSERT INTO attachments
        (uid, title, source, file_path, filename, file_size, mime_type, category)
        VALUES (?, ?, 'local', ?, ?, ?, ?, ?)""",
        (uid, display_title, str(file_path), file.filename, len(content), mime_type, category),
    )

    attachment_id = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()["id"]

    if record_type in _VALID_RECORD_TYPES and record_id:
        conn.execute(
            "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, ?, ?)",
            (attachment_id, record_type, int(record_id)),
        )

    conn.commit()
    logger.info("Attachment uploaded: %s — %s (%s)", uid, display_title, _format_file_size(len(content)))

    return JSONResponse({
        "ok": True,
        "uid": uid,
        "attachment_id": attachment_id,
        "title": display_title,
    })


# ── Search + Panel (must be before {uid} routes) ────────────────────────────


@router.get("/api/attachments/search")
async def search_attachments(q: str = Query(""), conn=Depends(get_db)):
    """Search attachments by title for the 'Link Existing' feature."""
    if not q.strip():
        return JSONResponse({"ok": True, "results": []})

    rows = conn.execute(
        "SELECT id, uid, title, source, mime_type, filename, file_size FROM attachments "
        "WHERE title LIKE ? ORDER BY updated_at DESC LIMIT 20",
        (f"%{q.strip()}%",),
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["file_info"] = _file_type_info(d.get("mime_type", ""), d.get("filename", ""))
        d["size_display"] = _format_file_size(d.get("file_size", 0))
        results.append(d)

    return JSONResponse({"ok": True, "results": results})


@router.get("/api/attachments/panel", response_class=HTMLResponse)
async def attachment_panel_partial(
    request: Request,
    record_type: str = Query(...),
    record_id: int = Query(...),
    conn=Depends(get_db),
):
    """Render the attachment panel partial for embedding in any page."""
    attachments = _get_attachments_for_record(conn, record_type, record_id)
    categories = cfg.get("attachment_categories", [])
    dt_available = is_devonthink_available()

    return templates.TemplateResponse("attachments/_attachments_panel.html", {
        "request": request,
        "attachments": attachments,
        "record_type": record_type,
        "record_id": record_id,
        "categories": categories,
        "dt_available": dt_available,
    })


# ── Get / Update / Delete attachment ─────────────────────────────────────────


@router.get("/api/attachments/{uid}")
async def get_attachment(uid: str, conn=Depends(get_db)):
    att = conn.execute("SELECT * FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not att:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    d = dict(att)
    d["file_info"] = _file_type_info(d.get("mime_type", ""), d.get("filename", ""))
    d["size_display"] = _format_file_size(d.get("file_size", 0))
    return JSONResponse({"ok": True, "attachment": d})


@router.patch("/api/attachments/{uid}")
async def update_attachment(uid: str, request: Request, conn=Depends(get_db)):
    att = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not att:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    data = await request.json()
    updates = []
    params = []
    for field in ("title", "category", "description"):
        if field in data:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if updates:
        params.append(att["id"])
        conn.execute(f"UPDATE attachments SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    return JSONResponse({"ok": True})


@router.delete("/api/attachments/{uid}")
async def delete_attachment(uid: str, conn=Depends(get_db)):
    att = conn.execute("SELECT id, source, file_path FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not att:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # Remove local file if it exists
    if att["source"] == "local" and att["file_path"]:
        fp = Path(att["file_path"])
        if fp.exists():
            fp.unlink()

    # Cascade: record_attachments deleted by FK ON DELETE CASCADE
    conn.execute("DELETE FROM attachments WHERE id = ?", (att["id"],))
    conn.commit()
    logger.info("Attachment deleted: %s", uid)
    return JSONResponse({"ok": True})


# ── Download local file ──────────────────────────────────────────────────────


@router.get("/attachments/{uid}/download")
async def download_attachment(uid: str, conn=Depends(get_db)):
    att = conn.execute(
        "SELECT file_path, filename, mime_type, source FROM attachments WHERE uid = ?", (uid,)
    ).fetchone()
    if not att or att["source"] != "local" or not att["file_path"]:
        return RedirectResponse("/", status_code=303)

    file_path = Path(att["file_path"]).resolve()
    if not str(file_path).startswith(str(_ATTACHMENTS_DIR.resolve())):
        return JSONResponse({"ok": False, "error": "Invalid file path"}, status_code=403)
    if not file_path.exists():
        return JSONResponse({"ok": False, "error": "File not found on disk"}, status_code=404)

    return FileResponse(
        path=str(file_path),
        filename=att["filename"],
        media_type=att["mime_type"],
    )


# ── Record attachment links ──────────────────────────────────────────────────


@router.post("/api/record-attachments")
async def link_attachment(request: Request, conn=Depends(get_db)):
    """Link an existing attachment to a record."""
    data = await request.json()
    attachment_id = data.get("attachment_id")
    record_type = data.get("record_type", "")
    record_id = data.get("record_id")

    if not attachment_id or record_type not in _VALID_RECORD_TYPES or not record_id:
        return JSONResponse({"ok": False, "error": "Missing required fields"}, status_code=400)

    # Verify attachment exists
    att = conn.execute("SELECT uid FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if not att:
        return JSONResponse({"ok": False, "error": "Attachment not found"}, status_code=404)

    conn.execute(
        "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, ?, ?)",
        (int(attachment_id), record_type, int(record_id)),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@router.delete("/api/record-attachments/{link_id}")
async def unlink_attachment(link_id: int, conn=Depends(get_db)):
    """Remove a record-attachment link (detach). Does not delete the attachment itself."""
    conn.execute("DELETE FROM record_attachments WHERE id = ?", (link_id,))
    conn.commit()
    return JSONResponse({"ok": True})


@router.get("/api/record-attachments")
async def list_record_attachments(
    record_type: str = Query(...),
    record_id: int = Query(...),
    conn=Depends(get_db),
):
    """List attachments for a specific record."""
    attachments = _get_attachments_for_record(conn, record_type, record_id)
    return JSONResponse({"ok": True, "attachments": attachments})


# ── Search attachments ───────────────────────────────────────────────────────


# ── Refresh DevonThink metadata ──────────────────────────────────────────────


@router.post("/api/attachments/{uid}/refresh-dt")
async def refresh_dt_metadata(uid: str, conn=Depends(get_db)):
    """Re-fetch metadata from DevonThink for a DT-linked attachment."""
    att = conn.execute("SELECT id, dt_uuid, source FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not att:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    if att["source"] != "devonthink" or not att["dt_uuid"]:
        return JSONResponse({"ok": False, "error": "Not a DevonThink attachment"}, status_code=400)

    meta = fetch_item_metadata(att["dt_uuid"])
    if not meta:
        return JSONResponse({"ok": False, "error": "Could not reach DevonThink"}, status_code=503)

    conn.execute(
        """UPDATE attachments SET
            title = ?, filename = ?, file_size = ?, mime_type = ?
        WHERE id = ?""",
        (meta["name"], meta["filename"], meta["size"], meta["mime_type"], att["id"]),
    )
    conn.commit()

    return JSONResponse({"ok": True, "title": meta["name"], "filename": meta["filename"]})
