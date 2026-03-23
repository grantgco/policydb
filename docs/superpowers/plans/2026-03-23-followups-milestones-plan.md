# Follow-ups Urgency Tiers + Timeline Milestone Activation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken "Act Now" bucket with proportional urgency tiers, activate the dormant timeline engine via suggest+confirm profiles, and auto-surface overdue milestones in the follow-up tiers.

**Architecture:** Refactor `_followups_ctx()` to split items into 8 buckets (triage/today/overdue/stale/nudge/prep/watching/scheduled) using date math and disposition mapping. Wire the existing `timeline_engine.py` with a `suggest_profile()` function and review-screen acceptance flow. Inject overdue milestone items into urgency tiers as virtual follow-up rows.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, existing timeline_engine.py + config.py infrastructure

**Spec:** `docs/superpowers/specs/2026-03-23-followups-milestones-redesign.md`

**Important:** There are TWO `_followups_ctx()` functions in the codebase:
- `action_center.py:51` — serves the Action Center follow-ups tab (THIS is what we refactor)
- `activities.py:713` — serves Plan Week, legacy `/followups/results`, and renewal pipeline pages. Uses old overdue/upcoming bucketing. **Leave this alone** — those pages are separate from the Action Center redesign.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/policydb/config.py` | Modify | Verify `stale_threshold_days` exists (line 19) |
| `src/policydb/web/routes/settings.py` | Modify | Add `stale_threshold_days` numeric config to Settings UI |
| `src/policydb/web/templates/settings/page.html` | Modify | Render numeric config input |
| `src/policydb/web/routes/action_center.py` | Modify | Refactor `_followups_ctx()` — 8 buckets, nudge fix, milestone injection |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Rewrite | 8 sections with urgency colors, triage UX, milestone variant |
| `src/policydb/web/templates/action_center/_followups.html` | Modify | Summary bar, updated filter pills |
| `src/policydb/timeline_engine.py` | Modify | Add `suggest_profile()` function |
| `src/policydb/web/routes/review.py` | Modify | Suggestion badge, bulk accept endpoint |
| `src/policydb/web/templates/review/_policy_row.html` | Modify | Suggestion badge + Accept button |
| `src/policydb/web/routes/policies.py` | Modify | Regen triggers on date/archive changes |
| `tests/test_followups_tiers.py` | Create | Tests for bucketing logic, triage rules, date tiers |
| `tests/test_nudge_escalation.py` | Create | Tests for policy+disposition nudge counting |
| `tests/test_suggest_profile.py` | Create | Tests for profile suggestion + bulk accept |
| `tests/test_milestone_injection.py` | Create | Tests for overdue milestones in tiers |

---

### Task 1: Settings UI — stale_threshold_days

Add `stale_threshold_days` as a numeric config option in the Settings page.

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings/page.html`

- [ ] **Step 1: Read current settings.py and page.html to find where numeric config inputs are rendered**

Check if there are existing numeric config inputs (like `renewal_window_days`, `activity_cluster_days`) to follow the pattern.

- [ ] **Step 2: Add POST endpoint for stale_threshold_days**

In `src/policydb/web/routes/settings.py`, add endpoint:

```python
@router.post("/config/stale-threshold")
def update_stale_threshold(request: Request, value: int = Form(...), conn=Depends(get_db)):
    import policydb.config as cfg
    cfg.set("stale_threshold_days", max(1, min(value, 90)))
    cfg.save_config()
    return RedirectResponse("/settings", status_code=303)
```

- [ ] **Step 3: Add input field to Settings page template**

In `settings/page.html`, in the appropriate section (near other numeric configs), add:

```html
<form method="post" action="/settings/config/stale-threshold" class="flex items-center gap-3">
  <label class="text-sm font-medium text-gray-700 whitespace-nowrap">Stale threshold (days)</label>
  <input type="number" name="value" value="{{ cfg.get('stale_threshold_days', 14) }}"
         min="1" max="90" class="w-20 rounded border-gray-300 text-sm px-2 py-1">
  <button type="submit" class="text-sm text-blue-600 hover:underline">Save</button>
  <span class="text-xs text-gray-400">Follow-ups older than this are "Stale" (red)</span>
</form>
```

- [ ] **Step 4: Start server and verify Settings page renders the input**

Run: `policydb serve` → navigate to `/settings` → verify the "Stale threshold" input appears with value 14.

- [ ] **Step 5: Test changing the value**

Change to 7, click Save, verify page reloads with 7. Check `~/.policydb/config.yaml` has `stale_threshold_days: 7`.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings/page.html
git commit -m "feat: add stale_threshold_days to Settings UI"
```

---

### Task 2: Refactor _followups_ctx — 8-bucket bucketing

Replace the single `act_now` bucket with triage/today/overdue/stale, fix future `my_action` routing, and update template context.

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` (lines 51-177)
- Create: `tests/test_followups_tiers.py`

