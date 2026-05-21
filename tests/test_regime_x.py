"""Tests de la Variable 𝒳 v1 — detector topológico de régimen (TDA)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.loader import synthetic_ohlcv  # noqa: E402
from sleeves.futures_tda import regime_x as rx  # noqa: E402


def test_takens_embedding_shape() -> None:
    s = np.arange(10.0)
    emb = rx.takens_embedding(s, dim=3, delay=2)
    # n_points = 10 - (3-1)*2 = 6
    assert emb.shape == (6, 3)
    assert emb[0].tolist() == [0.0, 2.0, 4.0]


def test_n_max_is_enforced() -> None:
    pts = np.random.default_rng(0).random((rx.N_MAX + 500, 2))
    reduced = rx._enforce_n_max(pts)
    assert reduced.shape[0] == rx.N_MAX
    # y persistence_diagrams nunca llama a ripser con > N_MAX puntos
    dgms = rx.persistence_diagrams(pts, maxdim=0)
    assert len(dgms) >= 1


def test_detects_regime_shift_and_writes(tmp_db: Path) -> None:
    # serie con cambio de régimen claro (vol x6 a mitad)
    df = synthetic_ohlcv(600, seed=7, regime_shift_at=300, regime_vol_mult=6.0)
    result = rx.detect_regime_from_close(df["close"])
    assert result.label == "shift"
    assert result.d_topo > 0.02
    assert result.bottleneck_h0 >= 0.0

    rid = rx.write_regime_state(tmp_db, result)
    assert rid > 0

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT classifier, regime_label, posterior_json, features_json "
            "FROM regime_states WHERE id = ?",
            (rid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "tda_v1"
    assert row[1] == "shift"
    assert '"d_topo"' in row[3]


def test_stationary_series_no_false_alarm(tmp_db: Path) -> None:
    df = synthetic_ohlcv(600, seed=7, vol=0.01)  # sin shift
    result = rx.detect_regime_from_close(df["close"])
    assert result.label == "stable"
    assert result.d_topo < 0.02

    rid = rx.write_regime_state(tmp_db, result)
    conn = sqlite3.connect(tmp_db)
    try:
        label = conn.execute(
            "SELECT regime_label FROM regime_states WHERE id = ?", (rid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert label == "stable"


def test_shift_divergence_exceeds_stationary() -> None:
    shift = rx.detect_regime_from_close(
        synthetic_ohlcv(600, seed=3, regime_shift_at=300, regime_vol_mult=6.0)["close"]
    )
    stable = rx.detect_regime_from_close(synthetic_ohlcv(600, seed=3, vol=0.01)["close"])
    assert shift.d_topo > stable.d_topo
    # margen amplio: separación topológica robusta
    assert shift.d_topo > 3 * stable.d_topo


def test_persistence_features_keys() -> None:
    df = synthetic_ohlcv(300, seed=1)
    result = rx.detect_regime_from_close(df["close"])
    for k in ("mu", "var", "persistence_entropy", "n_h0", "n_h1"):
        assert k in result.features
