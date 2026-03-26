# LLM Bulk Contact Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM-powered bulk contact import to the client contacts tab, enabling mass import from email signatures, rosters, or any text.

**Architecture:** New schema + prompt generator + parser in `llm_schemas.py`, three HTMX routes in `clients.py`, one new review template, one button added to contacts tab. Follows the identical pattern as the existing bulk policy import and policy-level contact extraction.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, existing `llm_schemas.py` framework

**Spec:** `docs/superpowers/specs/2026-03-26-llm-bulk-contact-import-design.md`

---

## Task 1: Add `brokerage_name` config key

**Files:**
- Modify: `src/policydb/config.py` (add to `_DEFAULTS` dict around line 12)
- Modify: `src/policydb/web/routes/settings.py` (add save endpoint + expose in context)
- Modify: `src/policydb/web/templates/settings.html` (add input field in Database & Admin tab)

- [ ] **Step 1: Add `brokerage_name` to `_DEFAULTS` in `config.py`**

In `src/policydb/config.py`, add after `"default_account_exec": "Grant",` (line 12):

```python
"brokerage_name": "",
```

- [ ] **Step 2: Add `brokerage_name` to settings context**

In `src/policydb/web/routes/settings.py`, in the `_build_tab_ctx()` function, in the `tab == "admin"` block (near line 246 where `google_places_api_key` is added), add:

```python
ctx["brokerage_name"] = cfg.get("brokerage_name", "")
```

- [ ] **Step 3: Add save endpoint for `brokerage_name`**

In `src/policydb/web/routes/settings.py`, near the `update_google_places` endpoint (around line 499), add:

```python
@router.post("/config/brokerage")
def update_brokerage(request: Request, brokerage_name: str = Form("")):
    full = dict(cfg.load_config())
    full["brokerage_name"] = brokerage_name.strip()
    cfg.save_config(full)
    cfg.reload_config()
    return RedirectResponse("/settings?tab=admin", status_code=303)
```

- [ ] **Step 4: Add input field in settings template**

In `src/policydb/web/templates/settings.html`, in the Database & Admin tab section near the Google Places API key field, add a "Brokerage Name" text input:

```html
<form method="post" action="/settings/config/brokerage" class="space-y-2">
  <label class="block text-sm font-medium text-gray-700">Brokerage Name</label>
  <p class="text-xs text-gray-400">Used for AI contact import to identify internal team members.</p>
  <div class="flex gap-2">
    <input type="text" name="brokerage_name" value="{{ brokerage_name }}"
           class="flex-1 rounded-lg border-gray-300 text-sm focus:ring-marsh focus:border-marsh"
           placeholder="e.g. Marsh McLennan">
    <button type="submit" class="px-3 py-1.5 bg-marsh text-white text-sm rounded-lg hover:bg-marsh-light">Save</button>
  </div>
</form>
```

- [ ] **Step 5: Test manually**

Start server, navigate to Settings → Database & Admin tab. Verify "Brokerage Name" field appears, can be saved, and persists on reload.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat: add brokerage_name config key for AI contact import"
```

---

## Task 2: Add `CONTACT_BULK_IMPORT_SCHEMA` to `llm_schemas.py`

**Files:**
- Modify: `src/policydb/llm_schemas.py` (add schema after `CONTACT_EXTRACTION_SCHEMA` at line 1541)

- [ ] **Step 1: Add the schema definition**

After the closing `}` of `CONTACT_EXTRACTION_SCHEMA` (line 1541) in `src/policydb/llm_schemas.py`, add:

```python
# ---------------------------------------------------------------------------
# Contact Bulk Import Schema — client-level mass contact import
# ---------------------------------------------------------------------------

