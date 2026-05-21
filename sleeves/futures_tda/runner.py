"""Runner del primer sleeve end-to-end (paper · Track B).

Corre el LOOP COMPLETO de MIDAS sobre OHLCV histórico local:

    loader OHLCV → Variable 𝒳 (régimen) → señal baseline condicionada al régimen
    → TradeSignal → Risk Manager (reglas duras + ontología + DD-gates)
    → simulación de fills con el cost model → trades / executions / equity_curve

Invariante sagrado: **ninguna orden evita el Risk Manager**. El runner solo abre
posición si `manager.evaluate` devuelve APPROVE / APPROVE_PENDING_HUMAN; un REJECT
se registra como trade `vetoed` sin ejecución. La señal depende del régimen:
régimen estable → momentum; cambio de régimen → mean-reversion (fade).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from core.models import AccountState, Direction, FirmAccount, RiskVerdict, SetupType, TradeSignal
from core.risk import manager
from core.risk.gates import evaluate_gate
from core.risk.kill_switch import KillSwitch
from core.risk.ontology import OntologyRule
from infra.audit import chain
from research.cost_model import CostOrder, calibrate_instrument, estimate_cost
from sleeves.futures_tda.regime_x import CLASSIFIER, detect_regime_from_close

SLIPPAGE_TICKS = 1.0


@dataclass
class RunResult:
    sleeve_id: str
    n_decisions: int
    n_executions: int
    n_vetoed: int
    final_equity: float
    starting_equity: float
    trade_statuses: dict[str, int] = field(default_factory=dict)


class SleeveRunner:
    """Sleeve TDA paper · Track B sobre datos locales."""

    def __init__(
        self,
        *,
        db_path: Path,
        chain_path: Path | None = None,
        sleeve_id: str = "futures_tda_v1",
        instrument: str = "NQ",
        track: str = "B",
        warmup: int = 100,
        step: int = 25,
        lookback: int = 20,
        starting_equity: float = 50_000.0,
    ) -> None:
        from research.cost_model.almgren_chriss import spec_for

        self.db_path = Path(db_path)
        self.chain_path = chain_path if chain_path is not None else chain.DEFAULT_CHAIN
        self.sleeve_id = sleeve_id
        self.instrument = instrument
        self.track = track
        self.warmup = warmup
        self.step = step
        self.lookback = lookback
        self.starting_equity = starting_equity
        self.tick_size, self.tick_value = spec_for(instrument)
        self.point_value = self.tick_value / self.tick_size

    # ── infraestructura ─────────────────────────────────────────────

    def _ensure_sleeve(self, conn: sqlite3.Connection) -> None:
        now = dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        conn.execute(
            """
            INSERT OR IGNORE INTO sleeves
                (id, name, track, status, asset_class, instruments, created_utc)
            VALUES (?, ?, ?, 'paper', 'futures', ?, ?)
            """,
            (
                self.sleeve_id,
                f"TDA {self.instrument}",
                self.track,
                json.dumps([self.instrument]),
                now,
            ),
        )
        conn.commit()

    def _audit_trade(self, conn: sqlite3.Connection, payload: dict) -> int:
        event = chain.append_event(
            actor=f"agent:sleeve:{self.sleeve_id}",
            event_type="trade",
            event_key=self.sleeve_id,
            payload=payload,
            chain_path=self.chain_path,
        )
        return chain.project_event(conn, event)

    # ── señal condicionada al régimen ───────────────────────────────

    def _make_signal(
        self, close: np.ndarray, i: int, ts: dt.datetime, regime_label: str
    ) -> TradeSignal | None:
        recent = close[i - self.lookback : i]
        if recent.size < 2:
            return None
        mom = float(close[i] - recent[0])
        # estable → momentum (seguir la tendencia); shift → mean-reversion (fade)
        go_long = mom < 0 if regime_label == "shift" else mom >= 0

        std_price = float(np.std(np.diff(recent)))
        risk_dist = max(self.tick_size, 2.0 * std_price)
        entry = float(close[i])
        rr = 2.5
        if go_long:
            direction = Direction.LONG
            stop = entry - risk_dist
            tp1 = entry + rr * risk_dist
        else:
            direction = Direction.SHORT
            stop = entry + risk_dist
            tp1 = entry - rr * risk_dist

        risk_per_contract = Decimal(str(round(risk_dist * self.point_value, 2)))
        if risk_per_contract <= 0:
            return None

        return TradeSignal(
            symbol=self.instrument,
            direction=direction,
            setup_type=SetupType.SETUP_1,
            timeframe="1m",
            entry_price=Decimal(str(round(entry, 2))),
            stop_loss=Decimal(str(round(stop, 2))),
            take_profit_1=Decimal(str(round(tp1, 2))),
            confidence=0.65,
            justification=f"baseline {regime_label} régimen tda_v1",
            target_account=FirmAccount.ZENITHSTONE_LIVE,
            suggested_size_contracts=2,
            risk_per_contract_usd=risk_per_contract,
            created_at=ts,
        )

    def _account_state(self, equity: float, trades_today: int, daily_pnl: float) -> AccountState:
        return AccountState(
            account_id=f"PAPER-{self.sleeve_id}",
            firm=FirmAccount.ZENITHSTONE_LIVE,
            equity_usd=Decimal(str(round(equity, 2))),
            starting_balance_usd=Decimal(str(round(self.starting_equity, 2))),
            daily_pnl_usd=Decimal(str(round(daily_pnl, 2))),
            trades_today=trades_today,
            max_drawdown_threshold_usd=Decimal("2500"),
        )

    def _write_equity(
        self, conn: sqlite3.Connection, ts: dt.datetime, equity: float, peak: float
    ) -> None:
        dd = (peak - equity) / peak if peak > 0 else 0.0
        conn.execute(
            """
            INSERT INTO equity_curve
                (ts_utc, ts_ms, sleeve_id, track, equity, peak_equity, drawdown_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                int(ts.timestamp() * 1000),
                self.sleeve_id,
                self.track,
                round(equity, 2),
                round(peak, 2),
                round(dd, 6),
            ),
        )

    def _write_regime(
        self, conn: sqlite3.Connection, ts: dt.datetime, label: str, dtopo: float
    ) -> int:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO regime_states
                (ts_utc, ts_ms, classifier, regime_label, posterior_json, features_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                int(ts.timestamp() * 1000),
                CLASSIFIER,
                label,
                json.dumps({"label": label}),
                json.dumps({"d_topo": round(dtopo, 6)}),
            ),
        )
        return int(cur.lastrowid or 0)

    # ── loop principal ──────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame,
        *,
        kill_switch: KillSwitch,
        ontology_rules: list[OntologyRule] | None = None,
    ) -> RunResult:
        close = df["close"].to_numpy(dtype=float)
        index = df.index
        calibrations = {
            c.hour_utc: c for c in calibrate_instrument(df, self.instrument, db_path=self.db_path)
        }

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_sleeve(conn)

        equity = self.starting_equity
        peak = equity
        statuses: dict[str, int] = {}
        n_decisions = n_exec = n_vetoed = 0
        position: dict | None = None
        cur_day = None
        trades_today = 0
        daily_pnl = 0.0

        try:
            for i in range(self.warmup, len(df) - 1, self.step):
                ts = index[i].to_pydatetime()
                day = ts.date()
                if day != cur_day:
                    cur_day, trades_today, daily_pnl = day, 0, 0.0

                # 1. realizar PnL de la posición abierta en la decisión anterior
                if position is not None:
                    move = (close[i] - position["entry"]) * position["sign"]
                    pnl = move * self.point_value * position["size"] - position["round_trip_cost"]
                    equity += pnl
                    daily_pnl += pnl
                    position = None

                # 2. registrar equity_curve (el gate lo leerá)
                peak = max(peak, equity)
                self._write_equity(conn, ts, equity, peak)
                conn.commit()

                # 3. DD-gate desde equity_curve
                gate = evaluate_gate(
                    conn,
                    sleeve_id=self.sleeve_id,
                    track=self.track,
                    ts=ts,
                    chain_path=self.chain_path,
                )

                # 4. régimen (Variable 𝒳) + señal
                rx = detect_regime_from_close(close[i - self.warmup : i])
                regime_id = self._write_regime(conn, ts, rx.label, rx.d_topo)
                signal = self._make_signal(close, i, ts, rx.label)
                if signal is None:
                    continue
                n_decisions += 1

                # 5. RISK MANAGER — ninguna orden lo evita
                state = self._account_state(equity, trades_today, daily_pnl)
                decision = manager.evaluate(
                    signal,
                    state,
                    kill_switch=kill_switch,
                    ontology_rules=ontology_rules,
                    gate=gate,
                )

                trade_id = uuid.uuid4().hex
                sign = 1 if signal.direction == Direction.LONG else -1
                side = "long" if sign == 1 else "short"
                approved = decision.verdict in (
                    RiskVerdict.APPROVE,
                    RiskVerdict.APPROVE_PENDING_HUMAN,
                )
                status = "filled" if approved and decision.approved_size_contracts > 0 else "vetoed"
                statuses[status] = statuses.get(status, 0) + 1

                audit_id = self._audit_trade(
                    conn,
                    {
                        "trade_id": trade_id,
                        "verdict": decision.verdict,
                        "status": status,
                        "instrument": self.instrument,
                        "side": side,
                        "size": decision.approved_size_contracts,
                    },
                )
                conn.execute(
                    """
                    INSERT INTO trades
                        (id, sleeve_id, track, ts_signal_utc, ts_signal_ms, instrument, side,
                         size_target, entry_target, stop_target, take_target, regime_id,
                         status, veto_reason, audit_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_id,
                        self.sleeve_id,
                        self.track,
                        ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        int(ts.timestamp() * 1000),
                        self.instrument,
                        side,
                        float(decision.approved_size_contracts),
                        float(signal.entry_price),
                        float(signal.stop_loss),
                        float(signal.take_profit_1),
                        regime_id,
                        status,
                        None if approved else " | ".join(decision.reasons)[:300],
                        audit_id,
                    ),
                )

                if status == "filled":
                    size = decision.approved_size_contracts
                    cal = calibrations.get(ts.hour)
                    cost = (
                        estimate_cost(CostOrder(self.instrument, size), calibration=cal)
                        if cal
                        else 0.0
                    )
                    fill_price = float(signal.entry_price) + sign * SLIPPAGE_TICKS * self.tick_size
                    conn.execute(
                        """
                        INSERT INTO executions
                            (id, trade_id, ts_fill_utc, ts_fill_ms, instrument, side, size_filled,
                             price_fill, commission, fees, slippage_ticks, broker, audit_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            trade_id,
                            ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                            int(ts.timestamp() * 1000),
                            self.instrument,
                            side,
                            float(size),
                            fill_price,
                            round(cost, 4),
                            0.0,
                            SLIPPAGE_TICKS,
                            "paper_b",
                            audit_id,
                        ),
                    )
                    n_exec += 1
                    trades_today += 1
                    position = {
                        "entry": fill_price,
                        "size": size,
                        "sign": sign,
                        "round_trip_cost": 2.0 * cost,
                    }
                else:
                    n_vetoed += 1
                conn.commit()

            # cerrar posición final al último cierre
            if position is not None:
                last = float(close[-1])
                move = (last - position["entry"]) * position["sign"]
                equity += move * self.point_value * position["size"] - position["round_trip_cost"]
                peak = max(peak, equity)
                self._write_equity(conn, index[-1].to_pydatetime(), equity, peak)
                conn.commit()
        finally:
            conn.close()

        return RunResult(
            sleeve_id=self.sleeve_id,
            n_decisions=n_decisions,
            n_executions=n_exec,
            n_vetoed=n_vetoed,
            final_equity=equity,
            starting_equity=self.starting_equity,
            trade_statuses=statuses,
        )
