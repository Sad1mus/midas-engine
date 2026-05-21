"""Verify the SQLite schema is correctly applied by init_db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

EXPECTED_TABLES = {
    "schema_version",
    "audit_log",
    "decisions",
    "sleeves",
    "trades",
    "executions",
    "equity_curve",
    "regime_states",
    "agent_decisions",
    "risk_gates_state",
    "cost_model_calibrations",
    "backtest_results",
    "ontology_rules",
    "bma_weights",
}


def _all_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_all_expected_tables_present(db_connection: sqlite3.Connection) -> None:
    tables = _all_tables(db_connection)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing expected tables: {missing}"


def test_schema_version_is_set(db_connection: sqlite3.Connection) -> None:
    row = db_connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    assert row is not None, "schema_version row missing"
    assert row[0] == 3, f"expected version 3, got {row[0]}"


def test_foreign_keys_enforced(db_connection: sqlite3.Connection) -> None:
    fk_status = db_connection.execute("PRAGMA foreign_keys").fetchone()
    assert fk_status[0] == 1, "foreign_keys must be ON"


def test_journal_mode_is_wal(tmp_db: Path) -> None:
    # Open a fresh connection; WAL is persistent across opens.
    conn = sqlite3.connect(tmp_db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"expected WAL, got {mode}"
    finally:
        conn.close()


def test_audit_log_chain_constraints(db_connection: sqlite3.Connection) -> None:
    # Verify CHECK constraints on hash lengths
    with db_connection:
        # Valid insert — should succeed
        db_connection.execute(
            """
            INSERT INTO audit_log
                (ts_utc, ts_ms, actor, event_type, payload_json,
                 payload_hash, prev_hash, chain_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-20T12:00:00.000Z",
                1747742400000,
                "test",
                "test_event",
                '{"k":"v"}',
                "a" * 64,
                "0" * 64,
                "b" * 64,
            ),
        )

    # Invalid hash length should be rejected
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            """
            INSERT INTO audit_log
                (ts_utc, ts_ms, actor, event_type, payload_json,
                 payload_hash, prev_hash, chain_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-20T12:00:00.000Z",
                1747742400001,
                "test",
                "test_event",
                '{"k":"v"}',
                "a" * 63,  # invalid length
                "0" * 64,
                "c" * 64,
            ),
        )


def test_risk_gate_levels_constrained(db_connection: sqlite3.Connection) -> None:
    import pytest

    # Valid gate level
    with db_connection:
        db_connection.execute(
            """
            INSERT INTO risk_gates_state (ts_utc, ts_ms, current_dd_pct, gate_level, sizing_mult)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-05-20T12:00:00.000Z", 1747742400000, 0.03, "normal", 1.0),
        )

    # Invalid gate level
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            """
            INSERT INTO risk_gates_state (ts_utc, ts_ms, current_dd_pct, gate_level, sizing_mult)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-05-20T12:00:00.000Z", 1747742400001, 0.03, "yolo", 1.0),
        )


def test_trade_status_constrained(db_connection: sqlite3.Connection) -> None:
    import pytest

    # First need a sleeve and audit_log row to satisfy FKs
    with db_connection:
        db_connection.execute(
            """
            INSERT INTO sleeves
                (id, name, track, status, asset_class, instruments, created_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("test_sleeve", "Test", "B", "research", "futures", '["MNQ"]', "2026-05-20T12:00:00Z"),
        )
        db_connection.execute(
            """
            INSERT INTO audit_log
                (ts_utc, ts_ms, actor, event_type, payload_json,
                 payload_hash, prev_hash, chain_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-20T12:00:00.000Z",
                1747742400000,
                "test",
                "trade",
                "{}",
                "d" * 64,
                "0" * 64,
                "e" * 64,
            ),
        )

    # Invalid trade status
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            """
            INSERT INTO trades
                (id, sleeve_id, track, ts_signal_utc, ts_signal_ms,
                 instrument, side, size_target, status, audit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "trade_001",
                "test_sleeve",
                "B",
                "2026-05-20T12:00:00.000Z",
                1747742400000,
                "MNQ",
                "long",
                1.0,
                "mythical_status",
                1,
            ),
        )


def _seed_audit_row(conn: sqlite3.Connection) -> None:
    """Insert a minimal audit_log row so ontology_rules.audit_id FK is satisfiable."""
    conn.execute(
        """
        INSERT INTO audit_log
            (ts_utc, ts_ms, actor, event_type, payload_json,
             payload_hash, prev_hash, chain_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-20T12:00:00.000Z",
            1747742400000,
            "human:test",
            "ontology_rule",
            "{}",
            "a" * 64,
            "0" * 64,
            "f" * 64,
        ),
    )


def test_ontology_rules_table_present_at_v2(db_connection: sqlite3.Connection) -> None:
    assert "ontology_rules" in _all_tables(db_connection)
    version = db_connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
    assert version >= 2, f"ontology_rules ships from schema v2 onward, got {version}"


def test_ontology_rules_accepts_valid_restrictive_actions(
    db_connection: sqlite3.Connection,
) -> None:
    with db_connection:
        _seed_audit_row(db_connection)
        for i, action in enumerate(("veto", "size_down")):
            db_connection.execute(
                """
                INSERT INTO ontology_rules
                    (id, ts_utc, ts_ms, rule_type, instrument, window_spec,
                     action, params_json, audit_id, vault_path, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"rule_{action}",
                    "2026-05-20T12:00:00.000Z",
                    1747742400000 + i,
                    "event_window",
                    "NQ",
                    '{"event":"FOMC","pre_min":30,"post_min":30}',
                    action,
                    '{"size_mult":0.5}',
                    1,
                    "20_Ontology/rule.md",
                    1,
                ),
            )
    rows = db_connection.execute("SELECT action FROM ontology_rules ORDER BY ts_ms").fetchall()
    assert [r[0] for r in rows] == ["veto", "size_down"]


def test_ontology_rules_rejects_non_restrictive_action(
    db_connection: sqlite3.Connection,
) -> None:
    """An ontology rule must never carry an APPROVE-like action — only veto/size_down."""
    with db_connection:
        _seed_audit_row(db_connection)
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            """
            INSERT INTO ontology_rules
                (id, ts_utc, ts_ms, rule_type, action, audit_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("rule_bad", "2026-05-20T12:00:00.000Z", 1747742400002, "event_window", "approve", 1),
        )


def test_ontology_rules_active_is_constrained(db_connection: sqlite3.Connection) -> None:
    with db_connection:
        _seed_audit_row(db_connection)
    with pytest.raises(sqlite3.IntegrityError):
        db_connection.execute(
            """
            INSERT INTO ontology_rules
                (id, ts_utc, ts_ms, rule_type, action, audit_id, active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("rule_bad2", "2026-05-20T12:00:00.000Z", 1747742400003, "event_window", "veto", 1, 7),
        )
