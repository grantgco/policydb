# Team Chart: Placement Colleague Suggestions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-suggest placement colleagues on the team chart editor so users can one-click confirm or permanently dismiss them.

**Architecture:** New migration adds a `team_chart_dismissals` table. A new API endpoint queries policy assignments to find contacts not yet on the internal team and not dismissed. The team chart template gets an amber "Suggested from Policies" section in both the preview canvas and editor panel, with confirm/dismiss buttons wired via HTMX.

**Tech Stack:** FastAPI, SQLite, Jinja2/HTMX, existing `assign_contact_to_client()` from `queries.py`

**Spec:** `docs/superpowers/specs/2026-04-02-team-chart-placement-suggestions-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/policydb/migrations/126_team_chart_dismissals.sql` | Create | Schema for dismissals table |
| `src/policydb/db.py` | Modify (~line 1717) | Wire migration 126 into `init_db()` |
| `src/policydb/web/routes/charts.py` | Modify (~lines 111-137) | Add suggestions endpoint, confirm endpoint, dismiss endpoint |
| `src/policydb/web/templates/charts/manual/_tpl_team_chart.html` | Modify | Add suggested section to canvas + editor panel |

---

### Task 1: Migration — `team_chart_dismissals` table

**Files:**
- Create: `src/policydb/migrations/126_team_chart_dismissals.sql`
- Modify: `src/policydb/db.py` (~line 1717, after migration 125 block)

- [ ] **Step 1: Create migration SQL file**

```sql
-- 126_team_chart_dismissals.sql
-- Track permanently dismissed placement colleague suggestions on team charts
CREATE TABLE IF NOT EXISTS team_chart_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    client_id    INTEGER NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
    dismissed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, client_id)
);
```

- [ ] **Step 2: Wire migration into `init_db()`**

In `src/policydb/db.py`, after the `if 125 not in applied:` block (~line 1717), add:

```python
if 126 not in applied:
    conn.executescript((_MIGRATIONS_DIR / "126_team_chart_dismissals.sql").read_text())
    conn.execute(
        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
        (126, "Track dismissed placement colleague suggestions on team charts"),
    )
    conn.commit()
    logger.info("Migration 126: created team_chart_dismissals table")
```

Also add `126` to the `_KNOWN_MIGRATIONS` set (~line 365).

- [ ] **Step 3: Verify migration runs**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.db import init_db, get_connection; conn = get_connection(); print([r['version'] for r in conn.execute('SELECT version FROM schema_version ORDER BY version DESC LIMIT 3').fetchall()])"`

Expected: `[126, 125, 124]`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/126_team_chart_dismissals.sql src/policydb/db.py
git commit -m "feat: add team_chart_dismissals migration (126)"
```

---

### Task 2: Suggestions API endpoint

**Files:**
- Modify: `src/policydb/web/routes/charts.py` (~line 137, after `chart_load_team`)

- [ ] **Step 1: Add GET `/api/team/{client_id}/suggestions` endpoint**

In `src/policydb/web/routes/charts.py`, after the existing `chart_load_team` function, add:

```python
@router.get("/api/team/{client_id}/suggestions", response_class=JSONResponse)
async def chart_team_suggestions(client_id: int, conn=Depends(get_db)):
    """Return placement colleagues not yet on the internal team and not dismissed."""
    rows = conn.execute(
        """
        SELECT DISTINCT c.id   AS contact_id,
               c.name, c.email, c.phone, c.mobile,
               GROUP_CONCAT(DISTINCT
                 COALESCE(
                   CASE WHEN cpa.is_placement_colleague = 1 THEN 'Placement' ELSE cpa.role END,
                   'Policy Contact'
                 ) || ' - ' || COALESCE(p.policy_type, '?')
               ) AS suggested_role
        FROM contact_policy_assignments cpa
        JOIN contacts c  ON c.id  = cpa.contact_id
        JOIN policies p  ON p.id  = cpa.policy_id
        WHERE p.client_id = ?
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
          AND cpa.contact_id NOT IN (
              SELECT contact_id FROM contact_client_assignments
              WHERE client_id = ? AND contact_type = 'internal'
          )
          AND cpa.contact_id NOT IN (
              SELECT contact_id FROM team_chart_dismissals
              WHERE client_id = ?
          )
        GROUP BY c.id
        ORDER BY c.name
        """,
        (client_id, client_id, client_id),
    ).fetchall()
    return [
        {
            "contact_id": r["contact_id"],
            "name":       r["name"] or "",
            "email":      r["email"] or "",
            "phone":      r["phone"] or "",
            "mobile":     r["mobile"] or "",
            "suggested_role": r["suggested_role"] or "",
        }
        for r in rows
    ]
