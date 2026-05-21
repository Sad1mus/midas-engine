"""Motor de DD-gates — drawdown-first, el principio rector de MIDAS.

La supervivencia manda: antes que maximizar retorno, el sistema reduce exposición
cuando el drawdown crece. Máquina de estados monótona y solo restrictiva:

    normal → dd5 → dd10 → dd15 → halt
    sizing_mult:  1.0   0.5    0.25   0.125   0.0

El gate lee el drawdown actual desde `equity_curve` (pico vs equity), escribe el
estado a `risk_gates_state` y, al **cruzar un umbral**, emite un evento `gate_trip`
al hash chain (fuente de verdad, DEC-005). El Risk Manager multiplica el tamaño ya
aprobado por `sizing_mult` — nunca lo aumenta (DEC-001: los gates solo reducen).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from infra.audit import chain

# Mapa explícito nivel → sizing_mult (única fuente de la escala).
SIZING_MULT: dict[str, float] = {
    "normal": 1.0,
    "dd5": 0.5,
    "dd10": 0.25,
    "dd15": 0.125,
    "halt": 0.0,
}

# Umbral de halt (drawdown catastrófico): por encima de esto se detiene del todo.
HALT_THRESHOLD = 0.20


@dataclass(frozen=True)
class GateState:
    level: str
    sizing_mult: float
    drawdown_pct: float
    tripped: bool = False  # True si esta evaluación cruzó a un nivel distinto
    previous_level: str | None = None

    @property
    def is_halt(self) -> bool:
        return self.level == "halt"


def level_for_drawdown(drawdown_pct: float) -> tuple[str, float]:
    """Devuelve (nivel, sizing_mult) para un drawdown dado (fracción 0..1)."""
    dd = max(0.0, float(drawdown_pct))
    if dd >= HALT_THRESHOLD:
        return "halt", SIZING_MULT["halt"]
    if dd >= 0.15:
        return "dd15", SIZING_MULT["dd15"]
    if dd >= 0.10:
        return "dd10", SIZING_MULT["dd10"]
    if dd >= 0.05:
        return "dd5", SIZING_MULT["dd5"]
    return "normal", SIZING_MULT["normal"]


def current_drawdown(
    conn: sqlite3.Connection, *, sleeve_id: str | None = None, track: str | None = None
) -> float:
    """Drawdown actual = (pico - equity_ultimo) / pico, leido de `equity_curve`.

    Filtra opcionalmente por sleeve/track. Devuelve 0.0 si no hay datos o no hay caída.
    """
    where = []
    params: list[object] = []
    if sleeve_id is not None:
        where.append("sleeve_id = ?")
        params.append(sleeve_id)
    if track is not None:
        where.append("track = ?")
        params.append(track)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT equity FROM equity_curve{clause} ORDER BY ts_ms ASC", params
    ).fetchall()
    if not rows:
        return 0.0
    equities = [float(r[0]) for r in rows]
    peak = equities[0]
    last = equities[-1]
    for e in equities:
        peak = max(peak, e)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - last) / peak)


def _last_recorded_level(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT gate_level FROM risk_gates_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def evaluate_gate(
    conn: sqlite3.Connection,
    *,
    sleeve_id: str | None = None,
    track: str | None = None,
    ts: dt.datetime | None = None,
    chain_path: Path | None = None,
    notes: str | None = None,
) -> GateState:
    """Evalúa el gate desde `equity_curve`, persiste el estado y emite `gate_trip` al cruzar.

    Escribe SIEMPRE una fila en `risk_gates_state` (rastro temporal); emite el evento
    `gate_trip` al chain SOLO cuando el nivel cambia respecto al último registrado.
    """
    now = ts or dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    dd = current_drawdown(conn, sleeve_id=sleeve_id, track=track)
    level, mult = level_for_drawdown(dd)
    previous = _last_recorded_level(conn)
    previous_effective = previous or "normal"
    tripped = previous_effective != level

    trigger_audit_id: int | None = None
    if tripped:
        event = chain.append_event(
            actor="system:dd_gate",
            event_type="gate_trip",
            event_key=level,
            payload={
                "from_level": previous_effective,
                "to_level": level,
                "drawdown_pct": round(dd, 6),
                "sizing_mult": mult,
                "sleeve_id": sleeve_id,
                "track": track,
            },
            chain_path=chain_path if chain_path is not None else chain.DEFAULT_CHAIN,
        )
        trigger_audit_id = chain.project_event(conn, event)

    conn.execute(
        """
        INSERT INTO risk_gates_state
            (ts_utc, ts_ms, current_dd_pct, gate_level, sizing_mult, trigger_audit_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ts_iso, ts_ms, round(dd, 6), level, mult, trigger_audit_id, notes),
    )
    conn.commit()

    return GateState(
        level=level,
        sizing_mult=mult,
        drawdown_pct=dd,
        tripped=tripped,
        previous_level=previous,
    )


def apply_sizing(size: int, gate: GateState | None) -> int:
    """Aplica el sizing_mult del gate a un tamaño ya aprobado. Solo reduce (DEC-001)."""
    if gate is None:
        return size
    mult = min(1.0, max(0.0, gate.sizing_mult))  # invariante defensivo: nunca aumenta
    return int(size * mult)
