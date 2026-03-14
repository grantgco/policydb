"""Contacts management route — global registry of placement team contacts."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb.web.app import get_db, templates

router = APIRouter(prefix="/contacts")

_CONTACT_BASE_SQL = """
    SELECT pc.name,
           MAX(pc.email)        AS email,
           MAX(pc.phone)        AS phone,
           MAX(pc.organization) AS organization,
           MAX(pc.role)         AS role,
           COUNT(DISTINCT pc.policy_id) AS policy_count
    FROM policy_contacts pc
    JOIN policies p ON pc.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE pc.name IS NOT NULL AND pc.name != ''
      AND p.archived = 0
    GROUP BY LOWER(TRIM(pc.name))
    ORDER BY LOWER(TRIM(pc.organization)) ASC, LOWER(TRIM(pc.name)) ASC
"""

_POLICY_DETAIL_SQL = """
    SELECT LOWER(TRIM(pc.name)) AS name_key,
           p.policy_uid, p.policy_type, p.carrier,
           p.expiration_date, p.target_effective_date,
           p.is_opportunity, p.opportunity_status,
           c.id AS client_id, c.name AS client_name
    FROM policy_contacts pc
    JOIN policies p ON pc.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE pc.name IS NOT NULL AND pc.name != ''
      AND p.archived = 0
    ORDER BY c.name ASC, p.policy_type ASC
"""


def _attach_policies(contacts: list[dict], conn) -> list[dict]:
    """Fetch structured policy data for all contacts and attach as c['policies']."""
    policy_rows = conn.execute(_POLICY_DETAIL_SQL).fetchall()
    by_name: dict[str, list] = {}
    for r in policy_rows:
        by_name.setdefault(r["name_key"], []).append(dict(r))
    for c in contacts:
        c["policies"] = by_name.get(c["name"].lower().strip(), [])
    return contacts


def _get_all_contacts(conn) -> list[dict]:
    """Return deduplicated contacts with attached policy list."""
    contacts = [dict(r) for r in conn.execute(_CONTACT_BASE_SQL).fetchall()]
    return _attach_policies(contacts, conn)


def _contact_policies(conn, name: str) -> list[dict]:
    """Return structured policy list for a single contact by name."""
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier,
                  p.expiration_date, p.target_effective_date,
                  p.is_opportunity, p.opportunity_status,
                  c.id AS client_id, c.name AS client_name
           FROM policy_contacts pc
           JOIN policies p ON pc.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(?)) AND p.archived = 0
           ORDER BY c.name ASC, p.policy_type ASC""",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


_INTERNAL_BASE_SQL = """
    SELECT LOWER(TRIM(cc.name)) AS name_key,
           cc.name,
           MAX(cc.title) AS title,
           MAX(cc.email) AS email,
           MAX(cc.phone) AS phone,
           MAX(cc.role)  AS role,
           COUNT(DISTINCT cc.client_id) AS client_count
    FROM client_contacts cc
    WHERE cc.contact_type = 'internal'
      AND cc.name IS NOT NULL AND cc.name != ''
    GROUP BY LOWER(TRIM(cc.name))
    ORDER BY LOWER(TRIM(cc.name))
"""

_INTERNAL_CLIENT_SQL = """
    SELECT LOWER(TRIM(cc.name)) AS name_key,
           c.id AS client_id, c.name AS client_name,
           cc.assignment
    FROM client_contacts cc
    JOIN clients c ON cc.client_id = c.id
    WHERE cc.contact_type = 'internal'
      AND cc.name IS NOT NULL AND cc.name != ''
    ORDER BY c.name
"""

_INTERNAL_POLICY_SQL = """
    SELECT LOWER(TRIM(cc.name)) AS name_key,
           p.policy_uid, p.policy_type, p.carrier,
           p.expiration_date, p.is_opportunity,
           c.id AS client_id, c.name AS client_name
    FROM client_contacts cc
    JOIN policy_contacts pc ON LOWER(TRIM(pc.name)) = LOWER(TRIM(cc.name))
    JOIN policies p ON pc.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE cc.contact_type = 'internal'
      AND cc.name IS NOT NULL AND cc.name != ''
      AND p.archived = 0
    ORDER BY c.name, p.policy_type
"""


