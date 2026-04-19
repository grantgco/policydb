# Named Insured Compliance — Design Spec

**Date:** 2026-04-17
**Status:** Awaiting implementation
**Scope:** One PR. Full schema, backfill, matching library, compliance-check integration, all UI surfaces.

## Problem

The current compliance review checks policy **limits**, **deductibles**, and **required endorsements** against each `coverage_requirement` sourced from a contract. It does **not** verify that the contracting entity on *our client's* side is actually afforded coverage on the linked policy.

Concrete example: the client is XYZ Holdings. A subsidiary, ABC Corp, signs a lease with Landlord Inc. The lease requires ABC Corp to carry $2M GL with the Landlord as Additional Insured. Today, the compliance tool will mark this Compliant as long as a $2M GL policy exists with a blanket AI endorsement — even if ABC Corp is not a named insured on that policy. In reality the policy affords no coverage to ABC Corp's lease operations, and the client has an uninsured loss waiting to happen.

The broker has been informally using the `billing_accounts` table as a proxy roster of subsidiary entities. That's wrong semantically (billing ≠ named insured) and doesn't solve the per-policy and per-contract verification problem.

## Goals

1. Maintain a canonical roster of every legal entity in each client's org.
2. Record, per policy, which of those entities are on the dec page or schedule, and at what status (Named Insured, Additional Named Insured, Additional Insured).
3. Record, per contract (`requirement_source`), which of those entities the contract requires to be afforded which status.
4. Extend the compliance check to verify those entity requirements, with specific failure reasons and one-click write-back.
5. Handle accounts with 50-100 entities without crushing data entry.

## Non-goals (v1)

- Modelling corporate hierarchy (parent → sub → sub-sub) as a self-FK.
- Sharing rosters across `client_groups` (linked accounts).
- OCR or PDF parse of dec pages.
- FEIN-driven matching (FEIN is stored and displayed, not used in resolution logic).
- Automated "ask the carrier to add this entity" email flow.

---

## Terminology

- **NI (Named Insured)**: listed on the policy declarations page. First-party coverage.
- **ANI (Additional Named Insured)**: added to the dec-page schedule via endorsement. Treated as a named insured for coverage purposes.
- **AI (Additional Insured)**: endorsement (CG 20 10, CG 20 37, CG 20 26, blanket forms) extending liability coverage to a third party for their vicarious liability arising from the named insured's operations.
- **Blanket AI**: an endorsement that automatically extends AI status to anyone the named insured has contracted to add. No schedule.
- **Scheduled AI**: an endorsement with a specific written schedule of AIs.
- **Roster**: the client's master list of legal entities (new).
- **Required insured**: an entity that a contract requires to be afforded NI/ANI/AI status.

---

## Data Model

All changes ship in migration `163_named_insured_compliance.sql`. Run-once, backfill inline, wired into `init_db()`.

### New tables

```sql
CREATE TABLE client_entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    primary_name    TEXT NOT NULL,
    fein            TEXT,
    entity_type     TEXT,           -- 'parent' | 'subsidiary' | 'affiliate' | 'dba' | 'jv' | 'other'
    status          TEXT NOT NULL DEFAULT 'active',    -- 'active' | 'inactive' | 'dissolved'
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_client_entities_client ON client_entities(client_id);
CREATE TRIGGER client_entities_updated_at
    AFTER UPDATE ON client_entities
    FOR EACH ROW
    BEGIN UPDATE client_entities SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

CREATE TABLE client_entity_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES client_entities(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,          -- original spelling as entered
    alias_norm      TEXT NOT NULL,          -- normalized for matching (index-backed)
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, alias_norm)
);
CREATE INDEX idx_entity_aliases_entity ON client_entity_aliases(entity_id);
CREATE INDEX idx_entity_aliases_norm   ON client_entity_aliases(alias_norm);

CREATE TABLE policy_insureds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL,          -- policies.policy_uid (text UID, no hard FK)
    entity_id       INTEGER NOT NULL REFERENCES client_entities(id) ON DELETE CASCADE,
    status          TEXT NOT NULL,          -- 'NI' | 'ANI' | 'AI_scheduled' | 'AI_blanket'
    endorsement_form TEXT,                  -- e.g. 'CG 20 10 04 13'
    effective_date  DATE,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_uid, entity_id)           -- one status per entity per policy
);
CREATE INDEX idx_policy_insureds_policy ON policy_insureds(policy_uid);
CREATE INDEX idx_policy_insureds_entity ON policy_insureds(entity_id);

CREATE TABLE requirement_source_insureds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES requirement_sources(id) ON DELETE CASCADE,
    entity_id       INTEGER REFERENCES client_entities(id) ON DELETE CASCADE,  -- null when match_mode='all_active'
    required_status TEXT NOT NULL,          -- 'NI' | 'ANI' | 'AI'
    notes           TEXT,
    UNIQUE(source_id, entity_id, required_status)
);
CREATE INDEX idx_req_src_insureds_source ON requirement_source_insureds(source_id);

-- match_mode on the source itself
ALTER TABLE requirement_sources ADD COLUMN match_mode TEXT NOT NULL DEFAULT 'explicit';
    -- 'explicit'   => requirement_source_insureds must have at least one row with non-null entity_id.
    --                 Each row specifies an entity + required_status to check.
    -- 'all_active' => requirement_source_insureds must have exactly one row with entity_id IS NULL,
    --                 which carries the default required_status applied to every active client_entities
    --                 row at review time. Explicit entity_id rows are disallowed in this mode.
    -- Both invariants enforced at save time in the route layer.
```