- [ ] **Step 1: Write failing tests for triage detection**

```python
# tests/test_followups_tiers.py
"""Tests for follow-ups urgency tier bucketing logic."""
import pytest
from datetime import date, timedelta


def test_activity_without_disposition_goes_to_triage():
    """Activity follow-ups with no disposition go to Triage, not date tiers."""
    item = {
        "source": "activity",
        "disposition": None,
        "follow_up_date": date.today().isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "triage"


def test_activity_with_disposition_skips_triage():
    """Activity follow-ups WITH disposition go to date tiers."""
    item = {
        "source": "activity",
        "disposition": "Left VM",
        "follow_up_date": date.today().isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket != "triage"


def test_policy_reminder_skips_triage():
    """Policy reminders skip triage even without disposition."""
    item = {
        "source": "policy",
        "disposition": None,
        "follow_up_date": date.today().isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "today"


def test_today_bucket():
    """my_action items due today go to Today."""
    item = {
        "source": "activity",
        "disposition": "Connected",
        "follow_up_date": date.today().isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "today"


def test_overdue_bucket():
    """my_action items 1-14 days overdue go to Overdue."""
    item = {
        "source": "activity",
        "disposition": "No Answer",
        "follow_up_date": (date.today() - timedelta(days=5)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "overdue"


def test_stale_bucket():
    """my_action items 14+ days overdue go to Stale."""
    item = {
        "source": "activity",
        "disposition": "No Answer",
        "follow_up_date": (date.today() - timedelta(days=20)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "stale"


def test_future_my_action_goes_to_watching():
    """Future my_action items go to Watching."""
    item = {
        "source": "activity",
        "disposition": "Connected",
        "follow_up_date": (date.today() + timedelta(days=3)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "watching"


def test_waiting_external_overdue_goes_to_nudge():
    """waiting_external items due/overdue go to Nudge Due."""
    item = {
        "source": "activity",
        "disposition": "Waiting on Client",
        "follow_up_date": (date.today() - timedelta(days=2)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "nudge_due"


def test_waiting_external_future_goes_to_watching():
    """waiting_external items with future date go to Watching."""
    item = {
        "source": "activity",
        "disposition": "Waiting on Client",
        "follow_up_date": (date.today() + timedelta(days=5)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "watching"


def test_scheduled_goes_to_scheduled():
    """scheduled items go to Scheduled."""
    item = {
        "source": "activity",
        "disposition": "Meeting Scheduled",
        "follow_up_date": (date.today() + timedelta(days=1)).isoformat(),
    }
    bucket = _classify_item(item, date.today(), stale_threshold=14, dispositions=DISPOSITIONS)
    assert bucket == "scheduled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_followups_tiers.py -v`
Expected: FAIL — `_classify_item` not defined

- [ ] **Step 3: Extract `_classify_item()` helper in action_center.py**

Add a pure function at the top of `action_center.py` (before routes) that encapsulates the bucketing logic. This is extracted from the existing `_followups_ctx` so it can be unit tested:

```python
def _classify_item(item: dict, today: date, stale_threshold: int, dispositions: list[dict]) -> str:
    """Classify a follow-up item into a bucket.

    Returns one of: triage, today, overdue, stale, nudge_due, watching, scheduled
    """
    source = item.get("source", "activity")
    disposition = item.get("disposition") or ""
    fu_date_str = item.get("follow_up_date", "")

    # Step 1: Triage — activity items with no disposition
    if source == "activity" and not disposition.strip():
        return "triage"

    # Step 2: Map disposition → accountability
    accountability = "my_action"  # default
    for d in dispositions:
        if d.get("label", "").lower() == disposition.lower():
            accountability = d.get("accountability", "my_action")
            break

    # Step 3: Scheduled
    if accountability == "scheduled":
        return "scheduled"

    # Parse date
    try:
        fu_date = date.fromisoformat(fu_date_str)
    except (ValueError, TypeError):
        return "triage"  # bad date → triage

    days_overdue = (today - fu_date).days

    # Step 4: waiting_external
    if accountability == "waiting_external":
        return "nudge_due" if days_overdue >= 0 else "watching"

    # Step 5: my_action date tiers
    if days_overdue == 0:
        return "today"
    elif days_overdue > stale_threshold:
        return "stale"
    elif days_overdue > 0:
        return "overdue"
    else:
        return "watching"  # future my_action → watching with "my turn" badge
```

- [ ] **Step 4: Wire `_classify_item` into tests (import) and run tests**

Update tests to import from `policydb.web.routes.action_center`. Add `DISPOSITIONS` fixture from config.

Run: `pytest tests/test_followups_tiers.py -v`
Expected: All PASS

- [ ] **Step 5: Refactor `_followups_ctx()` to use `_classify_item`**

Replace the existing bucketing logic in `_followups_ctx()` (lines ~60-110) with a loop that calls `_classify_item()` for each item and appends to the appropriate list:

