# Phase 4 — Timesheet Review Design

**Status:** Design — awaiting user approval
**Date:** 2026-04-16
**Depends on:** PRs #253 and #255 (Phases 1–3D — hours capture + tiered email defaults)
**Follow-on:** Phase 5 (Issue hour estimates)

## Overview

A weekly human-review layer over the hours captured by Phases 1–3. Lives as a new tab under the Action Center (`/action-center?tab=timesheet`) and surfaces on the dashboard when flags exist.

The page's job: (a) correct auto-captured hours that are wrong, and (b) catch work you forgot to log. It opens to the current Mon–Fri week by default with Day / Week / Range toggles.

**Success criteria:** When I close out a week, I trust that every hour logged reflects actual work, and I've had a prompt to fill any obvious gap.

## Flag types

Four signals drive attention on the page:

1. **Low-hour day** — any weekday with ≥ 1 activity logged but total `< timesheet_low_day_threshold_hours` (default `4.0h`). Zero-activity weekdays render an "Add activity" prompt rather than a flag.
2. **Client silence** — a client with an open followup OR a policy renewing within `timesheet_silence_renewal_window_days` (default `30d`) OR an open issue, AND zero activities in the range.
3. **Unreviewed heuristic email** — an activity with `source IN ('outlook_sync', 'thread_inherit')` where `reviewed_at IS NULL` and the row is in range.
4. **Activity with no hours logged** — any `activity_log` row in range where `duration_hours IS NULL`. Catches open tasks closed before Phase 1's COALESCE fix, plus anything else that slipped through without a hours value.

Deferred from this phase:
- Working-hours gap detection (Phase 5+, requires workday-hour inference).
- Formal-default high-volume pattern ("10+ formal-template emails on one client"); revisit after real usage.

## Review model

Two complementary mechanisms:

- **Auto-review on touch** — focusing any editable field on an activity row fires `POST /timesheet/activity/{id}/review`, stamping `reviewed_at`. One shot, idempotent. Zero-click for the common case.
- **Close out week** — a single button at the top right soft-stamps every un-touched activity in the range with `reviewed_at` and writes a `timesheet_closeouts` row capturing the snapshot (total hours, activity count, flag count, closed_at).

Closeouts are soft — post-close edits are allowed and show a "δ +1.5h since close" indicator on the badge. No hard lock, no re-open flow beyond a rarely-used `POST /timesheet/closeout/{id}/reopen`.

## Layout

Day-grouped timeline. Each weekday in range renders as a card, top-down:

- Optional **closeout badge** (if the range is already closed).
- **Flag strip** (only when `silent_clients` is non-empty) — top-of-week banner listing silent clients with inline "Add activity" buttons.
- **Day cards** — one per day in range, including zero-activity days. Header shows day label + total hours; low-day card gets a warm-amber border + flag pill. Body lists activities with inline-edit controls. Footer has an "➕ Add activity for {day}" button that expands an inline form.
- **Range toggle** — three-segment pill control (Day / Week / Range) swaps the panel via HTMX.
- **Close-out button** — top-right.

## Data model

### Migration `161_timesheet_review.sql`

```sql
-- Per-activity review stamp
ALTER TABLE activity_log ADD COLUMN reviewed_at TEXT;
CREATE INDEX IF NOT EXISTS idx_activity_log_reviewed_at
    ON activity_log (reviewed_at) WHERE reviewed_at IS NULL;

-- Week-level closeout log (soft stamp; snapshot-at-close)
CREATE TABLE IF NOT EXISTS timesheet_closeouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start DATE NOT NULL,
    week_end   DATE NOT NULL,
    closed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    total_hours REAL NOT NULL,
    activity_count INTEGER NOT NULL,
    flag_count INTEGER NOT NULL,
    UNIQUE (week_start)
);
```

The migration must be wired into `init_db()` in `db.py` with the standard `schema_version` check + INSERT (per `feedback_migration_reminder`).

**Why a partial index on `reviewed_at`:** most rows will eventually be reviewed; `WHERE reviewed_at IS NULL` keeps the "unreviewed emails" query fast.

**Why a dedicated closeouts table (not a column):** week-level facts don't belong on row-level records; the separate table preserves the point-in-time snapshot even if rows are later edited.

## Config keys

Scalar thresholds live in a new `timesheet_thresholds` dict in `_DEFAULTS` (mirroring the existing `anomaly_thresholds` pattern). Three keys:

- `low_day_threshold_hours` — default `4.0`.
- `silence_renewal_window_days` — default `30`.
- `range_cap_days` — default `92`. Hard cap on Range toggle.

