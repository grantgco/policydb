"""7-step onboarding workflow for new clients."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from policydb import config as cfg
from policydb.analysis import build_program_audit
from policydb.db import next_policy_uid
from policydb.display import fmt_currency, console as display_console
from policydb.importer import PolicyImporter, _parse_date, _parse_currency
from policydb.queries import get_client_by_name

_console = Console()


def _prompt_client_name(default: str | None = None) -> str:
    prompt = "Client name"
    if default:
        return click.prompt(prompt, default=default)
    return click.prompt(prompt)


def _create_client(conn: sqlite3.Connection, name: str) -> int:
    industry = click.prompt(
        "Industry segment",
        type=click.Choice(cfg.get("industry_segments"), case_sensitive=False),
    )
    primary_contact = click.prompt("Primary contact (optional)", default="", show_default=False)
    contact_email = click.prompt("Contact email (optional)", default="", show_default=False)
    account_exec = cfg.get("default_account_exec", "Grant")

    cursor = conn.execute(
        """INSERT INTO clients
           (name, industry_segment, primary_contact, contact_email, account_exec)
           VALUES (?, ?, ?, ?, ?)""",
        (
            name,
            industry,
            primary_contact or None,
            contact_email or None,
            account_exec,
        ),
    )
    conn.commit()
    click.echo(f"  Created client: {name} (id={cursor.lastrowid})")
    return cursor.lastrowid


def _interactive_policy_entry(conn: sqlite3.Connection, client_id: int) -> int:
    """Guided interactive policy entry. Returns count of policies added."""
    count = 0
    policy_types = cfg.get("policy_types")
    account_exec = cfg.get("default_account_exec", "Grant")

    click.echo("\nEnter policies one at a time. Press Ctrl-C or type 'done' to finish.\n")

    while True:
        try:
            click.echo(f"  — Policy #{count + 1} —")
            pol_type = click.prompt(
                "  Line of business",
                type=click.Choice(policy_types, case_sensitive=False),
            )
            carrier = click.prompt("  Carrier")
            effective = click.prompt("  Effective date (YYYY-MM-DD)")
            expiration = click.prompt("  Expiration date (YYYY-MM-DD)")
            premium = click.prompt("  Annual premium", type=float)

            eff_parsed = _parse_date(effective)
            exp_parsed = _parse_date(expiration)
            if not eff_parsed or not exp_parsed:
                click.echo("  [!] Invalid date format. Try YYYY-MM-DD.")
                continue

            uid = next_policy_uid(conn)

            conn.execute(
                """INSERT INTO policies
                   (policy_uid, client_id, policy_type, carrier, effective_date,
                    expiration_date, premium, account_exec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, client_id, pol_type, carrier, eff_parsed, exp_parsed, premium, account_exec),
            )
            conn.commit()
            count += 1
            click.echo(f"  Saved as {uid}\n")

            # Optional detail pass
            if click.confirm("  Add more detail now (limit, description, layer, etc.)?", default=False):
                limit = click.prompt("  Policy limit (0 if none)", type=float, default=0.0)
                description = click.prompt("  Description (client-facing)", default="", show_default=False)
                layer = click.prompt("  Layer position", default="Primary")
                tower = click.prompt("  Tower group (optional)", default="", show_default=False)
                pol_number = click.prompt("  Policy number (optional)", default="", show_default=False)
                colleague = click.prompt("  Placement colleague (optional)", default="", show_default=False)

                conn.execute(
                    """UPDATE policies SET
                       limit_amount = ?, description = ?, layer_position = ?,
                       tower_group = ?, policy_number = ?, placement_colleague = ?
                       WHERE policy_uid = ?""",
                    (
                        limit or None,
                        description or None,
                        layer or "Primary",
                        tower or None,
                        pol_number or None,
                        colleague or None,
                        uid,
                    ),
                )
                conn.commit()

            if not click.confirm("\n  Add another policy?", default=True):
                break

        except (KeyboardInterrupt, EOFError):
            click.echo("\n  Stopping policy entry.")
            break

    return count


