# Contacts Management Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up deprecated contact edit patterns, add a contact detail page, improve merge UI with side-by-side comparison, add bulk actions, and add contact email tokens.

**Architecture:** Remove old-style edit form templates and endpoints. Create a new contact detail page at `/contacts/{contact_id}`. Add expertise pills to matrix rows. Unify JS controllers. Add checkboxes + bulk action bar. Improve merge with comparison panel. Add placement colleague tokens to email system.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-19-contacts-overhaul-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Delete | `src/policydb/web/templates/contacts/_edit_row.html` | Deprecated edit form |
| Delete | `src/policydb/web/templates/contacts/_internal_edit_row.html` | Deprecated edit form |
| Delete | `src/policydb/web/templates/contacts/_client_contact_edit_row.html` | Deprecated edit form |
| Create | `src/policydb/web/templates/contacts/detail.html` | Contact detail page |
| Modify | `src/policydb/web/routes/contacts.py` | Remove deprecated endpoints, add detail page, bulk actions, follow-up counts |
| Modify | `src/policydb/web/templates/contacts/list.html` | Checkboxes, bulk bar, merge comparison, expandable indicators, unify JS |
| Modify | `src/policydb/web/templates/contacts/_placement_matrix_row.html` | Expertise pills, follow-up badge, checkbox, detail link |
| Modify | `src/policydb/web/templates/contacts/_internal_matrix_row.html` | Same |
| Modify | `src/policydb/web/templates/contacts/_client_matrix_row.html` | Same |
| Modify | `src/policydb/email_templates.py` | Placement colleague tokens |

---

### Task 1: Remove Deprecated Edit Templates + Endpoints

**Files:**
- Delete: `src/policydb/web/templates/contacts/_edit_row.html`
- Delete: `src/policydb/web/templates/contacts/_internal_edit_row.html`
- Delete: `src/policydb/web/templates/contacts/_client_contact_edit_row.html`
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Delete the three deprecated edit template files**

```bash
rm src/policydb/web/templates/contacts/_edit_row.html
rm src/policydb/web/templates/contacts/_internal_edit_row.html
rm src/policydb/web/templates/contacts/_client_contact_edit_row.html
```

- [ ] **Step 2: Remove deprecated edit endpoints from contacts.py**

Find and remove these endpoint functions:
- `GET /{name}/edit` → `contact_edit_form`
- `POST /{name}/edit` → `contact_edit_post`
- `GET /internal/{name}/edit` → `internal_contact_edit_form`
- `POST /internal/{name}/edit` → `internal_contact_edit_post`
- `GET /client/{name}/edit` → `client_contact_edit_form`
- `POST /client/{name}/edit` → `client_contact_edit_post`

Search for these function names and remove entire functions. The contenteditable matrix rows handle editing directly via PATCH cell endpoints.

- [ ] **Step 3: Run tests to verify nothing breaks**

Run: `pytest tests/ -v`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated contact edit form templates and endpoints"
```

---

### Task 2: Expertise Pills + Follow-Up Badges on Matrix Rows

**Files:**
- Modify: `src/policydb/web/templates/contacts/_placement_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_internal_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_client_matrix_row.html`
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Add expertise pills to matrix rows**

In each of the three matrix row templates, after the contact name cell content, add:

```html
{% if c.expertise_lines or c.expertise_industries %}
<div class="flex flex-wrap gap-0.5 mt-0.5">
  {% for t in c.expertise_lines %}<span class="text-[8px] px-1 py-0 rounded bg-blue-50 text-blue-500">{{ t }}</span>{% endfor %}
  {% for t in c.expertise_industries %}<span class="text-[8px] px-1 py-0 rounded bg-green-50 text-green-500">{{ t }}</span>{% endfor %}