### Existing-table tweaks

```sql
ALTER TABLE clients ADD COLUMN entity_id INTEGER REFERENCES client_entities(id) ON DELETE SET NULL;
ALTER TABLE billing_accounts ADD COLUMN entity_id INTEGER REFERENCES client_entities(id) ON DELETE SET NULL;
-- policies.first_named_insured stays as freeform text (legacy display, importer-friendly).
-- policy_insureds is the canonical source of truth. After any write to policy_insureds
-- (add / change / delete), a sync step updates policies.first_named_insured to:
--   * the primary_name of the single entity with status='NI' on that policy, or
--   * the primary_name of the earliest-inserted NI if multiple NIs exist, or
--   * unchanged if zero NIs (preserve legacy value for reference).
```

### Backfill (inline in migration 163)

1. For every row in `clients`, insert `client_entities(client_id=clients.id, primary_name=clients.name, fein=clients.fein, entity_type='parent')`. Insert `client_entity_aliases(alias=clients.name)`. Update `clients.entity_id`.

2. For every `billing_accounts` row with non-empty `entity_name`:
   - Call `resolve_entity(conn, client_id, entity_name, fuzzy_threshold=95)`.
   - If exact/alias match → set `billing_accounts.entity_id`.
   - Otherwise insert a new `client_entities` row (`entity_type='subsidiary'` if `billing_accounts.is_master=0`, else `'parent'`) and link.

3. For every `policies` row with non-empty `first_named_insured`:
   - Skip if `is_opportunity=1`.
   - Call `resolve_entity(conn, policies.client_id, first_named_insured, fuzzy_threshold=95)`.
   - Exact/alias match → insert `policy_insureds(policy_uid, entity_id, status='NI')`.
   - No match → insert a new `client_entities` row (`entity_type='other'`), add its name as an alias, insert `policy_insureds` with `status='NI'`.

4. Collect every unmatched-or-fuzzy case in a new `roster_review_flags` in-memory list during migration; surface it on a "Needs roster review" filter in the Entities tab (not a blocker to migration).

Backfill is idempotent: re-running inserts nothing (UNIQUE constraints on alias_norm and `policy_insureds(policy_uid, entity_id, status)`).

---

## Matching Library

New module: `src/policydb/entity_matching.py`.

### Normalization

```python
CORPORATE_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "llc", "l l c", "ltd", "limited",
    "co", "company", "lp", "llp", "plc", "gmbh", "sa", "ag", "nv", "bv", "pty",
}

def normalize_entity_name(name: str) -> str:
    """Returns the casefold, stripped, punctuation-free, suffix-stripped form."""
```

- Casefolds, strips, collapses whitespace, removes punctuation (`.,&()/`).
- Iteratively strips trailing corporate suffixes (handles "Inc LLC", "Corporation, Inc.").
- Preserves substantive words that can appear in a suffix position but carry meaning: `holdings`, `trust`, `partners`, `associates`, `group`. ("XYZ Family Trust" must not normalize to "xyz family".)
- Pure function; unit-tested against 30+ real-world name variants.

### Resolution