```python
def _followups_ctx(conn, today_str, cfg):
    today = date.fromisoformat(today_str)
    stale_threshold = cfg.get("stale_threshold_days", 14)
    dispositions = cfg.get("follow_up_dispositions", [])

    overdue_items, upcoming_items = get_all_followups(conn)
    all_items = overdue_items + upcoming_items

    buckets = {
        "triage": [], "today": [], "overdue": [], "stale": [],
        "nudge_due": [], "watching": [], "scheduled": [],
    }

    for item in all_items:
        bucket = _classify_item(item, today, stale_threshold, dispositions)
        # Add computed fields
        fu_date = item.get("follow_up_date", "")
        try:
            d = date.fromisoformat(fu_date)
            item["days_overdue"] = (today - d).days
        except (ValueError, TypeError):
            item["days_overdue"] = 0
        # Mark future my_action items
        if bucket == "watching":
            disp = (item.get("disposition") or "").lower()
            acct = "my_action"
            for dd in dispositions:
                if dd.get("label", "").lower() == disp:
                    acct = dd.get("accountability", "my_action")
                    break
            item["is_my_turn"] = (acct == "my_action")
        buckets[bucket].append(item)

    # ... keep existing nudge escalation, prep_coming query, etc.
    # (nudge escalation will be replaced in Task 3)

    return buckets
```

- [ ] **Step 6: Update the template context in the followups endpoint**

In the `GET /action-center/followups` route, update the template context to pass the new bucket names instead of `act_now`:

```python
ctx = _followups_ctx(conn, today_str, cfg)
return templates.TemplateResponse("action_center/_followups.html", {
    "request": request,
    "triage": ctx["triage"],
    "today": ctx["today"],
    "overdue": ctx["overdue"],
    "stale": ctx["stale"],
    "nudge_due": ctx["nudge_due"],
    "prep_coming": ctx.get("prep_coming", []),
    "watching": ctx["watching"],
    "scheduled": ctx["scheduled"],
    # ... keep existing context vars (renewal_statuses, etc.)
})
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/test_followups_tiers.py -v && pytest tests/ -x --timeout=30`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_followups_tiers.py
git commit -m "refactor: split act_now into urgency tiers (triage/today/overdue/stale)"
```

---

### Task 3: Replace nudge escalation — policy+disposition counting

Replace broken `thread_id` counting with policy+disposition-based nudge counting.

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` (lines ~110-123)
- Create: `tests/test_nudge_escalation.py`

- [ ] **Step 1: Write failing test for nudge counting**

```python
# tests/test_nudge_escalation.py
"""Tests for nudge escalation counting by policy+disposition."""


def test_single_nudge_is_normal(test_db):
    """One waiting_external activity = normal tier."""
    # Insert policy + one waiting_external activity
    # Call _compute_nudge_count(conn, policy_uid)
    # Assert count == 1, tier == "normal"


def test_two_nudges_is_elevated(test_db):
    """Two waiting_external activities for same policy = elevated."""
    # Assert count == 2, tier == "elevated"


def test_three_nudges_is_urgent(test_db):
    """Three+ waiting_external activities for same policy = urgent."""
    # Assert count >= 3, tier == "urgent"


def test_nudge_count_ignores_my_action(test_db):
    """my_action activities on same policy don't count toward nudge."""
    # Insert mix of waiting_external and my_action
    # Assert only waiting_external counted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nudge_escalation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_compute_nudge_tier()` function**

Replace lines 110-123 in `action_center.py`:

```python
def _compute_nudge_tier(conn, policy_uid: str, dispositions: list[dict]) -> tuple[int, str]:
    """Count waiting_external activities for a policy in last 90 days.
    Returns (count, tier) where tier is normal/elevated/urgent.
    """
    waiting_labels = [
        d["label"] for d in dispositions
        if d.get("accountability") == "waiting_external"
    ]
    if not waiting_labels or not policy_uid:
        return 1, "normal"

    placeholders = ",".join("?" * len(waiting_labels))
    count = conn.execute(
        f"""SELECT COUNT(*) FROM activity_log
            WHERE policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
              AND disposition IN ({placeholders})
              AND activity_date >= date('now', '-90 days')""",
        [policy_uid] + waiting_labels,
    ).fetchone()[0]

    count = max(count, 1)
    tier = "urgent" if count >= 3 else "elevated" if count >= 2 else "normal"
    return count, tier
```

- [ ] **Step 4: Wire into `_followups_ctx` nudge_due items**

Replace the old thread_id counting block with:

```python
for item in buckets["nudge_due"]:
    count, tier = _compute_nudge_tier(conn, item.get("policy_uid"), dispositions)
    item["nudge_count"] = count
    item["escalation_tier"] = tier
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_nudge_escalation.py tests/test_followups_tiers.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_nudge_escalation.py
git commit -m "fix: replace broken thread_id nudge counting with policy+disposition"
```

---

