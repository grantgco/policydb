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


_FORBIDDEN_FS_CHARS = re.compile(r'[/\\:*?"<>|]')


def _friendly_folder_name(name: str, max_len: int = 60) -> str:
    """Make a client-facing, human-readable folder name from free text.

    Preserves spaces, capitalization, punctuation like ``-`` and ``&`` —
    only strips characters that are forbidden on Windows/macOS filesystems,
    then collapses whitespace and trims to ``max_len``.
    """
    n = _FORBIDDEN_FS_CHARS.sub("", name or "")
    n = re.sub(r"\s+", " ", n).strip(" .")
    if len(n) > max_len:
        n = n[:max_len].rstrip(" .")
    return n or "Untitled"


# Kept as an alias so call sites that want filesystem-safe filenames
# (e.g. the download filename in the Content-Disposition header) still work.
def _slugify_folder(name: str, max_len: int = 40) -> str:
    """Make a filesystem-safe, short folder/file name from free text (underscored)."""
    n = re.sub(r"[^\w\s-]", "", name or "").strip()
    n = re.sub(r"\s+", "_", n)
    return n[:max_len] or "Untitled"


def _rfi_item_folder(item: dict) -> str:
    """Build a nested folder path for an RFI item's attachments.

    Layout: ``{Project}/{Coverage Line}/{NNN} - {description}/`` — mirrors
    the tabbed worksheet the client receives so they can match each
    attachment to its row.  Uses client-friendly folder names (spaces,
    capitals preserved). Falls back to ``Shared`` / ``General`` when the
    item has no project or coverage line.
    """
    project = (
        item.get("project_name")
        or item.get("policy_project_name")
        or "Shared"
    )
    coverage = (
        item.get("policy_coverage_line")
        or item.get("category")
        or "General"
    )
    desc = _friendly_folder_name(item.get("description") or "Item", max_len=50)
    so = item.get("sort_order") or 0
    return (
        f"{_friendly_folder_name(project, max_len=50)}/"
        f"{_friendly_folder_name(coverage, max_len=50)}/"
        f"{so:03d} - {desc}/"
    )


def _zip_name_allocator() -> tuple[dict, callable]:
    """Return a ``(state, alloc)`` pair where ``alloc(folder, fname)``
    returns a unique filename within ``folder``, de-duplicating collisions
    by appending ``_2``, ``_3``, … before the extension.
    """
    used: dict[str, set[str]] = {}

    def alloc(folder: str, fname: str) -> str:
        bucket = used.setdefault(folder, set())
        if fname not in bucket:
            bucket.add(fname)
            return fname
        base, ext = os.path.splitext(fname)
        i = 2
        while True:
            candidate = f"{base}_{i}{ext}"
            if candidate not in bucket:
                bucket.add(candidate)
                return candidate
            i += 1

    return used, alloc


def _add_attachment_to_zip(
    zf: zipfile.ZipFile,
    folder: str,
    att: dict,
    alloc,
) -> None:
    """Add one attachment to ``zf`` under ``folder``. Silently skips
    files that are missing on disk or have an unknown source — the
    companion xlsx still lists them, so the user's workbook remains
    complete even when physical files are unavailable.
    """
    raw_name = att.get("filename") or att.get("title") or "file"
    fname = _sanitize_filename(str(raw_name).strip() or "file")

    if att.get("source") == "local":
        fp = Path(att.get("file_path") or "")
        try:
            resolved = fp.resolve()
            if not str(resolved).startswith(str(_ATTACHMENTS_DIR.resolve())):
                return
            if not resolved.exists() or not resolved.is_file():
                return
            uniq = alloc(folder, fname)
            arcname = f"{folder}{uniq}" if folder else uniq
            zf.write(str(resolved), arcname=arcname)
        except Exception as exc:
            logger.warning("Zip: failed to add local attachment %s: %s", att.get("uid"), exc)
        return

    if att.get("source") == "devonthink":
        dt_uuid = att.get("dt_uuid")
        meta = fetch_item_metadata(dt_uuid) if dt_uuid else None
        dt_path = (meta or {}).get("path") if meta else None
        if not dt_path:
            return
        try:
            src = Path(dt_path)
            if not src.exists() or not src.is_file():
                return
            dt_filename = _sanitize_filename((meta or {}).get("filename") or src.name or fname)
            uniq = alloc(folder, dt_filename)
            arcname = f"{folder}{uniq}" if folder else uniq
            zf.write(str(src), arcname=arcname)
        except Exception as exc:
            logger.warning("Zip: failed to add DT attachment %s: %s", att.get("uid"), exc)


