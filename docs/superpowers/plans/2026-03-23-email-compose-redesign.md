# Email Compose System Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all inconsistent inline compose panels and ep-trigger popovers with a unified right-side compose slideover featuring transparent role-based recipients, simplified templates (7→2 contexts), and RFI team notification.

**Architecture:** New `/compose` route module serves a single slideover partial loaded via HTMX from any page. Recipients loaded via JSON endpoint with role/badge metadata. All 13+ trigger points converted to call `openComposeSlideover()`. Old infrastructure removed after all triggers migrated. Template contexts consolidated via SQL migration.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, Tailwind CSS (CDN)

**Spec:** `docs/superpowers/specs/2026-03-23-email-compose-redesign.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/policydb/web/routes/compose.py` | Compose slideover endpoints: panel, recipients, render |
| `src/policydb/web/templates/_compose_slideover.html` | Right-side slideover shell + JS |
| `src/policydb/web/templates/_recipient_picker.html` | Reusable recipient picker partial |
| `src/policydb/migrations/071_simplify_template_contexts.sql` | Context consolidation migration |

### Modified Files
| File | Change |
|------|--------|
| `src/policydb/web/app.py` | Register compose router |
| `src/policydb/web/templates/base.html` | Slideover container, `openComposeSlideover()` JS, extract `buildMailto()`, remove ep-trigger IIFE |
| `src/policydb/email_templates.py` | Add `rfi_notify_context()`, merge token groups |
| `src/policydb/config.py` | Add `email_subject_rfi_notify` |
| `src/policydb/web/routes/settings.py` | Add key to `_allowed` set, pass to template |
| `src/policydb/web/templates/settings.html` | Render new subject field |
| `src/policydb/db.py` | Wire migration 071 |
| `src/policydb/web/routes/templates.py` | Update `_CONTEXT_LABELS`, update CRUD for 2 contexts |
| `src/policydb/web/templates/templates/_template_form.html` | Pill buttons instead of dropdown |
| 13+ trigger templates | Convert ep-trigger buttons to `openComposeSlideover()` |

---

## Phase 1 — Build New Infrastructure (Additive)

### Task 1: Create Compose Route Module

**Files:**
- Create: `src/policydb/web/routes/compose.py`
- Modify: `src/policydb/web/app.py:189-205`

- [ ] **Step 1: Create compose.py with router skeleton**

Create `src/policydb/web/routes/compose.py`:

```python
"""Unified email compose slideover."""

from __future__ import annotations

import json
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse

from policydb import config as cfg
from policydb.web.app import get_db, templates as jinja_templates
from policydb.email_templates import (
    render_tokens,
    policy_context,
    client_context,
    location_context,
    followup_context,
    meeting_context,
    timeline_context,
    CONTEXT_TOKEN_GROUPS,
    CONTEXT_TOKENS,
)

router = APIRouter(prefix="/compose", tags=["compose"])
```

**Note:** This follows the exact pattern from `templates.py` line 20: `from policydb.web.app import get_db, templates`. All route functions use `conn=Depends(get_db)` as a parameter — never create connections inline.

- [ ] **Step 2: Add `_load_recipients()` function**

Port and enhance `_load_contacts()` from `templates.py` (lines 165–226). Add `pre_checked` flag and `badge` info.

```python
def _load_recipients(
    conn,
    policy_uid: str = "",
    client_id: int = 0,
    project_name: str = "",
    mode: str = "",
) -> list[dict]:
    """Load recipients with role badges and pre-check suggestions.

    Returns list of dicts:
      {name, email, role, source, badge, pre_checked}

    source: "client" | "internal" | "policy"
    badge: "CLIENT" | "INTERNAL" | "PLACEMENT" | "UNDERWRITER"
    """
    contacts = []
    seen_emails = set()

    def _add(name, email, role, source, badge, pre_checked=False):
        if not email:
            return
        key = email.strip().lower()
        if key in seen_emails:
            return
        seen_emails.add(key)
        contacts.append({
            "name": name or "",
            "email": email.strip(),
            "role": role or "",
            "source": source,
            "badge": badge,
            "pre_checked": pre_checked,
        })

    is_rfi_notify = mode == "rfi_notify"

    # Policy-level contacts (placement colleagues, underwriters)
    if policy_uid:
        rows = conn.execute(
            """SELECT c.name, c.email, cpa.role, cpa.is_placement_colleague
               FROM contact_policy_assignments cpa
               JOIN contacts c ON c.id = cpa.contact_id
               JOIN policies p ON p.id = cpa.policy_id
               WHERE p.policy_uid = ?""",
            (policy_uid,),
        ).fetchall()
        for r in rows:
            badge = "PLACEMENT" if r["is_placement_colleague"] else "UNDERWRITER"
            _add(r["name"], r["email"], r["role"], "policy", badge,
                 pre_checked=(badge == "PLACEMENT" or is_rfi_notify))

    # Location: all policy contacts for all policies in project
    if project_name and client_id and not policy_uid:
        rows = conn.execute(
            """SELECT DISTINCT c.name, c.email, cpa.role, cpa.is_placement_colleague
               FROM contact_policy_assignments cpa
               JOIN contacts c ON c.id = cpa.contact_id
               JOIN policies p ON p.id = cpa.policy_id
               WHERE p.client_id = ? AND p.project_name = ?""",
            (client_id, project_name),
        ).fetchall()
        for r in rows:
            badge = "PLACEMENT" if r["is_placement_colleague"] else "UNDERWRITER"
            _add(r["name"], r["email"], r["role"], "policy", badge, pre_checked=True)

    # Client-level contacts
    if client_id:
        # Internal team
        rows = conn.execute(
            """SELECT c.name, c.email, cca.role
               FROM contact_client_assignments cca
               JOIN contacts c ON c.id = cca.contact_id
               WHERE cca.client_id = ? AND cca.contact_type = 'internal'""",
            (client_id,),
        ).fetchall()
        for r in rows:
            _add(r["name"], r["email"], r["role"], "internal", "INTERNAL",
                 pre_checked=True)

        # External client contacts (skip for rfi_notify)
        if not is_rfi_notify:
            rows = conn.execute(
                """SELECT c.name, c.email, cca.role, cca.is_primary
                   FROM contact_client_assignments cca
                   JOIN contacts c ON c.id = cca.contact_id
                   WHERE cca.client_id = ? AND cca.contact_type = 'client'
                   ORDER BY cca.is_primary DESC, c.name""",
                (client_id,),
            ).fetchall()
            for r in rows:
                _add(r["name"], r["email"], r["role"], "client", "CLIENT",
                     pre_checked=False)

    return contacts
```

