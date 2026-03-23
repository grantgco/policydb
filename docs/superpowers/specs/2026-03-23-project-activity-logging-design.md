# Project-Level Activity Logging Redesign

**Date:** 2026-03-23
**Issue:** #26 (Logging Location/Project Activity)
**Status:** Design approved

---

## Problem Statement

Two bugs in the project-level activity logging flow:

1. **Contact picker empty** — The datalist ID `proj-log-contacts` is hardcoded and collides when multiple project sections exist on the client page. Additionally, the contact query only returns contacts formally assigned to policies via `contact_policy_assignments`, returning empty for projects where no contacts have been assigned at the policy level.

2. **Activity fan-out** — Logging one activity at the project level creates N identical `activity_log` rows (one per policy in the project). If a follow-up date is set with "All policies" scope (the default), N separate follow-ups appear in Action Center. The user had one conversation but sees ten follow-up items.

## Design Overview

1. **Add `project_id` to `activity_log`** — project-level activities are stored as one row with `project_id` set and `policy_id` NULL.
2. **Fix the contact picker** — scope datalist ID per project, broaden the contact query.
3. **Redesign follow-up scope** — replace "lead/all" with "project-level" (default) and "specific policy" (dropdown picker).
4. **Integrate with Action Center** — project follow-ups display inline with purple location badge.
5. **Timeline cross-reference** — project activities show under the project header AND as lighter references in each policy's timeline via a display JOIN.

---

## 1. Data Model: Migration 072

```sql
ALTER TABLE activity_log ADD COLUMN project_id INTEGER REFERENCES projects(id);
```

**Activity types after migration:**

| Scenario | `client_id` | `policy_id` | `project_id` |
|----------|-------------|-------------|---------------|
| Policy-level activity | Set | Set | NULL |
| Project-level activity | Set | NULL | Set |
| Specific policy within project | Set | Set | Set (optional) |
| Client-level activity (no policy) | Set | NULL | NULL |

No changes to `projects`, `policies`, or `contacts` tables. Existing `activity_log` rows retain their current `policy_id` values and get `project_id = NULL`.

---

## 2. Contact Picker Fix

### Datalist ID Scoping

Change hardcoded `id="proj-log-contacts"` to `id="proj-log-contacts-{{ project_id }}"` in `_project_log_form.html`. Update the `list=` attribute on the input to match.

### Broaden Contact Query

`_project_contacts()` in `clients.py` currently only queries `contact_policy_assignments`. Change to UNION three sources:

1. **Policy-assigned contacts** — existing query (contacts assigned to policies in this project)
2. **Client-level contacts** — contacts from `contact_client_assignments` for this client (fallback)
3. **All client contacts** — contacts from `contacts` table linked to this client via any assignment

Deduplicate by `contact.id`. This ensures the datalist is never empty for a valid client.

---

## 3. Project Log Form Redesign

### Follow-Up Scope Selector

Replace the current "Lead policy only" / "All N policies" radio buttons with:

| Option | Value | Behavior |
|--------|-------|----------|
| **Project-level** (default) | `scope=project` | One `activity_log` row with `project_id` set, `policy_id` NULL. Follow-up is project-level. |
| **Specific policy** | `scope=policy` | Shows a `<select>` dropdown of policies in the project. One `activity_log` row with that `policy_id` set. Traditional per-policy behavior. |

When "Specific policy" is selected, a dropdown appears showing all active policies in the project (format: `POL-xxx — Policy Type — Carrier`).

### Post-Save Behavior

- `project_log_save()` creates **one** `activity_log` row (not N)
- If scope is `project`: set `project_id`, leave `policy_id` NULL
- If scope is `policy`: set `policy_id` from the dropdown, optionally set `project_id` too
- Contact resolution: resolve `contact_person` to `contact_id` using existing pattern
- After save: refresh the project header section (existing HTMX swap)

---

## 4. Follow-Up System Integration

### `get_all_followups()` Update

Add a UNION branch for project-level follow-ups:

