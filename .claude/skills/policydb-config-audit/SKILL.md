---
name: policydb-config-audit
description: >
  Config system audit checklist for PolicyDB — _DEFAULTS ↔ EDITABLE_LISTS cross-check,
  deep merge limitations, and known gaps. Use when adding new config keys, modifying
  settings.py, or auditing config coverage.
---

# Config System Audit

## Architecture

- **Defaults:** `src/policydb/config.py` → `_DEFAULTS` dict
- **User overrides:** `~/.policydb/config.yaml` (merged at load time)
- **Settings UI:** `src/policydb/web/routes/settings.py` → `EDITABLE_LISTS` dict + `TAB_LISTS`
- **API:** `cfg.get(key, default)`, `cfg.add_list_item()`, `cfg.remove_list_item()`, `cfg.save_config()`

## The Rule

**Every user-facing categorized list MUST exist in BOTH places:**
1. `_DEFAULTS` in `config.py` (provides default values)
2. `EDITABLE_LISTS` in `settings.py` (makes it editable via Settings UI)

And must be assigned to a tab in `TAB_LISTS`.

## Cross-Check Checklist

When adding a new config list:

- [ ] Add to `_DEFAULTS` in `config.py` with sensible defaults
- [ ] Add to `EDITABLE_LISTS` in `settings.py` with human-readable label
- [ ] Assign to appropriate `TAB_LISTS` tab
- [ ] Read via `cfg.get("key_name")` at runtime — never hardcode
- [ ] If the list feeds a dropdown/combobox, pass it to the template context

## Deep Merge Limitation (WARNING)

`load_config()` only deep-merges the `renewal_windows` key as a special case. **All other nested dict keys are replaced wholesale** if the user's `config.yaml` includes them.

This means if a user customizes one field in `anomaly_thresholds`:
```yaml
# config.yaml
anomaly_thresholds:
  stale_days: 90  # user only wanted to change this
```

They lose ALL other default thresholds (premium_swing_pct, missing_field_count, etc.).

**Affected nested dict keys:**
- `anomaly_thresholds`
- `readiness_weights`, `readiness_thresholds`, `readiness_status_scores`
- `escalation_thresholds`
- `followup_workload_thresholds`
- `timeline_engine`
- `field_trust_defaults`
- `data_health_fields`
- `standard_exposure_types`

**Fix (when implemented):** Generalize the deep merge in `load_config()`:
```python
for key, val in user.items():
    if isinstance(val, dict) and isinstance(result.get(key), dict):
        result[key] = {**result[key], **val}
    else:
        result[key] = val
```

## Known Gaps (from 2026-04-02 audit)

### Missing from EDITABLE_LISTS

| Config Key | In `_DEFAULTS` | In `EDITABLE_LISTS` | Action |
|-----------|:-:|:-:|--------|
| `risk_review_prompt_categories` | Yes | **No** | Add to EDITABLE_LISTS + assign to property-risk tab |
| `relationship_risk_levels` | Yes | **No** | Add to EDITABLE_LISTS |
| `import_source_names` | Yes | **No** | Add to EDITABLE_LISTS |
| `insurance_reminder_tiers` | Yes | **No** | Add to EDITABLE_LISTS (or document as internal-only) |

### Intentionally NOT in EDITABLE_LISTS

These are complex structured configs (list-of-dicts) with dedicated editors:

- `follow_up_dispositions` — has its own CRUD in settings
- `issue_severities` — has structured editor in Issues tab
- `quick_log_templates` — managed via dedicated section
- `mandated_activities` — managed via timeline settings
- `milestone_profiles`, `milestone_profile_rules` — managed via timeline settings
- `carrier_aliases` — has its own management section
- `data_health_fields` — internal scoring config
- `coverage_gap_rules`, `auto_sub_coverages` — internal automation config

### Hardcoded Lists Found in Code

| File | Line | List | Should Be |
|------|------|------|-----------|
| `queries.py` | ~1563 | Disposition labels | `cfg.get("follow_up_dispositions")` filtered by accountability |
| `cli.py` | ~387, ~596 | Renewal statuses | `cfg.get("renewal_statuses")` |
| `onboard.py` | ~182 | Renewal statuses | `cfg.get("renewal_statuses")` |

## Config Error Handling

`load_config()` currently swallows YAML parse errors silently (`except Exception: pass`). If a user's config.yaml has a syntax error, the app starts with defaults only and gives no warning. A logger.warning should be added.
