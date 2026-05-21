"""Tests del cost model Almgren-Chriss v1."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import synthetic_ohlcv  # noqa: E402
from research.cost_model import (  # noqa: E402
    CostOrder,
    calibrate_instrument,
    estimate_cost,
    load_calibration,
)


def test_calibration_writes_coherent_params(tmp_db: Path) -> None:
    df = synthetic_ohlcv(600, seed=5, freq="1min")  # ~10 horas
    cals = calibrate_instrument(df, "NQ", db_path=tmp_db)
    assert cals, "se esperaba al menos una calibración por hora"

    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT instrument, hour_utc, spread_ticks_mean, impact_eta, impact_gamma, n_samples "
            "FROM cost_model_calibrations"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "no se escribió cost_model_calibrations"
    for instrument, hour, spread, eta, gamma, n in rows:
        assert instrument == "NQ"
        assert 0 <= hour < 24
        assert spread >= 1.0  # al menos un tick
        assert eta > 0 and gamma > 0  # impactos positivos
        assert n > 0


def test_estimate_cost_increases_with_order_size(tmp_db: Path) -> None:
    df = synthetic_ohlcv(600, seed=5, freq="1min")
    calibrate_instrument(df, "NQ", db_path=tmp_db)
    hour = int(df.index[0].hour)
    cal = load_calibration(tmp_db, "NQ", hour)
    assert cal is not None

    c1 = estimate_cost(CostOrder("NQ", 1), calibration=cal)
    c5 = estimate_cost(CostOrder("NQ", 5), calibration=cal)
    c20 = estimate_cost(CostOrder("NQ", 20), calibration=cal)
    assert c1 < c5 < c20, f"el costo debe crecer con el tamaño: {c1}, {c5}, {c20}"
    assert c1 > 0


def test_estimate_cost_loads_from_db_by_hour(tmp_db: Path) -> None:
    df = synthetic_ohlcv(600, seed=9, freq="1min")
    calibrate_instrument(df, "NQ", db_path=tmp_db)
    hour = int(df.index[10].hour)
    cost = estimate_cost(CostOrder("NQ", 3, hour_utc=hour), db_path=tmp_db)
    assert cost > 0


def test_estimate_cost_quadratic_impact_dominates_large_orders(tmp_db: Path) -> None:
    df = synthetic_ohlcv(600, seed=5, freq="1min")
    calibrate_instrument(df, "NQ", db_path=tmp_db)
    cal = load_calibration(tmp_db, "NQ", int(df.index[0].hour))
    assert cal is not None
    # doblar el tamaño más que dobla el costo (impacto cuadrático)
    c10 = estimate_cost(CostOrder("NQ", 10), calibration=cal)
    c20 = estimate_cost(CostOrder("NQ", 20), calibration=cal)
    assert c20 > 2 * c10
