"""Configuración central de MIDAS, cargada desde variables de entorno.

Sin `pydantic-settings` a propósito: el stack Fase 1 (DEC-001) está bajo lockdown
y esa dependencia no está en la lista. Usamos pydantic puro + un loader explícito
desde os.environ. Todo el sistema importa `settings` desde aquí.
"""
from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class SystemMode(StrEnum):
    """Modo de ejecución — determina latencia tolerable y restricciones de compliance."""

    A_AI_DRIVEN = "A"      # Zenithstone live propio. Sub-segundo. Sin restricciones.
    B_AI_ASSISTED = "B"    # Apex / Track A. Humano confirma <5s.
    C_LOCAL_ONLY = "C"     # TopStep. PC física local.


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw is not None else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw is not None else default


class Settings(BaseModel):
    """Configuración tipada. Construir con `Settings.from_env()` o usar el singleton `settings`."""

    model_config = {"frozen": True}

    # General
    environment: str = "development"
    system_mode: SystemMode = SystemMode.A_AI_DRIVEN

    # Seguridad (arranca en modo seguro)
    dry_run: bool = True
    paper_trading: bool = True

    # Límites de riesgo (el Veto los lee)
    max_daily_loss_pct: float = 5.0
    max_daily_trades: int = 3
    max_consecutive_losses: int = 3
    min_trade_approval_usd: float = 1000.0

    # Compliance por firma (Track A)
    apex_ai_allowed: bool = False
    topstep_ai_allowed: bool = True
    lucid_ai_allowed: bool = False

    # Almacenamiento
    sqlite_path: Path = Path(".midas/midas.db")
    obsidian_vault_path: Path = Path("vault")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=_env_str("ENVIRONMENT", "development"),
            system_mode=SystemMode(_env_str("SYSTEM_MODE", "A")),
            dry_run=_env_bool("DRY_RUN", True),
            paper_trading=_env_bool("PAPER_TRADING", True),
            max_daily_loss_pct=_env_float("MAX_DAILY_LOSS_PCT", 5.0),
            max_daily_trades=_env_int("MAX_DAILY_TRADES", 3),
            max_consecutive_losses=_env_int("MAX_CONSECUTIVE_LOSSES", 3),
            min_trade_approval_usd=_env_float("MIN_TRADE_APPROVAL_USD", 1000.0),
            apex_ai_allowed=_env_bool("APEX_AI_ALLOWED", False),
            topstep_ai_allowed=_env_bool("TOPSTEP_AI_ALLOWED", True),
            lucid_ai_allowed=_env_bool("LUCID_AI_ALLOWED", False),
            sqlite_path=Path(_env_str("SQLITE_PATH", ".midas/midas.db")),
            obsidian_vault_path=Path(_env_str("OBSIDIAN_VAULT_PATH", "vault")),
        )

    @property
    def is_live(self) -> bool:
        return not self.dry_run and not self.paper_trading

    @property
    def can_execute_autonomously(self) -> bool:
        """Modo A o C ejecutan sin confirmación humana. Modo B requiere humano."""
        return self.system_mode in (SystemMode.A_AI_DRIVEN, SystemMode.C_LOCAL_ONLY)


# Singleton — importar como `from core.config import settings`
settings = Settings.from_env()
