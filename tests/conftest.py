"""Shared pytest fixtures for MIDAS Capital tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_db(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide a freshly initialized SQLite db in a temp dir.

    Applies all migrations from infra/sqlite/migrations/.
    """
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from scripts.init_db import init_db

    db_path = tmp_path / "test.db"
    init_db(db_path)
    yield db_path


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide an empty vault directory for decision_log tests."""
    vault = tmp_path / "vault"
    (vault / "10_Decisions").mkdir(parents=True)
    yield vault


@pytest.fixture
def db_connection(tmp_db: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a connection to the temp db with foreign keys on."""
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
