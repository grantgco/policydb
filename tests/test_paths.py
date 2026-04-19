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


def test_config_module_uses_paths_data_dir(tmp_path, monkeypatch):
    """config.CONFIG_PATH must come from policydb.paths, not via db.py re-export."""
    monkeypatch.setattr("policydb.paths.DATA_DIR", tmp_path)
    from importlib import reload
    import policydb.config as cfg
    reload(cfg)
    assert cfg.CONFIG_PATH == tmp_path / "config.yaml"


def test_outlook_available_is_jinja_global():
    """outlook_available must be callable from any template."""
    from policydb.web.app import templates
    assert "outlook_available" in templates.env.globals
    assert callable(templates.env.globals["outlook_available"])


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    from policydb.db import init_db
    init_db(path=db_path)
    from policydb.web.app import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def test_outlook_status_404_on_windows(app_client, monkeypatch):
    monkeypatch.setattr("policydb.paths.outlook_available", lambda: False)
    r = app_client.get("/outlook/status")
    assert r.status_code == 404


def test_outlook_sync_404_on_windows(app_client, monkeypatch):
    monkeypatch.setattr("policydb.paths.outlook_available", lambda: False)
    r = app_client.post("/outlook/sync")
    assert r.status_code in (404, 405)  # 405 if form validation runs first


def test_base_template_hides_sync_outlook_on_windows(app_client, monkeypatch):
    monkeypatch.setattr("policydb.paths.outlook_available", lambda: False)
    r = app_client.get("/action-center")
    assert r.status_code == 200
    assert "Sync Outlook" not in r.text


@pytest.fixture(autouse=True)
def restore_paths_module():
    """Reload policydb.paths and policydb.db back to their real state after each test."""
    yield
    import policydb.paths as paths
    import policydb.db as db
    import policydb.config as cfg
    from importlib import reload
    reload(paths)
    reload(db)
    reload(cfg)
