# Knowledge Base Reference Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign KB with unified type-aware cards, expanded filters, bi-directional linking across all entity types, and backlinks panels on every detail page.

**Architecture:** New `kb_links` table replaces split `kb_record_links`/`record_attachments` for KB linking. Unified search route gains source, sort, and linked-to filter params. A shared `_references_panel.html` partial is embedded via HTMX on all detail pages. Cards get Style B treatment (left border color + source line).

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, Tailwind CSS (CDN)

**Spec:** `docs/superpowers/specs/2026-04-02-kb-reference-web-design.md`

---

## File Structure

### New Files
- `src/policydb/migrations/131_kb_links.sql` — new linking table + data migration
- `src/policydb/web/templates/kb/_references_panel.html` — shared backlinks partial

### Modified Files
- `src/policydb/db.py` — wire migration 131
- `src/policydb/web/routes/kb.py` — new link API, expanded search, source-aware index
- `src/policydb/web/templates/kb/index.html` — expanded filter bar
- `src/policydb/web/templates/kb/_card.html` — Style B visual identity
- `src/policydb/web/templates/kb/_search_results.html` — link count badge
- `src/policydb/web/templates/kb/article.html` — replace record links with references panel
- `src/policydb/web/templates/kb/document.html` — replace record links with references panel
- `src/policydb/web/templates/kb/_entity_kb_links.html` — update to use kb_links
- `src/policydb/web/templates/kb/_entry_search_results.html` — update link target
- `src/policydb/web/templates/issues/detail.html` — add References section
- `src/policydb/web/templates/clients/_sticky_sidebar.html` — update KB section to use kb_links
- `src/policydb/web/templates/clients/project.html` — add References section
- `src/policydb/web/templates/policies/_tab_details.html` — add References section

---

### Task 1: Migration — kb_links Table

**Files:**
- Create: `src/policydb/migrations/131_kb_links.sql`
- Modify: `src/policydb/db.py:365` (add 131 to `_KNOWN_MIGRATIONS`)
- Modify: `src/policydb/db.py` (add migration block after line 1768)

- [ ] **Step 1: Create migration SQL file**

Create `src/policydb/migrations/131_kb_links.sql`:

```sql
-- Unified bi-directional linking table for Knowledge Base reference web.
-- Replaces split kb_record_links / record_attachments for KB-related links.

CREATE TABLE IF NOT EXISTS kb_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,   -- 'kb_article', 'attachment', 'issue', 'policy', 'client', 'activity', 'project'
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_type, source_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_links_source ON kb_links(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kb_links_target ON kb_links(target_type, target_id);

-- Migrate existing kb_record_links into kb_links
INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id, created_at)
SELECT entry_type, entry_id, entity_type, entity_id, linked_at
FROM kb_record_links;

-- Migrate record_attachments where record_type = 'kb_article' into kb_links
INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id, created_at)
SELECT 'attachment', attachment_id, 'kb_article', record_id, created_at
FROM record_attachments
WHERE record_type = 'kb_article';
```

- [ ] **Step 2: Wire migration into init_db()**

In `src/policydb/db.py`:
1. Add `131` to the `_KNOWN_MIGRATIONS` set on line 365
2. After the migration 130 block (after line 1768), add:

```python
    if 131 not in applied:
        conn.executescript((_MIGRATIONS_DIR / "131_kb_links.sql").read_text())
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (131, "Unified kb_links table for bi-directional reference web"),
        )
        conn.commit()
        logger.info("Migration 131: created kb_links table with data migration")
```

- [ ] **Step 3: Verify migration runs**

```bash
cd /Users/grantgreeson/Documents/Projects/policydb
python -c "from policydb.db import init_db; init_db()"
```

Expected: Migration 131 logged, no errors. Verify table exists:

```bash
sqlite3 ~/.policydb/policydb.sqlite ".schema kb_links"
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/131_kb_links.sql src/policydb/db.py
git commit -m "feat: add kb_links table for bi-directional reference web (migration 131)"
```

---

### Task 2: Card Template — Style B Visual Identity

**Files:**
- Modify: `src/policydb/web/templates/kb/_card.html`
- Modify: `src/policydb/web/routes/kb.py:106-126` (add `source` field to document entries)

- [ ] **Step 1: Update route to pass source info for documents**

In `src/policydb/web/routes/kb.py`, in the `kb_index` function (line 118-125), and the `kb_search` function (line 168-185), for each document entry add a `source_type` field after setting `entry_type`:

In `kb_index` (after line 123 `d["file_size_fmt"] = ...`):
```python
        d["source_type"] = d.get("source", "local")  # 'local' or 'devonthink'
```

In `kb_search` (after line 184 `d["file_size_fmt"] = ...`):
```python
            d["source_type"] = d.get("source", "local")
```

Also add `source_type = "article"` for articles in both functions:
- In `kb_index` after line 116 `d["colors"] = ...`: add `d["source_type"] = "article"`
- In `kb_search` after line 165 `d["colors"] = ...`: add `d["source_type"] = "article"`

- [ ] **Step 2: Rewrite _card.html with Style B**

Replace the full content of `src/policydb/web/templates/kb/_card.html` with:

