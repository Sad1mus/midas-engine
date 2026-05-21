"""Ontología de riesgo — reglas ADITIVAS inyectadas por humano (vía Magister).

El Risk Manager lee las reglas activas de la tabla ``ontology_rules`` (schema v2,
ver DEC-006) y las aplica DESPUÉS de sus reglas duras. El invariante sagrado:
estas reglas **solo restringen** (``veto`` o ``size_down``). Nunca aprueban algo
que el Veto rechazaría — estructuralmente imposible, porque se aplican como un
filtro adicional sobre una señal que ya pasó las reglas duras, y solo pueden
rechazar o reducir el tamaño.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.config import settings
from core.models import TradeSignal

VALID_ACTIONS = ("veto", "size_down")


@dataclass(frozen=True)
class OntologyRule:
    """Una regla ontológica restrictiva, materializada desde ``ontology_rules``."""

    id: str
    rule_type: str
    action: str  # 'veto' | 'size_down'
    instrument: str | None = None
    window_spec: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    active: bool = True

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"ontology rule {self.id!r}: action must be one of {VALID_ACTIONS}, "
                f"got {self.action!r} — las reglas solo restringen"
            )


def load_active_rules(db_path: Path | None = None) -> list[OntologyRule]:
    """Lee las reglas ontológicas activas desde SQLite. Si la tabla no existe, []."""
    path = Path(db_path) if db_path is not None else settings.sqlite_path
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        try:
            rows = conn.execute(
                """
                SELECT id, rule_type, action, instrument, window_spec, params_json, active
                FROM ontology_rules
                WHERE active = 1
                ORDER BY ts_ms
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # ontology_rules ships in schema v2; older db → no rules
    finally:
        conn.close()

    rules: list[OntologyRule] = []
    for rid, rule_type, action, instrument, window_spec, params_json, active in rows:
        rules.append(
            OntologyRule(
                id=rid,
                rule_type=rule_type,
                action=action,
                instrument=instrument,
                window_spec=json.loads(window_spec) if window_spec else {},
                params=json.loads(params_json) if params_json else {},
                active=bool(active),
            )
        )
    return rules


def _within_window(rule: OntologyRule, ts: datetime) -> bool:
    """¿El instante ``ts`` cae dentro de la ventana de la regla?

    - ``instrument_block``: sin ventana temporal, siempre aplica (lo más restrictivo).
    - ventanas de evento/tiempo: ``window_spec`` lleva ``event_ts`` (ISO-8601),
      ``pre_min`` y ``post_min``; aplica si ts ∈ [event - pre, event + post].
    - sin ``event_ts``: aplica siempre (interpretación restrictiva por defecto).
    """
    if rule.rule_type == "instrument_block":
        return True
    event_ts = rule.window_spec.get("event_ts")
    if not event_ts:
        return True
    event = datetime.fromisoformat(event_ts)
    if event.tzinfo is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=event.tzinfo)
    pre = timedelta(minutes=float(rule.window_spec.get("pre_min", 0)))
    post = timedelta(minutes=float(rule.window_spec.get("post_min", 0)))
    return event - pre <= ts <= event + post


def rule_applies(rule: OntologyRule, signal: TradeSignal) -> bool:
    """¿Esta regla activa afecta a esta señal? (instrumento + ventana)."""
    if not rule.active:
        return False
    if rule.instrument is not None and rule.instrument != signal.symbol:
        return False
    return _within_window(rule, signal.created_at)


def matching_rules(rules: list[OntologyRule], signal: TradeSignal) -> list[OntologyRule]:
    return [r for r in rules if rule_applies(r, signal)]


def size_multiplier(rules: list[OntologyRule]) -> Decimal:
    """Multiplicador combinado de los ``size_down`` (producto, acotado a [0,1])."""
    mult = Decimal("1")
    for r in rules:
        if r.action == "size_down":
            raw = Decimal(str(r.params.get("size_mult", "0.5")))
            mult *= max(Decimal("0"), min(Decimal("1"), raw))
    return mult
