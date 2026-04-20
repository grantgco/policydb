# Coverage — PolicyDB macOS Client

Native macOS SwiftUI client for PolicyDB. Coexists with the Python webapp by
reading and writing the same `~/.policydb/policydb.sqlite` database directly
via GRDB — Python owns the schema, Swift is a second reader/writer.

## Requirements

- macOS 26 Tahoe
- Xcode 26+
- Existing PolicyDB Python setup (`~/.policydb/policydb.sqlite` and
  `~/.policydb/config.yaml` must exist and be at schema version
  `DatabaseManager.minimumSupportedSchemaVersion` or later)

## Build & Run

```bash
open Coverage.xcodeproj
```

Press **⌘R**. On launch the window runs a foundation smoke test — opens
the SQLite DB in WAL mode, enforces the schema-version compatibility
range, and parses the YAML config. If it's green, you'll see schema +
carrier/renewal-status counts. If it's red, the error text explains
what's wrong (usually: DB missing, schema too new/old, or config can't
parse).

> **Sandbox:** App Sandbox is off so Swift can read `~/.policydb/`
> directly. This is intentional for a single-user local tool and matches
> how the Python webapp already runs.

## Tests

```bash
xcodebuild test \
  -project Coverage.xcodeproj \
  -scheme Coverage \
  -destination 'platform=macOS'
```

Or press **⌘U** in Xcode.

## Rule Parity

Five rules are ported from Python (`src/policydb/utils.py`,
`src/policydb/db.py`) and verified against a shared JSON fixture:

| Swift type      | Python function                  |
|-----------------|----------------------------------|
| `CurrencyParser`| `parse_currency_with_magnitude`  |
| `PhoneFormatter`| `format_phone`                   |
| `EmailCleaner`  | `clean_email`                    |
| `UIDMinter`     | `generate_issue_uid`             |
| `RefTagBuilder` | `build_ref_tag`                  |

Regenerate the fixture whenever Python rule implementations change:

```bash
cd /Users/grantgreeson/Developer/policydb
~/.policydb/venv/bin/python scripts/export_swift_fixtures.py
```

Then **⌘U** — any Swift tests that break need their port updated to
match the new Python behavior. The fixture is the contract.

## Schema Version Range

Swift understands PolicyDB schema versions between
`DatabaseManager.minimumSupportedSchemaVersion` and
`maximumSupportedSchemaVersion` (both in
`Coverage/Data/DatabaseManager.swift`). When Python ships a new
migration, bump the max after manually verifying Swift still works.

## Architecture

Full design spec: `docs/2026-04-20-swift-macos-frontend-design.md`.

Phase 1 (this tree) ships foundation only — ported rules, config
reader, database manager, schema-compat gate, and a smoke-test window.
Phase 2 begins with `ClientsRepository` + the Clients list UI.
