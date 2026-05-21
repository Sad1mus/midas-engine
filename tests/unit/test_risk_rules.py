"""Tests de las 11 reglas de riesgo. Cada regla, su camino feliz y su rechazo."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time
from decimal import Decimal

import pytest

from core.models import AccountState, Direction, FirmAccount, SetupType, TradeSignal
from core.risk import rules
from core.risk.rules import evaluate_all

Signal = Callable[..., TradeSignal]
State = Callable[..., AccountState]


def test_valid_signal_passes_all_rules(make_signal: Signal, make_state: State) -> None:
    failures, audit = evaluate_all(make_signal(), make_state())
    assert failures == [], f"No debería fallar nada: {failures}"
    assert all(audit.values())
    assert len(audit) == 11  # las 11 reglas se evaluaron


def test_max_daily_trades_rejects(make_signal: Signal, make_state: State) -> None:
    failures, _ = evaluate_all(make_signal(), make_state(trades_today=3))
    assert any("max_daily_trades" in f for f in failures)


def test_max_consecutive_losses_rejects(make_signal: Signal, make_state: State) -> None:
    failures, _ = evaluate_all(make_signal(), make_state(consecutive_losses=3))
    assert any("max_consecutive_losses" in f for f in failures)


def test_max_daily_loss_rejects(make_signal: Signal, make_state: State) -> None:
    # -6% sobre 50k = -3000, supera el límite de -5%
    failures, _ = evaluate_all(make_signal(), make_state(daily_pnl_usd=Decimal("-3000")))
    assert any("max_daily_loss" in f for f in failures)


def test_trailing_dd_buffer_rejects(make_signal: Signal, make_state: State) -> None:
    # DD actual >= 50% de 2500 = 1250
    failures, _ = evaluate_all(make_signal(), make_state(current_drawdown_usd=Decimal("1300")))
    assert any("trailing_dd_buffer" in f for f in failures)


def test_risk_reward_minimum_rejects(make_signal: Signal, make_state: State) -> None:
    # RR 1.0 (reward == risk) bajo el piso 2.0 de SETUP_1
    sig = make_signal(stop_loss=Decimal("96"), take_profit_1=Decimal("104"))
    failures, _ = evaluate_all(sig, make_state())
    assert any("risk_reward_minimum" in f for f in failures)


def test_confidence_threshold_rejects(make_signal: Signal, make_state: State) -> None:
    failures, _ = evaluate_all(make_signal(confidence=0.4), make_state())
    assert any("confidence_threshold" in f for f in failures)


def test_topstep_requires_mode_c(make_signal: Signal, make_state: State) -> None:
    # Modo por defecto = A; TopStep exige C
    failures, _ = evaluate_all(make_signal(), make_state(firm=FirmAccount.TOPSTEP))
    assert any("topstep_no_vps" in f for f in failures)


def test_apex_consistency_rejects(make_signal: Signal, make_state: State) -> None:
    state = make_state(firm=FirmAccount.APEX, consistency_pct_today=29.0)
    failures, _ = evaluate_all(make_signal(), state)
    assert any("apex_consistency" in f for f in failures)


def test_scalp_prohibited_on_apex(make_signal: Signal, make_state: State) -> None:
    sig = make_signal(
        setup_type=SetupType.SCALP_EMA,
        stop_loss=Decimal("98"),
        take_profit_1=Decimal("104"),  # RR 2.0 > piso 1.5
    )
    failures, _ = evaluate_all(sig, make_state(firm=FirmAccount.APEX))
    assert any("scalp_only_in_allowed_mode" in f for f in failures)


def test_scalp_window_inside(
    make_signal: Signal, make_state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "_now_ny", lambda: time(9, 0))  # dentro de la ventana AM
    sig = make_signal(
        setup_type=SetupType.SCALP_EMA, stop_loss=Decimal("98"), take_profit_1=Decimal("104")
    )
    failures, _ = evaluate_all(sig, make_state())
    assert not any("scalp_window" in f for f in failures)


def test_scalp_window_outside(
    make_signal: Signal, make_state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rules, "_now_ny", lambda: time(12, 0))  # fuera de ambas ventanas
    sig = make_signal(
        setup_type=SetupType.SCALP_EMA, stop_loss=Decimal("98"), take_profit_1=Decimal("104")
    )
    failures, _ = evaluate_all(sig, make_state())
    assert any("scalp_window" in f for f in failures)


def test_short_geometry_validation() -> None:
    """La geometría SHORT inválida debe fallar en validación del modelo."""
    with pytest.raises(ValueError, match="SHORT"):
        TradeSignal(
            symbol="NQ",
            direction=Direction.SHORT,
            setup_type=SetupType.SETUP_1,
            timeframe="30m",
            entry_price=Decimal("100"),
            stop_loss=Decimal("96"),  # SHORT: SL debería ser > entry → inválido
            take_profit_1=Decimal("90"),
            confidence=0.7,
            justification="geometría inválida",
            target_account=FirmAccount.ZENITHSTONE_LIVE,
            suggested_size_contracts=1,
            risk_per_contract_usd=Decimal("100"),
        )