- [ ] **Step 3: Add `GET /compose` panel endpoint**

```python
@router.get("", response_class=HTMLResponse)
def compose_panel(
    request: Request,
    conn=Depends(get_db),
    context: str = Query("policy"),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    bundle_id: int = Query(0),
    mode: str = Query(""),
    to_email: str = Query(""),
    template_id: int = Query(0),
):
    """Return the compose slideover HTML partial."""

    # Build rendering context
    render_ctx = {}
    context_label = ""

    if mode == "rfi_notify" and bundle_id:
        from policydb.email_templates import rfi_notify_context
        render_ctx = rfi_notify_context(conn, bundle_id)
        context_label = f"{render_ctx.get('client_name', '')} — {render_ctx.get('rfi_uid', '')} Notify"
        # Derive client_id from bundle
        bundle = conn.execute(
            "SELECT client_id FROM client_request_bundles WHERE id = ?",
            (bundle_id,),
        ).fetchone()
        if bundle:
            client_id = bundle["client_id"]
    elif policy_uid:
        render_ctx = policy_context(conn, policy_uid)
        context_label = f"{render_ctx.get('client_name', '')} — {render_ctx.get('policy_type', '')} ({policy_uid})"
        if not client_id:
            pol = conn.execute(
                "SELECT client_id FROM policies WHERE policy_uid = ?",
                (policy_uid,),
            ).fetchone()
            if pol:
                client_id = pol["client_id"]
    elif project_name and client_id:
        render_ctx = location_context(conn, client_id, project_name)
        render_ctx.update(client_context(conn, client_id))
        context_label = f"{render_ctx.get('client_name', '')} — {project_name}"
    elif client_id:
        render_ctx = client_context(conn, client_id)
        context_label = render_ctx.get("client_name", "")

    # Load recipients
    recipients = _load_recipients(
        conn, policy_uid=policy_uid, client_id=client_id,
        project_name=project_name, mode=mode,
    )

    # Determine primary (To) contact
    primary_contact = None
    if to_email:
        for r in recipients:
            if r["email"].lower() == to_email.lower():
                primary_contact = r
                break
    if not primary_contact and mode != "rfi_notify":
        for r in recipients:
            if r["badge"] == "CLIENT":
                primary_contact = r
                break

    # Pre-fill subject from config
    subject_key = "email_subject_policy"
    if mode == "rfi_notify":
        subject_key = "email_subject_rfi_notify"
    elif context == "client" and not policy_uid:
        subject_key = "email_subject_client"
    subject_template = cfg.get(subject_key, "")
    subject = render_tokens(subject_template, render_ctx)

    # Build body for RFI notify
    body = ""
    ref_tag = render_ctx.get("ref_tag", "")
    if mode == "rfi_notify":
        received = render_ctx.get("received_items", [])
        outstanding = render_ctx.get("outstanding_items", [])
        lines = [f"FYI — Received the following from {render_ctx.get('client_name', '')}:\n"]
        for item in received:
            lines.append(f"  • {item}")
        if outstanding:
            lines.append(f"\nStill outstanding:")
            for item in outstanding:
                lines.append(f"  • {item}")
        if ref_tag:
            lines.append(f"\n[PDB:{ref_tag}]")
        body = "\n".join(lines)
    elif ref_tag:
        body = f"\n\n[PDB:{ref_tag}]"

    # Load templates for dropdown
    tpl_context = "policy" if policy_uid or project_name else "client"
    templates = conn.execute(
        "SELECT id, name, context FROM email_templates WHERE context = ? OR context = 'client' ORDER BY name",
        (tpl_context,),
    ).fetchall()

    # Render selected template if provided
    rendered_subject = subject
    rendered_body = body
    if template_id:
        tpl = conn.execute(
            "SELECT subject_template, body_template FROM email_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if tpl:
            rendered_subject = render_tokens(tpl["subject_template"], render_ctx)
            rendered_body = render_tokens(tpl["body_template"], render_ctx)
            if ref_tag:
                rendered_body += f"\n\n[PDB:{ref_tag}]"

    return jinja_templates.TemplateResponse(
        "_compose_slideover.html",
        {
            "request": request,
            "context": context,
            "context_label": context_label,
            "policy_uid": policy_uid,
            "client_id": client_id,
            "project_name": project_name,
            "bundle_id": bundle_id,
            "mode": mode,
            "recipients": recipients,
            "primary_contact": primary_contact,
            "subject": rendered_subject,
            "body": rendered_body,
            "ref_tag": ref_tag,
            "templates": templates,
            "template_id": template_id,
        },
    )
```

- [ ] **Step 4: Add `GET /compose/recipients` JSON endpoint**

```python
@router.get("/recipients")
def compose_recipients(
    conn=Depends(get_db),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
    mode: str = Query(""),
):
    """Return recipient list as JSON for dynamic updates."""
    recipients = _load_recipients(
        conn, policy_uid=policy_uid, client_id=client_id,
        project_name=project_name, mode=mode,
    )
    return recipients
```

- [ ] **Step 5: Add `GET /compose/render` template render endpoint**

```python
@router.get("/render")
def compose_render(
    conn=Depends(get_db),
    template_id: int = Query(0),
    policy_uid: str = Query(""),
    client_id: int = Query(0),
    project_name: str = Query(""),
):
    """Render a template and return subject + body as JSON.

    The slideover JS updates the existing subject input and body textarea
    with the rendered values — no HTML returned, avoiding XSS and
    duplicate element issues.
    """
    render_ctx = {}
    if policy_uid:
        render_ctx = policy_context(conn, policy_uid)
    elif project_name and client_id:
        render_ctx = location_context(conn, client_id, project_name)
        render_ctx.update(client_context(conn, client_id))
    elif client_id:
        render_ctx = client_context(conn, client_id)

    subject = ""
    body = ""
    ref_tag = render_ctx.get("ref_tag", "")

    if template_id:
        tpl = conn.execute(
            "SELECT subject_template, body_template FROM email_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if tpl:
            subject = render_tokens(tpl["subject_template"], render_ctx)
            body = render_tokens(tpl["body_template"], render_ctx)
            if ref_tag:
                body += f"\n\n[PDB:{ref_tag}]"

    return {"subject": subject, "body": body}
```

