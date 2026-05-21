"""Fixtures compartidas para los tests del núcleo de riesgo."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from core.models import AccountState, Direction, FirmAccount, SetupType, TradeSignal
from core.risk.kill_switch import KillSwitch


@pytest.fixture
def make_signal() -> Callable[..., TradeSignal]:
    """Factory de TradeSignal válida por defecto (SETUP_1 LONG, RR 2.5, conf 0.7)."""

    def _make(**overrides: object) -> TradeSignal:
        defaults: dict[str, object] = {
            "symbol": "NQ",
            "direction": Direction.LONG,
            "setup_type": SetupType.SETUP_1,
            "timeframe": "30m",
            "entry_price": Decimal("100"),
            "stop_loss": Decimal("96"),  # riesgo 4
            "take_profit_1": Decimal("110"),  # reward 10 → RR 2.5
            "confidence": 0.7,
            "justification": "setup válido de prueba",
            "target_account": FirmAccount.ZENITHSTONE_LIVE,
            "suggested_size_contracts": 2,
            "risk_per_contract_usd": Decimal("100"),
        }
        defaults.update(overrides)
        return TradeSignal(**defaults)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def make_state() -> Callable[..., AccountState]:
    """Factory de AccountState sana por defecto (ZENITHSTONE_LIVE, sin DD)."""

    def _make(**overrides: object) -> AccountState:
        defaults: dict[str, object] = {
            "account_id": "ACC-TEST",
            "firm": FirmAccount.ZENITHSTONE_LIVE,
            "equity_usd": Decimal("50000"),
            "starting_balance_usd": Decimal("50000"),
            "daily_pnl_usd": Decimal("0"),
            "max_drawdown_threshold_usd": Decimal("2500"),
        }
        defaults.update(overrides)
        return AccountState(**defaults)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def kill(tmp_path: Path) -> KillSwitch:
    """KillSwitch con base SQLite temporal y aislada por test."""
    return KillSwitch(db_path=tmp_path / "kill.db")
