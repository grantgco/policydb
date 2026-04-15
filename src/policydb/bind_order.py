"""Bind Order — unified renew-and-bind workflow for policies and programs.

This module provides the pure-logic core for the Bind Order action:

    Subject (program OR standalone policy)
            │
            ├── For each CHECKED child:
            │     - if needs_renew: renew_policy() → new term row
            │     - then mark Bound (set bound_date, log activity, complete
            │       milestones, close follow-ups, cascade to program)
            │
            ├── For each UNCHECKED child with a disposition:
            │     - apply disposition (Declined / Lost / Non-Renewed / Defer)
            │
            ├── If subject is a program AND every child terminal:
            │     - update programs.bound_date + renewal_status='Bound'
            │
            ├── Generate post-bind follow-ups ONCE per subject
            │     (program-scoped if program; policy-scoped if standalone)
            │
            └── auto_resolve_renewal_issue() for the subject

The module is FastAPI-free. Routes live in web/routes/bind_order.py.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from policydb import config as cfg
from policydb.queries import (
    get_policy_by_uid,
    get_program_by_uid,
    get_program_child_policies,
    renew_policy,
)
from policydb.renewal_issues import (
    auto_resolve_renewal_issue,
    cascade_program_renewal_close,
)

logger = logging.getLogger("policydb.bind_order")

ChildState = Literal["needs_renew", "ready_to_bind", "already_bound"]
SubjectType = Literal["program", "policy"]
Disposition = Literal["Bound", "Declined", "Lost", "Non-Renewed", "Defer"]


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass
class BindSubject:
    """A subject (program or standalone policy) being bound in this action."""
    subject_type: SubjectType
    subject_uid: str  # program_uid or policy_uid

    @classmethod
    def parse(cls, token: str) -> "BindSubject":
        """Parse a 'program:PGM-042' or 'policy:POL-017' token."""
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
    """One child policy row as displayed in the bind panel."""
    policy_uid: str
    policy_id: int
    policy_type: str | None
    carrier: str | None
    premium: float | None
    renewal_status: str | None
    state: ChildState  # needs_renew | ready_to_bind | already_bound
    effective_date: str | None
    expiration_date: str | None
    bound_date: str | None


@dataclass
class SubjectPanelSection:
    """A section of the bind panel — one program or one standalone policy."""
    subject_type: SubjectType
    subject_uid: str
    subject_id: int
    client_id: int
    display_name: str
    new_effective: str  # ISO date — pre-filled default
    new_expiration: str  # ISO date — pre-filled default
    children: list[ChildPanelRow] = field(default_factory=list)


@dataclass
class BindPanelData:
    """Full payload for rendering the bind panel."""
    bind_date: str  # ISO today
    sections: list[SubjectPanelSection]
    errors: list[str] = field(default_factory=list)


@dataclass
class BindChildPayload:
    """Per-child instruction submitted from the panel."""
    policy_uid: str
    checked: bool
    disposition: str | None  # Bound (default if checked) or one of the exceptions
    new_premium: float | None = None  # only used when checked + override


@dataclass
class BindSubjectPayload:
    """Per-subject submission."""
    subject_type: SubjectType
    subject_uid: str
    new_effective: str  # ISO date
    new_expiration: str  # ISO date
    children: list[BindChildPayload]


@dataclass
class BindOrderPayload:
    """Whole panel submission."""
    bind_date: str  # ISO date
    bind_note: str
    subjects: list[BindSubjectPayload]


@dataclass
class BindOrderResult:
    bound_count: int = 0
    excepted_count: int = 0
    renewed_inline_count: int = 0
    bind_event_ids: list[int] = field(default_factory=list)
    toast_message: str = ""


# ─── State resolution ────────────────────────────────────────────────────────


def resolve_child_state(conn: sqlite3.Connection, policy_uid: str) -> ChildState:
    """Decide whether a policy needs to be renewed before binding, is ready to
    bind directly, or has already been bound for its current term.

    Logic:
      - already_bound: bound_date IS NOT NULL on the current row (this term has
        already had its bind event recorded)
      - ready_to_bind: bound_date IS NULL AND there's no successor row (no
        prior_policy_uid pointing to this row) — i.e., this row IS the new term
        we want to bind. Either the user pre-renewed proactively, or this is the
        natural in-place state if the row's expiration hasn't been rolled.
      - needs_renew: bound_date IS NULL AND the row's expiration_date is in the
        past or the user has explicitly asked us to renew it. In practice we
        treat any non-bound row with no successor as ready_to_bind UNLESS the
        expiration_date has already passed (meaning the row is the *expired*
        term and we need to roll forward).

    The needs_renew vs ready_to_bind distinction at panel-load time is
    informational — the panel shows the user which rows will be touched by
    renew_policy() before bind. Final routing happens in execute_bind_order()
    so the user can change their mind by editing dates in the panel.
    """
    row = conn.execute(
        """SELECT policy_uid, bound_date, expiration_date, archived
           FROM policies WHERE policy_uid = ?""",
        (policy_uid,),
    ).fetchone()
    if not row:
        raise ValueError(f"Policy {policy_uid} not found")
    if row["archived"]:
        return "already_bound"
    if row["bound_date"]:
        return "already_bound"

    # Has any newer row pointing back at this one (i.e., already renewed)?
    successor = conn.execute(
        "SELECT 1 FROM policies WHERE prior_policy_uid = ? AND archived = 0 LIMIT 1",
        (policy_uid,),
    ).fetchone()
    if successor:
        # This row is the OLD term — caller should bind the successor instead.
        # Mark already_bound so the panel skips it (we don't want double-binding).
        return "already_bound"

    # Has the term already lapsed? If so, the row needs to be rolled forward
    # before binding makes sense.
    if row["expiration_date"]:
        try:
            exp = date.fromisoformat(row["expiration_date"])
            if exp < date.today():
                return "needs_renew"
        except ValueError:
            pass

    # Default: this row is current, unbound, and ready to receive the bind event.
    return "ready_to_bind"


# ─── Panel preview ───────────────────────────────────────────────────────────


def _median(values: list[int]) -> int:
    if not values:
        return 365
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) // 2


def _compute_default_dates(children: list[dict]) -> tuple[str, str]:
    """Pick the default new_effective + new_expiration for a subject section.

    new_effective = max(child.expiration_date) — the latest term we need to roll past
    new_expiration = new_effective + median(child term length in days)
    """
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


def _row_to_child(row: dict, state: ChildState) -> ChildPanelRow:
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
    )


def preview_bind_panel(
    conn: sqlite3.Connection, subjects: list[BindSubject]
) -> BindPanelData:
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
                _row_to_child(c, resolve_child_state(conn, c["policy_uid"]))
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
        else:  # policy
            row = get_policy_by_uid(conn, subject.subject_uid)
            if not row:
                errors.append(f"Policy {subject.subject_uid} not found")
                continue
            row_dict = dict(row)
            new_eff, new_exp = _compute_default_dates([row_dict])
            child = _row_to_child(
                row_dict, resolve_child_state(conn, subject.subject_uid)
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

    return BindPanelData(
        bind_date=date.today().isoformat(),
        sections=sections,
        errors=errors,
    )


# ─── Bind execution ──────────────────────────────────────────────────────────


def _next_bind_event_uid(conn: sqlite3.Connection) -> str:
    """Generate the next BIND-NNN uid. Low-frequency event — simple SELECT MAX
    is sufficient (no need for the uid_sequence table)."""
    row = conn.execute(
        """SELECT bind_event_uid FROM bind_events
           WHERE bind_event_uid LIKE 'BIND-%'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if row and row["bind_event_uid"]:
        try:
            n = int(row["bind_event_uid"].split("-", 1)[1])
            return f"BIND-{n + 1:03d}"
        except (ValueError, IndexError):
            pass
    return "BIND-001"


