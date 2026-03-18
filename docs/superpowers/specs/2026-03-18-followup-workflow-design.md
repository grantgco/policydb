# Follow-Up Workflow Improvements — Design Spec

**Date:** 2026-03-18
**Status:** Draft
**Scope:** Disposition tracking, disposition-driven auto-scheduling, follow-up threading with correspondence tags, inline thread summaries, full thread history on policy edit page

---

## Problem Statement

The current follow-up system is functionally solid but lacks structure around **what happened** on each attempt and **how attempts relate** to each other. The primary user workflow is "nagging" — sending RFIs, waiting on placement colleagues, waiting on carriers/clients, and following up repeatedly until a response is received. This creates three friction points:

1. **No structured outcome** — completing a follow-up only captures a freeform note, making it impossible to filter by outcome or see patterns (e.g., "I've left VM 4 times")
2. **Disconnected chains** — re-diary creates a new activity with "Follow-up:" prefix, but there's no way to see the full sequence of attempts on a policy at a glance or trace them back via a stable reference
3. **Manual re-scheduling** — every re-diary requires manually picking the next date, even though the cadence is predictable (Left VM → try again in 3 days, Sent Email → check back in 7 days)

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Outcome tracking | Structured disposition dropdown + existing note field | Filter/report on outcomes while keeping freeform detail |
| Auto-scheduling | Disposition-driven defaults, no per-policy rules | 90% of value with zero config overhead. Most follow-ups are "waiting on someone" with predictable cadence |
| Threading mechanism | `thread_id` column on `activity_log` | Simple GROUP BY, no recursion, no ambiguity. Lazy creation on first re-diary |
| Thread reference tag | `COR-{thread_id}` | Stable tag for email correspondence tracking. Persists across all re-diary attempts |
| Thread visibility | Inline compact summary + full history on policy page | At-a-glance context during daily workflow + deep dive when needed |
| Disposition config | Config list with `default_days` per item | Managed in Settings UI, customizable cadence per outcome type |

---

## 1. Schema Changes

### New columns on `activity_log`

```sql
ALTER TABLE activity_log ADD COLUMN disposition TEXT;
ALTER TABLE activity_log ADD COLUMN thread_id INTEGER;
```

**Migration file:** `src/policydb/migrations/059_followup_threading.sql` (numbered after 058_program_carriers_table.sql which ships first)

- `disposition` — stores the outcome label (e.g., "Left VM", "Waiting on Colleague"). Plain text, no FK. The config list drives dropdown options but doesn't constrain stored values.
- `thread_id` — FK to `activity_log.id` pointing to the first activity in the chain. `NULL` for standalone activities (no thread).

### Thread mechanics

- **New activity with follow-up (not re-diary):** `thread_id = NULL` (standalone)
- **Re-diary from existing activity:**
  - If parent has `thread_id`: new activity inherits same `thread_id`
  - If parent has `thread_id = NULL`: set parent's `thread_id = parent.id`, then new activity gets `thread_id = parent.id` (lazy thread creation)
- **Query full chain:** `SELECT * FROM activity_log WHERE thread_id = ? ORDER BY activity_date, id`
- **Thread reference tag:** `COR-{thread_id}` — displayed in UI, copyable via `⧉` button

### Thread ID index

```sql
CREATE INDEX IF NOT EXISTS idx_activity_thread ON activity_log(thread_id);
```

---

## 2. Configuration

### New config key: `follow_up_dispositions`

Added to `_DEFAULTS` in `src/policydb/config.py`:

```python
"follow_up_dispositions": [
    {"label": "Left VM", "default_days": 3},
    {"label": "No Answer", "default_days": 1},
    {"label": "Sent Email", "default_days": 7},
    {"label": "Sent RFI", "default_days": 7},
    {"label": "Waiting on Colleague", "default_days": 5},
    {"label": "Waiting on Client", "default_days": 7},
    {"label": "Waiting on Carrier", "default_days": 7},
    {"label": "Connected", "default_days": 0},
    {"label": "Received Response", "default_days": 0},
    {"label": "Meeting Scheduled", "default_days": 0},
    {"label": "Escalated", "default_days": 3},
],
```

