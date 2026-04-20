# Swift / macOS Native Frontend — Design

**Date:** 2026-04-20
**Author:** Grant Greeson (with Claude)
**Status:** Design — awaiting review

---

## 1. Overview

Build a native macOS SwiftUI client for PolicyDB that reads and writes the existing SQLite database directly, co-existing with the Python/FastAPI webapp. Scope is deliberately narrow: a test/proof-of-concept covering three flows — **Clients CRUD**, **Renewal Queue**, and **Data Import + Edit** — built as a foundation to expand on later.

The app leans on a CRM/Salesforce shape: master records, related lists, sortable/filterable queues, polished keyboard-first navigation. Unlike the Python webapp, it does **not** replicate automated intelligence (focus queue scoring, timeline health, anomaly detection, auto-close rules, review gates). Where Python relies on algorithms to surface state, Swift relies on **visual cues** — color, typography, spacing, motion — so the user sees state directly and decides.

### 1.1 Goals

- Ship a polished native macOS app that runs independently of `policydb serve`.
- Cover the three v1 flows end-to-end, with no round-trips to the webapp required to complete a flow.
- Share the existing SQLite database with the Python webapp — changes from either side are visible to the other on next view.
- Preserve PolicyDB's UID / ref-tag copy behavior so the Swift app is a drop-in replacement for Outlook copy workflow.
- Validate the native-app direction cheaply before committing to a larger build-out.

### 1.2 Non-Goals (v1)

Explicitly out of scope for v1:

- Issues / RFIs
- Inbox capture
- Timeline engine / milestone health
- Compliance review / contract workspace
- Charts, reports, exports
- Prompt Builder
- Email compose / template rendering
- Outlook AppleScript bridge
- FTS5 search (v1 uses simple `LIKE` queries; FTS5 stays Python-side)
- Activities feed (log entries, follow-ups) — added in a later phase
- Focus queue, anomaly engine, review queue, supersession, auto-close
- iPad / iPhone versions
- CloudKit sync, multi-user, sharing

---

## 2. Architecture

```
┌───────────────────────────────────────────┐
│  Swift/SwiftUI macOS app (this project)   │
│                                           │
│  UI Layer (SwiftUI, Observation)          │
│    │                                      │
│  Repository Layer (actors, async/await)   │
│    │                                      │
│  GRDB (SQLite driver with WAL support)    │
└─────────────┬─────────────────────────────┘
              │
              ▼
   ~/.policydb/policydb.sqlite (WAL mode)
              ▲
              │
┌─────────────┴─────────────────────────────┐
│  Python FastAPI webapp (unchanged)        │
│  - Schema owner (migrations, views)       │
│  - Intelligence layer (focus queue,       │
│    timeline, anomaly, review)             │
│  - Integrations (Outlook, importer,       │
│    exporter, email, FTS5)                 │
└───────────────────────────────────────────┘
```

### 2.1 Coexistence Rules

