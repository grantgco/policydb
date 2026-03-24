# Policy Pulse — Design Spec

## Context

GitHub issue #33. The Account Pulse on the client detail page provides a quick health snapshot for an entire client account. Users working through the renewal pipeline need the same at-a-glance triage capability at the **individual policy** level — is this policy on track, what's slipping, what needs my attention right now?

Primary use case: **renewal triage** — scanning policies in the pipeline and quickly assessing health, blockers, and next actions without digging through multiple tabs.

## Design Decision

**Dense single-column layout** as a new 5th tab on the policy edit page, alongside Details, Activity, Contacts, and Workflow. Lazy-loaded via HTMX like the other tabs.

## Sections (top to bottom)

### 1. Header + Review Badge

- Policy Pulse title + client name link + `cn_number` + ref tag pill (uses `build_ref_tag()` + `copyRefTag()`)
- "Reviewed [date]" badge or "Not yet reviewed" (amber) — uses existing `last_reviewed_at` column
- "Mark Reviewed" button → POST to existing `/review/policies/{uid}/reviewed` endpoint

### 2. Metrics Bar

Four equal-width cards in a horizontal row:

| Card | Data Source | Color Logic |
|------|-------------|-------------|
| **Readiness** score (0-100) | `_attach_readiness_score()` — requires `id` key on dict | Green ≥75, Amber 50-74, Red <50 |
| **Days to renewal** | Computed inline: `(date.fromisoformat(p["expiration_date"]) - date.today()).days` | Red ≤30d, Amber ≤90d, Blue >90d |
| **Premium** + rate change % | `premium` from policy; rate change computed: `(premium - prior_premium) / prior_premium` if `prior_premium > 0` else `None` | Green if positive, Red if negative |
| **Effort** total hours | `get_policy_total_hours(conn, p["id"])` from `queries.py` | Neutral color |

### 3. Needs Attention

Color-coded action items, ordered by severity. Three sources merged:

| Source | Badge | Border Color |
|--------|-------|-------------|
| Overdue follow-ups (from `activity_log` + `policies.follow_up_date`) | `OVERDUE` (red) | Red |
| Drifting/compressed/at-risk/critical timeline milestones (from `policy_timeline`) | Health state label (amber/red) | Amber or Red |
| Waiting-on items (from `policy_timeline` WHERE `accountability = 'waiting_external'`) | `WAITING` (indigo) | Indigo |

Each row shows: description, context line (days overdue / days behind / days waiting), severity badge.

**Empty state:** Green "All clear" message when no items need attention.

### 4. Milestones

- **Progress bar:** Segmented horizontal bar, one segment per milestone in the policy's `milestone_profile`. Colors: green (completed), amber (drifting/compressed), red (at_risk/critical), gray (pending).
- **Count:** "X of Y complete" right-aligned.
- **Next milestone detail:** Card showing milestone name, ideal date, projected date, health badge. Only shown if there are incomplete milestones.

Data: `get_policy_timeline(conn, policy_uid)` + `_build_checklist(conn, policy_uid)`.

**Empty state:** If no `milestone_profile` assigned, show "No milestone profile — assign one in Workflow tab" with a link.

### 5. Key Contacts

Single card, two columns separated by a vertical divider:

| Column | Data Source |
|--------|-------------|
| **Placement colleague** — name, email, phone | From `contact_policy_assignments` WHERE `is_placement_colleague = 1` (canonical source per unified contacts refactor). Falls back to `placement_colleague` text field on policy if no assignment exists. |
| **Underwriter** — name, email, phone | From `contact_policy_assignments` filtered by underwriter role. Falls back to `underwriter_name` / `underwriter_contact` text fields on policy if no assignment exists. |

Email/phone are clickable (`mailto:` / `tel:`).

**Empty state:** "No [role] assigned" in muted text for either column.

### 6. Recent Activity

Last 5 activities for this policy (from `activity_log WHERE policy_id = ?`, ordered by `activity_date DESC`).

Each row: activity type badge (colored) | subject (truncated) | date | duration hours.

Data: Same query pattern as policy Activity tab, limited to 5.

**Empty state:** "No recent activity" in muted text.

### 7. Working Notes

- Scratchpad content from `policy_scratchpad WHERE policy_uid = ?`
- Clipped to 3 lines with CSS `-webkit-line-clamp`
- "edit →" link navigates to the Workflow tab (or opens scratchpad editor)

**Empty state:** "No working notes" with "add →" link.

### 8. Quick Log

Compact activity logging form:

- **Row 1:** Activity type (**combobox**, not `<select>` — per CLAUDE.md) | Subject (text) | Hours (number, step 0.1)
- **Row 2:** Follow-up date (optional date input) | "Log Activity" button
- **Hidden inputs:** `client_id` + `policy_id` for proper activity linking

POST to existing activity creation endpoint. On success: refresh `#pulse-recent-activity` and `#pulse-needs-attention` sections via `hx-swap-oob`.

**Template context required:** `activity_types=cfg.get("activity_types")` for the combobox options.

## Route & Template

- **Route:** `GET /policies/{uid}/tab/pulse` in `policies.py`
- **Template:** `src/policydb/web/templates/policies/_tab_pulse.html`
- **Tab registration:** Add "Pulse" as 5th tab in `edit.html` tab bar, lazy-loaded via `hx-get`

## Data Assembly (Route Handler)

