"""Read-only reconciliation logic: compare an uploaded CSV against PolicyDB policies."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from rapidfuzz import fuzz, process

from policydb.importer import PolicyImporter, _parse_currency, _parse_date
from policydb.utils import _COVERAGE_ALIASES, normalize_coverage_type


def _normalize_coverage(value: str) -> str:
    """Normalize a policy type / line of business name to a canonical form."""
    return normalize_coverage_type(value)


_LEGAL_SUFFIX_RE = re.compile(
    r'\s*(,?\s*)?(LLC|LLP|LP|PLLC|Inc\.?|Corp\.?|Corporation|Co\.?|'
    r'Ltd\.?|Limited|Company|Enterprises?)\b[.,]?$',
    re.IGNORECASE,
)


_PLACEHOLDER_POLICY_NUMBERS = {
    "999", "TBD", "TBA", "PENDING", "NA", "N/A", "NONE", "XXX", "000", "123",
    "NEW", "RENEWAL", "RENEW", "QUOTE", "QUOTED", "APPLIED",
}

def _normalize_policy_number(pn: str) -> str:
    """Normalize a policy number for comparison — strip formatting characters."""
    if not pn:
        return ""
    # Remove spaces, dashes, slashes, dots; uppercase
    normalized = re.sub(r'[\s\-/.]', '', pn.strip().upper())
    # Strip leading zeros
    normalized = normalized.lstrip('0') or '0'
    # Skip placeholders — these cause false matches across unrelated policies
    if normalized in _PLACEHOLDER_POLICY_NUMBERS:
        return ""
    return normalized


def _normalize_client_name(name: str) -> str:
    """Strip common legal entity suffixes and normalize case before fuzzy scoring."""
    if not name:
        return name
    cleaned = re.sub(r'\s+', ' ', _LEGAL_SUFFIX_RE.sub('', name.strip())).strip()
    # Title-case to normalize "AVALONBAY COMMUNITIES" vs "Avalonbay Communities"
    return cleaned.title()


# ─── TYPES ────────────────────────────────────────────────────────────────────

MatchStatus = Literal["MATCH", "DIFF", "MISSING", "EXTRA"]

COMPARE_FIELDS = [
    "client_name",
    "policy_type",
    "carrier",
    "policy_number",
    "effective_date",
    "expiration_date",
    "premium",
    "limit_amount",
    "deductible",
]

_CURRENCY_FIELDS = {"premium", "limit_amount", "deductible"}
_DATE_FIELDS = {"effective_date", "expiration_date"}
_TEXT_FIELDS = {"client_name", "policy_type", "carrier"}

_STATUS_SORT = {"DIFF": 0, "MISSING": 1, "EXTRA": 2, "MATCH": 3}


@dataclass
class ReconcileRow:
    status: MatchStatus
    ext: dict | None        # uploaded record; None for EXTRA
    db: dict | None         # PolicyDB record; None for MISSING
    diff_fields: list[str] = field(default_factory=list)
    match_score: float = 100.0
    # Matching metadata
    match_method: str = ""          # "policy_number", "date_pair", "fuzzy", "manual", ""
    eff_delta_days: int | None = None
    exp_delta_days: int | None = None
    ext_type_raw: str = ""          # original coverage name from upload
    ext_type_normalized: str = ""   # after _normalize_coverage()
    coverage_alias_applied: bool = False  # True if normalization changed the name
    cosmetic_diffs: list[str] = field(default_factory=list)  # diffs that are only cosmetic (normalized values match)
    fillable_fields: list[str] = field(default_factory=list)  # DB fields that are 0/null but ext has a value (optional auto-fill)
    is_program_match: bool = False  # True if matched to a program record
    matched_carrier_id: int | None = None  # ID of matched program_carriers row


# ─── FILE PARSING ─────────────────────────────────────────────────────────────

def _normalize_headers(raw_headers: list[str]) -> dict[str, str]:
    """Map raw CSV column names to canonical PolicyDB field names via PolicyImporter.ALIASES."""
    aliases = PolicyImporter.ALIASES
    mapping = {}
    for h in raw_headers:
        if h is None:
            continue
        key = h.strip().lower().replace(" ", "_").replace("-", "_")
        canonical = aliases.get(key, key)
        mapping[h] = canonical
    return mapping


def _process_raw_rows(
    raw_rows: list[dict],
    column_mapping: dict | None,
    warnings: list[str],
) -> list[dict]:
    """Normalize, map, and parse a list of raw row dicts (from CSV or XLSX)."""
    if not raw_rows:
        warnings.append("No data rows found in uploaded file.")
        return []

    if column_mapping:
        raw_headers = list(raw_rows[0].keys())
        header_map = {h: column_mapping.get(h, h) for h in raw_headers}
    else:
        header_map = _normalize_headers(list(raw_rows[0].keys()))

    rows: list[dict] = []
    for i, raw in enumerate(raw_rows, start=2):  # row 1 = header
        row: dict = {}
        for raw_key, value in raw.items():
            canonical = header_map.get(raw_key, raw_key)
            row[canonical] = (str(value).strip() if value is not None else "")

        if not row.get("client_name") and not row.get("policy_number"):
            warnings.append(f"Row {i}: skipped — no client name or policy number found.")
            continue

        for field_name in ("effective_date", "expiration_date"):
            if row.get(field_name):
                parsed = _parse_date(row[field_name])
                if parsed:
                    row[field_name] = parsed
                else:
                    warnings.append(f"Row {i}: could not parse date '{row[field_name]}' for {field_name}.")
                    row[field_name] = ""

        for field_name in ("premium", "limit_amount", "deductible"):
            if row.get(field_name):
                row[field_name] = _parse_currency(row[field_name])
            else:
                row[field_name] = 0.0

        rows.append(row)
    return rows


def _parse_csv_content(content: bytes, column_mapping: dict | None) -> tuple[list[dict], list[str]]:
    """Parse CSV bytes into normalized dicts."""
    warnings: list[str] = []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        warnings.append("File encoding detected as Latin-1 (not UTF-8). Special characters may be affected.")

    reader = csv.DictReader(io.StringIO(text))
    raw_rows = list(reader)
    rows = _process_raw_rows(raw_rows, column_mapping, warnings)
    return rows, warnings


def _parse_xlsx_content(content: bytes, column_mapping: dict | None) -> tuple[list[dict], list[str]]:
    """Parse XLSX bytes into normalized dicts using openpyxl."""
    from openpyxl import load_workbook

    warnings: list[str] = []
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        return [], [f"Could not read Excel file: {e}"]

    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    # First row = headers
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], ["Excel file has no data."]

    headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(header_row)]

    raw_rows: list[dict] = []
    for row_vals in rows_iter:
        if all(v is None for v in row_vals):
            continue  # skip blank rows
        raw = {}
        for h, v in zip(headers, row_vals):
            if v is None:
                raw[h] = ""
            elif isinstance(v, datetime):
                raw[h] = v.strftime("%Y-%m-%d")
            else:
                raw[h] = str(v).strip()
        raw_rows.append(raw)

    wb.close()
    rows = _process_raw_rows(raw_rows, column_mapping, warnings)
    return rows, warnings


def parse_uploaded_file(
    content: bytes,
    column_mapping: dict | None = None,
    filename: str = "",
) -> tuple[list[dict], list[str]]:
    """
    Parse uploaded CSV or XLSX bytes into normalized dicts.

    Auto-detects format by filename extension or content magic bytes.
    """
    if filename.lower().endswith(('.xlsx', '.xls')) or content[:2] == b'PK':
        return _parse_xlsx_content(content, column_mapping)
    return _parse_csv_content(content, column_mapping)


# Backward compatibility alias
parse_uploaded_csv = parse_uploaded_file


# ─── FIELD COMPARISON ─────────────────────────────────────────────────────────

def _date_delta_days(d1: str | None, d2: str | None) -> int | None:
    """Return absolute day difference between two YYYY-MM-DD strings, or None."""
    if not d1 or not d2:
        return None
    try:
        dt1 = datetime.strptime(d1, "%Y-%m-%d")
        dt2 = datetime.strptime(d2, "%Y-%m-%d")
        return abs((dt1 - dt2).days)
    except ValueError:
        return None


def _compare_fields(ext: dict, db: dict) -> tuple[list[str], list[str], list[str], float]:
    """
    Compare COMPARE_FIELDS between ext and db records.

    Returns:
        diff_fields: list of field names that truly differ
        cosmetic_diffs: list of fields where raw strings differ but normalized values match
        fillable_fields: list of currency/date fields where DB is 0/null but ext has a value
        score: minimum WRatio across text fields (confidence)
    """
    diff_fields: list[str] = []
    cosmetic_diffs: list[str] = []
    fillable_fields: list[str] = []
    min_text_score = 100.0

    for f in COMPARE_FIELDS:
        ext_val = ext.get(f)
        db_val = db.get(f)

        # Universal fillable check: ext has value, DB is empty/null/0
        ext_has = bool(str(ext_val or "").strip()) if f not in _CURRENCY_FIELDS else (float(ext_val) > 0 if ext_val else False)
        db_empty = not str(db_val or "").strip() if f not in _CURRENCY_FIELDS else (float(db_val or 0) == 0)

        if f in _TEXT_FIELDS:
            if ext_val and db_val:
                # Normalize coverage names before comparing
                ev = _normalize_coverage(str(ext_val)) if f == "policy_type" else str(ext_val)
                dv = _normalize_coverage(str(db_val)) if f == "policy_type" else str(db_val)
                score = fuzz.WRatio(ev, dv)
                min_text_score = min(min_text_score, score)
                if score < 85:
                    diff_fields.append(f)
                elif f == "policy_type" and str(ext_val).strip().lower() != str(db_val).strip().lower():
                    cosmetic_diffs.append(f)
            elif ext_has and db_empty and f not in ("client_name", "policy_type"):
                # DB missing carrier — fillable (skip client_name/policy_type as those are match keys)
                fillable_fields.append(f)

        elif f in _DATE_FIELDS:
            if ext_has and db_empty:
                fillable_fields.append(f)
            else:
                delta = _date_delta_days(ext_val or "", db_val or "")
                if delta is not None and delta > 14:
                    diff_fields.append(f)

        elif f in _CURRENCY_FIELDS:
            try:
                ev = float(ext_val) if ext_val else 0.0
                dv = float(db_val) if db_val else 0.0
                if ev > 0 and dv > 0:
                    pct_diff = abs(ev - dv) / max(ev, dv)
                    if pct_diff > 0.01:
                        diff_fields.append(f)
                elif ev > 0 and dv == 0:
                    fillable_fields.append(f)
            except (TypeError, ValueError):
                pass

        elif f == "policy_number":
            ext_pn = (ext_val or "").strip().upper()
            db_pn = (db_val or "").strip().upper()
            if ext_pn and db_pn and ext_pn != db_pn:
                diff_fields.append(f)
            elif ext_pn and not db_pn:
                fillable_fields.append(f)

    return diff_fields, cosmetic_diffs, fillable_fields, min_text_score


def _attach_metadata(row: ReconcileRow, match_method: str) -> None:
    """Populate metadata fields on a ReconcileRow after matching."""
    row.match_method = match_method
    ext = row.ext or {}
    db = row.db or {}

    # Coverage normalization tracking
    raw_type = ext.get("policy_type", "")
    if raw_type:
        normalized = _normalize_coverage(raw_type)
        row.ext_type_raw = raw_type
        row.ext_type_normalized = normalized
        row.coverage_alias_applied = (raw_type.strip().lower() != normalized.lower())

    # Date deltas
    row.eff_delta_days = _date_delta_days(
        ext.get("effective_date", ""), db.get("effective_date", "")
    )
    row.exp_delta_days = _date_delta_days(
        ext.get("expiration_date", ""), db.get("expiration_date", "")
    )


# ─── MATCHING ─────────────────────────────────────────────────────────────────

def _same_year(d1: str | None, d2: str | None) -> bool:
    """Return True if two YYYY-MM-DD strings share the same calendar year."""
    if not d1 or not d2 or len(d1) < 4 or len(d2) < 4:
        return False
    return d1[:4] == d2[:4]


def _fuzzy_match(ext_row: dict, candidates: list[dict], date_priority: bool = False, single_client: bool = False) -> tuple[dict | None, float]:
    """
    Find the best fuzzy match for ext_row among candidates.

    Scoring pipeline:
      - client_name WRatio must be >= 60 (hard filter)
      - policy_type contributes to score but does NOT gate (user reconciles type manually)
      - Base: client × 0.60 + type × 0.20
      - expiration date: +25 if ≤14d, +15 if ≤45d, +5 if same year, −10 if >60d
      - effective date: +15 if ≤14d, +10 if ≤45d
      - carrier: +10 if WRatio >= 70
      - policy number (normalized): +50 exact, +40 fuzzy ≥90, +20 fuzzy ≥75
      - Accept if combined score >= 65

    When date_priority=True:
      - Base: client × 0.50 only (type ignored)
      - Effective date: +35 if ≤14d, +25 if ≤45d
      - Expiration date: +30 if ≤14d, +20 if ≤45d
      - Acceptance threshold lowered to 55
    """
    if not ext_row.get("client_name"):
        return None, 0.0

    ext_client = ext_row.get("client_name", "")
    ext_client_norm = _normalize_client_name(ext_client)
    ext_fni = ext_row.get("first_named_insured", "")
    ext_fni_norm = _normalize_client_name(ext_fni) if ext_fni else ""
    ext_type = _normalize_coverage(ext_row.get("policy_type", ""))
    ext_exp = ext_row.get("expiration_date", "")
    ext_eff = ext_row.get("effective_date", "")
    ext_carrier = ext_row.get("carrier", "")
    ext_pn = _normalize_policy_number(ext_row.get("policy_number") or "")

    best_candidate = None
    best_score = 0.0

    for db in candidates:
        # When reconciling a single client, skip client name comparison entirely
        if single_client:
            client_score = 100.0
        else:
            # Hard filter: client name must be recognizably the same
            # Also check FNI as a bonus — can improve but never reduces score
            db_client_norm = _normalize_client_name(db.get("client_name", ""))
            db_fni_norm = _normalize_client_name(db.get("first_named_insured", "")) if db.get("first_named_insured") else ""

            client_score = fuzz.WRatio(ext_client_norm, db_client_norm)

            # FNI cross-matching: ext client vs db FNI, ext FNI vs db client
            if db_fni_norm:
                client_score = max(client_score, fuzz.WRatio(ext_client_norm, db_fni_norm))
            if ext_fni_norm:
                client_score = max(client_score, fuzz.WRatio(ext_fni_norm, db_client_norm))
                if db_fni_norm:
                    client_score = max(client_score, fuzz.WRatio(ext_fni_norm, db_fni_norm))

            if client_score < 60:
                continue

        # Programs with structured carrier rows: carrier match is a bonus, not a gate.
        # A program might have a carrier row with a slightly different name (data quality).
        # Don't skip — let the scoring handle it.

        # Type scoring — no hard gate; contributes to base score only
        db_type = _normalize_coverage(db.get("policy_type", ""))
        type_score = fuzz.WRatio(ext_type, db_type) if (ext_type and db_type) else 50

        # Base score
        if date_priority:
            combined = client_score * 0.50  # type ignored in date-priority mode
        else:
            combined = client_score * 0.60 + type_score * 0.20

        # Carrier bonus
        if ext_carrier:
            carrier_score = fuzz.WRatio(ext_carrier, db.get("carrier", ""))
            if carrier_score >= 70:
                combined += 10
            elif db.get("is_program") and db.get("_program_carrier_rows"):
                for _pc in db["_program_carrier_rows"]:
                    if fuzz.WRatio(ext_carrier, _pc.get("carrier", "")) >= 70:
                        combined += 10
                        _pc_pn = _normalize_policy_number(_pc.get("policy_number") or "")
                        if ext_pn and _pc_pn:
                            if ext_pn == _pc_pn:
                                combined += 50
                            elif fuzz.ratio(ext_pn, _pc_pn) >= 90:
                                combined += 40
                            elif fuzz.ratio(ext_pn, _pc_pn) >= 75:
                                combined += 20
                        break

        # Expiration date
        db_exp = db.get("expiration_date", "")
        exp_delta = _date_delta_days(ext_exp, db_exp) if (ext_exp and db_exp) else None
        if exp_delta is not None:
            if date_priority:
                if exp_delta <= 14:
                    combined += 30
                elif exp_delta <= 45:
                    combined += 20
                elif _same_year(ext_exp, db_exp):
                    combined += 10
                else:
                    combined -= 5
            else:
                if exp_delta <= 14:
                    combined += 25
                elif exp_delta <= 45:
                    combined += 15
                elif _same_year(ext_exp, db_exp):
                    combined += 5
                else:
                    combined -= 10

        # Effective date
        db_eff = db.get("effective_date", "")
        eff_delta = _date_delta_days(ext_eff, db_eff) if (ext_eff and db_eff) else None
        if eff_delta is not None:
            if date_priority:
                if eff_delta <= 14:
                    combined += 35
                elif eff_delta <= 45:
                    combined += 25
            else:
                if eff_delta <= 14:
                    combined += 15
                elif eff_delta <= 45:
                    combined += 10

        # Policy number — strongest match signal after client name
        db_pn = _normalize_policy_number(db.get("policy_number") or "")
        if ext_pn and db_pn:
            if ext_pn == db_pn:
                combined += 50   # exact match — near-certain identification
            else:
                pn_score = fuzz.ratio(ext_pn, db_pn)
                if pn_score >= 90:
                    combined += 40
                elif pn_score >= 75:
                    combined += 20

        if combined > best_score:
            best_score = combined
            best_candidate = db

    threshold = 55 if date_priority else 65
    if best_score < threshold:
        return None, 0.0

    return best_candidate, best_score


def find_candidates(ext_row: dict, db_rows: list[dict], limit: int = 8, single_client: bool = False) -> list[tuple[dict, float]]:
    """
    Return top candidate DB rows for a given ext_row, for manual match selection.
    Uses a wider tolerance than the automatic matcher to surface more options.
    Returns list of (db_row, score) sorted by score descending.
    """
    ext_client = ext_row.get("client_name", "")
    ext_client_norm = _normalize_client_name(ext_client) if ext_client else ""
    ext_fni = ext_row.get("first_named_insured", "")
    ext_fni_norm = _normalize_client_name(ext_fni) if ext_fni else ""
    ext_type = _normalize_coverage(ext_row.get("policy_type", ""))
    ext_exp = ext_row.get("expiration_date", "")
    ext_eff = ext_row.get("effective_date", "")
    ext_carrier = ext_row.get("carrier", "")

    scored: list[tuple[dict, float]] = []

    for db in db_rows:
        if single_client:
            client_score = 100.0
        else:
            db_client_norm = _normalize_client_name(db.get("client_name", ""))
            db_fni_norm = _normalize_client_name(db.get("first_named_insured", "")) if db.get("first_named_insured") else ""

            client_score = fuzz.WRatio(ext_client_norm, db_client_norm) if ext_client_norm else 0
            # FNI cross-matching bonus
            if db_fni_norm and ext_client_norm:
                client_score = max(client_score, fuzz.WRatio(ext_client_norm, db_fni_norm))
            if ext_fni_norm:
                client_score = max(client_score, fuzz.WRatio(ext_fni_norm, db_client_norm))
                if db_fni_norm:
                    client_score = max(client_score, fuzz.WRatio(ext_fni_norm, db_fni_norm))

            if client_score < 50:
                continue

        type_score = fuzz.WRatio(ext_type, _normalize_coverage(db.get("policy_type", ""))) if ext_type else 50
        carrier_score = fuzz.WRatio(ext_carrier, db.get("carrier", "")) if ext_carrier else 0

        combined = (client_score + type_score) / 2
        combined += 10 if carrier_score >= 70 else 0
        # Program carrier list boost for suggestions
        if ext_carrier and not (carrier_score >= 70) and db.get("is_program") and db.get("_program_carrier_rows"):
            for _pc in db["_program_carrier_rows"]:
                if fuzz.WRatio(ext_carrier, _pc.get("carrier", "")) >= 70:
                    combined += 10
                    _pc_pn = _normalize_policy_number(_pc.get("policy_number") or "")
                    _ext_pn_s = _normalize_policy_number(ext_row.get("policy_number") or "")
                    if _ext_pn_s and _pc_pn:
                        if _ext_pn_s == _pc_pn:
                            combined += 30
                        elif fuzz.ratio(_ext_pn_s, _pc_pn) >= 90:
                            combined += 25
                        elif fuzz.ratio(_ext_pn_s, _pc_pn) >= 75:
                            combined += 10
                    break

        # Expiration date within 60 days — bonus scoring, not a hard filter for suggestions
        db_exp = db.get("expiration_date", "")
        if ext_exp and db_exp:
            exp_delta = _date_delta_days(ext_exp, db_exp)
            if exp_delta is not None:
                if exp_delta <= 14:
                    combined += 20
                elif exp_delta <= 60:
                    combined += 10

        # Effective date — strong signal for matching (exact date = likely same program)
        db_eff = db.get("effective_date", "")
        if ext_eff and db_eff:
            eff_delta = _date_delta_days(ext_eff, db_eff)
            if eff_delta is not None:
                if eff_delta == 0:
                    combined += 25  # exact effective date match — strong signal
                elif eff_delta <= 14:
                    combined += 15
                elif eff_delta <= 60:
                    combined += 5

        # Policy number — normalized for formatting flexibility
        ext_pn = _normalize_policy_number(ext_row.get("policy_number") or "")
        db_pn = _normalize_policy_number(db.get("policy_number") or "")
        if ext_pn and db_pn:
            if ext_pn == db_pn:
                combined += 30
            elif fuzz.ratio(ext_pn, db_pn) >= 90:
                combined += 20

        scored.append((db, round(combined, 1)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


# ─── MAIN RECONCILE ───────────────────────────────────────────────────────────

def reconcile(ext_rows: list[dict], db_rows: list[dict], date_priority: bool = False, single_client: bool = False) -> list[ReconcileRow]:
    """
    Match ext_rows against db_rows.

    Pass 1: Exact policy number match
    Pass 2: Fuzzy match on client_name + policy_type + expiration_date

    When date_priority=True, Pass 2 uses date-focused scoring (effective/expiration
    dates weighted much higher, coverage type ignored, lower acceptance threshold).
    Pass 3: Remaining DB rows → EXTRA

    Returns rows sorted: DIFF, MISSING, EXTRA, MATCH.
    """
    results: list[ReconcileRow] = []

    # Build DB indexes — use normalized policy numbers for flexible matching
    db_by_polnum: dict[str, dict] = {}
    for db in db_rows:
        pn = _normalize_policy_number(db.get("policy_number") or "")
        if pn and pn not in db_by_polnum:
            db_by_polnum[pn] = db
        # Also index program carrier row policy numbers → parent program
        if db.get("is_program") and db.get("_program_carrier_rows"):
            for pc in db["_program_carrier_rows"]:
                pc_pn = _normalize_policy_number(pc.get("policy_number") or "")
                if pc_pn and pc_pn not in db_by_polnum:
                    db_by_polnum[pc_pn] = db  # maps to the parent program

    db_unmatched: set[int] = set(range(len(db_rows)))
    ext_matched: set[int] = set()

    # Track which db rows are programs (allow multiple matches)
    _program_indices: set[int] = set()
    for idx, db in enumerate(db_rows):
        if db.get("is_program"):
            _program_indices.add(idx)

    def _claim_db(db, db_idx):
        """Remove db row from candidates unless it's a program (programs accept multiple matches)."""
        if db_idx not in _program_indices:
            db_unmatched.discard(db_idx)

    # Pass 1: Exact policy number match (after normalization)
    for i, ext in enumerate(ext_rows):
        ext_pn = _normalize_policy_number(ext.get("policy_number") or "")
        if not ext_pn:
            continue
        db = db_by_polnum.get(ext_pn)
        if db is None:
            continue
        db_idx = db_rows.index(db)
        if db_idx not in db_unmatched:
            continue  # already claimed — send to Pass 2
        _claim_db(db, db_idx)
        ext_matched.add(i)
        # Determine matched carrier ID for program matches
        _matched_cid = None
        _compare_target = db  # default: compare against the DB policy/program record
        if db_idx in _program_indices and db.get("_program_carrier_rows"):
            _ext_carrier = ext.get("carrier", "")
            # First try matching by policy number (strongest signal)
            for _pc in db["_program_carrier_rows"]:
                _pc_pn = _normalize_policy_number(_pc.get("policy_number") or "")
                if ext_pn and _pc_pn and ext_pn == _pc_pn:
                    _matched_cid = _pc.get("id")
                    _compare_target = _pc  # compare against the carrier row, not the program
                    break
            # Fall back to carrier name fuzzy match
            if not _matched_cid and _ext_carrier:
                for _pc in db["_program_carrier_rows"]:
                    if fuzz.WRatio(_ext_carrier, _pc.get("carrier", "")) >= 70:
                        _matched_cid = _pc.get("id")
                        _compare_target = _pc
                        break
        diff_fields, cosmetic, fillable, score = _compare_fields(ext, _compare_target)
        status: MatchStatus = "DIFF" if diff_fields else "MATCH"
        row = ReconcileRow(status, ext, db, diff_fields, score, cosmetic_diffs=cosmetic, fillable_fields=fillable,
                           is_program_match=db_idx in _program_indices,
                           matched_carrier_id=_matched_cid)
        _attach_metadata(row, "policy_number")
        results.append(row)

    # Pass 1.5: Date-pair match — both effective + expiration within 45 days AND client WRatio >= 80
    # Catches cases where policy numbers are missing but dates are reliable identifiers.
    # Type score is used only for tie-breaking, not as a gate.
    remaining_ext = [i for i in range(len(ext_rows)) if i not in ext_matched]
    candidates_15 = [db_rows[i] for i in sorted(db_unmatched)]
    for i in list(remaining_ext):
        ext = ext_rows[i]
        ext_eff = ext.get("effective_date", "")
        ext_exp = ext.get("expiration_date", "")
        ext_client = ext.get("client_name", "")
        if not (ext_eff and ext_exp and ext_client):
            continue
        ext_client_norm_15 = _normalize_client_name(ext_client)
        best_db = None
        best_score_15 = 0.0
        ext_carrier_15 = ext.get("carrier", "")
        for db in candidates_15:
            if not single_client and fuzz.WRatio(ext_client_norm_15, _normalize_client_name(db.get("client_name", ""))) < 80:
                continue
            eff_delta = _date_delta_days(ext_eff, db.get("effective_date", ""))
            exp_delta = _date_delta_days(ext_exp, db.get("expiration_date", ""))
            if eff_delta is None or exp_delta is None:
                continue
            if eff_delta <= 45 and exp_delta <= 45:
                type_score = fuzz.WRatio(
                    _normalize_coverage(ext.get("policy_type", "")),
                    _normalize_coverage(db.get("policy_type", ""))
                )
                # Type used for tie-breaking only — not a gate
                if type_score > best_score_15:
                    best_score_15 = type_score
                    best_db = db
        if best_db is not None:
            db_idx = db_rows.index(best_db)
            _claim_db(best_db, db_idx)
            if db_idx not in _program_indices:
                candidates_15 = [c for c in candidates_15 if c is not best_db]
            ext_matched.add(i)
            # Determine matched carrier ID for program matches
            _matched_cid = None
            _compare_target_15 = best_db
            if db_idx in _program_indices and best_db.get("_program_carrier_rows"):
                _ext_carrier = ext.get("carrier", "")
                for _pc in best_db["_program_carrier_rows"]:
                    if fuzz.WRatio(_ext_carrier, _pc.get("carrier", "")) >= 70:
                        _matched_cid = _pc.get("id")
                        _compare_target_15 = _pc
                        break
            diff_fields, cosmetic, fillable, score = _compare_fields(ext, _compare_target_15)
            status = "DIFF" if diff_fields else "MATCH"
            row = ReconcileRow(status, ext, best_db, diff_fields, score, cosmetic_diffs=cosmetic,
                               is_program_match=db_idx in _program_indices,
                               matched_carrier_id=_matched_cid)
            _attach_metadata(row, "date_pair")
            results.append(row)

    # Pass 2: Fuzzy match for unmatched ext rows
    candidates = [db_rows[i] for i in sorted(db_unmatched)]
    for i, ext in enumerate(ext_rows):
        if i in ext_matched:
            continue
        db, score = _fuzzy_match(ext, candidates, date_priority=date_priority, single_client=single_client)
        if db is not None:
            db_idx = db_rows.index(db)
            _claim_db(db, db_idx)
            if db_idx not in _program_indices:
                candidates = [c for c in candidates if c is not db]
            # Determine matched carrier ID for program matches
            _matched_cid = None
            _compare_target_2 = db
            if db_idx in _program_indices and db.get("_program_carrier_rows") and ext:
                _ext_carrier = ext.get("carrier", "")
                for _pc in db["_program_carrier_rows"]:
                    if fuzz.WRatio(_ext_carrier, _pc.get("carrier", "")) >= 70:
                        _matched_cid = _pc.get("id")
                        _compare_target_2 = _pc
                        break
            diff_fields, cosmetic, fillable, _ = _compare_fields(ext, _compare_target_2)
            status = "DIFF" if diff_fields else "MATCH"
            row = ReconcileRow(status, ext, db, diff_fields, score, cosmetic_diffs=cosmetic,
                               is_program_match=db_idx in _program_indices,
                               matched_carrier_id=_matched_cid)
            _attach_metadata(row, "fuzzy")
            results.append(row)
        else:
            missing_row = ReconcileRow("MISSING", ext, None, [], 0.0)
            _attach_metadata(missing_row, "")
            results.append(missing_row)

    # Pass 3: Remaining DB rows → EXTRA (exclude programs that got at least one match)
    _program_matched_indices = {db_rows.index(r.db) for r in results if r.is_program_match and r.db is not None}
    for i in sorted(db_unmatched):
        if i in _program_matched_indices:
            continue  # program got matches, not truly "extra"
        results.append(ReconcileRow("EXTRA", None, db_rows[i], [], 0.0))

    # Sort: DIFF, MISSING, EXTRA, MATCH; within group by client_name + expiration_date
    def _sort_key(r: ReconcileRow):
        side = r.db if r.db else r.ext
        client = (side or {}).get("client_name", "") or ""
        exp = (side or {}).get("expiration_date", "") or ""
        return (_STATUS_SORT[r.status], client.lower(), exp)

    results.sort(key=_sort_key)
    return results


