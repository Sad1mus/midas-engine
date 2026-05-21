"""Portability test for the MIDAS "portable mind".

Proves the audit hash chain in infra/audit/chain.ndjson is the source of truth:
an ADR can be created, the SQLite db destroyed, and the decision recovered
purely by replaying the text chain.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.audit import chain  # noqa: E402
from scripts import decision_log, verify_chain  # noqa: E402
from scripts.init_db import init_db  # noqa: E402


def test_chain_file_verify_detects_tampering(tmp_path: Path) -> None:
    chain_path = tmp_path / "chain.ndjson"
    chain.append_event(
        actor="t", event_type="e", event_key=None, payload={"i": 1}, chain_path=chain_path
    )
    chain.append_event(
        actor="t", event_type="e", event_key=None, payload={"i": 2}, chain_path=chain_path
    )
    ok, _ = chain.verify_file(chain_path)
    assert ok

    # Tamper with the first line's payload; the chain must no longer verify.
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"i":1', '"i":999')
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = chain.verify_file(chain_path)
    assert not ok, msg


def test_mind_is_portable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chain_path = tmp_path / "infra_audit" / "chain.ndjson"
    vault = tmp_path / "vault"
    (vault / "10_Decisions").mkdir(parents=True)
    db_path = tmp_path / "midas.db"

    monkeypatch.setattr(decision_log, "VAULT_DIR", vault)
    monkeypatch.setattr(decision_log, "DECISIONS_DIR", vault / "10_Decisions")
    monkeypatch.setattr(decision_log, "CHAIN_PATH", chain_path)

    # Empty start: db has tables, chain file does not exist yet.
    init_db(db_path)
    assert not chain_path.exists()

    # 1. Create an ADR -> the chain.ndjson grows by exactly one event.
    rc = decision_log.cmd_new("Portable mind test", db_path=db_path, owner="tester")
    assert rc == 0
    events = chain.read_events(chain_path)
    assert len(events) == 1
    assert events[0]["event_type"] == "decision"
    assert events[0]["payload"]["id"] == "DEC-001"
    ok, msg = chain.verify_file(chain_path)
    assert ok, msg

    # The decision is in the SQLite projection.
    conn = sqlite3.connect(db_path)
    assert (
        conn.execute("SELECT title FROM decisions WHERE id = 'DEC-001'").fetchone()[0]
        == "Portable mind test"
    )
    conn.close()

    # 2. DESTROY the database entirely.
    db_path.unlink()
    assert not db_path.exists()

    # 3. Rebuild purely from the portable text chain (replay).
    init_db(db_path, chain_path=chain_path)

    # 4. verify_chain passes against the rebuilt db.
    assert verify_chain.verify_chain(db_path) == 0

    # 5. The decision reappeared — recovered from text alone.
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT title, status, owner FROM decisions WHERE id = 'DEC-001'"
    ).fetchone()
    conn.close()
    assert row == ("Portable mind test", "proposed", "tester")
