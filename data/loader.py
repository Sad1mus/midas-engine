"""Capa de datos — carga OHLCV desde archivos locales (CSV / parquet).

Fase 1: SOLO archivos locales. Nada de fuentes en vivo (broker/ccxt/yfinance) —
eso requiere un DEC de datos (DEC-001 lockdown). Este módulo normaliza cualquier
CSV/parquet razonable a un DataFrame canónico:

  * columnas exactamente: ``open, high, low, close, volume`` (float, salvo volume)
  * ``DatetimeIndex`` en UTC, ordenado ascendente, sin duplicados
  * validaciones de coherencia OHLC (high≥low, sin NaN, volume≥0)

Incluye un generador sintético determinista (``synthetic_ohlcv``) para tests y
para alimentar la Variable 𝒳 (TDA) y el pipeline anti-overfitting sin datos reales.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Alias frecuentes → nombre canónico.
_COLUMN_ALIASES = {
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "vol": "volume",
    "adj close": "close",
    "adj_close": "close",
}
_TIMESTAMP_ALIASES = ("timestamp", "time", "date", "datetime", "ts", "dt")


class OHLCVError(ValueError):
    """Error de carga/validación de datos OHLCV (schema o coherencia)."""


def _canonical_name(name: str) -> str:
    key = str(name).strip().lower()
    return _COLUMN_ALIASES.get(key, key)


def _find_timestamp(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if str(col).strip().lower() in _TIMESTAMP_ALIASES:
            return col
    return None


def normalize_ohlcv(df: pd.DataFrame, *, timestamp_col: str | None = None) -> pd.DataFrame:
    """Normaliza un DataFrame arbitrario al esquema OHLCV canónico. Pura, sin I/O."""
    df = df.copy()

    # 1. Localizar y fijar el índice temporal (UTC).
    if timestamp_col is None and not isinstance(df.index, pd.DatetimeIndex):
        timestamp_col = _find_timestamp(df)
    if timestamp_col is not None:
        if timestamp_col not in df.columns:
            raise OHLCVError(f"columna de tiempo '{timestamp_col}' no está en los datos")
        idx = pd.to_datetime(df[timestamp_col], utc=True, errors="coerce")
        df = df.drop(columns=[timestamp_col])
        df.index = pd.DatetimeIndex(idx)
    elif isinstance(df.index, pd.DatetimeIndex):
        df.index = (
            df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
        )
    else:
        raise OHLCVError("no se encontró columna de tiempo (timestamp/date/...) ni DatetimeIndex")

    if df.index.isna().any():
        raise OHLCVError("hay timestamps no parseables (NaT) tras la conversión")

    # 2. Renombrar columnas a canónicas y exigir el set OHLCV.
    df.columns = [_canonical_name(c) for c in df.columns]
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise OHLCVError(f"faltan columnas requeridas: {missing}; presentes: {list(df.columns)}")
    df = df[OHLCV_COLUMNS]

    # 3. Tipado numérico.
    for col in OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 4. Orden temporal + deduplicado (conserva la última observación del timestamp).
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    _validate(df)
    return df


def _validate(df: pd.DataFrame) -> None:
    if df.empty:
        raise OHLCVError("el dataset OHLCV está vacío")
    if df[OHLCV_COLUMNS].isna().any().any():
        bad = df.columns[df[OHLCV_COLUMNS].isna().any()].tolist()
        raise OHLCVError(f"valores no numéricos / NaN en columnas: {bad}")
    if not df.index.is_monotonic_increasing:
        raise OHLCVError("el índice temporal no es monótono creciente tras ordenar")
    hi, lo = df["high"], df["low"]
    o, c = df["open"], df["close"]
    if (hi < lo).any():
        raise OHLCVError("incoherencia OHLC: high < low en alguna fila")
    if (hi < o).any() or (hi < c).any():
        raise OHLCVError("incoherencia OHLC: high < open/close en alguna fila")
    if (lo > o).any() or (lo > c).any():
        raise OHLCVError("incoherencia OHLC: low > open/close en alguna fila")
    if (df["volume"] < 0).any():
        raise OHLCVError("volume negativo")


def load_ohlcv(path: str | Path, *, timestamp_col: str | None = None) -> pd.DataFrame:
    """Carga OHLCV desde un archivo local CSV o parquet y lo normaliza/valida."""
    p = Path(path)
    if not p.exists():
        raise OHLCVError(f"archivo no encontrado: {p}")
    suffix = p.suffix.lower()
    if suffix == ".csv":
        raw = pd.read_csv(p)
    elif suffix in (".parquet", ".pq"):
        try:
            raw = pd.read_parquet(p)
        except ImportError as e:  # sin pyarrow/fastparquet (no están en el stack lockdown)
            raise OHLCVError(
                "leer parquet requiere un engine (pyarrow/fastparquet) que no está en el "
                "stack Fase 1; usa CSV o abre un DEC para añadir el engine"
            ) from e
        if not isinstance(raw, pd.DataFrame):
            raise OHLCVError("el parquet no produjo un DataFrame")
    else:
        raise OHLCVError(f"formato no soportado: '{suffix}' (usa .csv o .parquet)")
    return normalize_ohlcv(raw, timestamp_col=timestamp_col)


def synthetic_ohlcv(
    n: int = 500,
    *,
    start: str = "2026-01-01",
    freq: str = "1min",
    seed: int = 0,
    start_price: float = 100.0,
    drift: float = 0.0,
    vol: float = 0.01,
    regime_shift_at: int | None = None,
    regime_vol_mult: float = 4.0,
) -> pd.DataFrame:
    """Genera un OHLCV sintético determinista (precio tipo GBM) para tests/research.

    Si ``regime_shift_at`` se indica, la volatilidad se multiplica por
    ``regime_vol_mult`` a partir de ese índice — útil para probar detección de régimen.
    """
    if n <= 0:
        raise OHLCVError("n debe ser > 0")
    rng = np.random.default_rng(seed)
    index = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")

    vols = np.full(n, vol)
    if regime_shift_at is not None and 0 <= regime_shift_at < n:
        vols[regime_shift_at:] *= regime_vol_mult

    returns = rng.normal(loc=drift, scale=vols, size=n)
    close = start_price * np.exp(np.cumsum(returns))
    prev_close = np.concatenate([[start_price], close[:-1]])

    open_ = prev_close
    noise_hi = np.abs(rng.normal(0.0, vols, size=n)) * close
    noise_lo = np.abs(rng.normal(0.0, vols, size=n)) * close
    high = np.maximum(open_, close) + noise_hi
    low = np.minimum(open_, close) - noise_lo
    volume = rng.integers(100, 10_000, size=n).astype(float)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
    df.index.name = "timestamp"
    _validate(df)
    return df
