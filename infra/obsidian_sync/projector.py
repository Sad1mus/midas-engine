"""Projector — SQLite → Markdown (read-only, batch, idempotente).

El cockpit de MIDAS: proyecta el estado vivo del `.db` a notas Markdown con
frontmatter YAML, para que un humano lo VEA en Obsidian (+ plugin Dataview).

Principios inviolables (DEC-001 / Obsidian asíncrono):
  * **Read-only**: abre el `.db` con `mode=ro` (URI). NUNCA un INSERT/UPDATE.
  * **Batch / offline**: este módulo NO debe importarse desde `core.risk` ni el
    hot path del sleeve. El vault es proyección, jamás fuente de verdad.
  * **No clobber**: escribe SOLO en carpetas generadas (`vault/00_System/cockpit/`
    y `vault/_generated/`). Nunca toca `vault/10_Decisions` (notas humanas).
  * **Idempotente**: el contenido es función pura de los datos (sin `now()`), así
    que `project_all` dos veces produce bytes idénticos (sin duplicados).

Cada nota generada lleva `generated: true` y un aviso "no editar a mano".
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GENERATED_DIR = "_generated"
COCKPIT_DIR = "00_System/cockpit"
RECENT_LIMIT = 50

# Secciones del dashboard: (título, carpeta FROM, type_filter|None, [campos], sort|None, limit|None).
# Esta spec ALIMENTA las queries Dataview y la verificación del test (quedan en sync).
DASHBOARD_SECTIONS: list[tuple[str, str, str | None, list[str], str | None, int | None]] = [
    ("🛡️ DD-gate (estado)", GENERATED_DIR, "cockpit-equity",
     ["gate_level", "sizing_mult", "current_dd_pct"], None, None),
    ("💰 Equity / PnL", GENERATED_DIR, "cockpit-equity",
     ["equity", "peak_equity", "drawdown_pct", "realized_pnl"], None, None),
    ("📈 Trades recientes", f"{GENERATED_DIR}/trades", "cockpit-trade",
     ["instrument", "side", "status", "vetoed"], "ts DESC", 20),
    ("🧪 Sleeves — veredicto GO/NO-GO", f"{GENERATED_DIR}/sleeves", "cockpit-sleeve",
     ["verdict", "dsr", "pbo", "max_dd_pct"], "pbo ASC", None),
    ("🌀 Regímenes 𝒳 recientes", f"{GENERATED_DIR}/regimes", "cockpit-regime",
     ["regime", "d_topo", "ts"], "ts DESC", 10),
    ("⚖️ Reglas de Magister activas", f"{GENERATED_DIR}/ontology", "cockpit-rule",
     ["action", "instrument", "rule_type"], None, None),
    ("📜 Decisiones (DEC) recientes", "10_Decisions", None,
     ["status", "title"], "id DESC", 10),
]

GENERATED_WARNING = (
    "> ⚠️ Nota **generada** por el cockpit (`scripts/cockpit.py`). "
    "NO editar a mano: se sobrescribe en cada proyección."
)


# ─────────────────────────────────────────────────────────────────────
# YAML determinista (sin pyyaml en la salida → orden estable, idempotente)
# ─────────────────────────────────────────────────────────────────────


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    s = str(value)
    # citar si contiene caracteres conflictivos para YAML
    if s == "" or any(c in s for c in ':#"\n') or s[0] in "[]{}>|*&!%@`'\"":
        return json.dumps(s, ensure_ascii=False)
    return s


def to_yaml(frontmatter: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in frontmatter.items():
        if isinstance(value, list):
            inner = ", ".join(_yaml_scalar(v) for v in value)
            lines.append(f"{key}: [{inner}]")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def render_note(frontmatter: dict[str, Any], body: str) -> str:
    """Contenido completo de una nota: frontmatter YAML + aviso + cuerpo."""
    fm = dict(frontmatter)
    fm.setdefault("generated", True)
    return f"---\n{to_yaml(fm)}---\n\n{GENERATED_WARNING}\n\n{body.rstrip()}\n"


def write_note(path: Path, frontmatter: dict[str, Any], body: str) -> Path:
    """Escribe una nota de forma idempotente (mismo contenido ⇒ mismos bytes)."""
    content = render_note(frontmatter, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return path
    path.write_text(content, encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────
# Lectura read-only de la BD
# ─────────────────────────────────────────────────────────────────────


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Conexión SOLO lectura (URI mode=ro). Cualquier escritura lanzaría error."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _verdict(dsr: float | None, pbo: float | None) -> str:
    if dsr is None or pbo is None:
        return "PENDING"
    return "GO" if (dsr > 0 and pbo < 0.5) else "NO-GO"


@dataclass
class ProjectionSummary:
    vault: str
    counts: dict[str, int] = field(default_factory=dict)
    gate_level: str = "n/a"
    sizing_mult: float | None = None
    equity: float | None = None
    drawdown_pct: float | None = None
    sleeves_go: int = 0
    sleeves_nogo: int = 0


# ─────────────────────────────────────────────────────────────────────
# Proyección por entidad
# ─────────────────────────────────────────────────────────────────────


def _project_equity(conn: sqlite3.Connection, gen: Path, summary: ProjectionSummary) -> None:
    """Nota de estado: equity/PnL + nivel de DD-gate actual."""
    eq = conn.execute(
        "SELECT ts_utc, equity, peak_equity, drawdown_pct, realized_pnl, unrealized_pnl "
        "FROM equity_curve ORDER BY ts_ms DESC LIMIT 1"
    ).fetchone()
    gate = conn.execute(
        "SELECT ts_utc, gate_level, sizing_mult, current_dd_pct "
        "FROM risk_gates_state ORDER BY id DESC LIMIT 1"
    ).fetchone()

    fm: dict[str, Any] = {
        "type": "cockpit-equity",
        "title": "Estado actual — Equity & DD-gate",
        "equity": round(float(eq["equity"]), 2) if eq else None,
        "peak_equity": round(float(eq["peak_equity"]), 2) if eq else None,
        "drawdown_pct": round(float(eq["drawdown_pct"]), 6) if eq else None,
        "realized_pnl": round(float(eq["realized_pnl"]), 2) if eq else None,
        "gate_level": gate["gate_level"] if gate else "normal",
        "sizing_mult": float(gate["sizing_mult"]) if gate else 1.0,
        "current_dd_pct": round(float(gate["current_dd_pct"]), 6) if gate else 0.0,
        "as_of": (eq["ts_utc"] if eq else (gate["ts_utc"] if gate else "n/a")),
    }
    summary.gate_level = fm["gate_level"]
    summary.sizing_mult = fm["sizing_mult"]
    summary.equity = fm["equity"]
    summary.drawdown_pct = fm["drawdown_pct"]

    body = (
        f"# Estado actual\n\n"
        f"- **Equity:** {fm['equity']}  (pico {fm['peak_equity']})\n"
        f"- **Drawdown:** {fm['drawdown_pct']}\n"
        f"- **DD-gate:** `{fm['gate_level']}`  (sizing ×{fm['sizing_mult']})\n"
    )
    write_note(gen / "equity.md", fm, body)
    summary.counts["equity"] = 1


def _project_sleeves(conn: sqlite3.Connection, gen: Path, summary: ProjectionSummary) -> None:
    ids = {
        r[0] for r in conn.execute("SELECT id FROM sleeves").fetchall()
    } | {
        r[0] for r in conn.execute("SELECT DISTINCT sleeve_id FROM backtest_results").fetchall()
    }
    n = 0
    for sid in sorted(ids):
        meta = conn.execute(
            "SELECT name, track, status, asset_class, instruments FROM sleeves WHERE id = ?", (sid,)
        ).fetchone()
        bt = conn.execute(
            "SELECT ts_utc, dsr, pbo, max_dd_pct, n_paths, sharpe_mean, embargo_pct, purge_pct "
            "FROM backtest_results WHERE sleeve_id = ? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()
        dsr = float(bt["dsr"]) if bt else None
        pbo = float(bt["pbo"]) if bt else None
        verdict = _verdict(dsr, pbo)
        if verdict == "GO":
            summary.sleeves_go += 1
        elif verdict == "NO-GO":
            summary.sleeves_nogo += 1
        fm: dict[str, Any] = {
            "type": "cockpit-sleeve",
            "sleeve_id": sid,
            "name": meta["name"] if meta else sid,
            "track": meta["track"] if meta else None,
            "status": meta["status"] if meta else "research",
            "dsr": round(dsr, 6) if dsr is not None else None,
            "pbo": round(pbo, 6) if pbo is not None else None,
            "max_dd_pct": round(float(bt["max_dd_pct"]), 6) if bt else None,
            "sharpe_mean": round(float(bt["sharpe_mean"]), 6) if bt else None,
            "n_paths": int(bt["n_paths"]) if bt else None,
            "verdict": verdict,
            "as_of": bt["ts_utc"] if bt else "n/a",
        }
        body = (
            f"# Sleeve `{sid}`\n\n"
            f"- **Veredicto:** **{verdict}**  (DSR={fm['dsr']}, PBO={fm['pbo']})\n"
            f"- **Track:** {fm['track']}  ·  **Status:** {fm['status']}\n"
            f"- **maxDD:** {fm['max_dd_pct']}  ·  **paths:** {fm['n_paths']}\n"
        )
        write_note(gen / "sleeves" / f"{sid}.md", fm, body)
        n += 1
    summary.counts["sleeves"] = n


def _project_regimes(conn: sqlite3.Connection, gen: Path, summary: ProjectionSummary) -> None:
    rows = conn.execute(
        "SELECT id, ts_utc, classifier, regime_label, features_json "
        "FROM regime_states ORDER BY ts_ms DESC LIMIT ?", (RECENT_LIMIT,)
    ).fetchall()
    n = 0
    for r in rows:
        feats = json.loads(r["features_json"]) if r["features_json"] else {}
        fm: dict[str, Any] = {
            "type": "cockpit-regime",
            "regime_id": int(r["id"]),
            "classifier": r["classifier"],
            "regime": r["regime_label"],
            "d_topo": round(float(feats.get("d_topo", 0.0)), 6),
            "ts": r["ts_utc"],
        }
        body = f"# Régimen `{r['regime_label']}`\n\n- **classifier:** {r['classifier']}\n- **D_topo:** {fm['d_topo']}\n- **ts:** {fm['ts']}\n"
        write_note(gen / "regimes" / f"{int(r['id']):08d}.md", fm, body)
        n += 1
    summary.counts["regimes"] = n


def _project_trades(conn: sqlite3.Connection, gen: Path, summary: ProjectionSummary) -> None:
    rows = conn.execute(
        "SELECT id, ts_signal_utc, instrument, side, size_target, status, veto_reason "
        "FROM trades ORDER BY ts_signal_ms DESC LIMIT ?", (RECENT_LIMIT,)
    ).fetchall()
    n = 0
    for r in rows:
        fm: dict[str, Any] = {
            "type": "cockpit-trade",
            "trade_id": r["id"],
            "instrument": r["instrument"],
            "side": r["side"],
            "size": float(r["size_target"]),
            "status": r["status"],
            "vetoed": r["status"] == "vetoed",
            "ts": r["ts_signal_utc"],
        }
        reason = (r["veto_reason"] or "").strip()
        body = f"# Trade {r['instrument']} {r['side']} — {r['status']}\n\n- **size:** {fm['size']}\n- **ts:** {fm['ts']}\n"
        if reason:
            body += f"- **veto:** {reason}\n"
        write_note(gen / "trades" / f"{r['id']}.md", fm, body)
        n += 1
    summary.counts["trades"] = n


def _project_ontology(conn: sqlite3.Connection, gen: Path, summary: ProjectionSummary) -> None:
    try:
        rows = conn.execute(
            "SELECT id, rule_type, instrument, action, active, window_spec "
            "FROM ontology_rules WHERE active = 1 ORDER BY ts_ms DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        summary.counts["ontology"] = 0
        return
    n = 0
    for r in rows:
        fm: dict[str, Any] = {
            "type": "cockpit-rule",
            "rule_id": r["id"],
            "rule_type": r["rule_type"],
            "instrument": r["instrument"],
            "action": r["action"],
            "active": bool(r["active"]),
        }
        body = f"# Regla Magister `{r['id']}`\n\n- **acción:** {r['action']} (solo restringe)\n- **instrumento:** {r['instrument'] or 'todos'}\n- **tipo:** {r['rule_type']}\n"
        write_note(gen / "ontology" / f"{r['id']}.md", fm, body)
        n += 1
    summary.counts["ontology"] = n


def _dataview_block(
    folder: str, type_filter: str | None, fields: list[str], sort: str | None, limit: int | None
) -> str:
    q = f"TABLE {', '.join(fields)}\nFROM \"{folder}\""
    if type_filter:
        q += f'\nWHERE type = "{type_filter}"'
    if sort:
        q += f"\nSORT {sort}"
    if limit:
        q += f"\nLIMIT {limit}"
    return f"```dataview\n{q}\n```"


def _project_dashboard(gen_summary: ProjectionSummary, vault: Path) -> None:
    """Nota índice del cockpit con las 7 consultas Dataview que agregan el estado."""
    intro = (
        "## Qué estoy viendo\n\n"
        "Este es el **cockpit de MIDAS**: el estado vivo del fondo proyectado desde SQLite "
        "(read-only) a notas que Dataview agrega aquí. Arriba, la **supervivencia** "
        "(DD-gate + equity); abajo, qué hace el sistema (**trades**, **sleeves** con su "
        "veredicto GO/NO-GO, **regímenes 𝒳**, **reglas de Magister** y **decisiones**).\n\n"
        "> Requiere el plugin **Dataview** en Obsidian. Regenerar con "
        "`python scripts/cockpit.py`.\n"
    )
    sections = [intro]
    for title, folder, type_filter, fields, sort, limit in DASHBOARD_SECTIONS:
        sections.append(
            f"## {title}\n\n{_dataview_block(folder, type_filter, fields, sort, limit)}"
        )
    body = "# MIDAS — Cockpit\n\n" + "\n\n".join(sections)
    fm = {"type": "cockpit-dashboard", "title": "MIDAS — Cockpit", "cssclass": "cockpit"}
    write_note(vault / COCKPIT_DIR / "MIDAS — Cockpit.md", fm, body)
    gen_summary.counts["dashboard"] = 1


def project_all(db_path: Path, vault_path: Path) -> ProjectionSummary:
    """Proyecta TODO el estado del .db a notas Markdown en las carpetas generadas.

    Read-only y batch. Devuelve un resumen con conteos y estado del gate.
    """
    vault = Path(vault_path)
    gen = vault / GENERATED_DIR
    summary = ProjectionSummary(vault=str(vault))

    conn = _connect_ro(db_path)
    try:
        _project_equity(conn, gen, summary)
        _project_sleeves(conn, gen, summary)
        _project_regimes(conn, gen, summary)
        _project_trades(conn, gen, summary)
        _project_ontology(conn, gen, summary)
    finally:
        conn.close()
    _project_dashboard(summary, vault)
    return summary
