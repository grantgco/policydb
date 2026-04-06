# Simmer: Contacts Listing & Capture — Final Trajectory

| Iter | Capture | Density | Nav | Maint | Composite | Key Change |
|------|---------|---------|-----|-------|-----------|------------|
| 0    | 5.3     | 6.7     | 6.0 | 3.0   | 5.2       | seed       |
| 1    | 5.6     | 6.8     | 6.3 | 4.8   | 5.9       | consolidated cell-save/rename, form fix, tab persistence, JS dedup |
| 2    | 5.6     | 6.8     | 6.3 | 5.4   | 6.1       | unified matrix row template (3→1), consolidated delete (3→1) |
| 3    | 5.6     | 6.8     | 6.3 | 6.0   | 6.2       | row-restore + tbody use unified template, 6 legacy templates orphaned |

**Best: Iteration 3 (6.2/10)**

## Cumulative Impact

| Metric | Seed | Final | Change |
|--------|------|-------|--------|
| Routes file | 1931 lines | 1753 lines | -178 (-9%) |
| Route count | 36 endpoints | 28 endpoints | -8 (-22%) |
| Row templates | 6 files (652 lines) | 1 file (172 lines) | -480 (-74%) |
| JS utils | 3x flashCell, 2x escapeHtml | 1x each (global) | deduped |
| Bugs fixed | 2 (form target mismatch, __escapeHtml typo) | — | — |

## Remaining Opportunities (from judge board)

1. **Delete 6 orphaned legacy templates** — they're unreferenced but still on disk
2. **Refactor contacts_list()** — 252-line monolith with 4x filter logic; split into lazy-loaded tabs
3. **Consolidate add-row + create endpoints** — 6 endpoints with near-identical logic
4. **Improve capture with store picker** — New Contact form only creates placement; Quick Add creates stubs
5. **Unify JS controllers** — unified "All People" and matrix controllers are ~90% identical (~100 lines each)
