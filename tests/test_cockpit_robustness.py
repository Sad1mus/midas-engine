"""Tests de robustez del cockpit: aislamiento, read-only, idempotencia, .db vacío."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.obsidian_sync import projector  # noqa: E402


def test_projector_does_not_import_core_risk() -> None:
    """Aislamiento (Obsidian asíncrono): importar el projector NO arrastra core.risk."""
    code = (
        "import sys; sys.path.insert(0, '.');"
        "import infra.obsidian_sync.projector;"
        "leaks=[m for m in sys.modules if m.startswith('core.risk')];"
        "print('LEAK:'+','.join(leaks) if leaks else 'OK');"
        "sys.exit(1 if leaks else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"el projector arrastró core.risk: {result.stdout}{result.stderr}"
    assert "OK" in result.stdout


def test_empty_db_projects_coherently(tmp_db: Path, tmp_path: Path) -> None:
    """Robustez ante .db vacío: no peta, genera un cockpit vacío coherente."""
    vault = tmp_path / "vault"
    summary = projector.project_all(tmp_db, vault)  # tmp_db = schema sin datos
    # dashboard + equity siempre existen; entidades a 0
    assert (vault / "00_System" / "cockpit" / "MIDAS — Cockpit.md").exists()
    assert (vault / "_generated" / "equity.md").exists()
    assert summary.counts.get("sleeves") == 0
    assert summary.counts.get("trades") == 0
    assert summary.counts.get("regimes") == 0
    assert summary.gate_level == "normal"
    assert summary.equity is None  # sin equity_curve


def test_empty_db_is_idempotent(tmp_db: Path, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    projector.project_all(tmp_db, vault)
    snap1 = {p.name: p.read_bytes() for p in vault.rglob("*.md")}
    projector.project_all(tmp_db, vault)
    snap2 = {p.name: p.read_bytes() for p in vault.rglob("*.md")}
    assert snap1 == snap2


def test_readonly_global_snapshot(tmp_db: Path, tmp_path: Path) -> None:
    """La BD no cambia entre snapshots (read-only end-to-end)."""
    before = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
    projector.project_all(tmp_db, tmp_path / "vault")
    projector.project_all(tmp_db, tmp_path / "vault")
    after = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
    assert before == after