```

- [ ] **Step 2: Verify endpoint returns data**

Start the server and test with a client that has placement colleagues:

```bash
curl -s http://127.0.0.1:8000/api/team/1/suggestions | python3 -m json.tool
```

Expected: JSON array (possibly empty if no placement colleagues exist for client 1). Verify structure has `contact_id`, `name`, `email`, `phone`, `mobile`, `suggested_role`.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/charts.py
git commit -m "feat: add team chart suggestions API endpoint"
```

---

### Task 3: Confirm and dismiss endpoints

**Files:**
- Modify: `src/policydb/web/routes/charts.py` (after the suggestions endpoint)

- [ ] **Step 1: Add POST confirm endpoint**

```python
@router.post("/api/team/{client_id}/suggestions/{contact_id}/confirm", response_class=JSONResponse)
async def chart_team_confirm(client_id: int, contact_id: int, conn=Depends(get_db)):
    """Confirm a suggested placement colleague as an internal team member."""
    from policydb.queries import assign_contact_to_client

    # Build smart role from policy assignments
    role_row = conn.execute(
        """
        SELECT GROUP_CONCAT(DISTINCT
                 COALESCE(
                   CASE WHEN cpa.is_placement_colleague = 1 THEN 'Placement' ELSE cpa.role END,
                   'Policy Contact'
                 ) || ' - ' || COALESCE(p.policy_type, '?')
               ) AS suggested_role
        FROM contact_policy_assignments cpa
        JOIN policies p ON p.id = cpa.policy_id
        WHERE cpa.contact_id = ? AND p.client_id = ?
        """,
        (contact_id, client_id),
    ).fetchone()

    assignment_id = assign_contact_to_client(
        conn, contact_id, client_id,
        contact_type="internal",
        assignment=role_row["suggested_role"] if role_row else "",
    )
    conn.commit()
    return {"ok": True, "assignment_id": assignment_id}
```

- [ ] **Step 2: Add POST dismiss endpoint**

```python
@router.post("/api/team/{client_id}/suggestions/{contact_id}/dismiss", response_class=JSONResponse)
async def chart_team_dismiss(client_id: int, contact_id: int, conn=Depends(get_db)):
    """Permanently dismiss a placement colleague suggestion for this client."""
    conn.execute(
        "INSERT OR IGNORE INTO team_chart_dismissals (contact_id, client_id) VALUES (?, ?)",
        (contact_id, client_id),
    )
    conn.commit()
    return {"ok": True}
```

- [ ] **Step 3: Verify both endpoints**

Test confirm (use a real contact_id and client_id from your DB):

```bash
curl -s -X POST http://127.0.0.1:8000/api/team/1/suggestions/5/confirm | python3 -m json.tool
```

Expected: `{"ok": true, "assignment_id": <number>}`

Test dismiss:

```bash
curl -s -X POST http://127.0.0.1:8000/api/team/1/suggestions/5/dismiss | python3 -m json.tool
```

Expected: `{"ok": true}`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/charts.py
git commit -m "feat: add confirm and dismiss endpoints for team chart suggestions"
```

---

### Task 4: Template — suggested section in editor panel

**Files:**
- Modify: `src/policydb/web/templates/charts/manual/_tpl_team_chart.html`

This task adds the amber "Suggested from Policies" section to the **editor panel** (the right-side panel where team members are managed). Suggestions are fetched via JS alongside the existing team data load.

- [ ] **Step 1: Add suggestion cards container to editor panel**

In `_tpl_team_chart.html`, find the "Add Member" button (~line 390):

```html
<button type="button" class="tc-btn-add-member" onclick="addMember()">+ Add Member</button>
```

Insert the following **above** that button:

```html
<!-- ── Suggested from Policies ── -->
<div id="suggestions-section" style="display:none; margin-bottom: 0.75rem;">
  <div style="background: #FFFBEB; border: 1px dashed #D97706; border-radius: 4px; padding: 0.6rem 0.65rem;">
    <div style="font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #92400E; margin-bottom: 0.5rem;">
      ✦ Suggested from Policies
    </div>
    <div id="suggestions-list" style="display: flex; flex-direction: column; gap: 0.4rem;"></div>
  </div>
