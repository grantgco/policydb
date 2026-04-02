# Team Chart: Placement Colleague Suggestions

**Date:** 2026-04-02
**Status:** Approved

## Problem

When building team charts, placement colleagues who are actively working a client's policies don't automatically surface as potential team members. Users must manually remember and re-add them to the client's internal team — easy to forget, especially when colleagues work multiple policies across the account.

## Solution

Auto-suggest placement colleagues on the team chart editor. The chart queries `contact_policy_assignments` for the client's policies, finds contacts not already on the client's internal team (`contact_client_assignments` with `contact_type='internal'`), and surfaces them as suggested cards with one-click confirm/dismiss.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Visual distinction after confirm | None — same as any team member | A team member is a team member regardless of origin |
| Suggestion style | Amber dashed container below confirmed team | Clearly distinct but not disruptive |
| Role auto-fill | Smart default from policy assignments (e.g., "Placement - GL, Property") | Saves a step; editable after confirm |
| Dismiss behavior | Permanent per client | Prevents repeated noise; can always manually add later |
| Confirm action | Creates `contact_client_assignments` record with `contact_type='internal'` | Uses existing unified contacts architecture |

## Data Flow

### Suggestion Query

Find placement colleagues on the client's policies who are NOT already internal team members and NOT dismissed:

```sql
SELECT DISTINCT c.id, c.name, c.email, c.phone, c.mobile,
       GROUP_CONCAT(DISTINCT
         CASE WHEN cpa.is_placement_colleague = 1
              THEN 'Placement'
              ELSE cpa.role
         END || ' - ' || p.policy_type
       ) AS suggested_role
FROM contact_policy_assignments cpa
JOIN contacts c ON c.id = cpa.contact_id
JOIN policies p ON p.id = cpa.policy_id
WHERE p.client_id = :client_id
  AND p.is_opportunity = 0
  AND cpa.contact_id NOT IN (
    SELECT contact_id FROM contact_client_assignments
    WHERE client_id = :client_id AND contact_type = 'internal'
  )
  AND cpa.contact_id NOT IN (
    SELECT contact_id FROM team_chart_dismissals
    WHERE client_id = :client_id
  )
GROUP BY c.id
```

### Confirm Flow

1. User clicks "Add to Team" on a suggested card
2. POST to existing contact assignment endpoint
3. Creates `contact_client_assignments` row: `contact_id`, `client_id`, `contact_type='internal'`, `role` = smart default from policy assignments
4. Card moves from suggested section into the confirmed team grid (HTMX swap)
5. Role is editable inline after confirm (existing team chart editor behavior)

### Dismiss Flow

1. User clicks "✕" on a suggested card
2. POST to new dismiss endpoint
3. Inserts row into `team_chart_dismissals` table
4. Card removed from suggestions via HTMX swap
5. Contact never suggested again for this client (can still be manually added)

## Schema Changes

### New Table: `team_chart_dismissals`

```sql
CREATE TABLE IF NOT EXISTS team_chart_dismissals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, client_id)
);
```

Single new migration. No changes to existing tables.

## UI Changes

### Team Chart Template (`_tpl_team_chart.html`)

**Preview canvas:** After the last group of confirmed team cards, render a "Suggested from Policies" section if suggestions exist. Uses amber dashed styling (matching the mockup). Section is hidden when no suggestions remain. Not rendered in export/snapshot mode.

**Editor panel:** Add suggested contacts as a collapsible section below the existing member list. Each suggestion card shows:
- Name, email, phone (from `contacts` table)
- Smart role default (from query `GROUP_CONCAT`)
- "Add to Team" button (POST, creates assignment, refreshes chart)
- "✕" dismiss button (POST, creates dismissal, removes card)

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/team/{client_id}/suggestions` | Returns suggested placement colleagues |
| POST | `/api/team/{client_id}/suggestions/{contact_id}/confirm` | Confirms suggestion (creates client assignment) |
| POST | `/api/team/{client_id}/suggestions/{contact_id}/dismiss` | Dismisses suggestion (creates dismissal record) |

## Edge Cases

- **Contact already dismissed then manually added**: The manual add creates a `contact_client_assignments` record. The suggestion query's `NOT IN (SELECT ... internal)` clause naturally excludes them. The dismissal record is inert.
- **Contact removed from internal team after being confirmed**: The confirm flow does NOT create a dismissal record — it creates a client assignment. If that assignment is later deleted, the contact reappears as a suggestion (since they're still on policies but no longer internal, and no dismissal exists). This is intentional — the user can re-confirm or dismiss.
- **No placement colleagues on any policy**: Suggestion section is simply not rendered.
- **All suggestions dismissed**: Suggestion section is not rendered.
- **Export/snapshot**: Suggestion section is excluded (only confirmed team members appear in exports).
