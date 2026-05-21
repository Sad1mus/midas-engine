"""Deflated Sharpe Ratio (DSR) — López de Prado & Bailey (2014).

El DSR corrige el Sharpe observado por (a) el número de configuraciones probadas
(selection bias / multiple testing), (b) la longitud de la serie y (c) la no
normalidad de los retornos (asimetría y curtosis). Es la PSR evaluada contra el
**máximo Sharpe esperado bajo la hipótesis nula** de N pruebas independientes.

DSR ∈ [0, 1]: probabilidad de que el Sharpe verdadero supere el umbral de azar.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import kurtosis, norm, skew

EULER_MASCHERONI = 0.5772156649015329


def sharpe_ratio(returns: np.ndarray) -> float:
    """Sharpe por observación (no anualizado). 0 si la varianza es nula."""
    r = np.asarray(returns, dtype=float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def probabilistic_sharpe_ratio(
    observed_sr: float, benchmark_sr: float, n_obs: int, gamma3: float, gamma4: float
) -> float:
    """PSR: P(SR_verdadero > benchmark_sr) dado el SR observado y los momentos."""
    if n_obs < 2:
        return 0.0
    denom = np.sqrt(
        max(1e-12, 1.0 - gamma3 * observed_sr + ((gamma4 - 1.0) / 4.0) * observed_sr**2)
    )
    z = (observed_sr - benchmark_sr) * np.sqrt(n_obs - 1) / denom
    return float(norm.cdf(z))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Máximo Sharpe esperado bajo la nula tras ``n_trials`` pruebas independientes (SR0)."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(np.sqrt(sr_variance) * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2))


def deflated_sharpe_ratio(
    returns: np.ndarray, *, n_trials: int, sr_variance: float | None = None
) -> float:
    """DSR de una serie de retornos. ``sr_variance`` = Var de los Sharpe de las pruebas.

    Si no se da ``sr_variance`` se usa una aproximación conservadora (1/(n_obs-1)),
    la varianza del estimador de Sharpe bajo normalidad y SR≈0.
    """
    r = np.asarray(returns, dtype=float)
    n_obs = r.size
    sr = sharpe_ratio(r)
    g3 = float(skew(r)) if n_obs > 2 else 0.0
    g4 = float(kurtosis(r, fisher=False)) if n_obs > 3 else 3.0
    var = sr_variance if sr_variance is not None else 1.0 / max(1, n_obs - 1)
    sr0 = expected_max_sharpe(var, n_trials)
    return probabilistic_sharpe_ratio(sr, sr0, n_obs, g3, g4)
