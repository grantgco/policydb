"""Compliance engine: inheritance resolution, conflict resolution, policy matching."""

from __future__ import annotations

import json

from policydb.utils import normalize_coverage_type


def _parse_endorsements(val) -> list[str]:
    """Parse required_endorsements from DB (JSON string) or dict (already parsed)."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def resolve_governing_requirements(
    requirements: list[dict],
) -> dict[str, dict]:
    """Resolve a list of requirements to one governing requirement per coverage line.

    When multiple sources require the same coverage line:
    - Highest required_limit wins
    - Lowest max_deductible wins (more stringent)
    - Endorsement lists unioned across all sources (if ANY source requires it, it's required)
    - governing_source tracks which source drove the most stringent limit

    Args:
        requirements: List of requirement dicts (from coverage_requirements table
                      joined with requirement_sources for source_name)

    Returns:
        Dict keyed by coverage_line, each value is the governing requirement dict
        with an added 'governing_source' field and 'source_requirements' list.
    """
    if not requirements:
        return {}

    # Group by coverage_line
    by_line: dict[str, list[dict]] = {}
    for req in requirements:
        line = req["coverage_line"]
        by_line.setdefault(line, []).append(req)

    governing: dict[str, dict] = {}
    for line, reqs in by_line.items():
        if len(reqs) == 1:
            gov = dict(reqs[0])
            gov["governing_source"] = gov.get("source_name", "")
            gov["source_requirements"] = reqs
            governing[line] = gov
            continue

        # Resolve to most stringent
        gov = dict(reqs[0])
        gov["required_endorsements"] = _parse_endorsements(gov.get("required_endorsements"))
        gov_limit_source = gov.get("source_name", "")

        for req in reqs[1:]:
            req_source = req.get("source_name", "")

            # Higher limit is more stringent (client needs MORE coverage)
            req_limit = req.get("required_limit") or 0
            cur_limit = gov.get("required_limit") or 0
            limit_improved = req_limit > cur_limit
            if limit_improved:
                gov["required_limit"] = req_limit
                gov_limit_source = req_source

            # Lower max_deductible is more stringent
            req_ded = req.get("max_deductible")
            gov_ded = gov.get("max_deductible")
            ded_improved = req_ded is not None and (gov_ded is None or req_ded < gov_ded)
            if ded_improved:
                gov["max_deductible"] = req_ded
                gov["deductible_type"] = req.get("deductible_type")
                if not limit_improved:
                    gov_limit_source = req_source

            # Union endorsements across all sources
            req_endorsements = _parse_endorsements(req.get("required_endorsements"))
            existing = set(gov["required_endorsements"])
            for e in req_endorsements:
                if e not in existing:
                    gov["required_endorsements"].append(e)
                    existing.add(e)

        gov["governing_source"] = gov_limit_source
        gov["source_requirements"] = reqs
        governing[line] = gov

    return governing


def get_location_requirements(
    conn,
    client_id: int,
    project_id: int | None,
) -> list[dict]:
    """Fetch all requirements that apply to a specific location.

    Includes:
    - Client-level requirements (project_id IS NULL) — inherited
    - Location-specific requirements (project_id = given project_id)

    Each row is joined with requirement_sources for source_name.
    """
    sql = """
        SELECT cr.*, rs.name AS source_name, rs.counterparty, rs.clause_ref,
               rs.project_id AS source_project_id, rs.notes AS source_notes
        FROM coverage_requirements cr
        LEFT JOIN requirement_sources rs ON cr.source_id = rs.id
        WHERE cr.client_id = ?
          AND (
            /* Requirement explicitly for this location */
            cr.project_id = ?
            /* OR requirement is client-wide AND its source is also client-wide (or unlinked) */
            OR (cr.project_id IS NULL AND (rs.project_id IS NULL OR rs.id IS NULL))
            /* OR requirement is client-wide AND its source is scoped to THIS location */
            OR (cr.project_id IS NULL AND rs.project_id = ?)
          )
        ORDER BY cr.coverage_line, cr.source_id
    """
    rows = conn.execute(sql, (client_id, project_id, project_id)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Pre-parse endorsements JSON for template rendering
        d["_endorsements_list"] = _parse_endorsements(d.get("required_endorsements"))
        result.append(d)
    return result


def suggest_policy_for_requirement(
    gov_req: dict,
    policies: list[dict],
    location_project_id: int | None = None,
) -> dict | None:
    """Suggest the best policy match for a governing requirement.

    Priority:
    1. Location-specific policy (project_id matches) with matching coverage type
    2. Corporate policy (project_id IS NULL) with matching coverage type
    3. None if no match

    Uses normalize_coverage_type for fuzzy matching.
    """
    target_line = gov_req.get("coverage_line", "")
    target_normalized = normalize_coverage_type(target_line)

    location_matches = []
    corporate_matches = []

    for pol in policies:
        pol_type = normalize_coverage_type(pol.get("policy_type", ""))
        if pol_type != target_normalized:
            continue

        if location_project_id and pol.get("project_id") == location_project_id:
            location_matches.append(pol)
        elif not pol.get("project_id"):
            corporate_matches.append(pol)

    # Prefer location-specific, then corporate
    if location_matches:
        return location_matches[0]
    if corporate_matches:
        return corporate_matches[0]
    return None


def compute_compliance_summary(governing: dict[str, dict]) -> dict:
    """Compute aggregate compliance stats from governing requirements.

    Returns dict with: total, compliant, gap, partial, waived, na,
    needs_review, compliance_pct.
    """
    total = len(governing)
    counts = {"compliant": 0, "gap": 0, "partial": 0, "waived": 0,
              "na": 0, "needs_review": 0}

    for gov in governing.values():
        status = (gov.get("compliance_status") or "Needs Review").lower().replace(" ", "_").replace("/", "")
        if status == "compliant":
            counts["compliant"] += 1
        elif status == "gap":
            counts["gap"] += 1
        elif status == "partial":
            counts["partial"] += 1
        elif status == "waived":
            counts["waived"] += 1
        elif status in ("na", "n/a", "n_a"):
            counts["na"] += 1
        else:
            counts["needs_review"] += 1

    pct = round(counts["compliant"] / total * 100) if total else 0
    return {"total": total, **counts, "compliance_pct": pct}


def get_client_compliance_data(conn, client_id: int) -> dict:
    """Build the full compliance dataset for a client.

    Returns:
        {
            "locations": [
                {
                    "project": {id, name, address, ...},
                    "requirements": [...],
                    "governing": {coverage_line: {...}, ...},
                    "summary": {total, compliant, gap, ...},
                    "policies": [...],
                },
                ...
            ],
            "client_requirements": [...],  # project_id IS NULL
            "sources": [...],
            "overall_summary": {total, compliant, gap, ...},
        }
    """
    # Get all locations for this client
    locations = [dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Get all policies for this client (non-archived)
    all_policies = [dict(r) for r in conn.execute(
        "SELECT policy_uid, policy_type, carrier, limit_amount, deductible, "
        "project_id, policy_number FROM policies "
        "WHERE client_id=? AND archived=0 ORDER BY policy_type",
        (client_id,),
    ).fetchall()]

    # Get all sources for this client
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? ORDER BY name",
        (client_id,),
    ).fetchall()]

    # Client-level requirements (no project_id)
    client_reqs = [dict(r) for r in conn.execute(
        """SELECT cr.*, rs.name AS source_name, rs.counterparty, rs.clause_ref
           FROM coverage_requirements cr
           LEFT JOIN requirement_sources rs ON cr.source_id = rs.id
           WHERE cr.client_id = ? AND cr.project_id IS NULL
           ORDER BY cr.coverage_line""",
        (client_id,),
    ).fetchall()]

    # Build per-location data
    location_data = []
    all_governing = {}

    for loc in locations:
        loc_reqs = get_location_requirements(conn, client_id, loc["id"])
        gov = resolve_governing_requirements(loc_reqs)

        # Auto-suggest policies for each governing requirement
        for line, gov_req in gov.items():
            if not gov_req.get("linked_policy_uid"):
                suggestion = suggest_policy_for_requirement(
                    gov_req, all_policies, location_project_id=loc["id"]
                )
                if suggestion:
                    gov_req["suggested_policy"] = suggestion

        summary = compute_compliance_summary(gov)

        location_data.append({
            "project": loc,
            "requirements": loc_reqs,
            "governing": gov,
            "summary": summary,
            "policies": [p for p in all_policies
                         if p.get("project_id") == loc["id"]
                         or not p.get("project_id")],
        })

        # Merge into overall
        for line, g in gov.items():
            key = f"{loc['id']}:{line}"
            all_governing[key] = g

    overall_summary = compute_compliance_summary(all_governing)

    return {
        "locations": location_data,
        "client_requirements": client_reqs,
        "sources": sources,
        "all_policies": all_policies,
        "overall_summary": overall_summary,
    }


def get_risk_review_prompts(
    client: dict,
    locations: list[dict],
    policies: list[dict],
    cfg_prompts: list[dict],
) -> list[dict]:
    """Generate guided risk review prompts from config-driven definitions.

    Dynamically sets priority based on:
    - Industry keyword matches (from 'industry_keywords_high' field in prompt def)
    - Coverage gaps (if 'coverage_lines' in prompt def are missing from policy set)

    Args:
        client: Client dict (must include 'industry' field)
        locations: List of location/project dicts
        policies: List of policy dicts for this client
        cfg_prompts: List of prompt definitions from config, each with:
            - prompt: str (the question text)
            - priority: str ("High", "Medium", "Low")
            - industry_keywords_high: list[str] (optional — escalates to High)
            - coverage_lines: list[str] (optional — escalates to High if missing)

    Returns:
        List of prompt dicts with added 'priority' and 'relevance' fields.
    """
    client_industry = (client.get("industry") or "").lower()
    policy_types_normalized = {
        normalize_coverage_type(p.get("policy_type", ""))
        for p in policies
    }

    result = []
    for prompt_def in cfg_prompts:
        prompt = dict(prompt_def)
        priority = prompt.get("priority", "Low")
        relevance_notes = []

        # Check industry keyword escalation
        industry_keywords = prompt.get("industry_keywords_high", [])
        for kw in industry_keywords:
            if kw.lower() in client_industry:
                priority = "High"
                relevance_notes.append(f"Industry match: {kw}")
                break

        # Check coverage gap escalation
        required_lines = prompt.get("coverage_lines", [])
        missing_lines = []
        for line in required_lines:
            normalized = normalize_coverage_type(line)
            if normalized not in policy_types_normalized:
                missing_lines.append(line)

        if missing_lines:
            priority = "High"
            relevance_notes.append(f"Missing coverage: {', '.join(missing_lines)}")

        prompt["priority"] = priority
        prompt["relevance"] = "; ".join(relevance_notes) if relevance_notes else ""
        result.append(prompt)

    return result


# ── Risk → Requirement Spawning ──────────────────────────────────────────────


def spawn_requirements_from_risk(
    conn,
    client_id: int,
    risk_id: int,
    source_id: int | None = None,
) -> list[int]:
    """Create coverage_requirements for each risk_coverage_line not already present.

    For each coverage_line linked to the risk that doesn't already have a
    matching coverage_requirement (same client_id, risk_id, coverage_line),
    create one with compliance_status='Needs Review'.

    Returns list of created requirement IDs.
    """
    # Get the risk description for notes
    risk_row = conn.execute(
        "SELECT description, category FROM client_risks WHERE id=?", (risk_id,)
    ).fetchone()
    risk_desc = dict(risk_row).get("description", "") if risk_row else ""

    # Get coverage lines for this risk
    risk_lines = conn.execute(
        "SELECT coverage_line FROM risk_coverage_lines WHERE risk_id=?",
        (risk_id,),
    ).fetchall()

    if not risk_lines:
        return []

    # Get existing requirements already spawned from this risk
    existing = {
        r["coverage_line"]
        for r in conn.execute(
            "SELECT coverage_line FROM coverage_requirements WHERE client_id=? AND risk_id=?",
            (client_id, risk_id),
        ).fetchall()
    }

    created_ids = []
    for row in risk_lines:
        line = row["coverage_line"]
        if line in existing:
            continue

        cur = conn.execute(
            """INSERT INTO coverage_requirements
               (client_id, risk_id, source_id, coverage_line,
                compliance_status, notes, required_endorsements)
               VALUES (?, ?, ?, ?, 'Needs Review', ?, '[]')""",
            (
                client_id,
                risk_id,
                source_id,
                line,
                f"Auto-created from risk: {risk_desc}" if risk_desc else None,
            ),
        )
        created_ids.append(cur.lastrowid)

    if created_ids:
        conn.commit()

    return created_ids
