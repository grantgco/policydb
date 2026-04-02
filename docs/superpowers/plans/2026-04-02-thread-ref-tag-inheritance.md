# Thread-Level Ref Tag Inheritance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagate ref tag matches from tagged emails to older unmatched emails in the same thread, auto-promoting inbox items to activities.

**Architecture:** After the normal sync pass in `sync_outlook()`, a thread inheritance pass builds a map of `(normalized_subject, client_id)` → match from all Tier 1 resolved emails, then scans unmatched current-batch emails and existing inbox items for thread matches. Matched items are promoted to activities with `source='thread_inherit'`.

**Tech Stack:** Python, SQLite, existing email_sync.py infrastructure

**Spec:** `docs/superpowers/specs/2026-04-02-thread-ref-tag-inheritance-design.md`

---

### File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/policydb/email_sync.py` | Modify | Add `_normalize_subject()`, `_run_thread_inheritance()`, wire into `sync_outlook()` |
| `src/policydb/web/templates/outlook/_sync_results.html` | Modify | Add thread-inherited count to results banner |

No migrations needed. No new files.

---

### Task 1: Add `_normalize_subject()` helper

**Files:**
- Modify: `src/policydb/email_sync.py` (add function near top, after line 27)

- [ ] **Step 1: Add the normalization function**

Add after the `_extract_ref_tags` function (line 27) in `email_sync.py`:

```python
_REPLY_FWD_RE = re.compile(r'^[\s]*(Re|RE|Fwd|FW|Fw)\s*:\s*', re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    """Normalize an email subject for thread comparison.

    Strips Re:/Fwd:/FW: prefixes (repeated/nested), collapses whitespace, lowercases.
    """
    s = subject or ""
    # Repeatedly strip reply/forward prefixes
    while True:
        stripped = _REPLY_FWD_RE.sub('', s)
        if stripped == s:
            break
        s = stripped
    # Collapse whitespace and lowercase
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s
```

- [ ] **Step 2: Verify manually**

Quick sanity check — in a Python shell from the project root:

```bash
cd /Users/grantgreeson/Documents/Projects/policydb
python -c "
from policydb.email_sync import _normalize_subject
assert _normalize_subject('RE: FW: Re: GL Renewal Discussion') == 'gl renewal discussion'
assert _normalize_subject('Fwd: Fw: Hello World') == 'hello world'
assert _normalize_subject('  Re:  Re:Re: test  subject  ') == 'test subject'
assert _normalize_subject('No prefix here') == 'no prefix here'
assert _normalize_subject('') == ''
print('All assertions passed')
"
```