def _description_pass(conn: sqlite3.Connection, client_id: int) -> None:
    """Offer to fill in missing descriptions."""
    rows = conn.execute(
        """SELECT policy_uid, policy_type, carrier
           FROM policies WHERE client_id = ? AND (description IS NULL OR description = '') AND archived = 0""",
        (client_id,),
    ).fetchall()

    if not rows:
        return

    n = len(rows)
    click.echo(f"\n{n} {'policy' if n == 1 else 'policies'} missing descriptions.")
    click.echo("Descriptions appear on client-facing schedules of insurance.")
    if not click.confirm("Add descriptions now?", default=True):
        return

    for r in rows:
        click.echo(f"\n  {r['policy_uid']}: {r['policy_type']} / {r['carrier']}")
        desc = click.prompt("  Description (client-facing, or Enter to skip)", default="", show_default=False)
        if desc.strip():
            conn.execute(
                "UPDATE policies SET description = ? WHERE policy_uid = ?",
                (desc.strip(), r["policy_uid"]),
            )
    conn.commit()


def _renewal_setup(conn: sqlite3.Connection, client_id: int) -> None:
    """Flag near-term renewals and prompt for status + colleague assignment."""
    from datetime import date
    near_term = conn.execute(
        """SELECT policy_uid, policy_type, carrier, expiration_date,
                  CAST(julianday(expiration_date) - julianday('now') AS INTEGER) AS days
           FROM policies
           WHERE client_id = ? AND archived = 0
             AND julianday(expiration_date) - julianday('now') <= 180
           ORDER BY expiration_date""",
        (client_id,),
    ).fetchall()

    if not near_term:
        return

    click.echo(f"\n{len(near_term)} {'policy' if len(near_term) == 1 else 'policies'} expire within 180 days.")
    if not click.confirm("Configure renewal assignments now?", default=True):
        return

    valid_statuses = ["Not Started", "In Progress", "Pending Bind", "Bound"]

    for r in near_term:
        click.echo(f"\n  {r['policy_uid']}: {r['policy_type']} / {r['carrier']} — expires {r['expiration_date']} ({r['days']}d)")
        status = click.prompt(
            "  Renewal status",
            type=click.Choice(valid_statuses, case_sensitive=False),
            default="Not Started",
        )
        colleague = click.prompt(
            "  Placement colleague (optional)",
            default="",
            show_default=False,
        )
        conn.execute(
            "UPDATE policies SET renewal_status = ?, placement_colleague = ? WHERE policy_uid = ?",
            (status, colleague or None, r["policy_uid"]),
        )
    conn.commit()


