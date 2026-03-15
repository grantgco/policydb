"""CSV/JSON import with validation and flexible column handling."""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Any

import click
import dateparser

from policydb import config as cfg
from policydb.db import next_policy_uid


# ─── NORMALIZATION HELPERS ───────────────────────────────────────────────────

def _parse_currency(value: str) -> float:
    """Strip currency symbols, commas; return float."""
    if not value or not str(value).strip():
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_date(value: str) -> str | None:
    """Parse various date formats to YYYY-MM-DD string."""
    if not value or not str(value).strip():
        return None
    parsed = dateparser.parse(str(value).strip(), settings={"PREFER_DAY_OF_MONTH": "first"})
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    return None


def _parse_bool(value: str) -> int:
    """Parse boolean-ish values to 0 or 1."""
    if not value:
        return 0
    return 1 if str(value).strip().lower() in ("1", "true", "yes", "y") else 0


def _normalize_renewal_status(value: str) -> str:
    valid = {"Not Started", "In Progress", "Pending Bind", "Bound"}
    v = str(value).strip().title() if value else "Not Started"
    # Fuzzy match
    from rapidfuzz import process, fuzz
    result = process.extractOne(v, valid, scorer=fuzz.WRatio, score_cutoff=60)
    return result[0] if result else "Not Started"


# ─── CLIENT IMPORTER ─────────────────────────────────────────────────────────