### Task 4: Follow-up section templates — 8 sections with urgency colors

Rewrite `_followup_sections.html` with new sections and triage UX.

**Files:**
- Rewrite: `src/policydb/web/templates/action_center/_followup_sections.html`
- Modify: `src/policydb/web/templates/action_center/_followups.html`

- [ ] **Step 1: Read the existing `fu_row` macro and section HTML to understand structure**

Read `_followup_sections.html` fully. The `fu_row` macro (lines 6-124) renders each item. Sections below the macro render each bucket.

- [ ] **Step 2: Add section color config macro at top of _followup_sections.html**

Define colors per section to avoid repetition:

```jinja2
{# Section color definitions #}
{% set section_styles = {
  'triage':   {'border': 'border-gray-300', 'bg': 'bg-gray-50',    'dot': 'text-gray-400',  'badge_bg': 'bg-gray-500',   'item_border': 'border-dashed border-gray-300', 'date_color': 'text-gray-500'},
  'today':    {'border': 'border-blue-500', 'bg': 'bg-blue-50',    'dot': 'text-blue-500',  'badge_bg': 'bg-blue-800',   'item_border': 'border-blue-200', 'date_color': 'text-blue-700'},
  'overdue':  {'border': 'border-amber-500','bg': 'bg-amber-50',   'dot': 'text-amber-500', 'badge_bg': 'bg-amber-600',  'item_border': 'border-amber-200','date_color': 'text-amber-700'},
  'stale':    {'border': 'border-red-500',  'bg': 'bg-red-50',     'dot': 'text-red-500',   'badge_bg': 'bg-red-600',    'item_border': 'border-red-200',  'date_color': 'text-red-700'},
  'nudge_due':{'border': 'border-indigo-500','bg': 'bg-indigo-50', 'dot': 'text-indigo-500','badge_bg': 'bg-indigo-600', 'item_border': 'border-indigo-200','date_color': 'text-indigo-700'},
  'prep':     {'border': 'border-purple-500','bg': 'bg-purple-50', 'dot': 'text-purple-500','badge_bg': 'bg-purple-600', 'item_border': 'border-purple-200','date_color': 'text-purple-700'},
  'watching': {'border': 'border-gray-300', 'bg': 'bg-white',      'dot': 'text-gray-400',  'badge_bg': 'bg-gray-400',   'item_border': 'border-gray-200', 'date_color': 'text-gray-500'},
  'scheduled':{'border': 'border-indigo-400','bg': 'bg-white',     'dot': 'text-indigo-400','badge_bg': 'bg-indigo-400', 'item_border': 'border-indigo-200','date_color': 'text-indigo-500'},
} %}
```

- [ ] **Step 3: Rewrite sections — replace act_now with triage + today + overdue + stale**

Replace the `act_now` section block with four new sections. Each section follows the pattern:

```jinja2
{# ══════ TRIAGE ══════ #}
{% set s = section_styles.triage %}
{% if triage %}
<div class="border-l-4 {{ s.border }} {{ s.bg }} px-4 py-3" data-section="triage">
  <div class="flex items-center gap-2 mb-2">
    <span class="text-white text-xs font-semibold px-2.5 py-0.5 rounded-full {{ s.badge_bg }}">Triage</span>
    <span class="text-gray-400 text-xs">{{ triage | length }} items need a disposition</span>
  </div>
  {% for item in triage %}
    {{ fu_row(item, s, is_triage=true) }}
  {% endfor %}
</div>
{% endif %}
```

Repeat for today, overdue, stale, nudge_due, prep_coming, watching (collapsed), scheduled (collapsed).

- [ ] **Step 4: Update the `fu_row` macro to accept section styles and triage/milestone variants**

Add parameters to the macro:
- `s` — section style dict (colors)
- `is_triage` — show "Set disposition →" button instead of "Follow Up"
- `is_milestone` — show ◆ icon, milestone name, Complete button

For triage items, the row uses dashed border and a disposition pill selector.
For milestone items, the row shows ◆ icon and a Complete button alongside Follow Up.

- [ ] **Step 5: Add Watching section "my turn" badge**

In the Watching section loop, check `item.is_my_turn`:

```jinja2
{% if item.is_my_turn %}
  <span class="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">my turn</span>
{% endif %}
```

- [ ] **Step 6: Update _followups.html — summary bar**

At the top of `_followups.html`, before the filter bar, add:

```jinja2
<div class="flex gap-4 px-4 py-2 bg-white border-b text-sm text-gray-500">
  {% if today %}<span><strong class="text-blue-700">{{ today|length }}</strong> Today</span>{% endif %}
  {% if overdue %}<span><strong class="text-amber-600">{{ overdue|length }}</strong> Overdue</span>{% endif %}
  {% if stale %}<span><strong class="text-red-600">{{ stale|length }}</strong> Stale</span>{% endif %}
  {% if triage %}<span><strong class="text-gray-500">{{ triage|length }}</strong> Triage</span>{% endif %}
  {% if prep_coming %}<span class="ml-auto"><strong class="text-purple-600">{{ prep_coming|length }}</strong> Prep</span>{% endif %}
  {% if nudge_due %}<span><strong class="text-indigo-600">{{ nudge_due|length }}</strong> Nudge</span>{% endif %}
</div>
```

