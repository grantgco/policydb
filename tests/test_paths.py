"""Tests for policydb.paths — platform-aware data directory helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_data_dir_mac_is_home_policydb(tmp_path):
    with patch.object(sys, "platform", "darwin"), \
         patch.object(Path, "home", return_value=tmp_path):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.DATA_DIR == tmp_path / ".policydb"
        assert paths.DATA_DIR.exists()


def test_data_dir_windows_is_appdata_policydb(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    with patch.object(sys, "platform", "win32"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.DATA_DIR == appdata / "PolicyDB"
        assert paths.DATA_DIR.exists()


def test_db_path_and_config_path(tmp_path):
    with patch.object(sys, "platform", "darwin"), \
         patch.object(Path, "home", return_value=tmp_path):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.db_path() == tmp_path / ".policydb" / "policydb.sqlite"
        assert paths.config_path() == tmp_path / ".policydb" / "config.yaml"


def test_outlook_available_only_on_mac():
    with patch.object(sys, "platform", "darwin"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is True
    with patch.object(sys, "platform", "win32"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is False
    with patch.object(sys, "platform", "linux"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is False


def test_db_module_uses_paths_data_dir(tmp_path, monkeypatch):
    """db.DB_DIR and db.DB_PATH must come from policydb.paths, not local literals."""
    monkeypatch.setattr("policydb.paths.DATA_DIR", tmp_path)
    from importlib import reload
    import policydb.db as db
    reload(db)
    assert db.DB_DIR == tmp_path
    assert db.DB_PATH == tmp_path / "policydb.sqlite"


@pytest.fixture(autouse=True)
def restore_paths_module():
    """Reload policydb.paths and policydb.db back to their real state after each test."""
    yield
    import policydb.paths as paths
    import policydb.db as db
    from importlib import reload
    reload(paths)
    reload(db)