def program_reconcile_summary(results: list[ReconcileRow], carrier_map: dict | None = None) -> dict[str, dict]:
    """Build per-program reconciliation summary from results.

    Args:
        results: List of ReconcileRow from reconcile()
        carrier_map: {program_id: [carrier_row_dicts]} for per-carrier detail

    Returns: {policy_uid: {total_premium, matched_premium, matched_count, carrier_count,
                           fully_reconciled, carrier_detail, new_carriers}}
    """
    carrier_map = carrier_map or {}
    summaries: dict[str, dict] = {}
    for r in results:
        if not r.is_program_match or r.db is None:
            continue
        uid = r.db.get("policy_uid", "")
        pid = r.db.get("id")
        if uid not in summaries:
            db_carriers = carrier_map.get(pid, [])
            summaries[uid] = {
                "policy_type": r.db.get("policy_type", ""),
                "total_premium": float(r.db.get("premium") or 0),
                "carrier_count": len(db_carriers),
                "matched_premium": 0.0,
                "matched_count": 0,
                "carrier_detail": [],
                "new_carriers": [],
                "_matched_carrier_ids": set(),
            }
        ext_prem = float(r.ext.get("premium") or 0) if r.ext else 0
        summaries[uid]["matched_premium"] += ext_prem
        summaries[uid]["matched_count"] += 1
        if r.matched_carrier_id:
            summaries[uid]["_matched_carrier_ids"].add(r.matched_carrier_id)
            db_carrier = next((c for c in carrier_map.get(pid, []) if c["id"] == r.matched_carrier_id), None)
            db_prem = float(db_carrier["premium"]) if db_carrier else 0
            status = "MATCH" if abs(ext_prem - db_prem) <= db_prem * 0.01 else "DIFF"
            summaries[uid]["carrier_detail"].append({
                "carrier_id": r.matched_carrier_id,
                "carrier": r.ext.get("carrier", "") if r.ext else "",
                "db_premium": db_prem,
                "ext_premium": ext_prem,
                "status": status,
            })
        else:
            summaries[uid]["new_carriers"].append({
                "carrier": r.ext.get("carrier", "") if r.ext else "",
                "policy_number": r.ext.get("policy_number", "") if r.ext else "",
                "premium": ext_prem,
                "limit_amount": float(r.ext.get("limit_amount") or 0) if r.ext else 0,
            })

    for uid, s in summaries.items():
        total = s["total_premium"]
        s["fully_reconciled"] = s["matched_premium"] >= total * 0.95 if total > 0 else s["matched_count"] > 0
        del s["_matched_carrier_ids"]
    return summaries