```html
{% set colors = entry.colors %}
{% set is_doc = entry.entry_type == 'document' %}
{% set is_dt = entry.source_type == 'devonthink' %}
{% set url = '/kb/documents/' ~ entry.uid if is_doc else '/kb/articles/' ~ entry.uid %}

{# Border color: articles=blue, DEVONthink=purple, local files=file-type color #}
{% if not is_doc %}
  {% set border_color = '#0B4BFF' %}
{% elif is_dt %}
  {% set border_color = '#8b5cf6' %}
{% elif entry.file_info.label == 'PDF' %}
  {% set border_color = '#ef4444' %}
{% elif entry.file_info.label == 'Word' %}
  {% set border_color = '#3b82f6' %}
{% elif entry.file_info.label == 'Excel' %}
  {% set border_color = '#22c55e' %}
{% elif entry.file_info.label == 'PowerPoint' %}
  {% set border_color = '#f59e0b' %}
{% else %}
  {% set border_color = '#9ca3af' %}
{% endif %}

<a href="{{ url }}" class="block bg-white border border-[#e5e2dc] rounded-lg p-3 hover:shadow-md transition-shadow" style="border-left: 4px solid {{ border_color }}">
  {# Row 1: UID + Title + Category + Size + Date #}
  <div class="flex items-center gap-2">
    <span class="text-[9px] font-mono bg-[#f3f2ee] text-gray-500 rounded px-1.5 py-0.5 flex-shrink-0">{{ entry.uid }}</span>

    <span class="text-gray-900 font-semibold text-sm flex-1 min-w-0 truncate">{{ entry.title }}</span>

    <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium {{ colors.bg }} {{ colors.text }} flex-shrink-0">
      {{ entry.category }}
    </span>

    {% if is_doc and entry.file_size_fmt %}
    <span class="text-[10px] text-gray-400 flex-shrink-0">{{ entry.file_size_fmt }}</span>
    {% endif %}

    {% if entry.link_count is defined and entry.link_count > 0 %}
    <span class="text-[9px] text-gray-400 flex-shrink-0" title="{{ entry.link_count }} reference{{ 's' if entry.link_count != 1 else '' }}">
      <svg class="w-3 h-3 inline -mt-px" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
      {{ entry.link_count }}
    </span>
    {% endif %}

    <span class="text-[10px] text-gray-400 flex-shrink-0 whitespace-nowrap">
      {{ entry.updated_at[:10] if entry.updated_at else '' }}
    </span>
  </div>

  {# Row 2: Source line — content preview, filename, or DEVONthink indicator #}
  <div class="text-xs mt-1 ml-0">
    {% if not is_doc %}
      {# Article: content preview #}
      {% if entry.content %}
      <div class="text-gray-500 line-clamp-2">{{ entry.content[:200] | replace('\n', ' ') | replace('#', '') | truncate(150) }}</div>
      {% endif %}
    {% elif is_dt %}
      {# DEVONthink: link icon + label + filename #}
      <div>
        <svg class="w-3 h-3 inline -mt-px" fill="none" stroke="#8b5cf6" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path stroke-linecap="round" stroke-linejoin="round" d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>
        <span class="text-[#8b5cf6] font-medium">DEVONthink</span>
        <span class="text-gray-400">&middot; {{ entry.filename }}</span>
      </div>
    {% else %}
      {# Local file: file icon + filename #}
      <div class="text-gray-500">
        <svg class="w-3 h-3 inline -mt-px {{ entry.file_info.color }}" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/></svg>
        {{ entry.filename }}
      </div>
    {% endif %}
  </div>

  {# Row 3: Tags + Source badge #}
  {% if entry.tags_list or (entry.get('source') and entry.source != 'authored') %}
  <div class="flex items-center gap-1.5 mt-1.5">
    {% for tag in entry.tags_list[:4] %}
    <span class="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600 font-medium">{{ tag }}</span>
    {% endfor %}
    {% if entry.tags_list | length > 4 %}
    <span class="text-[9px] text-gray-400">+{{ entry.tags_list | length - 4 }}</span>
    {% endif %}

    {% if entry.get('source') and entry.source not in ('authored', 'local') %}
    <span class="ml-auto text-[9px] px-1.5 py-0.5 rounded-full {% if entry.source == 'llm-assisted' %}bg-blue-50 text-blue-600{% else %}bg-amber-50 text-amber-600{% endif %} font-medium">
      {{ entry.source }}
    </span>
    {% endif %}
  </div>
  {% endif %}
</a>
```

- [ ] **Step 3: Verify card rendering**

Start the server and navigate to `/kb`. Verify:
- Article cards have blue left border
- Document cards have file-type colored border
- DEVONthink links (if any) have purple border with "DEVONthink" source line
- All cards show UID, title, category badge, date
- No overflow or layout issues

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/kb.py src/policydb/web/templates/kb/_card.html
git commit -m "feat: Style B card design — type-specific borders and source lines"
```

---

### Task 3: Expanded Filter Bar

**Files:**
- Modify: `src/policydb/web/templates/kb/index.html`

- [ ] **Step 1: Replace filter bar in index.html**

Replace the `<!-- Search + Filters -->` section (lines 27-58) with:

```html
  <!-- Search + Filters -->
  <div class="mb-5">
    <div class="flex items-center gap-3 mb-3">
      <div class="relative flex-1">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
        </svg>
        <input type="search" id="kb-search" placeholder="Search articles and documents..."
               class="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-marsh focus:border-marsh outline-none"
               hx-get="/kb/search" hx-trigger="keyup changed delay:300ms" hx-target="#kb-results"
               hx-include="#kb-filters" name="q">
      </div>
      <div id="kb-filters" class="flex items-center gap-2">
        <!-- Source toggle -->
        <button class="btn-filter active" onclick="setSourceFilter(this, '')">All</button>
        <button class="btn-filter" onclick="setSourceFilter(this, 'article')">Articles</button>
        <button class="btn-filter" onclick="setSourceFilter(this, 'local')">Files</button>
        <button class="btn-filter" onclick="setSourceFilter(this, 'devonthink')">DEVONthink</button>
        <input type="hidden" name="source_filter" id="kb-source-filter" value="">
        <input type="hidden" name="category" id="kb-cat-filter" value="">
        <input type="hidden" name="sort" id="kb-sort-filter" value="updated">
        <input type="hidden" name="linked_type" id="kb-linked-type" value="">
        <input type="hidden" name="linked_id" id="kb-linked-id" value="">
      </div>
    </div>

    <div class="flex items-center gap-3 flex-wrap">
      <!-- Category pills -->
      <div class="flex items-center gap-1.5 flex-wrap flex-1">
        <button class="rounded-full px-3 py-1 text-xs font-medium bg-marsh text-white"
                onclick="setCatFilter(this, '')">All</button>
        {% for cat in categories %}
        <button class="rounded-full px-3 py-1 text-xs font-medium border border-gray-200 text-gray-600 hover:border-gray-400 transition-colors"
                onclick="setCatFilter(this, '{{ cat }}')">{{ cat }}</button>
        {% endfor %}
      </div>

      <!-- Sort -->
      <select id="kb-sort-select" onchange="setSortFilter(this.value)"
              class="text-xs border border-gray-200 rounded-lg px-2 py-1.5 text-gray-600 focus:ring-1 focus:ring-marsh outline-none">
        <option value="updated" selected>Recently Updated</option>
        <option value="title">Title A-Z</option>
        <option value="most_linked">Most Linked</option>
        <option value="category">Category</option>
      </select>

      <!-- Linked-to filter -->
      <div class="relative">
        <input type="text" id="kb-linked-search" placeholder="Linked to..."
               class="text-xs border border-gray-200 rounded-lg pl-2 pr-6 py-1.5 w-40 text-gray-600 focus:ring-1 focus:ring-marsh outline-none"
               hx-get="/kb/search-linkable" hx-trigger="keyup changed delay:300ms"
               hx-target="#kb-linked-results" name="q"
               autocomplete="off">
        <button id="kb-linked-clear" class="hidden absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-sm"
                onclick="clearLinkedFilter()">&times;</button>
        <div id="kb-linked-results" class="absolute top-full left-0 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto hidden"></div>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Update the script block**

