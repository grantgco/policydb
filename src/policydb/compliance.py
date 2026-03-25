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

    # Exclude Waived and N/A from the denominator — they don't represent
    # active coverage needs, so they shouldn't drag down the percentage.
    applicable = total - counts["waived"] - counts["na"]
    pct = round(counts["compliant"] / applicable * 100) if applicable else (100 if total else 0)
    return {"total": total, **counts, "compliance_pct": pct}


def compute_auto_status(requirement: dict, policy: dict | None) -> str:
    """Auto-compute compliance status from requirement vs. linked policy.

    Returns "Compliant", "Partial", or "Gap".
    - No policy → Gap
    - Policy limit < required limit → Gap
    - Policy deductible > max deductible → Gap
    - Limits pass but endorsements required → Partial
    - Limits pass and no endorsements → Compliant
    """
    if policy is None:
        return "Gap"

    req_limit = float(requirement.get("required_limit") or 0)
    pol_limit = float(policy.get("limit_amount") or 0)
    if req_limit > 0 and pol_limit < req_limit:
        return "Gap"

    max_ded = requirement.get("max_deductible")
    if max_ded is not None:
        pol_ded = float(policy.get("deductible") or 0)
        if pol_ded > float(max_ded):
            return "Gap"

    # Check endorsements
    endorsements_raw = requirement.get("required_endorsements") or "[]"
    try:
        endorsements = json.loads(endorsements_raw) if isinstance(endorsements_raw, str) else endorsements_raw
    except (ValueError, TypeError):
        endorsements = []
    if endorsements:
        return "Partial"

    return "Compliant"


