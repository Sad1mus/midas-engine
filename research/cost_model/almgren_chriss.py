"""Cost model Almgren-Chriss v1 — costo de transacción por instrumento/hora.

Estima el costo de ejecutar una orden como la suma de tres componentes:

    costo(X) = ½·spread·tick_value·X            (cruzar el spread)
             + γ·X²                              (impacto permanente)
             + η·X²/horizonte                    (impacto temporal, Almgren-Chriss)

Los coeficientes se **calibran** desde OHLCV/volumen local (sin datos de
ejecución en Fase 1): la volatilidad por barra y el volumen medio (proxy de
liquidez) determinan η y γ por (instrumento, hora). Se persisten en
`cost_model_calibrations`. `estimate_cost(order)` los usa en el backtest.

El costo es monótono creciente en el tamaño de la orden (más contratos ⇒ más
spread + impacto cuadrático), como exige la microestructura.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Specs mínimas por instrumento: (tick_size, tick_value_usd). Fallback genérico.
INSTRUMENT_SPECS: dict[str, tuple[float, float]] = {
    "NQ": (0.25, 5.0),
    "MNQ": (0.25, 0.5),
    "ES": (0.25, 12.5),
    "MES": (0.25, 1.25),
    "GC": (0.10, 10.0),
}
DEFAULT_SPEC = (0.25, 1.0)

# Coeficientes de impacto (calibración v1): impacto ∝ vol_dólar / liquidez.
ETA_COEF = 0.10   # impacto temporal
GAMMA_COEF = 0.10  # impacto permanente


@dataclass(frozen=True)
class CostCalibration:
    instrument: str
    hour_utc: int
    spread_ticks_mean: float
    spread_ticks_p95: float
    impact_eta: float
    impact_gamma: float
    slippage_var: float
    n_samples: int
    tick_value: float = 1.0


@dataclass(frozen=True)
class CostOrder:
    instrument: str
    size: int
    hour_utc: int | None = None
    side: str = "long"


def spec_for(instrument: str) -> tuple[float, float]:
    return INSTRUMENT_SPECS.get(instrument, DEFAULT_SPEC)


def _calibrate_block(
    block: pd.DataFrame, instrument: str, hour: int, tick_size: float, tick_value: float
) -> CostCalibration:
    close = block["close"].to_numpy(dtype=float)
    rng_ticks = (block["high"].to_numpy(dtype=float) - block["low"].to_numpy(dtype=float)) / tick_size
    # spread proxy: ~10% del rango de la barra (acotado a >= 1 tick)
    spread_ticks = np.maximum(1.0, 0.10 * rng_ticks)
    returns = np.diff(np.log(close)) if close.size > 1 else np.array([0.0])
    sigma = float(np.std(returns)) if returns.size else 0.0
    price = float(np.mean(close))
    adv = float(np.mean(block["volume"].to_numpy(dtype=float)))
    sigma_dollar = sigma * price  # volatilidad en $/contrato por barra
    liquidity = max(adv, 1.0)

    return CostCalibration(
        instrument=instrument,
        hour_utc=hour,
        spread_ticks_mean=float(np.mean(spread_ticks)),
        spread_ticks_p95=float(np.percentile(spread_ticks, 95)),
        impact_eta=ETA_COEF * sigma_dollar / liquidity,
        impact_gamma=GAMMA_COEF * sigma_dollar / liquidity,
        slippage_var=float(np.var(spread_ticks)),
        n_samples=int(block.shape[0]),
        tick_value=tick_value,
    )


def calibrate_instrument(
    df: pd.DataFrame,
    instrument: str,
    *,
    db_path: Path,
    tick_size: float | None = None,
    tick_value: float | None = None,
) -> list[CostCalibration]:
    """Calibra el cost model por hora del día y escribe `cost_model_calibrations`."""
    ts0, tv0 = spec_for(instrument)
    tick_size = tick_size if tick_size is not None else ts0
    tick_value = tick_value if tick_value is not None else tv0

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df debe tener DatetimeIndex (usa data.loader)")

    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    calibrations: list[CostCalibration] = []
    conn = sqlite3.connect(db_path)
    try:
        for hour, block in df.groupby(df.index.hour):
            cal = _calibrate_block(block, instrument, int(hour), tick_size, tick_value)
            calibrations.append(cal)
            conn.execute(
                """
                INSERT OR REPLACE INTO cost_model_calibrations
                    (ts_utc, ts_ms, instrument, hour_utc, spread_ticks_mean, spread_ticks_p95,
                     impact_eta, impact_gamma, slippage_var, n_samples)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_iso, ts_ms, instrument, cal.hour_utc, cal.spread_ticks_mean,
                    cal.spread_ticks_p95, cal.impact_eta, cal.impact_gamma,
                    cal.slippage_var, cal.n_samples,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return calibrations


def load_calibration(db_path: Path, instrument: str, hour_utc: int) -> CostCalibration | None:
    """Carga la calibración más reciente para (instrumento, hora)."""
    _, tick_value = spec_for(instrument)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT spread_ticks_mean, spread_ticks_p95, impact_eta, impact_gamma,
                   slippage_var, n_samples
            FROM cost_model_calibrations
            WHERE instrument = ? AND hour_utc = ?
            ORDER BY ts_ms DESC LIMIT 1
            """,
            (instrument, hour_utc),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return CostCalibration(
        instrument=instrument, hour_utc=hour_utc,
        spread_ticks_mean=row[0], spread_ticks_p95=row[1], impact_eta=row[2],
        impact_gamma=row[3], slippage_var=row[4], n_samples=row[5], tick_value=tick_value,
    )


def estimate_cost(
    order: CostOrder,
    *,
    calibration: CostCalibration | None = None,
    db_path: Path | None = None,
    horizon: float = 1.0,
) -> float:
    """Costo esperado (USD) de ejecutar ``order``. Monótono creciente en el tamaño.

    Usa ``calibration`` si se da; si no, la carga de la BD por (instrumento, hora).
    """
    if calibration is None:
        if db_path is None or order.hour_utc is None:
            raise ValueError("se requiere calibration, o db_path + order.hour_utc")
        calibration = load_calibration(db_path, order.instrument, order.hour_utc)
        if calibration is None:
            raise ValueError(
                f"sin calibración para {order.instrument} hora {order.hour_utc}"
            )

    size = abs(int(order.size))
    spread_cost = 0.5 * calibration.spread_ticks_mean * calibration.tick_value * size
    permanent = calibration.impact_gamma * size**2
    temporary = calibration.impact_eta * size**2 / max(horizon, 1e-9)
    return float(spread_cost + permanent + temporary)