```python
@dataclass
class ResolveResult:
    entity_id: int | None
    confidence: str           # 'exact' | 'alias' | 'fuzzy' | 'none'
    score: float              # 0-100
    suggestions: list[dict]   # up to 3: [{entity_id, primary_name, score}]

def resolve_entity(
    conn, client_id: int, name: str, *, fuzzy_threshold: int = 90,
) -> ResolveResult: ...

def resolve_entities_bulk(
    conn, client_id: int, names: list[str], *, fuzzy_threshold: int = 90,
) -> list[ResolveResult]:
    """Loads the client roster once, reuses the RapidFuzz corpus across all inputs."""

def create_entity_from_name(
    conn, client_id: int, name: str, *, entity_type: str = 'other',
) -> int:
    """Insert new client_entities row + seed alias. Returns new id."""
```

Lookup order: exact normalized match (alias_norm index) → alias match (same index, different entity) → RapidFuzz WRatio across primary_name + all aliases for the client; ≥ threshold → `'fuzzy'`; else `'none'` with top-3 suggestions.

Resolution is always scoped to a single `client_id`. No cross-client matching.

### DBA parsing (bonus)

The paste modal and importer pre-split input like `"ABC Corp dba XYZ Trading"` on case-insensitive `\s+d[/.]?b[/.]?a\s+`, `\s+doing business as\s+`, `\s+f[/.]?k[/.]?a\s+`. The first fragment is the primary, the rest become aliases on that entity.

---

## Compliance Check Integration

### Refactored compute_auto_status

```python
@dataclass
class ComplianceAxis:
    ok: bool
    reason: str = ""
    # axis-specific detail
    required: float | None = None
    actual: float | None = None
    missing: list | None = None

@dataclass
class ComplianceCheck:
    status: str                         # 'Compliant' | 'Partial' | 'Gap'
    axes: dict[str, ComplianceAxis]     # keys: 'limits' | 'deductible' | 'endorsements' | 'insureds'

def compute_auto_status(
    requirement: dict,
    policy: dict | None,
    required_insureds: list[dict],      # rows from requirement_source_insureds joined w/ client_entities
    policy_insureds: list[dict],        # rows from policy_insureds for the linked policy
    effective_limit: float | None = None,
) -> ComplianceCheck: ...

def compute_auto_status_str(...) -> str:
    """Thin shim: returns .status. Preserves compatibility for callers that only want the string."""
```

### Entity axis rules