def get_requirement_links(conn, requirement_id: int) -> list[dict]:
    """Return all policy links for a given requirement."""
    rows = conn.execute(
        """SELECT rpl.*, p.policy_type, p.carrier, p.limit_amount, p.deductible,
                  p.is_program, p.program_id, p.policy_number, p.expiration_date
           FROM requirement_policy_links rpl
           LEFT JOIN policies p ON p.policy_uid = rpl.policy_uid
           WHERE rpl.requirement_id = ?
           ORDER BY rpl.is_primary DESC, rpl.created_at""",
        (requirement_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_requirement_links(conn, client_id: int) -> dict[int, list[dict]]:
    """Return all links for all requirements belonging to a client, keyed by requirement_id."""
    rows = conn.execute(
        """SELECT rpl.*, p.policy_type, p.carrier, p.limit_amount, p.deductible,
                  p.is_program, p.program_id, p.policy_number, p.expiration_date
           FROM requirement_policy_links rpl
           JOIN coverage_requirements cr ON cr.id = rpl.requirement_id
           LEFT JOIN policies p ON p.policy_uid = rpl.policy_uid
           WHERE cr.client_id = ?
           ORDER BY rpl.is_primary DESC, rpl.created_at""",
        (client_id,),
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for r in rows:
        d = dict(r)
        result.setdefault(d["requirement_id"], []).append(d)
    return result


def _sync_primary_link(conn, requirement_id: int) -> None:
    """Sync the denormalized linked_policy_uid on coverage_requirements from the junction table."""
    row = conn.execute(
        "SELECT policy_uid FROM requirement_policy_links WHERE requirement_id=? AND is_primary=1",
        (requirement_id,),
    ).fetchone()
    uid = row["policy_uid"] if row else None
    conn.execute(
        "UPDATE coverage_requirements SET linked_policy_uid=? WHERE id=?",
        (uid, requirement_id),
    )


def link_policy_to_requirement(
    conn,
    requirement_id: int,
    policy_uid: str,
    link_type: str = "direct",
    is_primary: bool = False,
    notes: str | None = None,
) -> int:
    """Create a link between a policy and a requirement. Returns link id."""
    # If marking as primary, clear other primaries first
    if is_primary:
        conn.execute(
            "UPDATE requirement_policy_links SET is_primary=0 WHERE requirement_id=?",
            (requirement_id,),
        )

    # Check if no other links exist — auto-set as primary
    existing = conn.execute(
        "SELECT COUNT(*) as cnt FROM requirement_policy_links WHERE requirement_id=?",
        (requirement_id,),
    ).fetchone()
    if existing["cnt"] == 0:
        is_primary = True

    cur = conn.execute(
        """INSERT OR REPLACE INTO requirement_policy_links
           (requirement_id, policy_uid, link_type, is_primary, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (requirement_id, policy_uid, link_type, 1 if is_primary else 0, notes),
    )
    _sync_primary_link(conn, requirement_id)
    conn.commit()
    return cur.lastrowid


def unlink_policy_from_requirement(conn, requirement_id: int, link_id: int) -> None:
    """Remove a link between a policy and a requirement."""
    # Check if this was the primary
    was_primary = conn.execute(
        "SELECT is_primary FROM requirement_policy_links WHERE id=? AND requirement_id=?",
        (link_id, requirement_id),
    ).fetchone()

    conn.execute(
        "DELETE FROM requirement_policy_links WHERE id=? AND requirement_id=?",
        (link_id, requirement_id),
    )

    # If we deleted the primary, promote the next link
    if was_primary and was_primary["is_primary"]:
        next_link = conn.execute(
            "SELECT id FROM requirement_policy_links WHERE requirement_id=? ORDER BY created_at LIMIT 1",
            (requirement_id,),
        ).fetchone()
        if next_link:
            conn.execute(
                "UPDATE requirement_policy_links SET is_primary=1 WHERE id=?",
                (next_link["id"],),
            )

    _sync_primary_link(conn, requirement_id)
    conn.commit()


def set_primary_link(conn, requirement_id: int, link_id: int) -> None:
    """Set a specific link as the primary for a requirement."""
    conn.execute(
        "UPDATE requirement_policy_links SET is_primary=0 WHERE requirement_id=?",
        (requirement_id,),
    )
    conn.execute(
        "UPDATE requirement_policy_links SET is_primary=1 WHERE id=? AND requirement_id=?",
        (link_id, requirement_id),
    )
    _sync_primary_link(conn, requirement_id)
    conn.commit()


def get_linkable_policies(conn, client_id: int, req_project_id: int | None = None) -> list[dict]:
    """Return all non-archived, non-opportunity policies for a client, grouped for UI display.

    Returns programs first (with nested children), then standalone policies.
    When req_project_id is provided, each policy is tagged with _location_match
    ('this', 'corporate', or 'other') and _project_name for display.
    """
    rows = conn.execute(
        """SELECT p.policy_uid, p.policy_type, p.carrier, p.limit_amount, p.deductible,
                  p.policy_number, p.is_program, p.program_id, p.effective_date,
                  p.expiration_date, p.project_id,
                  pr.name AS _project_name
           FROM policies p
           LEFT JOIN projects pr ON p.project_id = pr.id
           WHERE p.client_id=? AND p.archived=0
             AND (p.is_opportunity=0 OR p.is_opportunity IS NULL)
           ORDER BY p.is_program DESC, p.policy_type, p.carrier""",
        (client_id,),
    ).fetchall()
    policies = [dict(r) for r in rows]

    # Tag each policy with location match info
    for p in policies:
        if req_project_id is not None:
            if p.get("project_id") == req_project_id:
                p["_location_match"] = "this"
            elif not p.get("project_id"):
                p["_location_match"] = "corporate"
            else:
                p["_location_match"] = "other"
        else:
            p["_location_match"] = None

    # Separate programs, children, and standalone
    programs = []
    children_by_program: dict[int, list[dict]] = {}
    standalone = []

    # Build a uid→id map for program lookups
    uid_to_id = {}
    for p in policies:
        # We need the actual id for program_id matching
        row = conn.execute(
            "SELECT id FROM policies WHERE policy_uid=?", (p["policy_uid"],)
        ).fetchone()
        if row:
            uid_to_id[p["policy_uid"]] = row["id"]

    id_to_uid = {v: k for k, v in uid_to_id.items()}

    for p in policies:
        if p.get("is_program"):
            pid = uid_to_id.get(p["policy_uid"])
            p["children"] = []
            p["_id"] = pid
            programs.append(p)
        elif p.get("program_id"):
            children_by_program.setdefault(p["program_id"], []).append(p)
        else:
            standalone.append(p)

    # Attach children to programs
    for prog in programs:
        prog["children"] = children_by_program.get(prog.get("_id"), [])

    # Also fetch program_carriers for program display
    for prog in programs:
        carriers = conn.execute(
            "SELECT carrier FROM program_carriers WHERE program_id=? ORDER BY sort_order",
            (prog.get("_id"),),
        ).fetchall()
        prog["program_carrier_names"] = [r["carrier"] for r in carriers]

    # Sort standalone: location-matched first, then corporate, then other
    if req_project_id is not None:
        sort_order = {"this": 0, "corporate": 1, "other": 2}
        standalone.sort(key=lambda p: (sort_order.get(p.get("_location_match"), 2), p.get("policy_type", "")))

    return programs + standalone


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
        "project_id, policy_number, is_program, program_id FROM policies "
        "WHERE client_id=? AND archived=0 ORDER BY policy_type",
        (client_id,),
    ).fetchall()]

    # Load all requirement-policy links for this client (bulk)
    all_links = get_all_requirement_links(conn, client_id)

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
        # Attach policy links to each raw requirement for export
        for req in loc_reqs:
            req_id = req.get("id")
            req["policy_links"] = all_links.get(req_id, []) if req_id else []
        gov = resolve_governing_requirements(loc_reqs)

        # Attach links and auto-suggest policies for each governing requirement
        for line, gov_req in gov.items():
            req_id = gov_req.get("id")
            gov_req["policy_links"] = all_links.get(req_id, []) if req_id else []
            if not gov_req.get("linked_policy_uid") and not gov_req["policy_links"]:
                suggestion = suggest_policy_for_requirement(
                    gov_req, all_policies, location_project_id=loc["id"]
                )
                if suggestion:
                    gov_req["suggested_policy"] = suggestion

        # Auto-compute status for "Needs Review" governing requirements
        for line, gov_req in gov.items():
            status = (gov_req.get("compliance_status") or "Needs Review")
            override = gov_req.get("status_manual_override", 0)
            if status == "Needs Review" and not override and gov_req.get("linked_policy_uid"):
                # Fetch primary linked policy data
                pol = conn.execute(
                    "SELECT limit_amount, deductible FROM policies WHERE policy_uid = ? AND archived = 0",
                    (gov_req["linked_policy_uid"],),
                ).fetchone()
                if pol:
                    new_status = compute_auto_status(gov_req, dict(pol))
                    if new_status != status:
                        conn.execute(
                            "UPDATE coverage_requirements SET compliance_status = ? WHERE id = ?",
                            (new_status, gov_req["id"]),
                        )
                        gov_req["compliance_status"] = new_status
        conn.commit()

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