Replace the entire `<script>` block at the bottom of `index.html` with:

```html
<script>
function _kbRefresh() {
  htmx.ajax('GET', '/kb/search', {
    target: '#kb-results',
    values: {
      q: document.getElementById('kb-search').value,
      source_filter: document.getElementById('kb-source-filter').value,
      category: document.getElementById('kb-cat-filter').value,
      sort: document.getElementById('kb-sort-filter').value,
      linked_type: document.getElementById('kb-linked-type').value,
      linked_id: document.getElementById('kb-linked-id').value
    }
  });
}
function setSourceFilter(btn, val) {
  document.getElementById('kb-source-filter').value = val;
  btn.closest('#kb-filters').querySelectorAll('.btn-filter').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  _kbRefresh();
}
function setCatFilter(btn, val) {
  document.getElementById('kb-cat-filter').value = val;
  var pills = btn.parentElement.querySelectorAll('button');
  pills.forEach(function(p) {
    p.className = 'rounded-full px-3 py-1 text-xs font-medium border border-gray-200 text-gray-600 hover:border-gray-400 transition-colors';
  });
  btn.className = 'rounded-full px-3 py-1 text-xs font-medium bg-marsh text-white';
  _kbRefresh();
}
function setSortFilter(val) {
  document.getElementById('kb-sort-filter').value = val;
  _kbRefresh();
}
function selectLinkedEntity(type, id, label) {
  document.getElementById('kb-linked-type').value = type;
  document.getElementById('kb-linked-id').value = id;
  document.getElementById('kb-linked-search').value = label;
  document.getElementById('kb-linked-results').classList.add('hidden');
  document.getElementById('kb-linked-clear').classList.remove('hidden');
  _kbRefresh();
}
function clearLinkedFilter() {
  document.getElementById('kb-linked-type').value = '';
  document.getElementById('kb-linked-id').value = '';
  document.getElementById('kb-linked-search').value = '';
  document.getElementById('kb-linked-clear').classList.add('hidden');
  _kbRefresh();
}
document.addEventListener('click', function(e) {
  var results = document.getElementById('kb-linked-results');
  if (results && !results.contains(e.target) && e.target.id !== 'kb-linked-search') {
    results.classList.add('hidden');
  }
});
</script>
```

- [ ] **Step 3: Verify filter bar renders correctly**

