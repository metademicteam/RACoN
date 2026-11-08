"""Application path resolution must honour explicit overrides.

The test suite sets ``RACON_DB_PATH``; if the application ignored it, the FastAPI
lifespan would run interrupted-job recovery against the real per-user database and mark
a live job failed while the desktop app was still working on it.
"""
from backend.racon.core import config


def test_db_path_honours_override(monkeypatch, tmp_path):
    target = tmp_path / "isolated.db"
    monkeypatch.setenv("RACON_DB_PATH", str(target))

    assert config.get_db_path() == target


def test_db_path_defaults_to_user_data_directory(monkeypatch):
    monkeypatch.delenv("RACON_DB_PATH", raising=False)

    assert config.get_db_path() == config.get_data_dir() / "racon_v2.db"