```python
@router.get("/{uid}/tab/pulse")
async def policy_tab_pulse(uid: str, request: Request):
    conn = get_db()
    p, client_info = _policy_base(conn, uid)
    _today = date.today()

    # 1. Readiness score — needs dict with "id" key
    rows = [dict(p)]
    _attach_readiness_score(conn, rows)
    readiness = rows[0]

    # 2. Computed metrics (not available from _policy_base directly)
    days_to_renewal = (date.fromisoformat(p["expiration_date"]) - _today).days if p.get("expiration_date") else None
    rate_change = round((p["premium"] - p["prior_premium"]) / p["prior_premium"], 4) if p.get("prior_premium") else None

    # 3. Effort hours — reuse existing helper
    effort = get_policy_total_hours(conn, p["id"])

    # 4. Overdue follow-ups
    overdue_activities = conn.execute(
        """SELECT subject, follow_up_date,
           CAST(julianday('now') - julianday(follow_up_date) AS INTEGER) AS days_overdue
           FROM activity_log WHERE policy_id=? AND follow_up_done=0
           AND follow_up_date < ? ORDER BY follow_up_date""",
        (p["id"], _today.isoformat()),
    ).fetchall()
    overdue_policy_fu = None
    if p.get("follow_up_date") and p["follow_up_date"] < _today.isoformat():
        overdue_policy_fu = {
            "subject": "Policy follow-up",
            "follow_up_date": p["follow_up_date"],
            "days_overdue": (_today - date.fromisoformat(p["follow_up_date"])).days,
        }

    # 5. Timeline milestones
    timeline = get_policy_timeline(conn, uid)
    checklist = _build_checklist(conn, uid)

    # 6. Attention items (merge overdue + unhealthy milestones + waiting)
    # All date computations happen here in Python, not in Jinja2
    attention_items = _build_pulse_attention_items(
        overdue_activities, overdue_policy_fu, timeline, _today
    )

    # 7. Contacts — canonical source is contact_policy_assignments
    contacts = get_policy_contacts(conn, p["id"])
    placement = next((c for c in contacts if c.get("is_placement_colleague")), None)
    underwriter = next((c for c in contacts if c.get("role", "").lower() == "underwriter"), None)
    # Fallback to text fields if no assignment
    if not placement and p.get("placement_colleague"):
        placement = {"name": p["placement_colleague"], "email": p.get("placement_colleague_email")}
    if not underwriter and p.get("underwriter_name"):
        underwriter = {"name": p["underwriter_name"], "email": p.get("underwriter_contact")}

    # 8. Recent activity (last 5)
    recent = conn.execute(
        "SELECT * FROM activity_log WHERE policy_id=? ORDER BY activity_date DESC LIMIT 5",
        (p["id"],),
    ).fetchall()

    # 9. Working notes
    scratchpad = conn.execute(
        "SELECT content, updated_at FROM policy_scratchpad WHERE policy_uid=?",
        (uid,),
    ).fetchone()

    return templates.TemplateResponse("policies/_tab_pulse.html", {
        "request": request, "policy": p, "client": client_info,
        "readiness": readiness, "days_to_renewal": days_to_renewal,
        "rate_change": rate_change, "effort": effort,
        "attention_items": attention_items,
        "timeline": timeline, "checklist": checklist,
        "placement": placement, "underwriter": underwriter,
        "recent": recent, "scratchpad": scratchpad,
        "activity_types": cfg.get("activity_types"),
        "today": _today.isoformat(),
    })
```

## Helper: `_build_pulse_attention_items()` (in `policies.py`)

All date difference computations happen in Python (not Jinja2). Merges three sources into a single sorted list:

1. **Overdue follow-ups** → `{type: "overdue", text, days_overdue, date}`
2. **Unhealthy milestones** (health != "on_track" and not completed) → `{type: "milestone", text, health, days_behind, date}` — `days_behind` computed as `(projected_date - ideal_date).days`
3. **Waiting items** (accountability = "waiting_external") → `{type: "waiting", text, waiting_on, days_waiting, date}` — `days_waiting` computed from timeline row dates

Sort order: overdue first (by days_overdue desc), then milestones (by health severity), then waiting (by days_waiting desc).

## Reused Existing Code

| Function | Location | Purpose |
|----------|----------|---------|
| `_policy_base()` | `policies.py` | Load policy + client info |
| `_attach_readiness_score()` | `policies.py` | Compute readiness score (needs `id` key on dict) |
| `get_policy_timeline()` | `timeline_engine.py` | Timeline milestones |
| `_build_checklist()` | `policies.py` | Checklist items |
| `get_policy_contacts()` | `policies.py` or queries | Policy contacts via junction table |
| `get_policy_total_hours()` | `queries.py` | Total effort hours for a policy |
| `_attach_milestone_progress()` | `policies.py` | Milestone done/total |
| `build_ref_tag()` | `utils.py` | Ref tag for header pill |

## OOB Element IDs

The template must use these stable IDs for `hx-swap-oob` refresh after Quick Log submit:

- `id="pulse-needs-attention"` — the Needs Attention section container
- `id="pulse-recent-activity"` — the Recent Activity section container
- `id="pulse-metrics"` — the Metrics Bar (to update effort hours after logging)

## Verification

1. Start server (`policydb serve`), navigate to a policy with active data
2. Click the Pulse tab — verify lazy load works
3. Verify metrics bar shows correct values (cross-reference with Details + Workflow tabs)
4. Verify Needs Attention items match overdue follow-ups + timeline health
5. Verify milestones progress bar matches Workflow tab checklist
6. Verify contacts match Contacts tab data
7. Log an activity via Quick Log — confirm Recent Activity + Needs Attention refresh
8. Click Mark Reviewed — confirm badge updates
9. Test empty states: policy with no activities, no milestones, no contacts, no scratchpad
10. Test responsive: verify single-column stacks correctly on narrow viewports
