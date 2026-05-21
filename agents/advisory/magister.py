"""Magister — el escritor único de reglas ontológicas.

Magister es el ÚNICO componente autorizado a inyectar reglas en la ontología de
riesgo. Toma una intención en lenguaje natural (más sus parámetros estructurados)
y, en un solo acto atómico:

  1. Hace **append al hash chain** (``infra/audit/chain.ndjson``) — la fuente de
     verdad (DEC-005). El evento es ``ontology_rule``.
  2. **Proyecta** el evento a la tabla ``ontology_rules`` (schema v2, DEC-006).
  3. **Proyecta una nota** legible al vault (``vault/20_Ontology/<id>.md``).

Invariante sagrado: las reglas son **solo aditivas y restrictivas**
(``action ∈ {veto, size_down}``). Magister nunca puede emitir una regla que
apruebe algo; si se le pide, lanza ``ValueError``. El Risk Manager las lee con
``core.risk.ontology.load_active_rules`` y las aplica después de sus reglas duras.

Uso programático:
    from agents.advisory import magister
    rule = magister.inject_rule(
        description="congelar NQ ±30min de FOMC",
        rule_type="event_window", action="veto", instrument="NQ",
        window_spec={"event": "FOMC", "event_ts": "2026-06-18T18:00:00+00:00",
                     "pre_min": 30, "post_min": 30},
    )

Uso CLI (el skill /magister lo invoca):
    python -m agents.advisory.magister inject \
        --description "congelar NQ ±30min de FOMC" \
        --rule-type event_window --action veto --instrument NQ \
        --window '{"event":"FOMC","event_ts":"2026-06-18T18:00:00+00:00","pre_min":30,"post_min":30}'
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from core.risk.ontology import VALID_ACTIONS, OntologyRule  # noqa: E402
from infra.audit import chain  # noqa: E402

VAULT_ONTOLOGY_SUBDIR = "20_Ontology"


def _slug_id(description: str) -> str:
    """Deriva un id estable y legible desde la descripción en lenguaje natural."""
    s = re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")
    return f"rule_{s[:48]}" if s else "rule_unnamed"


def _vault_note(
    *,
    rule_id: str,
    description: str,
    rule_type: str,
    instrument: str | None,
    window_spec: dict[str, Any],
    action: str,
    params: dict[str, Any],
    owner: str,
    event: dict[str, Any],
) -> str:
    """Genera el Markdown (con frontmatter) de la nota de la regla."""
    created = event["ts_utc"].split("T")[0]
    fm = {
        "id": rule_id,
        "title": description,
        "type": "ontology_rule",
        "status": "active",
        "owner": owner,
        "created": created,
        "action": action,
        "instrument": instrument,
        "audit_chain_hash": event["chain_hash"],
        "tags": ["ontology", "magister", "risk"],
    }
    front = json.dumps(fm, indent=2, ensure_ascii=False)
    return (
        f"---\n{_to_yaml(fm)}---\n\n"
        f"# Regla ontológica: {description}\n\n"
        f"> Inyectada por **Magister** (escritor único). Solo restringe — "
        f"acción `{action}`. Anclada al hash chain en `{event['chain_hash'][:16]}…`.\n\n"
        f"## Definición estructurada\n\n"
        f"- **id:** `{rule_id}`\n"
        f"- **tipo:** `{rule_type}`\n"
        f"- **instrumento:** `{instrument or 'todos'}`\n"
        f"- **acción:** `{action}`\n"
        f"- **ventana:** `{json.dumps(window_spec, ensure_ascii=False)}`\n"
        f"- **params:** `{json.dumps(params, ensure_ascii=False)}`\n\n"
        f"## Procedencia (auditoría)\n\n"
        f"- **seq:** {event['seq']}  ·  **ts:** {event['ts_utc']}\n"
        f"- **payload_hash:** `{event['payload_hash']}`\n"
        f"- **chain_hash:** `{event['chain_hash']}`\n\n"
        f"```json\n{front}\n```\n"
    )


def _to_yaml(meta: dict[str, Any]) -> str:
    """Pequeño serializador YAML (evita depender de pyyaml aquí; stack lockdown)."""
    lines: list[str] = []
    for key, value in meta.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, list):
            inner = ", ".join(str(v) for v in value)
            lines.append(f"{key}: [{inner}]")
        elif isinstance(value, str):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def inject_rule(
    *,
    description: str,
    rule_type: str,
    action: str,
    instrument: str | None = None,
    window_spec: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    owner: str = "unknown",
    rule_id: str | None = None,
    db_path: Path | None = None,
    chain_path: Path | None = None,
    vault_dir: Path | None = None,
) -> OntologyRule:
    """Inyecta una regla ontológica restrictiva. Único punto de escritura.

    Append al chain (verdad) → proyección a ``ontology_rules`` → nota al vault.
    Lanza ``ValueError`` si la acción no es restrictiva (defensa del invariante).
    """
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Magister solo inyecta reglas restrictivas {VALID_ACTIONS}; "
            f"recibido {action!r}. Una regla NUNCA puede aprobar."
        )

    window_spec = window_spec or {}
    params = params or {}
    rule_id = rule_id or _slug_id(description)
    db = Path(db_path) if db_path is not None else settings.sqlite_path
    cpath = Path(chain_path) if chain_path is not None else chain.DEFAULT_CHAIN
    vroot = Path(vault_dir) if vault_dir is not None else settings.obsidian_vault_path
    vault_rel = f"{VAULT_ONTOLOGY_SUBDIR}/{rule_id}.md"

    payload: dict[str, Any] = {
        "op": "create",
        "id": rule_id,
        "rule_type": rule_type,
        "instrument": instrument,
        "window_spec": window_spec,
        "action": action,
        "params": params,
        "vault_path": vault_rel,
        "active": 1,
        "description": description,
    }

    # 1. Append al hash chain (fuente de verdad).
    event = chain.append_event(
        actor=f"human:{owner}",
        event_type="ontology_rule",
        event_key=rule_id,
        payload=payload,
        chain_path=cpath,
    )

    # 2. Proyectar a SQLite (ontology_rules).
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        chain.project_event(conn, event)
        conn.commit()
    finally:
        conn.close()

    # 3. Proyectar la nota al vault.
    note_path = vroot / vault_rel
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        _vault_note(
            rule_id=rule_id,
            description=description,
            rule_type=rule_type,
            instrument=instrument,
            window_spec=window_spec,
            action=action,
            params=params,
            owner=owner,
            event=event,
        ),
        encoding="utf-8",
    )

    return OntologyRule(
        id=rule_id,
        rule_type=rule_type,
        action=action,
        instrument=instrument,
        window_spec=window_spec,
        params=params,
        active=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Magister — inyección de reglas ontológicas")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--owner", type=str, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inject", help="inyectar una regla restrictiva (veto|size_down)")
    p.add_argument("--description", required=True)
    p.add_argument("--rule-type", required=True)
    p.add_argument("--action", required=True, choices=list(VALID_ACTIONS))
    p.add_argument("--instrument", default=None)
    p.add_argument("--window", type=str, default="{}", help="JSON de la ventana (window_spec)")
    p.add_argument("--params", type=str, default="{}", help="JSON de params de la acción")
    p.add_argument("--rule-id", default=None)

    args = parser.parse_args()
    owner = args.owner or "cli"

    if args.cmd == "inject":
        rule = inject_rule(
            description=args.description,
            rule_type=args.rule_type,
            action=args.action,
            instrument=args.instrument,
            window_spec=json.loads(args.window),
            params=json.loads(args.params),
            owner=owner,
            rule_id=args.rule_id,
            db_path=args.db,
        )
        ts = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        print(f"[{ts}] Magister inyectó regla {rule.id} ({rule.action}) — solo restringe.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
