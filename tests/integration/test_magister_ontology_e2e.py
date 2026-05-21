"""E2E del Magister: lenguaje natural → regla ontológica → veto del Risk Manager.

Cubre el ciclo completo de la Tarea 4:
  inyectar "congelar NQ ±30min de FOMC" vía Magister
  → fila en ontology_rules
  → el manager veta un trade de NQ dentro de la ventana
  → el evento queda en chain.ndjson y verify_chain pasa
  → (bonus) borrar el .db y reconstruir por replay recupera la regla.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.advisory import magister  # noqa: E402
from core.models import (  # noqa: E402
    AccountState,
    Direction,
    FirmAccount,
    RiskVerdict,
    SetupType,
    TradeSignal,
)
from core.risk import manager, ontology  # noqa: E402
from core.risk.kill_switch import KillSwitch  # noqa: E402
from infra.audit import chain  # noqa: E402
from scripts import verify_chain  # noqa: E402
from scripts.init_db import init_db  # noqa: E402

FOMC = datetime(2026, 6, 18, 18, 0, tzinfo=UTC)


def _nq_signal(created_at: datetime) -> TradeSignal:
    """Señal NQ limpia (pasa todas las reglas duras) con timestamp controlado."""
    return TradeSignal(
        symbol="NQ",
        direction=Direction.LONG,
        setup_type=SetupType.SETUP_1,
        timeframe="30m",
        entry_price=Decimal("100"),
        stop_loss=Decimal("96"),
        take_profit_1=Decimal("110"),  # RR 2.5
        confidence=0.7,
        justification="setup válido de prueba",
        target_account=FirmAccount.ZENITHSTONE_LIVE,
        suggested_size_contracts=2,
        risk_per_contract_usd=Decimal("100"),
        created_at=created_at,
    )


def _healthy_state() -> AccountState:
    return AccountState(
        account_id="ACC-TEST",
        firm=FirmAccount.ZENITHSTONE_LIVE,
        equity_usd=Decimal("50000"),
        starting_balance_usd=Decimal("50000"),
        daily_pnl_usd=Decimal("0"),
        max_drawdown_threshold_usd=Decimal("2500"),
    )


def test_magister_freeze_nq_around_fomc(tmp_path: Path) -> None:
    db_path = tmp_path / "midas.db"
    chain_path = tmp_path / "infra_audit" / "chain.ndjson"
    vault_dir = tmp_path / "vault"
    kill = KillSwitch(db_path=tmp_path / "kill.db")

    init_db(db_path)  # schema v2, sin replay (chain aún vacío)

    # ── Sanity: sin reglas ontológicas, una señal NQ limpia se aprueba ──
    in_window = _nq_signal(FOMC + timedelta(minutes=10))
    baseline = manager.evaluate(in_window, _healthy_state(), kill_switch=kill)
    assert baseline.verdict == RiskVerdict.APPROVE
    assert baseline.approved_size_contracts == 2

    # ── Magister inyecta la regla desde lenguaje natural ──
    rule = magister.inject_rule(
        description="congelar NQ ±30min de FOMC",
        rule_type="event_window",
        action="veto",
        instrument="NQ",
        window_spec={
            "event": "FOMC",
            "event_ts": FOMC.isoformat(),
            "pre_min": 30,
            "post_min": 30,
        },
        owner="Jordy Marin",
        db_path=db_path,
        chain_path=chain_path,
        vault_dir=vault_dir,
    )
    assert rule.action == "veto"

    # ── 1) Fila en ontology_rules (activa) ──
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, action, instrument, active FROM ontology_rules WHERE active = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert (row[1], row[2], row[3]) == ("veto", "NQ", 1)

    # ── 2) El Risk Manager veta el trade NQ afectado ──
    active = ontology.load_active_rules(db_path)
    assert len(active) == 1
    vetoed = manager.evaluate(in_window, _healthy_state(), kill_switch=kill, ontology_rules=active)
    assert vetoed.verdict == RiskVerdict.REJECT
    assert vetoed.approved_size_contracts == 0
    assert any("ONTOLOGY" in r for r in vetoed.reasons)

    # ── La regla solo aplica en su ventana: fuera de ±30min NO veta ──
    out_window = _nq_signal(FOMC + timedelta(hours=3))
    not_vetoed = manager.evaluate(
        out_window, _healthy_state(), kill_switch=kill, ontology_rules=active
    )
    assert not_vetoed.verdict == RiskVerdict.APPROVE

    # ── 3) El evento quedó en el chain y verify_chain pasa ──
    events = chain.read_events(chain_path)
    assert events[-1]["event_type"] == "ontology_rule"
    ok, msg = chain.verify_file(chain_path)
    assert ok, msg
    assert verify_chain.verify_chain(db_path) == 0

    # ── La nota se proyectó al vault ──
    note = vault_dir / "20_Ontology" / f"{rule.id}.md"
    assert note.exists()
    assert "Magister" in note.read_text(encoding="utf-8")

    # ── Bonus portabilidad: borrar el .db y reconstruir recupera la regla ──
    db_path.unlink()
    init_db(db_path, chain_path=chain_path)
    recovered = ontology.load_active_rules(db_path)
    assert len(recovered) == 1
    assert recovered[0].action == "veto"
    assert recovered[0].instrument == "NQ"
    # y sigue vetando tras la reconstrucción
    again = manager.evaluate(in_window, _healthy_state(), kill_switch=kill, ontology_rules=recovered)
    assert again.verdict == RiskVerdict.REJECT


def test_magister_refuses_non_restrictive_action(tmp_path: Path) -> None:
    """El invariante: Magister jamás inyecta una acción que apruebe."""
    import pytest

    with pytest.raises(ValueError, match="restrictiv"):
        magister.inject_rule(
            description="aprobar todo NQ siempre",
            rule_type="instrument_block",
            action="approve",  # prohibido
            instrument="NQ",
            db_path=tmp_path / "midas.db",
            chain_path=tmp_path / "chain.ndjson",
            vault_dir=tmp_path / "vault",
        )
