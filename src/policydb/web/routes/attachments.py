"""Universal Attachments routes — DevonThink links + local file uploads.

Supports attaching files to any record type: policy, client, activity,
rfi_bundle, rfi_item, kb_article, meeting, project, issue. Also exposes
a zip-all endpoint that packages every file attached to a record (and,
for rfi_bundle, every file attached to its items) as a downloadable
archive ready to send to a client.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

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

_VALID_RECORD_TYPES = {"policy", "client", "activity", "rfi_bundle", "rfi_item", "kb_article", "project", "issue"}


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


# ── Download all attachments as a single zip ────────────────────────────────
# NOTE: this route must be defined BEFORE the /api/attachments/{uid} routes
# below, otherwise FastAPI matches the {uid} pattern first with uid="zip".


def _slugify_folder(name: str, max_len: int = 40) -> str:
    """Make a filesystem-safe, short folder name from free text."""
    n = re.sub(r"[^\w\s-]", "", name or "").strip()
    n = re.sub(r"\s+", "_", n)
    return n[:max_len] or "Untitled"


@router.get("/api/attachments/zip")
def download_attachments_zip(
    record_type: str = Query(...),
    record_id: int = Query(...),
    conn=Depends(get_db),
):
    """Download all attachments linked to a record as a single zip.

    For record_type='rfi_bundle', also includes attachments from every
    item in the bundle — bundle-level files go under ``Bundle/`` and
    each item with attachments gets its own ``Item_NNN_<slug>/`` folder.
    DevonThink files are resolved via ``fetch_item_metadata`` and pulled
    from their on-disk path; files that can't be resolved are listed in
    ``MANIFEST.txt`` as UNAVAILABLE so the user can retrieve them by hand.
    """
    if record_type not in _VALID_RECORD_TYPES:
        return JSONResponse({"ok": False, "error": "Invalid record type"}, status_code=400)

    # Direct attachments on this record
    direct_rows = conn.execute(
        """SELECT a.*, ra.sort_order AS link_sort
           FROM attachments a
           JOIN record_attachments ra ON ra.attachment_id = a.id
           WHERE ra.record_type = ? AND ra.record_id = ?
           ORDER BY ra.sort_order, a.created_at""",
        (record_type, record_id),
    ).fetchall()

    # For rfi_bundle, also pull item-level attachments keyed by item
    item_groups: list[tuple[dict, list]] = []
    bundle_meta = None
    if record_type == "rfi_bundle":
        bundle_meta = conn.execute(
            "SELECT rfi_uid, title FROM client_request_bundles WHERE id = ?",
            (record_id,),
        ).fetchone()
        items = conn.execute(
            """SELECT id, description, sort_order
               FROM client_request_items
               WHERE bundle_id = ?
               ORDER BY sort_order, id""",
            (record_id,),
        ).fetchall()
        for item in items:
            att_rows = conn.execute(
                """SELECT a.*, ra.sort_order AS link_sort
                   FROM attachments a
                   JOIN record_attachments ra ON ra.attachment_id = a.id
                   WHERE ra.record_type = 'rfi_item' AND ra.record_id = ?
                   ORDER BY ra.sort_order, a.created_at""",
                (item["id"],),
            ).fetchall()
            if att_rows:
                item_groups.append((dict(item), att_rows))

    if not direct_rows and not item_groups:
        return JSONResponse({"ok": False, "error": "No attachments to download"}, status_code=404)

    # Build zip in memory
    buf = io.BytesIO()
    manifest_lines: list[str] = []
    used_names_by_folder: dict[str, set[str]] = {}

    def _uniq_name(folder: str, fname: str) -> str:
        used = used_names_by_folder.setdefault(folder, set())
        if fname not in used:
            used.add(fname)
            return fname
        base, ext = os.path.splitext(fname)
        i = 2
        while True:
            candidate = f"{base}_{i}{ext}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            i += 1

    def _add_attachment(zf: zipfile.ZipFile, folder: str, att: dict) -> None:
        """Add one attachment to the zip and append a manifest line."""
        raw_name = att.get("filename") or att.get("title") or "file"
        fname = _sanitize_filename(str(raw_name).strip() or "file")

        if att.get("source") == "local":
            fp = Path(att.get("file_path") or "")
            try:
                resolved = fp.resolve()
                if not str(resolved).startswith(str(_ATTACHMENTS_DIR.resolve())):
                    manifest_lines.append(f"  SKIPPED (invalid path): {fname}")
                    return
                if not resolved.exists() or not resolved.is_file():
                    manifest_lines.append(f"  SKIPPED (missing on disk): {fname}")
                    return
                uniq = _uniq_name(folder, fname)
                arcname = f"{folder}{uniq}" if folder else uniq
                zf.write(str(resolved), arcname=arcname)
                sz = att.get("file_size") or resolved.stat().st_size
                manifest_lines.append(f"  {arcname}  [Local, {_format_file_size(sz)}]")
            except Exception as exc:
                logger.warning("Zip: failed to add local attachment %s: %s", att.get("uid"), exc)
                manifest_lines.append(f"  SKIPPED (error): {fname}")
            return

        if att.get("source") == "devonthink":
            dt_uuid = att.get("dt_uuid")
            dt_url = att.get("dt_url") or ""
            meta = fetch_item_metadata(dt_uuid) if dt_uuid else None
            dt_path = (meta or {}).get("path") if meta else None
            display_title = att.get("title") or fname
            if not dt_path:
                manifest_lines.append(
                    f"  UNAVAILABLE: {display_title}  [DevonThink link: {dt_url}]"
                )
                return
            try:
                src = Path(dt_path)
                if not src.exists() or not src.is_file():
                    manifest_lines.append(
                        f"  UNAVAILABLE: {display_title}  [DevonThink path missing, link: {dt_url}]"
                    )
                    return
                dt_filename = _sanitize_filename((meta or {}).get("filename") or src.name or fname)
                uniq = _uniq_name(folder, dt_filename)
                arcname = f"{folder}{uniq}" if folder else uniq
                zf.write(str(src), arcname=arcname)
                sz = src.stat().st_size
                manifest_lines.append(f"  {arcname}  [DevonThink, {_format_file_size(sz)}]")
            except Exception as exc:
                logger.warning("Zip: failed to add DT attachment %s: %s", att.get("uid"), exc)
                manifest_lines.append(
                    f"  UNAVAILABLE: {display_title}  [Error: {exc}, link: {dt_url}]"
                )
            return

        # Unknown source — skip
        manifest_lines.append(f"  SKIPPED (unknown source): {fname}")

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Header
        if record_type == "rfi_bundle" and bundle_meta:
            manifest_lines.append(
                f"RFI: {bundle_meta['rfi_uid'] or ''} — {bundle_meta['title'] or ''}".strip(" —")
            )
        else:
            manifest_lines.append(f"{record_type.upper()} #{record_id}")
        manifest_lines.append("=" * 60)
        manifest_lines.append("")

        # Direct / bundle-level files
        if direct_rows:
            if record_type == "rfi_bundle":
                manifest_lines.append("Bundle-level files:")
                folder = "Bundle/"
            else:
                manifest_lines.append("Files:")
                folder = ""
            for r in direct_rows:
                _add_attachment(zf, folder, dict(r))
            manifest_lines.append("")

        # Item-level files (rfi_bundle only)
        for item_info, att_rows in item_groups:
            slug = _slugify_folder(item_info.get("description") or "Item")
            so = item_info.get("sort_order") or 0
            folder = f"Item_{so:03d}_{slug}/"
            manifest_lines.append(
                f"Item: {item_info.get('description') or '(no description)'}"
            )
            for r in att_rows:
                _add_attachment(zf, folder, dict(r))
            manifest_lines.append("")

        zf.writestr("MANIFEST.txt", "\n".join(manifest_lines).encode("utf-8"))

    buf.seek(0)
    data = buf.getvalue()

    # Download filename
    if record_type == "rfi_bundle" and bundle_meta:
        base_name = bundle_meta["rfi_uid"] or f"RFI_{record_id}"
        if bundle_meta["title"]:
            base_name = f"{base_name}_{_slugify_folder(bundle_meta['title'])}"
    else:
        base_name = f"{record_type}_{record_id}"
    download_name = f"{_sanitize_filename(base_name)}_files.zip"

    logger.info(
        "Zip download: %s/%s → %d bytes (%d direct, %d item groups)",
        record_type,
        record_id,
        len(data),
        len(direct_rows),
        len(item_groups),
    )

    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


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