</div>
```

- [ ] **Step 2: Add CSS for suggestion cards**

In the `<style>` block at the top of the template, add:

```css
/* ── Suggestion cards (editor panel) ── */
.tc-suggestion-card {
  background: #fff;
  border: 1px dashed #D97706;
  border-radius: 4px;
  padding: 0.55rem 0.65rem;
}
.tc-suggestion-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: #000F47;
}
.tc-suggestion-role {
  font-size: 0.7rem;
  color: #7B7974;
  margin-top: 1px;
}
.tc-suggestion-actions {
  display: flex;
  gap: 0.3rem;
  margin-top: 0.4rem;
}
.tc-suggestion-confirm {
  flex: 1;
  padding: 0.3rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 600;
  background: #000F47;
  color: #fff;
  border: none;
  border-radius: 3px;
  cursor: pointer;
}
.tc-suggestion-confirm:hover { background: #0B4BFF; }
.tc-suggestion-dismiss {
  padding: 0.3rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 500;
  background: #fff;
  color: #7B7974;
  border: 1px solid #B9B6B1;
  border-radius: 3px;
  cursor: pointer;
}
.tc-suggestion-dismiss:hover { color: #c8102e; border-color: #c8102e; }
```

- [ ] **Step 3: Add JS to fetch and render suggestions**

In the `<script>` block, add these functions (before or after the existing `addMember` function):

```javascript
/* ── Placement colleague suggestions ── */
var suggestions = [];

function loadSuggestions() {
  var clientId = document.getElementById('team-canvas')?.dataset.clientId;
  if (!clientId) return;
  fetch('/api/team/' + clientId + '/suggestions')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      suggestions = data;
      buildSuggestions();
    })
    .catch(function() { suggestions = []; buildSuggestions(); });
}

function buildSuggestions() {
  var section = document.getElementById('suggestions-section');
  var list = document.getElementById('suggestions-list');
  if (!suggestions.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';
  list.innerHTML = suggestions.map(function(s) {
    return '<div class="tc-suggestion-card" data-contact-id="' + s.contact_id + '">'
      + '<div class="tc-suggestion-name">' + ManualChart.esc(s.name) + '</div>'
      + '<div class="tc-suggestion-role">' + ManualChart.esc(s.suggested_role) + '</div>'
      + (s.email ? '<div style="font-size:0.65rem;color:#7B7974;margin-top:2px;">✉ ' + ManualChart.esc(s.email) + '</div>' : '')
      + '<div class="tc-suggestion-actions">'
      + '  <button class="tc-suggestion-confirm" onclick="confirmSuggestion(' + s.contact_id + ')">✓ Add to Team</button>'
      + '  <button class="tc-suggestion-dismiss" onclick="dismissSuggestion(' + s.contact_id + ')">✕</button>'
      + '</div>'
      + '</div>';
  }).join('');
}

function confirmSuggestion(contactId) {
  var clientId = document.getElementById('team-canvas')?.dataset.clientId;
  fetch('/api/team/' + clientId + '/suggestions/' + contactId + '/confirm', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.ok) return;
      // Find the confirmed suggestion and add to members list
      var s = suggestions.find(function(x) { return x.contact_id === contactId; });
      if (s) {
        members.push({
          id: nextId++,
          name: s.name,
          title: '',
          role: '',
          assignment: s.suggested_role,
          phone: s.phone,
          email: s.email,
          mobile: s.mobile,
          notes: '',
        });
        serializeState();
        buildMemberEditor();
        buildCanvas();
      }
      // Remove from suggestions
      suggestions = suggestions.filter(function(x) { return x.contact_id !== contactId; });
      buildSuggestions();
    });
}

function dismissSuggestion(contactId) {
  var clientId = document.getElementById('team-canvas')?.dataset.clientId;
  fetch('/api/team/' + clientId + '/suggestions/' + contactId + '/dismiss', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.ok) return;
      suggestions = suggestions.filter(function(x) { return x.contact_id !== contactId; });
      buildSuggestions();
    });
}
```

- [ ] **Step 4: Wire `loadSuggestions()` into the existing init flow**

Find the existing code that loads team data on page init. It will be in the `loadTeamData()` or similar function that calls `fetch('/api/team/' + clientId)`. At the end of its `.then()` callback (after members are populated and `buildCanvas()` is called), add:

```javascript
loadSuggestions();
```

This ensures suggestions load after the team data is ready.

- [ ] **Step 5: Add `data-client-id` to the canvas element**

The suggestions JS needs to know the client ID. Find the canvas element in the template and ensure it has the client ID available. In the chart's init/load function where `clientId` is determined, add to the canvas element:

```javascript
document.getElementById('team-canvas').dataset.clientId = clientId;
```

If `clientId` is already available as a JS variable from the template context (check how the existing `/api/team/{client_id}` call gets its client ID), use the same source.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_team_chart.html
git commit -m "feat: add suggested placement colleagues to team chart editor"
```

---

### Task 5: Template — suggested section in preview canvas

