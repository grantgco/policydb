# Contact Expertise & Specialty Tracking — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** Two-category expertise tagging (line + industry) for contacts, searchable/filterable on contacts page, suggested during policy assignment.

---

## Problem Statement

When looking for the right person for a specific need — "Who does casualty placements for sports & entertainment?" — the user relies on memory. There's no structured way to record what someone is good at, search by specialty, or get suggestions when assigning contacts to policies.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How expertise is captured | Config-managed tags (two categories) + free-text notes | Structured for search, notes for context |
| Tag categories | Line (what they place) + Industry (who they serve) | Two dimensions capture the real-world need: "casualty for sports" |
| Storage | `contact_expertise` table with `category` column | Proper relational, clean queries, no comma-separated text |
| Notes | `expertise_notes` TEXT on base `contacts` table | Global to the person, not per-assignment |
| Tag management | Config lists in Settings | Same simple pattern as all other config lists |
| Search/filter | Tag filter pills on contacts page | Quick filtering by line and/or industry |
| Suggestions | Highlighted contacts during policy assignment | Non-blocking — suggests, doesn't force |
| Suggestion matching | Direct tag match for v1 | If expertise tag matches policy_type, suggest. Mapping can come later |

---

## 1. Schema

### New table: `contact_expertise`

```sql
CREATE TABLE IF NOT EXISTS contact_expertise (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category   TEXT NOT NULL DEFAULT 'line',
    tag        TEXT NOT NULL,
    UNIQUE(contact_id, category, tag)
);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_tag ON contact_expertise(tag);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_contact ON contact_expertise(contact_id);
```

**Migration file:** `src/policydb/migrations/063_contact_expertise.sql`

### New column on `contacts` table

```sql
ALTER TABLE contacts ADD COLUMN expertise_notes TEXT;
```

### Categories

- `line` — insurance line expertise: Casualty, Property, D&O, Workers Comp, etc.
- `industry` — domain/niche expertise: Sports & Entertainment, Construction, Healthcare, etc.

---

## 2. Configuration

Added to `_DEFAULTS` in `src/policydb/config.py`:

```python
"expertise_lines": [
    "Casualty", "Property", "Workers Compensation", "Professional Liability",
    "D&O", "Cyber", "Construction", "Environmental", "Marine",
    "Aviation", "Surety", "Executive Risk", "Employee Benefits",
],
"expertise_industries": [
    "Sports & Entertainment", "Construction", "Healthcare", "Real Estate",
    "Technology", "Manufacturing", "Hospitality", "Energy",
    "Financial Services", "Public Entity", "Transportation",
],
```

Managed in Settings UI via existing `_list_card.html` pattern (two new flat string lists).

**Files affected:**
- `src/policydb/config.py` — add defaults
- `src/policydb/web/routes/settings.py` — pass to template context
- `src/policydb/web/templates/settings.html` — include two new list cards

---

## 3. UI — Editing Expertise on Contacts

### Contact matrix / detail view

When viewing or editing a contact (on client detail, policy detail, or contacts list page), add an expandable "Expertise" section below the contact's name/email/phone:

**Two rows of pill buttons:**

```
Lines:    [Casualty] [Property] [D&O] [Workers Comp] ...
Industry: [Sports & Ent.] [Construction] [Healthcare] ...
```

- Selected pills are highlighted (bg-marsh pattern)
- Click to add/remove (toggles the tag in `contact_expertise` via PATCH)
- Below the pills, a small contenteditable notes field for `expertise_notes`

**Endpoints:**

`POST /contacts/{contact_id}/expertise`
- Body: `{"category": "line", "tag": "Casualty"}` — adds tag
- Response: `{"ok": true}`

`DELETE /contacts/{contact_id}/expertise`
- Body: `{"category": "line", "tag": "Casualty"}` — removes tag
- Response: `{"ok": true}`

`PATCH /contacts/{contact_id}/expertise-notes`
- Body: `{"value": "Best resource for large middle-market casualty programs in TX"}`
- Response: `{"ok": true, "formatted": "..."}`

### Quick-tag from context (inline expertise tagging)

When working with a contact in a policy or client context, a small tag button appears next to the contact's name. Clicking it expands an inline pill row for quick expertise tagging without navigating away.

**Where the quick-tag button appears:**
- Policy edit page — next to placement colleague and underwriter names
- Client detail page — contact matrix rows
- Follow-up rows — next to the contact person name (when contact_id is resolved)

