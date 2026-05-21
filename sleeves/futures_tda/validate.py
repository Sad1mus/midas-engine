"""Validación anti-overfitting del sleeve TDA — conecta con CPCV → DSR → PBO.

Construye la matriz de performance del sleeve (T barras × C configuraciones de
`lookback`) usando su misma lógica direccional (régimen estable → momentum;
régimen de alta vol → mean-reversion), la pasa por el pipeline de Round 2
(`research.backtest.run_validation`) y emite un veredicto **GO / NO-GO**:

    GO  ⟺  DSR > 0  y  PBO < 0.5      (sin tocar el sleeve en vivo)

El régimen para el barrido de research usa un proxy rápido de volatilidad
realizada (consistente con la conmutación momentum/mean-rev del runner), evitando
miles de llamadas a `ripser` por configuración.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest import run_validation


@dataclass(frozen=True)
class SleeveVerdict:
    verdict: str  # 'GO' | 'NO-GO'
    dsr: float
    pbo: float
    sharpe_mean: float
    max_dd_pct: float
    n_paths: int


def _strategy_returns(close: np.ndarray, *, lookback: int) -> np.ndarray:
    """Retornos por barra de la estrategia para un `lookback`.

    Régimen proxy: vol realizada rodante por encima de su mediana ⇒ 'shift' (fade);
    si no, 'stable' (momentum). position[t] aplicado al retorno [t+1].
    """
    s = pd.Series(close, dtype=float)
    ret = s.pct_change().fillna(0.0)
    mom = np.sign(s - s.shift(lookback)).fillna(0.0)
    vol = ret.rolling(lookback).std()
    high_vol = vol > vol.median()
    direction = np.where(high_vol, -mom, mom)  # shift → fade; stable → momentum
    position = pd.Series(direction, index=s.index).shift(1).fillna(0.0)
    strat = position.to_numpy() * ret.to_numpy()
    return strat[lookback + 1 :]  # descartar el calentamiento


def build_returns_matrix(close: np.ndarray, lookbacks: tuple[int, ...]) -> np.ndarray:
    """Matriz (T × C) de retornos del sleeve, una columna por `lookback`."""
    series = [_strategy_returns(close, lookback=lb) for lb in lookbacks]
    t = min(len(s) for s in series)
    return np.column_stack([s[-t:] for s in series])


def validate_sleeve(
    df: pd.DataFrame,
    *,
    db_path: Path,
    sleeve_id: str = "futures_tda_v1",
    lookbacks: tuple[int, ...] = (10, 20, 30, 40, 50),
    chain_path: Path | None = None,
    n_groups: int = 6,
    k_test: int = 2,
    n_partitions: int = 10,
    embargo_pct: float = 0.01,
    purge_pct: float = 0.02,
    verbose: bool = True,
) -> SleeveVerdict:
    """Corre el sleeve por CPCV/DSR/PBO, escribe `backtest_results` y emite GO/NO-GO."""
    close = df["close"].to_numpy(dtype=float)
    matrix = build_returns_matrix(close, lookbacks)

    metrics = run_validation(
        matrix,
        db_path=db_path,
        sleeve_id=sleeve_id,
        n_groups=n_groups,
        k_test=k_test,
        n_partitions=n_partitions,
        embargo_pct=embargo_pct,
        purge_pct=purge_pct,
        config={"lookbacks": list(lookbacks), "strategy": "tda_regime_momentum"},
        chain_path=chain_path,
    )

    go = metrics["dsr"] > 0 and metrics["pbo"] < 0.5
    verdict = "GO" if go else "NO-GO"
    result = SleeveVerdict(
        verdict=verdict,
        dsr=metrics["dsr"],
        pbo=metrics["pbo"],
        sharpe_mean=metrics["sharpe_mean"],
        max_dd_pct=metrics["max_dd_pct"],
        n_paths=metrics["n_paths"],
    )
    if verbose:
        print(
            f"VEREDICTO {sleeve_id}: {verdict}  "
            f"(DSR={result.dsr:.3f}, PBO={result.pbo:.3f}, "
            f"Sharpe={result.sharpe_mean:.3f}, maxDD={result.max_dd_pct:.3f}, "
            f"paths={result.n_paths})"
        )
    return result
