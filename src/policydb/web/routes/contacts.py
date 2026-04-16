"""Contacts management route — global registry of contacts (unified schema)."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from policydb import config as cfg
from policydb.queries import (
    get_contacts_for_client,
    get_or_create_contact,
    merge_contacts,
    search_contacts,
)
from policydb.utils import clean_email, format_phone
from policydb.web.app import get_db, templates

router = APIRouter(prefix="/contacts")


# ---------------------------------------------------------------------------
# Helpers: resolve contact name → contacts.id
# ---------------------------------------------------------------------------

def _resolve_contact_id(conn, name: str) -> int | None:
    """Find contact id by name in unified contacts table."""
    row = conn.execute(
        "SELECT id FROM contacts WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))", (name,)
    ).fetchone()
    return row["id"] if row else None


def _find_similar_contacts(conn, name: str, threshold: int = 85, source: str = "client") -> list[dict]:
    """Find existing contacts with names similar to the given name using fuzzy matching."""
    from rapidfuzz import fuzz
    existing = conn.execute(
        """SELECT co.id, co.name, co.email, co.phone,
                  GROUP_CONCAT(DISTINCT c.name) AS client_names
           FROM contacts co
           LEFT JOIN contact_client_assignments cca ON cca.contact_id = co.id
           LEFT JOIN clients c ON cca.client_id = c.id
           GROUP BY co.id"""
    ).fetchall()
    matches = []
    for r in existing:
        score = fuzz.WRatio(name.strip(), r["name"])
        if score >= threshold:
            matches.append({
                "id": r["id"], "name": r["name"],
                "email": r["email"], "phone": r["phone"],
                "client_names": r["client_names"] or "",
                "score": round(score),
                "match_type": "name",
                "source": source,
            })
    return sorted(matches, key=lambda x: -x["score"])


# ---------------------------------------------------------------------------
# AI Contact Import (directory-level — no client association)
# ---------------------------------------------------------------------------

_DIRECTORY_CONTACT_IMPORT_CACHE: dict = {}


@router.get("/ai-import/prompt", response_class=HTMLResponse)
def contacts_ai_import_prompt(request: Request):
    """Return the AI import panel with contact extraction prompt."""
    import json as _j
    from policydb.llm_schemas import CONTACT_BULK_IMPORT_SCHEMA

    prompt_text = (
        "Extract contacts from the email text below. Return a JSON array of objects.\n\n"
        "Each object should have these fields (all optional except name):\n"
        '  name (required), email, phone, mobile, organization, title\n\n'
        "Look for:\n"
        "- Email signature blocks\n"
        "- CC/To recipients\n"
        "- Names mentioned in the body with roles or titles\n"
        "- Phone numbers near names\n\n"
        "Return ONLY the JSON array, no other text."
    )

    example = {}
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        if f.get("example") and f["key"] not in ("contact_type", "role"):
            example[f["key"]] = f["example"]
    json_template = _j.dumps([example], indent=2)

    return templates.TemplateResponse("_ai_import_panel.html", {
        "request": request,
        "import_type": "directory_contacts",
        "prompt_text": prompt_text,
        "json_template": json_template,
        "context_display": {"Scope": "Global Directory"},
        "parse_url": "/contacts/ai-import/parse",
        "import_target": "#ai-contact-import-result",
    })


@router.post("/ai-import/parse", response_class=HTMLResponse)
def contacts_ai_import_parse(
    request: Request,
    json_text: str = Form(...),
    conn=Depends(get_db),
):
    """Parse LLM contact JSON and return review panel."""
    import time
    import uuid
    from policydb.llm_schemas import parse_contact_bulk_import_json

    result = parse_contact_bulk_import_json(json_text)
    if not result["ok"]:
        return HTMLResponse(
            f'<div class="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">'
            f'{result["error"]}</div>',
            status_code=422,
        )

    contacts = result["contacts"]
    warnings = result.get("warnings", [])

    # Check which contacts already exist globally
    for contact in contacts:
        existing = conn.execute(
            "SELECT id, email, phone, mobile, organization, title FROM contacts "
            "WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
            (contact["name"],),
        ).fetchone()
        contact["existing_contact"] = dict(existing) if existing else None

    token = str(uuid.uuid4())
    _DIRECTORY_CONTACT_IMPORT_CACHE[token] = (contacts, time.time())

    # Purge stale cache entries (>30 min)
    now = time.time()
    stale = [k for k, v in _DIRECTORY_CONTACT_IMPORT_CACHE.items() if now - v[1] > 1800]
    for k in stale:
        _DIRECTORY_CONTACT_IMPORT_CACHE.pop(k, None)

    return templates.TemplateResponse("contacts/_ai_contacts_review.html", {
        "request": request,
        "contacts": contacts,
        "warnings": warnings,
        "token": token,
        "contact_roles": cfg.get("contact_roles", []),
    })


@router.post("/ai-import/apply", response_class=HTMLResponse)
async def contacts_ai_import_apply(request: Request, conn=Depends(get_db)):
    """Apply selected contacts from AI import to the global directory."""
    form = await request.form()
    token = form.get("token", "")

    cache = _DIRECTORY_CONTACT_IMPORT_CACHE.get(token)
    if not cache:
        return HTMLResponse(
            '<div class="p-4 text-red-600 text-sm">Session expired — please re-parse.</div>'
        )

    contacts, ts = cache
    created = 0
    updated = 0
    errors: list[str] = []

    for i, contact in enumerate(contacts):
        if not form.get(f"select_{i}"):
            continue

        name = form.get(f"name_{i}", contact.get("name", "")).strip()
        email = form.get(f"email_{i}", contact.get("email", "")).strip()
        phone = form.get(f"phone_{i}", contact.get("phone", "")).strip()
        mobile = form.get(f"mobile_{i}", contact.get("mobile", "")).strip()
        org = form.get(f"org_{i}", contact.get("organization", "")).strip()
        title = form.get(f"title_{i}", contact.get("title", "")).strip()

        if not name:
            continue

        try:
            if email:
                email = clean_email(email)
            if phone:
                phone = format_phone(phone)
            if mobile:
                mobile = format_phone(mobile)

            contact_id_str = form.get(f"contact_id_{i}", "").strip()

            if contact_id_str:
                # Existing contact — update only fields the user opted to overwrite
                cid = int(contact_id_str)
                field_updates: list[str] = []
                params: list = []
                for field, value, flag in [
                    ("email", email, f"overwrite_email_{i}"),
                    ("phone", phone, f"overwrite_phone_{i}"),
                    ("mobile", mobile, f"overwrite_mobile_{i}"),
                    ("organization", org, f"overwrite_org_{i}"),
                    ("title", title, f"overwrite_title_{i}"),
                ]:
                    if value and form.get(flag):
                        field_updates.append(f"{field}=?")
                        params.append(value)
                if field_updates:
                    field_updates.append("updated_at=CURRENT_TIMESTAMP")
                    params.append(cid)
                    conn.execute(
                        f"UPDATE contacts SET {', '.join(field_updates)} WHERE id=?", params
                    )
                    updated += 1
            else:
                # New contact
                cid = get_or_create_contact(
                    conn, name,
                    email=email or None,
                    phone=phone or None,
                    mobile=mobile or None,
                    organization=org or None,
                )
                if title:
                    conn.execute(
                        "UPDATE contacts SET title = ? WHERE id = ?", (title, cid)
                    )
                created += 1
        except Exception as e:
            errors.append(f"{name}: {e}")

    conn.commit()
    _DIRECTORY_CONTACT_IMPORT_CACHE.pop(token, None)

    total = created + updated
    parts = [
        '<div class="p-4 space-y-3">',
        '<div class="flex items-center gap-2">',
        '<svg class="w-5 h-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">',
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>',
        "</svg>",
        f'<span class="text-sm font-medium text-gray-900">'
        f'{total} contact{"s" if total != 1 else ""} imported to directory</span>',
        "</div>",
        '<div class="flex gap-2">',
    ]
    if created:
        parts.append(
            f'<span class="px-2 py-0.5 rounded-full text-xs bg-green-50 text-green-700">'
            f"{created} created</span>"
        )
    if updated:
        parts.append(
            f'<span class="px-2 py-0.5 rounded-full text-xs bg-blue-50 text-blue-700">'
            f"{updated} updated</span>"
        )
    parts.append("</div>")
    if errors:
        parts.append('<div class="mt-2 text-xs text-red-600">')
        for err in errors:
            parts.append(f"<p>{err}</p>")
        parts.append("</div>")
    parts.append(
        '<button type="button" onclick="closeAiImport(); window.location.reload();" '
        'class="mt-3 text-xs text-marsh hover:underline">Close &amp; refresh</button>'
    )
    parts.append("</div>")
    return HTMLResponse("".join(parts))


# ---------------------------------------------------------------------------
# Autocomplete endpoint (already uses new schema via queries.py)
# ---------------------------------------------------------------------------

@router.get("/suggest")
def contact_suggest(client_id: int = 0, conn=Depends(get_db)):
    """Return contacts for a client as JSON for autocomplete."""
    if not client_id:
        return JSONResponse([])
    contacts = get_contacts_for_client(conn, client_id)
    return JSONResponse(contacts)


# ---------------------------------------------------------------------------
# Placement contacts — contacts table joined via contact_policy_assignments
# ---------------------------------------------------------------------------

_CONTACT_BASE_SQL = """
    SELECT co.id AS contact_id,
           co.name,
           co.email,
           co.phone,
           co.mobile,
           co.organization,
           MAX(cpa.role)  AS role,
           MAX(cpa.title) AS title,
           MAX(cpa.notes) AS notes,
           COUNT(DISTINCT cpa.policy_id) AS policy_count
    FROM contacts co
    JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
    JOIN policies p ON cpa.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE co.name IS NOT NULL AND co.name != ''
      AND p.archived = 0
    GROUP BY co.id
    ORDER BY LOWER(TRIM(co.organization)) ASC, LOWER(TRIM(co.name)) ASC
