# Ref Tree Lookup, COR Auto-Default & Inbox Contact Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ref tree lookup page that resolves any UID into a hierarchical tree of related references, auto-default the COR toggle based on configurable triggers, and enable `@` contact tagging in inbox capture.

**Architecture:** Three independent features sharing no dependencies. Ref tree is a new query function + route + template. COR auto-default is config + JS wiring on 3 existing forms. Inbox contact tagging is a migration + endpoint update + JS autocomplete.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-20-ref-tree-and-inbox-enhancements-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/web/routes/ref_lookup.py` | Ref lookup route + resolve_ref_tree function |
| Create | `src/policydb/web/templates/ref_lookup.html` | Ref tree page |
| Create | `src/policydb/migrations/065_inbox_contact_id.sql` | Add contact_id to inbox |
| Modify | `src/policydb/db.py` | Register migration 065 |
| Modify | `src/policydb/web/app.py` | Register ref_lookup router |
| Modify | `src/policydb/web/templates/base.html` | Add Ref Lookup to Tools dropdown, `@` autocomplete JS, capture form hidden field |
| Modify | `src/policydb/web/routes/inbox.py` | Contact search endpoint, update capture to accept contact_id |
| Modify | `src/policydb/web/templates/inbox.html` | `@` autocomplete on inline add, contact display, capture hidden field |
| Modify | `src/policydb/web/routes/dashboard.py` | Search banner for UID patterns |
| Modify | `src/policydb/config.py` | Add `cor_auto_triggers` to defaults |
| Modify | `src/policydb/web/routes/settings.py` | Add `cor_auto_triggers` to EDITABLE_LISTS |
| Modify | `src/policydb/web/templates/policies/edit.html` | COR auto-trigger JS |
| Modify | `src/policydb/web/templates/clients/detail.html` | COR auto-trigger JS |

---

### Task 1: Ref Tree Core Function + Route

**Files:**
- Create: `src/policydb/web/routes/ref_lookup.py`
- Modify: `src/policydb/web/app.py`

- [ ] **Step 1: Create ref_lookup route module with resolve_ref_tree**

Create `src/policydb/web/routes/ref_lookup.py`:

```python
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
```

- [ ] **Step 2: Register router in app.py**

In `src/policydb/web/app.py`, add to the imports and include:

```python
from policydb.web.routes import ..., ref_lookup  # add to existing import
app.include_router(ref_lookup.router)
```

- [ ] **Step 3: Add ref-lookup to Tools dropdown in base.html**

In `base.html`, find the Tools dropdown and add `'ref-lookup'` to the active condition and add the nav link:

Add `'ref-lookup'` to the Tools active check: `{% if active in ['briefing','reconcile','templates','settings','ref-lookup'] %}`

Add the link inside the Tools dropdown panel:
```html
<a href="/ref-lookup" class="block px-4 py-2 text-sm text-white/90 hover:bg-marsh-light {% if active == 'ref-lookup' %}bg-marsh-light{% endif %}">Ref Lookup</a>
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/ref_lookup.py src/policydb/web/app.py src/policydb/web/templates/base.html
git commit -m "feat: ref tree lookup core function and route"
```

---

### Task 2: Ref Tree Template + Search Banner

**Files:**
- Create: `src/policydb/web/templates/ref_lookup.html`
- Modify: `src/policydb/web/routes/dashboard.py`

- [ ] **Step 1: Create ref_lookup.html template**

```html
{% extends "base.html" %}
{% block title %}Ref Lookup — Coverage{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto">
  <div class="flex items-center gap-3 mb-4">
    <h1 class="text-lg font-bold text-gray-900">Ref Lookup</h1>
    <span class="text-xs text-gray-400">Paste any UID to see all related references</span>
  </div>

  <form action="/ref-lookup" method="get" class="mb-6">
    <div class="flex gap-2">
      <input name="q" type="text" value="{{ q }}" placeholder="CN122333627, POL-2025-0441, COR-7, INB-42, A-108..." autofocus
        class="flex-1 text-sm border border-gray-200 rounded-lg px-4 py-2.5 focus:border-marsh focus:outline-none focus:ring-1 focus:ring-marsh">
      <button type="submit" class="btn-primary px-5 py-2.5 text-sm">Lookup</button>
    </div>
  </form>

  {% if q and not tree %}
  <div class="card p-6 text-center text-gray-400 text-sm">
    No matching reference found for "{{ q }}"
  </div>
  {% endif %}

  {% if tree %}
  <div class="card overflow-hidden">
    {# Client root #}
    <div class="px-4 py-3 bg-gray-50 border-b border-gray-100 flex items-center gap-2">
      <button onclick="navigator.clipboard.writeText('{{ tree.client.uid }}');if(typeof showToast==='function')showToast('Copied',true)"
        class="bg-blue-100 text-blue-700 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-blue-200 {% if tree.highlight == tree.client.uid %}ring-2 ring-amber-400{% endif %}">{{ tree.client.uid }}</button>
      <a href="/clients/{{ tree.client.id }}" class="text-sm font-medium text-marsh hover:underline">{{ tree.client.name }}</a>
    </div>

    <div class="px-4 py-2 space-y-1">
      {# Policies #}
      {% for p in tree.policies %}
      <div class="ml-4 border-l-2 border-gray-200 pl-3 py-1">
        <div class="flex items-center gap-2">
          <span class="text-gray-300 text-xs">├</span>
          <button onclick="navigator.clipboard.writeText('{{ p.uid }}');if(typeof showToast==='function')showToast('Copied',true)"
            class="bg-blue-100 text-blue-700 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-blue-200 {% if p.uid in tree.highlight %}ring-2 ring-amber-400{% endif %}">{{ p.uid }}</button>
          <a href="/policies/{{ p.uid }}/edit" class="text-xs text-gray-700 hover:text-marsh">{{ p.type }}{% if p.carrier %} — {{ p.carrier }}{% endif %}</a>
        </div>

        {# COR threads under policy #}
        {% for t in p.threads %}
        <div class="ml-6 mt-1">
          <div class="flex items-center gap-2">
            <span class="text-gray-300 text-xs">└</span>
            <button onclick="navigator.clipboard.writeText('{{ t.uid }}');if(typeof showToast==='function')showToast('Copied',true)"
              class="bg-indigo-100 text-indigo-700 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-indigo-200 {% if t.uid in tree.highlight %}ring-2 ring-amber-400{% endif %}">{{ t.uid }}</button>
            <span class="text-xs text-gray-400">({{ t.activity_count }} activit{{ 'y' if t.activity_count == 1 else 'ies' }})</span>
          </div>
          {% for a in t.activities %}
          <div class="ml-8 flex items-center gap-2 mt-0.5">
            <span class="text-gray-200 text-[10px]">{{ '└' if loop.last else '├' }}</span>
            <button onclick="navigator.clipboard.writeText('A-{{ a.id }}');if(typeof showToast==='function')showToast('Copied',true)"
              class="bg-gray-100 text-gray-500 text-[10px] font-mono px-1.5 py-0.5 rounded cursor-pointer hover:bg-gray-200 {% if ('A-' ~ a.id|string) == tree.highlight %}ring-2 ring-amber-400{% endif %}">A-{{ a.id }}</button>
            <span class="text-xs text-gray-600 truncate">{{ a.subject or '—' }}</span>
            <span class="text-[10px] text-gray-300">{{ a.activity_date }}</span>
          </div>
          {% endfor %}
        </div>
        {% endfor %}

        {# Standalone activities (collapsed) #}
        {% if p.standalone_activities %}
        <details class="ml-6 mt-1">
          <summary class="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">{{ p.standalone_activities | length }} standalone activit{{ 'y' if p.standalone_activities | length == 1 else 'ies' }}</summary>
          {% for a in p.standalone_activities %}
          <div class="ml-2 flex items-center gap-2 mt-0.5">
            <button onclick="navigator.clipboard.writeText('A-{{ a.id }}');if(typeof showToast==='function')showToast('Copied',true)"
              class="bg-gray-100 text-gray-500 text-[10px] font-mono px-1.5 py-0.5 rounded cursor-pointer hover:bg-gray-200">A-{{ a.id }}</button>
            <span class="text-xs text-gray-600 truncate">{{ a.subject or '—' }}</span>
            <span class="text-[10px] text-gray-300">{{ a.activity_date }}</span>
          </div>
          {% endfor %}
        </details>
        {% endif %}
      </div>
      {% endfor %}

      {# Client-level COR threads (no policy) #}
      {% for t in tree.client_threads %}
      <div class="ml-4 border-l-2 border-indigo-100 pl-3 py-1">
        <div class="flex items-center gap-2">
          <span class="text-gray-300 text-xs">├</span>
          <button onclick="navigator.clipboard.writeText('{{ t.uid }}');if(typeof showToast==='function')showToast('Copied',true)"
            class="bg-indigo-100 text-indigo-700 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-indigo-200 {% if t.uid in tree.highlight %}ring-2 ring-amber-400{% endif %}">{{ t.uid }}</button>
          <span class="text-xs text-gray-400">({{ t.activity_count }} activit{{ 'y' if t.activity_count == 1 else 'ies' }}, no policy)</span>
        </div>
        {% for a in t.activities %}
        <div class="ml-8 flex items-center gap-2 mt-0.5">
          <span class="text-gray-200 text-[10px]">{{ '└' if loop.last else '├' }}</span>
          <button onclick="navigator.clipboard.writeText('A-{{ a.id }}');if(typeof showToast==='function')showToast('Copied',true)"
            class="bg-gray-100 text-gray-500 text-[10px] font-mono px-1.5 py-0.5 rounded cursor-pointer hover:bg-gray-200">A-{{ a.id }}</button>
          <span class="text-xs text-gray-600 truncate">{{ a.subject or '—' }}</span>
          <span class="text-[10px] text-gray-300">{{ a.activity_date }}</span>
        </div>
        {% endfor %}
      </div>
      {% endfor %}

      {# RFI bundles #}
      {% for r in tree.rfis %}
      <div class="ml-4 pl-3 py-1 flex items-center gap-2">
        <span class="text-gray-300 text-xs">├</span>
        <button onclick="navigator.clipboard.writeText('{{ r.rfi_uid }}');if(typeof showToast==='function')showToast('Copied',true)"
          class="bg-green-100 text-green-700 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-green-200 {% if r.rfi_uid in tree.highlight %}ring-2 ring-amber-400{% endif %}">{{ r.rfi_uid }}</button>
        <span class="text-xs text-gray-600">{{ r.title }} — {{ r.received_count }}/{{ r.item_count }} received</span>
        <span class="text-[10px] px-1.5 py-0.5 rounded {% if r.status == 'complete' %}bg-green-50 text-green-600{% elif r.status == 'sent' %}bg-blue-50 text-blue-600{% else %}bg-amber-50 text-amber-600{% endif %}">{{ r.status }}</span>
      </div>
      {% endfor %}

      {# Inbox items #}
      {% for i in tree.inbox_items %}
      <div class="ml-4 pl-3 py-1 flex items-center gap-2">
        <span class="text-gray-300 text-xs">{{ '└' if loop.last else '├' }}</span>
        <button onclick="navigator.clipboard.writeText('{{ i.inbox_uid }}');if(typeof showToast==='function')showToast('Copied',true)"
          class="bg-indigo-50 text-indigo-600 text-[10px] font-bold font-mono px-2 py-0.5 rounded cursor-pointer hover:bg-indigo-100 {% if i.inbox_uid in tree.highlight %}ring-2 ring-amber-400{% endif %}">{{ i.inbox_uid }}</button>
        <span class="text-xs text-gray-600 truncate">{{ i.content }}</span>
        {% if i.status == 'processed' and i.activity_subject %}
        <span class="text-[10px] text-green-600">→ {{ i.activity_subject }}</span>
        {% elif i.status == 'processed' %}
        <span class="text-[10px] text-gray-400">Dismissed</span>
        {% else %}
        <span class="text-[10px] text-amber-600">Pending</span>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 2: Add search banner for UID patterns in dashboard.py**

In `src/policydb/web/routes/dashboard.py`, in the `search` function, after computing results and before returning the template, detect UID patterns and pass a flag:

```python
# Detect UID pattern for ref tree banner
uid_pattern = bool(re.match(
    r'^(CN?\d{5,}|POL-|COR-\d+|INB-\d+|A-\d+|CN?\d+-RFI\d+)',
    q.strip(), re.IGNORECASE
)) if q.strip() else False
```

Add `"uid_detected": uid_pattern` to the template context.

In `src/policydb/web/templates/search.html`, add at the top of results:

```html
{% if uid_detected %}
<div class="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2 mb-4 text-sm text-blue-700">
  This looks like a reference tag — <a href="/ref-lookup?q={{ q }}" class="font-medium underline">View full ref tree</a>
</div>
{% endif %}
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/ref_lookup.html src/policydb/web/routes/dashboard.py src/policydb/web/templates/search.html
git commit -m "feat: ref tree lookup page and search banner"
```

---

### Task 3: COR Auto-Default

**Files:**
- Modify: `src/policydb/config.py`
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/inbox.html`
- Modify: `src/policydb/web/templates/policies/edit.html`
- Modify: `src/policydb/web/templates/clients/detail.html`

- [ ] **Step 1: Add config default and settings list**

In `src/policydb/config.py`, add to `_DEFAULTS`:
```python
"cor_auto_triggers": ["Email", "Left VM", "Sent Email", "Awaiting Response"],
```

In `src/policydb/web/routes/settings.py`, add to `EDITABLE_LISTS`:
```python
"cor_auto_triggers": "COR Auto-Triggers",
```

- [ ] **Step 2: Wire COR auto-trigger JS on inbox process form**

In `src/policydb/web/templates/inbox.html`, in the `<script>` block at the bottom, add COR auto-trigger logic. The process form has activity type pill radio buttons and a `start_correspondence` checkbox.

Add a data attribute to the process form's activity type pills container and wire the auto-check:

```javascript
// COR auto-trigger for inbox process forms
var corTriggers = {{ cfg.get("cor_auto_triggers", []) | tojson }};
document.addEventListener('change', function(e) {
  if (e.target.name === 'activity_type') {
    var form = e.target.closest('form');
    if (!form) return;
    var corBox = form.querySelector('[name="start_correspondence"]');
    if (corBox && corTriggers.includes(e.target.value)) {
      corBox.checked = true;
    }
  }
});
```

Pass `cor_auto_triggers` in the inbox page context by adding to `inbox_page()`:
```python
"cor_auto_triggers": cfg.get("cor_auto_triggers", []),
```

- [ ] **Step 3: Wire COR auto-trigger on policy edit quick log**

In `src/policydb/web/templates/policies/edit.html`, find the quick log form's activity type pills. Add similar JS that checks `start_correspondence` when the selected type matches a trigger.

The template already receives config values. Add to the script section:

```javascript
var corTriggers = {{ cor_auto_triggers | tojson }};
```

And wire the change handler on the activity type radio buttons.

Pass `cor_auto_triggers` in the policy edit context.

- [ ] **Step 4: Wire COR auto-trigger on client detail quick log**

Same pattern in `src/policydb/web/templates/clients/detail.html` — find the quick log form, add the auto-trigger JS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py src/policydb/web/templates/inbox.html src/policydb/web/templates/policies/edit.html src/policydb/web/templates/clients/detail.html
git commit -m "feat: COR auto-default from configurable triggers"
```

---

### Task 4: Inbox Contact Tagging — Migration + Endpoint

**Files:**
- Create: `src/policydb/migrations/065_inbox_contact_id.sql`
- Modify: `src/policydb/db.py`
- Modify: `src/policydb/web/routes/inbox.py`

- [ ] **Step 1: Create migration**

```sql
-- 065_inbox_contact_id.sql
ALTER TABLE inbox ADD COLUMN contact_id INTEGER REFERENCES contacts(id);
```

Register in `db.py`: add 65 to `_KNOWN_MIGRATIONS`, add if-block.

- [ ] **Step 2: Add contact search endpoint to inbox.py**

```python
@router.get("/inbox/contacts/search")
def inbox_contact_search(q: str = "", conn=Depends(get_db)):
    """Search contacts for @ autocomplete."""
    if len(q) < 2:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT id, name, organization FROM contacts
        WHERE name LIKE ? ORDER BY name LIMIT 15
    """, (f"%{q}%",)).fetchall()
    return JSONResponse([{"id": r["id"], "name": r["name"], "org": r["organization"] or ""} for r in rows])
```

- [ ] **Step 3: Update inbox_capture to accept contact_id**

Update the `inbox_capture` function signature to accept `contact_id: int = Form(0)` and include it in the INSERT:

```python
def inbox_capture(content: str = Form(...), client_id: int = Form(0), contact_id: int = Form(0), conn=Depends(get_db)):
    conn.execute(
        "INSERT INTO inbox (content, client_id, contact_id, inbox_uid) VALUES (?, ?, ?, '')",
        (content.strip(), client_id or None, contact_id or None),
    )
```

- [ ] **Step 4: Update inbox_page query to include contact name**

Update the pending query to join contacts:

```sql
SELECT i.*, c.name AS client_name, ct.name AS contact_name
FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
LEFT JOIN contacts ct ON i.contact_id = ct.id
WHERE i.status = 'pending'
ORDER BY i.created_at DESC
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/migrations/065_inbox_contact_id.sql src/policydb/db.py src/policydb/web/routes/inbox.py
git commit -m "feat: inbox contact_id column, contact search endpoint"
```

---

### Task 5: Inbox Contact Tagging — `@` Autocomplete UI

**Files:**
- Modify: `src/policydb/web/templates/base.html`
- Modify: `src/policydb/web/templates/inbox.html`

- [ ] **Step 1: Add hidden contact_id field to capture forms**

In `base.html`, add to the nav sub-bar capture form:
```html
<input type="hidden" name="contact_id" id="capture-contact-id" value="0">
```

In `inbox.html`, add to the inline add-item form:
```html
<input type="hidden" name="contact_id" id="inbox-add-contact-id" value="0">
```

- [ ] **Step 2: Add `@` autocomplete JS to base.html**

Add a reusable `@` autocomplete function in a `<script>` block in `base.html`:

```javascript
(function() {
  function initAtComplete(input, hiddenId) {
    var dropdown = null;
    var atStart = -1;

    function closeDropdown() {
      if (dropdown) { dropdown.remove(); dropdown = null; }
      atStart = -1;
    }

    input.addEventListener('input', function() {
      var val = input.value;
      var cursor = input.selectionStart || val.length;
      // Find @ before cursor
      var lastAt = val.lastIndexOf('@', cursor - 1);
      if (lastAt < 0) { closeDropdown(); return; }
      var query = val.substring(lastAt + 1, cursor);
      if (query.length < 2) { if (query.length === 0 && lastAt >= 0) { atStart = lastAt; } return; }
      atStart = lastAt;

      fetch('/inbox/contacts/search?q=' + encodeURIComponent(query))
        .then(function(r) { return r.json(); })
        .then(function(results) {
          closeDropdown();
          if (!results.length) return;
          dropdown = document.createElement('div');
          dropdown.className = 'absolute z-50 bg-white border border-gray-200 rounded-lg shadow-lg mt-1 max-h-48 overflow-y-auto';
          dropdown.style.minWidth = '200px';
          results.forEach(function(c) {
            var item = document.createElement('div');
            item.className = 'px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50';
            item.textContent = c.name + (c.org ? ' (' + c.org + ')' : '');
            item.addEventListener('mousedown', function(e) {
              e.preventDefault();
              var before = input.value.substring(0, atStart);
              var after = input.value.substring(cursor);
              input.value = before + c.name + ' ' + after;
              document.getElementById(hiddenId).value = c.id;
              closeDropdown();
              input.focus();
            });
            dropdown.appendChild(item);
          });
          var rect = input.getBoundingClientRect();
          dropdown.style.position = 'fixed';
          dropdown.style.top = (rect.bottom + 2) + 'px';
          dropdown.style.left = rect.left + 'px';
          document.body.appendChild(dropdown);
        });
    });

    input.addEventListener('blur', function() { setTimeout(closeDropdown, 200); });
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') closeDropdown();
    });
    // Reset hidden field on form reset
    input.closest('form').addEventListener('reset', function() {
      document.getElementById(hiddenId).value = '0';
    });
  }

  // Init on capture input
  var captureInput = document.getElementById('capture-input');
  if (captureInput) initAtComplete(captureInput, 'capture-contact-id');

  // Init on inbox add input (if present)
  var inboxAdd = document.getElementById('inbox-add-input');
  if (inboxAdd) initAtComplete(inboxAdd, 'inbox-add-contact-id');
})();
```

Add `id="inbox-add-input"` to the inbox page inline add input.

- [ ] **Step 3: Show tagged contact on inbox items**

In `inbox.html`, in the pending item display, show the contact name if tagged:

```html
{% if item.contact_name %}
<span class="text-xs text-gray-500">· {{ item.contact_name }}</span>
{% endif %}
```

- [ ] **Step 4: Pre-fill contact on process/schedule forms**

In the process and schedule forms, if the item has a `contact_name`, show it as context. The activity log's `contact_person` field can be pre-filled from the tagged contact name.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/base.html src/policydb/web/templates/inbox.html
git commit -m "feat: @ contact autocomplete in inbox capture"
```

---

### Task 6: Manual Test + Fixes

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

- [ ] **Step 2: Manual test checklist**

1. **Ref Lookup:** Navigate to /ref-lookup. Type a CN number → see full tree. Type COR-N → see tree highlighted at that thread. Type INB-N → see tree with inbox item highlighted. Type POL-UID → see tree highlighted at policy.
2. **Search banner:** Search for "COR-7" in search bar → banner appears with "View full ref tree" link.
3. **Copy pills:** Click any UID pill in the tree → copied to clipboard, toast confirms.
4. **COR auto-trigger:** Go to policy edit, open quick log. Select "Email" type → COR toggle auto-checks. Select "Note" → COR stays unchecked. Uncheck COR, switch to "Left VM" → re-checks.
5. **COR settings:** Go to /settings → "COR Auto-Triggers" list card visible. Add/remove items.
6. **@ autocomplete:** In nav capture bar, type "Got call from @Jo" → dropdown shows matching contacts. Select one → name appears in text, hidden field set. Press Enter → captured with contact_id.
7. **Inbox contact display:** Go to /inbox → tagged items show contact name.
8. **Inbox inline add:** Type in the add-item input on /inbox with @ mention → works same as nav bar.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: ref tree and inbox enhancement adjustments"
```
