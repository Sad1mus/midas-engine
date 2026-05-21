"""Combinatorial Purged Cross-Validation (CPCV) — López de Prado.

Divide la muestra en ``n_groups`` grupos contiguos y elige todas las combinaciones
de ``k_test`` grupos como test (→ C(n_groups, k_test) *paths*). El entrenamiento es
el resto, con **purge** (elimina muestras de train cuyo horizonte solapa el test) y
**embargo** (elimina un margen de train inmediatamente posterior a cada bloque de
test). Esto descontamina la validación de fuga de información temporal.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations
from math import comb

import numpy as np


@dataclass(frozen=True)
class CPCVSplit:
    train_idx: np.ndarray
    test_idx: np.ndarray


def n_paths(n_groups: int, k_test: int) -> int:
    """Número de paths combinatorios = C(n_groups, k_test)."""
    return comb(n_groups, k_test)


def _contiguous_blocks(test_idx: np.ndarray) -> list[tuple[int, int]]:
    """Devuelve los tramos contiguos [(start, end_inclusive), ...] de un índice ordenado."""
    vals = test_idx.tolist()  # ints de Python (evita casts redundantes sobre np.int64)
    if not vals:
        return []
    blocks: list[tuple[int, int]] = []
    start = prev = vals[0]
    for v in vals[1:]:
        if v == prev + 1:
            prev = v
        else:
            blocks.append((start, prev))
            start = prev = v
    blocks.append((start, prev))
    return blocks


def cpcv_split(
    n_samples: int,
    *,
    n_groups: int = 6,
    k_test: int = 2,
    embargo_pct: float = 0.01,
    purge_pct: float = 0.0,
) -> Iterator[CPCVSplit]:
    """Genera los splits CPCV (train purgado/embargado, test) para cada combinación."""
    if not (1 <= k_test < n_groups):
        raise ValueError("se requiere 1 <= k_test < n_groups")
    if n_samples < n_groups:
        raise ValueError("n_samples debe ser >= n_groups")

    groups = np.array_split(np.arange(n_samples), n_groups)
    embargo = round(embargo_pct * n_samples)
    purge = round(purge_pct * n_samples)

    for combo in combinations(range(n_groups), k_test):
        test_idx = np.sort(np.concatenate([groups[i] for i in combo]))
        keep = np.ones(n_samples, dtype=bool)
        keep[test_idx] = False
        # purge + embargo alrededor de cada bloque de test contiguo
        for start, end in _contiguous_blocks(test_idx):
            lo = max(0, start - purge)
            hi = min(n_samples, end + 1 + embargo)
            keep[lo:hi] = False
        keep[test_idx] = False  # asegurar test fuera de train
        train_idx = np.nonzero(keep)[0]
        yield CPCVSplit(train_idx=train_idx, test_idx=test_idx)
