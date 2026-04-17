# PolicyDB ↔ Outlook Bridge

**Date:** 2026-04-17
**Status:** Design — pending implementation plan

## Problem

Replying to email in Outlook triggers two recurring workflow taxes:

1. **Forward flow (Outlook → PolicyDB):** Finding the right ref tag to attach is a window-switch, a search, a click, a copy, a switch back. It happens many times a day and the switching cost is friction that adds up.
2. **Reverse flow (PolicyDB → Outlook):** Searching Outlook for correspondence about a record requires remembering which UID was actually used on prior emails. Using an issue UID when the prior threads were tagged with the policy UID (or vice versa) produces empty result sets and forces retries.

Both pains are caused by the same gap: there is no direct bridge between a live Outlook message and the PolicyDB record it belongs to. We already have the pieces (ref tags, FTS5 search, `outlook.py` AppleScript bridge, `/ref-lookup`). The design below wires them together into two focused features.

## Goals

- **Forward flow:** Cut the "paste the right ref tag into my reply" round-trip to a single keystroke + Enter, with no window switch required beyond looking at the pinned dock.
- **Reverse flow:** A single click on any record page produces an Outlook search that covers the record *and* its relatives, so "wrong UID type" stops producing empty result sets.
- **IT-friendly:** No new binaries, no hotkey helpers, no menu bar apps. Everything lives in the existing PolicyDB web app plus the existing `osascript` subprocess pattern.

## Non-goals

