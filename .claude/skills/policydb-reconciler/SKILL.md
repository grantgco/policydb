---
name: policydb-reconciler
description: >
  Reconciler and Pairing Board pattern reference for PolicyDB. Use when working on statement
  reconciliation, the pairing board UI, match scoring, drag-to-pair, location assignment,
  or any record-matching workflow. Covers scoring principles, normalization, diff tracking,
  the pairing board UI pattern, and the reconcile workflow.
---

# Reconciler

`src/policydb/reconciler.py` — matches imported rows to existing policies using additive scoring via `_score_pair()`.

## Core Principles
- **No hard gates** — every signal contributes independently, no single field blocks a match
- **Two normalization categories:** display/save functions (write to DB) vs matching functions (comparison only, never save)
- Track diffs at **both** levels: `diff_fields` (real) AND `cosmetic_diffs` (same after normalization)
- **Railroad Protective Liability** is a distinct type — never alias to General Liability
- Coverage aliases: `_COVERAGE_ALIASES` in `utils.py` + user-learned `coverage_aliases` in config
- Carrier aliases: `carrier_aliases` in config (merged via `rebuild_carrier_aliases()`)

See `reconciler.py` for scoring weights/tiers and `utils.py` for normalization functions.

## Reconcile UI Workflow
Upload → column mapping → validation panel → pairing board → confirm → export XLSX. Endpoints under `/reconcile/*`.

**Location Assignment Board:** `/clients/{id}/locations` — same pairing board pattern for policies → physical locations.

---

# Pairing Board Pattern

Reusable UI for matching records from two sources: left (source) | center (score badge) | right (target) | actions (confirm/break/create). Drag-to-pair supported. OOB counter pattern: every action returns row HTML + `hx-swap-oob` counter update.

## Colors
- Green (high >=75)
- Amber (medium 45–74)
- Red (unmatched source)
- Purple (extra target/draggable)

## Reference Implementation
`reconcile/_pairing_board.html`. See `reconciler.py` for the `_score_pair()` pattern.