Expected: `All assertions passed`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_sync.py
git commit -m "feat: add _normalize_subject() for thread matching"
```

---

### Task 2: Add `_run_thread_inheritance()` function

**Files:**
- Modify: `src/policydb/email_sync.py` (add function before `sync_outlook`, around line 480)

- [ ] **Step 1: Add the thread inheritance function**

Add before the `sync_outlook()` function in `email_sync.py`:

```python
def _run_thread_inheritance(
    conn: sqlite3.Connection,
    current_batch_inbox: list[dict],
    results: dict,
) -> None:
    """Propagate ref tag matches to unmatched emails in the same thread.

    1. Build thread map from Tier 1 matched activities (current + historical)
    2. Scan unmatched items (current batch inbox + existing inbox items)
    3. Promote matches to activities with source='thread_inherit'

    Args:
        conn: Database connection
        current_batch_inbox: List of dicts from results["suggestions"] — emails
            that went to inbox during the current sync batch. Each has keys:
            subject, sender, folder, date, category, inbox_uid.
        results: The sync results dict — mutated to add thread_inherited count.
    """
    results["thread_inherited"] = 0

    # ── 1. Build thread map from Tier 1 matched activities ──
    # Recent activities created by outlook_sync with message IDs (Tier 1 matches)
    matched_rows = conn.execute("""
        SELECT a.client_id, a.policy_id, a.program_id, a.issue_id,
               a.subject, a.outlook_message_id
        FROM activity_log a
        WHERE a.source IN ('outlook_sync', 'thread_inherit')
          AND a.outlook_message_id IS NOT NULL
          AND a.client_id IS NOT NULL
          AND a.activity_date >= date('now', '-90 days')
    """).fetchall()

    # thread_map: (normalized_subject, client_id) -> match dict
    thread_map: dict[tuple[str, int], dict] = {}
    for row in matched_rows:
        norm = _normalize_subject(row["subject"])
        if not norm:
            continue
        key = (norm, row["client_id"])
        match = {
            "client_id": row["client_id"],
            "policy_id": row["policy_id"],
            "program_id": row["program_id"],
            "issue_id": row["issue_id"],
        }
        # Keep the most recent match per thread key (later rows = more recent)
        thread_map[key] = match

    if not thread_map:
        return

    # ── 2. Collect unmatched inbox items (current batch + historical) ──
    inbox_candidates = []

    # 2a. Current batch items that went to inbox
    for suggestion in current_batch_inbox:
        inbox_uid = suggestion.get("inbox_uid", "")
        if not inbox_uid:
            continue
        inbox_row = conn.execute(
            "SELECT id, content, outlook_message_id, email_subject, email_date FROM inbox WHERE inbox_uid = ?",
            (inbox_uid,),
        ).fetchone()
        if inbox_row:
            inbox_candidates.append(dict(inbox_row))

    # 2b. Historical inbox items with outlook_message_id (from prior syncs)
    historical = conn.execute("""
        SELECT id, content, outlook_message_id, email_subject, email_date
        FROM inbox
        WHERE outlook_message_id IS NOT NULL
          AND status = 'pending'
    """).fetchall()
    seen_ids = {c["id"] for c in inbox_candidates}
    for row in historical:
        if row["id"] not in seen_ids:
            inbox_candidates.append(dict(row))

    # ── 3. Try to match each candidate via thread map ──
    for candidate in inbox_candidates:
        subject = candidate.get("email_subject") or ""
        if not subject:
            # Try to extract subject from content (format: "[Outlook Sent] Subject\nFrom: ...")
            content = candidate.get("content", "")
            first_line = content.split("\n")[0] if content else ""
            # Strip [Outlook Sent/Received/Flagged] prefix
            subject = re.sub(r'^\[Outlook [^\]]*\]\s*', '', first_line)

        norm = _normalize_subject(subject)
        if not norm:
            continue

        # Extract email addresses from content for domain matching
        content = candidate.get("content", "")
        addresses = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)

        # Try domain match to get candidate client_id
        domain_match = _match_by_domain(conn, addresses)
        if not domain_match:
            continue
        candidate_client_id = domain_match.get("client_id")
        if not candidate_client_id:
            continue

        # Look up in thread map
        key = (norm, candidate_client_id)
        inherited_match = thread_map.get(key)
        if not inherited_match:
            continue

        # Check dedup — skip if outlook_message_id already exists as activity
        message_id = candidate.get("outlook_message_id", "")
        if message_id:
            dismissed = conn.execute(
                "SELECT 1 FROM dismissed_outlook_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
            if dismissed:
                continue
            existing = conn.execute(
                "SELECT 1 FROM activity_log WHERE outlook_message_id=?",
                (message_id,),
            ).fetchone()
            if existing:
                continue

        # ── Promote: create activity from inbox item ──
        email_date = (candidate.get("email_date") or "")[:10]
        if not email_date:
            email_date = datetime.now().strftime("%Y-%m-%d")

        # Extract sender from content
        sender = ""
        for line in (candidate.get("content") or "").split("\n"):
            if line.startswith("From: "):
                sender = line[6:].strip()
                break

        # Resolve contact from sender
        contact_id = None
        contact_person = sender
        if sender:
            contact = conn.execute(
                "SELECT id, name FROM contacts WHERE LOWER(TRIM(email))=?",
                (sender.strip().lower(),),
            ).fetchone()
            if contact:
                contact_id = contact["id"]
                contact_person = contact["name"] or sender

        # Build snippet from content (skip the header lines)
        content_lines = (candidate.get("content") or "").split("\n")
        snippet_lines = [l for l in content_lines[4:] if l.strip()]  # Skip header lines
        snippet = "\n".join(snippet_lines)[:2500]

        conn.execute(
            """INSERT INTO activity_log
               (activity_date, client_id, policy_id, program_id, activity_type, subject,
                details, contact_person, contact_id, source, outlook_message_id,
                email_snippet, issue_id, follow_up_done)
               VALUES (?, ?, ?, ?, 'Email', ?, ?, ?, ?, 'thread_inherit', ?, ?, ?, 1)""",
            (
                email_date,
                inherited_match["client_id"],
                inherited_match.get("policy_id"),
                inherited_match.get("program_id"),
                subject,
                "Linked via thread inheritance",
                contact_person,
                contact_id,
                message_id,
                snippet,
                inherited_match.get("issue_id"),
            ),
        )

        # Remove from inbox
        conn.execute("DELETE FROM inbox WHERE id = ?", (candidate["id"],))
        conn.commit()

        results["thread_inherited"] += 1
        logger.info(
            "Thread inherit: promoted inbox %s → client %s (subject: %s)",
            candidate.get("id"), inherited_match["client_id"], norm,
        )
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import py_compile; py_compile.compile('src/policydb/email_sync.py', doraise=True)"
```

Expected: no output (clean compile)

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_sync.py
git commit -m "feat: add _run_thread_inheritance() for thread-level ref tag propagation"
```

---

### Task 3: Wire thread inheritance into `sync_outlook()`

**Files:**
- Modify: `src/policydb/email_sync.py` — `sync_outlook()` function

- [ ] **Step 1: Initialize the results counter**

In the `results` dict initialization inside `sync_outlook()` (around line 498), add `thread_inherited`:

Find:
```python
    results = {
        "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
        "suggestions": [],
        "skipped": 0,
        "errors": [],
        "total_scanned": 0,
        "since": since.strftime("%b %d, %Y %H:%M"),
        "new_contacts_found": 0,
    }
```

Replace with:
```python
    results = {
        "auto_linked": {"sent": 0, "received": 0, "flagged": 0},
        "suggestions": [],
        "skipped": 0,
        "errors": [],
        "total_scanned": 0,
        "since": since.strftime("%b %d, %Y %H:%M"),
        "new_contacts_found": 0,
        "thread_inherited": 0,
    }
```

- [ ] **Step 2: Call thread inheritance after the three scan phases**

In `sync_outlook()`, add the thread inheritance call after the flagged scan block and before the `new_contacts_found` count query. Find the line:

```python
    # Count contacts captured during this sync run
    results["new_contacts_found"] = conn.execute(
```

Insert before it:

```python
    # ── Thread inheritance pass ─────────────────────────────────────
    try:
        _run_thread_inheritance(conn, list(results["suggestions"]), results)
    except Exception as e:
        logger.exception("Thread inheritance pass failed: %s", e)
        results["errors"].append(f"Thread inheritance error: {e}")

```

- [ ] **Step 3: Update the log summary to include thread_inherited**

Find the logger.info call at the end of `sync_outlook()`:

```python
    logger.info(
        "Outlook sync complete: %d scanned, %d auto-linked (sent=%d recv=%d flag=%d), "
        "%d suggestions, %d skipped",
        results["total_scanned"],
        sum(results["auto_linked"].values()),
        results["auto_linked"]["sent"],
        results["auto_linked"]["received"],
        results["auto_linked"]["flagged"],
        len(results["suggestions"]),
        results["skipped"],
    )
```

Replace with:

```python
    logger.info(
        "Outlook sync complete: %d scanned, %d auto-linked (sent=%d recv=%d flag=%d), "
        "%d thread-inherited, %d suggestions, %d skipped",
        results["total_scanned"],
        sum(results["auto_linked"].values()),
        results["auto_linked"]["sent"],
        results["auto_linked"]["received"],
        results["auto_linked"]["flagged"],
        results["thread_inherited"],
        len(results["suggestions"]),
        results["skipped"],
    )
```

- [ ] **Step 4: Verify syntax**

```bash
python -c "import py_compile; py_compile.compile('src/policydb/email_sync.py', doraise=True)"
```

Expected: no output (clean compile)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/email_sync.py
git commit -m "feat: wire thread inheritance into sync_outlook() pipeline"
```

---

### Task 4: Update sync results template

**Files:**
- Modify: `src/policydb/web/templates/outlook/_sync_results.html`

- [ ] **Step 1: Add thread-inherited count to the grid**

Change the summary grid from `grid-cols-4` to `grid-cols-5` and add the thread-inherited column. Find:

```html
  <div class="grid grid-cols-4 gap-3 mb-3">
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.sent }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Sent</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.received }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Received</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.flagged }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Flagged</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ skipped }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Skipped</div>
    </div>
  </div>
