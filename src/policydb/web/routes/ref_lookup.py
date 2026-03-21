"""Ref tree lookup — resolve any UID into a tree of related references."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates

router = APIRouter()


def _parse_uid(uid: str) -> tuple[str, str]:
    """Parse a UID string and return (type, value).

    Returns: ('client', cn_number), ('policy', policy_uid), ('cor', thread_id),
             ('inb', inbox_id), ('activity', activity_id), ('rfi', rfi_uid),
             ('unknown', original)
    """
    uid = uid.strip()

    # Full ref tag — extract deepest segment
    # e.g., CN122333627-POL20250441-COR7 → COR-7
    if re.match(r'^CN?\d+-.+', uid, re.IGNORECASE):
        parts = uid.split('-')
        last = parts[-1] if len(parts) > 1 else parts[0]
        # Check if last segment is COR, A, RFI, INB
        cor = re.match(r'^COR(\d+)$', last, re.IGNORECASE)
        if cor:
            return ('cor', cor.group(1))
        act = re.match(r'^A(\d+)$', last, re.IGNORECASE)
        if act:
            return ('activity', act.group(1))
        # Check for RFI at end: ...RFI01
        rfi = re.match(r'^RFI\d+$', last, re.IGNORECASE)
        if rfi:
            return ('rfi', uid)  # use full composite for lookup
        # Check for POL segment
        pol = re.match(r'^POL', last, re.IGNORECASE)
        if pol:
            # Reconstruct policy UID: POL20250441 → POL-2025-0441
            return ('policy_compact', last)
        # Fallback: treat as client CN
        cn = re.match(r'^CN?(\d+)', uid, re.IGNORECASE)
        if cn:
            return ('client', cn.group(1))

    # Standalone patterns
    cor = re.match(r'^COR-(\d+)$', uid, re.IGNORECASE)
    if cor:
        return ('cor', cor.group(1))

    inb = re.match(r'^INB-(\d+)$', uid, re.IGNORECASE)
    if inb:
        return ('inb', inb.group(1))

    act = re.match(r'^A-(\d+)$', uid, re.IGNORECASE)
    if act:
        return ('activity', act.group(1))

    # RFI composite: CN122333627-RFI01
    rfi = re.match(r'^CN?\d+-RFI\d+$', uid, re.IGNORECASE)
    if rfi:
        return ('rfi', uid)

    # Policy UID: POL-2025-0441
    pol = re.match(r'^POL-', uid, re.IGNORECASE)
    if pol:
        return ('policy', uid)

    # Client CN: CN122333627 or just digits
    cn = re.match(r'^CN?(\d{5,})$', uid, re.IGNORECASE)
    if cn:
        return ('client', cn.group(1))

    return ('unknown', uid)


def _find_client_id(conn, uid_type: str, uid_value: str) -> int | None:
    """Given a parsed UID, walk up to find the client_id."""
    if uid_type == 'client':
        row = conn.execute(
            "SELECT id FROM clients WHERE cn_number LIKE ? OR cn_number LIKE ?",
            (uid_value, f"CN{uid_value}"),
        ).fetchone()
        return row["id"] if row else None

    if uid_type == 'policy':
        row = conn.execute("SELECT client_id FROM policies WHERE policy_uid = ?", (uid_value,)).fetchone()
        return row["client_id"] if row else None

    if uid_type == 'policy_compact':
        # POL20250441 → try matching stripped policy_uid
        row = conn.execute(
            "SELECT client_id FROM policies WHERE REPLACE(policy_uid, '-', '') = ?",
            (uid_value,),
        ).fetchone()
        return row["client_id"] if row else None

    if uid_type == 'cor':
        row = conn.execute(
            "SELECT client_id FROM activity_log WHERE thread_id = ? LIMIT 1",
            (int(uid_value),),
        ).fetchone()
        return row["client_id"] if row else None

    if uid_type == 'activity':
        row = conn.execute("SELECT client_id FROM activity_log WHERE id = ?", (int(uid_value),)).fetchone()
        return row["client_id"] if row else None

    if uid_type == 'inb':
        row = conn.execute("SELECT client_id, activity_id FROM inbox WHERE id = ?", (int(uid_value),)).fetchone()
        if row and row["client_id"]:
            return row["client_id"]
        if row and row["activity_id"]:
            act = conn.execute("SELECT client_id FROM activity_log WHERE id = ?", (row["activity_id"],)).fetchone()
            return act["client_id"] if act else None
        return None

    if uid_type == 'rfi':
        row = conn.execute("SELECT client_id FROM client_request_bundles WHERE rfi_uid = ?", (uid_value,)).fetchone()
        return row["client_id"] if row else None

    return None


def resolve_ref_tree(conn, uid_string: str) -> dict | None:
    """Resolve any UID into a full reference tree."""
    uid_type, uid_value = _parse_uid(uid_string)
    if uid_type == 'unknown':
        return None

    client_id = _find_client_id(conn, uid_type, uid_value)
    if not client_id:
        return None

    # Get client info
    client = conn.execute("SELECT id, name, cn_number FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        return None

    cn = client["cn_number"] or ""
    cn_clean = re.sub(r'^[Cc][Nn]', '', cn) if cn else str(client_id)
    client_uid = f"CN{cn_clean}" if cn_clean else f"C{client_id}"

    # Get policies
    policies = []
    for p in conn.execute(
        "SELECT id, policy_uid, policy_type, carrier FROM policies WHERE client_id = ? AND archived = 0 ORDER BY policy_type",
        (client_id,),
    ).fetchall():
        # Get COR threads for this policy
        threads = []
        thread_rows = conn.execute(
            """SELECT DISTINCT thread_id FROM activity_log
               WHERE policy_id = ? AND thread_id IS NOT NULL AND thread_id > 0
               ORDER BY thread_id""",
            (p["id"],),
        ).fetchall()
        for t in thread_rows:
            activities = [dict(a) for a in conn.execute(
                """SELECT id, subject, activity_date, activity_type
                   FROM activity_log WHERE thread_id = ? ORDER BY activity_date""",
                (t["thread_id"],),
            ).fetchall()]
            threads.append({
                "thread_id": t["thread_id"], "uid": f"COR-{t['thread_id']}",
                "activity_count": len(activities), "activities": activities,
            })

        # Get standalone activities (no thread, on this policy)
        standalone = [dict(a) for a in conn.execute(
            """SELECT id, subject, activity_date, activity_type
               FROM activity_log WHERE policy_id = ? AND (thread_id IS NULL OR thread_id = 0)
               ORDER BY activity_date DESC LIMIT 10""",
            (p["id"],),
        ).fetchall()]

        policies.append({
            "id": p["id"], "uid": p["policy_uid"], "type": p["policy_type"],
            "carrier": p["carrier"] or "", "threads": threads,
            "standalone_activities": standalone,
        })

    # Get client-level COR threads (no policy)
    client_threads = []
    ct_rows = conn.execute(
        """SELECT DISTINCT thread_id FROM activity_log
           WHERE client_id = ? AND (policy_id IS NULL OR policy_id = 0)
           AND thread_id IS NOT NULL AND thread_id > 0
           ORDER BY thread_id""",
        (client_id,),
    ).fetchall()
    for t in ct_rows:
        activities = [dict(a) for a in conn.execute(
            "SELECT id, subject, activity_date, activity_type FROM activity_log WHERE thread_id = ? ORDER BY activity_date",
            (t["thread_id"],),
        ).fetchall()]
        client_threads.append({
            "thread_id": t["thread_id"], "uid": f"COR-{t['thread_id']}",
            "activity_count": len(activities), "activities": activities,
        })

    # Get RFI bundles
    rfis = [dict(r) for r in conn.execute(
        """SELECT id, rfi_uid, title, status,
           (SELECT COUNT(*) FROM client_request_items WHERE bundle_id = client_request_bundles.id) AS item_count,
           (SELECT COUNT(*) FROM client_request_items WHERE bundle_id = client_request_bundles.id AND received = 1) AS received_count
           FROM client_request_bundles WHERE client_id = ? ORDER BY created_at DESC""",
        (client_id,),
    ).fetchall()]

    # Get inbox items
    inbox_items = [dict(i) for i in conn.execute(
        """SELECT i.id, i.inbox_uid, i.content, i.status, i.activity_id, a.subject AS activity_subject
           FROM inbox i LEFT JOIN activity_log a ON i.activity_id = a.id
           WHERE i.client_id = ? ORDER BY i.created_at DESC LIMIT 20""",
        (client_id,),
    ).fetchall()]

    return {
        "client": {"id": client["id"], "name": client["name"], "cn_number": cn, "uid": client_uid},
        "policies": policies,
        "client_threads": client_threads,
        "rfis": rfis,
        "inbox_items": inbox_items,
        "highlight": uid_string.strip(),
    }


@router.get("/ref-lookup", response_class=HTMLResponse)
def ref_lookup_page(request: Request, q: str = "", conn=Depends(get_db)):
    """Ref tree lookup page."""
    tree = None
    if q.strip():
        tree = resolve_ref_tree(conn, q.strip())
    return templates.TemplateResponse("ref_lookup.html", {
        "request": request,
        "active": "ref-lookup",
        "q": q,
        "tree": tree,
    })