"""

_POLICY_DETAIL_SQL = """
    SELECT co.id AS contact_id,
           p.policy_uid, p.policy_type, p.carrier,
           p.expiration_date, p.target_effective_date,
           p.is_opportunity, p.opportunity_status,
           c.id AS client_id, c.name AS client_name
    FROM contacts co
    JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
    JOIN policies p ON cpa.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE co.name IS NOT NULL AND co.name != ''
      AND p.archived = 0
    ORDER BY c.name ASC, p.policy_type ASC
"""


def _attach_policies(contacts: list[dict], conn) -> list[dict]:
    """Fetch structured policy data for all contacts and attach as c['policies']."""
    policy_rows = conn.execute(_POLICY_DETAIL_SQL).fetchall()
    by_id: dict[int, list] = {}
    for r in policy_rows:
        by_id.setdefault(r["contact_id"], []).append(dict(r))
    for c in contacts:
        c["policies"] = by_id.get(c["contact_id"], [])
    return contacts


def _get_all_contacts(conn) -> list[dict]:
    """Return deduplicated placement contacts with attached policy list."""
    contacts = [dict(r) for r in conn.execute(_CONTACT_BASE_SQL).fetchall()]
    return _attach_policies(contacts, conn)


def _contact_policies(conn, contact_id: int) -> list[dict]:
    """Return structured policy list for a single contact by id."""
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier,
                  p.expiration_date, p.target_effective_date,
                  p.is_opportunity, p.opportunity_status,
                  c.id AS client_id, c.name AS client_name
           FROM contact_policy_assignments cpa
           JOIN policies p ON cpa.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE cpa.contact_id = ? AND p.archived = 0
           ORDER BY c.name ASC, p.policy_type ASC""",
        (contact_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal contacts — contacts table joined via contact_client_assignments
# ---------------------------------------------------------------------------

_INTERNAL_BASE_SQL = """
    SELECT co.id AS contact_id,
           co.name,
           MAX(cca.title)  AS title,
           co.email,
           co.phone,
           co.mobile,
           MAX(cca.role)   AS role,
           MAX(cca.notes)  AS notes,
           COUNT(DISTINCT cca.client_id) AS client_count
    FROM contacts co
    JOIN contact_client_assignments cca ON co.id = cca.contact_id
    WHERE cca.contact_type = 'internal'
      AND co.name IS NOT NULL AND co.name != ''
    GROUP BY co.id
    ORDER BY LOWER(TRIM(co.name))
"""

_INTERNAL_CLIENT_SQL = """
    SELECT co.id AS contact_id,
           c.id AS client_id, c.name AS client_name,
           cca.assignment
    FROM contacts co
    JOIN contact_client_assignments cca ON co.id = cca.contact_id
    JOIN clients c ON cca.client_id = c.id
    WHERE cca.contact_type = 'internal'
      AND co.name IS NOT NULL AND co.name != ''
    ORDER BY c.name
"""

_INTERNAL_POLICY_SQL = """
    SELECT co.id AS contact_id,
           p.policy_uid, p.policy_type, p.carrier,
           p.expiration_date, p.is_opportunity,
           c.id AS client_id, c.name AS client_name
    FROM contacts co
    JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
    JOIN policies p ON cpa.policy_id = p.id
    JOIN clients c ON p.client_id = c.id
    WHERE co.id IN (
        SELECT contact_id FROM contact_client_assignments WHERE contact_type = 'internal'
    )
      AND co.name IS NOT NULL AND co.name != ''
      AND p.archived = 0
    ORDER BY c.name, p.policy_type
"""


def _attach_clients(internal: list[dict], conn) -> list[dict]:
    """Attach per-client assignments and policy cross-references to each internal contact."""
    client_rows = conn.execute(_INTERNAL_CLIENT_SQL).fetchall()
    by_id: dict[int, list] = {}
    for r in client_rows:
        by_id.setdefault(r["contact_id"], []).append(dict(r))

    policy_rows = conn.execute(_INTERNAL_POLICY_SQL).fetchall()
    by_id_pol: dict[int, list] = {}
    for r in policy_rows:
        by_id_pol.setdefault(r["contact_id"], []).append(dict(r))

    for c in internal:
        cid = c["contact_id"]
        c["clients"] = by_id.get(cid, [])
        c["also_on_policies"] = by_id_pol.get(cid, [])
        c["policy_cross_count"] = len(c["also_on_policies"])
    return internal


def _internal_contact_policies(conn, contact_id: int) -> list[dict]:
    """Return policies where this internal contact also appears as a policy contact."""
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier,
                  p.expiration_date, p.is_opportunity,
                  c.id AS client_id, c.name AS client_name
           FROM contact_policy_assignments cpa
           JOIN policies p ON cpa.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE cpa.contact_id = ? AND p.archived = 0
           ORDER BY c.name, p.policy_type""",
        (contact_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_internal_contacts(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(_INTERNAL_BASE_SQL).fetchall()]
    return _attach_clients(rows, conn)


def _internal_contact_clients(conn, contact_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id AS client_id, c.name AS client_name, cca.assignment
           FROM contact_client_assignments cca
           JOIN clients c ON cca.client_id = c.id
           WHERE cca.contact_id = ? AND cca.contact_type = 'internal'
           ORDER BY c.name""",
        (contact_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Client-type contacts (contact_type='client' in contact_client_assignments)
# ---------------------------------------------------------------------------

_CLIENT_TYPE_BASE_SQL = """
    SELECT co.id AS contact_id,
           co.name,
           MAX(cca.title)  AS title,
           co.email,
           co.phone,
           co.mobile,
           MAX(cca.role)   AS role,
           MAX(cca.notes)  AS notes,
           co.organization,
           COUNT(DISTINCT cca.client_id) AS client_count
    FROM contacts co
    JOIN contact_client_assignments cca ON co.id = cca.contact_id
    WHERE cca.contact_type = 'client'
      AND co.name IS NOT NULL AND co.name != ''
    GROUP BY co.id
    ORDER BY LOWER(TRIM(co.name))
"""

_CLIENT_TYPE_CLIENT_SQL = """
    SELECT co.id AS contact_id,
           c.id AS client_id, c.name AS client_name
    FROM contacts co
    JOIN contact_client_assignments cca ON co.id = cca.contact_id
    JOIN clients c ON cca.client_id = c.id
    WHERE cca.contact_type = 'client'
      AND co.name IS NOT NULL AND co.name != ''
    ORDER BY c.name
"""


def _attach_client_type_clients(contacts: list[dict], conn) -> list[dict]:
    """Attach per-client list to each client-type contact."""
    client_rows = conn.execute(_CLIENT_TYPE_CLIENT_SQL).fetchall()
    by_id: dict[int, list] = {}
    for r in client_rows:
        by_id.setdefault(r["contact_id"], []).append(dict(r))
    for c in contacts:
        c["clients"] = by_id.get(c["contact_id"], [])
    return contacts


def _get_client_type_contacts(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(_CLIENT_TYPE_BASE_SQL).fetchall()]
    return _attach_client_type_clients(rows, conn)


def _client_type_contact_clients(conn, contact_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id AS client_id, c.name AS client_name
           FROM contact_client_assignments cca
           JOIN clients c ON cca.client_id = c.id
           WHERE cca.contact_id = ? AND cca.contact_type = 'client'
           ORDER BY c.name""",
        (contact_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Main listing page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def contacts_list(request: Request, q: str = "", org: str = "", role: str = "", client_filter: str = "", line: str = "", industry: str = "", conn=Depends(get_db)):
    contacts = _get_all_contacts(conn)
    internal = _get_internal_contacts(conn)
    client_type = _get_client_type_contacts(conn)

    # Collect orgs before filtering
    all_orgs = sorted({c["organization"] for c in contacts if c["organization"]})

    # All clients for pickers and filter dropdown
    _all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    all_clients_json = _json.dumps([{"id": c["id"], "name": c["name"]} for c in _all_clients])

    # Cross-store badge sets: build contact_id→stores lookup
    _placement_ids = {c["contact_id"] for c in contacts}
    _internal_ids = {c["contact_id"] for c in internal}
    _client_ids = {c["contact_id"] for c in client_type}

    # Also maintain name-based sets for template backward compat
    _placement_names = {c["name"].lower().strip() for c in contacts if c.get("name")}
    _internal_names = {c["name"].lower().strip() for c in internal if c.get("name")}
    _client_names = {c["name"].lower().strip() for c in client_type if c.get("name")}

    for c in contacts:
        cid = c["contact_id"]
        key = (c["name"] or "").lower().strip()
        c["also_team"] = cid in _internal_ids or key in _internal_names
        c["also_client"] = cid in _client_ids or key in _client_names
    for c in internal:
        cid = c["contact_id"]
        key = (c["name"] or "").lower().strip()
        c["also_placement"] = cid in _placement_ids or key in _placement_names
        c["also_client"] = cid in _client_ids or key in _client_names
    for c in client_type:
        cid = c["contact_id"]
        key = (c["name"] or "").lower().strip()
        c["also_placement"] = cid in _placement_ids or key in _placement_names
        c["also_team"] = cid in _internal_ids or key in _internal_names

    # Filter by text search
    if q:
        q_lower = q.lower()
        contacts = [c for c in contacts if q_lower in (c["name"] or "").lower()
                    or q_lower in (c["email"] or "").lower()
                    or q_lower in (c["organization"] or "").lower()]
        internal = [c for c in internal if q_lower in (c["name"] or "").lower()
                    or q_lower in (c["email"] or "").lower()
                    or q_lower in (c["role"] or "").lower()]
        client_type = [c for c in client_type if q_lower in (c["name"] or "").lower()
                       or q_lower in (c["email"] or "").lower()
                       or q_lower in (c["role"] or "").lower()]
    if org:
        contacts = [c for c in contacts if (c["organization"] or "").lower() == org.lower()]
    if role:
        contacts = [c for c in contacts if (c.get("role") or "").lower() == role.lower()]
        internal = [c for c in internal if (c.get("role") or "").lower() == role.lower()]
        client_type = [c for c in client_type if (c.get("role") or "").lower() == role.lower()]

    # Filter by client
    if client_filter:
        try:
            cid = int(client_filter)
        except ValueError:
            cid = 0
        if cid:
            # Placement: contacts on policies belonging to this client
            pol_contact_ids = {r["contact_id"] for r in conn.execute(
                """SELECT DISTINCT cpa.contact_id
                   FROM contact_policy_assignments cpa
                   JOIN policies p ON cpa.policy_id = p.id
                   WHERE p.client_id = ? AND p.archived = 0""",
                (cid,),
            ).fetchall()}
            contacts = [c for c in contacts if c["contact_id"] in pol_contact_ids]
            # Internal: contacts assigned to this client as internal
            int_contact_ids = {r["contact_id"] for r in conn.execute(
                "SELECT DISTINCT contact_id FROM contact_client_assignments WHERE client_id=? AND contact_type='internal'",
                (cid,),
            ).fetchall()}
            internal = [c for c in internal if c["contact_id"] in int_contact_ids]
            # Client-type: contacts assigned to this client as client
            cli_contact_ids = {r["contact_id"] for r in conn.execute(
                "SELECT DISTINCT contact_id FROM contact_client_assignments WHERE client_id=? AND contact_type='client'",
                (cid,),
            ).fetchall()}
            client_type = [c for c in client_type if c["contact_id"] in cli_contact_ids]

    # Attach expertise tags to each contact group
    _attach_expertise(conn, contacts)
    _attach_expertise(conn, internal)
    _attach_expertise(conn, client_type)

    # Batch follow-up counts for placement, internal, and client-type contact lists
    _matrix_contact_ids = list({
        c["contact_id"] for c in contacts + internal + client_type if c.get("contact_id")
    })
    _matrix_fu_map: dict[int, int] = {}
    if _matrix_contact_ids:
        _mfp = ",".join("?" * len(_matrix_contact_ids))
        _mfu_rows = conn.execute(
            f"""SELECT contact_id, COUNT(*) AS cnt FROM activity_log
                WHERE contact_id IN ({_mfp})
                  AND follow_up_done = 0 AND follow_up_date IS NOT NULL
                GROUP BY contact_id""",
            _matrix_contact_ids,
        ).fetchall()
        _matrix_fu_map = {r["contact_id"]: r["cnt"] for r in _mfu_rows}
    for _c in contacts + internal + client_type:
        _c["open_followup_count"] = _matrix_fu_map.get(_c.get("contact_id", 0), 0)

    # Filter by expertise line
    if line:
        _line_ids = {r[0] for r in conn.execute(
            "SELECT contact_id FROM contact_expertise WHERE category='line' AND tag=?", (line,)
        ).fetchall()}
        contacts = [c for c in contacts if c.get("contact_id") in _line_ids]
        internal = [c for c in internal if c.get("contact_id") in _line_ids]
        client_type = [c for c in client_type if c.get("contact_id") in _line_ids]

    # Filter by expertise industry
    if industry:
        _industry_ids = {r[0] for r in conn.execute(
            "SELECT contact_id FROM contact_expertise WHERE category='industry' AND tag=?", (industry,)
        ).fetchall()}
        contacts = [c for c in contacts if c.get("contact_id") in _industry_ids]
        internal = [c for c in internal if c.get("contact_id") in _industry_ids]
        client_type = [c for c in client_type if c.get("contact_id") in _industry_ids]

    # Build unified "all people" list from contacts table
    _all_people_rows = conn.execute("""
        SELECT co.id AS contact_id, co.name, co.email, co.phone, co.mobile, co.organization,
               COALESCE(
                   (SELECT cca_t.title FROM contact_client_assignments cca_t WHERE cca_t.contact_id = co.id AND cca_t.title IS NOT NULL LIMIT 1),
                   (SELECT cpa_t.title FROM contact_policy_assignments cpa_t WHERE cpa_t.contact_id = co.id AND cpa_t.title IS NOT NULL LIMIT 1)
               ) AS title,
               COALESCE(
                   (SELECT cca_n.notes FROM contact_client_assignments cca_n WHERE cca_n.contact_id = co.id AND cca_n.notes IS NOT NULL LIMIT 1),
                   (SELECT cpa_n.notes FROM contact_policy_assignments cpa_n WHERE cpa_n.contact_id = co.id AND cpa_n.notes IS NOT NULL LIMIT 1)
               ) AS notes,
               (SELECT COUNT(DISTINCT cpa2.policy_id) FROM contact_policy_assignments cpa2
                JOIN policies p2 ON cpa2.policy_id = p2.id WHERE cpa2.contact_id = co.id AND p2.archived = 0) AS policy_count,
               (SELECT COUNT(DISTINCT cca_i.client_id) FROM contact_client_assignments cca_i
                WHERE cca_i.contact_id = co.id AND cca_i.contact_type = 'internal') AS internal_client_count,
               (SELECT COUNT(DISTINCT cca_c.client_id) FROM contact_client_assignments cca_c
                WHERE cca_c.contact_id = co.id AND cca_c.contact_type = 'client') AS client_count,
               (SELECT COUNT(*) FROM activity_log al
                WHERE al.contact_id = co.id AND al.follow_up_done = 0 AND al.follow_up_date IS NOT NULL) AS open_followups
        FROM contacts co
        WHERE co.name IS NOT NULL AND co.name != ''
        ORDER BY co.name
    """).fetchall()
    all_people = []
    for r in _all_people_rows:
        d = dict(r)
        d["is_placement"] = d["policy_count"] > 0
        d["is_internal"] = d["internal_client_count"] > 0
        d["is_client"] = d["client_count"] > 0
        d["store_count"] = sum([d["is_placement"], d["is_internal"], d["is_client"]])
        all_people.append(d)

    # Batch-fetch open follow-up details for all contacts that have them
    _fu_contact_ids = [c["contact_id"] for c in all_people if c["open_followups"] > 0]
    _followups_by_contact: dict[int, list] = {}
    if _fu_contact_ids:
        _fu_ph = ",".join("?" * len(_fu_contact_ids))
        _fu_rows = conn.execute(f"""
            SELECT a.id, a.contact_id, a.subject, a.follow_up_date, a.activity_type,
                   c.name AS client_name, c.id AS client_id,
                   p.policy_uid, p.policy_type,
                   CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
            FROM activity_log a
            JOIN clients c ON a.client_id = c.id
            LEFT JOIN policies p ON a.policy_id = p.id
            WHERE a.contact_id IN ({_fu_ph})
              AND a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
            ORDER BY a.follow_up_date ASC
        """, _fu_contact_ids).fetchall()
        for fr in _fu_rows:
            _followups_by_contact.setdefault(fr["contact_id"], []).append(dict(fr))
    for c in all_people:
        c["followups"] = _followups_by_contact.get(c["contact_id"], [])
    # Attach expertise to all_people
    _attach_expertise(conn, all_people)
    # Apply same text filter to unified list
    if q:
        q_lower = q.lower()
        all_people = [c for c in all_people if q_lower in (c["name"] or "").lower()
                      or q_lower in (c["email"] or "").lower()
                      or q_lower in (c["organization"] or "").lower()]
    if org:
        all_people = [c for c in all_people if (c["organization"] or "").lower() == org.lower()]
    if role:
        # Role filter uses placement/internal role fields
        _role_contact_ids = {r[0] for r in conn.execute(
            """SELECT DISTINCT contact_id FROM (
                SELECT cpa.contact_id FROM contact_policy_assignments cpa WHERE LOWER(cpa.role) = LOWER(?)
                UNION
                SELECT cca.contact_id FROM contact_client_assignments cca WHERE LOWER(cca.role) = LOWER(?)
            )""", (role, role)
        ).fetchall()}
        all_people = [c for c in all_people if c["contact_id"] in _role_contact_ids]
    if client_filter:
        try:
            _cf_id = int(client_filter)
        except ValueError:
            _cf_id = 0
        if _cf_id:
            _cf_ids = {r[0] for r in conn.execute(
                """SELECT DISTINCT contact_id FROM (
                    SELECT cpa.contact_id FROM contact_policy_assignments cpa
                    JOIN policies p ON cpa.policy_id = p.id WHERE p.client_id = ? AND p.archived = 0
                    UNION
                    SELECT cca.contact_id FROM contact_client_assignments cca WHERE cca.client_id = ?
                )""", (_cf_id, _cf_id)
            ).fetchall()}
            all_people = [c for c in all_people if c["contact_id"] in _cf_ids]
    # Filter all_people by expertise
    if line:
        all_people = [c for c in all_people if line in c.get("expertise_lines", [])]
    if industry:
        all_people = [c for c in all_people if industry in c.get("expertise_industries", [])]

    # Collect all roles for filter
    all_roles = sorted({r[0] for r in conn.execute(
        """SELECT DISTINCT role FROM (
            SELECT role FROM contact_policy_assignments WHERE role IS NOT NULL AND role != ''
            UNION
            SELECT role FROM contact_client_assignments WHERE role IS NOT NULL AND role != ''
        ) ORDER BY role"""
    ).fetchall()})

    return templates.TemplateResponse("contacts/list.html", {
        "request": request,
        "active": "contacts",
        "contacts": contacts,
        "internal_contacts": internal,
        "client_type_contacts": client_type,
        "all_people": all_people,
        "q": q,
        "org": org,
        "role": role,
        "client_filter": client_filter,
        "all_orgs": all_orgs,
        "all_roles": all_roles,
        "all_clients": _all_clients,
        "all_clients_json": all_clients_json,
        "contact_roles": cfg.get("contact_roles", []),
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "line_filter": line,
        "industry_filter": industry,
    })


# ---------------------------------------------------------------------------
# Search — unified across all stores via contacts table
# (MUST be before /{contact_id} to avoid route capture)
# ---------------------------------------------------------------------------

@router.get("/search", response_class=HTMLResponse)
def contacts_search(request: Request, q: str = "", context: str = "", target_id: str = "", conn=Depends(get_db)):
    """Unified contact search across all stores. Returns HTML partial for picker."""
    if len(q.strip()) < 2:
        return HTMLResponse('<p class="text-xs text-gray-400 py-2">Type at least 2 characters...</p>')

    rows = search_contacts(conn, q, limit=20)

    # Enrich each result with sources and assignment-level fields (title, role)
    results = []
    for r in rows:
        cid = r["id"]
        sources = []
        if conn.execute(
            "SELECT 1 FROM contact_policy_assignments WHERE contact_id=? LIMIT 1", (cid,)
        ).fetchone():
            sources.append("policy")
        int_row = conn.execute(
            "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='internal' LIMIT 1",
            (cid,),
        ).fetchone()
        cli_row = conn.execute(
            "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='client' LIMIT 1",
            (cid,),
        ).fetchone()
        if int_row:
            sources.append("team")
        if cli_row:
            sources.append("client")

        title = None
        role = None
        asg = conn.execute(
            "SELECT title, role FROM contact_client_assignments WHERE contact_id=? AND (title IS NOT NULL OR role IS NOT NULL) LIMIT 1",
            (cid,),
        ).fetchone()
        if asg:
            title = asg["title"]
            role = asg["role"]
        if not title or not role:
            pol_asg = conn.execute(
                "SELECT title, role FROM contact_policy_assignments WHERE contact_id=? AND (title IS NOT NULL OR role IS NOT NULL) LIMIT 1",
                (cid,),
            ).fetchone()
            if pol_asg:
                if not title:
                    title = pol_asg["title"]
                if not role:
                    role = pol_asg["role"]

        results.append({
            "name": r["name"],
            "email": r.get("email"),
            "phone": r.get("phone"),
            "mobile": r.get("mobile"),
            "title": title,
            "role": role,
            "organization": r.get("organization"),
            "sources": ",".join(sources) if sources else "",
        })

    return templates.TemplateResponse("contacts/_search_results.html", {
        "request": request,
        "results": results,
        "q": q,
        "context": context,
        "target_id": target_id,
    })


# ---------------------------------------------------------------------------
# Contact edit slideover
# ---------------------------------------------------------------------------

@router.get("/{contact_id}/edit-slideover", response_class=HTMLResponse)
def contact_edit_slideover(request: Request, contact_id: int, conn=Depends(get_db)):
    """Return the edit slideover partial for a contact."""
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    contact = dict(contact)
    _attach_expertise(conn, [contact])

    # Client assignments for context
    client_assignments = [dict(r) for r in conn.execute("""
        SELECT cca.contact_type, cca.role, cca.title, cca.assignment, cca.notes,
               c.name AS client_name, c.id AS client_id
        FROM contact_client_assignments cca
        JOIN clients c ON cca.client_id = c.id
        WHERE cca.contact_id = ? AND c.archived = 0
        ORDER BY c.name
    """, (contact_id,)).fetchall()]

    # Policy assignments for context
    policy_assignments = [dict(r) for r in conn.execute("""
        SELECT cpa.role, cpa.title, cpa.notes,
               p.policy_uid, p.policy_type, c.name AS client_name
        FROM contact_policy_assignments cpa
        JOIN policies p ON cpa.policy_id = p.id
        JOIN clients c ON p.client_id = c.id
        WHERE cpa.contact_id = ? AND p.archived = 0
        ORDER BY c.name, p.policy_type
    """, (contact_id,)).fetchall()]

    return templates.TemplateResponse("contacts/_edit_contact_slideover.html", {
        "request": request,
        "contact": contact,
        "client_assignments": client_assignments,
        "policy_assignments": policy_assignments,
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
    })


@router.patch("/{contact_id}/field")
async def contact_patch_field(request: Request, contact_id: int, conn=Depends(get_db)):
    """Update a single field on a contact (slideover inline edit)."""
    body = await request.json()
    if not body:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"name", "email", "phone", "mobile", "organization", "expertise_notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)

    contact = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    formatted = (value or "").strip()

    if field == "name" and not formatted:
        return JSONResponse({"ok": False, "error": "Name cannot be empty"}, status_code=400)
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""

    conn.execute(
        f"UPDATE contacts SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (formatted or None, contact_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


# ---------------------------------------------------------------------------
# Contact detail page
# ---------------------------------------------------------------------------

@router.get("/{contact_id}", response_class=HTMLResponse)
def contact_detail(request: Request, contact_id: int, conn=Depends(get_db)):
    """Full contact detail page — dossier + management hub."""
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return HTMLResponse("Contact not found", status_code=404)
    contact = dict(contact)
    _attach_expertise(conn, [contact])

    # Representative title from junction tables (title lives per-assignment)
    title_row = conn.execute(
        """SELECT title FROM contact_client_assignments
                WHERE contact_id = ? AND title IS NOT NULL AND TRIM(title) != ''
           UNION ALL
           SELECT title FROM contact_policy_assignments
                WHERE contact_id = ? AND title IS NOT NULL AND TRIM(title) != ''
           LIMIT 1""",
        (contact_id, contact_id),
    ).fetchone()
    contact["title"] = title_row["title"] if title_row else ""

    # ── Policy assignments ────────────────────────────────────────────────
    policy_assignments = [dict(r) for r in conn.execute("""
        SELECT cpa.*, p.policy_uid, p.policy_type, p.carrier, p.renewal_status,
               p.is_opportunity, p.opportunity_status,
               c.name AS client_name, c.id AS client_id
        FROM contact_policy_assignments cpa
        JOIN policies p ON cpa.policy_id = p.id
        JOIN clients c ON p.client_id = c.id
        WHERE cpa.contact_id = ? AND p.archived = 0
        ORDER BY c.name, p.policy_type
    """, (contact_id,)).fetchall()]

    # ── Client assignments ────────────────────────────────────────────────
    client_assignments = [dict(r) for r in conn.execute("""
        SELECT cca.*, c.name AS client_name, c.id AS client_id
        FROM contact_client_assignments cca
        JOIN clients c ON cca.client_id = c.id
        WHERE cca.contact_id = ? AND c.archived = 0
        ORDER BY c.name
    """, (contact_id,)).fetchall()]

    # ── Group by client ───────────────────────────────────────────────────
    assignments: dict[int, dict] = {}
    for pa in policy_assignments:
        cid = pa["client_id"]
        if cid not in assignments:
            assignments[cid] = {"name": pa["client_name"], "id": cid, "policies": [], "team": None, "contact": None}
        assignments[cid]["policies"].append(pa)
    for ca in client_assignments:
        cid = ca["client_id"]
        if cid not in assignments:
            assignments[cid] = {"name": ca["client_name"], "id": cid, "policies": [], "team": None, "contact": None}
        if ca["contact_type"] == "internal":
            assignments[cid]["team"] = ca
        else:
            assignments[cid]["contact"] = ca

    # ── Activities ────────────────────────────────────────────────────────
    activities = [dict(r) for r in conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.contact_id = ?
        ORDER BY a.activity_date DESC, a.id DESC
        LIMIT 50
    """, (contact_id,)).fetchall()]
    total_hours = sum(float(a["duration_hours"] or 0) for a in activities)

    # ── Pending follow-ups ────────────────────────────────────────────────
    followups = [dict(r) for r in conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type, p.carrier,
               CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.contact_id = ?
          AND a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
        ORDER BY a.follow_up_date
    """, (contact_id,)).fetchall()]

    return templates.TemplateResponse("contacts/detail.html", {
        "request": request, "active": "contacts",
        "contact": contact,
        "assignments": sorted(assignments.values(), key=lambda a: a["name"]),
        "activities": activities,
        "total_hours": total_hours,
        "followups": followups,
        "expertise_lines": cfg.get("expertise_lines", []),
        "expertise_industries": cfg.get("expertise_industries", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    })


# ---------------------------------------------------------------------------
# Matrix cell-save endpoints
# Templates use name-based URLs: patchBase + '/' + encodeURIComponent(name) + '/cell'
# We resolve name → contact_id and update the unified contacts table (shared fields)
# or the junction table (per-assignment fields like role, title).
# ---------------------------------------------------------------------------

# Shared fields live on the contacts table; assignment fields on the junction table.
_CONTACTS_TABLE_FIELDS = {"email", "phone", "mobile", "organization"}
_CLIENT_ASSIGNMENT_FIELDS = {"title", "role", "notes"}
_POLICY_ASSIGNMENT_FIELDS = {"role", "title", "notes"}


@router.patch("/{store}/{name}/cell")
async def contact_cell(request: Request, store: str, name: str, conn=Depends(get_db)):
    """Save a single cell value for a contact. Store controls which junction table is updated.

    Stores: unified (all), client, internal, placement.
    """
    if store not in ("unified", "client", "internal", "placement"):
        return JSONResponse({"ok": False, "error": "Invalid store"}, status_code=400)
    body = await request.json()
    field, value = body.get("field", ""), body.get("value", "")
    allowed = {"title", "role", "email", "phone", "mobile", "organization", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)
    formatted = value.strip()
    if field in ("phone", "mobile"):
        formatted = format_phone(formatted) if formatted else ""
    elif field == "email":
        formatted = clean_email(formatted) or ""

    contact_id = _resolve_contact_id(conn, name)
    if not contact_id:
        return JSONResponse({"ok": False, "error": "Contact not found"}, status_code=404)

    if field in _CONTACTS_TABLE_FIELDS:
        conn.execute(
            f"UPDATE contacts SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (formatted or None, contact_id),
        )

    if store == "unified":
        if field in _POLICY_ASSIGNMENT_FIELDS:
            conn.execute(
                f"UPDATE contact_policy_assignments SET {field}=? WHERE contact_id=?",
                (formatted or None, contact_id),
            )
        if field in _CLIENT_ASSIGNMENT_FIELDS:
            conn.execute(
                f"UPDATE contact_client_assignments SET {field}=? WHERE contact_id=?",
                (formatted or None, contact_id),
            )
    elif store == "client":
        if field in _CLIENT_ASSIGNMENT_FIELDS:
            conn.execute(
                f"UPDATE contact_client_assignments SET {field}=? WHERE contact_id=? AND contact_type='client'",
                (formatted or None, contact_id),
            )
    elif store == "internal":
        if field in _CLIENT_ASSIGNMENT_FIELDS:
            conn.execute(
                f"UPDATE contact_client_assignments SET {field}=? WHERE contact_id=? AND contact_type='internal'",
                (formatted or None, contact_id),
            )
    elif store == "placement":
        if field in _POLICY_ASSIGNMENT_FIELDS:
            conn.execute(
                f"UPDATE contact_policy_assignments SET {field}=? WHERE contact_id=?",
                (formatted or None, contact_id),
            )

    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})


@router.post("/{store}/{name}/rename")
async def contact_rename(request: Request, store: str, name: str, conn=Depends(get_db)):
    """Rename a contact (store param is accepted for URL consistency but rename is always global)."""
    if store not in ("unified", "client", "internal", "placement"):
        return JSONResponse({"ok": False, "error": "Invalid store"}, status_code=400)
    body = await request.json()
    new_name = (body.get("new_name", "") or "").strip()
    if not new_name:
        return JSONResponse({"ok": False, "error": "Name cannot be empty"}, status_code=400)

    contact_id = _resolve_contact_id(conn, name)
    if not contact_id:
        return JSONResponse({"ok": False, "error": "Contact not found"}, status_code=404)

    conn.execute(
        "UPDATE contacts SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_name, contact_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "new_name": new_name})


@router.post("/client/add-row", response_class=HTMLResponse)
def client_type_contact_add_row(request: Request, conn=Depends(get_db)):
    """Create a new client-type contact row and return matrix row HTML."""
    contact_id = get_or_create_contact(conn, "New Contact")
    first = conn.execute("SELECT id FROM clients WHERE archived=0 ORDER BY name LIMIT 1").fetchone()
    client_id = first["id"] if first else 1
    conn.execute(
        """INSERT OR IGNORE INTO contact_client_assignments (contact_id, client_id, contact_type)
           VALUES (?, ?, 'client')""",
        (contact_id, client_id),
    )
    conn.commit()
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    c = {"name": "New Contact", "contact_id": contact_id, "title": None, "role": None,
         "notes": None,
         "email": None, "phone": None, "mobile": None, "organization": None,
         "client_count": 0, "clients": []}
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "client",
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


@router.post("/internal/add-row", response_class=HTMLResponse)
def internal_contact_add_row(request: Request, conn=Depends(get_db)):
    """Create a new internal contact row and return matrix row HTML."""
    contact_id = get_or_create_contact(conn, "New Contact")
    first = conn.execute("SELECT id FROM clients WHERE archived=0 ORDER BY name LIMIT 1").fetchone()
    client_id = first["id"] if first else 1
    conn.execute(
        """INSERT OR IGNORE INTO contact_client_assignments (contact_id, client_id, contact_type)
           VALUES (?, ?, 'internal')""",
        (contact_id, client_id),
    )
    conn.commit()
    c = {"name": "New Contact", "contact_id": contact_id, "title": None, "role": None,
         "notes": None,
         "email": None, "phone": None, "mobile": None, "client_count": 0,
         "policy_count": 0, "clients": [], "policies": []}
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "internal",
        "contact_roles": cfg.get("contact_roles", []),
    })


# ---------------------------------------------------------------------------
# Client-type contact HTMX endpoints (must be before /{name}/... catch-all)
# ---------------------------------------------------------------------------

@router.get("/client/{name}/row", response_class=HTMLResponse)
def client_type_contact_row(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: restore client-type contact display row (Cancel button)."""
    row = conn.execute(
        """SELECT co.id AS contact_id, co.name, MAX(cca.title) AS title, co.email,
                  co.phone, co.mobile, MAX(cca.role) AS role,
                  COUNT(DISTINCT cca.client_id) AS client_count
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           JOIN clients c ON cca.client_id = c.id
           WHERE LOWER(TRIM(co.name)) = LOWER(TRIM(?)) AND cca.contact_type = 'client'
           GROUP BY co.id""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    c = dict(row)
    c["clients"] = _client_type_contact_clients(conn, c["contact_id"])
    _attach_expertise(conn, [c])
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "client",
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


@router.post("/client/new", response_class=HTMLResponse)
def client_type_contact_create(
    request: Request,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    client_name: str = Form(""),
    conn=Depends(get_db),
):
    """Create a new client-type contact, optionally assigned to a client."""
    contact_id = get_or_create_contact(
        conn, name.strip(),
        email=clean_email(email) or None,
        phone=format_phone(phone) if phone.strip() else None,
        mobile=format_phone(mobile) if mobile.strip() else None,
    )

    client_id = None
    if client_name:
        row = conn.execute(
            "SELECT id FROM clients WHERE LOWER(TRIM(name))=LOWER(TRIM(?)) AND archived=0",
            (client_name.strip(),),
        ).fetchone()
        if row:
            client_id = row["id"]
    if client_id:
        conn.execute(
            """INSERT OR IGNORE INTO contact_client_assignments
               (contact_id, client_id, contact_type, title, role)
               VALUES (?,?,?,?,?)""",
            (contact_id, client_id, "client", title.strip() or None, role.strip() or None),
        )
    conn.commit()
    client_type = _get_client_type_contacts(conn)
    all_clients_json = _json.dumps([
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
        ).fetchall()
    ])
    return templates.TemplateResponse("contacts/_client_contact_tbody.html", {
        "request": request,
        "client_type_contacts": client_type,
        "all_clients_json": all_clients_json,
    })


@router.post("/add-row", response_class=HTMLResponse)
def placement_contact_add_row(request: Request, conn=Depends(get_db)):
    """Create a new placement contact row and return matrix row HTML."""
    contact_id = get_or_create_contact(conn, "New Contact")
    first = conn.execute("SELECT id FROM policies WHERE archived=0 LIMIT 1").fetchone()
    policy_id = first["id"] if first else 1
    conn.execute(
        """INSERT OR IGNORE INTO contact_policy_assignments (contact_id, policy_id)
           VALUES (?, ?)""",
        (contact_id, policy_id),
    )
    conn.commit()
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    c = {"name": "New Contact", "contact_id": contact_id, "organization": None, "role": None,
         "title": None, "notes": None,
         "email": None, "phone": None, "mobile": None, "policy_count": 0, "policies": []}
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "placement",
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


@router.get("/internal/{name}/row", response_class=HTMLResponse)
def internal_contact_row(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: restore internal contact display row (Cancel button)."""
    row = conn.execute(
        """SELECT co.id AS contact_id, co.name, MAX(cca.title) AS title, co.email,
                  co.phone, co.mobile, MAX(cca.role) AS role,
                  COUNT(DISTINCT cca.client_id) AS client_count
           FROM contacts co
           JOIN contact_client_assignments cca ON co.id = cca.contact_id
           JOIN clients c ON cca.client_id = c.id
           WHERE LOWER(TRIM(co.name)) = LOWER(TRIM(?)) AND cca.contact_type = 'internal'
           GROUP BY co.id""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    c = dict(row)
    c["clients"] = _internal_contact_clients(conn, c["contact_id"])
    c["also_on_policies"] = _internal_contact_policies(conn, c["contact_id"])
    c["policy_cross_count"] = len(c["also_on_policies"])
    _attach_expertise(conn, [c])
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "internal",
        "contact_roles": cfg.get("contact_roles", []),
    })


@router.get("/{name}/row", response_class=HTMLResponse)
def contact_row(request: Request, name: str, conn=Depends(get_db)):
    """HTMX: restore display row (Cancel button)."""
    row = conn.execute(
        """SELECT co.id AS contact_id, co.name, co.email, co.phone, co.mobile,
                  co.organization, MAX(cpa.role) AS role,
                  COUNT(DISTINCT cpa.policy_id) AS policy_count
           FROM contacts co
           JOIN contact_policy_assignments cpa ON co.id = cpa.contact_id
           JOIN policies p ON cpa.policy_id = p.id
           JOIN clients c ON p.client_id = c.id
           WHERE LOWER(TRIM(co.name)) = LOWER(TRIM(?)) AND p.archived = 0
           GROUP BY co.id""",
        (name,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    c = dict(row)
    c["policies"] = _contact_policies(conn, c["contact_id"])
    _attach_expertise(conn, [c])
    all_orgs = sorted({r["organization"] for r in conn.execute(
        "SELECT DISTINCT organization FROM contacts WHERE organization IS NOT NULL AND organization != ''"
    ).fetchall()})
    return templates.TemplateResponse("contacts/_contact_matrix_row.html", {
        "request": request, "c": c, "store": "placement",
        "contact_roles": cfg.get("contact_roles", []),
        "all_orgs": all_orgs,
    })


# ---------------------------------------------------------------------------
# Create endpoints
# ---------------------------------------------------------------------------

@router.post("/new", response_class=HTMLResponse)
def contact_create(
    request: Request,
    name: str = Form(...),
    organization: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Create a new placement contact, optionally attached to a policy."""
    contact_id = get_or_create_contact(
        conn, name.strip(),
        email=clean_email(email) or None,
        phone=format_phone(phone) if phone.strip() else None,
        mobile=format_phone(mobile) if mobile.strip() else None,
        organization=organization.strip() or None,
    )

    policy_id = None
    if policy_uid:
        row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (policy_uid.strip().upper(),)).fetchone()
        if row:
            policy_id = row["id"]
    if policy_id:
        conn.execute(
            """INSERT OR IGNORE INTO contact_policy_assignments
               (contact_id, policy_id, role)
               VALUES (?,?,?)""",
            (contact_id, policy_id, role.strip() or None),
        )
    conn.commit()
    # Form uses hx-swap="none" + page reload on success
    return HTMLResponse("")


@router.post("/internal/new", response_class=HTMLResponse)
def internal_contact_create(
    request: Request,
    name: str = Form(...),
    title: str = Form(""),
    role: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    mobile: str = Form(""),
    client_name: str = Form(""),
    conn=Depends(get_db),
):
    """Create a new internal team member, optionally assigned to a client."""
    contact_id = get_or_create_contact(
        conn, name.strip(),
        email=clean_email(email) or None,
        phone=format_phone(phone) if phone.strip() else None,
        mobile=format_phone(mobile) if mobile.strip() else None,
    )

    client_id = None
    if client_name:
        row = conn.execute(
            "SELECT id FROM clients WHERE LOWER(TRIM(name))=LOWER(TRIM(?)) AND archived=0",
            (client_name.strip(),),
        ).fetchone()
        if row:
            client_id = row["id"]
    if client_id:
        conn.execute(
            """INSERT OR IGNORE INTO contact_client_assignments
               (contact_id, client_id, contact_type, title, role)
               VALUES (?,?,?,?,?)""",
            (contact_id, client_id, "internal", title.strip() or None, role.strip() or None),
        )
    conn.commit()
    internal = _get_internal_contacts(conn)
    all_clients_json = _json.dumps([
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
        ).fetchall()
    ])
    return templates.TemplateResponse("contacts/_internal_tbody.html", {
        "request": request,
        "internal_contacts": internal,
        "all_clients_json": all_clients_json,
    })


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

@router.get("/check-duplicate", response_class=HTMLResponse)
def check_duplicate(request: Request, name: str = "", email: str = "", conn=Depends(get_db)):
    """HTMX: check if a contact with similar name or same email already exists.
    Returns a warning banner partial if duplicates found, empty string otherwise."""
    name = name.strip()
    email = email.strip().lower()
    if not name and not email:
        return HTMLResponse("")

    matches: list[dict] = []

    # Exact email match in unified contacts table
    if email:
        email_hits = conn.execute(
            "SELECT id, name, email FROM contacts WHERE LOWER(TRIM(email)) = ?",
            (email,),
        ).fetchall()
        for r in email_hits:
            # Determine which stores this contact appears in
            sources = []
            if conn.execute(
                "SELECT 1 FROM contact_policy_assignments WHERE contact_id=? LIMIT 1", (r["id"],)
            ).fetchone():
                sources.append("policy")
            internal_row = conn.execute(
                "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='internal' LIMIT 1",
                (r["id"],),
            ).fetchone()
            client_row = conn.execute(
                "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='client' LIMIT 1",
                (r["id"],),
            ).fetchone()
            if internal_row:
                sources.append("team")
            if client_row:
                sources.append("client")
            source = sources[0] if sources else "contact"
            matches.append({"name": r["name"], "email": r["email"], "source": source, "match_type": "email"})

    # Fuzzy name match using RapidFuzz
    if name and len(name) >= 2:
        from rapidfuzz import fuzz
        all_names = conn.execute(
            "SELECT id, name, email FROM contacts WHERE name IS NOT NULL AND name != ''"
        ).fetchall()
        seen_names: set[str] = {m["name"].lower().strip() for m in matches}
        for r in all_names:
            existing = r["name"]
            if existing.lower().strip() in seen_names:
                continue
            score = fuzz.ratio(name.lower(), existing.lower())
            if score >= 80:
                # Determine source
                sources = []
                if conn.execute(
                    "SELECT 1 FROM contact_policy_assignments WHERE contact_id=? LIMIT 1", (r["id"],)
                ).fetchone():
                    sources.append("policy")
                int_row = conn.execute(
                    "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='internal' LIMIT 1",
                    (r["id"],),
                ).fetchone()
                cli_row = conn.execute(
                    "SELECT 1 FROM contact_client_assignments WHERE contact_id=? AND contact_type='client' LIMIT 1",
                    (r["id"],),
                ).fetchone()
                if int_row:
                    sources.append("team")
                if cli_row:
                    sources.append("client")
                source = sources[0] if sources else "contact"
                matches.append({
                    "name": existing, "email": r["email"] or "",
                    "source": source, "match_type": "name",
                    "score": score,
                })
                seen_names.add(existing.lower().strip())

    if not matches:
        return HTMLResponse("")

    return templates.TemplateResponse("contacts/_duplicate_warning.html", {
        "request": request,
        "matches": matches[:5],
    })


# ---------------------------------------------------------------------------
# Unified delete — removes ALL assignments and the contact record itself
# ---------------------------------------------------------------------------

@router.post("/unified/{contact_id}/delete-by-id")
def unified_contact_delete_by_id(request: Request, contact_id: int, conn=Depends(get_db)):
    """Delete a contact entirely by numeric ID (used by bulk delete)."""
    conn.execute("UPDATE activity_log SET contact_id = NULL WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM contact_policy_assignments WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM contact_client_assignments WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    return JSONResponse({"ok": True})


@router.post("/unified/{name}/delete")
def unified_contact_delete(request: Request, name: str, conn=Depends(get_db)):
    """Delete a contact entirely: all policy assignments, all client assignments, and the contact record."""
    contact_id = _resolve_contact_id(conn, name)
    if contact_id:
        # Clear activity_log FK references so the activity rows aren't orphaned
        conn.execute("UPDATE activity_log SET contact_id = NULL WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM contact_policy_assignments WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM contact_client_assignments WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Delete endpoint — remove store-specific assignments, clean up if orphaned
# ---------------------------------------------------------------------------

_DELETE_SQL = {
    "placement": "DELETE FROM contact_policy_assignments WHERE contact_id = ?",
    "internal": "DELETE FROM contact_client_assignments WHERE contact_id = ? AND contact_type='internal'",
    "client": "DELETE FROM contact_client_assignments WHERE contact_id = ? AND contact_type='client'",
}


@router.post("/{store}/{name}/delete")
def store_contact_delete(request: Request, store: str, name: str, conn=Depends(get_db)):
    """Delete store-specific assignments for a contact. If no assignments remain, delete the contact."""
    if store not in _DELETE_SQL:
        return JSONResponse({"ok": False, "error": "Invalid store"}, status_code=400)
    contact_id = _resolve_contact_id(conn, name)
    if contact_id:
        conn.execute(_DELETE_SQL[store], (contact_id,))
        remaining = conn.execute(
            """SELECT 1 FROM contact_client_assignments WHERE contact_id = ?
               UNION ALL
               SELECT 1 FROM contact_policy_assignments WHERE contact_id = ?""",
            (contact_id, contact_id),
        ).fetchone()
        if not remaining:
            conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Merge — use unified merge_contacts from queries.py
# ---------------------------------------------------------------------------

@router.get("/merge-compare", response_class=HTMLResponse)
def merge_compare(request: Request, id1: int = 0, id2: int = 0, conn=Depends(get_db)):
    """HTMX: side-by-side merge comparison panel for two contacts by ID."""
    if not id1 or not id2:
        return HTMLResponse("<p class='text-sm text-red-500'>Both contact IDs required.</p>", status_code=400)
    r1 = conn.execute("SELECT * FROM contacts WHERE id = ?", (id1,)).fetchone()
    r2 = conn.execute("SELECT * FROM contacts WHERE id = ?", (id2,)).fetchone()
    if not r1 or not r2:
        return HTMLResponse("<p class='text-sm text-red-500'>One or both contacts not found.</p>", status_code=404)
    c1, c2 = dict(r1), dict(r2)
    for c in [c1, c2]:
        c["policy_count"] = conn.execute(
            "SELECT COUNT(*) FROM contact_policy_assignments WHERE contact_id = ?", (c["id"],)
        ).fetchone()[0]
        c["client_count"] = conn.execute(
            "SELECT COUNT(*) FROM contact_client_assignments WHERE contact_id = ?", (c["id"],)
        ).fetchone()[0]
        _attach_expertise(conn, [c])
    return templates.TemplateResponse("contacts/_merge_compare.html", {
        "request": request, "c1": c1, "c2": c2,
    })


@router.post("/merge", response_class=HTMLResponse)
async def contact_merge(request: Request, conn=Depends(get_db)):
    """Merge source_name into target_name via unified contacts."""
    body = await request.json()
    source = (body.get("source_name") or "").strip()
    target = (body.get("target_name") or "").strip()
    if not source or not target:
        return JSONResponse({"ok": False, "error": "Both names required"}, status_code=400)

    source_id = _resolve_contact_id(conn, source)
    target_id = _resolve_contact_id(conn, target)

    if not source_id:
        return JSONResponse({"ok": False, "error": f"Source contact '{source}' not found"}, status_code=404)
    if not target_id:
        return JSONResponse({"ok": False, "error": f"Target contact '{target}' not found"}, status_code=404)
    if source_id == target_id:
        return JSONResponse({"ok": True, "target_name": target})

    merge_contacts(conn, source_id, target_id)
    conn.commit()
    return JSONResponse({"ok": True, "target_name": target})


# ---------------------------------------------------------------------------
# Add to other store — create assignment in another store for existing contact
# ---------------------------------------------------------------------------

@router.post("/{name}/add-to-store", response_class=HTMLResponse)
async def contact_add_to_store(request: Request, name: str, conn=Depends(get_db)):
    """Copy a contact's shared fields into another store by creating an assignment."""
    body = await request.json()
    target_store = body.get("target_store", "")

    contact_id = _resolve_contact_id(conn, name)
    if not contact_id:
        return JSONResponse({"ok": False, "error": "Contact not found"}, status_code=404)

    # Get assignment-level fields from existing assignments
    asg = conn.execute(
        """SELECT MAX(title) AS title, MAX(role) AS role
           FROM (
               SELECT title, role FROM contact_client_assignments WHERE contact_id = ?
               UNION ALL
               SELECT NULL AS title, role FROM contact_policy_assignments WHERE contact_id = ?
           )""",
        (contact_id, contact_id),
    ).fetchone()
    title = asg["title"] if asg else None
    role = asg["role"] if asg else None

    if target_store == "internal":
        first_client = conn.execute("SELECT id FROM clients WHERE archived=0 ORDER BY name LIMIT 1").fetchone()
        cid = first_client["id"] if first_client else 1
        conn.execute(
            """INSERT OR IGNORE INTO contact_client_assignments
               (contact_id, client_id, contact_type, title, role)
               VALUES (?,?,?,?,?)""",
            (contact_id, cid, "internal", title, role),
        )
    elif target_store == "client":
        first_client = conn.execute("SELECT id FROM clients WHERE archived=0 ORDER BY name LIMIT 1").fetchone()
        cid = first_client["id"] if first_client else 1
        conn.execute(
            """INSERT OR IGNORE INTO contact_client_assignments
               (contact_id, client_id, contact_type, title, role)
               VALUES (?,?,?,?,?)""",
            (contact_id, cid, "client", title, role),
        )
    elif target_store == "policy":
        first_policy = conn.execute("SELECT id FROM policies WHERE archived=0 LIMIT 1").fetchone()
        pid = first_policy["id"] if first_policy else 1
        conn.execute(
            """INSERT OR IGNORE INTO contact_policy_assignments
               (contact_id, policy_id, role)
               VALUES (?,?,?)""",
            (contact_id, pid, role),
        )
    else:
        return JSONResponse({"ok": False, "error": "Invalid store"}, status_code=400)

    conn.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Expertise helpers and CRUD endpoints
# ---------------------------------------------------------------------------

def _attach_expertise(conn, contacts: list[dict]) -> None:
    """Attach expertise tags to a list of contact dicts (mutates in place)."""
    if not contacts:
        return
    # Contact dicts may use "id" (from detail page) or "contact_id" (from listing queries)
    ids = [c.get("id") or c.get("contact_id") for c in contacts]
    ids = [i for i in ids if i]
    if not ids:
        return
    rows = conn.execute(
        f"SELECT contact_id, category, tag FROM contact_expertise WHERE contact_id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    tag_map: dict[int, dict] = {}
    for r in rows:
        tag_map.setdefault(r["contact_id"], {"line": [], "industry": []})
        tag_map[r["contact_id"]][r["category"]].append(r["tag"])
    for c in contacts:
        cid = c.get("id") or c.get("contact_id")
        c["expertise_lines"] = tag_map.get(cid, {}).get("line", [])
        c["expertise_industries"] = tag_map.get(cid, {}).get("industry", [])


@router.post("/{contact_id}/expertise")
async def contact_expertise_toggle(
    request: Request,
    contact_id: int,
    conn=Depends(get_db),
):
    """Add or remove an expertise tag for a contact."""
    body = await request.json()
    category = body.get("category", "")
    tag = body.get("tag", "")
    action = body.get("action", "add")  # "add" or "remove"

    if category not in ("line", "industry") or not tag:
        return JSONResponse({"ok": False, "error": "Invalid"}, status_code=400)

    contact = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    if action == "remove":
        conn.execute(
            "DELETE FROM contact_expertise WHERE contact_id = ? AND category = ? AND tag = ?",
            (contact_id, category, tag),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO contact_expertise (contact_id, category, tag) VALUES (?, ?, ?)",
            (contact_id, category, tag),
        )
    conn.commit()

    # Return current tags
    tags = conn.execute(
        "SELECT category, tag FROM contact_expertise WHERE contact_id = ?", (contact_id,)
    ).fetchall()
    return JSONResponse({"ok": True, "tags": [dict(t) for t in tags]})


@router.patch("/{contact_id}/expertise-notes")
async def contact_expertise_notes(
    request: Request,
    contact_id: int,
    conn=Depends(get_db),
):
    """Update expertise notes for a contact."""
    body = await request.json()
    value = body.get("value", "").strip()
    conn.execute(
        "UPDATE contacts SET expertise_notes = ? WHERE id = ?",
        (value or None, contact_id),
    )
    conn.commit()
    return JSONResponse({"ok": True, "formatted": value})