For each row in `required_insureds` (or, if `match_mode='all_active'`, one synthetic row per active `client_entities` using the source's default required_status):

- Find any `policy_insureds` row with `entity_id == required.entity_id` and `status` satisfying the ladder below.
- Miss → append `{entity_id, entity_name, required_status}` to `axes['insureds'].missing`.

**Status equivalence ladder** — what satisfies a required status:

| Required | Accepted on policy |
|---|---|
| `NI`  | `NI` |
| `ANI` | `NI`, `ANI` |
| `AI`  | `NI`, `ANI`, `AI_scheduled`, `AI_blanket` |

Rationale: first-named-insured status is strictly stronger than additional-named or additional-insured. If a contract requires ANI and the entity is already an NI, the coverage intent is satisfied (being primary is more protective than being additionally named). If a contract requires NI specifically and the entity is only ANI, that IS a gap — some contract clauses give first-named-insured-specific rights (notice, premium handling, first-party claim rights) that ANI doesn't carry.

### Rollup

- Any axis `ok=False` → `status = 'Gap'`.
- All axes `ok=True` → `status = 'Compliant'`.
- `'Partial'` is no longer auto-assigned. It becomes a manual-only reviewer override meaning "I acknowledge a gap exists but it's being actively resolved" — requires a note (e.g., "carrier has agreed to endorse Sub LLC, awaiting docs by 2026-04-25"). Existing `Partial` rows migrated from pre-v1 data stay as-is until a reviewer touches them.

### Stale detection

`detect_stale_compliance()` adds a new trigger: for every `Compliant`/`Partial` requirement, recompute the `insureds` axis. If a previously-satisfied required entity no longer appears on the policy schedule, flip to `Needs Review` with auto-note:

> `[Auto 2026-04-17] ABC Corp no longer on POL-042 Named Insured schedule`

Uses the same every-page-load cadence as the existing stale checks. No background job.

### all_active resolution timing

`match_mode='all_active'` resolves **at review time**, not at contract save time. If the client later adds a subsidiary to the roster, every contract with `all_active` automatically requires that entity to be covered. This matches the broker's intent on master services agreements ("Tenant and its subsidiaries").

Backend validation: `required_status='AI'` is incompatible with `match_mode='all_active'` (nobody requires all client entities as AI on a counterparty policy). Rejected at save time with an inline error.

---

## UI

All UI follows existing PolicyDB patterns: contenteditable cells with HTMX PATCH on blur, combobox pickers, no Save buttons, click-to-edit.

### 1. Client page — Entities tab

New tab between "Contacts" and the less-frequent tabs. Lazy-loaded via HTMX on first click (existing tab pattern). Tab state persisted in `sessionStorage`.

- Contenteditable matrix: Primary Name, Type (combobox from new `entity_types` config list), FEIN, Status (combobox), Aliases (count cell → expand panel).
- Row `…` menu: **Merge into…** (entity picker; transactional move of refs), **Archive** (sets status='inactive').
- Row expansion shows usage summary: `"On 4 policies · Required by 2 contracts · Linked to billing account 123456"`.
- `+ Add entity` button → appends a blank row (existing matrix pattern).
- `Bulk paste` button → opens shared paste modal (see §4).

### 2. Policy Details subtab — Insured Schedule

Added to the existing policy Details tab, below the endorsements section. Not a new top-level tab.

- Contenteditable matrix, one row per entity *on* the policy (not the whole roster).
- Columns: Entity (typeahead on add; read-only once set — use delete to remove), Status (combobox: NI / ANI / AI scheduled / AI blanket), Endorsement form, Effective date, Notes.
- `+ Add entity` → typeahead against client roster with "Create new entity…" at the bottom of the dropdown.
- `Bulk paste` → shared paste modal with the extra "Status on this policy" column.
- `Copy from policy…` → modal listing this client's other non-archived policies; pick one, see a diff preview (entities to add / already present / on source but not copied), commit or cancel.

### 3. Requirement source — Required Entities section

Added to the existing requirement_source edit screen (already has name / counterparty / clause_ref / notes).

- Radio: `Specific entities` (match_mode='explicit') / `All active client entities` (match_mode='all_active').
- In `explicit` mode: contenteditable matrix with Entity (typeahead), Required As (combobox NI/ANI/AI), Notes.
- In `all_active` mode: a single read-only row showing the default required status with a note explaining "All entities in the XYZ Holdings roster marked Active will be checked against this requirement."
- "Add counterparty as new entity" shortcut button — one-click: takes `requirement_sources.counterparty` text, creates a roster entity (entity_type='other'), adds a row requiring `AI`.

### 4. Bulk paste modal (shared)

Single component, called from the Entities tab and from each policy's Insured Schedule.

Flow:

1. User pastes text. Free-form, one line per entity. Tabs optionally separate columns (name\tstatus\tendorsement).
2. On submit, server calls `resolve_entities_bulk()`, splits DBAs, returns a resolution table.
3. Each pasted line shows: original text, matched entity (with confidence badge), action dropdown.
   - Exact match: action fixed to "Use existing."
   - Alias match: same.
   - Fuzzy match: dropdown defaults to "Use existing (ABC Corp — 94%)"; alternatives "Add as new entity" or "Skip."
   - No match: defaults to "Add new entity"; alternatives are each top-3 suggestion or "Skip."
4. Policy context adds a "Status on this policy" column (default inherited from a "Set all" bulk control).
5. Commit → single transaction: INSERT any new entities, UPSERT policy_insureds rows, return re-rendered matrix.

### 5. Compliance review slideover (4-axis display)

Replaces the current single status display in the compliance review slideover.

```
Requirement: General Liability · required $2M / $10k max ded
Linked policy: POL-042 (Traveler's)   [Change]

Limits              ✓  $2M / required $2M
Deductible          ✓  $10k / max $25k
Endorsements        ✗  Missing: Waiver of Subrogation, Primary & Non-contributory
                       [Mark present on POL-042]   [Waive with note]
Required Entities   ✗  Missing:
                       · ABC Corp (required NI)      [Add as NI to POL-042]
                       · Sub LLC (required ANI)      [Add as ANI to POL-042]  [Mark pending]

Overall: Gap
[Set to Partial with note]  [Mark Pending Info]  [Waive with note]
```

Write-back buttons each POST a small HTMX partial endpoint:

- Endorsement write-back → existing pattern, no change.
- `Add as NI/ANI to POL-042` → INSERT `policy_insureds`, re-run compute, return re-rendered slideover section.
- `Mark pending` → sets `compliance_status='Pending Info'` on the requirement with a required note.
- After any write-back, the page-level list re-renders so newly Compliant requirements update visually.

### 6. FTS5 search

Add `client_entity` to the search index (`rebuild_search_index()` in `queries.py`):

- `title` = primary_name
- `subtitle` = entity_type · FEIN
- `body` = all aliases joined

### 7. Routes

All new endpoints under existing prefixes where possible.

```
GET    /clients/{id}/entities              → tab body (lazy-load target)
GET    /clients/{id}/entities/partial      → matrix body for HTMX swaps
POST   /clients/{id}/entities/add-row      → append blank row
POST   /clients/{id}/entities/{eid}/cell   → cell PATCH
GET    /clients/{id}/entities/{eid}/aliases
POST   /clients/{id}/entities/{eid}/aliases
POST   /clients/{id}/entities/{eid}/merge/{target_eid}   -- {eid} is source (deleted after merge); {target_eid} is destination
DELETE /clients/{id}/entities/{eid}
POST   /clients/{id}/entities/bulk-paste/resolve  → resolution table
POST   /clients/{id}/entities/bulk-paste/commit   → apply resolution

GET    /policies/{uid}/insureds            → subtab body
POST   /policies/{uid}/insureds/add        → new row (entity_id + status)
POST   /policies/{uid}/insureds/{id}/cell  → cell PATCH
DELETE /policies/{uid}/insureds/{id}
POST   /policies/{uid}/insureds/bulk-paste/resolve
POST   /policies/{uid}/insureds/bulk-paste/commit
POST   /policies/{uid}/insureds/copy-from  → {source_policy_uid: ...}

POST   /requirement-sources/{id}/match-mode       → set explicit | all_active
POST   /requirement-sources/{id}/insureds/add
POST   /requirement-sources/{id}/insureds/{rsi_id}/cell
DELETE /requirement-sources/{id}/insureds/{rsi_id}
POST   /requirement-sources/{id}/insureds/add-counterparty  → one-click
```

### 8. Config

New editable lists in `config.yaml` (added to `_DEFAULTS` + `EDITABLE_LISTS`):

- `entity_types` — `['parent', 'subsidiary', 'affiliate', 'dba', 'jv', 'other']`
- `entity_statuses` — `['active', 'inactive', 'dissolved']`
- `insured_statuses` — `['NI', 'ANI', 'AI_scheduled', 'AI_blanket']`  (UI label map: `'Named Insured', 'Additional Named Insured', 'Additional Insured (Scheduled)', 'Additional Insured (Blanket)'`)
- `required_statuses` — `['NI', 'ANI', 'AI']` (UI label: same minus the AI distinction)
- `compliance_match_modes` — `['explicit', 'all_active']`

---

## Email templates

New token helpers exposed in `email_templates.py` context:

- `{{missing_insureds}}` — "ABC Corp, Sub LLC"
- `{{missing_insureds_list}}` — bulletted list for rich templates
- Scope: requirement-context and policy-context emails.

Added to `CONTEXT_TOKEN_GROUPS` under "Compliance" group.

---

## Exports

- **Schedule of Insurance xlsx** (`exporter.py`): new "Insured Schedule" sheet. Columns: Policy UID, Policy Number, Entity, Status, Endorsement Form, Effective Date.
- **Compliance report** (`exporter.py`): per-requirement row gains four columns — `Limits OK`, `Deductible OK`, `Endorsements OK`, `Entities OK` — before the rolled-up status.
- **Copy-table** (Action Center compliance gaps): row detail text includes `Missing: ABC Corp (NI), Sub LLC (ANI)` when the insureds axis failed.

---

## Importer

`importer.py` gains a new canonical field plus alias map entries:

- `insured_entities` — accepts a semicolon-separated list of names, optionally with `:STATUS` suffix (`"ABC Corp:NI; Sub LLC:ANI"`; default status `NI`).
- Each name passed through `resolve_entity(create_if_missing=True)` after the client is resolved.
- Rows inserted into `policy_insureds`. The existing `first_named_insured` column alias still populates `policies.first_named_insured` for legacy display.

---

## Edge cases

- **Opportunity policies** skip entity verification (`is_opportunity=1`).
- **Linked accounts** (`client_groups`): each client keeps its own roster. No cross-group sharing in v1.
- **Archived entities**: filtered out of `all_active` matching, shown with a yellow warning if still referenced explicitly in a `requirement_source_insureds` row.
- **Hard delete of roster entity** only allowed when zero `policy_insureds` + zero `requirement_source_insureds` references. Otherwise `…` menu only offers Archive.
- **Merge**: moves `policy_insureds`, `requirement_source_insureds`, `billing_accounts`, aliases to target; dedupe `policy_insureds` by `(policy_uid, entity_id, status)` keeping the strongest status (NI > ANI > AI_scheduled > AI_blanket); delete source. Transactional.
- **Contract counterparty is a client entity** (intercompany lease): handled normally; both sides can be required insureds on the same contract with different required statuses.
- **Required entity archived mid-lifecycle**: requirement flips to `Needs Review` with auto-note `[Auto …] Required entity Sub LLC archived`.
- **Policy-level `first_named_insured` diverges from `policy_insureds`**: the Insured Schedule subtab is the truth; `first_named_insured` is kept in sync on save from the matrix (the entity marked `status='NI'` and `endorsement_form IS NULL` becomes the first named, or the first-inserted NI if multiple).

---

## Tests

New test modules, all using the existing real-SQLite pattern (no mocks):

1. `tests/test_entity_matching.py`
   - Normalization covers 30+ real-world name variants (suffixes, punctuation, DBAs, "fka", trust/holdings).
   - Exact, alias, fuzzy paths each covered.
   - Fuzzy threshold boundary cases (89 vs 90 vs 91).
   - Bulk resolution reuses the corpus and returns in the same order.

2. `tests/test_compliance_entity_check.py`
   - NI required + NI present → Compliant.
   - NI required + ANI present → Gap.
   - ANI required + NI present → Compliant (upward ladder).
   - AI required + AI_blanket present → Compliant.
   - Missing entity → Gap with correct `axes.insureds.missing` content.
   - `match_mode='all_active'`, one active sub absent from policy → Gap.
   - Archive an entity → no longer in required list, Compliant restored.
   - `required_status='AI'` + `match_mode='all_active'` rejected at save.

3. `tests/test_compliance_writeback.py`
   - Add entity as NI via slideover button → `policy_insureds` row created → status flips Gap → Compliant.
   - Endorsement write-back still works (regression).

4. `tests/test_bulk_paste.py`
   - 10 pasted names mixing exact / alias / fuzzy / none → correct resolution table.
   - Commit inserts new entities + `policy_insureds` rows transactionally.
   - DBA parsing splits correctly.
   - Reopening the modal on a policy with existing insureds shows existing as "already present."

5. `tests/test_backfill.py`
   - Given a sample DB with clients, billing_accounts (mixed entity_names), and policies with first_named_insured — run migration.
   - All clients get a parent roster entity.
   - `billing_accounts.entity_id` populated where possible.
   - `policy_insureds` NI rows match on existing names.
   - No data loss: `first_named_insured` preserved on policies.

6. `tests/test_merge_entities.py`
   - Merge ABC → XYZ with overlapping policy_insureds.
   - Strongest status wins per (policy_uid, entity_id).
   - Source deleted, aliases moved, UNIQUE conflicts handled.

7. `tests/test_stale_entity_removal.py`
   - Policy with ABC as NI; requirement Compliant.
   - Remove ABC from policy_insureds.
   - Reload compliance page → requirement flipped to Needs Review with `[Auto …]` note.

---

## Rollout

Single PR. Migration 163 handles backfill inline. After merge:

1. Deploy; `init_db()` runs the migration automatically on server start.
2. Spot-check the "Needs roster review" filter on the Entities tab of 3-5 real clients — fix any flagged entities manually.
3. Pick one real compliance review with a known entity gap (pick a lease); verify the 4-axis slideover surfaces it correctly and the write-back button works.

Rollback: migration is schema-additive only (no column drops, no data deletes on existing tables). If rollback is needed, drop the four new tables and the two added columns; `first_named_insured` data is untouched.

---

## Open questions captured for v2

- Parent/child hierarchy on `client_entities` (self-FK `parent_entity_id`).
- Shared rosters across `client_groups` (linked accounts).
- OCR/parse from uploaded dec-page PDF.
- FEIN-driven matching (currently displayed only).
- Auto-draft "request endorsement to add entity" email from the slideover write-back.
- Policy-level "NI template" (inherit insured list from umbrella).