</div>
{% endif %}
```

- [ ] **Step 2: Add follow-up badge to matrix rows**

After the name, add a follow-up overdue indicator:

```html
{% if c.open_followup_count %}
<span class="text-[9px] bg-red-100 text-red-600 px-1 py-0.5 rounded-full ml-1" title="{{ c.open_followup_count }} pending">{{ c.open_followup_count }}</span>
{% endif %}
```

- [ ] **Step 3: Add detail page link to matrix rows**

Add a small link icon after the name that navigates to the contact detail page:

```html
{% if c.id %}
<a href="/contacts/{{ c.id }}" class="text-gray-300 hover:text-marsh text-xs ml-1" title="View contact detail">&#8599;</a>
{% endif %}
```

- [ ] **Step 4: Attach follow-up counts in contacts.py**

In the contacts list handler, after building the contact lists, batch-query follow-up counts:

```python
# Batch follow-up counts for all contacts
_all_contact_ids = [c["id"] for c in all_people if c.get("id")]
if _all_contact_ids:
    _fu_rows = conn.execute(f"""
        SELECT contact_id, COUNT(*) AS cnt FROM activity_log
        WHERE contact_id IN ({','.join('?' * len(_all_contact_ids))})
          AND follow_up_done = 0 AND follow_up_date IS NOT NULL
        GROUP BY contact_id
    """, _all_contact_ids).fetchall()
    _fu_map = {r["contact_id"]: r["cnt"] for r in _fu_rows}
    for c in all_people:
        c["open_followup_count"] = _fu_map.get(c.get("id"), 0)
```

Apply same to placement, internal, client contact lists.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: expertise pills, follow-up badges, and detail links on contact matrix rows"
```

---

### Task 3: Contact Detail Page

**Files:**
- Create: `src/policydb/web/templates/contacts/detail.html`
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Add detail page route**

Add to `contacts.py`:

```python
@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(request: Request, contact_id: int, conn=Depends(get_db)):
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return HTMLResponse("Contact not found", status_code=404)
    contact = dict(contact)
    _attach_expertise(conn, [contact])

    # Assignments
    policy_assignments = [dict(r) for r in conn.execute("""
        SELECT cpa.*, p.policy_uid, p.policy_type, p.carrier, p.renewal_status,
               c.name AS client_name, c.id AS client_id
        FROM contact_policy_assignments cpa
        JOIN policies p ON cpa.policy_id = p.id
        JOIN clients c ON p.client_id = c.id
        WHERE cpa.contact_id = ? AND p.archived = 0
        ORDER BY c.name, p.policy_type
    """, (contact_id,)).fetchall()]

    client_assignments = [dict(r) for r in conn.execute("""
        SELECT cca.*, c.name AS client_name, c.id AS client_id
        FROM contact_client_assignments cca
        JOIN clients c ON cca.client_id = c.id
        WHERE cca.contact_id = ? AND c.archived = 0
        ORDER BY c.name
    """, (contact_id,)).fetchall()]

    # Group by client
    assignments = {}
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

    # Activities
    activities = [dict(r) for r in conn.execute("""
        SELECT a.*, c.name AS client_name, p.policy_uid
        FROM activity_log a
        JOIN clients c ON a.client_id = c.id
        LEFT JOIN policies p ON a.policy_id = p.id
        WHERE a.contact_id = ?
        ORDER BY a.activity_date DESC, a.id DESC
        LIMIT 50
    """, (contact_id,)).fetchall()]
    total_hours = sum(float(a["duration_hours"] or 0) for a in activities)

    # Pending follow-ups
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
```

- [ ] **Step 2: Create detail.html template**

Create `src/policydb/web/templates/contacts/detail.html` extending base template. Sections:

**Header:** Contact name (large), org, email/phone/mobile (all contenteditable with PATCH save to existing endpoints), expertise pills with ⭐ toggle, expertise notes.

**Assignments:** Collapsible card grouped by client. Each client shows name (link), type badges (Placement/Internal/Client), linked policies with type+carrier+status. "Remove" action per assignment.

**Activity History:** Collapsible card, reverse chronological list. Date, type badge, disposition badge, subject, policy link, duration. Total hours footer. COR tags where present.