### Settings UI for dispositions

Managed in the Settings page (`/settings`) alongside other config lists. Each disposition item shows:
- Label (editable)
- Default days value (editable number input)
- Reorder up/down buttons
- Remove button

The existing `_list_card.html` and settings routes (`/settings/list/add`, `remove`, `reorder`) operate on flat string lists and cannot handle `{label, default_days}` objects. **New dedicated endpoints** are needed:

- `POST /settings/dispositions/add` — accepts `label` and `default_days`, appends `{label, default_days}` object to the list
- `POST /settings/dispositions/remove` — removes by label match
- `POST /settings/dispositions/reorder` — reorder by label
- `PATCH /settings/dispositions/update` — update `default_days` for an existing label

A new template partial `_disposition_card.html` renders each item with two fields: the label text and a small number input for `default_days`. This is separate from `_list_card.html` to avoid complicating the flat-list pattern.

**Files affected:**
- `src/policydb/web/routes/settings.py` — NEW disposition-specific endpoints (not extending existing list handlers)
- `src/policydb/web/templates/settings/_disposition_card.html` — NEW partial for object-list with `{label, default_days}` items
- `src/policydb/web/templates/settings.html` — add the dispositions card to the settings page

---

## 3. Disposition UI & Auto-Scheduling

### Completion flow changes

**Files affected:**
- `src/policydb/web/routes/activities.py:128-189` — `activity_complete` endpoint
- `src/policydb/web/routes/activities.py:311-401` — `activity_followup` endpoint
- `src/policydb/web/templates/followups/_row.html:198-235` — completion form
- `src/policydb/web/templates/followups/_row.html:137-197` — re-diary form

### Modified completion form

The existing two separate form `<tr>` rows (`followup-form-row-*` at lines 137-197 and `complete-form-row-*` at lines 198-235) are replaced by a single unified form `<tr>`. Both the "Follow Up" and "Done/Clear" buttons open the same form — the disposition selection determines whether a re-diary happens. The two existing toggle functions (`toggleFollowupForm`, `toggleCompleteForm`) merge into one (`toggleDispositionForm`). At the top of the unified form: a disposition dropdown. The form adapts based on selection:

```
┌─────────────────────────────────────────────────┐
│ Disposition:  [Left VM        ▾]                │
│                                                 │
│ Hours:        [0.3    ]                         │
│ Note:         [Called, went to VM. Will try aga]│
│                                                 │
│ Next Follow-Up: [2026-03-21] (auto: +3 days)   │
│   +1d  +3d  +7d  +14d                          │
│                                                 │
│  [Log + Re-Diary]        [Mark Done]            │
└─────────────────────────────────────────────────┘
```

**Behavior:**

1. User selects disposition from dropdown (optional — backward compatible without it)
2. If disposition has `default_days > 0`:
   - Next Follow-Up date auto-fills to `today + default_days`
   - Re-diary section expands automatically
   - User can override the date or clear it to just mark done
3. If disposition has `default_days = 0` (Connected, Meeting Scheduled, Received Response):
   - No date auto-fill
   - User chooses: mark done (no re-diary) or manually set a follow-up date
4. "Mark Done" saves disposition + note + hours, sets `follow_up_done = 1`, no re-diary
5. "Log + Re-Diary" saves disposition + note + hours, marks original done, creates new threaded activity with disposition's suggested date

### Backend changes

**`activity_complete` endpoint** (`activities.py:128-189`):
- Accept new `disposition: str = Form("")` parameter
- Save `disposition` to `activity_log` on completion
- No threading logic here — this is "mark done, no re-diary"

