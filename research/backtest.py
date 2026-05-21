"""Orquestador de validación anti-overfitting: CPCV → DSR → PBO → backtest_results.

Toma una matriz de performance (T observaciones × C configuraciones de una
estrategia candidata), y produce el veredicto científico estilo López de Prado:

  * **PBO** (CSCV) sobre todas las configs — ¿la selección IS generaliza?
  * **CPCV** paths sobre la config elegida — distribución de Sharpe (mean/std).
  * **DSR** de la config elegida, deflactado por C pruebas y la no-normalidad.
  * **max drawdown** de la config elegida.

Escribe una fila a `backtest_results`, anclando el evento al hash chain
(`event_type='backtest'`, fuente de verdad — DEC-005).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.audit import chain  # noqa: E402
from research.cpcv import cpcv_split, n_paths  # noqa: E402
from research.deflated_sharpe import deflated_sharpe_ratio, sharpe_ratio  # noqa: E402
from research.pbo import pbo  # noqa: E402


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=REPO_ROOT,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return "untracked"


def _max_drawdown(returns: np.ndarray) -> float:
    """Máximo drawdown (fracción 0..1) de una serie de retornos por periodo."""
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / peak
    return float(np.max(dd)) if dd.size else 0.0


def run_validation(
    returns_matrix: np.ndarray,
    *,
    db_path: Path,
    sleeve_id: str,
    candidate_col: int | None = None,
    n_groups: int = 6,
    k_test: int = 2,
    n_partitions: int = 10,
    embargo_pct: float = 0.01,
    purge_pct: float = 0.02,
    config: dict[str, Any] | None = None,
    chain_path: Path | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Corre el pipeline completo y escribe a `backtest_results`. Devuelve las métricas."""
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        raise ValueError("returns_matrix debe ser (T × C) con C>=2")
    t_obs, n_configs = M.shape

    # PBO sobre todas las configuraciones (CSCV)
    pbo_res = pbo(M, n_partitions=n_partitions)

    # Config candidata = mejor Sharpe global (o la indicada)
    col_sharpes = np.array([sharpe_ratio(M[:, c]) for c in range(n_configs)])
    candidate = candidate_col if candidate_col is not None else int(np.argmax(col_sharpes))
    cand_returns = M[:, candidate]

    # CPCV: Sharpe por path (sobre el test de cada combinación)
    path_sharpes = [
        sharpe_ratio(cand_returns[split.test_idx])
        for split in cpcv_split(
            t_obs,
            n_groups=n_groups,
            k_test=k_test,
            embargo_pct=embargo_pct,
            purge_pct=purge_pct,
        )
    ]
    paths = n_paths(n_groups, k_test)

    sr_variance = float(np.var(col_sharpes, ddof=1)) if n_configs > 1 else 0.0
    dsr = deflated_sharpe_ratio(cand_returns, n_trials=n_configs, sr_variance=sr_variance)
    max_dd = _max_drawdown(cand_returns)
    sharpe_mean = float(np.mean(path_sharpes))
    sharpe_std = float(np.std(path_sharpes, ddof=1)) if len(path_sharpes) > 1 else 0.0
    calmar = float(sharpe_mean / max_dd) if max_dd > 0 else None

    metrics: dict[str, Any] = {
        "sleeve_id": sleeve_id,
        "n_paths": paths,
        "sharpe_mean": sharpe_mean,
        "sharpe_std": sharpe_std,
        "dsr": dsr,
        "pbo": pbo_res.pbo,
        "max_dd_pct": max_dd,
        "calmar": calmar,
        "embargo_pct": embargo_pct,
        "purge_pct": purge_pct,
        "candidate_col": candidate,
    }

    # Ancla al hash chain (fuente de verdad) y proyecta a audit_log.
    cfg = {**(config or {}), "candidate_col": candidate, "n_configs": n_configs}
    commit = git_commit or _git_commit()
    event = chain.append_event(
        actor="agent:research",
        event_type="backtest",
        event_key=sleeve_id,
        payload={"sleeve_id": sleeve_id, "pbo": pbo_res.pbo, "dsr": dsr, "git_commit": commit},
        chain_path=chain_path if chain_path is not None else chain.DEFAULT_CHAIN,
    )

    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        audit_id = chain.project_event(conn, event)
        conn.execute(
            """
            INSERT INTO backtest_results
                (sleeve_id, git_commit, ts_utc, ts_ms, n_paths, sharpe_mean, sharpe_std,
                 dsr, pbo, max_dd_pct, calmar, embargo_pct, purge_pct, config_json, audit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sleeve_id,
                commit,
                ts_iso,
                ts_ms,
                paths,
                sharpe_mean,
                sharpe_std,
                dsr,
                pbo_res.pbo,
                max_dd,
                calmar,
                embargo_pct,
                purge_pct,
                json.dumps(cfg, sort_keys=True),
                audit_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    metrics["audit_id"] = audit_id
    return metrics
