# Insurance Needed By — Reminders & Visual Urgency

**Date:** 2026-04-01
**Status:** Approved
**Field:** `projects.insurance_needed_by` (DATE, migration 061)

## Problem

The `insurance_needed_by` field on the project pipeline is display-only. Users set a date but get no reminders, no escalation, and no visual pressure as the deadline approaches. Insurance deadlines slip silently.

## Design

### 1. Escalating Suggested Follow-ups

Three tiers of suggested follow-ups for projects with `insurance_needed_by` set, surfaced in the Action Center follow-ups tab under the existing "Suggested" section.

| Tier | Trigger | Label |
|------|---------|-------|
| 30 days out | `insurance_needed_by - 30d <= today` | Normal |
| 14 days out | `insurance_needed_by - 14d <= today` | High |
| 7 days out | `insurance_needed_by - 7d <= today` | Urgent |

**Suppression rules** — a suggestion clears ONLY when:
- The `insurance_needed_by` date passes (past due — no longer a "suggestion", handled by overdue badge)
- The project stage changes to `Bound`, `Active`, or `Complete`
- The `insurance_needed_by` date is cleared (set to NULL)

Recent activity does NOT suppress suggestions. The follow-up persists until the deadline is structurally resolved.

**Display format:** Each suggestion shows:
- Project name + client name
- "Insurance needed in Xd" (or tier label)
- Link to the client page / project pipeline

### 2. Visual Urgency Badge on Pipeline Row

Color-coded countdown badge rendered next to the `insurance_needed_by` date cell on the project pipeline table.

| State | Condition | Appearance |
|-------|-----------|------------|
| OK | >30 days out | Green pill — "32d" |
| Approaching | 30–14 days | Amber pill — "21d" |
| Soon | 14–7 days | Orange pill — "9d" |
| Urgent | <7 days | Red pill — "3d" |
| Overdue | Past due | Dark red pill — "5d overdue" |
| No date | NULL | No badge |

Badge is computed at render time from `insurance_needed_by` vs `date.today()`. No database column needed.

### 3. Configuration

New config key with default in `config.py`:

```python
"insurance_reminder_tiers": [30, 14, 7]
```

Editable in Settings UI under a new "Project Pipeline" section (or appended to existing list management). The three values represent days-before-deadline for each escalation tier.

Completed stages that suppress suggestions:

```python
"insurance_completed_stages": ["Bound", "Active", "Complete"]
```

## Implementation Scope

### queries.py
- New function `get_insurance_deadline_suggestions(conn, tiers=None)` that:
  - Queries `projects` for rows where `insurance_needed_by` is set, not NULL
  - Filters out projects in completed stages
  - Computes days remaining and assigns tier
  - Returns list of suggestion dicts matching the existing suggested follow-up format (`client_id`, `client_name`, `project_name`, `follow_up_date`, `subject`, `urgency`, etc.)

### action_center.py
- Call `get_insurance_deadline_suggestions()` alongside existing `get_suggested_followups()`
- Merge results into the suggested follow-ups section
- Each suggestion links to the client page (project pipeline tab)

### clients.py / _project_pipeline_row.html
- Add urgency badge partial next to the date input
- Compute days remaining in the route or as a Jinja2 filter
- Render colored pill with countdown text

### config.py
- Add `insurance_reminder_tiers` and `insurance_completed_stages` to `_DEFAULTS`

### settings.py
- Add both new config keys to `EDITABLE_LISTS` so they appear in the Settings UI

## Out of Scope

- Automatic issue creation from insurance deadlines
- Email notifications (follow existing manual compose flow)
- Changes to the timeline bar visualization (red marker already exists)
- Changes to exports (already included)