def _attach_clients(internal: list[dict], conn) -> list[dict]:
    """Attach per-client assignments and policy cross-references to each internal contact."""
    client_rows = conn.execute(_INTERNAL_CLIENT_SQL).fetchall()
    by_name: dict[str, list] = {}
    for r in client_rows:
        by_name.setdefault(r["name_key"], []).append(dict(r))

    policy_rows = conn.execute(_INTERNAL_POLICY_SQL).fetchall()
    by_name_pol: dict[str, list] = {}
    for r in policy_rows:
        by_name_pol.setdefault(r["name_key"], []).append(dict(r))

    for c in internal:
        key = c["name"].lower().strip()
        c["clients"] = by_name.get(key, [])
        c["also_on_policies"] = by_name_pol.get(key, [])
        c["policy_cross_count"] = len(c["also_on_policies"])
    return internal


def _internal_contact_policies(conn, name: str) -> list[dict]:
    """Return policies where this internal contact also appears as a policy_contact."""
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier,
                  p.expiration_date, p.is_opportunity,
                  c.id AS client_id, c.name AS client_name
           FROM policy_contacts pc
           JOIN policies p ON pc.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(?)) AND p.archived = 0
           ORDER BY c.name, p.policy_type""",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_internal_contacts(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(_INTERNAL_BASE_SQL).fetchall()]
    return _attach_clients(rows, conn)


def _internal_contact_clients(conn, name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id AS client_id, c.name AS client_name, cc.assignment
           FROM client_contacts cc
           JOIN clients c ON cc.client_id = c.id
           WHERE LOWER(TRIM(cc.name)) = LOWER(TRIM(?)) AND cc.contact_type = 'internal'
           ORDER BY c.name""",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("", response_class=HTMLResponse)
def contacts_list(request: Request, q: str = "", org: str = "", conn=Depends(get_db)):
    contacts = _get_all_contacts(conn)
    internal = _get_internal_contacts(conn)

    # Collect orgs before filtering
    all_orgs = sorted({c["organization"] for c in contacts if c["organization"]})

    # All clients for the "Assign to client" picker on internal contact rows
    all_clients_json = _json.dumps([
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
        ).fetchall()
    ])

    # Filter placement contacts
    if q:
        q_lower = q.lower()
        contacts = [c for c in contacts if q_lower in (c["name"] or "").lower()
                    or q_lower in (c["email"] or "").lower()
                    or q_lower in (c["organization"] or "").lower()]
        internal = [c for c in internal if q_lower in (c["name"] or "").lower()
                    or q_lower in (c["email"] or "").lower()
                    or q_lower in (c["role"] or "").lower()]
    if org:
        contacts = [c for c in contacts if (c["organization"] or "").lower() == org.lower()]

    return templates.TemplateResponse("contacts/list.html", {
        "request": request,
        "active": "contacts",
        "contacts": contacts,
        "internal_contacts": internal,
        "q": q,
        "org": org,
        "all_orgs": all_orgs,
        "all_clients_json": all_clients_json,
    })


@router.get("/{name}/edit", response_class=HTMLResponse)
def contact_edit_form(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: inline edit form for a contact row."""
    row = conn.execute(
        """SELECT pc.name, MAX(pc.email) AS email, MAX(pc.phone) AS phone,
                  MAX(pc.organization) AS organization, MAX(pc.role) AS role
           FROM policy_contacts pc
           WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(?))
           GROUP BY LOWER(TRIM(pc.name))""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("contacts/_edit_row.html", {
        "request": request,
        "c": dict(row),
    })