def _sync_policy_to_program_location(
    conn: sqlite3.Connection, policy_uid: str, program: dict
) -> None:
    """Mirror of programs.py:_sync_policy_to_program_location to avoid circular
    import. Sets project_id + project_name + exposure address from the program's
    linked project, using COALESCE so blank project values don't overwrite."""
    if not program.get("project_id"):
        return
    project = conn.execute(
        "SELECT id, name, address, city, state, zip FROM projects WHERE id = ?",
        (program["project_id"],),
    ).fetchone()
    if not project:
        return
    conn.execute(
        """UPDATE policies SET
              project_id = ?,
              project_name = ?,
              exposure_address = COALESCE(NULLIF(?, ''), exposure_address),
              exposure_city = COALESCE(NULLIF(?, ''), exposure_city),
              exposure_state = COALESCE(NULLIF(?, ''), exposure_state),
              exposure_zip = COALESCE(NULLIF(?, ''), exposure_zip)
           WHERE policy_uid = ?""",
        (
            project["id"],
            project["name"],
            project["address"] or "",
            project["city"] or "",
            project["state"] or "",
            project["zip"] or "",
            policy_uid,
        ),
    )


def _copy_tower_mappings(
    conn: sqlite3.Connection, old_to_new_id: dict[int, int]
) -> None:
    """Mirror of programs.py:renew_program tower copy logic — remap excess +
    underlying policy ids to the new term rows."""
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
            except Exception:
                pass

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
            except Exception:
                pass


