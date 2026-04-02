# Action Center — Source-Specific Edit Slideovers

**Date:** 2026-04-02
**Status:** Approved

## Problem

The Action Center follow-ups tab only shows the edit pencil (slideover trigger) on `activity` and `project` sourced items. Policy reminders, client follow-ups, and issues have no inline edit affordance — users must navigate away to edit them. The Activities tab also lacks a pencil for quick editing, and its unused board view adds clutter.

## Goals

1. Add lightweight edit slideovers for **policy**, **client**, and **issue** source types in the Follow-ups tab
2. Add pencil buttons on the **Activities tab** (table rows) and **Issues tab** (list + board views) for consistent edit UX
3. Remove the unused **board view** from the Activities tab, defaulting to table only
4. Reuse the existing `fu-edit-panel` container in `base.html` — no new JS infrastructure

## Architecture

All slideovers load into the existing `#fu-edit-content` container via `hx-get` → `hx-target="#fu-edit-content"` `hx-swap="innerHTML"`, reusing `openFollowupEdit()` / `closeFollowupEdit()` from `base.html`. The panel is 480px wide, right-anchored, with backdrop overlay.

### New Partials

| Partial | Location | Loaded by |
|---------|----------|-----------|
| `_edit_policy_slideover.html` | `templates/action_center/` | `GET /policies/{uid}/edit-followup-slideover` |
| `_edit_client_slideover.html` | `templates/action_center/` | `GET /clients/{id}/edit-followup-slideover` |
| `_edit_issue_slideover.html` | `templates/action_center/` | `GET /issues/{id}/edit-slideover` |

### Pencil Button Routing (Follow-ups Tab)

In `_followup_sections.html`, the pencil button dispatches based on `item.source`:

| Source | Endpoint | Has pencil |
|--------|----------|-----------|
| `activity` | `/activities/{id}/edit-slideover` | Yes (existing) |
| `project` | `/activities/{id}/edit-slideover` | Yes (just fixed) |
| `policy` | `/policies/{policy_uid}/edit-followup-slideover` | **New** |
| `client` | `/clients/{id}/edit-followup-slideover` | **New** |
| `issue` | `/issues/{id}/edit-slideover` | **New** |
| `milestone` | N/A | No — timeline-driven, no editable record |

### Cross-Tab Pencil Buttons

| Tab | Row template | Slideover |
|-----|-------------|-----------|
| Activities tab | Table rows in `_activities.html` | Existing activity edit slideover |
| Issues tab (list) | `_issue_row.html` | New issue edit slideover |
| Issues tab (board) | `_issue_board_card.html` | New issue edit slideover |

## Slideover Fields

### Policy Slideover

- **Header:** Client name + policy UID + carrier
- **Follow-up date** — `<input type="date">` with +1d / +3d / +7d / +2w quick buttons
- **Renewal status** — pill buttons from `renewal_statuses` config
- **Save:** PATCH on change/blur, green flash feedback

### Client Slideover

- **Header:** Client name
- **Follow-up date** — `<input type="date">` with +1d / +3d / +7d / +2w quick buttons
- **Notes** — textarea saving to `clients.notes` on blur
- **Save:** PATCH on change/blur, green flash feedback

### Issue Slideover

- **Header:** Issue UID + client name
- **Due date** — `<input type="date">` with +1d / +3d / +7d quick buttons
- **Severity** — pill buttons (Critical / High / Normal / Low)
- **Status** — pill buttons from `issue_statuses` config
- **Subject** — text input, save on blur
- **Details** — textarea, save on blur
- **Save:** Uses existing PATCH endpoints (`/issues/{id}/due-date`, `/issues/{id}/status`, `/issues/{id}/severity`, `/issues/{id}/subject`, `/issues/{id}/details`)

### Visual Pattern

All slideovers match the existing activity edit slideover styling:
- 480px width, right-anchored
- Label: `text-[10px] font-medium text-gray-500 uppercase tracking-wide`
- Inputs: `text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh`
- Green flash on successful save (`#ecfdf5` background, 800ms fade)
- Close button (X) top-right, backdrop click to close

## Backend Endpoints

### New Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/policies/{uid}/edit-followup-slideover` | Return policy slideover partial |
| `PATCH` | `/policies/{uid}/followup-field` | Update `follow_up_date` or `renewal_status` |
| `GET` | `/clients/{id}/edit-followup-slideover` | Return client slideover partial |
| `PATCH` | `/clients/{id}/followup-field` | Update `follow_up_date` or `notes` |
| `GET` | `/issues/{id}/edit-slideover` | Return issue slideover partial |

### Existing Endpoints (reused by issue slideover)

- `PATCH /issues/{id}/due-date`
- `POST /issues/{id}/status`
- `PATCH /issues/{id}/severity`
- `PATCH /issues/{id}/subject`
- `PATCH /issues/{id}/details`

Note: Issue status update is `POST` (existing), not `PATCH`. The slideover JS will use the existing endpoint as-is.

## Activities Tab — Board View Removal

### Remove

- Board/table toggle buttons in `_activities.html` (lines 117-132)
- Hidden input `#ac-act-view-mode` (line 114)
- Conditional board include `{% if view_mode == 'board' %}` block (line 140-141)
- `_activities_board.html` template file
- `view_mode` parameter handling in the activities route

### Keep

Table view becomes the only view. No toggle UI needed.

### Add

Pencil button in the last column of each activity table row, opening the existing activity edit slideover via `hx-get="/activities/{id}/edit-slideover"`.

## Files Changed

### Templates (modified)
- `action_center/_followup_sections.html` — expand pencil button routing for all sources
- `action_center/_activities.html` — remove board toggle/include, add pencil column
- `action_center/_issue_row.html` — add pencil button
- `action_center/_issue_board_card.html` — add pencil button

### Templates (new)
- `action_center/_edit_policy_slideover.html`
- `action_center/_edit_client_slideover.html`
- `action_center/_edit_issue_slideover.html`

### Templates (deleted)
- `action_center/_activities_board.html`

### Routes (modified)
- `routes/policies.py` — add `GET edit-followup-slideover` + `PATCH followup-field`
- `routes/clients.py` — add `GET edit-followup-slideover` + `PATCH followup-field`
- `routes/issues.py` — add `GET edit-slideover`
- `routes/action_center.py` — remove `view_mode` param from activities endpoint

## Out of Scope

- No activity logging from within policy/client slideovers (existing activity system handles this)
- No slideover for milestone items (timeline-driven)
- No changes to inbox, scratchpads, anomalies, activity-review, or data-health tabs
- Issues tab board/list toggle stays (it is used)
