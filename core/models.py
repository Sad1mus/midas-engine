"""Esquemas pydantic intercambiados entre agentes.

Estos son los **contratos** del sistema. Si cambias un campo, todo agente que lo
consume debe actualizarse. Trátalos como una API pública.

Flujo:
    Technical Analyst  --[TradeSignal]-->   Risk Manager
    Risk Manager       --[RiskDecision]-->  Bot Executor (o humano si Modo B)
    Bot Executor       --[OrderRequest]-->  Broker API
    Broker             --[TradeOutcome]-->  Memory/Logger
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow() -> datetime:
    """Timezone-aware UTC. Reemplaza al deprecado datetime.utcnow()."""
    return datetime.now(UTC)


# =============================================================================
# Enums
# =============================================================================
class SetupType(StrEnum):
    """Identificadores de estrategia — deben coincidir con strategies/*."""

    SETUP_1 = "SETUP_1"  # Haxel — EMA 230 weekly, conservador
    SETUP_2 = "SETUP_2"  # Haxel — pullback estructural
    SETUP_3 = "SETUP_3"  # Haxel — reacción S/R
    SCALP_EMA = "SCALP_EMA"  # 1m: EMA 1380 + EMA 6900
    NEWS_DRIVEN = "NEWS_DRIVEN"  # NFP / CPI / FOMC (Fase 2)


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class FirmAccount(StrEnum):
    APEX = "APEX"
    TOPSTEP = "TOPSTEP"
    LUCID = "LUCID"
    ZENITHSTONE_LIVE = "ZENITHSTONE_LIVE"


class RiskVerdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    APPROVE_PENDING_HUMAN = "APPROVE_PENDING_HUMAN"  # Modo B / monto > umbral


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


# =============================================================================
# Trade Signal — salida del Technical Analyst
# =============================================================================
class TradeSignal(BaseModel):
    """Una idea de trade propuesta por el Technical Analyst.

    El Risk Manager la validará contra compliance + capital + correlación antes de
    que cualquier orden se envíe.
    """

    model_config = ConfigDict(use_enum_values=True, frozen=False)

    signal_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utcnow)

    # Qué
    symbol: str = Field(..., examples=["NQ", "GC", "ES"])
    direction: Direction
    setup_type: SetupType
    timeframe: str = Field(..., examples=["1m", "5m", "30m", "1h", "4h", "D", "W"])

    # Dónde
    entry_price: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal | None = None
    take_profit_3: Decimal | None = None

    # Confianza + justificación (producidas por LLM)
    confidence: float = Field(..., ge=0.0, le=1.0)
    justification: str = Field(..., min_length=10)
    indicators_snapshot: dict[str, float] = Field(default_factory=dict)

    # Contexto
    target_account: FirmAccount
    suggested_size_contracts: int = Field(..., ge=1)
    risk_per_contract_usd: Decimal

    # Routing
    requires_news_filter: bool = True
    valid_until: datetime | None = None

    @property
    def risk_reward_ratio(self) -> float:
        """RR calculado desde entry/SL/TP1."""
        risk = abs(float(self.entry_price - self.stop_loss))
        reward = abs(float(self.take_profit_1 - self.entry_price))
        return reward / risk if risk > 0 else 0.0

    @model_validator(mode="after")
    def _validate_sl_tp_geometry(self) -> TradeSignal:
        if self.direction == Direction.LONG:
            if self.stop_loss >= self.entry_price:
                raise ValueError("LONG: stop_loss debe ser < entry_price")
            if self.take_profit_1 <= self.entry_price:
                raise ValueError("LONG: take_profit_1 debe ser > entry_price")
        else:  # SHORT
            if self.stop_loss <= self.entry_price:
                raise ValueError("SHORT: stop_loss debe ser > entry_price")
            if self.take_profit_1 >= self.entry_price:
                raise ValueError("SHORT: take_profit_1 debe ser < entry_price")
        return self


# =============================================================================
# Risk Decision — salida del Risk Manager (poder de VETO)
# =============================================================================
class RiskDecision(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    decision_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID
    decided_at: datetime = Field(default_factory=_utcnow)

    verdict: RiskVerdict
    reasons: list[str] = Field(..., min_length=1)

    # Ajustes (el Risk Manager puede reducir, nunca aumentar)
    approved_size_contracts: int = Field(..., ge=0)
    adjusted_stop_loss: Decimal | None = None

    # Checks de compliance realizados (rastro de auditoría)
    checks_performed: dict[str, bool] = Field(default_factory=dict)
    consecutive_losses: int = 0

    # ¿Requiere aprobación humana?
    requires_human_confirm: bool = False
    human_confirm_reason: str | None = None


# =============================================================================
# Order Request — entrada del Bot Executor
# =============================================================================
class OrderRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    order_id: UUID = Field(default_factory=uuid4)
    signal_id: UUID
    decision_id: UUID
    submitted_at: datetime = Field(default_factory=_utcnow)

    account_id: str
    symbol: str
    direction: Direction
    order_type: OrderType
    size_contracts: int

    entry_price: Decimal | None = None  # None para MARKET
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal | None = None

    bracket: bool = True
    time_in_force: Literal["DAY", "GTC", "IOC"] = "DAY"


# =============================================================================
# Trade Outcome — entrada del Memory/Logger
# =============================================================================
class TradeOutcome(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    outcome_id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    signal_id: UUID
    closed_at: datetime = Field(default_factory=_utcnow)

    status: OrderStatus
    fill_price: Decimal | None = None
    exit_price: Decimal | None = None
    realized_pnl_usd: Decimal | None = None
    realized_r_multiple: float | None = None
    slippage_ticks: float | None = None
    duration_seconds: int | None = None

    notes: str | None = None


# =============================================================================
# Account State — snapshot actual para el contexto del Risk Manager
# =============================================================================
class AccountState(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    account_id: str
    firm: FirmAccount
    snapshot_at: datetime = Field(default_factory=_utcnow)

    equity_usd: Decimal
    starting_balance_usd: Decimal
    daily_pnl_usd: Decimal
    realized_daily_pnl_usd: Decimal = Decimal(0)
    unrealized_pnl_usd: Decimal = Decimal(0)

    open_positions: int = 0
    trades_today: int = 0
    consecutive_losses: int = 0

    # Trailing DD
    max_drawdown_threshold_usd: Decimal
    current_drawdown_usd: Decimal = Decimal(0)

    # Específico de compliance (Apex)
    consistency_pct_today: float | None = None  # regla Apex 30%/día

    @property
    def daily_pnl_pct(self) -> float:
        if self.starting_balance_usd == 0:
            return 0.0
        return float(self.daily_pnl_usd / self.starting_balance_usd) * 100