- Automatic "read the open Outlook message, propose a ref tag" matching. (Deferred — forward flow is scoped-by-client per user; fast fuzzy search on a pinned view is enough.)
- Global macOS hotkey or Outlook script menu entries.
- A two-way sync or reply-from-PolicyDB flow. Compose already exists; this design is only about lookup and search.
- Keyword AND filters on top of the OR-joined tag search. (Users can append keywords manually in Outlook's search bar after the auto-fill.)

## User-visible features

### Feature 1 — The Dock (forward flow)

A narrow PolicyDB view at `/dock` (aliased `/d`) designed for a 320-400px always-visible browser window. Single column. Autofocus search, fuzzy match across clients/policies/issues/projects/programs. Arrow keys + Enter copy `[PDB:...]` to clipboard, toast confirms, search clears, focus returns. No window switching required beyond glancing at the dock.

- Reuses existing `full_text_search()` (FTS5 + RapidFuzz fallback).
- Recents list (last 10 copied, from `localStorage`) shown when search box is empty.
- `↗` icon on each row opens the record in a new tab for the rare deep-dive case; click/Enter on the row itself only copies.
- `🔍` icon on each row fires Feature 2 (wide Outlook search) for that record — the dock is a full bridge in both directions from one window.

### Feature 2 — Search Outlook (reverse flow)

A button (`🔍 Search Outlook`) on every record detail page (issue, policy, project, program, client). Click generates a wide OR-joined search string covering that record plus all relatives, then attempts to run the search in Outlook via AppleScript + System Events, gracefully degrading to clipboard-only if UI scripting is unavailable.

- Default mode: **wide** (record + relatives). Solves the "wrong UID type" problem structurally.
- If the relatives graph exceeds the cap (default 60 tokens), the toast includes a "narrow" link that re-fires with `mode=narrow` (UID only).
- Same button available inline on each dock result row.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                    │
│  ┌────────────────┐     ┌───────────────────────────────┐   │
│  │ /dock (narrow) │     │ /issues/ISS-7 (record page)   │   │
│  │  ┌──────────┐  │     │  [🔍 Search Outlook] button   │   │
│  │  │  Search  │  │     └───────────────┬───────────────┘   │
│  │  └────┬─────┘  │                     │                   │
│  │       │        │                     │ POST              │
│  │       │ /search/live                 │ /outlook/search   │
│  └───────┼────────┘                     │                   │
└──────────┼──────────────────────────────┼───────────────────┘
           │                              │
┌──────────▼──────────────────────────────▼───────────────────┐
│  FastAPI                                                    │
│  ┌──────────────────┐     ┌───────────────────────────┐     │
│  │ queries.py       │     │ web/routes/outlook_routes │     │
│  │ full_text_search │     │ POST /outlook/search      │     │
│  └──────────────────┘     └─────┬─────────────────────┘     │
│                                 │                           │
│  ┌──────────────────────────────▼──────────────┐            │
│  │ ref_tags.py (NEW)                           │            │
│  │ build_wide_search(conn, type, id) -> dict   │            │
│  └──────────────────┬──────────────────────────┘            │
│                     │                                       │
│  ┌──────────────────▼──────────────────────────┐            │
│  │ outlook.py                                  │            │
│  │ trigger_search(query) -> dict  (NEW)        │            │
│  └──────────────────┬──────────────────────────┘            │
└─────────────────────┼───────────────────────────────────────┘
                      │ osascript subprocess
                      ▼
                ┌─────────────┐
                │ Outlook.app │
                │  (legacy)   │
                └─────────────┘
```

## Component 1 — `/dock` view

### Route

New router `src/policydb/web/routes/dock.py`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/dock` | Render dock template |
| GET | `/d` | Alias of `/dock` (muscle memory) |

Search reuses existing `/search/live` endpoint (already HTMX-enabled, 300ms debounce, returns a partial).

### Template

`src/policydb/web/templates/dock.html` — a standalone layout that does **not** extend the regular `base.html` chrome (no top nav, no sidebar). Loads only the bits it needs (Tailwind CDN, `copyRefTag()` helper from base).

Structure:

```html
<div class="dock">
  <header>🔒 PolicyDB Dock</header>
  <input id="q" autofocus placeholder="Client, policy, issue…" />
  <div id="results">
    <!-- HTMX-loaded partial from /search/live?mode=dock -->
  </div>
  <footer>
    <div id="recents"><!-- localStorage-driven --></div>
  </footer>
</div>
```

### Partial: dock-mode search results

Extend `/search/live` to accept `mode=dock`, returning a narrower partial (`_dock_results.html`) that renders each hit as:

```html
<div class="result" data-ref="CN123-POL042" data-url="/policies/POL-042/edit">
  <span class="type-badge">📄</span>
  <span class="name">Acme GL 2026</span>
  <button class="tag-pill" onclick="dockCopy(this)">[PDB:CN123-POL042]</button>
  <button class="outlook-search" hx-post="/outlook/search"
          hx-vals='{"entity_type":"policy","entity_id":"POL-042"}'>🔍</button>
  <a class="open" href="/policies/POL-042/edit" target="_blank">↗</a>
</div>
```

### JavaScript (`src/policydb/web/static/dock.js`)

Small vanilla module:

- **Focus management:** Autofocus `#q` on load and on `window.focus` event (returning from Outlook refocuses the search box).
- **Keyboard:**
  - Arrow Up/Down moves a `.selected` class across result rows.
  - Enter calls `dockCopy()` on the selected row.
  - Esc clears `#q` and refocuses.
- **`dockCopy(el)`:** Reads `data-ref` from the result row, wraps in `[PDB:…]`, writes to clipboard, flashes the row green, clears `#q`, prepends to recents list in `localStorage` (capped 10 entries, dedup), re-renders the recents footer.
- **Recents:** Render on load from `localStorage['dock:recents']`. Clicking a recent re-copies.
- **No navigation on row click** — only the `↗` icon opens records. Primary action is copy.

### Styling

Reuses existing Tailwind utilities and brand palette. The `dock` class sets:
- Background: `bg-neutral-250` (page bg)
- Width: full viewport (the user sizes the browser window)
- Max body width: 400px centered (so a too-wide window still looks right)
- Text: DM Sans at compact spacing; tag pills stay at the existing `text-[10px] font-mono`

### Recents data model

Stored entirely client-side in `localStorage`. No server round-trip:

```json
[
  {"ref": "CN123-POL042", "label": "Acme GL 2026", "type": "policy", "url": "/policies/POL-042/edit"},
  ...
]
```

Capped at 10 entries, newest first, deduped by `ref`.

## Component 2 — `ref_tags.build_wide_search()`

### Module

New file `src/policydb/ref_tags.py`. Single responsibility: given an entity, return the search query that covers it and its relatives.

### Public API

```python
from dataclasses import dataclass

@dataclass
class WideSearchResult:
    query: str          # OR-joined quoted token string for Outlook search bar
    tokens: list[str]   # Raw tokens before quoting/joining, ordered by specificity
    total_available: int
    truncated: bool

def build_wide_search(
    conn: sqlite3.Connection,
    entity_type: str,     # "client" | "policy" | "issue" | "project" | "program"
    entity_id: int | str,
    mode: str = "wide",   # "wide" | "narrow" | "client"
    cap: int = 60,
) -> WideSearchResult:
    ...
```

### Token format (informed by existing `build_ref_tag()` behavior)

`src/policydb/utils.py:build_ref_tag()` strips dashes from some UID types but not others when building compound `[PDB:...]` tags. The search tokens must match what actually appears in historic emails. Per-type rules:

| Record type | Stored form | In compound `[PDB:...]` tag | Tokens to search for |
|---|---|---|---|
| Policy | `POL-042` | `POL042` (dash stripped) | **both** `POL-042` and `POL042` |
| Program | `PRG-3` | `PRG3` (dash stripped) | **both** `PRG-3` and `PRG3` |
| Issue | `ISS-2026-001` | `ISS-2026-001` (verbatim) | `ISS-2026-001` |
| Client CN | `122333627` | `CN122333627` | `CN122333627` |
| Project | (internal ID, e.g. `5`) | `L5` | **skipped** — `L5` alone is too ambiguous to search |

Emitting both dashed and undashed forms for policies/programs catches two cases: the compound-tag form (what PolicyDB generates when copying a ref pill) and the natural-text form users sometimes type in emails manually. This approximately doubles the token count for those types, but the cap still keeps it manageable.

The literal strings are wrapped in Outlook's quoted-literal search — `"POL042"` matches the substring inside `[PDB:CN123-POL042]` because Outlook's indexer tokenizes on `-`, `:`, `[`, `]`.

### Relatives graph

Ordered by specificity (most specific first, so truncation drops the broadest tokens last):

| Entity | Relatives walked (in order) |
|---|---|
| **Client** | all ISS for client → all POL → all PRG → self (CN) |
| **Policy** | self (POL) → all ISS on this policy → client (CN) — parent PRJ not emitted (no searchable token) |
| **Issue** | self (ISS) → linked POL → client (CN) — linked PRJ not emitted |
| **Project** | all child ISS → all child POL → client (CN) — project itself not emitted |
| **Program** | self (PRG) → all ISS on member policies → all member POL → client (CN) |

Each "relative walked" contributes its per-type tokens from the table above (so a policy walked contributes *two* tokens, `POL-042` and `POL042`).

### Query construction

- Quote each token: `"POL-042"` → `"POL-042"`.
- Join with space-delimited `OR`: `"ISS-2026-007" OR "POL-042" OR "POL042" OR "CN122333627"`.
- No leading/trailing parentheses (Outlook for Mac doesn't need them for flat OR lists).

### Cap & truncation

Default `cap=60`. If `len(tokens) > cap`, truncate to the first `cap` (most-specific) tokens, set `truncated=True`, expose `total_available` so the UI can show "Showing 60 of 84".

Modes:
- `wide` (default) — full relatives graph per the table above.
- `narrow` — the entity's own tokens only, no relatives walked. One token for issues/CN/programs-that-stay-dashed, two tokens for policies and programs (dashed + undashed). For a project, `narrow` degrades to the client CN because projects have no own searchable token.
- `client` — collapse to just the client CN. Useful as a fallback: "show me everything ever about this client."

### Edge cases

- **Entity not found:** raise `KeyError` — the route catches it and returns HTTP 404.
- **Entity has no relatives:** `tokens = [own_uid]`, `truncated=False`. Always one token minimum.
- **Empty client CN:** fall back to `C{client_id}` matching existing `build_ref_tag()` behavior.
- **Deleted/archived records:** include the tag anyway — old emails still reference them, the user presumably cares.

### Testing

Pure function, no side effects. Pytest fixtures set up an in-memory SQLite DB with a known relatives graph (1 client, 3 policies, 2 issues, 1 project), call `build_wide_search()` for each entity type, assert tokens in expected specificity order.

## Component 3 — `outlook.trigger_search()`

### Function signature

```python
def trigger_search(query: str, auto_paste: bool = True) -> dict:
    """
    Args:
        query: The OR-joined search string to run.
        auto_paste: If False, skip UI scripting and just put query on clipboard.

    Returns:
        {
            "status": "searched" | "clipboard_only" | "unavailable",
            "query": str,
            "message": str,  # Human-readable, used for toast copy.
        }
    """
```

### AppleScript plan

Single `osascript` call with three layered attempts:

```applescript
-- 1. Always: put query on clipboard
set the clipboard to "<query>"

-- 2. Attempt to activate Outlook
try
    tell application "Microsoft Outlook" to activate
on error
    return "unavailable"
end try

-- 3. Attempt UI-scripted paste + run
if <auto_paste> then
    try
        tell application "System Events"
            tell process "Microsoft Outlook"
                keystroke "f" using {command down, option down}  -- focus search box
                delay 0.15
                keystroke "v" using {command down}               -- paste
                delay 0.05
                keystroke return                                 -- run search
            end tell
        end tell
        return "searched"
    on error
        return "clipboard_only"
    end try
end if

return "clipboard_only"
```

The return value is parsed by `trigger_search()` and shaped into the dict response.

### Status outcomes

| Condition | status | Toast |
|---|---|---|
| `osascript` fails to find Outlook.app | `unavailable` | "Outlook isn't running. Query copied — paste into search." |
| Outlook activated but System Events errors (Accessibility denied, UI changed) | `clipboard_only` | "Copied — ⌘V into Outlook search, then Return." |
| All three steps succeeded | `searched` | "Searched Outlook for {N} related tags." |

### Accessibility permission

The `keystroke` calls inside `tell process "Microsoft Outlook"` require the parent process of `osascript` (typically Terminal, iTerm, or VS Code) to have Accessibility permission. Users who have not granted this permission will fall into `clipboard_only` mode automatically — no error visible to them, just the clipboard fallback toast.

A one-time setup note goes into `docs/outlook-setup.md` (or is appended to the existing doc if one exists).

### Config key

New key in `_DEFAULTS`:

```python
"outlook_search_auto_paste": True,
```

Editable in Settings → Email & Contacts. If flipped to `False`, `trigger_search()` skips the UI scripting attempt entirely and always returns `clipboard_only`. Provides a belt-and-suspenders escape hatch if UI scripting gets flaky on a specific machine after an Outlook update.

## Component 4 — `POST /outlook/search` route

### Location

Add to existing `src/policydb/web/routes/outlook_routes.py`.

### Request model

```python
class OutlookSearchRequest(BaseModel):
    entity_type: Literal["client", "policy", "issue", "project", "program"]
    entity_id: str  # accepts both numeric ids (clients) and string UIDs (POL-42, ISS-7)
    mode: Literal["wide", "narrow", "client"] = "wide"
```

### Response

```json
{
  "status": "searched" | "clipboard_only" | "unavailable",
  "query": "\"ISS-2026-007\" OR \"POL-042\" OR \"POL042\" OR \"CN122333627\"",
  "tokens": ["ISS-2026-007", "POL-042", "POL042", "CN122333627"],
  "total_available": 3,
  "truncated": false,
  "message": "Searched Outlook for 3 related tags."
}
```

### Flow

```python
def outlook_search(req, conn):
    if not cfg.get("outlook_search_auto_paste", True):
        auto_paste = False
    else:
        auto_paste = True

    result = build_wide_search(conn, req.entity_type, req.entity_id, mode=req.mode)
    trigger_result = trigger_search(result.query, auto_paste=auto_paste)

    return {
        **trigger_result,
        "tokens": result.tokens,
        "total_available": result.total_available,
        "truncated": result.truncated,
    }
```

### Error handling

- Unknown entity type → 400.
- Entity not found → 404.
- `trigger_search` always returns a dict (never raises on Outlook failure — that's the whole point of status-based graceful degradation).

## Component 5 — UI entry points

### Record pages

Add `_search_outlook_btn.html` partial. Drop it into the header actions of:

| Template | Location |
|---|---|
| `issues/detail.html` | Header, next to Compose |
| `policies/edit.html` | Header actions bar |
| `policies/_tab_pulse.html` | Header |
| `projects/detail.html` | Header actions |
| `programs/detail.html` | Header actions |
| `clients/detail.html` | Header actions |

Partial structure:

```html
<button type="button"
        class="btn-secondary text-xs"
        hx-post="/outlook/search"
        hx-vals='{"entity_type":"{{entity_type}}","entity_id":"{{entity_id}}","mode":"wide"}'
        hx-swap="none"
        hx-on::after-request="handleOutlookSearchResponse(event)"
        title="Find all correspondence about this {{entity_type}} and its relatives">
  🔍 Search Outlook
</button>
```

Global JS handler (`handleOutlookSearchResponse`) parses the JSON response, renders the toast, and if `truncated`, includes an inline "narrow search →" link that refires with `mode=narrow`.

### Dock row integration

Each `.result` row in `_dock_results.html` gets the same `🔍` button inline (smaller icon, no text). Same endpoint, same handler.

### Toast behavior

Reuses the existing toast helper (whatever the codebase uses — either the existing `flashCell()` pattern or a dedicated toast). Three variants matching status:
- `searched` → green toast
- `clipboard_only` → amber toast with ⌘V hint
- `unavailable` → red toast

## Data flow

**Forward (dock → copy):**
```
user types "Acme"
  → /search/live?mode=dock&q=Acme
  → full_text_search() returns ranked hits
  → _dock_results.html rendered
  → user presses Enter on highlighted row
  → dockCopy() writes [PDB:CN123-POL042] to clipboard, flashes green
  → user pastes into Outlook reply
```

**Reverse (button → Outlook search):**
```
user clicks 🔍 on issue page
  → POST /outlook/search {entity_type:"issue", entity_id:"ISS-7", mode:"wide"}
  → build_wide_search() returns query = "\"ISS-2026-007\" OR \"POL-042\" OR \"POL042\" OR \"CN122333627\""
  → trigger_search(query) sets clipboard, activates Outlook, UI-scripts paste+return
  → returns {status:"searched", tokens:[...], truncated:false}
  → handleOutlookSearchResponse() shows "Searched Outlook for 3 related tags."
  → Outlook now displays the search results
```

## Files to add / modify

### New files

- `src/policydb/ref_tags.py` — wide search generator
- `src/policydb/web/routes/dock.py` — `/dock`, `/d` routes
- `src/policydb/web/templates/dock.html` — dock view
- `src/policydb/web/templates/_dock_results.html` — dock-mode search results partial
- `src/policydb/web/templates/_search_outlook_btn.html` — reusable button
- `src/policydb/web/static/dock.js` — dock keyboard handling + recents
- `tests/test_ref_tags.py` — `build_wide_search()` fixtures

### Modified files

- `src/policydb/outlook.py` — add `trigger_search()`
- `src/policydb/web/routes/outlook_routes.py` — add `POST /outlook/search`
- `src/policydb/web/routes/dashboard.py` (or wherever `/search/live` lives) — support `mode=dock` partial
- `src/policydb/web/app.py` — register the new dock router
- `src/policydb/config.py` — add `outlook_search_auto_paste` default
- `src/policydb/web/routes/settings.py` — include new config key in Settings UI
- `src/policydb/web/templates/issues/detail.html` — include `_search_outlook_btn.html`
- `src/policydb/web/templates/policies/edit.html` — include partial
- `src/policydb/web/templates/policies/_tab_pulse.html` — include partial
- `src/policydb/web/templates/projects/detail.html` — include partial
- `src/policydb/web/templates/programs/detail.html` — include partial
- `src/policydb/web/templates/clients/detail.html` — include partial

### Documentation

- `docs/outlook-setup.md` — note the Accessibility permission requirement for auto-paste

## Security & privacy

- `trigger_search` writes to clipboard. This is a deliberate user action initiated by a button click; no silent clipboard manipulation.
- The search string contains only UIDs (`POL-042`, `CN-xxxxx`) — no PII or confidential content.
- Accessibility permission is a one-time system-level decision the user makes; PolicyDB cannot grant it to itself.
- No new external network calls — everything runs on localhost.

## Testing plan

### Unit tests

- `tests/test_ref_tags.py` — relatives graph for each entity type, cap behavior, narrow/wide/client mode selection, edge cases (no relatives, missing CN, archived records).

### Integration tests

- Route `POST /outlook/search`:
  - Returns `unavailable` when Outlook can't be activated (mocked).
  - Returns `clipboard_only` when System Events errors (mocked).
  - Returns `searched` on success (mocked osascript).
  - Respects `outlook_search_auto_paste=False` config.
- Route `GET /dock` — renders, search input is present, autofocus attribute present.
- Route `GET /search/live?mode=dock` — returns dock-flavored partial.

### Manual QA

- Browser: open `/dock` in a narrow window, verify autofocus, type a client name, verify ref tag copies, verify recents populate.
- Browser: click 🔍 on an issue page, verify toast shows one of the three statuses.
- Outlook: confirm clipboard contents after a `clipboard_only` click are the correct query string.
- Outlook: with Accessibility granted, confirm the search bar populates and results filter.
- Outlook: with Accessibility NOT granted, confirm graceful fallback to `clipboard_only`.

## Rollout

Two features, one or two PRs — they're independent:

1. **PR 1 — `ref_tags.py` + `POST /outlook/search` + button on record pages.** Ships the reverse flow. This is the one that directly eliminates the "wrong UID type" pain.
2. **PR 2 — `/dock` view + recents + search mode=dock.** Ships the forward flow and the dock-embedded 🔍 affordance.

Either order works. If only one ships, they both still deliver value independently.

## Future enhancements (explicitly out of scope for v1)

- **Auto-match from open Outlook message:** Read current selection via AppleScript, propose ref tags based on sender/subject. Only worth building if the dock's fuzzy search turns out to be insufficient.
- **Split-button modes on every "Search Outlook" button:** Add an explicit dropdown for narrow/wide/client instead of relying on the truncation link.
- **Keyword AND combiners:** UI input next to the Search Outlook button that appends ` AND "keyword"` to the generated query. (Users can type manually into Outlook's search bar for v1.)
- **Correspondence tab per record:** Pull Outlook search results back into PolicyDB. Requires a lot more AppleScript plumbing and isn't needed for the stated problem.
- **Scripts menu entry in Outlook:** A `.scpt` file dropped into `~/Library/Application Scripts/com.microsoft.Outlook/` that reads the current message and opens the dock scoped to that sender's client. Useful but IT-fragile; revisit if the dock alone doesn't close the loop.
