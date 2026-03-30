"""PolicyDB CLI — Click entry point and command groups."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console

from policydb import config as cfg
from policydb.db import get_connection, get_db_path, init_db, next_policy_uid
from policydb.display import (
    activity_table,
    calendar_table,
    client_table,
    console,
    fmt_currency,
    history_table,
    overdue_table,
    policy_table,
    renewal_dashboard,
    renewal_table,
    rows_to_csv,
    rows_to_json,
    tower_panel,
    urgency_text,
)
from policydb.queries import (
    full_text_search,
    get_activities,
    get_activity_by_id,
    get_all_clients,
    get_all_policies,
    get_client_by_name,
    get_client_summary,
    get_db_stats,
    get_overdue_followups,
    get_policies_for_client,
    get_policy_by_uid,
    get_premium_history,
    get_renewal_calendar,
    get_renewal_metrics,
    get_renewal_pipeline,
    get_stale_renewals,
    get_tower_for_client,
)

FORMAT_CHOICES = click.Choice(["table", "json", "csv", "markdown"], case_sensitive=False)


def _require_db() -> None:
    if not get_db_path().exists():
        raise click.ClickException("Database not found. Run: policydb db init")


def _get_conn():
    _require_db()
    return get_connection()


def _resolve_client(conn, name_or_id: str):
    """Return client row by name or numeric ID. Exits on failure."""
    if name_or_id.isdigit():
        from policydb.queries import get_client_by_id
        row = get_client_by_id(conn, int(name_or_id))
    else:
        row = get_client_by_name(conn, name_or_id)
    if not row:
        raise click.ClickException(f"Client not found: {name_or_id}")
    return row


def _output(content: str) -> None:
    """Write to stdout (supports piping)."""
    click.echo(content)


# ─── ROOT GROUP ──────────────────────────────────────────────────────────────

@click.group()
@click.version_option("7.1.0", prog_name="policydb")
def main():
    """PolicyDB — Insurance Book of Business Management."""


# ─── DB COMMANDS ─────────────────────────────────────────────────────────────

@main.group("db")
def db_group():
    """Database management commands."""


@db_group.command("init")
def db_init():
    """Initialize database and schema."""
    db_path = get_db_path()
    if db_path.exists():
        if not click.confirm(f"Database already exists at {db_path}. Re-initialize?", default=False):
            return
    init_db()
    cfg.write_default_config()
    click.echo(f"Database initialized: {db_path}")
    click.echo(f"Config: {cfg.CONFIG_PATH}")


@db_group.command("seed")
def db_seed():
    """Load sample data."""
    _require_db()
    from policydb.seed import run_seed
    conn = get_connection()
    run_seed(conn)
    conn.close()
    click.echo("Seed data loaded.")


@db_group.command("backup")
@click.option("--dest-dir", default=None, hidden=True, help="DEPRECATED — ignored. Backups always go to ~/.policydb/backups/.")
@click.option("--keep", default=30, show_default=True, help="Number of backups to retain before pruning oldest.")
def db_backup(dest_dir, keep):
    """Back up the database to a timestamped file and prune old backups.

    Creates ~/.policydb/backups/policydb_YYYY-MM-DD_HHMMSS.sqlite.
    Run daily via launchd or cron; oldest copies beyond --keep are deleted.
    """
    if dest_dir is not None:
        click.echo("Warning: --dest-dir is deprecated and ignored. Backups are always written to ~/.policydb/backups/")

    src = get_db_path()
    if not src.exists():
        raise click.ClickException("No database found.")

    from policydb.db import _auto_backup, _HEALTH_STATUS
    _auto_backup(src, max_backups=keep)

    backup_path = _HEALTH_STATUS.get("last_backup", "")
    verified = _HEALTH_STATUS.get("last_backup_verified", False)
    count = _HEALTH_STATUS.get("backup_count", 0)

    if backup_path:
        click.echo(f"Backup saved: {backup_path}")
        click.echo(f"Integrity: {'Verified' if verified else 'UNVERIFIED'}")
        click.echo(f"Total backups: {count} (keeping {keep})")
    else:
        click.echo("Warning: Backup may have failed — check ~/.policydb/backups/")


@db_group.command("stats")
def db_stats():
    """Show row counts and database info."""
    conn = _get_conn()
    stats = get_db_stats(conn)
    conn.close()
    size = get_db_path().stat().st_size
    console.print(f"\n[bold]Database:[/bold] {get_db_path()}")
    console.print(f"[bold]Size:[/bold] {size:,} bytes ({size // 1024} KB)")
    console.print("\n[bold]Row Counts:[/bold]")
    for table, count in stats.items():
        console.print(f"  {table:<22} {count:>6}")
    console.print()


# ─── CLIENT COMMANDS ─────────────────────────────────────────────────────────

@main.group("client")
def client_group():
    """Client management commands."""


@client_group.command("list")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def client_list(fmt):
    """List all clients with summary stats."""
    conn = _get_conn()
    rows = get_all_clients(conn)
    conn.close()

    if fmt == "table":
        console.print(client_table(rows))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    elif fmt == "markdown":
        lines = ["# Clients", "", "| Name | Segment | Policies | Premium | Next Renewal |",
                 "|------|---------|----------|---------|--------------|"]
        for r in rows:
            nr = f"{r['next_renewal_days']}d" if r["next_renewal_days"] else "—"
            lines.append(f"| {r['name']} | {r['industry_segment']} | {r['total_policies']} | {fmt_currency(r['total_premium'])} | {nr} |")
        _output("\n".join(lines))


@client_group.command("add")
def client_add():
    """Add a new client interactively."""
    conn = _get_conn()
    name = click.prompt("Client name")
    existing = get_client_by_name(conn, name)
    if existing:
        raise click.ClickException(f"Client already exists: {existing['name']}")

    industry = click.prompt(
        "Industry segment",
        type=click.Choice(cfg.get("industry_segments"), case_sensitive=False),
    )
    primary_contact = click.prompt("Primary contact (optional)", default="", show_default=False)
    contact_email = click.prompt("Contact email (optional)", default="", show_default=False)
    contact_phone = click.prompt("Contact phone (optional)", default="", show_default=False)
    address = click.prompt("Address (optional)", default="", show_default=False)
    notes = click.prompt("Notes (optional)", default="", show_default=False)
    account_exec = cfg.get("default_account_exec", "Grant")

    conn.execute(
        """INSERT INTO clients
           (name, industry_segment, primary_contact, contact_email,
            contact_phone, address, notes, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, industry, primary_contact or None, contact_email or None,
         contact_phone or None, address or None, notes or None, account_exec),
    )
    conn.commit()
    conn.close()
    click.echo(f"Client added: {name}")


