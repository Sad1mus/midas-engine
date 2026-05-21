"""Variable 𝒳 v1 — detector topológico de régimen (TDA).

El operador 𝒳 mapea una serie de retornos a una etiqueta de régimen vía topología:

    retornos → embedding de Takens (nube de puntos con retardo)
            → complejo de Vietoris-Rips (ripser, N_max = 10³)
            → diagramas de persistencia (H0, H1)
            → features topológicas (μ, σ², entropía de persistencia, |H0|, |H1|)
            → divergencia D_topo vs una referencia (bottleneck B0 + B1)
            → etiqueta de régimen → tabla `regime_states` (classifier='tda_v1')

v1 trabaja sobre OHLCV (retornos del cierre). El order-flow L2 real es un upgrade
futuro que requerirá un DEC de datos. N_max = 10³ está *enforced* en código
(DEC-001): si el embedding excede 1000 puntos se submuestrea de forma determinista
antes de llamar a ripser.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from persim import bottleneck
from ripser import ripser

# Enforced por DEC-001: cota dura de puntos para Vietoris-Rips.
N_MAX = 1000

CLASSIFIER = "tda_v1"


@dataclass(frozen=True)
class RegimeXResult:
    """Salida del operador 𝒳 para una ventana frente a su referencia."""

    label: str  # 'shift' | 'stable'
    d_topo: float
    bottleneck_h0: float
    bottleneck_h1: float
    features: dict[str, float] = field(default_factory=dict)
    reference_features: dict[str, float] = field(default_factory=dict)

    def posterior(self) -> dict[str, float]:
        """Pseudo-posterior suave a partir de D_topo (solo para `posterior_json`)."""
        p_shift = float(1.0 - np.exp(-self.d_topo)) if self.d_topo > 0 else 0.0
        return {"shift": round(p_shift, 6), "stable": round(1.0 - p_shift, 6)}


# ─────────────────────────────────────────────────────────────────────
# Pipeline topológico
# ─────────────────────────────────────────────────────────────────────


def returns_from_close(close: pd.Series | np.ndarray) -> np.ndarray:
    """Log-retornos de una serie de cierres."""
    arr = np.asarray(close, dtype=float)
    if arr.ndim != 1 or arr.size < 2:
        raise ValueError("se requiere una serie 1D de al menos 2 cierres")
    return np.diff(np.log(arr))


def takens_embedding(series: np.ndarray, *, dim: int = 3, delay: int = 1) -> np.ndarray:
    """Embedding de Takens: vectores de retardo en R^dim a partir de una serie 1D."""
    x = np.asarray(series, dtype=float).ravel()
    if dim < 1 or delay < 1:
        raise ValueError("dim>=1 y delay>=1")
    n_points = x.size - (dim - 1) * delay
    if n_points < 2:
        raise ValueError("serie demasiado corta para el embedding pedido")
    return np.column_stack([x[i * delay : i * delay + n_points] for i in range(dim)])


def _enforce_n_max(points: np.ndarray, *, seed: int = 0) -> np.ndarray:
    """Submuestreo determinista a N_MAX puntos (DEC-001)."""
    if points.shape[0] <= N_MAX:
        return points
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(points.shape[0], size=N_MAX, replace=False))
    return points[idx]


def persistence_diagrams(points: np.ndarray, *, maxdim: int = 1) -> list[np.ndarray]:
    """Diagramas de persistencia (H0..Hmaxdim) vía Vietoris-Rips (ripser). Enforce N_MAX."""
    pts = _enforce_n_max(points)
    if pts.shape[0] > N_MAX:  # invariante defensivo
        raise RuntimeError(f"N_max excedido: {pts.shape[0]} > {N_MAX}")
    return ripser(pts, maxdim=maxdim)["dgms"]


def _finite_lifetimes(dgm: np.ndarray) -> np.ndarray:
    if dgm is None or len(dgm) == 0:
        return np.array([])
    d = np.asarray(dgm, dtype=float)
    finite = d[np.isfinite(d[:, 1])]
    return finite[:, 1] - finite[:, 0]


def persistence_entropy(lifetimes: np.ndarray) -> float:
    """Entropía de persistencia normalizada de un conjunto de lifetimes."""
    lt = lifetimes[lifetimes > 0]
    if lt.size == 0:
        return 0.0
    p = lt / lt.sum()
    return float(-np.sum(p * np.log(p)))


def persistence_features(dgms: list[np.ndarray]) -> dict[str, float]:
    """Features topológicas: μ, σ², entropía (sobre H1) y conteos de H0/H1."""
    h0_lt = _finite_lifetimes(dgms[0]) if len(dgms) > 0 else np.array([])
    h1_lt = _finite_lifetimes(dgms[1]) if len(dgms) > 1 else np.array([])
    focus = h1_lt if h1_lt.size > 0 else h0_lt
    return {
        "mu": float(focus.mean()) if focus.size else 0.0,
        "var": float(focus.var()) if focus.size else 0.0,
        "persistence_entropy": persistence_entropy(focus),
        "n_h0": float(h0_lt.size),
        "n_h1": float(h1_lt.size),
        "max_lifetime_h1": float(h1_lt.max()) if h1_lt.size else 0.0,
    }


def _finite_diagram(dgm: np.ndarray) -> np.ndarray:
    """Diagrama sin barras infinitas (bottleneck no admite muerte=inf)."""
    if dgm is None or len(dgm) == 0:
        return np.empty((0, 2))
    d = np.asarray(dgm, dtype=float)
    return d[np.isfinite(d[:, 1])]


def topo_divergence(
    dgms_a: list[np.ndarray], dgms_b: list[np.ndarray]
) -> tuple[float, float, float]:
    """Divergencia topológica entre dos diagramas: (D_topo, B0, B1) por bottleneck."""
    b0 = float(bottleneck(_finite_diagram(dgms_a[0]), _finite_diagram(dgms_b[0])))
    b1 = 0.0
    if len(dgms_a) > 1 and len(dgms_b) > 1:
        b1 = float(bottleneck(_finite_diagram(dgms_a[1]), _finite_diagram(dgms_b[1])))
    return b0 + b1, b0, b1


# ─────────────────────────────────────────────────────────────────────
# Operador 𝒳
# ─────────────────────────────────────────────────────────────────────


def compute_x(
    current_returns: np.ndarray,
    reference_returns: np.ndarray,
    *,
    dim: int = 3,
    delay: int = 1,
    threshold: float = 0.02,
) -> RegimeXResult:
    """Aplica 𝒳: compara la topología de la ventana actual vs la de referencia.

    Etiqueta 'shift' si D_topo supera ``threshold``, si no 'stable'.
    """
    dgms_ref = persistence_diagrams(takens_embedding(reference_returns, dim=dim, delay=delay))
    dgms_cur = persistence_diagrams(takens_embedding(current_returns, dim=dim, delay=delay))

    d_topo, b0, b1 = topo_divergence(dgms_ref, dgms_cur)
    label = "shift" if d_topo > threshold else "stable"
    return RegimeXResult(
        label=label,
        d_topo=d_topo,
        bottleneck_h0=b0,
        bottleneck_h1=b1,
        features=persistence_features(dgms_cur),
        reference_features=persistence_features(dgms_ref),
    )


def detect_regime_from_close(
    close: pd.Series | np.ndarray,
    *,
    split: float = 0.5,
    dim: int = 3,
    delay: int = 1,
    threshold: float = 0.02,
) -> RegimeXResult:
    """Operador 𝒳 sobre una serie de cierres: referencia = primer tramo, actual = último."""
    r = returns_from_close(close)
    cut = int(len(r) * split)
    reference, current = r[:cut], r[cut:]
    if reference.size < dim + 1 or current.size < dim + 1:
        raise ValueError("serie demasiado corta para dividir en referencia/actual")
    return compute_x(current, reference, dim=dim, delay=delay, threshold=threshold)


# ─────────────────────────────────────────────────────────────────────
# Persistencia a regime_states
# ─────────────────────────────────────────────────────────────────────


def write_regime_state(
    db_path: Path,
    result: RegimeXResult,
    *,
    ts: dt.datetime | None = None,
    extra_features: dict[str, Any] | None = None,
) -> int:
    """Escribe la etiqueta de régimen a `regime_states` (classifier='tda_v1'). Devuelve el id."""
    now = ts or dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    features = {
        **result.features,
        "d_topo": result.d_topo,
        "bottleneck_h0": result.bottleneck_h0,
        "bottleneck_h1": result.bottleneck_h1,
        **(extra_features or {}),
    }

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO regime_states
                (ts_utc, ts_ms, classifier, regime_label, posterior_json, features_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts_iso,
                ts_ms,
                CLASSIFIER,
                result.label,
                json.dumps(result.posterior(), sort_keys=True),
                json.dumps(features, sort_keys=True),
            ),
        )
        conn.commit()
        rid = cur.lastrowid
    finally:
        conn.close()
    if rid is None:
        raise RuntimeError("no se pudo insertar en regime_states")
    return rid