- [ ] **Step 6: Register compose router in app.py**

In `src/policydb/web/app.py`, add the import around line 189 and include around line 205:

```python
# Add to imports line (~189):
from policydb.web.routes import ..., compose  # add compose

# Add after last include_router (~205):
app.include_router(compose.router)
```

- [ ] **Step 7: Verify server starts**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/reverent-goldberg && pip install -e . && policydb serve &`

Verify: Server starts without import errors. `curl http://127.0.0.1:8000/compose` returns HTML (even if incomplete template).

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/compose.py src/policydb/web/app.py
git commit -m "feat(compose): add compose route module with panel, recipients, render endpoints"
```

---

### Task 2: Create Compose Slideover Template

**Files:**
- Create: `src/policydb/web/templates/_compose_slideover.html`
- Create: `src/policydb/web/templates/_recipient_picker.html`

- [ ] **Step 1: Create `_recipient_picker.html`**

Create `src/policydb/web/templates/_recipient_picker.html`:

```html
{# Recipient picker partial — role-based groups with badges #}

{# To section #}
<div class="mb-3">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">To</div>
  {% if primary_contact %}
  <div id="compose-to-row" class="flex items-center gap-2 px-3 py-2 bg-green-50 border border-green-200 rounded-lg">
    <span class="bg-green-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded">{{ primary_contact.badge }}</span>
    <span class="text-sm font-medium">{{ primary_contact.name }}</span>
    <span class="text-xs text-gray-500">{{ primary_contact.email }}</span>
    <button type="button" onclick="removeComposeTo()" class="ml-auto text-gray-400 hover:text-red-500 text-xs">✕</button>
  </div>
  <input type="hidden" id="compose-to-email" value="{{ primary_contact.email }}">
  <input type="hidden" id="compose-to-name" value="{{ primary_contact.name }}">
  {% elif mode != 'rfi_notify' %}
  <div class="text-xs text-gray-400 italic px-3 py-2 border border-dashed border-gray-200 rounded-lg">
    No primary contact — use search below to add one
  </div>
  <input type="hidden" id="compose-to-email" value="">
  <input type="hidden" id="compose-to-name" value="">
  {% endif %}
</div>

{# Suggested CC section #}
<div class="mb-3">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
    {% if mode == 'rfi_notify' %}To — internal notification{% else %}Suggested CC{% endif %}
  </div>
  <div id="compose-cc-list" class="flex flex-col gap-1.5 max-h-40 overflow-y-auto">
    {% for c in recipients %}
    {% if c != primary_contact %}
    <label class="flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer transition-colors
      {% if c.badge == 'CLIENT' %}bg-green-50 border border-green-200 hover:bg-green-100
      {% elif c.badge == 'INTERNAL' %}bg-blue-50 border border-blue-200 hover:bg-blue-100
      {% else %}bg-amber-50 border border-amber-200 hover:bg-amber-100{% endif %}">
      <input type="checkbox" class="compose-cc-check accent-{% if c.badge == 'CLIENT' %}green{% elif c.badge == 'INTERNAL' %}blue{% else %}amber{% endif %}-500"
             value="{{ c.email }}" data-name="{{ c.name }}"
             {% if c.pre_checked %}checked{% endif %}
             onchange="updateComposePreview()">
      <span class="text-white text-[10px] font-bold px-1.5 py-0.5 rounded
        {% if c.badge == 'CLIENT' %}bg-green-500
        {% elif c.badge == 'INTERNAL' %}bg-blue-500
        {% else %}bg-amber-500{% endif %}">{{ c.badge }}</span>
      <span class="text-sm">{{ c.name }}</span>
      <span class="text-xs text-gray-500">{{ c.role }}</span>
    </label>
    {% endif %}
    {% endfor %}
    {% if not recipients %}
    <div class="text-xs text-gray-400 italic px-3 py-2 border border-dashed border-gray-200 rounded-lg">
      No contacts found — add one below or type an email address
    </div>
    {% endif %}
  </div>
</div>

{# Add recipient #}
<div class="mb-3">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Add recipient</div>
  <div class="flex gap-2">
    <input type="text" id="compose-add-email" placeholder="Search contacts or type email..."
           class="flex-1 px-3 py-1.5 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-marsh/30 focus:border-marsh"
           onkeydown="if(event.key==='Enter'){addComposeRecipient(); event.preventDefault();}">
    <button type="button" onclick="addComposeRecipient()"
            class="px-2 py-1.5 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg border border-gray-200">Add</button>
  </div>
</div>

{# Preview line #}
<div id="compose-preview" class="px-3 py-2 bg-gray-100 rounded-lg text-xs text-gray-600 font-mono">
  <!-- Updated by JS -->
</div>
```

- [ ] **Step 2: Create `_compose_slideover.html`**

Create `src/policydb/web/templates/_compose_slideover.html`:

```html
{# Unified compose slideover content — loaded via HTMX into #compose-slideover-body #}

<div class="flex items-center justify-between mb-3 pb-3 border-b border-gray-200">
  <h3 class="text-base font-semibold text-gray-800">Compose Email</h3>
  <button type="button" onclick="closeComposeSlideover()" class="text-gray-400 hover:text-gray-600 text-lg">✕</button>
</div>

{# Context label #}
{% if context_label %}
<div class="text-xs text-gray-500 mb-3 px-2 py-1 bg-gray-50 rounded">{{ context_label }}</div>
{% endif %}

{# Recipient picker #}
{% include "_recipient_picker.html" %}

<div class="border-t border-gray-200 my-3"></div>

{# Subject #}
<div class="mb-2">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Subject</div>
  <input id="compose-subject" type="text" value="{{ subject }}"
         class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-marsh/30 focus:border-marsh"
         oninput="markComposeEdited()">
</div>

{# Template selector #}
<div class="mb-2">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Template <span class="font-normal text-gray-400">(optional)</span></div>
  <select id="compose-template-select"
          class="w-full px-3 py-1.5 border border-gray-200 rounded-lg text-sm bg-white"
          onchange="loadComposeTemplate(this.value)">
    <option value="">No template — quick email</option>
    {% for t in templates %}
    <option value="{{ t.id }}" {% if t.id == template_id %}selected{% endif %}>{{ t.name }}{% if t.context != context %} ({{ t.context }}){% endif %}</option>
    {% endfor %}
  </select>
</div>

{# Body #}
<div class="mb-3">
  <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Body</div>
  <textarea id="compose-body" rows="8"
            class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-marsh/30 focus:border-marsh"
            oninput="markComposeEdited()">{{ body }}</textarea>
</div>

<div class="border-t border-gray-200 my-3"></div>

{# Preview #}
<div id="compose-preview-final" class="px-3 py-2 bg-gray-100 rounded-lg text-xs text-gray-600 font-mono mb-3">
  <!-- Updated by updateComposePreview() -->
</div>

{# Actions #}
<div class="flex gap-2">
  <button type="button" onclick="copyComposeAll()"
          class="flex-1 px-3 py-2 text-sm font-medium bg-gray-100 hover:bg-gray-200 rounded-lg border border-gray-200 transition-colors">
    📋 Copy All
  </button>
  <button type="button" onclick="openComposeInMail()"
          class="flex-1 px-3 py-2 text-sm font-medium text-white bg-marsh hover:bg-marsh-light rounded-lg transition-colors">
    ✉ Open in Mail →
  </button>
</div>

<script>
var composeEdited = false;

function markComposeEdited() { composeEdited = true; }

function loadComposeTemplate(templateId) {
  if (!templateId) {
    // Reset to quick email defaults
    document.getElementById('compose-subject').value = '{{ subject }}';
    document.getElementById('compose-body').value = '{{ body }}';
    return;
  }
  var qs = 'template_id=' + templateId +
    '&policy_uid={{ policy_uid }}' +
    '&client_id={{ client_id }}' +
    '&project_name={{ project_name | urlencode }}';
  fetch('/compose/render?' + qs)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      document.getElementById('compose-subject').value = data.subject || '';
      document.getElementById('compose-body').value = data.body || '';
    });
}

function updateComposePreview() {
  var toEmail = document.getElementById('compose-to-email');
  var to = toEmail ? toEmail.value : '';
  var ccEmails = [];
  document.querySelectorAll('.compose-cc-check:checked').forEach(function(cb) {
    ccEmails.push(cb.value);
  });
  var parts = [];
  if (to) parts.push('To: ' + to);
  if (ccEmails.length) parts.push('CC: ' + ccEmails.join(', '));
  var preview = document.getElementById('compose-preview');
  var previewFinal = document.getElementById('compose-preview-final');
  var text = parts.join(' · ') || 'No recipients selected';
  if (preview) preview.textContent = text;
  if (previewFinal) previewFinal.textContent = text;
}

function removeComposeTo() {
  var row = document.getElementById('compose-to-row');
  if (row) row.remove();
  document.getElementById('compose-to-email').value = '';
  document.getElementById('compose-to-name').value = '';
  updateComposePreview();
}

function addComposeRecipient() {
  var input = document.getElementById('compose-add-email');
  var email = input.value.trim();
  if (!email) return;
  // Add as a CC checkbox
  var container = document.getElementById('compose-cc-list');
  var label = document.createElement('label');
  label.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer bg-gray-50 border border-gray-200';
  label.innerHTML = '<input type="checkbox" class="compose-cc-check accent-gray-500" value="' + email + '" data-name="' + email + '" checked onchange="updateComposePreview()">' +
    '<span class="bg-gray-400 text-white text-[10px] font-bold px-1.5 py-0.5 rounded">MANUAL</span>' +
    '<span class="text-sm">' + email + '</span>';
  container.appendChild(label);
  input.value = '';
  updateComposePreview();
}

function copyComposeAll() {
  var subject = document.getElementById('compose-subject').value;
  var body = document.getElementById('compose-body').value;
  var text = subject + '\n\n' + body;
  navigator.clipboard.writeText(text).then(function() {
    showToast('Copied to clipboard');
  });
}

function openComposeInMail() {
  var to = (document.getElementById('compose-to-email') || {}).value || '';
  var subject = document.getElementById('compose-subject').value;
  var body = document.getElementById('compose-body').value;
  var ccEmails = [];
  document.querySelectorAll('.compose-cc-check:checked').forEach(function(cb) {
    ccEmails.push(cb.value);
  });
  var ref = '{{ ref_tag }}';
  var url = buildMailto(to, subject, ccEmails, body, ref ? '[PDB:' + ref + ']' : '');
  window.location.href = url;
}

// Initialize preview on load
updateComposePreview();
</script>
```

- [ ] **Step 3: Verify templates render**

Start server, then `curl http://127.0.0.1:8000/compose?context=policy` — should return HTML with the slideover content. Check for Jinja2 syntax errors in server logs.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/_compose_slideover.html src/policydb/web/templates/_recipient_picker.html
git commit -m "feat(compose): add slideover and recipient picker templates"
```

---

### Task 3: Add Slideover Container + JS to base.html

**Files:**
- Modify: `src/policydb/web/templates/base.html`

- [ ] **Step 1: Add slideover container after `</main>` (around line 565)**

Insert after the closing `</main>` tag:

```html
<!-- Compose Slideover -->
<div id="compose-slideover-overlay" class="fixed inset-0 bg-black/30 z-40 hidden" onclick="closeComposeSlideover()"></div>
<div id="compose-slideover" class="fixed top-0 right-0 h-full w-96 max-w-full bg-white shadow-2xl z-50 transform translate-x-full transition-transform duration-200 ease-in-out overflow-y-auto">
  <div id="compose-slideover-body" class="p-4">
    <!-- Loaded via HTMX -->
  </div>
</div>
```

- [ ] **Step 2: Add `openComposeSlideover()` and `closeComposeSlideover()` JS**

Add after the slideover container div (or in the existing script section):

```html
<script>
function buildMailto(to, subject, ccEmails, body, ref) {
  if (ref) body = (body ? body + '\n\n' : '') + ref;
  var url = 'mailto:' + encodeURIComponent(to) + '?subject=' + encodeURIComponent(subject);
  if (ccEmails.length) url += '&cc=' + encodeURIComponent(ccEmails.join(','));
  if (body) url += '&body=' + encodeURIComponent(body);
  return url;
}

function openComposeSlideover(params) {
  var qs = new URLSearchParams();
  if (params.context) qs.set('context', params.context);
  if (params.policy_uid) qs.set('policy_uid', params.policy_uid);
  if (params.client_id) qs.set('client_id', params.client_id);
  if (params.project_name) qs.set('project_name', params.project_name);
  if (params.bundle_id) qs.set('bundle_id', params.bundle_id);
  if (params.mode) qs.set('mode', params.mode);
  if (params.to_email) qs.set('to_email', params.to_email);
  if (params.template_id) qs.set('template_id', params.template_id);

  htmx.ajax('GET', '/compose?' + qs.toString(), {
    target: '#compose-slideover-body',
    swap: 'innerHTML'
  }).then(function() {
    document.getElementById('compose-slideover').classList.remove('translate-x-full');
    document.getElementById('compose-slideover-overlay').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  });
}

function closeComposeSlideover() {
  if (typeof composeEdited !== 'undefined' && composeEdited) {
    if (!confirm('You have unsaved changes. Close anyway?')) return;
  }
  document.getElementById('compose-slideover').classList.add('translate-x-full');
  document.getElementById('compose-slideover-overlay').classList.add('hidden');
  document.body.style.overflow = '';
  composeEdited = false;
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && !document.getElementById('compose-slideover').classList.contains('translate-x-full')) {
    closeComposeSlideover();
  }
});
</script>
```

- [ ] **Step 3: Verify slideover opens**

Navigate to any page in browser. Open browser console, run:
```javascript
openComposeSlideover({context: 'client', client_id: 1})
```
Verify: Slideover slides in from right with overlay. Escape closes it.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/base.html
git commit -m "feat(compose): add slideover container and JS trigger functions to base.html"
```

---

### Task 4: Add RFI Notify Context Builder

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add `rfi_notify_context()` function**

Add after `timeline_context()` (around line 405):

```python
def rfi_notify_context(conn, bundle_id: int) -> dict:
    """Build token dict for RFI receipt notification."""
    bundle = conn.execute(
        """SELECT b.id, b.client_id, b.title, b.status, b.rfi_uid, b.sent_at,
                  c.name AS client_name, c.cn_number
           FROM client_request_bundles b
           JOIN clients c ON c.id = b.client_id
           WHERE b.id = ?""",
        (bundle_id,),
    ).fetchone()
    if not bundle:
        return {}

    items = conn.execute(
        """SELECT description, received
           FROM client_request_items
           WHERE bundle_id = ?
           ORDER BY sort_order, id""",
        (bundle_id,),
    ).fetchall()

    received = [r["description"] for r in items if r["received"]]
    outstanding = [r["description"] for r in items if not r["received"]]

    from policydb.utils import build_ref_tag
    ref_tag = bundle["rfi_uid"] or ""

    return {
        "rfi_uid": bundle["rfi_uid"] or "",
        "request_title": bundle["title"] or "",
        "client_name": bundle["client_name"] or "",
        "cn_number": bundle["cn_number"] or "",
        "bundle_status": bundle["status"] or "",
        "sent_at": bundle["sent_at"] or "",
        "received_items": received,
        "outstanding_items": outstanding,
        "ref_tag": ref_tag,
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat(compose): add rfi_notify_context() for RFI team notification"
```

---

### Task 5: Add RFI Notify Config + Settings

**Files:**
- Modify: `src/policydb/config.py:394-397`
- Modify: `src/policydb/web/routes/settings.py:109-113,149`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Add config key to `_DEFAULTS`**

In `src/policydb/config.py`, add after the existing email subject keys (around line 490, after `email_subject_request_all`):

```python
"email_subject_rfi_notify": "FYI: {{client_name}} — {{rfi_uid}} Items Received",
```

- [ ] **Step 2: Add to settings `_allowed` set**

In `src/policydb/web/routes/settings.py`, update line 149:

```python
_allowed = {"email_subject_policy", "email_subject_client", "email_subject_followup", "email_subject_request", "email_subject_request_all", "email_subject_rfi_notify"}
```

- [ ] **Step 3: Pass to settings template**

In `settings.py`, add to the template context dict (around line 113):

```python
"email_subject_rfi_notify": cfg.get("email_subject_rfi_notify", ""),
```

- [ ] **Step 4: Add input field to settings.html**

Find the Email Subject Lines section in `settings.html`. After the last email subject field (likely `email_subject_request_all`), add:

```html
<div class="mt-3">
  <label class="block text-xs font-medium text-gray-600 mb-1">RFI Team Notification</label>
  <div class="flex gap-2">
    <input type="text" id="email_subject_rfi_notify" value="{{ email_subject_rfi_notify }}"
           class="flex-1 px-3 py-1.5 border border-gray-200 rounded text-sm font-mono"
           onblur="saveEmailSubject('email_subject_rfi_notify','email_subject_rfi_notify','status-rfi-notify')">
    <span id="status-rfi-notify" class="text-xs text-green-600 self-center hidden">Saved</span>
  </div>
  <div class="flex flex-wrap gap-1 mt-1">
    <button type="button" class="text-[10px] px-1.5 py-0.5 bg-gray-100 rounded hover:bg-gray-200"
            onclick="insertSubjectToken('email_subject_rfi_notify','client_name')">client_name</button>
    <button type="button" class="text-[10px] px-1.5 py-0.5 bg-gray-100 rounded hover:bg-gray-200"
            onclick="insertSubjectToken('email_subject_rfi_notify','rfi_uid')">rfi_uid</button>
  </div>
</div>
```

- [ ] **Step 5: Verify settings page**

Navigate to `/settings`. Scroll to Email Subject Lines. Verify the RFI Team Notification field appears with the default value and token pills. Edit, blur, verify "Saved" appears.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat(compose): add email_subject_rfi_notify config and settings UI"
```

---

## Phase 2 — Convert Trigger Points

### Task 6: Convert Policy Page Compose

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_contacts.html`

- [ ] **Step 1: Replace `<details>` compose with button**

Find the `<details id="compose-panel-policy">` block (around line 56-65) and replace with:

```html
<button type="button"
  onclick="openComposeSlideover({context:'policy', policy_uid:'{{ policy.policy_uid }}', client_id:{{ policy.client_id }}})"
  class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-marsh hover:text-white bg-white hover:bg-marsh border border-marsh/30 hover:border-marsh rounded-lg transition-colors no-print">
  ✉ Compose Email
</button>
```

- [ ] **Step 2: Verify in browser**

Navigate to a policy page → Contacts tab. Click "Compose Email". Verify: slideover opens with policy context, correct recipients, subject pre-filled.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/policies/_tab_contacts.html
git commit -m "feat(compose): convert policy contacts compose to slideover"
```

---

### Task 7: Convert Client Page Compose

**Files:**
- Modify: `src/policydb/web/templates/clients/_tab_contacts.html`

- [ ] **Step 1: Replace `<details>` compose with button**

Find the `<details id="compose-panel-client">` block (around line 11-20) and replace with:

```html
<button type="button"
  onclick="openComposeSlideover({context:'client', client_id:{{ client.id }}})"
  class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-marsh hover:text-white bg-white hover:bg-marsh border border-marsh/30 hover:border-marsh rounded-lg transition-colors no-print">
  ✉ Compose Email
</button>
```

- [ ] **Step 2: Verify in browser**

Navigate to a client page → Contacts tab. Click "Compose Email". Verify: client context, client contacts as recipients.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_tab_contacts.html
git commit -m "feat(compose): convert client contacts compose to slideover"
```

---

### Task 8: Convert Location/Project Header Compose

**Files:**
- Modify: `src/policydb/web/templates/clients/_project_header.html`

- [ ] **Step 1: Replace inline compose with slideover button**

Find the compose button and inline JS panel. Replace with a simple button:

```html
<button type="button"
  onclick="openComposeSlideover({context:'policy', client_id:{{ client.id }}, project_name:'{{ proj.name | e }}'})"
  class="text-xs text-marsh hover:text-marsh-light border border-marsh/30 hover:border-marsh rounded px-2 py-1 transition-colors no-print">
  ✉ Compose
</button>
```

Remove the hidden `.proj-compose` div and the inline `htmx.ajax` call that loaded the old compose panel.

- [ ] **Step 2: Verify in browser**

Navigate to a client page with locations. Click the compose button on a project card header. Verify: location context, project team as CC.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_project_header.html
git commit -m "feat(compose): convert location header compose to slideover"
```

---

### Task 9: Convert Follow-up Row Compose + EP-Triggers

**Files:**
- Modify: `src/policydb/web/templates/followups/_row.html`

- [ ] **Step 1: Replace compose row and ep-trigger buttons**

Find all `ep-trigger` buttons in `_row.html` and replace with `openComposeSlideover()` calls. The pattern for each button:

Old:
```html
<button class="ep-trigger" data-to="..." data-subject="..." data-cc-url="...">✉</button>
```

New:
```html
<button type="button" onclick="openComposeSlideover({context:'policy', policy_uid:'{{ r.policy_uid }}', client_id:{{ r.client_id }}, to_email:'{{ r.contact_email or '' }}'})" class="text-gray-400 hover:text-marsh text-sm no-print">✉</button>
```

Also remove the hidden `compose-row-{row_id}` `<tr>` elements and the `toggleComposeRow()` function.

- [ ] **Step 2: Verify in browser**

Navigate to Action Center → Follow-ups tab. Click email icon on a follow-up row. Verify: slideover opens with policy context, contact_person as To.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/followups/_row.html
git commit -m "feat(compose): convert follow-up row compose/ep-triggers to slideover"
```

---

### Task 10: Convert RFI Bundle + Add Notify Team

**Files:**
- Modify: `src/policydb/web/templates/clients/_request_bundle.html`

- [ ] **Step 1: Replace "Compose Email" button**

Replace the existing `toggleRequestCompose()` button with:

```html
<button type="button"
  onclick="openComposeSlideover({context:'client', client_id:{{ client.id }}, bundle_id:{{ bundle.id }}})"
  class="text-xs text-marsh hover:text-marsh-light border border-marsh/30 hover:border-marsh rounded px-2 py-1 transition-colors no-print">
  ✉ Compose Email
</button>
```

- [ ] **Step 2: Add "Notify Team" button**

Add next to the Compose button, visible only when item_done > 0:

```html
{% if bundle.item_done and bundle.item_done > 0 %}
<button type="button"
  onclick="openComposeSlideover({mode:'rfi_notify', bundle_id:{{ bundle.id }}, client_id:{{ client.id }}})"
  class="text-xs text-blue-600 hover:text-blue-800 border border-blue-300 hover:border-blue-500 rounded px-2 py-1 transition-colors no-print">
  📨 Notify Team
</button>
{% endif %}
```

- [ ] **Step 3: Remove old compose infrastructure**

Remove:
- The hidden `request-compose-panel-{bundle.id}` div
- The `toggleRequestCompose()` JS function

- [ ] **Step 4: Verify in browser**

Navigate to a client page → Requests tab. Verify: "Compose Email" opens slideover. Mark an item as received. Verify: "Notify Team" button appears. Click it. Verify: slideover opens with internal contacts only, received/outstanding items in body.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/clients/_request_bundle.html
git commit -m "feat(compose): convert RFI bundle compose + add Notify Team button"
```

---

### Task 11: Convert Remaining EP-Trigger Templates

**Files (each gets the same conversion pattern):**
- `src/policydb/web/templates/dashboard.html`
- `src/policydb/web/templates/briefing.html`
- `src/policydb/web/templates/briefing_client.html`
- `src/policydb/web/templates/activities/_activity_row.html`
- `src/policydb/web/templates/policies/_opportunities_section.html`
- `src/policydb/web/templates/policies/_opp_row.html`
- `src/policydb/web/templates/policies/_policy_renew_row.html`

- [ ] **Step 1: Search all remaining ep-trigger buttons**

Run: `grep -rn 'ep-trigger' src/policydb/web/templates/ --include='*.html'`

For each file found, convert the `ep-trigger` button to an `openComposeSlideover()` call.

**Conversion pattern:**
```
Old:  class="ep-trigger" data-to="X" data-subject="Y" data-cc-url="Z"
New:  onclick="openComposeSlideover({context:'policy', policy_uid:'UID', client_id:ID, to_email:'X'})"
```

Extract `policy_uid` and `client_id` from the template context available in each file. The `data-to` email becomes `to_email` param. Subject and CC are now handled by the slideover.

- [ ] **Step 2: Convert each file**

Work through each file, replacing `ep-trigger` buttons. Key context variables per file:

| File | `policy_uid` source | `client_id` source |
|------|--------------------|--------------------|
| `dashboard.html` | `r.policy_uid` | `r.client_id` |
| `briefing.html` | `r.policy_uid` | `r.client_id` |
| `briefing_client.html` | `p.policy_uid` | `client.id` |
| `_activity_row.html` | `a.policy_uid` | `a.client_id` |
| `_opportunities_section.html` | `o.policy_uid` | `o.client_id` |
| `_opp_row.html` | `opp.policy_uid` | `opp.client_id` |
| `_policy_renew_row.html` | `r.policy_uid` | `r.client_id` |

- [ ] **Step 3: Verify no ep-trigger buttons remain**

Run: `grep -rn 'ep-trigger' src/policydb/web/templates/ --include='*.html'`

Expected: Zero results (or only in files pending removal like `_compose_panel.html`).

- [ ] **Step 4: Verify key pages in browser**

Test: dashboard, renewal pipeline, opportunities page, briefing page. Click email buttons on each. Verify slideover opens with correct context.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/
git commit -m "feat(compose): convert all remaining ep-trigger buttons to slideover"
```

---

## Phase 3 — Remove Old Infrastructure

### Task 12: Remove Old Compose Templates + Endpoints

**Files:**
- Delete: `src/policydb/web/templates/templates/_compose_panel.html`
- Delete: `src/policydb/web/templates/templates/_compose_rendered.html`
- Delete: `src/policydb/web/templates/clients/_request_compose.html`
- Modify: `src/policydb/web/routes/templates.py` — remove compose/render endpoints
- Modify: `src/policydb/web/routes/clients.py` — remove request compose endpoints
- Modify: `src/policydb/web/routes/policies.py` — remove team-cc endpoint
- Modify: `src/policydb/web/templates/base.html` — remove ep-trigger IIFE

- [ ] **Step 1: Delete old compose templates**

```bash
rm src/policydb/web/templates/templates/_compose_panel.html
rm src/policydb/web/templates/templates/_compose_rendered.html
rm src/policydb/web/templates/clients/_request_compose.html
```

- [ ] **Step 2: Remove old endpoints from templates.py**

In `src/policydb/web/routes/templates.py`:
- Remove `_load_contacts()` function (lines 165–226)
- Remove `GET /templates/render` endpoint (lines 231–274)
- Remove `GET /templates/compose` endpoint (lines 277–365)

- [ ] **Step 3: Remove request compose endpoints from clients.py**

In `src/policydb/web/routes/clients.py`:
- Remove `GET /clients/{id}/requests/{bundle_id}/compose` endpoint
- Remove `GET /clients/{id}/requests/compose-all` endpoint
- Remove `GET /clients/{id}/projects/{name}/email-team` endpoint
- Remove `GET /clients/{id}/team-cc` endpoint

- [ ] **Step 4: Remove team-cc endpoint from policies.py**

In `src/policydb/web/routes/policies.py`:
- Remove `GET /policies/{uid}/team-cc` endpoint

- [ ] **Step 5: Remove `team_cc_json` computation from routes**

Search for `team_cc_json` in `policies.py` and `clients.py`. Remove the computation and the template variable passing.

- [ ] **Step 6: Remove ep-trigger IIFE from base.html**

In `src/policydb/web/templates/base.html`:
- Remove the ep-trigger IIFE (lines ~627–767)
- Remove the `#email-popover` div (lines ~621–624)
- Keep `buildMailto()` — it was already extracted to standalone in Task 3

- [ ] **Step 7: Verify server starts and no 500 errors**

Restart server. Navigate to: dashboard, a policy page, a client page, follow-ups, settings. Verify no 500 errors or template-not-found errors.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(compose): remove old compose panels, ep-trigger system, team-cc endpoints"
```

---

## Phase 4 — Template Context Migration

### Task 13: Create Template Context Migration

**Files:**
- Create: `src/policydb/migrations/071_simplify_template_contexts.sql`
- Modify: `src/policydb/db.py`

- [ ] **Step 1: Create migration SQL**

Create `src/policydb/migrations/071_simplify_template_contexts.sql`:

```sql
-- Consolidate email template contexts from 7 to 2 (policy + client)
UPDATE email_templates SET context = 'policy' WHERE context IN ('location', 'followup', 'timeline');
UPDATE email_templates SET context = 'client' WHERE context IN ('general', 'meeting');
```

- [ ] **Step 2: Wire migration into `init_db()`**

In `src/policydb/db.py`:

Add `71` to `_KNOWN_MIGRATIONS` set.

Add migration block after the version 70 block:

```python
if 71 not in applied:
    sql = (_MIGRATIONS_DIR / "071_simplify_template_contexts.sql").read_text()
    conn.executescript(sql)
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (71, "Simplify template contexts to policy+client"),
    )
    conn.commit()
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/migrations/071_simplify_template_contexts.sql src/policydb/db.py
git commit -m "feat(compose): add migration 071 to consolidate template contexts"
```

---

### Task 14: Merge Token Groups + Update Template Builder

**Files:**
- Modify: `src/policydb/email_templates.py:464-593`
- Modify: `src/policydb/web/routes/templates.py:30-35`
- Modify: `src/policydb/web/templates/templates/_template_form.html`

- [ ] **Step 1: Merge token groups in `CONTEXT_TOKEN_GROUPS`**

In `src/policydb/email_templates.py`, restructure `CONTEXT_TOKEN_GROUPS` (lines 464–587) to only have `policy` and `client` keys. Merge collapsed context tokens into the surviving groups:

**Policy gains:** Location group, Followup group, Timeline group
**Client gains:** Meeting group

The policy context should have groups like:
```python
"policy": [
    ("Policy", [...existing...]),
    ("Dates", [...existing...]),
    ("Financials", [...existing...]),
    ("Client", [...existing...]),
    ("Contact", [...existing...]),
    ("Location", [
        ("location_name", "Location Name"),
        ("location_description", "Location Description"),
        ("policy_count", "Policy Count"),
        ("total_premium", "Total Premium"),
        ("team_names", "Team Names"),
        ("team_emails", "Team Emails"),
        ("placement_colleagues", "Placement Colleagues"),
        ("placement_emails", "Placement Emails"),
    ]),
    ("Follow-up", [
        ("subject", "Subject"),
        ("contact_person", "Contact Person"),
        ("duration_hours", "Duration"),
        ("disposition", "Disposition"),
        ("thread_ref", "Thread Ref"),
    ]),
    ("Timeline", [
        ("days_to_expiry", "Days to Expiry"),
        ("drift_days", "Drift Days"),
        ("blocking_reason", "Blocking Reason"),
        ("current_status", "Current Status"),
        ("milestones_complete", "Milestones Complete"),
        ("milestones_remaining", "Milestones Remaining"),
    ]),
    ("Tracking", [...existing...]),
]
```

And rebuild `CONTEXT_TOKENS` from the updated groups.

- [ ] **Step 2: Update `_CONTEXT_LABELS` in templates.py**

In `src/policydb/web/routes/templates.py`, update lines 30–35:

```python
_CONTEXT_LABELS = {
    "policy": "Policy",
    "client": "Client",
}
```

- [ ] **Step 3: Update template form to pill buttons**

In `src/policydb/web/templates/templates/_template_form.html`, replace the context `<select>` dropdown with pill buttons:

```html
<div class="mb-3">
  <label class="block text-xs font-medium text-gray-600 mb-1">Template Type</label>
  <div class="flex gap-2">
    <label class="cursor-pointer">
      <input type="radio" name="context" value="policy" class="peer sr-only"
             {% if template.context == 'policy' or not template %}checked{% endif %}>
      <span class="px-3 py-1.5 text-sm rounded-full border border-gray-200 peer-checked:bg-marsh peer-checked:text-white peer-checked:border-marsh transition-colors">
        Policy emails
      </span>
    </label>
    <label class="cursor-pointer">
      <input type="radio" name="context" value="client" class="peer sr-only"
             {% if template.context == 'client' %}checked{% endif %}>
      <span class="px-3 py-1.5 text-sm rounded-full border border-gray-200 peer-checked:bg-marsh peer-checked:text-white peer-checked:border-marsh transition-colors">
        Client emails
      </span>
    </label>
  </div>
</div>
```

- [ ] **Step 4: Update template CRUD validation**

In `templates.py`, update any validation that checks `context in (...)` to only accept `"policy"` and `"client"`.

- [ ] **Step 5: Verify template builder**

Navigate to `/templates`. Create a new template. Verify: only 2 pill buttons (Policy / Client). Select Policy — verify all token groups appear including Location, Follow-up, Timeline. Select Client — verify Meeting tokens appear.

Edit an existing template that was previously `location` or `followup` context — verify it now shows as `policy`.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/email_templates.py src/policydb/web/routes/templates.py src/policydb/web/templates/templates/_template_form.html
git commit -m "feat(compose): consolidate template contexts to policy+client, merge token groups"
```

---

## Phase 5 — Final QA + Cleanup

### Task 15: Full QA Pass

- [ ] **Step 1: Verify all core compose flows**

Test each trigger point in the browser:
1. Policy page → Contacts tab → Compose Email → slideover with policy context
2. Client page → Contacts tab → Compose Email → slideover with client context
3. Follow-up row → ✉ icon → slideover with policy context + contact as To
4. Location header → Compose → slideover with location context + project team CC
5. RFI bundle → Compose Email → slideover with client context
6. RFI bundle → Notify Team → slideover with internal recipients + received items

- [ ] **Step 2: Verify recipient picker**

For each trigger: verify role badges display correctly, pre-checked contacts match spec table, preview line updates when toggling checkboxes, "Add recipient" search works.

- [ ] **Step 3: Verify template rendering**

Open slideover → select a template from dropdown → verify subject + body update with rendered tokens. Select "No template" → verify fields reset to quick email defaults.

- [ ] **Step 4: Verify Open in Mail + Copy All**

Click "Open in Mail" → verify Outlook opens with correct To/CC/Subject/Body.
Click "Copy All" → paste into a text editor → verify subject + body present.

- [ ] **Step 5: Verify regression pages**

Check dashboard, renewal pipeline, opportunities, briefing, activity rows — all email buttons should open the slideover (no broken `ep-trigger` buttons, no 404s).

- [ ] **Step 6: Verify edge cases**

- No contacts: trigger compose for a client with zero contacts → see empty picker with search
- Narrow viewport: resize browser to < 640px → slideover should be full-width
- Escape key: open slideover → press Escape → closes
- Edit + close: type in body → click outside → confirmation dialog appears

- [ ] **Step 7: Verify settings**

Navigate to `/settings` → Email Subject Lines → verify `email_subject_rfi_notify` field with token pills.

- [ ] **Step 8: Verify migration**

Navigate to `/templates` → verify all existing templates have either "policy" or "client" context. No templates with old context values.

- [ ] **Step 9: Commit any fixes**

If QA found issues, fix them and commit:

```bash
git add -A
git commit -m "fix(compose): QA fixes from full verification pass"
```

---

### Task 16: Final Commit + Branch Wrap-Up

- [ ] **Step 1: Verify clean state**

```bash
git status
git log --oneline -15
```

Verify: all changes committed, no untracked files.

- [ ] **Step 2: Update CLAUDE.md if needed**

If any new lessons learned or patterns emerged during implementation, add them to the CLAUDE.md Lessons Learned section.

- [ ] **Step 3: Ready for merge/PR**

Use `superpowers:finishing-a-development-branch` skill to decide: merge to main, create PR, or keep working.