**UI pattern:**
```
John Smith [⭐]                    ← click to expand
John Smith [⭐]
  Lines:    [Casualty] [Property]  ← inline pill row, click to toggle
  Industry: [Sports & Ent.]       ← same pills as full editor
```

- `⭐` button toggles the inline pill row
- Pills save immediately on click via `POST /contacts/{contact_id}/expertise`
- No modal, no navigation — stays in context
- If the contact already has expertise tags, they show as small pills next to the name (visible without clicking the tag button)

**Requirement:** The contact must have a resolved `contact_id` (not just a freeform name string). If `contact_id` is NULL, the tag button is hidden.

### Where expertise displays

When a contact has expertise tags, show them as small colored pills next to their name:

- Line tags: blue pills (`bg-blue-50 text-blue-600`)
- Industry tags: green pills (`bg-green-50 text-green-600`)

This appears in:
- Contact matrix rows (client detail, policy detail)
- Contacts list page
- Contact picker/autocomplete suggestions

---

## 4. UI — Search & Filter on Contacts Page

### Tag filter on contacts list

Add two rows of filter pills at the top of the contacts list page:

```
Filter by Line:     [All] [Casualty] [Property] [D&O] ...
Filter by Industry: [All] [Sports & Ent.] [Construction] ...
```

- Click a pill to filter the contact list to only those with that tag
- Can select one from each category to do AND filtering: "Casualty AND Sports & Entertainment"
- "All" clears the filter for that category
- Filter via HTMX partial reload with query params: `/contacts?line=Casualty&industry=Sports+%26+Entertainment`

### Query

```python
def get_contacts_filtered(conn, line: str = "", industry: str = ""):
    sql = """
        SELECT co.*,
               GROUP_CONCAT(DISTINCT ce_l.tag) AS line_tags,
               GROUP_CONCAT(DISTINCT ce_i.tag) AS industry_tags
        FROM contacts co
        LEFT JOIN contact_expertise ce_l ON ce_l.contact_id = co.id AND ce_l.category = 'line'
        LEFT JOIN contact_expertise ce_i ON ce_i.contact_id = co.id AND ce_i.category = 'industry'
    """
    where = []
    params = []
    if line:
        where.append("EXISTS(SELECT 1 FROM contact_expertise x WHERE x.contact_id = co.id AND x.category = 'line' AND x.tag = ?)")
        params.append(line)
    if industry:
        where.append("EXISTS(SELECT 1 FROM contact_expertise x WHERE x.contact_id = co.id AND x.category = 'industry' AND x.tag = ?)")
        params.append(industry)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY co.id ORDER BY co.name"
    return conn.execute(sql, params).fetchall()
```

---

## 5. UI — Expertise Suggestions During Assignment

### When assigning contacts to a policy

When the user is adding a placement colleague or underwriter on a policy edit page, the contact picker shows suggested contacts whose expertise matches the policy's coverage type.

**Logic:**
1. Get the policy's `policy_type` (e.g., "General Liability")
2. Query contacts with a matching `line` expertise tag
3. Mark those contacts as "suggested" in the picker results
4. Sort suggested contacts to the top

**Display:**
```
John Smith ⭐ Casualty, Sports & Ent.
Bob Wilson ⭐ Casualty
Jane Doe
Mike Brown
```

- Suggested contacts get a star icon and their expertise tags displayed
- Non-matching contacts still appear below
- Direct match for v1: `WHERE ce.category = 'line' AND ce.tag = ?` with the policy_type

**Where this applies:**
- Policy edit page — placement colleague picker
- Policy edit page — underwriter picker
- Any contact autocomplete that's in a policy context

---

## 6. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Contact with no expertise tags | No pills shown, appears normally in lists, never suggested |
| Tag removed from config | Existing contacts keep the tag in DB. Tag no longer appears as a selectable pill. User can clean up manually. |
| Contact with both line and industry tags | Both rows of pills show. Both filter dimensions work for search. |
| Search with line + industry filter | AND logic — contact must have BOTH tags to appear |
| Expertise notes empty | Notes field shows placeholder "Add expertise notes..." |
| Delete contact | CASCADE removes all expertise rows |
| Suggestion with no match | No contacts get star. Picker works normally. |
| Policy type doesn't match any expertise tag exactly | No suggestions. Direct match only for v1. |
| Multiple contacts with same expertise | All shown, sorted alphabetically. No ranking beyond suggested/not-suggested. |
