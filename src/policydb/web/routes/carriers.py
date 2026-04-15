"""Carrier & wholesaler directory — loss run email addresses + metadata.

First-class table promoted from the legacy `carriers` config list. Used by
the loss run request automation to look up where to send requests, and by
the importer to ensure every referenced carrier has a row.
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.utils import clean_email, normalize_carrier
from policydb.web.app import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/carriers", tags=["carriers"])

VALID_TYPES = {"carrier", "wholesaler"}
EDITABLE_FIELDS = {"name", "type", "loss_run_email", "loss_run_cc", "notes"}


# ── Lookup helpers used by other modules ─────────────────────────────────────


FUZZY_MATCH_THRESHOLD = 85  # rapidfuzz WRatio — mirrors reconciler carrier threshold


def _fuzzy_lookup(conn: sqlite3.Connection, query: str, type_: str) -> dict | None:
    """Fuzzy-match `query` against directory rows of the given type.

    Uses rapidfuzz.WRatio (same scorer the reconciler uses for carrier pairing).
    Returns the best row above FUZZY_MATCH_THRESHOLD, or None.
    """
    query = (query or "").strip()
    if not query:
        return None
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        return None
    rows = conn.execute(
        "SELECT * FROM carriers WHERE type = ? AND TRIM(name) != ''",
        (type_,),
    ).fetchall()
    if not rows:
        return None
    choices = {r["name"]: dict(r) for r in rows}
    match = process.extractOne(
        query,
        list(choices.keys()),
        scorer=fuzz.WRatio,
        score_cutoff=FUZZY_MATCH_THRESHOLD,
    )
    if match:
        return choices[match[0]]
    return None


def lookup_destination(
    conn: sqlite3.Connection,
    *,
    carrier_name: str = "",
    wholesaler_name: str = "",
) -> dict | None:
    """Return the preferred loss-run destination for a policy.

    Wholesaler wins when present — mirrors real business flow where the
    intermediary owns the carrier relationship. Falls back to the carrier
    row when no wholesaler is set. Returns None if neither matches.

    Lookup chain per candidate:
        1. Exact normalized name (via normalize_carrier + carrier_aliases)
        2. Exact raw name (case-insensitive) — covers rows not yet in aliases
        3. Fuzzy WRatio >= 85 (same threshold the reconciler uses for carriers)
    """
    wholesaler_name = (wholesaler_name or "").strip()
    if wholesaler_name:
        row = conn.execute(
            "SELECT * FROM carriers WHERE name = ? COLLATE NOCASE AND type = 'wholesaler' LIMIT 1",
            (wholesaler_name,),
        ).fetchone()
        if row:
            return dict(row)
        fuzzy = _fuzzy_lookup(conn, wholesaler_name, "wholesaler")
        if fuzzy:
            return fuzzy

    carrier_name = (carrier_name or "").strip()
    if carrier_name:
        canonical = normalize_carrier(carrier_name)
        row = conn.execute(
            "SELECT * FROM carriers WHERE name = ? COLLATE NOCASE AND type = 'carrier' LIMIT 1",
            (canonical or carrier_name,),
        ).fetchone()
        if row:
            return dict(row)
        if canonical and canonical.lower() != carrier_name.lower():
            row = conn.execute(
                "SELECT * FROM carriers WHERE name = ? COLLATE NOCASE AND type = 'carrier' LIMIT 1",
                (carrier_name,),
            ).fetchone()
            if row:
                return dict(row)
        # Fuzzy fallback — catches typos and minor variants
        fuzzy = _fuzzy_lookup(conn, canonical or carrier_name, "carrier")
        if fuzzy:
            return fuzzy
    return None


def ensure_carrier_row(conn: sqlite3.Connection, name: str, type_: str = "carrier") -> None:
    """Insert a blank carriers row if the name isn't already present.

    Called by the importer and by the compose "Save destination" flow so
    the directory stays comprehensive without the user having to manually
    pre-populate it. Idempotent — safe to call repeatedly.
    """
    name = (name or "").strip()
    if not name:
        return
    if type_ not in VALID_TYPES:
        type_ = "carrier"
    conn.execute(
        "INSERT OR IGNORE INTO carriers (name, type) VALUES (?, ?)",
        (name, type_),
    )


def upsert_loss_run_email(
    conn: sqlite3.Connection,
    *,
    name: str,
    type_: str,
    email: str,
    cc: str = "",
) -> None:
    """Insert-or-update the loss run destination for a carrier/wholesaler.

    Used by the compose "Save destination" checkbox so the directory
    compiles itself as the user works.
    """
    name = (name or "").strip()
    email = clean_email(email) or (email or "").strip()
    if not name or not email:
        return
    if type_ not in VALID_TYPES:
        type_ = "carrier"
    row = conn.execute(
        "SELECT id FROM carriers WHERE name = ? COLLATE NOCASE AND type = ? LIMIT 1",
        (name, type_),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE carriers SET loss_run_email = ?, loss_run_cc = ? WHERE id = ?",
            (email, cc or "", row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO carriers (name, type, loss_run_email, loss_run_cc) VALUES (?, ?, ?, ?)",
            (name, type_, email, cc or ""),
        )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def carriers_index(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    q: str = Query(""),
    type_filter: str = Query("all", alias="type"),
):
    """Directory page — contenteditable table with search + type filter."""
    sql = "SELECT * FROM carriers"
    where = []
    params: list = []
    if q.strip():
        where.append("(name LIKE ? OR loss_run_email LIKE ? OR notes LIKE ?)")
        needle = f"%{q.strip()}%"
        params.extend([needle, needle, needle])
    if type_filter in VALID_TYPES:
        where.append("type = ?")
        params.append(type_filter)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY type DESC, name COLLATE NOCASE"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    counts = conn.execute(
        "SELECT type, COUNT(*) AS c FROM carriers GROUP BY type"
    ).fetchall()
    count_by_type = {r["type"]: r["c"] for r in counts}
    count_by_type.setdefault("carrier", 0)
    count_by_type.setdefault("wholesaler", 0)
    count_with_email = conn.execute(
        "SELECT COUNT(*) FROM carriers WHERE TRIM(loss_run_email) != ''"
    ).fetchone()[0]

    return templates.TemplateResponse("carriers/index.html", {
        "request": request,
        "active": "carriers",
        "carriers": rows,
        "q": q,
        "type_filter": type_filter if type_filter in VALID_TYPES else "all",
        "count_by_type": count_by_type,
        "count_with_email": count_with_email,
        "count_total": sum(count_by_type.values()),
    })


@router.post("", response_class=HTMLResponse)
def carriers_create(
    request: Request,
    name: str = Form(""),
    type_: str = Form("carrier", alias="type"),
    loss_run_email: str = Form(""),
    loss_run_cc: str = Form(""),
    notes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Insert a new row from the `+ Add row` affordance. Returns the new row."""
    name = (name or "").strip() or "New carrier"
    if type_ not in VALID_TYPES:
        type_ = "carrier"
    email_clean = clean_email(loss_run_email) if loss_run_email else ""
    cc_clean = clean_email(loss_run_cc) if loss_run_cc else ""
    try:
        cur = conn.execute(
            """INSERT INTO carriers (name, type, loss_run_email, loss_run_cc, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (name, type_, email_clean, cc_clean, (notes or "").strip()),
        )
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Duplicate name+type — return existing row so the UI can surface it
        row = conn.execute(
            "SELECT id FROM carriers WHERE name = ? COLLATE NOCASE AND type = ? LIMIT 1",
            (name, type_),
        ).fetchone()
        new_id = row["id"] if row else None
    if not new_id:
        return HTMLResponse("", status_code=400)
    row = conn.execute("SELECT * FROM carriers WHERE id = ?", (new_id,)).fetchone()
    return templates.TemplateResponse("carriers/_row.html", {
        "request": request,
        "c": dict(row),
    })


@router.patch("/{carrier_id}")
def carriers_patch(
    carrier_id: int,
    field: str = Form(...),
    value: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Cell-save PATCH — contenteditable blur handler.

    Returns `{"ok": true, "formatted": ...}` per the standard PolicyDB PATCH
    contract so the JS callback can flash the cell.

    Renames cascade: when `field='name'`, matching policies.carrier (or
    access_point for wholesalers) are updated in the same transaction and
    the in-memory alias map is rebuilt so subsequent normalize_carrier()
    calls see the new canonical.
    """
    if field not in EDITABLE_FIELDS:
        return JSONResponse({"ok": False, "error": f"unknown field: {field}"}, status_code=400)
    row = conn.execute("SELECT * FROM carriers WHERE id = ?", (carrier_id,)).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    raw = (value or "").strip()
    formatted = raw
    old_name = row["name"]
    row_type = row["type"]

    if field == "type":
        if raw not in VALID_TYPES:
            return JSONResponse({"ok": False, "error": "type must be carrier or wholesaler"}, status_code=400)
    elif field in ("loss_run_email", "loss_run_cc"):
        if raw:
            cleaned = clean_email(raw)
            if not cleaned:
                return JSONResponse({"ok": False, "error": "invalid email"}, status_code=400)
            formatted = cleaned
        else:
            formatted = ""
    elif field == "name":
        if not raw:
            return JSONResponse({"ok": False, "error": "name required"}, status_code=400)

    try:
        conn.execute(
            f"UPDATE carriers SET {field} = ? WHERE id = ?",
            (formatted, carrier_id),
        )
        # Rename cascade: propagate the new name to policies in the same transaction
        cascaded = 0
        if field == "name" and formatted and formatted.lower() != (old_name or "").lower():
            if row_type == "carrier":
                cur = conn.execute(
                    "UPDATE policies SET carrier = ? WHERE carrier = ? COLLATE NOCASE",
                    (formatted, old_name),
                )
                cascaded = cur.rowcount or 0
            elif row_type == "wholesaler":
                cur = conn.execute(
                    "UPDATE policies SET access_point = ? WHERE access_point = ? COLLATE NOCASE",
                    (formatted, old_name),
                )
                cascaded = cur.rowcount or 0
        conn.commit()
        if field == "name":
            # Refresh the in-memory alias map so normalize_carrier() picks up the rename.
            try:
                from policydb.utils import rebuild_carrier_aliases
                rebuild_carrier_aliases()
            except Exception:
                pass
            if cascaded:
                logger.info(
                    "Carrier rename cascaded: %r → %r across %d policies (type=%s)",
                    old_name, formatted, cascaded, row_type,
                )
    except sqlite3.IntegrityError:
        return JSONResponse({"ok": False, "error": "duplicate name+type"}, status_code=409)

    response: dict = {"ok": True, "formatted": formatted}
    if field == "name":
        response["cascaded"] = cascaded
    return JSONResponse(response)


@router.delete("/{carrier_id}")
def carriers_delete(
    carrier_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    conn.execute("DELETE FROM carriers WHERE id = ?", (carrier_id,))
    conn.commit()
    return HTMLResponse("")