# ─── CROSS-PAIR SCORING ───────────────────────────────────────────────────────

def _cross_pair_score(ext: dict, db: dict) -> float:
    """Score a MISSING ext dict against an EXTRA db dict using relaxed thresholds.

    Base score is client name only — coverage type is reviewed manually by the user
    and is only a small bonus here. Effective dates and policy numbers are the primary
    signals that distinguish policies for the same client.
    """
    ext_client = _normalize_client_name(ext.get("client_name", ""))
    db_client = _normalize_client_name(db.get("client_name", ""))
    ext_fni = _normalize_client_name(ext.get("first_named_insured", "")) if ext.get("first_named_insured") else ""
    db_fni = _normalize_client_name(db.get("first_named_insured", "")) if db.get("first_named_insured") else ""

    if not ext_client:
        return 0.0

    client_score = fuzz.WRatio(ext_client, db_client)
    # FNI cross-matching bonus
    if db_fni:
        client_score = max(client_score, fuzz.WRatio(ext_client, db_fni))
    if ext_fni:
        client_score = max(client_score, fuzz.WRatio(ext_fni, db_client))
        if db_fni:
            client_score = max(client_score, fuzz.WRatio(ext_fni, db_fni))

    if client_score < 55:  # relaxed from 70 to catch legal name vs DBA variants
        return 0.0

    # Base = client name only; coverage mismatch is fine — user reviews it manually
    combined = float(client_score)

    # Coverage: small bonus if types happen to align well
    ext_type = _normalize_coverage(ext.get("policy_type", ""))
    db_type = _normalize_coverage(db.get("policy_type", ""))
    if ext_type and db_type and fuzz.WRatio(ext_type, db_type) >= 80:
        combined += 5

    # Carrier bonus
    ext_carrier = ext.get("carrier", "")
    if ext_carrier and fuzz.WRatio(ext_carrier, db.get("carrier", "")) >= 70:
        combined += 8

    # Expiration date — primary date signal (larger bonuses than main fuzzy matcher)
    ext_exp = ext.get("expiration_date", "")
    db_exp = db.get("expiration_date", "")
    exp_delta = _date_delta_days(ext_exp, db_exp) if (ext_exp and db_exp) else None
    if exp_delta is not None:
        if exp_delta <= 14:
            combined += 25
        elif exp_delta <= 45:
            combined += 12
        elif _same_year(ext_exp, db_exp):
            combined += 5
        else:
            combined -= 10

    # Effective date — strong secondary date signal
    ext_eff = ext.get("effective_date", "")
    db_eff = db.get("effective_date", "")
    eff_delta = _date_delta_days(ext_eff, db_eff) if (ext_eff and db_eff) else None
    if eff_delta is not None:
        if eff_delta <= 14:
            combined += 15
        elif eff_delta <= 45:
            combined += 7

    # Policy number — normalized for formatting flexibility
    ext_pn = _normalize_policy_number(ext.get("policy_number") or "")
    db_pn = _normalize_policy_number(db.get("policy_number") or "")
    if ext_pn and db_pn:
        if ext_pn == db_pn:
            combined += 30
        else:
            pn_score = fuzz.ratio(ext_pn, db_pn)
            if pn_score >= 90:
                combined += 25
            elif pn_score >= 75:
                combined += 12

    return combined


