#!/usr/bin/env python3
"""Export Python rule outputs as JSON fixtures for Swift parity tests.

Regenerate whenever Python rule implementations change:

    ~/.policydb/venv/bin/python scripts/export_swift_fixtures.py

Writes to Coverage/CoverageTests/Fixtures/python-rule-outputs.json.

The fixture captures what Python *actually* returns for each input so Swift
parity tests can replay byte-for-byte. Python's rule functions are designed
to never raise on user input (they return safe defaults like `0.0` or `""`),
so `error` stays `null` for normal cases.
"""
from __future__ import annotations

import json
from pathlib import Path

from policydb.utils import (
    parse_currency_with_magnitude,
    format_phone,
    clean_email,
    build_ref_tag,
)
from policydb.db import generate_issue_uid


REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "Coverage" / "CoverageTests" / "Fixtures" / "python-rule-outputs.json"


CURRENCY_INPUTS = [
    "1m", "1M", "1.5m", "500k", "500K",
    "$2,000,000", "$1,234.56", "2500", "2500.00",
    "  $1,000  ", "", "abc",
    "1.2m", "0.5k", "2b",
    "$15M", "$800K", "$1.2B", "$15,000,000",
]

PHONE_INPUTS = [
    # Real valid NANP numbers — these hit the formatted-output path
    "6502530000",       # Google HQ
    "650-253-0000",
    "(650) 253-0000",
    "+16502530000",
    "+1 650 253 0000",
    # UK formatted international
    "+44 20 7946 0958",
    # 555-exchange numbers are invalid per libphonenumber — stripped-raw fallback path
    "(555) 123-4567",
    "5551234567",
    # Empty / garbage
    "",
    "invalid",
    "not a phone",
]

EMAIL_INPUTS = [
    "Grant@Example.com",
    "  grant@example.com  ",
    "GRANT@EXAMPLE.COM",
    "grant+policy@example.com",
    "Jane Doe <jane@example.com>",
    "<jane@example.com>",
    "mailto:jane@example.com",
    "jane@example.com;",
    '"jane@example.com"',
    "",
    "not an email",
    "user@",
]

REF_TAG_INPUTS = [
    {"cn_number": "123456789"},
    {"cn_number": "CN123456789"},
    {"cn_number": "123456789", "project_id": 5},
    {"cn_number": "123456789", "project_id": 5, "policy_uid": "POL-042"},
    {"cn_number": "123456789", "project_id": 5, "policy_uid": "POL-042", "activity_id": 789},
    {"cn_number": "123456789", "policy_uid": "POL-042", "thread_id": 42},
    {"cn_number": "123456789", "rfi_uid": "CN123456789-RFI01"},
    {"cn_number": "123456789", "issue_uid": "A7F2C3B1"},
    {"cn_number": "", "client_id": 7},
    {"cn_number": None},
    {"cn_number": "None"},
    {"cn_number": "9999999", "project_id": 12, "policy_uid": "POL-500"},
]


def _call_scalar(fn, value):
    """Run a scalar-input function, capture output + any error."""
    try:
        return {"input": value, "output": fn(value), "error": None}
    except Exception as exc:  # noqa: BLE001 — fixture captures errors deliberately
        return {"input": value, "output": None, "error": f"{type(exc).__name__}: {exc}"}


def export_currency():
    return [_call_scalar(parse_currency_with_magnitude, v) for v in CURRENCY_INPUTS]


def export_phone():
    return [_call_scalar(format_phone, v) for v in PHONE_INPUTS]


def export_email():
    return [_call_scalar(clean_email, v) for v in EMAIL_INPUTS]


def export_ref_tag():
    results = []
    for case in REF_TAG_INPUTS:
        try:
            output = build_ref_tag(**case)
            results.append({"input": case, "output": output, "error": None})
        except Exception as exc:  # noqa: BLE001
            results.append({"input": case, "output": None, "error": f"{type(exc).__name__}: {exc}"})
    return results


def export_issue_uid_shape():
    """Can't fixture-test randomness — capture shape only."""
    samples = [generate_issue_uid() for _ in range(10)]
    return {
        "samples": samples,
        "length": 8,
        "charset": "0123456789ABCDEF",
    }


def main():
    fixture = {
        "parse_currency_with_magnitude": export_currency(),
        "format_phone": export_phone(),
        "clean_email": export_email(),
        "build_ref_tag": export_ref_tag(),
        "generate_issue_uid_shape": export_issue_uid_shape(),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
