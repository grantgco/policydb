"""Data assembly functions for the Chart Deck Builder.

Each function takes a sqlite3 connection (with row_factory) and a client_id,
returning plain dicts/lists that are directly JSON-serializable.  Formatting
(currency symbols, abbreviations) happens client-side -- raw numbers here.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

from policydb.ghost_rows import inject_schedule_ghost_rows
from policydb.queries import get_client_exposures, get_exposure_observations, get_sub_coverages_by_policy_id, get_sub_coverages_full_by_policy_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NON_OPP_FILTER = "(p.is_opportunity = 0 OR p.is_opportunity IS NULL)"
_NON_ARCHIVED_FILTER = "(p.archived = 0 OR p.archived IS NULL)"
_ACTIVE_POLICY = f"{_NON_OPP_FILTER} AND {_NON_ARCHIVED_FILTER}"

# v_schedule / v_tower use client_name, not client_id
_CLIENT_NAME_SUB = "(SELECT name FROM clients WHERE id = ?)"


def _rows_to_dicts(rows) -> list[dict]:
    """Convert sqlite3.Row results to plain dicts."""
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 1. Premium Comparison  (grouped bar: prior vs current by policy_type)
# ---------------------------------------------------------------------------

def get_premium_comparison_data(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Grouped bar chart data: prior vs current premium by policy_type.

    Returns: [{"policy_type": "GL", "prior_premium": 50000, "premium": 55000}, ...]
    """
    rows = conn.execute(
        f"""
        SELECT
            p.policy_type,
            SUM(COALESCE(p.prior_premium, 0)) AS prior_premium,
            SUM(COALESCE(p.premium, 0))       AS premium
        FROM policies p
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
        GROUP BY p.policy_type
        ORDER BY SUM(p.premium) DESC
        """,
        (client_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# 2. Schedule of Insurance
# ---------------------------------------------------------------------------

def get_schedule_data(conn: sqlite3.Connection, client_id: int) -> dict:
    """Schedule of Insurance table data with ghost rows for package sub-coverages.

    Returns: {"rows": [...], "total_premium": float, "policy_count": int,
              "package_policies": [...]}
    Uses v_schedule view filtered by client_name subquery.
    Ghost rows are injected via the universal ghost_rows utility at the Python
    level — v_schedule and exporters are NOT affected.
    """
    rows = conn.execute(
        f"""
        SELECT *
        FROM v_schedule
        WHERE client_name = {_CLIENT_NAME_SUB}
        """,
        (client_id,),
    ).fetchall()

    # Normalize v_schedule column names to lowercase/underscored keys for templates
    row_dicts = []
    for r in _rows_to_dicts(rows):
        row_dicts.append({
            "line": r.get("Line of Business"),
            "carrier": r.get("Carrier"),
            "policy_number": r.get("Policy Number"),
            "effective": r.get("Effective"),
            "expiration": r.get("Expiration"),
            "limit": r.get("Limit"),
            "deductible": r.get("Deductible"),
            "premium": r.get("Premium"),
            "form": r.get("Form"),
            "is_package": False,
            "is_ghost": False,
        })

    # Inject enriched ghost rows via universal ghost row utility
    enriched_rows, package_policies = inject_schedule_ghost_rows(
        row_dicts, conn, client_id
    )

    # Total premium excludes ghost rows (avoid double-counting)
    total_premium = sum(r.get("premium") or 0 for r in enriched_rows if not r.get("is_ghost"))
    real_row_count = sum(1 for r in enriched_rows if not r.get("is_ghost"))
    return {
        "rows": enriched_rows,
        "total_premium": total_premium,
        "policy_count": real_row_count,
        "package_policies": package_policies,
    }


# ---------------------------------------------------------------------------
# 3. Tower / Layer Diagram
# ---------------------------------------------------------------------------

def _layer_notation(limit, attachment_point, participation_of):
    """Generate insurance tower notation: $5M x $10M, $10M po $30M x $70M."""
    def _fmt(val):
        if val is None or val == 0:
            return "$0"
        v = abs(val)
        if v >= 1_000_000:
            m = v / 1_000_000
            s = f"${m:.1f}M" if m != int(m) else f"${int(m)}M"
        elif v >= 1_000:
            k = v / 1_000
            s = f"${k:.1f}K" if k != int(k) else f"${int(k)}K"
        else:
            s = f"${v:,.0f}"
        return s

    lim_str = _fmt(limit)
    att = attachment_point or 0

    if participation_of and participation_of > 0:
        return f"{lim_str} po {_fmt(participation_of)} x {_fmt(att)}"
    if att > 0:
        return f"{lim_str} x {_fmt(att)}"
    if att == 0 and limit:
        return f"{lim_str} x Primary"
    return lim_str


def get_tower_data(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Tower/layer diagram data for Marsh-style program schematic.

    Returns grouped structure with underlying lines and excess layers separated:
    [
        {
            "program_name": "Casualty",
            "underlying": [
                {"label": "General Liability", "carrier": "Travelers",
                 "deductible": 2000000, "limit": 2000000, "premium": 35000, "column": 1},
                ...
            ],
            "layers": [
                {"policy_type": "Umbrella", "carrier": "Berkshire Hathaway",
                 "limit": 10000000, "attachment_point": 0,
                 "notation": "$10M x Primary", "premium": 50000,
                 "participants": [], "form_type": null},
                ...
            ]
        },
        ...
    ]
    """
    rows = conn.execute(
        f"""
        SELECT *
        FROM v_tower
        WHERE client_name = {_CLIENT_NAME_SUB}
        """,
        (client_id,),
    ).fetchall()

    # Build program participants lookup from child policies
    program_rows_db = conn.execute(
        """
        SELECT pg.id AS program_id, pg.name AS program_name
        FROM programs pg
        WHERE pg.client_id = ? AND pg.archived = 0
        """,
        (client_id,),
    ).fetchall()

    participants_by_program: dict[int, list[dict]] = {}
    for pgm in program_rows_db:
        children = conn.execute(
            """
            SELECT DISTINCT p.carrier, p.premium, p.limit_amount, p.policy_number
            FROM policies p
            WHERE p.program_id = ? AND p.archived = 0
            ORDER BY p.carrier
            """,
            (pgm["program_id"],),
        ).fetchall()
        if children:
            participants_by_program[pgm["program_id"]] = [
                {
                    "carrier": r["carrier"] or "",
                    "premium": r["premium"] or 0,
                    "limit": r["limit_amount"] or 0,
                    "policy_number": r["policy_number"] or "",
                }
                for r in children
            ]

    # Build key -> program_id mapping from policies that have program_id
    program_key_to_id: dict[tuple, int] = {}
    prog_policy_rows = conn.execute(
        """
        SELECT p.program_id, p.tower_group, p.attachment_point, p.layer_position
        FROM policies p
        WHERE p.client_id = ? AND p.program_id IS NOT NULL
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
        """,
        (client_id,),
    ).fetchall()
    for r in prog_policy_rows:
        key = (r["tower_group"], r["attachment_point"], r["layer_position"])
        if r["program_id"] and key not in program_key_to_id:
            program_key_to_id[key] = r["program_id"]

    # Build sub-coverage lookup (full dicts with limit_amount, deductible)
    tower_policy_rows = conn.execute(
        """
        SELECT p.id, COALESCE(pg.name, p.tower_group) AS program_name,
               p.tower_group, p.policy_type, p.carrier, p.policy_number
        FROM policies p
        LEFT JOIN programs pg ON pg.id = p.program_id
        WHERE p.client_id = ?
          AND (p.tower_group IS NOT NULL OR p.program_id IS NOT NULL)
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
        """,
        (client_id,),
    ).fetchall()
    tower_policy_ids = [r["id"] for r in tower_policy_rows]
    sub_cov_full_map = get_sub_coverages_full_by_policy_id(conn, tower_policy_ids)
    # Map (program_name, policy_type, carrier, policy_number) -> sub-coverage info
    package_lookup: dict[tuple, dict] = {}
    for r in tower_policy_rows:
        subs = sub_cov_full_map.get(r["id"], [])
        if subs:
            key = (r["program_name"] or r["tower_group"], r["policy_type"], r["carrier"], r["policy_number"])
            package_lookup[key] = {
                "is_package": True,
                "package_parent_type": r["policy_type"] or "",
                "sub_coverages": subs,
            }

    # Build program_tower_coverage lookup: excess_policy_id -> [covered_labels]
    coverage_map: dict[int, list[str]] = {}
    try:
        ptc_rows = conn.execute(
            """
            SELECT ptc.excess_policy_id,
                   COALESCE(up.policy_type, sc.coverage_type) AS covered_label
            FROM program_tower_coverage ptc
            LEFT JOIN policies up ON ptc.underlying_policy_id = up.id
            LEFT JOIN policy_sub_coverages sc ON ptc.underlying_sub_coverage_id = sc.id
            WHERE ptc.excess_policy_id IN (
                SELECT id FROM policies WHERE client_id = ? AND archived = 0
            )
            """,
            (client_id,),
        ).fetchall()
        for r in ptc_rows:
            coverage_map.setdefault(r["excess_policy_id"], []).append(r["covered_label"])
    except Exception:
        pass  # Table may not exist on older DBs

    # Build policy_id lookup from tower rows for coverage_map matching
    tower_policy_id_lookup: dict[tuple, int] = {}
    for r in tower_policy_rows:
        key = (r["program_name"] or r["tower_group"], r["policy_type"], r["carrier"], r["policy_number"])
        tower_policy_id_lookup[key] = r["id"]

    # Group by program_name (falling back to tower_group), splitting into underlying (primary) and excess layers
    _WC_KEYWORDS = ("workers comp", "workers' comp")
    groups: dict[str, dict] = {}
    for r in rows:
        tg = r.get("program_name") or r["tower_group"] or "Ungrouped"
        if tg not in groups:
            groups[tg] = {"underlying": [], "layers": []}

        lp = (r["layer_position"] or "Primary").strip().lower()
        att = r["attachment_point"] or 0
        is_primary = lp == "primary" or (att == 0 and lp not in ("umbrella",))

        # Check if this policy is a package
        pkg_key = (tg, r["policy_type"], r["carrier"], r["policy_number"])
        pkg_info = package_lookup.get(pkg_key)
        policy_id = tower_policy_id_lookup.get(pkg_key)

        if is_primary:
            pt_lower = (r["policy_type"] or "").lower()
            is_statutory = any(kw in pt_lower for kw in _WC_KEYWORDS)
            entry = {
                "label": r["policy_type"] or "Unknown",
                "carrier": r["carrier"] or "",
                "deductible": r["deductible"] or 0,
                "limit": r["limit_amount"] or 0,
                "premium": r["premium"] or 0,
                "form_type": r["coverage_form"] or "",
                "column": r["schematic_column"],
                "is_statutory": is_statutory,
            }
            if pkg_info:
                entry["is_package"] = True
                entry["package_parent_type"] = pkg_info["package_parent_type"]
                entry["_sub_coverages"] = pkg_info["sub_coverages"]
            groups[tg]["underlying"].append(entry)
        else:
            is_umb = lp in ("umbrella", "umbrella liability") or "umbrella" in lp
            layer = {
                "policy_type": r["policy_type"] or "",
                "carrier": r["carrier"] or "",
                "limit": r["limit_amount"] or 0,
                "attachment_point": att,
                "participation_of": r["participation_of"],
                "notation": _layer_notation(
                    r["limit_amount"], r["attachment_point"], r["participation_of"]
                ),
                "layer_position": r["layer_position"] or "",
                "is_umbrella": is_umb,
                "premium": r["premium"] or 0,
                "form_type": r["coverage_form"] or "",
                "participants": [],
                "covered_types": coverage_map.get(policy_id, []) if policy_id else [],
            }
            if pkg_info:
                layer["is_package"] = True
                layer["package_parent_type"] = pkg_info["package_parent_type"]
            # Attach co-insured participants for program layers
            key = (r["tower_group"], r["attachment_point"], r["layer_position"])  # matches program_key_to_id built from policies
            pid = program_key_to_id.get(key)
            if pid and pid in participants_by_program:
                plist = participants_by_program[pid]
                for p in plist:
                    p["notation"] = _layer_notation(
                        p["limit"], r["attachment_point"], r["participation_of"]
                    )
                layer["participants"] = plist
            groups[tg]["layers"].append(layer)

    # --- Post-process: explode package sub-coverages into individual columns ---
    # Sub-coverage types that should be placed as excess/umbrella layers, not underlying columns
    _EXCESS_SUB_COV_KEYWORDS = ("umbrella", "excess")

    def _is_excess_sub_cov(cov_type: str) -> bool:
        ct = (cov_type or "").lower()
        return any(kw in ct for kw in _EXCESS_SUB_COV_KEYWORDS)

    for tg, grp in groups.items():
        expanded = []
        for entry in grp["underlying"]:
            subs = entry.pop("_sub_coverages", None)
            if subs:
                subs_with_limits = [s for s in subs if s.get("limit_amount")]
                # Separate excess-type sub-coverages from underlying-type
                excess_subs = [s for s in subs_with_limits if _is_excess_sub_cov(s.get("coverage_type", ""))]
                underlying_subs = [s for s in subs_with_limits if not _is_excess_sub_cov(s.get("coverage_type", ""))]

                # Promote excess-type sub-coverages to the layers list
                for sc in excess_subs:
                    sc_att = sc.get("attachment_point") or 0
                    grp["layers"].append({
                        "policy_type": sc["coverage_type"],
                        "carrier": entry["carrier"],
                        "limit": sc["limit_amount"],
                        "attachment_point": sc_att,
                        "participation_of": None,
                        "notation": _layer_notation(sc["limit_amount"], sc_att, None),
                        "layer_position": "Umbrella" if "umbrella" in (sc["coverage_type"] or "").lower() else "Excess",
                        "is_umbrella": "umbrella" in (sc["coverage_type"] or "").lower(),
                        "premium": 0,
                        "form_type": sc.get("coverage_form") or "",
                        "participants": [],
                        "covered_types": [],
                        "is_package": True,
                        "package_parent_type": entry["label"],
                    })

                if entry.get("is_statutory"):
                    # WC: keep statutory column, add EL as separate column
                    el_subs = [s for s in underlying_subs if "employer" in (s.get("coverage_type") or "").lower()]
                    expanded.append(entry)  # WC statutory column stays
                    for el in el_subs:
                        if el.get("limit_amount"):
                            expanded.append({
                                "label": el["coverage_type"],
                                "carrier": entry["carrier"],
                                "deductible": el.get("deductible") or 0,
                                "limit": el["limit_amount"],
                                "premium": 0,
                                "form_type": el.get("coverage_form") or "",
                                "column": None,
                                "is_package": True,
                                "package_parent_type": entry["label"],
                                "is_statutory": False,
                            })
                elif underlying_subs:
                    # Non-WC package (BOP, etc.): replace parent with sub-cov columns
                    for sc in underlying_subs:
                        expanded.append({
                            "label": sc["coverage_type"],
                            "carrier": entry["carrier"],
                            "deductible": sc.get("deductible") or entry.get("deductible", 0),
                            "limit": sc["limit_amount"],
                            "premium": 0,  # avoid double-counting
                            "form_type": sc.get("coverage_form") or "",
                            "column": None,
                            "is_package": True,
                            "package_parent_type": entry["label"],
                            "is_statutory": False,
                        })
                elif not excess_subs:
                    # Sub-coverages exist but none have limits — keep parent as-is
                    expanded.append(entry)
                else:
                    # Only excess subs had limits — keep parent for the underlying slot
                    expanded.append(entry)
            else:
                expanded.append(entry)
        grp["underlying"] = expanded

    # --- Smart rename: suppress "Ungrouped" for simple placements ---
    if len(groups) == 1 and "Ungrouped" in groups:
        groups[""] = groups.pop("Ungrouped")
    elif "Ungrouped" in groups and len(groups) > 1:
        groups["Other Lines"] = groups.pop("Ungrouped")

    # Sort and assemble result
    result = []
    for tg in sorted(groups.keys()):
        g = groups[tg]
        # Sort underlying by schematic_column (fallback: alphabetical)
        underlying = sorted(
            g["underlying"],
            key=lambda u: (u["column"] if u["column"] is not None else 999, u["label"]),
        )
        # Sort excess layers by attachment_point ascending (bottom-to-top)
        layers = sorted(g["layers"], key=lambda l: l["attachment_point"])
        result.append({
            "program_name": tg,
            "underlying": underlying,
            "layers": layers,
        })
    return result


# ---------------------------------------------------------------------------
# 4. Carrier Breakdown  (donut chart: premium by carrier)
# ---------------------------------------------------------------------------

def get_carrier_breakdown_data(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Donut chart data: premium by carrier.

    Returns: [{"carrier": "Hartford", "premium": 150000, "pct": 35.2}, ...]
    """
    rows = conn.execute(
        f"""
        SELECT
            p.carrier,
            SUM(COALESCE(p.premium, 0)) AS premium
        FROM policies p
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
        GROUP BY p.carrier
        ORDER BY SUM(p.premium) DESC
        """,
        (client_id,),
    ).fetchall()

    total = sum(r["premium"] or 0 for r in rows)
    result = []
    for r in rows:
        prem = r["premium"] or 0
        pct = round((prem / total) * 100, 1) if total > 0 else 0
        result.append({
            "carrier": r["carrier"],
            "premium": prem,
            "pct": pct,
        })
    return result


# ---------------------------------------------------------------------------
# 5. Rate Change  (horizontal bar: pct change by policy_type)
# ---------------------------------------------------------------------------

def get_rate_change_data(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Horizontal bar chart data: rate change by policy_type.

    Returns: [{"policy_type": "GL", "pct_change": 5.2, "premium": 55000,
               "prior_premium": 52300}, ...]
    Division guard: skip where prior_premium is 0 or NULL.
    """
    rows = conn.execute(
        f"""
        SELECT
            p.policy_type,
            SUM(COALESCE(p.premium, 0))       AS premium,
            SUM(COALESCE(p.prior_premium, 0))  AS prior_premium
        FROM policies p
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
        GROUP BY p.policy_type
        HAVING SUM(p.prior_premium) > 0
        ORDER BY p.policy_type
        """,
        (client_id,),
    ).fetchall()

    result = []
    for r in rows:
        prior = r["prior_premium"]
        current = r["premium"]
        if prior and prior > 0:
            pct = round(((current - prior) / prior) * 100, 1)
            result.append({
                "policy_type": r["policy_type"],
                "pct_change": pct,
                "premium": current,
                "prior_premium": prior,
            })
    # Sort by absolute change descending for visual impact
    result.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
    return result


# ---------------------------------------------------------------------------
# 6. Activity Timeline
# ---------------------------------------------------------------------------

def get_activity_timeline_data(
    conn: sqlite3.Connection,
    client_id: int,
    days_back: int = 180,
) -> list[dict]:
    """Vertical timeline data: activities for the client.

    Returns: [{"date": "2026-03-15", "activity_type": "Call",
               "subject": "...", "contacted": "...", "notes": "..."}, ...]
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    rows = conn.execute(
        """
        SELECT
            a.activity_date  AS date,
            a.activity_type,
            a.subject,
            a.contact_person AS contacted,
            a.details        AS notes
        FROM activity_log a
        WHERE a.client_id = ?
          AND a.activity_date >= ?
        ORDER BY a.activity_date DESC, a.created_at DESC
        """,
        (client_id, cutoff),
    ).fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# 7. Coverage Comparison  (current vs prior term per policy_type)
# ---------------------------------------------------------------------------

def get_coverage_comparison_data(conn: sqlite3.Connection, client_id: int) -> list[dict]:
    """Side-by-side comparison: current vs prior term per policy_type.

    Returns:
    [
        {
            "policy_type": "GL",
            "current": {"carrier": "Hartford", "premium": 55000,
                        "limit": 1000000, "deductible": 5000},
            "prior":   {"carrier": "Zurich", "premium": 50000,
                        "limit": 1000000, "deductible": 5000},
            "changes": ["carrier", "premium"]
        },
        ...
    ]

    Joins current policies with most recent premium_history row per
    policy_type.
    """
    # Current term aggregated by policy_type
    current_rows = conn.execute(
        f"""
        SELECT
            p.policy_type,
            p.carrier,
            COALESCE(p.premium, 0)       AS premium,
            COALESCE(p.prior_premium, 0) AS prior_premium,
            p.limit_amount,
            p.deductible,
            p.effective_date,
            p.expiration_date,
            p.coverage_form
        FROM policies p
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
        ORDER BY p.policy_type, p.premium DESC
        """,
        (client_id,),
    ).fetchall()

    # Most recent premium_history row per policy_type
    prior_rows = conn.execute(
        """
        SELECT ph.*
        FROM premium_history ph
        INNER JOIN (
            SELECT client_id, policy_type, MAX(term_effective) AS max_eff
            FROM premium_history
            WHERE client_id = ?
            GROUP BY client_id, policy_type
        ) latest
            ON ph.client_id = latest.client_id
            AND ph.policy_type = latest.policy_type
            AND ph.term_effective = latest.max_eff
        WHERE ph.client_id = ?
        """,
        (client_id, client_id),
    ).fetchall()

    prior_map: dict[str, dict] = {}
    for r in prior_rows:
        prior_map[r["policy_type"]] = {
            "carrier": r["carrier"],
            "premium": r["premium"] or 0,
            "limit": r["limit_amount"],
            "deductible": r["deductible"],
            "effective_date": r["term_effective"],
            "expiration_date": r["term_expiration"],
        }

    # Deduplicate current policies -- take the one with highest premium per type
    seen_types: dict[str, dict] = {}
    for r in current_rows:
        pt = r["policy_type"]
        if pt not in seen_types:
            seen_types[pt] = {
                "carrier": r["carrier"],
                "premium": r["premium"],
                "limit": r["limit_amount"],
                "deductible": r["deductible"],
                "effective_date": r["effective_date"],
                "expiration_date": r["expiration_date"],
                "coverage_form": r["coverage_form"],
            }

    result = []
    for pt, cur in sorted(seen_types.items()):
        prior = prior_map.get(pt)
        changes = []
        if prior:
            for field in ("carrier", "premium", "limit", "deductible"):
                if cur.get(field) != prior.get(field):
                    changes.append(field)
        result.append({
            "policy_type": pt,
            "current": cur,
            "prior": prior,
            "changes": changes,
        })
    return result


# ---------------------------------------------------------------------------
# 8. Premium History  (multi-line time series by policy_type)
# ---------------------------------------------------------------------------

def get_premium_history_data(
    conn: sqlite3.Connection,
    client_id: int,
    num_terms: int = 5,
) -> dict:
    """Multi-line time series: premium over terms by policy_type.

    Returns:
    {
        "series": [
            {"policy_type": "GL", "data": [{"term": "2022-01", "premium": 50000}, ...]},
            ...
        ],
        "terms": ["2022-01", "2023-01", ...]
    }
    """
    rows = conn.execute(
        """
        SELECT
            ph.policy_type,
            ph.term_effective,
            ph.premium
        FROM premium_history ph
        WHERE ph.client_id = ?
        ORDER BY ph.term_effective ASC
        """,
        (client_id,),
    ).fetchall()

    # Collect all unique terms and series
    all_terms: set[str] = set()
    series_map: dict[str, list[dict]] = {}
    for r in rows:
        term = r["term_effective"][:7] if r["term_effective"] else "unknown"
        all_terms.add(term)
        pt = r["policy_type"]
        series_map.setdefault(pt, []).append({
            "term": term,
            "premium": r["premium"] or 0,
        })

    # Sort terms chronologically and limit to most recent num_terms
    sorted_terms = sorted(all_terms)
    if len(sorted_terms) > num_terms:
        sorted_terms = sorted_terms[-num_terms:]
    term_set = set(sorted_terms)

    # Filter series data to only the selected terms
    series = []
    for pt in sorted(series_map.keys()):
        data = [d for d in series_map[pt] if d["term"] in term_set]
        data.sort(key=lambda d: d["term"])
        series.append({"policy_type": pt, "data": data})

    return {"series": series, "terms": sorted_terms}


# ---------------------------------------------------------------------------
# 9. Exposure Trend  (multi-line: exposure values by type across years)
# ---------------------------------------------------------------------------

def get_exposure_trend_data(
    conn: sqlite3.Connection,
    client_id: int,
    num_years: int = 5,
) -> dict:
    """Multi-line time series: exposure values by type across years.

    Returns:
    {
        "series": [
            {"type": "Payroll", "unit": "currency",
             "data": [{"year": 2022, "amount": 5000000}, ...]},
            ...
        ],
        "years": [2022, 2023, ...]
    }

    Queries client_exposures WHERE project_id IS NULL (corporate level).
    """
    rows = conn.execute(
        """
        SELECT exposure_type, unit, year, amount
        FROM client_exposures
        WHERE client_id = ? AND project_id IS NULL
        ORDER BY year ASC, exposure_type
        """,
        (client_id,),
    ).fetchall()

    all_years: set[int] = set()
    series_map: dict[str, dict] = {}  # type -> {"unit": ..., "data": [...]}
    for r in rows:
        yr = r["year"]
        all_years.add(yr)
        et = r["exposure_type"]
        if et not in series_map:
            series_map[et] = {"unit": r["unit"], "data": []}
        series_map[et]["data"].append({
            "year": yr,
            "amount": r["amount"],
        })

    sorted_years = sorted(all_years)
    if len(sorted_years) > num_years:
        sorted_years = sorted_years[-num_years:]
    year_set = set(sorted_years)

    series = []
    for et in sorted(series_map.keys()):
        info = series_map[et]
        data = [d for d in info["data"] if d["year"] in year_set]
        data.sort(key=lambda d: d["year"])
        series.append({
            "type": et,
            "unit": info["unit"],
            "data": data,
        })

    return {"series": series, "years": sorted_years}


# ---------------------------------------------------------------------------
# 10. Normalized Premium  (premium per $M of exposure by type)
# ---------------------------------------------------------------------------

def get_normalized_premium_data(
    conn: sqlite3.Connection,
    client_id: int,
) -> list[dict]:
    """Grouped bar: premium per $M of exposure by type.

    Returns: [{"exposure_type": "Payroll", "current_rate": 12.5,
               "prior_rate": 11.8, "unit": "per $M"}, ...]

    Joins total premium with client_exposures for current year.
    """
    current_year = date.today().year
    prior_year = current_year - 1

    # Total premium across all active policies
    prem_row = conn.execute(
        f"""
        SELECT
            SUM(COALESCE(p.premium, 0))       AS total_premium,
            SUM(COALESCE(p.prior_premium, 0)) AS total_prior_premium
        FROM policies p
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
        """,
        (client_id,),
    ).fetchone()

    total_premium = (prem_row["total_premium"] or 0) if prem_row else 0
    total_prior = (prem_row["total_prior_premium"] or 0) if prem_row else 0

    if total_premium == 0 and total_prior == 0:
        return []

    # Current-year exposures (corporate level)
    current_exposures = conn.execute(
        """
        SELECT exposure_type, unit, amount
        FROM client_exposures
        WHERE client_id = ? AND year = ? AND project_id IS NULL
        ORDER BY exposure_type
        """,
        (client_id, current_year),
    ).fetchall()

    # Prior-year exposures
    prior_exposures = conn.execute(
        """
        SELECT exposure_type, amount
        FROM client_exposures
        WHERE client_id = ? AND year = ? AND project_id IS NULL
        """,
        (client_id, prior_year),
    ).fetchall()
    prior_map = {r["exposure_type"]: r["amount"] for r in prior_exposures}

    result = []
    for exp in current_exposures:
        current_amt = exp["amount"]
        prior_amt = prior_map.get(exp["exposure_type"])

        # Rate = premium per $1M of exposure
        current_rate = None
        if current_amt and current_amt > 0:
            current_rate = round((total_premium / current_amt) * 1_000_000, 2)

        prior_rate = None
        if prior_amt and prior_amt > 0 and total_prior > 0:
            prior_rate = round((total_prior / prior_amt) * 1_000_000, 2)

        if current_rate is not None or prior_rate is not None:
            result.append({
                "exposure_type": exp["exposure_type"],
                "current_rate": current_rate,
                "prior_rate": prior_rate,
                "unit": "per $M",
            })

    return result


# ---------------------------------------------------------------------------
# 11. Exposure Observations  (YoY change cards)
# ---------------------------------------------------------------------------

def get_exposure_observations_data(
    conn: sqlite3.Connection,
    client_id: int,
) -> list[dict]:
    """Key observations cards: YoY exposure changes.

    Returns: [{"exposure_type": "Payroll", "pct_change": 9.5,
               "direction": "up", "notes": "..."}, ...]

    Reuses get_exposure_observations from queries.py for current year.
    """
    current_year = date.today().year
    return get_exposure_observations(conn, client_id, current_year)


# ---------------------------------------------------------------------------
# 12. Exposure vs Premium Growth  (dual-axis line)
# ---------------------------------------------------------------------------

def get_exposure_vs_premium_data(
    conn: sqlite3.Connection,
    client_id: int,
) -> dict:
    """Dual-axis line: exposure growth % vs premium growth % over time.

    Returns:
    {
        "years": [2022, 2023, ...],
        "exposure_growth": [null, 5.2, ...],
        "premium_growth": [null, 3.1, ...]
    }

    Compares YoY change rates.  First year is always null (no prior to
    compare against).
    """
    # Aggregate exposure totals by year (corporate level, currency units only)
    exp_rows = conn.execute(
        """
        SELECT year, SUM(amount) AS total_amount
        FROM client_exposures
        WHERE client_id = ?
          AND project_id IS NULL
          AND unit = 'currency'
        GROUP BY year
        ORDER BY year
        """,
        (client_id,),
    ).fetchall()

    # Premium totals by term year from premium_history
    prem_rows = conn.execute(
        """
        SELECT
            CAST(SUBSTR(term_effective, 1, 4) AS INTEGER) AS year,
            SUM(premium) AS total_premium
        FROM premium_history
        WHERE client_id = ?
        GROUP BY CAST(SUBSTR(term_effective, 1, 4) AS INTEGER)
        ORDER BY year
        """,
        (client_id,),
    ).fetchall()

    exp_by_year = {r["year"]: r["total_amount"] for r in exp_rows}
    prem_by_year = {r["year"]: r["total_premium"] for r in prem_rows}

    # Union of all years
    all_years = sorted(set(exp_by_year.keys()) | set(prem_by_year.keys()))
    if not all_years:
        return {"years": [], "exposure_growth": [], "premium_growth": []}

    exposure_growth: list[Optional[float]] = []
    premium_growth: list[Optional[float]] = []

    prev_exp: Optional[float] = None
    prev_prem: Optional[float] = None

    for yr in all_years:
        cur_exp = exp_by_year.get(yr)
        cur_prem = prem_by_year.get(yr)

        # Exposure growth
        if prev_exp and prev_exp > 0 and cur_exp is not None:
            exposure_growth.append(
                round(((cur_exp - prev_exp) / prev_exp) * 100, 1)
            )
        else:
            exposure_growth.append(None)

        # Premium growth
        if prev_prem and prev_prem > 0 and cur_prem is not None:
            premium_growth.append(
                round(((cur_prem - prev_prem) / prev_prem) * 100, 1)
            )
        else:
            premium_growth.append(None)

        prev_exp = cur_exp if cur_exp is not None else prev_exp
        prev_prem = cur_prem if cur_prem is not None else prev_prem

    return {
        "years": all_years,
        "exposure_growth": exposure_growth,
        "premium_growth": premium_growth,
    }


# ---------------------------------------------------------------------------
# 13. Executive Financial Summary  (bound program table by section)
# ---------------------------------------------------------------------------

def get_exec_financial_summary_data(
    conn: sqlite3.Connection,
    client_id: int,
) -> dict:
    """Executive Financial Summary — bound program premiums grouped by tower section.

    Returns:
    {
        "sections": [
            {
                "title": "Casualty — Primary",
                "rows": [
                    {"line": "General Liability", "carrier": "Travelers",
                     "expiring": 35000, "normalized": null, "renewal": 38000,
                     "delta_dollars": 3000, "delta_pct": 8.6},
                    ...
                ],
                "subtotal_expiring": 70000,
                "subtotal_normalized": null,
                "subtotal_renewal": 76000,
                "subtotal_delta_dollars": 6000,
                "subtotal_delta_pct": 8.6
            },
            ...
        ],
        "grand_total_expiring": float,
        "grand_total_normalized": null,
        "grand_total_renewal": float,
        "grand_total_delta_dollars": float,
        "grand_total_delta_pct": float | null
    }
    """
    rows = conn.execute(
        f"""
        SELECT
            p.policy_type,
            p.carrier,
            COALESCE(pg.name, p.tower_group) AS program_name,
            p.layer_position,
            p.attachment_point,
            p.limit_amount,
            p.participation_of,
            COALESCE(p.prior_premium, 0) AS prior_premium,
            COALESCE(p.premium, 0)       AS premium
        FROM policies p
        LEFT JOIN programs pg ON pg.id = p.program_id
        WHERE p.client_id = ? AND {_ACTIVE_POLICY}
          AND p.policy_type IS NOT NULL AND p.policy_type != ''
        ORDER BY program_name, p.layer_position, p.attachment_point
        """,
        (client_id,),
    ).fetchall()

    # Group into sections: {program_name} — Primary / Excess
    sections: dict[str, dict] = {}
    for r in rows:
        tg = r["program_name"] or "General"
        lp = (r["layer_position"] or "Primary").strip().lower()
        att = r["attachment_point"] or 0
        is_primary = lp == "primary" or (att == 0 and lp not in ("umbrella",))

        section_label = "Primary" if is_primary else "Excess"
        section_key = f"{tg}|{section_label}"
        if section_key not in sections:
            sections[section_key] = {"title": f"{tg} — {section_label}", "rows": []}

        prior = r["prior_premium"] or 0
        current = r["premium"] or 0
        delta = current - prior
        delta_pct = round((delta / prior) * 100, 1) if prior > 0 else None

        # Build line description
        if is_primary:
            line = r["policy_type"] or "Unknown"
        else:
            line = _layer_notation(
                r["limit_amount"], r["attachment_point"], r["participation_of"]
            )
            pt = r["policy_type"]
            if pt:
                line = f"{pt} — {line}"

        sections[section_key]["rows"].append({
            "line": line,
            "carrier": r["carrier"] or "",
            "expiring": prior,
            "normalized": None,
            "renewal": current,
            "delta_dollars": delta,
            "delta_pct": delta_pct,
        })

    # Calculate subtotals and assemble
    result_sections = []
    grand_exp = 0
    grand_ren = 0
    for key in sorted(sections.keys()):
        section = sections[key]
        sub_exp = sum(r["expiring"] for r in section["rows"])
        sub_ren = sum(r["renewal"] for r in section["rows"])
        sub_delta = sub_ren - sub_exp
        section["subtotal_expiring"] = sub_exp
        section["subtotal_normalized"] = None
        section["subtotal_renewal"] = sub_ren
        section["subtotal_delta_dollars"] = sub_delta
        section["subtotal_delta_pct"] = (
            round((sub_delta / sub_exp) * 100, 1) if sub_exp > 0 else None
        )
        grand_exp += sub_exp
        grand_ren += sub_ren
        result_sections.append(section)

    grand_delta = grand_ren - grand_exp
    return {
        "sections": result_sections,
        "grand_total_expiring": grand_exp,
        "grand_total_normalized": None,
        "grand_total_renewal": grand_ren,
        "grand_total_delta_dollars": grand_delta,
        "grand_total_delta_pct": (
            round((grand_delta / grand_exp) * 100, 1) if grand_exp > 0 else None
        ),
    }