@client_group.command("show")
@click.argument("name_or_id")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def client_show(name_or_id, fmt):
    """Show full client detail: profile + policies + recent activity."""
    conn = _get_conn()
    client = _resolve_client(conn, name_or_id)
    client_id = client["id"]
    summary = get_client_summary(conn, client_id)
    policies = get_policies_for_client(conn, client_id)
    activities = get_activities(conn, client_id=client_id, days=90)
    conn.close()

    if fmt == "json":
        from policydb.exporter import export_client_json
        conn2 = get_connection()
        _output(export_client_json(conn2, client_id))
        conn2.close()
        return
    elif fmt == "markdown":
        from policydb.exporter import export_client_md
        conn2 = get_connection()
        _output(export_client_md(conn2, client_id))
        conn2.close()
        return
    elif fmt == "csv":
        from policydb.exporter import export_client_csv
        conn2 = get_connection()
        _output(export_client_csv(conn2, client_id))
        conn2.close()
        return

    # Default: rich table output
    console.print(f"\n[bold white]Client: {client['name']}[/bold white]")
    console.print(f"  Industry: {client['industry_segment']}")
    console.print(f"  Contact: {client['primary_contact'] or '—'} | {client['contact_email'] or '—'} | {client['contact_phone'] or '—'}")
    if summary:
        console.print(f"  Policies: {summary['total_policies']} | Premium: {fmt_currency(summary['total_premium'])} | Carriers: {summary['carrier_count']}")
    console.print()
    if policies:
        console.print(policy_table(policies, title=f"Policies — {client['name']}"))
    if activities:
        console.print()
        console.print(activity_table(activities, title="Recent Activity (90d)"))


@client_group.command("edit")
@click.argument("name_or_id")
def client_edit(name_or_id):
    """Update client fields interactively."""
    conn = _get_conn()
    client = _resolve_client(conn, name_or_id)

    click.echo(f"Editing: {client['name']} (press Enter to keep current value)")
    fields = {
        "name": ("Name", client["name"]),
        "industry_segment": ("Industry segment", client["industry_segment"]),
        "primary_contact": ("Primary contact", client["primary_contact"] or ""),
        "contact_email": ("Contact email", client["contact_email"] or ""),
        "contact_phone": ("Contact phone", client["contact_phone"] or ""),
        "address": ("Address", client["address"] or ""),
        "notes": ("Notes", client["notes"] or ""),
    }
    updates = {}
    for col, (label, current) in fields.items():
        val = click.prompt(f"  {label}", default=current, show_default=True)
        if val != current:
            updates[col] = val or None

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE clients SET {set_clause} WHERE id = ?",
            (*updates.values(), client["id"]),
        )
        conn.commit()
        click.echo(f"Updated {len(updates)} field(s).")
    else:
        click.echo("No changes.")
    conn.close()


@client_group.command("archive")
@click.argument("name_or_id")
def client_archive(name_or_id):
    """Soft-delete a client."""
    conn = _get_conn()
    client = _resolve_client(conn, name_or_id)
    if not click.confirm(f"Archive '{client['name']}'? (soft delete)", default=False):
        return
    conn.execute("UPDATE clients SET archived = 1 WHERE id = ?", (client["id"],))
    conn.commit()
    conn.close()
    click.echo(f"Archived: {client['name']}")


@client_group.command("import")
@click.argument("file", type=click.Path(exists=True))
def client_import(file):
    """Bulk import clients from CSV."""
    from policydb.importer import ClientImporter
    conn = _get_conn()
    importer = ClientImporter(conn)
    importer.import_csv(Path(file))
    conn.close()


# ─── POLICY COMMANDS ─────────────────────────────────────────────────────────

@main.group("policy")
def policy_group():
    """Policy management commands."""


