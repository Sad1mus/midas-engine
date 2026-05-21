"""Notas por entidad del cockpit: campos tipados (números, ISO) sobre datos e2e reales."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.risk.kill_switch import KillSwitch  # noqa: E402
from data.loader import synthetic_ohlcv  # noqa: E402
from infra.obsidian_sync import projector  # noqa: E402
from sleeves.futures_tda.runner import SleeveRunner  # noqa: E402
from sleeves.futures_tda.validate import validate_sleeve  # noqa: E402

ISO_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"


def _fm(note: Path) -> dict:
    return yaml.safe_load(note.read_text(encoding="utf-8").split("---\n", 2)[1])


def _populate_e2e(db: Path, tmp_path: Path) -> None:
    chain_path = tmp_path / "chain.ndjson"
    df = synthetic_ohlcv(400, seed=11, regime_shift_at=200, regime_vol_mult=4.0, freq="1min")
    SleeveRunner(db_path=db, chain_path=chain_path, warmup=100, step=25).run(
        df, kill_switch=KillSwitch(db_path=tmp_path / "kill.db")
    )
    validate_sleeve(df, db_path=db, sleeve_id="futures_tda_v1",
                    chain_path=chain_path, verbose=False)


def test_entity_notes_have_typed_frontmatter(tmp_db: Path, tmp_path: Path) -> None:
    _populate_e2e(tmp_db, tmp_path)
    vault = tmp_path / "vault"
    projector.project_all(tmp_db, vault)

    # ── nota Equity (con nivel de gate) ──
    eq = _fm(vault / "_generated" / "equity.md")
    assert eq["type"] == "cockpit-equity"
    assert isinstance(eq["equity"], int | float)
    assert isinstance(eq["peak_equity"], int | float)
    assert isinstance(eq["drawdown_pct"], int | float)
    assert isinstance(eq["sizing_mult"], int | float)
    assert isinstance(eq["gate_level"], str)
    assert eq["generated"] is True

    # ── nota por sleeve (con backtest_results) ──
    sleeve_notes = list((vault / "_generated" / "sleeves").glob("*.md"))
    assert sleeve_notes
    s = _fm(sleeve_notes[0])
    assert s["type"] == "cockpit-sleeve"
    assert isinstance(s["dsr"], int | float)
    assert isinstance(s["pbo"], int | float)
    assert isinstance(s["max_dd_pct"], int | float)
    assert isinstance(s["sharpe_mean"], int | float)
    assert isinstance(s["n_paths"], int)
    assert s["verdict"] in ("GO", "NO-GO", "PENDING")

    # ── notas por régimen 𝒳 ──
    regime_notes = list((vault / "_generated" / "regimes").glob("*.md"))
    assert regime_notes
    r = _fm(regime_notes[0])
    assert r["type"] == "cockpit-regime"
    assert isinstance(r["d_topo"], int | float)
    assert isinstance(r["regime"], str)
    assert isinstance(str(r["ts"]), str)


def test_entity_dates_are_iso(tmp_db: Path, tmp_path: Path) -> None:
    import re

    _populate_e2e(tmp_db, tmp_path)
    vault = tmp_path / "vault"
    projector.project_all(tmp_db, vault)
    r = _fm(next((vault / "_generated" / "regimes").glob("*.md")))
    # 'ts' debe ser ISO-8601 (yaml puede entregarlo como str o datetime)
    assert re.match(ISO_RE, str(r["ts"]))