def _handle_bound_transition(
    conn: sqlite3.Connection,
    policy_uid: str,
    bind_date: str,
    bind_note: str | None = None,
    bind_event_id: int | None = None,
) -> None:
    """Single choke point for marking a single policy bound. Executes the
    per-policy cascade — does NOT generate post-bind follow-ups (those are
    generated once per subject by the caller)."""
    uid = policy_uid.upper()
    pol = conn.execute(
        """SELECT id, client_id, policy_uid, is_opportunity, program_id
           FROM policies WHERE policy_uid = ?""",
        (uid,),
    ).fetchone()
    if not pol:
        raise ValueError(f"Policy {uid} not found")
    if pol["is_opportunity"]:
        # Mirror policy_bound_confirm() current behavior — opportunities are skipped.
        return

    # 1. Status + bound_date + updated_at
    conn.execute(
        """UPDATE policies
           SET renewal_status = 'Bound',
               bound_date = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE policy_uid = ?""",
        (bind_date, uid),
    )

    # 2. Log "Renewal bound" activity on the policy
    details = bind_note if bind_note else None
    bind_event_note = f" [bind_event_id={bind_event_id}]" if bind_event_id else ""
    full_details = (details or "") + bind_event_note
    conn.execute(
        """INSERT INTO activity_log
           (client_id, policy_id, activity_type, subject, details, activity_date, created_at)
           VALUES (?, ?, 'Milestone', 'Renewal bound', ?, ?, datetime('now'))""",
        (pol["client_id"], pol["id"], full_details.strip() or None, bind_date),
    )

    # 3. Complete remaining timeline milestones (mirror current policy_bound_confirm:
    #    skip if program child — those milestones live at program level)
    if not pol["program_id"]:
        from policydb.timeline_engine import complete_timeline_milestone
        incomplete = conn.execute(
            "SELECT milestone_name FROM policy_timeline WHERE policy_uid = ? AND completed_date IS NULL",
            (uid,),
        ).fetchall()
        for m in incomplete:
            try:
                complete_timeline_milestone(conn, uid, m["milestone_name"])
            except Exception as exc:
                logger.warning("Timeline milestone completion failed for %s/%s: %s",
                               uid, m["milestone_name"], exc)

    # 4. Close all open follow-ups on this policy
    conn.execute(
        """UPDATE activity_log
           SET follow_up_done = 1,
               auto_close_reason = 'renewal_bound',
               auto_closed_at = datetime('now'),
               auto_closed_by = 'bind_order'
           WHERE policy_id = ? AND follow_up_done = 0 AND follow_up_date IS NOT NULL""",
        (pol["id"],),
    )

    # 5. Cascade to program (if any) + auto-resolve renewal issue at policy scope
    if pol["program_id"]:
        cascade_program_renewal_close(conn, uid)
    auto_resolve_renewal_issue(conn, policy_uid=uid)


def _generate_post_bind_followups(
    conn: sqlite3.Connection,
    *,
    client_id: int,
    bind_date: str,
    program_id: int | None = None,
    policy_id: int | None = None,
) -> int:
    """Insert one follow-up activity_log row per item in
    config.post_bind_activities. Returns count inserted."""
    if program_id is None and policy_id is None:
        raise ValueError("Must provide program_id or policy_id")

    items = cfg.get("post_bind_activities", []) or []
    if not items:
        return 0

    try:
        bind_dt = date.fromisoformat(bind_date)
    except ValueError:
        bind_dt = date.today()

    inserted = 0
    for item in items:
        try:
            offset_days = int(item.get("days_after_bind", 0))
        except (TypeError, ValueError):
            offset_days = 0
        fu_date = (bind_dt + timedelta(days=offset_days)).isoformat()
        subject = item.get("subject") or item.get("name") or "Post-bind follow-up"
        activity_type = item.get("activity_type") or "Follow-up"

        conn.execute(
            """INSERT INTO activity_log
               (client_id, policy_id, program_id, activity_type, subject,
                activity_date, follow_up_date, follow_up_done, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))""",
            (
                client_id,
                policy_id,
                program_id,
                activity_type,
                subject,
                bind_date,
                fu_date,
            ),
        )
        inserted += 1
    return inserted


