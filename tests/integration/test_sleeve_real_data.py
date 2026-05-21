"""El SleeveRunner sobre barras REALES de GLD (fixture, SIN red) → GC."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.risk.kill_switch import KillSwitch  # noqa: E402
from data.loaders import yfinance_loader as yl  # noqa: E402
from infra.audit import chain  # noqa: E402
from scripts import verify_chain  # noqa: E402
from sleeves.futures_tda.runner import SleeveRunner  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def test_runner_on_real_gld_bars(tmp_db: Path, tmp_path: Path) -> None:
    # barras REALES de GLD desde el fixture (offline-first, sin red)
    df = yl.fetch_ohlcv("GLD", interval="1d", cache_dir=FIXTURES)
    assert len(df) == 200
    instrument = yl.PROXY_TO_INSTRUMENT["GLD"]  # GLD → GC
    assert instrument == "GC"

    chain_path = tmp_path / "chain.ndjson"
    runner = SleeveRunner(
        db_path=tmp_db,
        chain_path=chain_path,
        sleeve_id="gld_gc_real",
        instrument=instrument,
        warmup=60,
        step=15,
    )
    result = runner.run(df, kill_switch=KillSwitch(db_path=tmp_path / "kill.db"))

    # corrió el loop completo sobre datos reales
    assert result.n_decisions > 0
    conn = sqlite3.connect(tmp_db)
    try:
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        equity = conn.execute("SELECT COUNT(*) FROM equity_curve").fetchone()[0]
        # ninguna ejecución cuelga de un trade que no pasó el Veto
        orphan = conn.execute(
            "SELECT COUNT(*) FROM executions e JOIN trades t ON t.id = e.trade_id "
            "WHERE t.status != 'filled'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert trades == result.n_decisions
    assert equity > 0
    assert orphan == 0  # el Veto es infranqueable

    # auditoría íntegra sobre el chain de test
    ev = chain.read_events(chain_path)
    assert any(e["event_type"] == "trade" for e in ev)
    ok, msg = chain.verify_file(chain_path)
    assert ok, msg
    assert verify_chain.verify_chain(tmp_db) == 0


def test_proxy_instrument_specs_exist() -> None:
    """Los instrumentos mapeados existen en INSTRUMENT_SPECS (GLD→GC, QQQ→NQ)."""
    from research.cost_model.almgren_chriss import INSTRUMENT_SPECS

    for proxy, instrument in yl.PROXY_TO_INSTRUMENT.items():
        assert instrument in INSTRUMENT_SPECS, f"{proxy}→{instrument} sin spec"