**`activity_followup` endpoint** (`activities.py:311-401`):
- Accept new `disposition: str = Form("")` parameter
- Save `disposition` on the original activity being completed
- Thread logic on the NEW activity:
  - If original has `thread_id`: new activity gets same `thread_id`
  - If original has no `thread_id`: set original's `thread_id = original.id`, new activity gets `thread_id = original.id`
- The new activity's subject: keep existing "Follow-up: " prefix behavior
- The new activity inherits `policy_id`, `client_id`, `account_exec` from original (already does this)

### Disposition in activity row display

When an activity has a disposition, show it as a small badge next to the activity type:

```html
<span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">Left VM</span>
```

This appears in:
- `src/policydb/web/templates/activities/_activity_row.html` — activity list rows
- `src/policydb/web/templates/followups/_row.html` — follow-up rows

---

## 4. Thread Summary & History UI

### 4a. Inline thread summary (follow-up rows)

**File:** `src/policydb/web/templates/followups/_row.html`

When a follow-up row is part of a thread (`thread_id IS NOT NULL`), display a compact summary line below the subject:

```
[A-87] Call · Left VM · Acme Corp — Property Renewal
       COR-42 · 4th attempt · last: Sent Email 5d ago
```

**Data needed per row** (computed in the query):
- `thread_id` — to build the `COR-{id}` tag
- `thread_attempt_num` — `ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY activity_date, id)` or computed at Python layer
- `thread_total` — `COUNT(*) OVER (PARTITION BY thread_id)`
- `prev_disposition` — the previous activity's disposition in the thread
- `prev_days_ago` — days since the previous activity

**Query approach:** Compute these at the Python layer after fetching follow-ups, using a single batch query:

```python
# For all thread_ids in the current follow-up list:
thread_stats = conn.execute("""
    SELECT thread_id, COUNT(*) AS total,
           MAX(id) AS latest_id
    FROM activity_log
    WHERE thread_id IN (...)
    GROUP BY thread_id
""").fetchall()
```

Then for each row, attach `thread_total` and the previous activity's disposition. This avoids complex window functions in SQLite.

### 4b. Thread reference tag (COR-{id})

**Display:** Next to the existing `A-{id}` tag in follow-up rows and activity rows

```html
{% if r.thread_id %}
<span class="text-[10px] text-blue-400 font-mono">COR-{{ r.thread_id }}</span>
{% endif %}
```

**Copy button:** The existing `⧉` copy-ref-tag button (line 132 of `_row.html`) should include the `COR-{thread_id}` tag in the copied text when a thread exists. Format: `COR-42 | A-87` or just `COR-42` (the thread tag is what the user pastes into emails).

**Searchability:** The search page (`/search`) should find activities by `COR-{id}` — query `WHERE thread_id = ?` when the search term matches `COR-\d+` pattern.

### 4c. Full thread history (policy edit page)

**File:** `src/policydb/web/templates/policies/edit.html:119-243` (Activity Log section)

Add a new section **above** the existing Activity Log: "Correspondence Threads"

This section shows all active threads for the policy, each as a collapsible card:

```
┌──────────────────────────────────────────────────────────────┐
│ ▶ Correspondence Threads · 2 active                         │
├──────────────────────────────────────────────────────────────┤
│ COR-42 · Property Renewal · 4 attempts · 1.1h total         │
│  3/18  Left VM         Called main line, went to VM.    0.2h│
│  3/15  Sent Email      Sent follow-up on property RFI  0.1h│
│  3/11  Waiting on Col. Pinged Sarah for loss runs      0.3h│
│  3/08  Sent RFI        Initial RFI to client           0.5h│
├──────────────────────────────────────────────────────────────┤
│ COR-58 · Quote Follow-Up · 2 attempts · 0.4h total          │
│  3/17  Waiting on Car. Emailed underwriter for quote    0.2h│
│  3/10  Sent Email      Requested quote from Chubb      0.2h│
└──────────────────────────────────────────────────────────────┘
```

