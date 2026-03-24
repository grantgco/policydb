# Policy Pulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Policy Pulse tab to the policy edit page — a dense, single-column health dashboard for individual policy renewal triage.

**Architecture:** New lazy-loaded 5th tab on the policy edit page. One new route handler + one helper function in `policies.py`, one new Jinja2 template. All data sourced from existing queries/helpers — no new tables or migrations. Quick Log form POSTs to existing `/activities/log` endpoint with OOB section refresh.

**Tech Stack:** FastAPI route, Jinja2 template, HTMX lazy-load + OOB swap, Tailwind CSS utilities.

**Spec:** `docs/superpowers/specs/2026-03-23-policy-pulse-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/policydb/web/routes/policies.py` | Add `_build_pulse_attention_items()` helper + `policy_tab_pulse()` route |
| Modify | `src/policydb/web/templates/policies/edit.html` | Add Pulse tab button to tab bar |
| Create | `src/policydb/web/templates/policies/_tab_pulse.html` | Pulse tab template (all 8 sections) |

---

## Task 1: Add Pulse Tab Button to Tab Bar

**Files:**
- Modify: `src/policydb/web/templates/policies/edit.html:85-88`

- [ ] **Step 1: Add Pulse tab button**

Insert before the closing `</div>` of the tab-bar div (after the Workflow button, around line 87):

```html
<button class="tab-btn" data-tab="pulse"
  data-tab-url="/policies/{{ policy.policy_uid }}/tab/pulse">Pulse</button>
```

This follows the exact same pattern as the other 4 tab buttons. No badge needed — the pulse tab itself is the summary.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/policies/edit.html
git commit -m "feat: add Pulse tab button to policy edit page"
```

---

## Task 2: Create `_build_pulse_attention_items()` Helper

**Files:**
- Modify: `src/policydb/web/routes/policies.py` (add after `_build_checklist()` around line 676)

- [ ] **Step 1: Write the helper function**

Add this function in `policies.py` after `_build_checklist()` (line ~676):

```python
def _build_pulse_attention_items(
    overdue_activities: list,
    overdue_policy_fu: dict | None,
    timeline: list[dict],
    today: date,
) -> list[dict]:
    """Merge overdue follow-ups, unhealthy milestones, and waiting items
    into a single sorted attention list for the Policy Pulse tab."""
    items = []

    # 1. Overdue follow-ups from activity_log
    for row in overdue_activities:
        r = dict(row) if not isinstance(row, dict) else row
        items.append({
            "type": "overdue",
            "text": r.get("subject", "Follow-up"),
            "days": r.get("days_overdue", 0),
            "date": r.get("follow_up_date", ""),
            "severity": 0,  # highest priority
        })

    # 2. Overdue policy-level follow-up
    if overdue_policy_fu:
        items.append({
            "type": "overdue",
            "text": overdue_policy_fu.get("subject", "Policy follow-up"),
            "days": overdue_policy_fu.get("days_overdue", 0),
            "date": overdue_policy_fu.get("follow_up_date", ""),
            "severity": 0,
        })

    # 3. Unhealthy milestones (not completed, health != on_track)
    _health_severity = {"critical": 1, "at_risk": 2, "compressed": 3, "drifting": 4}
    for t in timeline:
        if t.get("completed_date"):
            continue
        health = t.get("health", "on_track")
        if health == "on_track":
            continue
        days_behind = 0
        if t.get("projected_date") and t.get("ideal_date"):
            try:
                days_behind = (
                    date.fromisoformat(t["projected_date"])
                    - date.fromisoformat(t["ideal_date"])
                ).days
            except (ValueError, TypeError):
                pass
        items.append({
            "type": "milestone",
            "text": f"{t.get('milestone_name', 'Milestone')} milestone {health}",
            "days": max(days_behind, 0),
            "date": t.get("projected_date", ""),
            "health": health,
            "severity": _health_severity.get(health, 5),
        })

    # 4. Waiting-on items
    for t in timeline:
        if t.get("completed_date"):
            continue
        if t.get("accountability") != "waiting_external":
            continue
        days_waiting = 0
        if t.get("projected_date"):
            try:
                days_waiting = (today - date.fromisoformat(t["projected_date"])).days
                if days_waiting < 0:
                    days_waiting = 0
            except (ValueError, TypeError):
                pass
        items.append({
            "type": "waiting",
            "text": f"Waiting on {t.get('waiting_on', 'external')} for {t.get('milestone_name', '')}",
            "days": days_waiting,
            "date": t.get("projected_date", ""),
            "severity": 6,
        })

    # Sort: severity first, then days descending within same severity
    items.sort(key=lambda x: (x["severity"], -x["days"]))
    return items
