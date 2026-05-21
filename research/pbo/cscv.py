"""Probability of Backtest Overfitting (PBO) vía CSCV — López de Prado (2015).

Combinatorially Symmetric Cross-Validation: dada una matriz de performance
``M`` (T observaciones × C configuraciones), se parten las filas en ``n_partitions``
bloques y se forman todas las combinaciones de la mitad como in-sample (IS), el
resto como out-of-sample (OOS). Para cada combinación: se elige la config con mejor
Sharpe IS y se mide su **rango relativo OOS**; su logit indica si el ganador IS
queda por debajo de la mediana OOS (señal de overfit).

PBO = fracción de combinaciones donde el ganador IS rinde por debajo de la mediana
OOS (logit ≤ 0). PBO alto ⇒ la selección IS no generaliza ⇒ overfitting.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy.stats import rankdata


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    n_combos: int
    logits: np.ndarray
    median_logit: float


def _sharpe_columns(block: np.ndarray) -> np.ndarray:
    """Sharpe por columna (config) de un bloque de retornos (filas = obs)."""
    mean = block.mean(axis=0)
    std = block.std(axis=0, ddof=1)
    out = np.zeros_like(mean)
    nz = std > 0
    out[nz] = mean[nz] / std[nz]
    return out


def pbo(returns_matrix: np.ndarray, *, n_partitions: int = 10) -> PBOResult:
    """Calcula la PBO por CSCV sobre una matriz (T × C) de retornos por configuración."""
    M = np.asarray(returns_matrix, dtype=float)
    if M.ndim != 2:
        raise ValueError("returns_matrix debe ser 2D (T observaciones × C configs)")
    t_obs, n_configs = M.shape
    if n_configs < 2:
        raise ValueError("se requieren al menos 2 configuraciones")
    if n_partitions % 2 != 0:
        raise ValueError("n_partitions debe ser par")
    if n_partitions > t_obs:
        raise ValueError("n_partitions no puede exceder el número de observaciones")

    parts = np.array_split(np.arange(t_obs), n_partitions)
    half = n_partitions // 2
    logits: list[float] = []

    for is_combo in combinations(range(n_partitions), half):
        is_set = set(is_combo)
        is_rows = np.concatenate([parts[i] for i in is_combo])
        oos_rows = np.concatenate([parts[i] for i in range(n_partitions) if i not in is_set])

        is_perf = _sharpe_columns(M[is_rows])
        oos_perf = _sharpe_columns(M[oos_rows])

        n_star = int(np.argmax(is_perf))  # mejor config en IS
        oos_ranks = rankdata(oos_perf)  # 1..C (mayor = mejor)
        w = oos_ranks[n_star] / (n_configs + 1)  # rango relativo en (0,1)
        w = min(max(w, 1e-9), 1 - 1e-9)
        logits.append(float(np.log(w / (1.0 - w))))

    arr = np.asarray(logits)
    return PBOResult(
        pbo=float(np.mean(arr <= 0.0)),
        n_combos=arr.size,
        logits=arr,
        median_logit=float(np.median(arr)),
    )