**Files:**
- Modify: `src/policydb/web/templates/charts/manual/_tpl_team_chart.html`

This task adds suggested cards to the **preview canvas** (the left-side rendered chart) so the user sees them in context. Suggestions render with dashed amber borders and action buttons. They are excluded from export/snapshot.

- [ ] **Step 1: Add suggestion rendering to `buildCanvas()`**

Find the `buildCanvas()` function (~line 770). At the end, after the layout is built (after the grid or grouped content is appended), add:

```javascript
// ── Suggested from policies (canvas preview, not exported) ──
if (suggestions.length && !isExporting) {
  var sugHtml = '<div class="tc-suggestions-preview" style="padding:12px 32px 24px;">'
    + '<div style="background:#FFFBEB;border:1px dashed #D97706;border-radius:4px;padding:12px 16px;">'
    + '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#92400E;margin-bottom:10px;">✦ Suggested from Policies</div>'
    + '<div style="display:flex;flex-wrap:wrap;gap:12px;">';
  suggestions.forEach(function(s) {
    var sz = cardSizes(members.length + suggestions.length);
    sugHtml += '<div style="border:1px dashed #D97706;border-radius:4px;padding:' + sz.pad + ';min-width:200px;flex:1;max-width:280px;background:#fff;">'
      + '<div style="font-family:\'Noto Serif\',serif;font-size:' + sz.name + ';font-weight:700;color:#000F47;">' + ManualChart.esc(s.name) + '</div>'
      + '<div style="font-size:' + sz.sub + ';color:#7B7974;margin-bottom:4px;">' + ManualChart.esc(s.suggested_role) + '</div>'
      + (s.email ? '<div style="font-size:10px;color:#7B7974;">✉ ' + ManualChart.esc(s.email) + '</div>' : '')
      + '</div>';
  });
  sugHtml += '</div></div></div>';
  canvas.querySelector('.tc-grid, .tc-grouped')?.insertAdjacentHTML('afterend', sugHtml);
}
```

- [ ] **Step 2: Add `isExporting` guard**

The suggestion section must NOT appear in exports/snapshots. Check how the existing export flow works — there will be a flag or a function that renders the canvas for export. Find where `html2canvas` or snapshot logic is invoked. Before calling it, set a module-level flag:

```javascript
var isExporting = false;
```

In the export function, wrap the call:

```javascript
isExporting = true;
buildCanvas();
// ... html2canvas / export logic ...
isExporting = false;
buildCanvas();
```

If an `isExporting` or similar flag already exists, use it instead of creating a new one.

- [ ] **Step 3: Verify canvas preview**

1. Open a team chart for a client that has placement colleagues on their policies
2. Confirm the amber dashed section appears below the team grid in the canvas preview
3. Confirm clicking "Add to Team" in the editor moves the card from suggestions into the regular team grid
4. Confirm clicking "✕" removes the card from both editor and canvas suggestions

- [ ] **Step 4: Verify export excludes suggestions**

1. With suggestions visible, trigger the chart export/snapshot
2. Confirm the exported image does NOT include the amber suggestion section

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_team_chart.html
git commit -m "feat: show placement suggestions in team chart canvas preview"
```

---

### Task 6: QA — end-to-end verification

**Files:** None (testing only)

- [ ] **Step 1: Test with a client that has placement colleagues**

1. Navigate to `/manual/team_chart` and select a client with policies that have placement colleagues
2. Verify the amber "Suggested from Policies" section appears in both the editor panel and canvas preview
3. Verify suggestion cards show the smart role (e.g., "Placement - GL, Property")

- [ ] **Step 2: Test confirm flow**

1. Click "Add to Team" on a suggested card
2. Verify the contact moves from suggestions into the regular member list in the editor
3. Verify the canvas preview updates — card moves from amber section to main grid
4. Verify the contact now has a `contact_client_assignments` record with `contact_type='internal'`

- [ ] **Step 3: Test dismiss flow**

1. Click "✕" on a suggested card
2. Verify the card disappears from both editor and canvas
3. Refresh the page — verify the dismissed contact does NOT reappear
4. Verify a row exists in `team_chart_dismissals`

- [ ] **Step 4: Test edge cases**

1. Client with no placement colleagues — verify no suggestion section appears
2. All suggestions dismissed — verify suggestion section hides
3. Export/snapshot — verify suggestions are excluded

- [ ] **Step 5: Take screenshots of working feature**

Capture screenshots of:
- Team chart with suggestions visible
- After confirming one suggestion
- After dismissing one suggestion
- Export without suggestions

- [ ] **Step 6: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: QA fixes for team chart placement suggestions"
```
