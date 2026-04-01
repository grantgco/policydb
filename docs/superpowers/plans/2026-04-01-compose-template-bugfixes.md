# Compose Mail & Template System Bugfixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 bugs and discrepancies in the email compose panel, template token system, and formal format / policy table coupling.

**Architecture:** All fixes are targeted edits to existing files — no new files, no migrations, no new routes. Token rendering fixes in `email_templates.py`, UI coupling fixes in `_compose_slideover.html` and `outlook_routes.py`, and a doc correction in the outlook skill.

**Tech Stack:** Python (FastAPI), Jinja2, JavaScript, HTML

---

## Fix Summary

| # | Issue | Severity | File(s) |
|---|-------|----------|---------|
| 1 | `rfi_notify_context()` returns Python lists that render as `['a', 'b']` | Bug | `email_templates.py` |
| 2 | `exposure_denominator` renders literal "None" | Bug | `email_templates.py` |
| 3 | Template select overwrites user edits without confirmation | UX | `_compose_slideover.html` |
| 4 | Formal format checkbox hint — UI doesn't explain table forces HTML | UX | `_compose_slideover.html` |
| 5 | mailto fallback silently drops formal format + policy table | UX | `_compose_slideover.html` |
| 6 | Outlook skill doc says formal/table are "Independent" — incorrect | Doc | `policydb-outlook/SKILL.md` |

---

### Task 1: Fix rfi_notify_context() list stringification

**Files:**
- Modify: `src/policydb/email_templates.py:904-917`

The `received_items` and `outstanding_items` tokens are returned as Python lists. When `render_tokens()` calls `str(value)`, they produce `"['Item 1', 'Item 2']"` in the email body. Must be formatted as newline-separated bullet strings.

- [ ] **Step 1: Fix the list formatting**

In `src/policydb/email_templates.py`, change the return dict in `rfi_notify_context()` (around line 909-918):

```python
# BEFORE (lines 916-917):
        "received_items": received,
        "outstanding_items": outstanding,

# AFTER:
        "received_items": "\n".join(f"  - {item}" for item in received) if received else "",
        "outstanding_items": "\n".join(f"  - {item}" for item in outstanding) if outstanding else "",
```

This formats lists identically to how `compose.py:348-358` already formats them for the auto-generated RFI body, ensuring consistency between auto-body and template token rendering.

- [ ] **Step 2: Verify the server starts cleanly**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.email_templates import rfi_notify_context; print('OK')"`

Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "fix: format rfi_notify_context list tokens as bullet strings"
```

---

### Task 2: Fix exposure_denominator None rendering

**Files:**
- Modify: `src/policydb/email_templates.py:661`

When a policy has exposure data (`primary` exists) but `denominator` is None, the code does `str(None)` which produces the literal string `"None"` in the email.

- [ ] **Step 1: Add None guard to denominator**

In `src/policydb/email_templates.py`, line 661:

```python
# BEFORE:
    ctx["exposure_denominator"] = str(primary["denominator"]) if primary else ""

