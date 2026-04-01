# Client About Section + Email Token Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface Business Description and Internal Notes as inline-editable fields on the client overview, and fix 3 email template token gaps.

**Architecture:** New "About" card inserted between Account Pulse and Account Strategy on the client overview tab. Uses the same contenteditable + save-on-blur pattern as the strategy section, hitting the existing `PATCH /clients/{id}/field` endpoint. Email token fixes are isolated changes to `email_templates.py`.

**Tech Stack:** Jinja2 templates, HTMX/fetch, existing PATCH endpoint, Python email_templates.py.

---

### Task 1: Add "About" Card to Client Overview

**Files:**
- Create: `src/policydb/web/templates/clients/_about_section.html`
- Modify: `src/policydb/web/templates/clients/_tab_overview.html`

- [ ] **Step 1: Create the About section partial**

Create `src/policydb/web/templates/clients/_about_section.html` with two contenteditable fields (Business Description and Internal Notes), each saving on blur via `PATCH /clients/{id}/field`. Include green flash on success and red border + toast on failure.

- [ ] **Step 2: Include the About card in the overview tab**

In `src/policydb/web/templates/clients/_tab_overview.html`, insert `{% include "clients/_about_section.html" %}` between the Account Pulse and Account Strategy includes.

- [ ] **Step 3: Commit**

---

### Task 2: Remove Notes Snippet from Account Pulse

**Files:**
- Modify: `src/policydb/web/templates/clients/_account_pulse.html`

- [ ] **Step 1: Remove the Internal Notes snippet (lines 89-96)**

Delete the `{# Internal notes snippet #}` block that shows a truncated notes preview with an "edit" link.

- [ ] **Step 2: Commit**

---

### Task 3: Fix contact_organization Token

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add `_resolve_primary_org()` helper after `_resolve_primary_contact()` (line 49)**

Query `contact_client_assignments` + `contacts` for the primary contact's organization.

- [ ] **Step 2: Update `_client_tokens()` to call it**

Replace the hardcoded empty string on line 513 with a call to `_resolve_primary_org(conn, client_id)`.

- [ ] **Step 3: Commit**

---

### Task 4: Add policy_notes and policy_description Tokens

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add `p.description AS policy_description_raw, p.notes AS policy_notes_raw` to policy_context() SELECT**

- [ ] **Step 2: Add tokens to the ctx.update() dict**

```python
"policy_description": row.get("policy_description_raw") or "",
"policy_notes": row.get("policy_notes_raw") or "",
```

- [ ] **Step 3: Register in CONTEXT_TOKEN_GROUPS under the Policy group**

```python
("policy_description", "Policy Description"),
("policy_notes", "Policy Notes"),
```

- [ ] **Step 4: Commit**

---

### Task 5: Add issue_details Token

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Add to issue_context() return dict**

```python
"issue_details": issue.get("details") or "",
```

The `details` column is already in `SELECT al.*`.

- [ ] **Step 2: Register in CONTEXT_TOKEN_GROUPS under the Issue group**

```python
("issue_details", "Issue Details"),
```

- [ ] **Step 3: Commit**

---

### Task 6: QA and Push

- [ ] **Step 1: Verify app loads**
- [ ] **Step 2: Verify About section renders on client page**
- [ ] **Step 3: Verify notes snippet removed from Account Pulse**
- [ ] **Step 4: Verify all new tokens registered in CONTEXT_TOKEN_GROUPS**
- [ ] **Step 5: Push**
