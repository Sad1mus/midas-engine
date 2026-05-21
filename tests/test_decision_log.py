"""Tests for decision_log.py and verify_chain.py.

Covers:
  - ADR creation writes file with valid frontmatter
  - ADR creation appends to audit_log with valid hash chain
  - Sequential ADR IDs allocate correctly
  - Hash chain verification detects tampering
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import decision_log, verify_chain  # noqa: E402


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Hash primitives
# ─────────────────────────────────────────────────────────────────────


def test_sha256_hex_matches_known_value() -> None:
    # Known answer test
    assert decision_log.sha256_hex("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_chain_step_deterministic() -> None:
    prev = "a" * 64
    payload = "b" * 64
    expected = sha256(prev + payload)
    assert decision_log.chain_step(prev, payload) == expected


def test_chain_step_rejects_short_hashes() -> None:
    with pytest.raises(ValueError):
        decision_log.chain_step("a" * 63, "b" * 64)
    with pytest.raises(ValueError):
        decision_log.chain_step("a" * 64, "b" * 65)


# ─────────────────────────────────────────────────────────────────────
# ADR ID allocation
# ─────────────────────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    assert decision_log.slugify("Adopt DuckDB for hot path") == "Adopt-DuckDB-for-hot-path"
    assert decision_log.slugify("Hello, World!") == "Hello-World"
    assert decision_log.slugify("  Leading and trailing  ") == "Leading-and-trailing"


def test_next_adr_id_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    decisions_dir = tmp_path / "10_Decisions"
    decisions_dir.mkdir()
    monkeypatch.setattr(decision_log, "DECISIONS_DIR", decisions_dir)
    assert decision_log.next_adr_id() == "DEC-001"


def test_next_adr_id_increments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    decisions_dir = tmp_path / "10_Decisions"
    decisions_dir.mkdir()
    (decisions_dir / "DEC-001_Foo.md").write_text("---\n---\n", encoding="utf-8")
    (decisions_dir / "DEC-002_Bar.md").write_text("---\n---\n", encoding="utf-8")
    (decisions_dir / "DEC-007_Baz.md").write_text("---\n---\n", encoding="utf-8")
    monkeypatch.setattr(decision_log, "DECISIONS_DIR", decisions_dir)
    assert decision_log.next_adr_id() == "DEC-008"


# ─────────────────────────────────────────────────────────────────────
# Audit log append
# ─────────────────────────────────────────────────────────────────────


def test_append_audit_first_row_has_zero_prev(db_connection: sqlite3.Connection) -> None:
    audit_id = decision_log.append_audit(
        db_connection,
        actor="test",
        event_type="test_event",
        event_key="TEST-001",
        payload={"x": 1},
    )
    row = db_connection.execute(
        "SELECT prev_hash, payload_hash, chain_hash, payload_json FROM audit_log WHERE id = ?",
        (audit_id,),
    ).fetchone()
    prev_hash, payload_hash, chain_hash, payload_json = row
    assert prev_hash == "0" * 64
    assert payload_hash == sha256(payload_json)
    assert chain_hash == sha256(prev_hash + payload_hash)


def test_append_audit_chains_correctly(db_connection: sqlite3.Connection) -> None:
    id1 = decision_log.append_audit(
        db_connection, actor="a", event_type="e1", event_key=None, payload={"i": 1}
    )
    id2 = decision_log.append_audit(
        db_connection, actor="b", event_type="e2", event_key=None, payload={"i": 2}
    )
    row1 = db_connection.execute(
        "SELECT chain_hash FROM audit_log WHERE id = ?", (id1,)
    ).fetchone()
    row2 = db_connection.execute(
        "SELECT prev_hash, payload_hash, chain_hash FROM audit_log WHERE id = ?", (id2,)
    ).fetchone()
    assert row2[0] == row1[0], "row2.prev_hash must equal row1.chain_hash"
    assert row2[2] == sha256(row2[0] + row2[1])


def test_payload_json_is_canonical(db_connection: sqlite3.Connection) -> None:
    """Same dict in different insertion orders must produce same hash."""
    audit_id = decision_log.append_audit(
        db_connection,
        actor="a",
        event_type="e",
        event_key=None,
        payload={"b": 2, "a": 1, "c": 3},
    )
    row = db_connection.execute(
        "SELECT payload_json FROM audit_log WHERE id = ?", (audit_id,)
    ).fetchone()
    # Keys must be sorted; no whitespace
    assert row[0] == '{"a":1,"b":2,"c":3}'


# ─────────────────────────────────────────────────────────────────────
# Full ADR creation flow
# ─────────────────────────────────────────────────────────────────────


def test_cmd_new_creates_adr_and_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_db: Path, tmp_vault: Path
) -> None:
    monkeypatch.setattr(decision_log, "VAULT_DIR", tmp_vault)
    monkeypatch.setattr(decision_log, "DECISIONS_DIR", tmp_vault / "10_Decisions")
    monkeypatch.setattr(decision_log, "CHAIN_PATH", tmp_vault / "chain.ndjson")

    rc = decision_log.cmd_new("Adopt foo over bar", db_path=tmp_db, owner="alice")
    assert rc == 0

    # Vault file created
    files = list((tmp_vault / "10_Decisions").glob("DEC-001_*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("---\n")
    meta, body = decision_log.split_frontmatter(text)
    assert meta["id"] == "DEC-001"
    assert meta["title"] == "Adopt foo over bar"
    assert meta["status"] == "proposed"
    assert meta["owner"] == "alice"

    # decisions table row created
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT id, title, status, owner, vault_path, content_hash FROM decisions"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "DEC-001"
    assert row[1] == "Adopt foo over bar"
    assert row[2] == "proposed"
    assert row[3] == "alice"
    assert row[5] == sha256(body)


# ─────────────────────────────────────────────────────────────────────
# Chain verification
# ─────────────────────────────────────────────────────────────────────


def test_verify_chain_passes_on_clean_db(tmp_db: Path, db_connection: sqlite3.Connection) -> None:
    decision_log.append_audit(
        db_connection, actor="a", event_type="e1", event_key=None, payload={"i": 1}
    )
    decision_log.append_audit(
        db_connection, actor="b", event_type="e2", event_key=None, payload={"i": 2}
    )
    db_connection.close()  # close before reopening in verify_chain
    rc = verify_chain.verify_chain(tmp_db)
    assert rc == 0


def test_verify_chain_detects_payload_tampering(
    tmp_db: Path, db_connection: sqlite3.Connection
) -> None:
    decision_log.append_audit(
        db_connection, actor="a", event_type="e1", event_key=None, payload={"i": 1}
    )
    # Tamper: modify payload_json directly
    db_connection.execute(
        "UPDATE audit_log SET payload_json = ? WHERE id = 1", ('{"tampered":true}',)
    )
    db_connection.commit()
    db_connection.close()

    rc = verify_chain.verify_chain(tmp_db)
    assert rc == 1


def test_verify_chain_detects_prev_hash_tampering(
    tmp_db: Path, db_connection: sqlite3.Connection
) -> None:
    decision_log.append_audit(
        db_connection, actor="a", event_type="e1", event_key=None, payload={"i": 1}
    )
    decision_log.append_audit(
        db_connection, actor="b", event_type="e2", event_key=None, payload={"i": 2}
    )
    # Tamper with prev_hash of row 2
    db_connection.execute(
        "UPDATE audit_log SET prev_hash = ? WHERE id = 2", ("f" * 64,)
    )
    db_connection.commit()
    db_connection.close()

    rc = verify_chain.verify_chain(tmp_db)
    assert rc == 1


def test_verify_chain_empty_db_is_ok(tmp_db: Path) -> None:
    rc = verify_chain.verify_chain(tmp_db)
    assert rc == 0
