"""Loader OHLCV real offline-first vía yfinance (ETF proxies GLD/QQQ).

Aprobado por DEC-012. Descarga de Yahoo Finance y **cachea** en disco; offline-first:
si el cache existe, lo usa **sin tocar la red**. La única ruta que toca la red es la
descarga (`_download`), invocada solo cuando falta el cache o con `refresh=True`
(p. ej. desde el CLI). Los tests NUNCA llaman a la red: usan un fixture commiteado.

El cache es **CSV** (no parquet): `pyarrow`/`fastparquet` no compilan en Python 3.14
en este entorno (ver DEC-012). CSV preserva el contrato offline-first y es diffeable.
Las barras se normalizan al MISMO contrato que `data.loader` (open/high/low/close/
volume, DatetimeIndex UTC, ordenado).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import normalize_ohlcv  # noqa: E402

DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"

# ETF proxies aprobados (DEC-012): símbolo Yahoo → instrumento MIDAS.
PROXY_TO_INSTRUMENT = {"GLD": "GC", "QQQ": "NQ"}


def cache_path(symbol: str, interval: str, cache_dir: Path | None = None) -> Path:
    base = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    return base / f"{symbol}_{interval}.csv"


def _download(symbol: str, start: str | None, end: str | None, interval: str) -> pd.DataFrame:
    """ÚNICA función que toca la red. Los tests la monkeypatchean para garantizar 0 red."""
    import yfinance as yf

    raw = yf.download(
        symbol, start=start, end=end, interval=interval,
        auto_adjust=True, progress=False, multi_level_index=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance no devolvió datos para {symbol} ({interval})")
    return raw


def _normalize_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """Aplana columnas (yfinance puede dar MultiIndex) y normaliza al contrato OHLCV."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Evita columna 'Adj Close' duplicando 'close' (auto_adjust ya la elimina, defensa extra).
    df = df.loc[:, ~df.columns.astype(str).str.lower().str.startswith("adj")]
    return normalize_ohlcv(df)


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="timestamp")


def _read_cache(path: Path) -> pd.DataFrame:
    return normalize_ohlcv(pd.read_csv(path))


def fetch_ohlcv(
    symbol: str,
    *,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    refresh: bool = False,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Devuelve OHLCV normalizado de ``symbol``. Offline-first: usa el cache si existe.

    Solo toca la red si el cache falta o ``refresh=True``. Tras descargar, cachea a CSV.
    """
    path = cache_path(symbol, interval, cache_dir)
    if path.exists() and not refresh:
        return _read_cache(path)

    raw = _download(symbol, start, end, interval)
    df = _normalize_raw(raw)
    _write_cache(df, path)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresca el cache OHLCV desde yfinance (ÚNICA ruta que toca la red)"
    )
    parser.add_argument("symbol", help="símbolo Yahoo, p. ej. GLD o QQQ")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args()

    df = fetch_ohlcv(
        args.symbol, start=args.start, end=args.end, interval=args.interval,
        refresh=True, cache_dir=args.cache_dir,
    )
    instrument = PROXY_TO_INSTRUMENT.get(args.symbol, "?")
    print(
        f"cacheadas {len(df)} barras de {args.symbol} ({args.interval}, proxy→{instrument}) "
        f"→ {cache_path(args.symbol, args.interval, args.cache_dir)}"
    )
    print(f"columnas: {list(df.columns)}  rango: {df.index.min()} … {df.index.max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
