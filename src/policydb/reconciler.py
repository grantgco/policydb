"""Read-only reconciliation logic: compare an uploaded CSV against PolicyDB policies."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from rapidfuzz import fuzz, process

from policydb.importer import PolicyImporter, _parse_currency, _parse_date

# ─── COVERAGE NAME NORMALIZATION ──────────────────────────────────────────────
# Maps common AMS abbreviations and alternate names to canonical PolicyDB values.
# Applied before fuzzy matching so "CGL" vs "General Liability" score 100, not 40.
_COVERAGE_ALIASES: dict[str, str] = {
    # ── General Liability ────────────────────────────────────────────────────
    "cgl": "General Liability",
    "gl": "General Liability",
    "general liability": "General Liability",
    "commercial general liability": "General Liability",
    "premises liability": "General Liability",
    "general liability (part of package)": "General Liability",
    "gl (part of package)": "General Liability",

    # ── Property / Builders Risk ─────────────────────────────────────────────
    # All these map to the combined canonical "Property / Builders Risk"
    "prop": "Property / Builders Risk",
    "bop": "Property / Builders Risk",
    "commercial property": "Property / Builders Risk",
    "building": "Property / Builders Risk",
    "property": "Property / Builders Risk",
    "all risks property": "Property / Builders Risk",
    "all risk property": "Property / Builders Risk",
    "difference in conditions": "Property / Builders Risk",
    "dic": "Property / Builders Risk",
    "inland marine": "Inland Marine",
    "special risk": "Property / Builders Risk",
    "blended program / package": "Property / Builders Risk",
    "blended program/package": "Property / Builders Risk",
    "blended program": "Property / Builders Risk",
    "package policy": "Property / Builders Risk",

    # ── Builders Risk (Standalone) ───────────────────────────────────────────
    "builders risk": "Builders Risk (Standalone)",
    "builders all risks": "Builders Risk (Standalone)",
    "builders all risk": "Builders Risk (Standalone)",
    "installation floater": "Builders Risk (Standalone)",
    "builders risk (standalone)": "Builders Risk (Standalone)",

    # ── Equipment Breakdown ───────────────────────────────────────────────────
    "boiler & machinery": "Equipment Breakdown",
    "boiler and machinery": "Equipment Breakdown",
    "equipment breakdown": "Equipment Breakdown",

    # ── Umbrella / Excess ────────────────────────────────────────────────────
    "umb": "Umbrella / Excess",
    "excess": "Umbrella / Excess",
    "umbrella": "Umbrella / Excess",
    "excess liability": "Umbrella / Excess",
    "umbrella liability": "Umbrella / Excess",
    "excess/umbrella": "Umbrella / Excess",
    "umbrella / excess": "Umbrella / Excess",
    "umbrella and/or bumbershoot liability": "Umbrella / Excess",
    "bumbershoot": "Umbrella / Excess",
    "marine excess liability": "Umbrella / Excess",

    # ── Workers Compensation ─────────────────────────────────────────────────
    "wc": "Workers Compensation",
    "workers comp": "Workers Compensation",
    "workers' comp": "Workers Compensation",
    "workers' compensation": "Workers Compensation",
    "work comp": "Workers Compensation",
    "workers compensation": "Workers Compensation",

    # ── Commercial Auto ──────────────────────────────────────────────────────
    "ca": "Commercial Auto",
    "bap": "Commercial Auto",
    "commercial auto": "Commercial Auto",
    "business auto": "Commercial Auto",
    "hired & non-owned auto": "Commercial Auto",
    "hired and non-owned": "Commercial Auto",
    "automobile liability/physical damage (part of package)": "Commercial Auto",
    "automobile liability/physical damage": "Commercial Auto",
    "automobile/motor liability & physical damage": "Commercial Auto",
    "automobile/motor liability and physical damage": "Commercial Auto",
    "automobile/motor non-owned": "Commercial Auto",
    "auto liability": "Commercial Auto",
    "commercial automobile": "Commercial Auto",

    # ── Cyber / Tech E&O ─────────────────────────────────────────────────────
    "cyber": "Cyber / Tech E&O",
    "cyber liability": "Cyber / Tech E&O",
    "cyber / tech e&o": "Cyber / Tech E&O",
    "cyber, tech, media programs": "Cyber / Tech E&O",
    "cyber tech media": "Cyber / Tech E&O",
    "technology e&o": "Cyber / Tech E&O",
    "tech e&o": "Cyber / Tech E&O",
    "media liability": "Cyber / Tech E&O",
    "network security": "Cyber / Tech E&O",

    # ── Professional Liability / E&O ─────────────────────────────────────────
    "e&o": "Professional Liability / E&O",
    "professional liability": "Professional Liability / E&O",
    "professional liability / e&o": "Professional Liability / E&O",
    "pl": "Professional Liability / E&O",
    "errors & omissions": "Professional Liability / E&O",
    "errors and omissions": "Professional Liability / E&O",
    "professional indemnity": "Professional Liability / E&O",

    # ── Directors & Officers ─────────────────────────────────────────────────
    "d&o": "Directors & Officers",
    "directors & officers": "Directors & Officers",
    "directors and officers": "Directors & Officers",
    "directors & officers liability": "Directors & Officers",
    "directors and officers liability": "Directors & Officers",
    "excess side-a dic d&o": "Directors & Officers",
    "excess side-a d&o": "Directors & Officers",
    "excess directors & officers liability": "Directors & Officers",
    "excess directors and officers liability": "Directors & Officers",
    "excess d&o": "Directors & Officers",
    "side a d&o": "Directors & Officers",
    "fiduciary": "Directors & Officers",
    "fiduciary liability": "Directors & Officers",
    "erisa": "Directors & Officers",

    # ── EPLI ──────────────────────────────────────────────────────────────────
    "epli": "Employment Practices Liability",
    "employment practices": "Employment Practices Liability",
    "employment practices liability": "Employment Practices Liability",
    "epl": "Employment Practices Liability",

    # ── Crime / Fidelity ──────────────────────────────────────────────────────
    "crime": "Crime / Fidelity",
    "fidelity": "Crime / Fidelity",
    "crime / fidelity": "Crime / Fidelity",
    "commercial crime": "Crime / Fidelity",
    "employee dishonesty": "Crime / Fidelity",

    # ── Environmental ─────────────────────────────────────────────────────────
    "env": "Environmental",
    "environmental liability": "Environmental",
    "pollution liability": "Environmental",
    "pollution legal liability": "Environmental",
    "pll": "Environmental",
    "environmental impairment": "Environmental",

    # ── Marine ────────────────────────────────────────────────────────────────
    "marine": "Inland Marine",
    "ocean marine": "Inland Marine",
    "cargo": "Inland Marine",
    "multi-peril marine package": "Inland Marine",
    "marine package": "Inland Marine",

    # ── Railroad Protective ───────────────────────────────────────────────────
    "railroad protective liability": "Railroad Protective",
    "railroad protective": "Railroad Protective",

    # ── OCIP / Wrap-Up ────────────────────────────────────────────────────────
    "ocip": "OCIP",
    "ccip": "OCIP",
    "wrap-up": "OCIP",
    "wrap up": "OCIP",
    "owner controlled insurance program": "OCIP",
    "contractor controlled insurance program": "OCIP",
}


def _normalize_coverage(value: str) -> str:
    """Normalize a policy type / line of business name to a canonical form."""
    if not value:
        return value
    key = value.strip().lower()
    return _COVERAGE_ALIASES.get(key, value.strip())

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


# ─── CSV PARSING ──────────────────────────────────────────────────────────────

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


def parse_uploaded_csv(
    content: bytes,
    column_mapping: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Parse uploaded CSV bytes into normalized dicts.

    Args:
        content: Raw CSV file bytes.
        column_mapping: Optional explicit mapping of original column names to
            canonical PolicyDB field names (e.g. {"Policy Expiry": "expiration_date"}).
            When provided, bypasses ALIASES auto-detection entirely.

    Returns:
        rows: list of dicts with canonical field names and parsed values
        warnings: human-readable parse warnings
    """
    warnings: list[str] = []

    # Try UTF-8-sig (handles BOM from AMS360/Excel), fall back to latin-1
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        warnings.append("File encoding detected as Latin-1 (not UTF-8). Special characters may be affected.")

    reader = csv.DictReader(io.StringIO(text))
    raw_rows = list(reader)

    if not raw_rows:
        warnings.append("No data rows found in uploaded file.")
        return [], warnings

    if column_mapping:
        # User-defined mapping — use directly, unmapped columns kept as-is
        raw_headers = list(raw_rows[0].keys())
        header_map = {h: column_mapping.get(h, h) for h in raw_headers}
    else:
        header_map = _normalize_headers(list(raw_rows[0].keys()))
    rows: list[dict] = []

    for i, raw in enumerate(raw_rows, start=2):  # row 1 = header
        row: dict = {}
        for raw_key, value in raw.items():
            canonical = header_map.get(raw_key, raw_key)
            row[canonical] = (value or "").strip()

        # Skip rows with no identifiable data
        if not row.get("client_name") and not row.get("policy_number"):
            warnings.append(f"Row {i}: skipped — no client name or policy number found.")
            continue

        # Parse typed fields
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

    return rows, warnings


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