Navigate to `/kb`, verify:
- Source toggle shows All / Articles / Files / DEVONthink
- Category pills render from config
- Sort dropdown appears with 4 options
- Linked-to combobox appears with placeholder

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/kb/index.html
git commit -m "feat: expanded KB filter bar — source toggle, sort, linked-to combobox"
```

---

### Task 4: Updated Search Route + Linkable Search Endpoint

**Files:**
- Modify: `src/policydb/web/routes/kb.py:140-193` (search route)
- Modify: `src/policydb/web/routes/kb.py` (add search-linkable endpoint)

- [ ] **Step 1: Replace the kb_search route**

Replace the `kb_search` function (lines 140-193) with:

```python
@router.get("/search", response_class=HTMLResponse)
async def kb_search(
    request: Request,
    q: str = Query(""),
    category: str = Query(""),
    source_filter: str = Query(""),
    sort: str = Query("updated"),
    linked_type: str = Query(""),
    linked_id: int = Query(0),
    conn=Depends(get_db),
):
    pattern = f"%{q}%"
    entries = []

    # If linked-to filter is active, get the set of linked entry IDs
    linked_article_ids = None
    linked_attachment_ids = None
    if linked_type and linked_id:
        link_rows = conn.execute(
            "SELECT source_type, source_id, target_type, target_id FROM kb_links "
            "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
            (linked_type, linked_id, linked_type, linked_id),
        ).fetchall()
        linked_article_ids = set()
        linked_attachment_ids = set()
        for lr in link_rows:
            lr = dict(lr)
            # The "other" side of the link is what we want
            if lr["source_type"] == linked_type and lr["source_id"] == linked_id:
                other_type, other_id = lr["target_type"], lr["target_id"]
            else:
                other_type, other_id = lr["source_type"], lr["source_id"]
            if other_type == "kb_article":
                linked_article_ids.add(other_id)
            elif other_type == "attachment":
                linked_attachment_ids.add(other_id)

    # Articles
    if source_filter in ("", "article"):
        where = "WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
        params = [pattern, pattern, pattern]
        if category:
            where += " AND category = ?"
            params.append(category)
        articles = conn.execute(
            f"SELECT * FROM kb_articles {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        for a in articles:
            d = dict(a)
            if linked_article_ids is not None and d["id"] not in linked_article_ids:
                continue
            d["entry_type"] = "article"
            d["source_type"] = "article"
            d["tags_list"] = _parse_tags(d.get("tags"))
            d["colors"] = _get_colors(d["category"])
            d["link_count"] = _get_link_count(conn, "kb_article", d["id"])
            entries.append(d)

    # Attachments (local and devonthink)
    if source_filter in ("", "local", "devonthink"):
        where = "WHERE (title LIKE ? OR description LIKE ? OR filename LIKE ? OR tags LIKE ?)"
        params = [pattern, pattern, pattern, pattern]
        if category:
            where += " AND category = ?"
            params.append(category)
        if source_filter in ("local", "devonthink"):
            where += " AND source = ?"
            params.append(source_filter)
        documents = conn.execute(
            f"SELECT * FROM attachments {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
        for doc in documents:
            d = dict(doc)
            if linked_attachment_ids is not None and d["id"] not in linked_attachment_ids:
                continue
            d["entry_type"] = "document"
            d["source_type"] = d.get("source", "local")
            d["tags_list"] = _parse_tags(d.get("tags"))
            d["colors"] = _get_colors(d["category"])
            d["file_info"] = _file_type_info(d.get("mime_type", ""), d.get("filename", ""))
            d["file_size_fmt"] = _format_file_size(d.get("file_size", 0) or 0)
            d["link_count"] = _get_link_count(conn, "attachment", d["id"])
            entries.append(d)

    # Sort
    if sort == "title":
        entries.sort(key=lambda e: (e.get("title") or "").lower())
    elif sort == "most_linked":
        entries.sort(key=lambda e: e.get("link_count", 0), reverse=True)
    elif sort == "category":
        entries.sort(key=lambda e: (e.get("category") or "").lower())
    else:  # "updated" default
        entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)

    return templates.TemplateResponse("kb/_search_results.html", {
        "request": request,
        "entries": entries,
        "category_colors": CATEGORY_COLORS,
    })
```

- [ ] **Step 2: Add `_get_link_count` helper**

Add this helper function above the search route (after `_file_type_info`):

```python
def _get_link_count(conn, entity_type: str, entity_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM kb_links "
        "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
        (entity_type, entity_id, entity_type, entity_id),
    ).fetchone()
    return row["cnt"] if row else 0
```

- [ ] **Step 3: Update kb_index to include source_type and link_count**

In the `kb_index` function (lines 100-137), add `source_type` and `link_count` to both article and document entries:

For articles (after `d["colors"] = _get_colors(d["category"])`):
```python
        d["source_type"] = "article"
        d["link_count"] = _get_link_count(conn, "kb_article", d["id"])
```

For documents (after `d["file_size_fmt"] = _format_file_size(d["file_size"])`):
```python
        d["source_type"] = d.get("source", "local")
        d["link_count"] = _get_link_count(conn, "attachment", d["id"])
```

- [ ] **Step 4: Add search-linkable endpoint**

Add this new route after the `search_entities` route:

```python
@router.get("/search-linkable", response_class=HTMLResponse)
async def search_linkable(
    request: Request,
    q: str = Query(""),
    conn=Depends(get_db),
):
    """Search all linkable entity types for the linked-to filter combobox."""
    if len(q.strip()) < 2:
        return HTMLResponse("")
    pattern = f"%{q}%"
    results = []

    # Clients
    for r in conn.execute(
        "SELECT id, name FROM clients WHERE name LIKE ? ORDER BY name LIMIT 5", (pattern,)
    ).fetchall():
        results.append({"type": "client", "id": r["id"], "label": r["name"], "icon": "client"})

    # Policies
    for r in conn.execute(
        "SELECT p.id, p.policy_uid, p.carrier, p.policy_type, c.name AS client_name "
        "FROM policies p LEFT JOIN clients c ON c.id = p.client_id "
        "WHERE p.policy_uid LIKE ? OR p.carrier LIKE ? OR c.name LIKE ? "
        "ORDER BY p.policy_uid LIMIT 5",
        (pattern, pattern, pattern),
    ).fetchall():
        label = f"{r['policy_uid']} — {r['carrier'] or ''} {r['policy_type'] or ''}".strip()
        results.append({"type": "policy", "id": r["id"], "label": label, "icon": "policy"})

    # Issues
    for r in conn.execute(
        "SELECT id, issue_uid, subject FROM issues WHERE issue_uid LIKE ? OR subject LIKE ? ORDER BY issue_uid DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "issue", "id": r["id"], "label": f"{r['issue_uid']} — {r['subject']}", "icon": "issue"})

    # KB Articles
    for r in conn.execute(
        "SELECT id, uid, title FROM kb_articles WHERE uid LIKE ? OR title LIKE ? ORDER BY updated_at DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "kb_article", "id": r["id"], "label": f"{r['uid']} — {r['title']}", "icon": "article"})

    # Attachments
    for r in conn.execute(
        "SELECT id, uid, title FROM attachments WHERE uid LIKE ? OR title LIKE ? ORDER BY updated_at DESC LIMIT 5",
        (pattern, pattern),
    ).fetchall():
        results.append({"type": "attachment", "id": r["id"], "label": f"{r['uid']} — {r['title']}", "icon": "document"})

    # Projects
    for r in conn.execute(
        "SELECT id, name FROM projects WHERE name LIKE ? ORDER BY name LIMIT 5",
        (pattern,),
    ).fetchall():
        results.append({"type": "project", "id": r["id"], "label": r["name"], "icon": "project"})

    # Render inline HTML for dropdown
    if not results:
        return HTMLResponse('<div class="px-3 py-2 text-xs text-gray-400">No results</div>')

    html_parts = []
    for r in results:
        html_parts.append(
            f'<button type="button" class="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 flex items-center gap-2" '
            f'onclick="selectLinkedEntity(\'{r["type"]}\', {r["id"]}, \'{r["label"].replace(chr(39), "&#39;")}\');">'
            f'<span class="text-[9px] uppercase font-medium text-gray-400 w-12">{r["type"].replace("kb_article","article").replace("attachment","file")}</span>'
            f'<span class="text-gray-700 truncate">{r["label"]}</span>'
            f'</button>'
        )
    html = '<div class="py-1">' + ''.join(html_parts) + '</div>'
    return HTMLResponse(html)
```

Also add an `htmx:afterOnLoad` handler to show/hide the dropdown. Add this JS snippet inside the `<script>` block in `index.html` (at the end, before `</script>`):

```javascript
document.body.addEventListener('htmx:afterOnLoad', function(e) {
  if (e.detail.target && e.detail.target.id === 'kb-linked-results') {
    var el = e.detail.target;
    el.classList.toggle('hidden', el.innerHTML.trim() === '');
  }
});
```

- [ ] **Step 5: Verify filters work**

Navigate to `/kb`, test:
- Source toggle filters correctly
- Category pills work with source toggle (AND logic)
- Sort changes order
- Linked-to search shows results, selecting one filters the list
- Clear button on linked-to resets

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/kb.py src/policydb/web/templates/kb/index.html
git commit -m "feat: expanded KB search — source filter, sort, linked-to combobox, link counts"
```

---

### Task 5: Link/Unlink API Using kb_links

**Files:**
- Modify: `src/policydb/web/routes/kb.py` (new endpoints, update existing)

- [ ] **Step 1: Add new link/unlink endpoints**

Add these routes after the existing `unlink_record` route:

```python
# ── Unified kb_links API ──────────────────────────────────────────────────────

@router.post("/links", response_class=HTMLResponse)
async def create_kb_link(
    request: Request,
    source_type: str = Form(...),
    source_id: int = Form(...),
    target_type: str = Form(...),
    target_id: int = Form(...),
    return_panel_for_type: str = Form(""),
    return_panel_for_id: int = Form(0),
    conn=Depends(get_db),
):
    """Create a bi-directional link between two entities."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id) VALUES (?, ?, ?, ?)",
            (source_type, source_id, target_type, target_id),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to create kb_link")

    # Return refreshed references panel for the requesting entity
    if return_panel_for_type and return_panel_for_id:
        return await references_panel(request, return_panel_for_type, return_panel_for_id, conn)
    return HTMLResponse("")


@router.delete("/links/{link_id}", response_class=HTMLResponse)
async def delete_kb_link(
    request: Request,
    link_id: int,
    return_panel_for_type: str = Query(""),
    return_panel_for_id: int = Query(0),
    conn=Depends(get_db),
):
    """Remove a link."""
    conn.execute("DELETE FROM kb_links WHERE id = ?", (link_id,))
    conn.commit()

    if return_panel_for_type and return_panel_for_id:
        return await references_panel(request, return_panel_for_type, return_panel_for_id, conn)
    return HTMLResponse("")


@router.get("/references/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def references_panel(
    request: Request,
    entity_type: str,
    entity_id: int,
    conn=Depends(get_db),
):
    """Get all references for an entity — used to render the references panel partial."""
    rows = conn.execute(
        "SELECT id, source_type, source_id, target_type, target_id FROM kb_links "
        "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
        (entity_type, entity_id, entity_type, entity_id),
    ).fetchall()

    references = []
    for r in rows:
        r = dict(r)
        # Determine the "other" entity
        if r["source_type"] == entity_type and r["source_id"] == entity_id:
            ref_type, ref_id = r["target_type"], r["target_id"]
        else:
            ref_type, ref_id = r["source_type"], r["source_id"]

        ref = {"link_id": r["id"], "type": ref_type, "id": ref_id}

        # Resolve display info
        if ref_type == "kb_article":
            entry = conn.execute("SELECT uid, title, category FROM kb_articles WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = entry["uid"]
                ref["title"] = entry["title"]
                ref["category"] = entry["category"]
                ref["url"] = f"/kb/articles/{entry['uid']}"
                ref["colors"] = _get_colors(entry["category"])
        elif ref_type == "attachment":
            entry = conn.execute("SELECT uid, title, category, source FROM attachments WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = entry["uid"]
                ref["title"] = entry["title"]
                ref["category"] = entry["category"]
                ref["url"] = f"/kb/documents/{entry['uid']}"
                ref["colors"] = _get_colors(entry["category"])
                ref["source"] = entry["source"]
        elif ref_type == "client":
            entry = conn.execute("SELECT id, name FROM clients WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = f"CLT-{entry['id']}"
                ref["title"] = entry["name"]
                ref["url"] = f"/clients/{entry['id']}"
        elif ref_type == "policy":
            entry = conn.execute(
                "SELECT p.id, p.policy_uid, p.policy_type, p.carrier, c.name AS client_name "
                "FROM policies p LEFT JOIN clients c ON c.id = p.client_id WHERE p.id = ?",
                (ref_id,),
            ).fetchone()
            if entry:
                ref["uid"] = entry["policy_uid"]
                ref["title"] = f"{entry['carrier'] or ''} {entry['policy_type'] or ''}".strip() or entry["policy_uid"]
                ref["url"] = f"/policies/{entry['policy_uid']}"
        elif ref_type == "issue":
            entry = conn.execute("SELECT id, issue_uid, subject FROM issues WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = entry["issue_uid"]
                ref["title"] = entry["subject"]
                ref["url"] = f"/issues/{entry['issue_uid']}"
        elif ref_type == "activity":
            entry = conn.execute("SELECT id, summary FROM activity_log WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = f"ACT-{entry['id']}"
                ref["title"] = entry["summary"] or "Activity"
                ref["url"] = "#"  # Activities don't have standalone pages
        elif ref_type == "project":
            entry = conn.execute("SELECT id, name FROM projects WHERE id = ?", (ref_id,)).fetchone()
            if entry:
                ref["uid"] = f"PRJ-{entry['id']}"
                ref["title"] = entry["name"]
                ref["url"] = f"/clients/projects/{entry['id']}"

        if "title" in ref:
            references.append(ref)

    # Group by type for display
    type_order = ["kb_article", "attachment", "issue", "policy", "client", "activity", "project"]
    type_labels = {
        "kb_article": "Articles", "attachment": "Documents", "issue": "Issues",
        "policy": "Policies", "client": "Clients", "activity": "Activities", "project": "Projects",
    }
    grouped = {}
    for ref in references:
        grouped.setdefault(ref["type"], []).append(ref)
    ordered_groups = [(t, type_labels.get(t, t), grouped[t]) for t in type_order if t in grouped]

    return templates.TemplateResponse("kb/_references_panel.html", {
        "request": request,
        "grouped_references": ordered_groups,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "total_count": len(references),
    })
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/kb.py
git commit -m "feat: kb_links API — create, delete, references panel endpoint"
```

---

### Task 6: References Panel Partial Template

**Files:**
- Create: `src/policydb/web/templates/kb/_references_panel.html`

- [ ] **Step 1: Create the shared references panel partial**

Create `src/policydb/web/templates/kb/_references_panel.html`:

```html
{# Shared references panel — shows all entities linked to the current entity.
   Expects: grouped_references (list of (type, label, refs)), entity_type, entity_id, total_count #}

{% if grouped_references %}
<div class="space-y-3">
  {% for ref_type, ref_label, refs in grouped_references %}
  <div>
    <div class="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">{{ ref_label }}</div>
    <div class="space-y-1">
      {% for ref in refs %}
      <div class="flex items-center gap-2 group">
        <a href="{{ ref.url }}" class="flex items-center gap-2 text-xs text-gray-700 hover:text-marsh flex-1 min-w-0">
          {# Type-specific icon #}
          {% if ref.type == 'kb_article' %}
          <svg class="w-3.5 h-3.5 text-[#0B4BFF] flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
          {% elif ref.type == 'attachment' and ref.get('source') == 'devonthink' %}
          <svg class="w-3.5 h-3.5 text-[#8b5cf6] flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/>
          </svg>
          {% elif ref.type == 'attachment' %}
          <svg class="w-3.5 h-3.5 text-red-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/>
          </svg>
          {% elif ref.type == 'issue' %}
          <svg class="w-3.5 h-3.5 text-amber-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/>
          </svg>
          {% elif ref.type == 'policy' %}
          <svg class="w-3.5 h-3.5 text-violet-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
          </svg>
          {% elif ref.type == 'client' %}
          <svg class="w-3.5 h-3.5 text-green-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"/>
          </svg>
          {% elif ref.type == 'project' %}
          <svg class="w-3.5 h-3.5 text-orange-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
          </svg>
          {% elif ref.type == 'activity' %}
          <svg class="w-3.5 h-3.5 text-teal-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          {% endif %}
          <span class="text-[9px] font-mono bg-gray-100 text-gray-500 rounded px-1 py-0.5 flex-shrink-0">{{ ref.uid }}</span>
          <span class="flex-1 min-w-0 truncate">{{ ref.title }}</span>
          {% if ref.get('category') %}
          <span class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-medium {{ ref.colors.bg }} {{ ref.colors.text }} flex-shrink-0">
            {{ ref.category }}
          </span>
          {% endif %}
        </a>
        <button type="button"
          hx-delete="/kb/links/{{ ref.link_id }}?return_panel_for_type={{ entity_type }}&return_panel_for_id={{ entity_id }}"
          hx-target="#references-panel-{{ entity_type }}-{{ entity_id }}"
          hx-swap="innerHTML"
          hx-confirm="Remove this reference?"
          class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity no-print flex-shrink-0"
          title="Remove reference">&times;</button>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<p class="text-[10px] text-gray-400 italic">No references linked</p>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/kb/_references_panel.html
git commit -m "feat: shared references panel partial for backlinks display"
```

---

### Task 7: Wire References Panel into KB Detail Pages

**Files:**
- Modify: `src/policydb/web/templates/kb/article.html`
- Modify: `src/policydb/web/templates/kb/document.html`

- [ ] **Step 1: Read current article.html and document.html sidebar sections**

Read the article.html sidebar area (around line 90-110 where linked records appear) and the document.html equivalent to understand current layout.

- [ ] **Step 2: Replace linked records section in article.html**

Find the existing linked records `<section>` (which uses `_record_links_list.html`) and replace it with the new references panel:

```html
  {# ── References ── #}
  <div class="py-3 border-b border-gray-100">
    <div class="flex items-center justify-between mb-2">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">References</p>
      <button type="button" onclick="var el=document.getElementById('ref-picker-{{ article.uid }}');el.classList.toggle('hidden');if(!el.classList.contains('hidden'))el.querySelector('input').focus();"
        class="text-[10px] text-marsh hover:underline no-print">+ Link</button>
    </div>
    <div id="references-panel-kb_article-{{ article.id }}"
      hx-get="/kb/references/kb_article/{{ article.id }}"
      hx-trigger="load"
      hx-swap="innerHTML">
      <span class="text-xs text-gray-300">Loading...</span>
    </div>
    <div id="ref-picker-{{ article.uid }}" class="hidden mt-2 pt-2 border-t border-gray-100 relative">
      <input type="text" placeholder="Search to link..."
        hx-get="/kb/search-linkable"
        hx-trigger="keyup changed delay:300ms"
        hx-target="#ref-picker-results-{{ article.uid }}"
        name="q"
        class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh placeholder-gray-300"
        autocomplete="off">
      <div id="ref-picker-results-{{ article.uid }}" class="absolute top-full left-0 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto hidden"></div>
    </div>
  </div>
```

The `selectLinkedEntity` function from the search-linkable results needs to be adapted for the detail page context. Add a script block or modify the search-linkable endpoint to accept a `return_context` that triggers the right link creation.

Actually, for simplicity, the search-linkable results on detail pages should call a different JS function. Add this script to article.html:

```html
<script>
function linkRefFromKB(targetType, targetId) {
  fetch('/kb/links', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({
      source_type: 'kb_article',
      source_id: '{{ article.id }}',
      target_type: targetType,
      target_id: targetId,
      return_panel_for_type: 'kb_article',
      return_panel_for_id: '{{ article.id }}'
    })
  }).then(r => r.text()).then(html => {
    document.getElementById('references-panel-kb_article-{{ article.id }}').innerHTML = html;
    var picker = document.getElementById('ref-picker-{{ article.uid }}');
    picker.classList.add('hidden');
    picker.querySelector('input').value = '';
  });
}
</script>
```

Update the `search-linkable` endpoint to accept an optional `link_fn` param, or better: make the search-linkable return buttons that call `linkRefFromKB` when a global function is defined. The simplest approach: override `selectLinkedEntity` on the detail page to call `linkRefFromKB` instead.

Add to the detail page script:

```javascript
function selectLinkedEntity(type, id, label) {
  linkRefFromKB(type, id);
}
```

- [ ] **Step 3: Do the same for document.html**

Same pattern but with `source_type: 'attachment'` and `source_id: '{{ document.id }}'`:

```html
  {# ── References ── #}
  <div class="py-3 border-b border-gray-100">
    <div class="flex items-center justify-between mb-2">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">References</p>
      <button type="button" onclick="var el=document.getElementById('ref-picker-{{ document.uid }}');el.classList.toggle('hidden');if(!el.classList.contains('hidden'))el.querySelector('input').focus();"
        class="text-[10px] text-marsh hover:underline no-print">+ Link</button>
    </div>
    <div id="references-panel-attachment-{{ document.id }}"
      hx-get="/kb/references/attachment/{{ document.id }}"
      hx-trigger="load"
      hx-swap="innerHTML">
      <span class="text-xs text-gray-300">Loading...</span>
    </div>
    <div id="ref-picker-{{ document.uid }}" class="hidden mt-2 pt-2 border-t border-gray-100 relative">
      <input type="text" placeholder="Search to link..."
        hx-get="/kb/search-linkable"
        hx-trigger="keyup changed delay:300ms"
        hx-target="#ref-picker-results-{{ document.uid }}"
        name="q"
        class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh placeholder-gray-300"
        autocomplete="off">
      <div id="ref-picker-results-{{ document.uid }}" class="absolute top-full left-0 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto hidden"></div>
    </div>
  </div>

<script>
function linkRefFromKB(targetType, targetId) {
  fetch('/kb/links', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({
      source_type: 'attachment',
      source_id: '{{ document.id }}',
      target_type: targetType,
      target_id: targetId,
      return_panel_for_type: 'attachment',
      return_panel_for_id: '{{ document.id }}'
    })
  }).then(r => r.text()).then(html => {
    document.getElementById('references-panel-attachment-{{ document.id }}').innerHTML = html;
    var picker = document.getElementById('ref-picker-{{ document.uid }}');
    picker.classList.add('hidden');
    picker.querySelector('input').value = '';
  });
}
function selectLinkedEntity(type, id, label) {
  linkRefFromKB(type, id);
}
</script>
```

- [ ] **Step 4: QA — verify on KB article and document detail pages**

Navigate to `/kb/articles/KB-001`, verify:
- References panel loads (may show "No references linked" if none exist)
- "+ Link" button opens picker
- Searching finds entities
- Selecting an entity creates the link and updates the panel
- "x" button removes a link

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/kb/article.html src/policydb/web/templates/kb/document.html
git commit -m "feat: references panel on KB article and document detail pages"
```

---

### Task 8: Wire References into Entity Detail Pages

**Files:**
- Modify: `src/policydb/web/templates/issues/detail.html`
- Modify: `src/policydb/web/templates/clients/_sticky_sidebar.html`
- Modify: `src/policydb/web/templates/clients/project.html`
- Modify: `src/policydb/web/templates/policies/_tab_details.html`

- [ ] **Step 1: Add References section to issue detail page**

Read `src/policydb/web/templates/issues/detail.html` in full, find the right place in the sidebar or metadata area. Add a References section using the same pattern:

```html
  {# ── Knowledge Base References ── #}
  <div class="bg-white border border-[#e5e2dc] rounded-lg p-4 mb-4">
    <div class="flex items-center justify-between mb-2">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">References</p>
      <button type="button" onclick="var el=document.getElementById('ref-picker-issue-{{ issue.id }}');el.classList.toggle('hidden');if(!el.classList.contains('hidden'))el.querySelector('input').focus();"
        class="text-[10px] text-marsh hover:underline no-print">+ Link</button>
    </div>
    <div id="references-panel-issue-{{ issue.id }}"
      hx-get="/kb/references/issue/{{ issue.id }}"
      hx-trigger="load"
      hx-swap="innerHTML">
      <span class="text-xs text-gray-300">Loading...</span>
    </div>
    <div id="ref-picker-issue-{{ issue.id }}" class="hidden mt-2 pt-2 border-t border-gray-100 relative">
      <input type="text" placeholder="Search to link..."
        hx-get="/kb/search-linkable"
        hx-trigger="keyup changed delay:300ms"
        hx-target="#ref-picker-results-issue-{{ issue.id }}"
        name="q"
        class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh placeholder-gray-300"
        autocomplete="off">
      <div id="ref-picker-results-issue-{{ issue.id }}" class="absolute top-full left-0 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto hidden"></div>
    </div>
  </div>

<script>
function linkRefFromKB(targetType, targetId) {
  fetch('/kb/links', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({
      source_type: 'issue',
      source_id: '{{ issue.id }}',
      target_type: targetType,
      target_id: targetId,
      return_panel_for_type: 'issue',
      return_panel_for_id: '{{ issue.id }}'
    })
  }).then(r => r.text()).then(html => {
    document.getElementById('references-panel-issue-{{ issue.id }}').innerHTML = html;
    var picker = document.getElementById('ref-picker-issue-{{ issue.id }}');
    picker.classList.add('hidden');
    picker.querySelector('input').value = '';
  });
}
function selectLinkedEntity(type, id, label) {
  linkRefFromKB(type, id);
}
</script>
```

- [ ] **Step 2: Update client sidebar KB section to use kb_links**

In `src/policydb/web/templates/clients/_sticky_sidebar.html` (lines 133-157), replace the Knowledge Base section. Update the `hx-get` to use the new references endpoint:

```html
  {# ── Knowledge Base ── #}
  <div class="py-3 border-b border-gray-100">
    <div class="flex items-center justify-between mb-2">
      <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">References</p>
      <button type="button" onclick="var el=document.getElementById('ref-picker-client-{{ client.id }}');el.classList.toggle('hidden');if(!el.classList.contains('hidden'))el.querySelector('input').focus();"
        class="text-[10px] text-marsh hover:underline no-print">+ Link</button>
    </div>
    <div id="references-panel-client-{{ client.id }}"
      hx-get="/kb/references/client/{{ client.id }}"
      hx-trigger="load"
      hx-swap="innerHTML">
      <span class="text-xs text-gray-300">Loading...</span>
    </div>
    <div id="ref-picker-client-{{ client.id }}" class="hidden mt-2 pt-2 border-t border-gray-100 relative">
      <input type="text" placeholder="Search to link..."
        hx-get="/kb/search-linkable"
        hx-trigger="keyup changed delay:300ms"
        hx-target="#ref-picker-results-client-{{ client.id }}"
        name="q"
        class="w-full text-xs border border-gray-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh placeholder-gray-300"
        autocomplete="off">
      <div id="ref-picker-results-client-{{ client.id }}" class="absolute top-full left-0 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg z-50 max-h-48 overflow-y-auto hidden"></div>
    </div>
  </div>
```

Note: The client page script needs the `selectLinkedEntity` override. Since the sidebar is an include, add a `<script>` block at the bottom of the sidebar partial or in the client detail page itself:

```html
<script>
(function() {
  var clientId = {{ client.id }};
  window._clientLinkRef = function(targetType, targetId) {
    fetch('/kb/links', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({
        source_type: 'client',
        source_id: clientId,
        target_type: targetType,
        target_id: targetId,
        return_panel_for_type: 'client',
        return_panel_for_id: clientId
      })
    }).then(function(r) { return r.text(); }).then(function(html) {
      document.getElementById('references-panel-client-' + clientId).innerHTML = html;
      var picker = document.getElementById('ref-picker-client-' + clientId);
      picker.classList.add('hidden');
      picker.querySelector('input').value = '';
    });
  };
  // Override for search-linkable results
  if (!window._origSelectLinkedEntity) window._origSelectLinkedEntity = window.selectLinkedEntity;
  window.selectLinkedEntity = function(type, id, label) {
    if (window._clientLinkRef) window._clientLinkRef(type, id);
  };
})();
</script>
```

- [ ] **Step 3: Add References section to policy detail tab**

Read `src/policydb/web/templates/policies/_tab_details.html` to find the right insertion point. Add a References card section at the bottom of the details tab, using the same HTMX pattern with `entity_type='policy'` and `entity_id=policy.id`.

- [ ] **Step 4: Add References section to project detail page**

Read `src/policydb/web/templates/clients/project.html` and add the References section in the appropriate location, using `entity_type='project'` and `entity_id=project.id`.

- [ ] **Step 5: QA — verify all entity pages**

Test on each page type:
- Client detail sidebar — References section loads and linking works
- Issue detail — References section shows, can link/unlink
- Policy detail tab — References section visible
- Project detail — References section visible
- Verify bi-directionality: link from Issue -> Article, then check Article detail shows Issue in references

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/issues/detail.html \
  src/policydb/web/templates/clients/_sticky_sidebar.html \
  src/policydb/web/templates/clients/project.html \
  src/policydb/web/templates/policies/_tab_details.html
git commit -m "feat: References panel on issue, client, policy, and project detail pages"
```

---

### Task 9: Update Entity-Side Endpoints to Use kb_links

**Files:**
- Modify: `src/policydb/web/routes/kb.py` (update existing entity-side endpoints)

- [ ] **Step 1: Update `kb_links_for_entity` to query kb_links**

Replace the `kb_links_for_entity` route (line 780-811) to query `kb_links` instead of `kb_record_links`:

```python
@router.get("/for-entity/{entity_type}/{entity_id}", response_class=HTMLResponse)
async def kb_links_for_entity(
    request: Request,
    entity_type: str,
    entity_id: int,
    conn=Depends(get_db),
):
    """Legacy endpoint — redirects to references panel."""
    return await references_panel(request, entity_type, entity_id, conn)
```

- [ ] **Step 2: Update `link_from_entity` to use kb_links**

Replace the `link_from_entity` route (line 852-872):

```python
@router.post("/link-from-entity", response_class=HTMLResponse)
async def link_from_entity(
    request: Request,
    entry_type: str = Form(...),
    entry_id: int = Form(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    """Create a KB link from a policy/client page — uses kb_links."""
    source_type = "kb_article" if entry_type == "article" else "attachment"
    try:
        conn.execute(
            "INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id) VALUES (?, ?, ?, ?)",
            (entity_type, entity_id, source_type, entry_id),
        )
        conn.commit()
    except Exception:
        pass
    return await references_panel(request, entity_type, entity_id, conn)
```

- [ ] **Step 3: Update `unlink_from_entity` to use kb_links**

Replace the `unlink_from_entity` route (line 875-886):

```python
@router.post("/unlink-from-entity", response_class=HTMLResponse)
async def unlink_from_entity(
    request: Request,
    link_id: int = Form(...),
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    """Remove a KB link from a policy/client page — uses kb_links."""
    conn.execute("DELETE FROM kb_links WHERE id = ?", (link_id,))
    conn.commit()
    return await references_panel(request, entity_type, entity_id, conn)
```

- [ ] **Step 4: Also update `_get_record_links` and the KB-side link/unlink routes**

Update `_get_record_links` (line 621-675) to query `kb_links`:

```python
def _get_record_links(conn, entry_type: str, entry_id: int) -> list[dict]:
    """Get entities linked to a KB entry via kb_links."""
    source_type = "kb_article" if entry_type == "article" else "attachment"
    rows = conn.execute(
        "SELECT id, source_type, source_id, target_type, target_id FROM kb_links "
        "WHERE (source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?)",
        (source_type, entry_id, source_type, entry_id),
    ).fetchall()
    links = []
    for r in rows:
        r = dict(r)
        if r["source_type"] == source_type and r["source_id"] == entry_id:
            ref_type, ref_id = r["target_type"], r["target_id"]
        else:
            ref_type, ref_id = r["source_type"], r["source_id"]

        d = {"id": r["id"], "entity_type": ref_type, "entity_id": ref_id}

        if ref_type == "client":
            entity = conn.execute("SELECT id, name FROM clients WHERE id = ?", (ref_id,)).fetchone()
            if entity:
                d["entity_name"] = entity["name"]
                d["entity_url"] = f"/clients/{entity['id']}"
        elif ref_type == "policy":
            entity = conn.execute(
                "SELECT p.id, p.policy_uid, p.policy_type, p.carrier "
                "FROM policies p WHERE p.id = ?", (ref_id,)
            ).fetchone()
            if entity:
                d["entity_name"] = f"{entity['policy_uid']} — {entity['carrier'] or ''} {entity['policy_type'] or ''}"
                d["entity_url"] = f"/policies/{entity['policy_uid']}"
        elif ref_type == "issue":
            entity = conn.execute("SELECT id, issue_uid, subject FROM issues WHERE id = ?", (ref_id,)).fetchone()
            if entity:
                d["entity_name"] = f"{entity['issue_uid']} — {entity['subject']}"
                d["entity_url"] = f"/issues/{entity['issue_uid']}"
        # Add other types as needed

        if "entity_name" in d:
            links.append(d)
    return links
```

Update the KB-side `link_record` route (line 678-718) to use `kb_links`:

```python
@router.post("/{entry_type}/{uid}/link")
async def link_record(
    request: Request,
    entry_type: str,
    uid: str,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    conn=Depends(get_db),
):
    if entry_type == "article":
        row = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
        source_type = "kb_article"
    else:
        row = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()
        source_type = "attachment"

    if not row:
        return JSONResponse({"ok": False})

    try:
        conn.execute(
            "INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id) VALUES (?, ?, ?, ?)",
            (source_type, row["id"], entity_type, entity_id),
        )
        conn.commit()
    except Exception:
        pass

    record_links = _get_record_links(conn, entry_type, row["id"])
    return templates.TemplateResponse("kb/_record_links_list.html", {
        "request": request,
        "record_links": record_links,
        "entry_type": entry_type,
        "uid": uid,
    })
```

Update `unlink_record` (line 721-749) to delete from `kb_links`:

```python
@router.post("/{entry_type}/{uid}/unlink")
async def unlink_record(
    request: Request,
    entry_type: str,
    uid: str,
    link_id: int = Form(...),
    conn=Depends(get_db),
):
    conn.execute("DELETE FROM kb_links WHERE id = ?", (link_id,))
    conn.commit()

    if entry_type == "article":
        row = conn.execute("SELECT id FROM kb_articles WHERE uid = ?", (uid,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM attachments WHERE uid = ?", (uid,)).fetchone()

    if not row:
        return JSONResponse({"ok": False})

    record_links = _get_record_links(conn, entry_type, row["id"])
    return templates.TemplateResponse("kb/_record_links_list.html", {
        "request": request,
        "record_links": record_links,
        "entry_type": entry_type,
        "uid": uid,
    })
```

- [ ] **Step 5: Verify all linking flows work end-to-end**

Test:
1. Link article -> client from KB article page
2. Verify client sidebar shows the article
3. Link issue -> article from issue page
4. Verify article detail shows the issue
5. Unlink from both directions

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/kb.py
git commit -m "feat: migrate all KB link operations to unified kb_links table"
```

---

### Task 10: Final QA + Cleanup

- [ ] **Step 1: Full QA pass**

Navigate through all affected pages and verify:
1. `/kb` — cards render with Style B, filters all work (source, category, sort, linked-to)
2. `/kb/articles/KB-001` — References panel, link picker, link/unlink
3. Client detail sidebar — References section loads, link/unlink works
4. Issue detail — References section
5. Policy detail — References section
6. Project detail — References section
7. Bi-directionality: create link from one page, verify it appears on the other

- [ ] **Step 2: Check for any broken references to old filter params**

Search for `entry_type` param references in templates — the old `entry_type` filter is now `source_filter`. Make sure no template is still passing `entry_type`.

```bash
grep -r "entry_type" src/policydb/web/templates/kb/index.html
```

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: QA cleanup for KB reference web feature"
```
