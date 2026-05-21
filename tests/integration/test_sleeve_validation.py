"""Validación del sleeve por CPCV → DSR → PBO con veredicto GO/NO-GO."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import synthetic_ohlcv  # noqa: E402
from sleeves.futures_tda.validate import build_returns_matrix, validate_sleeve  # noqa: E402


def test_returns_matrix_shape() -> None:
    close = synthetic_ohlcv(400, seed=1)["close"].to_numpy()
    m = build_returns_matrix(close, (10, 20, 30))
    assert m.ndim == 2
    assert m.shape[1] == 3
    assert m.shape[0] > 100


def test_validate_sleeve_writes_backtest_and_prints_verdict(
    tmp_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    df = synthetic_ohlcv(800, seed=3, drift=0.0005, freq="1min")
    verdict = validate_sleeve(
        df, db_path=tmp_db, sleeve_id="futures_tda_v1", chain_path=tmp_path / "chain.ndjson"
    )

    # veredicto coherente con la regla GO ⟺ DSR>0 y PBO<0.5
    expected = "GO" if (verdict.dsr > 0 and verdict.pbo < 0.5) else "NO-GO"
    assert verdict.verdict == expected
    assert verdict.verdict in ("GO", "NO-GO")
    assert 0.0 <= verdict.pbo <= 1.0
    assert 0.0 <= verdict.dsr <= 1.0
    assert verdict.n_paths == 15  # C(6,2) >= 10

    # el veredicto se imprime (VISIBLE)
    out = capsys.readouterr().out
    assert "VEREDICTO" in out
    assert verdict.verdict in out

    # fila escrita en backtest_results con las métricas requeridas
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT sleeve_id, n_paths, dsr, pbo, max_dd_pct, embargo_pct, purge_pct "
            "FROM backtest_results WHERE sleeve_id='futures_tda_v1' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "futures_tda_v1"
    assert row[1] == 15
    assert 0.0 <= row[3] <= 1.0  # pbo persistido
    assert row[5] == 0.01 and row[6] == 0.02  # embargo/purge persistidos


def test_overfit_sleeve_gets_nogo(tmp_db: Path, tmp_path: Path) -> None:
    """El sleeve v1 sobre datos sintéticos NO generaliza → el pipeline lo frena (NO-GO)."""
    df = synthetic_ohlcv(800, seed=3, drift=0.0008, freq="1min")
    verdict = validate_sleeve(
        df, db_path=tmp_db, sleeve_id="trend_sleeve",
        chain_path=tmp_path / "chain.ndjson", verbose=False,
    )
    # PBO alto ⇒ la selección de lookback no generaliza ⇒ NO-GO (el pipeline protege)
    assert verdict.verdict == "NO-GO"
    assert verdict.pbo >= 0.5
