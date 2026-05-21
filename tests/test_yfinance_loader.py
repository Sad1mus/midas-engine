"""Tests del loader yfinance offline-first (SIN red — usa el fixture commiteado)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import OHLCV_COLUMNS  # noqa: E402
from data.loaders import yfinance_loader as yl  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _boom(*_a, **_k):
    raise AssertionError("¡_download NO debe llamarse cuando el cache existe (offline-first)!")


def test_offline_first_uses_cache_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yl, "_download", _boom)  # cualquier toque de red = fallo
    df = yl.fetch_ohlcv("GLD", interval="1d", cache_dir=FIXTURES)
    assert len(df) == 200
    assert list(df.columns) == OHLCV_COLUMNS


def test_cache_contract_dtypes_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yl, "_download", _boom)
    df = yl.fetch_ohlcv("GLD", interval="1d", cache_dir=FIXTURES)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    for col in OHLCV_COLUMNS:
        assert pd.api.types.is_numeric_dtype(df[col])
    # datos REALES de GLD 2024: precios plausibles
    assert 150 < df["close"].iloc[-1] < 350


def test_refresh_calls_download_and_writes_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fake_download(symbol, start, end, interval):
        calls["n"] += 1
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        return pd.DataFrame(
            {
                "Open": [1, 2, 3, 4, 5],
                "High": [2, 3, 4, 5, 6],
                "Low": [0.5, 1, 2, 3, 4],
                "Close": [1.5, 2.5, 3.5, 4.5, 5.5],
                "Volume": [10, 20, 30, 40, 50],
            },
            index=idx,
        )

    monkeypatch.setattr(yl, "_download", fake_download)
    df = yl.fetch_ohlcv("FAKE", interval="1d", refresh=True, cache_dir=tmp_path)
    assert calls["n"] == 1
    assert list(df.columns) == OHLCV_COLUMNS
    cache = yl.cache_path("FAKE", "1d", tmp_path)
    assert cache.exists()
    # segunda llamada SIN refresh → no vuelve a descargar (offline-first)
    monkeypatch.setattr(yl, "_download", _boom)
    df2 = yl.fetch_ohlcv("FAKE", interval="1d", cache_dir=tmp_path)
    assert len(df2) == 5


def test_missing_cache_triggers_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = {"n": 0}

    def fake_download(symbol, start, end, interval):
        called["n"] += 1
        idx = pd.date_range("2024-02-01", periods=3, freq="D")
        return pd.DataFrame(
            {
                "Open": [1, 2, 3],
                "High": [2, 3, 4],
                "Low": [0.5, 1, 2],
                "Close": [1.5, 2.5, 3.5],
                "Volume": [10, 20, 30],
            },
            index=idx,
        )

    monkeypatch.setattr(yl, "_download", fake_download)
    yl.fetch_ohlcv("NEW", interval="1d", cache_dir=tmp_path)
    assert called["n"] == 1


def test_proxy_mapping() -> None:
    assert yl.PROXY_TO_INSTRUMENT["GLD"] == "GC"
    assert yl.PROXY_TO_INSTRUMENT["QQQ"] == "NQ"