def _compare_fields(ext: dict, db: dict) -> tuple[list[str], float]:
    """
    Compare COMPARE_FIELDS between ext and db records.

    Returns:
        diff_fields: list of field names that differ
        score: minimum WRatio across text fields (confidence)
    """
    diff_fields: list[str] = []
    min_text_score = 100.0

    for f in COMPARE_FIELDS:
        ext_val = ext.get(f)
        db_val = db.get(f)

        if f in _TEXT_FIELDS:
            if ext_val and db_val:
                # Normalize coverage names before comparing
                ev = _normalize_coverage(str(ext_val)) if f == "policy_type" else str(ext_val)
                dv = _normalize_coverage(str(db_val)) if f == "policy_type" else str(db_val)
                score = fuzz.WRatio(ev, dv)
                min_text_score = min(min_text_score, score)
                if score < 85:
                    diff_fields.append(f)

        elif f in _DATE_FIELDS:
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
            except (TypeError, ValueError):
                pass

        # policy_number: only flag if one side has it and the other doesn't
        elif f == "policy_number":
            ext_pn = (ext_val or "").strip().upper()
            db_pn = (db_val or "").strip().upper()
            if ext_pn and db_pn and ext_pn != db_pn:
                diff_fields.append(f)

    return diff_fields, min_text_score


