"""Cross-source identity memory for reconciliation.

Remembers which external identifiers (policy numbers, composite keys) from
which sources map to which PolicyDB policies.  Used by the reconciler's
Pass 0 to auto-match before scoring.
"""

from __future__ import annotations

import logging
from sqlite3 import Connection

from policydb.utils import normalize_policy_number_for_matching

logger = logging.getLogger(__name__)


# ── Lookup ────────────────────────────────────────────────────────────────────

def lookup(conn: Connection, source_name: str, external_key: str) -> int | None:
    """Return policy_id if a match memory entry exists, else None."""
    if not source_name or not external_key:
        return None
    row = conn.execute(
        "SELECT policy_id FROM import_match_memory WHERE source_name = ? AND external_key = ?",
        (source_name, external_key),
    ).fetchone()
    return row["policy_id"] if row else None


def lookup_batch(conn: Connection, source_name: str, external_keys: list[str]) -> dict[str, int]:
    """Return {external_key: policy_id} for all known keys from a source."""
    if not source_name or not external_keys:
        return {}
    placeholders = ",".join("?" * len(external_keys))
    rows = conn.execute(
        f"SELECT external_key, policy_id FROM import_match_memory "
        f"WHERE source_name = ? AND external_key IN ({placeholders})",
        [source_name] + list(external_keys),
    ).fetchall()
    return {r["external_key"]: r["policy_id"] for r in rows}


# ── Learning ──────────────────────────────────────────────────────────────────

def learn(
    conn: Connection,
    policy_id: int,
    source_name: str,
    external_key: str,
    key_type: str = "policy_number",
    learned_from: str = "user",
    confidence: float = 100.0,
) -> bool:
    """Store a cross-source identity pair.  Returns True if new, False if already existed."""
    if not source_name or not external_key:
        return False
    try:
        conn.execute(
            "INSERT INTO import_match_memory (policy_id, source_name, external_key, key_type, confidence, learned_from) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_name, external_key) DO UPDATE SET "
            "  policy_id = excluded.policy_id, "
            "  confidence = excluded.confidence, "
            "  learned_from = excluded.learned_from, "
            "  learned_at = CURRENT_TIMESTAMP",
            (policy_id, source_name, external_key, key_type, confidence, learned_from),
        )
        conn.commit()
        logger.info(
            "Match memory: learned %s/%s → policy_id=%d (from=%s)",
            source_name, external_key, policy_id, learned_from,
        )
        return True
    except Exception:
        logger.exception("Match memory learn failed: %s/%s", source_name, external_key)
        return False


def learn_from_reconcile_pair(
    conn: Connection,
    policy_id: int,
    source_name: str,
    ext_row: dict,
) -> int:
    """Learn identity from a confirmed reconcile pair.  Stores the policy_number
    (if present) as external_key.  Returns count of entries created."""
    if not source_name or not ext_row:
        return 0
    count = 0
    # Store raw policy number
    raw_pn = (ext_row.get("policy_number") or "").strip()
    if raw_pn:
        if learn(conn, policy_id, source_name, raw_pn, "policy_number", "reconcile"):
            count += 1
    # Also store normalized form (in case future uploads use a different format)
    norm_pn = normalize_policy_number_for_matching(raw_pn)
    if norm_pn and norm_pn != raw_pn:
        if learn(conn, policy_id, source_name, norm_pn, "policy_number_normalized", "reconcile"):
            count += 1
    return count


# ── Forget ────────────────────────────────────────────────────────────────────

def forget(conn: Connection, source_name: str, external_key: str) -> bool:
    """Remove a match memory entry."""
    conn.execute(
        "DELETE FROM import_match_memory WHERE source_name = ? AND external_key = ?",
        (source_name, external_key),
    )
    conn.commit()
    return True


def forget_all_for_policy(conn: Connection, policy_id: int) -> int:
    """Remove all match memory entries for a policy.  Returns count deleted."""
    cur = conn.execute(
        "DELETE FROM import_match_memory WHERE policy_id = ?",
        (policy_id,),
    )
    conn.commit()
    return cur.rowcount


# ── Query ─────────────────────────────────────────────────────────────────────

def get_all_for_policy(conn: Connection, policy_id: int) -> list[dict]:
    """Return all identity entries for a policy."""
    rows = conn.execute(
        "SELECT * FROM import_match_memory WHERE policy_id = ? ORDER BY learned_at DESC",
        (policy_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn: Connection) -> dict:
    """Return aggregate match memory stats."""
    row = conn.execute(
        "SELECT COUNT(*) as total, COUNT(DISTINCT policy_id) as policies, "
        "COUNT(DISTINCT source_name) as sources FROM import_match_memory"
    ).fetchone()
    return dict(row) if row else {"total": 0, "policies": 0, "sources": 0}


# ── Duplicate detection ───────────────────────────────────────────────────────

def detect_duplicates_in_upload(
    conn: Connection,
    source_name: str,
    ext_rows: list[dict],
) -> list[tuple[int, int, int]]:
    """Check if multiple ext_rows resolve to the same policy_id via match memory.

    Returns list of (ext_idx_a, ext_idx_b, policy_id) tuples where two uploaded
    rows point to the same PolicyDB policy.
    """
    if not source_name or not ext_rows:
        return []
    # Build external keys for all rows
    keys: list[tuple[int, str]] = []
    for i, row in enumerate(ext_rows):
        pn = (row.get("policy_number") or "").strip()
        if pn:
            keys.append((i, pn))
        norm = normalize_policy_number_for_matching(pn)
        if norm and norm != pn:
            keys.append((i, norm))

    if not keys:
        return []

    # Batch lookup
    all_keys = [k for _, k in keys]
    memory = lookup_batch(conn, source_name, all_keys)

    # Group by policy_id
    pid_to_indices: dict[int, list[int]] = {}
    for idx, key in keys:
        pid = memory.get(key)
        if pid is not None:
            pid_to_indices.setdefault(pid, []).append(idx)

    # Find duplicates (same policy_id, different ext row indices)
    duplicates = []
    for pid, indices in pid_to_indices.items():
        unique_indices = sorted(set(indices))
        if len(unique_indices) > 1:
            for i in range(len(unique_indices)):
                for j in range(i + 1, len(unique_indices)):
                    duplicates.append((unique_indices[i], unique_indices[j], pid))
    return duplicates
