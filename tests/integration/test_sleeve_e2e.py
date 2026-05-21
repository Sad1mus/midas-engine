"""E2E del primer sleeve (paper · Track B): loop completo con el Veto obligatorio."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.risk.kill_switch import KillSwitch  # noqa: E402
from data.loader import synthetic_ohlcv  # noqa: E402
from infra.audit import chain  # noqa: E402
from scripts import verify_chain  # noqa: E402
from sleeves.futures_tda.runner import SleeveRunner  # noqa: E402


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    try:
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        filled = conn.execute("SELECT COUNT(*) FROM trades WHERE status='filled'").fetchone()[0]
        vetoed = conn.execute("SELECT COUNT(*) FROM trades WHERE status='vetoed'").fetchone()[0]
        execs = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        equity = conn.execute("SELECT COUNT(*) FROM equity_curve").fetchone()[0]
        # ninguna ejecución cuelga de un trade no-filled (nada se ejecuta sin pasar el Veto)
        orphan = conn.execute(
            "SELECT COUNT(*) FROM executions e JOIN trades t ON t.id = e.trade_id "
            "WHERE t.status != 'filled'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "trades": trades, "filled": filled, "vetoed": vetoed,
        "execs": execs, "equity": equity, "orphan": orphan,
    }


def test_sleeve_runs_end_to_end(tmp_db: Path, tmp_path: Path) -> None:
    chain_path = tmp_path / "chain.ndjson"
    df = synthetic_ohlcv(400, seed=11, regime_shift_at=200, regime_vol_mult=4.0, freq="1min")
    runner = SleeveRunner(db_path=tmp_db, chain_path=chain_path, warmup=100, step=25)
    ks = KillSwitch(db_path=tmp_path / "kill.db")

    result = runner.run(df, kill_switch=ks)

    # generó decisiones, trades y curva de equity
    assert result.n_decisions > 3
    assert result.n_executions > 0
    c = _counts(tmp_db)
    assert c["trades"] == result.n_decisions
    assert c["equity"] > 0
    # cada ejecución corresponde a un trade 'filled' — nada se ejecuta sin pasar el Veto
    assert c["execs"] == c["filled"] == result.n_executions
    assert c["orphan"] == 0
    # el límite de trades diarios (regla dura) veta los trades extra → hay rechazos
    assert c["vetoed"] > 0
    assert result.n_executions <= 3  # max_daily_trades = 3

    # la cadena de auditoría (trade + gate_trip) verifica
    events = chain.read_events(chain_path)
    assert any(e["event_type"] == "trade" for e in events)
    ok, msg = chain.verify_file(chain_path)
    assert ok, msg
    assert verify_chain.verify_chain(tmp_db) == 0


def test_kill_switch_blocks_all_executions(tmp_db: Path, tmp_path: Path) -> None:
    chain_path = tmp_path / "chain.ndjson"
    df = synthetic_ohlcv(300, seed=4, freq="1min")
    runner = SleeveRunner(db_path=tmp_db, chain_path=chain_path, warmup=100, step=25)
    ks = KillSwitch(db_path=tmp_path / "kill.db")
    ks.trigger("revisión manual — sin ejecución")

    result = runner.run(df, kill_switch=ks)

    assert result.n_decisions > 0
    assert result.n_executions == 0, "con kill switch NADA debe ejecutarse"
    c = _counts(tmp_db)
    assert c["filled"] == 0
    assert c["execs"] == 0
    assert c["vetoed"] == result.n_decisions  # todo vetado por el Veto
