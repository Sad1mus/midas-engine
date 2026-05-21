"""Tests del Risk Manager — el flujo completo de veto end-to-end."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time
from decimal import Decimal

import pytest

from core.models import AccountState, FirmAccount, RiskVerdict, SetupType, TradeSignal
from core.risk import manager, rules
from core.risk.kill_switch import KillSwitch

Signal = Callable[..., TradeSignal]
State = Callable[..., AccountState]


def test_clean_signal_is_approved(make_signal: Signal, make_state: State, kill: KillSwitch) -> None:
    decision = manager.evaluate(make_signal(), make_state(), kill_switch=kill)
    assert decision.verdict == RiskVerdict.APPROVE
    assert decision.approved_size_contracts == 2
    assert decision.requires_human_confirm is False


def test_kill_switch_overrides_everything(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    kill.trigger("revisión manual")
    decision = manager.evaluate(make_signal(), make_state(), kill_switch=kill)
    assert decision.verdict == RiskVerdict.REJECT
    assert any("KILL_SWITCH" in r for r in decision.reasons)
    assert decision.approved_size_contracts == 0


def test_rule_failure_rejects(make_signal: Signal, make_state: State, kill: KillSwitch) -> None:
    decision = manager.evaluate(make_signal(confidence=0.3), make_state(), kill_switch=kill)
    assert decision.verdict == RiskVerdict.REJECT
    assert any("confidence" in r for r in decision.reasons)


def test_size_down_to_zero_rejects(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    # riesgo/contrato gigante: 1.3% de 50k = 650 < 100000 → 0 contratos
    sig = make_signal(risk_per_contract_usd=Decimal("100000"))
    decision = manager.evaluate(sig, make_state(), kill_switch=kill)
    assert decision.verdict == RiskVerdict.REJECT
    assert decision.approved_size_contracts == 0
    assert any("cero" in r.lower() for r in decision.reasons)


def test_size_down_caps_below_suggested(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    # 1.3% de 50k = 650; /200 = 3 contratos; sugeridos 10 → recortado a 3
    sig = make_signal(suggested_size_contracts=10, risk_per_contract_usd=Decimal("200"))
    decision = manager.evaluate(sig, make_state(), kill_switch=kill)
    assert decision.verdict == RiskVerdict.APPROVE
    assert decision.approved_size_contracts == 3


def test_scalp_uses_lower_risk_budget(
    make_signal: Signal, make_state: State, kill: KillSwitch, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SCALP_EMA usa 0.7%: 0.7% de 50k = 350; /200 = 1 contrato (vs 3 con SETUP_1)
    monkeypatch.setattr(rules, "_now_ny", lambda: time(9, 0))  # dentro de ventana scalping
    sig = make_signal(
        setup_type=SetupType.SCALP_EMA,
        suggested_size_contracts=10,
        risk_per_contract_usd=Decimal("200"),
        stop_loss=Decimal("96"),
        take_profit_1=Decimal("110"),
    )
    decision = manager.evaluate(sig, make_state(), kill_switch=kill)
    assert decision.approved_size_contracts == 1


def test_apex_requires_human_confirm(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    # APEX + SETUP_1 (no scalp) pasa reglas pero exige humano (Modo B)
    decision = manager.evaluate(
        make_signal(target_account=FirmAccount.APEX),
        make_state(firm=FirmAccount.APEX),
        kill_switch=kill,
    )
    assert decision.verdict == RiskVerdict.APPROVE_PENDING_HUMAN
    assert decision.requires_human_confirm is True
    assert "Apex" in str(decision.human_confirm_reason)


def test_large_exposure_requires_human_confirm(
    make_signal: Signal, make_state: State, kill: KillSwitch
) -> None:
    # Equity alto: 1.3% de 200k = 2600; /1100 = 2 contratos; exposición 2200 >= 1000 → humano
    sig = make_signal(suggested_size_contracts=3, risk_per_contract_usd=Decimal("1100"))
    state = make_state(equity_usd=Decimal("200000"), starting_balance_usd=Decimal("200000"))
    decision = manager.evaluate(sig, state, kill_switch=kill)
    assert decision.verdict == RiskVerdict.APPROVE_PENDING_HUMAN
    assert decision.requires_human_confirm is True
