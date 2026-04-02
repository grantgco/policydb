"""Knowledge Base routes — articles, documents, attachments, record links."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import policydb.config as cfg
from policydb.db import DB_PATH, next_attachment_uid, next_kb_article_uid
from policydb.web.app import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kb")

_KB_FILES_DIR = Path.home() / ".policydb" / "files" / "kb" / "docs"

_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

_MIME_MAP = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Max upload size: 50 MB
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Category color map for templates
CATEGORY_COLORS = {
    "Glossary": {"bg": "bg-blue-50", "text": "text-blue-700", "border_hex": "#3b82f6"},
    "Procedure": {"bg": "bg-green-50", "text": "text-green-700", "border_hex": "#22c55e"},
    "Coverage": {"bg": "bg-purple-50", "text": "text-purple-700", "border_hex": "#a855f7"},
    "Carrier Intel": {"bg": "bg-amber-50", "text": "text-amber-700", "border_hex": "#f59e0b"},
    "Underwriting": {"bg": "bg-teal-50", "text": "text-teal-700", "border_hex": "#14b8a6"},
    "Claims": {"bg": "bg-red-50", "text": "text-red-700", "border_hex": "#ef4444"},
    "General": {"bg": "bg-gray-100", "text": "text-gray-600", "border_hex": "#9ca3af"},
}

_DEFAULT_COLORS = {"bg": "bg-gray-100", "text": "text-gray-600", "border_hex": "#9ca3af"}


def _get_colors(category: str) -> dict:
    return CATEGORY_COLORS.get(category, _DEFAULT_COLORS)


def _parse_tags(tags_str: str | None) -> list[str]:
    if not tags_str:
        return []
    try:
        return json.loads(tags_str)
    except (json.JSONDecodeError, TypeError):
        return []


def _sanitize_filename(name: str, max_len: int = 100) -> str:
    name = re.sub(r'[/\\:*?"<>|]', '_', name)
    name = re.sub(r'\s+', '_', name).strip('_')
    if len(name) > max_len:
        base, ext = os.path.splitext(name)
        name = base[:max_len - len(ext)] + ext
    return name


def _file_type_info(mime_type: str, filename: str) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return {"icon": "pdf", "color": "text-red-500", "label": "PDF"}
    elif ext in (".doc", ".docx"):
        return {"icon": "word", "color": "text-blue-500", "label": "Word"}
    elif ext in (".xls", ".xlsx"):
        return {"icon": "excel", "color": "text-green-600", "label": "Excel"}
    elif ext in (".ppt", ".pptx"):
        return {"icon": "ppt", "color": "text-orange-500", "label": "PowerPoint"}
    return {"icon": "file", "color": "text-gray-500", "label": "File"}


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _get_link_count(conn, entity_type: str, entity_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM kb_links "
        "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
        (entity_type, entity_id, entity_type, entity_id),
    ).fetchone()
    return row["cnt"] if row else 0


# ── Index ────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def kb_index(request: Request, conn=Depends(get_db)):
    categories = cfg.get("kb_categories", [])
    articles = conn.execute(
        "SELECT * FROM kb_articles ORDER BY updated_at DESC"
    ).fetchall()
    documents = conn.execute(
        "SELECT * FROM attachments ORDER BY updated_at DESC"
    ).fetchall()

    # Merge into unified list with type marker
    entries = []
    for a in articles:
        d = dict(a)
        d["entry_type"] = "article"
        d["tags_list"] = _parse_tags(d.get("tags"))
        d["colors"] = _get_colors(d["category"])
        d["source_type"] = "article"
        d["link_count"] = _get_link_count(conn, "kb_article", d["id"])
        entries.append(d)
    for doc in documents:
        d = dict(doc)
        d["entry_type"] = "document"
        d["tags_list"] = _parse_tags(d.get("tags"))
        d["colors"] = _get_colors(d["category"])
        d["file_info"] = _file_type_info(d["mime_type"], d["filename"])
        d["file_size_fmt"] = _format_file_size(d["file_size"])
        d["source_type"] = d.get("source", "local")
        d["link_count"] = _get_link_count(conn, "attachment", d["id"])
        entries.append(d)

    entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)

    return templates.TemplateResponse("kb/index.html", {
        "request": request,
        "entries": entries,
        "categories": categories,
        "category_colors": CATEGORY_COLORS,
        "active": "kb",
        "total_articles": len(articles),
        "total_documents": len(documents),
    })


@router.get("/search", response_class=HTMLResponse)
async def kb_search(
    request: Request,
    q: str = Query(""),
    category: str = Query(""),
    source_filter: str = Query(""),
    sort: str = Query("updated"),
    linked_type: str = Query(""),
    linked_id: int = Query(0),
    conn=Depends(get_db),
):
    pattern = f"%{q}%"
    entries = []

    # If linked-to filter is active, get the set of linked entry IDs
    linked_article_ids = None
    linked_attachment_ids = None
    if linked_type and linked_id:
        link_rows = conn.execute(
            "SELECT source_type, source_id, target_type, target_id FROM kb_links "
            "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
            (linked_type, linked_id, linked_type, linked_id),
        ).fetchall()
        linked_article_ids = set()
        linked_attachment_ids = set()
        for lr in link_rows:
            lr = dict(lr)
            if lr["source_type"] == linked_type and lr["source_id"] == linked_id:
                other_type, other_id = lr["target_type"], lr["target_id"]
            else:
                other_type, other_id = lr["source_type"], lr["source_id"]
            if other_type == "kb_article":
                linked_article_ids.add(other_id)
            elif other_type == "attachment":
                linked_attachment_ids.add(other_id)

    # Articles
    if source_filter in ("", "article"):
        where = "WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
        params = [pattern, pattern, pattern]
        if category:
            where += " AND category = ?"
            params.append(category)
        articles = conn.execute(
            f"SELECT * FROM kb_articles {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        for a in articles:
            d = dict(a)
            if linked_article_ids is not None and d["id"] not in linked_article_ids:
                continue
            d["entry_type"] = "article"
            d["source_type"] = "article"
            d["tags_list"] = _parse_tags(d.get("tags"))
            d["colors"] = _get_colors(d["category"])
            d["link_count"] = _get_link_count(conn, "kb_article", d["id"])
            entries.append(d)

    # Attachments (local and devonthink)
    if source_filter in ("", "local", "devonthink"):
        where = "WHERE (title LIKE ? OR description LIKE ? OR filename LIKE ? OR tags LIKE ?)"
        params = [pattern, pattern, pattern, pattern]
        if category:
            where += " AND category = ?"
            params.append(category)
        if source_filter in ("local", "devonthink"):
            where += " AND source = ?"
            params.append(source_filter)
        documents = conn.execute(
            f"SELECT * FROM attachments {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        for doc in documents:
            d = dict(doc)
            if linked_attachment_ids is not None and d["id"] not in linked_attachment_ids:
                continue
            d["entry_type"] = "document"
            d["source_type"] = d.get("source", "local")
            d["tags_list"] = _parse_tags(d.get("tags"))
            d["colors"] = _get_colors(d["category"])
            d["file_info"] = _file_type_info(d.get("mime_type", ""), d.get("filename", ""))
            d["file_size_fmt"] = _format_file_size(d.get("file_size", 0) or 0)
            d["link_count"] = _get_link_count(conn, "attachment", d["id"])
            entries.append(d)

    # Sort
    if sort == "title":
        entries.sort(key=lambda e: (e.get("title") or "").lower())
    elif sort == "most_linked":
        entries.sort(key=lambda e: e.get("link_count", 0), reverse=True)
    elif sort == "category":
        entries.sort(key=lambda e: (e.get("category") or "").lower())
    else:  # "updated" default
        entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)

    return templates.TemplateResponse("kb/_search_results.html", {
        "request": request,
        "entries": entries,
        "category_colors": CATEGORY_COLORS,
    })


# ── Articles CRUD ────────────────────────────────────────────────────────────

@router.get("/articles/new", response_class=HTMLResponse)
async def new_article_form(request: Request, conn=Depends(get_db)):
    categories = cfg.get("kb_categories", [])
    sources = cfg.get("kb_article_sources", [])
    return templates.TemplateResponse("kb/article_new.html", {
        "request": request,
        "categories": categories,
        "sources": sources,
        "active": "kb",
    })


@router.post("/articles/new")
async def create_article(
    request: Request,
    title: str = Form(""),
    category: str = Form("General"),
    content: str = Form(""),
    source: str = Form("authored"),
    conn=Depends(get_db),
):
    uid = next_kb_article_uid(conn)
    conn.execute(
        "INSERT INTO kb_articles (uid, title, category, content, source, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, title or "Untitled", category, content, source, "[]"),
    )
    conn.commit()
    logger.info("KB article created: %s — %s", uid, title)
    return RedirectResponse(f"/kb/articles/{uid}", status_code=303)


@router.get("/articles/{uid}", response_class=HTMLResponse)
async def article_detail(request: Request, uid: str, conn=Depends(get_db)):
    article = conn.execute("SELECT * FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if not article:
        return RedirectResponse("/kb", status_code=303)

    article = dict(article)
    article["tags_list"] = _parse_tags(article.get("tags"))
    article["colors"] = _get_colors(article["category"])

    # Attachments
    attachments = conn.execute("""
        SELECT a.*, ra.id as attach_id FROM record_attachments ra
        JOIN attachments a ON a.id = ra.attachment_id
        WHERE ra.record_type = 'kb_article' AND ra.record_id = ?
        ORDER BY ra.sort_order, ra.created_at
    """, (article["id"],)).fetchall()
    attach_list = []
    for att in attachments:
        d = dict(att)
        d["file_info"] = _file_type_info(d["mime_type"], d["filename"])
        d["file_size_fmt"] = _format_file_size(d["file_size"])
        attach_list.append(d)

    # Record links
    record_links = _get_record_links(conn, "article", article["id"])

    categories = cfg.get("kb_categories", [])
    sources = cfg.get("kb_article_sources", [])

    return templates.TemplateResponse("kb/article.html", {
        "request": request,
        "article": article,
        "attachments": attach_list,
        "record_links": record_links,
        "categories": categories,
        "sources": sources,
        "category_colors": CATEGORY_COLORS,
        "active": "kb",
    })


@router.post("/articles/{uid}/field")
async def article_save_field(
    request: Request,
    uid: str,
    conn=Depends(get_db),
):
    form = await request.form()
    field = form.get("field", "")
    value = form.get("value", "")

    allowed = {"title", "category", "content", "source"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"})

    conn.execute(f"UPDATE kb_articles SET {field} = ? WHERE uid = ?", (value, uid))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


@router.post("/articles/{uid}/tags")
async def article_update_tags(
    request: Request,
    uid: str,
    action: str = Form("add"),
    tag: str = Form(""),
    conn=Depends(get_db),
):
    article = conn.execute("SELECT id, tags FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if not article:
        return JSONResponse({"ok": False})

    tags = _parse_tags(article["tags"])
    tag = tag.strip().lower()
    if not tag:
        return JSONResponse({"ok": False})

    if action == "add" and tag not in tags:
        tags.append(tag)
    elif action == "remove" and tag in tags:
        tags.remove(tag)

    conn.execute("UPDATE kb_articles SET tags = ? WHERE uid = ?", (json.dumps(tags), uid))
    conn.commit()

    article_dict = dict(article)
    article_dict["tags_list"] = tags
    return templates.TemplateResponse("kb/_tags.html", {
        "request": request,
        "entry": article_dict,
        "entry_type": "article",
        "uid": uid,
    })


@router.post("/articles/{uid}/delete")
async def delete_article(uid: str, conn=Depends(get_db)):
    article = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if article:
        conn.execute("DELETE FROM record_attachments WHERE record_type = 'kb_article' AND record_id = ?", (article["id"],))
        conn.execute("DELETE FROM kb_record_links WHERE entry_type = 'article' AND entry_id = ?", (article["id"],))
        conn.execute("DELETE FROM kb_articles WHERE id = ?", (article["id"],))
        conn.commit()
        logger.info("KB article deleted: %s", uid)
    return RedirectResponse("/kb", status_code=303)


# ── Documents CRUD ───────────────────────────────────────────────────────────

@router.post("/documents/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("General"),
    conn=Depends(get_db),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return RedirectResponse("/kb?error=invalid_type", status_code=303)

    # Read file content
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        return RedirectResponse("/kb?error=too_large", status_code=303)

    uid = next_attachment_uid(conn)
    safe_name = _sanitize_filename(file.filename or "document")
    stored_name = f"{uid}_{safe_name}"

    # Ensure directory exists
    from policydb.web.routes.attachments import _ATTACHMENTS_DIR
    _ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

    file_path = _ATTACHMENTS_DIR / stored_name
    with open(file_path, "wb") as f:
        f.write(content)

    mime_type = _MIME_MAP.get(ext, "application/octet-stream")
    display_title = title or os.path.splitext(file.filename or "Document")[0]

    conn.execute(
        "INSERT INTO attachments (uid, title, source, category, filename, file_path, file_size, mime_type, tags) "
        "VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?)",
        (uid, display_title, category, file.filename, str(file_path), len(content), mime_type, "[]"),
    )
    conn.commit()
    logger.info("KB document uploaded: %s — %s (%s)", uid, display_title, _format_file_size(len(content)))
    return RedirectResponse(f"/kb/documents/{uid}", status_code=303)


@router.get("/documents/{uid}", response_class=HTMLResponse)
async def document_detail(request: Request, uid: str, conn=Depends(get_db)):
    doc = conn.execute("SELECT * FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not doc:
        return RedirectResponse("/kb", status_code=303)

    doc = dict(doc)
    doc["tags_list"] = _parse_tags(doc.get("tags"))
    doc["colors"] = _get_colors(doc["category"])
    doc["file_info"] = _file_type_info(doc["mime_type"], doc["filename"])
    doc["file_size_fmt"] = _format_file_size(doc["file_size"])

    # Articles referencing this document
    referencing = conn.execute("""
        SELECT a.* FROM record_attachments ra
        JOIN kb_articles a ON a.id = ra.record_id
        WHERE ra.attachment_id = ? AND ra.record_type = 'kb_article'
        ORDER BY a.updated_at DESC
    """, (doc["id"],)).fetchall()
    referencing_articles = [dict(r) for r in referencing]
    for r in referencing_articles:
        r["colors"] = _get_colors(r["category"])

    record_links = _get_record_links(conn, "document", doc["id"])
    categories = cfg.get("kb_categories", [])

    return templates.TemplateResponse("kb/document.html", {
        "request": request,
        "doc": doc,
        "referencing_articles": referencing_articles,
        "record_links": record_links,
        "categories": categories,
        "category_colors": CATEGORY_COLORS,
        "active": "kb",
    })


@router.post("/documents/{uid}/field")
async def document_save_field(
    request: Request,
    uid: str,
    conn=Depends(get_db),
):
    form = await request.form()
    field = form.get("field", "")
    value = form.get("value", "")

    allowed = {"title", "category", "description"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"})

    conn.execute(f"UPDATE attachments SET {field} = ? WHERE uid = ?", (value, uid))
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})


@router.post("/documents/{uid}/tags")
async def document_update_tags(
    request: Request,
    uid: str,
    action: str = Form("add"),
    tag: str = Form(""),
    conn=Depends(get_db),
):
    doc = conn.execute("SELECT id, tags FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not doc:
        return JSONResponse({"ok": False})

    tags = _parse_tags(doc["tags"])
    tag = tag.strip().lower()
    if not tag:
        return JSONResponse({"ok": False})

    if action == "add" and tag not in tags:
        tags.append(tag)
    elif action == "remove" and tag in tags:
        tags.remove(tag)

    conn.execute("UPDATE attachments SET tags = ? WHERE uid = ?", (json.dumps(tags), uid))
    conn.commit()

    doc_dict = dict(doc)
    doc_dict["tags_list"] = tags
    return templates.TemplateResponse("kb/_tags.html", {
        "request": request,
        "entry": doc_dict,
        "entry_type": "document",
        "uid": uid,
    })


@router.get("/documents/{uid}/download")
async def document_download(uid: str, conn=Depends(get_db)):
    doc = conn.execute("SELECT file_path, filename, mime_type FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if not doc:
        return RedirectResponse("/kb", status_code=303)

    file_path = Path(doc["file_path"])
    if not file_path.exists():
        return RedirectResponse("/kb", status_code=303)

    return FileResponse(
        path=str(file_path),
        filename=doc["filename"],
        media_type=doc["mime_type"],
    )


@router.post("/documents/{uid}/delete")
async def delete_document(uid: str, conn=Depends(get_db)):
    doc = conn.execute("SELECT id, file_path, source FROM attachments WHERE uid = ?", (uid,)).fetchone()
    if doc:
        # Remove file from disk if local
        if doc["source"] == "local" and doc["file_path"]:
            file_path = Path(doc["file_path"])
            if file_path.exists():
                file_path.unlink()
        # record_attachments cascade via FK ON DELETE CASCADE
        conn.execute("DELETE FROM attachments WHERE id = ?", (doc["id"],))
        conn.commit()
        logger.info("KB document deleted: %s", uid)
    return RedirectResponse("/kb", status_code=303)


# ── Attachments (article ↔ document) ─────────────────────────────────────────

@router.post("/articles/{uid}/attach")
async def attach_document(
    request: Request,
    uid: str,
    document_id: int = Form(...),
    conn=Depends(get_db),
):
    article = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if not article:
        return JSONResponse({"ok": False})

    try:
        conn.execute(
            "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, 'kb_article', ?)",
            (document_id, article["id"]),
        )
        conn.commit()
    except Exception:
        pass

    return await _render_attachments_partial(request, conn, uid, article["id"])


@router.post("/articles/{uid}/upload-attach")
async def upload_and_attach(
    request: Request,
    uid: str,
    file: UploadFile = File(...),
    category: str = Form("General"),
    conn=Depends(get_db),
):
    article = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if not article:
        return JSONResponse({"ok": False})

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return JSONResponse({"ok": False, "error": "Invalid file type"})

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        return JSONResponse({"ok": False, "error": "File too large"})

    doc_uid = next_attachment_uid(conn)
    safe_name = _sanitize_filename(file.filename or "document")
    stored_name = f"{doc_uid}_{safe_name}"
    from policydb.web.routes.attachments import _ATTACHMENTS_DIR
    _ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

    file_path = _ATTACHMENTS_DIR / stored_name
    with open(file_path, "wb") as f:
        f.write(content)

    mime_type = _MIME_MAP.get(ext, "application/octet-stream")
    display_title = os.path.splitext(file.filename or "Document")[0]

    conn.execute(
        "INSERT INTO attachments (uid, title, source, category, filename, file_path, file_size, mime_type, tags) "
        "VALUES (?, ?, 'local', ?, ?, ?, ?, ?, ?)",
        (doc_uid, display_title, category, file.filename, str(file_path), len(content), mime_type, "[]"),
    )
    doc_row = conn.execute("SELECT id FROM attachments WHERE uid = ?", (doc_uid,)).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, 'kb_article', ?)",
        (doc_row["id"], article["id"]),
    )
    conn.commit()

    return await _render_attachments_partial(request, conn, uid, article["id"])


@router.post("/articles/{uid}/detach")
async def detach_document(
    request: Request,
    uid: str,
    document_id: int = Form(...),
    conn=Depends(get_db),
):
    article = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    if not article:
        return JSONResponse({"ok": False})

    conn.execute(
        "DELETE FROM record_attachments WHERE attachment_id = ? AND record_type = 'kb_article' AND record_id = ?",
        (document_id, article["id"]),
    )
    conn.commit()

    return await _render_attachments_partial(request, conn, uid, article["id"])


async def _render_attachments_partial(request, conn, uid, article_id):
    attachments = conn.execute("""
        SELECT a.*, ra.id as attach_id FROM record_attachments ra
        JOIN attachments a ON a.id = ra.attachment_id
        WHERE ra.record_type = 'kb_article' AND ra.record_id = ?
        ORDER BY ra.sort_order, ra.created_at
    """, (article_id,)).fetchall()
    attach_list = []
    for att in attachments:
        d = dict(att)
        d["file_info"] = _file_type_info(d["mime_type"], d["filename"])
        d["file_size_fmt"] = _format_file_size(d["file_size"])
        attach_list.append(d)

    return templates.TemplateResponse("kb/_attachments.html", {
        "request": request,
        "attachments": attach_list,
        "uid": uid,
    })


# ── Record Links ─────────────────────────────────────────────────────────────

def _get_record_links(conn, entry_type: str, entry_id: int) -> list[dict]:
    if entry_type == "document":
        # Documents use record_attachments (migrated from kb_record_links)
        rows = conn.execute(
            "SELECT * FROM record_attachments WHERE attachment_id = ? AND record_type IN ('client', 'policy')",
            (entry_id,),
        ).fetchall()
        links = []
        for r in rows:
            d = dict(r)
            d["entity_type"] = d["record_type"]
            d["entity_id"] = d["record_id"]
            if d["entity_type"] == "client":
                entity = conn.execute("SELECT id, name FROM clients WHERE id = ?", (d["entity_id"],)).fetchone()
                if entity:
                    d["entity_name"] = entity["name"]
                    d["entity_url"] = f"/clients/{entity['id']}"
            elif d["entity_type"] == "policy":
                entity = conn.execute(
                    "SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name as client_name "
                    "FROM policies p LEFT JOIN clients c ON c.id = p.client_id "
                    "WHERE p.id = ?", (d["entity_id"],)
                ).fetchone()
                if entity:
                    d["entity_name"] = f"{entity['policy_uid']} — {entity['carrier'] or ''} {entity['policy_type'] or ''}"
                    d["entity_url"] = f"/policies/{entity['policy_uid']}"
            if "entity_name" in d:
                links.append(d)
        return links
    else:
        # Articles still use kb_record_links
        rows = conn.execute(
            "SELECT * FROM kb_record_links WHERE entry_type = ? AND entry_id = ?",
            (entry_type, entry_id),
        ).fetchall()
        links = []
        for r in rows:
            d = dict(r)
            if d["entity_type"] == "client":
                entity = conn.execute("SELECT id, name FROM clients WHERE id = ?", (d["entity_id"],)).fetchone()
                if entity:
                    d["entity_name"] = entity["name"]
                    d["entity_url"] = f"/clients/{entity['id']}"
            elif d["entity_type"] == "policy":
                entity = conn.execute(
                    "SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name as client_name "
                    "FROM policies p LEFT JOIN clients c ON c.id = p.client_id "
                    "WHERE p.id = ?", (d["entity_id"],)
                ).fetchone()
                if entity:
                    d["entity_name"] = f"{entity['policy_uid']} — {entity['carrier'] or ''} {entity['policy_type'] or ''}"
                    d["entity_url"] = f"/policies/{entity['policy_uid']}"
            if "entity_name" in d:
                links.append(d)
        return links


@router.post("/{entry_type}/{uid}/link")
async def link_record(
    request: Request,
    entry_type: str,
    uid: str,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    if entry_type == "article":
        row = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()

    if not row:
        return JSONResponse({"ok": False})

    try:
        if entry_type == "document":
            # Documents use record_attachments
            conn.execute(
                "INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id) VALUES (?, ?, ?)",
                (row["id"], entity_type, entity_id),
            )
        else:
            # Articles use kb_record_links
            conn.execute(
                "INSERT OR IGNORE INTO kb_record_links (entry_type, entry_id, entity_type, entity_id) VALUES (?, ?, ?, ?)",
                (entry_type, row["id"], entity_type, entity_id),
            )
        conn.commit()
    except Exception:
        pass

    record_links = _get_record_links(conn, entry_type, row["id"])
    return templates.TemplateResponse("kb/_record_links_list.html", {
        "request": request,
        "record_links": record_links,
        "entry_type": entry_type,
        "uid": uid,
    })


@router.post("/{entry_type}/{uid}/unlink")
async def unlink_record(
    request: Request,
    entry_type: str,
    uid: str,
    link_id: int = Form(...),
    conn=Depends(get_db),
):
    if entry_type == "document":
        conn.execute("DELETE FROM record_attachments WHERE id = ?", (link_id,))
    else:
        conn.execute("DELETE FROM kb_record_links WHERE id = ?", (link_id,))
    conn.commit()

    if entry_type == "article":
        row = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()

    if not row:
        return JSONResponse({"ok": False})

    record_links = _get_record_links(conn, entry_type, row["id"])
    return templates.TemplateResponse("kb/_record_links_list.html", {
        "request": request,
        "record_links": record_links,
        "entry_type": entry_type,
        "uid": uid,
    })


@router.get("/search-entities", response_class=HTMLResponse)
async def search_entities(
    request: Request,
    q: str = Query(""),
    conn=Depends(get_db),
):
    pattern = f"%{q}%"
    clients = conn.execute(
        "SELECT id, name FROM clients WHERE name LIKE ? ORDER BY name LIMIT 10",
        (pattern,),
    ).fetchall()
    policies = conn.execute(
        "SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name as client_name "
        "FROM policies p LEFT JOIN clients c ON c.id = p.client_id "
        "WHERE p.policy_uid LIKE ? OR p.policy_type LIKE ? OR p.carrier LIKE ? OR c.name LIKE ? "
        "ORDER BY p.policy_uid LIMIT 10",
        (pattern, pattern, pattern, pattern),
    ).fetchall()

    return templates.TemplateResponse("kb/_entity_picker.html", {
        "request": request,
        "clients": [dict(c) for c in clients],
        "policies": [dict(p) for p in policies],
    })


@router.get("/search-linkable", response_class=HTMLResponse)
async def search_linkable(
    request: Request,
    q: str = Query(""),
    conn=Depends(get_db),
):
    """Search all linkable entity types for the linked-to filter combobox."""
    if len(q.strip()) < 2:
        return HTMLResponse("")
    pattern = f"%{q}%"
    results = []

    # Clients
    for r in conn.execute(
        "SELECT id, name FROM clients WHERE name LIKE ? ORDER BY name LIMIT 5", (pattern,)
    ).fetchall():
        results.append({"type": "client", "id": r["id"], "label": r["name"], "icon": "client"})

    # Policies
    for r in conn.execute(
        "SELECT p.id, p.policy_uid, p.carrier, p.policy_type, c.name AS client_name "
        "FROM policies p LEFT JOIN clients c ON c.id = p.client_id "
        "WHERE p.policy_uid LIKE ? OR p.carrier LIKE ? OR c.name LIKE ? "
        "ORDER BY p.policy_uid LIMIT 5",
        (pattern, pattern, pattern),
    ).fetchall():
        label = f"{r['policy_uid']} — {r['carrier'] or ''} {r['policy_type'] or ''}".strip()
        results.append({"type": "policy", "id": r["id"], "label": label, "icon": "policy"})

    # Issues
    for r in conn.execute(
        "SELECT id, issue_uid, subject FROM issues WHERE issue_uid LIKE ? OR subject LIKE ? ORDER BY issue_uid DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "issue", "id": r["id"], "label": f"{r['issue_uid']} — {r['subject']}", "icon": "issue"})

    # KB Articles
    for r in conn.execute(
        "SELECT id, uid, title FROM kb_articles WHERE uid LIKE ? OR title LIKE ? ORDER BY updated_at DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "kb_article", "id": r["id"], "label": f"{r['uid']} — {r['title']}", "icon": "article"})

    # Attachments
    for r in conn.execute(
        "SELECT id, uid, title FROM attachments WHERE uid LIKE ? OR title LIKE ? ORDER BY updated_at DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "attachment", "id": r["id"], "label": f"{r['uid']} — {r['title']}", "icon": "document"})

    # Projects
    for r in conn.execute(
        "SELECT id, name FROM projects WHERE name LIKE ? ORDER BY name LIMIT 5",
        (pattern,),
    ).fetchall():
        results.append({"type": "project", "id": r["id"], "label": r["name"], "icon": "project"})

    # Render inline HTML for dropdown
    if not results:
        return HTMLResponse('<div class="px-3 py-2 text-xs text-gray-400">No results</div>')

    html_parts = []
    for r in results:
        escaped_label = r["label"].replace("'", "&#39;").replace('"', "&quot;")
        html_parts.append(
            f'<button type="button" class="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center gap-2" '
            f'onclick="selectLinkedEntity(\'{r["type"]}\', {r["id"]}, \'{escaped_label}\');">'
            f'<span class="text-[9px] uppercase font-medium text-gray-400 w-12">{r["type"].replace("kb_article","article").replace("attachment","file")}</span>'
            f'<span class="text-gray-700 truncate">{r["label"]}</span>'
            f'</button>'
        )
    html = '<div class="py-1">' + ''.join(html_parts) + '</div>'
    return HTMLResponse(html)


# ── KB links for client/policy pages ─────────────────────────────────────────

@router.get("/for-entity/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def kb_links_for_entity(
    request: Request,
    entity_type: str,
    entity_id: int,
    conn=Depends(get_db),
):
    rows = conn.execute(
        "SELECT * FROM kb_record_links WHERE entity_type = ? AND entity_id = ?",
        (entity_type, entity_id),
    ).fetchall()
    links = []
    for r in rows:
        d = dict(r)
        if d["entry_type"] == "article":
            entry = conn.execute("SELECT uid, title, category FROM kb_articles WHERE id = ?", (d["entry_id"],)).fetchone()
        else:
            entry = conn.execute("SELECT uid, title, category FROM attachments WHERE id = ?", (d["entry_id"],)).fetchone()
        if entry:
            d["entry_uid"] = entry["uid"]
            d["entry_title"] = entry["title"]
            d["entry_category"] = entry["category"]
            d["colors"] = _get_colors(entry["category"])
            d["entry_url"] = f"/kb/{'articles' if d['entry_type'] == 'article' else 'documents'}/{entry['uid']}"
            links.append(d)

    return templates.TemplateResponse("kb/_entity_kb_links.html", {
        "request": request,
        "kb_links": links,
        "entity_type": entity_type,
        "entity_id": entity_id,
    })


# ── Entity-side KB linking (from policy/client pages) ─────────────────────────


@router.get("/search-entries", response_class=HTMLResponse)
async def search_entries(
    request: Request,
    q: str = Query(""),
    entity_type: str = Query(""),
    entity_id: int = Query(0),
    conn=Depends(get_db),
):
    """Search KB articles and documents — used from policy/client pages to find entries to link."""
    pattern = f"%{q}%"
    articles = conn.execute(
        "SELECT id, uid, title, category, 'article' AS entry_type FROM kb_articles "
        "WHERE title LIKE ? OR category LIKE ? ORDER BY updated_at DESC LIMIT 10",
        (pattern, pattern),
    ).fetchall()
    documents = conn.execute(
        "SELECT id, uid, title, category, 'document' AS entry_type FROM attachments "
        "WHERE title LIKE ? OR filename LIKE ? ORDER BY updated_at DESC LIMIT 10",
        (pattern, pattern),
    ).fetchall()

    entries = []
    for row in list(articles) + list(documents):
        d = dict(row)
        d["colors"] = _get_colors(d.get("category") or "")
        entries.append(d)

    return templates.TemplateResponse("kb/_entry_search_results.html", {
        "request": request,
        "entries": entries,
        "entity_type": entity_type,
        "entity_id": entity_id,
    })


@router.post("/link-from-entity", response_class=HTMLResponse)
async def link_from_entity(
    request: Request,
    entry_type: str = Form(...),
    entry_id: int = Form(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    """Create a KB link from a policy/client page."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO kb_record_links (entry_type, entry_id, entity_type, entity_id) VALUES (?, ?, ?, ?)",
            (entry_type, entry_id, entity_type, entity_id),
        )
        conn.commit()
    except Exception:
        pass

    # Return refreshed entity KB links
    return await kb_links_for_entity(request, entity_type, entity_id, conn)


@router.post("/unlink-from-entity", response_class=HTMLResponse)
async def unlink_from_entity(
    request: Request,
    link_id: int = Form(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    """Remove a KB link from a policy/client page."""
    conn.execute("DELETE FROM kb_record_links WHERE id = ?", (link_id,))
    conn.commit()
    return await kb_links_for_entity(request, entity_type, entity_id, conn)


# ── Document search (for attach picker) ─────────────────────────────────────

@router.get("/search-documents", response_class=HTMLResponse)
async def search_documents(
    request: Request,
    q: str = Query(""),
    conn=Depends(get_db),
):
    pattern = f"%{q}%"
    documents = conn.execute(
        "SELECT id, uid, title, filename, mime_type FROM attachments "
        "WHERE title LIKE ? OR filename LIKE ? ORDER BY updated_at DESC LIMIT 10",
        (pattern, pattern),
    ).fetchall()

    results = []
    for doc in documents:
        d = dict(doc)
        d["file_info"] = _file_type_info(d["mime_type"], d["filename"])
        results.append(d)

    return templates.TemplateResponse("kb/_document_picker.html", {
        "request": request,
        "documents": results,
    })
