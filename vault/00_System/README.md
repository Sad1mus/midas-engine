---
id: SYS-001
title: MIDAS Capital Vault — Índice
type: system
owner: Jordy Marin
created: 2026-05-20
tags: [system, index]
---

# MIDAS Capital Vault

Knowledge base canónico del fondo. Mantenido por Obsidian; proyectado
desde y hacia SQLite (`.midas/midas.db`) por el worker async en
`infra/obsidian_sync/`.

## Estructura

| Carpeta | Contenido |
|---|---|
| `00_System/` | Convenciones, índice (este archivo), glosario |
| `10_Decisions/` | Architecture Decision Records (DEC-NNN) |
| `20_Sleeves/` | Una nota por estrategia, lifecycle, métricas |
| `30_Regimes/` | Catálogo de regímenes detectados |
| `40_Postmortems/` | Post-mortem de gate trips y eventos críticos |
| `50_Daily/` | Daily journal automatizado |
| `90_Archive/` | Notas obsoletas preservadas por auditoría |

## Reglas

1. Todo archivo tiene **YAML frontmatter** con `id`, `title`, `type`,
   `owner`, `created`. Verificado por pre-commit.
2. Python escribe **solo** desde el worker async. Hot path nunca toca
   archivos.
3. Cambios humanos a archivos se proyectan al `audit_log` con
   `actor='human:<name>'`.
4. Decisiones se gestionan **siempre** via `scripts/decision_log.py`, no
   creando archivos directamente.

## Decisiones aceptadas

- [[10_Decisions/DEC-001_Stack-Fase-1-Lockdown]]
- [[10_Decisions/DEC-002_Monorepo-Layout]]
- [[10_Decisions/DEC-003_SQLite-Schema-v1]]
- [[10_Decisions/DEC-004_Vault-Conventions]]

## Quick start para humanos

```bash
# Listar todas las decisiones
python scripts/decision_log.py list

# Ver una decisión
python scripts/decision_log.py show DEC-001

# Crear una nueva
python scripts/decision_log.py new "Adoptar X en lugar de Y"

# Verificar la integridad del hash chain
python scripts/verify_chain.py
```