**Follow-Ups:** Collapsible card (open if any pending). Same row pattern as follow-ups page with disposition pills, snooze buttons.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/contacts/detail.html src/policydb/web/routes/contacts.py
git commit -m "feat: contact detail page with assignments, activity history, and follow-ups"
```

---

### Task 4: Checkboxes + Bulk Action Bar

**Files:**
- Modify: `src/policydb/web/templates/contacts/list.html`
- Modify: `src/policydb/web/templates/contacts/_placement_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_internal_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_client_matrix_row.html`
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Add checkboxes to matrix rows**

In each matrix row template, add a checkbox as the first cell:

```html
<td class="px-2 py-2 w-8">
  <input type="checkbox" class="contact-check rounded border-gray-300" value="{{ c.id }}" onchange="updateContactBulkBar()">
</td>
```

Add corresponding `<th>` with master toggle in the table headers in `list.html`.

- [ ] **Step 2: Add bulk action bar to list.html**

At the bottom of `list.html`, add a fixed bulk action bar (same pattern as follow-ups):

```html
<div id="contact-bulk-bar" class="hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 shadow-lg px-6 py-3 flex items-center gap-3 z-50">
  <span class="text-sm font-medium text-gray-700" id="contact-bulk-count">0 selected</span>
  <button onclick="bulkDeleteContacts()" class="bg-red-500 hover:bg-red-600 text-white text-sm px-3 py-1.5 rounded">Delete Selected</button>
  <button onclick="showMergeComparison()" id="contact-merge-btn" class="hidden bg-blue-500 hover:bg-blue-600 text-white text-sm px-3 py-1.5 rounded">Merge Selected</button>
  <button onclick="clearContactChecks()" class="text-sm text-gray-400 hover:text-gray-600 ml-auto">Clear</button>
</div>
```

- [ ] **Step 3: Add bulk action JS**

```javascript
function updateContactBulkBar() {
  var checked = document.querySelectorAll('.contact-check:checked');
  var bar = document.getElementById('contact-bulk-bar');
  var count = document.getElementById('contact-bulk-count');
  var mergeBtn = document.getElementById('contact-merge-btn');
  if (checked.length > 0) {
    bar.classList.remove('hidden');
    count.textContent = checked.length + ' selected';
    mergeBtn.classList.toggle('hidden', checked.length !== 2);
  } else {
    bar.classList.add('hidden');
  }
}

function bulkDeleteContacts() {
  var ids = Array.from(document.querySelectorAll('.contact-check:checked')).map(cb => cb.value);
  if (!confirm('Delete ' + ids.length + ' contact(s)? This removes all their assignments.')) return;
  Promise.all(ids.map(id =>
    fetch('/contacts/unified/' + id + '/delete', {method: 'POST'})
  )).then(() => window.location.reload());
}

function clearContactChecks() {
  document.querySelectorAll('.contact-check').forEach(cb => cb.checked = false);
  updateContactBulkBar();
}
```

- [ ] **Step 4: Add bulk delete endpoint (by ID)**

In contacts.py, add an endpoint that accepts contact_id for deletion (the existing ones use name-based routing). Or use the existing unified delete with name lookup.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: checkboxes and bulk action bar on contacts list"
```

---

### Task 5: Improved Merge UI

**Files:**
- Modify: `src/policydb/web/templates/contacts/list.html`
- Modify: `src/policydb/web/routes/contacts.py`

- [ ] **Step 1: Add merge comparison endpoint**

```python
@router.get("/contacts/merge-compare", response_class=HTMLResponse)
def merge_compare(request: Request, id1: int = 0, id2: int = 0, conn=Depends(get_db)):
    c1 = dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (id1,)).fetchone()) if id1 else None
    c2 = dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (id2,)).fetchone()) if id2 else None
    if not c1 or not c2:
        return HTMLResponse("Both contacts required", status_code=400)
    # Attach counts
    for c in [c1, c2]:
        c["policy_count"] = conn.execute("SELECT COUNT(*) FROM contact_policy_assignments WHERE contact_id = ?", (c["id"],)).fetchone()[0]
        c["client_count"] = conn.execute("SELECT COUNT(*) FROM contact_client_assignments WHERE contact_id = ?", (c["id"],)).fetchone()[0]
        _attach_expertise(conn, [c])
    return templates.TemplateResponse("contacts/_merge_compare.html", {
        "request": request, "c1": c1, "c2": c2,
    })
```