Accessed via `cfg.get("timesheet_thresholds", {}).get("low_day_threshold_hours", 4.0)` etc. A dedicated `POST /settings/timesheet-thresholds` form route (modelled on `save_anomaly_thresholds`) exposes these in the Settings UI on the existing "Data Health" tab (or new "Timesheet" subsection). `EDITABLE_LISTS` is NOT used — that dict is for list-valued config keys only.

Reserved for later (not used in Phase 4): `workday_start_hour`, `workday_end_hour`.

## Module & queries

New module `src/policydb/timesheet.py`. Single entrypoint:

```python
def build_timesheet_payload(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    cfg: Config,
) -> dict:
    """
    Returns:
      {
        "range": {"start", "end", "label", "kind"},
        "totals": {"total_hours", "activity_count", "flag_count"},
        "flags": {
          "low_days":   [date, ...],
          "silent_clients": [{client_id, name, reason, href}, ...],
          "unreviewed_emails": int,
          "null_hour_activities": int,
        },
        "days": [
          {"date", "label", "total_hours", "is_low", "activities": [...]},
          ...   # one per day in range
        ],
        "closeout": {"closed_at": str|None, "snapshot": {...}|None},
      }
    """
```

**Five queries, all live (Approach 1 — no materialized view, no background job):**

1. **Activities in range** — `SELECT` from `activity_log` joined to `clients`, `policies`, `open_tasks` for labels. Group-by-date in Python.
2. **Day totals** — derived from #1; flag `is_low` where total > 0 AND total < threshold AND date is a weekday AND date ≤ today.
3. **Silent clients** — `clients` LEFT JOIN `activity_log` in range WHERE `a.id IS NULL`, filtered to clients with open followup / imminent renewal / open issue.
4. **Unreviewed heuristic emails** — `SELECT COUNT(*) FROM activity_log WHERE reviewed_at IS NULL AND source IN ('outlook_sync', 'thread_inherit') AND activity_date BETWEEN ? AND ?`.
5. **Activities with NULL hours** — `SELECT COUNT(*) FROM activity_log WHERE duration_hours IS NULL AND activity_date BETWEEN ? AND ?`.

**Closeout lookup** — `SELECT * FROM timesheet_closeouts WHERE week_start = ?` drives the badge.

**Dashboard badge helper** — `get_timesheet_badge(conn)` in `queries.py` returns `{flags: int, unreviewed_emails: int}` for the conditional dashboard card.

## Routes

New router `src/policydb/web/routes/timesheet.py`, prefix `/timesheet`, registered in `app.py`. Per `feedback_route_ordering_literals_first`, literal paths come before parameterized paths.

| Verb | Path | Purpose |
|---|---|---|
| `GET` | `/timesheet/panel` | HTMX partial — full page body. `?kind=week\|day\|range&start=YYYY-MM-DD&end=YYYY-MM-DD`. |
| `GET` | `/timesheet` | Full-page wrapper (dashboard card link + bookmarking). |
| `POST` | `/timesheet/closeout` | Write closeout row, bulk-stamp un-reviewed rows. |
| `POST` | `/timesheet/closeout/{id}/reopen` | Delete a closeout row. |
| `POST` | `/timesheet/activity` | Create a new activity for a gap; stamps `reviewed_at` on create. |
| `POST` | `/timesheet/activity/{id}/review` | Idempotent `reviewed_at = now()`. Called on field focus. |
| `PATCH` | `/timesheet/activity/{id}` | Save any field; delegates to existing activity PATCH; auto-stamps `reviewed_at`. Returns `{ok, formatted, total_hours}`. |
| `DELETE` | `/timesheet/activity/{id}` | Delete with inline confirm. |