def _add_rfi_bundle_to_zip(
    conn,
    zf: zipfile.ZipFile,
    bundle_id: int,
    top_folder: str,
    alloc,
) -> int:
    """Write all files for an RFI bundle into ``zf`` under ``top_folder``.

    Bundle-level files go under ``{top_folder}``; each item's files are
    nested at ``{top_folder}{Project}/{Coverage}/{NNN - description}/``
    so the folder tree mirrors the worksheet sent to the client.
    Returns the total number of files actually written.
    """
    count = 0

    # Bundle-level files (sit directly under the bundle folder)
    direct_rows = conn.execute(
        """SELECT a.*, ra.sort_order AS link_sort
             FROM attachments a
             JOIN record_attachments ra ON ra.attachment_id = a.id
            WHERE ra.record_type = 'rfi_bundle' AND ra.record_id = ?
            ORDER BY ra.sort_order, a.created_at""",
        (bundle_id,),
    ).fetchall()
    for r in direct_rows:
        _add_attachment_to_zip(zf, top_folder, dict(r), alloc)
    count += len(direct_rows)

    # Item-level files nested by project / coverage
    items = conn.execute(
        """SELECT i.id, i.description, i.sort_order, i.policy_uid,
                  i.project_name, i.category,
                  p.policy_type AS policy_coverage_line,
                  p.project_name AS policy_project_name
             FROM client_request_items i
             LEFT JOIN policies p ON p.policy_uid = i.policy_uid
            WHERE i.bundle_id = ?
            ORDER BY i.sort_order, i.id""",
        (bundle_id,),
    ).fetchall()
    for item in items:
        item_folder = top_folder + _rfi_item_folder(dict(item))
        att_rows = conn.execute(
            """SELECT a.*, ra.sort_order AS link_sort
                 FROM attachments a
                 JOIN record_attachments ra ON ra.attachment_id = a.id
                WHERE ra.record_type = 'rfi_item' AND ra.record_id = ?
                ORDER BY ra.sort_order, a.created_at""",
            (item["id"],),
        ).fetchall()
        for r in att_rows:
            _add_attachment_to_zip(zf, item_folder, dict(r), alloc)
        count += len(att_rows)

    return count


def _rfi_bundle_zip_base_name(bundle_meta) -> str:
    """Friendly base filename for a single-bundle ZIP download."""
    rfi_uid = bundle_meta["rfi_uid"] if bundle_meta else None
    title = bundle_meta["title"] if bundle_meta else None
    parts = [p for p in (rfi_uid, title) if p]
    if not parts:
        return "RFI Files"
    return " - ".join(parts)


@router.get("/api/attachments/zip")
def download_attachments_zip(
    record_type: str = Query(...),
    record_id: int = Query(...),
    conn=Depends(get_db),
):
    """Download all attachments linked to a record as a single zip.

    For ``record_type='rfi_bundle'`` the ZIP contains a client-facing
    workbook (``Request Summary.xlsx``) at the root listing every item
    alongside its attached filenames, then the files themselves nested
    as ``{Project}/{Coverage Line}/{NNN - description}/`` so the client
    can match each file to a row. Bundle-level files (files on the
    bundle itself rather than an item) sit directly at the root. No
    MANIFEST.txt is produced — the workbook is the manifest.
    """
    if record_type not in _VALID_RECORD_TYPES:
        return JSONResponse({"ok": False, "error": "Invalid record type"}, status_code=400)

    if record_type == "rfi_bundle":
        return _download_rfi_bundle_zip(conn, record_id)

    # Non-RFI record types — just bundle all direct attachments, flat.
    direct_rows = conn.execute(
        """SELECT a.*, ra.sort_order AS link_sort
             FROM attachments a
             JOIN record_attachments ra ON ra.attachment_id = a.id
            WHERE ra.record_type = ? AND ra.record_id = ?
            ORDER BY ra.sort_order, a.created_at""",
        (record_type, record_id),
    ).fetchall()
    if not direct_rows:
        return JSONResponse({"ok": False, "error": "No attachments to download"}, status_code=404)

    buf = io.BytesIO()
    _, alloc = _zip_name_allocator()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in direct_rows:
            _add_attachment_to_zip(zf, "", dict(r), alloc)

    buf.seek(0)
    data = buf.getvalue()
    base_name = f"{record_type}_{record_id}"
    download_name = f"{_sanitize_filename(base_name)}_files.zip"
    logger.info(
        "Zip download: %s/%s → %d bytes (%d direct)",
        record_type, record_id, len(data), len(direct_rows),
    )
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