class ClientImporter:
    REQUIRED = {"name", "industry_segment"}

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.imported = 0
        self.skipped = 0
        self.warnings: list[str] = []

    def import_csv(self, path: Path) -> None:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            click.echo("No data rows found.")
            return

        headers = {h.strip().lower() for h in rows[0].keys()}
        missing_required = self.REQUIRED - headers
        if missing_required:
            raise click.ClickException(f"Missing required columns: {missing_required}")

        account_exec = cfg.get("default_account_exec", "Grant")

        for i, raw in enumerate(rows, start=2):
            row = {k.strip().lower(): v.strip() if isinstance(v, str) else v for k, v in raw.items()}
            name = row.get("name", "").strip()
            if not name:
                self.warnings.append(f"Row {i}: empty name, skipping")
                self.skipped += 1
                continue

            existing = self.conn.execute(
                "SELECT id FROM clients WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchone()
            if existing:
                self.warnings.append(f"Row {i}: client '{name}' already exists, skipping")
                self.skipped += 1
                continue

            self.conn.execute(
                """INSERT INTO clients
                   (name, industry_segment, primary_contact, contact_email,
                    contact_phone, address, notes, account_exec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    row.get("industry_segment", ""),
                    row.get("primary_contact") or None,
                    row.get("contact_email") or None,
                    row.get("contact_phone") or None,
                    row.get("address") or None,
                    row.get("notes") or None,
                    account_exec,
                ),
            )
            self.imported += 1

        self.conn.commit()
        self._print_summary()

    def _print_summary(self) -> None:
        click.echo(f"\nClient import complete: {self.imported} imported, {self.skipped} skipped")
        for w in self.warnings:
            click.echo(f"  [warn] {w}")


# ─── POLICY IMPORTER ─────────────────────────────────────────────────────────

class PolicyImporter:
    REQUIRED = {"client_name", "policy_type", "carrier", "effective_date", "expiration_date", "premium"}
    # Column aliases: normalize various header names
    ALIASES = {
        "insured": "client_name",
        "client": "client_name",
        "line_of_business": "policy_type",
        "lob": "policy_type",
        "type": "policy_type",
        "limit": "limit_amount",
        "limits": "limit_amount",
        "ded": "deductible",
        "ded.": "deductible",
        "effective": "effective_date",
        "expiration": "expiration_date",
        "expiry": "expiration_date",
        "exp_date": "expiration_date",
        "eff_date": "effective_date",
        "colleague": "placement_colleague",
        "underwriter": "underwriter_name",
        "uw": "underwriter_name",
        "commission": "commission_rate",
        "comm_rate": "commission_rate",
        "status": "renewal_status",
        "standalone": "is_standalone",
        "layer": "layer_position",
        "tower": "tower_group",
        "prior": "prior_premium",
        "pol_number": "policy_number",
        "pol_num": "policy_number",
        "pol_no": "policy_number",
        "coverage": "policy_type",
        "coverage_type": "policy_type",
        "line": "policy_type",
        "carrier_name": "carrier",
        "company": "carrier",
        "insurer": "carrier",
        "insured_name": "client_name",
        "named_insured": "client_name",
        "policy_expiry": "expiration_date",
        "expiry_date": "expiration_date",
        "policy_expiration": "expiration_date",
        "annual_premium": "premium",
        "written_premium": "premium",
        "total_premium": "premium",
        "eff": "effective_date",
        "first_named_insured": "first_named_insured",
        "fni": "first_named_insured",
        "named_insured_1": "first_named_insured",
        "first_insured": "first_named_insured",
        "access_point": "access_point",
        "access": "access_point",
        "entry_point": "access_point",
    }

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.imported = 0
        self.skipped = 0
        self.warnings: list[str] = []
        self._missing_descriptions: list[str] = []

    def _normalize_headers(self, raw_headers: list[str]) -> dict[str, str]:
        """Map raw CSV headers to canonical field names."""
        mapping = {}
        for h in raw_headers:
            if h is None:
                continue
            key = h.strip().lower().replace(" ", "_").replace("-", "_")
            canonical = self.ALIASES.get(key, key)
            mapping[h] = canonical
        return mapping

    def _get_or_create_client(self, name: str) -> int | None:
        """Return client_id, creating if needed with user prompt."""
        row = self.conn.execute(
            "SELECT id FROM clients WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if row:
            return row["id"]

        click.echo(f"\nClient '{name}' not found.")
        if click.confirm(f"  Create new client '{name}'?", default=True):
            industry = click.prompt(
                "  Industry segment",
                type=click.Choice(cfg.get("industry_segments"), case_sensitive=False),
            )
            account_exec = cfg.get("default_account_exec", "Grant")
            cursor = self.conn.execute(
                "INSERT INTO clients (name, industry_segment, account_exec) VALUES (?, ?, ?)",
                (name, industry, account_exec),
            )
            self.conn.commit()
            return cursor.lastrowid
        return None

    def import_csv(self, path: Path, interactive: bool = True) -> None:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)

        if not raw_rows:
            click.echo("No data rows found.")
            return

        header_map = self._normalize_headers(list(raw_rows[0].keys()))
        missing_req = self.REQUIRED - set(header_map.values())
        if missing_req:
            raise click.ClickException(f"Missing required columns: {missing_req}")

        account_exec = cfg.get("default_account_exec", "Grant")
        seen_policy_numbers: dict[str, int] = {}

        for i, raw in enumerate(raw_rows, start=2):
            row: dict[str, Any] = {
                header_map[k]: v.strip() if isinstance(v, str) else v
                for k, v in raw.items()
                if k is not None and k in header_map
            }

            client_name = row.get("client_name", "").strip()
            if not client_name:
                self.warnings.append(f"Row {i}: empty client_name, skipping")
                self.skipped += 1
                continue

            policy_type = row.get("policy_type", "").strip()
            carrier = row.get("carrier", "").strip()
            if not policy_type or not carrier:
                self.warnings.append(f"Row {i}: missing policy_type or carrier, skipping")
                self.skipped += 1
                continue

            eff = _parse_date(row.get("effective_date", ""))
            exp = _parse_date(row.get("expiration_date", ""))
            if not eff or not exp:
                self.warnings.append(f"Row {i}: invalid dates ('{row.get('effective_date')}' / '{row.get('expiration_date')}'), skipping")
                self.skipped += 1
                continue

            premium = _parse_currency(row.get("premium", "0"))

            client_id = self._get_or_create_client(client_name)
            if client_id is None:
                self.skipped += 1
                continue

            # Duplicate policy number check
            pol_number = row.get("policy_number", "").strip() or None
            if pol_number and interactive:
                existing = self.conn.execute(
                    "SELECT policy_uid FROM policies WHERE policy_number = ? AND archived = 0",
                    (pol_number,),
                ).fetchone()
                if existing:
                    if pol_number in seen_policy_numbers:
                        self.warnings.append(f"Row {i}: policy number '{pol_number}' already imported this session, skipping")
                        self.skipped += 1
                        continue
                    overwrite = click.confirm(
                        f"  Policy number '{pol_number}' exists ({existing['policy_uid']}). Overwrite?",
                        default=False,
                    )
                    if not overwrite:
                        self.skipped += 1
                        continue

            uid = next_policy_uid(self.conn)
            description = row.get("description", "").strip() or None
            limit_amount = _parse_currency(row.get("limit_amount", "0"))
            deductible = _parse_currency(row.get("deductible", "0"))
            commission_rate = _parse_currency(row.get("commission_rate", "0"))
            prior_premium = _parse_currency(row.get("prior_premium", "0")) or None

            self.conn.execute(
                """INSERT INTO policies
                   (policy_uid, client_id, policy_type, carrier, policy_number,
                    effective_date, expiration_date, premium, limit_amount, deductible,
                    description, coverage_form, layer_position, tower_group, is_standalone,
                    placement_colleague, underwriter_name, underwriter_contact,
                    renewal_status, commission_rate, prior_premium, account_exec, notes,
                    access_point)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    client_id,
                    policy_type,
                    carrier,
                    pol_number,
                    eff,
                    exp,
                    premium,
                    limit_amount or None,
                    deductible or None,
                    description,
                    row.get("coverage_form") or None,
                    row.get("layer_position") or "Primary",
                    row.get("tower_group") or None,
                    _parse_bool(row.get("is_standalone", "0")),
                    row.get("placement_colleague") or None,
                    row.get("underwriter_name") or None,
                    row.get("underwriter_contact") or None,
                    _normalize_renewal_status(row.get("renewal_status", "")),
                    commission_rate or None,
                    prior_premium,
                    account_exec,
                    row.get("notes") or None,
                    row.get("access_point") or None,
                ),
            )

            if pol_number:
                seen_policy_numbers[pol_number] = client_id

            if not description:
                self._missing_descriptions.append(uid)

            self.imported += 1

        self.conn.commit()
        self._print_summary(interactive)

    def _print_summary(self, interactive: bool = True) -> None:
        click.echo(f"\nPolicy import complete: {self.imported} imported, {self.skipped} skipped, {len(self.warnings)} warnings")
        for w in self.warnings:
            click.echo(f"  [warn] {w}")

        missing = len(self._missing_descriptions)
        if missing > 0 and interactive:
            click.echo(f"\n{missing} {'policy' if missing == 1 else 'policies'} missing descriptions.")
            click.echo("Descriptions appear on client-facing schedules of insurance.")