The Action Center tab (`/action-center?tab=timesheet`) lazy-loads `/timesheet/panel` (per CLAUDE.md's "Tabs lazy-load on first click" default).

## Templates

New directory `src/policydb/web/templates/timesheet/` with six partials:

```
timesheet/
├── _panel.html              ← full page body (range controls + flag strip + day cards)
├── _flag_strip.html         ← top-of-week silent-client banner
├── _day_card.html           ← one day's card
├── _activity_row.html       ← display + inline-edit variants
├── _add_activity_form.html  ← inline form expanded from "➕ Add activity"
└── _closeout_badge.html     ← "Closed Apr 16" banner with δ indicator
```

**HTMX conventions (per `policydb-design-system` and CLAUDE.md):**

- Contenteditable cells for `duration_hours` and `notes`; combobox for `activity_type`; click-to-edit for `subject`.
- Each editable field: `hx-patch="/timesheet/activity/{id}"`, `hx-trigger="blur changed, keyup[keyCode==13]"`, `hx-swap="none"` — JS handles the flash on the returned `formatted` value.
- Focus on any field fires `POST /timesheet/activity/{id}/review` once, guarded by a `data-reviewed` client-side flag.
- Range toggle uses `hx-get` + `hx-push-url="true"` so URL state persists; sessionStorage remembers the last tab choice (CLAUDE.md default).
- Close-out button uses inline-confirm (NOT native `confirm()`). Destructive/finalization flows per CLAUDE.md error-state rules.
- Print styles: `.no-print` on toggle + buttons; day cards print cleanly.

**Dashboard card** — new `dashboard/_timesheet_card.html`, rendered conditionally when `badge.flags + badge.unreviewed_emails > 0`. Links to `/timesheet`.

## Edge cases

1. **Zero-activity weekday** — card renders with "No activity logged" prompt. No low-day flag (flag requires ≥ 1 activity logged below threshold).
2. **Weekend days** — cards only appear if activity was logged OR if range explicitly includes them; never low-day flagged.
3. **Future dates in range** — render as "Upcoming" with no flags and no add-activity.
4. **Activity edited out of range** — disappears on next panel render; day totals recompute.
5. **Close-out + post-close edit** — allowed. Snapshot stays frozen; "δ +1.5h since close" indicator appears on the badge.
6. **Double-firing HTMX** — `/review` endpoint is idempotent (UPSERT on `reviewed_at IS NULL`).
7. **Silent-client resolves** — flag strip hides on next panel render; no animation.
8. **Client deleted mid-review** — row renders "(client deleted)"; no crash (`activity_log.client_id` is nullable).
9. **Heuristic email false positive** — if a manually-entered `0.25h` row coincidentally matches a formal default, it shows in the "unreviewed" set until acked. Acceptable.
10. **Very large ranges** — Range toggle capped at `timesheet_range_cap_days` (default 92). Beyond that, returns 400.
11. **Multi-user** — not in scope. Single-user local tool; no `account_exec` filter in Phase 4.
12. **Timezone** — local-time `date('now')` / `activity_date`, per existing app convention.

## Testing

**Unit tests (`tests/test_timesheet.py`):**

1. `build_timesheet_payload()` shape + day count for a standard week.
2. Low-day flag — `3.9h` flags, `4.1h` does not, `0h` does not.
3. Silent-client detection — client with open followup + zero activities appears; client with activity does not.
4. Unreviewed-email count — seed 5 unreviewed + 3 reviewed → count = 5.
5. Null-hour activity count — mixed NULL/non-NULL fixtures → correct count.
6. Closeout — write a row, reload payload, assert snapshot returned + `closed_at` set.

**Route tests (`tests/test_timesheet_routes.py`):**

1. `GET /timesheet/panel` default → 200, day cards present.
2. `GET /timesheet/panel?kind=day` → one day card.
3. `POST /timesheet/activity/{id}/review` → `reviewed_at` stamped; second call no-op.
4. `PATCH /timesheet/activity/{id}` with `duration_hours=1.25` → DB updated, `reviewed_at` stamped, response has `formatted` + `total_hours`.
5. `POST /timesheet/activity` → row created, `reviewed_at` stamped, appears in next panel load.
6. `DELETE /timesheet/activity/{id}` → row gone, totals recompute.
7. `POST /timesheet/closeout` → row created, un-reviewed rows stamped; duplicate POST rejected (UNIQUE).
8. Range cap — `GET /timesheet/panel?kind=range&start=2025-01-01&end=2026-04-15` → 400.

**Manual QA (per CLAUDE.md):**

1. Screenshot `/action-center?tab=timesheet` with real data.
2. Contenteditable → blur → flash + day total update.
3. "➕ Add activity" → lands in right day, auto-stamps.
4. Delete row → inline confirm, no native `alert()`.
5. Close-out → badge appears, all rows ✓, dashboard card drops.
6. Post-close edit → δ indicator on badge.
7. Day / Week / Range toggle → URL updates, sessionStorage persists.
8. Dashboard card → shows/hides based on flag count.
9. Print mode.
10. Empty-state DB.

## What we are NOT building

- No timesheet export (CSV / xlsx) — defer until demand surfaces.
- No narrative generation for workload justification — queued under the existing `project_time_projection_planning` memory.
- No working-hours gap detection — Phase 5+.
- No auto-split of multi-client activities — single-row model only.
- No multi-user / account_exec filtering.
- No hard lock on closed weeks — soft stamp only.
- No Phase 3E sync-status work — separate phase.
- No Phase 5 issue estimates — separate phase.

## Open follow-ups (post-ship)

- Should the flag strip expose a "silence threshold" editor inline, or leave it in Settings? (Default: Settings.)
- Do we add a Phase 4.5 export once the feature has a month of usage data, or defer to Phase 5?
- Should the closeout δ indicator surface on the dashboard card as well? (Default: no — too noisy for dashboard.)
