"""Reglas de riesgo — cada regla es una función pura que devuelve (passed, reason).

Añadir una regla nueva = añadir una función y registrarla en `ALL_RULES`.
Esto mantiene el loop de evaluación trivial y hace cada regla testeable por separado.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.config import settings
from core.models import AccountState, FirmAccount, SetupType, TradeSignal

NY_TZ = ZoneInfo("America/New_York")

RuleResult = tuple[bool, str]
Rule = Callable[[TradeSignal, AccountState], RuleResult]


def _now_ny() -> time:
    """Hora actual en NY. Aislada para poder inyectarla/monkeypatchearla en tests."""
    return datetime.now(NY_TZ).time()


# =============================================================================
# Reglas duras (aplican a TODOS los trades, cuentas y setups)
# =============================================================================
def rule_max_daily_trades(signal: TradeSignal, state: AccountState) -> RuleResult:
    if state.trades_today >= settings.max_daily_trades:
        return False, f"Máx trades diarios ({state.trades_today}/{settings.max_daily_trades})"
    return True, "ok"


def rule_max_consecutive_losses(signal: TradeSignal, state: AccountState) -> RuleResult:
    if state.consecutive_losses >= settings.max_consecutive_losses:
        return (
            False,
            f"Kill switch: {state.consecutive_losses} pérdidas consecutivas — pausa para revisión",
        )
    return True, "ok"


def rule_max_daily_loss(signal: TradeSignal, state: AccountState) -> RuleResult:
    if state.daily_pnl_pct <= -settings.max_daily_loss_pct:
        return (
            False,
            f"Pérdida diaria {state.daily_pnl_pct:.2f}% supera el límite "
            f"-{settings.max_daily_loss_pct}%",
        )
    return True, "ok"


def rule_trailing_dd_buffer(signal: TradeSignal, state: AccountState) -> RuleResult:
    """No operar si estamos dentro del 50% del umbral de trailing DD."""
    buffer_required = state.max_drawdown_threshold_usd * Decimal("0.5")
    if state.current_drawdown_usd >= buffer_required:
        return (
            False,
            f"Buffer trailing DD: ${state.current_drawdown_usd} > 50% de "
            f"${state.max_drawdown_threshold_usd}",
        )
    return True, "ok"


def rule_risk_reward_minimum(signal: TradeSignal, state: AccountState) -> RuleResult:
    """El RR debe superar el piso del tipo de setup."""
    rr_floor = {
        SetupType.SCALP_EMA: 1.5,  # Mayor frecuencia, vara algo más baja
        SetupType.SETUP_1: 2.0,
        SetupType.SETUP_2: 2.0,
        SetupType.SETUP_3: 2.0,
        SetupType.NEWS_DRIVEN: 2.5,
    }
    floor = rr_floor.get(SetupType(signal.setup_type), 2.0)
    if signal.risk_reward_ratio < floor:
        return (
            False,
            f"RR {signal.risk_reward_ratio:.2f} bajo el piso {floor} para {signal.setup_type}",
        )
    return True, "ok"


def rule_confidence_threshold(signal: TradeSignal, state: AccountState) -> RuleResult:
    if signal.confidence < 0.55:
        return False, f"Confianza {signal.confidence:.2f} bajo el umbral 0.55"
    return True, "ok"


# =============================================================================
# Reglas específicas por firma
# =============================================================================
def rule_apex_no_ai_in_pa_live(signal: TradeSignal, state: AccountState) -> RuleResult:
    """Apex: IA/bots prohibidos en PA/Live. Solo Modo B (confirmado por humano)."""
    if state.firm == FirmAccount.APEX and not settings.apex_ai_allowed:
        return True, "Apex IA bloqueada → requerirá confirmación humana (Modo B)"
    return True, "ok"


def rule_topstep_no_vps(signal: TradeSignal, state: AccountState) -> RuleResult:
    """TopStep prohíbe VPS/bots en nube. Solo ejecución local."""
    if state.firm == FirmAccount.TOPSTEP and settings.system_mode.value != "C":
        return False, "TopStep requiere Modo C (PC local) — el modo actual no es C"
    return True, "ok"


def rule_apex_consistency_30_pct(signal: TradeSignal, state: AccountState) -> RuleResult:
    """Regla de consistencia Apex: ningún día > 30% del profit total."""
    if state.firm != FirmAccount.APEX:
        return True, "ok"
    if state.consistency_pct_today is not None and state.consistency_pct_today > 28.0:
        return (
            False,
            f"Consistencia Apex: hoy es {state.consistency_pct_today:.1f}% del profit total "
            "(límite 30%, buffer 2%)",
        )
    return True, "ok"


# =============================================================================
# Reglas específicas de scalping
# =============================================================================
def rule_scalp_only_in_allowed_mode(signal: TradeSignal, state: AccountState) -> RuleResult:
    """Scalping (SCALP_EMA) prohibido en Modo B (Apex) por constitución."""
    if signal.setup_type != SetupType.SCALP_EMA:
        return True, "ok"
    if state.firm == FirmAccount.APEX:
        return False, "Scalping prohibido en Apex (latencia Modo B incompatible)"
    if state.firm == FirmAccount.TOPSTEP and not settings.topstep_ai_allowed:
        return False, "Scalping en TopStep requiere TOPSTEP_AI_ALLOWED + Modo C"
    return True, "ok"


def rule_scalp_window(signal: TradeSignal, state: AccountState) -> RuleResult:
    """Scalping solo en NY 08:30-11:30 o 14:00-15:45."""
    if signal.setup_type != SetupType.SCALP_EMA:
        return True, "ok"
    now_ny = _now_ny()
    am = time(8, 30) <= now_ny <= time(11, 30)
    pm = time(14, 0) <= now_ny <= time(15, 45)
    if not (am or pm):
        return (
            False,
            f"Fuera de ventana scalping (NY {now_ny}). Permitido: 08:30-11:30, 14:00-15:45",
        )
    return True, "ok"


# =============================================================================
# Registro — evaluado en orden. Primer fallo = REJECT.
# =============================================================================
ALL_RULES: list[Rule] = [
    # Límites duros primero (fail fast)
    rule_max_daily_trades,
    rule_max_consecutive_losses,
    rule_max_daily_loss,
    rule_trailing_dd_buffer,
    # Calidad del setup
    rule_risk_reward_minimum,
    rule_confidence_threshold,
    # Por firma
    rule_topstep_no_vps,
    rule_apex_consistency_30_pct,
    rule_apex_no_ai_in_pa_live,
    # Scalping
    rule_scalp_only_in_allowed_mode,
    rule_scalp_window,
]


def evaluate_all(signal: TradeSignal, state: AccountState) -> tuple[list[str], dict[str, bool]]:
    """Corre cada regla. Devuelve (lista_de_motivos_de_fallo, rastro_de_auditoría).

    Lista de fallos vacía => aprobado (sujeto a lógica de size-down).
    """
    failures: list[str] = []
    audit: dict[str, bool] = {}
    for rule in ALL_RULES:
        passed, reason = rule(signal, state)
        audit[rule.__name__] = passed
        if not passed:
            failures.append(f"[{rule.__name__}] {reason}")
    return failures, audit
