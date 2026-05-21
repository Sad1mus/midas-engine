"""Corre el sleeve sobre DATOS REALES (GLD, QQQ) y puebla el chain de PRODUCCIÓN.

Flujo (DEC-012/DEC-013):
  1. Reconstruye el `.db` de producción desde el chain (`init_db`).
  2. Para cada proxy (GLD→GC, QQQ→NQ): carga OHLCV real (offline-first; descarga solo
     si falta el cache o con --refresh), corre el `SleeveRunner` y escribe
     trades/executions/equity_curve al `.db` real + eventos `trade`/`gate_trip` al
     chain de PRODUCCIÓN (así el cockpit se puebla).
  3. Imprime un resumen VISIBLE por símbolo.

La ÚNICA ruta que toca la red es la descarga del loader. Si no hay red y no hay
cache, cae al fixture commiteado (tests/fixtures) y lo deja EXPLÍCITO en el log.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.risk.kill_switch import KillSwitch  # noqa: E402
from data.loaders import yfinance_loader as yl  # noqa: E402
from infra.audit import chain  # noqa: E402
from scripts.init_db import DEFAULT_DB, init_db  # noqa: E402
from sleeves.futures_tda.runner import RunResult, SleeveRunner  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
SYMBOLS = ("GLD", "QQQ")


def _load_bars(symbol: str, interval: str, start: str, refresh: bool) -> tuple[pd.DataFrame, str]:
    """Carga OHLCV real. Devuelve (df, fuente). Cae al fixture si no hay red ni cache."""
    try:
        df = yl.fetch_ohlcv(symbol, start=start, interval=interval, refresh=refresh)
        return df, ("red/cache" if refresh else "cache/red")
    except Exception as e:
        fixture = FIXTURES / f"{symbol}_{interval}.csv"
        if fixture.exists():
            print(f"  [!] descarga falló ({e}); usando FIXTURE {fixture.name} (sin red)")
            return yl.fetch_ohlcv(symbol, interval=interval, cache_dir=FIXTURES), "fixture"
        raise


def _summary(db_path: Path, sleeve_id: str, r: RunResult, bars: int, instrument: str, src: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        dd = conn.execute(
            "SELECT MAX(drawdown_pct) FROM equity_curve WHERE sleeve_id = ?", (sleeve_id,)
        ).fetchone()[0]
        gate = conn.execute(
            "SELECT gate_level, sizing_mult FROM risk_gates_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    print(f"── {sleeve_id}  (proxy→{instrument}, fuente={src}) ──")
    print(f"   barras={bars}  decisiones={r.n_decisions}  filled={r.n_executions}  vetoed={r.n_vetoed}")
    print(f"   equity: {r.starting_equity:.0f} → {r.final_equity:.2f}")
    print(f"   max drawdown: {(dd or 0.0):.4f}   gate: {gate[0] if gate else 'normal'} (×{gate[1] if gate else 1.0})")


def run(
    *, db_path: Path, chain_path: Path, symbols: tuple[str, ...],
    interval: str, start: str, refresh: bool, warmup: int, step: int,
) -> int:
    print(f"reconstruyendo .db de producción desde el chain → {db_path}")
    init_db(db_path, chain_path=chain_path)

    ks = KillSwitch(db_path=REPO_ROOT / ".midas" / "kill.db")
    print(f"\ncorriendo el sleeve sobre datos REALES: {', '.join(symbols)}\n")
    for symbol in symbols:
        instrument = yl.PROXY_TO_INSTRUMENT.get(symbol)
        if instrument is None:
            print(f"  [!] {symbol} sin mapeo proxy→instrumento; saltando")
            continue
        df, src = _load_bars(symbol, interval, start, refresh)
        sleeve_id = f"{symbol.lower()}_{instrument.lower()}_real"
        runner = SleeveRunner(
            db_path=db_path, chain_path=chain_path, sleeve_id=sleeve_id,
            instrument=instrument, warmup=warmup, step=step,
        )
        result = runner.run(df, kill_switch=ks)
        _summary(db_path, sleeve_id, result, len(df), instrument, src)
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sleeve sobre datos reales → chain de producción")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--chain-file", type=Path, default=chain.DEFAULT_CHAIN)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--refresh", action="store_true", help="forzar descarga (toca la red)")
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--step", type=int, default=20)
    args = parser.parse_args()

    return run(
        db_path=args.db, chain_path=args.chain_file,
        symbols=tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip()),
        interval=args.interval, start=args.start, refresh=args.refresh,
        warmup=args.warmup, step=args.step,
    )


if __name__ == "__main__":
    sys.exit(main())
