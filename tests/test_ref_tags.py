"""Tests for src/policydb/ref_tags.py — wide Outlook search builder."""
from __future__ import annotations

from datetime import date

import pytest

from policydb.db import get_connection, init_db
from policydb.ref_tags import build_wide_search


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


@pytest.fixture
def seeded(tmp_db):
    """Client with CN, two policies, one issue on POL-042, one program."""
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, cn_number, industry_segment, account_exec) "
        "VALUES ('Acme Corp', '122333627', 'Manufacturing', 'Grant')"
    )
    client_id = conn.execute(
        "SELECT id FROM clients WHERE name='Acme Corp'"
    ).fetchone()["id"]
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-042', ?, 'GL', 'Zurich', ?, ?, 10000, 'Grant')",
        (client_id, today, today),
    )
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-043', ?, 'AUTO', 'Chubb', ?, ?, 5000, 'Grant')",
        (client_id, today, today),
    )
    policy_id = conn.execute(
        "SELECT id FROM policies WHERE policy_uid='POL-042'"
    ).fetchone()["id"]
    # Issue linked to POL-042
    conn.execute(
        "INSERT INTO activity_log (client_id, policy_id, item_kind, issue_uid, "
        "subject, activity_type) "
        "VALUES (?, ?, 'issue', 'ISS-2026-007', 'Claim on GL', 'Issue')",
        (client_id, policy_id),
    )
    # Program on client
    conn.execute(
        "INSERT INTO programs (program_uid, client_id, name) "
        "VALUES ('PGM-3', ?, 'Acme Main Program')",
        (client_id,),
    )
    conn.commit()
    yield {"conn": conn, "client_id": client_id}
    conn.close()


def test_wide_search_client_includes_all_relatives(seeded):
    result = build_wide_search(seeded["conn"], "client", seeded["client_id"], mode="wide")
    # Issue token (verbatim), both policy forms, both program forms, CN
    assert "ISS-2026-007" in result.tokens
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    assert "POL-043" in result.tokens
    assert "POL043" in result.tokens
    assert "PGM-3" in result.tokens
    assert "PGM3" in result.tokens
    assert "CN122333627" in result.tokens
    # Quoted, OR-joined
    assert result.query == " OR ".join(f'"{t}"' for t in result.tokens)
    assert result.truncated is False


def test_wide_search_policy(seeded):
    result = build_wide_search(seeded["conn"], "policy", "POL-042", mode="wide")
    # Self + linked issue. Client CN is intentionally excluded — it would
    # OR-match every message about the client and drown the policy results.
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    assert "ISS-2026-007" in result.tokens
    assert "CN122333627" not in result.tokens
    # Other policy POL-043 NOT included when searching from POL-042
    assert "POL-043" not in result.tokens


def test_wide_search_issue(seeded):
    result = build_wide_search(
        seeded["conn"], "issue", "ISS-2026-007", mode="wide"
    )
    assert result.tokens[0] == "ISS-2026-007"
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    # CN is out of wide — use mode="client" to sweep all client correspondence.
    assert "CN122333627" not in result.tokens


def test_wide_search_program(seeded):
    result = build_wide_search(seeded["conn"], "program", "PGM-3", mode="wide")
    assert "PGM-3" in result.tokens
    assert "PGM3" in result.tokens
    assert "CN122333627" not in result.tokens


def test_client_mode_policy_returns_cn_only(seeded):
    """Shift-click escape hatch: mode='client' from a policy sweeps all client correspondence."""
    result = build_wide_search(
        seeded["conn"], "policy", "POL-042", mode="client"
    )
    assert result.tokens == ["CN122333627"]


def test_client_mode_issue_returns_cn_only(seeded):
    result = build_wide_search(
        seeded["conn"], "issue", "ISS-2026-007", mode="client"
    )
    assert result.tokens == ["CN122333627"]


def test_client_mode_program_returns_cn_only(seeded):
    result = build_wide_search(
        seeded["conn"], "program", "PGM-3", mode="client"
    )
    assert result.tokens == ["CN122333627"]


def test_narrow_mode_issue(seeded):
    result = build_wide_search(
        seeded["conn"], "issue", "ISS-2026-007", mode="narrow"
    )
    assert result.tokens == ["ISS-2026-007"]
    assert result.query == '"ISS-2026-007"'


def test_narrow_mode_policy(seeded):
    result = build_wide_search(seeded["conn"], "policy", "POL-042", mode="narrow")
    assert result.tokens == ["POL-042", "POL042"]


def test_truncation(seeded):
    result = build_wide_search(
        seeded["conn"], "client", seeded["client_id"], mode="wide", cap=2
    )
    assert result.truncated is True
    assert result.total_available > 2
    assert len(result.tokens) == 2
    # Most specific first — issue tokens should survive
    assert result.tokens[0] == "ISS-2026-007"


def test_unknown_entity_type_raises(seeded):
    with pytest.raises(ValueError, match="Unknown entity_type"):
        build_wide_search(seeded["conn"], "foobar", 1)  # type: ignore[arg-type]


def test_missing_entity_raises_keyerror(seeded):
    with pytest.raises(KeyError):
        build_wide_search(seeded["conn"], "policy", "POL-9999")


def test_client_with_missing_cn_falls_back_to_cnumeric(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('NoCN', 'Technology', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='NoCN'").fetchone()["id"]
    conn.commit()
    result = build_wide_search(conn, "client", cid, mode="wide")
    assert result.tokens == [f"C{cid}"]
    conn.close()
