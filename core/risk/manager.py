"""Risk Manager — lógica de VETO pura y determinista.

El principio sagrado de MIDAS: la IA propone, el riesgo dispone. Ninguna operación
llega al broker sin pasar por aquí. Esta capa NO importa nada de la capa cognitiva
(LLM/MCP): es la barrera que garantiza que ninguna alucinación salte el Veto.

Diseño: una función pura `evaluate()` que recibe la señal, el estado de la cuenta y un
KillSwitch (inyectado para testeo), y devuelve una RiskDecision. Sin estado global, sin
side-effects más allá de la consulta al kill switch.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from core.config import Settings
from core.config import settings as default_settings
from core.models import (
    AccountState,
    FirmAccount,
    RiskDecision,
    RiskVerdict,
    SetupType,
    TradeSignal,
)
from core.risk.gates import GateState, apply_sizing
from core.risk.kill_switch import KillSwitch
from core.risk.ontology import OntologyRule, matching_rules, size_multiplier
from core.risk.rules import evaluate_all

logger = structlog.get_logger(__name__)

# Riesgo por trade como fracción del equity. El Risk Manager NUNCA aumenta, solo reduce.
PER_TRADE_RISK_PCT = Decimal("0.013")
SCALP_PER_TRADE_RISK_PCT = Decimal("0.007")  # scalping: más frecuente, menos por tiro


def compute_approved_size(signal: TradeSignal, state: AccountState) -> int:
    """Lógica de size-down. Devuelve el tamaño aprobado (<= sugerido, nunca mayor)."""
    risk_pct = (
        SCALP_PER_TRADE_RISK_PCT
        if signal.setup_type == SetupType.SCALP_EMA.value
        else PER_TRADE_RISK_PCT
    )
    max_risk_dollars = state.equity_usd * risk_pct
    cost_per_contract = signal.risk_per_contract_usd
    if cost_per_contract <= 0:
        return 0
    max_by_dollar = int(max_risk_dollars / cost_per_contract)
    return min(signal.suggested_size_contracts, max(0, max_by_dollar))


def requires_human_confirm(
    signal: TradeSignal,
    state: AccountState,
    size: int,
    cfg: Settings,
) -> tuple[bool, str | None]:
    """Cuentas Modo B (Apex) siempre requieren humano. También trades de monto grande."""
    if state.firm == FirmAccount.APEX.value:
        return True, "Apex requiere confirmación humana (Modo B)"
    dollar_exposure = Decimal(size) * signal.risk_per_contract_usd
    if dollar_exposure >= Decimal(str(cfg.min_trade_approval_usd)):
        return True, f"Exposición ${dollar_exposure} >= umbral ${cfg.min_trade_approval_usd}"
    return False, None


def evaluate(
    signal: TradeSignal,
    state: AccountState,
    *,
    kill_switch: KillSwitch,
    cfg: Settings = default_settings,
    ontology_rules: list[OntologyRule] | None = None,
    gate: GateState | None = None,
) -> RiskDecision:
    """Punto de entrada del Veto. Determinista y sin efectos colaterales (salvo log).

    Orden de evaluación:
      1. Kill switch — anula todo.
      2. Todas las reglas duras — primer fallo = REJECT.
      3. Reglas ontológicas (Magister) — SOLO restringen: ``veto`` → REJECT;
         ``size_down`` → recorta el tamaño. Se aplican después de las duras, así que
         jamás pueden aprobar algo que el Veto ya rechazó.
      4. Size-down (1.3%/0.7% + ontología + **DD-gate**, todo solo reduce) — 0 ⇒ REJECT.
      5. ¿Requiere humano? → APPROVE_PENDING_HUMAN, si no APPROVE.
    """
    # 1. Kill switch anula todo
    if kill_switch.is_triggered():
        ks_reason = str(kill_switch.status().get("reason") or "active")
        logger.warning("veto.kill_switch", signal_id=str(signal.signal_id))
        return RiskDecision(
            signal_id=signal.signal_id,
            verdict=RiskVerdict.REJECT,
            reasons=[f"KILL_SWITCH: {ks_reason}"],
            approved_size_contracts=0,
            checks_performed={"kill_switch_check": False},
            consecutive_losses=state.consecutive_losses,
        )

    # 2. Reglas duras
    failures, audit = evaluate_all(signal, state)
    if failures:
        logger.info("veto.rejected", signal_id=str(signal.signal_id), failures=failures)
        return RiskDecision(
            signal_id=signal.signal_id,
            verdict=RiskVerdict.REJECT,
            reasons=failures,
            approved_size_contracts=0,
            checks_performed=audit,
            consecutive_losses=state.consecutive_losses,
        )

    # 3. Reglas ontológicas (aditivas, solo restringen)
    applied = matching_rules(ontology_rules or [], signal)
    ontology_size_down = [r for r in applied if r.action == "size_down"]
    ontology_vetoes = [r for r in applied if r.action == "veto"]
    for r in applied:
        audit[f"ontology:{r.id}"] = False  # una regla que aplica = una restricción activa
    if ontology_vetoes:
        reasons = [
            f"ONTOLOGY[{r.id}]: {r.rule_type} veta {r.instrument or 'cualquier'} "
            f"({r.window_spec.get('event', 'ventana')})"
            for r in ontology_vetoes
        ]
        logger.info("veto.ontology", signal_id=str(signal.signal_id), rules=reasons)
        return RiskDecision(
            signal_id=signal.signal_id,
            verdict=RiskVerdict.REJECT,
            reasons=reasons,
            approved_size_contracts=0,
            checks_performed=audit,
            consecutive_losses=state.consecutive_losses,
        )

    # 4. Size-down (regla del 1.3%/0.7% + recorte ontológico + DD-gate; todo solo reduce)
    approved_size = compute_approved_size(signal, state)
    if ontology_size_down:
        approved_size = int(approved_size * size_multiplier(ontology_size_down))
    if gate is not None:
        audit[f"dd_gate:{gate.level}"] = gate.sizing_mult >= 1.0
        approved_size = apply_sizing(approved_size, gate)
    if approved_size == 0:
        if gate is not None and gate.is_halt:
            reason = f"DD-gate HALT: drawdown {gate.drawdown_pct * 100:.1f}% — trading detenido"
        else:
            reason = "Reducido a cero — el riesgo por contrato supera el 1.3%/0.7% del equity"
        logger.warning("veto.size_zero", signal_id=str(signal.signal_id), reason=reason)
        return RiskDecision(
            signal_id=signal.signal_id,
            verdict=RiskVerdict.REJECT,
            reasons=[reason],
            approved_size_contracts=0,
            checks_performed=audit,
            consecutive_losses=state.consecutive_losses,
        )

    # 5. ¿Humano?
    needs_human, reason = requires_human_confirm(signal, state, approved_size, cfg)
    verdict = RiskVerdict.APPROVE_PENDING_HUMAN if needs_human else RiskVerdict.APPROVE

    reasons = [f"Pasaron las {len(audit)} reglas", f"Tamaño aprobado: {approved_size} contratos"]
    if gate is not None and gate.sizing_mult < 1.0:
        reasons.append(f"DD-gate {gate.level} recortó el tamaño (×{gate.sizing_mult})")

    logger.info(
        "veto.approved",
        signal_id=str(signal.signal_id),
        size=approved_size,
        needs_human=needs_human,
        gate=gate.level if gate else "none",
    )
    return RiskDecision(
        signal_id=signal.signal_id,
        verdict=verdict,
        reasons=reasons,
        approved_size_contracts=approved_size,
        checks_performed=audit,
        consecutive_losses=state.consecutive_losses,
        requires_human_confirm=needs_human,
        human_confirm_reason=reason,
    )