```sql
SELECT a.id, a.activity_date, a.subject, a.follow_up_date, a.contact_person,
       a.disposition, a.activity_type, a.client_id, a.project_id,
       c.name AS client_name, p.name AS project_name,
       NULL AS policy_uid, NULL AS policy_type, NULL AS carrier,
       1 AS is_project_followup
FROM activity_log a
JOIN clients c ON a.client_id = c.id
JOIN projects p ON a.project_id = p.id
WHERE a.follow_up_date IS NOT NULL
  AND a.follow_up_done = 0
  AND a.project_id IS NOT NULL
  AND a.policy_id IS NULL
```

Also attach `policy_count` — count of active policies in that project — for the badge display.

### Action Center Display

Project follow-ups render **inline**, sorted by date alongside policy follow-ups. Visual treatment:

- Light purple background (`bg-purple-50`)
- Purple location badge: `📍 Project Name` (instead of policy type/UID)
- Shows contact name + "N policies" count
- Same urgency classification (overdue, today, upcoming)
- Same disposition pills (Completed, Re-diary, Waiting, etc.)
- Clicking navigates to client detail page

### Disposition Handling

Project follow-ups use the same disposition flow as policy follow-ups. Key difference: `supersede_followups()` is NOT called (that's per-policy chain logic). Instead:

- **Completed:** Set `follow_up_done = 1` on the activity
- **Re-diary:** Create a new `activity_log` row with the new `follow_up_date`, same `project_id`, mark old one done
- **Waiting:** Update `disposition` to waiting state, keep follow-up active

---

## 5. Timeline Display

### Project Header Timeline

On the client detail page, project-level activities appear under the project/location header in the activity timeline. Full display: date, type badge, subject, contact, follow-up date.

### Policy Timeline Cross-Reference

When viewing a policy's activity history (on client detail or policy edit page), project-level activities for the same project appear as **lighter references**:

- Italic text, purple "📍 Project" badge
- Same date, subject, contact — but visually distinct from direct policy activities
- Not a separate DB row — rendered via a JOIN:

```sql
-- In the policy timeline query, add:
UNION ALL
SELECT a.*, 1 AS is_project_activity
FROM activity_log a
WHERE a.project_id = (SELECT project_id FROM policies WHERE id = :policy_id)
  AND a.policy_id IS NULL
  AND a.project_id IS NOT NULL
ORDER BY activity_date DESC
```

This ensures when reviewing any individual policy, you see the full context including project-level conversations — without duplicating data.

---

## 6. Route Changes

### Modified Routes

| Route | File | Change |
|-------|------|--------|
| `POST /clients/{cid}/project/log` | `clients.py` | Rewrite `project_log_save()` — one row, scope selector, project_id |
| `GET /clients/{cid}/project/log-form` | `clients.py` | Pass policies list for dropdown, fix contact query |

### Modified Queries

| Function | File | Change |
|----------|------|--------|
| `get_all_followups()` | `queries.py` | Add UNION for project-level follow-ups |
| `get_recent_activities()` | `queries.py` | Include project-level activities in timeline |
| `_project_contacts()` | `clients.py` | Broaden to include client-level contacts |

### Template Changes

| Template | Change |
|----------|--------|
| `clients/_project_log_form.html` | Scope datalist ID, new scope selector with policy dropdown |
| `action_center/_followups.html` | Purple badge rendering for project follow-ups |
| Client activity timeline partial | Cross-reference rendering for project activities in policy sections |

---

## 7. File Changes Summary

| File | Type | Changes |
|------|------|---------|
| `src/policydb/migrations/072_activity_project_id.sql` | **New** | `ALTER TABLE activity_log ADD COLUMN project_id` |
| `src/policydb/db.py` | Modify | Wire migration 072 |
| `src/policydb/web/routes/clients.py` | Modify | Rewrite `project_log_save()`, fix `_project_contacts()`, update log form GET |
| `src/policydb/queries.py` | Modify | Update `get_all_followups()`, `get_recent_activities()` |
| `src/policydb/web/templates/clients/_project_log_form.html` | Modify | Scoped datalist ID, scope selector, policy dropdown |
| `src/policydb/web/templates/action_center/_followups.html` | Modify | Project follow-up row rendering with purple badge |
| Activity timeline template(s) | Modify | Cross-reference display for project activities |

---

## Non-Goals

- Migrating existing fan-out activities to project-level (historical data stays as-is)
- Project-level activity from compliance review page (future enhancement)
- Bulk activity logging across multiple projects
- Project-level follow-up reminders/notifications distinct from policy ones