@router.post("/{name}/edit", response_class=HTMLResponse)
def contact_edit_save(
    request: Request,
    name: str,
    email: str = Form(""),
    phone: str = Form(""),
    organization: str = Form(""),
    role: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save contact updates across all policy_contacts rows with this name."""
    conn.execute(
        """UPDATE policy_contacts
           SET email = ?, phone = ?, organization = ?, role = ?
           WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))""",
        (email.strip() or None, phone.strip() or None,
         organization.strip() or None, role.strip() or None,
         name),
    )
    conn.commit()
    row = conn.execute(
        """SELECT pc.name, MAX(pc.email) AS email, MAX(pc.phone) AS phone,
                  MAX(pc.organization) AS organization, MAX(pc.role) AS role,
                  COUNT(DISTINCT pc.policy_id) AS policy_count
           FROM policy_contacts pc
           JOIN policies p ON pc.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(?)) AND p.archived = 0
           GROUP BY LOWER(TRIM(pc.name))""",
        (name,),
    ).fetchone()
    c = dict(row) if row else {"name": name, "policy_count": 0}
    c["policies"] = _contact_policies(conn, name)
    return templates.TemplateResponse("contacts/_row.html", {
        "request": request,
        "c": c,
    })


@router.get("/internal/{name}/edit", response_class=HTMLResponse)
def internal_contact_edit_form(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: inline edit form for an internal team member's shared contact info."""
    row = conn.execute(
        """SELECT cc.name, MAX(cc.title) AS title, MAX(cc.email) AS email,
                  MAX(cc.phone) AS phone, MAX(cc.role) AS role,
                  COUNT(DISTINCT cc.client_id) AS client_count
           FROM client_contacts cc
           WHERE LOWER(TRIM(cc.name)) = LOWER(TRIM(?)) AND cc.contact_type = 'internal'
           GROUP BY LOWER(TRIM(cc.name))""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse("contacts/_internal_edit_row.html", {
        "request": request,
        "c": dict(row),
    })


@router.post("/internal/{name}/edit", response_class=HTMLResponse)
def internal_contact_edit_save(
    request: Request,
    name: str,
    title: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    conn=Depends(get_db),
):
    """HTMX: save shared contact info across all client_contacts rows with this name."""
    conn.execute(
        """UPDATE client_contacts SET title=?, role=?, email=?, phone=?
           WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND contact_type='internal'""",
        (title.strip() or None, role.strip() or None,
         email.strip() or None, phone.strip() or None,
         name),
    )
    conn.commit()
    row = conn.execute(
        """SELECT cc.name, MAX(cc.title) AS title, MAX(cc.email) AS email,
                  MAX(cc.phone) AS phone, MAX(cc.role) AS role,
                  COUNT(DISTINCT cc.client_id) AS client_count
           FROM client_contacts cc
           JOIN clients c ON cc.client_id = c.id
           WHERE LOWER(TRIM(cc.name)) = LOWER(TRIM(?)) AND cc.contact_type = 'internal'
           GROUP BY LOWER(TRIM(cc.name))""",
        (name,),
    ).fetchone()
    c = dict(row) if row else {"name": name, "client_count": 0}
    c["clients"] = _internal_contact_clients(conn, name)
    c["also_on_policies"] = _internal_contact_policies(conn, name)
    c["policy_cross_count"] = len(c["also_on_policies"])
    return templates.TemplateResponse("contacts/_internal_row.html", {
        "request": request,
        "c": c,
    })


@router.get("/internal/{name}/row", response_class=HTMLResponse)
def internal_contact_row(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: restore internal contact display row (Cancel button)."""
    row = conn.execute(
        """SELECT cc.name, MAX(cc.title) AS title, MAX(cc.email) AS email,
                  MAX(cc.phone) AS phone, MAX(cc.role) AS role,
                  COUNT(DISTINCT cc.client_id) AS client_count
           FROM client_contacts cc
           JOIN clients c ON cc.client_id = c.id
           WHERE LOWER(TRIM(cc.name)) = LOWER(TRIM(?)) AND cc.contact_type = 'internal'
           GROUP BY LOWER(TRIM(cc.name))""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    c = dict(row)
    c["clients"] = _internal_contact_clients(conn, name)
    c["also_on_policies"] = _internal_contact_policies(conn, name)
    c["policy_cross_count"] = len(c["also_on_policies"])
    return templates.TemplateResponse("contacts/_internal_row.html", {
        "request": request,
        "c": c,
    })


@router.get("/{name}/row", response_class=HTMLResponse)
def contact_row(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: restore display row (Cancel button)."""
    row = conn.execute(
        """SELECT pc.name, MAX(pc.email) AS email, MAX(pc.phone) AS phone,
                  MAX(pc.organization) AS organization, MAX(pc.role) AS role,
                  COUNT(DISTINCT pc.policy_id) AS policy_count
           FROM policy_contacts pc
           JOIN policies p ON pc.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE LOWER(TRIM(pc.name)) = LOWER(TRIM(?)) AND p.archived = 0
           GROUP BY LOWER(TRIM(pc.name))""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    c = dict(row)
    c["policies"] = _contact_policies(conn, name)
    return templates.TemplateResponse("contacts/_row.html", {
        "request": request,
        "c": c,
    })