def _log_initial_activity(conn: sqlite3.Connection, client_id: int) -> None:
    """Prompt to log the onboarding activity."""
    if not click.confirm("\nLog the onboarding meeting/call?", default=True):
        return

    account_exec = cfg.get("default_account_exec", "Grant")
    activity_type = click.prompt(
        "Activity type",
        type=click.Choice(cfg.get("activity_types"), case_sensitive=False),
        default="Meeting",
    )
    subject = click.prompt("Subject", default="Client onboarding — program review")
    details = click.prompt("Details (optional)", default="", show_default=False)
    follow_up_date = click.prompt("Follow-up date (optional, YYYY-MM-DD)", default="", show_default=False)

    conn.execute(
        """INSERT INTO activity_log
           (client_id, activity_type, subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            client_id,
            activity_type,
            subject,
            details or None,
            _parse_date(follow_up_date) if follow_up_date else None,
            account_exec,
        ),
    )
    conn.commit()
    click.echo("  Activity logged.")


def _print_summary(conn: sqlite3.Connection, client_id: int, client_name: str) -> None:
    """Print onboarding summary and optionally save as Markdown."""
    audit = build_program_audit(conn, client_id)
    summary = conn.execute("SELECT * FROM v_client_summary WHERE id = ?", (client_id,)).fetchone()

    total_premium = summary["total_premium"] if summary else 0

    _console.print(Panel(
        "\n".join([
            f"[bold]Client:[/bold] {client_name}",
            f"[bold]Total Policies:[/bold] {audit['policy_count']}",
            f"[bold]Coverage Lines:[/bold] {', '.join(audit['coverage_lines'])}",
            f"[bold]Carriers:[/bold] {', '.join(audit['carriers'])}",
            f"[bold]Total Premium:[/bold] {fmt_currency(total_premium)}",
            f"[bold]Near-Term Renewals:[/bold] {audit['near_term_count']} (within 180d)",
            f"[bold]Standalone Policies:[/bold] {audit['standalone_count']}",
            "",
            "[bold]Coverage Gaps:[/bold]",
        ] + [f"  • {obs}" for obs in audit["gap_observations"]] or ["  None detected."],
        ),
        title="[bold green]Onboarding Complete[/bold green]",
        border_style="green",
    ))

    if click.confirm("\nSave summary as Markdown?", default=False):
        from policydb.exporter import export_llm_client_md, save_export
        content = export_llm_client_md(conn, client_id)
        safe_name = client_name.lower().replace(" ", "_")
        path = save_export(content, f"{safe_name}_onboarding.md")
        click.echo(f"  Saved: {path}")


def run_onboarding(conn: sqlite3.Connection, client_name: str | None = None) -> None:
    """Run the full 7-step onboarding workflow."""
    _console.print(Panel(
        "Guided client onboarding\nSteps: setup → policies → descriptions → audit → renewals → activity → summary",
        title="[bold blue]PolicyDB Onboarding[/bold blue]",
        border_style="blue",
    ))

    # Step 1: Client setup
    click.echo("\n[Step 1/7] Client Setup")
    name = _prompt_client_name(default=client_name)
    existing = get_client_by_name(conn, name)
    if existing:
        click.echo(f"  Found existing client: {existing['name']} (id={existing['id']})")
        client_id = existing["id"]
        client_name = existing["name"]
        if not click.confirm("  Continue with this client?", default=True):
            return
    else:
        click.echo(f"  No client found for '{name}'. Creating new client.")
        client_id = _create_client(conn, name)
        client_name = name

    # Step 2: Policy intake
    click.echo("\n[Step 2/7] Policy Intake")
    csv_path = click.prompt(
        "  CSV file path (or Enter for interactive entry)",
        default="",
        show_default=False,
    )
    if csv_path.strip():
        path = Path(csv_path.strip().replace("~", str(Path.home())))
        if not path.exists():
            click.echo(f"  [!] File not found: {path}. Switching to interactive entry.")
            count = _interactive_policy_entry(conn, client_id)
        else:
            importer = PolicyImporter(conn)
            importer.import_csv(path)
            count = importer.imported
    else:
        count = _interactive_policy_entry(conn, client_id)

    click.echo(f"  {count} policies added.")

    # Step 3: Description pass
    click.echo("\n[Step 3/7] Description Pass")
    _description_pass(conn, client_id)

    # Step 4: Program audit
    click.echo("\n[Step 4/7] Program Audit")
    audit = build_program_audit(conn, client_id)
    click.echo(f"  {audit['policy_count']} policies across {len(audit['coverage_lines'])} lines with {audit['carrier_count']} carriers")

    if audit["gap_observations"]:
        click.echo("  Coverage gaps detected:")
        for obs in audit["gap_observations"]:
            click.echo(f"    • {obs}")
    else:
        click.echo("  No coverage gaps detected.")

    if audit["duplicate_count"]:
        click.echo(f"  [!] {audit['duplicate_count']} potential duplicate policies detected — review recommended.")

    if audit["near_term_count"]:
        click.echo(f"  {audit['near_term_count']} policies expire within 180 days.")

    # Step 5: Renewal setup
    click.echo("\n[Step 5/7] Renewal Setup")
    _renewal_setup(conn, client_id)

    # Step 6: Initial activity
    click.echo("\n[Step 6/7] Activity Log")
    _log_initial_activity(conn, client_id)

    # Step 7: Summary
    click.echo("\n[Step 7/7] Summary")
    _print_summary(conn, client_id, client_name)
