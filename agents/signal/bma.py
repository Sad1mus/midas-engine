"""Bayesian Model Averaging (BMA) sobre las señales de N sleeves.

Combina varios sleeves ponderándolos por su desempeño *out-of-sample*, con un
prior escéptico. La idea (López de Prado / Geweke-Amisano sobre *predictive
likelihood*): no elegir un único sleeve "ganador" (sobreajuste), sino promediar
las señales con un peso posterior que crece con la evidencia de skill genuino.

Modelo bayesiano por sleeve i, sobre su serie de retornos OOS r_i (T_i barras):

    r_{i,t} ~ Normal(μ_i, σ_i²)            (σ_i² ≈ varianza muestral, plug-in)
    μ_i     ~ Normal(0, τ²)                (prior ESCÉPTICO: sin edge a priori)

El prior centrado en 0 encarna el principio MIDAS: no asumimos edge: hay que
ganárselo con datos. Por defecto τ² = σ_i² (prior de *unit information*: el prior
pesa como una observación). La media posterior de μ_i se "encoge" hacia 0:

    prec_post = T_i/σ_i² + 1/τ²
    μ_post    = (T_i·μ̂_i/σ_i²) / prec_post
    z_post    = μ_post / sqrt(1/prec_post)        (señal/ruido posterior de skill)

El *log Bayes-factor* (skill positivo vs. no-skill), aproximación unilateral de
información unitaria, es 0.5·max(z_post, 0)². Es UNILATERAL a propósito: un sleeve
con drift negativo (un mal sleeve) no recibe evidencia — solo el prior. El peso
posterior del modelo es entonces

    w_i ∝ prior_i · exp(log_BF_i)            (softmax estable, Σ w_i = 1)

Invariante sagrado (CLAUDE.md §1): el BMA **no decide tamaño**. Devuelve pesos;
la señal combinada resultante sigue pasando por el Risk Manager (Veto + DD-gates)
como cualquier otra. SOLO usa numpy/scipy (stack lockdown, DEC-001).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from infra.audit import chain


@dataclass(frozen=True)
class SleeveEvidence:
    """Evidencia bayesiana de un sleeve dentro de una corrida de BMA."""

    sleeve_id: str
    weight: float          # peso posterior, en [0, 1]; Σ sobre la corrida = 1
    prior: float           # prior usado, en [0, 1]
    log_evidence: float    # log Bayes-factor (skill vs no-skill)
    post_mean: float       # media posterior del retorno por barra (encogida a 0)
    post_z: float          # z-score posterior de skill
    sharpe: float          # Sharpe muestral OOS de la serie
    n_obs: int             # nº de retornos OOS usados


@dataclass(frozen=True)
class BMAResult:
    """Resultado de una corrida de BMA: pesos posteriores que suman 1."""

    run_id: str
    evidence: tuple[SleeveEvidence, ...]

    @property
    def weights(self) -> dict[str, float]:
        return {e.sleeve_id: e.weight for e in self.evidence}


def _sharpe(returns: np.ndarray, *, ddof: int = 1) -> float:
    """Sharpe muestral por barra (sin anualizar). 0 si var ≈ 0."""
    r = returns[np.isfinite(returns)]
    if r.size < 2:
        return 0.0
    sd = float(np.std(r, ddof=ddof))
    if sd <= 0.0:
        return 0.0
    return float(np.mean(r)) / sd


def bma_weights(
    returns: dict[str, np.ndarray],
    *,
    prior: dict[str, float] | None = None,
    prior_mean_std: float | None = None,
    ddof: int = 1,
    var_floor: float = 1e-12,
) -> BMAResult:
    """Pesos de Bayesian Model Averaging sobre las series OOS de N sleeves.

    Args:
        returns: ``{sleeve_id: serie de retornos OOS}``. N >= 1.
        prior: prior por sleeve (no normalizado; se renormaliza). Default uniforme.
        prior_mean_std: τ, desviación del prior escéptico sobre μ por barra. Si es
            None se usa el prior de *unit information* (τ² = varianza muestral).
        ddof: ddof de la varianza muestral.
        var_floor: piso de varianza para evitar divisiones por ~0.

    Returns:
        BMAResult con un peso posterior por sleeve (Σ = 1) y la evidencia bayesiana.
        El mejor sleeve OOS (mayor Sharpe positivo) recibe MÁS peso; un sleeve sin
        skill (drift ≤ 0) cae al prior.

    Raises:
        ValueError: si ``returns`` está vacío o un prior es negativo.
    """
    if not returns:
        raise ValueError("returns no puede estar vacío (N >= 1)")

    ids = list(returns.keys())
    n = len(ids)

    if prior is None:
        prior_vec = np.full(n, 1.0 / n)
    else:
        raw = np.array([float(prior.get(s, 0.0)) for s in ids], dtype=float)
        if np.any(raw < 0):
            raise ValueError("los priors no pueden ser negativos")
        total = float(raw.sum())
        prior_vec = raw / total if total > 0 else np.full(n, 1.0 / n)

    log_ev = np.zeros(n)
    post_means = np.zeros(n)
    post_zs = np.zeros(n)
    sharpes = np.zeros(n)
    n_obs = np.zeros(n, dtype=int)

    for k, sid in enumerate(ids):
        r = np.asarray(returns[sid], dtype=float)
        r = r[np.isfinite(r)]
        t = r.size
        n_obs[k] = t
        if t < 2:
            # sin datos suficientes para evidencia: solo el prior decide
            continue
        mu_hat = float(np.mean(r))
        var = max(float(np.var(r, ddof=ddof)), var_floor)
        tau2 = (prior_mean_std**2) if prior_mean_std is not None else var  # unit-info por defecto
        prec_post = t / var + 1.0 / tau2
        mu_post = (t * mu_hat / var) / prec_post
        sd_post = float(np.sqrt(1.0 / prec_post))
        z_post = mu_post / sd_post if sd_post > 0 else 0.0
        post_means[k] = mu_post
        post_zs[k] = z_post
        sharpes[k] = _sharpe(r, ddof=ddof)
        # log Bayes-factor unilateral (unit-information): solo skill POSITIVO suma.
        log_ev[k] = 0.5 * max(z_post, 0.0) ** 2

    # peso posterior ∝ prior · exp(log_evidence); softmax numéricamente estable.
    with np.errstate(divide="ignore"):
        log_prior = np.where(prior_vec > 0, np.log(prior_vec), -np.inf)
    log_unnorm = log_prior + log_ev
    log_unnorm -= np.max(log_unnorm[np.isfinite(log_unnorm)])
    w = np.exp(log_unnorm)
    w[~np.isfinite(w)] = 0.0
    total_w = float(w.sum())
    w = w / total_w if total_w > 0 else prior_vec

    evidence = tuple(
        SleeveEvidence(
            sleeve_id=ids[k],
            weight=float(w[k]),
            prior=float(prior_vec[k]),
            log_evidence=float(log_ev[k]),
            post_mean=float(post_means[k]),
            post_z=float(post_zs[k]),
            sharpe=float(sharpes[k]),
            n_obs=int(n_obs[k]),
        )
        for k in range(n)
    )
    return BMAResult(run_id=uuid.uuid4().hex, evidence=evidence)


def persist_weights(
    conn: sqlite3.Connection,
    result: BMAResult,
    *,
    chain_path: Path | None = None,
) -> int:
    """Ancla la corrida al hash chain (event_type='bma') y escribe `bma_weights`.

    Devuelve el ``audit_id`` del evento. Las N filas de la corrida comparten
    ``run_id`` y ese ``audit_id``. La cadena de texto es la fuente de verdad
    (DEC-005); `bma_weights` es una proyección derivada (como backtest_results).
    """
    event = chain.append_event(
        actor="agent:signal:bma",
        event_type="bma",
        event_key=result.run_id,
        payload={
            "run_id": result.run_id,
            "weights": {e.sleeve_id: round(e.weight, 8) for e in result.evidence},
        },
        chain_path=chain_path if chain_path is not None else chain.DEFAULT_CHAIN,
    )
    audit_id = chain.project_event(conn, event)

    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    conn.executemany(
        """
        INSERT INTO bma_weights
            (ts_utc, ts_ms, run_id, sleeve_id, weight, prior, log_evidence,
             post_mean, post_z, sharpe, n_obs, audit_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ts_iso, ts_ms, result.run_id, e.sleeve_id, e.weight, e.prior,
                e.log_evidence, e.post_mean, e.post_z, e.sharpe, e.n_obs, audit_id,
            )
            for e in result.evidence
        ],
    )
    conn.commit()
    return audit_id