def execute_bind_order(
    conn: sqlite3.Connection, payload: BindOrderPayload
) -> BindOrderResult:
    """Apply a bind order across one or more subjects in a single transaction."""
    result = BindOrderResult()
    bind_date = payload.bind_date or date.today().isoformat()
    bind_note = (payload.bind_note or "").strip() or None
    terminal_for_program = set(cfg.get("renewal_terminal_statuses", ["Bound", "Lost", "Non-Renewed", "Declined"]))

    for sp in payload.subjects:
        # Resolve the subject row
        if sp.subject_type == "program":
            program = get_program_by_uid(conn, sp.subject_uid)
            if not program:
                raise ValueError(f"Program {sp.subject_uid} not found")
            subject_id = program["id"]
            client_id = program["client_id"]
        else:
            pol = conn.execute(
                "SELECT id, client_id FROM policies WHERE policy_uid = ?",
                (sp.subject_uid,),
            ).fetchone()
            if not pol:
                raise ValueError(f"Policy {sp.subject_uid} not found")
            subject_id = pol["id"]
            client_id = pol["client_id"]
            program = None

        # 1. Insert the bind_events row
        bind_event_uid = _next_bind_event_uid(conn)
        cur = conn.execute(
            """INSERT INTO bind_events
               (bind_event_uid, bind_date, subject_type, subject_id, subject_uid,
                client_id, bind_note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                bind_event_uid,
                bind_date,
                sp.subject_type,
                subject_id,
                sp.subject_uid,
                client_id,
                bind_note,
            ),
        )
        bind_event_id = cur.lastrowid
        result.bind_event_ids.append(bind_event_id)

        old_to_new_id: dict[int, int] = {}
        bound_in_subject = 0
        excepted_in_subject = 0
        total_premium_bound = 0.0
        all_terminal = True

        # 2. Process each child
        for ch in sp.children:
            old_uid = ch.policy_uid.upper()
            if not ch.checked:
                # Disposition path — apply to the existing (expiring) row
                disposition = (ch.disposition or "Defer").strip()
                if disposition == "Defer":
                    # Leave row untouched, but record the bind_event_children entry
                    conn.execute(
                        """INSERT INTO bind_event_children
                           (bind_event_id, old_policy_uid, new_policy_uid,
                            renewed_inline, disposition)
                           VALUES (?, ?, ?, 0, ?)""",
                        (bind_event_id, old_uid, old_uid, "Defer"),
                    )
                    excepted_in_subject += 1
                    all_terminal = False
                    continue
                # Apply terminal status
                conn.execute(
                    """UPDATE policies
                       SET renewal_status = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE policy_uid = ?""",
                    (disposition, old_uid),
                )
                # Log activity capturing the disposition
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
                            pol_row["client_id"],
                            pol_row["id"],
                            f"Renewal {disposition.lower()}",
                            f"Disposition recorded via Bind Order [bind_event_id={bind_event_id}]",
                            bind_date,
                        ),
                    )
                conn.execute(
                    """INSERT INTO bind_event_children
                       (bind_event_id, old_policy_uid, new_policy_uid,
                        renewed_inline, disposition)
                       VALUES (?, ?, ?, 0, ?)""",
                    (bind_event_id, old_uid, old_uid, disposition),
                )
                excepted_in_subject += 1
                if disposition not in terminal_for_program:
                    all_terminal = False
                continue

            # CHECKED path — bind it (renewing first if needed)
            state = resolve_child_state(conn, old_uid)
            renewed_inline = 0
            new_uid = old_uid

            if state == "needs_renew":
                old_row = conn.execute(
                    "SELECT id FROM policies WHERE policy_uid = ?", (old_uid,)
                ).fetchone()
                old_id = old_row["id"] if old_row else None
                new_uid = renew_policy(
                    conn,
                    old_uid,
                    new_effective=sp.new_effective,
                    new_expiration=sp.new_expiration,
                    new_premium=ch.new_premium,
                )
                renewed_inline = 1
                result.renewed_inline_count += 1

                # Re-link the new row to its program (renew_policy doesn't copy program_id)
                if program:
                    new_row = conn.execute(
                        "SELECT id, schematic_column FROM policies WHERE policy_uid = ?",
                        (new_uid,),
                    ).fetchone()
                    if new_row:
                        # Look up the OLD row's program metadata to preserve schematic_column
                        old_meta = conn.execute(
                            """SELECT tower_group, schematic_column
                               FROM policies WHERE id = ?""",
                            (old_id,),
                        ).fetchone() if old_id else None
                        conn.execute(
                            """UPDATE policies
                               SET program_id = ?,
                                   tower_group = ?,
                                   schematic_column = ?
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
            elif ch.new_premium is not None:
                # ready_to_bind but user edited premium in the panel — apply the override
                conn.execute(
                    "UPDATE policies SET premium = ? WHERE policy_uid = ?",
                    (ch.new_premium, old_uid),
                )

            # Mark bound (single choke point)
            _handle_bound_transition(conn, new_uid, bind_date, bind_note, bind_event_id)

            # Capture details for the bind_event_children row + premium total
            new_row_full = conn.execute(
                """SELECT id, premium, effective_date, expiration_date
                   FROM policies WHERE policy_uid = ?""",
                (new_uid,),
            ).fetchone()
            if new_row_full and new_row_full["premium"]:
                try:
                    total_premium_bound += float(new_row_full["premium"])
                except (TypeError, ValueError):
                    pass
            conn.execute(
                """INSERT INTO bind_event_children
                   (bind_event_id, old_policy_uid, new_policy_uid, renewed_inline,
                    disposition, bound_effective_date, bound_expiration_date, bound_premium)
                   VALUES (?, ?, ?, ?, 'Bound', ?, ?, ?)""",
                (
                    bind_event_id,
                    old_uid,
                    new_uid,
                    renewed_inline,
                    new_row_full["effective_date"] if new_row_full else None,
                    new_row_full["expiration_date"] if new_row_full else None,
                    new_row_full["premium"] if new_row_full else None,
                ),
            )
            bound_in_subject += 1

        # 3. Copy tower mappings if any children were renewed inline (program path only)
        if program and old_to_new_id:
            _copy_tower_mappings(conn, old_to_new_id)

        # 4. Update the program-level row if every child terminal
        if sp.subject_type == "program" and all_terminal and bound_in_subject > 0:
            conn.execute(
                """UPDATE programs
                   SET bound_date = ?,
                       renewal_status = 'Bound',
                       updated_at = CURRENT_TIMESTAMP
                   WHERE program_uid = ?""",
                (bind_date, sp.subject_uid),
            )

        # 5. Generate post-bind follow-ups ONCE per subject
        if bound_in_subject > 0:
            if sp.subject_type == "program":
                _generate_post_bind_followups(
                    conn,
                    client_id=client_id,
                    bind_date=bind_date,
                    program_id=subject_id,
                )
            else:
                # Standalone — anchor on the (possibly new) policy row
                # If we renewed inline, prefer the new row's id
                if old_to_new_id:
                    new_id = next(iter(old_to_new_id.values()))
                    target_policy_id = new_id
                else:
                    # Re-fetch in case the bound child id differs from subject_id
                    target_policy_id = subject_id
                _generate_post_bind_followups(
                    conn,
                    client_id=client_id,
                    bind_date=bind_date,
                    policy_id=target_policy_id,
                )

        # 6. Auto-resolve the subject's renewal issue
        if sp.subject_type == "program":
            auto_resolve_renewal_issue(conn, program_uid=sp.subject_uid)
        else:
            auto_resolve_renewal_issue(conn, policy_uid=sp.subject_uid)

        # 7. Update the counts on bind_events row
        conn.execute(
            """UPDATE bind_events
               SET policy_count_bound = ?,
                   policy_count_excepted = ?,
                   total_premium = ?
               WHERE id = ?""",
            (bound_in_subject, excepted_in_subject, total_premium_bound, bind_event_id),
        )

        result.bound_count += bound_in_subject
        result.excepted_count += excepted_in_subject

    conn.commit()

    # Compose toast message
    parts = []
    if result.bound_count:
        parts.append(f"Bound {result.bound_count}")
    if result.excepted_count:
        parts.append(f"{result.excepted_count} excepted")
    if result.renewed_inline_count:
        parts.append(f"{result.renewed_inline_count} renewed inline")
    result.toast_message = (
        " — ".join(parts) + " — see Action Center for post-bind follow-ups."
        if parts
        else "Bind order complete."
    )

    logger.info(
        "Bind order complete: %d bound, %d excepted, %d renewed inline (events=%s)",
        result.bound_count, result.excepted_count, result.renewed_inline_count,
        result.bind_event_ids,
    )
    return result