# ─── PREMIUM HISTORY IMPORTER ─────────────────────────────────────────────────

class PremiumHistoryImporter:
    REQUIRED = {"client_name", "policy_type", "term_effective", "term_expiration", "premium"}

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.imported = 0
        self.skipped = 0
        self.warnings: list[str] = []

    def import_csv(self, path: Path) -> None:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            click.echo("No data rows found.")
            return

        headers = {h.strip().lower().replace(" ", "_") for h in rows[0].keys()}
        missing_req = self.REQUIRED - headers
        if missing_req:
            raise click.ClickException(f"Missing required columns: {missing_req}")

        for i, raw in enumerate(rows, start=2):
            row = {k.strip().lower().replace(" ", "_"): v.strip() if isinstance(v, str) else v for k, v in raw.items()}

            client_name = row.get("client_name", "").strip()
            client_row = self.conn.execute(
                "SELECT id FROM clients WHERE LOWER(name) = LOWER(?)", (client_name,)
            ).fetchone()
            if not client_row:
                self.warnings.append(f"Row {i}: client '{client_name}' not found, skipping")
                self.skipped += 1
                continue

            eff = _parse_date(row.get("term_effective", ""))
            exp = _parse_date(row.get("term_expiration", ""))
            if not eff or not exp:
                self.warnings.append(f"Row {i}: invalid dates, skipping")
                self.skipped += 1
                continue

            try:
                self.conn.execute(
                    """INSERT INTO premium_history
                       (client_id, policy_type, carrier, term_effective, term_expiration,
                        premium, limit_amount, deductible, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        client_row["id"],
                        row.get("policy_type", ""),
                        row.get("carrier") or None,
                        eff,
                        exp,
                        _parse_currency(row.get("premium", "0")),
                        _parse_currency(row.get("limit", "0")) or None,
                        _parse_currency(row.get("deductible", "0")) or None,
                        row.get("notes") or None,
                    ),
                )
                self.imported += 1
            except Exception as e:
                self.warnings.append(f"Row {i}: {e}")
                self.skipped += 1

        self.conn.commit()
        click.echo(f"\nHistory import complete: {self.imported} imported, {self.skipped} skipped, {len(self.warnings)} warnings")
        for w in self.warnings:
            click.echo(f"  [warn] {w}")