- [ ] **Step 7: Update filter pills**

Replace the "Act Now" pill in the filter bar with separate pills:
- Triage, Today, Overdue, Stale, Nudge Due, Prep, Watching, Scheduled

Update the `filterFuStatus()` JS function to match the new `data-section` attributes.

- [ ] **Step 8: Start server and visually verify**

Run: `policydb serve` → navigate to `/action-center`
- Verify: sections render with correct colors
- Verify: triage items show dashed border + "Set disposition →"
- Verify: summary bar counts are correct
- Verify: filter pills toggle visibility
- Verify: Watching and Scheduled are collapsed by default
- Take screenshots for QA

- [ ] **Step 9: Commit**

```bash
git add src/policydb/web/templates/action_center/_followup_sections.html
git add src/policydb/web/templates/action_center/_followups.html
git commit -m "feat: urgency tier UI — triage/today/overdue/stale sections with color hierarchy"
```

---

### Task 5: Triage disposition flow

Wire the "Set disposition" action on triage items so setting a disposition moves the item to the correct bucket.

**Files:**
- Modify: `src/policydb/web/templates/action_center/_followup_sections.html`
- Modify: `src/policydb/web/routes/action_center.py` or `activities.py`

- [ ] **Step 1: Implement triage inline disposition selector**

In the triage row variant, when "Set disposition →" is clicked, reveal inline disposition pills (same pattern as the existing fu_row follow-up form). On pill click, POST to the existing `/activities/{id}/followup` endpoint with the selected disposition.

The key difference: triage items just need a disposition set (no re-diary required). The existing follow-up endpoint already handles setting disposition on an activity.

- [ ] **Step 2: Ensure HTMX swap moves the item out of Triage**

After setting disposition, the response should return the updated item HTML for the correct section. Use `hx-swap-oob` to remove the item from triage and insert it into the right section, OR trigger a full tab reload:

```html
hx-post="/activities/{{ item.id }}/set-disposition"
hx-target="#followup-content"
hx-swap="innerHTML"
```

Simplest approach: retrigger the entire followups tab content via HTMX after disposition set.

- [ ] **Step 3: Add `POST /activities/{id}/set-disposition` endpoint if needed**

If the existing `/activities/{id}/followup` endpoint is too heavy (creates a new activity), add a lightweight endpoint in `action_center.py` (prefix `/action-center`):

```python
@router.post("/set-disposition/{activity_id}")
def set_disposition(request: Request, activity_id: int, disposition: str = Form(""), conn=Depends(get_db)):
    conn.execute("UPDATE activity_log SET disposition = ? WHERE id = ?", (disposition, activity_id))
    conn.commit()
    # Re-render the full followups tab content so the item moves to the right section
    ctx = _followups_ctx(conn, ...)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)
```

Full URL: `POST /action-center/set-disposition/{activity_id}`. Lives on the `action_center` router.

- [ ] **Step 4: Test the triage flow end-to-end**

1. Create a follow-up activity with no disposition
2. Verify it appears in Triage section
3. Click "Set disposition →", select "Left VM"
4. Verify item moves to Today/Overdue/Stale (depending on date) or Nudge Due

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py
git add src/policydb/web/templates/action_center/_followup_sections.html
git commit -m "feat: triage disposition flow — set disposition moves item to correct bucket"
```

---

### Task 6: suggest_profile() + Review Screen Integration

Add profile suggestion function and wire it into the review screen with suggestion badges and bulk accept.

**Files:**
- Modify: `src/policydb/timeline_engine.py`
- Modify: `src/policydb/web/routes/review.py`
- Modify: `src/policydb/web/templates/review/_policy_row.html`
- Create: `tests/test_suggest_profile.py`

- [ ] **Step 1: Write failing tests for suggest_profile**

```python
# tests/test_suggest_profile.py
"""Tests for milestone profile suggestion logic."""


def test_high_premium_suggests_full_renewal(test_db):
    """Policy with premium >= 100k gets Full Renewal suggestion."""
    # Insert policy with premium 150000
    suggestions = suggest_profile(conn)
    assert suggestions[uid] == "Full Renewal"


def test_mid_premium_suggests_standard(test_db):
    """Policy with premium 25k-100k gets Standard Renewal."""
    # Insert policy with premium 50000
    suggestions = suggest_profile(conn)
    assert suggestions[uid] == "Standard Renewal"


def test_low_premium_suggests_simple(test_db):
    """Policy with premium < 25k gets Simple Renewal."""
    # Insert policy with premium 10000
    suggestions = suggest_profile(conn)
    assert suggestions[uid] == "Simple Renewal"


