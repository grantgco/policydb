"""Universal ghost row convention for PolicyDB.

A ghost row is a read-only reference to a real record that appears in a view
where it logically belongs but doesn't canonically live.  Ghost rows are a
display-time concept — they are injected at the Python query level and never
stored in the database.

Ghost row sources (convention-based):
  - Package sub-coverage → schedule coverage section  (badge: "Package")
  - Program child policy → program summary section   (badge: "Program")
  - Future: linked account policy                    (badge: "Linked")

Rendering rules (universal):
  1. Muted styling — lighter text, italic
  2. Colored badge — "Package" (indigo), "Program" (blue), "Linked" (purple)
  3. Premium shows "—" when NULL to prevent double-counting
  4. Click navigates to the canonical record
  5. Non-editable in-place
  6. Sorted after standalone records within the same section
"""

from __future__ import annotations

import sqlite3
from typing import Any

from policydb.queries import get_sub_coverages_full_by_policy_id


def resolve_ghost_fields(
    sub_cov: dict[str, Any],
    parent_policy: dict[str, Any],
    *,
    ghost_reason: str = "sub_coverage",
    ghost_badge: str = "Package",
) -> dict[str, Any]:
    """Build a ghost row dict by merging sub-coverage overrides with parent.

    Sub-coverage field wins if populated (non-None / non-empty);
    otherwise fall back to parent policy value.
    """
    return {
        # Identity
        "coverage_type": sub_cov.get("coverage_type") or "",
        "line": sub_cov.get("coverage_type") or "",
        # Financial — sub-cov overrides win
        "limit": sub_cov.get("limit_amount"),
        "limit_amount": sub_cov.get("limit_amount"),
        "deductible": sub_cov.get("deductible"),
        "premium": sub_cov.get("premium"),  # None = show "—" (no double-counting)
        "form": sub_cov.get("coverage_form") or parent_policy.get("coverage_form") or "",
        "coverage_form": sub_cov.get("coverage_form") or parent_policy.get("coverage_form") or "",
        # Override fields — fall back to parent
        "carrier": sub_cov.get("carrier") or parent_policy.get("carrier") or "",
        "policy_number": sub_cov.get("policy_number") or parent_policy.get("policy_number") or "",
        # Tower fields
        "attachment_point": sub_cov.get("attachment_point"),
        "participation_of": sub_cov.get("participation_of"),
        "layer_position": sub_cov.get("layer_position") or "Primary",
        "description": sub_cov.get("description") or "",
        # Always inherited from parent
        "effective": parent_policy.get("effective_date") or parent_policy.get("effective") or "",
        "expiration": parent_policy.get("expiration_date") or parent_policy.get("expiration") or "",
        "effective_date": parent_policy.get("effective_date") or parent_policy.get("effective") or "",
        "expiration_date": parent_policy.get("expiration_date") or parent_policy.get("expiration") or "",
        "policy_uid": parent_policy.get("policy_uid") or "",
        "client_id": parent_policy.get("client_id"),
        # Ghost metadata
        "is_ghost": True,
        "is_package": False,
        "ghost_reason": ghost_reason,
        "ghost_source_id": sub_cov.get("id"),
        "ghost_parent_uid": parent_policy.get("policy_uid") or "",
        "ghost_parent_type": parent_policy.get("policy_type") or parent_policy.get("line") or "",
        "ghost_badge": ghost_badge,
        "package_parent_type": parent_policy.get("policy_type") or parent_policy.get("line") or "",
    }


_ACTIVE_POLICY = (
    "p.archived = 0 "
    "AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)"
)


def inject_schedule_ghost_rows(
    rows: list[dict[str, Any]],
    conn: sqlite3.Connection,
    client_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inject enriched ghost rows into schedule row list.

    Replaces the inline ghost injection in ``charts.py:get_schedule_data()``.
    Uses ``get_sub_coverages_full_by_policy_id`` so ghost rows carry real
    financial data (limit, deductible, premium, carrier overrides) instead
    of showing all dashes.

    Returns:
        (enriched_rows, package_policies) — the full row list with ghosts
        injected after their parent, plus a list of package policy summaries
        for the "Package Policies" header section.
    """
    # Fetch policy id/type/number for sub-coverage lookup
    policy_rows = conn.execute(
        f"SELECT id, policy_type, policy_number, carrier, "  # noqa: S608
        f"effective_date, expiration_date, policy_uid, coverage_form, client_id "
        f"FROM policies p "
        f"WHERE p.client_id = ? AND {_ACTIVE_POLICY}",
        (client_id,),
    ).fetchall()
    policy_ids = [r["id"] for r in policy_rows]

    # Build lookup: policy_number -> policy row
    pnum_to_policy: dict[str, dict] = {}
    for pr in policy_rows:
        if pr["policy_number"]:
            pnum_to_policy[pr["policy_number"]] = dict(pr)

    # Batch-fetch full sub-coverages (with financial data)
    sub_cov_full_map = get_sub_coverages_full_by_policy_id(conn, policy_ids)
    package_policy_ids = set(sub_cov_full_map.keys())

    # Build package_policies summary list
    package_policies: list[dict] = []
    for pr in policy_rows:
        if pr["id"] in package_policy_ids:
            package_policies.append({
                "policy_type": pr["policy_type"],
                "carrier": pr["carrier"],
                "policy_number": pr["policy_number"],
                "sub_coverages": [
                    sc["coverage_type"] for sc in sub_cov_full_map[pr["id"]]
                ],
            })

    # Inject ghost rows after their parent
    enriched: list[dict] = []
    for row in rows:
        pnum = row.get("policy_number")
        matched_policy = pnum_to_policy.get(pnum) if pnum else None
        is_package = bool(matched_policy and matched_policy["id"] in package_policy_ids)

        # Tag real rows
        row["is_package"] = is_package
        if "is_ghost" not in row:
            row["is_ghost"] = False
        enriched.append(row)

        # Inject ghost rows for each sub-coverage
        if is_package:
            for sc in sub_cov_full_map[matched_policy["id"]]:
                ghost = resolve_ghost_fields(sc, matched_policy)
                enriched.append(ghost)

    return enriched, package_policies
