"""Data Health scoring engine — completeness + freshness per client/policy."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

import policydb.config as cfg


# ── Lifecycle stage detection ────────────────────────────────────────────────


def detect_stage(policy: dict) -> str:
    """Determine lifecycle stage for a policy record.

    Returns one of: opportunity, renewal_window, bound_complete, active.
    """
    if policy.get("is_opportunity"):
        return "opportunity"

    exp_str = policy.get("expiration_date") or ""
    status = (policy.get("renewal_status") or "").lower()

    if status == "bound" and exp_str:
        return "bound_complete"

    if exp_str:
        try:
            exp_date = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return "active"
        upcoming_days = cfg.get("renewal_windows", {}).get("upcoming", 180)
        if (exp_date - date.today()).days <= upcoming_days:
            return "renewal_window"

    return "active"


def detect_client_stage(conn: sqlite3.Connection, client_id: int) -> str:
    """Determine lifecycle stage for a client based on its policies.

    Uses the most urgent policy stage.
    """
    rows = conn.execute(
        "SELECT is_opportunity, expiration_date, renewal_status "
        "FROM policies WHERE client_id = ? AND archived = 0",
        (client_id,),
    ).fetchall()
    if not rows:
        return "active"
    stages = [detect_stage(dict(r)) for r in rows]
    priority = ["renewal_window", "bound_complete", "active", "opportunity"]
    for s in priority:
        if s in stages:
            return s
    return "active"


# ── Field-level staleness ────────────────────────────────────────────────────


def get_field_last_changed(
    conn: sqlite3.Connection,
    table_name: str,
    record_id: int | str,
    fields: list[str],
) -> dict[str, date | None]:
    """Query audit_log for the last time each field was changed.

    Returns {field_name: date_or_None}.
    """
    result: dict[str, date | None] = {f: None for f in fields}
    row_id = str(record_id)

    for field in fields:
        row = conn.execute(
            "SELECT MAX(changed_at) AS last_changed FROM audit_log "
            "WHERE table_name = ? AND row_id = ? AND operation = 'UPDATE' "
            "AND json_extract(new_values, ?) IS NOT NULL",
            (table_name, row_id, f"$.{field}"),
        ).fetchone()
        if row and row["last_changed"]:
            try:
                result[field] = datetime.strptime(
                    row["last_changed"][:10], "%Y-%m-%d"
                ).date()
            except (ValueError, TypeError):
                pass

    return result


# ── Score computation ────────────────────────────────────────────────────────


def compute_health_score(
    record: dict,
    record_type: str,
    stage: str,
    field_config: dict | None = None,
    field_dates: dict[str, date | None] | None = None,
) -> dict:
    """Compute health score (0-100) for a single record.

    Returns {
        score: int,
        missing: list[dict],   # [{field, label, weight, impact}]
        stale: list[dict],     # [{field, label, days_since, decay_days}]
        filled: int,
        total: int,
    }
    """
    if field_config is None:
        field_config = cfg.get("data_health_fields", {})

    fields = field_config.get(record_type, [])
    applicable = [f for f in fields if stage in f.get("stages", [])]

    # Skip policy_number from scoring when the user has flagged it as unknown
    if record_type == "policy" and record.get("policy_number_unknown"):
        applicable = [f for f in applicable if f["field"] != "policy_number"]

    if not applicable:
        return {"score": 100, "missing": [], "stale": [], "filled": 0, "total": 0}

    today = date.today()
    total_weight = sum(f["weight"] for f in applicable)
    earned = 0
    missing = []
    stale = []
    filled_count = 0

    for f in applicable:
        value = record.get(f["field"])
        is_filled = value is not None and str(value).strip() != ""

        if not is_filled:
            impact = round((f["weight"] / total_weight) * 100)
            missing.append({
                "field": f["field"],
                "label": f["label"],
                "weight": f["weight"],
                "impact": impact,
            })
            continue

        filled_count += 1
        field_score = f["weight"]

        # Check staleness
        decay_days = f.get("decay_days")
        if decay_days and field_dates:
            last_changed = field_dates.get(f["field"])
            if last_changed:
                days_since = (today - last_changed).days
                if days_since > decay_days:
                    field_score *= 0.5
                    stale.append({
                        "field": f["field"],
                        "label": f["label"],
                        "days_since": days_since,
                        "decay_days": decay_days,
                    })

        earned += field_score

    score = round((earned / total_weight) * 100) if total_weight else 100

    return {
        "score": score,
        "missing": sorted(missing, key=lambda x: -x["impact"]),
        "stale": sorted(stale, key=lambda x: -x["days_since"]),
        "filled": filled_count,
        "total": len(applicable),
    }


# ── Contact completeness check ───────────────────────────────────────────────


def _has_primary_contact(conn: sqlite3.Connection, client_id: int) -> bool:
    """Check if client has at least one contact with an email."""
    row = conn.execute(
        "SELECT COUNT(*) FROM contacts c "
        "JOIN contact_client_assignments ca ON ca.contact_id = c.id "
        "WHERE ca.client_id = ? "
        "AND c.email IS NOT NULL AND c.email != ''",
        (client_id,),
    ).fetchone()
    return (row[0] or 0) > 0


# ── Batch scoring ────────────────────────────────────────────────────────────


def score_policies(
    conn: sqlite3.Connection,
    policies: list[dict],
    include_staleness: bool = False,
) -> list[dict]:
    """Batch-compute health scores for a list of policy dicts.

    Mutates each dict to add: health_score, health_missing, health_stale,
    health_filled, health_total, health_stage.
    """
    field_config = cfg.get("data_health_fields", {})
    stale_fields = [
        f["field"] for f in field_config.get("policy", []) if f.get("decay_days")
    ]

    for p in policies:
        stage = detect_stage(p)
        field_dates = None
        if include_staleness and stale_fields:
            uid = p.get("policy_uid") or p.get("id")
            if uid:
                field_dates = get_field_last_changed(conn, "policies", uid, stale_fields)

        result = compute_health_score(p, "policy", stage, field_config, field_dates)
        p["health_score"] = result["score"]
        p["health_missing"] = result["missing"]
        p["health_stale"] = result["stale"]
        p["health_filled"] = result["filled"]
        p["health_total"] = result["total"]
        p["health_stage"] = stage

    return policies


def score_client(
    conn: sqlite3.Connection,
    client: dict,
    include_staleness: bool = False,
) -> dict:
    """Compute health score for a client (client fields + aggregate policy scores).

    Mutates the dict to add health_* keys.
    """
    field_config = cfg.get("data_health_fields", {})
    stage = detect_client_stage(conn, client["id"])

    # Client-level field score
    client_result = compute_health_score(client, "client", stage, field_config)

    # Contact check
    has_contact = _has_primary_contact(conn, client["id"])
    if not has_contact and stage != "opportunity":
        client_result["missing"].append({
            "field": "_primary_contact",
            "label": "Primary Contact with Email",
            "weight": 2,
            "impact": 10,
        })
        if client_result["total"] > 0:
            adj = 2 / (client_result["total"] * (2 / max(len(field_config.get("client", [])), 1) + 1))
            client_result["score"] = max(0, round(client_result["score"] * (1 - adj * 0.3)))

    # Policy-level aggregate
    policy_rows = conn.execute(
        "SELECT * FROM policies WHERE client_id = ? AND archived = 0",
        (client["id"],),
    ).fetchall()
    policy_dicts = [dict(r) for r in policy_rows]

    if policy_dicts:
        score_policies(conn, policy_dicts, include_staleness=include_staleness)
        policy_scores = [p["health_score"] for p in policy_dicts]
        avg_policy = round(sum(policy_scores) / len(policy_scores))
    else:
        avg_policy = 100

    combined = round(client_result["score"] * 0.4 + avg_policy * 0.6)

    client["health_score"] = combined
    client["health_client_score"] = client_result["score"]
    client["health_policy_score"] = avg_policy
    client["health_missing"] = client_result["missing"]
    client["health_stale"] = client_result["stale"]
    client["health_stage"] = stage
    client["health_policy_count"] = len(policy_dicts)

    return client


def get_book_health_summary(conn: sqlite3.Connection) -> dict:
    """Compute book-wide health summary stats."""
    threshold = cfg.get("data_health_threshold", 85)
    clients = conn.execute(
        "SELECT * FROM clients WHERE archived = 0"
    ).fetchall()
    client_dicts = [dict(r) for r in clients]

    scores = []
    incomplete = 0
    critical = 0

    for c in client_dicts:
        score_client(conn, c, include_staleness=False)
        scores.append(c["health_score"])
        if c["health_score"] < threshold:
            incomplete += 1
        if c["health_score"] < 60:
            critical += 1

    avg = round(sum(scores) / len(scores)) if scores else 100

    return {
        "avg_score": avg,
        "total_clients": len(client_dicts),
        "incomplete_count": incomplete,
        "stale_count": 0,  # Simplified — full stale count is expensive for summary
        "critical_count": critical,
    }


def get_missing_fields_report(conn: sqlite3.Connection) -> list[dict]:
    """Get all missing fields across the book, sorted by impact."""
    items = []
    field_config = cfg.get("data_health_fields", {})

    rows = conn.execute(
        "SELECT p.*, c.name AS client_name FROM policies p "
        "JOIN clients c ON c.id = p.client_id "
        "WHERE p.archived = 0 AND c.archived = 0"
    ).fetchall()

    for r in rows:
        p = dict(r)
        stage = detect_stage(p)
        result = compute_health_score(p, "policy", stage, field_config)
        for m in result["missing"]:
            items.append({
                "client_name": p["client_name"],
                "client_id": p["client_id"],
                "policy_uid": p["policy_uid"],
                "policy_type": p.get("policy_type", ""),
                "field": m["field"],
                "label": m["label"],
                "impact": m["impact"],
                "stage": stage,
            })

    # Client-level fields
    clients = conn.execute(
        "SELECT * FROM clients WHERE archived = 0"
    ).fetchall()
    for r in clients:
        c = dict(r)
        stage = detect_client_stage(conn, c["id"])
        result = compute_health_score(c, "client", stage, field_config)
        for m in result["missing"]:
            items.append({
                "client_name": c["name"],
                "client_id": c["id"],
                "policy_uid": None,
                "policy_type": None,
                "field": m["field"],
                "label": m["label"],
                "impact": m["impact"],
                "stage": stage,
            })

    return sorted(items, key=lambda x: -x["impact"])