@policy_group.command("list")
@click.option("--client", "client_name", default=None)
@click.option("--status", "urgency", default=None, type=click.Choice(["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"], case_sensitive=False))
@click.option("--type", "policy_type", default=None)
@click.option("--standalone", is_flag=True, default=False)
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def policy_list(client_name, urgency, policy_type, standalone, fmt):
    """List policies with optional filters."""
    conn = _get_conn()
    client_id = None
    if client_name:
        client = get_client_by_name(conn, client_name)
        if not client:
            raise click.ClickException(f"Client not found: {client_name}")
        client_id = client["id"]

    rows = get_all_policies(conn, client_id=client_id, urgency=urgency, policy_type=policy_type, standalone_only=standalone)
    conn.close()

    if fmt == "table":
        title = "Policies"
        if client_name:
            title += f" — {client_name}"
        if urgency:
            title += f" ({urgency})"
        console.print(policy_table(rows, title=title))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    elif fmt == "markdown":
        lines = ["# Policies", "", "| UID | Client | Line | Carrier | Expires | Premium | Status |",
                 "|-----|--------|------|---------|---------|---------|--------|"]
        for r in rows:
            lines.append(f"| {r['policy_uid']} | {r['client_name']} | {r['policy_type']} | {r['carrier']} | {r['expiration_date']} | {fmt_currency(r['premium'])} | {r['renewal_status']} |")
        _output("\n".join(lines))


@policy_group.command("add")
@click.option("--client", "client_name", default=None)
def policy_add(client_name):
    """Add a policy interactively."""
    conn = _get_conn()
    account_exec = cfg.get("default_account_exec", "Grant")
    policy_types = cfg.get("policy_types")
    valid_statuses = ["Not Started", "In Progress", "Pending Bind", "Bound"]

    if client_name:
        client = _resolve_client(conn, client_name)
    else:
        name = click.prompt("Client name")
        client = _resolve_client(conn, name)

    pol_type = click.prompt(
        "Line of business",
        type=click.Choice(policy_types, case_sensitive=False),
    )
    carrier = click.prompt("Carrier")
    policy_number = click.prompt("Policy number (optional)", default="", show_default=False)
    effective_raw = click.prompt("Effective date")
    expiration_raw = click.prompt("Expiration date")
    premium = click.prompt("Annual premium", type=float)

    from policydb.importer import _parse_date
    eff = _parse_date(effective_raw)
    exp = _parse_date(expiration_raw)
    if not eff or not exp:
        raise click.ClickException("Invalid date format.")

    uid = next_policy_uid(conn)

    # Optional fields
    if click.confirm("Add optional fields now?", default=False):
        limit_amount = click.prompt("Policy limit (0 if none)", type=float, default=0.0)
        deductible = click.prompt("Deductible (0 if none)", type=float, default=0.0)
        description = click.prompt("Description (client-facing)", default="", show_default=False)
        coverage_form = click.prompt("Coverage form (Occurrence/Claims-Made/etc.)", default="", show_default=False)
        layer_position = click.prompt("Layer position", default="Primary")
        is_standalone = click.confirm("Is this a standalone policy?", default=False)
        colleague = click.prompt("Placement colleague (optional)", default="", show_default=False)
        uw_name = click.prompt("Underwriter name (optional)", default="", show_default=False)
        status = click.prompt(
            "Renewal status",
            type=click.Choice(valid_statuses, case_sensitive=False),
            default="Not Started",
        )
        commission_rate = click.prompt("Commission rate (0.12 = 12%, 0 if unknown)", type=float, default=0.0)
        exposure_basis = click.prompt("Exposure basis (e.g. Payroll, Revenue, Sq Ft — optional)", default="", show_default=False)
        exposure_amount = click.prompt("Exposure amount (0 if unknown)", type=float, default=0.0)
        exposure_unit = click.prompt("Exposure unit (e.g. per $100, per $1,000, per unit, per sq ft)", default="", show_default=False)
        notes = click.prompt("Internal notes (optional)", default="", show_default=False)
    else:
        limit_amount = deductible = commission_rate = exposure_amount = 0.0
        description = coverage_form = layer_position = "Primary"
        colleague = uw_name = exposure_basis = exposure_unit = notes = ""
        is_standalone = False
        status = "Not Started"

    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, coverage_form, layer_position, is_standalone,
            underwriter_name, renewal_status, commission_rate,
            exposure_basis, exposure_amount, exposure_unit, account_exec, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            uid, client["id"], pol_type, carrier, policy_number or None,
            eff, exp, premium, limit_amount or None, deductible or None,
            description or None, coverage_form or None, layer_position or "Primary",
            1 if is_standalone else 0,
            uw_name or None, status, commission_rate or None,
            exposure_basis or None, exposure_amount or None, exposure_unit or None,
            account_exec, notes or None,
        ),
    )
    conn.commit()
    # Create structured contact records for placement colleague and underwriter
    _p_row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (uid,)).fetchone()
    if _p_row:
        _pid = _p_row["id"]
        if colleague:
            from policydb.queries import get_or_create_contact, assign_contact_to_policy
            _pc_cid = get_or_create_contact(conn, colleague.strip())
            assign_contact_to_policy(conn, _pc_cid, _pid, is_placement_colleague=1)
        if uw_name:
            from policydb.queries import get_or_create_contact, assign_contact_to_policy
            _uw_cid = get_or_create_contact(conn, uw_name.strip())
            assign_contact_to_policy(conn, _uw_cid, _pid, role="Underwriter")
        conn.commit()
    conn.close()
    click.echo(f"Policy added: {uid} ({pol_type} / {carrier})")