# ─── MATCHING ─────────────────────────────────────────────────────────────────

def _same_year(d1: str | None, d2: str | None) -> bool:
    """Return True if two YYYY-MM-DD strings share the same calendar year."""
    if not d1 or not d2 or len(d1) < 4 or len(d2) < 4:
        return False
    return d1[:4] == d2[:4]


def _fuzzy_match(ext_row: dict, candidates: list[dict]) -> tuple[dict | None, float]:
    """
    Find the best fuzzy match for ext_row among candidates.

    Scoring pipeline:
      - client_name WRatio must be >= 70 (hard filter)
      - policy_type WRatio must be >= 60 (soft — combined score handles borderline cases)
      - expiration date: +20 if ≤14d, +10 if ≤45d, +5 if same year, −10 if >60d
      - effective date: +10 if ≤14d, +5 if ≤45d
      - carrier: +10 if WRatio >= 70
      - policy number similarity: +15 if fuzzy match >= 90
      - Accept if combined score >= 65
    """
    if not ext_row.get("client_name"):
        return None, 0.0

    ext_client = ext_row.get("client_name", "")
    ext_type = _normalize_coverage(ext_row.get("policy_type", ""))
    ext_exp = ext_row.get("expiration_date", "")
    ext_eff = ext_row.get("effective_date", "")
    ext_carrier = ext_row.get("carrier", "")
    ext_pn = (ext_row.get("policy_number") or "").strip().upper()

    best_candidate = None
    best_score = 0.0

    for db in candidates:
        # Hard filter: client name must be recognizably the same
        client_score = fuzz.WRatio(ext_client, db.get("client_name", ""))
        if client_score < 70:
            continue

        # Soft filter: policy type — lower threshold; combined scoring handles borderline cases
        db_type = _normalize_coverage(db.get("policy_type", ""))
        type_score = fuzz.WRatio(ext_type, db_type) if (ext_type and db_type) else 50
        if type_score < 60:
            continue

        # Base score: weighted average of client + type (type counts slightly less since
        # aliases may not cover every AMS360 variant)
        combined = client_score * 0.55 + type_score * 0.45

        # Carrier bonus
        if ext_carrier:
            carrier_score = fuzz.WRatio(ext_carrier, db.get("carrier", ""))
            if carrier_score >= 70:
                combined += 10

        # Expiration date — graduated bonus/penalty
        db_exp = db.get("expiration_date", "")
        exp_delta = _date_delta_days(ext_exp, db_exp) if (ext_exp and db_exp) else None
        if exp_delta is not None:
            if exp_delta <= 14:
                combined += 20
            elif exp_delta <= 45:
                combined += 10
            elif _same_year(ext_exp, db_exp):
                combined += 5    # same year, different month — some confidence
            else:
                combined -= 10   # clearly different period; penalize but allow strong name+type to rescue

        # Effective date — smaller bonus
        db_eff = db.get("effective_date", "")
        eff_delta = _date_delta_days(ext_eff, db_eff) if (ext_eff and db_eff) else None
        if eff_delta is not None:
            if eff_delta <= 14:
                combined += 10
            elif eff_delta <= 45:
                combined += 5

        # Policy number similarity bonus (catches near-exact numbers with formatting differences)
        db_pn = (db.get("policy_number") or "").strip().upper()
        if ext_pn and db_pn:
            pn_score = fuzz.ratio(ext_pn, db_pn)
            if pn_score >= 90:
                combined += 15
            elif pn_score >= 75:
                combined += 5

        if combined > best_score:
            best_score = combined
            best_candidate = db

    if best_score < 65:
        return None, 0.0

    return best_candidate, best_score


