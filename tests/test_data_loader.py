"""Tests del loader OHLCV local (CSV/parquet → DataFrame normalizado)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import (  # noqa: E402
    OHLCV_COLUMNS,
    OHLCVError,
    load_ohlcv,
    normalize_ohlcv,
    synthetic_ohlcv,
)


def _sample_csv(path: Path) -> None:
    path.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-02,10,12,9,11,1000\n"
        "2026-01-01,9,11,8,10,800\n"  # desordenado a propósito
        "2026-01-03,11,13,10,12,1200\n",
        encoding="utf-8",
    )


def test_load_csv_normalizes_schema(tmp_path: Path) -> None:
    csv = tmp_path / "sample.csv"
    _sample_csv(csv)
    df = load_ohlcv(csv)

    assert list(df.columns) == OHLCV_COLUMNS
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing  # se ordenó
    assert len(df) == 3
    # primera fila tras ordenar = 2026-01-01
    assert df.iloc[0]["close"] == 10.0


def test_corrupt_high_below_low_raises(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01,10,8,9,10,100\n",  # high(8) < low(9)
        encoding="utf-8",
    )
    with pytest.raises(OHLCVError, match="high < low"):
        load_ohlcv(csv)


def test_missing_column_raises(tmp_path: Path) -> None:
    csv = tmp_path / "missing.csv"
    csv.write_text(
        "timestamp,open,high,low,close\n2026-01-01,10,12,9,11\n",  # sin volume
        encoding="utf-8",
    )
    with pytest.raises(OHLCVError, match="faltan columnas"):
        load_ohlcv(csv)


def test_non_numeric_value_raises(tmp_path: Path) -> None:
    csv = tmp_path / "nan.csv"
    csv.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-01,abc,12,9,11,100\n",
        encoding="utf-8",
    )
    with pytest.raises(OHLCVError, match=r"NaN|no numéric"):
        load_ohlcv(csv)


def test_unsupported_format_raises(tmp_path: Path) -> None:
    bad = tmp_path / "data.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(OHLCVError, match="formato no soportado"):
        load_ohlcv(bad)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OHLCVError, match="no encontrado"):
        load_ohlcv(tmp_path / "ghost.csv")


def test_synthetic_is_valid_and_deterministic() -> None:
    a = synthetic_ohlcv(200, seed=42)
    b = synthetic_ohlcv(200, seed=42)
    assert list(a.columns) == OHLCV_COLUMNS
    assert len(a) == 200
    assert a.index.is_monotonic_increasing
    assert str(a.index.tz) == "UTC"
    # determinismo
    pd.testing.assert_frame_equal(a, b)
    # coherencia OHLC garantizada por _validate dentro del generador
    assert (a["high"] >= a["low"]).all()


def test_synthetic_regime_shift_increases_volatility() -> None:
    df = synthetic_ohlcv(400, seed=1, regime_shift_at=200, regime_vol_mult=5.0)
    r = df["close"].pct_change().dropna()
    pre = r.iloc[:190].std()
    post = r.iloc[210:].std()
    assert post > pre * 1.5, f"esperaba más vol tras el shift (pre={pre:.4f}, post={post:.4f})"


def test_normalize_accepts_existing_datetime_index() -> None:
    idx = pd.date_range("2026-01-01", periods=3, freq="1min")  # naive
    raw = pd.DataFrame(
        {"open": [1, 2, 3], "high": [2, 3, 4], "low": [0.5, 1.5, 2.5],
         "close": [1.5, 2.5, 3.5], "volume": [10, 20, 30]},
        index=idx,
    )
    df = normalize_ohlcv(raw)
    assert str(df.index.tz) == "UTC"
    assert list(df.columns) == OHLCV_COLUMNS