def test_already_assigned_excluded(test_db):
    """Policies with existing profile are not in suggestions."""
    # Insert policy with milestone_profile = "Full Renewal"
    suggestions = suggest_profile(conn)
    assert uid not in suggestions


def test_opportunities_excluded(test_db):
    """Opportunity policies are not suggested."""
    # Insert policy with is_opportunity = 1
    suggestions = suggest_profile(conn)
    assert uid not in suggestions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_suggest_profile.py -v`
Expected: FAIL — `suggest_profile` not defined

- [ ] **Step 3: Implement `suggest_profile()` in timeline_engine.py**

```python
def suggest_profile(conn, policy_uid=None):
    """Return {policy_uid: suggested_profile_name} for policies without a profile.

    Uses milestone_profile_rules from config to map premium thresholds to profiles.
    Excludes opportunities, archived, and child policies.
    """
    import policydb.config as cfg
    rules = cfg.get("milestone_profile_rules", [])
    default_profile = "Simple Renewal"

    where = """
        WHERE (milestone_profile IS NULL OR milestone_profile = '')
          AND (is_opportunity = 0 OR is_opportunity IS NULL)
          AND (archived = 0 OR archived IS NULL)
          AND (program_id IS NULL OR program_id = '')
    """
    params = []
    if policy_uid:
        where += " AND policy_uid = ?"
        params.append(policy_uid)

    rows = conn.execute(
        f"SELECT policy_uid, premium FROM policies {where}", params
    ).fetchall()

    suggestions = {}
    for row in rows:
        premium = row["premium"] or 0
        profile = default_profile
        for rule in rules:
            if premium >= rule.get("min_premium", 0):
                profile = rule.get("profile", default_profile)
                break  # rules are ordered high→low
        suggestions[row["policy_uid"]] = profile

    return suggestions
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_suggest_profile.py -v`
Expected: All PASS

- [ ] **Step 5: Add suggestion badge to review page template**

In `review/_policy_row.html`, next to the profile `<select>`, show a suggestion badge if the policy has no profile:

```jinja2
{% if not row.milestone_profile and row.policy_uid in suggestions %}
  <span class="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">
    Suggested: {{ suggestions[row.policy_uid] }}
  </span>
  <button hx-post="/review/policies/{{ row.policy_uid }}/accept-profile"
          hx-vals='{"profile": "{{ suggestions[row.policy_uid] }}"}'
          hx-target="#review-row-{{ row.policy_uid }}"
          hx-swap="outerHTML"
          class="text-xs text-green-600 hover:underline ml-1">Accept</button>
{% endif %}
```

- [ ] **Step 6: Add accept-profile endpoint in review.py**

```python
@router.post("/review/policies/{uid}/accept-profile")
def accept_profile(request: Request, uid: str, profile: str = Form(""), conn=Depends(get_db)):
    conn.execute("UPDATE policies SET milestone_profile = ? WHERE policy_uid = ?", (profile, uid))
    conn.commit()
    generate_policy_timelines(conn, policy_uid=uid)
    # Return updated row HTML — same pattern as existing profile change endpoint
    row = conn.execute("SELECT * FROM v_review_queue WHERE policy_uid = ?", (uid,)).fetchone()
    if not row:
        return HTMLResponse("")
    suggestions = suggest_profile(conn)
    return templates.TemplateResponse("review/_policy_row.html", {
        "request": request,
        "row": dict(row),
        "suggestions": suggestions,
        "milestone_profiles": cfg.get("milestone_profiles", {}),
    })
```

- [ ] **Step 7: Add bulk accept endpoint**

```python
@router.post("/review/accept-all-profiles")
def accept_all_profiles(request: Request, conn=Depends(get_db)):
    suggestions = suggest_profile(conn)
    for uid, profile in suggestions.items():
        conn.execute("UPDATE policies SET milestone_profile = ? WHERE policy_uid = ?", (profile, uid))
    conn.commit()
    generate_policy_timelines(conn)
    return RedirectResponse("/review", status_code=303)
```

- [ ] **Step 8: Add "Accept All Suggestions" button to review page**

In the review page template header area:

```jinja2
{% if unassigned_count > 0 %}
<button hx-post="/review/accept-all-profiles"
        hx-confirm="Accept suggested profiles for {{ unassigned_count }} policies?"
        class="text-sm bg-green-600 text-white px-3 py-1 rounded hover:bg-green-700">
  Accept All Suggestions ({{ unassigned_count }})