@policy_group.command("show")
@click.argument("policy_uid")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def policy_show(policy_uid, fmt):
    """Show full policy detail."""
    conn = _get_conn()
    row = get_policy_by_uid(conn, policy_uid.upper())
    conn.close()
    if not row:
        raise click.ClickException(f"Policy not found: {policy_uid}")

    if fmt == "json":
        _output(json.dumps(dict(row), indent=2, default=str))
        return
    if fmt == "csv":
        _output(rows_to_csv([row]))
        return

    console.print(f"\n[bold]Policy: {row['policy_uid']}[/bold]")
    fields = [
        ("Client", row["client_name"]),
        ("Line of Business", row["policy_type"]),
        ("Carrier", row["carrier"]),
        ("Policy Number", row["policy_number"] or "—"),
        ("Effective", row["effective_date"]),
        ("Expiration", row["expiration_date"]),
        ("Days to Renewal", str(row["days_to_renewal"]) if row["days_to_renewal"] is not None else "—"),
        ("Urgency", row["urgency"]),
        ("Premium", fmt_currency(row["premium"])),
        ("Limit", fmt_currency(row["limit_amount"]) if row["limit_amount"] else "—"),
        ("Deductible", fmt_currency(row["deductible"]) if row["deductible"] else "—"),
        ("Coverage Form", row["coverage_form"] or "—"),
        ("Layer", row["layer_position"] or "Primary"),
        ("Standalone", "Yes" if row["is_standalone"] else "No"),
        ("Description", row["description"] or "—"),
        ("Renewal Status", row["renewal_status"]),
        ("Placement Colleague", row["placement_colleague"] or "—"),
        ("Underwriter", row["underwriter_name"] or "—"),
        ("Exposure Basis", row["exposure_basis"] or "—"),
        ("Exposure Amount", fmt_currency(row["exposure_amount"]) if row["exposure_amount"] else "—"),
        ("Exposure Unit", row["exposure_unit"] or "—"),
        ("Internal Notes", row["notes"] or "—"),
    ]
    for label, value in fields:
        console.print(f"  [bold]{label:<22}[/bold] {value}")
    console.print()


@policy_group.command("edit")
@click.argument("policy_uid")
def policy_edit(policy_uid):
    """Update policy fields interactively."""
    conn = _get_conn()
    row = get_policy_by_uid(conn, policy_uid.upper())
    if not row:
        raise click.ClickException(f"Policy not found: {policy_uid}")

    click.echo(f"Editing: {row['policy_uid']} — {row['policy_type']} / {row['carrier']}")
    click.echo("(Press Enter to keep current value)")

    editable = [
        ("policy_type", "Line of business", row["policy_type"]),
        ("carrier", "Carrier", row["carrier"]),
        ("policy_number", "Policy number", row["policy_number"] or ""),
        ("effective_date", "Effective date", row["effective_date"]),
        ("expiration_date", "Expiration date", row["expiration_date"]),
        ("premium", "Annual premium", str(row["premium"])),
        ("limit_amount", "Limit", str(row["limit_amount"] or 0)),
        ("deductible", "Deductible", str(row["deductible"] or 0)),
        ("description", "Description", row["description"] or ""),
        ("coverage_form", "Coverage form", row["coverage_form"] or ""),
        ("layer_position", "Layer position", row["layer_position"] or "Primary"),
        ("placement_colleague", "Placement colleague", row["placement_colleague"] or ""),
        ("underwriter_name", "Underwriter name", row["underwriter_name"] or ""),
        ("renewal_status", "Renewal status", row["renewal_status"]),
        ("commission_rate", "Commission rate", str(row["commission_rate"] or 0)),
        ("prior_premium", "Prior premium", str(row["prior_premium"] or "")),
        ("exposure_basis", "Exposure basis", row["exposure_basis"] or ""),
        ("exposure_amount", "Exposure amount", str(row["exposure_amount"] or "")),
        ("exposure_unit", "Exposure unit", row["exposure_unit"] or ""),
        ("notes", "Internal notes", row["notes"] or ""),
    ]
    updates = {}
    for col, label, current in editable:
        val = click.prompt(f"  {label}", default=current, show_default=True)
        if val != current:
            # Type coercion for numeric fields
            if col in ("premium", "limit_amount", "deductible", "commission_rate", "prior_premium", "exposure_amount"):
                try:
                    updates[col] = float(val) if val else None
                except ValueError:
                    pass
            else:
                updates[col] = val or None

    # Handle placement_colleague through the contact system
    pc_update = updates.pop("placement_colleague", None)
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE policies SET {set_clause} WHERE policy_uid = ?",
            (*updates.values(), policy_uid.upper()),
        )
        conn.commit()
    if pc_update:
        from policydb.queries import get_or_create_contact, assign_contact_to_policy
        _p_row = conn.execute("SELECT id FROM policies WHERE policy_uid=?", (policy_uid.upper(),)).fetchone()
        if _p_row:
            _pc_cid = get_or_create_contact(conn, pc_update.strip())
            assign_contact_to_policy(conn, _pc_cid, _p_row["id"], is_placement_colleague=1)
            conn.commit()
    if updates or pc_update:
        click.echo(f"Updated {len(updates) + (1 if pc_update else 0)} field(s).")
    else:
        click.echo("No changes.")
    conn.close()


@policy_group.command("set-status")
@click.argument("policy_uid")
@click.argument("status", type=click.Choice(["Not Started", "In Progress", "Pending Bind", "Bound"], case_sensitive=False))
def policy_set_status(policy_uid, status):
    """Quick renewal status update."""
    conn = _get_conn()
    row = get_policy_by_uid(conn, policy_uid.upper())
    if not row:
        raise click.ClickException(f"Policy not found: {policy_uid}")
    conn.execute(
        "UPDATE policies SET renewal_status = ? WHERE policy_uid = ?",
        (status, policy_uid.upper()),
    )
    conn.commit()
    conn.close()
    click.echo(f"{policy_uid}: renewal status → {status}")


