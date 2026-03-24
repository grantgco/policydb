# PolicyDB Overnight QA Session

You are running an autonomous overnight QA session for PolicyDB. Your job is to systematically visit every page in the application, screenshot it, check for UI bugs, log everything to a report, and fix what you can. Do NOT ask the user any questions — work completely autonomously until done.

---

## Phase 0: Setup

1. **Start the dev server** using `preview_start` with name `policydb` (config already exists in `.claude/launch.json` — port 8001).
2. **Create report directory:**
   ```bash
   mkdir -p ~/.policydb/qa-reports/$(date +%Y-%m-%d)/screenshots
   ```
3. **Initialize the bug report file** at `~/.policydb/qa-reports/$(date +%Y-%m-%d)/qa-report.md` with this header:
   ```markdown
   # PolicyDB QA Report — {DATE}

   **Started:** {timestamp}
   **Server:** http://127.0.0.1:8001

   ## Summary
   | Metric | Count |
   |--------|-------|
   | Pages tested | 0 |
   | Tabs tested | 0 |
   | Bugs found | 0 |
   | Bugs fixed | 0 |
   | Screenshots taken | 0 |

   ## Bugs Found

   ## Page Results
   ```
4. **Open a Chrome tab** using `tabs_context_mcp` (createIfEmpty: true), then `navigate` to `http://127.0.0.1:8001`.

---

## Phase 1: Systematic Page Walkthrough

For EVERY page below, follow this exact protocol:

### Per-Page Protocol
1. **Navigate** to the URL using `navigate` tool
2. **Wait** 2 seconds for page load (`computer` action: `wait`, duration: 2)
3. **Screenshot** the page (`computer` action: `screenshot`) and save to `~/.policydb/qa-reports/{DATE}/screenshots/{page-name}.png`
4. **Read the page** using `read_page` to get the accessibility tree — check for:
   - Missing or empty text nodes that should have content
   - Broken element hierarchy
   - Elements with no accessible names on buttons/links
5. **Check console** using `read_console_messages` with pattern `error|Error|ERR|exception|Exception|TypeError|ReferenceError|500` for JS errors
6. **Check for visual issues** by examining the screenshot:
   - Text overflow or truncation
   - Overlapping elements
   - Missing borders, backgrounds, or styling
   - Buttons or links that appear broken
   - Empty tables/cards that should have data (vs legitimate empty states)
   - Misaligned columns in tables
   - Elements cut off at page edges
7. **Click each tab** (if tabbed page) — screenshot each tab, check console after each click
8. **Test one interactive element** per page (click a dropdown, hover a button, open a popover)
9. **Log results** to the report file — either PASS or list specific bugs found

### Bug Entry Format
```markdown
### BUG-{NNN}: {Short title}
- **Severity:** Critical / Major / Minor / Cosmetic
- **Page:** {page name} — {URL}
- **Tab:** {tab name if applicable}
- **Description:** {What's wrong}
- **Screenshot:** `screenshots/{filename}.png`
- **Expected:** {What it should look like}
- **Actual:** {What it actually looks like}
- **Fix attempted:** Yes/No — {details if yes}
```

### Severity Guide
- **Critical:** Page won't load, server error (500), data loss risk, completely broken feature
- **Major:** Feature doesn't work (button does nothing, form won't save, missing data that should exist)
- **Minor:** Layout issue (overflow, misalignment, wrong spacing) but feature works
- **Cosmetic:** Visual nit (color slightly off, extra whitespace, minor style inconsistency)

---

## Phase 2: Page Test Matrix

Test pages in this exact order. Use real data — pick the first client and first policy from the database for detail pages.

### Step 1: Get test data IDs
Before starting, query the database to get real IDs:
```bash
sqlite3 ~/.policydb/policydb.sqlite "SELECT id, name FROM clients LIMIT 3;"
sqlite3 ~/.policydb/policydb.sqlite "SELECT uid, policy_type, client_id FROM policies LIMIT 3;"
sqlite3 ~/.policydb/policydb.sqlite "SELECT id FROM meetings LIMIT 1;"
```
Store these for use in URLs below. Use `{CLIENT_ID}` for the first client's id and `{POLICY_UID}` for the first policy's uid.

### Step 2: Walk every page

---

#### PAGE 1: Dashboard
**URL:** `/`
**Check:** Metrics summary bar renders with numbers. Pipeline table has rows (or clean empty state). Scratchpad area is visible. Nav bar fully rendered with all dropdowns.
**Interactive test:** Click each nav dropdown to verify they open.

#### PAGE 2: Client List
**URL:** `/clients`
**Check:** Client cards or table rows appear. Filter buttons (segment, urgent, inactive, prospect) render. Search bar present. Sorting controls work.
**Interactive test:** Click one filter button, verify list updates. Click sort header.

#### PAGE 3: Client Detail — Overview Tab
**URL:** `/clients/{CLIENT_ID}`
**Check:** Client name in header. Tab bar with 4 tabs. Overview tab loads by default. Activity timeline, linked accounts section, Account Pulse widget visible. Sidebar with Key Dates + Quick Actions.
**Interactive test:** Click the sidebar Quick Actions buttons.

