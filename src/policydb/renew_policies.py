"""Renew Policies — batch renewal workflow (successor to Bind Order).

Creates new term rows for one or more subjects (programs or standalone
policies). This module does NOT mark the new terms bound — marking bound is a
separate per-policy action that happens later when a binder is received; see
policy_bind.py for that lifecycle transition.

    Subject (program OR standalone policy)
            │
            ├── For each CHECKED child:
            │     - renew_policy() → archive old term, create new term row
            │     - relink new row to program (if applicable)
            │     - copy tower mappings (program path)
            │     - generate renewal timeline milestones on the new row
            │
            ├── For each UNCHECKED child with a non-Defer disposition:
            │     - apply disposition (Declined / Lost / Non-Renewed) to old row
            │     - log activity capturing disposition
            │
            └── Record to renewal_batches + auto_resolve_renewal_issue(subject)

The module is FastAPI-free. Routes live in web/routes/renew_policies.py.
See .claude/plans/snappy-strolling-fountain.md for context.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from policydb import config as cfg
from policydb.queries import (
    get_policy_by_uid,
    get_program_by_uid,
    get_program_child_policies,
    renew_policy,
)
from policydb.renewal_issues import auto_resolve_renewal_issue

logger = logging.getLogger("policydb.renew_policies")

RenewalState = Literal["needs_renew", "ready_to_renew", "already_renewed"]
SubjectType = Literal["program", "policy"]
Disposition = Literal["Renew", "Declined", "Lost", "Non-Renewed", "Defer"]


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass
class RenewSubject:
    subject_type: SubjectType
    subject_uid: str

    @classmethod
    def parse(cls, token: str) -> "RenewSubject":
        if ":" not in token:
            raise ValueError(f"Invalid subject token (missing ':'): {token}")
        kind, uid = token.split(":", 1)
        kind = kind.strip().lower()
        uid = uid.strip().upper()
        if kind not in ("program", "policy"):
            raise ValueError(f"Unknown subject type: {kind}")
        if not uid:
            raise ValueError(f"Empty uid in subject token: {token}")
        return cls(subject_type=kind, subject_uid=uid)  # type: ignore[arg-type]

    def to_token(self) -> str:
        return f"{self.subject_type}:{self.subject_uid}"


@dataclass
class ChildPanelRow:
    policy_uid: str
    policy_id: int
    policy_type: str | None
    carrier: str | None
    premium: float | None
    renewal_status: str | None
    state: RenewalState
    effective_date: str | None
    expiration_date: str | None
    bound_date: str | None
    is_opportunity: bool = False


@dataclass
class SubjectPanelSection:
    subject_type: SubjectType
    subject_uid: str
    subject_id: int
    client_id: int
    display_name: str
    new_effective: str
    new_expiration: str
    children: list[ChildPanelRow] = field(default_factory=list)


@dataclass
class RenewPanelData:
    bind_date: str  # ISO today — kept for backward-compat with panel template
    sections: list[SubjectPanelSection]
    errors: list[str] = field(default_factory=list)


@dataclass
class RenewChildPayload:
    policy_uid: str
    checked: bool
    disposition: str | None
    new_premium: float | None = None


@dataclass
class RenewSubjectPayload:
    subject_type: SubjectType
    subject_uid: str
    new_effective: str
    new_expiration: str
    children: list[RenewChildPayload]


@dataclass
class RenewPayload:
    bind_date: str  # kept for back-compat with shared panel template
    bind_note: str
    subjects: list[RenewSubjectPayload]


@dataclass
class CreateRenewalsResult:
    new_uids: list[str] = field(default_factory=list)
    excepted_count: int = 0
    skipped_already_renewed: list[str] = field(default_factory=list)
    batch_ids: list[int] = field(default_factory=list)
    toast_message: str = ""


# ─── State resolution ────────────────────────────────────────────────────────


def resolve_renewal_state(conn: sqlite3.Connection, policy_uid: str) -> RenewalState:
    """Classify a policy for the renew panel UI.

    Returns:
        already_renewed — archived, bound_date set, or a successor row exists.
            The panel disables the checkbox so the row can't be renewed twice.
        needs_renew — bound_date is null and expiration has already lapsed.
        ready_to_renew — default; term is current and unbound.

    This is a UI-level label. execute_create_renewals() calls renew_policy()
    on every checked, non-already_renewed child regardless of the label.
    """
    row = conn.execute(
        """SELECT policy_uid, bound_date, expiration_date, archived
           FROM policies WHERE policy_uid = ?""",
        (policy_uid,),
    ).fetchone()
    if not row:
        raise ValueError(f"Policy {policy_uid} not found")
    if row["archived"]:
        return "already_renewed"
    if row["bound_date"]:
        return "already_renewed"

    successor = conn.execute(
        "SELECT 1 FROM policies WHERE prior_policy_uid = ? AND archived = 0 LIMIT 1",
        (policy_uid,),
    ).fetchone()
    if successor:
        return "already_renewed"

    if row["expiration_date"]:
        try:
            exp = date.fromisoformat(row["expiration_date"])
            if exp < date.today():
                return "needs_renew"
        except ValueError:
            pass

    return "ready_to_renew"


# ─── Panel preview ───────────────────────────────────────────────────────────


def _median(values: list[int]) -> int:
    if not values:
        return 365
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) // 2


def _compute_default_dates(children: list[dict]) -> tuple[str, str]:
    today = date.today()
    expirations: list[date] = []
    term_lengths: list[int] = []
    for c in children:
        if c.get("expiration_date"):
            try:
                exp = date.fromisoformat(c["expiration_date"])
                expirations.append(exp)
                if c.get("effective_date"):
                    eff = date.fromisoformat(c["effective_date"])
                    term_lengths.append((exp - eff).days)
            except ValueError:
                continue

    new_eff = max(expirations) if expirations else today
    term_days = _median(term_lengths) if term_lengths else 365
    new_exp = new_eff + timedelta(days=term_days)
    return new_eff.isoformat(), new_exp.isoformat()


def _row_to_child(row: dict, state: RenewalState) -> ChildPanelRow:
    return ChildPanelRow(
        policy_uid=row["policy_uid"],
        policy_id=row["id"],
        policy_type=row.get("policy_type"),
        carrier=row.get("carrier"),
        premium=row.get("premium"),
        renewal_status=row.get("renewal_status"),
        state=state,
        effective_date=row.get("effective_date"),
        expiration_date=row.get("expiration_date"),
        bound_date=row.get("bound_date"),
        is_opportunity=bool(row.get("is_opportunity") or 0),
    )


def preview_renew_panel(
    conn: sqlite3.Connection, subjects: list[RenewSubject]
) -> RenewPanelData:
    """Build the panel preview for the given subjects."""
    sections: list[SubjectPanelSection] = []
    errors: list[str] = []

    for subject in subjects:
        if subject.subject_type == "program":
            program = get_program_by_uid(conn, subject.subject_uid)
            if not program:
                errors.append(f"Program {subject.subject_uid} not found")
                continue
            children_rows = get_program_child_policies(conn, program["id"])
            if not children_rows:
                errors.append(f"Program {subject.subject_uid} has no active children")
                continue
            new_eff, new_exp = _compute_default_dates(children_rows)
            children = [
                _row_to_child(c, resolve_renewal_state(conn, c["policy_uid"]))
                for c in children_rows
            ]
            sections.append(SubjectPanelSection(
                subject_type="program",
                subject_uid=subject.subject_uid,
                subject_id=program["id"],
                client_id=program["client_id"],
                display_name=program.get("name") or subject.subject_uid,
                new_effective=new_eff,
                new_expiration=new_exp,
                children=children,
            ))
        else:
            row = get_policy_by_uid(conn, subject.subject_uid)
            if not row:
                errors.append(f"Policy {subject.subject_uid} not found")
                continue
            row_dict = dict(row)
            new_eff, new_exp = _compute_default_dates([row_dict])
            child = _row_to_child(
                row_dict, resolve_renewal_state(conn, subject.subject_uid)
            )
            sections.append(SubjectPanelSection(
                subject_type="policy",
                subject_uid=subject.subject_uid,
                subject_id=row_dict["id"],
                client_id=row_dict["client_id"],
                display_name=row_dict.get("policy_type") or subject.subject_uid,
                new_effective=new_eff,
                new_expiration=new_exp,
                children=[child],
            ))

    return RenewPanelData(
        bind_date=date.today().isoformat(),
        sections=sections,
        errors=errors,
    )


# ─── Helpers: program linking + tower copy (mirrored from legacy path) ────────


def _sync_policy_to_program_location(
    conn: sqlite3.Connection, policy_uid: str, program: dict
) -> None:
    if not program.get("project_id"):
        return
    project = conn.execute(
        "SELECT id, name, address, city, state, zip FROM projects WHERE id = ?",
        (program["project_id"],),
    ).fetchone()
    if not project:
        return
    conn.execute(
        """UPDATE policies SET project_id = ?, project_name = ? WHERE policy_uid = ?""",
        (project["id"], project["name"], policy_uid),
    )


def _copy_tower_mappings(
    conn: sqlite3.Connection, old_to_new_id: dict[int, int]
) -> None:
    if not old_to_new_id:
        return
    placeholders = ",".join(str(k) for k in old_to_new_id.keys())

    coverages = conn.execute(
        f"""SELECT * FROM program_tower_coverage
            WHERE excess_policy_id IN ({placeholders})"""  # noqa: S608
    ).fetchall()
    for cov in coverages:
        new_excess = old_to_new_id.get(cov["excess_policy_id"])
        new_underlying = old_to_new_id.get(cov["underlying_policy_id"]) if cov["underlying_policy_id"] else None
        if new_excess:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO program_tower_coverage
                       (excess_policy_id, underlying_policy_id, underlying_sub_coverage_id)
                       VALUES (?, ?, ?)""",
                    (
                        new_excess,
                        new_underlying or cov["underlying_policy_id"],
                        cov["underlying_sub_coverage_id"],
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Tower coverage carry-forward failed for excess %s → %s: %s",
                    cov["excess_policy_id"], new_excess, exc,
                )

    lines = conn.execute(
        f"""SELECT * FROM program_tower_lines
            WHERE source_policy_id IN ({placeholders})"""  # noqa: S608
    ).fetchall()
    for line in lines:
        new_source = old_to_new_id.get(line["source_policy_id"])
        if new_source:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO program_tower_lines
                       (program_policy_id, source_policy_id, sub_coverage_id,
                        label, include_in_tower, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        line["program_policy_id"],
                        new_source,
                        line["sub_coverage_id"],
                        line["label"],
                        line["include_in_tower"],
                        line["sort_order"],
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Tower line carry-forward failed for source %s → %s: %s",
                    line["source_policy_id"], new_source, exc,
                )


