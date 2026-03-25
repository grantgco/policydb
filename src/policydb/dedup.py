"""Client-level policy deduplication engine.

Finds likely duplicate policies within a single client — policies imported
from multiple sources (e.g., accounting system + AE spreadsheet) that are
actually the same policy.  Uses additive scoring with no hard gates (same
pattern as reconciler._score_pair).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from itertools import combinations

from rapidfuzz import fuzz

from policydb.utils import (
    normalize_carrier,
    normalize_coverage_type,
    normalize_policy_number_for_matching,
)

logger = logging.getLogger("policydb.dedup")

# ── Fields we compare for diffs ─────────────────────────────────────────────

_COMPARE_FIELDS = [
    "policy_number",
    "policy_type",
    "carrier",
    "effective_date",
    "expiration_date",
    "premium",
    "limit_amount",
    "deductible",
    "retention",
    "project_name",
    "first_named_insured",
    "exposure_address",
    "exposure_city",
    "exposure_state",
    "exposure_zip",
    "notes",
    "broker_fee",
]

# Display labels for diff fields
_FIELD_LABELS = {
    "policy_number": "Policy #",
    "policy_type": "Type",
    "carrier": "Carrier",
    "effective_date": "Eff Date",
    "expiration_date": "Exp Date",
    "premium": "Premium",
    "limit_amount": "Limit",
    "deductible": "Deductible",
    "retention": "Retention",
    "project_name": "Project/Location",
    "first_named_insured": "First Named Insured",
    "exposure_address": "Address",
    "exposure_city": "City",
    "exposure_state": "State",
    "exposure_zip": "ZIP",
    "notes": "Notes",
    "broker_fee": "Broker Fee",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _date_delta_days(d1: str | None, d2: str | None) -> int | None:
    """Return absolute day difference between two YYYY-MM-DD strings, or None."""
    if not d1 or not d2:
        return None
    try:
        dt1 = datetime.strptime(str(d1).strip(), "%Y-%m-%d")
        dt2 = datetime.strptime(str(d2).strip(), "%Y-%m-%d")
        return abs((dt1 - dt2).days)
    except (ValueError, TypeError):
        return None


def _val(row: dict, field: str) -> str:
    """Get a trimmed string value from a row dict, empty string for None."""
    v = row.get(field)
    if v is None:
        return ""
    return str(v).strip()


def _numeric(row: dict, field: str) -> float:
    """Get a numeric value from a row dict, 0.0 for None/empty."""
    v = row.get(field)
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ── Scoring ──────────────────────────────────────────────────────────────────

def _score_pair(a: dict, b: dict) -> dict | None:
    """Score a pair of policies for deduplication.

    Returns a dict with score, confidence, match_signals, diff_fields,
    fillable_a, fillable_b, recommendation, or None if the pair should be
    skipped entirely.

    Scoring (max ~115, but capped at 100 for display):
      - Same eff+exp dates (exact): +35 points
      - Same eff+exp dates (within 14d): +25 points
      - Same policy type (fuzzy >=85): +20 points
      - Same policy type (fuzzy >=70): +12 points
      - Same carrier (fuzzy >=80): +15 points
      - Same policy number (exact normalized): +30 points
      - Same policy number (fuzzy >=75): +20 points
      - Premium within 5%: +10 points
      - Premium within 20%: +5 points
      - Same limit: +5 points
    """

    # ── Skip rules ───────────────────────────────────────────────────────
    # Skip if both are programs
    if a.get("is_program") and b.get("is_program"):
        return None

    # Skip if one is a child of the other
    a_uid = a.get("policy_uid", "")
    b_uid = b.get("policy_uid", "")
    if a.get("program_id") and a["program_id"] == b.get("id"):
        return None
    if b.get("program_id") and b["program_id"] == a.get("id"):
        return None

    # Skip if both have different (non-empty) policy numbers
    pn_a = normalize_policy_number_for_matching(_val(a, "policy_number"))
    pn_b = normalize_policy_number_for_matching(_val(b, "policy_number"))
    if pn_a and pn_b and pn_a != pn_b:
        # Even fuzzy-check — if clearly different, skip
        pn_ratio = fuzz.ratio(pn_a, pn_b)
        if pn_ratio < 75:
            return None

    # ── Policy Number (max 30) ───────────────────────────────────────────
    score_pn = 0.0
    signals = []
    if pn_a and pn_b:
        if pn_a == pn_b:
            score_pn = 30.0
            signals.append("policy_number")
        else:
            pn_ratio = fuzz.ratio(pn_a, pn_b)
            if pn_ratio >= 75:
                score_pn = 20.0
                signals.append("policy_number~")

    # ── Dates (max 35) ───────────────────────────────────────────────────
    eff_a = _val(a, "effective_date")
    eff_b = _val(b, "effective_date")
    exp_a = _val(a, "expiration_date")
    exp_b = _val(b, "expiration_date")

    eff_delta = _date_delta_days(eff_a, eff_b)
    exp_delta = _date_delta_days(exp_a, exp_b)

    score_dates = 0.0
    # Both dates must be present for a date match
    if eff_delta is not None and exp_delta is not None:
        if eff_delta == 0 and exp_delta == 0:
            score_dates = 35.0
            signals.append("dates")
        elif eff_delta <= 14 and exp_delta <= 14:
            score_dates = 25.0
            signals.append("dates~")

    # ── Policy Type (max 20) ─────────────────────────────────────────────
    type_a = normalize_coverage_type(_val(a, "policy_type"))
    type_b = normalize_coverage_type(_val(b, "policy_type"))

    score_type = 0.0
    if type_a and type_b:
        if type_a.lower() == type_b.lower():
            score_type = 20.0
            signals.append("type")
        else:
            type_ratio = fuzz.WRatio(type_a, type_b)
            if type_ratio >= 85:
                score_type = 20.0
                signals.append("type")
            elif type_ratio >= 70:
                score_type = 12.0
                signals.append("type~")

    # ── Carrier (max 15) ─────────────────────────────────────────────────
    carrier_a = normalize_carrier(_val(a, "carrier"))
    carrier_b = normalize_carrier(_val(b, "carrier"))

    score_carrier = 0.0
    if carrier_a and carrier_b:
        if carrier_a.lower() == carrier_b.lower():
            score_carrier = 15.0
            signals.append("carrier")
        else:
            carrier_ratio = fuzz.WRatio(carrier_a, carrier_b)
            if carrier_ratio >= 80:
                score_carrier = 15.0
                signals.append("carrier")

    # ── Premium (max 10) ─────────────────────────────────────────────────
    prem_a = _numeric(a, "premium")
    prem_b = _numeric(b, "premium")

    score_prem = 0.0
    if prem_a > 0 and prem_b > 0:
        prem_ratio = min(prem_a, prem_b) / max(prem_a, prem_b)
        if prem_ratio >= 0.95:
            score_prem = 10.0
            signals.append("premium")
        elif prem_ratio >= 0.80:
            score_prem = 5.0
            signals.append("premium~")

    # ── Limit (max 5) ────────────────────────────────────────────────────
    limit_a = _numeric(a, "limit_amount")
    limit_b = _numeric(b, "limit_amount")

    score_limit = 0.0
    if limit_a > 0 and limit_b > 0 and limit_a == limit_b:
        score_limit = 5.0
        signals.append("limit")

    # ── Total ────────────────────────────────────────────────────────────
    total = score_pn + score_dates + score_type + score_carrier + score_prem + score_limit
    score = min(100, int(round(total)))

    if score < 40:
        return None

    # ── Confidence / Recommendation ──────────────────────────────────────
    if score >= 75:
        confidence = "high"
        recommendation = "likely_duplicate"
    elif score >= 50:
        confidence = "medium"
        recommendation = "possible_duplicate"
    else:
        confidence = "low"
        recommendation = "different_policies"

    # ── Diff fields ──────────────────────────────────────────────────────
    diff_fields = []
    fillable_a = []  # fields A has that B doesn't
    fillable_b = []  # fields B has that A doesn't

    for field in _COMPARE_FIELDS:
        va = _val(a, field)
        vb = _val(b, field)

        if va == vb:
            continue

        label = _FIELD_LABELS.get(field, field)
        diff_fields.append({
            "field": field,
            "label": label,
            "val_a": va,
            "val_b": vb,
        })

        if va and not vb:
            fillable_a.append(field)
        elif vb and not va:
            fillable_b.append(field)

    return {
        "policy_a": a,
        "policy_b": b,
        "score": score,
        "confidence": confidence,
        "match_signals": signals,
        "diff_fields": diff_fields,
        "fillable_a": fillable_a,
        "fillable_b": fillable_b,
        "recommendation": recommendation,
    }


# ── Public API ───────────────────────────────────────────────────────────────

def find_duplicate_candidates(
    conn: sqlite3.Connection,
    client_id: int,
) -> list[dict]:
    """Find all likely duplicate policy pairs within a client.

    Returns list of scored pair dicts, sorted highest score first.
    Excludes previously dismissed pairs.
    """
    # Fetch all non-archived, non-opportunity policies for this client
    rows = conn.execute(
        """
        SELECT p.*, p.id as policy_pk
        FROM policies p
        WHERE p.client_id = ?
          AND p.archived = 0
          AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
        ORDER BY p.policy_uid
        """,
        (client_id,),
    ).fetchall()

    policies = [dict(r) for r in rows]

    if len(policies) < 2:
        return []

    # Load dismissed pairs
    dismissed_rows = conn.execute(
        """
        SELECT policy_uid_a, policy_uid_b FROM dedup_dismissed
        WHERE client_id = ?
        """,
        (client_id,),
    ).fetchall()

    dismissed_set: set[tuple[str, str]] = set()
    for dr in dismissed_rows:
        # Store both orderings for quick lookup
        dismissed_set.add((dr["policy_uid_a"], dr["policy_uid_b"]))
        dismissed_set.add((dr["policy_uid_b"], dr["policy_uid_a"]))

    # Score all pairs
    candidates = []
    for a, b in combinations(policies, 2):
        uid_a = a.get("policy_uid", "")
        uid_b = b.get("policy_uid", "")

        # Skip dismissed pairs
        if (uid_a, uid_b) in dismissed_set:
            continue

        result = _score_pair(a, b)
        if result is not None:
            candidates.append(result)

    # Sort by score descending
    candidates.sort(key=lambda c: -c["score"])

    return candidates


def merge_policies(
    conn: sqlite3.Connection,
    keep_uid: str,
    archive_uid: str,
    cherry_pick: dict,
) -> dict:
    """Merge two policies — keep one, cherry-pick fields from the other, archive the loser.

    Args:
        keep_uid: policy_uid to keep
        archive_uid: policy_uid to archive
        cherry_pick: {field_name: value} — fields to copy from archive to keep

    Returns:
        {"ok": True, "kept": keep_uid, "archived": archive_uid, "fields_transferred": list}
    """
    # Look up both policies
    keep_row = conn.execute(
        "SELECT * FROM policies WHERE policy_uid = ?", (keep_uid,)
    ).fetchone()
    archive_row = conn.execute(
        "SELECT * FROM policies WHERE policy_uid = ?", (archive_uid,)
    ).fetchone()

    if not keep_row or not archive_row:
        return {"ok": False, "error": "One or both policies not found"}

    keep_id = keep_row["id"]
    archive_id = archive_row["id"]

    fields_transferred = []

    # 1. Update keep policy with cherry_pick fields
    if cherry_pick:
        safe_fields = {k: v for k, v in cherry_pick.items() if k in _COMPARE_FIELDS}
        if safe_fields:
            set_clause = ", ".join(f"{k} = ?" for k in safe_fields)
            values = list(safe_fields.values()) + [keep_id]
            conn.execute(
                f"UPDATE policies SET {set_clause} WHERE id = ?",  # noqa: S608
                values,
            )
            fields_transferred = list(safe_fields.keys())

    # 2. Move activity_log entries from archive to keep
    conn.execute(
        "UPDATE activity_log SET policy_id = ? WHERE policy_id = ?",
        (keep_id, archive_id),
    )

    # 3. Move policy_milestones from archive to keep (only if keep doesn't have them)
    existing_milestones = {
        r["milestone"]
        for r in conn.execute(
            "SELECT milestone FROM policy_milestones WHERE policy_uid = ?",
            (keep_uid,),
        ).fetchall()
    }
    archive_milestones = conn.execute(
        "SELECT * FROM policy_milestones WHERE policy_uid = ?",
        (archive_uid,),
    ).fetchall()
    for m in archive_milestones:
        if m["milestone"] not in existing_milestones:
            conn.execute(
                "UPDATE policy_milestones SET policy_uid = ? WHERE id = ?",
                (keep_uid, m["id"]),
            )

    # 4. Move policy_contacts from archive to keep
    conn.execute(
        "UPDATE contact_policy_assignments SET policy_id = ? WHERE policy_id = ?",
        (keep_id, archive_id),
    )

    # 5. Transfer project_id if archive has one and keep doesn't
    if archive_row["project_id"] and not keep_row["project_id"]:
        conn.execute(
            "UPDATE policies SET project_id = ? WHERE id = ?",
            (archive_row["project_id"], keep_id),
        )
        fields_transferred.append("project_id")

    # 6. Archive the loser
    today = date.today().isoformat()
    conn.execute(
        "UPDATE policies SET archived = 1, notes = COALESCE(notes, '') || ? WHERE id = ?",
        (f"\n[Merged into {keep_uid} on {today}]", archive_id),
    )

    # 7. Log activity on keep
    cherry_desc = ", ".join(fields_transferred) if fields_transferred else "none"
    conn.execute(
        """
        INSERT INTO activity_log (client_id, policy_id, activity_type, activity_date, details)
        VALUES (?, ?, 'Note', ?, ?)
        """,
        (
            keep_row["client_id"],
            keep_id,
            today,
            f"Merged duplicate {archive_uid} -- cherry-picked: {cherry_desc}",
        ),
    )

    conn.commit()

    return {
        "ok": True,
        "kept": keep_uid,
        "archived": archive_uid,
        "fields_transferred": fields_transferred,
    }


def dismiss_pair(
    conn: sqlite3.Connection,
    client_id: int,
    policy_uid_a: str,
    policy_uid_b: str,
) -> dict:
    """Dismiss a duplicate pair so it doesn't resurface.

    Always stores the pair in canonical order (alphabetically smaller UID first).
    """
    # Canonical ordering
    uid_a, uid_b = sorted([policy_uid_a, policy_uid_b])
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO dedup_dismissed (client_id, policy_uid_a, policy_uid_b)
            VALUES (?, ?, ?)
            """,
            (client_id, uid_a, uid_b),
        )
        conn.commit()
        return {"ok": True}
    except Exception as e:
        logger.warning("Failed to dismiss dedup pair: %s", e)
        return {"ok": False, "error": str(e)}