#### PAGE 4: Client Detail — Contacts Tab
**URL:** `/clients/{CLIENT_ID}` → click Contacts tab
**Check:** Contact matrix renders (internal + external sections). Add-row buttons present. Cells are editable on click.
**Interactive test:** Click a cell to verify contenteditable activates.

#### PAGE 5: Client Detail — Policies Tab
**URL:** `/clients/{CLIENT_ID}` → click Policies tab
**Check:** Policy cards/rows for this client. Status badges. Quick-edit popovers. "Open →" links.
**Interactive test:** Click a status badge dropdown.

#### PAGE 6: Client Detail — Risk Tab
**URL:** `/clients/{CLIENT_ID}` → click Risk tab
**Check:** Risk matrix table renders. Severity column, controls, LOB sub-rows. Add Risk button.
**Interactive test:** Click Add Risk button (verify form appears, then cancel).

#### PAGE 7: Client Edit
**URL:** `/clients/{CLIENT_ID}/edit`
**Check:** All fields render with current values. Comboboxes for industry, segment. Per-field save on blur works.
**Interactive test:** Click into a field, verify it becomes editable. Blur without changing — no error.

#### PAGE 8: Client Locations
**URL:** `/clients/{CLIENT_ID}/locations`
**Check:** Pairing board layout (left/right columns). Policy cards are draggable. Location list renders.
**Interactive test:** Verify drag handles are visible.

#### PAGE 9: Client Requests/RFI
**URL:** `/clients/{CLIENT_ID}/requests`
**Check:** Bundle list or empty state. Create bundle button. Item forms if bundles exist.
**Interactive test:** Click "New Bundle" if present.

#### PAGE 10: Client Projects
**URL:** `/clients/{CLIENT_ID}/projects/pipeline` (if exists, otherwise skip)
**Check:** Pipeline table, timeline visualization.

#### PAGE 11: Compliance
**URL:** `/compliance/client/{CLIENT_ID}`
**Check:** Requirements matrix loads. Source dropdown. Location tabs. Review mode toggle.
**Interactive test:** Toggle review mode on/off.

#### PAGE 12: Renewal Pipeline
**URL:** `/renewals`
**Check:** Pipeline table with policies. Window filter buttons (30/60/90/180 days). Status badges. Row edit buttons. Bulk action controls.
**Interactive test:** Click a window filter button. Click one row's edit button, verify inline form appears, then cancel.

#### PAGE 13: Renewal Calendar
**URL:** `/renewals/calendar`
**Check:** Calendar grid renders with month headers. Policy dots/entries on dates. Navigation arrows work.
**Interactive test:** Click forward/back month arrows.

#### PAGE 14: Policy Detail
**URL:** `/policies/{POLICY_UID}/edit`
**Check all 4 tabs:**
- **Details tab:** All fields render (carrier, type, dates, premium, limits). Comboboxes work. Status badge.
- **Activity tab:** Activity timeline. Log activity form. Follow-up section.
- **Contacts tab:** Placement colleague matrix. Underwriter contacts. Add-row buttons.
- **Workflow tab:** Timeline visualization (if milestone profile assigned). Milestone checklist.
**Interactive test per tab:** Click a combobox, verify dropdown appears. Click log activity, verify form. Click a cell in contacts. Click a milestone checkbox.

#### PAGE 15: New Policy
**URL:** `/policies/new`
**Check:** Form renders with all fields. Client selector. Carrier combobox. Date pickers. Policy type dropdown. Opportunity toggle.
**Interactive test:** Click client selector, verify dropdown. Click carrier combobox.

#### PAGE 16: Action Center — Follow-ups Tab
**URL:** `/action-center`
**Check:** 5 sections render: Act Now, Nudge Due, Prep Coming Up, Watching, Scheduled. Disposition pill buttons on each row. Portfolio health sidebar.
**Interactive test:** Click a disposition pill on one follow-up.

#### PAGE 17: Action Center — Inbox Tab
**URL:** `/action-center?tab=inbox`
**Check:** Inbox items list or empty state. Capture form. Process/dismiss buttons on items.
**Interactive test:** Click capture form area.

#### PAGE 18: Action Center — Activities Tab
**URL:** `/action-center?tab=activities`
**Check:** Activity timeline with clustering. Filter controls. Activity entries with ref tags.
**Interactive test:** Click a filter option.

#### PAGE 19: Action Center — Activity Review Tab
**URL:** `/action-center?tab=activity-review`
**Check:** AI suggestion cards or empty state. Scan button. Log/dismiss buttons per suggestion.
**Interactive test:** Click scan button if visible.

#### PAGE 20: Action Center — Scratchpads Tab
**URL:** `/action-center?tab=scratchpads`
**Check:** Dashboard scratchpad + all client scratchpads. Each scratchpad is editable. "Log as Activity" buttons.
**Interactive test:** Click into a scratchpad to verify editing.

#### PAGE 21: Contacts Directory
**URL:** `/contacts`
**Check:** Unified contact list (client + internal). Search bar. Add contact button. Contact rows with phone, email, role columns.
**Interactive test:** Type in search bar, verify filtering. Click add-row button.

