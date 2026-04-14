#!/usr/bin/env python3
"""Recover policies that were hard-deleted by the pre-fix tower matrix
delete buttons (delete_underlying_v2 / delete_excess_v2 in programs.py).

Bug
---
Before this script's sibling fix, clicking the red X on an Underlying or
Excess row in the Schematic tab did an unconditional hard DELETE on the
`policies` row. That was fine for blank placeholder rows but wiped real
policies that had been linked to a program via the `/assign` layer-picker
flow. The user reports: "I deleted the Excess policy from the child
policies table in the tower construction tab. However, it is now not
showing up anywhere in the system."

How this script works
---------------------
1. Reads the live DB at ~/.policydb/policydb.sqlite.
2. Walks every backup in ~/.policydb/backups/ from newest to oldest.
3. For every policy_uid present in any backup but missing from live,
   picks the most recent backup that still contained the row and reports
   it. That's our best reconstruction of the deleted record.
4. With --restore, re-INSERTs those rows into the live `policies` table,
   preserving `program_id` / `tower_group` / `layer_position` so they
   pop back onto the correct program and layer.

Usage
-----
    # Dry run (default) — show what would be restored, don't touch DB.
    python scripts/recover_deleted_program_policies.py

    # Inspect a specific policy_uid
    python scripts/recover_deleted_program_policies.py --uid POL-042

    # Actually restore. Makes a safety backup first.
    python scripts/recover_deleted_program_policies.py --restore

    # Only restore policies on a specific program
    python scripts/recover_deleted_program_policies.py --restore --program PGM-001

The script is idempotent — re-running after a restore is a no-op because
the rows are back in live.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LIVE_DB = Path(os.path.expanduser("~/.policydb/policydb.sqlite"))
BACKUP_DIR = Path(os.path.expanduser("~/.policydb/backups"))


def _conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _policies_columns(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(policies)").fetchall()]


@dataclass
class Candidate:
    uid: str
    source_backup: Path
    row: sqlite3.Row
    program_uid: str | None  # resolved from backup's programs table


def _resolve_program_uid(conn: sqlite3.Connection, program_id: int | None) -> str | None:
    if not program_id:
        return None
    r = conn.execute(
        "SELECT program_uid FROM programs WHERE id = ?", (program_id,)
    ).fetchone()
    return r["program_uid"] if r else None


def find_missing_policies(
    live_path: Path, backup_dir: Path, uid_filter: str | None = None
) -> list[Candidate]:
    live = _conn(live_path)
    live_uids = {r[0] for r in live.execute("SELECT policy_uid FROM policies").fetchall()}
    live.close()

    backups = sorted(backup_dir.glob("policydb_*.sqlite"), reverse=True)
    if not backups:
        print(f"No backups found in {backup_dir}", file=sys.stderr)
        return []

    # Walk newest → oldest. First time we see a missing uid, that's the
    # freshest copy of its data.
    found: dict[str, Candidate] = {}
    for b in backups:
        try:
            bconn = _conn(b)
        except sqlite3.Error as e:
            print(f"  ! could not open {b.name}: {e}", file=sys.stderr)
            continue
        try:
            rows = bconn.execute("SELECT * FROM policies").fetchall()
        except sqlite3.Error as e:
            print(f"  ! {b.name}: schema mismatch, skipping ({e})", file=sys.stderr)
            bconn.close()
            continue
        for r in rows:
            uid = r["policy_uid"]
            if uid in live_uids or uid in found:
                continue
            if uid_filter and uid != uid_filter:
                continue
            program_uid = _resolve_program_uid(bconn, r["program_id"])
            found[uid] = Candidate(
                uid=uid, source_backup=b, row=r, program_uid=program_uid
            )
        bconn.close()

    return list(found.values())


def _describe(c: Candidate) -> str:
    r = c.row
    bits = [
        f"uid={c.uid}",
        f"type={r['policy_type']!r}",
        f"carrier={r['carrier']!r}",
        f"number={r['policy_number']!r}",
        f"limit={r['limit_amount']}",
        f"layer={r['layer_position']!r}",
        f"program={c.program_uid or '(none)'}",
    ]
    return " ".join(bits)


def restore(candidates: list[Candidate], live_path: Path) -> tuple[int, int]:
    """Re-INSERT each candidate. Skips if a row with that uid reappeared
    between the scan and the write. Returns (restored, skipped)."""
    if not candidates:
        return 0, 0

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safety = live_path.parent / "backups" / f"policydb_{ts}_preres.sqlite"
    safety.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live_path, safety)
    print(f"  Safety backup written: {safety}")

    live = _conn(live_path)
    live.execute("PRAGMA foreign_keys = OFF")  # we want to insert as-is
    cols = _policies_columns(live)
    restored = 0
    skipped = 0
    try:
        for c in candidates:
            exists = live.execute(
                "SELECT 1 FROM policies WHERE policy_uid = ?", (c.uid,)
            ).fetchone()
            if exists:
                print(f"  ~ {c.uid} already present in live, skipping")
                skipped += 1
                continue

            # Resolve program_id on the LIVE db — the backup id may differ.
            live_prog_id = None
            if c.program_uid:
                prow = live.execute(
                    "SELECT id FROM programs WHERE program_uid = ?", (c.program_uid,)
                ).fetchone()
                live_prog_id = prow["id"] if prow else None

            payload: dict[str, object] = {}
            for col in cols:
                if col == "id":
                    continue  # let SQLite reuse the original id or pick a new one
                if col == "program_id":
                    payload[col] = live_prog_id
                elif col in c.row.keys():
                    payload[col] = c.row[col]
                else:
                    payload[col] = None

            # If the original id is free, reuse it for referential stability.
            orig_id = c.row["id"] if "id" in c.row.keys() else None
            if orig_id is not None:
                clash = live.execute(
                    "SELECT 1 FROM policies WHERE id = ?", (orig_id,)
                ).fetchone()
                if not clash:
                    payload = {"id": orig_id, **payload}

            placeholders = ", ".join(["?"] * len(payload))
            col_list = ", ".join(payload.keys())
            live.execute(
                f"INSERT INTO policies ({col_list}) VALUES ({placeholders})",
                list(payload.values()),
            )
            restored += 1
            print(f"  + restored {_describe(c)}")
    except Exception:
        live.rollback()
        live.close()
        raise
    live.commit()
    live.close()
    return restored, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", type=Path, default=LIVE_DB, help="path to live policydb.sqlite"
    )
    parser.add_argument(
        "--backups", type=Path, default=BACKUP_DIR, help="path to backups directory"
    )
    parser.add_argument(
        "--uid", type=str, default=None, help="only consider this policy_uid"
    )
    parser.add_argument(
        "--program",
        type=str,
        default=None,
        help="only recover policies that were linked to this program_uid",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="actually re-INSERT the rows (default: dry run)",
    )
    args = parser.parse_args()

    if not args.live.exists():
        print(f"Live DB not found: {args.live}", file=sys.stderr)
        return 2
    if not args.backups.exists():
        print(f"Backups dir not found: {args.backups}", file=sys.stderr)
        return 2

    print(f"Live:    {args.live}")
    print(f"Backups: {args.backups}")
    print()

    candidates = find_missing_policies(args.live, args.backups, uid_filter=args.uid)
    if args.program:
        candidates = [c for c in candidates if c.program_uid == args.program]

    if not candidates:
        print("Nothing to restore — no policy_uids in backups that are missing from live.")
        return 0

    print(f"Found {len(candidates)} policy row(s) in backups that are missing from live:")
    for c in candidates:
        print(f"  {_describe(c)}  ← {c.source_backup.name}")
    print()

    if not args.restore:
        print("Dry run only. Re-run with --restore to apply.")
        return 0

    restored, skipped = restore(candidates, args.live)
    print()
    print(f"Done. Restored={restored}  Skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
