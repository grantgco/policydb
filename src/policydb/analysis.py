"""Coverage gap analysis, tower detection, and program audit logic."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from policydb import config as cfg


def run_coverage_gap_analysis(
    conn: sqlite3.Connection, client_id: int
) -> list[str]:
    """Return list of coverage gap observation strings."""
    rows = conn.execute(
        "SELECT policy_type FROM policies WHERE client_id = ? AND archived = 0",
        (client_id,),
    ).fetchall()
    present_types = {r["policy_type"] for r in rows}

    client_row = conn.execute(
        "SELECT industry_segment FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    industry = client_row["industry_segment"] if client_row else ""

    rules = cfg.get("coverage_gap_rules", [])
    observations: list[str] = []

    for rule in rules:
        if "if_present" in rule:
            if rule["if_present"] in present_types and rule["should_have"] not in present_types:
                observations.append(rule["message"])
        elif "if_industry" in rule:
            if rule["if_industry"].lower() == industry.lower() and rule["should_have"] not in present_types:
                observations.append(rule["message"])

    return observations


def detect_towers(policies: list[dict]) -> dict[str, list[dict]]:
    """Group policies by program. Returns {program_name: [policy, ...]}."""
    towers: dict[str, list[dict]] = defaultdict(list)
    for p in policies:
        group = p.get("program_name") or p.get("program_id")
        if group:
            towers[str(group)].append(p)
    return dict(towers)


def detect_standalones(policies: list[dict]) -> list[dict]:
    """Return policies not assigned to any program."""
    return [p for p in policies if not p.get("program_id")]


def find_duplicate_policies(policies: list[dict]) -> list[tuple[dict, dict]]:
    """Return pairs of policies that may be duplicates (same policy_number or same type+carrier)."""
    duplicates = []
    seen_numbers: dict[str, dict] = {}
    seen_type_carrier: dict[tuple, dict] = {}

    for p in policies:
        # Exact policy number match
        pnum = p.get("policy_number")
        if pnum:
            if pnum in seen_numbers:
                duplicates.append((seen_numbers[pnum], p))
            else:
                seen_numbers[pnum] = p

        # Same type + carrier on same client (potential overlap)
        key = (p.get("client_id"), p.get("policy_type"), p.get("carrier"))
        if all(key):
            if key in seen_type_carrier:
                existing = seen_type_carrier[key]
                # Only flag if not clearly layered in a program
                if not (p.get("program_id") or existing.get("program_id")):
                    duplicates.append((existing, p))
            else:
                seen_type_carrier[key] = p

    return duplicates


def layer_notation(limit_amount, attachment_point=None, participation_of=None) -> str:
    """Return standard insurance tower notation string.

    Examples:
        layer_notation(5_000_000)                          → "$5M Primary"
        layer_notation(5_000_000, 0)                       → "$5M X Primary"
        layer_notation(5_000_000, 5_000_000)               → "$5M X $5M"
        layer_notation(12_500_000, 25_000_000, 25_000_000) → "$12.5M PO $25M X $25M"
    """
    if not limit_amount:
        return ""

    def _fmt(v: float) -> str:
        m = v / 1_000_000
        return f"${m:g}M"

    lim = _fmt(limit_amount)

    if attachment_point is None:
        # This IS the primary layer
        if participation_of:
            return f"{lim} PO {_fmt(participation_of)} Primary"
        return f"{lim} Primary"
    elif attachment_point == 0:
        # Excess of primary (attaches right above primary layer)
        if participation_of:
            return f"{lim} PO {_fmt(participation_of)} X Primary"
        return f"{lim} X Primary"
    else:
        att = _fmt(attachment_point)
        if participation_of:
            return f"{lim} PO {_fmt(participation_of)} X {att}"
        return f"{lim} X {att}"


def cluster_expirations(policies: list[dict]) -> dict[str, list[dict]]:
    """Group policies by expiration month for date clustering analysis."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for p in policies:
        exp = p.get("expiration_date", "")
        if exp:
            month = exp[:7]  # YYYY-MM
            clusters[month].append(p)
    return dict(clusters)


def build_program_audit(
    conn: sqlite3.Connection, client_id: int
) -> dict[str, Any]:
    """Run all audit checks and return structured results."""
    rows = conn.execute(
        "SELECT * FROM v_policy_status WHERE client_id = ?", (client_id,)
    ).fetchall()
    policies = [dict(r) for r in rows]

    gap_observations = run_coverage_gap_analysis(conn, client_id)
    towers = detect_towers(policies)
    standalones = detect_standalones(policies)
    duplicates = find_duplicate_policies(policies)
    expiration_clusters = cluster_expirations(policies)

    # Scale summary
    carrier_set = {p["carrier"] for p in policies}
    type_set = {p["policy_type"] for p in policies}

    # Near-term expirations (within 180 days)
    near_term = [p for p in policies if p.get("days_to_renewal") is not None and 0 <= p["days_to_renewal"] <= 180]

    return {
        "policy_count": len(policies),
        "coverage_lines": sorted(type_set),
        "carrier_count": len(carrier_set),
        "carriers": sorted(carrier_set),
        "tower_count": len(towers),
        "towers": towers,
        "standalone_count": len(standalones),
        "standalones": standalones,
        "gap_observations": gap_observations,
        "duplicate_count": len(duplicates),
        "duplicates": duplicates,
        "expiration_clusters": expiration_clusters,
        "near_term_renewals": near_term,
        "near_term_count": len(near_term),
    }