#### PAGE 22: Meetings
**URL:** `/meetings`
**Check:** Meeting list or empty state. New meeting button. Calendar view if present. Upcoming section.
**Interactive test:** Click "New Meeting" button, verify form renders. If meetings exist, click into one and check detail page (agenda, attendees, decisions, actions sections).

#### PAGE 23: Review
**URL:** `/review`
**Check:** Review queue table. Policy rows with review status. Accept/cycle buttons. Stats dashboard.
**Interactive test:** Click stats tab if present.

#### PAGE 24: Briefing
**URL:** `/briefing`
**Check:** Client search/selector. Briefing renders for selected client with all sections.
**Interactive test:** Search for a client, select it, verify briefing loads.

#### PAGE 25: Reconcile
**URL:** `/reconcile`
**Check:** Upload form renders. File input. Template download links. Reference guide link.
**Interactive test:** Verify upload area is clickable/interactive.

#### PAGE 26: Templates
**URL:** `/templates`
**Check:** Template list with names and contexts. New template button. Edit/delete/duplicate buttons per template.
**Interactive test:** Click "New Template", verify editor loads with token pill toolbar.

#### PAGE 27: Settings — All 8 Tabs
**URL:** `/settings`
**Check each tab by clicking:**
1. **Workflow:** Config lists (statuses, activity types, dispositions). Add/remove/reorder controls. Email subject templates.
2. **Timeline Engine:** Milestone profiles. Mandated activities. Readiness weights.
3. **Readiness:** Scoring configuration.
4. **Carriers:** Carrier alias groups. Add/remove alias controls.
5. **Email Contacts:** Internal contact email preferences.
6. **Property/Risk:** Risk categories and templates.
7. **Database:** Schema info. Backup/vacuum/purge buttons. SQL query tool.
8. **Audit Log:** Redirects to `/logs?tab=audit`.
**Interactive test:** On Workflow tab, verify a list's add button opens input. On Database tab, verify schema loads.

#### PAGE 28: Logs
**URL:** `/logs`
**Check:** App Log tab and Audit Log tab. Log entries render in tables with timestamps, levels, messages. Filters work.
**Interactive test:** Switch between tabs. Apply a log level filter.

#### PAGE 29: Ref Lookup
**URL:** `/ref-lookup`
**Check:** Search input renders. Results area. Help text about ref tag format.
**Interactive test:** Type a partial ref tag, verify search executes.

#### PAGE 30: Search
**URL:** `/search`
**Check:** Search input. Results grouped by type (clients, policies, activities). Result cards with links.
**Interactive test:** Search for a known client name, verify results appear.

---

## Phase 3: Bug Fixing

After completing the full walkthrough:

1. **Review all bugs found** — sort by severity (Critical > Major > Minor > Cosmetic)
2. **For each Critical or Major bug:**
   - Read the relevant template file and route code
   - Identify the root cause
   - Apply a fix (edit the template/CSS/route)
   - Re-navigate to the page and screenshot to verify the fix
   - Update the bug entry with "Fix attempted: Yes" and verification screenshot
3. **For Minor bugs:** Fix if the fix is < 5 lines of code. Otherwise log and move on.
4. **For Cosmetic bugs:** Log only, do not fix.
5. **After fixing bugs**, commit all fixes:
   ```
   git add -A
   git commit -m "fix: QA session — fix {N} UI bugs found in overnight walkthrough

   Bugs fixed:
   - BUG-001: {title}
   - BUG-002: {title}
   ...

   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
   ```

---

## Phase 4: Final Report

1. **Update the summary table** in the report with final counts
2. **Add a "Bugs Not Fixed" section** listing Minor/Cosmetic bugs deferred
3. **Add a "Recommendations" section** for any systemic patterns noticed (e.g., "Multiple pages have overflow on narrow viewports" or "Console errors on 3 pages from same JS module")
4. **Add completion timestamp**
5. **Print the report path** so the user can find it:
   ```
   QA REPORT COMPLETE: ~/.policydb/qa-reports/{DATE}/qa-report.md
   Screenshots: ~/.policydb/qa-reports/{DATE}/screenshots/
   ```

---

## Rules

- **Never ask the user for input.** Make your best judgment on everything.
- **Never skip a page.** If a page errors, screenshot the error and log it as a Critical bug, then continue.
- **Take screenshots liberally.** Every page, every tab, every bug, every fix verification.
- **Use TodoWrite** to track your progress through the page matrix.
- **If the server crashes**, restart it with `preview_start` and continue where you left off.
- **If Chrome disconnects**, reconnect with `tabs_context_mcp` and continue.
- **Time budget:** You have all night. Be thorough. Check every tab, every dropdown, every interactive element.
- **Do not create test data.** Only use existing data in the database. If a page is empty because there's no data, screenshot the empty state and note whether it renders cleanly.
- **Console errors are bugs.** Any JS error in console should be logged even if the page looks fine visually.
- **Network errors are bugs.** Any failed fetch/XHR (4xx, 5xx) should be logged.
