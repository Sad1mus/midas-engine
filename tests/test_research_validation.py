"""Tests del pipeline anti-overfitting v1 — CPCV → DSR → PBO → backtest_results."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.backtest import run_validation  # noqa: E402
from research.cpcv import cpcv_split, n_paths  # noqa: E402
from research.deflated_sharpe import deflated_sharpe_ratio, sharpe_ratio  # noqa: E402
from research.pbo import pbo  # noqa: E402


def _robust_matrix(t_obs: int = 1000, n_configs: int = 20, seed: int = 0) -> np.ndarray:
    """Una config con edge real y persistente; el resto, ruido."""
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, size=(t_obs, n_configs))
    m[:, 0] += 0.004  # drift positivo consistente in/out of sample
    return m


def _overfit_matrix(
    t_obs: int = 1000, n_configs: int = 20, d: float = 0.004, seed: int = 0
) -> np.ndarray:
    """Edge in-sample que se invierte out-of-sample (ganador IS = perdedor OOS)."""
    rng = np.random.default_rng(seed)
    mid = t_obs // 2
    spread = np.linspace(-d, d, n_configs)
    m = rng.normal(0, 0.01, size=(t_obs, n_configs))
    m[:mid, :] += spread[None, :]
    m[mid:, :] += -spread[None, :]
    return m


# ── CPCV ──────────────────────────────────────────────────────────────


def test_cpcv_paths_count_and_disjoint() -> None:
    assert n_paths(6, 2) == 15  # C(6,2), >= 10 (CHECK de backtest_results)
    splits = list(cpcv_split(600, n_groups=6, k_test=2, embargo_pct=0.01, purge_pct=0.02))
    assert len(splits) == 15
    for s in splits:
        assert set(s.train_idx).isdisjoint(set(s.test_idx))


def test_cpcv_purge_embargo_shrinks_train() -> None:
    no_purge = next(cpcv_split(600, n_groups=6, k_test=2, embargo_pct=0.0, purge_pct=0.0))
    with_purge = next(cpcv_split(600, n_groups=6, k_test=2, embargo_pct=0.05, purge_pct=0.05))
    assert with_purge.train_idx.size < no_purge.train_idx.size


# ── DSR ───────────────────────────────────────────────────────────────


def test_dsr_in_unit_interval_and_deflates() -> None:
    rng = np.random.default_rng(1)
    edge = rng.normal(0.004, 0.01, size=1000)
    dsr_1 = deflated_sharpe_ratio(edge, n_trials=1)
    dsr_many = deflated_sharpe_ratio(edge, n_trials=200)
    assert 0.0 <= dsr_many <= 1.0
    assert dsr_many <= dsr_1  # más pruebas ⇒ más deflación
    noise = rng.normal(0.0, 0.01, size=1000)
    assert deflated_sharpe_ratio(noise, n_trials=200) < deflated_sharpe_ratio(edge, n_trials=200)
    assert sharpe_ratio(edge) > sharpe_ratio(noise)


# ── PBO ───────────────────────────────────────────────────────────────


def test_pbo_overfit_high_robust_low() -> None:
    over = pbo(_overfit_matrix(seed=0), n_partitions=10)
    robust = pbo(_robust_matrix(seed=0), n_partitions=10)
    assert over.pbo > 0.5, f"overfit PBO debería ser alto, fue {over.pbo}"
    assert robust.pbo < 0.1, f"robust PBO debería ser bajo, fue {robust.pbo}"


# ── Orquestador → backtest_results ────────────────────────────────────


def _read_backtest_row(db: Path) -> tuple:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT n_paths, pbo, dsr, sharpe_mean, embargo_pct, purge_pct FROM backtest_results "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()


def test_run_validation_overfit_writes_high_pbo(tmp_db: Path, tmp_path: Path) -> None:
    metrics = run_validation(
        _overfit_matrix(seed=2),
        db_path=tmp_db,
        sleeve_id="overfit_candidate",
        chain_path=tmp_path / "chain.ndjson",
    )
    assert metrics["pbo"] > 0.5
    assert metrics["n_paths"] == 15  # >= 10
    row = _read_backtest_row(tmp_db)
    assert row[0] == 15
    assert row[1] > 0.5  # pbo persistido
    assert 0.0 <= row[2] <= 1.0  # dsr en [0,1]


def test_run_validation_robust_writes_low_pbo(tmp_db: Path, tmp_path: Path) -> None:
    metrics = run_validation(
        _robust_matrix(seed=2),
        db_path=tmp_db,
        sleeve_id="robust_candidate",
        chain_path=tmp_path / "chain.ndjson",
    )
    assert metrics["pbo"] < 0.1
    row = _read_backtest_row(tmp_db)
    assert row[1] < 0.1
