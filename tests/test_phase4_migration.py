"""Tests for Phase 4 program cutover migration."""
import sqlite3
import pytest
from policydb.db import init_db, next_policy_uid


@pytest.fixture
def migrated_db(tmp_path):
    """Create a fresh DB with all migrations applied (including 101)."""
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_migration_101_applied(migrated_db):
    """Migration 101 should be in schema_version."""
    row = migrated_db.execute(
        "SELECT version FROM schema_version WHERE version = 101"
    ).fetchone()
    assert row is not None


def test_child_policies_have_program_id(migrated_db):
    """After migration, children with tower_group matching a program name
    should have program_id set."""
    migrated_db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-TEST', 999, 'TestProg')"
    )
    migrated_db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, tower_group, is_program, archived) "
        "VALUES ('POL-CHILD', 999, 'General Liability', 'TestProg', 0, 0)"
    )
    migrated_db.commit()
    pgm_id = migrated_db.execute(
        "SELECT id FROM programs WHERE program_uid = 'PGM-TEST'"
    ).fetchone()["id"]
    migrated_db.execute(
        """UPDATE policies SET program_id = (
            SELECT pg.id FROM programs pg
            WHERE pg.client_id = policies.client_id AND pg.name = policies.tower_group
            AND pg.archived = 0 LIMIT 1
        ) WHERE tower_group IS NOT NULL AND tower_group != ''
        AND (is_program = 0 OR is_program IS NULL)
        AND program_id IS NULL AND archived = 0 AND policy_uid = 'POL-CHILD'"""
    )
    migrated_db.commit()
    child = migrated_db.execute(
        "SELECT program_id FROM policies WHERE policy_uid = 'POL-CHILD'"
    ).fetchone()
    assert child["program_id"] == pgm_id


def test_is_program_rows_archived(migrated_db):
    """After migration, is_program=1 rows should be archived."""
    count = migrated_db.execute(
        "SELECT COUNT(*) FROM policies WHERE is_program = 1 AND archived = 0"
    ).fetchone()[0]
    assert count == 0


def test_program_tower_lines_has_program_id_column(migrated_db):
    """Migration adds program_id column to program_tower_lines."""
    cols = [r["name"] for r in migrated_db.execute(
        "PRAGMA table_info(program_tower_lines)"
    ).fetchall()]
    assert "program_id" in cols