**Structure per thread:**
- Header: `COR-{id}` tag, subject of first activity in thread, attempt count, total hours
- Rows: reverse chronological, showing date, disposition badge, truncated note, hours
- Collapsed by default if thread is completed (all activities have `follow_up_done = 1`)
- Open by default if thread has a pending follow-up

**Data query:**

```python
# In policy edit route, after loading the policy:
threads = conn.execute("""
    SELECT thread_id,
           MIN(subject) AS thread_subject,
           COUNT(*) AS attempt_count,
           COALESCE(SUM(duration_hours), 0) AS total_hours,
           MAX(CASE WHEN follow_up_done = 0 THEN 1 ELSE 0 END) AS has_pending
    FROM activity_log
    WHERE policy_id = ? AND thread_id IS NOT NULL
    GROUP BY thread_id
    ORDER BY MAX(activity_date) DESC
""", (policy_id,)).fetchall()

# For each thread, load the individual activities:
for t in threads:
    t["activities"] = conn.execute("""
        SELECT activity_date, disposition, details, duration_hours, follow_up_done
        FROM activity_log
        WHERE thread_id = ?
        ORDER BY activity_date DESC, id DESC
    """, (t["thread_id"],)).fetchall()
```

---

## 5. Modified Queries

### `get_all_followups()` enhancement

**File:** `src/policydb/queries.py:531-633`

The activity source query (lines 535-556) needs additional columns:

```sql
SELECT 'activity' AS source, a.id, a.subject, a.follow_up_date, a.activity_type,
       a.contact_person, a.disposition, a.thread_id,
       ...
```

After fetching results, batch-compute thread stats for any rows with `thread_id IS NOT NULL`:
- `thread_total` — total attempts in the thread
- `thread_attempt_num` — this activity's position in the chain
- `prev_disposition` — previous activity's disposition
- `prev_days_ago` — days since previous activity

### `supersede_followups()` — no changes needed

The existing supersede logic (lines 514-528) works correctly with threads. When a new follow-up supersedes old ones, the old activities get `follow_up_done = 1` but their `thread_id` remains intact for history.

---

## 6. Email Template Tokens

**File:** `src/policydb/email_templates.py`

Add to `followup_context()` and `CONTEXT_TOKENS`:

```python
# In followup_context():
ctx["disposition"] = row.get("disposition") or ""
ctx["thread_ref"] = f"COR-{row['thread_id']}" if row.get("thread_id") else ""
ctx["attempt_number"] = ""  # computed if thread_id present

# In CONTEXT_TOKENS under "followup":
("disposition", "Disposition"),
("thread_ref", "Thread Reference"),
```

The `{{thread_ref}}` token lets users include the `COR-{id}` tag in email subject templates, making email search even more seamless.

---

## 7. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Complete without disposition | Allowed — disposition is optional for backward compatibility |
| Re-diary without disposition | Allowed — thread still created, disposition just empty |
| Delete activity in middle of thread | Thread chain has a gap but `thread_id` grouping still works. Attempt count adjusts. |
| Delete first activity (thread anchor) | Other activities still share `thread_id` pointing to deleted row. `COR-{id}` tag still valid as a reference number even if row is gone. |
| Merge two threads | Not supported. If needed, manually re-diary from one to create a link. |
| Thread spans multiple policies | Not supported. `thread_id` groups by activity chain, which is always same policy. |
| Standalone activity with no follow-up | `thread_id = NULL`, `disposition = NULL`. No thread display. Behaves exactly as today. |
| Bulk complete with disposition | The bulk-complete form gets a disposition dropdown. All selected follow-ups get the same disposition. |
| Snooze (+1d/+3d/+7d) | No disposition set — snooze is a quick reschedule, not a completion. Thread unaffected. |
| Config: add new disposition | Appears in dropdown immediately. Existing activities with old disposition labels unaffected. |
| Config: remove disposition | Removed from dropdown. Existing activities retain their stored disposition text. |
| Search for COR-{id} | Search page matches `thread_id` when input matches `COR-\d+` pattern. Returns all activities in the thread. |