def _find_likely_pairs(
    missing_rows: list[ReconcileRow],
    extra_rows: list[ReconcileRow],
    threshold: float = 65.0,
) -> list[dict]:
    """Cross-match MISSING rows against EXTRA rows with relaxed scoring.

    Returns [{"id": int, "score": float, "missing": ReconcileRow, "extra": ReconcileRow}, ...]
    sorted by score descending, deduplicated (one pair per MISSING, one per EXTRA).
    """
    if not missing_rows or not extra_rows:
        return []

    # Score all combinations
    scored: list[tuple[float, int, int]] = []
    for mi, mr in enumerate(missing_rows):
        if not mr.ext:
            continue
        for ei, er in enumerate(extra_rows):
            if not er.db:
                continue
            score = _cross_pair_score(mr.ext, er.db)
            if score >= threshold:
                scored.append((score, mi, ei))

    # Sort by score descending, deduplicate greedily
    scored.sort(key=lambda x: x[0], reverse=True)
    used_missing: set[int] = set()
    used_extra: set[int] = set()
    pairs: list[dict] = []

    for score, mi, ei in scored:
        if mi in used_missing or ei in used_extra:
            continue
        used_missing.add(mi)
        used_extra.add(ei)
        pairs.append({
            "id": len(pairs),
            "score": round(score, 1),
            "missing": missing_rows[mi],
            "extra": extra_rows[ei],
        })

    return pairs