- **Python is the schema owner.** It runs migrations on startup and drops/recreates views. Swift never runs migrations, never creates tables, never modifies views.
- **Shared SQLite database, WAL mode.** Both processes can read concurrently. Only one writer at a time (SQLite's lock). WAL minimizes contention.
- **Swift writes only to columns with known rules.** See §5 (Write Safety). Everything else is read-only from Swift's perspective.
- **No cache.** Swift re-queries on view appear. The existing database is small (< 100 MB typical) and SQLite is fast; caching introduces staleness bugs when two processes write.
- **Schema drift tolerance.** If Python adds a column Swift doesn't know, Swift ignores it. If Python removes a column Swift depends on, Swift surfaces a clear error ("Database schema is newer than this app supports — update the Swift app"). Migrations always add, never remove (per `CLAUDE.md`).

### 2.2 Python's Fate

Coexist for now. Later (post-v1 evaluation), we may move Python toward headless-only (importer, Outlook bridge, exporter, email templates) with Swift as the sole daily UI. That decision defers until the Swift test proves out.

---

## 3. Entity Scope (v1)

Six entities, aligned to existing PolicyDB tables:

| Entity | Table | Role in v1 |
|---|---|---|
| Client | `clients` | Full CRUD |
| Policy | `policies` | Read + renewal queue + edit (limited fields) |
| Project/Location | `projects` | Read + edit (limited fields) |
| Program | `programs` (or equivalent — see §3.1) | Read + edit (limited fields) |
| Contact | `contacts` / `client_contacts` | Read + edit inline on Client detail |
| Carrier | `carriers` | Read-only (used as lookup in edit forms) |

### 3.1 "Program" Clarification

"Program" in user vocabulary covers **both**:

- **Master renewal programs** — a grouping of policies for a client-year (e.g., a casualty renewal bundling GL + Auto + WC + Umbrella, or a property renewal bundling Property + BI + EQ).
- **Location data** — project/location records for clients with multi-location exposures.

In the schema, these are distinct concepts:

- `projects` table holds locations/projects (L-prefixed in ref tags).
- Insurance programs live as PGM-prefixed tagged bind orders (see Python's `bind_order.py`) — a lighter-weight concept than a full table row.

Swift v1 treats both as first-class: a Client's detail view shows a **Locations** related list (from `projects`) and a **Programs** related list (from the bind-order grouping). Edits are limited to simple inert fields (see §5).

---

## 4. UI Shape

Mail.app-style three-pane layout:

```
┌────────────┬───────────────────────┬──────────────────────────┐
│ Sidebar    │ List Pane             │ Detail Inspector         │
│            │                       │                          │
│ Clients    │ ▸ Acme Corp           │ Acme Corp                │
│ Renewals   │ ▸ Beta LLC            │ CN1234567 · Construction │
│ Import     │ ▸ Gamma Industries    │ ───────────────────────  │
│            │ ▸ Delta Partners      │ Policies (4)             │
│            │   …                   │ Contacts (6)             │
│            │                       │ Locations (2)            │
│            │                       │ Programs (1)             │
│            │                       │ Scratchpad               │
└────────────┴───────────────────────┴──────────────────────────┘
```

### 4.1 Sidebar (fixed)

- **Clients** — master list of all clients.
- **Renewals** — renewal queue (policies nearing expiration, inherits `v_renewal_pipeline` window).
- **Import** — drag-drop CSV/XLSX import workspace.

Future items (grayed out placeholders or hidden in v1): Activities, Issues, Reports.

### 4.2 List Pane

- Sortable, filterable list.
- Keyboard navigable: ↑↓ to move, Return to focus detail, ⌘F to search, Tab to filter bar.
- Inline search bar at top (simple `LIKE` across name + alias columns; FTS5 stays Python).
- Right-click context menu: New, Duplicate, Delete, Copy Ref Tag.
- Saved views (Renewals only in v1) — pill buttons above the list for quick filter application.

### 4.3 Detail Inspector

- Header: record title, UID, ref-tag pill (click to copy `[PDB:CN…-POL…]` to clipboard).
- Scrollable body with sections (always-open, no collapsible `<details>` — consistent with PolicyDB convention).
- Each field is edit-in-place: tap to edit, blur to save (per-field PATCH semantics, matching the webapp's auto-save pattern).
- Related lists rendered as sub-tables (Policies / Contacts / Locations / Programs on Client; minimal sub-tables on Policy).
- No Save button. Field-level changes persist on blur; validation errors appear inline (red border + message).

### 4.4 Design Language

- Target **macOS 26 Tahoe** — Liquid Glass sidebar materials, latest SwiftUI primitives (`NavigationSplitView`, `Table`, `Inspector`).
- System fonts (SF Pro) for UI; SF Mono for UIDs / ref tags / numeric fields.
- Accent color: blue (Apple standard). Deliberately **not** matching Marsh brand in v1 — this is a native-feeling app, and trying to brand-match a Mac app fights the platform. Brand alignment happens in exported deliverables (which stay in Python).
- Dark mode and light mode both supported (follow system).
- Dynamic Type support for accessibility.

---

## 5. Data Access & Write Safety

### 5.1 Access Pattern

- **GRDB.swift** as the SQLite driver. GRDB supports WAL, concurrent reads, custom collations, and has strong async/await integration.
- **Repository actors** per entity: `ClientsRepository`, `PoliciesRepository`, `ContactsRepository`, `ImportRepository`. Each owns a shared database queue and exposes async read/write methods.
- **Observation**: the UI observes repositories via `@Observable` models. After a write, the relevant repository emits a change notification; dependent views re-query.

### 5.2 Write-Rule Parity (ported from Python)

Swift ports **exactly** these rules from Python, one-for-one:

| Rule | Python source | Swift port |
|---|---|---|
| Currency parsing (`1m`, `500k`, `$2,000,000`) | `parse_currency_with_magnitude` (`utils.py`) | `CurrencyParser.parse(_:)` |
| Phone formatting (E.164 / pretty) | `format_phone` (`utils.py`, uses `phonenumbers`) | `PhoneFormatter.format(_:)` (uses Apple's `Contacts` framework or `libphonenumber` pod) |
| Email normalization | `clean_email` (`utils.py`) | `EmailCleaner.clean(_:)` |
| Next policy UID | `next_policy_uid` (`db.py`) — queries max(POL-N), increments | `UIDMinter.nextPolicyUID()` — same SQL logic |
| Next client UID fallback (`C{client_id}`) | derived from `clients.id` autoincrement | `UIDMinter.clientUID(for: id)` — formats as `C{id}` when `cn_number` is absent |
| Ref tag builder | `build_ref_tag` (`utils.py`) | `RefTagBuilder.build(client:location:policy:)` |
| Ref tag copy format | `copyRefTag()` JS helper | Native `NSPasteboard` write with `[PDB:…]` wrapper |

**Test harness:** each ported rule has a Swift unit test that pins to the identical Python behavior using a corpus of known inputs → expected outputs (exported from Python as a JSON fixture).

### 5.3 Write Column Whitelist (v1)

Swift writes only to the following columns. All other columns are **read-only** from Swift:

**clients:**
- `name`, `cn_number`, `industry`, `scratchpad`, `notes`, `address_*` (all address fields), `primary_email`, `primary_phone`

**policies:**
- `policy_number`, `carrier`, `policy_type`, `line_of_business`, `effective_date`, `expiration_date`, `premium`, `status`, `renewal_status`, `notes`

**projects (locations):**
- `name`, `address_*`, `notes`

**contacts / client_contacts:**
- `name`, `role`, `phone`, `mobile`, `email`, `notes`, `contact_type`

**Explicitly not written by Swift v1** (Python-owned):
- `activity_log` (any row)
- `policy_milestones` / timeline state
- `issues` / RFIs
- `inbox_items`
- Any column with a `computed_*` prefix or used by the anomaly engine
- `is_opportunity` flag cascades
- Ref tag minting on insert (Swift mints for new records via `UIDMinter`, but no write-back cascades)

### 5.4 Write Flow

1. User edits a field in the detail inspector.
2. On blur or Return, Swift calls the repository's update method.
3. Repository:
   a. Validates the value via the ported formatter (currency/phone/email).
   b. If valid, writes to SQLite via GRDB in a transaction.
   c. Returns the formatted value to the UI.
4. UI flashes the field (green tint) to confirm save, shows the formatted value.
5. Validation errors: red border + inline message; original value retained until corrected.

### 5.5 Concurrent Writer Handling

- SQLite WAL allows one writer at a time. If Python is mid-write, Swift's transaction waits (GRDB handles this transparently with a busy timeout).
- If contention is >500ms, Swift shows a subtle "Syncing…" indicator; if >5s, shows an error with retry.
- Swift never holds a write transaction open across UI interactions. Each field save is a short transaction.

---

## 6. Feature Detail

### 6.1 Clients CRUD

**List view:**

- Columns: Name, CN Number, Industry, Active Policies (count), Last Touched, Renewal Health (visual dot).
- Sort by any column. Default: Name ascending.
- Search bar (top): `LIKE` match on name + CN number + dba aliases.
- "+" button in toolbar → New Client sheet.

**Detail view:**

- Header: Name (large), CN Number (mono), ref-tag pill, industry badge.
- Sections (all open by default):
  1. **Overview** — name, CN, industry, address, primary email/phone. Edit-in-place.
  2. **Policies** — related list (all policies for this client). Click row to focus policy detail in a modal inspector sheet (or drill into the inspector pane).
  3. **Contacts** — related list, inline add/edit/remove. Matrix-style.
  4. **Locations** — related list of projects. Inline add/edit/remove.
  5. **Programs** — related list of bind orders (read-only in v1 from Swift; create/edit programs stays in Python).
  6. **Scratchpad** — full-width text area, auto-saves on blur.

**Create:**

- "+" in toolbar opens a compact sheet: Name, CN Number (optional, auto-minted if blank via `UIDMinter.nextClientUID`), Industry, Address.
- Save → inserts row → focuses newly created client in the detail inspector.

**Delete:**

- Right-click → Delete → confirmation dialog ("Delete Acme Corp? This will also delete 4 policies, 6 contacts, 2 locations.") → hard delete in cascading transaction.
- **Important:** only hard delete if no activity_log / issues / inbox references exist. Otherwise block deletion with a message directing the user to the webapp.

### 6.2 Renewal Queue

**Source:** `v_renewal_pipeline` view (Python-owned). Swift queries it read-only for the list; edits go directly to the underlying `policies` rows.

**List view:**

- Columns: Policy UID, Client, Carrier, LOB, Effective, Expiration, Premium, Days to Renewal, Renewal Status.
- Default sort: Days to Renewal ascending (most urgent first).
- Visual urgency cue: colored left-edge bar per row (red < 14 days, amber < 45 days, gray otherwise). This is a **visual cue replacing the focus-queue score** — no algorithm, just honest date math.
- Saved view pills above the list:
  - **Next 30 days** (default)
  - **Next 90 days**
  - **Overdue** (expired, still open status)
  - **By Carrier** (groups by carrier, accordion)
  - **By LOB** (groups by line of business)
- Inline edit of `renewal_status`, `premium`, `notes` via double-click cell.

**Detail panel:**

- Same three-pane: sidebar → list → detail inspector.
- Inspector shows policy detail: all fields, client link, carrier link, renewal history.
- Ref-tag pill copies `[PDB:CN…-L…-POL…]`.

**Excluded from queue** (matching Python behavior):

- Policies with `is_opportunity = 1`.
- Policies whose `renewal_status` is in the `renewal_statuses_excluded` config list (Swift reads this list from `config.yaml` — see §7.2).

### 6.3 Data Import + Edit

**Workflow:**

1. Sidebar → Import → drag-drop zone or file picker.
2. Swift parses CSV or XLSX (via native `CoreXLSX` for xlsx, `TabularData` for CSV).
3. Column auto-detection using a ported version of Python's `COLUMN_ALIASES` dict (e.g., "Policy #" → `policy_number`, "Named Insured" → `first_named_insured`, etc.).
4. **Mapping UI:** a table shows source columns on the left, PolicyDB fields on the right. User can override any mapping. Unmapped columns are ignored (flagged as "skipped").
5. **Preview:** bottom pane shows first 10 rows with parsed values formatted (currency, phone, email, dates). Validation errors highlighted per cell.
6. **Target entity picker:** user chooses Clients, Policies, or Contacts (v1 doesn't import Programs/Locations — those stay Python).
7. **Commit:** on Import button click, Swift inserts rows in a single transaction. UIDs are minted via `UIDMinter`. Duplicate detection is **not** performed in v1 (users can dedupe via the Python webapp after).
8. **Result summary:** "Imported 47 clients, skipped 2 (missing name column), 3 validation errors." Errors linked to preview rows for correction and re-import.

**Aliases file:**

- Ported from `src/policydb/importer.py` as a Swift JSON file (`ImportAliases.json`).
- Synced manually — whenever Python `importer.py` adds aliases, we mirror them in the Swift JSON. (A future enhancement: Swift reads aliases from a database table so they stay in sync automatically.)

---

## 7. Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| UI | SwiftUI (macOS 26 Tahoe target) | Latest platform, `NavigationSplitView`, Liquid Glass, `Table` |
| Data binding | Observation (`@Observable`) | Modern replacement for `ObservableObject` |
| DB driver | GRDB.swift | Mature SQLite driver with WAL, async/await, custom collations |
| CSV parsing | `TabularData` (Apple) | Native, no dep |
| XLSX parsing | `CoreXLSX` | Maintained Swift package |
| Phone formatting | `PhoneNumberKit` | Mirrors Python's `phonenumbers` library |
| Testing | `XCTest` + `swift-testing` (macro-based) | Unit + integration; snapshot tests for views |
| Build | Xcode project (not Swift Package for the app target itself) | Ships binary; SPM for dependencies |
| Distribution | `.app` bundle, user-built from Xcode | No notarization / Developer ID in v1 (solo user) |

### 7.1 Project Layout

```
PolicyDBMac/
├── PolicyDBMac.xcodeproj
├── PolicyDBMac/
│   ├── App/
│   │   └── PolicyDBMacApp.swift
│   ├── UI/
│   │   ├── Sidebar/
│   │   ├── Clients/
│   │   ├── Renewals/
│   │   ├── Import/
│   │   └── Shared/                 # ref-tag pill, edit-in-place cell, etc.
│   ├── Data/
│   │   ├── DatabaseManager.swift   # GRDB setup, WAL config
│   │   ├── Repositories/
│   │   │   ├── ClientsRepository.swift
│   │   │   ├── PoliciesRepository.swift
│   │   │   ├── ContactsRepository.swift
│   │   │   └── ImportRepository.swift
│   │   └── Models/                 # Plain structs (Codable, Identifiable)
│   ├── Rules/                      # Ported Python rules
│   │   ├── CurrencyParser.swift
│   │   ├── PhoneFormatter.swift
│   │   ├── EmailCleaner.swift
│   │   ├── UIDMinter.swift
│   │   └── RefTagBuilder.swift
│   ├── Import/
│   │   ├── ImportAliases.json
│   │   └── ImportPreview.swift
│   └── Resources/
│       ├── Assets.xcassets
│       └── Info.plist
├── PolicyDBMacTests/
│   ├── RulesTests/                 # Parity tests against Python fixtures
│   ├── RepositoryTests/
│   └── Fixtures/
│       └── python-rule-outputs.json  # exported from Python, committed
└── README.md
```

### 7.2 Config Reading

Swift reads `~/.policydb/config.yaml` at startup (via Yams) to pick up:

- `renewal_statuses_excluded` — filters renewal queue
- `renewal_statuses` — options in status dropdowns
- `policy_types`, `carriers`, `activity_types` — dropdowns
- `log_retention_days` — (currently not used by Swift; reserved for future activity log view)

Config is read-only from Swift v1. Editing config stays in the Python Settings UI.

---

## 8. Testing Approach

### 8.1 Rule Parity Tests

Export Python rule outputs as a JSON fixture:

```json
{
  "parse_currency": [
    {"input": "1m", "expected": 1000000},
    {"input": "500k", "expected": 500000},
    {"input": "$2,000,000", "expected": 2000000}
  ],
  "format_phone": [
    {"input": "(555) 123-4567", "expected": "+15551234567"},
    ...
  ]
}
```

Swift test: load fixture, run each input through the Swift port, assert `==` expected. Committed in `Tests/Fixtures/`.

### 8.2 Repository Tests

- Use an in-memory SQLite database seeded with a minimal schema + fixture rows.
- Test CRUD methods, filter queries, and write-rule enforcement.
- Do **not** load Python migrations. Instead, maintain a minimal schema snapshot (`TestSchema.sql`) sufficient for Swift's query surface.

### 8.3 UI Snapshot Tests

- Use `swift-snapshot-testing` for the three main pane types.
- Snapshot the list row, detail inspector, and import preview in light and dark mode.

### 8.4 Integration Tests

- A single integration test runs against a real copy of `~/.policydb/policydb.sqlite` (opt-in via environment variable) to verify Swift reads valid data from the user's actual DB.
- Write tests use a temporary clone of the DB and verify Python can open it after Swift writes (no corruption).

### 8.5 Manual QA

Per `CLAUDE.md`, any UI change requires manual visual verification. For Swift:

- Screenshot each pane in both light and dark mode before claiming complete.
- Verify ref-tag copy round-trip (copy from Swift → paste into Outlook → visible text matches Python's format exactly).
- Run Python webapp concurrently and verify: create a client in Swift, refresh webapp client list, confirm it appears.

---

## 9. UID & Ref-Tag Behavior

### 9.1 UID Minting

- **POL-NNN** (policies): next integer after max existing POL-N. Swift mints locally before INSERT, matching Python's `next_policy_uid()`.
- **Client records:** use `cn_number` if present, otherwise fall back to `C{client_id}` (matches Python). Swift does not mint `cn_number` — that comes from the user or AMS import.
- **Race safety:** UID minting runs inside the same transaction as the INSERT. If Python mints the same UID concurrently (possible but unlikely), the INSERT fails with a UNIQUE constraint violation and Swift retries with a fresh mint.

### 9.2 Ref-Tag Copy

Clicking any ref-tag pill in the Swift UI writes the following to `NSPasteboard.general`:

```
[PDB:CN1234567-L3-POL042]
```

Format is identical to the webapp's `copyRefTag()` JavaScript behavior. Hierarchy depth matches the record context:

- Client pill → `[PDB:CN1234567]`
- Location pill → `[PDB:CN1234567-L3]`
- Policy pill → `[PDB:CN1234567-L3-POL042]` (full hierarchy)
- Program pill → `[PDB:CN1234567-PGM12]`

A copy confirmation toast ("Ref tag copied") flashes briefly.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Swift and Python drift in formatting rules (e.g., Python updates `parse_currency_with_magnitude` edge case, Swift doesn't) | Rule-parity test fixtures. Regenerate fixtures on every Python utils change. Document update process in Swift README. |
| Concurrent write contention causes UI jank | WAL mode + short transactions. "Syncing…" indicator for visible contention. |
| User expects Swift to do something Python handles (e.g., logs a note, expects auto-close) | Clear UI messaging: "Activities are managed in the Python app — open at http://localhost:8000". Sidebar placeholders for un-built features. |
| Schema change in Python breaks Swift | Schema-version check at Swift startup. Block launch with update prompt. |
| Import with bad column mapping corrupts data | Dry-run preview mandatory. Commit is a single transaction — rollback on any error. |
| CN number collision (Swift creates client with CN already used elsewhere) | UNIQUE constraint on `cn_number` at DB level. Swift shows clear conflict error. |
| Swift writes a field Python doesn't expect (schema drift the other direction) | Whitelist enforcement in repository layer. No dynamic column setters. |

---

## 11. Success Criteria

v1 is successful if:

1. **All three flows complete end-to-end in Swift** without falling back to the webapp.
2. **Data survives a round-trip:** create/edit in Swift, verify in Python; edit in Python, verify in Swift.
3. **UID / ref-tag copy matches Python format exactly** — byte-identical output verified by paste-into-test.
4. **No SQLite corruption** after 1 week of concurrent use (both apps running, user edits in both).
5. **Performance:** client list of 1,000 rows renders in <200ms; policy list of 5,000 rows renders in <500ms.
6. **Rule parity tests pass** (all ported rules match Python fixtures).
7. **Subjective:** the app feels native and polished enough that the user prefers it for the three covered flows.

If criteria 1–6 pass but 7 does not, we iterate on UI polish before expanding scope. If 1–6 fail, we pause and re-evaluate (the coexist architecture may be too fragile, or the rule-port surface too large).

---

## 12. Future Phases (out of scope, sketched)

**Phase 2 (after v1 evaluation):**

- Activities view (read + create log entries; no auto-close, no focus queue)
- Contacts as a top-level entity (not just under clients)
- Policies as a top-level list (not just via client or renewal queue)

**Phase 3:**

- Programs editor (create/edit bind orders natively)
- Carrier management (currently read-only from Swift)
- Saved search / filter persistence across launches

**Phase 4 — Python shrink (Z):**

- Move Outlook bridge to Swift (native macOS `NSAppleScript` or ScriptingBridge).
- Move importer UX fully into Swift; Python `importer.py` becomes a CLI-only fallback.
- Retire Python UI; keep Python as headless exporter + email-template renderer + FTS5 search service.

Each future phase gets its own spec at design time.

---

## 13. Open Questions

1. **Architecture assumption:** are you comfortable running Python and Swift concurrently with both writing to the same SQLite file? WAL makes it safe, but if you'd rather Swift be reader-only when Python is running (and writer-only when Python is stopped), that changes the repository design.
2. **macOS 26 Tahoe vs. macOS 15 Sequoia:** Tahoe gets Liquid Glass and latest SwiftUI, but if you're not on Tahoe yet, v1 should target 15 to avoid blocking yourself. Which macOS are you running?
3. **Ref-tag pill copy — one or two formats:** the webapp copies `[PDB:…]` wrapped. Should Swift also offer a plain-text variant (⌥-click to copy without brackets) for cases where you want just the ref tag? Or keep it single-format for consistency?
4. **Import scope — which entities:** v1 proposes Clients / Policies / Contacts. Is that the right set, or should Projects/Locations be included from day one?
5. **Delete semantics:** hard delete with cascade warning, or soft-delete flag? Python uses hard delete today; Swift could introduce a soft-delete column if you want undo, but that's a schema change (and Python would need to learn to filter it).

---

## 14. Next Step

Once this spec is approved, the next step is an implementation plan (via the `writing-plans` skill) — breaking the work into ordered, verifiable tasks with dependencies and acceptance criteria per task.