@policy_group.command("tower")
@click.argument("client_name")
def policy_tower(client_name):
    """Show tower/layer structure for a client."""
    conn = _get_conn()
    client = _resolve_client(conn, client_name)
    rows = get_tower_for_client(conn, client["id"])
    conn.close()
    if not rows:
        click.echo(f"No policies found for: {client['name']}")
        return
    tower_panel(client["name"], rows)


@policy_group.command("archive")
@click.argument("policy_uid")
def policy_archive(policy_uid):
    """Soft-delete a policy."""
    conn = _get_conn()
    row = get_policy_by_uid(conn, policy_uid.upper())
    if not row:
        raise click.ClickException(f"Policy not found: {policy_uid}")
    if not click.confirm(f"Archive {policy_uid}? (soft delete)", default=False):
        return
    conn.execute("UPDATE policies SET archived = 1 WHERE policy_uid = ?", (policy_uid.upper(),))
    conn.commit()
    conn.close()
    click.echo(f"Archived: {policy_uid}")


@policy_group.command("renew")
@click.argument("policy_uid")
def policy_renew(policy_uid):
    """Create a new renewal term from an existing policy (archives prior term)."""
    from policydb.queries import renew_policy
    conn = _get_conn()
    if not get_policy_by_uid(conn, policy_uid.upper()):
        raise click.ClickException(f"Policy not found: {policy_uid}")
    new_uid = renew_policy(conn, policy_uid.upper())
    conn.close()
    click.echo(f"Renewal created: {new_uid}  (prior term {policy_uid.upper()} archived)")
    click.echo(f"Edit new term:   policydb policy edit {new_uid}")


@policy_group.command("import")
@click.argument("file", type=click.Path(exists=True))
def policy_import(file):
    """Bulk import policies from CSV."""
    from policydb.importer import PolicyImporter
    conn = _get_conn()
    importer = PolicyImporter(conn)
    importer.import_csv(Path(file))
    conn.close()


# ─── RENEWAL COMMANDS ─────────────────────────────────────────────────────────

@main.group("renewal")
def renewal_group():
    """Renewal pipeline commands."""


@renewal_group.command("list")
@click.option("--window", "days", default=180, type=int, show_default=True)
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def renewal_list(days, fmt):
    """List upcoming renewals within window."""
    conn = _get_conn()
    rows = get_renewal_pipeline(conn, window_days=days)
    conn.close()

    if fmt == "table":
        console.print(renewal_table(rows, title=f"Renewals — Next {days} Days"))
    elif fmt == "json":
        _output(rows_to_json(rows, extra={"window_days": days}))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    elif fmt == "markdown":
        from policydb.exporter import export_renewals_md
        conn2 = get_connection()
        _output(export_renewals_md(conn2, window_days=days))
        conn2.close()


@renewal_group.command("urgent")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def renewal_urgent(fmt):
    """Shortcut: renewals within 90 days."""
    conn = _get_conn()
    rows = get_renewal_pipeline(conn, window_days=90)
    conn.close()

    if fmt == "table":
        console.print(renewal_table(rows, title="URGENT Renewals — Next 90 Days"))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    elif fmt == "markdown":
        from policydb.exporter import export_renewals_md
        conn2 = get_connection()
        _output(export_renewals_md(conn2, window_days=90))
        conn2.close()


@renewal_group.command("dashboard")
def renewal_dashboard_cmd():
    """Full renewal metrics dashboard."""
    conn = _get_conn()
    metrics = get_renewal_metrics(conn)
    pipeline = get_renewal_pipeline(conn, window_days=180)
    conn.close()
    renewal_dashboard(metrics, pipeline)


@renewal_group.command("stale")
@click.option("--days", "stale_days", default=None, type=int, help="Override stale threshold (default from config).")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def renewal_stale(stale_days, fmt):
    """Renewals within 180d still at 'Not Started'."""
    conn = _get_conn()
    threshold = stale_days or cfg.get("stale_threshold_days", 14)
    rows = get_stale_renewals(conn, window_days=180, stale_days=threshold)
    conn.close()

    if not rows:
        click.echo("No stale renewals.")
        return
    if fmt == "table":
        console.print(renewal_table(rows, title="Stale Renewals (Not Started)"))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))


@renewal_group.command("calendar")
@click.option("--months", default=6, type=int, show_default=True)
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def renewal_calendar(months, fmt):
    """Month-by-month expiration view."""
    conn = _get_conn()
    rows = get_renewal_calendar(conn, months=months)
    conn.close()

    if fmt == "table":
        console.print(calendar_table(rows, months=months))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    elif fmt == "markdown":
        lines = [f"# Renewal Calendar — Next {months} Months", "",
                 "| Month | Policies | Premium |", "|-------|----------|---------|"]
        for r in rows:
            lines.append(f"| {r['month']} | {r['policy_count']} | {fmt_currency(r['total_premium'])} |")
        _output("\n".join(lines))


# ─── ACTIVITY COMMANDS ───────────────────────────────────────────────────────

@main.group("activity")
def activity_group():
    """Activity log commands."""