```

- [ ] **Step 2: Verify import of `date`**

Confirm `from datetime import date` is already imported at the top of `policies.py`. It should be — the file already uses `date.today()` in other routes.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: add _build_pulse_attention_items() helper for Policy Pulse"
```

---

## Task 3: Create Policy Pulse Route Handler

**Files:**
- Modify: `src/policydb/web/routes/policies.py` (add after `policy_tab_workflow()` around line 1740)

**Imports needed** (verify these are present, add if missing):
- `from policydb.queries import get_policy_total_hours, get_policy_contacts`
- `from policydb.timeline_engine import get_policy_timeline`
- `import policydb.config as cfg`

- [ ] **Step 1: Write the route handler**

Add after the `policy_tab_workflow()` function:

```python
@router.get("/{policy_uid}/tab/pulse", response_class=HTMLResponse)
def policy_tab_pulse(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """Policy Pulse tab — dense single-column health dashboard for renewal triage."""
    policy_uid = policy_uid.upper()
    p, client_info = _policy_base(conn, policy_uid)
    if not p:
        return HTMLResponse("Not found", status_code=404)
    _today = date.today()

    # Readiness score — needs milestone_done/milestone_total first
    rows = [dict(p)]
    _attach_milestone_progress(conn, rows)
    _attach_readiness_score(conn, rows)
    readiness = rows[0]

    # Computed metrics
    days_to_renewal = None
    if p.get("expiration_date"):
        try:
            days_to_renewal = (date.fromisoformat(p["expiration_date"]) - _today).days
        except (ValueError, TypeError):
            pass

    rate_change = None
    if p.get("prior_premium") and p["prior_premium"] > 0 and p.get("premium"):
        rate_change = round((p["premium"] - p["prior_premium"]) / p["prior_premium"], 4)

    # Effort hours
    effort = get_policy_total_hours(conn, p["id"])

    # Overdue follow-ups from activity_log
    overdue_activities = conn.execute(
        """SELECT subject, follow_up_date,
           CAST(julianday('now') - julianday(follow_up_date) AS INTEGER) AS days_overdue
           FROM activity_log WHERE policy_id = ? AND follow_up_done = 0
           AND follow_up_date IS NOT NULL AND follow_up_date < ?
           ORDER BY follow_up_date""",
        (p["id"], _today.isoformat()),
    ).fetchall()

    # Overdue policy-level follow-up
    overdue_policy_fu = None
    if p.get("follow_up_date") and p["follow_up_date"] < _today.isoformat():
        overdue_policy_fu = {
            "subject": "Policy follow-up",
            "follow_up_date": p["follow_up_date"],
            "days_overdue": (_today - date.fromisoformat(p["follow_up_date"])).days,
        }

    # Timeline + checklist
    timeline = get_policy_timeline(conn, policy_uid)
    checklist = _build_checklist(conn, policy_uid)

    # Attention items
    attention_items = _build_pulse_attention_items(
        overdue_activities, overdue_policy_fu, timeline, _today
    )

    # Contacts — canonical source is contact_policy_assignments
    all_contacts = get_policy_contacts(conn, p["id"])
    placement = next((c for c in all_contacts if c.get("is_placement_colleague")), None)
    underwriter = next(
        (c for c in all_contacts if (c.get("role") or "").lower() in ("underwriter", "uw")),
        None,
    )
    # Fallback to text fields if no assignment
    if not placement and p.get("placement_colleague"):
        placement = {"name": p["placement_colleague"], "email": p.get("placement_colleague_email")}
    if not underwriter and p.get("underwriter_name"):
        underwriter = {"name": p["underwriter_name"], "email": p.get("underwriter_contact")}

    # Recent activity (last 5)
    recent = conn.execute(
        """SELECT activity_type, subject, activity_date, duration_hours
           FROM activity_log WHERE policy_id = ?
           ORDER BY activity_date DESC, id DESC LIMIT 5""",
        (p["id"],),
    ).fetchall()

    # Working notes
    scratchpad = conn.execute(
        "SELECT content, updated_at FROM policy_scratchpad WHERE policy_uid = ?",
        (policy_uid,),
    ).fetchone()

    # Review info
    days_since_review = None
    if p.get("last_reviewed_at"):
        try:
            days_since_review = (_today - date.fromisoformat(p["last_reviewed_at"][:10])).days
        except (ValueError, TypeError):
            pass

    return templates.TemplateResponse("policies/_tab_pulse.html", {
        "request": request,
        "policy": dict(p),
        "client": client_info,
        "readiness": readiness,
        "days_to_renewal": days_to_renewal,
        "rate_change": rate_change,
        "effort": effort,
        "attention_items": attention_items,
        "timeline": timeline,
        "checklist": checklist,
        "placement": placement,
        "underwriter": underwriter,
        "recent": recent,
        "scratchpad": dict(scratchpad) if scratchpad else None,
        "activity_types": cfg.get("activity_types"),
        "today": _today.isoformat(),
        "days_since_review": days_since_review,
    })
```

