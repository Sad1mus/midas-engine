---
id: DEC-011
title: Cockpit - Proyeccion read-only de SQLite a Obsidian (Dataview)
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-011: Cockpit — Proyección read-only de SQLite a Obsidian (Dataview)

## Context

MIDAS ya razona, decide y se autolimita, pero un humano (Sad1mus o el socio) no
tenía forma de **ver** el estado vivo del fondo de un vistazo: equity/PnL, estado
del DD-gate, trades, sleeves con su veredicto GO/NO-GO, regímenes 𝒳, reglas de
Magister y decisiones. Hacía falta un **cockpit** — sin violar el stack lockdown
([[DEC-001]]) ni el principio de Obsidian asíncrono ([[DEC-004]]).

## Decision

Un **projector** (`infra/obsidian_sync/projector.py`) proyecta el estado del `.db`
a notas Markdown que Obsidian + el plugin **Dataview** agregan en un dashboard.

### Read-only (la BD nunca se escribe)

El projector abre SQLite con `mode=ro` (URI). Cualquier intento de escritura lanza
`OperationalError`. La proyección es una **vista**; la fuente de verdad sigue siendo
el hash chain ([[DEC-005]]) y el `.db` su reconstrucción.

### Batch / Obsidian asíncrono (inviolable)

El projector corre **offline/batch** (vía `scripts/cockpit.py`). Está **PROHIBIDO**
importarlo desde `core/risk` o el hot path del sleeve — un test de aislamiento lo
verifica (importar el projector no arrastra `core.risk`). El vault jamás está en el
camino de ejecución de un trade.

### Dataview como capa de vista (cero deps nuevas)

Dataview es un **plugin de Obsidian**, no una dependencia de Python. El projector
solo **emite Markdown con frontmatter YAML tipado** (números como números, fechas
ISO); Dataview hace las consultas en el cliente. No se añadió ninguna dep a
`pyproject` (DEC-001 intacto).

### Carpetas generadas vs notas humanas

- **Generadas** (el cockpit las sobrescribe): `vault/00_System/cockpit/MIDAS — Cockpit.md`
  y `vault/_generated/` (equity, sleeves, regímenes, trades, ontología). Cada nota
  lleva `generated: true` y un aviso "no editar a mano".
- **Humanas** (el cockpit NUNCA las toca): `vault/10_Decisions/` (los ADRs).

### Idempotencia

El contenido de cada nota es función pura de los datos (sin `now()`), así que
`project_all` dos veces produce **bytes idénticos** — `git status` queda limpio.

## Consequences

### Positive

- Un humano abre `vault/` en Obsidian y ve MIDAS en una pantalla.
- Cero deps nuevas; proyección auditable y reproducible desde el chain.
- Aislamiento garantizado por test: el cockpit no puede contaminar el hot path.

### Negative / Trade-offs

- Requiere instalar el plugin Dataview (cliente) — documentado en el README del cockpit.
- v1 cap de notas recientes (50); sin limpieza de notas huérfanas (iteración futura).

### Risks

- **Editar a mano una nota generada** → mitigado: aviso explícito + se sobrescribe.
- **Acoplar el projector al hot path** → mitigado: test de aislamiento de imports.

## Alternatives Considered

- **Dashboard web propio (FastAPI/Streamlit):** rechazado en Fase 1 — dep nueva e infra;
  Obsidian ya es la corteza semántica del sistema ([[DEC-004]]).
- **Escribir directo a notas desde el hot path:** rechazado de plano — viola Obsidian
  asíncrono; el vault es proyección, nunca fuente de verdad.

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (Dataview es plugin, no dep de Python).
- [[DEC-004]] — Convenciones del Vault / Obsidian asíncrono (write-back batch).
- [[DEC-005]] — Mente Portable (el `.db` es vista; el chain es la verdad).
- `infra/obsidian_sync/projector.py`, `scripts/cockpit.py`,
  `vault/00_System/cockpit/` (dashboard + README), `vault/_generated/`