def find_candidates(ext_row: dict, db_rows: list[dict], limit: int = 8) -> list[tuple[dict, float]]:
    """
    Return top candidate DB rows for a given ext_row, for manual match selection.
    Uses a wider tolerance than the automatic matcher to surface more options.
    Returns list of (db_row, score) sorted by score descending.
    """
    ext_client = ext_row.get("client_name", "")
    ext_type = _normalize_coverage(ext_row.get("policy_type", ""))
    ext_exp = ext_row.get("expiration_date", "")
    ext_eff = ext_row.get("effective_date", "")
    ext_carrier = ext_row.get("carrier", "")

    scored: list[tuple[dict, float]] = []

    for db in db_rows:
        client_score = fuzz.WRatio(ext_client, db.get("client_name", "")) if ext_client else 0
        if client_score < 50:
            continue

        type_score = fuzz.WRatio(ext_type, _normalize_coverage(db.get("policy_type", ""))) if ext_type else 50
        carrier_score = fuzz.WRatio(ext_carrier, db.get("carrier", "")) if ext_carrier else 0

        combined = (client_score + type_score) / 2
        combined += 10 if carrier_score >= 70 else 0

        # Expiration date within 60 days — bonus scoring, not a hard filter for suggestions
        db_exp = db.get("expiration_date", "")
        if ext_exp and db_exp:
            exp_delta = _date_delta_days(ext_exp, db_exp)
            if exp_delta is not None:
                if exp_delta <= 14:
                    combined += 20
                elif exp_delta <= 60:
                    combined += 10

        # Effective date within 60 days — additional bonus
        db_eff = db.get("effective_date", "")
        if ext_eff and db_eff:
            eff_delta = _date_delta_days(ext_eff, db_eff)
            if eff_delta is not None:
                if eff_delta <= 14:
                    combined += 15
                elif eff_delta <= 60:
                    combined += 5

        # Exact policy number match is a very strong signal
        ext_pn = (ext_row.get("policy_number") or "").strip().upper()
        db_pn = (db.get("policy_number") or "").strip().upper()
        if ext_pn and db_pn and ext_pn == db_pn:
            combined += 30

        scored.append((db, round(combined, 1)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


# ─── MAIN RECONCILE ───────────────────────────────────────────────────────────

def reconcile(ext_rows: list[dict], db_rows: list[dict]) -> list[ReconcileRow]:
    """
    Match ext_rows against db_rows.

    Pass 1: Exact policy number match
    Pass 2: Fuzzy match on client_name + policy_type + expiration_date
    Pass 3: Remaining DB rows → EXTRA

    Returns rows sorted: DIFF, MISSING, EXTRA, MATCH.
    """
    results: list[ReconcileRow] = []

    # Build DB indexes
    db_by_polnum: dict[str, dict] = {}
    for db in db_rows:
        pn = (db.get("policy_number") or "").strip().upper()
        if pn and pn not in db_by_polnum:
            db_by_polnum[pn] = db

    db_unmatched: set[int] = set(range(len(db_rows)))
    ext_matched: set[int] = set()

    # Pass 1: Exact policy number match
    for i, ext in enumerate(ext_rows):
        ext_pn = (ext.get("policy_number") or "").strip().upper()
        if not ext_pn:
            continue
        db = db_by_polnum.get(ext_pn)
        if db is None:
            continue
        db_idx = db_rows.index(db)
        if db_idx not in db_unmatched:
            continue  # already claimed — send to Pass 2
        db_unmatched.discard(db_idx)
        ext_matched.add(i)
        diff_fields, score = _compare_fields(ext, db)
        status: MatchStatus = "DIFF" if diff_fields else "MATCH"
        results.append(ReconcileRow(status, ext, db, diff_fields, score))

    # Pass 1.5: Date-pair match — both effective + expiration within 14 days AND client WRatio >= 80
    # Catches cases where policy numbers are missing but dates are reliable identifiers.
    remaining_ext = [i for i in range(len(ext_rows)) if i not in ext_matched]
    candidates_15 = [db_rows[i] for i in sorted(db_unmatched)]
    for i in list(remaining_ext):
        ext = ext_rows[i]
        ext_eff = ext.get("effective_date", "")
        ext_exp = ext.get("expiration_date", "")
        ext_client = ext.get("client_name", "")
        if not (ext_eff and ext_exp and ext_client):
            continue
        best_db = None
        best_score_15 = 0.0
        for db in candidates_15:
            if fuzz.WRatio(ext_client, db.get("client_name", "")) < 80:
                continue
            eff_delta = _date_delta_days(ext_eff, db.get("effective_date", ""))
            exp_delta = _date_delta_days(ext_exp, db.get("expiration_date", ""))
            if eff_delta is None or exp_delta is None:
                continue
            if eff_delta <= 30 and exp_delta <= 30:
                type_score = fuzz.WRatio(
                    _normalize_coverage(ext.get("policy_type", "")),
                    _normalize_coverage(db.get("policy_type", ""))
                )
                if type_score >= 70 and type_score > best_score_15:
                    best_score_15 = type_score
                    best_db = db
        if best_db is not None:
            db_idx = db_rows.index(best_db)
            db_unmatched.discard(db_idx)
            candidates_15 = [c for c in candidates_15 if c is not best_db]
            ext_matched.add(i)
            diff_fields, score = _compare_fields(ext, best_db)
            status = "DIFF" if diff_fields else "MATCH"
            results.append(ReconcileRow(status, ext, best_db, diff_fields, score))

    # Pass 2: Fuzzy match for unmatched ext rows
    candidates = [db_rows[i] for i in sorted(db_unmatched)]
    for i, ext in enumerate(ext_rows):
        if i in ext_matched:
            continue
        db, score = _fuzzy_match(ext, candidates)
        if db is not None:
            db_idx = db_rows.index(db)
            db_unmatched.discard(db_idx)
            candidates = [c for c in candidates if c is not db]
            diff_fields, _ = _compare_fields(ext, db)
            status = "DIFF" if diff_fields else "MATCH"
            results.append(ReconcileRow(status, ext, db, diff_fields, score))
        else:
            results.append(ReconcileRow("MISSING", ext, None, [], 0.0))

    # Pass 3: Remaining DB rows → EXTRA
    for i in sorted(db_unmatched):
        results.append(ReconcileRow("EXTRA", None, db_rows[i], [], 0.0))

    # Sort: DIFF, MISSING, EXTRA, MATCH; within group by client_name + expiration_date
    def _sort_key(r: ReconcileRow):
        side = r.db if r.db else r.ext
        client = (side or {}).get("client_name", "") or ""
        exp = (side or {}).get("expiration_date", "") or ""
        return (_STATUS_SORT[r.status], client.lower(), exp)

    results.sort(key=_sort_key)
    return results


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
            if r.db:
                row["db_policy_uid"] = r.db.get("policy_uid", "")
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