# AFTER:
    ctx["exposure_denominator"] = str(primary["denominator"]) if primary and primary.get("denominator") is not None else ""
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.email_templates import policy_context; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "fix: guard exposure_denominator against None rendering in email tokens"
```

---

### Task 3: Confirm before overwriting user edits on template select

**Files:**
- Modify: `src/policydb/web/templates/_compose_slideover.html:145-169`

When the user manually edits the subject or body, then picks a template from the dropdown, the template silently overwrites their edits. The `composeEdited` flag is tracked but never checked.

- [ ] **Step 1: Add confirmation check in loadComposeTemplate()**

In `_compose_slideover.html`, modify the `loadComposeTemplate` function (around line 145):

```javascript
  window.loadComposeTemplate = function(templateId) {
    if (!templateId) {
      // Reset to original values
      document.getElementById('compose-subject').value = originalSubject;
      document.getElementById('compose-body').value = originalBody;
      composeEdited = false;
      return;
    }
    // Warn if user has made edits
    if (composeEdited) {
      if (!confirm('You have unsaved edits. Replace with template?')) {
        // Reset dropdown to previous selection
        document.getElementById('compose-template-select').value = '';
        return;
      }
    }
    var url = '/compose/render?template_id=' + encodeURIComponent(templateId)
      + '&policy_uid=' + encodeURIComponent(composeParams.policy_uid)
      + '&client_id=' + encodeURIComponent(composeParams.client_id)
      + '&project_name=' + encodeURIComponent(composeParams.project_name)
      + '&bundle_id=' + encodeURIComponent(composeParams.bundle_id)
      + '&mode=' + encodeURIComponent(composeParams.mode);
    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.subject) document.getElementById('compose-subject').value = data.subject;
        if (data.body !== undefined) document.getElementById('compose-body').value = data.body;
        composeEdited = false;
      })
      .catch(function(err) {
        console.error('Failed to load template:', err);
      });
  };
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/_compose_slideover.html
git commit -m "fix: confirm before overwriting user edits on template select"
```

---

### Task 4: Add hint text explaining formal format / table coupling

**Files:**
- Modify: `src/policydb/web/templates/_compose_slideover.html:86-98`

The "Include policy table" checkbox silently forces the email into formal HTML mode. Add a subtle hint so the user understands the coupling. Also auto-check "Formal email format" when "Include policy table" is checked, and label it clearly.

- [ ] **Step 1: Update the options section**

In `_compose_slideover.html`, replace the options block (lines 86-98):

```html
    {# ── Options ── #}
    <div class="space-y-2">
      <div class="flex items-center gap-4 flex-wrap">
        <label class="flex items-center gap-1.5 cursor-pointer text-xs text-gray-600">
          <input type="checkbox" id="compose-formal" class="rounded border-gray-300">
          <span>Formal email format</span>
        </label>
        {% if policy_uid or project_name or issue_uid %}
        <label class="flex items-center gap-1.5 cursor-pointer text-xs text-gray-600">
          <input type="checkbox" id="compose-include-table" class="rounded border-gray-300"
                 onchange="if(this.checked) document.getElementById('compose-formal').checked = true;">
          <span>Include policy table</span>
        </label>
        {% endif %}
      </div>
      <p id="compose-formal-hint" class="text-[11px] text-gray-400 hidden">
        Formal format uses Marsh-branded HTML styling. Only applies to Outlook drafts.
      </p>
    </div>
```

- [ ] **Step 2: Show/hide the hint when formal is toggled**

Add an `onchange` to the formal checkbox to show the hint, and also show it when the table checkbox auto-checks formal. Update the formal checkbox line:

```html
          <input type="checkbox" id="compose-formal" class="rounded border-gray-300"
                 onchange="document.getElementById('compose-formal-hint').classList.toggle('hidden', !this.checked)">
```

And update the table checkbox onchange to also show the hint:

```html
          <input type="checkbox" id="compose-include-table" class="rounded border-gray-300"
                 onchange="if(this.checked){var f=document.getElementById('compose-formal');f.checked=true;f.dispatchEvent(new Event('change'));}">
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/_compose_slideover.html
git commit -m "fix: hint text and auto-check formal when policy table selected"
```

---

### Task 5: Show toast warning when mailto fallback drops formatting options

**Files:**
- Modify: `src/policydb/web/templates/_compose_slideover.html:307-327`

When Outlook is unavailable and the compose falls back to `mailto:`, both "Formal email format" and "Include policy table" are silently ignored. Add a warning toast if either was checked.

- [ ] **Step 1: Update openComposeInMail() to warn about dropped options**

In `_compose_slideover.html`, modify `openComposeInMail()` (around line 307):

```javascript
  window.openComposeInMail = function() {
    // Warn if formatting options will be lost in mailto fallback
    var formalCheck = document.getElementById('compose-formal');
    var tableCheck = document.getElementById('compose-include-table');
    var lostOptions = [];
    if (formalCheck && formalCheck.checked) lostOptions.push('formal formatting');
    if (tableCheck && tableCheck.checked) lostOptions.push('policy table');
    if (lostOptions.length && typeof showToast === 'function') {
      showToast('Note: ' + lostOptions.join(' and ') + ' not available in mail client — plain text only', false);
    }

    var toEl = document.getElementById('compose-to-email');
    var toEmail = toEl ? toEl.value : '';
    var subject = document.getElementById('compose-subject').value || '';
    var body = document.getElementById('compose-body').value || '';
    var ref = composeParams.ref_tag || '';

    var ccEmails = [];
    document.querySelectorAll('.compose-cc-check:checked').forEach(function(cb) {
      ccEmails.push(cb.value);
    });

    if (typeof buildMailto === 'function') {
      window.location.href = buildMailto(toEmail, subject, ccEmails, body, ref);
    } else {
      var url = 'mailto:' + encodeURIComponent(toEmail) + '?subject=' + encodeURIComponent(subject);
      if (ccEmails.length) url += '&cc=' + encodeURIComponent(ccEmails.join(','));
      if (body) url += '&body=' + encodeURIComponent(body);
      window.location.href = url;
    }
  };
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/_compose_slideover.html
git commit -m "fix: warn user when mailto fallback drops formal format or policy table"
```

---

### Task 6: Correct policydb-outlook skill documentation

**Files:**
- Modify: `/Users/grantgreeson/Documents/Projects/policydb/.claude/skills/policydb-outlook/SKILL.md:29`

The skill doc says formal format and policy table are "Independent" — they are not. The backend uses `if req.formal_format or policy_table_html:` which means the table checkbox forces formal mode.

- [ ] **Step 1: Fix the misleading line**

In `policydb-outlook/SKILL.md`, change line 29:

```markdown
# BEFORE:
**Formal email format (checkbox):** Marsh-branded HTML shell via `wrap_email_html()` — navy header, Noto fonts, structured layout. Independent of policy table.

# AFTER:
**Formal email format (checkbox):** Marsh-branded HTML shell via `wrap_email_html()` — navy header, Noto fonts, structured layout. Coupled with policy table — checking either triggers HTML shell (see `policydb-compose` skill for details).
```

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/policydb-outlook/SKILL.md
git commit -m "docs: correct formal format / policy table coupling in outlook skill"
```