# ─── SUMMARY ──────────────────────────────────────────────────────────────────

def summarize(results: list[ReconcileRow]) -> dict:
    counts = {"total": len(results), "match": 0, "diff": 0, "missing": 0, "extra": 0}
    for r in results:
        counts[r.status.lower()] += 1
    return counts


# ─── XLSX EXPORT ──────────────────────────────────────────────────────────────

def build_reconcile_xlsx(results: list[ReconcileRow], run_date: str = "", filename: str = "") -> bytes:
    """Build a 5-sheet XLSX reconciliation report."""
    from openpyxl import Workbook
    from policydb.exporter import _write_sheet, _wb_to_bytes

    wb = Workbook()
    wb.remove(wb.active)

    summary = summarize(results)

    # Sheet 1: Summary
    ws_sum = wb.create_sheet("Summary")
    from policydb.exporter import _HEADER_FILL, _HEADER_FONT
    from openpyxl.styles import Alignment
    ws_sum.append(["PolicyDB Reconciliation Report"])
    ws_sum.append(["Run Date", run_date or ""])
    ws_sum.append(["Source File", filename or ""])
    ws_sum.append([])
    ws_sum.append(["Status", "Count", "Description"])
    ws_sum["A5"].font = _HEADER_FONT
    ws_sum["B5"].font = _HEADER_FONT
    ws_sum["C5"].font = _HEADER_FONT
    ws_sum["A5"].fill = _HEADER_FILL
    ws_sum["B5"].fill = _HEADER_FILL
    ws_sum["C5"].fill = _HEADER_FILL
    ws_sum.append(["MATCH", summary["match"], "Aligned within tolerances"])
    ws_sum.append(["DIFF", summary["diff"], "Matched but field discrepancies found"])
    ws_sum.append(["MISSING", summary["missing"], "In uploaded file, not found in PolicyDB"])
    ws_sum.append(["EXTRA", summary["extra"], "In PolicyDB, not in uploaded file"])
    ws_sum.append(["TOTAL", summary["total"], ""])
    ws_sum.column_dimensions["A"].width = 12
    ws_sum.column_dimensions["B"].width = 10
    ws_sum.column_dimensions["C"].width = 45

    # Sheets 2-5: build row dicts
    def _diff_rows():
        return [_diff_dict(r) for r in results if r.status == "DIFF"]

    def _missing_rows():
        return [r.ext for r in results if r.status == "MISSING"]

    def _extra_rows():
        cols = ["policy_uid", "client_name", "policy_type", "carrier", "policy_number",
                "effective_date", "expiration_date", "premium", "limit_amount", "deductible"]
        return [{c: (r.db or {}).get(c, "") for c in cols} for r in results if r.status == "EXTRA"]

    def _all_rows():
        rows = []
        for r in results:
            side = r.db if r.db else r.ext
            row = {"status": r.status, "match_score": round(r.match_score, 1),
                   "diff_fields": ", ".join(r.diff_fields)}
            for f in COMPARE_FIELDS:
                row[f"ext_{f}"] = (r.ext or {}).get(f, "")
                row[f"db_{f}"] = (r.db or {}).get(f, "")
            row["db_policy_uid"] = r.db.get("policy_uid", "") if r.db else ""
            rows.append(row)
        return rows

    _write_sheet(wb, "Differences (DIFF)", _diff_rows())
    _write_sheet(wb, "Missing", _missing_rows())
    _write_sheet(wb, "Extra in PolicyDB", _extra_rows())
    _write_sheet(wb, "All Results", _all_rows())

    return _wb_to_bytes(wb)


def _diff_dict(r: ReconcileRow) -> dict:
    """Build a flat comparison dict for a DIFF row."""
    row = {
        "diff_fields": ", ".join(r.diff_fields),
        "match_score": round(r.match_score, 1),
        "db_policy_uid": (r.db or {}).get("policy_uid", ""),
    }
    for f in COMPARE_FIELDS:
        row[f"ext_{f}"] = (r.ext or {}).get(f, "")
        row[f"db_{f}"] = (r.db or {}).get(f, "")
    return row
