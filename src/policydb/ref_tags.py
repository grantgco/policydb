"""Build wide OR-joined Outlook search queries for a record and its relatives.

Informed by the `[PDB:...]` tag format produced by `utils.build_ref_tag()`:
    - Policy UIDs stored as POL-042, appear undashed (POL042) in compound tags.
    - Program UIDs stored as PGM-3, appear undashed (PGM3) in compound tags.
    - Issue UIDs stored as ISS-2026-001, appear verbatim.
    - Client CN appears as CN{number}.
    - Project location shows up only as L{id} — too ambiguous to search on.

So this module emits BOTH dashed and undashed forms for policies/programs
(to catch natural-text mentions and compound-tag mentions), verbatim forms
for issues/CN, and skips projects' own token in favor of their children.

Mode semantics:
    narrow — self only (e.g. just POL-042 / POL042).
    wide   — self + immediate relatives (issues under a policy; policies +
             issues under a program/project/client). Does NOT include the
             client CN for sub-client entities — the CN would OR-match
             every message about the client and drown the record-specific
             results.
    client — client CN only. Escape hatch to sweep every message tagged
             to the client, regardless of which sub-record you launched
             from.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Callable, Literal


EntityType = Literal["client", "policy", "issue", "project", "program"]
Mode = Literal["wide", "narrow", "client"]


@dataclass
class WideSearchResult:
    query: str
    tokens: list[str]
    total_available: int
    truncated: bool = False


def build_wide_search(
    conn: sqlite3.Connection,
    entity_type: EntityType,
    entity_id: int | str,
    mode: Mode = "wide",
    cap: int = 60,
) -> WideSearchResult:
    """Return an OR-joined quoted Outlook search covering a record + relatives.

    Tokens are ordered by specificity (most specific first), so the cap drops
    the broadest tokens last. See module docstring for the token format rules.
    """
    walker = _WALKERS.get(entity_type)
    if walker is None:
        raise ValueError(f"Unknown entity_type: {entity_type}")

    tokens = walker(conn, entity_id, mode)
    total = len(tokens)
    truncated = False
    if total > cap:
        tokens = tokens[:cap]
        truncated = True

    query = " OR ".join(f'"{t}"' for t in tokens)
    return WideSearchResult(
        query=query,
        tokens=tokens,
        total_available=total,
        truncated=truncated,
    )


def _policy_tokens(policy_uid: str) -> list[str]:
    """Policy contributes two tokens: dashed and undashed."""
    undashed = policy_uid.replace("-", "")
    if undashed == policy_uid:
        return [policy_uid]
    return [policy_uid, undashed]


def _program_tokens(program_uid: str) -> list[str]:
    """Program contributes two tokens: dashed and undashed."""
    undashed = program_uid.replace("-", "")
    if undashed == program_uid:
        return [program_uid]
    return [program_uid, undashed]


def _issue_tokens(issue_uid: str) -> list[str]:
    """Issue UID appears verbatim in compound tags — single token."""
    return [issue_uid]


def _rfi_tokens(rfi_uid: str) -> list[str]:
    """RFI UID contributes two tokens: dashed (C1-RFI01) and undashed (C1RFI01)."""
    undashed = rfi_uid.replace("-", "")
    if undashed == rfi_uid:
        return [rfi_uid]
    return [rfi_uid, undashed]


def _collect_policy_rfi_uids(conn: sqlite3.Connection, policy_uids: list[str]) -> list[str]:
    """Return distinct RFI UIDs (order-preserving) for any open-or-closed bundle
    that has at least one item referencing one of ``policy_uids``."""
    if not policy_uids:
        return []
    placeholders = ",".join("?" * len(policy_uids))
    rows = conn.execute(
        f"SELECT DISTINCT b.rfi_uid "  # noqa: S608 — placeholders
        f"FROM client_request_bundles b "
        f"JOIN client_request_items i ON i.bundle_id = b.id "
        f"WHERE i.policy_uid IN ({placeholders}) AND b.rfi_uid IS NOT NULL",
        policy_uids,
    ).fetchall()
    return [r["rfi_uid"] for r in rows if r["rfi_uid"]]


def _cn_tokens(cn_number: str | None, client_id: int) -> list[str]:
    """Client CN — strips optional leading CN prefix (case-insensitive), then re-prefixes.

    Falls back to C{client_id} if cn_number is empty or None.
    Handles edge cases like "CNN123" correctly (not "N123").
    """
    if cn_number and cn_number not in ("None", "none", ""):
        # Use same regex as build_ref_tag() in utils.py: ^[Cc][Nn]
        cleaned = re.sub(r'^[Cc][Nn]', '', cn_number)
        return [f"CN{cleaned}"]
    return [f"C{client_id}"]


def _walk_client(
    conn: sqlite3.Connection, client_id: int | str, mode: Mode
) -> list[str]:
    cid = int(client_id)
    row = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (cid,)
    ).fetchone()
    if row is None:
        raise KeyError(f"client {client_id} not found")
    cn_tokens = _cn_tokens(row["cn_number"], cid)

    if mode == "client" or mode == "narrow":
        return cn_tokens

    tokens: list[str] = []
    # Issues first (most specific)
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE client_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (cid,),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    # Policies
    for r in conn.execute(
        "SELECT policy_uid FROM policies WHERE client_id = ? "
        "AND policy_uid IS NOT NULL",
        (cid,),
    ):
        tokens.extend(_policy_tokens(r["policy_uid"]))
    # Programs
    for r in conn.execute(
        "SELECT program_uid FROM programs WHERE client_id = ? "
        "AND program_uid IS NOT NULL",
        (cid,),
    ):
        tokens.extend(_program_tokens(r["program_uid"]))
    tokens.extend(cn_tokens)
    return tokens


def _walk_policy(
    conn: sqlite3.Connection, policy_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(policy_uid)
    row = conn.execute(
        "SELECT id, client_id, policy_uid FROM policies WHERE policy_uid = ?",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"policy {policy_uid} not found")
    policy_tokens = _policy_tokens(row["policy_uid"])

    if mode == "narrow":
        return policy_tokens

    if mode == "client":
        client_row = conn.execute(
            "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
        ).fetchone()
        return _cn_tokens(
            client_row["cn_number"] if client_row else None, row["client_id"]
        )

    tokens: list[str] = list(policy_tokens)
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE policy_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (row["id"],),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    # Cascade: any RFI with an item pointing to this policy.
    for rfi_uid in _collect_policy_rfi_uids(conn, [row["policy_uid"]]):
        tokens.extend(_rfi_tokens(rfi_uid))
    return tokens


def _walk_issue(
    conn: sqlite3.Connection, issue_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(issue_uid)
    row = conn.execute(
        "SELECT client_id, policy_id, issue_uid FROM activity_log "
        "WHERE issue_uid = ? AND item_kind = 'issue'",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"issue {issue_uid} not found")
    issue_tokens = _issue_tokens(row["issue_uid"])

    if mode == "narrow":
        return issue_tokens

    if mode == "client":
        cli = conn.execute(
            "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
        ).fetchone()
        return _cn_tokens(cli["cn_number"] if cli else None, row["client_id"])

    tokens: list[str] = list(issue_tokens)
    if row["policy_id"]:
        pol = conn.execute(
            "SELECT policy_uid FROM policies WHERE id = ?", (row["policy_id"],)
        ).fetchone()
        if pol and pol["policy_uid"]:
            tokens.extend(_policy_tokens(pol["policy_uid"]))
            # Cascade: any RFI tied to the issue's policy.
            for rfi_uid in _collect_policy_rfi_uids(conn, [pol["policy_uid"]]):
                tokens.extend(_rfi_tokens(rfi_uid))
    return tokens


def _walk_project(
    conn: sqlite3.Connection, project_id: int | str, mode: Mode
) -> list[str]:
    pid = int(project_id)
    row = conn.execute(
        "SELECT client_id FROM projects WHERE id = ?", (pid,)
    ).fetchone()
    if row is None:
        raise KeyError(f"project {project_id} not found")

    if mode == "narrow" or mode == "client":
        # Projects have no own searchable token → fall back to client CN.
        cli = conn.execute(
            "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
        ).fetchone()
        return _cn_tokens(
            cli["cn_number"] if cli else None, row["client_id"]
        )

    tokens: list[str] = []
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE project_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (pid,),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    member_policy_uids = [
        r["policy_uid"]
        for r in conn.execute(
            "SELECT policy_uid FROM policies WHERE project_id = ? "
            "AND policy_uid IS NOT NULL",
            (pid,),
        )
    ]
    for uid in member_policy_uids:
        tokens.extend(_policy_tokens(uid))
    # Cascade: RFIs tied to any policy in this project.
    for rfi_uid in _collect_policy_rfi_uids(conn, member_policy_uids):
        tokens.extend(_rfi_tokens(rfi_uid))
    return tokens


def _walk_program(
    conn: sqlite3.Connection, program_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(program_uid)
    row = conn.execute(
        "SELECT id, client_id, program_uid FROM programs WHERE program_uid = ?",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"program {program_uid} not found")
    program_tokens = _program_tokens(row["program_uid"])

    if mode == "narrow":
        return program_tokens

    if mode == "client":
        cli = conn.execute(
            "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
        ).fetchone()
        return _cn_tokens(
            cli["cn_number"] if cli else None, row["client_id"]
        )

    tokens: list[str] = list(program_tokens)
    # Member policies: policies.program_id is a direct FK (NO junction table).
    member_policy_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM policies WHERE program_id = ?", (row["id"],),
        )
    ]
    # Issues on the program directly
    issue_uids: list[str] = []
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE program_id = ? AND item_kind = 'issue' "
        "AND issue_uid IS NOT NULL AND merged_into_id IS NULL",
        (row["id"],),
    ):
        issue_uids.append(r["issue_uid"])

    # Issues on member policies
    if member_policy_ids:
        placeholders = ",".join("?" * len(member_policy_ids))
        for r in conn.execute(
            f"SELECT issue_uid FROM activity_log "
            f"WHERE policy_id IN ({placeholders}) AND item_kind = 'issue' "
            f"AND issue_uid IS NOT NULL AND merged_into_id IS NULL",
            member_policy_ids,
        ):
            issue_uids.append(r["issue_uid"])

    # Dedup while preserving order
    seen_issues: set[str] = set()
    for uid in issue_uids:
        if uid in seen_issues:
            continue
        seen_issues.add(uid)
        tokens.extend(_issue_tokens(uid))

    # Member policies themselves
    member_policy_uids: list[str] = []
    if member_policy_ids:
        placeholders = ",".join("?" * len(member_policy_ids))
        for r in conn.execute(
            f"SELECT policy_uid FROM policies WHERE id IN ({placeholders}) "
            f"AND policy_uid IS NOT NULL",
            member_policy_ids,
        ):
            member_policy_uids.append(r["policy_uid"])
            tokens.extend(_policy_tokens(r["policy_uid"]))

    # Cascade: RFIs tied to the program (via bundles.program_uid) OR to any
    # member policy (via items.policy_uid). Dedup across both sources.
    seen_rfi: set[str] = set()
    for r in conn.execute(
        "SELECT rfi_uid FROM client_request_bundles "
        "WHERE program_uid = ? AND rfi_uid IS NOT NULL",
        (row["program_uid"],),
    ):
        if r["rfi_uid"] not in seen_rfi:
            seen_rfi.add(r["rfi_uid"])
            tokens.extend(_rfi_tokens(r["rfi_uid"]))
    for rfi_uid in _collect_policy_rfi_uids(conn, member_policy_uids):
        if rfi_uid not in seen_rfi:
            seen_rfi.add(rfi_uid)
            tokens.extend(_rfi_tokens(rfi_uid))
    return tokens


_WALKERS: dict[str, Callable[[sqlite3.Connection, int | str, Mode], list[str]]] = {
    "client": _walk_client,
    "policy": _walk_policy,
    "issue": _walk_issue,
    "project": _walk_project,
    "program": _walk_program,
}
