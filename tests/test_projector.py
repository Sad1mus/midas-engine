"""Tests del projector core (SQLite → Markdown, read-only, idempotente)."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.obsidian_sync import projector  # noqa: E402


def _populate(db: Path) -> None:
    """Puebla un .db de prueba con filas mínimas para todas las entidades."""
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """INSERT INTO audit_log (ts_utc, ts_ms, actor, event_type, payload_json,
           payload_hash, prev_hash, chain_hash)
           VALUES ('2026-01-01T00:00:00.000Z', 1, 'test', 'trade', '{}',
           ?, ?, ?)""",
        ("a" * 64, "0" * 64, "b" * 64),
    )
    conn.execute(
        """INSERT INTO equity_curve (ts_utc, ts_ms, sleeve_id, track, equity, peak_equity, drawdown_pct)
           VALUES ('2026-01-01T01:00:00.000Z', 3600000, NULL, 'agg', 47000.0, 50000.0, 0.06)"""
    )
    conn.execute(
        """INSERT INTO risk_gates_state (ts_utc, ts_ms, current_dd_pct, gate_level, sizing_mult)
           VALUES ('2026-01-01T01:00:00.000Z', 3600000, 0.06, 'dd5', 0.5)"""
    )
    conn.execute(
        """INSERT INTO sleeves (id, name, track, status, asset_class, instruments, created_utc)
           VALUES ('futures_tda_v1', 'TDA NQ', 'B', 'paper', 'futures', '["NQ"]',
           '2026-01-01T00:00:00.000Z')"""
    )
    conn.execute(
        """INSERT INTO backtest_results (sleeve_id, git_commit, ts_utc, ts_ms, n_paths,
           sharpe_mean, sharpe_std, dsr, pbo, max_dd_pct, embargo_pct, purge_pct,
           config_json, audit_id)
           VALUES ('futures_tda_v1', 'abc123', '2026-01-01T02:00:00.000Z', 7200000, 15,
           0.05, 0.2, 0.67, 0.95, 0.14, 0.01, 0.02, '{}', 1)"""
    )
    conn.execute(
        """INSERT INTO regime_states (ts_utc, ts_ms, classifier, regime_label, posterior_json, features_json)
           VALUES ('2026-01-01T01:30:00.000Z', 5400000, 'tda_v1', 'shift',
           '{"shift":0.8}', '{"d_topo":0.055}')"""
    )
    conn.execute(
        """INSERT INTO trades (id, sleeve_id, track, ts_signal_utc, ts_signal_ms, instrument,
           side, size_target, status, audit_id)
           VALUES ('t1', 'futures_tda_v1', 'B', '2026-01-01T01:00:00.000Z', 3600000, 'NQ',
           'long', 2.0, 'filled', 1)"""
    )
    conn.execute(
        """INSERT INTO trades (id, sleeve_id, track, ts_signal_utc, ts_signal_ms, instrument,
           side, size_target, status, veto_reason, audit_id)
           VALUES ('t2', 'futures_tda_v1', 'B', '2026-01-01T01:10:00.000Z', 4200000, 'NQ',
           'long', 0.0, 'vetoed', 'Máx trades diarios', 1)"""
    )
    conn.execute(
        """INSERT INTO ontology_rules (id, ts_utc, ts_ms, rule_type, instrument, action,
           params_json, audit_id, active)
           VALUES ('rule_nq_fomc', '2026-01-01T00:30:00.000Z', 1800000, 'event_window', 'NQ',
           'veto', '{}', 1, 1)"""
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.md"))}


def test_project_all_writes_parseable_yaml(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    vault = tmp_path / "vault"
    summary = projector.project_all(tmp_db, vault)

    notes = list((vault / "_generated").rglob("*.md"))
    assert len(notes) >= 1
    # frontmatter YAML parseable + generated: true en cada nota
    for note in notes:
        text = note.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        assert meta["generated"] is True
        assert "no editar a mano" in text.lower()

    assert summary.counts.get("equity") == 1
    assert summary.gate_level == "dd5"
    # NUNCA escribe en notas humanas
    assert not (vault / "10_Decisions").exists()


def test_project_all_is_idempotent(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    vault = tmp_path / "vault"
    projector.project_all(tmp_db, vault)
    snap1 = _snapshot_tree(vault)
    projector.project_all(tmp_db, vault)
    snap2 = _snapshot_tree(vault)
    assert snap1 == snap2, "project_all debe ser idempotente (mismos bytes)"


def test_db_is_not_modified(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    before = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
    projector.project_all(tmp_db, tmp_path / "vault")
    after = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
    assert before == after, "la proyección NO debe modificar la BD (read-only)"


def _frontmatter(note: Path) -> dict:
    return yaml.safe_load(note.read_text(encoding="utf-8").split("---\n", 2)[1])


def test_dashboard_index_generated_with_referenced_fields(tmp_db: Path, tmp_path: Path) -> None:
    _populate(tmp_db)
    vault = tmp_path / "vault"
    projector.project_all(tmp_db, vault)

    index = vault / "00_System" / "cockpit" / "MIDAS — Cockpit.md"
    assert index.exists(), "falta la nota índice del cockpit"
    text = index.read_text(encoding="utf-8")
    assert "Qué estoy viendo" in text
    assert text.count("```dataview") == len(projector.DASHBOARD_SECTIONS) == 7

    # cada query Dataview sobre _generated referencia campos que SÍ existen en esas notas
    for _title, folder, type_filter, fields, _sort, _limit in projector.DASHBOARD_SECTIONS:
        if not folder.startswith(projector.GENERATED_DIR):
            continue  # la sección de DEC lee notas humanas (no generadas aquí)
        notes = list((vault / folder).glob("*.md"))
        assert notes, f"la query sobre {folder} no tiene notas generadas"
        for note in notes:
            meta = _frontmatter(note)
            if type_filter and meta.get("type") != type_filter:
                continue
            missing = [f for f in fields if f not in meta]
            assert not missing, f"{note.name} ({type_filter}) sin campos {missing}"


def test_connection_is_read_only(tmp_db: Path) -> None:
    _populate(tmp_db)
    conn = projector._connect_ro(tmp_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO sleeves (id, name, track, status, asset_class, "
                "instruments, created_utc) VALUES ('x','x','B','paper','futures','[]','t')"
            )
    finally:
        conn.close()