def _download_rfi_bundle_zip(conn, bundle_id: int) -> StreamingResponse:
    """Build and stream a single-bundle RFI ZIP with the friendly workbook
    at the root (no MANIFEST.txt)."""
    from policydb.exporter import export_request_bundle_xlsx

    bundle_meta = conn.execute(
        "SELECT rfi_uid, title FROM client_request_bundles WHERE id = ?",
        (bundle_id,),
    ).fetchone()
    if not bundle_meta:
        return JSONResponse({"ok": False, "error": "Bundle not found"}, status_code=404)

    # Pre-flight: if the bundle has zero attached files, fall through to 404
    # so the UI's "no attachments" handling still works.
    has_bundle_files = conn.execute(
        "SELECT 1 FROM record_attachments WHERE record_type='rfi_bundle' AND record_id=? LIMIT 1",
        (bundle_id,),
    ).fetchone()
    has_item_files = conn.execute(
        """SELECT 1 FROM record_attachments ra
             JOIN client_request_items i ON i.id = ra.record_id
            WHERE ra.record_type='rfi_item' AND i.bundle_id=? LIMIT 1""",
        (bundle_id,),
    ).fetchone()
    if not has_bundle_files and not has_item_files:
        return JSONResponse({"ok": False, "error": "No attachments to download"}, status_code=404)

    buf = io.BytesIO()
    _, alloc = _zip_name_allocator()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Workbook at the root — this is the client-facing "manifest"
        try:
            xlsx_bytes = export_request_bundle_xlsx(conn, bundle_id)
            zf.writestr("Request Summary.xlsx", xlsx_bytes)
        except Exception as exc:
            logger.warning("Zip: failed to embed bundle xlsx for %s: %s", bundle_id, exc)

        _add_rfi_bundle_to_zip(conn, zf, bundle_id, top_folder="", alloc=alloc)

    buf.seek(0)
    data = buf.getvalue()

    base_name = _rfi_bundle_zip_base_name(bundle_meta)
    download_name = f"{_friendly_folder_name(base_name, max_len=80)}.zip"
    logger.info("Zip download: rfi_bundle/%s → %d bytes", bundle_id, len(data))
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


def build_client_rfi_zip(conn, client_id: int) -> tuple[bytes, str, int]:
    """Build a ZIP of every open (non-complete) RFI bundle for a client.

    Layout:
        Outstanding Requests.xlsx            ← multi-sheet workbook (one sheet per bundle)
        {RFI title}/                         ← one top-level folder per open bundle
            ...bundle-level files...
            {Project}/{Coverage}/{NNN - description}/
                ...item files...

    Returns ``(zip_bytes, download_filename, total_files_written)``.
    Raises ``ValueError`` with a user-facing message if the client has no
    open bundles or no attached files to include.
    """
    from policydb.exporter import export_client_requests_xlsx

    bundles = conn.execute(
        """SELECT id, rfi_uid, title
             FROM client_request_bundles
            WHERE client_id = ? AND status != 'complete'
            ORDER BY COALESCE(sent_at, created_at) DESC, id DESC""",
        (client_id,),
    ).fetchall()
    if not bundles:
        raise ValueError("No open requests to download")

    buf = io.BytesIO()
    _, alloc = _zip_name_allocator()
    total_files = 0
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Workbook at the root is the manifest
        try:
            xlsx_bytes = export_client_requests_xlsx(conn, client_id)
            zf.writestr("Outstanding Requests.xlsx", xlsx_bytes)
        except Exception as exc:
            logger.warning("Zip: failed to embed client xlsx for %s: %s", client_id, exc)

        # One top folder per bundle, deduped so identically-titled bundles
        # still get unique folders.
        used_folders: set[str] = set()
        for b in bundles:
            label_parts = [p for p in (b["rfi_uid"], b["title"]) if p]
            raw_label = " - ".join(label_parts) if label_parts else f"Request {b['id']}"
            base_label = _friendly_folder_name(raw_label, max_len=70)
            label = base_label
            n = 2
            while label in used_folders:
                label = f"{base_label} ({n})"
                n += 1
            used_folders.add(label)
            top_folder = f"{label}/"
            total_files += _add_rfi_bundle_to_zip(conn, zf, b["id"], top_folder, alloc)

    buf.seek(0)
    data = buf.getvalue()

    client_row = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    client_name = (client_row["name"] if client_row else "Client") or "Client"
    from datetime import date
    download_name = (
        f"{_friendly_folder_name(client_name, max_len=60)}"
        f" - Outstanding Requests - {date.today().isoformat()}.zip"
    )
    return data, download_name, total_files


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