- [ ] **Step 2: Create merge comparison partial**

Create `src/policydb/web/templates/contacts/_merge_compare.html`:

Side-by-side table comparing both contacts' fields, assignment counts, expertise. Radio buttons to select which survives. Merge button POSTs to existing `/contacts/merge`.

- [ ] **Step 3: Wire merge button to comparison**

The `showMergeComparison()` JS function (from Task 4) gets the two selected contact IDs and loads the comparison via HTMX:

```javascript
function showMergeComparison() {
  var ids = Array.from(document.querySelectorAll('.contact-check:checked')).map(cb => cb.value);
  if (ids.length !== 2) return;
  htmx.ajax('GET', '/contacts/merge-compare?id1=' + ids[0] + '&id2=' + ids[1], {
    target: '#merge-panel', swap: 'innerHTML'
  });
  document.getElementById('merge-panel').classList.remove('hidden');
}
```

Add `<div id="merge-panel" class="hidden"></div>` to list.html.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: side-by-side merge comparison for contacts"
```

---

### Task 6: Expandable Indicators + Email Tokens

**Files:**
- Modify: `src/policydb/web/templates/contacts/_placement_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_internal_matrix_row.html`
- Modify: `src/policydb/web/templates/contacts/_client_matrix_row.html`
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add expandable chevron indicators**

In matrix row templates, on the Policies/Clients expandable column, add a `▸` chevron:

```html
<span class="text-xs text-gray-400 cursor-pointer hover:text-marsh" onclick="this.closest('details').open = !this.closest('details').open">
  {{ count }} ▸
</span>
```

Or if using `<details>/<summary>`, the browser handles the toggle already — just add a visual chevron to the `<summary>` text.

- [ ] **Step 2: Add placement colleague email tokens**

In `src/policydb/email_templates.py`, find `policy_context()`. Add:

```python
    # Placement colleague info
    _pc = conn.execute("""
        SELECT co.name, co.email, co.phone FROM contact_policy_assignments cpa
        JOIN contacts co ON cpa.contact_id = co.id
        WHERE cpa.policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
          AND cpa.is_placement_colleague = 1 LIMIT 1
    """, (policy_uid.upper(),)).fetchone()
    ctx["placement_colleague_name"] = _pc["name"] if _pc else ""
    ctx["placement_colleague_email"] = _pc["email"] if _pc else ""
    ctx["placement_colleague_phone"] = _pc["phone"] if _pc else ""
```

Add to `CONTEXT_TOKEN_GROUPS` under "Policy":
```python
    ("placement_colleague_name", "Placement Colleague"),
    ("placement_colleague_email", "Colleague Email"),
    ("placement_colleague_phone", "Colleague Phone"),
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: expandable chevrons on contact columns, placement colleague email tokens"
```

---

### Task 7: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

- [ ] **Step 2: Manual test**

1. **Deprecated forms gone:** Verify old edit buttons don't appear on any contact row
2. **Expertise pills:** Check all four tabs show expertise pills on contacts that have tags
3. **Follow-up badges:** Contacts with pending follow-ups show red count badge
4. **Detail page:** Click a contact name → full detail page with assignments, activities, follow-ups
5. **Checkboxes:** Select contacts → bulk bar appears → Delete Selected works
6. **Merge:** Select 2 contacts → Merge Selected opens comparison → merge works
7. **Expandable indicators:** Policies/Clients columns show ▸ chevron
8. **Email tokens:** In template builder, {{placement_colleague_name}} etc. available

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for contacts overhaul"
```
