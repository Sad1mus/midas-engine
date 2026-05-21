"""Tests del kill switch — el botón rojo que veta todo y persiste en disco."""

from __future__ import annotations

from pathlib import Path

from core.risk.kill_switch import KillSwitch


def test_starts_untriggered(kill: KillSwitch) -> None:
    assert kill.is_triggered() is False
    assert kill.status()["triggered"] is False


def test_trigger_sets_state_and_reason(kill: KillSwitch) -> None:
    kill.trigger("drawdown diario excedido")
    assert kill.is_triggered() is True
    status = kill.status()
    assert status["triggered"] is True
    assert status["reason"] == "drawdown diario excedido"
    assert status["triggered_at"] is not None


def test_clear_releases(kill: KillSwitch) -> None:
    kill.trigger("pánico")
    kill.clear(cleared_by="jordy")
    assert kill.is_triggered() is False
    status = kill.status()
    assert status["cleared_at"] is not None
    assert "jordy" in str(status["reason"])


def test_state_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"
    KillSwitch(db_path=db).trigger("se reinició el proceso")
    # Nueva instancia, misma DB → el estado debe sobrevivir
    assert KillSwitch(db_path=db).is_triggered() is True
