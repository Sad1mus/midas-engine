"""Tests del motor de DD-gates (drawdown-first)."""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.models import AccountState, RiskVerdict, TradeSignal  # noqa: E402
from core.risk import gates, manager  # noqa: E402
from core.risk.gates import GateState  # noqa: E402
from core.risk.kill_switch import KillSwitch  # noqa: E402
from infra.audit import chain  # noqa: E402
from scripts import verify_chain  # noqa: E402

Signal = Callable[..., TradeSignal]
State = Callable[..., AccountState]


def _insert_equity(conn: sqlite3.Connection, equity: float, ts_ms: int) -> None:
    peak = max(
        equity,
        (conn.execute("SELECT COALESCE(MAX(peak_equity), 0) FROM equity_curve").fetchone()[0]),
    )
    dd = (peak - equity) / peak if peak > 0 else 0.0
    conn.execute(
        """
        INSERT INTO equity_curve (ts_utc, ts_ms, sleeve_id, track, equity, peak_equity, drawdown_pct)
        VALUES (?, ?, NULL, 'agg', ?, ?, ?)
        """,
        (f"2026-01-01T00:00:{ts_ms % 60:02d}.000Z", ts_ms, equity, peak, dd),
    )
    conn.commit()


# ── unidad ────────────────────────────────────────────────────────────


def test_level_for_drawdown_ladder() -> None:
    assert gates.level_for_drawdown(0.0) == ("normal", 1.0)
    assert gates.level_for_drawdown(0.06) == ("dd5", 0.5)
    assert gates.level_for_drawdown(0.11) == ("dd10", 0.25)
    assert gates.level_for_drawdown(0.16) == ("dd15", 0.125)
    assert gates.level_for_drawdown(0.25) == ("halt", 0.0)


def test_apply_sizing_only_reduces() -> None:
    g = GateState(level="dd5", sizing_mult=0.5, drawdown_pct=0.06)
    assert gates.apply_sizing(10, g) == 5
    assert gates.apply_sizing(10, None) == 10
    # invariante: aunque venga un mult > 1 (no debería), nunca aumenta
    bad = GateState(level="normal", sizing_mult=2.0, drawdown_pct=0.0)
    assert gates.apply_sizing(10, bad) == 10


# ── integración con equity_curve + chain ───────────────────────────────


def test_drawdown_ladder_trips_and_writes_chain(tmp_db: Path, tmp_path: Path) -> None:
    chain_path = tmp_path / "chain.ndjson"
    conn = sqlite3.connect(tmp_db)

    expected = [
        (100_000, "normal", 1.0, False),
        (94_000, "dd5", 0.5, True),  # -6%
        (89_000, "dd10", 0.25, True),  # -11%
        (84_000, "dd15", 0.125, True),  # -16%
        (79_000, "halt", 0.0, True),  # -21%
    ]
    for i, (equity, level, mult, should_trip) in enumerate(expected):
        _insert_equity(conn, equity, ts_ms=1_000 + i)
        g = gates.evaluate_gate(
            conn,
            track="agg",
            chain_path=chain_path,
            ts=dt.datetime(2026, 1, 1, 0, 0, i, tzinfo=dt.UTC),
        )
        assert g.level == level, f"equity {equity} → esperaba {level}, fue {g.level}"
        assert g.sizing_mult == mult
        assert g.tripped is should_trip

    # cada cruce real escribió un gate_trip en el chain (dd5, dd10, dd15, halt = 4)
    trips = [e for e in chain.read_events(chain_path) if e["event_type"] == "gate_trip"]
    assert len(trips) == 4
    assert [t["payload"]["to_level"] for t in trips] == ["dd5", "dd10", "dd15", "halt"]

    # risk_gates_state registró todas las evaluaciones
    rows = conn.execute(
        "SELECT gate_level, sizing_mult FROM risk_gates_state ORDER BY id"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["normal", "dd5", "dd10", "dd15", "halt"]

    # el chain (archivo) verifica
    ok, msg = chain.verify_file(chain_path)
    assert ok, msg
    assert verify_chain.verify_chain(tmp_db) == 0


# ── integración con el Risk Manager ─────────────────────────────────────


def test_manager_halt_gate_rejects(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    halt = GateState(level="halt", sizing_mult=0.0, drawdown_pct=0.21)
    decision = manager.evaluate(make_signal(), make_state(), kill_switch=kill, gate=halt)
    assert decision.verdict == RiskVerdict.REJECT
    assert decision.approved_size_contracts == 0
    assert any("HALT" in r for r in decision.reasons)


def test_manager_gate_reduces_size(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    # sin gate: aprueba 2 contratos (caso base conocido)
    base = manager.evaluate(make_signal(), make_state(), kill_switch=kill)
    assert base.approved_size_contracts == 2
    # con dd10 (×0.25): 2 × 0.25 = 0 (recorte agresivo) → REJECT por tamaño cero
    dd10 = GateState(level="dd10", sizing_mult=0.25, drawdown_pct=0.11)
    reduced = manager.evaluate(make_signal(), make_state(), kill_switch=kill, gate=dd10)
    assert reduced.approved_size_contracts == 0
    # con dd5 (×0.5) sobre 10 sugeridos (cap dólar 3) → 3 × 0.5 = 1
    sig = make_signal(suggested_size_contracts=10, risk_per_contract_usd="200")
    dd5 = GateState(level="dd5", sizing_mult=0.5, drawdown_pct=0.06)
    out = manager.evaluate(sig, make_state(), kill_switch=kill, gate=dd5)
    assert out.approved_size_contracts == 1
    assert any("DD-gate dd5" in r for r in out.reasons)
