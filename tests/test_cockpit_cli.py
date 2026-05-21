"""Tests del CLI scripts/cockpit.py (orquestación reconstruir → proyectar)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cockpit import run_cockpit  # noqa: E402
from tests.test_projector import _populate, _snapshot_tree  # noqa: E402


def test_run_cockpit_projects_and_summarizes(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    vault = tmp_path / "vault"
    summary = run_cockpit(tmp_db, vault, reconstruct=False)
    assert summary.counts.get("dashboard") == 1
    assert summary.counts.get("sleeves", 0) >= 1
    assert summary.gate_level == "dd5"
    assert (vault / "00_System" / "cockpit" / "MIDAS — Cockpit.md").exists()


def test_run_cockpit_is_idempotent(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    vault = tmp_path / "vault"
    run_cockpit(tmp_db, vault, reconstruct=False)
    snap1 = _snapshot_tree(vault)
    run_cockpit(tmp_db, vault, reconstruct=False)
    snap2 = _snapshot_tree(vault)
    assert snap1 == snap2


def test_run_cockpit_reconstructs_from_chain(tmp_path: Path) -> None:
    """El CLI reconstruye el .db desde el chain y proyecta (camino completo)."""
    from infra.audit import chain

    chain_path = tmp_path / "chain.ndjson"
    chain.append_event(
        actor="human:test", event_type="decision", event_key="DEC-999",
        payload={"action": "create", "id": "DEC-999", "title": "T", "owner": "t",
                 "status": "proposed", "vault_path": "x.md", "content_hash": "a" * 64},
        chain_path=chain_path,
    )
    db = tmp_path / "midas.db"
    vault = tmp_path / "vault"
    summary = run_cockpit(db, vault, chain_path=chain_path, reconstruct=True)
    # el .db se reconstruyó desde el chain (la decisión está)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM decisions WHERE id='DEC-999'").fetchone()[0] == 1
    finally:
        conn.close()
    assert summary.counts.get("dashboard") == 1
