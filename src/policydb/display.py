"""Rich terminal formatting helpers."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from babel.numbers import format_currency
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

console = Console()


# ─── URGENCY STYLES ──────────────────────────────────────────────────────────

URGENCY_STYLES = {
    "EXPIRED": "bold red",
    "URGENT": "bold yellow",
    "WARNING": "bold orange3",
    "UPCOMING": "bold cyan",
    "OK": "green",
}


def urgency_text(urgency: str, label: str | None = None) -> Text:
    style = URGENCY_STYLES.get(urgency, "white")
    return Text(label or urgency, style=style)


# ─── CURRENCY / NUMBER FORMATTING ────────────────────────────────────────────

def fmt_currency(value: float | None, short: bool = False) -> str:
    if value is None or value == 0:
        return "—"
    if short:
        if abs(value) >= 1_000_000:
            return f"${value/1_000_000:.1f}M"
        if abs(value) >= 1_000:
            return f"${value/1_000:.0f}K"
    return format_currency(value, "USD", locale="en_US", format_type="standard")


def fmt_limit(value: float | None) -> str:
    if value is None or value == 0:
        return "—"
    return fmt_currency(value, short=True)


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value*100:.1f}%"


def fmt_days(days: int | None) -> str:
    if days is None:
        return "—"
    if days < 0:
        return f"{abs(days)}d ago"
    return f"{days}d"


# ─── TABLE BUILDERS ──────────────────────────────────────────────────────────

def client_table(rows) -> Table:
    t = Table(
        title="Clients",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        expand=False,
    )
    t.add_column("ID", style="dim", width=5)
    t.add_column("Client Name", min_width=30)
    t.add_column("Segment", min_width=22)
    t.add_column("Policies", justify="right", width=8)
    t.add_column("Total Premium", justify="right", min_width=15)
    t.add_column("Next Renewal", justify="right", width=13)
    t.add_column("Activity (90d)", justify="right", width=14)

    for r in rows:
        next_days = r["next_renewal_days"]
        if next_days is not None:
            if next_days <= 0:
                nr = Text(f"{abs(next_days)}d ago", style="bold red")
            elif next_days <= 90:
                nr = Text(f"{next_days}d", style="bold yellow")
            elif next_days <= 180:
                nr = Text(f"{next_days}d", style="cyan")
            else:
                nr = Text(f"{next_days}d", style="green")
        else:
            nr = Text("—", style="dim")

        t.add_row(
            str(r["id"]),
            r["name"],
            r["industry_segment"],
            str(r["total_policies"]),
            fmt_currency(r["total_premium"]),
            nr,
            str(r["activity_last_90d"]),
        )
    return t


def policy_table(rows, title: str = "Policies") -> Table:
    t = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        expand=True,
    )
    t.add_column("UID", style="dim", width=8)
    t.add_column("Client", min_width=20)
    t.add_column("Line of Business", min_width=24)
    t.add_column("Carrier", min_width=18)
    t.add_column("Expires", width=11)
    t.add_column("Days", justify="right", width=6)
    t.add_column("Premium", justify="right", min_width=12)
    t.add_column("Limit", justify="right", min_width=10)
    t.add_column("Status", width=14)
    t.add_column("!", width=8)

    for r in rows:
        urgency = r["urgency"] if "urgency" in r.keys() else "OK"
        t.add_row(
            r["policy_uid"],
            r["client_name"],
            r["policy_type"],
            r["carrier"],
            r["expiration_date"],
            fmt_days(r["days_to_renewal"] if "days_to_renewal" in r.keys() else None),
            fmt_currency(r["premium"]),
            fmt_limit(r["limit_amount"]),
            r["renewal_status"],
            urgency_text(urgency),
        )
    return t


def renewal_table(rows, title: str = "Renewal Pipeline") -> Table:
    t = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        expand=True,
    )
    t.add_column("UID", style="dim", width=8)
    t.add_column("Client", min_width=24)
    t.add_column("Line of Business", min_width=24)
    t.add_column("Carrier", min_width=18)
    t.add_column("Expires", width=11)
    t.add_column("Days", justify="right", width=6)
    t.add_column("!", width=9)
    t.add_column("Premium", justify="right", min_width=12)
    t.add_column("Renewal Status", min_width=14)
    t.add_column("Colleague", min_width=14)

    for r in rows:
        urgency = r["urgency"]
        days = r["days_to_renewal"]
        t.add_row(
            r["policy_uid"],
            r["client_name"],
            r["policy_type"],
            r["carrier"],
            r["expiration_date"],
            fmt_days(days),
            urgency_text(urgency),
            fmt_currency(r["premium"]),
            r["renewal_status"],
            r["placement_colleague"] or "—",
        )
    return t


def activity_table(rows, title: str = "Activity Log") -> Table:
    t = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        expand=True,
    )
    t.add_column("ID", style="dim", width=6)
    t.add_column("Date", width=11)
    t.add_column("Client", min_width=22)
    t.add_column("Type", width=18)
    t.add_column("Subject", min_width=30)
    t.add_column("Follow-Up", width=11)
    t.add_column("Done", width=5)

    for r in rows:
        fu_date = r["follow_up_date"] or "—"
        done = "[green]✓[/green]" if r["follow_up_done"] else "—"
        t.add_row(
            str(r["id"]),
            r["activity_date"],
            r["client_name"],
            r["activity_type"],
            r["subject"],
            fu_date,
            done,
        )
    return t


def overdue_table(rows) -> Table:
    t = Table(
        title="[bold red]Overdue Follow-Ups[/bold red]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        expand=True,
    )
    t.add_column("ID", style="dim", width=6)
    t.add_column("Client", min_width=22)
    t.add_column("Type", width=18)
    t.add_column("Subject", min_width=30)
    t.add_column("Follow-Up Due", width=13)
    t.add_column("Days Overdue", justify="right", width=13)

    for r in rows:
        overdue_days = r["days_overdue"]
        style = "bold red" if overdue_days > 7 else "yellow"
        t.add_row(
            str(r["id"]),
            r["client_name"],
            r["activity_type"],
            r["subject"],
            r["follow_up_date"],
            Text(str(overdue_days), style=style),
        )
    return t


def tower_panel(client_name: str, rows) -> None:
    """Print tower/layer structure for a client."""
    from collections import defaultdict
    towers: dict[str, list] = defaultdict(list)
    standalones = []
    for r in rows:
        group = r["tower_group"]
        if group:
            towers[group].append(r)
        else:
            standalones.append(r)

    console.print(f"\n[bold]Tower / Layer Structure: {client_name}[/bold]\n")

    for group_name, layers in towers.items():
        t = Table(title=f"[bold cyan]{group_name}[/bold cyan]", box=box.SIMPLE_HEAD)
        t.add_column("Layer", min_width=22)
        t.add_column("Carrier", min_width=16)
        t.add_column("Limit", justify="right", min_width=12)
        t.add_column("Premium", justify="right", min_width=12)
        t.add_column("Expires", width=11)
        t.add_column("Status", min_width=12)
        for r in layers:
            t.add_row(
                r["layer_position"] or "—",
                r["carrier"],
                fmt_limit(r["limit_amount"]),
                fmt_currency(r["premium"]),
                r["expiration_date"],
                r["renewal_status"],
            )
        console.print(t)

    if standalones:
        t = Table(title="[bold]Standalone / Primary[/bold]", box=box.SIMPLE_HEAD)
        t.add_column("Line of Business", min_width=24)
        t.add_column("Carrier", min_width=16)
        t.add_column("Limit", justify="right", min_width=12)
        t.add_column("Premium", justify="right", min_width=12)
        t.add_column("Expires", width=11)
        for r in standalones:
            t.add_row(
                r["policy_type"],
                r["carrier"],
                fmt_limit(r["limit_amount"]),
                fmt_currency(r["premium"]),
                r["expiration_date"],
            )
        console.print(t)


def renewal_dashboard(metrics: dict, pipeline_rows) -> None:
    """Print the renewal dashboard panel."""
    book = metrics.get("book", {})

    # Urgency summary panel
    urgency_lines = []
    for urg in ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]:
        data = metrics.get(urg, {"count": 0, "premium": 0})
        style = URGENCY_STYLES.get(urg, "white")
        urgency_lines.append(
            f"  [{style}]{urg:<10}[/{style}] {data['count']:>3} policies   {fmt_currency(data['premium'])}"
        )

    summary = Panel(
        "\n".join([
            f"[bold]Total Clients:[/bold]   {book.get('total_clients', 0)}",
            f"[bold]Total Policies:[/bold]  {book.get('total_policies', 0)}",
            f"[bold]Total Premium:[/bold]   {fmt_currency(book.get('total_premium', 0))}",
            "",
            "[bold]Urgency Breakdown:[/bold]",
        ] + urgency_lines),
        title="[bold white]Renewal Dashboard[/bold white]",
        border_style="blue",
    )
    console.print(summary)
    console.print()
    console.print(renewal_table(pipeline_rows))


def calendar_table(rows, months: int) -> Table:
    t = Table(
        title=f"Renewal Calendar — Next {months} Months",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
    )
    t.add_column("Month", width=10)
    t.add_column("Policies", justify="right", width=9)
    t.add_column("Premium Expiring", justify="right", min_width=16)
    t.add_column("Details", min_width=40)

    for r in rows:
        t.add_row(
            r["month"],
            str(r["policy_count"]),
            fmt_currency(r["total_premium"]),
            (r["policies"] or "")[:80],
        )
    return t


def history_table(rows, client_name: str) -> Table:
    t = Table(
        title=f"Premium History: {client_name}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
    )
    t.add_column("Line of Business", min_width=24)
    t.add_column("Term", width=22)
    t.add_column("Carrier", min_width=16)
    t.add_column("Premium", justify="right", min_width=12)
    t.add_column("Limit", justify="right", min_width=12)
    for r in rows:
        t.add_row(
            r["policy_type"],
            f"{r['term_effective']} → {r['term_expiration']}",
            r["carrier"] or "—",
            fmt_currency(r["premium"]),
            fmt_limit(r["limit_amount"]),
        )
    return t


# ─── FORMAT DISPATCH ─────────────────────────────────────────────────────────

def rows_to_csv(rows, columns: list[str] | None = None) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    cols = columns or list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r[k] for k in cols if k in r.keys()})
    return buf.getvalue()


def rows_to_json(rows, extra: dict | None = None) -> str:
    data = [dict(r) for r in rows]
    if extra:
        return json.dumps({**extra, "data": data}, indent=2, default=str)
    return json.dumps(data, indent=2, default=str)
