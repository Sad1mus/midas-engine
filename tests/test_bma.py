"""Tests de la infra de BMA (Round 6) — Bayesian Model Averaging sobre sleeves.

Verifica el contrato del agente de señal:
  * el BMA asigna MÁS peso al sleeve con mejor desempeño OOS (mayor Sharpe),
  * los pesos posteriores suman 1,
  * un prior escéptico se respeta y un sleeve sin skill cae al prior,
  * los pesos se persisten en `bma_weights` anclados al hash chain.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.signal.bma import bma_weights, persist_weights  # noqa: E402


def _three_sleeves(t: int = 800, seed: int = 7) -> dict[str, np.ndarray]:
    """3 series sintéticas de CALIDAD distinta (misma vol, distinto drift).

    good   → drift positivo fuerte  (mejor Sharpe)
    medium → drift positivo leve
    bad    → sin drift / negativo   (sin skill)
    """
    rng = np.random.default_rng(seed)
    vol = 0.01
    good = rng.normal(0.0045, vol, size=t)
    medium = rng.normal(0.0012, vol, size=t)
    bad = rng.normal(-0.0005, vol, size=t)
    return {"good": good, "medium": medium, "bad": bad}


# ── pesos: orden por calidad y normalización ────────────────────────────


def test_bma_assigns_more_weight_to_better_sleeve() -> None:
    res = bma_weights(_three_sleeves())
    w = res.weights
    assert w["good"] > w["medium"] > w["bad"], f"orden de pesos roto: {w}"


def test_bma_weights_sum_to_one() -> None:
    res = bma_weights(_three_sleeves())
    total = sum(e.weight for e in res.evidence)
    assert abs(total - 1.0) < 1e-9, f"los pesos deben sumar 1, suman {total}"
    assert all(0.0 <= e.weight <= 1.0 for e in res.evidence)


def test_bad_sleeve_gets_no_skill_evidence() -> None:
    """Un sleeve con drift ≤ 0 no recibe evidencia (log_BF ≈ 0): cae al prior."""
    res = bma_weights(_three_sleeves())
    bad = next(e for e in res.evidence if e.sleeve_id == "bad")
    good = next(e for e in res.evidence if e.sleeve_id == "good")
    assert bad.log_evidence < 1e-9
    assert good.log_evidence > bad.log_evidence
    assert good.sharpe > bad.sharpe


def test_skeptical_prior_shrinks_mean_toward_zero() -> None:
    """La media posterior se encoge hacia 0 (|μ_post| < |μ̂| muestral)."""
    series = _three_sleeves()
    res = bma_weights(series)
    good = next(e for e in res.evidence if e.sleeve_id == "good")
    mu_hat = float(np.mean(series["good"]))
    assert 0.0 < good.post_mean < mu_hat


def test_prior_is_respected() -> None:
    """Con priors fuertemente sesgados a un sleeve flojo, su peso sube vs. uniforme."""
    series = _three_sleeves()
    uniform = bma_weights(series).weights
    biased = bma_weights(series, prior={"good": 0.01, "medium": 0.01, "bad": 0.98}).weights
    assert biased["bad"] > uniform["bad"]
    assert abs(sum(biased.values()) - 1.0) < 1e-9


def test_single_sleeve_gets_full_weight() -> None:
    res = bma_weights({"solo": np.full(200, 0.001)})
    assert abs(res.weights["solo"] - 1.0) < 1e-9


# ── persistencia en bma_weights ─────────────────────────────────────────


def test_persist_weights_writes_table(tmp_db: Path, tmp_path: Path) -> None:
    res = bma_weights(_three_sleeves())
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        audit_id = persist_weights(conn, res, chain_path=tmp_path / "chain.ndjson")
        rows = conn.execute(
            "SELECT sleeve_id, weight, prior, audit_id FROM bma_weights WHERE run_id = ? "
            "ORDER BY weight DESC",
            (res.run_id,),
        ).fetchall()
        # 3 filas, una por sleeve, todas ancladas al mismo evento del chain
        assert len(rows) == 3
        assert all(r[3] == audit_id for r in rows)
        # suman 1 en la tabla
        assert abs(sum(r[1] for r in rows) - 1.0) < 1e-9
        # el mejor (good) quedó arriba al ordenar por weight
        assert rows[0][0] == "good"
        # el evento quedó en audit_log con event_type='bma'
        ev_type = conn.execute(
            "SELECT event_type FROM audit_log WHERE id = ?", (audit_id,)
        ).fetchone()[0]
        assert ev_type == "bma"
    finally:
        conn.close()


def test_persist_weights_unique_per_run(tmp_db: Path, tmp_path: Path) -> None:
    """Reinsertar el mismo run_id viola UNIQUE (idempotencia explícita)."""
    res = bma_weights(_three_sleeves())
    chain_path = tmp_path / "chain.ndjson"
    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        persist_weights(conn, res, chain_path=chain_path)
        try:
            persist_weights(conn, res, chain_path=chain_path)
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "UNIQUE(run_id, sleeve_id) debe impedir duplicar una corrida"
    finally:
        conn.close()