CONTACT_BULK_IMPORT_SCHEMA: dict = {
    "name": "contact_bulk_import",
    "version": 1,
    "description": (
        "Extract contacts from email signatures, rosters, meeting notes, "
        "or any text containing people and their contact details"
    ),
    "is_array": True,
    "fields": [
        {
            "key": "name",
            "label": "Full Name",
            "type": "string",
            "required": True,
            "description": "Full name of the person (first and last name)",
            "example": "Jane Smith",
        },
        {
            "key": "email",
            "label": "Email Address",
            "type": "string",
            "required": False,
            "description": "Email address (from headers, cc/bcc, or signature block)",
            "example": "jane.smith@example.com",
        },
        {
            "key": "phone",
            "label": "Phone Number",
            "type": "string",
            "required": False,
            "description": "Office or direct phone number from signature block",
            "example": "(555) 123-4567",
        },
        {
            "key": "mobile",
            "label": "Mobile Number",
            "type": "string",
            "required": False,
            "description": "Cell/mobile number from signature block",
            "example": "(555) 987-6543",
        },
        {
            "key": "organization",
            "label": "Company / Organization",
            "type": "string",
            "required": False,
            "description": (
                "Company or organization name from signature block or email domain"
            ),
            "example": "Acme Insurance",
        },
        {
            "key": "title",
            "label": "Job Title",
            "type": "string",
            "required": False,
            "description": "Job title or role from signature block",
            "example": "Senior Underwriter",
        },
        {
            "key": "role",
            "label": "Account Role",
            "type": "string",
            "required": False,
            "description": (
                "The person's role relative to this client account. "
                "Infer from context: carrier employees are likely Underwriters, "
                "brokerage colleagues are Brokers or Account Managers, "
                "client employees are client contacts."
            ),
            "config_values": "contact_roles",
            "config_mode": "prefer",
            "example": "Underwriter",
        },
        {
            "key": "contact_type",
            "label": "Contact Type",
            "type": "string",
            "required": False,
            "description": (
                "Whether this person is a 'client' contact (works for the client), "
                "'internal' (works at your brokerage), or 'external' (works at a "
                "carrier, vendor, or third party). Infer from organization name."
            ),
            "example": "client",
        },
    ],
}
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/llm_schemas.py
git commit -m "feat: add CONTACT_BULK_IMPORT_SCHEMA for client-level contact import"
```

---

## Task 3: Add `generate_contact_bulk_import_prompt()` to `llm_schemas.py`

**Files:**
- Modify: `src/policydb/llm_schemas.py` (add function after the schema)

- [ ] **Step 1: Add the prompt generator function**

After `CONTACT_BULK_IMPORT_SCHEMA` in `src/policydb/llm_schemas.py`, add:

```python
def generate_contact_bulk_import_prompt(conn, client_id: int) -> str:
    """Build a prompt for bulk contact import at the client level."""
    import policydb.config as _cfg

    client = conn.execute(
        "SELECT name, industry_segment FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()

    client_name = client["name"] if client else "Unknown"
    industry = (client["industry_segment"] or "") if client else ""

    # Gather context
    brokerage = _cfg.get("brokerage_name", "")
    contact_roles = _cfg.get("contact_roles", [])
    carriers_on_account = [
        r["carrier"]
        for r in conn.execute(
            "SELECT DISTINCT carrier FROM policies "
            "WHERE client_id = ? AND carrier IS NOT NULL AND carrier != ''",
            (client_id,),
        ).fetchall()
    ]
    existing_names = [
        r["name"]
        for r in conn.execute(
            "SELECT DISTINCT co.name FROM contacts co "
            "JOIN contact_client_assignments cca ON co.id = cca.contact_id "
            "WHERE cca.client_id = ?",
            (client_id,),
        ).fetchall()
    ]

    config_lists = {"contact_roles": contact_roles}
    parts: list[str] = []

    parts.append(
        "You are an insurance operations analyst. I will provide text that "
        "contains contact information — this may be email signatures, a contact "
        "roster, meeting notes, a distribution list, or any text mentioning "
        "people with their details. Your job is to extract all people and "
        "return their contact information as structured JSON.\n"
    )

    parts.append("## Output Format\n")
    parts.append(
        "Return a JSON **array** of contact objects. Each contact should have "
        "the fields listed below. Omit fields you cannot determine.\n"
    )

    parts.append("## Fields per Contact\n")
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        parts.append(_build_field_instruction(f, config_lists))

    parts.append("\n## Client Context\n")
    parts.append(f"- **Client**: {client_name}")
    if industry:
        parts.append(f"- **Industry**: {industry}")
    if carriers_on_account:
        parts.append(
            f"- **Known carriers on this account**: {', '.join(carriers_on_account)}"
        )
    if brokerage:
        parts.append(f"- **Your brokerage**: {brokerage}")
    if existing_names:
        parts.append(
            f"- **Already-known contacts** (skip or note if seen): "
            f"{', '.join(existing_names[:30])}"
        )

    parts.append("\n## Contact Type Inference Rules\n")
    if carriers_on_account:
        parts.append(
            f"- People from these carriers are 'external': "
            f"{', '.join(carriers_on_account)}"
        )
    if brokerage:
        parts.append(
            f"- People from '{brokerage}' or its subsidiaries are 'internal'"
        )
    parts.append("- All others are 'client' (unless context suggests otherwise)")
    parts.append(
        "- If you cannot determine contact_type, omit it (defaults to 'client')"
    )

    parts.append("\n## Extraction Rules\n")
    parts.append("- Extract contacts from email headers (From, To, CC, BCC)")
    parts.append("- Extract contact details from email signature blocks")
    parts.append("- Do NOT include generic/no-reply email addresses")
    parts.append(
        "- If the same person appears multiple times, merge into one entry "
        "with the most complete information"
    )

    # JSON template
    example = {}
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        if f.get("example"):
            example[f["key"]] = f["example"]
    parts.append("\n## JSON Template\n")
    parts.append(
        "Return ONLY valid JSON matching this structure (array of contacts):"
    )
    template = json.dumps([example], indent=2)
    parts.append(f"```json\n{template}\n```")

    parts.append("\n---\n")
    parts.append("**PASTE THE TEXT CONTAINING CONTACTS BELOW THIS LINE:**\n")

    return "\n".join(parts)
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/llm_schemas.py
git commit -m "feat: add generate_contact_bulk_import_prompt() for client-level import"
```

---

## Task 4: Add `parse_contact_bulk_import_json()` to `llm_schemas.py`

**Files:**
- Modify: `src/policydb/llm_schemas.py` (add function after the prompt generator)

- [ ] **Step 1: Add the parser function**

After `generate_contact_bulk_import_prompt()` in `src/policydb/llm_schemas.py`, add:

```python
_VALID_CONTACT_TYPES = {"client", "internal", "external"}


def parse_contact_bulk_import_json(raw_text: str) -> dict:
    """Parse LLM JSON response for bulk contact import.

    Expects a JSON array of contact objects. Normalizes each using
    CONTACT_BULK_IMPORT_SCHEMA field definitions. Validates contact_type.

    Returns:
        {"ok": True, "contacts": [...], "warnings": [...], "count": N}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
    if len(raw_text) > _MAX_INPUT_BYTES:
        return {
            "ok": False,
            "error": "Input too large (max 500KB).",
            "raw_text": raw_text[:200],
        }

    # Try code fences first, then raw JSON — same strategy as contact extraction
    json_str = _extract_json_str(raw_text)

    if json_str is None or (json_str.startswith("{") and "[" in raw_text):
        for pattern in [_RE_JSON_CODE_FENCE, _RE_GENERIC_CODE_FENCE]:
            m = pattern.search(raw_text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("["):
                    json_str = candidate
                    break
        if json_str is None or not json_str.startswith("["):
            start = raw_text.find("[")
            if start != -1:
                depth = 0
                in_string = False
                escape_next = False
                for i in range(start, len(raw_text)):
                    ch = raw_text[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == "\\":
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            json_str = raw_text[start : i + 1]
                            break

    if json_str is None:
        return {
            "ok": False,
            "error": "No JSON found in input.",
            "raw_text": raw_text,
        }

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"Invalid JSON: {e}",
            "raw_text": raw_text,
        }

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": "Expected a JSON array of contacts.",
            "raw_text": raw_text,
        }

    fields = CONTACT_BULK_IMPORT_SCHEMA["fields"]
    all_warnings: list[str] = []
    contacts: list[dict] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            all_warnings.append(f"Item [{i}] is not an object, skipping.")
            continue

        parsed, _raw, warnings = _parse_flat_fields(item, fields)
        for w in warnings:
            all_warnings.append(f"Contact [{i}]: {w}")

        if not parsed.get("name"):
            all_warnings.append(f"Contact [{i}]: Missing name, skipping.")
            continue

        # Validate contact_type
        ct = parsed.get("contact_type", "")
        if ct and ct.lower() not in _VALID_CONTACT_TYPES:
            all_warnings.append(
                f"Contact [{i}]: Invalid contact_type '{ct}', defaulting to 'client'."
            )
            parsed["contact_type"] = "client"
        elif ct:
            parsed["contact_type"] = ct.lower()

        parsed["_index"] = i
        contacts.append(parsed)

    if not contacts:
        return {
            "ok": False,
            "error": "No valid contacts extracted from JSON.",
            "raw_text": raw_text,
        }

    return {
        "ok": True,
        "contacts": contacts,
        "warnings": all_warnings,
        "count": len(contacts),
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/llm_schemas.py
git commit -m "feat: add parse_contact_bulk_import_json() with contact_type validation"
```

---

## Task 5: Add three client routes (prompt, parse, apply)

**Files:**
- Modify: `src/policydb/web/routes/clients.py` (add routes near the existing `ai-bulk-import` routes, around line 4860+)

- [ ] **Step 1: Add imports at top of clients.py**

At the top of `src/policydb/web/routes/clients.py`, add to the existing `llm_schemas` imports:

```python
from policydb.llm_schemas import (
    # ... existing imports ...
    CONTACT_BULK_IMPORT_SCHEMA,
    generate_contact_bulk_import_prompt,
    parse_contact_bulk_import_json,
)
```

- [ ] **Step 2: Add cache dict**

Near the existing `_BULK_IMPORT_CACHE` dict in `clients.py`, add:

```python
_CLIENT_CONTACT_IMPORT_CACHE: dict[str, tuple] = {}
```

- [ ] **Step 3: Add prompt route**

After the existing `ai-bulk-import` routes in `clients.py`, add:

```python
# ---------------------------------------------------------------------------
# AI Contact Import — client-level bulk contact import
# ---------------------------------------------------------------------------

@router.get("/{client_id}/ai-contact-import/prompt", response_class=HTMLResponse)
def client_ai_contact_import_prompt(
    request: Request, client_id: int, conn=Depends(get_db)
):
    """Return the AI import panel with contact bulk import prompt."""
    client = conn.execute(
        "SELECT * FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client:
        return HTMLResponse("Client not found", status_code=404)

    prompt_text = generate_contact_bulk_import_prompt(conn, client_id)

    # Build JSON template from schema examples
    example = {}
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        if f.get("example"):
            example[f["key"]] = f["example"]
    json_template = json.dumps([example], indent=2)

    context_display = {"Client": client["name"]}
    if client.get("industry_segment"):
        context_display["Industry"] = client["industry_segment"]

    return templates.TemplateResponse("_ai_import_panel.html", {
        "request": request,
        "import_type": "client_contacts",
        "prompt_text": prompt_text,
        "json_template": json_template,
        "context_display": context_display,
        "parse_url": f"/clients/{client_id}/ai-contact-import/parse",
        "import_target": "#ai-contact-import-result",
    })
```

- [ ] **Step 4: Add parse route**

```python
@router.post("/{client_id}/ai-contact-import/parse", response_class=HTMLResponse)
def client_ai_contact_import_parse(
    request: Request,
    client_id: int,
    json_text: str = Form(...),
    conn=Depends(get_db),
):
    """Parse LLM contact JSON and return review panel."""
    import time
    import uuid

    result = parse_contact_bulk_import_json(json_text)
    if not result["ok"]:
        return HTMLResponse(
            f'<div class="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">'
            f'{result["error"]}</div>',
            status_code=422,
        )

    client = conn.execute(
        "SELECT * FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    contacts = result["contacts"]
    warnings = result.get("warnings", [])

    # Fetch ALL existing client contacts across all types for dedup
    existing_names: set[str] = set()
    for ctype in ("client", "internal", "external"):
        rows = get_client_contacts(conn, client_id, contact_type=ctype)
        for r in rows:
            if r.get("name"):
                existing_names.add(r["name"].lower().strip())

    # Annotate contacts
    for contact in contacts:
        name_lower = contact["name"].lower().strip()
        contact["already_assigned"] = name_lower in existing_names

        existing = conn.execute(
            "SELECT id, email, phone, organization FROM contacts "
            "WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
            (contact["name"],),
        ).fetchone()
        contact["existing_contact"] = dict(existing) if existing else None

        # Default contact_type
        if not contact.get("contact_type"):
            contact["contact_type"] = "client"

    # Cache for apply step
    token = str(uuid.uuid4())
    _CLIENT_CONTACT_IMPORT_CACHE[token] = (
        contacts,
        client_id,
        time.time(),
    )

    # Purge stale cache entries (>30 min)
    now = time.time()
    stale = [k for k, v in _CLIENT_CONTACT_IMPORT_CACHE.items() if now - v[2] > 1800]
    for k in stale:
        _CLIENT_CONTACT_IMPORT_CACHE.pop(k, None)

    return templates.TemplateResponse("clients/_ai_contacts_review.html", {
        "request": request,
        "client": dict(client),
        "contacts": contacts,
        "warnings": warnings,
        "token": token,
        "client_id": client_id,
        "contact_roles": cfg.get("contact_roles", []),
    })
```

- [ ] **Step 5: Add apply route**

```python
@router.post("/{client_id}/ai-contact-import/apply", response_class=HTMLResponse)
async def client_ai_contact_import_apply(
    request: Request,
    client_id: int,
    conn=Depends(get_db),
):
    """Apply selected contacts from AI import to the client."""
    from policydb.utils import clean_email, format_phone

    form = await request.form()
    token = form.get("token", "")

    cache = _CLIENT_CONTACT_IMPORT_CACHE.get(token)
    if not cache:
        return HTMLResponse(
            '<div class="p-4 text-red-600 text-sm">Session expired — please re-parse.</div>'
        )

    contacts, cached_client_id, ts = cache
    if cached_client_id != client_id:
        return HTMLResponse(
            '<div class="p-4 text-red-600 text-sm">Client mismatch.</div>'
        )

    created = 0
    updated = 0
    errors: list[str] = []

    for i, contact in enumerate(contacts):
        if not form.get(f"select_{i}"):
            continue

        role = form.get(f"role_{i}", contact.get("role", ""))
        contact_type = form.get(f"type_{i}", contact.get("contact_type", "client"))

        try:
            # Normalize phone/email
            email = contact.get("email")
            if email:
                result = clean_email(email)
                email = result.get("formatted", email) if isinstance(result, dict) else email

            phone = contact.get("phone")
            if phone:
                result = format_phone(phone)
                phone = result.get("formatted", phone) if isinstance(result, dict) else phone

            mobile = contact.get("mobile")
            if mobile:
                result = format_phone(mobile)
                mobile = result.get("formatted", mobile) if isinstance(result, dict) else mobile

            cid = get_or_create_contact(
                conn,
                contact["name"],
                email=email,
                phone=phone,
                mobile=mobile,
                organization=contact.get("organization"),
            )

            assign_contact_to_client(
                conn,
                cid,
                client_id,
                contact_type=contact_type,
                role=role,
                title=contact.get("title", ""),
            )

            if contact.get("existing_contact"):
                updated += 1
            else:
                created += 1
        except Exception as e:
            errors.append(f"{contact['name']}: {e}")

    conn.commit()
    _CLIENT_CONTACT_IMPORT_CACHE.pop(token, None)

    total = created + updated
    parts = [
        '<div class="p-4 space-y-3">',
        '<div class="flex items-center gap-2">',
        '<svg class="w-5 h-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">',
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>',
        "</svg>",
        f'<span class="text-sm font-medium text-gray-900">'
        f'{total} contact{"s" if total != 1 else ""} imported</span>',
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
    if errors:
        parts.append(
            f'<span class="px-2 py-0.5 rounded-full text-xs bg-red-50 text-red-700">'
            f"{len(errors)} error(s)</span>"
        )
    parts.append("</div>")
    if errors:
        parts.append('<div class="text-xs text-red-600 mt-1">')
        for e in errors:
            parts.append(f"<p>{e}</p>")
        parts.append("</div>")
    parts.append(
        '<p class="text-xs text-gray-500 mt-1">Reload the Contacts tab to see updated list.</p>'
    )
    parts.append("</div>")
    return HTMLResponse("\n".join(parts))
```

- [ ] **Step 6: Verify imports exist**

Ensure `get_client_contacts`, `get_or_create_contact`, `assign_contact_to_client` are imported from `policydb.queries` at the top of `clients.py`. Check with a grep — these should already be imported for the existing contact management routes.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: add client-level AI contact import routes (prompt, parse, apply)"
```

---

## Task 6: Create `clients/_ai_contacts_review.html` template

**Files:**
- Create: `src/policydb/web/templates/clients/_ai_contacts_review.html`

- [ ] **Step 1: Create the review template**

Create `src/policydb/web/templates/clients/_ai_contacts_review.html` based on `policies/_ai_contacts_review.html`, adding the Type column:

```html
{# AI Contact Bulk Import Review Panel — shows extracted contacts with checkboxes for import.
   Context variables:
     client        — client dict
     contacts      — list of extracted contact dicts (name, email, phone, mobile, org, title, role, contact_type, already_assigned, existing_contact)
     warnings      — list of warning strings
     token         — cache token for apply step
     client_id     — client ID int
     contact_roles — list of role options from config
#}
<div id="ai-contact-import-result" class="space-y-4">

  {# Summary badges #}
  <div class="flex items-center gap-3 flex-wrap">
    <span class="px-2.5 py-1 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700">
      {{ contacts | length }} contact{{ 's' if contacts | length != 1 else '' }} extracted
    </span>
    {% set new_count = contacts | selectattr('already_assigned', 'equalto', false) | selectattr('existing_contact', 'none') | list | length %}
    {% set exists_count = contacts | selectattr('already_assigned', 'equalto', false) | rejectattr('existing_contact', 'none') | list | length %}
    {% set assigned_count = contacts | selectattr('already_assigned', 'equalto', true) | list | length %}
    {% if new_count %}
    <span class="px-2.5 py-1 rounded-full text-xs font-medium bg-green-50 text-green-700">
      {{ new_count }} new
    </span>
    {% endif %}
    {% if exists_count %}
    <span class="px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
      {{ exists_count }} exists globally
    </span>
    {% endif %}
    {% if assigned_count %}
    <span class="px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700">
      {{ assigned_count }} already assigned
    </span>
    {% endif %}
  </div>

  {# Warnings #}
  {% if warnings %}
  <div class="p-3 bg-amber-50 border border-amber-200 rounded-lg">
    <p class="text-xs font-semibold text-amber-800 mb-1">Warnings</p>
    {% for w in warnings %}
    <p class="text-xs text-amber-700">{{ w }}</p>
    {% endfor %}
  </div>
  {% endif %}

  {# Contact list form #}
  <form id="ai-contacts-apply-form">
    <input type="hidden" name="token" value="{{ token }}">
    <div class="border border-gray-200 rounded-lg overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs text-gray-400 uppercase tracking-wide bg-gray-50 border-b border-gray-200">
            <th class="px-3 py-2 w-8">
              <input type="checkbox" id="ai-contacts-select-all" checked
                     class="rounded border-gray-300 text-marsh focus:ring-marsh"
                     onchange="document.querySelectorAll('.ai-contact-cb:not(:disabled)').forEach(function(c){c.checked=this.checked}.bind(this))">
            </th>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Email</th>
            <th class="px-3 py-2">Phone</th>
            <th class="px-3 py-2">Org</th>
            <th class="px-3 py-2">Title</th>
            <th class="px-3 py-2">Role</th>
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2 w-16">Status</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {% for c in contacts %}
          <tr class="{% if c.already_assigned %}bg-gray-50 opacity-60{% else %}hover:bg-gray-50{% endif %}">
            <td class="px-3 py-2">
              <input type="checkbox" name="select_{{ loop.index0 }}" value="1"
                     class="ai-contact-cb rounded border-gray-300 text-marsh focus:ring-marsh"
                     {% if not c.already_assigned %}checked{% endif %}
                     {% if c.already_assigned %}disabled{% endif %}>
            </td>
            <td class="px-3 py-2 font-medium text-gray-900">{{ c.name }}</td>
            <td class="px-3 py-2 text-gray-600 text-xs">{{ c.email or '' }}</td>
            <td class="px-3 py-2 text-gray-600 text-xs">
              {{ c.phone or '' }}
              {% if c.mobile %}<br><span class="text-gray-400">Cell: {{ c.mobile }}</span>{% endif %}
            </td>
            <td class="px-3 py-2 text-gray-600 text-xs">{{ c.organization or '' }}</td>
            <td class="px-3 py-2 text-gray-500 text-xs">{{ c.title or '' }}</td>
            <td class="px-3 py-2">
              <select name="role_{{ loop.index0 }}"
                      class="w-full rounded border-gray-200 text-xs py-1 px-2 focus:ring-marsh focus:border-marsh">
                <option value="">-- Role --</option>
                {% for r in contact_roles %}
                <option value="{{ r }}" {% if c.role and c.role == r %}selected{% endif %}>{{ r }}</option>
                {% endfor %}
                {% if c.role and c.role not in contact_roles %}
                <option value="{{ c.role }}" selected>{{ c.role }}</option>
                {% endif %}
              </select>
            </td>
            <td class="px-3 py-2">
              <select name="type_{{ loop.index0 }}"
                      class="w-full rounded border-gray-200 text-xs py-1 px-2 focus:ring-marsh focus:border-marsh">
                <option value="client" {% if c.contact_type == 'client' %}selected{% endif %}>Client</option>
                <option value="internal" {% if c.contact_type == 'internal' %}selected{% endif %}>Internal</option>
                <option value="external" {% if c.contact_type == 'external' %}selected{% endif %}>External</option>
              </select>
            </td>
            <td class="px-3 py-2">
              {% if c.already_assigned %}
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-700">Assigned</span>
              {% elif c.existing_contact %}
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-100 text-blue-700">Exists</span>
              {% else %}
              <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 text-gray-500">New</span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between mt-4">
      <p class="text-xs text-gray-400">
        Uncheck contacts you don't want to import. Already-assigned contacts are skipped.
      </p>
      <button type="submit"
              hx-post="/clients/{{ client_id }}/ai-contact-import/apply"
              hx-target="#ai-contact-import-result"
              hx-swap="innerHTML"
              hx-include="#ai-contacts-apply-form"
              class="px-4 py-2 bg-marsh text-white text-sm font-medium rounded-lg hover:bg-marsh-light transition-colors">
        Apply Selected
      </button>
    </div>
  </form>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/clients/_ai_contacts_review.html
git commit -m "feat: add client-level AI contacts review template with type column"
```

---

## Task 7: Add AI Import button to contacts tab

**Files:**
- Modify: `src/policydb/web/templates/clients/_tab_contacts.html`

- [ ] **Step 1: Add button and result div to `_tab_contacts.html`**

The current file is simple (19 lines). Replace the opening with a header row containing the AI Import button:

Replace the contents of `_tab_contacts.html` with:

```html
{# Client Contacts Tab — all contact panels + compose email + AI import #}
<div id="tab-contacts-content">

{# Header with AI Import #}
<div class="flex items-center justify-between mb-4 no-print">
  <h3 class="text-sm font-semibold text-gray-700">Contacts</h3>
  <button type="button"
    hx-get="/clients/{{ client.id }}/ai-contact-import/prompt"
    hx-target="#ai-contact-import-result"
    hx-swap="innerHTML"
    class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 rounded-lg transition-colors">
    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
    </svg>
    AI Import
  </button>
</div>

{# AI Import result area #}
<div id="ai-contact-import-result" class="mb-4"></div>

{% include "clients/_contacts.html" %}
{% include "clients/_team_contacts.html" %}
{% include "clients/_external_contacts.html" %}
{% include "clients/_billing_accounts.html" %}
{% include "clients/_placement_colleagues.html" %}

<!-- Compose Email -->
<div class="mt-4 no-print">
  <button type="button"
    onclick="openComposeSlideover({context:'client', client_id:{{ client.id }}})"
    class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-marsh hover:text-white bg-white hover:bg-marsh border border-marsh/30 hover:border-marsh rounded-lg transition-colors no-print">
    ✉ Compose Email
  </button>
</div>

</div>
```

- [ ] **Step 2: Test manually**

Start server, navigate to a client → Contacts tab. Verify:
1. "AI Import" button visible in header row
2. Clicking it opens the `_ai_import_panel.html` slideover
3. The prompt contains client name, carriers, and brokerage name
4. Copy Prompt button works and advances to step 2

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_tab_contacts.html
git commit -m "feat: add AI Import button to client contacts tab"
```

---

## Task 8: End-to-end QA test

**Files:** None (testing only)

- [ ] **Step 1: Kill existing servers and start fresh**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; cd /path/to/policydb && pdb serve
```

- [ ] **Step 2: Navigate to a client with existing contacts**

Go to a client page → Contacts tab. Verify the AI Import button is visible.

- [ ] **Step 3: Click AI Import and copy prompt**

Click "AI Import" button. Verify:
- Slideover panel opens
- Prompt text includes client name, industry, known carriers, brokerage name
- Context badges show client info
- "Copy Prompt" button copies and advances to step 2

- [ ] **Step 4: Test with sample JSON**

Paste this sample JSON into the step 2 textarea:

```json
[
  {"name": "John Doe", "email": "john@client.com", "phone": "(555) 111-2222", "title": "Risk Manager", "role": "Manager", "contact_type": "client"},
  {"name": "Sarah Lee", "email": "sarah@marsh.com", "phone": "(555) 333-4444", "title": "Account Executive", "role": "Broker", "contact_type": "internal"},
  {"name": "Mike Chen", "email": "mike@travelers.com", "title": "Underwriter", "role": "Underwriter", "contact_type": "external"}
]
```

Click Parse. Verify:
- Review matrix shows 3 contacts
- Type column shows correct pre-selected values
- Role dropdowns work
- Status badges correct (New vs Exists vs Assigned)

- [ ] **Step 5: Apply and verify**

Select contacts, click Apply Selected. Verify:
- Success message shows correct counts
- Reload contacts tab — new contacts appear in correct sections (client/internal/external)

- [ ] **Step 6: Test edge cases**

- Paste invalid JSON → verify red error message
- Paste JSON with a contact that already exists → verify "Exists" badge
- Paste JSON with invalid contact_type → verify defaults to "client" with warning

- [ ] **Step 7: Screenshot results**

Take screenshots of: button, slideover, review matrix, success message, contacts tab after import.

- [ ] **Step 8: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: address QA issues from bulk contact import testing"
```