@activity_group.command("log")
@click.option("--client", "client_name", default=None)
def activity_log(client_name):
    """Log a new activity interactively."""
    conn = _get_conn()
    account_exec = cfg.get("default_account_exec", "Grant")

    if client_name:
        client = _resolve_client(conn, client_name)
    else:
        name = click.prompt("Client name")
        client = _resolve_client(conn, name)

    activity_type = click.prompt(
        "Activity type",
        type=click.Choice(cfg.get("activity_types"), case_sensitive=False),
    )
    subject = click.prompt("Subject")
    details = click.prompt("Details (optional)", default="", show_default=False)
    contact = click.prompt("Contact person (optional)", default="", show_default=False)

    # Optional: link to a policy
    policy_id = None
    if click.confirm("Link to a specific policy?", default=False):
        pol_uid = click.prompt("Policy UID (e.g. POL-001)")
        pol_row = get_policy_by_uid(conn, pol_uid.upper())
        if pol_row:
            policy_id = pol_row["id"]
        else:
            click.echo(f"  Policy {pol_uid} not found — logging without policy link.")

    activity_date = click.prompt("Activity date (YYYY-MM-DD, or Enter for today)", default="", show_default=False)
    from policydb.importer import _parse_date
    from datetime import date
    act_date = _parse_date(activity_date) if activity_date else date.today().isoformat()

    follow_up_date = click.prompt("Follow-up date (optional, YYYY-MM-DD)", default="", show_default=False)
    fu_date = _parse_date(follow_up_date) if follow_up_date else None

    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person,
            subject, details, follow_up_date, account_exec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (act_date, client["id"], policy_id, activity_type,
         contact or None, subject, details or None, fu_date, account_exec),
    )
    conn.commit()
    conn.close()
    click.echo("Activity logged.")


@activity_group.command("list")
@click.option("--client", "client_name", default=None)
@click.option("--days", default=90, type=int, show_default=True)
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def activity_list(client_name, days, fmt):
    """List activity log entries."""
    conn = _get_conn()
    client_id = None
    if client_name:
        client = _resolve_client(conn, client_name)
        client_id = client["id"]

    rows = get_activities(conn, client_id=client_id, days=days)
    conn.close()

    if fmt == "table":
        console.print(activity_table(rows, title=f"Activity Log (last {days}d)"))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))


@activity_group.command("overdue")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def activity_overdue(fmt):
    """Show overdue follow-ups."""
    conn = _get_conn()
    rows = get_overdue_followups(conn)
    conn.close()

    if not rows:
        click.echo("No overdue follow-ups.")
        return
    if fmt == "table":
        console.print(overdue_table(rows))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))


@activity_group.command("complete")
@click.argument("activity_id", type=int)
def activity_complete(activity_id):
    """Mark a follow-up as done."""
    conn = _get_conn()
    row = get_activity_by_id(conn, activity_id)
    if not row:
        raise click.ClickException(f"Activity not found: {activity_id}")
    conn.execute(
        "UPDATE activity_log SET follow_up_done = 1 WHERE id = ?", (activity_id,)
    )
    conn.commit()
    conn.close()
    click.echo(f"Activity {activity_id} marked complete.")


# ─── PREMIUM HISTORY COMMANDS ─────────────────────────────────────────────────

@main.group("history")
def history_group():
    """Premium history commands."""