```

Replace with:

```html
  <div class="grid grid-cols-5 gap-3 mb-3">
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.sent }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Sent</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.received }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Received</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ auto_linked.flagged }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Flagged</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold {% if thread_inherited %}text-indigo-700{% else %}text-gray-900{% endif %}">{{ thread_inherited }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Via Thread</div>
    </div>
    <div class="text-center">
      <div class="text-lg font-semibold text-gray-900">{{ skipped }}</div>
      <div class="text-[10px] text-gray-500 uppercase">Skipped</div>
    </div>
  </div>
```

- [ ] **Step 2: Verify template renders without errors**

Start the dev server and trigger a sync (or just verify the template parses):

```bash
python -c "
from jinja2 import Environment
env = Environment()
tpl = env.from_string(open('src/policydb/web/templates/outlook/_sync_results.html').read())
print('Template parsed OK')
"
```

Expected: `Template parsed OK`

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/outlook/_sync_results.html
git commit -m "feat: show thread-inherited count in sync results banner"
```

---

### Task 5: End-to-end QA via Outlook sync

**Files:** None (testing only)

- [ ] **Step 1: Start the dev server**

```bash
cd /Users/grantgreeson/Documents/Projects/policydb
~/.policydb/venv/bin/policydb serve --port 8042
```

