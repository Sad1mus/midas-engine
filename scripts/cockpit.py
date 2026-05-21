"""Cockpit CLI — regenera la proyección del estado de MIDAS para Obsidian.

Reconstruye el `.db` desde el hash chain (fuente de verdad, vía `init_db`) y luego
proyecta el estado a notas Markdown en el vault (read-only, batch). Imprime un
resumen y devuelve rc=0. Idempotente: correr dos veces no cambia el árbol.

    python scripts/cockpit.py                  # .midas/midas.db + vault/
    python scripts/cockpit.py --no-reconstruct # proyecta el .db tal cual está
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from infra.audit import chain  # noqa: E402
from infra.obsidian_sync import projector  # noqa: E402
from infra.obsidian_sync.projector import ProjectionSummary  # noqa: E402
from scripts.init_db import DEFAULT_DB, init_db  # noqa: E402


def run_cockpit(
    db_path: Path,
    vault_path: Path,
    *,
    chain_path: Path | None = None,
    reconstruct: bool = True,
) -> ProjectionSummary:
    """Reconstruye (opcional) el .db desde el chain y proyecta el cockpit al vault."""
    if reconstruct:
        init_db(db_path, chain_path=chain_path if chain_path is not None else chain.DEFAULT_CHAIN)
    return projector.project_all(db_path, vault_path)


def _print_summary(summary: ProjectionSummary) -> None:
    print("\n─── MIDAS Cockpit ───")
    print(f"vault: {summary.vault}")
    print("notas generadas por tipo:")
    for kind in ("dashboard", "equity", "sleeves", "regimes", "trades", "ontology"):
        print(f"  {kind:<10} {summary.counts.get(kind, 0)}")
    print(f"DD-gate: {summary.gate_level}  (sizing ×{summary.sizing_mult})")
    print(f"equity: {summary.equity}   drawdown: {summary.drawdown_pct}")
    print(f"sleeves GO/NO-GO: {summary.sleeves_go} / {summary.sleeves_nogo}")
    print("─────────────────────")


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenera el cockpit de MIDAS (SQLite → Obsidian)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--vault", type=Path, default=settings.obsidian_vault_path)
    parser.add_argument("--chain-file", type=Path, default=chain.DEFAULT_CHAIN)
    parser.add_argument(
        "--no-reconstruct",
        action="store_true",
        help="proyectar el .db tal cual (sin replay del chain)",
    )
    args = parser.parse_args()

    summary = run_cockpit(
        args.db,
        args.vault,
        chain_path=args.chain_file,
        reconstruct=not args.no_reconstruct,
    )
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