@history_group.command("add")
@click.option("--client", "client_name", default=None)
def history_add(client_name):
    """Add a historical premium term."""
    conn = _get_conn()
    if client_name:
        client = _resolve_client(conn, client_name)
    else:
        name = click.prompt("Client name")
        client = _resolve_client(conn, name)

    policy_types = cfg.get("policy_types")
    pol_type = click.prompt("Line of business", type=click.Choice(policy_types, case_sensitive=False))
    carrier = click.prompt("Carrier (optional)", default="", show_default=False)
    term_eff_raw = click.prompt("Term effective date")
    term_exp_raw = click.prompt("Term expiration date")
    premium = click.prompt("Premium", type=float)
    limit_amount = click.prompt("Limit (0 if none)", type=float, default=0.0)
    deductible = click.prompt("Deductible (0 if none)", type=float, default=0.0)
    notes = click.prompt("Notes (optional)", default="", show_default=False)

    from policydb.importer import _parse_date
    eff = _parse_date(term_eff_raw)
    exp = _parse_date(term_exp_raw)
    if not eff or not exp:
        raise click.ClickException("Invalid date format.")

    try:
        conn.execute(
            """INSERT INTO premium_history
               (client_id, policy_type, carrier, term_effective, term_expiration,
                premium, limit_amount, deductible, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client["id"], pol_type, carrier or None, eff, exp, premium,
             limit_amount or None, deductible or None, notes or None),
        )
        conn.commit()
        click.echo("Premium history entry added.")
    except Exception as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()


@history_group.command("show")
@click.argument("client_name")
@click.option("--type", "policy_type", default=None)
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def history_show(client_name, policy_type, fmt):
    """Show premium history for a client."""
    conn = _get_conn()
    client = _resolve_client(conn, client_name)
    rows = get_premium_history(conn, client["id"], policy_type=policy_type)
    conn.close()

    if not rows:
        click.echo(f"No premium history found for: {client['name']}")
        return
    if fmt == "table":
        console.print(history_table(rows, client["name"]))
    elif fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))


@history_group.command("import")
@click.argument("file", type=click.Path(exists=True))
def history_import(file):
    """Bulk import premium history from CSV."""
    from policydb.importer import PremiumHistoryImporter
    conn = _get_conn()
    importer = PremiumHistoryImporter(conn)
    importer.import_csv(Path(file))
    conn.close()


# ─── EXPORT COMMANDS ──────────────────────────────────────────────────────────

@main.group("export")
def export_group():
    """Export commands."""


@export_group.command("schedule")
@click.argument("client_name")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "csv", "json", "xlsx"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False, help="Save to exports directory.")
def export_schedule(client_name, fmt, save):
    """Client-facing schedule of insurance."""
    from policydb.exporter import (
        export_schedule_csv, export_schedule_json, export_schedule_md,
        export_schedule_xlsx, save_export, save_export_bytes,
    )
    conn = _get_conn()
    client = _resolve_client(conn, client_name)
    client_id = client["id"]
    safe = client["name"].lower().replace(" ", "_")

    if fmt == "xlsx":
        data = export_schedule_xlsx(conn, client_id, client["name"])
        conn.close()
        path = save_export_bytes(data, f"{safe}_schedule.xlsx")
        click.echo(f"Saved: {path}")
        return

    if fmt == "markdown":
        content = export_schedule_md(conn, client_id, client["name"])
        ext = "md"
    elif fmt == "csv":
        content = export_schedule_csv(conn, client_id)
        ext = "csv"
    else:
        content = export_schedule_json(conn, client_id, client["name"])
        ext = "json"
    conn.close()

    _output(content)
    if save:
        path = save_export(content, f"{safe}_schedule.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


@export_group.command("client")
@click.argument("client_name")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "markdown", "csv", "xlsx"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False)
def export_client(client_name, fmt, save):
    """Full client export."""
    from policydb.exporter import (
        export_client_csv, export_client_json, export_client_md,
        export_client_xlsx, save_export, save_export_bytes,
    )
    conn = _get_conn()
    client = _resolve_client(conn, client_name)
    client_id = client["id"]
    safe = client["name"].lower().replace(" ", "_")

    if fmt == "xlsx":
        data = export_client_xlsx(conn, client_id)
        conn.close()
        path = save_export_bytes(data, f"{safe}.xlsx")
        click.echo(f"Saved: {path}")
        return

    if fmt == "json":
        content = export_client_json(conn, client_id)
        ext = "json"
    elif fmt == "markdown":
        content = export_client_md(conn, client_id)
        ext = "md"
    else:
        content = export_client_csv(conn, client_id)
        ext = "csv"
    conn.close()

    _output(content)
    if save:
        path = save_export(content, f"{safe}.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


@export_group.command("book")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "markdown", "csv"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False)
def export_book(fmt, save):
    """Full book of business export."""
    from policydb.exporter import export_llm_book_json, export_llm_book_md, save_export
    conn = _get_conn()
    if fmt == "json":
        content = export_llm_book_json(conn)
        ext = "json"
    elif fmt == "markdown":
        content = export_llm_book_md(conn)
        ext = "md"
    else:
        rows = conn.execute("SELECT * FROM v_policy_status").fetchall()
        from policydb.display import rows_to_csv
        content = rows_to_csv(rows)
        ext = "csv"
    conn.close()

    _output(content)
    if save:
        from datetime import date
        path = save_export(content, f"book_{date.today().isoformat()}.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


@export_group.command("renewals")
@click.option("--window", "days", default=180, type=int)
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "csv", "json", "xlsx"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False)
def export_renewals(days, fmt, save):
    """Export renewal pipeline."""
    from policydb.exporter import (
        export_renewals_csv, export_renewals_json, export_renewals_md,
        export_renewals_xlsx, save_export, save_export_bytes,
    )
    conn = _get_conn()

    if fmt == "xlsx":
        data = export_renewals_xlsx(conn, window_days=days)
        conn.close()
        from datetime import date
        path = save_export_bytes(data, f"renewals_{date.today().isoformat()}.xlsx")
        click.echo(f"Saved: {path}")
        return

    if fmt == "markdown":
        content = export_renewals_md(conn, window_days=days)
        ext = "md"
    elif fmt == "csv":
        content = export_renewals_csv(conn, window_days=days)
        ext = "csv"
    else:
        content = export_renewals_json(conn, window_days=days)
        ext = "json"
    conn.close()

    _output(content)
    if save:
        from datetime import date
        path = save_export(content, f"renewals_{date.today().isoformat()}.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


@export_group.command("llm")
@click.argument("client_name")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "json"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False)
def export_llm(client_name, fmt, save):
    """LLM-optimized full context dump for a client."""
    from policydb.exporter import export_llm_client_json, export_llm_client_md, save_export
    conn = _get_conn()
    client = _resolve_client(conn, client_name)
    client_id = client["id"]

    if fmt == "markdown":
        content = export_llm_client_md(conn, client_id)
        ext = "md"
    else:
        content = export_llm_client_json(conn, client_id)
        ext = "json"
    conn.close()

    _output(content)
    if save:
        safe = client["name"].lower().replace(" ", "_")
        path = save_export(content, f"{safe}_llm.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


@export_group.command("llm-book")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "json"], case_sensitive=False))
@click.option("--save", is_flag=True, default=False)
def export_llm_book(fmt, save):
    """LLM-optimized full book of business dump."""
    from policydb.exporter import export_llm_book_json, export_llm_book_md, save_export
    conn = _get_conn()
    if fmt == "markdown":
        content = export_llm_book_md(conn)
        ext = "md"
    else:
        content = export_llm_book_json(conn)
        ext = "json"
    conn.close()

    _output(content)
    if save:
        from datetime import date
        path = save_export(content, f"llm_book_{date.today().isoformat()}.{ext}")
        click.echo(f"\nSaved: {path}", err=True)


# ─── SEARCH ──────────────────────────────────────────────────────────────────

@main.command("search")
@click.argument("query")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def search(query, fmt):
    """Full-text search across all tables."""
    conn = _get_conn()
    results = full_text_search(conn, query)
    conn.close()

    clients = results["clients"]
    policies = results["policies"]
    activities = results["activities"]

    total = len(clients) + len(policies) + len(activities)
    if total == 0:
        click.echo(f"No results for: {query}")
        return

    if fmt == "json":
        _output(json.dumps({k: [dict(r) for r in v] for k, v in results.items()}, indent=2, default=str))
        return

    if clients:
        console.print(f"\n[bold]Clients ({len(clients)})[/bold]")
        for r in clients:
            console.print(f"  {r['id']:>3}  {r['name']} — {r['industry_segment']}")

    if policies:
        console.print(f"\n[bold]Policies ({len(policies)})[/bold]")
        for r in policies:
            desc = (r["description"] or "")[:60]
            console.print(f"  {r['policy_uid']}  {r['client_name']} / {r['policy_type']} / {r['carrier']}  {desc}")

    if activities:
        console.print(f"\n[bold]Activities ({len(activities)})[/bold]")
        for r in activities:
            console.print(f"  {r['id']:>3}  {r['activity_date']}  {r['client_name']} — {r['subject']}")


# ─── ONBOARD ─────────────────────────────────────────────────────────────────

@main.command("onboard")
@click.argument("client_name", required=False, default=None)
def onboard(client_name):
    """Guided onboarding workflow for a new client."""
    from policydb.onboard import run_onboarding
    conn = _get_conn()
    try:
        run_onboarding(conn, client_name)
    finally:
        conn.close()


# ─── DIRECT SQL QUERY ────────────────────────────────────────────────────────

@main.command("query")
@click.argument("sql")
@click.option("--format", "fmt", default="table", type=FORMAT_CHOICES)
def query_cmd(sql, fmt):
    """Execute a read-only SQL query."""
    sql_lower = sql.strip().lower()
    if not sql_lower.startswith("select") and not sql_lower.startswith("with"):
        raise click.ClickException("Only SELECT queries allowed.")
    conn = _get_conn()
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        conn.close()
        raise click.ClickException(str(e))
    conn.close()

    if not rows:
        click.echo("No results.")
        return

    if fmt == "json":
        _output(rows_to_json(rows))
    elif fmt == "csv":
        _output(rows_to_csv(rows))
    else:
        from rich.table import Table as RTable
        from rich import box as rbox
        t = RTable(box=rbox.ROUNDED, show_header=True, header_style="bold white")
        for col in rows[0].keys():
            t.add_column(col)
        for r in rows:
            t.add_row(*[str(v) if v is not None else "—" for v in r])
        console.print(t)


# ─── SERVE (FASTAPI) ─────────────────────────────────────────────────────────

@main.command("serve")
@click.option("--port", default=8000, type=int, show_default=True, help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload (dev mode).")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Open browser automatically.")
def serve(port, host, reload, open_browser):
    """Launch the PolicyDB web UI (FastAPI)."""
    import webbrowser

    db_path = get_db_path()
    if not db_path.exists():
        raise click.ClickException("Database not found. Run: policydb db init")

    # Set up application logging before anything else
    from policydb.logging_config import setup_logging
    setup_logging()

    # Always run migrations + rebuild views on startup so the schema stays
    # current even when the user hasn't manually run `policydb db init`.
    init_db()

    # SQLite log handler attaches in the uvicorn worker process via
    # @app.on_event("startup") — NOT here, because uvicorn spawns a
    # separate worker and threads don't survive the transition.

    from policydb import __version__
    console.print(f"[bold green]PolicyDB v{__version__}[/bold green] → [link]http://{host}:{port}[/link]")
    console.print(f"  Database: {db_path}")
    console.print("  Press Ctrl-C to stop.\n")

    if open_browser:
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    try:
        import uvicorn
        uvicorn.run(
            "policydb.web.app:app",
            host=host,
            port=port,
            reload=reload,
            log_level="warning",
        )
    except ImportError:
        raise click.ClickException(
            "uvicorn not installed. Run: pip install uvicorn"
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Server stopped.[/dim]")


# ─── DATASETTE (READ-ONLY VIEWER) ────────────────────────────────────────────

@main.command("datasette")
@click.option("--port", default=8001, type=int, show_default=True, help="Port to listen on.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Open browser automatically.")
def datasette_serve(port, host, open_browser):
    """Launch Datasette read-only viewer for the PolicyDB database."""
    import shutil
    import subprocess

    if not shutil.which("datasette"):
        raise click.ClickException(
            "Datasette not installed. Run: pip install datasette"
        )

    db_path = get_db_path()
    if not db_path.exists():
        raise click.ClickException("Database not found. Run: policydb db init")

    metadata_path = db_path.parent / "datasette_metadata.json"

    cmd = [
        "datasette",
        "-i", str(db_path),
        "--host", host,
        "--port", str(port),
    ]

    if metadata_path.exists():
        cmd += ["--metadata", str(metadata_path)]

    if open_browser:
        cmd.append("--open")

    console.print(f"[bold green]PolicyDB Datasette[/bold green] → [link]http://{host}:{port}[/link]")
    console.print(f"  Database: {db_path}")
    if metadata_path.exists():
        console.print(f"  Metadata: {metadata_path}")
    console.print("  Press Ctrl-C to stop.\n")

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        console.print("\n[dim]Datasette stopped.[/dim]")