- [ ] **Step 2: Verify imports at top of file**

Check that these imports exist. Add any that are missing:
- `from policydb.queries import get_policy_total_hours` (line ~21, may need to append)
- `from policydb.queries import get_policy_contacts` (likely already imported)
- `from policydb.timeline_engine import get_policy_timeline` (likely already imported for Workflow tab)

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: add policy_tab_pulse() route handler"
```

---

## Task 4: Create Pulse Tab Template

**Files:**
- Create: `src/policydb/web/templates/policies/_tab_pulse.html`

- [ ] **Step 1: Create the template**

Create `src/policydb/web/templates/policies/_tab_pulse.html` with all 8 sections. This is the largest task. The template follows Tailwind utility classes matching the existing dark-themed policy page.

```html
{# Policy Pulse Tab — dense single-column health dashboard #}

<!-- ═══ SECTION 1: Header + Review Badge ═══ -->
<div class="flex items-center justify-between mb-4">
  <div>
    <h2 class="text-lg font-bold text-gray-100">Policy Pulse</h2>
    <div class="text-xs text-gray-500">
      <a href="/clients/{{ client.id }}" class="text-blue-400 hover:underline">{{ client.name }}</a>
      · {{ client.cn_number or '' }}
      {% if policy.policy_uid %}
        · <span class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-gray-800 rounded text-xs text-gray-300 cursor-pointer"
                onclick="copyRefTag('{{ build_ref_tag(policy) }}')" title="Copy ref tag">
            {{ policy.policy_uid }}
          </span>
      {% endif %}
    </div>
  </div>
  <div class="flex items-center gap-3">
    {% if days_since_review is not none %}
      <span class="text-xs text-gray-500">Reviewed {{ policy.last_reviewed_at[:10] }}</span>
    {% else %}
      <span class="text-xs text-amber-400">Not yet reviewed</span>
    {% endif %}
    <form method="post" action="/review/policies/{{ policy.policy_uid }}/mark">
      <input type="hidden" name="mark" value="1">
      <button type="submit" class="text-xs bg-blue-700 text-blue-200 px-3 py-1.5 rounded-md font-semibold hover:bg-blue-600">
        Mark Reviewed
      </button>
    </form>
  </div>
</div>

<!-- ═══ SECTION 2: Metrics Bar ═══ -->
<div id="pulse-metrics" class="grid grid-cols-4 gap-2 mb-4">
  {# Readiness #}
  {% set rs = readiness.get('readiness_score') or 0 %}
  {% if rs >= 75 %}
    {% set rs_bg = 'bg-emerald-900/50' %}
    {% set rs_text = 'text-emerald-300' %}
  {% elif rs >= 50 %}
    {% set rs_bg = 'bg-amber-900/50' %}
    {% set rs_text = 'text-amber-300' %}
  {% else %}
    {% set rs_bg = 'bg-red-900/50' %}
    {% set rs_text = 'text-red-300' %}
  {% endif %}
  <div class="{{ rs_bg }} rounded-lg p-3 text-center">
    <div class="text-2xl font-extrabold {{ rs_text }}">{{ rs }}</div>
    <div class="text-[10px] font-semibold {{ rs_text }} opacity-70 tracking-wider">READINESS</div>
  </div>

  {# Days to renewal #}
  {% if days_to_renewal is not none %}
    {% if days_to_renewal <= 30 %}
      {% set dr_bg = 'bg-red-900/50' %}
      {% set dr_text = 'text-red-300' %}
    {% elif days_to_renewal <= 90 %}
      {% set dr_bg = 'bg-amber-900/50' %}
      {% set dr_text = 'text-amber-300' %}
    {% else %}
      {% set dr_bg = 'bg-blue-900/50' %}
      {% set dr_text = 'text-blue-300' %}
    {% endif %}
  {% else %}
    {% set dr_bg = 'bg-gray-800' %}
    {% set dr_text = 'text-gray-400' %}
  {% endif %}
  <div class="{{ dr_bg }} rounded-lg p-3 text-center">
    <div class="text-2xl font-extrabold {{ dr_text }}">{{ days_to_renewal if days_to_renewal is not none else '—' }}<span class="text-sm font-normal">d</span></div>
    <div class="text-[10px] font-semibold {{ dr_text }} opacity-70 tracking-wider">TO RENEWAL</div>
  </div>

  {# Premium + rate change #}
  <div class="bg-gray-800 rounded-lg p-3 text-center">
    <div class="text-lg font-bold text-gray-100">{{ policy.premium | currency_short if policy.premium else '—' }}</div>
    {% if rate_change is not none %}
      {% if rate_change >= 0 %}
        <div class="text-[10px] font-semibold text-emerald-400">▲ {{ (rate_change * 100) | round(1) }}%</div>
      {% else %}
        <div class="text-[10px] font-semibold text-red-400">▼ {{ (rate_change * 100) | round(1) | abs }}%</div>
      {% endif %}
    {% else %}
      <div class="text-[10px] font-semibold text-gray-500">no prior</div>
    {% endif %}
  </div>

  {# Effort hours #}
  <div class="bg-gray-800 rounded-lg p-3 text-center">
    <div id="pulse-metrics-effort" class="text-lg font-bold text-gray-100">{{ effort | round(1) }}<span class="text-sm font-normal">h</span></div>
    <div class="text-[10px] font-semibold text-gray-400 tracking-wider">EFFORT</div>
  </div>
</div>

<!-- ═══ SECTION 3: Needs Attention ═══ -->
<div id="pulse-needs-attention" class="mb-4">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">NEEDS ATTENTION</div>
  {% if attention_items %}
    {% for item in attention_items %}
      {% if item.type == 'overdue' %}
        {% set border = 'border-l-red-500' %}
        {% set badge_bg = 'bg-red-950' %}
        {% set badge_text = 'text-red-300' %}
        {% set badge_label = 'OVERDUE' %}
        {% set text_color = 'text-red-300' %}
        {% set context = item.days | string + ' days overdue' %}
      {% elif item.type == 'milestone' %}
        {% set health = item.get('health', 'drifting') %}
        {% if health in ('critical', 'at_risk') %}
          {% set border = 'border-l-red-500' %}
          {% set badge_bg = 'bg-red-950' %}
          {% set badge_text = 'text-red-300' %}
        {% else %}
          {% set border = 'border-l-amber-500' %}
          {% set badge_bg = 'bg-amber-950' %}
          {% set badge_text = 'text-amber-300' %}
        {% endif %}
        {% set badge_label = health | upper %}
        {% set text_color = badge_text %}
        {% set context = item.days | string + ' days behind ideal' if item.days else 'projected: ' + item.date %}
      {% elif item.type == 'waiting' %}
        {% set border = 'border-l-indigo-500' %}
        {% set badge_bg = 'bg-indigo-950' %}
        {% set badge_text = 'text-indigo-300' %}
        {% set badge_label = 'WAITING' %}
        {% set text_color = 'text-indigo-300' %}
        {% set context = item.days | string + ' days' if item.days else '' %}
      {% endif %}
      <div class="bg-gray-800 rounded-md p-2 px-3 mb-1 flex items-center justify-between border-l-[3px] {{ border }}">
        <div>
          <div class="text-sm font-medium {{ text_color }}">{{ item.text }}</div>
          <div class="text-[10px] text-gray-500">{{ context }}</div>
        </div>
        <span class="text-[9px] font-semibold {{ badge_bg }} {{ badge_text }} rounded-full px-2 py-0.5">{{ badge_label }}</span>
      </div>
    {% endfor %}
  {% else %}
    <div class="bg-emerald-900/30 rounded-md p-2 px-3 text-sm text-emerald-300">All clear — no items need attention</div>
  {% endif %}
</div>

<!-- ═══ SECTION 4: Milestones ═══ -->
<div class="mb-4">
  <div class="flex items-center justify-between mb-1.5">
    <div class="text-[10px] text-gray-500 font-bold tracking-wider">MILESTONES</div>
    {% set done = checklist | selectattr('completed') | list | length %}
    {% set total_ms = checklist | length %}
    <div class="text-[10px] text-gray-400">{{ done }} of {{ total_ms }} complete</div>
  </div>

  {% if checklist %}
    {# Progress bar #}
    <div class="flex gap-0.5 mb-2">
      {% for item in checklist %}
        {% if item.completed %}
          {% set seg_color = 'bg-emerald-500' %}
        {% else %}
          {# Check timeline for health of this milestone — use namespace for scoping #}
          {% set ns = namespace(ms_health='pending') %}
          {% for t in timeline if t.milestone_name == item.name and not t.completed_date %}
            {% set ns.ms_health = t.health or 'pending' %}
          {% endfor %}
          {% if ns.ms_health in ('critical', 'at_risk') %}
            {% set seg_color = 'bg-red-500' %}
          {% elif ns.ms_health in ('drifting', 'compressed') %}
            {% set seg_color = 'bg-amber-500' %}
          {% else %}
            {% set seg_color = 'bg-gray-700' %}
          {% endif %}
        {% endif %}
        <div class="flex-1 h-1.5 rounded-sm {{ seg_color }}" title="{{ item.name }}{% if item.completed %} ✓{% endif %}"></div>
      {% endfor %}
    </div>

    {# Next milestone detail — use namespace for Jinja2 scoping #}
    {% set ns_next = namespace(ms=None) %}
    {% for t in timeline if not t.completed_date %}
      {% if not ns_next.ms %}
        {% set ns_next.ms = t %}
      {% endif %}
    {% endfor %}
    {% if ns_next.ms %}
      {% set next_ms = ns_next.ms %}
      <div class="bg-gray-800 rounded-md p-2 px-3 flex items-center justify-between">
        <div>
          <div class="text-xs text-gray-200 font-medium">Next: {{ next_ms.milestone_name }}</div>
          <div class="text-[10px] text-gray-500">
            Ideal: {{ next_ms.ideal_date or '—' }} · Projected: {{ next_ms.projected_date or '—' }}
          </div>
        </div>
        {% set h = next_ms.health or 'on_track' %}
        {% if h == 'on_track' %}
          <span class="text-[9px] font-semibold bg-emerald-950 text-emerald-300 rounded-full px-2 py-0.5">on track</span>
        {% elif h in ('critical', 'at_risk') %}
          <span class="text-[9px] font-semibold bg-red-950 text-red-300 rounded-full px-2 py-0.5">{{ h }}</span>
        {% else %}
          <span class="text-[9px] font-semibold bg-amber-950 text-amber-300 rounded-full px-2 py-0.5">{{ h }}</span>
        {% endif %}
      </div>
    {% endif %}
  {% else %}
    <div class="bg-gray-800 rounded-md p-2 px-3 text-xs text-gray-500">
      No milestone profile — <a href="#" class="text-blue-400 hover:underline" onclick="document.querySelector('[data-tab=workflow]').click()">assign one in Workflow tab</a>
    </div>
  {% endif %}
</div>

<!-- ═══ SECTION 5: Key Contacts ═══ -->
<div class="mb-4">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">KEY CONTACTS</div>
  <div class="bg-gray-800 rounded-md p-3 flex gap-4">
    <div class="flex-1">
      <div class="text-[9px] text-gray-500 font-semibold mb-0.5">PLACEMENT</div>
      {% if placement %}
        <div class="text-sm text-gray-200">{{ placement.name }}</div>
        <div class="text-[10px] text-blue-400">
          {% if placement.email %}<a href="mailto:{{ placement.email }}">{{ placement.email }}</a>{% endif %}
          {% if placement.email and placement.get('phone') %} · {% endif %}
          {% if placement.get('phone') %}<a href="tel:{{ placement.phone }}">{{ placement.phone }}</a>{% endif %}
        </div>
      {% else %}
        <div class="text-xs text-gray-600 italic">Not assigned</div>
      {% endif %}
    </div>
    <div class="w-px bg-gray-700"></div>
    <div class="flex-1">
      <div class="text-[9px] text-gray-500 font-semibold mb-0.5">UNDERWRITER</div>
      {% if underwriter %}
        <div class="text-sm text-gray-200">{{ underwriter.name }}</div>
        <div class="text-[10px] text-blue-400">
          {% if underwriter.email %}<a href="mailto:{{ underwriter.email }}">{{ underwriter.email }}</a>{% endif %}
          {% if underwriter.email and underwriter.get('phone') %} · {% endif %}
          {% if underwriter.get('phone') %}<a href="tel:{{ underwriter.phone }}">{{ underwriter.phone }}</a>{% endif %}
        </div>
      {% else %}
        <div class="text-xs text-gray-600 italic">Not assigned</div>
      {% endif %}
    </div>
  </div>
</div>

<!-- ═══ SECTION 6: Recent Activity ═══ -->
<div id="pulse-recent-activity" class="mb-4">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">RECENT ACTIVITY</div>
  {% if recent %}
    <div class="flex flex-col gap-1">
      {% for a in recent %}
        <div class="bg-gray-800 rounded-md p-2 px-3 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-[9px] font-semibold bg-gray-700 text-gray-300 rounded px-1.5 py-0.5">{{ a.activity_type or 'Note' }}</span>
            <span class="text-sm text-gray-200 truncate max-w-[300px]">{{ a.subject }}</span>
          </div>
          <div class="text-[10px] text-gray-500 whitespace-nowrap">
            {{ a.activity_date }}{% if a.duration_hours %} · {{ a.duration_hours | round(1) }}h{% endif %}
          </div>
        </div>
      {% endfor %}
    </div>
  {% else %}
    <div class="bg-gray-800 rounded-md p-2 px-3 text-xs text-gray-600 italic">No recent activity</div>
  {% endif %}
</div>

<!-- ═══ SECTION 7: Working Notes ═══ -->
<div class="mb-4">
  <div class="flex items-center justify-between mb-1.5">
    <div class="text-[10px] text-gray-500 font-bold tracking-wider">WORKING NOTES</div>
    <a href="#" class="text-[10px] text-blue-400 hover:underline" onclick="document.querySelector('[data-tab=workflow]').click()">edit →</a>
  </div>
  {% if scratchpad and scratchpad.content %}
    <div class="bg-gray-800 rounded-md p-3">
      <div class="text-xs text-gray-300 leading-relaxed line-clamp-3">{{ scratchpad.content }}</div>
    </div>
  {% else %}
    <div class="bg-gray-800 rounded-md p-2 px-3 text-xs text-gray-600 italic">
      No working notes — <a href="#" class="text-blue-400 hover:underline" onclick="document.querySelector('[data-tab=workflow]').click()">add →</a>
    </div>
  {% endif %}
</div>

<!-- ═══ SECTION 8: Quick Log ═══ -->
<div class="no-print">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">QUICK LOG</div>
  <div class="bg-gray-800 rounded-md p-3">
    <form hx-post="/activities/log"
          hx-target="#pulse-recent-activity"
          hx-swap="outerHTML"
          hx-vals='{"_pulse_oob": "1"}'
          class="space-y-2">
      <input type="hidden" name="client_id" value="{{ client.id }}">
      <input type="hidden" name="policy_id" value="{{ policy.id }}">
      <div class="grid grid-cols-[1fr_2fr_60px] gap-2">
        <div>
          <div class="text-[9px] text-gray-500 mb-1">Type</div>
          <div class="relative" data-combobox>
            <input type="text" name="activity_type" value="{{ activity_types[0] if activity_types else '' }}"
                   list="pulse-activity-types"
                   class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <datalist id="pulse-activity-types">
              {% for t in activity_types %}
                <option value="{{ t }}">
              {% endfor %}
            </datalist>
          </div>
        </div>
        <div>
          <div class="text-[9px] text-gray-500 mb-1">Subject</div>
          <input type="text" name="subject" required placeholder="What did you do?"
                 class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
        </div>
        <div>
          <div class="text-[9px] text-gray-500 mb-1">Hours</div>
          <input type="number" name="duration_hours" step="0.1" min="0" placeholder="0.1"
                 class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
        </div>
      </div>
      <div class="flex gap-2 items-end">
        <div class="flex-1">
          <div class="text-[9px] text-gray-500 mb-1">Follow-up date</div>
          <input type="date" name="follow_up_date"
                 class="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500">
        </div>
        <button type="submit"
                class="text-xs bg-blue-700 text-blue-200 px-4 py-1.5 rounded-md font-semibold hover:bg-blue-600 whitespace-nowrap">
          Log Activity
        </button>
      </div>
    </form>
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/policies/_tab_pulse.html
git commit -m "feat: create Policy Pulse tab template with all 8 sections"
```

---

## Task 5: Wire Up Quick Log OOB Refresh

The Quick Log form POSTs to `/activities/log`. Currently, that endpoint returns an activity row partial. For the Pulse tab, we need it to return updated Pulse sections via OOB swap.

**Files:**
- Modify: `src/policydb/web/routes/activities.py:63-123`

- [ ] **Step 1: Add OOB response path for pulse context**

In the `activity_log()` function in `activities.py`, add a `_pulse_oob` form parameter and check it **before** the existing final return. When present, return the updated Pulse sections instead of the activity row partial:

```python
# At top of activity_log() function, add the form parameter:
_pulse_oob: str = Form(""),

# BEFORE the existing final return statement, add:
if _pulse_oob:
    # Re-fetch pulse sections for OOB swap
    from policydb.timeline_engine import get_policy_timeline
    policy_uid_row = conn.execute(
        "SELECT policy_uid FROM policies WHERE id = ?", (policy_id,)
    ).fetchone()
    if policy_uid_row:
        _uid = policy_uid_row["policy_uid"]
        _today = date.today()

        # Recent activity
        recent = conn.execute(
            """SELECT activity_type, subject, activity_date, duration_hours
               FROM activity_log WHERE policy_id = ?
               ORDER BY activity_date DESC, id DESC LIMIT 5""",
            (policy_id,),
        ).fetchall()

        # Effort hours
        effort = get_policy_total_hours(conn, policy_id)

        # Recompute attention items for OOB refresh of #pulse-needs-attention
        overdue_activities = conn.execute(
            """SELECT subject, follow_up_date,
               CAST(julianday('now') - julianday(follow_up_date) AS INTEGER) AS days_overdue
               FROM activity_log WHERE policy_id = ? AND follow_up_done = 0
               AND follow_up_date IS NOT NULL AND follow_up_date < ?
               ORDER BY follow_up_date""",
            (policy_id, _today.isoformat()),
        ).fetchall()

        timeline = get_policy_timeline(conn, _uid)

        from policydb.web.routes.policies import _build_pulse_attention_items
        attention_items = _build_pulse_attention_items(
            overdue_activities, None, timeline, _today
        )

        return templates.TemplateResponse("policies/_pulse_oob.html", {
            "request": request,
            "recent": recent,
            "effort": effort,
            "attention_items": attention_items,
        })
```

- [ ] **Step 2: Create the OOB partial template**

Create `src/policydb/web/templates/policies/_pulse_oob.html`:

```html
{# OOB swap partial for Quick Log in Policy Pulse tab #}

{# Recent activity section replacement #}
<div id="pulse-recent-activity" class="mb-4">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">RECENT ACTIVITY</div>
  {% if recent %}
    <div class="flex flex-col gap-1">
      {% for a in recent %}
        <div class="bg-gray-800 rounded-md p-2 px-3 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="text-[9px] font-semibold bg-gray-700 text-gray-300 rounded px-1.5 py-0.5">{{ a.activity_type or 'Note' }}</span>
            <span class="text-sm text-gray-200 truncate max-w-[300px]">{{ a.subject }}</span>
          </div>
          <div class="text-[10px] text-gray-500 whitespace-nowrap">
            {{ a.activity_date }}{% if a.duration_hours %} · {{ a.duration_hours | round(1) }}h{% endif %}
          </div>
        </div>
      {% endfor %}
    </div>
  {% else %}
    <div class="bg-gray-800 rounded-md p-2 px-3 text-xs text-gray-600 italic">No recent activity</div>
  {% endif %}
</div>

{# Metrics effort update via OOB #}
<div id="pulse-metrics-effort" hx-swap-oob="innerHTML">{{ effort | round(1) }}<span class="text-sm font-normal">h</span></div>

{# Needs Attention section via OOB #}
<div id="pulse-needs-attention" hx-swap-oob="outerHTML" class="mb-4">
  <div class="text-[10px] text-gray-500 font-bold tracking-wider mb-1.5">NEEDS ATTENTION</div>
  {% if attention_items %}
    {% for item in attention_items %}
      {% if item.type == 'overdue' %}
        {% set border, badge_bg, badge_text, badge_label, text_color = 'border-l-red-500', 'bg-red-950', 'text-red-300', 'OVERDUE', 'text-red-300' %}
        {% set context = item.days | string + ' days overdue' %}
      {% elif item.type == 'milestone' %}
        {% set health = item.get('health', 'drifting') %}
        {% if health in ('critical', 'at_risk') %}
          {% set border, badge_bg, badge_text = 'border-l-red-500', 'bg-red-950', 'text-red-300' %}
        {% else %}
          {% set border, badge_bg, badge_text = 'border-l-amber-500', 'bg-amber-950', 'text-amber-300' %}
        {% endif %}
        {% set badge_label, text_color = health | upper, badge_text %}
        {% set context = item.days | string + ' days behind ideal' if item.days else 'projected: ' + item.date %}
      {% elif item.type == 'waiting' %}
        {% set border, badge_bg, badge_text, badge_label, text_color = 'border-l-indigo-500', 'bg-indigo-950', 'text-indigo-300', 'WAITING', 'text-indigo-300' %}
        {% set context = item.days | string + ' days' if item.days else '' %}
      {% endif %}
      <div class="bg-gray-800 rounded-md p-2 px-3 mb-1 flex items-center justify-between border-l-[3px] {{ border }}">
        <div>
          <div class="text-sm font-medium {{ text_color }}">{{ item.text }}</div>
          <div class="text-[10px] text-gray-500">{{ context }}</div>
        </div>
        <span class="text-[9px] font-semibold {{ badge_bg }} {{ badge_text }} rounded-full px-2 py-0.5">{{ badge_label }}</span>
      </div>
    {% endfor %}
  {% else %}
    <div class="bg-emerald-900/30 rounded-md p-2 px-3 text-sm text-emerald-300">All clear — no items need attention</div>
  {% endif %}
</div>
```

- [ ] **Step 3: Add imports to activities.py**

Verify/add at top of `activities.py`:
```python
from policydb.queries import get_policy_total_hours
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/activities.py src/policydb/web/templates/policies/_pulse_oob.html
git commit -m "feat: add OOB refresh for Quick Log in Policy Pulse tab"
```

---

## Task 6: QA Verification

**Files:** None (read-only testing)

- [ ] **Step 1: Start server and navigate to a policy**

Run `policydb serve`, open browser to a policy with active data (activities, follow-ups, milestones).

- [ ] **Step 2: Verify Pulse tab loads**

Click the Pulse tab. Confirm:
- All 8 sections render
- Metrics bar shows readiness score, days to renewal, premium, effort
- Attention items list (or "All clear" if none)
- Milestone progress bar with colored segments
- Contacts show (or "Not assigned")
- Recent activity list
- Working notes snippet (or empty state)
- Quick Log form visible

- [ ] **Step 3: Test Quick Log**

Fill in the Quick Log form and submit. Verify:
- Activity appears in Recent Activity section without full page reload
- Effort hours update in metrics bar

- [ ] **Step 4: Test Mark Reviewed**

Click "Mark Reviewed". Verify badge updates to show today's date.

- [ ] **Step 5: Test empty states**

Navigate to a policy with no activities, no milestones, no contacts, no scratchpad. Verify all empty state messages render correctly.

- [ ] **Step 6: Test responsive layout**

Shrink the browser to ~768px width. Verify the 4-column metrics bar stacks properly and nothing overflows.

- [ ] **Step 7: Fix any issues found, then commit**

```bash
git add -A
git commit -m "fix: address QA findings from Policy Pulse review"
```

---

## Task 7: Close GitHub Issue

- [ ] **Step 1: Close issue #33**

```bash
gh issue close 33 --comment "Implemented Policy Pulse tab — dense single-column health dashboard on the policy edit page"
```

- [ ] **Step 2: Final commit if any remaining cleanup**

```bash
git add -A
git commit -m "chore: finalize Policy Pulse feature"
```