</button>
{% endif %}
```

- [ ] **Step 9: Pass suggestions to review template context**

In the review page route, compute suggestions and pass to template:

```python
from policydb.timeline_engine import suggest_profile
suggestions = suggest_profile(conn)
# Add to template context: suggestions=suggestions, unassigned_count=len(suggestions)
```

- [ ] **Step 10: Test review screen integration**

1. Start server → navigate to `/review`
2. Verify policies without profiles show suggestion badges
3. Click Accept on one → verify profile assigned and timeline generated
4. Click "Accept All Suggestions" → verify all assigned
5. Navigate to Action Center → verify Prep Coming Up has items (if any milestones have prep_alert_date <= today)

- [ ] **Step 11: Commit**

```bash
git add src/policydb/timeline_engine.py src/policydb/web/routes/review.py
git add src/policydb/web/templates/review/_policy_row.html tests/test_suggest_profile.py
git commit -m "feat: milestone profile suggestion with suggest+confirm and bulk accept"
```

---

### Task 7: Timeline Regeneration Triggers

Wire timeline regeneration when policy dates or status change.

**Files:**
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Find policy date save endpoints**

Search `policies.py` for UPDATE statements that modify `effective_date` or `expiration_date`. These are the endpoints that need regen triggers.

- [ ] **Step 2: Add regen trigger after date saves**

After any UPDATE that changes effective_date, expiration_date, or archived status:

```python
# After date/archive update:
profile = conn.execute(
    "SELECT milestone_profile FROM policies WHERE policy_uid = ?", (uid,)
).fetchone()
if profile and profile["milestone_profile"]:
    from policydb.timeline_engine import generate_policy_timelines
    generate_policy_timelines(conn, policy_uid=uid)
```

Only regenerate if the policy has a profile assigned (don't generate for unassigned policies).

- [ ] **Step 3: Add regen trigger on opportunity conversion**

In the "Convert to Policy" endpoint, after clearing `is_opportunity`:

```python
# If profile is set, generate timeline for newly-converted policy
if profile:
    generate_policy_timelines(conn, policy_uid=uid)
```

- [ ] **Step 4: Add cleanup on archive**

When a policy is archived, remove its timeline rows:

```python
conn.execute("DELETE FROM policy_timeline WHERE policy_uid = ?", (uid,))
```

- [ ] **Step 5: Test regen triggers**

1. Accept a profile on a policy (from Task 6)
2. Change the expiration date → verify timeline rows updated with new dates
3. Archive the policy → verify timeline rows deleted
4. Convert an opportunity with a profile → verify timeline generated

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: timeline regen triggers on date/archive/conversion changes"
```

---

### Task 8: Milestone Injection into Urgency Tiers

Inject overdue milestone items into Today/Overdue/Stale tiers as virtual follow-up rows. Fix Prep Coming Up to exclude already-due milestones.

**Files:**
- Modify: `src/policydb/web/routes/action_center.py`
- Create: `tests/test_milestone_injection.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_milestone_injection.py
"""Tests for injecting overdue milestones into follow-up tiers."""


def test_milestone_due_today_in_today_bucket(test_db):
    """Milestone with projected_date == today appears in Today."""


def test_milestone_5d_overdue_in_overdue_bucket(test_db):
    """Milestone 5 days past projected_date appears in Overdue."""


def test_milestone_20d_overdue_in_stale_bucket(test_db):
    """Milestone 20 days past projected_date appears in Stale."""


def test_completed_milestone_not_injected(test_db):
    """Completed milestones don't appear in any tier."""


def test_prep_coming_excludes_due_milestones(test_db):
    """Prep Coming Up only shows milestones with projected_date > today."""


def test_no_duplicate_prep_and_tier(test_db):
    """A milestone appears in Prep OR a tier, never both."""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_milestone_injection.py -v`
Expected: FAIL

- [ ] **Step 3: Add milestone injection query to `_followups_ctx()`**

After building the human follow-up buckets, query overdue milestones:

```python
# Inject overdue milestones into tiers
try:
    milestone_rows = conn.execute("""
        SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
               pt.ideal_date, pt.health, pt.accountability, pt.completed_date,
               p.policy_type, c.name AS client_name, c.id AS client_id
        FROM policy_timeline pt
        JOIN policies p ON p.policy_uid = pt.policy_uid
        JOIN clients c ON c.id = p.client_id
        WHERE pt.projected_date <= ?
          AND pt.completed_date IS NULL
        ORDER BY pt.projected_date
    """, (today_str,)).fetchall()

    for row in milestone_rows:
        item = dict(row)
        item["source"] = "milestone"
        item["is_milestone"] = True
        item["follow_up_date"] = item["projected_date"]
        days_past = (today - date.fromisoformat(item["projected_date"])).days
        item["days_overdue"] = days_past

        if days_past == 0:
            buckets["today"].append(item)
        elif days_past > stale_threshold:
            buckets["stale"].append(item)
        elif days_past > 0:
            buckets["overdue"].append(item)
except Exception:
    pass  # policy_timeline may not exist yet
```

- [ ] **Step 4: Fix Prep Coming Up query — add `projected_date > today` filter**

Update the existing prep_coming query (around line 135) to exclude milestones already due:

```python
prep_rows = conn.execute("""
    SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
           pt.prep_alert_date, pt.accountability, pt.health,
           p.policy_type, c.name AS client_name, c.id AS client_id
    FROM policy_timeline pt
    JOIN policies p ON p.policy_uid = pt.policy_uid
    JOIN clients c ON c.id = p.client_id
    WHERE pt.prep_alert_date <= ?
      AND pt.projected_date > ?
      AND pt.completed_date IS NULL
      AND pt.prep_alert_date IS NOT NULL
    ORDER BY pt.projected_date
""", (today_str, today_str)).fetchall()
```

The key addition is `AND pt.projected_date > ?` — milestones past their projected_date go to the urgency tiers, not Prep.

- [ ] **Step 5: Add milestone row variant to template**

In `_followup_sections.html`, the `fu_row` macro needs to detect `item.is_milestone` and render differently:
- ◆ icon instead of ● dot
- Milestone name label (e.g., "◆ Submission Sent")
- Complete button alongside Follow Up
- Health badge if at_risk/critical

```jinja2
{% if item.is_milestone %}
  <span class="{{ s.dot }}">◆</span>
  <strong class="text-sm">{{ item.client_name }}</strong>
  <span class="text-xs text-gray-500">{{ item.policy_type }}</span>
  <span class="text-xs font-medium {{ s.date_color }}">{{ item.milestone_name }}</span>
  {% if item.health in ('at_risk', 'critical') %}
    <span class="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">{{ item.health }}</span>
  {% endif %}
{% endif %}
```

- [ ] **Step 6: Add Complete button for milestone items**

```jinja2
{% if item.is_milestone %}
  <button hx-post="/policies/{{ item.policy_uid }}/milestone/{{ item.milestone_name | urlencode }}/complete"
          hx-target="#followup-content" hx-swap="innerHTML"
          class="text-xs text-green-600 hover:underline">Complete</button>
{% endif %}
```

- [ ] **Step 7: Add milestone complete endpoint (if not already existing)**

Check if there's an existing route for completing milestones. If not, add:

```python
@router.post("/policies/{uid}/milestone/{milestone_name}/complete")
def complete_milestone(uid: str, milestone_name: str, conn=Depends(get_db)):
    from policydb.timeline_engine import complete_timeline_milestone
    complete_timeline_milestone(conn, uid, milestone_name)
    conn.commit()
    # Return updated followups tab
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_milestone_injection.py -v`
Expected: All PASS

- [ ] **Step 9: Start server and visual verify**

1. Accept profiles for some policies (Task 6)
2. Verify milestones with `projected_date <= today` appear in Today/Overdue/Stale with ◆ icon
3. Verify Prep Coming Up only shows future milestones
4. Click Complete on a milestone → verify it disappears
5. Take screenshots

- [ ] **Step 10: Commit**

```bash
git add src/policydb/web/routes/action_center.py
git add src/policydb/web/templates/action_center/_followup_sections.html
git add tests/test_milestone_injection.py
git commit -m "feat: inject overdue milestones into urgency tiers, fix prep deduplication"
```

---

### Task 9: End-to-End QA Verification

Visual and functional verification of the complete system.

**Files:** None (read-only verification)

- [ ] **Step 1: Start fresh server**

Run: `policydb serve`

- [ ] **Step 2: Verify Settings page**

Navigate to `/settings` → verify "Stale threshold (days)" input with value 14.

- [ ] **Step 3: Seed milestone profiles**

Navigate to `/review` → click "Accept All Suggestions" → verify profiles assigned.

- [ ] **Step 4: Verify Action Center follow-ups tab**

Navigate to `/action-center`:
- Verify summary bar shows correct counts
- Verify Triage section shows items with no disposition (dashed border)
- Verify Today section (blue) shows items due today
- Verify Overdue section (amber) shows 1-14d overdue items
- Verify Stale section (red) shows 14+d overdue items
- Verify Nudge Due (indigo) shows waiting_external items with escalation badges
- Verify Prep Coming Up (purple) shows timeline milestones approaching
- Verify Watching (collapsed) includes both waiting_external and "my turn" items
- Verify Scheduled (collapsed) shows booked items
- Take screenshots of each section

- [ ] **Step 5: Test triage flow**

Click "Set disposition →" on a triage item → select a disposition → verify item moves to correct section.

- [ ] **Step 6: Test milestone integration**

Verify overdue milestones appear in Today/Overdue/Stale with ◆ icon.
Click Complete on a milestone → verify it disappears.

- [ ] **Step 7: Test filter pills**

Click each filter pill → verify correct sections show/hide.

- [ ] **Step 8: Test responsive layout**

Resize browser to check layout doesn't break at narrow widths.

- [ ] **Step 9: Run all tests**

Run: `pytest tests/ -x --timeout=30 -v`
Expected: All PASS

- [ ] **Step 10: Final commit if any fixes needed**

Stage specific changed files and commit:

```bash
git commit -m "fix: QA fixes for follow-ups urgency tiers"
```