- [ ] **Step 2: Navigate to Action Center and trigger sync**

Open `http://127.0.0.1:8042/action-center` in the browser. Click "Sync Outlook". Verify:
- Sync completes without errors
- The results banner now shows 5 columns: Sent, Received, Flagged, Via Thread, Skipped
- The "Via Thread" count displays (0 is fine if no threads match)
- No visual overflow or layout issues in the 5-column grid

- [ ] **Step 3: Verify thread inheritance with real data (if available)**

If there are existing inbox items from prior syncs and matched activities with overlapping subjects:
- Check that inbox items with matching normalized subjects got promoted
- Verify the new activities show `source='thread_inherit'` in the database
- Verify the inbox items were removed

```bash
~/.policydb/venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('/Users/grantgreeson/.policydb/policydb.sqlite')
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT id, subject, client_id FROM activity_log WHERE source='thread_inherit' ORDER BY id DESC LIMIT 5\").fetchall()
print(f'{len(rows)} thread-inherited activities')
for r in rows:
    print(f'  id={r[\"id\"]} client={r[\"client_id\"]} subject={r[\"subject\"][:60]}')
"
```

- [ ] **Step 4: Take screenshots of the sync results banner**

Screenshot the Action Center after a sync to confirm the 5-column layout looks good.

- [ ] **Step 5: Final commit (if any QA fixes needed)**

```bash
git add -A
git commit -m "fix: QA adjustments for thread inheritance"
```