# ─── Execution ──────────────────────────────────────────────────────────────


def execute_create_renewals(
    conn: sqlite3.Connection, payload: RenewPayload
) -> CreateRenewalsResult:
    """Create new term rows for every checked child across all subjects.

    Unlike the legacy execute_bind_order, this does NOT set bound_date, does NOT
    generate post-bind follow-ups, and does NOT mark the program 'Bound'. The
    new rows start in 'Not Started' status and are ready to be edited in the
    batch edit grid. Marking bound happens later via policy_bind.mark_policy_bound.

    Raises:
        ValueError if there's nothing actionable in the payload.
    """
    result = CreateRenewalsResult()
    terminal_dispositions = {"Declined", "Lost", "Non-Renewed"}

    # Guard: reject zero-work submissions up front (the panel normally prevents
    # this, but a stale DOM or direct API call could slip through).
    actionable = 0
    for sp in payload.subjects:
        for ch in sp.children:
            if ch.checked:
                actionable += 1
            elif (ch.disposition or "").strip() in terminal_dispositions:
                actionable += 1
    if actionable == 0:
        raise ValueError(
            "Nothing to renew — all rows are already renewed, archived, or deferred."
        )

    for sp in payload.subjects:
        # Resolve the subject row
        if sp.subject_type == "program":
            program = get_program_by_uid(conn, sp.subject_uid)
            if not program:
                raise ValueError(f"Program {sp.subject_uid} not found")
        else:
            pol = conn.execute(
                "SELECT id, client_id FROM policies WHERE policy_uid = ?",
                (sp.subject_uid,),
            ).fetchone()
            if not pol:
                raise ValueError(f"Policy {sp.subject_uid} not found")
            program = None

        old_to_new_id: dict[int, int] = {}
        subject_new_uids: list[str] = []
        subject_excepted = 0

        for ch in sp.children:
            old_uid = ch.policy_uid.upper()

            if not ch.checked:
                # Disposition path — apply terminal status to the old row,
                # skip Defer entirely (no row change).
                disposition = (ch.disposition or "Defer").strip()
                if disposition not in terminal_dispositions:
                    continue
                conn.execute(
                    """UPDATE policies
                       SET renewal_status = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE policy_uid = ?""",
                    (disposition, old_uid),
                )
                pol_row = conn.execute(
                    "SELECT id, client_id FROM policies WHERE policy_uid = ?", (old_uid,)
                ).fetchone()
                if pol_row:
                    conn.execute(
                        """INSERT INTO activity_log
                           (client_id, policy_id, activity_type, subject, details,
                            activity_date, created_at)
                           VALUES (?, ?, 'Milestone', ?, ?, ?, datetime('now'))""",
                        (
                            pol_row["client_id"], pol_row["id"],
                            f"Renewal {disposition.lower()}",
                            "Disposition recorded via Renew Policies",
                            payload.bind_date or date.today().isoformat(),
                        ),
                    )
                subject_excepted += 1
                continue

            # CHECKED — create a new term row via the canonical renew_policy().
            # already_renewed rows normally can't be submitted (the panel
            # disables their checkbox), but a stale DOM or direct API call
            # could slip through. Track the skip so the response is explicit
            # rather than a silent "success with zero new terms".
            state = resolve_renewal_state(conn, old_uid)
            if state == "already_renewed":
                logger.warning("Skipping renewal for %s: already renewed", old_uid)
                result.skipped_already_renewed.append(old_uid)
                continue

            old_row = conn.execute(
                "SELECT id FROM policies WHERE policy_uid = ?", (old_uid,)
            ).fetchone()
            old_id = old_row["id"] if old_row else None

            # commit=False so the whole batch lives in one transaction — if a
            # later subject fails we roll back everything via the try/except
            # in the route handler.
            new_uid = renew_policy(
                conn,
                old_uid,
                new_effective=sp.new_effective,
                new_expiration=sp.new_expiration,
                new_premium=ch.new_premium,
                commit=False,
            )
            subject_new_uids.append(new_uid)

            # Relink the new row to its program (renew_policy doesn't carry program_id)
            if program:
                new_row = conn.execute(
                    "SELECT id FROM policies WHERE policy_uid = ?", (new_uid,),
                ).fetchone()
                if new_row:
                    old_meta = conn.execute(
                        "SELECT tower_group, schematic_column FROM policies WHERE id = ?",
                        (old_id,),
                    ).fetchone() if old_id else None
                    conn.execute(
                        """UPDATE policies
                           SET program_id = ?, tower_group = ?, schematic_column = ?
                           WHERE id = ?""",
                        (
                            program["id"],
                            program.get("name"),
                            old_meta["schematic_column"] if old_meta else None,
                            new_row["id"],
                        ),
                    )
                    if old_id:
                        old_to_new_id[old_id] = new_row["id"]
                    _sync_policy_to_program_location(conn, new_uid, program)

            # Fresh renewal timeline so the new term shows next-cycle milestones
            try:
                from policydb.timeline_engine import generate_policy_timelines
                generate_policy_timelines(conn, new_uid)
            except Exception as e:
                logger.warning("Failed to generate timeline for new term %s: %s", new_uid, e)

        # Copy tower mappings if any program children were renewed
        if program and old_to_new_id:
            _copy_tower_mappings(conn, old_to_new_id)

        # Record the batch
        if subject_new_uids or subject_excepted:
            cur = conn.execute(
                """INSERT INTO renewal_batches
                   (subject_token, new_uids_json, excepted_count)
                   VALUES (?, ?, ?)""",
                (
                    f"{sp.subject_type}:{sp.subject_uid}",
                    json.dumps(subject_new_uids),
                    subject_excepted,
                ),
            )
            result.batch_ids.append(cur.lastrowid)

        # Auto-resolve renewal issue (per user: on creation, not on bind)
        try:
            if sp.subject_type == "program":
                auto_resolve_renewal_issue(conn, program_uid=sp.subject_uid)
            else:
                auto_resolve_renewal_issue(conn, policy_uid=sp.subject_uid)
        except Exception as exc:
            logger.warning("auto_resolve_renewal_issue failed for %s: %s", sp.subject_uid, exc)

        result.new_uids.extend(subject_new_uids)
        result.excepted_count += subject_excepted

    conn.commit()

    parts = []
    if result.new_uids:
        parts.append(
            f"Created {len(result.new_uids)} new term"
            + ("s" if len(result.new_uids) != 1 else "")
        )
    if result.excepted_count:
        parts.append(f"{result.excepted_count} excepted")
    if result.skipped_already_renewed:
        parts.append(
            f"{len(result.skipped_already_renewed)} skipped (already renewed)"
        )
    if result.new_uids:
        result.toast_message = " — ".join(parts) + " — now edit the new-term fields."
    elif parts:
        result.toast_message = " — ".join(parts) + "."
    else:
        result.toast_message = "No renewals created."

    logger.info(
        "Renewal batch complete: %d new terms, %d excepted (batches=%s)",
        len(result.new_uids), result.excepted_count, result.batch_ids,
    )
    return result
